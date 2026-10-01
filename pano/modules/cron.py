"""⏰ Dışarıdan tetiklenen görevler (cron-job.org gibi ücretsiz bir servis çağırır).

PythonAnywhere ücretsiz planında zamanlanmış görev olmadığı için:
  GET /cron/<CRON_SECRET>/gunluk      -> herkese Telegram günlük özeti (günde bir kez)
  GET /cron/<CRON_SECRET>/hatirlatma  -> yapılacak, fatura ve ilaç hatırlatmaları (5 dakikada bir)
  GET /cron/<CRON_SECRET>/yedek       -> yöneticilere Telegram'dan veritabanı yedeği
CRON_SECRET ayarlı değilse bu adresler 404 döner.
"""
import os
import secrets
import shutil
import time
from datetime import timedelta

from flask import Blueprint, abort, jsonify, request, url_for

from .. import backup, budgets, external, scheduled, telegram, weather_alerts
from .. import todo_reminders as todo
from ..todo_reminders import done_buttons
from ..db import get_db, query, query_one
from ..reminders import medications_today, upcoming
from ..reports import monthly_report
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
        if user["weather_alerts"]:
            lines += [f"⚠️ {esc(a)}" for a in weather_alerts.alerts(d0)]

    items = upcoming(user["id"], days=3, long_days=7)
    if items:
        lines += ["", "<b>📌 Yaklaşanlar</b>"]
        for it in items[:15]:
            detail = f" · {esc(it['detail'])}" if it["detail"] else ""
            lines.append(f"{it['icon']} {esc(it['title'])} — <i>{rel_days(it['date'])}</i>{detail}")

    from .contacts import overdue, since_text
    late = overdue(user["id"], t)
    if late:
        names = ", ".join(f"{esc(c['name'])} ({since_text(st)})" for c, st in late[:5])
        more = f" +{len(late) - 5}" if len(late) > 5 else ""
        lines += ["", f"<b>📇 Aranacaklar:</b> {names}{more}"]

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
            continue
        # Ayın ilk günü: geçen ayın raporu (bir kez)
        t = todo.now_local().date()
        if t.day == 1:
            prev = (t.replace(day=1) - timedelta(days=1))
            report_key = f"monthly:{user['id']}:{prev.year:04d}-{prev.month:02d}"
            if _state_get(report_key) is None:
                try:
                    telegram.send_message(user["telegram_chat_id"], monthly_report(
                        user, prev.year, prev.month, url_for("expenses.index", ay=f"{prev.year:04d}-{prev.month:02d}",
                                                             _external=True), telegram.escape))
                    _state_set(report_key, today_str())
                    result["reports"] = result.get("reports", 0) + 1
                except telegram.TelegramError as e:
                    result["errors"].append(f"{user['username']} rapor: {e}")

    # Varlık değer geçmişi (grafik için günde bir kayıt; kur alınamazsa o gün atlanır)
    from .assets import record_snapshot
    for row in query("SELECT DISTINCT user_id FROM assets"):
        record_snapshot(row["user_id"])

    # Ufak bakım: 30 günü dolan çöp, eski önbellek ve giriş denemesi kayıtları
    from .. import trash
    result["trash_purged"] = trash.purge()
    from .. import automation
    automation.purge_log()
    external.purge_cache()
    db = get_db()
    db.execute("DELETE FROM login_attempts WHERE first_at < ?", (time.time() - 86400,))
    db.execute("DELETE FROM password_resets WHERE expires_at < ?", (time.time(),))
    db.commit()
    return jsonify(result)


@bp.route("/<secret>/hatirlatma")
def todo_reminders(secret):
    """Yapılacaklar, fatura ve ilaç hatırlatmaları. Sık çağrılmalı (ör. 5 dakikada bir); iş yoksa hemen döner."""
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

    # Etkinlikler: paylaşılan -> Telegram'ı bağlı herkes, özel -> ekleyen
    from .events import mark_sent as mark_event, message as event_message, pending as pending_events, \
        recipients as event_recipients
    ev_send, ev_stale = pending_events(todo.now_local())
    result["events_sent"] = 0
    result["stale"] += len(ev_stale)
    for ev in ev_stale:
        mark_event(ev["id"], "due")
    for kind, ev in ev_send:
        text = event_message(kind, ev, url_for("events.index", _external=True), telegram.escape)
        for chat_id in event_recipients(ev):
            try:
                telegram.send_message(chat_id, text)
                result["events_sent"] += 1
            except telegram.TelegramError as e:
                result["errors"].append(f"etkinlik {ev['id']}: {e}")
        mark_event(ev["id"], kind)

    # Faturalar: yarın / bugün son gün
    bills, stale_bills = scheduled.pending_bills()
    result["bills_sent"], result["stale"] = 0, result["stale"] + len(stale_bills)
    for bill in stale_bills:
        scheduled.mark_bill(bill["id"], "due")
    for kind, bill in bills:
        chat_id = scheduled.chat_of(bill["user_id"])
        if not chat_id:
            result["no_telegram"] += 1
            continue
        try:
            telegram.send_message(chat_id, scheduled.bill_message(kind, bill, url_for("bills.index", _external=True),
                                                                  telegram.escape),
                                  buttons=scheduled.bill_buttons(bill["id"]) if with_buttons else None)
            scheduled.mark_bill(bill["id"], kind)
            result["bills_sent"] += 1
        except telegram.TelegramError as e:
            result["errors"].append(f"fatura {bill['id']}: {e}")

    # Önemli günler: X gün önce ve o gün (varsayılan saatten sonra)
    from .rates import message as rate_message, mark_triggered, triggered
    from .specialdays import mark_sent as mark_day, message as day_message, pending as pending_days
    result["days_sent"] = 0
    now = todo.now_local()
    if now.strftime("%H:%M") >= todo.DEFAULT_DUE_TIME:
        for kind, row, on, days in pending_days(now):
            try:
                telegram.send_message(row["chat_id"], day_message(kind, row, on, days,
                                                                  url_for("specialdays.index", _external=True),
                                                                  telegram.escape))
                mark_day(row["id"], kind, on.year)
                result["days_sent"] += 1
            except telegram.TelegramError as e:
                result["errors"].append(f"önemli gün {row['id']}: {e}")

    # Belgeler: bitişe X gün kala ve bittiği gün (varsayılan saatten sonra)
    from .documents import mark_sent as mark_doc, message as doc_message, pending as pending_docs
    result["documents_sent"] = 0
    if now.strftime("%H:%M") >= todo.DEFAULT_DUE_TIME:
        for kind, doc, left in pending_docs(now):
            try:
                telegram.send_message(doc["chat_id"], doc_message(kind, doc, left, url_for("documents.index", _external=True),
                                                                  telegram.escape))
                mark_doc(doc, kind)
                result["documents_sent"] += 1
            except telegram.TelegramError as e:
                result["errors"].append(f"belge {doc['id']}: {e}")

    # Kişiler: görüşme vakti gelenlere dürtme (varsayılan saatten sonra, günde bir kontrol; her vade için bir kez)
    from . import contacts
    result["contacts_nudged"] = 0
    nudge_day = now.date().isoformat()
    if now.strftime("%H:%M") >= todo.DEFAULT_DUE_TIME and _state_get("contacts_nudge") != nudge_day:
        for row, st in contacts.pending(now):
            url = url_for("contacts.detail", contact_id=row["id"], _external=True)
            try:
                telegram.send_message(row["chat_id"], contacts.nudge_message(row, st, url, telegram.escape),
                                      buttons=contacts.nudge_buttons(row["id"]) if with_buttons else None)
                contacts.mark_nudged(row["id"], st["due"])
                result["contacts_nudged"] += 1
            except telegram.TelegramError as e:
                result["errors"].append(f"kişi {row['id']}: {e}")
        _state_set("contacts_nudge", nudge_day)

    # Siparişler: beklenen teslim günü, iade için son 2 gün ve son gün (varsayılan saatten sonra)
    from .orders import mark_sent as mark_order, message as order_message, pending as pending_orders
    result["orders_sent"] = 0
    if now.strftime("%H:%M") >= todo.DEFAULT_DUE_TIME:
        for kind, order, on in pending_orders(now):
            try:
                telegram.send_message(order["chat_id"], order_message(
                    kind, order, url_for("orders.edit", order_id=order["id"], _external=True), telegram.escape))
                mark_order(order, kind, on)
                result["orders_sent"] += 1
            except telegram.TelegramError as e:
                result["errors"].append(f"sipariş {order['id']}: {e}")

    # Hava uyarısı: akşam, yarın için yağmur / don / sıcak / fırtına (günde bir kez)
    result["weather_alerts"] = 0
    for user, day, found in weather_alerts.pending(now):
        try:
            telegram.send_message(user["telegram_chat_id"], weather_alerts.message(user, day, found, telegram.escape))
            weather_alerts.mark_sent(user["id"], now.date().isoformat())
            result["weather_alerts"] += 1
        except telegram.TelegramError as e:
            result["errors"].append(f"hava {user['username']}: {e}")

    # Günlük: belirlenen saatte, o gün yazılmadıysa "Bugün nasıldı?" (butonlar webhook kuruluysa)
    from . import journal
    result["journal_asked"] = 0
    for user in journal.pending(now):
        day = now.date().isoformat()
        try:
            telegram.send_message(user["telegram_chat_id"],
                                  journal.ask_message(url_for("journal.index", _external=True), telegram.escape,
                                                      interactive=with_buttons),
                                  buttons=journal.mood_buttons(day) if with_buttons else None)
            journal.mark_asked(user["id"], day)
            result["journal_asked"] += 1
        except telegram.TelegramError as e:
            result["errors"].append(f"günlük {user['username']}: {e}")

    # Zaman takibi: 10 saattir çalışan (unutulmuş olabilecek) sayaç için bir kez uyarı
    from .timetrack import long_running, mark_warned, stop_buttons, warn_message
    result["timers_warned"] = 0
    for entry in long_running(now):
        try:
            telegram.send_message(entry["chat_id"], warn_message(entry, now, url_for("timetrack.index", _external=True),
                                                                 telegram.escape),
                                  buttons=stop_buttons(entry["id"]) if with_buttons else None)
            mark_warned(entry["id"])
            result["timers_warned"] += 1
        except telegram.TelegramError as e:
            result["errors"].append(f"sayaç {entry['id']}: {e}")

    # Aktarma kutusu: süresi dolan metin ve dosyalar
    from .transfer import purge_expired
    result["transfers_purged"] = purge_expired()
    from .scanner import purge_inbox
    result["scan_pages_purged"] = purge_inbox()

    # Otomasyon: zamanı gelen kurallar (her dönemde bir kez)
    from .. import automation
    result["automations"] = automation.run_scheduled(now)

    # Bütçe uyarıları (%80 ve %100, her ay bir kez)
    result["budget_alerts"] = 0
    for user, row in budgets.pending_alerts(now):
        try:
            telegram.send_message(user["telegram_chat_id"], budgets.alert_message(
                row, url_for("expenses.index", _external=True), telegram.escape))
            budgets.mark_alert(user["id"], row, now)
            result["budget_alerts"] += 1
        except telegram.TelegramError as e:
            result["errors"].append(f"bütçe {user['username']}: {e}")

    # Kur alarmları (kur önbellekten; saatte en fazla birkaç istek)
    result["rate_alerts"] = 0
    for alert, value, chat_id in triggered():
        try:
            telegram.send_message(chat_id, rate_message(alert, value, url_for("rates.index", _external=True),
                                                        telegram.escape))
            mark_triggered(alert["id"], value)
            result["rate_alerts"] += 1
        except telegram.TelegramError as e:
            result["errors"].append(f"kur alarmı {alert['id']}: {e}")

    # İlaçlar: doz saati geldi
    result["meds_sent"] = 0
    for med, slot, chat_id, day in scheduled.pending_meds():
        log = scheduled.ensure_log(med["id"], day, slot)
        try:
            telegram.send_message(chat_id, scheduled.med_message(med, slot, telegram.escape),
                                  buttons=scheduled.med_buttons(log["id"]) if with_buttons else None)
            scheduled.mark_med_sent(log["id"])
            result["meds_sent"] += 1
        except telegram.TelegramError as e:
            result["errors"].append(f"ilaç {med['id']}: {e}")
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
