import os
import secrets
import sqlite3
from functools import wraps

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("RENDER") is not None  # Render'da HTTPS

DATABASE = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "app.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


# ---------- DB ----------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(os.path.dirname(os.path.abspath(DATABASE)), exist_ok=True)
    with sqlite3.connect(DATABASE) as db:
        db.executescript(SCHEMA)
        # Ortam değişkeniyle admin kullanıcısı oluştur (opsiyonel)
        admin_user = os.environ.get("ADMIN_USERNAME")
        admin_pass = os.environ.get("ADMIN_PASSWORD")
        if admin_user and admin_pass:
            exists = db.execute("SELECT 1 FROM users WHERE username = ?", (admin_user,)).fetchone()
            if not exists:
                db.execute(
                    "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 1)",
                    (admin_user, generate_password_hash(admin_pass)),
                )


init_db()


# ---------- Auth yardımcıları ----------
@app.before_request
def load_user():
    g.user = None
    uid = session.get("user_id")
    if uid:
        g.user = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Lütfen giriş yapın.", "warning")
            return redirect(url_for("login", next=request.path))
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


# ---------- CSRF ----------
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def check_csrf():
    if request.method == "POST":
        if request.form.get("_csrf") != session.get("_csrf"):
            abort(400, "Geçersiz CSRF token")


# ---------- Rotalar ----------
@app.route("/")
def index():
    if g.user:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if len(username) < 3:
            flash("Kullanıcı adı en az 3 karakter olmalı.", "error")
        elif len(password) < 6:
            flash("Şifre en az 6 karakter olmalı.", "error")
        else:
            db = get_db()
            try:
                db.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    (username, generate_password_hash(password)),
                )
                db.commit()
            except sqlite3.IntegrityError:
                flash("Bu kullanıcı adı zaten alınmış.", "error")
            else:
                flash("Kayıt başarılı, giriş yapabilirsiniz.", "success")
                return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            next_url = request.args.get("next", "")
            if not next_url.startswith("/") or next_url.startswith("//"):
                next_url = url_for("dashboard")
            return redirect(next_url)
        flash("Kullanıcı adı veya şifre hatalı.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Çıkış yapıldı.", "success")
    return redirect(url_for("login"))


@app.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    db = get_db()
    if request.method == "POST":
        content = request.form.get("content", "").strip()
        if content:
            db.execute("INSERT INTO notes (user_id, content) VALUES (?, ?)", (g.user["id"], content[:500]))
            db.commit()
        return redirect(url_for("dashboard"))
    notes = db.execute(
        "SELECT * FROM notes WHERE user_id = ? ORDER BY id DESC", (g.user["id"],)
    ).fetchall()
    return render_template("dashboard.html", notes=notes)


@app.route("/notes/<int:note_id>/delete", methods=["POST"])
@login_required
def delete_note(note_id):
    db = get_db()
    db.execute("DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, g.user["id"]))
    db.commit()
    return redirect(url_for("dashboard"))


@app.route("/admin")
@admin_required
def admin():
    users = get_db().execute(
        "SELECT u.id, u.username, u.is_admin, u.created_at, COUNT(n.id) AS note_count "
        "FROM users u LEFT JOIN notes n ON n.user_id = u.id GROUP BY u.id ORDER BY u.id"
    ).fetchall()
    return render_template("admin.html", users=users)


@app.route("/healthz")
def healthz():
    get_db().execute("SELECT 1")
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
