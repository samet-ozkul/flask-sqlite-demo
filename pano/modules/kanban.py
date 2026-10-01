"""🗂️ Kanban: sütunlu iş/proje panoları; kartlar sürükle-bırak ya da ← → ↑ ↓ butonlarıyla taşınır.

Erişim kuralı (listelerdeki gibi):
- shared=1 olan panoyu uygulamadaki herkes görür; sütunları ve kartları herkes düzenleyebilir.
- Pano ayarları (ad, paylaşım) ve panoyu silmek sadece sahibine açıktır.
- Erişemediği panoyu kullanıcı hiç görmez (404); görüp yönetemediği işlemde 403.

Sıra: sütunlarda ve kartlarda `position` tutulur, her taşımada 0..n yeniden numaralanır.
En sağdaki sütun "bitti" sayılır (panoda en az iki sütun varsa); son tarihi olup orada olmayan
kartlar yaklaşanlarda ve takvimde görünür (open_cards).
Sütun silinince kartları kaybolmaz: soldaki sütuna (en soldaysa sağdakine) taşınır; son sütun silinmez.
Sürükle-bırak kodu: static/kanban.js (JS yoksa aynı taşıma butonları formla çalışır).
"""
from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from .. import trash
from ..auth import login_required
from ..db import execute, get_db, query, query_one
from ..utils import form_bool, form_choice, form_date, form_int, form_str

bp = Blueprint("kanban", __name__, url_prefix="/kanban")

TEMPLATES = {
    "basic": ("Yapılacak / Yapılıyor / Bitti", ["Yapılacak", "Yapılıyor", "Bitti"]),
    "ideas": ("Fikir / Planlandı / Yapılıyor / Bitti", ["Fikir", "Planlandı", "Yapılıyor", "Bitti"]),
    "week": ("Bekleyenler / Bu hafta / Bugün / Bitti", ["Bekleyenler", "Bu hafta", "Bugün", "Bitti"]),
}
# Renk etiketleri; tonlar şablondaki CSS değişkenlerinde (açık/koyu tema)
COLORS = {"red": "Kırmızı", "orange": "Turuncu", "yellow": "Sarı", "green": "Yeşil", "blue": "Mavi", "purple": "Mor"}
NAME_MAX = 80         # pano adı
COLUMN_MAX = 40       # sütun adı
TITLE_MAX = 200
NOTE_MAX = 5000
MAX_COLUMNS = 12
CARD_LIMIT = 500      # pano başına

_BOARD_SELECT = ("SELECT b.*, u.username AS owner_username, u.display_name AS owner_display"
                 " FROM boards b JOIN users u ON u.id = b.user_id")

# Kart (c) panosunun "bitti" sütununda mı: en sağdaki sütun, panoda en az iki sütun varsa
IS_DONE = ("(c.column_id = (SELECT x.id FROM board_columns x WHERE x.board_id = c.board_id"
           " ORDER BY x.position DESC, x.id DESC LIMIT 1)"
           " AND (SELECT COUNT(*) FROM board_columns x WHERE x.board_id = c.board_id) > 1)")

# Kart + pano ve sütun adı; arama da kullanır
CARD_SELECT = ("SELECT c.*, b.name AS board_name, b.user_id AS board_owner, bc.name AS column_name,"
               f" {IS_DONE} AS done FROM cards c JOIN boards b ON b.id = c.board_id"
               " JOIN board_columns bc ON bc.id = c.column_id")


# ---------- Yardımcılar ----------
def board_or_404(board_id, user_id, owner_only=False):
    """Erişilebilir panoyu döner. Erişim yoksa 404, owner_only iken sahibi değilse 403."""
    row = query_one(_BOARD_SELECT + " WHERE b.id = ? AND (b.user_id = ? OR b.shared = 1)", (board_id, user_id))
    if row is None:
        abort(404)
    if owner_only and row["user_id"] != user_id:
        abort(403)
    return row


def _card_or_404(card_id, user_id):
    row = query_one(CARD_SELECT + " WHERE c.id = ? AND (b.user_id = ? OR b.shared = 1)", (card_id, user_id))
    if row is None:
        abort(404)
    return row


def _column_or_404(column_id, user_id):
    row = query_one("SELECT bc.* FROM board_columns bc JOIN boards b ON b.id = bc.board_id"
                    " WHERE bc.id = ? AND (b.user_id = ? OR b.shared = 1)", (column_id, user_id))
    if row is None:
        abort(404)
    return row


def open_cards(user_id, where="", args=()):
    """Son tarihli ve bitti sütununda olmayan kartlar (kendi + paylaşılan panolar): yaklaşanlar ve takvim."""
    return query(CARD_SELECT + f" WHERE c.due_date IS NOT NULL AND NOT {IS_DONE} AND (b.user_id = ? OR b.shared = 1)"
                 + where + " ORDER BY c.due_date, c.id", (user_id, *args))


def _column_ids(board_id):
    return [r["id"] for r in query("SELECT id FROM board_columns WHERE board_id = ? ORDER BY position, id", (board_id,))]


def _card_ids(column_id, exclude=None):
    """Sütundaki kartlar sırasıyla; exclude verilirse o kart hariç."""
    return [r["id"] for r in query("SELECT id FROM cards WHERE column_id = ? AND id IS NOT ? ORDER BY position, id",
                                   (column_id, exclude))]


def _renumber(table, ids):
    """Sırayı 0..n olarak yazar (commit etmez)."""
    db = get_db()
    for position, row_id in enumerate(ids):
        db.execute(f"UPDATE {table} SET position = ? WHERE id = ?", (position, row_id))


def move_card(card, column_id, position=None):
    """Kartı sütunda `position` sırasına koyar (None: sona); eski ve yeni sütunu 0..n yeniden numaralar.
    Kartın konduğu sırayı döner."""
    target = _card_ids(column_id, exclude=card["id"])
    position = len(target) if position is None else max(0, min(position, len(target)))
    target.insert(position, card["id"])
    db = get_db()
    if column_id != card["column_id"]:
        db.execute("UPDATE cards SET column_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (column_id, card["id"]))
        _renumber("cards", _card_ids(card["column_id"]))
    _renumber("cards", target)
    db.commit()
    return position


def _wants_json():
    return request.accept_mimetypes.best == "application/json"


def _to_board(board_id, anchor=None):
    return redirect(url_for(".board", board_id=board_id, _anchor=anchor))


# ---------- Panolar ----------
@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    boards = query(
        "SELECT b.*, u.username AS owner_username, u.display_name AS owner_display,"
        " (SELECT COUNT(*) FROM board_columns bc WHERE bc.board_id = b.id) AS column_count,"
        " (SELECT COUNT(*) FROM cards c WHERE c.board_id = b.id) AS card_count,"
        f" (SELECT COUNT(*) FROM cards c WHERE c.board_id = b.id AND NOT {IS_DONE}) AS open_count,"
        f" (SELECT MIN(c.due_date) FROM cards c WHERE c.board_id = b.id AND NOT {IS_DONE}) AS next_due"
        " FROM boards b JOIN users u ON u.id = b.user_id"
        " WHERE b.user_id = ? OR b.shared = 1"
        " ORDER BY (b.user_id = ?) DESC, b.name COLLATE NOCASE, b.id",
        (uid, uid),
    )
    return render_template("kanban/index.html", boards=boards,
                           templates=[(k, label) for k, (label, _cols) in TEMPLATES.items()])


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    name = form_str("name", NAME_MAX)
    if not name:
        flash("Pano adı boş olamaz.", "warning")
        return redirect(url_for(".index"))
    template = form_choice("template", TEMPLATES, "basic")
    db = get_db()
    board_id = db.execute("INSERT INTO boards (user_id, name, shared) VALUES (?, ?, ?)",
                          (g.user["id"], name, form_bool("shared"))).lastrowid
    for position, column in enumerate(TEMPLATES[template][1]):
        db.execute("INSERT INTO board_columns (board_id, name, position) VALUES (?, ?, ?)", (board_id, column, position))
    db.commit()
    flash(f"“{name}” panosu oluşturuldu.", "success")
    return _to_board(board_id)


@bp.route("/<int:board_id>")
@login_required
def board(board_id):
    uid = g.user["id"]
    b = board_or_404(board_id, uid)
    columns = query("SELECT * FROM board_columns WHERE board_id = ? ORDER BY position, id", (board_id,))
    cards = {}
    for c in query("SELECT * FROM cards WHERE board_id = ? ORDER BY position, id", (board_id,)):
        cards.setdefault(c["column_id"], []).append(c)
    return render_template("kanban/board.html", board=b, columns=columns, cards=cards, is_owner=b["user_id"] == uid,
                           max_columns=MAX_COLUMNS)


@bp.route("/<int:board_id>/ayarlar", methods=["POST"])
@login_required
def update(board_id):
    uid = g.user["id"]
    board_or_404(board_id, uid, owner_only=True)
    name = form_str("name", NAME_MAX)
    if not name:
        flash("Pano adı boş olamaz.", "warning")
        return _to_board(board_id)
    execute("UPDATE boards SET name = ?, shared = ? WHERE id = ? AND user_id = ?",
            (name, form_bool("shared"), board_id, uid))
    flash("Pano güncellendi.", "success")
    return _to_board(board_id)


@bp.route("/<int:board_id>/sil", methods=["POST"])
@login_required
def delete(board_id):
    uid = g.user["id"]
    b = board_or_404(board_id, uid, owner_only=True)
    # Sıra önemli: geri getirirken kartlar bağlı oldukları sütunlardan sonra yerine konur
    trash.move(uid, "kanban", f"🗂️ {b['name']} panosu", ("boards", board_id),
               children=[("board_columns", "board_id = ?"), ("cards", "board_id = ?")])
    flash(trash.notice(f"“{b['name']}” panosu"), "success")
    return redirect(url_for(".index"))


# ---------- Sütunlar ----------
@bp.route("/<int:board_id>/sutun", methods=["POST"])
@login_required
def add_column(board_id):
    board_or_404(board_id, g.user["id"])
    name = form_str("name", COLUMN_MAX)
    count = len(_column_ids(board_id))
    if not name:
        flash("Sütun adı boş olamaz.", "warning")
    elif count >= MAX_COLUMNS:
        flash(f"Bir panoda en fazla {MAX_COLUMNS} sütun olabilir.", "warning")
    else:
        column_id = execute("INSERT INTO board_columns (board_id, name, position) VALUES (?, ?, ?)",
                            (board_id, name, count)).lastrowid
        return _to_board(board_id, f"sutun-{column_id}")
    return _to_board(board_id)


@bp.route("/sutun/<int:column_id>", methods=["POST"])
@login_required
def rename_column(column_id):
    col = _column_or_404(column_id, g.user["id"])
    name = form_str("name", COLUMN_MAX)
    if name:
        execute("UPDATE board_columns SET name = ? WHERE id = ?", (name, column_id))
    else:
        flash("Sütun adı boş olamaz.", "warning")
    return _to_board(col["board_id"], f"sutun-{column_id}")


@bp.route("/sutun/<int:column_id>/tasi", methods=["POST"])
@login_required
def move_column(column_id):
    """dir=left/right: komşu sütunla yer değiştirir."""
    col = _column_or_404(column_id, g.user["id"])
    ids = _column_ids(col["board_id"])
    i = ids.index(column_id)
    j = i - 1 if request.form.get("dir") == "left" else i + 1
    if 0 <= j < len(ids):
        ids[i], ids[j] = ids[j], ids[i]
        _renumber("board_columns", ids)
        get_db().commit()
    return _to_board(col["board_id"], f"sutun-{column_id}")


@bp.route("/sutun/<int:column_id>/sil", methods=["POST"])
@login_required
def delete_column(column_id):
    """Kartlar soldaki sütunun (en soldaysa sağdakinin) sonuna taşınır; panonun son sütunu silinmez."""
    col = _column_or_404(column_id, g.user["id"])
    board_id = col["board_id"]
    ids = _column_ids(board_id)
    if len(ids) == 1:
        flash("Panoda en az bir sütun kalmalı.", "warning")
        return _to_board(board_id)
    i = ids.index(column_id)
    target = ids[i - 1] if i > 0 else ids[1]
    moving = _card_ids(column_id)
    existing = _card_ids(target)
    db = get_db()
    db.execute("UPDATE cards SET column_id = ?, updated_at = CURRENT_TIMESTAMP WHERE column_id = ?", (target, column_id))
    _renumber("cards", existing + moving)
    db.execute("DELETE FROM board_columns WHERE id = ?", (column_id,))
    ids.remove(column_id)
    _renumber("board_columns", ids)
    db.commit()
    if moving:
        target_name = query_one("SELECT name FROM board_columns WHERE id = ?", (target,))["name"]
        flash(f"“{col['name']}” sütunu silindi; {len(moving)} kart “{target_name}” sütununa taşındı.", "success")
    else:
        flash(f"“{col['name']}” sütunu silindi.", "success")
    return _to_board(board_id)


# ---------- Kartlar ----------
@bp.route("/<int:board_id>/kart", methods=["POST"])
@login_required
def add_card(board_id):
    board_or_404(board_id, g.user["id"])
    column = query_one("SELECT id FROM board_columns WHERE id = ? AND board_id = ?", (form_int("column_id"), board_id))
    if column is None:
        abort(400)
    title = form_str("title", TITLE_MAX)
    if not title:
        flash("Kart başlığı boş olamaz.", "warning")
        return _to_board(board_id, f"sutun-{column['id']}")
    if query_one("SELECT COUNT(*) AS n FROM cards WHERE board_id = ?", (board_id,))["n"] >= CARD_LIMIT:
        flash(f"Bir panoda en fazla {CARD_LIMIT} kart olabilir.", "warning")
        return _to_board(board_id)
    position = query_one("SELECT COALESCE(MAX(position) + 1, 0) AS p FROM cards WHERE column_id = ?", (column["id"],))["p"]
    card_id = execute("INSERT INTO cards (board_id, column_id, title, position, created_by) VALUES (?, ?, ?, ?, ?)",
                      (board_id, column["id"], title, position, g.user["id"])).lastrowid
    return _to_board(board_id, f"kart-{card_id}")


@bp.route("/kart/<int:card_id>", methods=["GET", "POST"])
@login_required
def card(card_id):
    c = _card_or_404(card_id, g.user["id"])
    columns = query("SELECT id, name FROM board_columns WHERE board_id = ? ORDER BY position, id", (c["board_id"],))
    if request.method == "POST":
        title = form_str("title", TITLE_MAX)
        if not title:
            flash("Kart başlığı boş olamaz.", "warning")
            return redirect(url_for(".card", card_id=card_id))
        color = request.form.get("color", "")
        execute("UPDATE cards SET title = ?, note = ?, due_date = ?, color = ?, updated_at = CURRENT_TIMESTAMP"
                " WHERE id = ?",
                (title, form_str("note", NOTE_MAX), form_date("due_date"), color if color in COLORS else "", card_id))
        column_id = form_int("column_id")
        if column_id != c["column_id"] and column_id in [col["id"] for col in columns]:
            move_card(c, column_id)  # yeni sütunun sonuna
        flash("Kart güncellendi.", "success")
        return _to_board(c["board_id"], f"kart-{card_id}")
    options = [(col["id"], col["name"]) for col in columns]
    if len(options) > 1:
        options[-1] = (options[-1][0], options[-1][1] + " (bitti)")
    creator = query_one("SELECT username, display_name FROM users WHERE id = ?", (c["created_by"],))
    return render_template("kanban/card.html", card=c, column_options=options, colors=COLORS, creator=creator)


@bp.route("/kart/<int:card_id>/tasi", methods=["POST"])
@login_required
def move(card_id):
    """Sürükle-bırak: column_id + position (0'dan; boşsa sona) -> JSON.
    Menüden (JS yoksa): dir=left/right (komşu sütunun sonuna) ya da up/down -> panoya döner."""
    c = _card_or_404(card_id, g.user["id"])
    error = None
    direction = request.form.get("dir")
    if direction in ("up", "down"):
        column_id = c["column_id"]
        position = _card_ids(column_id).index(card_id) + (-1 if direction == "up" else 1)
    elif direction in ("left", "right"):
        ids = _column_ids(c["board_id"])
        j = ids.index(c["column_id"]) + (-1 if direction == "left" else 1)
        column_id, position = (ids[j], None) if 0 <= j < len(ids) else (None, None)
        if column_id is None:
            error = "Bu yönde sütun yok."
    else:
        column_id, position = form_int("column_id"), form_int("position")
        if column_id not in _column_ids(c["board_id"]):
            error = "Kart sadece aynı panodaki bir sütuna taşınabilir."
    if error is None:
        position = move_card(c, column_id, position)
    if _wants_json():
        if error:
            return jsonify(error=error), 400
        return jsonify(ok=True, column_id=column_id, position=position)
    if error:
        flash(error, "warning")
    return _to_board(c["board_id"], f"kart-{card_id}")


@bp.route("/kart/<int:card_id>/sil", methods=["POST"])
@login_required
def delete_card(card_id):
    uid = g.user["id"]
    c = _card_or_404(card_id, uid)
    trash.move(uid, "kanban", f"🗂️ {c['title']} ({c['board_name']})", ("cards", card_id))
    flash(trash.notice("Kart"), "success")
    return _to_board(c["board_id"], f"sutun-{c['column_id']}")
