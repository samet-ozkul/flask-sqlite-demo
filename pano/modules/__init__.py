"""Modül kaydı: menü, pano ızgarası ve blueprint listesi buradan beslenir."""
from importlib import import_module

# (python modülü, endpoint, başlık, ikon, grup)
MODULES = [
    ("agenda", "calendar.index", "Takvim", "📅", "Genel"),
    ("events", "events.index", "Etkinlikler", "👨‍👩‍👧", "Genel"),
    ("notes", "notes.index", "Notlar", "📝", "Listeler ve notlar"),
    ("lists", "lists.index", "Listeler", "🛒", "Listeler ve notlar"),
    ("links", "links.index", "Sonra Bak", "🔖", "Listeler ve notlar"),
    ("vault", "vault.index", "Şifreli Kasa", "🔐", "Listeler ve notlar"),
    ("expenses", "expenses.index", "Harcamalar", "💸", "Para"),
    ("bills", "bills.index", "Faturalar", "🧾", "Para"),
    ("subscriptions", "subscriptions.index", "Abonelikler", "🔁", "Para"),
    ("debts", "debts.index", "Borç / Alacak", "🤝", "Para"),
    ("rates", "rates.index", "Kurlar", "💱", "Para"),
    ("assets", "assets.index", "Varlıklar", "💰", "Para"),
    ("goals", "goals.index", "Hedefler", "🏁", "Para"),
    ("splits", "splits.index", "Ortak Harcama", "👥", "Para"),
    ("car", "car.index", "Araç", "🚗", "Ev ve araç"),
    ("warranty", "warranty.index", "Garanti", "🛡️", "Ev ve araç"),
    ("inventory", "inventory.index", "Ev Envanteri", "📦", "Ev ve araç"),
    ("documents", "documents.index", "Belgeler", "🪪", "Ev ve araç"),
    ("habits", "habits.index", "Alışkanlıklar", "🔥", "Kişisel"),
    ("journal", "journal.index", "Günlük", "📓", "Kişisel"),
    ("health", "health.index", "Sağlık", "🩺", "Kişisel"),
    ("recipes", "recipes.index", "Tarifler", "🍲", "Kişisel"),
    ("watchlist", "watchlist.index", "İzleme / Okuma", "🎬", "Kişisel"),
    ("specialdays", "specialdays.index", "Önemli Günler", "🎂", "Kişisel"),
]

# Menüde gösterilmeyen altyapı modülleri
CORE = ["dashboard", "settings", "admin", "cron", "bot", "search", "trashbin"]


def module_groups():
    groups = {}
    for _mod, endpoint, title, icon, group in MODULES:
        groups.setdefault(group, []).append({"endpoint": endpoint, "title": title, "icon": icon})
    return groups


def register_blueprints(app):
    for name in CORE + [m[0] for m in MODULES]:
        module = import_module(f"{__name__}.{name}")
        app.register_blueprint(module.bp)
