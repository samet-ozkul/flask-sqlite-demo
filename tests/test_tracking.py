"""Takip: belge geçerlilik tarihleri, hava uyarısı, izleme/okuma listesi.

Dış servisler çağrılmaz: hava durumu ve kitap/film araması taklit edilir.
Çalıştır: .venv/Scripts/python tests/test_tracking.py
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

app = make_app()
os.environ["CRON_SECRET"] = "gizli"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"
os.environ.pop("TMDB_API_KEY", None)

import pano.external as external  # noqa: E402
import pano.modules.watchlist as watchlist  # noqa: E402
import pano.telegram as tg  # noqa: E402
import pano.todo_reminders as todo  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query_one  # noqa: E402
from pano.utils import TZ, today  # noqa: E402

CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {})) or {"message_id": 1}
NOW = [datetime(2026, 10, 10, 10, 0, tzinfo=TZ)]
todo.now_local = lambda: NOW[0]

FORECAST = {}  # tarih -> gün tahmini
WEATHER_CALLS = []


def fake_weather(lat, lon):
    WEATHER_CALLS.append((lat, lon))
    days = [{"date": d, "code": 3, "min": 12, "max": 22, "rain": 10, "wind_max": 15, **FORECAST.get(d, {})}
            for d in sorted(FORECAST)]
    return {"temp": 18, "feels": 18, "wind": 10, "code": 3, "days": days}


external.weather = fake_weather

with app.app_context():
    AYSE = create_user("ayse", "ayse12345")
    execute("UPDATE users SET telegram_chat_id = '100', city = 'Konya', lat = 37.87, lon = 32.48 WHERE id = 1")
    execute("UPDATE users SET telegram_chat_id = '200', city = 'Konya', lat = 37.87, lon = 32.48 WHERE id = ?", (AYSE,))
ADMIN = Client(app)
AYSE_C = Client(app, "ayse", "ayse12345")
RAW = app.test_client()


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def run(sql, args=()):
    with app.app_context():
        execute(sql, args)


def sent():
    return [(p["chat_id"], p["text"]) for m, p in CALLS if m == "sendMessage"]


def cron(name="hatirlatma"):
    r = RAW.get(f"/cron/gizli/{name}")
    assert r.status_code == 200, r.status_code
    return r.json


def test_documents():
    ADMIN.post("/belgeler/yeni", data={"kind": "passport", "holder": "Ayşe", "expires_on": "2026-11-01",
                                       "notify_days": "30", "note": "Randevu al"})
    ADMIN.post("/belgeler/yeni", data={"kind": "license", "name": "Ehliyet", "expires_on": "2027-06-01",
                                       "notify_days": "999"})  # geçersiz seçenek -> 30
    ADMIN.post("/belgeler/yeni", data={"kind": "id", "name": "Tarihsiz"})  # reddedilir
    passport = one("SELECT * FROM documents WHERE kind = 'passport'")
    assert passport["name"] == "Pasaport" and passport["holder"] == "Ayşe"  # ad boşsa türün adı
    assert one("SELECT notify_days FROM documents WHERE name = 'Ehliyet'")["notify_days"] == 30
    assert one("SELECT COUNT(*) AS n FROM documents")["n"] == 2
    page = ADMIN.text("/belgeler/")
    assert "Randevu al" in page and "Bitiş 1 Haz 2027" in page
    # Başkası göremez / düzenleyemez
    assert "Randevu al" not in AYSE_C.text("/belgeler/")
    assert AYSE_C.get(f"/belgeler/{passport['id']}").status_code == 404
    assert AYSE_C.post(f"/belgeler/{passport['id']}/sil").status_code == 404

    # Hatırlatma: 09:00'dan önce gönderilmez; 30 gün içindeyse bir kez "X gün sonra"
    NOW[0] = datetime(2026, 10, 10, 8, 30, tzinfo=TZ)
    assert cron()["documents_sent"] == 0
    NOW[0] = datetime(2026, 10, 10, 9, 5, tzinfo=TZ)
    before = len(sent())
    assert cron()["documents_sent"] == 1
    chat, text = sent()[-1]
    assert chat == "100" and "22 gün sonra bitiyor" in text and "Pasaport" in text and "(Ayşe)" in text
    assert cron()["documents_sent"] == 0 and len(sent()) == before + 1
    # Bittiği gün ayrı mesaj
    NOW[0] = datetime(2026, 11, 1, 9, 30, tzinfo=TZ)
    assert cron()["documents_sent"] == 1 and "Bugün bitiyor" in sent()[-1][1]
    assert cron()["documents_sent"] == 0
    # Yenilenince (tarih değişince) yeni tarih için yeniden hatırlatılır
    ADMIN.post(f"/belgeler/{passport['id']}", data={"kind": "passport", "name": "Pasaport", "holder": "Ayşe",
                                                   "expires_on": "2026-11-20", "notify_days": "30"})
    assert cron()["documents_sent"] == 1 and "19 gün sonra" in sent()[-1][1]
    NOW[0] = datetime(2026, 10, 10, 10, 0, tzinfo=TZ)

    # Pano (yaklaşanlar), takvim ve aramada görünür
    soon = (today() + timedelta(days=10)).isoformat()
    ADMIN.post("/belgeler/yeni", data={"kind": "insurance", "name": "Konut sigortası", "expires_on": soon})
    ADMIN.text("/belgeler/")  # "eklendi" bildirimi panoyu yanıltmasın
    assert "Konut sigortası bitiyor" in ADMIN.text("/")
    assert "Konut sigortası bitiyor" in ADMIN.text(f"/takvim/?ay={soon[:7]}")
    assert "Konut sigortası" in ADMIN.text("/ara/?q=konut")
    assert "Konut sigortası" not in AYSE_C.text("/ara/?q=konut")
    # Silme
    doc = one("SELECT id FROM documents WHERE name = 'Konut sigortası'")["id"]
    ADMIN.post(f"/belgeler/{doc}/sil")
    assert one("SELECT id FROM documents WHERE id = ?", (doc,)) is None
    print("  documents OK")


def test_weather_alerts():
    t = datetime(2026, 10, 12, 19, 0, tzinfo=TZ)
    NOW[0] = t
    tomorrow = "2026-10-13"
    FORECAST.clear()
    FORECAST.update({"2026-10-12": {}, tomorrow: {"code": 63, "rain": 85, "min": -1, "wind_max": 55}})
    # Akşam 20:00'den önce bakılmaz
    WEATHER_CALLS.clear()
    assert cron()["weather_alerts"] == 0 and not WEATHER_CALLS
    # Ayşe uyarıyı kapatır (ayarlar; şehir aynı kalınca konum yeniden aranmaz)
    AYSE_C.post("/ayarlar/profil", data={"display_name": "Ayşe", "city": "Konya", "notify_daily": "1"})
    assert one("SELECT weather_alerts, lat FROM users WHERE id = ?", (AYSE,))["weather_alerts"] == 0
    assert one("SELECT lat FROM users WHERE id = ?", (AYSE,))["lat"] == 37.87
    NOW[0] = t.replace(hour=20, minute=10)
    before = len(sent())
    assert cron()["weather_alerts"] == 1
    new = sent()[before:]
    assert [c for c, _ in new] == ["100"]
    text = new[0][1]
    assert "Yarın için hava uyarısı" in text and "Yağmur bekleniyor (%85)" in text
    assert "Don riski" in text and "Kuvvetli rüzgâr: 55 km/s" in text and "Konya" in text
    # Aynı akşam tekrar gönderilmez, hava servisine de yeniden sorulmaz
    WEATHER_CALLS.clear()
    assert cron()["weather_alerts"] == 0 and not WEATHER_CALLS
    # Ertesi akşam uyarı yoksa mesaj yok ve bir kez bakılır
    NOW[0] = datetime(2026, 10, 13, 21, 0, tzinfo=TZ)
    FORECAST.update({"2026-10-14": {}})
    WEATHER_CALLS.clear()
    assert cron()["weather_alerts"] == 0 and len(WEATHER_CALLS) == 1
    assert cron()["weather_alerts"] == 0 and len(WEATHER_CALLS) == 1
    # Sıcak ve kar/fırtına uyarı metinleri
    from pano.weather_alerts import alerts
    assert alerts({"code": 0, "max": 38, "min": 24}) == ["Aşırı sıcak: 38° — bol su iç, öğle güneşinden kaçın"]
    assert alerts({"code": 95, "rain": 90})[0].startswith("Gök gürültülü")
    assert alerts({"code": 73, "rain": 90, "min": -3})[0].startswith("Kar") and len(alerts({"code": 73, "min": -3})) == 2
    # Günlük özet: bugünün uyarıları (uyarısı açık olana)
    FORECAST.clear()
    FORECAST.update({today().isoformat(): {"rain": 70}})
    before = len(sent())
    cron("gunluk")
    daily = dict(sent()[before:])
    assert "⚠️ Yağmur bekleniyor (%70)" in daily["100"] and "⚠️" not in daily["200"]
    print("  weather alerts OK")


def test_watchlist():
    requests_seen = []

    def fake_get_json(url, headers=None):
        requests_seen.append((url, headers or {}))
        if "openlibrary.org" in url:
            return {"docs": [{"key": "/works/OL1W", "title": "Kürk Mantolu Madonna", "author_name": ["Sabahattin Ali"],
                              "first_publish_year": 1943, "cover_i": 123}, {"key": "/works/x"}]}
        return {"results": [
            {"media_type": "movie", "id": 5, "title": "Babam ve Oğlum", "release_date": "2005-11-18", "poster_path": "/p.jpg"},
            {"media_type": "tv", "id": 7, "name": "Leyla ile Mecnun", "first_air_date": "2011-02-01"},
            {"media_type": "person", "id": 9, "name": "Biri"},
        ]}

    watchlist._get_json = fake_get_json
    page = ADMIN.text("/izleme/ara?tur=book&q=kürk")
    assert "Kürk Mantolu Madonna" in page and "Sabahattin Ali" in page and "covers.openlibrary.org/b/id/123-M.jpg" in page
    # Anahtar yoksa film araması yapılmaz, elle ekleme önerilir
    requests_seen.clear()
    assert "TMDB_API_KEY" in ADMIN.text("/izleme/ara?tur=movie&q=babam") and not requests_seen
    os.environ["TMDB_API_KEY"] = "eyJtest"
    page = ADMIN.text("/izleme/ara?tur=movie&q=babam")
    assert "Babam ve Oğlum" in page and "Leyla ile Mecnun" in page and "Biri" not in page
    assert requests_seen[-1][1].get("Authorization") == "Bearer eyJtest" and "api_key" not in requests_seen[-1][0]
    os.environ["TMDB_API_KEY"] = "v3key"
    ADMIN.text("/izleme/ara?tur=movie&q=babam")
    assert "api_key=v3key" in requests_seen[-1][0] and "Authorization" not in requests_seen[-1][1]
    os.environ.pop("TMDB_API_KEY")

    # Sonuçtan ekleme; aynısı ikinci kez eklenmez; güvensiz kapak adresi atılır
    book = {"kind": "book", "title": "Kürk Mantolu Madonna", "year": "1943", "creator": "Sabahattin Ali",
            "image_url": "https://covers.openlibrary.org/b/id/123-M.jpg", "external_id": "/works/OL1W"}
    ADMIN.post("/izleme/yeni", data=book)
    ADMIN.post("/izleme/yeni", data=book)
    ADMIN.post("/izleme/yeni", data={"kind": "movie", "title": "Elle film", "image_url": "https://kotu.example/x.jpg",
                                     "status": "done"})
    ADMIN.post("/izleme/yeni", data={"kind": "series", "title": ""})  # adsız reddedilir
    assert one("SELECT COUNT(*) AS n FROM watchlist")["n"] == 2
    film = one("SELECT * FROM watchlist WHERE title = 'Elle film'")
    assert film["image_url"] == "" and film["status"] == "done" and film["finished_at"]
    item = one("SELECT * FROM watchlist WHERE external_id = '/works/OL1W'")
    assert item["status"] == "want" and item["started_at"] is None
    # Durum: başladım -> bitirdim (tarihler dolar), puan ve not
    ADMIN.post(f"/izleme/{item['id']}/durum", data={"status": "doing"})
    assert one("SELECT started_at FROM watchlist WHERE id = ?", (item["id"],))["started_at"]
    ADMIN.post(f"/izleme/{item['id']}", data={"title": item["title"], "kind": "book", "year": "1943",
                                              "creator": "Sabahattin Ali", "status": "done", "rating": "5",
                                              "note": "Çok güzel"})
    row = one("SELECT * FROM watchlist WHERE id = ?", (item["id"],))
    assert (row["status"], row["rating"], row["note"]) == ("done", 5, "Çok güzel") and row["finished_at"]
    ADMIN.text("/izleme/")  # birikmiş "eklendi" bildirimleri burada gösterilsin
    page = ADMIN.text("/izleme/?tur=book")
    assert "Kürk Mantolu Madonna" in page and "★★★★★" in page and "Elle film" not in page
    assert "Elle film" in ADMIN.text("/izleme/?durum=done")
    # Başkası göremez; aramada çıkar
    assert "Kürk" not in AYSE_C.text("/izleme/")
    assert AYSE_C.post(f"/izleme/{item['id']}/sil").status_code == 404
    assert "Kürk Mantolu Madonna" in ADMIN.text("/ara/?q=sabahattin")
    ADMIN.post(f"/izleme/{item['id']}/sil")
    assert one("SELECT id FROM watchlist WHERE id = ?", (item["id"],)) is None
    # Arama servisi hata verirse sayfa açılır
    watchlist._get_json = lambda url, headers=None: (_ for _ in ()).throw(OSError("yok"))
    assert "ulaşılamadı" in ADMIN.text("/izleme/ara?tur=book&q=x")
    print("  watchlist OK")


def test_menu():
    home = ADMIN.text("/")
    assert "Belgeler" in home and "İzleme / Okuma" in home
    print("  menu OK")


if __name__ == "__main__":
    test_documents()
    test_weather_alerts()
    test_watchlist()
    test_menu()
    print("OK")
