"""🪪 Belgeler: pasaport, ehliyet, kimlik, ruhsat, poliçe gibi belgelerin geçerlilik tarihleri.

Gizlilik için belge numarası ya da fotoğrafı saklanmaz; sadece ad, kimin olduğu ve bitiş tarihi.
Seçilen gün sayısı kadar önce ve bittiği gün 09:00'da Telegram'dan hatırlatılır (cron /hatirlatma);
tarih değişince (belge yenilenince) hatırlatmalar kendiliğinden yeni tarihe göre kurulur.
"""
from datetime import date

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from ..auth import login_required
from ..db import execute, owned_or_404, query
from ..utils import days_until, form_choice, form_date, form_int, form_str

bp = Blueprint("documents", __name__, url_prefix="/belgeler")

KINDS = {
    "passport": ("Pasaport", "🛂"), "license": ("Ehliyet", "🚘"), "id": ("Kimlik", "🪪"),
    "vehicle": ("Araç ruhsatı / muayene", "🚗"), "insurance": ("Sigorta poliçesi", "📄"),
    "residence": ("İkamet / vize", "🌍"), "other": ("Diğer", "📌"),
}
NOTIFY_OPTIONS = [(7, "1 hafta önce"), (30, "1 ay önce"), (60, "2 ay önce"), (90, "3 ay önce"), (180, "6 ay önce")]


def _form_values():
    v = {
        "name": form_str("name", 100),
        "kind": form_choice("kind", KINDS, "other"),
        "holder": form_str("holder", 60),
        "expires_on": form_date("expires_on"),
        "notify_days": form_int("notify_days"),
        "note": form_str("note", 500),
    }
    if v["notify_days"] not in dict(NOTIFY_OPTIONS):
        v["notify_days"] = 30
    if not v["name"]:
        v["name"] = KINDS[v["kind"]][0]
    return v


@bp.route("/")
@login_required
def index():
    docs = query("SELECT * FROM documents WHERE user_id = ? ORDER BY expires_on, id", (g.user["id"],))
    return render_template("documents/index.html", docs=docs, kinds=KINDS, notify_options=NOTIFY_OPTIONS,
                           days_until=days_until)


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    v = _form_values()
    if not v["expires_on"]:
        flash("Bitiş tarihi gerekli.", "error")
        return redirect(url_for(".index"))
    execute(
        "INSERT INTO documents (user_id, name, kind, holder, expires_on, notify_days, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (g.user["id"], v["name"], v["kind"], v["holder"], v["expires_on"], v["notify_days"], v["note"]),
    )
    flash(f"{KINDS[v['kind']][1]} {v['name']} eklendi.", "success")
    return redirect(url_for(".index"))


@bp.route("/<int:doc_id>", methods=["GET", "POST"])
@login_required
def edit(doc_id):
    doc = owned_or_404("documents", doc_id, g.user["id"])
    if request.method == "POST":
        v = _form_values()
        if not v["expires_on"]:
            flash("Bitiş tarihi gerekli.", "error")
            return redirect(url_for(".edit", doc_id=doc_id))
        execute(
            "UPDATE documents SET name = ?, kind = ?, holder = ?, expires_on = ?, notify_days = ?, note = ?"
            " WHERE id = ? AND user_id = ?",
            (v["name"], v["kind"], v["holder"], v["expires_on"], v["notify_days"], v["note"], doc_id, g.user["id"]),
        )
        flash("Belge güncellendi.", "success")
        return redirect(url_for(".index"))
    return render_template("documents/edit.html", doc=doc, kinds=KINDS, notify_options=NOTIFY_OPTIONS)


@bp.route("/<int:doc_id>/sil", methods=["POST"])
@login_required
def delete(doc_id):
    doc = owned_or_404("documents", doc_id, g.user["id"])
    execute("DELETE FROM documents WHERE id = ? AND user_id = ?", (doc_id, g.user["id"]))
    flash(f"{doc['name']} silindi.", "success")
    return redirect(url_for(".index"))


# ---------- Telegram hatırlatması (cron /hatirlatma) ----------
def pending(now):
    """[(tür, belge, kalan_gün)] — 'pre' (X gün kaldı) ya da 'day' (bugün bitiyor).
    Gönderim, o bitiş tarihi için bir kez yapılır (sent_for / day_sent_for = expires_on)."""
    t = now.date()
    out = []
    for d in query("SELECT d.*, u.telegram_chat_id AS chat_id FROM documents d JOIN users u ON u.id = d.user_id"
                   " WHERE u.telegram_chat_id IS NOT NULL AND d.expires_on >= ?", (t.isoformat(),)):
        left = (date.fromisoformat(d["expires_on"]) - t).days
        if left == 0 and d["day_sent_for"] != d["expires_on"]:
            out.append(("day", d, left))
        elif 0 < left <= d["notify_days"] and d["sent_for"] != d["expires_on"]:
            out.append(("pre", d, left))
    return out


def mark_sent(doc, kind):
    column = "day_sent_for" if kind == "day" else "sent_for"
    execute(f"UPDATE documents SET {column} = ? WHERE id = ?", (doc["expires_on"], doc["id"]))


def message(kind, doc, left, url, escape):
    from ..utils import fmt_date
    label, icon = KINDS[doc["kind"]]
    who = f" ({escape(doc['holder'])})" if doc["holder"] else ""
    if kind == "day":
        head = f"{icon} <b>Bugün bitiyor:</b> {escape(doc['name'])}{who}"
    else:
        head = f"{icon} <b>{left} gün sonra bitiyor:</b> {escape(doc['name'])}{who}"
    lines = [head, f"Bitiş: {fmt_date(doc['expires_on'], True)}"]
    if doc["note"]:
        lines.append(f"<i>{escape(doc['note'])}</i>")
    lines.append(f'<a href="{escape(url)}">Belgeler →</a>')
    return "\n".join(lines)
