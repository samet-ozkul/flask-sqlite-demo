"""🔥 Alışkanlıklar: bugünün işaretleri, seriler (streak), aylık takvim.

Kayıtlar habit_logs tablosunda (habit_id, date) çifti olarak tutulur; satır varsa o gün yapıldı.
habit_logs'un user_id'si yoktur, sahiplik her zaman bağlı olduğu alışkanlık üzerinden kontrol edilir.
"""
import calendar
import re
from collections import defaultdict
from datetime import date, timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from ..auth import login_required
from ..db import execute, owned_or_404, query, query_one
from ..utils import (MONTHS_TR, WEEKDAYS_TR_SHORT, add_months, fmt_date, form_choice, form_str, local_dt,
                     parse_date, redirect_back, today)

bp = Blueprint("habits", __name__, url_prefix="/aliskanliklar")

ICONS = ["✅", "💧", "🏃", "📚", "🧘", "🥗", "💊", "🛏️", "🚭", "✍️", "🎸", "🦷", "🚶", "🙏"]
DEFAULT_ICON = "✅"
BACKFILL_DAYS = 60   # en fazla bu kadar gün geriye işaret konabilir
RATE_DAYS = 30       # listedeki başarı yüzdesi penceresi


# ---------- Hesaplama yardımcıları ----------
def current_streak(done, t=None):
    """Bugün biten ardışık gün sayısı; bugün henüz yapılmadıysa dün biten seri sayılır."""
    t = t or today()
    d = t if t in done else t - timedelta(days=1)
    n = 0
    while d in done:
        n += 1
        d -= timedelta(days=1)
    return n


def longest_streak(done):
    best = run = 0
    prev = None
    for d in sorted(done):
        run = run + 1 if prev is not None and (d - prev).days == 1 else 1
        best = max(best, run)
        prev = d
    return best


def _logs_by_habit(user_id, habit_id=None):
    """{habit_id: {date, ...}} — sadece kullanıcının kendi alışkanlıkları."""
    sql = "SELECT l.habit_id, l.date FROM habit_logs l JOIN habits h ON h.id = l.habit_id WHERE h.user_id = ?"
    args = [user_id]
    if habit_id is not None:
        sql += " AND h.id = ?"
        args.append(habit_id)
    out = defaultdict(set)
    for r in query(sql, args):
        d = parse_date(r["date"])
        if d:
            out[r["habit_id"]].add(d)
    return out


def _start_date(habit, done):
    """Başarı oranının paydası için başlangıç: oluşturma günü ya da (geriye işaretlendiyse) ilk kayıt."""
    created = parse_date(local_dt(habit["created_at"], "%Y-%m-%d")) or today()
    return min([created] + list(done)) if done else created


def _rate(done, lo, hi):
    """lo..hi (dahil) aralığında yapılan günlerin yüzdesi; aralık boşsa None."""
    days = (hi - lo).days + 1
    if days <= 0:
        return None, 0
    count = sum(1 for d in done if lo <= d <= hi)
    return round(100 * count / days), count


def _summary(habit, done, t):
    last7 = []
    for i in range(6, -1, -1):
        d = t - timedelta(days=i)
        last7.append({"on": d in done, "today": d == t, "label": fmt_date(d, True)})
    lo = max(t - timedelta(days=RATE_DAYS - 1), _start_date(habit, done))
    rate30, _ = _rate(done, lo, t)
    return {
        "habit": habit,
        "done": t in done,
        "streak": current_streak(done, t),
        "last7": last7,
        "rate30": rate30 or 0,
        "total": len(done),
    }


def today_status(user_id):
    """Pano için: aktif alışkanlıklar [{"habit": row, "done": bool, "streak": int}]."""
    t = today()
    habits = query("SELECT * FROM habits WHERE user_id = ? AND active = 1 ORDER BY id", (user_id,))
    logs = _logs_by_habit(user_id)
    return [{"habit": h, "done": t in logs[h["id"]], "streak": current_streak(logs[h["id"]], t)}
            for h in habits]


def _parse_month(value, t):
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", (value or "").strip())
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 2000 <= year <= 2100 and 1 <= month <= 12:
            return year, month
    return t.year, t.month


# ---------- Rotalar ----------
@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    t = today()
    habits = query("SELECT * FROM habits WHERE user_id = ? ORDER BY id", (uid,))
    logs = _logs_by_habit(uid)
    active = [_summary(h, logs[h["id"]], t) for h in habits if h["active"]]
    archived = [_summary(h, logs[h["id"]], t) for h in habits if not h["active"]]
    return render_template(
        "habits/index.html", active=active, archived=archived, icons=ICONS, today=t,
        done_count=sum(1 for s in active if s["done"]),
    )


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    name = form_str("name", 60)
    icon = form_choice("icon", ICONS, DEFAULT_ICON)
    if not name:
        flash("Alışkanlık adı boş olamaz.", "warning")
        return redirect_back("habits.index")
    execute("INSERT INTO habits (user_id, name, icon) VALUES (?, ?, ?)", (g.user["id"], name, icon))
    flash(f"{icon} {name} eklendi.", "success")
    return redirect_back("habits.index")


@bp.route("/<int:habit_id>/isaretle", methods=["POST"])
@login_required
def toggle(habit_id):
    habit = owned_or_404("habits", habit_id, g.user["id"])
    t = today()
    raw = (request.form.get("date") or request.args.get("date") or "").strip()
    d = parse_date(raw) if raw else t
    if d is None:
        flash("Geçersiz tarih.", "error")
        return redirect_back("habits.index")
    if d > t:
        flash("İleri bir tarih işaretlenemez.", "error")
        return redirect_back("habits.index")
    if d < t - timedelta(days=BACKFILL_DAYS):
        flash(f"En fazla {BACKFILL_DAYS} gün geriye işaret konabilir.", "error")
        return redirect_back("habits.index")

    iso = d.isoformat()
    when = "bugün" if d == t else fmt_date(d)
    if query_one("SELECT 1 FROM habit_logs WHERE habit_id = ? AND date = ?", (habit_id, iso)):
        execute("DELETE FROM habit_logs WHERE habit_id = ? AND date = ?", (habit_id, iso))
        flash(f"{habit['icon']} {habit['name']}: {when} işareti kaldırıldı.", "success")
    else:
        execute("INSERT OR IGNORE INTO habit_logs (habit_id, date) VALUES (?, ?)", (habit_id, iso))
        flash(f"{habit['icon']} {habit['name']}: {when} yapıldı ✓", "success")
    return redirect_back("habits.index")


@bp.route("/<int:habit_id>")
@login_required
def detail(habit_id):
    uid = g.user["id"]
    habit = owned_or_404("habits", habit_id, uid)
    t = today()
    year, month = _parse_month(request.args.get("ay"), t)
    done = _logs_by_habit(uid, habit_id)[habit_id]

    first = date(year, month, 1)
    days_in_month = calendar.monthrange(year, month)[1]
    last = date(year, month, days_in_month)
    oldest_editable = t - timedelta(days=BACKFILL_DAYS)
    cells = []
    for day in range(1, days_in_month + 1):
        d = date(year, month, day)
        cells.append({
            "day": day, "iso": d.isoformat(), "on": d in done, "today": d == t, "future": d > t,
            "editable": oldest_editable <= d <= t, "label": fmt_date(d, True),
        })

    month_rate, month_done = _rate(done, max(first, _start_date(habit, done)), min(last, t))
    if month_rate is None:  # gelecek ay ya da alışkanlık bu aydan sonra başlamış
        month_done = sum(1 for d in done if first <= d <= last)

    prev_month = add_months(first, -1)
    next_month = add_months(first, 1)
    this_month = date(t.year, t.month, 1)
    return render_template(
        "habits/detail.html",
        habit=habit, icons=ICONS, cells=cells, lead=first.weekday(), weekdays=WEEKDAYS_TR_SHORT,
        month_title=f"{MONTHS_TR[month - 1]} {year}", month_name=MONTHS_TR[month - 1],
        self_url=url_for("habits.detail", habit_id=habit_id, ay=f"{year}-{month:02d}"),
        prev_ay=f"{prev_month.year}-{prev_month.month:02d}" if prev_month.year >= 2000 else None,
        next_ay=f"{next_month.year}-{next_month.month:02d}" if next_month <= this_month else None,
        is_this_month=first == this_month, this_ay=f"{t.year}-{t.month:02d}",
        streak=current_streak(done, t), longest=longest_streak(done), total=len(done),
        month_rate=month_rate, month_done=month_done, done_today=t in done,
        backfill_days=BACKFILL_DAYS,
    )


@bp.route("/<int:habit_id>/duzenle", methods=["POST"])
@login_required
def edit(habit_id):
    habit = owned_or_404("habits", habit_id, g.user["id"])
    name = form_str("name", 60)
    icon = form_choice("icon", ICONS, habit["icon"])
    if not name:
        flash("Alışkanlık adı boş olamaz.", "warning")
    else:
        execute("UPDATE habits SET name = ?, icon = ? WHERE id = ? AND user_id = ?",
                (name, icon, habit_id, g.user["id"]))
        flash("Alışkanlık güncellendi.", "success")
    return redirect(url_for("habits.detail", habit_id=habit_id))


@bp.route("/<int:habit_id>/arsiv", methods=["POST"])
@login_required
def archive(habit_id):
    habit = owned_or_404("habits", habit_id, g.user["id"])
    execute("UPDATE habits SET active = 1 - active WHERE id = ? AND user_id = ?", (habit_id, g.user["id"]))
    flash(f"{habit['name']} arşivlendi." if habit["active"] else f"{habit['name']} arşivden çıkarıldı.", "success")
    return redirect_back("habits.detail", habit_id=habit_id)


@bp.route("/<int:habit_id>/sil", methods=["POST"])
@login_required
def delete(habit_id):
    habit = owned_or_404("habits", habit_id, g.user["id"])
    execute("DELETE FROM habit_logs WHERE habit_id = ?", (habit_id,))
    execute("DELETE FROM habits WHERE id = ? AND user_id = ?", (habit_id, g.user["id"]))
    flash(f"{habit['name']} silindi.", "success")
    return redirect(url_for("habits.index"))
