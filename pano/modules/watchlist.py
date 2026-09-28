"""🎬 İzleme / okuma listesi: film, dizi, kitap.

Arama: kitaplar Open Library (anahtar gerekmez), film/dizi TMDB (TMDB_API_KEY: v3 anahtarı ya da
v4 okuma belirteci). İkisi de PythonAnywhere ücretsiz izin listesinde. Anahtar yoksa film/dizi elle eklenir.
Kapak resimleri kaydedilmez; adresleri saklanır ve tarayıcı doğrudan yükler (disk harcamaz).
"""
import json
import os
import urllib.parse
import urllib.request

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from ..auth import login_required
from ..db import execute, owned_or_404, query
from ..utils import form_choice, form_int, form_str, redirect_back, today_str

bp = Blueprint("watchlist", __name__, url_prefix="/izleme")

KINDS = {"movie": ("Film", "🎬"), "series": ("Dizi", "📺"), "book": ("Kitap", "📚")}
STATUSES = {"want": "İstiyorum", "doing": "İzliyorum / okuyorum", "done": "Bitti"}
STATUS_ICONS = {"want": "🔖", "doing": "▶️", "done": "✅"}
TIMEOUT = 6
SAFE_IMAGE_HOSTS = ("https://image.tmdb.org/", "https://covers.openlibrary.org/")


def _get_json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "KisiselPano/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def tmdb_enabled():
    return bool(os.environ.get("TMDB_API_KEY", "").strip())


def search_books(q):
    params = urllib.parse.urlencode({"q": q, "limit": 10, "fields": "key,title,author_name,first_publish_year,cover_i"})
    data = _get_json(f"https://openlibrary.org/search.json?{params}")
    return [{
        "kind": "book", "title": d.get("title", "")[:200], "year": str(d.get("first_publish_year") or ""),
        "creator": ", ".join((d.get("author_name") or [])[:2])[:120],
        "image_url": f"https://covers.openlibrary.org/b/id/{d['cover_i']}-M.jpg" if d.get("cover_i") else "",
        "external_id": d.get("key", ""),
    } for d in data.get("docs", []) if d.get("title")]


def search_screen(q):
    key = os.environ.get("TMDB_API_KEY", "").strip()
    params = {"query": q, "language": "tr-TR", "include_adult": "false"}
    headers = {}
    if key.startswith("eyJ"):  # v4 okuma belirteci
        headers["Authorization"] = f"Bearer {key}"
    else:
        params["api_key"] = key
    data = _get_json("https://api.themoviedb.org/3/search/multi?" + urllib.parse.urlencode(params), headers)
    out = []
    for r in data.get("results", []):
        if r.get("media_type") not in ("movie", "tv"):
            continue
        is_movie = r["media_type"] == "movie"
        date = r.get("release_date" if is_movie else "first_air_date") or ""
        out.append({
            "kind": "movie" if is_movie else "series", "title": (r.get("title") or r.get("name") or "")[:200],
            "year": date[:4], "creator": "",
            "image_url": f"https://image.tmdb.org/t/p/w185{r['poster_path']}" if r.get("poster_path") else "",
            "external_id": f"tmdb:{r['media_type']}:{r.get('id')}",
        })
    return out[:10]


def _safe_image(url):
    url = (url or "").strip()[:300]
    return url if url.startswith(SAFE_IMAGE_HOSTS) else ""


@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    kind = request.args.get("tur", "")
    status = request.args.get("durum", "")
    sql, args = "SELECT * FROM watchlist WHERE user_id = ?", [uid]
    if kind in KINDS:
        sql += " AND kind = ?"
        args.append(kind)
    if status in STATUSES:
        sql += " AND status = ?"
        args.append(status)
    sql += " ORDER BY CASE status WHEN 'doing' THEN 0 WHEN 'want' THEN 1 ELSE 2 END, COALESCE(finished_at, created_at) DESC"
    counts = {r["status"]: r["n"] for r in query("SELECT status, COUNT(*) AS n FROM watchlist WHERE user_id = ? GROUP BY status", (uid,))}
    return render_template("watchlist/index.html", items=query(sql, args), kinds=KINDS, statuses=STATUSES,
                           status_icons=STATUS_ICONS, kind=kind, status=status, counts=counts, tmdb=tmdb_enabled())


@bp.route("/ara")
@login_required
def search():
    q = request.args.get("q", "").strip()[:100]
    kind = request.args.get("tur", "book")
    results, error = [], None
    if q:
        try:
            if kind == "book":
                results = search_books(q)
            elif tmdb_enabled():
                results = search_screen(q)
            else:
                error = "Film/dizi araması için yöneticinin TMDB_API_KEY ayarlaması gerekiyor; aşağıdan elle ekleyebilirsin."
        except Exception:
            error = "Arama servisine şu an ulaşılamadı; elle ekleyebilirsin."
    return render_template("watchlist/search.html", q=q, kind=kind, results=results, error=error, kinds=KINDS,
                           tmdb=tmdb_enabled())


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    title = form_str("title", 200)
    if not title:
        flash("Ad boş olamaz.", "warning")
        return redirect(url_for(".index"))
    kind = form_choice("kind", KINDS, "movie")
    external_id = form_str("external_id", 100)
    if external_id and query("SELECT 1 FROM watchlist WHERE user_id = ? AND external_id = ?", (g.user["id"], external_id)):
        flash(f"“{title}” zaten listende.", "warning")
        return redirect(url_for(".index"))
    status = form_choice("status", STATUSES, "want")
    execute(
        "INSERT INTO watchlist (user_id, kind, title, year, creator, image_url, external_id, status, started_at, finished_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (g.user["id"], kind, title, form_str("year", 10), form_str("creator", 120), _safe_image(request.form.get("image_url")),
         external_id, status, today_str() if status == "doing" else None, today_str() if status == "done" else None),
    )
    flash(f"{KINDS[kind][1]} {title} listene eklendi.", "success")
    return redirect(url_for(".index", tur=kind))


@bp.route("/<int:item_id>", methods=["GET", "POST"])
@login_required
def edit(item_id):
    item = owned_or_404("watchlist", item_id, g.user["id"])
    if request.method == "POST":
        status = form_choice("status", STATUSES, item["status"])
        rating = form_int("rating")
        rating = rating if rating in (1, 2, 3, 4, 5) else None
        started = item["started_at"] or (today_str() if status in ("doing", "done") else None)
        finished = (item["finished_at"] or today_str()) if status == "done" else None
        execute(
            "UPDATE watchlist SET title = ?, kind = ?, year = ?, creator = ?, status = ?, rating = ?, note = ?,"
            " started_at = ?, finished_at = ? WHERE id = ? AND user_id = ?",
            (form_str("title", 200) or item["title"], form_choice("kind", KINDS, item["kind"]), form_str("year", 10),
             form_str("creator", 120), status, rating, form_str("note", 1000), started, finished, item_id, g.user["id"]),
        )
        flash("Güncellendi.", "success")
        return redirect(url_for(".index"))
    return render_template("watchlist/edit.html", item=item, kinds=KINDS, statuses=STATUSES)


@bp.route("/<int:item_id>/durum", methods=["POST"])
@login_required
def set_status(item_id):
    item = owned_or_404("watchlist", item_id, g.user["id"])
    status = form_choice("status", STATUSES, item["status"])
    started = item["started_at"] or (today_str() if status in ("doing", "done") else None)
    finished = (item["finished_at"] or today_str()) if status == "done" else None
    execute("UPDATE watchlist SET status = ?, started_at = ?, finished_at = ? WHERE id = ?",
            (status, started, finished, item_id))
    return redirect_back("watchlist.index")


@bp.route("/<int:item_id>/sil", methods=["POST"])
@login_required
def delete(item_id):
    item = owned_or_404("watchlist", item_id, g.user["id"])
    execute("DELETE FROM watchlist WHERE id = ? AND user_id = ?", (item_id, g.user["id"]))
    flash(f"{item['title']} silindi.", "success")
    return redirect(url_for(".index"))
