"""🩺 Sağlık: ölçümler (kilo, tansiyon, şeker, nabız), ilaçlar ve randevular.

Sekmeler: ?tab=olcum (varsayılan) | ilac | randevu
Tarih-saat alanları <input type=datetime-local> ile gelir ve 'YYYY-MM-DDTHH:MM' olarak saklanır (yerel saat).
"""
import re
from datetime import datetime, timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from ..auth import login_required
from ..db import execute, owned_or_404, query, query_one
from ..reminders import medications_today
from ..utils import fmt_number, form_choice, form_float, form_str, now_local, redirect_back, today

bp = Blueprint("health", __name__, url_prefix="/saglik")

TABS = {"olcum": "📈 Ölçümler", "ilac": "💊 İlaçlar", "randevu": "📅 Randevular"}

# tür -> etiket, birim, geçerli aralık, gösterimdeki ondalık
KINDS = {
    "weight": {"label": "Kilo", "unit": "kg", "option": "Kilo (kg)", "icon": "⚖️", "lo": 20, "hi": 400, "dec": 1},
    "bp": {"label": "Tansiyon", "unit": "mmHg", "option": "Tansiyon (mmHg)", "icon": "❤️", "lo": 40, "hi": 260, "dec": 0},
    "sugar": {"label": "Şeker", "unit": "mg/dL", "option": "Şeker (mg/dL)", "icon": "🩸", "lo": 20, "hi": 700, "dec": 0},
    "pulse": {"label": "Nabız", "unit": "bpm", "option": "Nabız (bpm)", "icon": "💓", "lo": 20, "hi": 250, "dec": 0},
}
RECORDS_SHOWN = 20
SPARK_POINTS = 30
SPARK_W, SPARK_H, SPARK_PAD = 300, 60, 4
MAX_TIMES = 12
TIME_RE = re.compile(r"(\d{1,2})(?:[:.](\d{2}))?")


# ---------- Yardımcılar ----------
def _now_str():
    return now_local().strftime("%Y-%m-%dT%H:%M")


def parse_datetime_local(value):
    """'YYYY-MM-DDTHH:MM' (saniyeli ya da boşluklu da olur) -> normalize metin; geçersizse None."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace(" ", "T"))
    except ValueError:
        return None
    if not 1900 <= dt.year <= 2100:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M")


def normalize_times(value):
    """'8:00; 20.30, 08:00' -> ('08:00, 20:30', None). Hatalıysa (None, hata mesajı)."""
    out = []
    for token in re.split(r"[,;\s]+", value or ""):
        if not token:
            continue
        m = TIME_RE.fullmatch(token)
        hour, minute = (int(m.group(1)), int(m.group(2) or 0)) if m else (99, 99)
        if hour > 23 or minute > 59:
            return None, f"Geçersiz saat: {token[:10]} (SS:DD biçiminde yazın, ör. 08:00)"
        t = f"{hour:02d}:{minute:02d}"
        if t not in out:
            out.append(t)
    if len(out) > MAX_TIMES:
        return None, f"En fazla {MAX_TIMES} saat girilebilir."
    return ", ".join(sorted(out)), None


def _delta_text(new, old, dec):
    if new is None or old is None:
        return ""
    d = round(new - old, dec)
    if d == 0:
        return "±0"
    return ("+" if d > 0 else "−") + fmt_number(abs(d), dec)


def _sparkline(*series):
    """Sunucu tarafında SVG polyline noktaları. series: kronolojik değer listeleri (aynı uzunlukta)."""
    n = len(series[0])
    values = [v for s in series for v in s if v is not None]
    if n < 2 or not values:
        return None
    lo, hi = min(values), max(values)
    span = hi - lo
    lines = []
    for s in series:
        points = []
        for i, v in enumerate(s):
            if v is None:
                continue
            x = SPARK_PAD + i * (SPARK_W - 2 * SPARK_PAD) / (n - 1)
            y = SPARK_H / 2 if span == 0 else SPARK_PAD + (hi - v) * (SPARK_H - 2 * SPARK_PAD) / span
            points.append(f"{x:.1f},{y:.1f}")
        if len(points) >= 2:
            lines.append(" ".join(points))
    return {"lines": lines, "w": SPARK_W, "h": SPARK_H, "lo": lo, "hi": hi, "n": n} if lines else None


def _metric_summary(uid, kind):
    meta = KINDS[kind]
    rows = query("SELECT * FROM health_metrics WHERE user_id = ? AND kind = ?"
                 " ORDER BY measured_at DESC, id DESC LIMIT ?", (uid, kind, max(SPARK_POINTS, RECORDS_SHOWN)))
    if not rows:
        return None
    latest = rows[0]
    prev = rows[1] if len(rows) > 1 else None
    since = (today() - timedelta(days=29)).isoformat()
    agg = query_one(
        "SELECT COUNT(*) AS n, MIN(value1) AS min1, AVG(value1) AS avg1, MAX(value1) AS max1,"
        " MIN(value2) AS min2, AVG(value2) AS avg2, MAX(value2) AS max2"
        " FROM health_metrics WHERE user_id = ? AND kind = ? AND measured_at >= ?", (uid, kind, since))
    total = query_one("SELECT COUNT(*) AS n FROM health_metrics WHERE user_id = ? AND kind = ?", (uid, kind))["n"]
    chrono = list(reversed(rows[:SPARK_POINTS]))
    series = [[r["value1"] for r in chrono]]
    if kind == "bp":
        series.append([r["value2"] for r in chrono])
    change = ""
    if prev is not None:
        change = _delta_text(latest["value1"], prev["value1"], meta["dec"])
        if kind == "bp":
            change += "/" + (_delta_text(latest["value2"], prev["value2"], 0) or "?")
    return {
        "kind": kind, "meta": meta, "latest": latest, "prev": prev, "change": change,
        "agg": agg if agg["n"] else None, "spark": _sparkline(*series),
        "records": rows[:RECORDS_SHOWN], "total": total,
    }


def _back(tab):
    return redirect_back("health.index", tab=tab)


# ---------- Ana sayfa (sekmeler) ----------
@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    tab = request.args.get("tab", "olcum")
    if tab not in TABS:
        tab = "olcum"
    now_s = _now_str()
    ctx = {"tab": tab, "tabs": TABS, "kinds": KINDS, "now_s": now_s}
    ctx["upcoming_count"] = query_one(
        "SELECT COUNT(*) AS n FROM appointments WHERE user_id = ? AND done = 0 AND starts_at >= ?",
        (uid, now_s))["n"]

    if tab == "olcum":
        ctx["summaries"] = [s for s in (_metric_summary(uid, k) for k in KINDS) if s]
        ctx["kind_options"] = [(k, m["option"]) for k, m in KINDS.items()]
        ctx["selected_kind"] = request.args.get("tur") if request.args.get("tur") in KINDS else "weight"
    elif tab == "ilac":
        meds = query("SELECT * FROM medications WHERE user_id = ? ORDER BY name COLLATE NOCASE", (uid,))
        ctx["active_meds"] = [m for m in meds if m["active"]]
        ctx["inactive_meds"] = [m for m in meds if not m["active"]]
        ctx["schedule"] = medications_today(uid)
        ctx["now_hm"] = now_s[11:16]
    else:
        ctx["upcoming"] = query(
            "SELECT * FROM appointments WHERE user_id = ? AND done = 0 AND starts_at >= ? ORDER BY starts_at, id",
            (uid, now_s))
        ctx["past"] = query(
            "SELECT * FROM appointments WHERE user_id = ? AND (done = 1 OR starts_at < ?)"
            " ORDER BY starts_at DESC, id DESC LIMIT 20", (uid, now_s))
        ctx["past_total"] = query_one(
            "SELECT COUNT(*) AS n FROM appointments WHERE user_id = ? AND (done = 1 OR starts_at < ?)",
            (uid, now_s))["n"]
    return render_template("health/index.html", **ctx)


# ---------- Ölçümler ----------
@bp.route("/olcum/yeni", methods=["POST"])
@login_required
def metric_create():
    kind = form_choice("kind", KINDS, None)
    if kind is None:
        flash("Ölçüm türünü seçin.", "error")
        return _back("olcum")
    m = KINDS[kind]
    v1 = form_float("value1")
    v2 = form_float("value2") if kind == "bp" else None
    rng = f"{m['lo']}–{m['hi']} {m['unit']}"
    error = None
    if v1 is None:
        error = f"{m['label']} değerini girin."
    elif not m["lo"] <= v1 <= m["hi"]:
        error = f"{m['label']} {rng} arasında olmalı."
    elif kind == "bp":
        if v2 is None:
            error = "Tansiyon için küçük (diastolik) değeri de girin."
        elif not m["lo"] <= v2 <= m["hi"]:
            error = f"Küçük tansiyon {rng} arasında olmalı."
        elif v2 >= v1:
            error = "Büyük tansiyon küçük tansiyondan yüksek olmalı."

    raw_dt = request.form.get("measured_at")
    measured_at = parse_datetime_local(raw_dt) if (raw_dt or "").strip() else _now_str()
    if error is None and measured_at is None:
        error = "Geçerli bir tarih ve saat girin."
    elif error is None and measured_at[:10] > today().isoformat():
        error = "İleri tarihli ölçüm girilemez."
    if error:
        flash(error, "error")
        return _back("olcum")

    execute(
        "INSERT INTO health_metrics (user_id, kind, value1, value2, measured_at, note) VALUES (?, ?, ?, ?, ?, ?)",
        (g.user["id"], kind, round(v1, 2), None if v2 is None else round(v2, 2), measured_at,
         form_str("note", 300)),
    )
    flash(f"{m['icon']} {m['label']} kaydedildi.", "success")
    return _back("olcum")


@bp.route("/olcum/<int:metric_id>/sil", methods=["POST"])
@login_required
def metric_delete(metric_id):
    owned_or_404("health_metrics", metric_id, g.user["id"])
    execute("DELETE FROM health_metrics WHERE id = ? AND user_id = ?", (metric_id, g.user["id"]))
    flash("Ölçüm silindi.", "success")
    return _back("olcum")


# ---------- İlaçlar ----------
def _med_form():
    """(değerler, hata)"""
    times, error = normalize_times(form_str("times", 200))
    v = {"name": form_str("name", 100), "dose": form_str("dose", 100), "times": times,
         "note": form_str("note", 500)}
    if not v["name"]:
        error = "İlaç adı boş olamaz."
    return v, error


@bp.route("/ilac/yeni", methods=["POST"])
@login_required
def med_create():
    v, error = _med_form()
    if error:
        flash(error, "error")
        return _back("ilac")
    execute("INSERT INTO medications (user_id, name, dose, times, note) VALUES (?, ?, ?, ?, ?)",
            (g.user["id"], v["name"], v["dose"], v["times"], v["note"]))
    flash(f"💊 {v['name']} eklendi.", "success")
    return _back("ilac")


@bp.route("/ilac/<int:med_id>", methods=["GET", "POST"])
@login_required
def med_edit(med_id):
    med = owned_or_404("medications", med_id, g.user["id"])
    if request.method == "POST":
        v, error = _med_form()
        if error:
            flash(error, "error")
            return redirect(url_for("health.med_edit", med_id=med_id))
        execute("UPDATE medications SET name = ?, dose = ?, times = ?, note = ? WHERE id = ? AND user_id = ?",
                (v["name"], v["dose"], v["times"], v["note"], med_id, g.user["id"]))
        flash("İlaç güncellendi.", "success")
        return _back("ilac")
    return render_template("health/med_edit.html", med=med)


@bp.route("/ilac/<int:med_id>/durum", methods=["POST"])
@login_required
def med_toggle(med_id):
    med = owned_or_404("medications", med_id, g.user["id"])
    execute("UPDATE medications SET active = 1 - active WHERE id = ? AND user_id = ?", (med_id, g.user["id"]))
    flash(f"{med['name']} pasife alındı." if med["active"] else f"{med['name']} tekrar aktif.", "success")
    return _back("ilac")


@bp.route("/ilac/<int:med_id>/sil", methods=["POST"])
@login_required
def med_delete(med_id):
    med = owned_or_404("medications", med_id, g.user["id"])
    execute("DELETE FROM medications WHERE id = ? AND user_id = ?", (med_id, g.user["id"]))
    flash(f"{med['name']} silindi.", "success")
    return _back("ilac")


# ---------- Randevular ----------
def _appt_form():
    v = {"title": form_str("title", 150), "place": form_str("place", 150), "note": form_str("note", 1000),
         "starts_at": parse_datetime_local(request.form.get("starts_at"))}
    if not v["title"]:
        return v, "Randevu başlığı boş olamaz."
    if not v["starts_at"]:
        return v, "Geçerli bir tarih ve saat girin."
    return v, None


@bp.route("/randevu/yeni", methods=["POST"])
@login_required
def appt_create():
    v, error = _appt_form()
    if error:
        flash(error, "error")
        return _back("randevu")
    execute("INSERT INTO appointments (user_id, title, place, starts_at, note) VALUES (?, ?, ?, ?, ?)",
            (g.user["id"], v["title"], v["place"], v["starts_at"], v["note"]))
    flash("📅 Randevu eklendi.", "success")
    return _back("randevu")


@bp.route("/randevu/<int:appt_id>", methods=["GET", "POST"])
@login_required
def appt_edit(appt_id):
    appt = owned_or_404("appointments", appt_id, g.user["id"])
    if request.method == "POST":
        v, error = _appt_form()
        if error:
            flash(error, "error")
            return redirect(url_for("health.appt_edit", appt_id=appt_id))
        execute("UPDATE appointments SET title = ?, place = ?, starts_at = ?, note = ? WHERE id = ? AND user_id = ?",
                (v["title"], v["place"], v["starts_at"], v["note"], appt_id, g.user["id"]))
        flash("Randevu güncellendi.", "success")
        return _back("randevu")
    return render_template("health/appt_edit.html", appt=appt)


@bp.route("/randevu/<int:appt_id>/tamam", methods=["POST"])
@login_required
def appt_toggle(appt_id):
    appt = owned_or_404("appointments", appt_id, g.user["id"])
    execute("UPDATE appointments SET done = 1 - done WHERE id = ? AND user_id = ?", (appt_id, g.user["id"]))
    flash("Randevu tamamlandı olarak işaretlendi." if not appt["done"] else "Randevu tekrar açıldı.", "success")
    return _back("randevu")


@bp.route("/randevu/<int:appt_id>/sil", methods=["POST"])
@login_required
def appt_delete(appt_id):
    owned_or_404("appointments", appt_id, g.user["id"])
    execute("DELETE FROM appointments WHERE id = ? AND user_id = ?", (appt_id, g.user["id"]))
    flash("Randevu silindi.", "success")
    return _back("randevu")
