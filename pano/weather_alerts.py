"""⚠️ Hava uyarısı: yarın yağmur, don, aşırı sıcak ya da fırtına varsa akşam Telegram'dan haber verir.

cron /hatirlatma her çağrıldığında bakar; ALERT_TIME'dan sonra, kullanıcı başına günde en fazla bir kez
(bakılan gün app_state'te). Şehri ayarlanmış ve ayarlardan uyarıyı kapatmamış kullanıcılara gider.
Aynı şehirdeki kullanıcılar için hava durumu önbellekten gelir (30 dakika).
"""
from datetime import date, timedelta

from . import external
from .db import get_db, query, query_one
from .utils import WEEKDAYS_TR, fmt_date

ALERT_TIME = "20:00"
RAIN_PCT = 60       # yağış olasılığı %
FROST_C = 0         # en düşük sıcaklık
HOT_C = 35          # en yüksek sıcaklık
WIND_KMH = 50       # en yüksek rüzgâr


def alerts(day):
    """Bir günün tahmininden uyarı metinleri: ['Yağmur bekleniyor (%80) — şemsiyeni al', ...]."""
    out = []
    code = day.get("code") or 0
    if code in (95, 96, 99):
        out.append("Gök gürültülü sağanak bekleniyor")
    elif code in (71, 73, 75, 77, 85, 86):
        out.append("Kar bekleniyor — yollar kaygan olabilir")
    elif (day.get("rain") or 0) >= RAIN_PCT:
        out.append(f"Yağmur bekleniyor (%{day['rain']}) — şemsiyeni al")
    if day.get("min") is not None and day["min"] <= FROST_C:
        out.append(f"Don riski: en düşük {day['min']}° — bitkileri ve aracı koru")
    if day.get("max") is not None and day["max"] >= HOT_C:
        out.append(f"Aşırı sıcak: {day['max']}° — bol su iç, öğle güneşinden kaçın")
    if (day.get("wind_max") or 0) >= WIND_KMH:
        out.append(f"Kuvvetli rüzgâr: {day['wind_max']} km/s'ye kadar")
    return out


def _key(user_id):
    return f"weather_alert:{user_id}"


def pending(now):
    """[(kullanıcı, yarının tahmini, uyarılar)] — sadece akşam ve bugün henüz bakılmadıysa.
    Uyarı çıkmayan kullanıcılar da işaretlenir; böylece akşam boyunca tekrar tekrar bakılmaz."""
    if now.strftime("%H:%M") < ALERT_TIME:
        return []
    today_s = now.date().isoformat()
    tomorrow = (now.date() + timedelta(days=1)).isoformat()
    out = []
    for user in query("SELECT * FROM users WHERE telegram_chat_id IS NOT NULL AND weather_alerts = 1"
                      " AND lat IS NOT NULL AND lon IS NOT NULL"):
        row = query_one("SELECT value FROM app_state WHERE key = ?", (_key(user["id"]),))
        if row and row["value"] >= today_s:
            continue
        weather = external.weather(user["lat"], user["lon"])
        day = next((d for d in (weather or {}).get("days", []) if d["date"] == tomorrow), None)
        if day is None:
            continue  # servis yanıt vermedi; sonraki çağrıda yeniden denenir
        found = alerts(day)
        if found:
            out.append((user, day, found))
        else:
            mark_sent(user["id"], today_s)
    return out


def mark_sent(user_id, checked_on):
    db = get_db()
    db.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)", (_key(user_id), checked_on))
    db.commit()


def message(user, day, found, escape):
    label, icon = external.weather_label(day["code"])
    d = date.fromisoformat(day["date"])
    lines = [f"⚠️ <b>Yarın için hava uyarısı</b> · {escape(user['city'] or '')}",
             f"{fmt_date(day['date'])} {WEEKDAYS_TR[d.weekday()]}: {icon} {day['min']}°/{day['max']}°, {label}", ""]
    lines += [f"• {escape(a)}" for a in found]
    return "\n".join(lines)
