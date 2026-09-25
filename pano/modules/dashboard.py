"""🏠 Pano: günün özeti ve tüm modüllere giriş."""
from flask import Blueprint, g, render_template

from .. import external
from ..auth import login_required
from ..db import query, query_one
from ..reminders import medications_today, upcoming
from ..storage import usage
from ..utils import MONTHS_TR, WEEKDAYS_TR, month_bounds, now_local, today, today_str

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
        " ORDER BY i.due_date, i.id LIMIT 8",
        (today_str(), user_id),
    )


def _shopping(user_id):
    return query(
        "SELECT l.id, l.name, COUNT(i.id) AS open_count FROM lists l"
        " LEFT JOIN list_items i ON i.list_id = l.id AND i.done = 0"
        " WHERE l.kind = 'shopping' AND (l.user_id = ? OR l.shared = 1)"
        " GROUP BY l.id HAVING open_count > 0 ORDER BY open_count DESC LIMIT 3",
        (user_id,),
    )


@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    now = now_local()
    t = today()

    weather = external.weather(g.user["lat"], g.user["lon"])
    rates = external.rates()
    gold = external.gold_gram_try()

    # Modüller arası bağımlılığı açılışta değil burada kuruyoruz
    from .expenses import CATEGORIES
    from .habits import today_status

    start, end = month_bounds(t.year, t.month)
    month_spent = query_one(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM expenses WHERE user_id = ? AND date >= ? AND date < ?",
        (uid, start, end),
    )["s"]
    pending = query_one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS s FROM bills WHERE user_id = ? AND paid = 0",
        (uid,),
    )

    disk = usage() if g.user["is_admin"] else None

    return render_template(
        "dashboard/index.html",
        greeting=_greeting(now.hour),
        date_label=f"{t.day} {MONTHS_TR[t.month - 1]}, {WEEKDAYS_TR[t.weekday()]}",
        weather=weather,
        weather_label=external.weather_label,
        rates=rates,
        gold=gold,
        upcoming=upcoming(uid, days=7, long_days=30)[:12],
        habits=today_status(uid),
        meds=medications_today(uid),
        todos=_todos(uid),
        shopping=_shopping(uid),
        categories=CATEGORIES,
        month_spent=month_spent,
        month_name=MONTHS_TR[t.month - 1],
        pending=pending,
        disk=disk,
    )


@bp.route("/menu")
@login_required
def menu():
    return render_template("dashboard/menu.html")
