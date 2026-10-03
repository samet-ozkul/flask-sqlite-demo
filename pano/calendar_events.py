"""Tüm tarihli kayıtlar tek listede: takvim sayfası ve telefon takvimi aboneliği (ICS) kullanır.

Kaynaklar: faturalar, abonelik yenilemeleri, borç/alacak vadeleri, araç tarihleri, garanti bitişleri,
randevular, yapılacaklar (açık), kanban kartları (bitti sütunu hariç), ev bakımı (sıradaki tarih), biletler,
önemli günler, kişilerin doğum günleri.
Abonelik, önemli günler ve doğum günleri aralık içinde çoğaltılır.
"""
from datetime import date, timedelta

from flask import url_for

from .db import query
from .utils import add_cycle, fmt_money

VEHICLE_FIELDS = [("inspection_date", "muayene"), ("insurance_date", "trafik sigortası"),
                  ("casco_date", "kasko"), ("service_date", "bakım")]


def _d(value):
    return date.fromisoformat(value[:10])


def events_between(user_id, start, end, external=False):
    """[{date, time, title, icon, kind, detail, url, uid, done}] start <= tarih <= end, tarihe göre sıralı.
    external=True ise url'ler tam adres (ICS için)."""
    s, e = start.isoformat(), end.isoformat()
    out = []

    def add(kind, key, on, title, icon, endpoint, detail="", time=None, done=False, **values):
        out.append({
            "date": on if isinstance(on, date) else _d(on), "time": time, "title": title, "icon": icon,
            "kind": kind, "detail": detail, "done": done,
            "url": url_for(endpoint, _external=external, **values),
            "uid": f"{kind}-{key}-{on if isinstance(on, str) else on.isoformat()}"[:120],
        })

    for r in query("SELECT * FROM bills WHERE user_id = ? AND due_date BETWEEN ? AND ?", (user_id, s, e)):
        add("bill", r["id"], r["due_date"], f"{r['name']} son gün", "🧾", "bills.edit",
            detail=fmt_money(r["amount"]) if r["amount"] else "", done=bool(r["paid"]), bill_id=r["id"])

    # Abonelikler: next_date'ten ileri ve geri döngüyle aralığa düşen yenilemeler
    for r in query("SELECT * FROM subscriptions WHERE user_id = ? AND active = 1", (user_id,)):
        anchor = _d(r["next_date"])
        d = anchor
        while d > start:  # geriye doğru aralığın başına kadar
            prev = _back(d, r["cycle"])
            if prev < start:
                break
            d = prev
        guard = 0
        while d <= end and guard < 400:
            if d >= start:
                add("sub", r["id"], d, f"{r['name']} yenileniyor", "🔁", "subscriptions.edit",
                    detail=fmt_money(r["amount"], r["currency"]), sub_id=r["id"])
            d = add_cycle(d, r["cycle"])
            guard += 1

    for r in query("SELECT * FROM debts WHERE user_id = ? AND settled = 0 AND due_date BETWEEN ? AND ?", (user_id, s, e)):
        title = f"{r['person']} ödeyecek" if r["direction"] == "lent" else f"{r['person']} kişisine ödeme"
        add("debt", r["id"], r["due_date"], title, "🤝", "debts.edit", detail=fmt_money(r["amount"], r["currency"]),
            debt_id=r["id"])

    for r in query("SELECT * FROM vehicles WHERE user_id = ?", (user_id,)):
        for field, label in VEHICLE_FIELDS:
            if r[field] and s <= r[field][:10] <= e:
                add(f"car-{field}", r["id"], r[field], f"{r['name']} {label}", "🚗", "car.detail",
                    detail=r["plate"], vehicle_id=r["id"])

    for r in query("SELECT * FROM warranties WHERE user_id = ? AND warranty_until BETWEEN ? AND ?", (user_id, s, e)):
        add("warranty", r["id"], r["warranty_until"], f"{r['product']} garantisi bitiyor", "🛡️", "warranty.detail",
            warranty_id=r["id"])

    for r in query("SELECT * FROM appointments WHERE user_id = ? AND substr(starts_at, 1, 10) BETWEEN ? AND ?",
                   (user_id, s, e)):
        add("appt", r["id"], r["starts_at"][:10], r["title"], "🩺", "health.appt_edit",
            detail=r["place"], time=r["starts_at"][11:16] or None, done=bool(r["done"]), appt_id=r["id"])

    for r in query(
        "SELECT i.*, l.name AS list_name FROM list_items i JOIN lists l ON l.id = i.list_id"
        " WHERE i.done = 0 AND i.due_date BETWEEN ? AND ? AND (l.user_id = ? OR l.shared = 1)", (s, e, user_id)
    ):
        add("todo", r["id"], r["due_date"], r["text"], "☑️", "lists.detail", detail=r["list_name"],
            time=r["due_time"], list_id=r["list_id"])

    from .modules.kanban import open_cards
    for r in open_cards(user_id, " AND c.due_date BETWEEN ? AND ?", (s, e)):
        add("kanban", r["id"], r["due_date"], r["title"], "🗂️", "kanban.board",
            detail=f"{r['board_name']} · {r['column_name']}", board_id=r["board_id"], _anchor=f"kart-{r['id']}")

    from .modules.documents import KINDS as DOC_KINDS
    for r in query("SELECT * FROM documents WHERE user_id = ? AND expires_on BETWEEN ? AND ?", (user_id, s, e)):
        add("document", r["id"], r["expires_on"], f"{r['name']} bitiyor", DOC_KINDS[r["kind"]][1], "documents.edit",
            detail=r["holder"], doc_id=r["id"])

    # Ev bakımı: etkin işlerin sıradaki tarihi
    from .modules.homecare import task_period
    for r in query("SELECT * FROM home_tasks WHERE user_id = ? AND active = 1 AND next_due BETWEEN ? AND ?",
                   (user_id, s, e)):
        add("home", r["id"], r["next_due"], r["name"], r["icon"], "homecare.detail", detail=task_period(r),
            task_id=r["id"])

    # Siparişler: beklenen teslim (iptal edilenler hariç) ve iade son günü (iade başlatılınca tamamlandı sayılır)
    for r in query("SELECT * FROM orders WHERE user_id = ? AND status != 'cancelled' AND expected_on BETWEEN ? AND ?",
                   (user_id, s, e)):
        add("order", r["id"], r["expected_on"], f"{r['item']} bekleniyor", "🚚", "orders.edit", detail=r["store"],
            done=r["status"] not in ("ordered", "shipped"), order_id=r["id"])
    for r in query("SELECT * FROM orders WHERE user_id = ? AND status IN ('delivered', 'returning', 'returned')"
                   " AND return_by BETWEEN ? AND ?", (user_id, s, e)):
        add("order-return", r["id"], r["return_by"], f"{r['item']} iade son günü", "↩️", "orders.edit",
            detail=r["store"], done=r["status"] != "delivered", order_id=r["id"])

    from .modules.events import visible_events
    for r in visible_events(user_id, " AND e.date BETWEEN ? AND ?", (s, e)):
        add("event", r["id"], r["date"], r["title"], "👨‍👩‍👧" if r["shared"] else "📅", "events.edit",
            detail=r["place"], time=r["time"], event_id=r["id"])

    # Biletler: etkinlik / kalkış saatiyle (elle arşivlenenler tamamlandı sayılır)
    from .modules.tickets import kind_icon
    for r in query("SELECT * FROM tickets WHERE user_id = ? AND starts_on BETWEEN ? AND ?", (user_id, s, e)):
        add("ticket", r["id"], r["starts_on"], r["title"], kind_icon(r["kind"]), "tickets.detail",
            detail=" · ".join(x for x in (r["venue"], r["seat"]) if x), time=r["starts_at"], done=bool(r["archived"]),
            ticket_id=r["id"])

    from .modules.specialdays import KINDS, occurrence, ordinal_text
    for r in query("SELECT * FROM special_days WHERE user_id = ?", (user_id,)):
        for year in range(start.year, end.year + 1):
            on = occurrence(r["month"], r["day"], year)
            if start <= on <= end:
                add("day", r["id"], on, r["name"], KINDS[r["kind"]][1], "specialdays.edit",
                    detail=ordinal_text(r, on), day_id=r["id"])

    from .modules.contacts import birthday_on, birthday_title
    for r in query("SELECT * FROM contacts WHERE user_id = ? AND birthday IS NOT NULL", (user_id,)):
        for year in range(start.year, end.year + 1):
            on = birthday_on(r, year)
            if on and start <= on <= end:
                add("contact-bday", r["id"], on, birthday_title(r, on), "🎂", "contacts.detail",
                    detail=r["relation"], contact_id=r["id"])

    out.sort(key=lambda x: (x["date"], x["time"] or "99:99", x["title"]))
    return out


def _back(d, cycle):
    from .utils import add_months
    if cycle == "weekly":
        return d - timedelta(days=7)
    if cycle == "yearly":
        return add_months(d, -12)
    return add_months(d, -1)


# ---------- ICS (iCalendar) ----------
def _ics_escape(text):
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line):
    """RFC 5545: satırlar 75 bayttan uzunsa bölünür (devam satırı boşlukla başlar)."""
    data = line.encode("utf-8")
    if len(data) <= 75:
        return line
    parts, current = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(current) + len(b) > (75 if not parts else 74):
            parts.append(current.decode("utf-8"))
            current = b""
        current += b
    parts.append(current.decode("utf-8"))
    return "\r\n ".join(parts)


def to_ics(events, name, tz, now_utc):
    """Olay listesini iCalendar metnine çevirir. Saatli olaylar UTC'ye çevrilir, diğerleri tüm gün."""
    from datetime import datetime, time as dtime, timezone
    stamp = now_utc.strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Kisisel Pano//TR", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{_ics_escape(name)}", "X-PUBLISHED-TTL:PT1H",
             "REFRESH-INTERVAL;VALUE=DURATION:PT1H"]
    for ev in events:
        lines += ["BEGIN:VEVENT", f"UID:{ev['uid']}@kisisel-pano", f"DTSTAMP:{stamp}"]
        if ev["time"]:
            hh, mm = ev["time"].split(":")
            local = datetime.combine(ev["date"], dtime(int(hh), int(mm)), tzinfo=tz)
            begin = local.astimezone(timezone.utc)
            finish = begin + (timedelta(hours=1) if ev["kind"] == "appt" else timedelta(minutes=15))
            lines += [f"DTSTART:{begin.strftime('%Y%m%dT%H%M%SZ')}", f"DTEND:{finish.strftime('%Y%m%dT%H%M%SZ')}"]
        else:
            lines += [f"DTSTART;VALUE=DATE:{ev['date'].strftime('%Y%m%d')}",
                      f"DTEND;VALUE=DATE:{(ev['date'] + timedelta(days=1)).strftime('%Y%m%d')}"]
        summary = f"{ev['icon']} {ev['title']}" + (" ✓" if ev["done"] else "")
        lines.append(f"SUMMARY:{_ics_escape(summary)}")
        if ev["detail"]:
            lines.append(f"DESCRIPTION:{_ics_escape(ev['detail'])}")
        lines += [f"URL:{ev['url']}", "TRANSP:TRANSPARENT", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
