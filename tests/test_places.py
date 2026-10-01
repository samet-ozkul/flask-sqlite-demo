"""Harita: şehir arama (geocoding), harita linki çözümleme, şehir/yer kayıtları, park yeri, Telegram konumu,
arama ve çöp kutusu.

Dış servis çağrılmaz: Open-Meteo geocoding taklit edilir.
Çalıştır: .venv/Scripts/python tests/test_places.py
"""
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

app = make_app()
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.external as external  # noqa: E402
import pano.telegram as tg  # noqa: E402
import pano.todo_reminders as todo  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query, query_one  # noqa: E402
from pano.modules.places import is_short_link, parse_map_link, parse_visit, visit_label  # noqa: E402
from pano.utils import TZ  # noqa: E402

CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {})) or {"message_id": 1}
NOW = [datetime(2026, 10, 5, 14, 30, tzinfo=TZ)]
todo.now_local = lambda: NOW[0]

ROMA = [
    {"name": "Roma", "admin": "Lazio", "country": "İtalya", "lat": 41.89193, "lon": 12.51133},
    {"name": "Roma", "admin": "Queensland", "country": "Avustralya", "lat": -26.56667, "lon": 148.78333},
]
GEO_CALLS = []


def fake_geocode_many(name, count=5):
    GEO_CALLS.append(name)
    if name == "hata":
        return None
    return ROMA if name.lower() == "roma" else []


with app.app_context():
    AYSE = create_user("ayse", "ayse12345")
    execute("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
with app.test_request_context():
    SECRET = tg.webhook_secret()
ADMIN = Client(app)
AYSE_C = Client(app, "ayse", "ayse12345")
RAW = app.test_client()


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def rows(sql, args=()):
    with app.app_context():
        return query(sql, args)


def map_data(page):
    m = re.search(r'<script type="application/json" id="map-data">(.*?)</script>', page, re.S)
    return json.loads(m.group(1))


def calls(method):
    return [p for m, p in CALLS if m == method]


def buttons(params):
    markup = json.loads(params.get("reply_markup") or "{}")
    return [b["callback_data"] for row in markup.get("inline_keyboard", []) for b in row]


def send(message, chat=100):
    RAW.post("/telegram/webhook", data=json.dumps({"message": {"message_id": 1, "chat": {"id": chat}, **message}}),
             content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})


def press(data, chat=100):
    RAW.post("/telegram/webhook", data=json.dumps({"callback_query": {
        "id": "cb", "data": data, "from": {"id": chat}, "message": {"message_id": 7, "chat": {"id": chat}}}}),
        content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})


def test_geocode():
    sample = {"results": [
        {"name": "Roma", "admin1": "Lazio", "country": "İtalya", "latitude": 41.891930, "longitude": 12.511330},
        {"name": "Roma", "country": "Avustralya", "latitude": -26.566670, "longitude": 148.783330},
        {"name": "Bozuk"},  # koordinatsız aday atlanır
    ]}
    urls = []
    original = external._fetch_json

    def fake_fetch(url, headers=None):
        urls.append(url)
        if "Yok" in url:
            raise OSError("bağlantı yok")
        return sample

    external._fetch_json = fake_fetch
    try:
        with app.app_context():
            got = external.geocode_many("  Roma ")
            assert got == [{"name": "Roma", "admin": "Lazio", "country": "İtalya", "lat": 41.89193, "lon": 12.51133},
                           {"name": "Roma", "admin": "", "country": "Avustralya", "lat": -26.56667, "lon": 148.78333}]
            assert "count=5" in urls[0] and "language=tr" in urls[0] and "name=Roma" in urls[0]
            assert external.geocode_many("roma") == got and len(urls) == 1  # önbellekten
            assert external.geocode_many("Yok") is None  # servise ulaşılamadı
            assert external.geocode_many("R") == []
            # Eski geocode (Ayarlar'daki şehir) bozulmadı
            assert external.geocode("Roma") == {"name": "Roma, Lazio", "lat": 41.89193, "lon": 12.51133}
    finally:
        external._fetch_json = original
    print("  geocode OK")


def test_links():
    cases = {
        # Google yer sayfası: veri kısmındaki asıl nokta (!3d!4d) harita merkezine (@) tercih edilir
        "https://www.google.com/maps/place/Mevlana+M%C3%BCzesi/@37.8708831,32.502426,17z/data=!3m1!4b1!4m6!3m5"
        "!1s0x14d085a3d4a3b5c1:0x9f!8m2!3d37.8708831!4d32.5050009!16s%2Fm%2F02r2zbq": (37.870883, 32.505001),
        "https://www.google.com/maps/@37.8746,32.4932,15z": (37.8746, 32.4932),
        "https://maps.google.com/?q=37.8746,32.4932": (37.8746, 32.4932),
        "https://www.google.com/maps/search/?api=1&query=37.8746%2C32.4932": (37.8746, 32.4932),
        "https://www.google.com/maps?q=loc:41.0082,28.9784": (41.0082, 28.9784),
        "https://www.google.com/maps/dir/?api=1&destination=37.87,32.49": (37.87, 32.49),
        "https://www.google.com/maps/search/37.8746,+32.4932?entry=tts": (37.8746, 32.4932),
        "https://maps.apple.com/?address=Konya&ll=37.8746,32.4932&q=Mevlana%20M%C3%BCzesi": (37.8746, 32.4932),
        "https://maps.apple.com/place?coordinate=37.8746,32.4932&name=Mevlana": (37.8746, 32.4932),
        "https://www.openstreetmap.org/?mlat=37.87461&mlon=32.49321#map=17/37.87461/32.49321": (37.87461, 32.49321),
        "https://www.openstreetmap.org/#map=15/-33.8688/151.2093": (-33.8688, 151.2093),
        "https://yandex.com.tr/harita/?ll=32.4932%2C37.8746&z=16": (37.8746, 32.4932),
        "Mevlana Müzesi\nAşkan Mah.\nhttps://maps.google.com/?q=37.8746,32.4932": (37.8746, 32.4932),
        "37.8746, 32.4932": (37.8746, 32.4932),
        "-22.9519,-43.2105": (-22.9519, -43.2105),
        "geo:37.8746,32.4932?q=Mevlana": (37.8746, 32.4932),
        "37.87461° N, 32.49321° E": (37.87461, 32.49321),
        "40,7128° K, 74,0060° B": (40.7128, -74.006),
    }
    for text, expected in cases.items():
        assert parse_map_link(text) == expected, (text, parse_map_link(text))
    for bad in ("", "Konya", "https://www.google.com/maps/place/Mevlana", "https://maps.app.goo.gl/AbCdEf123",
                "99.1, 32.4", "37.87, 200.5", "https://www.openstreetmap.org/node/123", "nan, nan"):
        assert parse_map_link(bad) is None, bad
    assert is_short_link("bak: https://maps.app.goo.gl/AbCdEf123") and is_short_link("https://goo.gl/maps/xyz")
    assert not is_short_link("https://www.google.com/maps/@37.8,32.4,15z")
    # Ziyaret tarihi
    assert parse_visit("2024-07") == "2024-07" and parse_visit("2024-7-5") == "2024-07-05"
    assert parse_visit("15.07.2024") == "2024-07-15" and parse_visit("07/2024") == "2024-07"
    assert parse_visit("2024-13") is None and parse_visit("31.02.2024") is None and parse_visit("yaz") is None
    assert visit_label("2024-07") == "Temmuz 2024" and visit_label("2024-07-15") == "15 Temmuz 2024"
    print("  links OK")


def test_cities():
    import pano.modules.places as places
    places.external.geocode_many = fake_geocode_many
    page = ADMIN.text("/harita/sehir/yeni?q=Roma")
    assert "Lazio, İtalya" in page and "Queensland, Avustralya" in page and "“Roma” adıyla ekle" in page
    assert "ulaşılamadı" in ADMIN.text("/harita/sehir/yeni?q=hata")
    # İkinci aday seçilir: ad, ülke, konum adaydan
    form = {"q": "Roma", "pick": "1", "status": "wish", "visited_on": "2027-05", "note": "Bir gün"}
    for i, c in enumerate(ROMA):
        form.update({f"name_{i}": c["name"], f"country_{i}": c["country"], f"lat_{i}": c["lat"], f"lon_{i}": c["lon"]})
    ADMIN.post("/harita/sehir/yeni", data=form)
    c = one("SELECT * FROM cities WHERE country = 'Avustralya'")
    assert (c["name"], c["lat"], c["lon"], c["status"], c["visited_on"], c["note"]) == \
        ("Roma", -26.56667, 148.78333, "wish", "2027-05", "Bir gün")
    ADMIN.post("/harita/sehir/yeni", data={**form, "pick": "0", "status": "visited", "visited_on": "07.2024",
                                           "note": "**Kolezyum** sabah erken"})
    roma = one("SELECT * FROM cities WHERE country = 'İtalya'")
    assert roma["status"] == "visited" and roma["visited_on"] == "2024-07" and roma["lat"] == 41.89193
    # Aynı şehir ikinci kez eklenmez
    r = ADMIN.post("/harita/sehir/yeni", data={**form, "pick": "0"}, follow_redirects=True)
    assert "zaten haritanda" in r.get_data(as_text=True)
    assert one("SELECT COUNT(*) AS n FROM cities WHERE user_id = 1")["n"] == 2
    # Listede yok: sadece adıyla (konumsuz); anlaşılmayan tarih boş kalır
    r = ADMIN.post("/harita/sehir/yeni", data={"q": "Konya", "pick": "manual", "country": "Türkiye",
                                               "visited_on": "geçen yaz"}, follow_redirects=True)
    assert "Tarih anlaşılamadı" in r.get_data(as_text=True)
    konya = one("SELECT * FROM cities WHERE name = 'Konya'")
    assert konya["lat"] is None and konya["country"] == "Türkiye" and konya["visited_on"] is None
    # Şehir sayfası: not Markdown, tarih Türkçe
    page = ADMIN.text(f"/harita/sehir/{roma['id']}")
    assert "<strong>Kolezyum</strong>" in page and "Temmuz 2024" in page and "Bu şehirde henüz yer yok" in page
    assert map_data(page)["view"] == {"lat": 41.89193, "lon": 12.51133, "zoom": 12}
    # Düzenle: linkle konum verilir; link yoksa konum korunur
    ADMIN.post(f"/harita/sehir/{konya['id']}/duzenle", data={"name": "Konya", "country": "Türkiye", "status": "visited",
                                                             "maplink": "https://maps.google.com/?q=37.8746,32.4932"})
    assert (one("SELECT lat, lon FROM cities WHERE id = ?", (konya["id"],))["lat"]) == 37.8746
    ADMIN.post(f"/harita/sehir/{konya['id']}/duzenle", data={"name": "Konya", "country": "Türkiye", "status": "visited",
                                                             "note": "Etliekmek", "visited_on": "2025-03-01"})
    k = one("SELECT * FROM cities WHERE id = ?", (konya["id"],))
    assert (k["lat"], k["lon"], k["note"], k["visited_on"]) == (37.8746, 32.4932, "Etliekmek", "2025-03-01")
    # Başkası göremez, değiştiremez, silemez
    assert AYSE_C.get(f"/harita/sehir/{roma['id']}").status_code == 404
    assert AYSE_C.post(f"/harita/sehir/{roma['id']}/duzenle", data={"name": "X"}).status_code == 404
    assert AYSE_C.post(f"/harita/sehir/{roma['id']}/sil").status_code == 404
    assert one("SELECT name FROM cities WHERE id = ?", (roma["id"],))["name"] == "Roma"
    print("  cities OK")


def test_places():
    roma = one("SELECT * FROM cities WHERE country = 'İtalya'")
    konya = one("SELECT * FROM cities WHERE name = 'Konya'")
    page = ADMIN.text(f"/harita/yer/yeni?sehir={roma['id']}")
    assert f'<option value="{roma["id"]}" selected>' in page and "📍 Şu anki konumum" in page
    # Haritaya dokunarak (gizli lat/lon)
    ADMIN.post("/harita/yer/yeni", data={"name": "Da Enzo", "category": "food", "status": "visited", "rating": "5",
                                         "city_id": roma["id"], "lat": "41.888612", "lon": "12.477135",
                                         "address": "Via dei Vascellari 29", "note": "Cacio e pepe"})
    enzo = one("SELECT * FROM places WHERE name = 'Da Enzo'")
    assert (enzo["city_id"], enzo["category"], enzo["status"], enzo["rating"], enzo["lat"], enzo["lon"]) == \
        (roma["id"], "food", "visited", 5, 41.888612, 12.477135)
    # Link yapıştırılınca gizli alanlara tercih edilir
    ADMIN.post("/harita/yer/yeni", data={"name": "Kolezyum", "category": "sight", "city_id": roma["id"], "lat": "1",
                                         "lon": "1", "maplink": "https://www.openstreetmap.org/?mlat=41.89021&mlon=12.49223"})
    assert tuple(one("SELECT lat, lon, status FROM places WHERE name = 'Kolezyum'")) == (41.89021, 12.49223, "wish")
    # Kısa link çözülemez: uyarı, yer konumsuz eklenir; geçersiz puan/kategori yok sayılır
    r = ADMIN.post("/harita/yer/yeni", data={"name": "Sille", "category": "uzay", "rating": "9", "city_id": konya["id"],
                                             "maplink": "https://maps.app.goo.gl/AbCdEf123"}, follow_redirects=True)
    text = r.get_data(as_text=True)
    assert "Kısa linkler" in text and "Konumu yok" in text
    sille = one("SELECT * FROM places WHERE name = 'Sille'")
    assert sille["lat"] is None and sille["category"] == "other" and sille["rating"] is None
    # Şehre bağlı olmayan yer; başkasının şehri seçilemez
    with app.app_context():
        ayse_city = execute("INSERT INTO cities (user_id, name) VALUES (?, 'Ayşe şehri')", (AYSE,)).lastrowid
    ADMIN.post("/harita/yer/yeni", data={"name": "Yol üstü çay", "category": "cafe", "city_id": ayse_city,
                                         "maplink": "38.5, 33.1"})
    cay = one("SELECT * FROM places WHERE name = 'Yol üstü çay'")
    assert cay["city_id"] is None and cay["lat"] == 38.5
    # Şehir sayfası: kategoriye göre gruplu, yol tarifi linki
    page = ADMIN.text(f"/harita/sehir/{roma['id']}")
    assert "🍽️ Yemek yerleri" in page and "🏛️ Gezilecek yerler" in page and "Cacio e pepe" in page
    assert "https://www.google.com/maps/dir/?api=1&amp;destination=41.888612,12.477135" in page
    data = map_data(page)
    assert {p["name"] for p in data["places"]} == {"Da Enzo", "Kolezyum"} and data["cities"][0]["count"] == 2
    # Düzenleme sayfası: yol tarifi + haritada aç; konumu kaldırma (boş gizli alanlar)
    page = ADMIN.text(f"/harita/yer/{enzo['id']}")
    assert "🧭 Yol tarifi" in page and "https://www.openstreetmap.org/?mlat=41.888612&amp;mlon=12.477135#map=17/41.888612/12.477135" in page
    ADMIN.post(f"/harita/yer/{cay['id']}", data={"name": "Yol üstü çay bahçesi", "category": "cafe", "status": "visited",
                                                 "lat": "", "lon": "", "city_id": konya["id"]})
    cay = one("SELECT * FROM places WHERE id = ?", (cay["id"],))
    assert (cay["name"], cay["lat"], cay["city_id"], cay["status"]) == ("Yol üstü çay bahçesi", None, konya["id"], "visited")
    # Gezdim işareti
    kol = one("SELECT id FROM places WHERE name = 'Kolezyum'")["id"]
    ADMIN.post(f"/harita/yer/{kol}/durum", data={"next": f"/harita/sehir/{roma['id']}"})
    assert one("SELECT status FROM places WHERE id = ?", (kol,))["status"] == "visited"
    # Başkası erişemez
    assert AYSE_C.get(f"/harita/yer/{enzo['id']}").status_code == 404
    for url in (f"/harita/yer/{enzo['id']}", f"/harita/yer/{enzo['id']}/sil", f"/harita/yer/{enzo['id']}/durum"):
        assert AYSE_C.post(url, data={"name": "X"}).status_code == 404
    assert one("SELECT name, status FROM places WHERE id = ?", (enzo["id"],))["name"] == "Da Enzo"
    print("  places OK")


def test_index():
    page = ADMIN.text("/harita/")
    assert "leaflet.min.js" in page and "integrity=" in page and "places.js" in page
    data = map_data(page)
    names = {p["name"] for p in data["places"]}
    assert names == {"Da Enzo", "Kolezyum"}  # konumsuzlar haritada yok
    assert {c["name"] for c in data["cities"]} == {"Roma", "Konya"} and len(data["cities"]) == 3
    # İstatistik: gezilen şehir (Roma İtalya, Konya), ülke, yer
    assert re.search(r'Şehir</div><div class="value">2</div>', page) and "+1 gidilecek" in page
    assert re.search(r'Ülke</div><div class="value">2</div>', page)
    assert re.search(r'Yer</div><div class="value">4</div>', page)
    assert "Sille" not in page  # Konya'ya bağlı ve konumsuz: ne haritada ne bağımsız yerler listesinde
    assert '<span class="pill-count">2 yer</span>' in page  # Roma (İtalya)
    # Filtreler
    data = map_data(ADMIN.text("/harita/?kategori=food"))
    assert [p["name"] for p in data["places"]] == ["Da Enzo"]
    page = ADMIN.text("/harita/?durum=wish")
    assert {c["country"] for c in map_data(page)["cities"]} == {"Avustralya"} and map_data(page)["places"] == []
    # Başkasının haritasında bu kullanıcının verisi yok
    page = AYSE_C.text("/harita/")
    data = map_data(page)
    assert data["places"] == [] and data["cities"] == [] and data["parking"] is None
    assert "Da Enzo" not in page and "İtalya" not in page and "Ayşe şehri" in page
    # Ad içindeki HTML gömülü JSON'u bozamaz
    ADMIN.post("/harita/yer/yeni", data={"name": "</script><b>x", "maplink": "1.5, 2.5"})
    page = ADMIN.text("/harita/")
    assert "</script><b>x" not in page and "</script><b>x" in {p["name"] for p in map_data(page)["places"]}
    print("  index OK")


def test_parking():
    assert "🅿️ Park ettim" in ADMIN.text("/harita/")
    r = ADMIN.post("/harita/park", data={"lat": "", "lon": ""}, follow_redirects=True)
    assert "Konum alınamadı" in r.get_data(as_text=True) and one("SELECT 1 x FROM parking") is None
    ADMIN.post("/harita/park", data={"lat": "37.871234", "lon": "32.484321", "note": "B2 kat, 14 numara"})
    p = one("SELECT * FROM parking WHERE user_id = 1")
    assert (p["lat"], p["lon"], p["note"]) == (37.871234, 32.484321, "B2 kat, 14 numara")
    page = ADMIN.text("/harita/")
    assert "<h2>🚗 Arabam nerede?</h2>" in page and "B2 kat, 14 numara" in page and "az önce" in page
    assert "✅ Arabayı aldım" in page
    assert "destination=37.871234,32.484321" in page and map_data(page)["parking"]["note"] == "B2 kat, 14 numara"
    # Üzerine yazar (kullanıcı başına tek kayıt)
    ADMIN.post("/harita/park", data={"lat": "37.9", "lon": "32.5"})
    assert one("SELECT COUNT(*) AS n FROM parking")["n"] == 1
    assert tuple(one("SELECT lat, note FROM parking WHERE user_id = 1")) == (37.9, "")
    assert map_data(AYSE_C.text("/harita/"))["parking"] is None
    AYSE_C.post("/harita/park/sil")  # Ayşe kendi (olmayan) kaydını siler; Admin'inki durur
    assert one("SELECT 1 x FROM parking WHERE user_id = 1")
    ADMIN.post("/harita/park/sil")
    assert one("SELECT 1 x FROM parking WHERE user_id = 1") is None
    page = ADMIN.text("/harita/")
    assert "<h2>🚗 Arabam nerede?</h2>" not in page and "<h2>🅿️ Park yeri</h2>" in page
    print("  parking OK")


def test_telegram():
    konya = one("SELECT * FROM cities WHERE name = 'Konya'")
    send({"location": {"latitude": 37.875, "longitude": 32.49}})
    msg = calls("sendMessage")[-1]
    assert "Bu konumu ne yapayım?" in msg["text"] and buttons(msg) == ["loc:park", "loc:place", "loc:x"]
    press("loc:park")
    assert tuple(one("SELECT lat, lon FROM parking WHERE user_id = 1")) == (37.875, 32.49)
    edit = calls("editMessageText")[-1]
    assert "Park yeri kaydedildi" in edit["text"] and "destination=37.875,32.49" in edit["text"] and "/harita/" in edit["text"]
    # Yer olarak: Konya'ya 30 km içinde -> şehre bağlanır
    send({"location": {"latitude": 37.9, "longitude": 32.4}})
    press("loc:place")
    place = one("SELECT * FROM places WHERE name = 'Telegram konumu 05.10 14:30'")
    assert (place["category"], place["lat"], place["lon"], place["city_id"]) == ("other", 37.9, 32.4, konya["id"])
    edit = calls("editMessageText")[-1]
    assert "Yer olarak kaydedildi" in edit["text"] and f"/harita/yer/{place['id']}" in edit["text"] and "(Konya)" in edit["text"]
    # Uzak konum şehre bağlanmaz; Telegram mekânı adıyla gelir
    send({"location": {"latitude": 10.0, "longitude": 10.0}, "venue": {
        "location": {"latitude": 10.0, "longitude": 10.0}, "title": "Çınaraltı", "address": "Köy meydanı"}})
    assert "<b>Çınaraltı</b>" in calls("sendMessage")[-1]["text"]
    press("loc:place")
    v = one("SELECT * FROM places WHERE name = 'Çınaraltı'")
    assert v["city_id"] is None and v["address"] == "Köy meydanı"
    # Vazgeç ve süresi dolmuş buton
    send({"location": {"latitude": 1, "longitude": 1}})
    press("loc:x")
    assert "Vazgeçildi" in calls("editMessageText")[-1]["text"]
    press("loc:park")
    assert "Süre doldu" in calls("editMessageText")[-1]["text"]
    assert one("SELECT lat FROM parking WHERE user_id = 1")["lat"] == 37.875
    # Bağlı olmayan sohbet
    send({"location": {"latitude": 1, "longitude": 1}}, chat=555)
    assert "bağlı değil" in calls("sendMessage")[-1]["text"]
    # Yardımda anlatılıyor
    send({"text": "/yardim"})
    assert "Konum gönder" in calls("sendMessage")[-1]["text"]
    print("  telegram OK")


def test_search():
    page = ADMIN.text("/ara/?q=cacio")
    assert "Da Enzo" in page and "🗺️" in page
    page = ADMIN.text("/ara/?q=avustralya")
    assert "🏙️ Roma, Avustralya" in page
    assert "Da Enzo" in ADMIN.text("/ara/?q=roma enzo")  # şehir adıyla birlikte
    page = ADMIN.text("/ara/?q=etliekmek")  # şehir notu
    assert "🏙️ Konya, Türkiye" in page and "Etliekmek" in page
    assert "Da Enzo" not in AYSE_C.text("/ara/?q=cacio")
    print("  search OK")


def test_trash():
    roma = one("SELECT * FROM cities WHERE country = 'İtalya'")
    ids = sorted(r["id"] for r in rows("SELECT id FROM places WHERE city_id = ?", (roma["id"],)))
    assert len(ids) == 2
    r = ADMIN.post(f"/harita/sehir/{roma['id']}/sil", follow_redirects=True)
    assert "Roma ve 2 yer çöp kutusuna taşındı" in r.get_data(as_text=True)
    assert one("SELECT 1 x FROM cities WHERE id = ?", (roma["id"],)) is None
    assert one("SELECT COUNT(*) AS n FROM places WHERE city_id = ?", (roma["id"],))["n"] == 0
    item = one("SELECT * FROM trash WHERE module = 'places' ORDER BY id DESC")
    assert item["label"] == "🏙️ Roma (2 yer)"
    assert AYSE_C.post(f"/cop-kutusu/{item['id']}/geri").status_code == 302
    assert one("SELECT 1 x FROM cities WHERE id = ?", (roma["id"],)) is None  # başkası geri getiremez
    ADMIN.post(f"/cop-kutusu/{item['id']}/geri")
    assert one("SELECT name FROM cities WHERE id = ?", (roma["id"],))["name"] == "Roma"
    assert sorted(r["id"] for r in rows("SELECT id FROM places WHERE city_id = ?", (roma["id"],))) == ids
    # Tek yer silme ve geri getirme
    enzo = one("SELECT * FROM places WHERE name = 'Da Enzo'")
    ADMIN.post(f"/harita/yer/{enzo['id']}/sil")
    assert one("SELECT 1 x FROM places WHERE id = ?", (enzo["id"],)) is None
    item = one("SELECT * FROM trash WHERE module = 'places' ORDER BY id DESC")
    assert item["label"] == "🍽️ Da Enzo"
    ADMIN.post(f"/cop-kutusu/{item['id']}/geri")
    assert tuple(one("SELECT rating, city_id FROM places WHERE id = ?", (enzo["id"],))) == (5, roma["id"])
    print("  trash OK")


if __name__ == "__main__":
    test_geocode()
    test_links()
    test_cities()
    test_places()
    test_index()
    test_parking()
    test_telegram()
    test_search()
    test_trash()
    print("OK")
