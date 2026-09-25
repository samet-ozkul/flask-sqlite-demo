"""⏰ Dışarıdan tetiklenen görevler (cron-job.org gibi ücretsiz bir servis çağırır).

PythonAnywhere ücretsiz planında zamanlanmış görev olmadığı için:
  GET /cron/<CRON_SECRET>/gunluk      -> herkese Telegram günlük özeti (günde bir kez)
  GET /cron/<CRON_SECRET>/hatirlatma  -> yapılacaklar hatırlatmaları (5 dakikada bir)
  GET /cron/<CRON_SECRET>/yedek       -> yöneticilere Telegram'dan veritabanı yedeği
CRON_SECRET ayarlı değilse bu adresler 404 döner.
"""
import os
import secrets
import shutil
import time

from flask import Blueprint, abort, jsonify, request, url_for

from .. import backup, external, telegram
from .. import todo_reminders as todo
from ..db import get_db, query, query_one
from .bot import done_buttons
from ..reminders import medications_today, upcoming
from ..utils import MONTHS_TR, WEEKDAYS_TR, fmt_money, now_local, rel_days, today, today_str

bp = Blueprint("cron", __name__, url_prefix="/cron")

TELEGRAM_FILE_LIMIT = 45 * 1024 * 1024  # bot API sınırı 50 MB


def _check_secret(secret):
    expected = os.environ.get("CRON_SECRET", "")
    if not expected or not secrets.compare_digest(secret, expected):
        abort(404)


def _state_get(key):
    row = query_one("SELECT value FROM app_state WHERE key = ?", (key,))
    return row["value"] if row else None


def _state_set(key, value):
    db = get_db()
    db.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)", (key, value))
    db.commit()


def build_daily_message(user, site_url):
    esc = telegram.escape
    t = today()
    name = user["display_name"] or user["username"]
    lines = [f"<b>☀️ Günaydın {esc(name)}!</b>", f"{t.day} {MONTHS_TR[t.month - 1]}, {WEEKDAYS_TR[t.weekday()]}"]

    weather = external.weather(user["lat"], user["lon"])
    if weather and weather.get("days"):
        label, icon = external.weather_label(weather["days"][0]["code"])
        d0 = weather["days"][0]
        rain = f", yağış %{d0['rain']}" if d0.get("rain") else ""
        lines.append(f"{icon} {esc(user['city'])}: {d0['min']}°/{d0['max']}°, {label}{rain}")

    items = upcoming(user["id"], days=3, long_days=7)
    if items:
        lines += ["", "<b>📌 Yaklaşanlar</b>"]
        for it in items[:15]:
            detail = f" · {esc(it['detail'])}" if it["detail"] else ""
            lines.append(f"{it['icon']} {esc(it['title'])} — <i>{rel_days(it['date'])}</i>{detail}")

    meds = medications_today(user["id"])
    if meds:
        lines += ["", "<b>💊 Bugünkü ilaçlar</b>"]
        lines += [f"{esc(time_)} {esc(m['name'])}" + (f" ({esc(m['dose'])})" if m["dose"] else "") for time_, m in meds]

    habits = query("SELECT icon, name FROM habits WHERE user_id = ? AND active = 1", (user["id"],))
    if habits:
        lines += ["", "<b>🔥 Alışkanlıklar:</b> " + ", ".join(f"{h['icon']} {esc(h['name'])}" for h in habits)]

    spent = query_one(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM expenses WHERE user_id = ? AND date >= ?",
        (user["id"], t.replace(day=1).isoformat()),
    )["s"]
    if spent:
        lines += ["", f"💸 Bu ay harcama: {fmt_money(spent)}"]

    lines += ["", f'<a href="{esc(site_url)}">Panoyu aç →</a>']
    return "\n".join(lines)


@bp.route("/<secret>/gunluk")
def daily(secret):
    _check_secret(secret)
    force = request.args.get("force") == "1"
    site_url = request.url_root
    result = {"sent": 0, "skipped": 0, "errors": []}
    if not telegram.enabled():
        return jsonify(error="TELEGRAM_BOT_TOKEN ayarlı değil"), 400
    for user in query("SELECT * FROM users WHERE telegram_chat_id IS NOT NULL AND notify_daily = 1"):
        key = f"daily:{user['id']}"
        if not force and _state_get(key) == today_str():
            result["skipped"] += 1
            continue
        try:
            telegram.send_message(user["telegram_chat_id"], build_daily_message(user, site_url))
            _state_set(key, today_str())
            result["sent"] += 1
        except telegram.TelegramError as e:
            result["errors"].append(f"{user['username']}: {e}")

    # Ufak bakım: eski önbellek ve giriş denemesi kayıtları
    external.purge_cache()
    db = get_db()
    db.execute("DELETE FROM login_attempts WHERE first_at < ?", (time.time() - 86400,))
    db.commit()
    return jsonify(result)


@bp.route("/<secret>/hatirlatma")
def todo_reminders(secret):
    """Yapılacaklar hatırlatmaları. Sık çağrılmalı (ör. 5 dakikada bir); iş yoksa hemen döner."""
    _check_secret(secret)
    if not telegram.enabled():
        return jsonify(error="TELEGRAM_BOT_TOKEN ayarlı değil"), 400
    to_send, stale = todo.pending()
    result = {"sent": 0, "stale": len(stale), "no_telegram": 0, "errors": []}
    # "✅ Tamamlandı" butonu sadece webhook kuruluysa çalışır; değilse hiç gösterme
    with_buttons = telegram.webhook_active()
    for item in stale:
        todo.mark_sent(item["id"], "due")
    for kind, item in to_send:
        chat_id = todo.recipient_chat_id(item)
        if not chat_id:
            result["no_telegram"] += 1
            continue
        url = url_for("lists.detail", list_id=item["list_id"], _external=True)
        try:
            telegram.send_message(chat_id, todo.message(kind, item, url, telegram.escape),
                                  buttons=done_buttons(item["id"]) if with_buttons else None)
            todo.mark_sent(item["id"], kind)
            result["sent"] += 1
        except telegram.TelegramError as e:
            result["errors"].append(f"madde {item['id']}: {e}")
    return jsonify(result)


@bp.route("/<secret>/yedek")
def backup_to_telegram(secret):
    _check_secret(secret)
    if not telegram.enabled():
        return jsonify(error="TELEGRAM_BOT_TOKEN ayarlı değil"), 400
    admins = query("SELECT * FROM users WHERE is_admin = 1 AND telegram_chat_id IS NOT NULL")
    if not admins:
        return jsonify(error="Telegram'ı bağlı yönetici yok"), 400
    zip_path, tmpdir = backup.make_backup_zip(include_files=False)
    try:
        size = os.path.getsize(zip_path)
        if size > TELEGRAM_FILE_LIMIT:
            return jsonify(error=f"Yedek {size // (1024 * 1024)} MB, Telegram sınırını aşıyor"), 413
        with open(zip_path, "rb") as f:
            data = f.read()
        stamp = now_local().strftime("%Y-%m-%d")
        sent, errors = 0, []
        for admin in admins:
            try:
                telegram.send_document(admin["telegram_chat_id"], f"pano-yedek-db-{stamp}.zip", data,
                                       caption=f"💾 Otomatik veritabanı yedeği · {stamp}")
                sent += 1
            except telegram.TelegramError as e:
                errors.append(f"{admin['username']}: {e}")
        _state_set("last_backup_telegram", stamp)
        return jsonify(sent=sent, size=size, errors=errors)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
