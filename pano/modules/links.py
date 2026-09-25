"""🔖 Sonra Bak: okunacak/izlenecek linkler.

- Okunmamış (varsayılan) / okundu / tümü filtresi, arama, etiket
- /linkler/paylas?url=&title=&text= : Android paylaşım hedefi; formu doldurur, GET'te kaydetmez
"""
import re
from urllib.parse import urlparse

from flask import Blueprint, flash, g, render_template, request

from ..auth import login_required
from ..db import execute, owned_or_404, query, query_one
from ..utils import form_bool, form_str, normalize_tags, redirect_back

bp = Blueprint("links", __name__, url_prefix="/linkler")

FILTERS = {"okunmamis": "Okunmamış", "okundu": "Okundu", "tumu": "Tümü"}
URL_MAX = 2000
TITLE_MAX = 300
NOTE_MAX = 2000
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def valid_url(url):
    if not url or not url.lower().startswith(("http://", "https://")):
        return False
    try:
        return bool(urlparse(url).hostname)
    except ValueError:
        return False


def domain_of(url):
    try:
        host = urlparse(url or "").hostname or ""
    except ValueError:
        host = ""
    return host[4:] if host.startswith("www.") else host


def _form_values():
    return {
        "url": form_str("url", URL_MAX),
        "title": form_str("title", TITLE_MAX),
        "note": form_str("note", NOTE_MAX),
        "tags": normalize_tags(form_str("tags", 300)),
        "is_read": form_bool("is_read"),
    }


def _url_error(url):
    if not url:
        return "Link adresi gerekli."
    if not valid_url(url):
        return "Link http:// veya https:// ile başlamalı."
    return None


@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    durum = request.args.get("durum", "okunmamis")
    if durum not in FILTERS:
        durum = "okunmamis"
    q = request.args.get("q", "").strip()
    tag = request.args.get("tag", "").strip().lower()

    sql = "SELECT * FROM links WHERE user_id = ?"
    args = [uid]
    if durum == "okunmamis":
        sql += " AND is_read = 0"
    elif durum == "okundu":
        sql += " AND is_read = 1"
    if q:
        sql += " AND (title LIKE ? OR url LIKE ? OR note LIKE ? OR tags LIKE ?)"
        args += [f"%{q}%"] * 4
    if tag:
        sql += " AND (', ' || tags || ',') LIKE ?"
        args.append(f"%, {tag},%")
    sql += " ORDER BY created_at DESC, id DESC"
    links = query(sql, args)

    c = query_one(
        "SELECT COALESCE(SUM(is_read = 0), 0) AS okunmamis, COALESCE(SUM(is_read = 1), 0) AS okundu,"
        " COUNT(*) AS tumu FROM links WHERE user_id = ?",
        (uid,),
    )
    all_tags = sorted({t for r in query("SELECT tags FROM links WHERE user_id = ?", (uid,))
                       for t in (r["tags"] or "").split(", ") if t})
    return render_template(
        "links/index.html", links=links, durum=durum, q=q, tag=tag, all_tags=all_tags,
        counts=dict(c), filters=FILTERS, domain=domain_of,
    )


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    uid = g.user["id"]
    v = _form_values()
    error = _url_error(v["url"])
    if error:
        flash(error, "error")
        return render_template("links/share.html", v=v, domain=domain_of)
    v["title"] = v["title"] or domain_of(v["url"])
    existing = query_one("SELECT id FROM links WHERE user_id = ? AND url = ?", (uid, v["url"]))
    if existing:
        # Aynı link yeniden paylaşıldıysa tekrar okunacaklara al
        execute("UPDATE links SET is_read = 0 WHERE id = ? AND user_id = ?", (existing["id"], uid))
        flash("Bu link zaten kayıtlıydı; okunmamışlara alındı.", "warning")
        return redirect_back("links.index")
    execute(
        "INSERT INTO links (user_id, url, title, note, tags) VALUES (?, ?, ?, ?, ?)",
        (uid, v["url"], v["title"], v["note"], v["tags"]),
    )
    flash("Link kaydedildi.", "success")
    return redirect_back("links.index")


@bp.route("/paylas")
@login_required
def share():
    """Paylaşım hedefi: formu doldurur, kaydetmek için kullanıcı onaylar (GET'te yazma yok)."""
    url = (request.args.get("url") or "").strip()
    title = (request.args.get("title") or "").strip()
    text = (request.args.get("text") or "").strip()
    note = ""
    if not url and text:
        m = URL_RE.search(text)
        if m:
            url = m.group(0).rstrip(".,;:!?)]}\"'")
            rest = (text[:m.start()] + " " + text[m.end():]).strip(" \t\r\n-–—:|·")
            rest = re.sub(r"\s+", " ", rest)
            if rest and not title:
                title = rest
            elif rest and rest != title:
                note = rest
        else:
            note = text
    elif text and text not in (url, title):
        note = text
    v = {"url": url[:URL_MAX], "title": title[:TITLE_MAX], "note": note[:NOTE_MAX], "tags": ""}
    return render_template("links/share.html", v=v, domain=domain_of)


@bp.route("/<int:link_id>/okundu", methods=["POST"])
@login_required
def toggle_read(link_id):
    link = owned_or_404("links", link_id, g.user["id"])
    execute("UPDATE links SET is_read = 1 - is_read WHERE id = ? AND user_id = ?", (link_id, g.user["id"]))
    flash("Okundu olarak işaretlendi." if not link["is_read"] else "Okunmamışlara alındı.", "success")
    return redirect_back("links.index")


@bp.route("/<int:link_id>", methods=["GET", "POST"])
@login_required
def edit(link_id):
    uid = g.user["id"]
    link = owned_or_404("links", link_id, uid)
    if request.method == "POST":
        v = _form_values()
        error = _url_error(v["url"])
        if error:
            flash(error, "error")
            return render_template("links/edit.html", link=link, v=v, domain=domain_of)
        v["title"] = v["title"] or domain_of(v["url"])
        execute(
            "UPDATE links SET url = ?, title = ?, note = ?, tags = ?, is_read = ? WHERE id = ? AND user_id = ?",
            (v["url"], v["title"], v["note"], v["tags"], v["is_read"], link_id, uid),
        )
        flash("Link güncellendi.", "success")
        return redirect_back("links.index")
    return render_template("links/edit.html", link=link, v=link, domain=domain_of)


@bp.route("/<int:link_id>/sil", methods=["POST"])
@login_required
def delete(link_id):
    owned_or_404("links", link_id, g.user["id"])
    execute("DELETE FROM links WHERE id = ? AND user_id = ?", (link_id, g.user["id"]))
    flash("Link silindi.", "success")
    return redirect_back("links.index")
