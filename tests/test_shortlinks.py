"""Kısa Link & QR: kod üretimi/doğrulama, hedef doğrulama (döngü dahil), /k/ yönlendirme (sayaç, başlıklar, 404'ler),
önizleme, erişim, kod değiştirme, çöp kutusu, QR PNG, Wi-Fi QR içeriği ve kartı, serbest QR, /kisalt, arama.

Çalıştır: .venv/Scripts/python tests/test_shortlinks.py
"""
import io
import json
import os
import re
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

from PIL import Image  # noqa: E402

app = make_app()
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.telegram as tg  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query, query_one  # noqa: E402
from pano.modules import shortlinks as sl  # noqa: E402
from pano.utils import today  # noqa: E402

CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {})) or {"message_id": 1}

with app.app_context():
    AYSE_ID = create_user("ayse", "ayse12345")
    execute("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
with app.test_request_context():
    SECRET = tg.webhook_secret()
ADMIN = Client(app)
AYSE = Client(app, "ayse", "ayse12345")
RAW = app.test_client()
AUTO_RE = re.compile(r"^[abcdefghjkmnpqrstuvwxyz23456789]{6}$")


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def rows(sql, args=()):
    with app.app_context():
        return query(sql, args)


def run(sql, args=()):
    with app.app_context():
        execute(sql, args)


def make(client, target, code="", title="", expires_at=""):
    return client.post("/kisa-link/yeni", data={"target": target, "code": code, "title": title, "expires_at": expires_at})


def link_by_code(code):
    return one("SELECT * FROM short_links WHERE code = ?", (code,))


def count():
    return one("SELECT COUNT(*) AS n FROM short_links")["n"]


def text(resp):
    return resp.get_data(as_text=True)


def test_create():
    assert RAW.get("/kisa-link/").status_code == 302   # yönetim girişsiz açılmaz
    assert "Henüz kısa link yok" in ADMIN.text("/kisa-link/")
    # Otomatik kod: 6 karakter, karışan harf/rakam (0 o 1 l i) yok
    r = make(ADMIN, "https://ornek.com/cok/uzun/bir/adres?utm=1", title="Kampanya afişi")
    assert r.status_code == 302
    auto = one("SELECT * FROM short_links ORDER BY id DESC LIMIT 1")
    assert AUTO_RE.match(auto["code"]) and auto["user_id"] == 1 and auto["title"] == "Kampanya afişi"
    assert auto["target"] == "https://ornek.com/cok/uzun/bir/adres?utm=1" and auto["clicks"] == 0 and auto["active"] == 1
    with app.test_request_context():
        codes = [sl.random_code() for _ in range(300)]
    assert all(AUTO_RE.match(c) for c in codes) and not any(ch in "".join(codes) for ch in "0o1li")
    # Elle kod; Türkçe harfler ve büyük harf dönüştürülür; şemasız adrese https:// eklenir
    assert make(ADMIN, "ornek.com/yaz", code="  ŞULE-Çİçek ").status_code == 302
    row = link_by_code("sule-cicek")
    assert row and row["target"] == "https://ornek.com/yaz"
    assert make(ADMIN, "http://ornek.com/a", code="Ab").status_code == 302 and link_by_code("ab")   # 2 karakter olur
    page = ADMIN.text("/kisa-link/")
    assert "http://localhost/k/sule-cicek" in page and f"http://localhost/k/{auto['code']}" in page
    assert 'data-copy="sl-url-' in page and "🎯 ornek.com" in page and "0 tıklanma" in page
    assert '<span class="pill-count">3</span>' in page
    print("  create OK")


def test_validation():
    n = count()
    bad_targets = ["", "javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,<script>x</script>",
                   "vbscript:msgbox(1)", "https://", "ftp://ornek.com/x", "mailto:a@ornek.com", "tel:+905321234567",
                   "java\tscript:alert(1)", "https://ornek.com/a b"]
    for bad in bad_targets:
        r = make(ADMIN, bad)
        assert r.status_code == 400 and ("geçersiz" in text(r) or "gerekli" in text(r)), bad
    r = make(ADMIN, "https://ornek.com/" + "x" * 2000)
    assert r.status_code == 400 and "en fazla 2000" in text(r)
    # Kendi /k/ adresimize işaret eden link: yönlendirme döngüsü
    for loop in ("http://localhost/k/sule-cicek", "HTTPS://LOCALHOST//K/./sule-cicek", "http://localhost/%6B/abc",
                 "http://localhost:80/k/x/onizle", "http://localhost/a/../k/abc", "http://localhost/k"):
        r = make(ADMIN, loop)
        assert r.status_code == 400 and "döngüsü" in text(r), loop
    assert make(ADMIN, "http://localhost/p/profil").status_code == 302   # sitenin başka sayfası olur
    run("DELETE FROM short_links WHERE target = 'http://localhost/p/profil'")
    # Kod biçimi
    for bad in ("a", "x" * 41, "-ab", "ab-", "a_b", "a.b", "a/b"):
        r = make(ADMIN, "https://ornek.com", code=bad)
        assert r.status_code == 400 and "küçük harf (a-z)" in text(r), bad
    r = make(ADMIN, "https://ornek.com", code="Yaz Kampanyası!")
    assert r.status_code == 400 and "Öneri: “yaz-kampanyasi”" in text(r)
    assert 'value="Yaz Kampanyası!"' in text(r) and 'value="https://ornek.com"' in text(r)   # yazılanlar kaybolmaz
    # Ayrılmış kelimeler (profil + bu modül)
    for reserved in ("admin", "api", "static", "giris", "kisa-link", "onizle", "Kisalt"):
        r = make(ADMIN, "https://ornek.com", code=reserved)
        assert r.status_code == 400 and "ayrılmış" in text(r), reserved
    assert "Öneri: “admin-2”" in text(make(ADMIN, "https://ornek.com", code="admin"))
    # Kodlar tüm kullanıcılar arasında tekil
    r = make(AYSE, "https://ayse.com", code="SULE-cicek")
    assert r.status_code == 400 and "başka biri tarafından alınmış" in text(r) and "Öneri: “sule-cicek-2”" in text(r)
    r = make(ADMIN, "https://ornek.com/b", code="sule-cicek")
    assert r.status_code == 400 and "başka bir linkinde" in text(r)
    # Geçmiş son kullanma tarihi
    r = make(ADMIN, "https://ornek.com", expires_at=(today() - timedelta(days=1)).isoformat())
    assert r.status_code == 400 and "geçmişte olamaz" in text(r)
    assert count() == n
    print("  validation OK")


def test_redirect():
    link = link_by_code("sule-cicek")
    r = RAW.get("/k/sule-cicek")
    assert r.status_code == 302 and r.headers["Location"] == "https://ornek.com/yaz"
    assert r.headers["Cache-Control"] == "no-store" and r.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert r.headers["Referrer-Policy"] == "no-referrer" and "Set-Cookie" not in r.headers
    row = link_by_code("sule-cicek")
    assert row["clicks"] == 1 and row["last_click_at"] and row["updated_at"] == link["updated_at"]
    # Büyük harf, Türkçe harf: aynı link
    assert RAW.get("/k/SULE-CICEK").status_code == 302 and RAW.get("/k/Şule-Çİçek").status_code == 302
    assert link_by_code("sule-cicek")["clicks"] == 3
    # HEAD sayılmaz
    assert RAW.head("/k/sule-cicek").status_code == 302 and link_by_code("sule-cicek")["clicks"] == 3
    # Link önizlemesi (WhatsApp, Telegram...) yönlendirilir ama sayılmaz
    for ua in ("WhatsApp/2.23.20.0 A", "TelegramBot (like TwitterBot)"):
        assert RAW.get("/k/sule-cicek", headers={"User-Agent": ua}).status_code == 302
    assert link_by_code("sule-cicek")["clicks"] == 3
    # Girişli istemci de yönlenir
    assert ADMIN.get("/k/sule-cicek").status_code == 302 and link_by_code("sule-cicek")["clicks"] == 4
    page = ADMIN.text("/kisa-link/")
    assert "4 tıklanma" in page and "son " in page
    # Türkçe karakterli hedef Location'da kodlanır
    make(ADMIN, "https://örnek.com/şehir?q=ç", code="turkce")
    loc = RAW.get("/k/turkce").headers["Location"]
    assert loc.startswith("https://xn--") and "%C5%9F" in loc
    print("  redirect OK")


def test_not_found():
    run("UPDATE short_links SET clicks = 0 WHERE code = 'sule-cicek'")
    missing = RAW.get("/k/olmayan-kod")
    ADMIN.post(f"/kisa-link/{link_by_code('sule-cicek')['id']}/durum")   # pasif
    assert link_by_code("sule-cicek")["active"] == 0
    inactive = RAW.get("/k/sule-cicek")
    assert missing.status_code == inactive.status_code == 404 and missing.get_data() == inactive.get_data()
    assert inactive.headers["Cache-Control"] == "no-store" and inactive.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert ADMIN.get("/k/sule-cicek").status_code == 404   # sahibi için de kapalı
    assert "Pasif" in ADMIN.text("/kisa-link/")
    ADMIN.post(f"/kisa-link/{link_by_code('sule-cicek')['id']}/durum")   # yeniden aç
    # Süresi dolmuş: dün bitti -> 404; bugün bitiyorsa hâlâ açılır
    run("UPDATE short_links SET expires_at = ? WHERE code = 'sule-cicek'", ((today() - timedelta(days=1)).isoformat(),))
    expired = RAW.get("/k/sule-cicek")
    assert expired.status_code == 404 and expired.get_data() == missing.get_data()
    assert "Süresi doldu" in ADMIN.text("/kisa-link/")
    assert link_by_code("sule-cicek")["clicks"] == 0
    run("UPDATE short_links SET expires_at = ? WHERE code = 'sule-cicek'", (today().isoformat(),))
    assert RAW.get("/k/sule-cicek").status_code == 302 and link_by_code("sule-cicek")["clicks"] == 1
    run("UPDATE short_links SET expires_at = NULL WHERE code = 'sule-cicek'")
    print("  not found OK")


def test_preview():
    before = link_by_code("sule-cicek")["clicks"]
    r = RAW.get("/k/SULE-cicek/onizle")
    page = text(r)
    assert r.status_code == 200 and "Bu kısa link şuraya gidiyor" in page and "https://ornek.com/yaz" in page
    assert '<a class="go" href="https://ornek.com/yaz" rel="noopener noreferrer nofollow">Devam et →</a>' in page
    assert "<script" not in page and "_csrf" not in page and "topbar" not in page
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "form-action 'none'" in csp and "frame-ancestors 'none'" in csp
    assert r.headers["X-Robots-Tag"] == "noindex, nofollow" and r.headers["Referrer-Policy"] == "no-referrer"
    assert link_by_code("sule-cicek")["clicks"] == before   # önizleme sayılmaz
    assert RAW.get("/k/olmayan/onizle").status_code == 404
    # Hedefteki HTML kaçışlanır
    make(ADMIN, 'https://ornek.com/?q="><b>x</b>', code="kacis")
    page = text(RAW.get("/k/kacis/onizle"))
    assert "<b>x</b>" not in page and "&lt;b&gt;x&lt;/b&gt;" in page
    print("  preview OK")


def test_access():
    link = link_by_code("sule-cicek")
    lid = link["id"]
    assert "sule-cicek" not in AYSE.text("/kisa-link/")
    assert AYSE.get(f"/kisa-link/{lid}").status_code == 404
    assert AYSE.get(f"/kisa-link/{lid}/qr.png").status_code == 404
    assert AYSE.post(f"/kisa-link/{lid}", data={"target": "https://kotu.com", "code": "sule-cicek",
                                                 "active": "1"}).status_code == 404
    assert AYSE.post(f"/kisa-link/{lid}/durum").status_code == 404
    assert AYSE.post(f"/kisa-link/{lid}/sil").status_code == 404
    after = link_by_code("sule-cicek")
    assert (after["target"], after["active"]) == (link["target"], link["active"])
    assert not one("SELECT 1 FROM trash WHERE module = 'shortlinks'")
    assert RAW.get(f"/kisa-link/{lid}").status_code == 302
    print("  access OK")


def test_edit():
    lid = link_by_code("sule-cicek")["id"]
    page = ADMIN.text(f"/kisa-link/{lid}")
    assert "http://localhost/k/sule-cicek" in page and "eski kısa adres ve basılmış QR&#39;lar çalışmaz" in page
    assert "Tıklanma" in page and 'class="qr"' in page
    clicks = link_by_code("sule-cicek")["clicks"]
    data = {"target": "ornek.com/yeni", "code": "Yeni-Kod", "title": "Yeni başlık", "expires_at": "", "active": "1"}
    r = ADMIN.post(f"/kisa-link/{lid}", data=data, follow_redirects=True)
    assert "eski kısa adres (http://localhost/k/sule-cicek)" in text(r) and "kaydedildi" in text(r)
    row = one("SELECT * FROM short_links WHERE id = ?", (lid,))
    assert (row["code"], row["target"], row["title"], row["clicks"]) == ("yeni-kod", "https://ornek.com/yeni",
                                                                         "Yeni başlık", clicks)
    assert RAW.get("/k/sule-cicek").status_code == 404 and RAW.get("/k/yeni-kod").status_code == 302
    # Boş kod: değişmez; aynı kod: uyarı yok
    r = ADMIN.post(f"/kisa-link/{lid}", data={**data, "code": ""}, follow_redirects=True)
    assert one("SELECT code FROM short_links WHERE id = ?", (lid,))["code"] == "yeni-kod" and "Kod değişti" not in text(r)
    # Başkasının kodu, döngü ve geçersiz hedef reddedilir; yazılanlar formda kalır
    make(AYSE, "https://ayse.com", code="ayse-kod")
    r = ADMIN.post(f"/kisa-link/{lid}", data={**data, "code": "ayse-kod"})
    assert r.status_code == 400 and "başka biri tarafından alınmış" in text(r) and 'value="ayse-kod"' in text(r)
    r = ADMIN.post(f"/kisa-link/{lid}", data={**data, "target": "http://localhost/k/ayse-kod"})
    assert r.status_code == 400 and "döngüsü" in text(r)
    assert one("SELECT code FROM short_links WHERE id = ?", (lid,))["code"] == "yeni-kod"
    # Pasife alma formdan; geçmiş tarih yeni girilirse hata
    ADMIN.post(f"/kisa-link/{lid}", data={**data, "active": ""})
    assert one("SELECT active FROM short_links WHERE id = ?", (lid,))["active"] == 0
    r = ADMIN.post(f"/kisa-link/{lid}", data={**data, "expires_at": "2020-01-01"})
    assert r.status_code == 400 and "geçmişte" in text(r)
    ADMIN.post(f"/kisa-link/{lid}", data=data)
    print("  edit OK")


def test_qr_png():
    lid = link_by_code("yeni-kod")["id"]
    r = ADMIN.get(f"/kisa-link/{lid}/qr.png")
    assert r.status_code == 200 and r.headers["Content-Type"] == "image/png"
    assert "attachment" in r.headers["Content-Disposition"] and "kisa-link-yeni-kod-qr.png" in r.headers["Content-Disposition"]
    img = Image.open(io.BytesIO(r.data))
    assert img.format == "PNG" and img.size[0] >= 600 and img.size[0] == img.size[1]
    r.close()
    # Sayfadaki SVG kısa adresin QR'ı
    with app.app_context():
        from pano.totp import qr_svg
        assert qr_svg("http://localhost/k/yeni-kod") in ADMIN.text(f"/kisa-link/{lid}")
    print("  qr png OK")


def test_delete_restore():
    lid = link_by_code("yeni-kod")["id"]
    r = ADMIN.post(f"/kisa-link/{lid}/sil", follow_redirects=True)
    assert "çöp kutusuna taşındı" in text(r)
    assert not one("SELECT 1 FROM short_links WHERE id = ?", (lid,)) and RAW.get("/k/yeni-kod").status_code == 404
    item = one("SELECT * FROM trash WHERE module = 'shortlinks' ORDER BY id DESC LIMIT 1")
    assert item["user_id"] == 1 and "Yeni başlık" in item["label"]
    # Kutudayken kod başkasına verilmez (geri getirilince çakışmasın)
    r = make(AYSE, "https://ayse.com", code="yeni-kod")
    assert r.status_code == 400 and "alınmış" in text(r)
    r = make(ADMIN, "https://ornek.com", code="yeni-kod")
    assert r.status_code == 400 and "çöp kutusundaki" in text(r)
    ADMIN.post(f"/cop-kutusu/{item['id']}/geri")
    row = one("SELECT * FROM short_links WHERE id = ?", (lid,))
    assert row and row["code"] == "yeni-kod" and RAW.get("/k/yeni-kod").status_code == 302
    print("  delete/restore OK")


def test_wifi_payload():
    card = {"security": "WPA", "ssid": 'Ev;Ağı:5G\\x', "password": 'p;a:s\\s,"w', "hidden": 0}
    assert sl.wifi_payload(card) == 'WIFI:T:WPA;S:Ev\\;Ağı\\:5G\\\\x;P:p\\;a\\:s\\\\s\\,\\"w;;'
    assert sl.wifi_payload({**card, "hidden": 1}).endswith(';H:true;;')
    assert sl.wifi_payload({"security": "nopass", "ssid": "Kafe", "password": "", "hidden": 0}) == "WIFI:T:nopass;S:Kafe;;"
    assert sl.wifi_payload({"security": "WEP", "ssid": "Eski", "password": "12345", "hidden": 1}) == \
        "WIFI:T:WEP;S:Eski;P:12345;H:true;;"
    print("  wifi payload OK")


def test_wifi_cards():
    assert "Henüz Wi-Fi kartı yok" in ADMIN.text("/kisa-link/?tab=wifi")
    base = {"title": "", "ssid": "Ev:Ağı;1", "password": "gizli;sifre\\1", "security": "WPA", "is_hidden": "1"}
    for bad, msg in (({"ssid": ""}, "Ağ adı (SSID) gerekli"), ({"password": "kisa"}, "8-63 karakter"),
                     ({"ssid": "ğ" * 17}, "32 bayt"), ({"security": "WEP", "password": ""}, "WEP şifresi gerekli")):
        r = ADMIN.post("/kisa-link/wifi/yeni", data={**base, **bad})
        assert r.status_code == 400 and msg in text(r), bad
    assert not one("SELECT 1 FROM wifi_cards")
    r = ADMIN.post("/kisa-link/wifi/yeni", data=base)
    card = one("SELECT * FROM wifi_cards ORDER BY id DESC LIMIT 1")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/kisa-link/wifi/{card['id']}/kart")
    assert (card["title"], card["ssid"], card["password"], card["security"], card["hidden"]) == \
        ("Ev:Ağı;1", "Ev:Ağı;1", "gizli;sifre\\1", "WPA", 1)   # başlık boşsa ağ adı
    page = ADMIN.text(f"/kisa-link/wifi/{card['id']}/kart")
    assert "Telefon kamerasıyla okut, otomatik bağlan" in page and "gizli;sifre\\1" in page and "Ev:Ağı;1" in page
    assert "@media print" in page and 'id="wifi-print"' in page and "onclick" not in page and "shortlinks.js" in page
    assert "Gizli ağ" in page
    with app.app_context():
        from pano.totp import qr_svg
        expected = qr_svg("WIFI:T:WPA;S:Ev\\:Ağı\\;1;P:gizli\\;sifre\\\\1;H:true;;")
    assert expected in page   # karttaki QR doğru içerikle
    # Listede şifre gizli (details içinde)
    page = ADMIN.text("/kisa-link/?tab=wifi")
    assert "🔑 ••••••••" in page and '<details class="wifi-pass">' in page and "Şifreli Kasa değildir" in page
    # Açık ağ: şifre kaydedilmez, QR'da P yok
    ADMIN.post("/kisa-link/wifi/yeni", data={"title": "Kafe", "ssid": "KafeWifi", "password": "yazilsa-da", "security": "nopass"})
    open_card = one("SELECT * FROM wifi_cards WHERE title = 'Kafe'")
    assert open_card["password"] == "" and sl.wifi_payload(open_card) == "WIFI:T:nopass;S:KafeWifi;;"
    assert "yok (açık ağ)" in ADMIN.text(f"/kisa-link/wifi/{open_card['id']}/kart")
    # PNG
    r = ADMIN.get(f"/kisa-link/wifi/{card['id']}/qr.png")
    assert r.status_code == 200 and Image.open(io.BytesIO(r.data)).format == "PNG" and "attachment" in r.headers["Content-Disposition"]
    r.close()
    # Düzenleme; hatada form açık kalır
    r = ADMIN.post(f"/kisa-link/wifi/{card['id']}", data={**base, "password": "1"})
    assert r.status_code == 400 and "<details class=\"card no-print\" open>" in text(r)
    ADMIN.post(f"/kisa-link/wifi/{card['id']}", data={**base, "title": "Ev", "password": "yenisifre123", "is_hidden": ""})
    card = one("SELECT * FROM wifi_cards WHERE id = ?", (card["id"],))
    assert (card["title"], card["password"], card["hidden"]) == ("Ev", "yenisifre123", 0)
    # Başkası göremez, düzenleyemez, silemez
    assert AYSE.get(f"/kisa-link/wifi/{card['id']}/kart").status_code == 404
    assert AYSE.get(f"/kisa-link/wifi/{card['id']}/qr.png").status_code == 404
    assert AYSE.post(f"/kisa-link/wifi/{card['id']}", data=base).status_code == 404
    assert AYSE.post(f"/kisa-link/wifi/{card['id']}/sil").status_code == 404
    assert "yenisifre123" not in AYSE.text("/kisa-link/?tab=wifi")
    # Silme -> çöp kutusu
    r = ADMIN.post(f"/kisa-link/wifi/{open_card['id']}/sil", follow_redirects=True)
    assert "çöp kutusuna taşındı" in text(r) and not one("SELECT 1 FROM wifi_cards WHERE id = ?", (open_card["id"],))
    print("  wifi cards OK")


def test_free_qr():
    with app.app_context():
        from pano.totp import qr_svg
        expected = qr_svg("Merhaba; dünya")
    r = ADMIN.post("/kisa-link/qr", data={"kind": "text", "data": "Merhaba; dünya"})
    assert r.status_code == 200 and expected in text(r)
    r = ADMIN.post("/kisa-link/qr", data={"kind": "url", "data": "ornek.com/menu"})
    assert "https://ornek.com/menu" in text(r) and '<svg' in text(r)
    r = ADMIN.post("/kisa-link/qr", data={"kind": "tel", "data": "0532 123 45 67"})
    assert "tel:+905321234567" in text(r)
    for kind, data, msg in (("url", "javascript:alert(1)", "Web adresi geçersiz"), ("tel", "abc", "Telefon numarası geçersiz"),
                            ("text", "  ", "bir şey yaz"), ("text", "x" * 1001, "En fazla 1000")):
        r = ADMIN.post("/kisa-link/qr", data={"kind": kind, "data": data})
        assert r.status_code == 400 and msg in text(r), (kind, data)
    r = ADMIN.post("/kisa-link/qr", data={"kind": "text", "data": "😀" * 900})
    assert r.status_code == 400 and "sığmayacak" in text(r)
    r = ADMIN.post("/kisa-link/qr.png", data={"kind": "text", "data": "Satır 1\r\nSatır 2"})
    assert r.status_code == 200 and r.headers["Content-Type"] == "image/png"
    img = Image.open(io.BytesIO(r.data))
    assert img.format == "PNG" and img.size[0] <= 1100
    r.close()
    r = ADMIN.post("/kisa-link/qr.png", data={"kind": "url", "data": "javascript:1"}, follow_redirects=True)
    assert "Web adresi geçersiz" in text(r)
    assert RAW.post("/kisa-link/qr", data={"kind": "text", "data": "x"}).status_code == 400   # girişsiz, anahtarsız
    print("  free qr OK")


def send(text_, chat=100):
    r = RAW.post("/telegram/webhook", data=json.dumps({"update_id": len(CALLS) + 1000, "message": {
        "message_id": 1, "chat": {"id": chat}, "text": text_}}), content_type="application/json",
        headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    assert r.status_code == 200
    return [p for m, p in CALLS if m == "sendMessage"][-1]["text"]


def test_bot():
    from pano.bot_commands import COMMANDS, HELP
    assert "kisalt" in dict(COMMANDS) and "/kisalt" in HELP
    msg = send("/kisalt https://ornek.com/telegram/uzun-adres")
    row = one("SELECT * FROM short_links ORDER BY id DESC LIMIT 1")
    assert row["user_id"] == 1 and row["target"] == "https://ornek.com/telegram/uzun-adres" and AUTO_RE.match(row["code"])
    assert f"<code>http://localhost/k/{row['code']}</code>" in msg and "🎯 ornek.com" in msg
    assert f'<a href="http://localhost/kisa-link/{row["id"]}">Yönet →</a>' in msg
    msg = send("/kisalt ornek.com/kampanya  Yaz-İndirimi")
    assert link_by_code("yaz-indirimi")["target"] == "https://ornek.com/kampanya" and "/k/yaz-indirimi" in msg
    n = count()
    assert "⚠️" in send("/kisalt javascript:alert(1)") and "geçersiz" in send("/kisalt javascript:alert(1)")
    assert "başka bir linkinde" in send("/kisalt https://ornek.com yaz-indirimi")
    assert "ayrılmış" in send("/kisalt https://ornek.com admin")
    assert "döngüsü" in send("/kisalt http://localhost/k/yaz-indirimi")
    assert "Örnek" in send("/kisalt")
    assert count() == n
    print("  bot OK")


def test_search():
    from pano.search import search
    with app.test_request_context():
        groups = {g["label"]: g for g in search(1, "KAMPANYA")}
        assert "Kısa Link" in groups
        urls = {r["url"] for r in groups["Kısa Link"]["results"]}
        assert f"/kisa-link/{link_by_code('yaz-indirimi')['id']}" in urls
        assert any("/k/yaz-indirimi → https://ornek.com/kampanya" in r["detail"] for r in groups["Kısa Link"]["results"])
        assert "Kısa Link" in {g["label"] for g in search(1, "yeni-kod")}   # koddan da bulunur
        assert "Kısa Link" not in {g["label"] for g in search(AYSE_ID, "kampanya")}   # başkasınınki çıkmaz
    print("  search OK")


def test_user_delete():
    code = rows("SELECT code FROM short_links WHERE user_id = ?", (AYSE_ID,))[0]["code"]
    assert RAW.get(f"/k/{code}").status_code == 302
    ADMIN.post(f"/yonetim/kullanicilar/{AYSE_ID}/sil")
    assert not rows("SELECT 1 FROM short_links WHERE user_id = ?", (AYSE_ID,))
    assert RAW.get(f"/k/{code}").status_code == 404 and RAW.get("/k/yaz-indirimi").status_code == 302
    print("  user delete OK")


if __name__ == "__main__":
    test_create()
    test_validation()
    test_redirect()
    test_not_found()
    test_preview()
    test_access()
    test_edit()
    test_qr_png()
    test_delete_restore()
    test_wifi_payload()
    test_wifi_cards()
    test_free_qr()
    test_bot()
    test_search()
    test_user_delete()
    print("OK")
