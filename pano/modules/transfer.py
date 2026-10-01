"""📤 Cihazlar arası aktarma: telefondan bilgisayara (ya da tersi) metin ve dosya.

- Her şey süreli: varsayılan 1 saat (10 dk – 24 saat); süresi dolan kayıt ve dosyası kendiliğinden silinir
  (sayfa her açıldığında ve 5 dakikalık cron'da)
- Dosya başına TRANSFER_MAX_MB (varsayılan 25), kişi başına aynı anda TRANSFER_TOTAL_MB (varsayılan 100);
  genel disk kotası (STORAGE_QUOTA_MB) da aşılamaz
- "Tek seferlik" dosya ilk indirmede silinir
- Dosyalar uploads/_aktarma/ altında rastgele adlarla durur; yedeğe girmez, her zaman indirme olarak
  sunulur (tarayıcıda açılıp çalıştırılamaz); önizleme sadece PNG/JPEG/GIF/WebP için
- Telegram: bota gönderilen metin ya da dosya "📤 Aktar" ile buraya konur; buradan da Telegram'a gönderilebilir
"""
import io
import os
import time
import uuid

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for)

from .. import telegram
from ..auth import large_upload, login_required
from ..db import execute, get_db, query, query_one
from ..utils import fmt_size

bp = Blueprint("transfer", __name__, url_prefix="/aktar")

MAX_FILE_MB = int(os.environ.get("TRANSFER_MAX_MB", "25"))
MAX_TOTAL_MB = int(os.environ.get("TRANSFER_TOTAL_MB", "100"))
TEXT_MAX = 50_000
ITEM_LIMIT = 50
FILES_PER_UPLOAD = 10
DURATIONS = [(10, "10 dakika"), (60, "1 saat"), (360, "6 saat"), (1440, "24 saat")]
DEFAULT_MINUTES = 60
PREVIEW_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
DIR_NAME = "_aktarma"   # uploads içinde; yedek ve sahipsiz dosya temizliği bu klasörü atlar


def transfer_dir(user_id=None):
    base = os.path.join(current_app.config["UPLOAD_DIR"], DIR_NAME)
    path = os.path.join(base, str(user_id)) if user_id is not None else base
    os.makedirs(path, exist_ok=True)
    return path


def device_label(user_agent):
    ua = (user_agent or "").lower()
    for key, label in (("iphone", "📱 iPhone"), ("ipad", "📱 iPad"), ("android", "📱 Android"),
                       ("windows", "💻 Windows"), ("macintosh", "💻 Mac"), ("linux", "💻 Linux")):
        if key in ua:
            return label
    return "🌐 Tarayıcı"


def _minutes():
    raw = request.form.get("minutes", "")
    return int(raw) if raw.isdigit() and int(raw) in dict(DURATIONS) else DEFAULT_MINUTES


# ---------- Temizlik ----------
def _remove(row):
    if row["stored_name"]:
        try:
            os.remove(os.path.join(transfer_dir(row["user_id"]), row["stored_name"]))
        except OSError:
            pass


def purge_expired():
    """Süresi dolan kayıtları ve hiçbir kayda ait olmayan eski dosyaları siler. Silinen kayıt sayısı."""
    now = time.time()
    db = get_db()
    old = db.execute("SELECT * FROM transfers WHERE expires_at <= ?", (now,)).fetchall()
    for row in old:
        _remove(row)
    db.execute("DELETE FROM transfers WHERE expires_at <= ?", (now,))
    db.commit()
    known = {r["stored_name"] for r in query("SELECT stored_name FROM transfers WHERE stored_name != ''")}
    base = transfer_dir()
    for root, _dirs, files in os.walk(base):
        for name in files:
            full = os.path.join(root, name)
            try:
                # Yazılmakta olan dosyaya dokunma: kaydı birkaç saniye sonra eklenir
                if name not in known and now - os.path.getmtime(full) > 600:
                    os.remove(full)
            except OSError:
                pass
    return len(old)


# ---------- Ekleme (web ve Telegram ortak) ----------
class TransferError(Exception):
    pass


def _check_capacity(user_id, extra_bytes):
    from ..storage import quota_bytes, usage
    row = query_one("SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS s FROM transfers WHERE user_id = ?", (user_id,))
    if row["n"] >= ITEM_LIMIT:
        raise TransferError(f"Aynı anda en fazla {ITEM_LIMIT} kayıt olabilir; eskileri sil.")
    if row["s"] + extra_bytes > MAX_TOTAL_MB * 1024 * 1024:
        raise TransferError(f"Aktarma kutusu dolu (en fazla {MAX_TOTAL_MB} MB). Eski dosyaları sil ya da süresinin dolmasını bekle.")
    if extra_bytes and usage()["used"] + extra_bytes > quota_bytes():
        raise TransferError("Sunucudaki depolama alanı dolu; dosya kaydedilemedi.")


def add_text(user_id, text, minutes=DEFAULT_MINUTES, device=""):
    text = (text or "").strip()
    if not text:
        raise TransferError("Metin boş.")
    if len(text) > TEXT_MAX:
        raise TransferError(f"Metin en fazla {TEXT_MAX:,} karakter olabilir.".replace(",", "."))
    _check_capacity(user_id, 0)
    return execute("INSERT INTO transfers (user_id, kind, text, device, expires_at) VALUES (?, 'text', ?, ?, ?)",
                   (user_id, text, device, time.time() + minutes * 60)).lastrowid


def add_file(user_id, stream, filename, mime="", minutes=DEFAULT_MINUTES, once=False, device="", size_hint=None):
    """stream: .save(yol) (FileStorage) ya da bayt. Boyut sınırı aşılırsa dosya silinir ve hata verilir."""
    limit = MAX_FILE_MB * 1024 * 1024
    if size_hint is not None and size_hint > limit:
        raise TransferError(f"Dosya en fazla {MAX_FILE_MB} MB olabilir.")
    _check_capacity(user_id, size_hint or 0)
    stored = uuid.uuid4().hex
    path = os.path.join(transfer_dir(user_id), stored)
    if isinstance(stream, (bytes, bytearray)):
        with open(path, "wb") as f:
            f.write(stream)
    else:
        stream.save(path)
    size = os.path.getsize(path)
    try:
        if size == 0:
            raise TransferError("Dosya boş.")
        if size > limit:
            raise TransferError(f"Dosya en fazla {MAX_FILE_MB} MB olabilir.")
        if not size_hint:
            _check_capacity(user_id, size)
    except TransferError:
        os.remove(path)
        raise
    name = os.path.basename((filename or "dosya").replace("\\", "/")).strip()[:150] or "dosya"
    return execute(
        "INSERT INTO transfers (user_id, kind, filename, stored_name, size, mime, once, device, expires_at)"
        " VALUES (?, 'file', ?, ?, ?, ?, ?, ?, ?)",
        (user_id, name, stored, size, (mime or "application/octet-stream")[:100], 1 if once else 0, device,
         time.time() + minutes * 60)).lastrowid


# ---------- Sayfa ----------
def _items(user_id):
    now = time.time()
    out = []
    for r in query("SELECT * FROM transfers WHERE user_id = ? AND expires_at > ? ORDER BY id DESC", (user_id, now)):
        left = int(r["expires_at"] - now)
        out.append({**dict(r), "left": f"{left // 3600} sa {left % 3600 // 60} dk" if left >= 3600
                    else f"{max(1, left // 60)} dk", "preview": r["kind"] == "file" and r["mime"] in PREVIEW_MIMES
                    and not r["once"]})
    return out


def _page_data():
    uid = g.user["id"]
    items = _items(uid)
    return {"items": items, "total": sum(i["size"] for i in items), "max_file_mb": MAX_FILE_MB,
            "max_total_mb": MAX_TOTAL_MB, "durations": DURATIONS, "default_minutes": DEFAULT_MINUTES,
            "telegram_linked": bool(g.user["telegram_chat_id"]) and telegram.enabled(), "fmt_size": fmt_size}


@bp.route("/")
@login_required
def index():
    purge_expired()
    return render_template("transfer/index.html", **_page_data())


@bp.route("/liste")
@login_required
def list_fragment():
    """Sayfa birkaç saniyede bir bunu çeker: başka cihazdan eklenen hemen görünsün."""
    resp = current_app.make_response(render_template("transfer/_list.html", **_page_data()))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/yeni", methods=["POST"])
@login_required
@large_upload(min(MAX_FILE_MB * FILES_PER_UPLOAD, MAX_TOTAL_MB) + 1)
def create():
    uid = g.user["id"]
    minutes, device = _minutes(), device_label(request.user_agent.string)
    files = [f for f in request.files.getlist("files") if f and f.filename][:FILES_PER_UPLOAD]
    text = request.form.get("text", "")
    added, errors = 0, []
    if text.strip():
        try:
            add_text(uid, text, minutes, device)
            added += 1
        except TransferError as e:
            errors.append(str(e))
    for f in files:
        try:
            add_file(uid, f, f.filename, f.mimetype, minutes, bool(request.form.get("once")), device,
                     size_hint=f.content_length or None)
            added += 1
        except TransferError as e:
            errors.append(f"{f.filename}: {e}")
    for e in errors:
        flash(e, "error")
    if added:
        flash(f"📤 {added} kayıt aktarma kutusuna kondu; {dict(DURATIONS)[minutes]} sonra silinecek.", "success")
    elif not errors:
        flash("Metin yaz ya da dosya seç.", "warning")
    return redirect(url_for(".index"))


def _own(item_id):
    row = query_one("SELECT * FROM transfers WHERE id = ? AND user_id = ? AND expires_at > ?",
                    (item_id, g.user["id"], time.time()))
    if row is None:
        abort(404)
    return row


def _file_path(row):
    path = os.path.join(transfer_dir(row["user_id"]), row["stored_name"])
    if row["kind"] != "file" or not os.path.isfile(path):
        abort(404)
    return path


@bp.route("/<int:item_id>/indir")
@login_required
def download(item_id):
    row = _own(item_id)
    path = _file_path(row)
    if row["once"]:
        # Tek seferlik: belleğe al, dosyayı ve kaydı hemen sil
        with open(path, "rb") as f:
            data = io.BytesIO(f.read())
        _remove(row)
        execute("DELETE FROM transfers WHERE id = ?", (item_id,))
        resp = send_file(data, mimetype="application/octet-stream", as_attachment=True, download_name=row["filename"])
    else:
        resp = send_file(path, mimetype="application/octet-stream", as_attachment=True, download_name=row["filename"])
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.route("/<int:item_id>/onizleme")
@login_required
def preview(item_id):
    row = _own(item_id)
    if row["mime"] not in PREVIEW_MIMES or row["once"]:
        abort(404)
    resp = send_file(_file_path(row), mimetype=row["mime"])
    resp.headers["Cache-Control"] = "private, max-age=3600"  # liste yenilenince önizleme yeniden inmesin
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "default-src 'none'"
    return resp


@bp.route("/<int:item_id>/telegram", methods=["POST"])
@login_required
def to_telegram(item_id):
    row = _own(item_id)
    if not (g.user["telegram_chat_id"] and telegram.enabled()):
        flash("Önce Ayarlar'dan Telegram'ı bağla.", "warning")
        return redirect(url_for(".index"))
    try:
        if row["kind"] == "text":
            telegram.send_message(g.user["telegram_chat_id"], "📤 " + telegram.escape(row["text"][:4000]))
        else:
            if row["size"] > 50 * 1024 * 1024:
                raise telegram.TelegramError("Telegram en fazla 50 MB dosya gönderebiliyor.")
            with open(_file_path(row), "rb") as f:
                telegram.send_document(g.user["telegram_chat_id"], row["filename"], f.read(), caption="📤 Aktarma kutusundan",
                                       mime=row["mime"] or "application/octet-stream")
        flash("Telegram'a gönderildi.", "success")
    except telegram.TelegramError as e:
        flash(f"Gönderilemedi: {e}", "error")
    return redirect(url_for(".index"))


@bp.route("/<int:item_id>/sil", methods=["POST"])
@login_required
def delete(item_id):
    row = _own(item_id)
    _remove(row)
    execute("DELETE FROM transfers WHERE id = ?", (item_id,))
    flash("Silindi.", "success")
    return redirect(url_for(".index"))


@bp.route("/temizle", methods=["POST"])
@login_required
def clear():
    rows = query("SELECT * FROM transfers WHERE user_id = ?", (g.user["id"],))
    for row in rows:
        _remove(row)
    execute("DELETE FROM transfers WHERE user_id = ?", (g.user["id"],))
    flash(f"Aktarma kutusu boşaltıldı ({len(rows)} kayıt).", "success")
    return redirect(url_for(".index"))
