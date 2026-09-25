"""🛡️ Garanti: ürün, fatura/garanti belgesi fotoğrafı, bitiş tarihi takibi.

Bitişe 30 gün kala pano ve Telegram özetinde hatırlatılır (reminders.py).
"""
from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from ..auth import login_required
from ..db import execute, owned_or_404, query
from ..storage import attachments_for, delete_for, first_thumbs
from ..utils import add_months, form_date, form_float, form_int, form_str, parse_date, today_str

bp = Blueprint("warranty", __name__, url_prefix="/garanti")

FILTERS = {"active": "Aktif", "expired": "Süresi dolan", "all": "Tümü"}
WARN_DAYS = 30
MAX_MONTHS = 600

_FOLD = str.maketrans({"ı": "i", "ş": "s", "ç": "c", "ğ": "g", "ö": "o", "ü": "u", "â": "a", "î": "i", "û": "u"})


def fold(text):
    """Türkçe harf ve büyük/küçük harf duyarsız arama anahtarı ('Şarj' ~ 'sarj', 'IŞIK' ~ 'ışık')."""
    return (text or "").replace("İ", "i").replace("I", "ı").lower().translate(_FOLD)


def is_active(row, today=None):
    until = row["warranty_until"]
    return until is None or until >= (today or today_str())


def months_between(start, end):
    a, b = parse_date(start), parse_date(end)
    if not (a and b) or b < a:
        return None
    months = (b.year - a.year) * 12 + b.month - a.month
    return months if months > 0 else None


def _form_values():
    """Formdan değerleri okur. Bitiş boşsa 'garanti süresi (ay)' + alış tarihinden hesaplar."""
    purchase = form_date("purchase_date")
    until = form_date("warranty_until")
    months = form_int("warranty_months")
    if months is not None and not 0 < months <= MAX_MONTHS:
        flash(f"Garanti süresi 1-{MAX_MONTHS} ay arasında olmalı; dikkate alınmadı.", "warning")
        months = None
    if not until and months:
        if purchase:
            until = add_months(parse_date(purchase), months).isoformat()
        else:
            flash("Garanti süresinden bitiş tarihi hesaplamak için alış tarihini de girin.", "warning")
    price = form_float("price")
    if price is not None and price < 0:
        price = None
    return {
        "product": form_str("product", 150),
        "brand": form_str("brand", 80),
        "store": form_str("store", 120),
        "serial_no": form_str("serial_no", 80),
        "purchase_date": purchase,
        "warranty_until": until,
        "price": price,
        "note": form_str("note", 2000),
    }


@bp.route("/")
@login_required
def index():
    f = request.args.get("f", "active")
    if f not in FILTERS:
        f = "active"
    q = request.args.get("q", "").strip()[:100]
    rows = query(
        "SELECT * FROM warranties WHERE user_id = ?"
        " ORDER BY warranty_until IS NULL, warranty_until, product COLLATE NOCASE, id",
        (g.user["id"],),
    )
    if q:
        needle = fold(q)
        rows = [r for r in rows
                if any(needle in fold(r[c]) for c in ("product", "brand", "store", "serial_no"))]
    t = today_str()
    active = [r for r in rows if is_active(r, t)]
    expired = [r for r in rows if not is_active(r, t)]
    counts = {"active": len(active), "expired": len(expired), "all": len(rows)}
    # Süresi dolanlarda en son biteni en üstte göster
    items = {"active": active, "expired": expired[::-1], "all": rows}[f]
    thumbs = first_thumbs("warranty", [r["id"] for r in items])
    total = query("SELECT COUNT(*) AS n FROM warranties WHERE user_id = ?", (g.user["id"],))[0]["n"]
    return render_template(
        "warranty/index.html", items=items, thumbs=thumbs, f=f, q=q, counts=counts, total=total,
        FILTERS=FILTERS, WARN_DAYS=WARN_DAYS,
    )


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    v = _form_values()
    if not v["product"]:
        flash("Ürün adı gerekli.", "warning")
        return redirect(url_for(".index"))
    cur = execute(
        "INSERT INTO warranties (user_id, product, brand, store, serial_no, purchase_date, warranty_until, price, note)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (g.user["id"], v["product"], v["brand"], v["store"], v["serial_no"], v["purchase_date"],
         v["warranty_until"], v["price"], v["note"]),
    )
    flash("Ürün kaydedildi. Şimdi faturanın veya garanti belgesinin fotoğrafını ekleyebilirsin.", "success")
    return redirect(url_for(".detail", warranty_id=cur.lastrowid))


@bp.route("/<int:warranty_id>", methods=["GET", "POST"])
@login_required
def detail(warranty_id):
    w = owned_or_404("warranties", warranty_id, g.user["id"])
    if request.method == "POST":
        v = _form_values()
        if not v["product"]:
            flash("Ürün adı gerekli.", "warning")
            return redirect(url_for(".detail", warranty_id=warranty_id))
        execute(
            "UPDATE warranties SET product = ?, brand = ?, store = ?, serial_no = ?, purchase_date = ?,"
            " warranty_until = ?, price = ?, note = ? WHERE id = ? AND user_id = ?",
            (v["product"], v["brand"], v["store"], v["serial_no"], v["purchase_date"], v["warranty_until"],
             v["price"], v["note"], warranty_id, g.user["id"]),
        )
        flash("Garanti kaydı güncellendi.", "success")
        return redirect(url_for(".detail", warranty_id=warranty_id))
    return render_template(
        "warranty/detail.html", w=w, files=attachments_for("warranty", warranty_id),
        months=months_between(w["purchase_date"], w["warranty_until"]), active=is_active(w), WARN_DAYS=WARN_DAYS,
    )


@bp.route("/<int:warranty_id>/sil", methods=["POST"])
@login_required
def delete(warranty_id):
    w = owned_or_404("warranties", warranty_id, g.user["id"])
    delete_for("warranty", warranty_id)
    execute("DELETE FROM warranties WHERE id = ? AND user_id = ?", (warranty_id, g.user["id"]))
    flash(f"{w['product']} silindi.", "success")
    return redirect(url_for(".index"))
