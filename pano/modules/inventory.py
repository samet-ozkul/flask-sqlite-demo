"""📦 Ev envanteri: "Matkap nerede?" — eşya, yer, kategori, adet, fotoğraf.

Arama Türkçe harf ve büyük/küçük harf duyarsızdır ("sarj" -> "Şarj aleti").
"""
from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from ..auth import login_required
from ..db import execute, owned_or_404, query
from ..storage import attachments_for, delete_for, first_thumbs
from ..utils import form_int, form_str, redirect_back

bp = Blueprint("inventory", __name__, url_prefix="/envanter")

MAX_QTY = 1_000_000

_FOLD = str.maketrans({"ı": "i", "ş": "s", "ç": "c", "ğ": "g", "ö": "o", "ü": "u", "â": "a", "î": "i", "û": "u"})


def fold(text):
    """Türkçe harf ve büyük/küçük harf duyarsız arama anahtarı."""
    return (text or "").replace("İ", "i").replace("I", "ı").lower().translate(_FOLD)


def _distinct(column, user_id):
    rows = query(
        f"SELECT {column} AS v, COUNT(*) AS n FROM inventory WHERE user_id = ? AND {column} != ''"
        f" GROUP BY {column}",
        (user_id,),
    )
    return sorted(((r["v"], r["n"]) for r in rows), key=lambda x: fold(x[0]))


def _form_values():
    qty = form_int("quantity")
    return {
        "name": form_str("name", 150),
        "location": form_str("location", 100),
        "category": form_str("category", 60),
        "quantity": 1 if qty is None else max(0, min(qty, MAX_QTY)),
        "note": form_str("note", 2000),
    }


@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    q = request.args.get("q", "").strip()[:100]
    loc = request.args.get("loc", "").strip()
    cat = request.args.get("cat", "").strip()
    sql = "SELECT * FROM inventory WHERE user_id = ?"
    args = [uid]
    if loc:
        sql += " AND location = ?"
        args.append(loc)
    if cat:
        sql += " AND category = ?"
        args.append(cat)
    rows = query(sql, args)
    if q:
        needle = fold(q)
        rows = [r for r in rows
                if any(needle in fold(r[c]) for c in ("name", "location", "category", "note"))]
    items = sorted(rows, key=lambda r: (fold(r["name"]), r["id"]))
    total = query("SELECT COUNT(*) AS n FROM inventory WHERE user_id = ?", (uid,))[0]["n"]
    return render_template(
        "inventory/index.html", items=items, q=q, loc=loc, cat=cat, total=total,
        locations=_distinct("location", uid), categories=_distinct("category", uid),
        thumbs=first_thumbs("inventory", [r["id"] for r in items]),
    )


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    v = _form_values()
    if not v["name"]:
        flash("Eşya adı gerekli.", "warning")
        return redirect_back(".index")
    execute(
        "INSERT INTO inventory (user_id, name, location, category, quantity, note) VALUES (?, ?, ?, ?, ?, ?)",
        (g.user["id"], v["name"], v["location"], v["category"], v["quantity"], v["note"]),
    )
    where = f" → {v['location']}" if v["location"] else ""
    flash(f"{v['name']} eklendi{where}.", "success")
    return redirect_back(".index")


@bp.route("/<int:item_id>", methods=["GET", "POST"])
@login_required
def edit(item_id):
    item = owned_or_404("inventory", item_id, g.user["id"])
    if request.method == "POST":
        v = _form_values()
        if not v["name"]:
            flash("Eşya adı gerekli.", "warning")
            return redirect(url_for(".edit", item_id=item_id))
        execute(
            "UPDATE inventory SET name = ?, location = ?, category = ?, quantity = ?, note = ?"
            " WHERE id = ? AND user_id = ?",
            (v["name"], v["location"], v["category"], v["quantity"], v["note"], item_id, g.user["id"]),
        )
        flash("Eşya güncellendi.", "success")
        return redirect(url_for(".index"))
    uid = g.user["id"]
    return render_template(
        "inventory/edit.html", item=item, files=attachments_for("inventory", item_id),
        locations=_distinct("location", uid), categories=_distinct("category", uid),
    )


@bp.route("/<int:item_id>/adet", methods=["POST"])
@login_required
def qty(item_id):
    owned_or_404("inventory", item_id, g.user["id"])
    delta = 1 if request.form.get("d") == "1" else -1 if request.form.get("d") == "-1" else 0
    if delta:
        execute(
            "UPDATE inventory SET quantity = MAX(0, MIN(?, quantity + ?)) WHERE id = ? AND user_id = ?",
            (MAX_QTY, delta, item_id, g.user["id"]),
        )
    return redirect_back(".index")


@bp.route("/<int:item_id>/sil", methods=["POST"])
@login_required
def delete(item_id):
    item = owned_or_404("inventory", item_id, g.user["id"])
    delete_for("inventory", item_id)
    execute("DELETE FROM inventory WHERE id = ? AND user_id = ?", (item_id, g.user["id"]))
    flash(f"{item['name']} silindi.", "success")
    return redirect(url_for(".index"))
