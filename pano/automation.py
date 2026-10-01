"""⚙️ Otomasyon motoru: "eğer şu olursa bunu yap" kuralları.

Tetikleyiciler
- Zaman (cron /hatirlatma, 5 dakikada bir): her gün / her hafta / her ay, belirli saatte. Her dönemde
  (gün, hafta, ay) en fazla bir kez çalışır; cron bir süre çalışmasa da dönem içinde geç de olsa bir kez yapılır.
- Olay: harcama eklenince, fatura ödenince, listeye ürün eklenip sayı eşiği geçince, yapılacak tamamlanınca.
  Uygulama kodu fire() çağırır; liste olayları listeye erişebilen herkesin kurallarına bakar.

Eylemler: Telegram mesajı (kendine ya da başka bir kullanıcıya), harcama, yapılacak, alışveriş ürünü, not.
Metinlerde {tutar}, {fatura}, {liste} gibi yer tutucular olaya göre doldurulur.

Güvenlik: otomasyonun yaptığı iş başka otomasyonu tetiklemez (döngü olmaz); bir kural günde en fazla
DAILY_CAP kez çalışır; olay sırasında çıkan hata asıl işlemi (ör. harcama kaydı) asla bozmaz.
"""
import calendar
import json
import logging
import re
from contextvars import ContextVar
from datetime import datetime, timedelta

from .db import execute, get_db, query, query_one
from .utils import fmt_date, fmt_money, fold

log = logging.getLogger(__name__)

TRIGGERS = {
    "daily": "Her gün",
    "weekly": "Her hafta",
    "monthly": "Her ay",
    "expense_added": "Harcama eklenince",
    "bill_paid": "Fatura ödenince",
    "list_count": "Listedeki ürün sayısı eşiği geçince",
    "todo_done": "Yapılacak tamamlanınca",
}
SCHEDULED = ("daily", "weekly", "monthly")
ACTIONS = {
    "notify": "Telegram mesajı gönder",
    "expense": "Harcama ekle",
    "todo": "Yapılacak ekle",
    "shopping": "Alışveriş listesine ekle",
    "note": "Not ekle",
}
# Olaya göre metinlerde kullanılabilecek yer tutucular
PLACEHOLDERS = {
    "daily": [], "weekly": [], "monthly": [],
    "expense_added": ["tutar", "kategori", "not"],
    "bill_paid": ["fatura", "tutar"],
    "list_count": ["liste", "adet"],
    "todo_done": ["is", "liste", "kim"],
}
COMMON_PLACEHOLDERS = ["tarih", "saat", "ay"]
WEEKDAYS = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim",
          "Kasım", "Aralık"]
DAILY_CAP = 20

_running = ContextVar("automation_running", default=False)


# ---------- Kayıt okuma ----------
def load(row):
    """Satırı sözlüğe çevirir; yapılandırma JSON'ları açılır."""
    r = dict(row)
    r["tconf"] = json.loads(r.get("trigger_config") or "{}")
    r["aconf"] = json.loads(r.get("action_config") or "{}")
    return r


# ---------- Zamanlama ----------
def scheduled_at(trigger, conf, now):
    """Bu dönemdeki (gün/hafta/ay) çalışma anı, yerel saat."""
    hh, mm = (int(x) for x in conf.get("time", "09:00").split(":"))
    t = now.date()
    if trigger == "daily":
        day = t
    elif trigger == "weekly":
        day = t - timedelta(days=t.weekday()) + timedelta(days=int(conf.get("weekday", 0)))
    else:
        last = calendar.monthrange(t.year, t.month)[1]
        day = t.replace(day=min(int(conf.get("day", 1)), last))  # 31'i olmayan ayda son gün
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=now.tzinfo)


def period_key(trigger, now):
    t = now.date()
    if trigger == "daily":
        return t.isoformat()
    if trigger == "weekly":
        y, w, _ = t.isocalendar()
        return f"{y}-W{w:02d}"
    return f"{t.year:04d}-{t.month:02d}"


def initial_period(trigger, conf, now):
    """Yeni (ya da zamanı değişen) kural: bu dönemin saati geçtiyse bu dönem 'yapıldı' sayılır;
    yoksa çarşamba günü kurulan 'her pazartesi' kuralı hemen çalışırdı."""
    if trigger not in SCHEDULED:
        return None
    return period_key(trigger, now) if now >= scheduled_at(trigger, conf, now) else None


def run_scheduled(now):
    """cron: zamanı gelen kuralları çalıştırır. Çalışan kural sayısını döner."""
    n = 0
    for row in query("SELECT * FROM automations WHERE enabled = 1 AND trigger_type IN ('daily', 'weekly', 'monthly')"):
        rule = load(row)
        key = period_key(rule["trigger_type"], now)
        # Dönem anahtarları ("2026-10", "2026-W42", "2026-10-19") metin olarak da sıralanır
        if (rule["last_period"] and rule["last_period"] >= key) or now < scheduled_at(rule["trigger_type"], rule["tconf"], now):
            continue
        execute("UPDATE automations SET last_period = ? WHERE id = ?", (key, rule["id"]))
        run(rule, {}, now)
        n += 1
    return n


# ---------- Olaylar ----------
def fire(event, user_id=None, list_id=None, **payload):
    """Uygulama kodundan çağrılır. Hiçbir zaman hata fırlatmaz."""
    if _running.get():
        return  # otomasyonun yaptığı iş başka otomasyonu tetiklemez
    try:
        from .todo_reminders import now_local
        now = now_local()
        for rule in _matching(event, user_id, list_id, payload):
            run(rule, payload, now)
    except Exception:  # olay asıl işlemi asla bozmasın
        log.exception("Otomasyon olayı işlenemedi: %s", event)


def _matching(event, user_id, list_id, payload):
    rows = query("SELECT * FROM automations WHERE enabled = 1 AND trigger_type = ?", (event,))
    out = []
    for row in rows:
        rule = load(row)
        c = rule["tconf"]
        if event in ("expense_added", "bill_paid"):
            if rule["user_id"] != user_id:
                continue
            if event == "expense_added":
                if c.get("category") and fold(c["category"]) != fold(payload.get("kategori", "")):
                    continue
                if c.get("min_amount") and (payload.get("tutar") or 0) < c["min_amount"]:
                    continue
        else:
            if c.get("list_id") and c["list_id"] != list_id:
                continue
            if not _can_access_list(rule["user_id"], list_id):
                continue
            if event == "list_count":
                threshold = int(c.get("count", 10))
                if not (payload.get("onceki", 0) < threshold <= payload.get("adet", 0)):
                    continue  # sadece eşik yukarı doğru geçildiğinde, bir kez
        out.append(rule)
    return out


def _can_access_list(user_id, list_id):
    return query_one("SELECT 1 FROM lists WHERE id = ? AND (user_id = ? OR shared = 1)", (list_id, user_id)) is not None


# ---------- Çalıştırma ----------
def _values(payload, now):
    v = {"tarih": now.strftime("%d.%m.%Y"), "saat": now.strftime("%H:%M"), "ay": MONTHS[now.month - 1]}
    for k, val in payload.items():
        if k == "tutar" and isinstance(val, (int, float)):
            v[k] = fmt_money(val)
        elif val is not None:
            v[k] = str(val)
    return v


def fill(text, values):
    """{yer_tutucu} -> değer; bilinmeyenler olduğu gibi kalır (str.format'ın tuzaklarından kaçınır)."""
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), text or "")


def run(rule, payload, now, test=False):
    """Kuralın eylemini yapar; sonucu kayda geçirir. (başarılı, açıklama)"""
    if not test:
        recent = query_one("SELECT COUNT(*) AS n FROM automation_log WHERE automation_id = ? AND ok = 1"
                           " AND ran_at >= datetime('now', '-1 day')", (rule["id"],))
        if recent["n"] >= DAILY_CAP:
            _log(rule, False, f"Günlük {DAILY_CAP} çalışma sınırı doldu; bugün atlandı.")
            return False, "Günlük sınır doldu."
    token = _running.set(True)
    try:
        ok, detail = True, _do(rule, _values(payload, now), now.date().isoformat())
    except ActionError as e:
        ok, detail = False, str(e)
    except Exception as e:
        log.exception("Otomasyon eylemi başarısız: %s", rule["id"])
        ok, detail = False, f"Beklenmeyen hata: {e}"
    finally:
        _running.reset(token)
    _log(rule, ok, ("🧪 Deneme: " if test else "") + detail)
    execute("UPDATE automations SET last_run_at = CURRENT_TIMESTAMP, run_count = run_count + 1, last_error = ?"
            " WHERE id = ?", (None if ok else detail, rule["id"]))
    return ok, detail


def _log(rule, ok, detail):
    execute("INSERT INTO automation_log (automation_id, user_id, ok, detail) VALUES (?, ?, ?, ?)",
            (rule["id"], rule["user_id"], 1 if ok else 0, detail[:300]))


class ActionError(Exception):
    pass


def _do(rule, values, today_iso):
    from . import telegram
    a, c, uid = rule["action_type"], rule["aconf"], rule["user_id"]
    if a == "notify":
        target = uid if c.get("to", "me") == "me" else int(c["to"])
        user = query_one("SELECT * FROM users WHERE id = ?", (target,))
        if user is None:
            raise ActionError("Mesajın gideceği kullanıcı artık yok.")
        if not user["telegram_chat_id"]:
            raise ActionError(f"{user['display_name'] or user['username']} Telegram'ı bağlamamış.")
        if not telegram.enabled():
            raise ActionError("Telegram botu ayarlı değil.")
        owner = query_one("SELECT username, display_name FROM users WHERE id = ?", (uid,))
        head = f"⚙️ <b>{telegram.escape(rule['name'])}</b>"
        if target != uid:
            head += f" · {telegram.escape(owner['display_name'] or owner['username'])}"
        telegram.send_message(user["telegram_chat_id"], head + "\n" + telegram.escape(fill(c.get("text"), values)))
        return "Mesaj gönderildi" + ("" if target == uid else f": {user['display_name'] or user['username']}") + "."
    if a == "expense":
        from .modules.expenses import normalize_category
        category = normalize_category(c.get("category"))
        execute("INSERT INTO expenses (user_id, amount, category, note, date) VALUES (?, ?, ?, ?, ?)",
                (uid, c["amount"], category, fill(c.get("note"), values)[:200], today_iso))
        return f"{fmt_money(c['amount'])} · {category} harcaması eklendi."
    if a in ("todo", "shopping"):
        from .modules.lists import add_items
        lst = query_one("SELECT * FROM lists WHERE id = ?", (c.get("list_id"),))
        if lst is None or not _can_access_list(uid, lst["id"]):
            raise ActionError("Seçili liste bulunamadı ya da artık erişilemiyor.")
        text = fill(c.get("text"), values)
        if a == "shopping":
            texts = [t.strip()[:200] for t in re.split(r"[,\n]", text) if t.strip()][:30]
            add_items(lst["id"], texts, uid)
            return f"“{lst['name']}” listesine {len(texts)} ürün eklendi."
        add_items(lst["id"], [text[:500]], uid, due_date=today_iso if c.get("due_today") else None)
        return f"“{lst['name']}” listesine yapılacak eklendi: {text[:60]}"
    if a == "note":
        title, content = fill(c.get("title"), values)[:200], fill(c.get("text"), values)[:20000]
        execute("INSERT INTO notes (user_id, title, content, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                (uid, title, content))
        return f"Not eklendi: {title or content[:40]}"
    raise ActionError("Bilinmeyen eylem.")


# ---------- Açıklama (arayüz) ----------
def describe_trigger(rule):
    t, c = rule["trigger_type"], rule["tconf"]
    if t == "daily":
        return f"Her gün {c.get('time')}"
    if t == "weekly":
        return f"Her {WEEKDAYS[int(c.get('weekday', 0))].lower()} {c.get('time')}"
    if t == "monthly":
        day = int(c.get("day", 1))
        return f"Her ayın {day}. günü {c.get('time')}" + (" (kısa aylarda son gün)" if day > 28 else "")
    if t == "expense_added":
        parts = [c["category"]] if c.get("category") else []
        if c.get("min_amount"):
            parts.append(f"en az {fmt_money(c['min_amount'])}")
        return "Harcama eklenince" + (f" ({', '.join(parts)})" if parts else "")
    if t == "bill_paid":
        return "Fatura ödenince"
    name = _list_name(c.get("list_id"))
    if t == "list_count":
        return f"“{name}” listesinde {c.get('count')} açık ürün olunca"
    return f"“{name}” listesinde iş tamamlanınca" if c.get("list_id") else "Bir yapılacak tamamlanınca"


def describe_action(rule):
    a, c = rule["action_type"], rule["aconf"]
    if a == "notify":
        if c.get("to", "me") == "me":
            return "Bana Telegram mesajı"
        u = query_one("SELECT username, display_name FROM users WHERE id = ?", (int(c["to"]),))
        return f"{(u['display_name'] or u['username']) if u else '?'} kişisine Telegram mesajı"
    if a == "expense":
        return f"{fmt_money(c.get('amount') or 0)} · {c.get('category') or 'Diğer'} harcaması ekle"
    if a == "todo":
        return f"“{_list_name(c.get('list_id'))}” listesine yapılacak ekle"
    if a == "shopping":
        return f"“{_list_name(c.get('list_id'))}” listesine ekle: {c.get('text', '')[:40]}"
    return "Not ekle"


def _list_name(list_id):
    row = query_one("SELECT name FROM lists WHERE id = ?", (list_id,)) if list_id else None
    return row["name"] if row else "silinmiş liste"


def purge_log(days=60):
    db = get_db()
    db.execute("DELETE FROM automation_log WHERE ran_at < datetime('now', ?)", (f"-{days} days",))
    db.commit()


def last_run_text(value):
    return fmt_date(value[:10]) if value else "henüz çalışmadı"
