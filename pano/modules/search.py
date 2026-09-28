"""🔍 Genel arama sayfası (mantık pano/search.py'de)."""
from flask import Blueprint, g, render_template, request

from ..auth import login_required
from ..search import search

bp = Blueprint("search", __name__, url_prefix="/ara")


@bp.route("/")
@login_required
def index():
    q = request.args.get("q", "").strip()[:100]
    groups = search(g.user["id"], q) if q else []
    return render_template("search/index.html", q=q, groups=groups, total=sum(gr["count"] for gr in groups))
