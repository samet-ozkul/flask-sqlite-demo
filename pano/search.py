"""Genel arama: tek kutudan tüm modüllerde.

Türkçe harf ve büyük/küçük harf duyarsız ("sarj" -> "Şarj aleti"); birden çok kelime yazılırsa
hepsinin geçtiği kayıtlar bulunur. SQLite LIKE Türkçe harflerde büyük/küçük ayırdığı için
eşleştirme Python'da yapılır; kişisel veride tablo başına son 1000 kayda bakmak yeterince hızlı.
"""
from flask import url_for

from .db import query
from .utils import fmt_date, fmt_money, fold, parse_date

ROW_LIMIT = 1000


def _mood(n):
    from .modules.journal import MOODS
    return MOODS.get(n, ("", ""))[0]


def _order_detail(r):
    from .modules.orders import STATUSES
    return " · ".join(x for x in (r["store"], STATUSES[r["status"]][0],
                                  fmt_money(r["amount"]) if r["amount"] is not None else "") if x)


def _home_period(r):
    from .modules.homecare import task_period
    return task_period(r).lower()


def _map_title(r):
    from .modules.places import CATEGORIES
    if r["kind"] == "city":
        return "🏙️ " + r["name"] + (f", {r['extra']}" if r["extra"] else "")
    return f"{CATEGORIES[r['category']][1]} {r['name']}"


def _map_url(r):
    if r["kind"] == "city":
        return url_for("places.city", city_id=r["id"])
    return url_for("places.edit", place_id=r["id"])


def _week_title(r):
    from .modules.weekly import SCORES, week_label
    return f"{week_label(parse_date(r['week_start']))} haftası" + (f" {SCORES[r['score']][0]}" if r["score"] else "")


def _week_detail(r):
    from .modules.weekly import parse_priorities
    priorities = ", ".join(p["text"] for p in parse_priorities(r["priorities"]))
    return " · ".join(x for x in (r["went_well"], r["hard"], r["learned"], priorities) if x)


def _sources(user_id):
    """(etiket, ikon, sorgu, parametreler, aranan alanlar, başlık, ayrıntı, adres)"""
    from .modules.kanban import CARD_SELECT
    return [
        ("Notlar", "📝", "SELECT * FROM notes WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id,),
         ("title", "content", "tags"), lambda r: r["title"] or r["content"][:60], lambda r: r["content"],
         lambda r: url_for("notes.edit", note_id=r["id"])),
        ("Listeler", "🛒",
         "SELECT i.*, l.name AS list_name FROM list_items i JOIN lists l ON l.id = i.list_id"
         " WHERE (l.user_id = ? OR l.shared = 1) ORDER BY i.done, i.id DESC LIMIT ?", (user_id,),
         ("text", "qty", "list_name"), lambda r: r["text"] + (" ✓" if r["done"] else ""), lambda r: r["list_name"],
         lambda r: url_for("lists.detail", list_id=r["list_id"])),
        ("Sonra Bak", "🔖", "SELECT * FROM links WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id,),
         ("title", "url", "note", "tags"), lambda r: r["title"], lambda r: r["url"],
         lambda r: url_for("links.edit", link_id=r["id"])),
        ("Kısa Link", "🔗", "SELECT * FROM short_links WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id,),
         ("title", "code", "target"), lambda r: r["title"] or "/k/" + r["code"],
         lambda r: f"/k/{r['code']} → {r['target']}", lambda r: url_for("shortlinks.detail", link_id=r["id"])),
        ("Ev Envanteri", "📦", "SELECT * FROM inventory WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id,),
         ("name", "location", "category", "note"), lambda r: r["name"],
         lambda r: "📍 " + r["location"] if r["location"] else r["category"],
         lambda r: url_for("inventory.edit", item_id=r["id"])),
        ("Garanti", "🛡️", "SELECT * FROM warranties WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id,),
         ("product", "brand", "store", "serial_no", "note"), lambda r: r["product"],
         lambda r: " · ".join(x for x in (r["brand"], r["store"],
                                          ("bitiş " + fmt_date(r["warranty_until"])) if r["warranty_until"] else "") if x),
         lambda r: url_for("warranty.detail", warranty_id=r["id"])),
        ("Tarifler", "🍲", "SELECT * FROM recipes WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id,),
         ("title", "ingredients", "tags"), lambda r: r["title"], lambda r: r["tags"],
         lambda r: url_for("recipes.detail", recipe_id=r["id"])),
        ("Araç", "🚗", "SELECT * FROM vehicles WHERE user_id = ? LIMIT ?", (user_id,),
         ("name", "plate", "note"), lambda r: r["name"], lambda r: r["plate"],
         lambda r: url_for("car.detail", vehicle_id=r["id"])),
        ("Faturalar", "🧾", "SELECT * FROM bills WHERE user_id = ? ORDER BY due_date DESC LIMIT ?", (user_id,),
         ("name", "note"), lambda r: r["name"] + (" ✓" if r["paid"] else ""),
         lambda r: fmt_date(r["due_date"]) + (" · " + fmt_money(r["amount"]) if r["amount"] else ""),
         lambda r: url_for("bills.edit", bill_id=r["id"])),
        ("Abonelikler", "🔁", "SELECT * FROM subscriptions WHERE user_id = ? LIMIT ?", (user_id,),
         ("name", "category", "note"), lambda r: r["name"], lambda r: fmt_money(r["amount"], r["currency"]),
         lambda r: url_for("subscriptions.edit", sub_id=r["id"])),
        ("Borç / Alacak", "🤝", "SELECT * FROM debts WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id,),
         ("person", "note"), lambda r: r["person"] + (" ✓" if r["settled"] else ""),
         lambda r: fmt_money(r["amount"], r["currency"]), lambda r: url_for("debts.edit", debt_id=r["id"])),
        ("Harcamalar", "💸", "SELECT * FROM expenses WHERE user_id = ? ORDER BY date DESC, id DESC LIMIT ?", (user_id,),
         ("note", "category"), lambda r: r["note"] or r["category"],
         lambda r: fmt_date(r["date"]) + " · " + fmt_money(r["amount"]),
         lambda r: url_for("expenses.edit", expense_id=r["id"])),
        ("Siparişler", "🚚", "SELECT * FROM orders WHERE user_id = ? ORDER BY ordered_on DESC, id DESC LIMIT ?",
         (user_id,), ("store", "item", "tracking_no", "note"), lambda r: r["item"], _order_detail,
         lambda r: url_for("orders.edit", order_id=r["id"])),
        ("Sağlık", "🩺", "SELECT * FROM appointments WHERE user_id = ? ORDER BY starts_at DESC LIMIT ?", (user_id,),
         ("title", "place", "note"), lambda r: r["title"], lambda r: fmt_date(r["starts_at"]) + " " + r["starts_at"][11:16],
         lambda r: url_for("health.appt_edit", appt_id=r["id"])),
        ("İlaçlar", "💊", "SELECT * FROM medications WHERE user_id = ? LIMIT ?", (user_id,),
         ("name", "dose", "note"), lambda r: r["name"], lambda r: r["times"],
         lambda r: url_for("health.med_edit", med_id=r["id"])),
        ("Etkinlikler", "📅", "SELECT * FROM events WHERE (user_id = ? OR shared = 1) ORDER BY date DESC LIMIT ?",
         (user_id,), ("title", "place", "note"), lambda r: r["title"],
         lambda r: fmt_date(r["date"]) + (" " + r["time"] if r["time"] else ""),
         lambda r: url_for("events.edit", event_id=r["id"])),
        ("Belgeler", "🪪", "SELECT * FROM documents WHERE user_id = ? ORDER BY expires_on LIMIT ?", (user_id,),
         ("name", "holder", "note"), lambda r: r["name"],
         lambda r: " · ".join(x for x in (r["holder"], "bitiş " + fmt_date(r["expires_on"])) if x),
         lambda r: url_for("documents.edit", doc_id=r["id"])),
        ("Ev Bakımı", "🔧", "SELECT * FROM home_tasks WHERE user_id = ? ORDER BY next_due LIMIT ?", (user_id,),
         ("name", "notes"), lambda r: r["name"] + ("" if r["active"] else " (pasif)"),
         lambda r: "sıradaki " + fmt_date(r["next_due"]) + " · " + _home_period(r),
         lambda r: url_for("homecare.detail", task_id=r["id"])),
        ("İzleme / Okuma", "🎬", "SELECT * FROM watchlist WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id,),
         ("title", "creator", "note", "year"), lambda r: r["title"] + (" ✓" if r["status"] == "done" else ""),
         lambda r: " · ".join(x for x in (r["year"], r["creator"]) if x),
         lambda r: url_for("watchlist.edit", item_id=r["id"])),
        # Şehirler (ad, ülke, not) ve yerler (ad, adres, not, bağlı olduğu şehir) tek grupta
        ("Harita", "🗺️",
         "SELECT 'city' AS kind, id, name, country AS extra, note, '' AS city_name, '' AS category FROM cities"
         " WHERE user_id = ? UNION ALL"
         " SELECT 'place', p.id, p.name, p.address, p.note, COALESCE(c.name, ''), p.category FROM places p"
         " LEFT JOIN cities c ON c.id = p.city_id WHERE p.user_id = ? ORDER BY 1, 2 DESC LIMIT ?", (user_id, user_id),
         ("name", "extra", "note", "city_name"), _map_title,
         lambda r: " · ".join(x for x in (r["city_name"], r["extra"] if r["kind"] == "place" else "", r["note"]) if x),
         _map_url),
        ("Günlük", "📓", "SELECT * FROM journal WHERE user_id = ? ORDER BY date DESC LIMIT ?", (user_id,),
         ("text",), lambda r: fmt_date(r["date"], True) + (" " + _mood(r["mood"]) if r["mood"] else ""),
         lambda r: r["text"], lambda r: url_for("journal.index", ay=r["date"][:7], gun=r["date"])),
        # Öncelikler JSON metniyle aranır (ensure_ascii=False: Türkçe harfler olduğu gibi)
        ("Haftalık Değerlendirme", "🗓️", "SELECT * FROM weekly_reviews WHERE user_id = ? ORDER BY week_start DESC LIMIT ?",
         (user_id,), ("went_well", "hard", "learned", "priorities"), _week_title, _week_detail,
         lambda r: url_for("weekly.index", hafta=r["week_start"])),
        ("Önemli Günler", "🎂", "SELECT * FROM special_days WHERE user_id = ? LIMIT ?", (user_id,),
         ("name", "note"), lambda r: r["name"], lambda r: f"{r['day']:02d}.{r['month']:02d}",
         lambda r: url_for("specialdays.edit", day_id=r["id"])),
        # Telefon boşluksuz da aranabilsin ("05321234567" ~ "0532 123 45 67")
        ("Kişiler", "📇",
         "SELECT *, replace(replace(replace(replace(phone, ' ', ''), '-', ''), '(', ''), ')', '') AS phone_digits"
         " FROM contacts WHERE user_id = ? ORDER BY name LIMIT ?", (user_id,),
         ("name", "relation", "phone", "phone_digits", "email", "note"), lambda r: r["name"],
         lambda r: " · ".join(x for x in (r["relation"], r["phone"], r["email"]) if x),
         lambda r: url_for("contacts.detail", contact_id=r["id"])),
        ("Kanban", "🗂️", CARD_SELECT + " WHERE (b.user_id = ? OR b.shared = 1) ORDER BY c.id DESC LIMIT ?", (user_id,),
         ("title", "note", "board_name"), lambda r: r["title"] + (" ✓" if r["done"] else ""),
         lambda r: f"{r['board_name']} · {r['column_name']}", lambda r: url_for("kanban.card", card_id=r["id"])),
    ]


def snippet(text, terms, width=90):
    """Metinde ilk eşleşmenin çevresinden kısa bir parça."""
    text = " ".join((text or "").split())
    if len(text) <= width:
        return text
    folded = fold(text)
    pos = min((folded.find(t) for t in terms if folded.find(t) >= 0), default=0)
    start = max(0, pos - width // 3)
    part = text[start:start + width]
    return ("…" if start else "") + part + ("…" if start + width < len(text) else "")


def search(user_id, q, per_group=8):
    """[{label, icon, count, results: [{title, detail, url}]}] — sonuç olmayan gruplar dahil edilmez."""
    terms = [t for t in fold(q).split() if t]
    if not terms:
        return []
    groups = []
    for label, icon, sql, params, fields, title, detail, url in _sources(user_id):
        hits = []
        for r in query(sql, (*params, ROW_LIMIT)):
            haystack = fold(" ".join(str(r[f] or "") for f in fields))
            if all(t in haystack for t in terms):
                hits.append(r)
        if hits:
            groups.append({
                "label": label, "icon": icon, "count": len(hits),
                "results": [{"title": title(r), "detail": snippet(detail(r), terms), "url": url(r)}
                          for r in hits[:per_group]],
            })
    return groups
