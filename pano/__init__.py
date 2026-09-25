"""Kişisel Pano: Flask + SQLite, PythonAnywhere / Render ücretsiz planına uygun."""
import logging
import mimetypes
import os
from datetime import timedelta

from flask import Flask, flash, redirect, render_template, request, send_from_directory, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from . import auth, db, storage, utils
from .modules import MODULES, module_groups, register_blueprints

mimetypes.add_type("application/manifest+json", ".webmanifest")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def create_app():
    app = Flask(__name__)
    database = os.environ.get("DATABASE_PATH", os.path.join(PROJECT_ROOT, "app.db"))
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # HTTPS üzerinde yayınlanırken (Render otomatik, PythonAnywhere'de env ile) cookie sadece HTTPS'te gönderilir
        SESSION_COOKIE_SECURE=os.environ.get("RENDER") is not None or os.environ.get("SESSION_COOKIE_SECURE") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        MAX_CONTENT_LENGTH=12 * 1024 * 1024,
        DATABASE=database,
        UPLOAD_DIR=os.environ.get("UPLOAD_DIR", os.path.join(os.path.dirname(os.path.abspath(database)), "uploads")),
        # 512 MB diskten virtualenv (~70 MB), kod ve pay düşülünce kalan güvenli alan
        STORAGE_QUOTA_MB=int(os.environ.get("STORAGE_QUOTA_MB", "350")),
    )
    if app.config["SECRET_KEY"] == "dev-secret-change-me" and not app.debug:
        logging.getLogger(__name__).warning("SECRET_KEY ayarlı değil! Yayında mutlaka ayarlayın.")

    # PythonAnywhere ve Render bir proxy arkasında: gerçek IP ve https bilgisini al
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    utils.init_app(app)
    auth.init_app(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(storage.bp)
    register_blueprints(app)

    app.jinja_env.globals.update(MODULES=MODULES, module_groups=module_groups)

    @app.route("/healthz")
    def healthz():
        db.get_db().execute("SELECT 1")
        return {"status": "ok"}

    @app.route("/sw.js")
    def service_worker():
        # Kök dizinden sunulmalı ki tüm siteyi kapsasın
        resp = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.route("/cevrimdisi")
    def offline():
        return render_template("offline.html")

    @app.errorhandler(413)
    def too_large(_e):
        flash("Dosya çok büyük.", "error")
        return redirect(request.referrer or url_for("dashboard.index"))

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    def http_error(e):
        return render_template("error.html", error=e), e.code

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    return app
