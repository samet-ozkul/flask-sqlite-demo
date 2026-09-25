"""📝 Notlar: hızlı not, etiket, sabitleme, arama, Markdown, ek dosya.

Diğer modüller için referans yapı:
- Blueprint adı = modül adı, url_prefix Türkçe
- Her rota @login_required, her sorgu user_id ile filtreli
- Yazma işlemleri POST + CSRF (şablonda ui.csrf()), sonra redirect
"""
from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from ..auth import login_required
from ..db import execute, owned_or_404, query
from ..storage import attachments_for, delete_for
from ..utils import form_bool, form_str, normalize_tags

bp = Blueprint("notes", __name__, url_prefix="/notlar")


def _form_values():
    return {
        "title": form_str("title", 150),
        "content": form_str("content", 20000),
        "tags": normalize_tags(form_str("tags", 300)),
        "pinned": form_bool("pinned"),
    }


@bp.route("/")
@login_required
def index():
    q = request.args.get("q", "").strip()
    tag = request.args.get("tag", "").strip().lower()
    sql = "SELECT * FROM notes WHERE user_id = ?"
    args = [g.user["id"]]
    if q:
        sql += " AND (title LIKE ? OR content LIKE ? OR tags LIKE ?)"
        args += [f"%{q}%"] * 3
    if tag:
        sql += " AND (', ' || tags || ',') LIKE ?"
        args.append(f"%, {tag},%")
    sql += " ORDER BY pinned DESC, COALESCE(updated_at, created_at) DESC"
    notes = query(sql, args)
    all_tags = sorted({t for r in query("SELECT tags FROM notes WHERE user_id = ?", (g.user["id"],))
                       for t in (r["tags"] or "").split(", ") if t})
    return render_template("notes/index.html", notes=notes, q=q, tag=tag, all_tags=all_tags)


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    v = _form_values()
    if not v["content"] and not v["title"]:
        flash("Not boş olamaz.", "warning")
        return redirect(request.referrer or url_for(".index"))
    execute(
        "INSERT INTO notes (user_id, title, content, tags, pinned, updated_at) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
        (g.user["id"], v["title"], v["content"], v["tags"], v["pinned"]),
    )
    flash("Not kaydedildi.", "success")
    # Panodaki hızlı not kutusundan geldiyse oraya dön
    if request.form.get("next") == "dashboard":
        return redirect(url_for("dashboard.index"))
    return redirect(url_for(".index"))


@bp.route("/<int:note_id>", methods=["GET", "POST"])
@login_required
def edit(note_id):
    note = owned_or_404("notes", note_id, g.user["id"])
    if request.method == "POST":
        v = _form_values()
        execute(
            "UPDATE notes SET title = ?, content = ?, tags = ?, pinned = ?, updated_at = CURRENT_TIMESTAMP"
            " WHERE id = ? AND user_id = ?",
            (v["title"], v["content"], v["tags"], v["pinned"], note_id, g.user["id"]),
        )
        flash("Not güncellendi.", "success")
        return redirect(url_for(".index"))
    return render_template("notes/edit.html", note=note, files=attachments_for("note", note_id))


@bp.route("/<int:note_id>/sabitle", methods=["POST"])
@login_required
def toggle_pin(note_id):
    owned_or_404("notes", note_id, g.user["id"])
    execute("UPDATE notes SET pinned = 1 - pinned WHERE id = ? AND user_id = ?", (note_id, g.user["id"]))
    return redirect(request.referrer or url_for(".index"))


@bp.route("/<int:note_id>/sil", methods=["POST"])
@login_required
def delete(note_id):
    owned_or_404("notes", note_id, g.user["id"])
    delete_for("note", note_id)
    execute("DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, g.user["id"]))
    flash("Not silindi.", "success")
    return redirect(url_for(".index"))
