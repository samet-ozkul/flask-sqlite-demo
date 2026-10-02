"""🏠 Pano: günün özeti ve tüm modüllere giriş."""
from flask import Blueprint, flash, g, render_template, request, url_for

from .. import external
from ..auth import login_required
from ..db import execute, query, query_one
from ..reminders import medications_today, upcoming
from ..storage import usage
from ..utils import MONTHS_TR, WEEKDAYS_TR, month_bounds, now_local, redirect_back, today, today_str

bp = Blueprint("dashboard", __name__)


def _greeting(hour):
    if 5 <= hour < 12:
        return "Günaydın"
    if 12 <= hour < 18:
        return "İyi günler"
    if 18 <= hour < 23:
        return "İyi akşamlar"
    return "İyi geceler"


def _todos(user_id):
    """Bugün veya daha önce vadesi gelen açık yapılacaklar."""
    return query(
        "SELECT i.*, l.name AS list_name FROM list_items i JOIN lists l ON l.id = i.list_id"
        " WHERE i.done = 0 AND i.due_date IS NOT NULL AND i.due_date <= ? AND (l.user_id = ? OR l.shared = 1)"
        " AND (i.assignee_id IS NULL OR i.assignee_id = ?)"
        " ORDER BY i.due_date, i.id LIMIT 8",
        (today_str(), user_id, user_id),
    )


def _shopping(user_id):
    return query(
        "SELECT l.id, l.name, COUNT(i.id) AS open_count FROM lists l"
        " LEFT JOIN list_items i ON i.list_id = l.id AND i.done = 0"
        " WHERE l.kind = 'shopping' AND (l.user_id = ? OR l.shared = 1)"
        " GROUP BY l.id HAVING open_count > 0 ORDER BY open_count DESC LIMIT 3",
        (user_id,),
    )


# Pano kartları: (anahtar, ayarlardaki ad). Sıra ve görünürlük users.dashboard_cards'ta (virgülle ayrılmış anahtarlar).
CARDS = [
    ("weather", "☀️ Hava durumu"),
    ("rates", "💱 Kurlar"),
    ("upcoming", "📌 Yaklaşanlar"),
    ("today", "🔥 Bugün: alışkanlıklar, günlük, ilaçlar"),
    ("todos", "☑️ Yapılacaklar ve alışveriş"),
    ("money", "💰 Varlıklar ve hedefler"),
    ("quick_expense", "💸 Hızlı harcama"),
    ("quick_note", "📝 Hızlı not"),
    ("modules", "▦ Modül kısayolları"),
]
CARD_TITLES = dict(CARDS)
WIDE_CARDS = {"upcoming", "modules"}


def card_keys(user):
    """Kullanıcının seçtiği kartlar, sırasıyla. Hiç seçim yapmadıysa hepsi."""
    raw = user["dashboard_cards"]
    if raw is None:
        return [k for k, _ in CARDS]
    return list(dict.fromkeys(k for k in raw.split(",") if k in CARD_TITLES))


def _money(user_id):
    """Varlık toplamı ve aktif hedefler; ikisi de yoksa None (kart gösterilmez)."""
    from .assets import total_value
    from .goals import summary
    has_assets = query_one("SELECT 1 FROM assets WHERE user_id = ? LIMIT 1", (user_id,)) is not None
    goals = summary(user_id)[:3]
    if not has_assets and not goals:
        return None
    return {"assets": total_value(user_id) if has_assets else None, "goals": goals}


@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    now = now_local()
    t = today()
    cards = card_keys(g.user)
    data = {}

    # Sadece gösterilen kartların verisi toplanır (dış servislere gereksiz istek gitmez)
    if "weather" in cards:
        data["weather"] = external.weather(g.user["lat"], g.user["lon"])
        data["weather_label"] = external.weather_label
    if "rates" in cards:
        data["rates"] = external.rates()
        data["gold"] = external.gold_gram_try()
    if "upcoming" in cards:
        data["upcoming"] = upcoming(uid, days=7, long_days=30)[:12]
    if "today" in cards:
        from .habits import today_status
        from .journal import MOODS
        data["habits"] = today_status(uid)
        data["meds"] = medications_today(uid)
        data["moods"] = MOODS
        data["journal_today"] = query_one("SELECT * FROM journal WHERE user_id = ? AND date = ?", (uid, t.isoformat()))
    if "todos" in cards:
        data["todos"] = _todos(uid)
        data["shopping"] = _shopping(uid)
    if "money" in cards:
        data["money"] = _money(uid)
    if "quick_expense" in cards:
        from .expenses import CATEGORIES
        start, end = month_bounds(t.year, t.month)
        data["categories"] = CATEGORIES
        data["month_name"] = MONTHS_TR[t.month - 1]
        data["month_spent"] = query_one(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM expenses WHERE user_id = ? AND date >= ? AND date < ?",
            (uid, start, end),
        )["s"]
        data["pending"] = query_one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS s FROM bills WHERE user_id = ? AND paid = 0",
            (uid,),
        )

    from .yearreview import banner_year
    return render_template(
        "dashboard/index.html",
        year_review=banner_year(uid),  # 15 Aralık – 31 Ocak arası "📊 Yıl özetin hazır"
        greeting=_greeting(now.hour),
        date_label=f"{t.day} {MONTHS_TR[t.month - 1]}, {WEEKDAYS_TR[t.weekday()]}",
        cards=cards,
        wide_cards=WIDE_CARDS,
        disk=usage() if g.user["is_admin"] else None,
        **data,
    )


@bp.route("/menu")
@login_required
def menu():
    return render_template("dashboard/menu.html")


# ---------- Üst menü ----------
@bp.app_context_processor
def _nav_context():
    """Her sayfada menü verisi: kısayollar, gruplar, bulunulan modül, hızlı geçiş listesi."""
    from . import GROUP_ICONS, MAX_PINS, current_module, module_groups, user_pins
    if not getattr(g, "user", None):
        return {}
    pins = user_pins(g.user)
    groups = module_groups()
    quick = [{"t": m["title"], "i": m["icon"], "u": url_for(m["endpoint"]), "g": group}
             for group, mods in groups.items() for m in mods]
    quick += [{"t": title, "i": icon, "u": url_for(endpoint), "g": "Hesap"} for title, icon, endpoint in (
        ("Pano", "🏠", "dashboard.index"), ("Ayarlar", "⚙️", "settings.index"), ("Çöp kutusu", "🗑️", "trashbin.index"))]
    return {
        "nav_pins": pins, "nav_pin_keys": {p["key"] for p in pins}, "nav_groups": groups, "nav_group_icons": GROUP_ICONS,
        "nav_current": current_module(request.endpoint), "nav_max_pins": MAX_PINS, "nav_quickjump": quick,
    }


@bp.route("/menu/sabitle", methods=["POST"])
@login_required
def pin():
    """☆ ile kısayol ekle/kaldır (en fazla MAX_PINS; yeni eklenen sona)."""
    from . import MAX_PINS, module_by_key, user_pins
    key = request.form.get("module", "")
    if key in module_by_key():
        keys = [p["key"] for p in user_pins(g.user)]
        if key in keys:
            keys.remove(key)
        elif len(keys) >= MAX_PINS:
            flash(f"En fazla {MAX_PINS} kısayol olabilir; önce birini kaldır.", "warning")
            return redirect_back("dashboard.menu")
        else:
            keys.append(key)
        execute("UPDATE users SET nav_pins = ? WHERE id = ?", (",".join(keys), g.user["id"]))
    return redirect_back("dashboard.menu")
