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
from .utils import TZ, now_local

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
    """Maddeyi ekleyen kişi (Telegram bağlıysa), yoksa liste sahibi."""
    db = get_db()
    for uid in (item["created_by"], item["owner_id"]):
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
