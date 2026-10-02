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
        " WHERE i.done = 0 AND i.due_date IS NOT NULL AND i.due_date <= ? AND (l.user_id = ? OR l.shared = 1)"
        " AND (i.assignee_id IS NULL OR i.assignee_id = ?)",
        (soon, user_id, user_id),
    ):
        detail = r["list_name"] + (f" · {r['due_time']}" if r["due_time"] else "")
        add("☑️", r["text"], r["due_date"], "lists.detail", detail=detail, list_id=r["lid"])

    from .modules.kanban import open_cards
    for r in open_cards(user_id, " AND c.due_date <= ?", (soon,)):
        add("🗂️", r["title"], r["due_date"], "kanban.board", detail=f"{r['board_name']} · {r['column_name']}",
            board_id=r["board_id"], _anchor=f"kart-{r['id']}")

    from .modules.documents import KINDS as DOC_KINDS
    for r in query("SELECT * FROM documents WHERE user_id = ? AND expires_on BETWEEN ? AND ?",
                   (user_id, (t - timedelta(days=30)).isoformat(), later)):
        add(DOC_KINDS[r["kind"]][1], f"{r['name']} bitiyor", r["expires_on"], "documents.edit", detail=r["holder"],
            doc_id=r["id"])

    # Ev bakımı: gecikenler ve kısa vadede (işin hatırlatma süresi daha uzunsa o kadar önceden) gelenler
    from .modules.homecare import task_period
    for r in query("SELECT * FROM home_tasks WHERE user_id = ? AND active = 1 AND next_due <= ?", (user_id, later)):
        if (days_until(r["next_due"]) or 0) <= max(days, r["remind_days"]):
            add(r["icon"], r["name"], r["next_due"], "homecare.detail", detail=task_period(r).lower(), task_id=r["id"])

    # Siparişler: beklenen teslim (gecikenler 30 gün görünür) ve iade son günü
    for r in query("SELECT * FROM orders WHERE user_id = ? AND status IN ('ordered', 'shipped')"
                   " AND expected_on BETWEEN ? AND ?", (user_id, (t - timedelta(days=30)).isoformat(), soon)):
        add("🚚", f"{r['item']} bekleniyor", r["expected_on"], "orders.edit", detail=r["store"], order_id=r["id"])
    for r in query("SELECT * FROM orders WHERE user_id = ? AND status = 'delivered' AND return_by BETWEEN ? AND ?",
                   (user_id, ts, soon)):
        add("↩️", f"{r['item']} iade son günü", r["return_by"], "orders.edit", detail=r["store"], overdue_ok=False,
            order_id=r["id"])

    from .modules.events import visible_events
    for e in visible_events(user_id, " AND e.date BETWEEN ? AND ?", (ts, soon)):
        detail = " · ".join(x for x in (e["time"] or "", e["place"]) if x)
        add("👨‍👩‍👧" if e["shared"] else "📅", e["title"], e["date"], "events.edit", detail=detail, overdue_ok=False,
            event_id=e["id"])

    from .modules.specialdays import KINDS, upcoming_rows
    for r, nxt, _days, ordinal in upcoming_rows(user_id, within_days=long_days):
        add(KINDS[r["kind"]][1], r["name"], nxt.isoformat(), "specialdays.index", detail=ordinal, overdue_ok=False)

    from .modules.contacts import birthday_title, next_birthday
    for r in query("SELECT * FROM contacts WHERE user_id = ? AND birthday IS NOT NULL", (user_id,)):
        nxt = next_birthday(r, t)
        if nxt and (nxt - t).days <= long_days:
            add("🎂", birthday_title(r, nxt), nxt.isoformat(), "contacts.detail", detail=r["relation"],
                overdue_ok=False, contact_id=r["id"])

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
