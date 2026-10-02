"""Modül kaydı: menü, pano ızgarası ve blueprint listesi buradan beslenir."""
from importlib import import_module

# (python modülü, endpoint, başlık, ikon, grup)
MODULES = [
    ("agenda", "calendar.index", "Takvim", "📅", "Genel"),
    ("events", "events.index", "Etkinlikler", "👨‍👩‍👧", "Genel"),
    ("automations", "automations.index", "Otomasyon", "⚙️", "Genel"),
    ("transfer", "transfer.index", "Aktar", "📤", "Genel"),
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
    ("orders", "orders.index", "Siparişler", "🚚", "Para"),
    ("car", "car.index", "Araç", "🚗", "Ev ve araç"),
    ("warranty", "warranty.index", "Garanti", "🛡️", "Ev ve araç"),
    ("inventory", "inventory.index", "Ev Envanteri", "📦", "Ev ve araç"),
    ("documents", "documents.index", "Belgeler", "🪪", "Ev ve araç"),
    ("habits", "habits.index", "Alışkanlıklar", "🔥", "Kişisel"),
    ("journal", "journal.index", "Günlük", "📓", "Kişisel"),
    ("weekly", "weekly.index", "Haftalık Değerlendirme", "🗓️", "Kişisel"),
    ("health", "health.index", "Sağlık", "🩺", "Kişisel"),
    ("recipes", "recipes.index", "Tarifler", "🍲", "Kişisel"),
    ("watchlist", "watchlist.index", "İzleme / Okuma", "🎬", "Kişisel"),
    ("places", "places.index", "Harita", "🗺️", "Kişisel"),
    ("specialdays", "specialdays.index", "Önemli Günler", "🎂", "Kişisel"),
    ("contacts", "contacts.index", "Kişiler", "📇", "Kişisel"),
    ("timetrack", "timetrack.index", "Zaman Takibi", "⏱️", "Araçlar"),
    ("calculators", "calculators.index", "Hesaplayıcılar", "🧮", "Araçlar"),
    ("kanban", "kanban.index", "Kanban", "🗂️", "Araçlar"),
    ("profile", "profile.edit", "Profil Sayfası", "🌐", "Araçlar"),
    ("scanner", "scanner.index", "Belge Tara", "📄", "Araçlar"),
    ("emergency", "emergency.index", "Acil Durum Kartı", "🆘", "Araçlar"),
    ("yearreview", "yearreview.index", "Yıl Özeti", "📊", "Araçlar"),
    ("shortlinks", "shortlinks.index", "Kısa Link & QR", "🔗", "Araçlar"),
]

# Menüde gösterilmeyen altyapı modülleri
CORE = ["dashboard", "settings", "admin", "cron", "bot", "search", "trashbin"]


GROUP_ICONS = {"Genel": "📅", "Listeler ve notlar": "📝", "Para": "💰", "Ev ve araç": "🏠", "Kişisel": "🧘",
               "Araçlar": "🧰"}

# Menü kısayolları: kullanıcı seçmediyse bunlar (üst çubukta hepsi, telefonda alt çubukta ilk 3'ü)
DEFAULT_PINS = ["lists", "notes", "expenses", "agenda"]
MAX_PINS = 6


def module_groups():
    groups = {}
    for mod, endpoint, title, icon, group in MODULES:
        groups.setdefault(group, []).append({"key": mod, "endpoint": endpoint, "title": title, "icon": icon})
    return groups


def module_by_key():
    return {m[0]: {"key": m[0], "endpoint": m[1], "title": m[2], "icon": m[3], "group": m[4]} for m in MODULES}


def user_pins(user):
    """Kullanıcının menü kısayolları (sırayla); kaydı yoksa varsayılanlar."""
    known = module_by_key()
    raw = user["nav_pins"] if user is not None else None
    keys = DEFAULT_PINS if raw is None else [k for k in raw.split(",") if k]
    return [known[k] for k in dict.fromkeys(keys) if k in known][:MAX_PINS]


def current_module(endpoint):
    """İstek hangi modülün sayfasında? (blueprint adına göre) — yoksa None."""
    if not endpoint:
        return None
    bp = endpoint.split(".")[0]
    return next((m for m in module_by_key().values() if m["endpoint"].split(".")[0] == bp), None)


def register_blueprints(app):
    for name in CORE + [m[0] for m in MODULES]:
        module = import_module(f"{__name__}.{name}")
        app.register_blueprint(module.bp)
