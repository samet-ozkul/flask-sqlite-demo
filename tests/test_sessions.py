"""Diğer cihazlardan çıkış: oturum sürümü, şifre değişince ve yönetici sıfırlayınca oturumların kapanması.

Çalıştır: .venv/Scripts/python tests/test_sessions.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

app = make_app()

from pano.auth import create_user  # noqa: E402
from pano.db import query_one  # noqa: E402

with app.app_context():
    AYSE = create_user("ayse", "ayse12345")
    VELI = create_user("veli", "veli12345")


def logged_in(client):
    r = client.get("/")
    return r.status_code == 200


def test_logout_others():
    phone, laptop = Client(app), Client(app)
    ayse = Client(app, "ayse", "ayse12345")
    assert logged_in(phone) and logged_in(laptop)
    page = phone.text("/ayarlar/")
    assert 'id="oturumlar"' in page and "Diğer cihazlardan çıkış yap" in page
    r = phone.post("/ayarlar/oturumlar/kapat", follow_redirects=True)
    assert "Diğer tüm cihazlardaki oturumlar kapatıldı" in r.get_data(as_text=True)
    # Bu cihaz açık kalır; diğeri giriş sayfasına düşer; başka kullanıcı etkilenmez
    assert logged_in(phone) and logged_in(ayse)
    r = laptop.get("/")
    assert r.status_code == 302 and "/giris" in r.headers["Location"]
    assert laptop.get("/").status_code == 302  # oturum temizlendi, tekrar denese de kapalı
    # Yeniden giriş yapılabilir
    laptop = Client(app)
    assert logged_in(laptop)
    # Kapalı oturumla form gönderilemez (CSRF anahtarı da gitti)
    old = Client(app)
    phone.post("/ayarlar/oturumlar/kapat")
    assert old.post("/notlar/yeni", data={"title": "x", "content": "y"}).status_code == 400
    assert query_one_app("SELECT COUNT(*) AS n FROM notes")["n"] == 0
    print("  logout others OK")


def test_password_change():
    phone, laptop = Client(app, "ayse", "ayse12345"), Client(app, "ayse", "ayse12345")
    r = phone.post("/ayarlar/sifre", data={"current": "ayse12345", "new": "yeni-sifre-1", "confirm": "yeni-sifre-1"},
                   follow_redirects=True)
    assert "diğer cihazlardaki oturumlar kapatıldı" in r.get_data(as_text=True)
    assert logged_in(phone) and not logged_in(laptop)
    assert logged_in(Client(app, "ayse", "yeni-sifre-1"))
    # Hatalı mevcut şifrede hiçbir oturum kapanmaz
    other = Client(app, "ayse", "yeni-sifre-1")
    phone.post("/ayarlar/sifre", data={"current": "yanlis", "new": "xxxxxxxx", "confirm": "xxxxxxxx"})
    assert logged_in(other)
    print("  password change OK")


def test_admin_reset():
    veli = Client(app, "veli", "veli12345")
    admin = Client(app)
    admin.post(f"/yonetim/kullanicilar/{VELI}/sifre", data={"password": "sifirlandi1"})
    assert not logged_in(veli) and logged_in(admin)
    assert logged_in(Client(app, "veli", "sifirlandi1"))
    print("  admin reset OK")


def test_old_cookie_without_epoch():
    # Bu özellikten önce açılmış oturum (çerezde sürüm yok) hiç kapatılmadıysa açık kalır
    with app.app_context():
        uid = create_user("eski", "eski12345")
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = uid
    assert c.get("/").status_code == 200
    Client(app, "eski", "eski12345").post("/ayarlar/oturumlar/kapat")
    assert c.get("/").status_code == 302
    print("  old cookie OK")


def query_one_app(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


if __name__ == "__main__":
    test_logout_others()
    test_password_change()
    test_admin_reset()
    test_old_cookie_without_epoch()
    print("OK")
