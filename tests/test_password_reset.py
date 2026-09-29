"""Şifremi unuttum: Telegram'a tek kullanımlık, süreli sıfırlama bağlantısı.

Çalıştır: .venv/Scripts/python tests/test_password_reset.py
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, csrf, make_app  # noqa: E402

app = make_app()
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.telegram as tg  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query_one  # noqa: E402

CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {})) or {"message_id": 1}

with app.app_context():
    AYSE = create_user("ayse", "ayse12345")
    create_user("veli", "veli12345")  # Telegram'ı bağlı değil
    execute("UPDATE users SET telegram_chat_id = '200' WHERE id = ?", (AYSE,))


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def run(sql, args=()):
    with app.app_context():
        execute(sql, args)


def sent():
    return [(p["chat_id"], p["text"]) for m, p in CALLS if m == "sendMessage"]


class Anon:
    """Giriş yapmamış tarayıcı: formlarda CSRF anahtarını sayfadan alır."""

    def __init__(self):
        self.c = app.test_client()

    def post(self, url, data, **kw):
        token = csrf(self.c.get(url))
        return self.c.post(url, data={**data, "_csrf": token}, **kw)


def request_link(username="ayse"):
    before = len(sent())
    r = Anon().post("/sifremi-unuttum", {"username": username}, follow_redirects=True)
    assert r.status_code == 200 and "sıfırlama bağlantısı Telegram" in r.get_data(as_text=True)
    new = sent()[before:]
    if not new:
        return None
    m = re.search(r'href="(http://localhost/sifre-sifirla/[\w-]+)"', new[-1][1])
    return m.group(1).replace("http://localhost", "")


def test_request():
    assert 'href="/sifremi-unuttum"' in app.test_client().get("/giris").get_data(as_text=True)
    link = request_link()
    chat, text = sent()[-1]
    assert chat == "200" and "ayse" in text and "15 dakika" in text and link
    # Veritabanında bağlantının kendisi değil özeti durur
    token = link.rsplit("/", 1)[1]
    row = one("SELECT * FROM password_resets")
    assert row["token_hash"] != token and len(row["token_hash"]) == 64
    # Bilinmeyen kullanıcı ve Telegram'sız kullanıcı: aynı cevap, mesaj yok
    assert request_link("yok-boyle-biri") is None and request_link("veli") is None
    # Girişliyken sayfa ayarlara yönlendirir
    assert Client(app, "ayse", "ayse12345").get("/sifremi-unuttum").status_code == 302
    run("DELETE FROM login_attempts")
    run("DELETE FROM password_resets")
    print("  request OK")


def test_reset():
    other_device = Client(app, "ayse", "ayse12345")
    link = request_link()
    # Bağlantıyı açmak onu harcamaz (önizleme / ön yükleme)
    r = app.test_client().get(link)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store" and r.headers["Referrer-Policy"] == "no-referrer"
    assert "ayse" in r.get_data(as_text=True)
    browser = Anon()
    # Kısa ve eşleşmeyen şifre reddedilir; bağlantı geçerli kalır
    for data in ({"new": "kisa", "confirm": "kisa"}, {"new": "yenisifre1", "confirm": "baskasi12"}):
        assert browser.post(link, data).status_code == 200
    assert one("SELECT COUNT(*) AS n FROM password_resets")["n"] == 1
    before = len(sent())
    r = browser.post(link, {"new": "yenisifre1", "confirm": "yenisifre1"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/giris")
    assert [c for c, t in sent()[before:] if "şifresi sıfırlama bağlantısıyla değiştirildi" in t] == ["200"]
    # Eski şifre çalışmaz, yenisi çalışır; diğer cihazdaki oturum kapandı
    assert other_device.get("/").status_code == 302
    Client(app, "ayse", "yenisifre1")
    c = app.test_client()
    r = c.post("/giris", data={"_csrf": csrf(c.get("/giris")), "username": "ayse", "password": "ayse12345"})
    assert r.status_code == 200 and "hatalı" in r.get_data(as_text=True)
    # Bağlantı bir kez kullanılır
    r = app.test_client().get(link)
    assert r.status_code == 302 and "/sifremi-unuttum" in r.headers["Location"]
    print("  reset OK")


def test_expiry_and_replacement():
    run("DELETE FROM login_attempts")
    first = request_link()
    second = request_link()
    assert app.test_client().get(first).status_code == 302  # yeni istek eskisini geçersiz kılar
    assert app.test_client().get(second).status_code == 200
    run("UPDATE password_resets SET expires_at = ?", (time.time() - 1,))
    assert app.test_client().get(second).status_code == 302
    assert app.test_client().get("/sifre-sifirla/uydurma").status_code == 302
    print("  expiry OK")


def test_limits():
    run("DELETE FROM login_attempts")
    links = [request_link() for _ in range(4)]
    assert all(links[:3]) and links[3] is None  # kullanıcı başına saatte 3
    for _ in range(6):
        Anon().post("/sifremi-unuttum", {"username": "yok"})
    r = Anon().post("/sifremi-unuttum", {"username": "ayse"})
    assert r.status_code == 429  # IP başına saatte 10
    print("  limits OK")


def test_lock_cleared_and_2fa_kept():
    run("DELETE FROM login_attempts")
    # Çok hatalı denemeyle kilitlenen kullanıcı, şifreyi sıfırlayınca kilitten kurtulur
    for _ in range(10):
        c = app.test_client()
        c.post("/giris", data={"_csrf": csrf(c.get("/giris")), "username": "ayse", "password": "yanlis"})
    c = app.test_client()
    r = c.post("/giris", data={"_csrf": csrf(c.get("/giris")), "username": "ayse", "password": "yenisifre1"})
    assert r.status_code == 429
    run("DELETE FROM login_attempts WHERE key LIKE 'ip:%' OR key LIKE 'reset-%'")
    link = request_link()
    Anon().post(link, {"new": "ucuncu-sifre", "confirm": "ucuncu-sifre"})
    Client(app, "ayse", "ucuncu-sifre")
    # İki adımlı giriş açıksa sıfırlama onu atlatmaz
    run("UPDATE users SET totp_enabled = 1, totp_secret = 'JBSWY3DPEHPK3PXP' WHERE id = ?", (AYSE,))
    link = request_link()
    Anon().post(link, {"new": "dorduncu-sifre", "confirm": "dorduncu-sifre"})
    c = app.test_client()
    r = c.post("/giris", data={"_csrf": csrf(c.get("/giris")), "username": "ayse", "password": "dorduncu-sifre"})
    assert r.status_code == 302 and "/giris/dogrulama" in r.headers["Location"]
    print("  lock/2fa OK")


def test_without_telegram():
    os.environ.pop("TELEGRAM_BOT_TOKEN")
    page = app.test_client().get("/sifremi-unuttum").get_data(as_text=True)
    assert "Telegram ayarlı olmadığı" in page
    os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"
    print("  no telegram OK")


if __name__ == "__main__":
    test_request()
    test_reset()
    test_expiry_and_replacement()
    test_limits()
    test_lock_cleared_and_2fa_kept()
    test_without_telegram()
    print("OK")
