"""Yedek alma ve geri yükleme.

Yedek sunucuda saklanmaz (disk 512 MB): geçici klasörde üretilir, indirilir/Telegram'a
gönderilir ve silinir. DB kopyası SQLite'ın backup API'siyle alınır (açık bağlantı olsa da tutarlı).
"""
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone

from flask import current_app

from .db import SCHEMA_VERSION, connect, migrate

DB_NAME = "pano.db"
MANIFEST = "manifest.json"


class RestoreError(Exception):
    pass


def make_backup_zip(include_files=True):
    """(zip_yolu, geçici_klasör) döner. Çağıran taraf işi bitince klasörü silmeli."""
    tmpdir = tempfile.mkdtemp(prefix="pano-yedek-")
    db_copy = os.path.join(tmpdir, DB_NAME)
    with closing(connect(current_app.config["DATABASE"])) as src, closing(sqlite3.connect(db_copy)) as dst:
        src.backup(dst)
    zip_path = os.path.join(tmpdir, "yedek.zip")
    upload_dir = current_app.config["UPLOAD_DIR"]
    file_count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.write(db_copy, DB_NAME)
        if include_files and os.path.isdir(upload_dir):
            for root, _dirs, files in os.walk(upload_dir):
                for name in files:
                    full = os.path.join(root, name)
                    arc = "uploads/" + os.path.relpath(full, upload_dir).replace(os.sep, "/")
                    # JPEG/PDF zaten sıkıştırılmış: tekrar sıkıştırıp CPU harcama
                    zf.write(full, arc, compress_type=zipfile.ZIP_STORED)
                    file_count += 1
        zf.writestr(MANIFEST, json.dumps({
            "app": "kisisel-pano",
            "schema_version": SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "include_files": include_files,
            "file_count": file_count,
        }, indent=2))
    os.remove(db_copy)
    return zip_path, tmpdir


def _validate_db(path):
    with open(path, "rb") as f:
        if f.read(16) != b"SQLite format 3\x00":
            raise RestoreError("Yedekteki veritabanı dosyası geçersiz.")
    try:
        with closing(connect(path)) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RestoreError("Veritabanı bütünlük kontrolünden geçmedi.")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RestoreError("Bu yedek uygulamanın daha yeni bir sürümünden alınmış.")
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone():
                raise RestoreError("Yedekte kullanıcı tablosu yok.")
            migrate(conn)  # eski sürüm yedeği ise güncel şemaya getir
            if not conn.execute("SELECT 1 FROM users WHERE is_admin = 1").fetchone():
                raise RestoreError("Yedekte yönetici kullanıcı yok; geri yüklenirse kimse yönetemez.")
    except sqlite3.DatabaseError as e:
        raise RestoreError(f"Veritabanı okunamadı: {e}")


def restore_from_zip(file_storage, max_uncompressed):
    """Yüklenen yedek zip'ini geri yükler. {'files': n, 'include_files': bool} döner."""
    tmpdir = tempfile.mkdtemp(prefix="pano-geri-")
    try:
        zip_path = os.path.join(tmpdir, "upload.zip")
        file_storage.save(zip_path)
        if not zipfile.is_zipfile(zip_path):
            raise RestoreError("Dosya bir zip değil.")
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            if DB_NAME not in names:
                raise RestoreError(f"Zip içinde {DB_NAME} yok. Bu uygulamanın yedeği mi?")
            if sum(i.file_size for i in zf.infolist()) > max_uncompressed:
                raise RestoreError("Yedek açıldığında depolama kotasını aşıyor.")
            db_tmp = os.path.join(tmpdir, DB_NAME)
            with zf.open(DB_NAME) as src, open(db_tmp, "wb") as dst:
                shutil.copyfileobj(src, dst)
            _validate_db(db_tmp)

            # Canlı DB'ye sayfa sayfa kopyala (dosyayı değiştirmekten daha güvenli)
            with closing(connect(db_tmp)) as src, closing(connect(current_app.config["DATABASE"])) as dst:
                src.backup(dst)

            upload_entries = [n for n in names if n.startswith("uploads/") and not n.endswith("/")]
            if upload_entries:
                upload_dir = os.path.abspath(current_app.config["UPLOAD_DIR"])
                shutil.rmtree(upload_dir, ignore_errors=True)
                os.makedirs(upload_dir, exist_ok=True)
                for name in upload_entries:
                    target = os.path.abspath(os.path.join(upload_dir, name[len("uploads/"):]))
                    if not target.startswith(upload_dir + os.sep):
                        continue  # zip içinden klasör dışına yazma denemesi
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(name) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        return {"files": len(upload_entries), "include_files": bool(upload_entries)}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
