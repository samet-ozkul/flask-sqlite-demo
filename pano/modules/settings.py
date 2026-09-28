"""⚙️ Ayarlar: profil, şehir (hava durumu), şifre, Telegram bildirimleri."""
import secrets

from flask import Blueprint, flash, g, make_response, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from .. import ai, external, telegram, totp
from ..auth import login_required, set_password
from ..db import execute
from ..utils import form_bool, form_str

bp = Blueprint("settings", __name__, url_prefix="/ayarlar")


@bp.route("/")
@login_required
def index():
    return render_template(
        "settings/index.html",
        telegram_enabled=telegram.enabled(),
        webhook_active=telegram.enabled() and telegram.webhook_active(),
        recovery_left=totp.recovery_left(g.user["id"]) if g.user["totp_enabled"] else 0,
        ai_config=ai.config(),
        bot_username=telegram.bot_username() if g.user["telegram_link_code"] else None,
    )


@bp.route("/profil", methods=["POST"])
@login_required
def profile():
    display_name = form_str("display_name", 40)
    city = form_str("city", 80)
    notify = form_bool("notify_daily")
    alerts = form_bool("weather_alerts")
    lat, lon = g.user["lat"], g.user["lon"]
    if not city:
        lat = lon = None
    elif city != g.user["city"] or lat is None:
        place = external.geocode(city)
        if place is None:
            flash(f"“{city}” bulunamadı veya hava durumu servisine ulaşılamadı.", "error")
            city = g.user["city"]
        else:
            city, lat, lon = place["name"], place["lat"], place["lon"]
    execute(
        "UPDATE users SET display_name = ?, city = ?, lat = ?, lon = ?, notify_daily = ?, weather_alerts = ? WHERE id = ?",
        (display_name, city, lat, lon, notify, alerts, g.user["id"]),
    )
    flash("Ayarlar kaydedildi.", "success")
    return redirect(url_for(".index"))


@bp.route("/sifre", methods=["POST"])
@login_required
def password():
    current = form_str("current", 200)
    new = form_str("new", 200)
    if not check_password_hash(g.user["password_hash"], current):
        flash("Mevcut şifre hatalı.", "error")
    elif len(new) < 8:
        flash("Yeni şifre en az 8 karakter olmalı.", "error")
    elif new != form_str("confirm", 200):
        flash("Yeni şifreler eşleşmiyor.", "error")
    else:
        set_password(g.user["id"], new)
        flash("Şifre değiştirildi.", "success")
    return redirect(url_for(".index"))


# ---------- Yapay zekâ ----------
@bp.route("/yapay-zeka", methods=["POST"])
@login_required
def ai_toggle():
    on = 1 if request.form.get("ai_enabled") else 0
    execute("UPDATE users SET ai_enabled = ? WHERE id = ?", (on, g.user["id"]))
    flash("Yapay zekâ özellikleri açıldı." if on else "Yapay zekâ özellikleri kapatıldı.", "success")
    return redirect(url_for(".index") + "#yapay-zeka")


# ---------- İki adımlı giriş ----------
@bp.route("/2fa", methods=["GET", "POST"])
@login_required
def twofa_setup():
    if g.user["totp_enabled"]:
        return redirect(url_for(".index") + "#iki-adimli")
    if request.method == "POST" or "2fa_setup" not in session:
        # Anahtar onaylanana kadar sadece oturumda durur
        session["2fa_setup"] = totp.new_secret()
        if request.method == "POST":
            return redirect(url_for(".twofa_setup"))
    secret = session["2fa_setup"]
    resp = make_response(render_template(
        "settings/2fa_setup.html", qr=totp.qr_svg(totp.provisioning_uri(secret, g.user["username"])),
        secret_text=totp.format_secret(secret)))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/2fa/onayla", methods=["POST"])
@login_required
def twofa_confirm():
    secret = session.get("2fa_setup")
    if not secret or g.user["totp_enabled"]:
        return redirect(url_for(".twofa_setup"))
    step = totp.verify(secret, request.form.get("code"))
    if step is None:
        flash("Kod tutmadı. Telefonun saatinin doğru olduğundan emin olup yeni kodla tekrar dene.", "error")
        return redirect(url_for(".twofa_setup"))
    execute("UPDATE users SET totp_secret = ?, totp_enabled = 1, totp_last_step = ? WHERE id = ?",
            (secret, step, g.user["id"]))
    session.pop("2fa_setup", None)
    flash("İki adımlı giriş açıldı. Bundan sonra girişte doğrulama kodu istenecek.", "success")
    return _show_codes(totp.new_recovery_codes(g.user["id"]))


def _show_codes(codes):
    resp = make_response(render_template("settings/2fa_codes.html", codes=codes))
    resp.headers["Cache-Control"] = "no-store"  # kodlar tarayıcı önbelleğinde kalmasın
    return resp


def _password_ok():
    if check_password_hash(g.user["password_hash"], request.form.get("password", "")):
        return True
    flash("Şifre hatalı.", "error")
    return False


@bp.route("/2fa/kodlar", methods=["POST"])
@login_required
def twofa_codes():
    if not g.user["totp_enabled"] or not _password_ok():
        return redirect(url_for(".index") + "#iki-adimli")
    flash("Yeni yedek kodlar oluşturuldu; eskiler artık geçersiz.", "success")
    return _show_codes(totp.new_recovery_codes(g.user["id"]))


@bp.route("/2fa/kapat", methods=["POST"])
@login_required
def twofa_disable():
    if g.user["totp_enabled"] and _password_ok():
        totp.disable(g.user["id"])
        flash("İki adımlı giriş kapatıldı.", "success")
    return redirect(url_for(".index") + "#iki-adimli")


# ---------- Takvim aboneliği ----------
@bp.route("/takvim", methods=["POST"])
@login_required
def calendar_feed():
    if request.form.get("action") == "revoke":
        execute("UPDATE users SET calendar_token = NULL WHERE id = ?", (g.user["id"],))
        flash("Takvim aboneliği kapatıldı; eski adres artık çalışmaz.", "success")
    else:
        execute("UPDATE users SET calendar_token = ? WHERE id = ?", (secrets.token_urlsafe(24), g.user["id"]))
        flash("Takvim adresi oluşturuldu. Eski bir adres varsa artık çalışmaz.", "success")
    return redirect(url_for(".index") + "#takvim")


# ---------- Telegram ----------
@bp.route("/telegram/kod", methods=["POST"])
@login_required
def telegram_code():
    if not telegram.enabled():
        flash("Telegram botu yönetici tarafından ayarlanmamış.", "error")
        return redirect(url_for(".index"))
    execute("UPDATE users SET telegram_link_code = ? WHERE id = ?", (secrets.token_hex(4), g.user["id"]))
    return redirect(url_for(".index") + "#telegram")


@bp.route("/telegram/dogrula", methods=["POST"])
@login_required
def telegram_verify():
    code = g.user["telegram_link_code"]
    if not code:
        # Webhook açıksa bağlantı bota "Başlat" denince zaten yapılmıştır
        if g.user["telegram_chat_id"]:
            flash("Telegram bağlandı.", "success")
        return redirect(url_for(".index") + "#telegram")
    if telegram.webhook_active():
        flash("Henüz bağlanmadı. Botta “Başlat”a bastıktan birkaç saniye sonra tekrar dene.", "warning")
        return redirect(url_for(".index") + "#telegram")
    try:
        chat_id = telegram.find_chat_for_code(code)
    except telegram.TelegramError as e:
        flash(f"Telegram hatası: {e}", "error")
        return redirect(url_for(".index") + "#telegram")
    if not chat_id:
        flash("Mesaj bulunamadı. Bota linkten gidip “Başlat”a bastığından emin ol, sonra tekrar dene.", "warning")
        return redirect(url_for(".index") + "#telegram")
    execute("UPDATE users SET telegram_chat_id = ?, telegram_link_code = NULL WHERE id = ?", (chat_id, g.user["id"]))
    try:
        telegram.send_message(chat_id, "✅ Kişisel Pano bağlandı. Günlük özetler buraya gelecek.")
    except telegram.TelegramError:
        pass
    flash("Telegram bağlandı.", "success")
    return redirect(url_for(".index") + "#telegram")


@bp.route("/telegram/test", methods=["POST"])
@login_required
def telegram_test():
    try:
        telegram.send_message(g.user["telegram_chat_id"], "👋 Test mesajı — Kişisel Pano")
        flash("Test mesajı gönderildi.", "success")
    except telegram.TelegramError as e:
        flash(f"Gönderilemedi: {e}", "error")
    return redirect(url_for(".index") + "#telegram")


@bp.route("/telegram/kaldir", methods=["POST"])
@login_required
def telegram_unlink():
    execute("UPDATE users SET telegram_chat_id = NULL, telegram_link_code = NULL WHERE id = ?", (g.user["id"],))
    flash("Telegram bağlantısı kaldırıldı.", "success")
    return redirect(url_for(".index") + "#telegram")
