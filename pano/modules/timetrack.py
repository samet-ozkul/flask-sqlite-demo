"""⏱️ Zaman takibi: başlat/durdur sayacı, elle kayıt, proje bazında rapor ve kazanç, Excel/CSV.

- Zamanlar UTC ('YYYY-MM-DD HH:MM:SS') saklanır, APP_TZ ile gösterilir. Kayıt başladığı (yerel) güne
  yazılır; gece yarısını geçen kayıt bölünmez. Çalışan sayaç raporlarda şu ana kadar sayılır.
- Kullanıcı başına aynı anda tek sayaç: yenisi başlarken çalışan durdurulur.
- Saatlik ücreti olan projede kazanç = süre × güncel ücret (₺).
- Telegram: /baslat [proje] [not], /durdur, /zaman (bot_commands); 10 saattir çalışan sayaç için
  bir kez uyarı ve "⏹️ Durdur" butonu (cron /hatirlatma).
Canlı sayaç: static/timetrack.js
"""
import csv
import io
import math
import re
from datetime import datetime, time, timedelta, timezone
from itertools import groupby

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for

from .. import todo_reminders as todo
from .. import trash
from ..auth import login_required
from ..db import execute, owned_or_404, query, query_one
from ..utils import (MONTHS_TR, TZ, add_months, fmt_date, fmt_money, fold, form_choice, form_float, form_int,
                     form_str, parse_date, redirect_back, safe_path)

bp = Blueprint("timetrack", __name__, url_prefix="/zaman")

COLORS = {
    "red": ("Kırmızı", "🔴"), "orange": ("Turuncu", "🟠"), "yellow": ("Sarı", "🟡"), "green": ("Yeşil", "🟢"),
    "blue": ("Mavi", "🔵"), "purple": ("Mor", "🟣"), "brown": ("Kahverengi", "🟤"), "black": ("Siyah", "⚫"),
}
NO_COLOR_ICON = "⚪"
NO_PROJECT = "Projesiz"
NO_PROJECT_ICON = "⏱️"
NOTE_MAX = 200
MAX_ENTRY_HOURS = 24      # elle eklenen / düzenlenen kayıt en fazla bu kadar sürebilir
LONG_RUNNING_HOURS = 10   # bu kadar saattir çalışan sayaç için Telegram'dan bir kez "unuttun mu?"
MAX_RATE = 1e7
RANGE_MAX_DAYS = 366
RANGES = {"hafta": "Bu hafta", "ay": "Bu ay", "gecen-ay": "Geçen ay", "ozel": "Özel aralık"}
DB_FORMAT = "%Y-%m-%d %H:%M:%S"

ENTRY_SQL = ("SELECT e.*, p.name AS project_name, p.color AS project_color, p.hourly_rate AS rate"
             " FROM time_entries e LEFT JOIN time_projects p ON p.id = e.project_id")


# ---------- Saat ve süre ----------
def now_local():
    """Yerel şimdi (saniye hassasiyetinde). Testlerde todo_reminders.now_local taklit edilir."""
    return todo.now_local().replace(microsecond=0)


def to_db(dt):
    """Saat dilimli datetime -> UTC metin."""
    return dt.astimezone(timezone.utc).strftime(DB_FORMAT)


def from_db(value):
    """UTC metin -> yerel saat dilimli datetime."""
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).astimezone(TZ)


def local_midnight(d):
    return datetime.combine(d, time(0, 0), tzinfo=TZ)


def _at(d, hhmm):
    hh, mm = hhmm.split(":")
    return datetime.combine(d, time(int(hh), int(mm)), tzinfo=TZ)


def seconds_of(entry, now=None):
    """Kaydın süresi (saniye); çalışan sayaç için şu ana kadar."""
    end = from_db(entry["ended_at"]) if entry["ended_at"] else (now or now_local())
    return max(0, int((end - from_db(entry["started_at"])).total_seconds()))


def fmt_duration(seconds):
    """12300 -> '3 sa 25 dk'; dakikanın altı atılır."""
    hours, minutes = divmod(int(seconds or 0) // 60, 60)
    if hours and minutes:
        return f"{hours} sa {minutes} dk"
    return f"{hours} sa" if hours else f"{minutes} dk"


def fmt_clock(seconds):
    """Sayaç görünümü: 3725 -> '1:02:05'."""
    s = int(seconds or 0)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def earning(seconds, rate):
    return None if rate is None else round(seconds / 3600 * rate, 2)


_CLOCK_RE = re.compile(r"(\d{1,2}):([0-5]\d)")
_DURATION_RE = re.compile(r"(?:(\d{1,2}(?:[.,]\d{1,2})?)\s*(?:saat|sa|s|h)\.?)?\s*(?:(\d{1,4})\s*(?:dakika|dak|dk|min|m)?\.?)?")


def parse_duration(value):
    """'1:30', '90 dk', '2 saat', '1,5 sa', '1 sa 15 dk', '45' (dakika) -> saniye; anlaşılmazsa None."""
    s = fold(value).strip()
    if not s:
        return None
    m = _CLOCK_RE.fullmatch(s)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60
    m = _DURATION_RE.fullmatch(s)
    if not m or not (m.group(1) or m.group(2)):
        return None
    hours = float(m.group(1).replace(",", ".")) if m.group(1) else 0
    return round(hours * 3600) + int(m.group(2) or 0) * 60


# ---------- Projeler ----------
def color_icon(color):
    return COLORS[color][1] if color in COLORS else NO_COLOR_ICON


def projects_of(user_id, include_archived=False):
    rows = query("SELECT * FROM time_projects WHERE user_id = ?" + ("" if include_archived else " AND archived = 0"),
                 (user_id,))
    return sorted(rows, key=lambda p: (p["archived"], fold(p["name"])))


def _form_project(user_id):
    """Formdaki proje; boşsa None, başkasınınsa 404."""
    project_id = form_int("project_id")
    if not project_id:
        return None
    return owned_or_404("time_projects", project_id, user_id)


def match_project(user_id, text):
    """'web sitesi ana sayfa' -> (Web sitesi projesi, 'ana sayfa'). Baştaki kelimeler bir projenin adıyla
    (Türkçe harf/büyük-küçük duyarsız) eşleşmezse (None, metnin tamamı). En uzun eşleşme kazanır."""
    names = {fold(" ".join(p["name"].split())): p for p in projects_of(user_id)}
    words = text.split()
    for n in range(len(words), 0, -1):
        project = names.get(fold(" ".join(words[:n])))
        if project:
            return project, " ".join(words[n:])
    return None, text


# ---------- Kayıtlar ----------
def get_entry(entry_id, user_id):
    return query_one(ENTRY_SQL + " WHERE e.id = ? AND e.user_id = ?", (entry_id, user_id))


def _entry_or_404(entry_id, user_id):
    row = get_entry(entry_id, user_id)
    if row is None:
        abort(404)
    return row


def running(user_id):
    """Kullanıcının çalışan sayacı (proje bilgisiyle) ya da None."""
    return query_one(ENTRY_SQL + " WHERE e.user_id = ? AND e.ended_at IS NULL ORDER BY e.started_at DESC, e.id DESC"
                     " LIMIT 1", (user_id,))


def stop_entry(entry_id, user_id, now=None):
    """Belirli bir sayacı durdurur (Telegram butonu: o arada başlatılan başka sayaca dokunmaz). Durduysa kaydı döner."""
    cur = execute("UPDATE time_entries SET ended_at = MAX(started_at, ?) WHERE id = ? AND user_id = ? AND ended_at IS NULL",
                  (to_db(now or now_local()), entry_id, user_id))
    return get_entry(entry_id, user_id) if cur.rowcount else None


def stop_timer(user_id, now=None):
    """Çalışan sayacı durdurur; durdurulan kaydı döner, çalışan yoksa None."""
    current = running(user_id)
    if current is None:
        return None
    # Normalde tek sayaç çalışır; yine de (ör. çöp kutusundan geri gelen) hepsi kapatılır
    execute("UPDATE time_entries SET ended_at = MAX(started_at, ?) WHERE user_id = ? AND ended_at IS NULL",
            (to_db(now or now_local()), user_id))
    return get_entry(current["id"], user_id)


def start_timer(user_id, project_id=None, note=""):
    """Yeni sayaç başlatır, çalışan varsa önce onu durdurur. (yeni kaydın id'si, durdurulan kayıt ya da None)"""
    now = now_local()
    stopped = stop_timer(user_id, now)
    entry_id = execute("INSERT INTO time_entries (user_id, project_id, note, started_at) VALUES (?, ?, ?, ?)",
                       (user_id, project_id, (note or "").strip()[:NOTE_MAX], to_db(now))).lastrowid
    return entry_id, stopped


def entries_between(user_id, first_day, end_day):
    """Başladığı yerel gün first_day <= gün < end_day olan kayıtlar (eskiden yeniye)."""
    return query(ENTRY_SQL + " WHERE e.user_id = ? AND e.started_at >= ? AND e.started_at < ?"
                 " ORDER BY e.started_at, e.id",
                 (user_id, to_db(local_midnight(first_day)), to_db(local_midnight(end_day))))


def label_of(entry):
    return entry["project_name"] or NO_PROJECT


def icon_of(entry):
    return color_icon(entry["project_color"]) if entry["project_id"] else NO_PROJECT_ICON


def view(entry, now):
    """Şablon için: yerel başlangıç/bitiş, süre, kazanç."""
    start = from_db(entry["started_at"])
    end = from_db(entry["ended_at"]) if entry["ended_at"] else None
    seconds = seconds_of(entry, now)
    return {**dict(entry), "start": start, "end": end, "seconds": seconds, "running": end is None,
            "next_day": end is not None and end.date() > start.date(), "label": label_of(entry), "icon": icon_of(entry),
            "earning": earning(seconds, entry["rate"]), "started_ms": int(start.timestamp() * 1000)}


def summarize(rows, now):
    """Toplam süre, kayıt sayısı, proje bazında dağılım ve (ücreti olan projelerden) kazanç."""
    per = {}
    for r in rows:
        p = per.setdefault(r["project_id"], {"id": r["project_id"], "name": label_of(r), "icon": icon_of(r),
                                             "rate": r["rate"], "seconds": 0, "count": 0})
        p["seconds"] += seconds_of(r, now)
        p["count"] += 1
    projects = sorted(per.values(), key=lambda p: (-p["seconds"], fold(p["name"])))
    for p in projects:
        p["earning"] = earning(p["seconds"], p["rate"])
    earned = [p["earning"] for p in projects if p["earning"] is not None]
    return {"total": sum(p["seconds"] for p in projects), "count": len(rows), "projects": projects,
            "earning": round(sum(earned), 2) if earned else None}


def day_summary(user_id, now):
    """Bugün başlayan kayıtlar ve özetleri."""
    t = now.date()
    rows = entries_between(user_id, t, t + timedelta(days=1))
    return rows, summarize(rows, now)


def _keep_seconds(new, old_value):
    """Formda saat değişmediyse (aynı dakika) kayıttaki saniyeler korunur."""
    if old_value:
        old = from_db(old_value)
        if old.replace(second=0) == new.replace(second=0):
            return old
    return new


def _times_from_form(now, entry=None):
    """Formdan (başlangıç, bitiş, hata). Bitiş başlangıçtan erkense ertesi güne sayılır (gece yarısı geçişi).
    Sadece süre yazılırsa: bugün için şu anda biter, geçmiş bir gün için 09:00'da başlar.
    Çalışan sayaç düzenlenirken bitiş None kalır."""
    day = parse_date(request.form.get("date")) or now.date()
    start_t = todo.parse_time(request.form.get("start"))
    end_t = todo.parse_time(request.form.get("end"))
    raw_duration = form_str("duration", 30)
    duration = parse_duration(raw_duration) if raw_duration else None
    if entry is not None and entry["ended_at"] is None:
        if not start_t:
            return None, None, "Başlangıç saatini yaz."
        start = _keep_seconds(_at(day, start_t), entry["started_at"])
        return (None, None, "Başlangıç ileri bir saat olamaz.") if start > now else (start, None, None)
    if raw_duration and duration is None and not (start_t and end_t):
        return None, None, "Süreyi anlayamadım. Örnek: 1:30, 90 dk, 2 saat."

    def keep(dt, column):
        return _keep_seconds(dt, entry[column]) if entry is not None else dt

    if start_t and end_t:
        start, end = _at(day, start_t), _at(day, end_t)
        if end < start:
            end += timedelta(days=1)  # ör. 23:00 – 01:00
        start, end = keep(start, "started_at"), keep(end, "ended_at")
    elif duration is not None and start_t:
        start = keep(_at(day, start_t), "started_at")
        end = start + timedelta(seconds=duration)
    elif duration is not None and end_t:
        end = keep(_at(day, end_t), "ended_at")
        start = end - timedelta(seconds=duration)
    elif duration is not None:
        if day == now.date():
            end = now
            start = end - timedelta(seconds=duration)
        else:
            start = _at(day, todo.DEFAULT_DUE_TIME)
            end = start + timedelta(seconds=duration)
    else:
        return None, None, "Başlangıç ve bitiş saatini ya da süreyi yaz (ör. 1:30, 90 dk, 2 saat)."
    seconds = (end - start).total_seconds()
    if seconds <= 0:
        return None, None, "Süre sıfırdan uzun olmalı."
    if seconds > MAX_ENTRY_HOURS * 3600:
        return None, None, f"Bir kayıt en fazla {MAX_ENTRY_HOURS} saat olabilir."
    if start > now:
        return None, None, "İleri bir zaman için kayıt eklenemez."
    return start, end, None


def _back():
    return safe_path(request.args.get("next") or request.form.get("next")) or url_for(".index")


def _ctx():
    """Şablonlarda kullanılan yardımcılar."""
    return {"duration": fmt_duration, "clock": fmt_clock, "color_icon": color_icon, "colors": COLORS,
            "no_project": NO_PROJECT, "no_project_icon": NO_PROJECT_ICON}


# ---------- Sayaç ----------
@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    now = now_local()
    t = now.date()
    rows, today_sum = day_summary(uid, now)
    monday = t - timedelta(days=t.weekday())
    week = summarize(entries_between(uid, monday, monday + timedelta(days=7)), now)
    current = running(uid)
    last = query_one("SELECT project_id FROM time_entries WHERE user_id = ? ORDER BY started_at DESC, id DESC LIMIT 1",
                     (uid,))
    return render_template(
        "timetrack/index.html", current=view(current, now) if current else None,
        entries=[view(r, now) for r in reversed(rows)], today_sum=today_sum, week=week, today=t,
        projects=projects_of(uid), last_project=last["project_id"] if last else None,
        now_ms=int(now.timestamp() * 1000), **_ctx(),
    )


@bp.route("/baslat", methods=["POST"])
@login_required
def start():
    project = _form_project(g.user["id"])
    _entry_id, stopped = start_timer(g.user["id"], project["id"] if project else None, form_str("note", NOTE_MAX))
    message = f"▶️ Sayaç başladı · {project['name'] if project else NO_PROJECT}"
    if stopped:
        message += f" (önceki sayaç durdu: {fmt_duration(seconds_of(stopped))})"
    flash(message, "success")
    return redirect_back("timetrack.index")


@bp.route("/durdur", methods=["POST"])
@login_required
def stop():
    stopped = stop_timer(g.user["id"])
    if stopped is None:
        flash("Çalışan sayaç yok.", "warning")
    else:
        flash(f"⏹️ Sayaç durdu: {fmt_duration(seconds_of(stopped))} · {label_of(stopped)}", "success")
    return redirect_back("timetrack.index")


@bp.route("/<int:entry_id>/devam", methods=["POST"])
@login_required
def resume(entry_id):
    """Aynı proje ve notla yeni sayaç."""
    entry = _entry_or_404(entry_id, g.user["id"])
    start_timer(g.user["id"], entry["project_id"], entry["note"])
    flash(f"▶️ Sayaç başladı · {label_of(entry)}", "success")
    return redirect_back("timetrack.index")


# ---------- Elle kayıt, düzenleme, silme ----------
@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    uid = g.user["id"]
    project = _form_project(uid)
    begin, end, error = _times_from_form(now_local())
    if error:
        flash(error, "error")
        return redirect(url_for(".index"))
    execute("INSERT INTO time_entries (user_id, project_id, note, started_at, ended_at) VALUES (?, ?, ?, ?, ?)",
            (uid, project["id"] if project else None, form_str("note", NOTE_MAX), to_db(begin), to_db(end)))
    flash(f"Kayıt eklendi: {fmt_duration((end - begin).total_seconds())} · {project['name'] if project else NO_PROJECT}",
          "success")
    return redirect(url_for(".index"))


@bp.route("/<int:entry_id>", methods=["GET", "POST"])
@login_required
def edit(entry_id):
    uid = g.user["id"]
    entry = _entry_or_404(entry_id, uid)
    now = now_local()
    if request.method == "POST":
        project = _form_project(uid)
        begin, end, error = _times_from_form(now, entry)
        if error:
            flash(error, "error")
            return redirect(url_for(".edit", entry_id=entry_id, next=_back()))
        # Başlangıç değişince "10 saattir çalışıyor" uyarısı yeni başlangıca göre yeniden gönderilebilir
        warned = entry["long_warned"] if to_db(begin) == entry["started_at"] else 0
        execute("UPDATE time_entries SET project_id = ?, note = ?, started_at = ?, ended_at = ?, long_warned = ?"
                " WHERE id = ? AND user_id = ?",
                (project["id"] if project else None, form_str("note", NOTE_MAX), to_db(begin),
                 to_db(end) if end else None, warned, entry_id, uid))
        flash("Kayıt güncellendi.", "success")
        return redirect(_back())
    # Arşivdeki proje sadece bu kayıt ona aitse seçilebilir
    projects = [p for p in projects_of(uid, include_archived=True) if not p["archived"] or p["id"] == entry["project_id"]]
    return render_template("timetrack/edit.html", e=view(entry, now), projects=projects, back=_back(), **_ctx())


@bp.route("/<int:entry_id>/sil", methods=["POST"])
@login_required
def delete(entry_id):
    uid = g.user["id"]
    entry = _entry_or_404(entry_id, uid)
    if entry["ended_at"] is None:  # çalışan sayaç silinirken durdurulur; geri gelirse çalışmaya devam etmesin
        entry = stop_entry(entry_id, uid) or entry
    start = from_db(entry["started_at"])
    trash.move(uid, "timetrack", f"⏱️ {label_of(entry)} · {fmt_duration(seconds_of(entry))} · {fmt_date(start.date())}",
               ("time_entries", entry_id))
    flash(trash.notice("Kayıt"), "success")
    return redirect(_back())


# ---------- Projeler ----------
def _project_values(user_id, project_id=None):
    """(değerler, hata)"""
    v = {"name": " ".join(form_str("name", 60).split()), "color": form_choice("color", COLORS, ""),
         "hourly_rate": form_float("hourly_rate")}
    rate = v["hourly_rate"]
    if not v["name"]:
        return v, "Proje adı boş olamaz."
    if rate is not None and not (math.isfinite(rate) and 0 <= rate < MAX_RATE):
        return v, "Saatlik ücret geçersiz."
    if rate == 0:
        v["hourly_rate"] = None
    key = fold(v["name"])
    if any(fold(p["name"]) == key and p["id"] != project_id for p in projects_of(user_id, include_archived=True)):
        return v, "Bu adla bir proje zaten var."
    return v, None


@bp.route("/projeler")
@login_required
def projects():
    uid = g.user["id"]
    now = now_local()
    stamp, month_start = to_db(now), to_db(local_midnight(now.date().replace(day=1)))
    span = "(julianday(COALESCE(ended_at, ?)) - julianday(started_at)) * 86400"
    totals = {r["project_id"]: r for r in query(
        f"SELECT project_id, SUM({span}) AS total, SUM(CASE WHEN started_at >= ? THEN {span} ELSE 0 END) AS month"
        " FROM time_entries WHERE user_id = ? AND project_id IS NOT NULL GROUP BY project_id",
        (stamp, month_start, stamp, uid))}
    rows = projects_of(uid, include_archived=True)
    return render_template("timetrack/projects.html", active=[p for p in rows if not p["archived"]],
                           archived=[p for p in rows if p["archived"]], totals=totals, **_ctx())


@bp.route("/projeler/yeni", methods=["POST"])
@login_required
def create_project():
    v, error = _project_values(g.user["id"])
    if error:
        flash(error, "error")
        return redirect_back("timetrack.projects")
    execute("INSERT INTO time_projects (user_id, name, color, hourly_rate) VALUES (?, ?, ?, ?)",
            (g.user["id"], v["name"], v["color"], v["hourly_rate"]))
    flash(f"{color_icon(v['color'])} {v['name']} eklendi.", "success")
    return redirect_back("timetrack.projects")


@bp.route("/proje/<int:project_id>", methods=["GET", "POST"])
@login_required
def edit_project(project_id):
    uid = g.user["id"]
    project = owned_or_404("time_projects", project_id, uid)
    if request.method == "POST":
        v, error = _project_values(uid, project_id)
        if error:
            flash(error, "error")
            return redirect(url_for(".edit_project", project_id=project_id))
        execute("UPDATE time_projects SET name = ?, color = ?, hourly_rate = ? WHERE id = ? AND user_id = ?",
                (v["name"], v["color"], v["hourly_rate"], project_id, uid))
        flash("Proje güncellendi.", "success")
        return redirect(url_for(".projects"))
    return render_template("timetrack/project.html", p=project, **_ctx())


@bp.route("/proje/<int:project_id>/arsiv", methods=["POST"])
@login_required
def archive_project(project_id):
    project = owned_or_404("time_projects", project_id, g.user["id"])
    execute("UPDATE time_projects SET archived = 1 - archived WHERE id = ? AND user_id = ?", (project_id, g.user["id"]))
    flash(f"{project['name']} arşivlendi." if not project["archived"] else f"{project['name']} arşivden çıkarıldı.",
          "success")
    return redirect_back("timetrack.projects")


@bp.route("/proje/<int:project_id>/sil", methods=["POST"])
@login_required
def delete_project(project_id):
    uid = g.user["id"]
    project = owned_or_404("time_projects", project_id, uid)
    # Bu projede çalışan sayaç durdurulur; proje geri getirilirse kayıtları da (bitmiş olarak) gelir
    execute("UPDATE time_entries SET ended_at = MAX(started_at, ?) WHERE project_id = ? AND ended_at IS NULL",
            (to_db(now_local()), project_id))
    trash.move(uid, "timetrack", f"⏱️ {project['name']} (proje ve kayıtları)", ("time_projects", project_id),
               children=[("time_entries", "project_id = ?")])
    flash(trash.notice(project["name"]), "success")
    return redirect(url_for(".projects"))


# ---------- Rapor ve dışa aktarma ----------
def resolve_range(key, first_raw, last_raw, t):
    """(anahtar, ilk gün, bitişten sonraki gün, başlık). Bilinmeyen anahtar = bu hafta (Pazartesi–Pazar)."""
    if key == "ozel":
        first, last = parse_date(first_raw), parse_date(last_raw)
        if first and last:
            if last < first:
                first, last = last, first
            last = min(last, first + timedelta(days=RANGE_MAX_DAYS - 1))
            return key, first, last + timedelta(days=1), f"{fmt_date(first)} – {fmt_date(last)}"
    if key in ("ay", "gecen-ay"):
        first = t.replace(day=1)
        if key == "gecen-ay":
            first = add_months(first, -1)
        return key, first, add_months(first, 1), f"{MONTHS_TR[first.month - 1]} {first.year}"
    monday = t - timedelta(days=t.weekday())
    return "hafta", monday, monday + timedelta(days=7), f"{fmt_date(monday)} – {fmt_date(monday + timedelta(days=6))}"


def _range_args(key, first, end):
    return {"aralik": key, "bas": first.isoformat(), "bit": (end - timedelta(days=1)).isoformat()} if key == "ozel" \
        else {"aralik": key}


def _report_range():
    a = request.args
    return resolve_range(a.get("aralik"), a.get("bas"), a.get("bit"), now_local().date())


@bp.route("/rapor")
@login_required
def report():
    uid = g.user["id"]
    now = now_local()
    key, first, end, title = _report_range()
    rows = entries_between(uid, first, end)
    s = summarize(rows, now)
    bars = [(f"{p['icon']} {p['name']}", p["seconds"],
             fmt_duration(p["seconds"]) + (f" · {fmt_money(p['earning'])}" if p["earning"] is not None else ""))
            for p in s["projects"]]
    views = [view(r, now) for r in reversed(rows)]
    days_list = [{"date": d, "entries": list(items)} for d, items in groupby(views, key=lambda v: v["start"].date())]
    for day in days_list:
        day["total"] = sum(v["seconds"] for v in day["entries"])
    day_bars = None
    if (end - first).days <= 7:  # haftalık görünüm: gün gün toplam (boş günler de)
        per_day = {d["date"]: d["total"] for d in days_list}
        day_bars = []
        for i in range((end - first).days):
            d = first + timedelta(days=i)
            day_bars.append((fmt_date(d, True), per_day.get(d, 0), fmt_duration(per_day.get(d, 0))))
    return render_template(
        "timetrack/report.html", key=key, first=first, last=end - timedelta(days=1), title=title, ranges=RANGES,
        s=s, bars=bars, day_bars=day_bars, days_list=days_list, range_args=_range_args(key, first, end),
        self_url=request.full_path.rstrip("?"), **_ctx(),
    )


@bp.route("/disa-aktar")
@login_required
def export():
    """Excel'de doğrudan açılan CSV (Harcamalar'daki gibi): UTF-8 BOM, ';' ayırıcı, virgüllü ondalık."""
    from .expenses import _csv_safe
    now = now_local()
    key, first, end, _title = _report_range()
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(["Tarih", "Başlangıç", "Bitiş", "Süre (saat)", "Proje", "Not", "Tutar (TL)"])
    for r in entries_between(g.user["id"], first, end):
        v = view(r, now)
        writer.writerow([v["start"].strftime("%d.%m.%Y"), v["start"].strftime("%H:%M"),
                         v["end"].strftime("%H:%M") if v["end"] else "",
                         f"{v['seconds'] / 3600:.2f}".replace(".", ","), _csv_safe(r["project_name"] or ""),
                         _csv_safe(r["note"]), f"{v['earning']:.2f}".replace(".", ",") if v["earning"] is not None else ""])
    last = end - timedelta(days=1)
    name = f"zaman-{first:%Y-%m}" if key in ("ay", "gecen-ay") else f"zaman-{first.isoformat()}_{last.isoformat()}"
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


# ---------- Telegram ----------
def entry_line(entry, escape):
    """'🔵 Web sitesi · ana sayfa'"""
    return f"{icon_of(entry)} {escape(label_of(entry))}" + (f" · {escape(entry['note'])}" if entry["note"] else "")


def stop_buttons(entry_id):
    return [[("⏹️ Durdur", f"tt:{entry_id}")]]


def stopped_message(entry, url, escape):
    """Durdurulan sayaç + bugünün toplamı."""
    now = now_local()
    seconds = seconds_of(entry, now)
    money = earning(seconds, entry["rate"])
    _rows, today_sum = day_summary(entry["user_id"], now)
    return "\n".join([
        f"⏹️ <b>Sayaç durdu: {fmt_duration(seconds)}</b>" + (f" · {fmt_money(money)}" if money is not None else ""),
        entry_line(entry, escape),
        f"📅 Bugün toplam: {fmt_duration(today_sum['total'])}",
        f'<a href="{escape(url)}">Kaydı düzelt →</a>',
    ])


def status_message(user_id, url, escape):
    """/zaman: çalışan sayaç ve bugünün toplamı."""
    now = now_local()
    current = running(user_id)
    lines = []
    if current:
        start = from_db(current["started_at"])
        since = start.strftime("%H:%M") if start.date() == now.date() else start.strftime("%d.%m %H:%M")
        lines.append(f"▶️ <b>Çalışıyor: {fmt_duration(seconds_of(current, now))}</b> (başlangıç {since})")
        lines.append(entry_line(current, escape))
    else:
        lines.append("⏸️ Çalışan sayaç yok. Başlatmak için: <code>/baslat proje not</code>")
    _rows, s = day_summary(user_id, now)
    lines.append("")
    lines.append(f"📅 <b>Bugün: {fmt_duration(s['total'])}</b> ({s['count']} kayıt)"
                 + (f" · {fmt_money(s['earning'])}" if s["earning"] is not None else ""))
    lines += [f"{p['icon']} {escape(p['name'])}: {fmt_duration(p['seconds'])}" for p in s["projects"][:8]]
    lines.append(f'<a href="{escape(url)}">Zaman takibi →</a>')
    return "\n".join(lines), current


def long_running(now):
    """LONG_RUNNING_HOURS'tan uzun çalışan, henüz uyarılmamış sayaçlar (Telegram'ı bağlı kullanıcılar)."""
    return query(
        "SELECT e.*, p.name AS project_name, p.color AS project_color, p.hourly_rate AS rate,"
        " u.telegram_chat_id AS chat_id FROM time_entries e LEFT JOIN time_projects p ON p.id = e.project_id"
        " JOIN users u ON u.id = e.user_id"
        " WHERE e.ended_at IS NULL AND e.long_warned = 0 AND e.started_at <= ? AND u.telegram_chat_id IS NOT NULL",
        (to_db(now - timedelta(hours=LONG_RUNNING_HOURS)),))


def mark_warned(entry_id):
    execute("UPDATE time_entries SET long_warned = 1 WHERE id = ?", (entry_id,))


def warn_message(entry, now, url, escape):
    hours = seconds_of(entry, now) // 3600
    start = from_db(entry["started_at"])
    return "\n".join([
        f"⏱️ <b>Sayaç {hours} saattir çalışıyor — unuttun mu?</b>",
        entry_line(entry, escape),
        f"Başlangıç: {start.strftime('%d.%m.%Y %H:%M')}",
        f'<a href="{escape(url)}">Zaman takibi →</a>',
    ])
