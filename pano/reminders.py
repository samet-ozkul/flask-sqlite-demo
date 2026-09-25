"""Yaklaşan tarihler: pano ve Telegram günlük özeti aynı listeyi kullanır."""
from datetime import timedelta

from flask import url_for

from .db import get_db, query
from .utils import add_cycle, add_months, days_until, fmt_money, parse_date, today


def advance_subscriptions(user_id=None):
    """Tarihi geçmiş aktif aboneliklerin next_date'ini döngüye göre ileri alır."""
    t = today()
    sql = "SELECT id, next_date, cycle FROM subscriptions WHERE active = 1 AND next_date < ?"
    args = [t.isoformat()]
    if user_id is not None:
        sql += " AND user_id = ?"
        args.append(user_id)
    db = get_db()
    for row in db.execute(sql, args).fetchall():
        d = parse_date(row["next_date"])
        if d is None:
            continue
        while d < t:
            d = add_cycle(d, row["cycle"])
        db.execute("UPDATE subscriptions SET next_date = ? WHERE id = ?", (d.isoformat(), row["id"]))
    db.commit()


def next_bill_date(due_date):
    """Tekrarlayan fatura ödenince bir sonraki ayın tarihi."""
    return add_months(parse_date(due_date), 1).isoformat()


def upcoming(user_id, days=7, long_days=30):
    """Yaklaşan/geciken işler. days: kısa vadeli (fatura, abonelik, randevu), long_days: araç/garanti."""
    advance_subscriptions(user_id)
    t = today()
    soon = (t + timedelta(days=days)).isoformat()
    later = (t + timedelta(days=long_days)).isoformat()
    ts = t.isoformat()
    items = []

    def add(icon, title, when, endpoint, detail="", overdue_ok=True, **kw):
        n = days_until(when)
        if n is None or (n < 0 and not overdue_ok):
            return
        items.append({"icon": icon, "title": title, "date": when[:10], "days": n, "detail": detail,
                      "url": url_for(endpoint, **kw)})

    for r in query("SELECT * FROM bills WHERE user_id = ? AND paid = 0 AND due_date <= ?", (user_id, soon)):
        add("🧾", r["name"], r["due_date"], "bills.index", detail=_money(r["amount"], "TRY"))

    for r in query("SELECT * FROM subscriptions WHERE user_id = ? AND active = 1 AND next_date <= ?",
                   (user_id, soon)):
        add("🔁", f"{r['name']} yenileniyor", r["next_date"], "subscriptions.index",
            detail=_money(r["amount"], r["currency"]))

    for r in query("SELECT * FROM debts WHERE user_id = ? AND settled = 0 AND due_date IS NOT NULL"
                   " AND due_date <= ?", (user_id, soon)):
        label = f"{r['person']} ödeyecek" if r["direction"] == "lent" else f"{r['person']} kişisine ödeme"
        add("🤝", label, r["due_date"], "debts.index", detail=_money(r["amount"], r["currency"]))

    vehicle_fields = [("inspection_date", "muayene"), ("insurance_date", "trafik sigortası"),
                      ("casco_date", "kasko"), ("service_date", "bakım")]
    for r in query("SELECT * FROM vehicles WHERE user_id = ?", (user_id,)):
        for field, label in vehicle_fields:
            if r[field] and r[field] <= later:
                add("🚗", f"{r['name']} {label}", r[field], "car.detail", detail=r["plate"], vehicle_id=r["id"])

    for r in query("SELECT * FROM warranties WHERE user_id = ? AND warranty_until BETWEEN ? AND ?",
                   (user_id, ts, later)):
        add("🛡️", f"{r['product']} garantisi bitiyor", r["warranty_until"], "warranty.index", overdue_ok=False)

    for r in query("SELECT * FROM appointments WHERE user_id = ? AND done = 0 AND substr(starts_at, 1, 10)"
                   " BETWEEN ? AND ?", (user_id, ts, soon)):
        add("🩺", r["title"], r["starts_at"], "health.index", detail=_time(r["starts_at"]) +
            (f" · {r['place']}" if r["place"] else ""), overdue_ok=False)

    for r in query(
        "SELECT i.*, l.name AS list_name, l.id AS lid FROM list_items i JOIN lists l ON l.id = i.list_id"
        " WHERE i.done = 0 AND i.due_date IS NOT NULL AND i.due_date <= ? AND (l.user_id = ? OR l.shared = 1)",
        (soon, user_id),
    ):
        detail = r["list_name"] + (f" · {r['due_time']}" if r["due_time"] else "")
        add("☑️", r["text"], r["due_date"], "lists.detail", detail=detail, list_id=r["lid"])

    items.sort(key=lambda x: (x["date"], x["title"]))
    return items


def medications_today(user_id):
    """[(saat, ilaç satırı)] saat sırasına göre."""
    out = []
    for r in query("SELECT * FROM medications WHERE user_id = ? AND active = 1", (user_id,)):
        times = [t.strip() for t in r["times"].replace(";", ",").split(",") if t.strip()] or ["—"]
        out.extend((t, r) for t in times)
    out.sort(key=lambda x: x[0])
    return out


def _money(amount, currency):
    return fmt_money(amount, currency) if amount is not None else ""


def _time(starts_at):
    return starts_at[11:16] if len(starts_at) >= 16 else ""
