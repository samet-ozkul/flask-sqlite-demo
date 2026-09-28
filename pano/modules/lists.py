"""🛒 Listeler: alışveriş ve yapılacaklar listeleri, paylaşılan listeler.

Erişim kuralı:
- Listenin sahibi her şeyi yapabilir.
- shared=1 olan listeyi tüm kullanıcılar görebilir, madde ekleyebilir, işaretleyebilir
  ve kendi eklediği maddeyi silebilir.
- Liste ayarları (ad, paylaşım, silme) ve "tamamlananları temizle" sadece sahibine açıktır.
- Erişemediği listeyi kullanıcı hiç görmez (404); görüp yönetemediği işlemde 403.

Tarifler modülü accessible_lists / list_or_404 / add_items yardımcılarını kullanır.
Yapılacaklara saat ve Telegram hatırlatması eklenebilir (bkz. pano/todo_reminders.py).
"""
import re

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import telegram
from .. import todo_reminders as todo
from ..auth import login_required
from ..db import execute, get_db, query, query_one
from ..utils import form_bool, form_choice, form_date, form_str, redirect_back

bp = Blueprint("lists", __name__, url_prefix="/listeler")

KINDS = {"shopping": "Alışveriş", "todo": "Yapılacaklar"}
KIND_ICONS = {"shopping": "🛒", "todo": "☑️"}
MAX_BATCH = 50        # tek seferde eklenebilecek madde
TEXT_MAX = 200
QTY_MAX = 30
DONE_SHOWN = 30       # tamamlananlar bölümünde gösterilen son madde sayısı

_LIST_SELECT = (
    "SELECT l.*, u.username AS owner_username, u.display_name AS owner_display"
    " FROM lists l JOIN users u ON u.id = l.user_id"
)


# ---------- Yardımcılar (tarifler de kullanır) ----------
def accessible_lists(user_id, kind=None):
    """Kullanıcının kendi listeleri + paylaşılan listeler; önce kendi listeleri."""
    sql = _LIST_SELECT + " WHERE (l.user_id = ? OR l.shared = 1)"
    args = [user_id]
    if kind:
        sql += " AND l.kind = ?"
        args.append(kind)
    sql += " ORDER BY (l.user_id = ?) DESC, l.name COLLATE NOCASE, l.id"
    args.append(user_id)
    return query(sql, args)


def list_or_404(list_id, user_id, owner_only=False):
    """Erişilebilir listeyi döner. Erişim yoksa 404, owner_only iken sahibi değilse 403."""
    row = query_one(_LIST_SELECT + " WHERE l.id = ? AND (l.user_id = ? OR l.shared = 1)", (list_id, user_id))
    if row is None:
        abort(404)
    if owner_only and row["user_id"] != user_id:
        abort(403)
    return row


def split_items(text, kind):
    """Alışveriş listesinde virgül ya da satır sonu ile birden çok madde; yapılacaklarda satır sonu."""
    pattern = r"[,\n]" if kind == "shopping" else r"\n"
    out, seen = [], set()
    for part in re.split(pattern, text or ""):
        part = part.strip().lstrip("-*•·").strip()[:TEXT_MAX]
        if part and part.lower() not in seen:
            seen.add(part.lower())
            out.append(part)
    return out[:MAX_BATCH]


def add_items(list_id, texts, user_id, qty="", due_date=None, due_time=None, remind_before=None, repeat=None,
              assignee_id=None):
    db = get_db()
    for text in texts:
        db.execute(
            "INSERT INTO list_items (list_id, text, qty, due_date, due_time, remind_before, repeat, created_by, assignee_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (list_id, text, qty, due_date, due_time, remind_before, repeat, user_id, assignee_id),
        )
    db.commit()
    return len(texts)


# ---------- İş atama ----------
def user_options():
    """[(id, görünen ad)] — atama listesi için tüm kullanıcılar."""
    return [(r["id"], r["display_name"] or r["username"])
            for r in query("SELECT id, username, display_name FROM users ORDER BY display_name, username")]


def _assignee(lst):
    """Formdaki atanan kişi; sadece paylaşılan listede ve var olan kullanıcıysa."""
    if not lst["shared"]:
        return None
    raw = (request.form.get("assignee") or "").strip()
    if not raw.isdigit():
        return None
    return int(raw) if query_one("SELECT 1 FROM users WHERE id = ?", (int(raw),)) else None


def notify_assignee(item_id, assigner):
    """Atanan kişiye Telegram'dan haber verir (kendine atadıysa ya da Telegram yoksa sessiz)."""
    from ..utils import fmt_date
    item = query_one(
        "SELECT i.*, l.name AS list_name, u.telegram_chat_id AS chat_id FROM list_items i"
        " JOIN lists l ON l.id = i.list_id JOIN users u ON u.id = i.assignee_id WHERE i.id = ?", (item_id,))
    if not item or not item["chat_id"] or item["assignee_id"] == assigner["id"] or not telegram.enabled():
        return
    esc = telegram.escape
    when = (f" · {fmt_date(item['due_date'], True)}" + (f" {item['due_time']}" if item["due_time"] else "")) if item["due_date"] else ""
    text = (f"📋 <b>{esc(assigner['display_name'] or assigner['username'])}</b> sana bir iş atadı: {esc(item['text'])}\n"
            f"{esc(item['list_name'])}{when}\n"
            f'<a href="{esc(url_for("lists.detail", list_id=item["list_id"], _external=True))}">Listeyi aç →</a>')
    try:
        telegram.send_message(item["chat_id"], text,
                              buttons=todo.done_buttons(item_id) if telegram.webhook_active() else None)
    except telegram.TelegramError:
        pass


def _due_fields():
    """Formdaki son tarih, saat, hatırlatma ve tekrar. Tarih yoksa diğerleri anlamsız."""
    due_date = form_date("due_date")
    if not due_date:
        return None, None, None, None
    return (due_date, todo.parse_time(request.form.get("due_time")), todo.parse_remind(request.form.get("remind")),
            todo.parse_repeat(request.form.get("repeat")))


def _can_edit(item, user_id):
    return item["list_owner"] == user_id or item["created_by"] == user_id


def _item_or_404(item_id, user_id):
    row = query_one(
        "SELECT i.*, l.user_id AS list_owner FROM list_items i JOIN lists l ON l.id = i.list_id"
        " WHERE i.id = ? AND (l.user_id = ? OR l.shared = 1)",
        (item_id, user_id),
    )
    if row is None:
        abort(404)
    return row


# ---------- Rotalar ----------
@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    lists = query(
        "SELECT l.*, u.username AS owner_username, u.display_name AS owner_display,"
        " (SELECT COUNT(*) FROM list_items i WHERE i.list_id = l.id AND i.done = 0) AS open_count,"
        " (SELECT COUNT(*) FROM list_items i WHERE i.list_id = l.id) AS total_count,"
        " (SELECT MIN(i.due_date) FROM list_items i WHERE i.list_id = l.id AND i.done = 0) AS next_due"
        " FROM lists l JOIN users u ON u.id = l.user_id"
        " WHERE l.user_id = ? OR l.shared = 1"
        " ORDER BY (l.user_id = ?) DESC, l.name COLLATE NOCASE, l.id",
        (uid, uid),
    )
    assigned = query(
        "SELECT i.*, l.name AS list_name FROM list_items i JOIN lists l ON l.id = i.list_id"
        " WHERE i.done = 0 AND i.assignee_id = ? AND (l.user_id = ? OR l.shared = 1)"
        " ORDER BY i.due_date IS NULL, i.due_date, i.id", (uid, uid))
    return render_template("lists/index.html", lists=lists, kinds=KINDS, icons=KIND_ICONS, assigned=assigned)


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    name = form_str("name", 80)
    if not name:
        flash("Liste adı boş olamaz.", "warning")
        return redirect(url_for("lists.index"))
    kind = form_choice("kind", KINDS, "todo")
    list_id = execute(
        "INSERT INTO lists (user_id, name, kind, shared) VALUES (?, ?, ?, ?)",
        (g.user["id"], name, kind, form_bool("shared")),
    ).lastrowid
    flash(f"“{name}” listesi oluşturuldu.", "success")
    return redirect(url_for("lists.detail", list_id=list_id))


@bp.route("/<int:list_id>")
@login_required
def detail(list_id):
    uid = g.user["id"]
    lst = list_or_404(list_id, uid)
    item_sql = (
        "SELECT i.*, u.username AS creator_username, u.display_name AS creator_display,"
        " a.username AS assignee_username, a.display_name AS assignee_display"
        " FROM list_items i LEFT JOIN users u ON u.id = i.created_by LEFT JOIN users a ON a.id = i.assignee_id"
        " WHERE i.list_id = ?"
    )
    open_items = query(
        item_sql + " AND i.done = 0 ORDER BY i.due_date IS NULL, i.due_date, i.created_at, i.id", (list_id,)
    )
    done_items = query(
        item_sql + " AND i.done = 1 ORDER BY i.done_at DESC, i.id DESC LIMIT ?", (list_id, DONE_SHOWN)
    )
    done_count = query_one(
        "SELECT COUNT(*) AS n FROM list_items WHERE list_id = ? AND done = 1", (list_id,)
    )["n"]
    return render_template(
        "lists/detail.html", lst=lst, open_items=open_items, done_items=done_items, done_count=done_count,
        is_owner=lst["user_id"] == uid, kinds=KINDS, icons=KIND_ICONS,
        remind_options=todo.REMIND_OPTIONS, default_remind=todo.DEFAULT_REMIND, remind_label=todo.remind_label,
        repeat_options=todo.REPEAT_OPTIONS, repeat_label=todo.repeat_label,
        default_time=todo.DEFAULT_DUE_TIME, telegram_enabled=telegram.enabled(),
        users=user_options() if lst["shared"] else [],
    )


@bp.route("/<int:list_id>/ekle", methods=["POST"])
@login_required
def add_item(list_id):
    lst = list_or_404(list_id, g.user["id"])
    texts = split_items(request.form.get("text", "")[:10000], lst["kind"])
    if not texts:
        flash("Madde boş olamaz.", "warning")
        return redirect_back("lists.detail", list_id=list_id)
    if lst["kind"] == "todo":
        due_date, due_time, remind, repeat = _due_fields()
    else:
        due_date, due_time, remind, repeat = form_date("due_date"), None, None, None
    assignee = _assignee(lst) if lst["kind"] == "todo" else None
    n = add_items(list_id, texts, g.user["id"], form_str("qty", QTY_MAX), due_date, due_time, remind, repeat, assignee)
    if assignee and assignee != g.user["id"]:
        for row in query("SELECT id FROM list_items WHERE list_id = ? ORDER BY id DESC LIMIT ?", (list_id, n)):
            notify_assignee(row["id"], g.user)
    flash(f"“{texts[0]}” eklendi." if n == 1 else f"{n} madde eklendi.", "success")
    return redirect_back("lists.detail", list_id=list_id)


@bp.route("/madde/<int:item_id>/isaretle", methods=["POST"])
@login_required
def toggle_item(item_id):
    item = _item_or_404(item_id, g.user["id"])
    todo.set_done(item_id, not item["done"])  # tekrarlayan maddede sonrakini de oluşturur
    # Liste sayfasında her dokunuşta mesaj göstermeyelim; başka sayfadan (pano) gelindiyse bildir
    if request.form.get("next") or request.args.get("next"):
        flash(f"“{item['text']}” " + ("tamamlandı." if not item["done"] else "yeniden açıldı."), "success")
    return redirect_back("lists.detail", list_id=item["list_id"])


@bp.route("/madde/<int:item_id>", methods=["GET", "POST"])
@login_required
def edit_item(item_id):
    uid = g.user["id"]
    item = _item_or_404(item_id, uid)
    if not _can_edit(item, uid):
        abort(403)
    lst = list_or_404(item["list_id"], uid)
    if request.method == "POST":
        text = form_str("text", TEXT_MAX)
        if not text:
            flash("Madde boş olamaz.", "warning")
            return redirect(url_for("lists.edit_item", item_id=item_id))
        if lst["kind"] == "todo":
            due_date, due_time, remind, repeat = _due_fields()
        else:
            due_date, due_time, remind, repeat = form_date("due_date"), None, None, None
        # Zaman ya da hatırlatma değiştiyse hatırlatmalar yeniden gönderilebilsin
        changed = (due_date, due_time, remind) != (item["due_date"], item["due_time"], item["remind_before"])
        assignee = _assignee(lst) if lst["kind"] == "todo" else None
        execute(
            "UPDATE list_items SET text = ?, qty = ?, due_date = ?, due_time = ?, remind_before = ?, repeat = ?, assignee_id = ?"
            + (", pre_sent_at = NULL, due_sent_at = NULL" if changed else "") + " WHERE id = ?",
            (text, form_str("qty", QTY_MAX), due_date, due_time, remind, repeat, assignee, item_id),
        )
        if assignee and assignee != item["assignee_id"] and assignee != uid:
            notify_assignee(item_id, g.user)
        flash("Madde güncellendi.", "success")
        return redirect_back("lists.detail", list_id=item["list_id"])
    return render_template(
        "lists/item_edit.html", item=item, lst=lst, remind_options=todo.REMIND_OPTIONS,
        repeat_options=todo.REPEAT_OPTIONS,
        default_time=todo.DEFAULT_DUE_TIME, telegram_enabled=telegram.enabled(),
        users=user_options() if lst["shared"] else [],
    )


@bp.route("/madde/<int:item_id>/sil", methods=["POST"])
@login_required
def delete_item(item_id):
    uid = g.user["id"]
    item = _item_or_404(item_id, uid)
    if not _can_edit(item, uid):
        abort(403)
    execute("DELETE FROM list_items WHERE id = ?", (item_id,))
    flash("Madde silindi.", "success")
    return redirect_back("lists.detail", list_id=item["list_id"])


@bp.route("/<int:list_id>/temizle", methods=["POST"])
@login_required
def clear_done(list_id):
    list_or_404(list_id, g.user["id"], owner_only=True)
    n = execute("DELETE FROM list_items WHERE list_id = ? AND done = 1", (list_id,)).rowcount
    flash(f"{n} tamamlanan madde temizlendi." if n else "Temizlenecek madde yok.", "success")
    return redirect(url_for("lists.detail", list_id=list_id))


@bp.route("/<int:list_id>/ayarlar", methods=["POST"])
@login_required
def update(list_id):
    uid = g.user["id"]
    list_or_404(list_id, uid, owner_only=True)
    name = form_str("name", 80)
    if not name:
        flash("Liste adı boş olamaz.", "warning")
        return redirect(url_for("lists.detail", list_id=list_id))
    execute(
        "UPDATE lists SET name = ?, shared = ? WHERE id = ? AND user_id = ?",
        (name, form_bool("shared"), list_id, uid),
    )
    flash("Liste güncellendi.", "success")
    return redirect(url_for("lists.detail", list_id=list_id))


@bp.route("/<int:list_id>/sil", methods=["POST"])
@login_required
def delete(list_id):
    uid = g.user["id"]
    lst = list_or_404(list_id, uid, owner_only=True)
    db = get_db()
    db.execute("DELETE FROM list_items WHERE list_id = ?", (list_id,))
    db.execute("DELETE FROM lists WHERE id = ? AND user_id = ?", (list_id, uid))
    db.commit()
    flash(f"“{lst['name']}” listesi silindi.", "success")
    return redirect(url_for("lists.index"))
