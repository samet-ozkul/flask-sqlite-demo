"""Acil durum kartı: kayıt ve doğrulama, açık/kapalı ve sahibin önizlemesi, herkese açık sayfa (sızıntı, kaçış,
başlıklar, boş alanlar), görüntülenme sayacı, bağlantı yenileme, kullanıcılar arası erişim, Sağlık'tan ilaç getirme,
Telegram bildirimi (30 dakika sınırı), duvar kâğıdı PNG, cüzdan kartı, silme → çöp kutusu → geri getirme.

Çalıştır: .venv/Scripts/python tests/test_emergency.py
"""
import io
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

from PIL import Image  # noqa: E402

app = make_app()
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.telegram as tg  # noqa: E402
import pano.todo_reminders as todo  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query_one  # noqa: E402
from pano.modules import emergency  # noqa: E402
from pano.utils import TZ, today  # noqa: E402

CALLS = []
FAIL = [False]


def fake_call(method, params=None, files=None):
    if FAIL[0]:
        raise tg.TelegramError("Bad Gateway")
    CALLS.append((method, params or {}))
    return {"message_id": 1}


tg._call = fake_call
NOW = [datetime(2026, 10, 2, 14, 30, tzinfo=TZ)]
todo.now_local = lambda: NOW[0]

with app.app_context():
    AYSE_ID = create_user("ayse", "ayse12345")
    execute("UPDATE users SET display_name = 'Şule Çiçek', telegram_chat_id = '100' WHERE id = 1")
    execute("UPDATE users SET display_name = 'Ayşe Kaya' WHERE id = ?", (AYSE_ID,))
    # Sayfadan asla sızmaması gereken veriler
    execute("INSERT INTO notes (user_id, title, content) VALUES (1, 'GIZLI-NOT', 'gizli not içeriği')")
ADMIN = Client(app)
AYSE = Client(app, "ayse", "ayse12345")
RAW = app.test_client()

EMPTY_ROWS = ["", "", "", ""]
BASE = {"enabled": "1", "full_name": "Şule Çiçek", "birth_date": "1990-03-12", "blood_type": "A-",
        "allergies": "Penisilin", "conditions": "Astım", "medications": "Ventolin 100 mcg", "organ_donor": "yes",
        "notes": "Gözlük kullanıyor", "contact_name": ["Ayşe Çiçek", *EMPTY_ROWS],
        "contact_relation": ["Annesi", *EMPTY_ROWS], "contact_phone": ["0532 123 45 67", *EMPTY_ROWS]}


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def run(sql, args=()):
    with app.app_context():
        execute(sql, args)


def save(client, **fields):
    data = {**BASE, **fields}
    if not data.get("enabled"):
        data.pop("enabled", None)   # işaretsiz kutu formda hiç gönderilmez
    return client.post("/acil-durum/", data=data)


def card(uid=1):
    return one("SELECT * FROM emergency_cards WHERE user_id = ?", (uid,))


def token(uid=1):
    return card(uid)["token"]


def text(resp):
    return resp.get_data(as_text=True)


def test_create_and_validation():
    page = ADMIN.text("/acil-durum/")
    assert 'value="Şule Çiçek"' in page and "Kartın" not in page   # ad kullanıcının adıyla gelir, kart yok
    assert "bilen <b>herkes</b> bu bilgileri girişsiz görebilir" in page
    cases = [
        ({"contact_phone": ["abc", *EMPTY_ROWS]}, "telefon numarası geçersiz"),
        ({"contact_phone": ["12 34", *EMPTY_ROWS]}, "telefon numarası geçersiz"),
        ({"contact_phone": ["+90 532 123 45 67 89 01", *EMPTY_ROWS]}, "telefon numarası geçersiz"),   # 16 hane
        ({"contact_phone": ["", *EMPTY_ROWS]}, "için telefon numarası yaz"),
        ({"contact_name": ["", *EMPTY_ROWS], "contact_relation": ["", *EMPTY_ROWS]}, "için ad ya da yakınlık yaz"),
        ({"contact_name": [f"Kişi {i}" for i in range(6)], "contact_relation": [""] * 6,
          "contact_phone": [f"0532 123 45 6{i}" for i in range(6)]}, "En fazla 5 acil kişi"),
        ({"birth_date": "2999-01-01"}, "Doğum tarihi geçersiz"),
        ({"birth_date": "1850-01-01"}, "Doğum tarihi geçersiz"),
        ({"birth_date": "bozuk"}, "Doğum tarihi geçersiz"),
    ]
    for fields, message in cases:
        r = save(ADMIN, **fields)
        assert r.status_code == 400 and message in text(r), (fields, message)
    r = save(ADMIN, contact_phone=["abc", *EMPTY_ROWS], allergies="Fıstık")
    assert 'value="abc"' in text(r) and "Fıstık</textarea>" in text(r)   # yazılanlar kaybolmaz
    assert one("SELECT COUNT(*) AS n FROM emergency_cards")["n"] == 0
    # Kayıt (kapalı), boş satır atlanır, sadece yakınlık yazılmış satır da olur
    r = save(ADMIN, enabled="", contact_name=["Ayşe Çiçek", "", "", "", ""],
             contact_relation=["Annesi", "", "Eşi", "", ""],
             contact_phone=["0532 123 45 67", "", "+90 555 000 11 22", "", ""])
    assert r.status_code == 302
    c = card()
    assert c["enabled"] == 0 and c["full_name"] == "Şule Çiçek" and c["birth_date"] == "1990-03-12"
    assert c["blood_type"] == "A-" and c["organ_donor"] == "yes" and len(c["token"]) == 22
    assert json.loads(c["contacts"]) == [{"name": "Ayşe Çiçek", "relation": "Annesi", "phone": "0532 123 45 67"},
                                         {"name": "", "relation": "Eşi", "phone": "+90 555 000 11 22"}]
    # Güncelleme: anahtar değişmez; bilinmeyen kan grubu boş sayılır; satır sonları korunur
    old = c["token"]
    r = save(ADMIN, enabled="", blood_type="Z+", allergies="Penisilin\r\nArı sokması", birth_date="")
    assert r.status_code == 302
    c = card()
    assert c["token"] == old and c["blood_type"] == "" and c["allergies"] == "Penisilin\nArı sokması"
    assert c["birth_date"] is None
    assert one("SELECT COUNT(*) AS n FROM emergency_cards")["n"] == 1
    print("  create/validation OK")


def test_disabled_and_preview():
    save(ADMIN, enabled="")
    t = token()
    missing = RAW.get("/acil/olmayan-anahtar-1234567")
    disabled = RAW.get(f"/acil/{t}")
    assert missing.status_code == disabled.status_code == 404
    assert missing.get_data() == disabled.get_data()   # kapalı ile olmayan aynı görünür
    assert RAW.get("/acil/" + "x" * 300).status_code == 404
    assert AYSE.get(f"/acil/{t}").status_code == 404   # başka kullanıcı da göremez
    page = ADMIN.text(f"/acil/{t}")   # sahibi önizler
    assert "Kart kapalı: şu an sadece sen görüyorsun" in page and "Penisilin" in page
    assert card()["views"] == 0 and card()["last_viewed_at"] is None   # önizleme sayılmaz
    admin_page = ADMIN.text("/acil-durum/")
    assert "Kapalı" in admin_page and "Kart yayında değil" in admin_page and t in admin_page
    assert '<svg' in admin_page and 'class="qr"' in admin_page
    print("  disabled/preview OK")


def test_public_page():
    save(ADMIN, allergies="Penisilin <script>alert('x')</script>\nFıstık",
         contact_name=["Ayşe <b>Çiçek</b>", "Ali", "", "", ""], contact_relation=["Annesi", "", "", "", ""],
         contact_phone=["0532 123 45 67", "+44 20 7946 0958", "", "", ""])
    run("UPDATE emergency_cards SET views = 0")
    CALLS.clear()
    r = RAW.get(f"/acil/{token()}")
    page = text(r)
    assert r.status_code == 200 and "Set-Cookie" not in r.headers   # girişsiz: oturum/CSRF çerezi yok
    age = emergency.age_of(datetime(1990, 3, 12).date(), today())
    for part in ("ACİL DURUM", "EMERGENCY", "Şule Çiçek", f"{age} yaş · Age {age} · 12.03.1990",
                 "Kan grubu · Blood type", "A Rh−", "Alerjiler · Allergies", "Kronik hastalıklar · Medical conditions",
                 "Astım", "Kullandığı ilaçlar · Medications", "Ventolin 100 mcg", "Organ bağışı · Organ donor",
                 "Evet · Yes", "Notlar · Notes", "Gözlük kullanıyor", "Acil durumda arayın · Emergency contacts",
                 "(Annesi)", 'href="tel:+905321234567"', 'href="tel:+442079460958"', "📞 Ara · Call",
                 "Son güncelleme · Last updated"):
        assert part in page, part
    assert '<div class="intl">' not in page   # A/B/AB için ayrı uluslararası yazılış yok (sadece 0 -> O)
    # Kaçış: kullanıcı metni HTML olarak çalışmaz; sayfada hiç betik yok; satır sonu korunur (pre-line)
    assert "Penisilin &lt;script&gt;alert(&#39;x&#39;)&lt;/script&gt;\nFıstık" in page and "<script" not in page
    assert "Ayşe &lt;b&gt;Çiçek&lt;/b&gt;" in page and "<b>Çiçek" not in page
    # Panoya ait hiçbir şey yok: menü, kullanıcı adı, CSRF, diğer kayıtlar
    for leak in ("GIZLI-NOT", "gizli not", "admin", "_csrf", "topbar", "bottomnav", "Çıkış", "/acil-durum", "Düzenle"):
        assert leak not in page, leak
    # Güvenlik başlıkları
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp and "form-action 'none'" in csp
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert '<meta name="robots" content="noindex, nofollow">' in page
    assert r.headers["Cache-Control"] == "no-store" and r.headers["Referrer-Policy"] == "no-referrer"
    # Sayaç: girişsiz ve başka kullanıcı sayılır; sahibi ve HEAD sayılmaz
    assert card()["views"] == 1
    other = AYSE.text(f"/acil/{token()}")
    assert "ayse" not in other and "Ayşe Kaya" not in other and "Düzenle" not in other
    assert card()["views"] == 2
    assert "Bu senin kartın · 👁️ 2 görüntülenme" in ADMIN.text(f"/acil/{token()}") and card()["views"] == 2
    RAW.head(f"/acil/{token()}")
    assert card()["views"] == 2
    # Link önizlemesi (WhatsApp, Telegram...) sayılmaz; sayfa yine açılır
    for ua in ("WhatsApp/2.23.20.0 A", "TelegramBot (like TwitterBot)", "facebookexternalhit/1.1"):
        assert RAW.get(f"/acil/{token()}", headers={"User-Agent": ua}).status_code == 200
    assert card()["views"] == 2
    admin_page = ADMIN.text("/acil-durum/")
    assert "Yayında" in admin_page and "👁️ 2 görüntülenme" in admin_page and "son 02.10.2026 14:30" in admin_page
    # Boş alanlar hiç görünmez
    save(ADMIN, full_name="", birth_date="", blood_type="", allergies="", conditions="", medications="", organ_donor="",
         notes="", contact_name=[""] * 5, contact_relation=[""] * 5, contact_phone=[""] * 5)
    page = text(RAW.get(f"/acil/{token()}"))
    for gone in ("Kan grubu", "Alerjiler", "Kronik", "İlaçlar", "Organ", "Notlar", "Ara · Call", "yaş ·", "<h1"):
        assert gone not in page, gone
    assert "Bu kartta henüz bilgi yok" in page and "ACİL DURUM" in page
    save(ADMIN, organ_donor="no", blood_type="0+")
    page = text(RAW.get(f"/acil/{token()}"))
    assert "Hayır · No" in page and "0 Rh+" in page and '<div class="intl">O+</div>' in page
    assert "henüz bilgi yok" not in page
    print("  public page OK")


def test_regenerate():
    save(ADMIN)
    old = token()
    assert RAW.get(f"/acil/{old}").status_code == 200
    page = ADMIN.text("/acil-durum/")
    assert "Bağlantıyı yenile" in page and "artık açılmaz" in page and "data-confirm" in page
    r = ADMIN.post("/acil-durum/baglanti-yenile", follow_redirects=True)
    assert r.status_code == 200 and "Eski bağlantı, QR, duvar kâğıdı ve cüzdan kartı artık açılmaz" in text(r)
    new = token()
    assert new != old and len(new) == 22
    assert RAW.get(f"/acil/{old}").status_code == 404 and RAW.get(f"/acil/{new}").status_code == 200
    assert card()["allergies"] == "Penisilin"   # bilgiler durur
    print("  regenerate OK")


def test_isolation():
    admin_token = token()
    page = AYSE.text("/acil-durum/")
    assert 'value="Ayşe Kaya"' in page and "Penisilin</textarea>" not in page and admin_token not in page
    assert "Kartın" not in page
    # Kartı olmayan kullanıcı başkasının kartını yenileyemez, silemez, indiremez
    for url in ("/acil-durum/baglanti-yenile", "/acil-durum/sil"):
        assert AYSE.post(url).status_code == 404
    for url in ("/acil-durum/duvar-kagidi.png", "/acil-durum/kart"):
        assert AYSE.get(url).status_code == 404, url
    assert token() == admin_token and card() is not None
    # Kaydedince kendi kartı oluşur; başkasınınki değişmez
    assert save(AYSE, full_name="Ayşe Kaya", allergies="Laktoz", enabled="").status_code == 302
    assert card(AYSE_ID)["allergies"] == "Laktoz" and card()["allergies"] == "Penisilin"
    assert card(AYSE_ID)["token"] != admin_token
    AYSE.post("/acil-durum/baglanti-yenile")
    assert token() == admin_token
    page = AYSE.text("/acil-durum/")
    assert "Laktoz</textarea>" in page and "Penisilin</textarea>" not in page and admin_token not in page
    assert "Penisilin" not in text(AYSE.get("/acil-durum/kart"))
    # Girişsiz yönetim sayfaları girişe yönlendirir
    for url in ("/acil-durum/", "/acil-durum/kart", "/acil-durum/duvar-kagidi.png"):
        assert RAW.get(url).status_code == 302, url
    print("  isolation OK")


def test_health_meds():
    run("INSERT INTO medications (user_id, name, dose, active) VALUES (1, 'Metformin', '500 mg', 1)")
    run("INSERT INTO medications (user_id, name, dose, active) VALUES (1, 'aspirin', '', 1)")
    run("INSERT INTO medications (user_id, name, dose, active) VALUES (1, 'Eski İlaç', '5 mg', 0)")
    run("INSERT INTO medications (user_id, name, dose, active) VALUES (?, 'AYSE-ILAC', '1 mg', 1)", (AYSE_ID,))
    page = ADMIN.text("/acil-durum/?ilaclar=1")
    assert ">aspirin\nMetformin 500 mg</textarea>" in page
    assert "Eski İlaç" not in page and "AYSE-ILAC" not in page
    assert "Sağlık&#39;tan 2 aktif ilaç getirildi" in page
    assert card()["medications"] == "Ventolin 100 mcg"   # kaydedilmez
    assert "Penisilin</textarea>" in page   # diğer alanlar kayıttaki gibi
    page = AYSE.text("/acil-durum/?ilaclar=1")
    assert ">AYSE-ILAC 1 mg</textarea>" in page and "Metformin" not in page
    run("UPDATE medications SET active = 0 WHERE user_id = ?", (AYSE_ID,))
    assert "Sağlık modülünde aktif ilaç yok" in AYSE.text("/acil-durum/?ilaclar=1")
    print("  health meds OK")


def test_contact_picks():
    run("INSERT INTO contacts (user_id, name, relation, phone) VALUES (1, 'Baba <x>', 'Aile', '0533 111 22 33')")
    run("INSERT INTO contacts (user_id, name, relation, phone) VALUES (1, 'Telefonsuz', '', '')")
    run("INSERT INTO contacts (user_id, name, relation, phone) VALUES (?, 'AYSE-KISI', '', '0555 999 88 77')",
        (AYSE_ID,))
    page = ADMIN.text("/acil-durum/")
    data = json.loads(page.split('<script type="application/json" id="em-picks">', 1)[1].split("</script>", 1)[0])
    assert data == [{"name": "Baba <x>", "relation": "Aile", "phone": "0533 111 22 33"}]
    assert "Baba <x>" not in page and "emergency.js" in page   # JSON güvenli kaçışlı
    assert "AYSE-KISI" not in page and "Telefonsuz" not in page
    print("  contact picks OK")


def test_telegram():
    save(ADMIN)
    t = token()
    run("UPDATE emergency_cards SET notified_at = NULL, views = 0")
    CALLS.clear()
    NOW[0] = datetime(2026, 10, 2, 14, 30, tzinfo=TZ)
    assert RAW.get(f"/acil/{t}").status_code == 200
    assert len(CALLS) == 1 and CALLS[0][0] == "sendMessage" and CALLS[0][1]["chat_id"] == "100"
    msg = CALLS[0][1]["text"]
    assert "🆘 <b>Acil durum kartın görüntülendi</b> (14:30)" in msg and "toplam 1 görüntülenme" in msg
    assert "/acil-durum/" in msg and "Penisilin" not in msg   # tıbbi bilgi mesaja yazılmaz
    assert card()["notified_at"] == "2026-10-02 11:30:00" and card()["last_viewed_at"] == "2026-10-02 11:30:00"
    # 30 dakika dolmadan yeni mesaj yok; sayaç yine artar
    NOW[0] += timedelta(minutes=10)
    RAW.get(f"/acil/{t}")
    NOW[0] += timedelta(minutes=19, seconds=59)
    RAW.get(f"/acil/{t}")
    assert len(CALLS) == 1 and card()["views"] == 3 and card()["last_viewed_at"] == "2026-10-02 11:59:59"
    NOW[0] = datetime(2026, 10, 2, 15, 0, tzinfo=TZ)   # tam 30 dakika
    RAW.get(f"/acil/{t}")
    assert len(CALLS) == 2 and "(15:00)" in CALLS[1][1]["text"] and "toplam 4 görüntülenme" in CALLS[1][1]["text"]
    # Sahibinin önizlemesi bildirim göndermez
    NOW[0] += timedelta(hours=1)
    ADMIN.get(f"/acil/{t}")
    assert len(CALLS) == 2
    # Telegram hatası sayfayı bozmaz
    FAIL[0] = True
    r = RAW.get(f"/acil/{t}")
    FAIL[0] = False
    assert r.status_code == 200 and "Penisilin" in text(r) and card()["views"] == 5
    # Telegram bağlı değilse mesaj yok
    run("UPDATE users SET telegram_chat_id = NULL WHERE id = 1")
    run("UPDATE emergency_cards SET notified_at = NULL")
    RAW.get(f"/acil/{t}")
    assert len(CALLS) == 2 and card()["notified_at"] is None
    assert "Telegram'ı" in ADMIN.text("/acil-durum/")
    run("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
    assert "Telegram'dan haber verilir" in ADMIN.text("/acil-durum/")
    print("  telegram OK")


def test_wallpaper():
    save(ADMIN, full_name="Şule Çiçek Gönül İşbilir Öztürk Uzunsoyadlı" * 2, allergies="Penisilin " * 40 + "\nFıstık",
         enabled="")   # uzun metin kısaltılır; kapalıyken de üretilir
    r = ADMIN.get("/acil-durum/duvar-kagidi.png")
    assert r.status_code == 200 and r.headers["Content-Type"] == "image/png"
    disposition = r.headers["Content-Disposition"]
    assert "attachment" in disposition and "acil-durum-duvar-kagidi.png" in disposition
    assert "no-store" in r.headers["Cache-Control"]
    img = Image.open(io.BytesIO(r.data))
    assert img.format == "PNG" and img.size == (1080, 2340)
    top = img.convert("RGB").crop((0, 0, 1080, int(2340 * 0.35)))
    assert len(top.getcolors()) == 1   # üst kısım kilit ekranı saati için boş
    lower = img.convert("RGB").crop((0, 1300, 1080, 2340))
    assert (255, 255, 255) in {c for _, c in lower.getcolors(maxcolors=100000)}   # QR'ın beyaz zemini
    r.close()
    # Ayrıntılar: yalnızca ad ve kişi yoksa da çalışır
    save(ADMIN, full_name="", blood_type="", allergies="", contact_name=[""] * 5, contact_relation=[""] * 5,
         contact_phone=[""] * 5, enabled="")
    r = ADMIN.get("/acil-durum/duvar-kagidi.png")
    assert Image.open(io.BytesIO(r.data)).size == (1080, 2340)
    r.close()
    page = ADMIN.text("/acil-durum/")
    assert "Kart yayında değil: QR, duvar kâğıdı ve cüzdan kartı" in page and "/acil-durum/duvar-kagidi.png" in page
    print("  wallpaper OK")


def test_wallet_card():
    save(ADMIN, enabled="", contact_name=["Ayşe Çiçek", "Ali Çiçek", "Üçüncü Kişi", "", ""],
         contact_relation=["Annesi", "Babası", "", "", ""],
         contact_phone=["0532 123 45 67", "0533 222 33 44", "0534 555 66 77", "", ""])
    page = ADMIN.text("/acil-durum/kart")
    for part in ("ACİL DURUM · EMERGENCY", "Şule Çiçek", "A Rh−", "Penisilin", "Ayşe Çiçek (Annesi)", "0532 123 45 67",
                 "Ali Çiçek (Babası)", "Tıbbi bilgiler için okutun", "Scan for medical info", "🖨️ Yazdır",
                 "@media print", "85.6mm", "54mm", "emergency.js", "Kart yayında değil"):
        assert part in page, part
    assert "Üçüncü Kişi" not in page   # ön yüzde ilk 2 kişi
    assert '<svg' in page and 'class="qr"' in page
    save(ADMIN)
    assert "Kart yayında değil" not in ADMIN.text("/acil-durum/kart")
    print("  wallet card OK")


def test_delete_restore():
    save(ADMIN)
    t = token()
    r = ADMIN.post("/acil-durum/sil", follow_redirects=True)
    assert r.status_code == 200 and "çöp kutusuna taşındı" in text(r)
    assert card() is None and RAW.get(f"/acil/{t}").status_code == 404
    assert ADMIN.get("/acil-durum/kart").status_code == 404
    item = one("SELECT * FROM trash WHERE user_id = 1 AND module = 'emergency'")
    assert item and item["label"] == "🆘 Acil durum kartı (Şule Çiçek)"
    assert card(AYSE_ID) is not None   # başkasının kartı durur
    r = ADMIN.post(f"/cop-kutusu/{item['id']}/geri", follow_redirects=True)
    assert "geri getirildi" in text(r)
    c = card()
    assert c["token"] == t and c["allergies"] == "Penisilin" and json.loads(c["contacts"])[0]["name"] == "Ayşe Çiçek"
    assert RAW.get(f"/acil/{t}").status_code == 200
    # Silindikten sonra yeni kart açıldıysa eskisi geri getirilemez ama yenisi bozulmaz
    ADMIN.post("/acil-durum/sil")
    assert save(ADMIN, allergies="Yeni kart").status_code == 302
    item = one("SELECT * FROM trash WHERE user_id = 1 AND module = 'emergency'")
    r = ADMIN.post(f"/cop-kutusu/{item['id']}/geri", follow_redirects=True)
    assert "Geri getirilemedi" in text(r) and card()["allergies"] == "Yeni kart"
    print("  delete/restore OK")


def test_user_delete():
    t = token(AYSE_ID)
    save(AYSE, full_name="Ayşe Kaya", allergies="Laktoz")
    assert RAW.get(f"/acil/{t}").status_code == 200
    ADMIN.post(f"/yonetim/kullanicilar/{AYSE_ID}/sil")
    assert card(AYSE_ID) is None and RAW.get(f"/acil/{t}").status_code == 404
    assert RAW.get(f"/acil/{token()}").status_code == 200   # diğer kart durur
    print("  user delete OK")


if __name__ == "__main__":
    test_create_and_validation()
    test_disabled_and_preview()
    test_public_page()
    test_regenerate()
    test_isolation()
    test_health_meds()
    test_contact_picks()
    test_telegram()
    test_wallpaper()
    test_wallet_card()
    test_delete_restore()
    test_user_delete()
    print("OK")
