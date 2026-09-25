"""Giriş/çıkış, oturum, yetki dekoratörleri, CSRF ve giriş deneme sınırı."""
import os
import secrets
import time
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import execute, get_db, query_one
from .utils import form_bool, safe_path

bp = Blueprint("auth", __name__)

# Kilit IP'ye bağlı: kullanıcı adı tek başına kilitlenseydi, başkası bilerek yanlış şifre
# deneyerek seni hesabından kilitleyebilirdi.
MAX_ATTEMPTS_USER = 10     # aynı IP'den aynı kullanıcı için
MAX_ATTEMPTS_IP = 30       # aynı IP'den tüm kullanıcılar için
LOCK_SECONDS = 15 * 60     # 15 dk kilit


def registration_open():
    return os.environ.get("ALLOW_REGISTRATION") == "1"


# ---------- Dekoratörler ----------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not g.user["is_admin"]:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def large_upload(megabytes):
    """Bu rotada varsayılandan büyük istek gövdesine izin verir (yedek geri yükleme gibi)."""
    def decorator(view):
        view.large_upload_bytes = megabytes * 1024 * 1024
        return view
    return decorator


def csrf_exempt(view):
    """Form değil, dış servis çağrısı olan POST rotaları için (Telegram webhook).
    Bu rotalar isteği kendi yöntemiyle doğrulamak zorundadır."""
    view.csrf_exempt = True
    return view


# ---------- CSRF ----------
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


def init_app(app):
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.globals["registration_open"] = registration_open

    @app.before_request
    def load_user_and_check_csrf():
        view = app.view_functions.get(request.endpoint)
        limit = getattr(view, "large_upload_bytes", None)
        if limit:
            request.max_content_length = limit

        g.user = None
        uid = session.get("user_id")
        if uid:
            g.user = query_one("SELECT * FROM users WHERE id = ?", (uid,))
            if g.user is None:
                session.clear()

        if (request.method == "POST" and not getattr(view, "csrf_exempt", False)
                and request.form.get("_csrf") != session.get("_csrf")):
            abort(400, "Geçersiz form anahtarı. Sayfayı yenileyip tekrar deneyin.")


# ---------- Giriş deneme sınırı ----------
def _attempt_keys(username):
    ip = request.remote_addr
    return [(f"ip:{ip}", MAX_ATTEMPTS_IP), (f"ipuser:{ip}:{username.lower()[:40]}", MAX_ATTEMPTS_USER)]


def _is_locked(username):
    now = time.time()
    for key, limit in _attempt_keys(username):
        row = query_one("SELECT count, first_at FROM login_attempts WHERE key = ?", (key,))
        if row and row["count"] >= limit and now - row["first_at"] < LOCK_SECONDS:
            return True
    return False


def _record_failure(username):
    now = time.time()
    db = get_db()
    for key, _limit in _attempt_keys(username):
        row = db.execute("SELECT count, first_at FROM login_attempts WHERE key = ?", (key,)).fetchone()
        if row is None or now - row["first_at"] >= LOCK_SECONDS:
            db.execute("INSERT OR REPLACE INTO login_attempts (key, count, first_at) VALUES (?, 1, ?)", (key, now))
        else:
            db.execute("UPDATE login_attempts SET count = count + 1 WHERE key = ?", (key,))
    db.commit()


def _clear_failures(username):
    db = get_db()
    db.execute("DELETE FROM login_attempts WHERE key = ?", (_attempt_keys(username)[1][0],))
    db.commit()


def _safe_next(url):
    return safe_path(url) or url_for("dashboard.index")


# ---------- Rotalar ----------
@bp.route("/giris", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if _is_locked(username):
            flash("Çok fazla hatalı deneme. 15 dakika sonra tekrar deneyin.", "error")
            return render_template("auth/login.html"), 429
        user = query_one("SELECT * FROM users WHERE username = ?", (username,))
        if user and check_password_hash(user["password_hash"], password):
            _clear_failures(username)
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = bool(form_bool("remember"))  # 30 gün; değilse tarayıcı kapanınca biter
            return redirect(_safe_next(request.args.get("next")))
        _record_failure(username)
        flash("Kullanıcı adı veya şifre hatalı.", "error")
    return render_template("auth/login.html")


@bp.route("/cikis", methods=["POST"])
def logout():
    session.clear()
    flash("Çıkış yapıldı.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/kayit", methods=["GET", "POST"])
def register():
    if not registration_open():
        abort(404)
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        error = validate_new_user(username, password)
        if error:
            flash(error, "error")
        else:
            create_user(username, password)
            flash("Kayıt başarılı, giriş yapabilirsiniz.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/register.html")


# ---------- Kullanıcı yardımcıları (yönetim sayfası da kullanır) ----------
def validate_new_user(username, password):
    if not (3 <= len(username) <= 30) or not username.replace("_", "").replace(".", "").isalnum():
        return "Kullanıcı adı 3-30 karakter olmalı; harf, rakam, nokta ve alt çizgi içerebilir."
    if len(password) < 8:
        return "Şifre en az 8 karakter olmalı."
    if query_one("SELECT 1 FROM users WHERE username = ?", (username,)):
        return "Bu kullanıcı adı zaten var."
    return None


def create_user(username, password, is_admin=False):
    return execute(
        "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
        (username, generate_password_hash(password), 1 if is_admin else 0),
    ).lastrowid


def set_password(user_id, password):
    execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(password), user_id))
