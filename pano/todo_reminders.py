"""Yapılacaklar için Telegram hatırlatmaları.

Her madde için isteğe bağlı saat (due_time) ve hatırlatma zamanı (remind_before, dakika):
- remind_before > 0: süre yaklaşınca "⏰ yaklaşıyor" + zamanı gelince "🔔 zamanı geldi"
- remind_before = 0: sadece zamanı gelince
- NULL: hatırlatma yok

Arka planda çalışan işlem olmadığı için /cron/<CRON_SECRET>/hatirlatma adresi dışarıdan
(ör. cron-job.org, 5 dakikada bir) çağrılır; zamanı gelen mesajlar o anda gönderilir.
"""
import os
import re
from datetime import datetime, time, timedelta

from .db import get_db
from .utils import TZ, add_months, now_local, today

REMIND_OPTIONS = [
    ("", "Hatırlatma yok"),
    ("0", "Zamanı gelince"),
    ("15", "15 dk önce"),
    ("60", "1 saat önce"),
    ("180", "3 saat önce"),
    ("1440", "1 gün önce"),
]
REMIND_LABELS = {int(v): label for v, label in REMIND_OPTIONS if v}
DEFAULT_REMIND = "0"
# Saati girilmemiş maddeler için hatırlatma saati
DEFAULT_DUE_TIME = os.environ.get("REMINDER_DEFAULT_TIME", "09:00")
# Cron bir süre çağrılmazsa (ör. site durdu) en fazla bu kadar gecikmiş "zamanı geldi" gönderilir;
# daha eskileri sessizce işaretlenir ki yığılmış eski mesajlar birden gelmesin
LATE_WINDOW = timedelta(hours=6)


REPEAT_OPTIONS = [
    ("", "Tekrar yok"),
    ("daily", "Her gün"),
    ("weekdays", "Hafta içi her gün"),
    ("weekly", "Her hafta"),
    ("monthly", "Her ay"),
    ("yearly", "Her yıl"),
]
REPEAT_LABELS = dict(REPEAT_OPTIONS[1:])


def done_buttons(item_id):
    """Hatırlatma mesajının butonları (webhook kuruluysa gönderilir)."""
    return [[("✅ Tamamlandı", f"done:{item_id}")],
            [("⏰ 1 saat ertele", f"snz:{item_id}:1h"), ("📅 Yarına", f"snz:{item_id}:1d")]]


def undo_buttons(item_id):
    return [[("↩️ Geri al", f"undo:{item_id}")]]


def parse_time(value):
    """'9:5', '09.30', '1430' -> 'HH:MM'; geçersiz/boşsa None."""
    s = (value or "").strip()
    m = re.fullmatch(r"(\d{1,2})[:.]?(\d{2})", s)
    if not m:
        return None
    h, mnt = int(m.group(1)), int(m.group(2))
    if h > 23 or mnt > 59:
        return None
    return f"{h:02d}:{mnt:02d}"


def parse_repeat(value):
    return value if value in REPEAT_LABELS else None


def repeat_label(value):
    return REPEAT_LABELS.get(value, "")


def _advance(d, repeat):
    if repeat == "daily":
        return d + timedelta(days=1)
    if repeat == "weekdays":
        d += timedelta(days=1)
        while d.weekday() >= 5:  # Cumartesi, Pazar
            d += timedelta(days=1)
        return d
    if repeat == "weekly":
        return d + timedelta(days=7)
    if repeat == "monthly":
        return add_months(d, 1)
    return add_months(d, 12)


def next_repeat_date(due_date, repeat):
    """Bir sonraki tekrar: son tarihten ileri, bugünden sonraki ilk gün (geç tamamlansa da geçmişe düşmez)."""
    d = datetime.fromisoformat(due_date[:10]).date()
    t = today()
    d = _advance(d, repeat)
    while d <= t:
        d = _advance(d, repeat)
    return d.isoformat()


def set_done(item_id, done, actor_id=None):
    """Maddeyi tamamla / yeniden aç. Tekrarlayan maddede tamamlanınca sonrakini oluşturur,
    geri alınınca (henüz dokunulmamışsa) o sonrakini siler. Web, pano ve Telegram aynı yolu kullanır.
    actor_id: işaretleyen kullanıcı (otomasyondaki {kim} için)."""
    db = get_db()
    item = db.execute("SELECT * FROM list_items WHERE id = ?", (item_id,)).fetchone()
    if item is None or bool(item["done"]) == bool(done):
        return item
    if done:
        spawned = None
        if item["repeat"] and item["due_date"]:
            spawned = db.execute(
                "INSERT INTO list_items (list_id, text, qty, due_date, due_time, remind_before, repeat, created_by)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (item["list_id"], item["text"], item["qty"], next_repeat_date(item["due_date"], item["repeat"]),
                 item["due_time"], item["remind_before"], item["repeat"], item["created_by"]),
            ).lastrowid
        db.execute("UPDATE list_items SET done = 1, done_at = CURRENT_TIMESTAMP, spawned_id = ? WHERE id = ?",
                   (spawned, item_id))
        db.commit()
        from . import automation
        actor = db.execute("SELECT username, display_name FROM users WHERE id = ?", (actor_id,)).fetchone() \
            if actor_id else None
        lst = db.execute("SELECT name FROM lists WHERE id = ?", (item["list_id"],)).fetchone()
        automation.fire("todo_done", list_id=item["list_id"], liste=lst["name"] if lst else "",
                        kim=(actor["display_name"] or actor["username"]) if actor else "",
                        **{"is": item["text"]})
    else:
        if item["spawned_id"]:
            db.execute("DELETE FROM list_items WHERE id = ? AND done = 0", (item["spawned_id"],))
        db.execute("UPDATE list_items SET done = 0, done_at = NULL, spawned_id = NULL WHERE id = ?", (item_id,))
    db.commit()
    return db.execute("SELECT * FROM list_items WHERE id = ?", (item_id,)).fetchone()


def snooze(item_id, mode):
    """'1h': şu andan 1 saat sonrasına; '1d': ertesi güne aynı saatte. Hatırlatma yeniden gönderilir.
    Yeni zamanı ('YYYY-MM-DD', 'HH:MM') döner."""
    db = get_db()
    item = db.execute("SELECT * FROM list_items WHERE id = ?", (item_id,)).fetchone()
    now = now_local().replace(second=0, microsecond=0)
    if mode == "1h":
        when = now + timedelta(hours=1)
        # 1 saat sonrası için sadece "zamanı geldi" mesajı (önceden uyarı hemen tekrar gelmesin)
        new_date, new_time, remind = when.date().isoformat(), when.strftime("%H:%M"), 0
    else:
        base = max(datetime.fromisoformat(item["due_date"][:10]).date(), now.date()) if item["due_date"] else now.date()
        new_date, new_time = (base + timedelta(days=1)).isoformat(), item["due_time"]
        remind = item["remind_before"] if item["remind_before"] is not None else 0
    db.execute(
        "UPDATE list_items SET due_date = ?, due_time = ?, remind_before = ?,"
        " pre_sent_at = NULL, due_sent_at = NULL WHERE id = ?",
        (new_date, new_time, remind, item_id),
    )
    db.commit()
    return new_date, new_time or DEFAULT_DUE_TIME


def parse_remind(value):
    """Form değeri -> dakika (int) ya da None."""
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return None
    return minutes if minutes in REMIND_LABELS else None


def due_at(item):
    """Maddenin son tarih+saatini yerel saatle döner (saat yoksa varsayılan saat)."""
    if not item["due_date"]:
        return None
    hh, mm = (item["due_time"] or DEFAULT_DUE_TIME).split(":")
    d = datetime.fromisoformat(item["due_date"][:10]).date()
    return datetime.combine(d, time(int(hh), int(mm)), tzinfo=TZ)


def remind_label(minutes):
    return REMIND_LABELS.get(minutes, "")


def pending(now=None):
    """Gönderilmesi gereken hatırlatmalar: [(tür, madde)] ve sessizce kapatılacak eski maddeler.

    tür: 'pre' (yaklaşıyor) ya da 'due' (zamanı geldi)
    """
    now = now or now_local()
    today = now.date()
    rows = get_db().execute(
        "SELECT i.*, l.name AS list_name, l.user_id AS owner_id FROM list_items i"
        " JOIN lists l ON l.id = i.list_id"
        " WHERE i.done = 0 AND i.remind_before IS NOT NULL AND i.due_date IS NOT NULL"
        " AND i.due_date BETWEEN ? AND ?"
        " AND (i.due_sent_at IS NULL OR (i.remind_before > 0 AND i.pre_sent_at IS NULL))",
        ((today - timedelta(days=2)).isoformat(), (today + timedelta(days=2)).isoformat()),
    ).fetchall()
    to_send, stale = [], []
    for item in rows:
        when = due_at(item)
        if item["due_sent_at"] is None and now >= when:
            if now - when <= LATE_WINDOW:
                to_send.append(("due", item))
            else:
                stale.append(item)
        elif (item["remind_before"] > 0 and item["pre_sent_at"] is None
              and when - timedelta(minutes=item["remind_before"]) <= now < when):
            to_send.append(("pre", item))
    # Bu aralığın dışında kalıp hiç gönderilmemiş eski maddeler (ör. geçmiş tarihle eklenmiş)
    stale += get_db().execute(
        "SELECT * FROM list_items WHERE done = 0 AND remind_before IS NOT NULL AND due_sent_at IS NULL"
        " AND due_date < ?",
        ((today - timedelta(days=2)).isoformat(),),
    ).fetchall()
    return to_send, stale


def mark_sent(item_id, kind):
    column = "due_sent_at" if kind == "due" else "pre_sent_at"
    db = get_db()
    # "Zamanı geldi" gönderildiyse artık "yaklaşıyor" da gönderilmesin
    extra = ", pre_sent_at = COALESCE(pre_sent_at, CURRENT_TIMESTAMP)" if kind == "due" else ""
    db.execute(f"UPDATE list_items SET {column} = CURRENT_TIMESTAMP{extra} WHERE id = ?", (item_id,))
    db.commit()


def recipient_chat_id(item):
    """Atanan kişi, yoksa maddeyi ekleyen kişi, yoksa liste sahibi (Telegram'ı bağlı olan ilki)."""
    db = get_db()
    for uid in (item["assignee_id"], item["created_by"], item["owner_id"]):
        if uid:
            row = db.execute("SELECT telegram_chat_id FROM users WHERE id = ?", (uid,)).fetchone()
            if row and row["telegram_chat_id"]:
                return row["telegram_chat_id"]
    return None


def message(kind, item, url, escape):
    when = due_at(item)
    now = now_local()
    if when.date() == now.date():
        day = "Bugün"
    elif when.date() == now.date() + timedelta(days=1):
        day = "Yarın"
    else:
        day = when.strftime("%d.%m.%Y")
    time_text = when.strftime("%H:%M") if item["due_time"] else ""
    head = "⏰ <b>Yaklaşıyor:</b>" if kind == "pre" else "🔔 <b>Zamanı geldi:</b>"
    lines = [f"{head} {escape(item['text'])}", f"{day} {time_text}".strip() + f" · {escape(item['list_name'])}"]
    lines.append(f'<a href="{escape(url)}">Listeyi aç →</a>')
    return "\n".join(lines)
