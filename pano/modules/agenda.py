"""📅 Takvim: tüm tarihli kayıtlar ay görünümünde + telefon takvimine abonelik (ICS).

/takvim?ay=YYYY-MM           -> ay ızgarası ve gün gün liste
/takvim/<gizli-anahtar>.ics  -> Google/Apple Takvim'in abone olduğu adres (giriş gerektirmez;
                                anahtar Ayarlar'dan oluşturulur, yenilenince eski adres çalışmaz)
"""
import calendar as cal
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, Response, abort, g, render_template, request

from ..auth import login_required
from ..calendar_events import events_between, to_ics
from ..db import query_one
from ..utils import MONTHS_TR, TZ, WEEKDAYS_TR_SHORT, add_months, today

bp = Blueprint("calendar", __name__, url_prefix="/takvim")

ICS_PAST_DAYS = 60
ICS_FUTURE_DAYS = 365


def _parse_month(value):
    try:
        y, m = (int(p) for p in (value or "").split("-"))
        if 2000 <= y <= 2100 and 1 <= m <= 12:
            return y, m
    except ValueError:
        pass
    t = today()
    return t.year, t.month


@bp.route("/")
@login_required
def index():
    year, month = _parse_month(request.args.get("ay"))
    first = date(year, month, 1)
    last = date(year, month, cal.monthrange(year, month)[1])
    events = events_between(g.user["id"], first, last)
    by_day = {}
    for ev in events:
        by_day.setdefault(ev["date"], []).append(ev)
    # Pazartesiden başlayan haftalar; ay dışındaki günler None
    weeks = [[d if d.month == month else None for d in week]
             for week in cal.Calendar(firstweekday=0).monthdatescalendar(year, month)]
    prev, nxt = add_months(first, -1), add_months(first, 1)
    return render_template(
        "calendar/index.html", weeks=weeks, by_day=by_day, year=year, month=month, today=today(),
        month_name=MONTHS_TR[month - 1], prev=prev, nxt=nxt, weekdays=WEEKDAYS_TR_SHORT, months=MONTHS_TR,
        count=len(events),
    )


@bp.route("/<token>.ics")
def feed(token):
    if len(token) < 20:
        abort(404)
    user = query_one("SELECT * FROM users WHERE calendar_token = ?", (token,))
    if user is None:
        abort(404)
    t = today()
    events = events_between(user["id"], t - timedelta(days=ICS_PAST_DAYS), t + timedelta(days=ICS_FUTURE_DAYS),
                            external=True)
    body = to_ics(events, "Kişisel Pano", TZ, datetime.now(timezone.utc))
    resp = Response(body, mimetype="text/calendar")
    resp.headers["Content-Disposition"] = 'inline; filename="pano.ics"'
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp
