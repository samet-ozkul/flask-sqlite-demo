"""⚙️ Ayarlar: profil, şehir (hava durumu), şifre, Telegram bildirimleri."""
import secrets

from flask import Blueprint, flash, g, redirect, render_template, url_for
from werkzeug.security import check_password_hash

from .. import external, telegram
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
        bot_username=telegram.bot_username() if g.user["telegram_link_code"] else None,
    )


@bp.route("/profil", methods=["POST"])
@login_required
def profile():
    display_name = form_str("display_name", 40)
    city = form_str("city", 80)
    notify = form_bool("notify_daily")
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
        "UPDATE users SET display_name = ?, city = ?, lat = ?, lon = ?, notify_daily = ? WHERE id = ?",
        (display_name, city, lat, lon, notify, g.user["id"]),
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
        return redirect(url_for(".index"))
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
