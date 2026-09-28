"""🗑️ Geri dönüşüm kutusu sayfası (mantık pano/trash.py'de)."""
from datetime import datetime, timezone

from flask import Blueprint, flash, g, redirect, render_template, url_for

from .. import trash
from ..auth import login_required
from ..db import query

bp = Blueprint("trashbin", __name__, url_prefix="/cop-kutusu")


@bp.route("/")
@login_required
def index():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    items = []
    for row in query("SELECT id, module, label, deleted_at FROM trash WHERE user_id = ? ORDER BY deleted_at DESC, id DESC",
                     (g.user["id"],)):
        age = (now - datetime.fromisoformat(row["deleted_at"])).days
        items.append({**dict(row), "left": max(0, trash.KEEP_DAYS - age)})
    return render_template("trashbin/index.html", items=items, keep_days=trash.KEEP_DAYS)


@bp.route("/<int:trash_id>/geri", methods=["POST"])
@login_required
def restore(trash_id):
    ok, message = trash.restore(trash_id, g.user["id"])
    flash(message, "success" if ok else "error")
    return redirect(url_for(".index"))


@bp.route("/<int:trash_id>/sil", methods=["POST"])
@login_required
def delete(trash_id):
    trash.delete_forever(trash_id, g.user["id"])
    flash("Kalıcı olarak silindi.", "success")
    return redirect(url_for(".index"))


@bp.route("/bosalt", methods=["POST"])
@login_required
def empty():
    rows = query("SELECT id FROM trash WHERE user_id = ?", (g.user["id"],))
    for row in rows:
        trash.delete_forever(row["id"], g.user["id"])
    flash(f"Çöp kutusu boşaltıldı ({len(rows)} kayıt).", "success")
    return redirect(url_for(".index"))
