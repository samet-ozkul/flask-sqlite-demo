"""Genel arama: tek kutudan tüm modüllerde.

Türkçe harf ve büyük/küçük harf duyarsız ("sarj" -> "Şarj aleti"); birden çok kelime yazılırsa
hepsinin geçtiği kayıtlar bulunur. SQLite LIKE Türkçe harflerde büyük/küçük ayırdığı için
eşleştirme Python'da yapılır; kişisel veride tablo başına son 1000 kayda bakmak yeterince hızlı.
"""
from flask import url_for

from .db import query
from .utils import fmt_date, fmt_money, fold

ROW_LIMIT = 1000


def _sources(user_id):
    """(etiket, ikon, sorgu, parametreler, aranan alanlar, başlık, ayrıntı, adres)"""
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
        ("İzleme / Okuma", "🎬", "SELECT * FROM watchlist WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id,),
         ("title", "creator", "note", "year"), lambda r: r["title"] + (" ✓" if r["status"] == "done" else ""),
         lambda r: " · ".join(x for x in (r["year"], r["creator"]) if x),
         lambda r: url_for("watchlist.edit", item_id=r["id"])),
        ("Önemli Günler", "🎂", "SELECT * FROM special_days WHERE user_id = ? LIMIT ?", (user_id,),
         ("name", "note"), lambda r: r["name"], lambda r: f"{r['day']:02d}.{r['month']:02d}",
         lambda r: url_for("specialdays.edit", day_id=r["id"])),
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
