"""🛠️ Yönetim: kullanıcılar, disk kullanımı, yedek al / geri yükle, temizlik."""
import os
import shutil
import time

from flask import (Blueprint, current_app, flash, g, redirect, render_template, request, send_file,
                   session, url_for)

from .. import backup as backup_lib, telegram
from ..auth import admin_required, create_user, large_upload, registration_open, set_password, validate_new_user
from ..db import SCHEMA_VERSION, execute, get_db, query, query_one
from ..external import purge_cache
from ..storage import cleanup_orphans, delete_user_files, dir_size, usage
from ..utils import form_bool, form_str, now_local

bp = Blueprint("admin", __name__, url_prefix="/yonetim")

COUNTED_TABLES = [
    ("notes", "Not"), ("list_items", "Liste öğesi"), ("links", "Link"), ("expenses", "Harcama"),
    ("bills", "Fatura"), ("subscriptions", "Abonelik"), ("debts", "Borç/alacak"), ("vehicle_logs", "Araç kaydı"),
    ("warranties", "Garanti"), ("inventory", "Envanter"), ("habit_logs", "Alışkanlık işareti"),
    ("health_metrics", "Sağlık ölçümü"), ("recipes", "Tarif"), ("attachments", "Dosya"),
]


@bp.route("/")
@admin_required
def index():
    counts = [(label, query_one(f"SELECT COUNT(*) AS n FROM {table}")["n"]) for table, label in COUNTED_TABLES]
    home = None
    if request.args.get("disk") == "1":
        # PythonAnywhere'de 512 MB kota tüm ev klasörünü kapsar (virtualenv, pip önbelleği dahil)
        started = time.time()
        home_dir = os.path.expanduser("~")
        home = {
            "path": home_dir,
            "size": dir_size(home_dir),
            "pip_cache": dir_size(os.path.join(home_dir, ".cache", "pip")),
            "seconds": round(time.time() - started, 1),
        }
    return render_template(
        "admin/index.html",
        disk=usage(),
        home=home,
        counts=counts,
        users=query("SELECT COUNT(*) AS n FROM users")[0]["n"],
        schema_version=SCHEMA_VERSION,
        telegram_enabled=telegram.enabled(),
        cron_enabled=bool(os.environ.get("CRON_SECRET")),
        registration=registration_open(),
        secret_default=current_app.config["SECRET_KEY"] == "dev-secret-change-me",
    )


# ---------- Kullanıcılar ----------
@bp.route("/kullanicilar", methods=["GET", "POST"])
@admin_required
def users():
    if request.method == "POST":
        username = form_str("username", 30)
        password = form_str("password", 200)
        error = validate_new_user(username, password)
        if error:
            flash(error, "error")
        else:
            create_user(username, password, is_admin=form_bool("is_admin"))
            flash(f"{username} oluşturuldu.", "success")
        return redirect(url_for(".users"))
    rows = query(
        "SELECT u.*, (SELECT COUNT(*) FROM attachments a WHERE a.user_id = u.id) AS files,"
        " (SELECT COALESCE(SUM(size), 0) FROM attachments a WHERE a.user_id = u.id) AS files_size"
        " FROM users u ORDER BY u.id"
    )
    return render_template("admin/users.html", users=rows)


@bp.route("/kullanicilar/<int:user_id>/sifre", methods=["POST"])
@admin_required
def reset_password(user_id):
    password = form_str("password", 200)
    if len(password) < 8:
        flash("Şifre en az 8 karakter olmalı.", "error")
    elif query_one("SELECT 1 FROM users WHERE id = ?", (user_id,)):
        set_password(user_id, password)
        flash("Şifre güncellendi.", "success")
    return redirect(url_for(".users"))


@bp.route("/kullanicilar/<int:user_id>/yonetici", methods=["POST"])
@admin_required
def toggle_admin(user_id):
    if user_id == g.user["id"]:
        flash("Kendi yöneticiliğini kaldıramazsın.", "error")
    else:
        execute("UPDATE users SET is_admin = 1 - is_admin WHERE id = ?", (user_id,))
    return redirect(url_for(".users"))


@bp.route("/kullanicilar/<int:user_id>/sil", methods=["POST"])
@admin_required
def delete_user(user_id):
    if user_id == g.user["id"]:
        flash("Kendi hesabını silemezsin.", "error")
        return redirect(url_for(".users"))
    user = query_one("SELECT username FROM users WHERE id = ?", (user_id,))
    if user:
        # Başkalarıyla paylaşılan listelerin öğelerinde created_by NULL olur, listeler sahibiyle silinir
        execute("DELETE FROM users WHERE id = ?", (user_id,))
        delete_user_files(user_id)
        flash(f"{user['username']} ve tüm verileri silindi.", "success")
    return redirect(url_for(".users"))


# ---------- Yedek ----------
@bp.route("/yedek")
@admin_required
def backup():
    last = query_one("SELECT value FROM app_state WHERE key = 'last_backup_download'")
    return render_template("admin/backup.html", disk=usage(), last=last["value"] if last else None,
                           telegram_enabled=telegram.enabled(), cron_enabled=bool(os.environ.get("CRON_SECRET")))



@bp.route("/yedek/indir", methods=["POST"])
@admin_required
def backup_download():
    include_files = request.form.get("mode") != "db"
    zip_path, tmpdir = backup_lib.make_backup_zip(include_files=include_files)
    stamp = now_local().strftime("%Y-%m-%d_%H%M")
    execute("INSERT OR REPLACE INTO app_state (key, value) VALUES ('last_backup_download', ?)",
            (now_local().strftime("%d.%m.%Y %H:%M"),))
    resp = send_file(zip_path, mimetype="application/zip", as_attachment=True,
                     download_name=f"pano-yedek-{'tam' if include_files else 'db'}-{stamp}.zip")
    resp.call_on_close(lambda: shutil.rmtree(tmpdir, ignore_errors=True))
    return resp


@bp.route("/yedek/geri-yukle", methods=["POST"])
@admin_required
@large_upload(400)
def backup_restore():
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Yedek dosyası seçilmedi.", "warning")
        return redirect(url_for(".backup"))
    if request.form.get("confirm") != "EVET":
        flash("Onay için kutuya EVET yazmalısın.", "warning")
        return redirect(url_for(".backup"))
    try:
        # Mevcut bağlantıyı kapat ki geri yükleme kilit beklemesin
        db = g.pop("db", None)
        if db is not None:
            db.close()
        result = backup_lib.restore_from_zip(f, max_uncompressed=current_app.config["STORAGE_QUOTA_MB"] * 1024 * 1024)
    except backup_lib.RestoreError as e:
        flash(f"Geri yüklenemedi: {e}", "error")
        return redirect(url_for(".backup"))
    session.clear()
    note = f" ve {result['files']} dosya" if result["include_files"] else " (dosyalar olduğu gibi bırakıldı)"
    flash(f"Yedek geri yüklendi: veritabanı{note}. Tekrar giriş yap.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/temizlik", methods=["POST"])
@admin_required
def cleanup():
    before = usage()["used"]
    removed, _freed = cleanup_orphans()
    purge_cache()
    db = get_db()
    db.execute("DELETE FROM login_attempts WHERE first_at < ?", (time.time() - 86400,))
    db.commit()
    db.execute("VACUUM")  # silinen kayıtların boşalttığı alanı dosyadan geri al
    after = usage()["used"]
    flash(f"Temizlik tamam: {removed} sahipsiz dosya silindi, {max(0, before - after) // 1024} KB kazanıldı.", "success")
    return redirect(request.referrer or url_for(".index"))
