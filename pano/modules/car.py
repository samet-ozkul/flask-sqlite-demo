"""🚗 Araç: muayene / trafik sigortası / kasko / bakım tarihleri, yakıt ve servis kayıtları.

- Tarih rozetleri 30 gün kala turuncu, geçince kırmızı olur (hatırlatıcılar reminders.py'de)
- Ortalama tüketim "dolu depo" yöntemiyle hesaplanır
- Kayıt eklerken istenirse tutar Harcamalar'a da işlenir
"""
import re

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..auth import login_required
from ..db import execute, owned_or_404, query, query_one
from ..storage import attachments_for, delete_for, first_thumbs
from ..utils import form_bool, form_choice, form_date, form_float, form_str, today, today_str

bp = Blueprint("car", __name__, url_prefix="/arac")

KINDS = {"fuel": "Yakıt", "service": "Bakım", "repair": "Onarım", "other": "Diğer"}
KIND_ICONS = {"fuel": "⛽", "service": "🔧", "repair": "🛠️", "other": "📌"}
DATE_FIELDS = [
    ("inspection_date", "Muayene"),
    ("insurance_date", "Trafik sigortası"),
    ("casco_date", "Kasko"),
    ("service_date", "Bakım"),
]
WARN_DAYS = 30
MAX_KM = 9_999_999


# ---------- Yardımcılar ----------
def form_km(name):
    """'120.000', '120 000 km', '120000' -> 120000; boş/geçersizse None.

    Kilometrede ondalık olmaz; nokta/virgül binlik ayırıcı kabul edilir.
    """
    raw = re.sub(r"[\s.,]|km", "", (request.form.get(name) or "").lower())
    if not raw.isdigit():
        return None
    return min(int(raw), MAX_KM)


def average_consumption(points):
    """Dolu depo yöntemi: points = [(km, litre), ...] (yakıt kayıtları).

    İlk (en düşük km'li) dolum hariç tüm litrelerin toplamı / (en yüksek km − en düşük km) × 100.
    En az 2 geçerli kayıt ve pozitif mesafe yoksa None.
    """
    pts = sorted((int(km), float(lt)) for km, lt in points if km is not None and lt)
    if len(pts) < 2:
        return None
    distance = pts[-1][0] - pts[0][0]
    if distance <= 0:
        return None
    return sum(lt for _km, lt in pts[1:]) / distance * 100


def _vehicle_values():
    return {
        "name": form_str("name", 80),
        "plate": form_str("plate", 20).upper(),
        "inspection_date": form_date("inspection_date"),
        "insurance_date": form_date("insurance_date"),
        "casco_date": form_date("casco_date"),
        "service_date": form_date("service_date"),
        "service_km": form_km("service_km"),
        "note": form_str("note", 2000),
    }


def _vehicle_stats(vehicle, logs):
    t = today()
    year_start, year_end = f"{t.year}-01-01", f"{t.year + 1}-01-01"
    this_year = [r for r in logs if year_start <= r["date"] < year_end]
    last_km = max((r["km"] for r in logs if r["km"] is not None), default=None)
    remaining = None
    if vehicle["service_km"] is not None and last_km is not None:
        remaining = vehicle["service_km"] - last_km
    fuel_points = [(r["km"], r["liters"]) for r in logs if r["kind"] == "fuel"]
    return {
        "year": t.year,
        "year_total": sum(r["amount"] or 0 for r in this_year),
        "year_count": len(this_year),
        "last_km": last_km,
        "remaining_km": remaining,
        "avg": average_consumption(fuel_points),
        "fuel_count": sum(1 for km, lt in fuel_points if km is not None and lt),
    }


def _own_log_or_404(vehicle_id, log_id):
    row = query_one("SELECT * FROM vehicle_logs WHERE id = ? AND vehicle_id = ?", (log_id, vehicle_id))
    if row is None:
        abort(404)
    return row


def _render_ctx(**kw):
    return dict(KINDS=KINDS, KIND_ICONS=KIND_ICONS, DATE_FIELDS=DATE_FIELDS, WARN_DAYS=WARN_DAYS, **kw)


# ---------- Rotalar ----------
@bp.route("/")
@login_required
def index():
    vehicles = query(
        "SELECT v.*, (SELECT MAX(km) FROM vehicle_logs l WHERE l.vehicle_id = v.id) AS last_km"
        " FROM vehicles v WHERE v.user_id = ? ORDER BY v.name COLLATE NOCASE, v.id",
        (g.user["id"],),
    )
    thumbs = first_thumbs("vehicle", [v["id"] for v in vehicles])
    return render_template("car/index.html", **_render_ctx(vehicles=vehicles, thumbs=thumbs))


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    v = _vehicle_values()
    if not v["name"]:
        flash("Araç adı gerekli.", "warning")
        return redirect(url_for(".index"))
    cur = execute(
        "INSERT INTO vehicles (user_id, name, plate, inspection_date, insurance_date, casco_date,"
        " service_date, service_km, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (g.user["id"], v["name"], v["plate"], v["inspection_date"], v["insurance_date"], v["casco_date"],
         v["service_date"], v["service_km"], v["note"]),
    )
    flash("Araç eklendi.", "success")
    return redirect(url_for(".detail", vehicle_id=cur.lastrowid))


@bp.route("/<int:vehicle_id>")
@login_required
def detail(vehicle_id):
    vehicle = owned_or_404("vehicles", vehicle_id, g.user["id"])
    logs = query(
        "SELECT * FROM vehicle_logs WHERE vehicle_id = ? ORDER BY date DESC, id DESC", (vehicle_id,)
    )
    return render_template(
        "car/detail.html",
        **_render_ctx(v=vehicle, logs=logs, stats=_vehicle_stats(vehicle, logs),
                      files=attachments_for("vehicle", vehicle_id)),
    )


@bp.route("/<int:vehicle_id>/duzenle", methods=["POST"])
@login_required
def update(vehicle_id):
    owned_or_404("vehicles", vehicle_id, g.user["id"])
    v = _vehicle_values()
    if not v["name"]:
        flash("Araç adı gerekli.", "warning")
        return redirect(url_for(".detail", vehicle_id=vehicle_id))
    execute(
        "UPDATE vehicles SET name = ?, plate = ?, inspection_date = ?, insurance_date = ?, casco_date = ?,"
        " service_date = ?, service_km = ?, note = ? WHERE id = ? AND user_id = ?",
        (v["name"], v["plate"], v["inspection_date"], v["insurance_date"], v["casco_date"],
         v["service_date"], v["service_km"], v["note"], vehicle_id, g.user["id"]),
    )
    flash("Araç bilgileri güncellendi.", "success")
    return redirect(url_for(".detail", vehicle_id=vehicle_id))


@bp.route("/<int:vehicle_id>/sil", methods=["POST"])
@login_required
def delete(vehicle_id):
    vehicle = owned_or_404("vehicles", vehicle_id, g.user["id"])
    delete_for("vehicle", vehicle_id)
    execute("DELETE FROM vehicle_logs WHERE vehicle_id = ?", (vehicle_id,))
    execute("DELETE FROM vehicles WHERE id = ? AND user_id = ?", (vehicle_id, g.user["id"]))
    flash(f"{vehicle['name']} silindi.", "success")
    return redirect(url_for(".index"))


@bp.route("/<int:vehicle_id>/kayit", methods=["POST"])
@login_required
def add_log(vehicle_id):
    vehicle = owned_or_404("vehicles", vehicle_id, g.user["id"])
    back = redirect(url_for(".detail", vehicle_id=vehicle_id))
    kind = form_choice("kind", KINDS, "fuel")
    log_date = form_date("date") or today_str()
    km = form_km("km")
    amount = form_float("amount")
    liters = form_float("liters") if kind == "fuel" else None
    note = form_str("note", 500)

    if (amount is not None and amount < 0) or (liters is not None and liters < 0):
        flash("Tutar ve litre negatif olamaz.", "warning")
        return back
    if liters == 0:
        liters = None
    if km is None and amount is None and liters is None and not note:
        flash("Kaydetmek için km, tutar, litre ya da not girin.", "warning")
        return back

    execute(
        "INSERT INTO vehicle_logs (vehicle_id, kind, date, km, amount, liters, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (vehicle_id, kind, log_date, km, amount, liters, note),
    )

    if form_bool("add_expense") and amount:
        label = f"{vehicle['name']} - {KINDS[kind]}"
        if note:
            label += f": {note}"
        execute(
            "INSERT INTO expenses (user_id, amount, category, note, date) VALUES (?, ?, ?, ?, ?)",
            (g.user["id"], amount, "Yakıt" if kind == "fuel" else "Ulaşım", label[:500], log_date),
        )
        flash(f"{KINDS[kind]} kaydı eklendi ve harcamalara işlendi.", "success")
    else:
        flash(f"{KINDS[kind]} kaydı eklendi.", "success")
    return back


@bp.route("/<int:vehicle_id>/kayit/<int:log_id>/sil", methods=["POST"])
@login_required
def delete_log(vehicle_id, log_id):
    owned_or_404("vehicles", vehicle_id, g.user["id"])
    _own_log_or_404(vehicle_id, log_id)
    execute("DELETE FROM vehicle_logs WHERE id = ? AND vehicle_id = ?", (log_id, vehicle_id))
    flash("Kayıt silindi.", "success")
    return redirect(url_for(".detail", vehicle_id=vehicle_id))
