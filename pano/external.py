"""Dış API'ler (hepsi PythonAnywhere ücretsiz izin listesinde) + SQLite önbelleği.

Pano açılışını yavaşlatmamak için kısa timeout; hata olursa eski önbellek kullanılır.
"""
import json
import os
import time
import urllib.parse
import urllib.request

from .db import get_db

TIMEOUT = 4
USER_AGENT = "KisiselPano/1.0"

WEATHER_CODES = {
    0: ("Açık", "☀️"), 1: ("Az bulutlu", "🌤️"), 2: ("Parçalı bulutlu", "⛅"), 3: ("Kapalı", "☁️"),
    45: ("Sisli", "🌫️"), 48: ("Kırağılı sis", "🌫️"),
    51: ("Hafif çisenti", "🌦️"), 53: ("Çisenti", "🌦️"), 55: ("Yoğun çisenti", "🌧️"),
    56: ("Dondurucu çisenti", "🌧️"), 57: ("Dondurucu çisenti", "🌧️"),
    61: ("Hafif yağmur", "🌦️"), 63: ("Yağmur", "🌧️"), 65: ("Kuvvetli yağmur", "🌧️"),
    66: ("Dondurucu yağmur", "🌧️"), 67: ("Dondurucu yağmur", "🌧️"),
    71: ("Hafif kar", "🌨️"), 73: ("Kar", "🌨️"), 75: ("Yoğun kar", "❄️"), 77: ("Kar taneleri", "🌨️"),
    80: ("Sağanak", "🌦️"), 81: ("Sağanak", "🌧️"), 82: ("Şiddetli sağanak", "⛈️"),
    85: ("Kar sağanağı", "🌨️"), 86: ("Yoğun kar sağanağı", "❄️"),
    95: ("Gök gürültülü", "⛈️"), 96: ("Dolu ile fırtına", "⛈️"), 99: ("Dolu ile fırtına", "⛈️"),
}


def _fetch_json(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cached(key, ttl, fetch):
    """Önbellekte tazeyse onu, değilse fetch() sonucunu döner. fetch hata verirse eski değer."""
    db = get_db()
    row = db.execute("SELECT value, expires_at FROM cache WHERE key = ?", (key,)).fetchone()
    if row and row["expires_at"] > time.time():
        return json.loads(row["value"])
    try:
        value = fetch()
    except Exception:
        return json.loads(row["value"]) if row else None
    if value is not None:
        db.execute(
            "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), time.time() + ttl),
        )
        db.commit()
    return value


def purge_cache():
    db = get_db()
    db.execute("DELETE FROM cache WHERE expires_at < ?", (time.time() - 7 * 86400,))
    db.commit()


# ---------- Hava durumu (Open-Meteo, anahtar gerekmez) ----------
def geocode(name):
    """Şehir adı -> {'name', 'lat', 'lon'} ya da None."""
    q = urllib.parse.urlencode({"name": name, "count": 1, "language": "tr", "format": "json"})
    try:
        data = _fetch_json(f"https://geocoding-api.open-meteo.com/v1/search?{q}")
    except Exception:
        return None
    results = data.get("results") or []
    if not results:
        return None
    r = results[0]
    label = r["name"] + (f", {r['admin1']}" if r.get("admin1") and r["admin1"] != r["name"] else "")
    return {"name": label, "lat": r["latitude"], "lon": r["longitude"]}


def weather(lat, lon):
    if lat is None or lon is None:
        return None
    lat, lon = round(lat, 2), round(lon, 2)

    def fetch():
        q = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": os.environ.get("APP_TZ", "Europe/Istanbul"),
            "forecast_days": 4,
        })
        data = _fetch_json(f"https://api.open-meteo.com/v1/forecast?{q}")
        cur = data["current"]
        daily = data["daily"]
        return {
            "temp": round(cur["temperature_2m"]),
            "feels": round(cur["apparent_temperature"]),
            "wind": round(cur["wind_speed_10m"]),
            "code": cur["weather_code"],
            "days": [
                {
                    "date": daily["time"][i],
                    "code": daily["weather_code"][i],
                    "max": round(daily["temperature_2m_max"][i]),
                    "min": round(daily["temperature_2m_min"][i]),
                    "rain": daily["precipitation_probability_max"][i],
                }
                for i in range(len(daily["time"]))
            ],
        }

    return cached(f"weather:{lat}:{lon}", 30 * 60, fetch)


def weather_label(code):
    return WEATHER_CODES.get(code, ("", "🌡️"))


# ---------- Döviz (Frankfurter / ECB, anahtar gerekmez) ----------
def rates():
    """{'USD': 48.85, 'EUR': 55.52, 'GBP': 64.6, 'date': '2026-09-24'} — 1 birimin TL karşılığı."""
    def fetch():
        data = None
        for base in ("https://api.frankfurter.dev/v1", "https://api.frankfurter.app"):
            try:
                data = _fetch_json(f"{base}/latest?base=TRY&symbols=USD,EUR,GBP")
                break
            except Exception:
                continue
        if not data:
            raise RuntimeError("kur alınamadı")
        out = {code: round(1 / value, 4) for code, value in data["rates"].items() if value}
        out["date"] = data.get("date")
        return out

    return cached("rates:TRY", 3 * 3600, fetch)


def to_try(amount, currency, rate_table=None):
    """Tutarı TL'ye çevirir; kur yoksa None."""
    if amount is None:
        return None
    if currency == "TRY":
        return amount
    table = rate_table if rate_table is not None else rates()
    if not table or currency not in table:
        return None
    return amount * table[currency]


# ---------- Gram altın (isteğe bağlı: GOLDAPI_KEY, goldapi.io ücretsiz plan) ----------
def gold_gram_try():
    key = os.environ.get("GOLDAPI_KEY")
    if not key:
        return None

    def fetch():
        data = _fetch_json("https://www.goldapi.io/api/XAU/TRY", headers={"x-access-token": key})
        gram = data.get("price_gram_24k") or (data["price"] / 31.1034768)
        return {"gram": round(gram, 2)}

    # Ücretsiz plan ayda ~100 istek: 8 saatlik önbellek ≈ ayda 90 istek
    result = cached("gold:gram", 8 * 3600, fetch)
    return result["gram"] if result else None
