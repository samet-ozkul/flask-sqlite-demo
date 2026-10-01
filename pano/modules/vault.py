"""🔐 Şifreli kasa: notlar tarayıcıda, kullanıcının belirlediği kasa parolasıyla şifrelenir.

Sunucu (ve yedekler) sadece şifreli veriyi görür; kasa parolası sunucuya hiç gönderilmez.
- Kasa parolasından PBKDF2-SHA256 (600.000 tur) ile anahtar türetilir; bu anahtar rastgele bir
  veri anahtarını (AES-256-GCM) sarar. Kayıtlar veri anahtarıyla şifrelenir; böylece kasa parolası
  değişince kayıtları yeniden şifrelemek gerekmez.
- Kasa parolası unutulursa veriler kurtarılamaz (bilerek). Sıfırlamak için hesap şifresi gerekir.
- Genel aramada, yapay zekâda ve Telegram'da kasa içeriği yoktur.
Şifreleme kodu: static/vault.js
"""
import base64
import binascii

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash

from .. import trash
from ..auth import login_required
from ..db import execute, get_db, query, query_one

bp = Blueprint("vault", __name__, url_prefix="/kasa")

MIN_ITERATIONS = 100_000
MAX_ITERATIONS = 5_000_000
ITEM_MAX = 60_000       # şifreli kayıt (base64) üst sınırı: ~40 KB metin
ITEM_LIMIT = 500        # 500 × 60 KB = en fazla ~30 MB (512 MB disk)


def _b64(name, min_len, max_len):
    """Formdaki base64 alanı; geçersizse 400."""
    value = (request.form.get(name) or "").strip()
    if not (min_len <= len(value) <= max_len):
        abort(400, "Geçersiz şifreli veri.")
    try:
        base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        abort(400, "Geçersiz şifreli veri.")
    return value


def _meta(user_id):
    return query_one("SELECT * FROM vault_meta WHERE user_id = ?", (user_id,))


def _account_password_ok():
    return check_password_hash(g.user["password_hash"], request.form.get("password", ""))


def _key_fields():
    iterations = request.form.get("iterations", "")
    if not iterations.isdigit() or not (MIN_ITERATIONS <= int(iterations) <= MAX_ITERATIONS):
        abort(400, "Geçersiz tur sayısı.")
    return {
        "salt": _b64("salt", 16, 64),
        "iterations": int(iterations),
        "wrapped_key": _b64("wrapped_key", 40, 200),
        "hint": (request.form.get("hint") or "").strip()[:100],
    }


@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    meta = _meta(uid)
    items = query("SELECT id, data, updated_at FROM vault_items WHERE user_id = ? ORDER BY id", (uid,)) if meta else []
    return render_template("vault/index.html", meta=meta, items=[dict(r) for r in items])


@bp.route("/kur", methods=["POST"])
@login_required
def setup():
    if _meta(g.user["id"]):
        abort(409, "Kasa zaten kurulu.")
    v = _key_fields()
    execute("INSERT INTO vault_meta (user_id, salt, iterations, wrapped_key, hint) VALUES (?, ?, ?, ?, ?)",
            (g.user["id"], v["salt"], v["iterations"], v["wrapped_key"], v["hint"]))
    return jsonify(ok=True)


@bp.route("/kayit", methods=["POST"])
@login_required
def create():
    uid = g.user["id"]
    if not _meta(uid):
        abort(409, "Önce kasayı kur.")
    if query_one("SELECT COUNT(*) AS n FROM vault_items WHERE user_id = ?", (uid,))["n"] >= ITEM_LIMIT:
        abort(400, f"Kasada en fazla {ITEM_LIMIT} kayıt olabilir.")
    cur = execute("INSERT INTO vault_items (user_id, data) VALUES (?, ?)", (uid, _b64("data", 24, ITEM_MAX)))
    row = query_one("SELECT id, updated_at FROM vault_items WHERE id = ?", (cur.lastrowid,))
    return jsonify(id=row["id"], updated_at=row["updated_at"])


def _item_or_404(item_id):
    row = query_one("SELECT * FROM vault_items WHERE id = ? AND user_id = ?", (item_id, g.user["id"]))
    if row is None:
        abort(404)
    return row


@bp.route("/kayit/<int:item_id>", methods=["POST"])
@login_required
def update(item_id):
    _item_or_404(item_id)
    execute("UPDATE vault_items SET data = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (_b64("data", 24, ITEM_MAX), item_id))
    return jsonify(updated_at=query_one("SELECT updated_at FROM vault_items WHERE id = ?", (item_id,))["updated_at"])


@bp.route("/kayit/<int:item_id>/sil", methods=["POST"])
@login_required
def delete(item_id):
    _item_or_404(item_id)
    # İçerik şifreli olduğu için çöp kutusunda genel bir adla durur; geri getirince yine kasa parolasıyla açılır
    trash.move(g.user["id"], "vault", "🔐 Kasa kaydı (şifreli)", ("vault_items", item_id))
    return jsonify(ok=True)


@bp.route("/parola", methods=["POST"])
@login_required
def change_passphrase():
    """Tarayıcı veri anahtarını eski parolayla açıp yenisiyle sarar; sunucu sadece sarılmış anahtarı değiştirir."""
    if not _meta(g.user["id"]):
        abort(409)
    if not _account_password_ok():
        return jsonify(error="Hesap şifresi hatalı."), 403
    v = _key_fields()
    execute("UPDATE vault_meta SET salt = ?, iterations = ?, wrapped_key = ?, hint = ?, updated_at = CURRENT_TIMESTAMP"
            " WHERE user_id = ?", (v["salt"], v["iterations"], v["wrapped_key"], v["hint"], g.user["id"]))
    return jsonify(ok=True)


@bp.route("/sifirla", methods=["POST"])
@login_required
def reset():
    """Kasa parolası unutulduysa: bütün kayıtlar kalıcı silinir, kasa yeniden kurulabilir."""
    if not _account_password_ok():
        flash("Hesap şifresi hatalı; kasa sıfırlanmadı.", "error")
    elif request.form.get("confirm", "").strip().upper() not in ("SİL", "SIL"):
        flash("Onay için SİL yazmalısın; kasa sıfırlanmadı.", "error")
    else:
        uid = g.user["id"]
        db = get_db()
        n = db.execute("DELETE FROM vault_items WHERE user_id = ?", (uid,)).rowcount
        db.execute("DELETE FROM vault_meta WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM trash WHERE user_id = ? AND module = 'vault'", (uid,))
        db.commit()
        flash(f"Kasa sıfırlandı ({n} kayıt silindi). Yeni kasa parolasıyla yeniden kurabilirsin.", "success")
    return redirect(url_for(".index"))
