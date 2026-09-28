"""Fatura ve ilaç hatırlatmaları (cron /hatirlatma çağrısında, yapılacaklarla birlikte çalışır).

Faturalar (remind=1, ödenmemiş): son günden bir gün önce ve son gün, varsayılan saatte (09:00)
  "🧾 Yarın / Bugün son gün" + [✅ Ödendi]. Son günü geçmiş ve hiç gönderilmemiş fatura için
  toplu eski mesaj gönderilmez, sessizce işaretlenir.
İlaçlar (notify=1, aktif): her doz saatinde "💊 ..." + [✅ Aldım]; saatten sonra en fazla 2 saat
  içinde gönderilir. Alındı bilgisi med_logs'ta tutulur (web'den de işaretlenebilir).
"""
from datetime import datetime, time, timedelta

from .db import get_db, query, query_one
from .todo_reminders import DEFAULT_DUE_TIME
from .utils import TZ, fmt_money, now_local

MED_WINDOW = timedelta(hours=2)


def _at(d, hhmm):
    hh, mm = hhmm.split(":")
    return datetime.combine(d, time(int(hh), int(mm)), tzinfo=TZ)


def chat_of(user_id):
    row = query_one("SELECT telegram_chat_id FROM users WHERE id = ?", (user_id,))
    return row["telegram_chat_id"] if row else None


# ---------- Faturalar ----------
def bill_buttons(bill_id):
    return [[("✅ Ödendi", f"bill:{bill_id}")]]


def bill_undo_buttons(bill_id):
    return [[("↩️ Geri al", f"billu:{bill_id}")]]


def pending_bills(now=None):
    """([(tür, fatura)], [sessizce kapatılacak faturalar]); tür 'pre' (yarın son gün) ya da 'due' (bugün)."""
    now = now or now_local()
    t = now.date()
    rows = get_db().execute(
        "SELECT * FROM bills WHERE paid = 0 AND remind = 1 AND due_sent_at IS NULL AND due_date <= ?",
        ((t + timedelta(days=1)).isoformat(),),
    ).fetchall()
    ready = now >= _at(t, DEFAULT_DUE_TIME)
    to_send, stale = [], []
    for bill in rows:
        due = bill["due_date"][:10]
        if due < t.isoformat():
            stale.append(bill)
        elif not ready:
            continue
        elif due == t.isoformat():
            to_send.append(("due", bill))
        elif bill["pre_sent_at"] is None:
            to_send.append(("pre", bill))
    return to_send, stale


def mark_bill(bill_id, kind):
    column = "due_sent_at" if kind == "due" else "pre_sent_at"
    extra = ", pre_sent_at = COALESCE(pre_sent_at, CURRENT_TIMESTAMP)" if kind == "due" else ""
    db = get_db()
    db.execute(f"UPDATE bills SET {column} = CURRENT_TIMESTAMP{extra} WHERE id = ?", (bill_id,))
    db.commit()


def bill_message(kind, bill, url, escape):
    head = "🧾 <b>Yarın son gün:</b>" if kind == "pre" else "🧾 <b>Bugün son gün:</b>"
    lines = [f"{head} {escape(bill['name'])}"]
    if bill["amount"]:
        lines.append(fmt_money(bill["amount"]))
    lines.append(f'<a href="{escape(url)}">Faturalar →</a>')
    return "\n".join(lines)


# ---------- İlaçlar ----------
def med_slots(med):
    return [s.strip() for s in (med["times"] or "").split(",") if s.strip()]


def med_buttons(log_id):
    return [[("✅ Aldım", f"med:{log_id}")]]


def med_undo_buttons(log_id):
    return [[("↩️ Geri al", f"medu:{log_id}")]]


def ensure_log(med_id, date, slot):
    db = get_db()
    db.execute("INSERT OR IGNORE INTO med_logs (med_id, date, slot) VALUES (?, ?, ?)", (med_id, date, slot))
    db.commit()
    return db.execute("SELECT * FROM med_logs WHERE med_id = ? AND date = ? AND slot = ?",
                      (med_id, date, slot)).fetchone()


def pending_meds(now=None):
    """[(ilaç, saat, chat_id, gün)] — zamanı gelmiş, henüz gönderilmemiş ve alınmamış dozlar."""
    now = now or now_local()
    t = now.date()
    out = []
    for med in query(
        "SELECT m.*, u.telegram_chat_id AS chat_id FROM medications m JOIN users u ON u.id = m.user_id"
        " WHERE m.active = 1 AND m.notify = 1 AND u.telegram_chat_id IS NOT NULL AND m.times != ''"
    ):
        for slot in med_slots(med):
            at = _at(t, slot)
            if not (at <= now <= at + MED_WINDOW):
                continue
            log = query_one("SELECT sent_at, taken_at FROM med_logs WHERE med_id = ? AND date = ? AND slot = ?",
                            (med["id"], t.isoformat(), slot))
            if log is None or (log["sent_at"] is None and log["taken_at"] is None):
                out.append((med, slot, med["chat_id"], t.isoformat()))
    return out


def mark_med_sent(log_id):
    db = get_db()
    db.execute("UPDATE med_logs SET sent_at = CURRENT_TIMESTAMP WHERE id = ?", (log_id,))
    db.commit()


def set_med_taken(log_id, taken):
    db = get_db()
    db.execute("UPDATE med_logs SET taken_at = " + ("CURRENT_TIMESTAMP" if taken else "NULL") + " WHERE id = ?",
               (log_id,))
    db.commit()


def med_message(med, slot, escape):
    dose = f" ({escape(med['dose'])})" if med["dose"] else ""
    note = f"\n<i>{escape(med['note'])}</i>" if med["note"] else ""
    return f"💊 <b>{escape(med['name'])}</b>{dose} — {slot}{note}"


def today_doses(user_id):
    """Bugünün dozları: [{'med', 'slot', 'taken', 'past'}] saat sırasına göre (web'deki program)."""
    now = now_local()
    t = now.date().isoformat()
    logs = {(r["med_id"], r["slot"]): r for r in query(
        "SELECT l.* FROM med_logs l JOIN medications m ON m.id = l.med_id WHERE m.user_id = ? AND l.date = ?",
        (user_id, t))}
    doses = []
    for med in query("SELECT * FROM medications WHERE user_id = ? AND active = 1", (user_id,)):
        for slot in med_slots(med):
            log = logs.get((med["id"], slot))
            doses.append({"med": med, "slot": slot, "taken": bool(log and log["taken_at"]),
                          "past": slot <= now.strftime("%H:%M")})
    doses.sort(key=lambda d: (d["slot"], d["med"]["name"]))
    return doses


def adherence(med, days=7):
    """Son `days` günde alınan / planlanan doz (bugünün sadece saati geçmiş dozları sayılır)."""
    slots = med_slots(med)
    if not slots:
        return None
    now = now_local()
    t = now.date()
    start = max(t - timedelta(days=days - 1), datetime.fromisoformat(med["created_at"][:10]).date())
    planned = sum(len(slots) for _ in range((t - start).days)) + sum(1 for s in slots if s <= now.strftime("%H:%M"))
    taken = query_one("SELECT COUNT(*) AS n FROM med_logs WHERE med_id = ? AND taken_at IS NOT NULL AND date >= ?",
                      (med["id"], start.isoformat()))["n"]
    return (min(taken, planned), planned) if planned else None
