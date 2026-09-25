"""Dosya ekleri (fatura/garanti fotoğrafı vb.) ve disk kotası.

512 MB'lık PythonAnywhere diskini korumak için:
- Fotoğraflar en fazla 1600px'e küçültülüp JPEG olarak yeniden kaydedilir (EXIF/GPS silinir)
- Her fotoğrafa ~400px küçük önizleme üretilir, listelerde o gösterilir
- PDF'ler en fazla 3 MB
- Toplam veri (DB + ekler) STORAGE_QUOTA_MB'yi aşarsa yükleme reddedilir
"""
import io
import os
import shutil
import uuid

from flask import (Blueprint, abort, current_app, flash, g, redirect, request,
                   send_from_directory, url_for)
from PIL import Image, ImageOps, UnidentifiedImageError

from .auth import login_required
from .db import execute, query, query_one

bp = Blueprint("files", __name__, url_prefix="/dosya")

IMAGE_MAX_PX = 1600
THUMB_MAX_PX = 400
JPEG_QUALITY = 72
PDF_MAX_BYTES = 3 * 1024 * 1024
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

# Ek eklenebilen kayıt türleri -> tablo adı
ENTITIES = {
    "warranty": "warranties",
    "inventory": "inventory",
    "recipe": "recipes",
    "vehicle": "vehicles",
    "note": "notes",
}

Image.MAX_IMAGE_PIXELS = 60_000_000  # dev boyutlu (decompression bomb) görselleri reddet


def upload_dir():
    return current_app.config["UPLOAD_DIR"]


def quota_bytes():
    return current_app.config["STORAGE_QUOTA_MB"] * 1024 * 1024


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def usage():
    """Uygulama verisinin disk kullanımı (bayt)."""
    db_path = current_app.config["DATABASE"]
    db_size = sum(os.path.getsize(p) for p in (db_path, db_path + "-journal", db_path + "-wal") if os.path.exists(p))
    files_size = dir_size(upload_dir()) if os.path.isdir(upload_dir()) else 0
    quota = quota_bytes()
    used = db_size + files_size
    return {
        "db": db_size,
        "files": files_size,
        "used": used,
        "quota": quota,
        "percent": min(100, round(used * 100 / quota)) if quota else 0,
    }


def _user_dir(user_id):
    path = os.path.join(upload_dir(), str(user_id))
    os.makedirs(path, exist_ok=True)
    return path


def _encode_jpeg(img, max_px):
    copy = img.copy()
    copy.thumbnail((max_px, max_px))
    buf = io.BytesIO()
    copy.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return buf.getvalue()


def process_upload(file_storage):
    """(ana_bayt, önizleme_bayt|None, mime) döner; geçersizse ValueError."""
    name = (file_storage.filename or "").lower()
    ext = os.path.splitext(name)[1]
    data = file_storage.read()
    if not data:
        raise ValueError("Dosya boş.")
    if ext == ".pdf" or data[:5] == b"%PDF-":
        if data[:5] != b"%PDF-":
            raise ValueError("Geçerli bir PDF değil.")
        if len(data) > PDF_MAX_BYTES:
            raise ValueError("PDF en fazla 3 MB olabilir.")
        return data, None, "application/pdf"
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            background = Image.new("RGB", img.size, "white")
            rgba = img.convert("RGBA")
            background.paste(rgba, mask=rgba.split()[-1])
            img = background
        elif img.mode == "L":
            img = img.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("Sadece fotoğraf (JPG, PNG, WEBP) veya PDF yüklenebilir.")
    return _encode_jpeg(img, IMAGE_MAX_PX), _encode_jpeg(img, THUMB_MAX_PX), "image/jpeg"


def save_attachment(file_storage, user_id, entity, entity_id):
    main, thumb, mime = process_upload(file_storage)
    new_size = len(main) + len(thumb or b"")
    if usage()["used"] + new_size > quota_bytes():
        raise ValueError("Depolama kotası doldu. Eski ekleri silin veya yönetim sayfasından temizlik yapın.")
    ext = ".pdf" if mime == "application/pdf" else ".jpg"
    stem = uuid.uuid4().hex
    folder = _user_dir(user_id)
    filename = f"{user_id}/{stem}{ext}"
    with open(os.path.join(folder, stem + ext), "wb") as f:
        f.write(main)
    thumb_name = None
    if thumb:
        thumb_name = f"{user_id}/{stem}_t.jpg"
        with open(os.path.join(folder, stem + "_t.jpg"), "wb") as f:
            f.write(thumb)
    original = os.path.basename(file_storage.filename or "")[:120]
    return execute(
        "INSERT INTO attachments (user_id, entity, entity_id, filename, thumb, original_name, mime, size)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, entity, entity_id, filename, thumb_name, original, mime, new_size),
    ).lastrowid


def _remove_files(row):
    for name in (row["filename"], row["thumb"]):
        if name:
            try:
                os.remove(os.path.join(upload_dir(), name))
            except OSError:
                pass  # dosya yoksa ya da kilitliyse kayıt yine silinir; temizlik sonradan kaldırır


def attachments_for(entity, entity_id):
    return query(
        "SELECT * FROM attachments WHERE entity = ? AND entity_id = ? ORDER BY id", (entity, entity_id)
    )


def first_thumbs(entity, ids):
    """{entity_id: attachment_row} — liste sayfalarında küçük resim göstermek için."""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = query(
        f"SELECT * FROM attachments WHERE entity = ? AND entity_id IN ({marks}) AND thumb IS NOT NULL"
        " ORDER BY id DESC",
        (entity, *ids),
    )
    return {r["entity_id"]: r for r in rows}


def delete_for(entity, entity_id):
    """Bir kayıt silinirken eklerini de siler. Kaydı silen rota bunu çağırmalı."""
    for row in attachments_for(entity, entity_id):
        _remove_files(row)
    execute("DELETE FROM attachments WHERE entity = ? AND entity_id = ?", (entity, entity_id))


def delete_user_files(user_id):
    shutil.rmtree(os.path.join(upload_dir(), str(user_id)), ignore_errors=True)


def cleanup_orphans():
    """Kaydı silinmiş ekleri ve tabloda olmayan dosyaları temizler. (silinen_dosya, kazanılan_bayt)"""
    removed, freed = 0, 0
    for entity, table in ENTITIES.items():
        for row in query(
            f"SELECT a.* FROM attachments a LEFT JOIN {table} t ON t.id = a.entity_id"
            " WHERE a.entity = ? AND t.id IS NULL",
            (entity,),
        ):
            _remove_files(row)
            execute("DELETE FROM attachments WHERE id = ?", (row["id"],))
            removed += 1
            freed += row["size"]
    known = set()
    for row in query("SELECT filename, thumb FROM attachments"):
        known.add(row["filename"])
        if row["thumb"]:
            known.add(row["thumb"])
    base = upload_dir()
    if os.path.isdir(base):
        for root, _dirs, files in os.walk(base):
            for name in files:
                full = os.path.join(root, name)
                rel = os.path.relpath(full, base).replace(os.sep, "/")
                if rel not in known:
                    freed += os.path.getsize(full)
                    os.remove(full)
                    removed += 1
    return removed, freed


# ---------- Rotalar ----------
def _own_attachment(att_id):
    row = query_one("SELECT * FROM attachments WHERE id = ? AND user_id = ?", (att_id, g.user["id"]))
    if row is None:
        abort(404)
    return row


def _back(default="dashboard.index"):
    target = request.form.get("next") or request.args.get("next") or ""
    if target.startswith("/") and not target.startswith("//"):
        return redirect(target)
    return redirect(url_for(default))


@bp.route("/<int:att_id>")
@login_required
def show(att_id):
    row = _own_attachment(att_id)
    folder, name = os.path.split(row["filename"])
    resp = send_from_directory(os.path.join(upload_dir(), folder), name, mimetype=row["mime"])
    resp.headers["Cache-Control"] = "private, max-age=86400"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.route("/<int:att_id>/kucuk")
@login_required
def thumb(att_id):
    row = _own_attachment(att_id)
    if not row["thumb"]:
        abort(404)
    folder, name = os.path.split(row["thumb"])
    resp = send_from_directory(os.path.join(upload_dir(), folder), name, mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@bp.route("/yukle", methods=["POST"])
@login_required
def upload():
    entity = request.form.get("entity")
    entity_id = request.form.get("entity_id", type=int)
    table = ENTITIES.get(entity)
    if not table or not entity_id:
        abort(400)
    if not query_one(f"SELECT 1 FROM {table} WHERE id = ? AND user_id = ?", (entity_id, g.user["id"])):
        abort(404)
    files = [f for f in request.files.getlist("file") if f and f.filename]
    if not files:
        flash("Dosya seçilmedi.", "warning")
        return _back()
    saved = 0
    for f in files[:5]:
        try:
            save_attachment(f, g.user["id"], entity, entity_id)
            saved += 1
        except ValueError as e:
            flash(f"{f.filename}: {e}", "error")
    if saved:
        flash(f"{saved} dosya eklendi.", "success")
    return _back()


@bp.route("/<int:att_id>/sil", methods=["POST"])
@login_required
def delete(att_id):
    row = _own_attachment(att_id)
    _remove_files(row)
    execute("DELETE FROM attachments WHERE id = ?", (att_id,))
    flash("Dosya silindi.", "success")
    return _back()
