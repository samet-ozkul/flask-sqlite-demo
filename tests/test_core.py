"""Çekirdek testleri: migration, giriş, ayarlar, yönetim, yedek, dosya kotası, cron, PWA.

Çalıştır: .venv/Scripts/python tests/test_core.py
"""
import io
import json
import os
import sqlite3
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, csrf, make_app  # noqa: E402

from PIL import Image  # noqa: E402


def png_bytes(w=3000, h=2000, color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def test_migration_from_demo_db():
    """İlk demo sürümünün veritabanı veri kaybı olmadan yeni şemaya geçmeli."""
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "old.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE notes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id)
            ON DELETE CASCADE, content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO users (username, password_hash, is_admin) VALUES ('eski', 'x', 1);
        INSERT INTO notes (user_id, content) VALUES (1, 'eski not');
    """)
    conn.close()
    from pano.db import SCHEMA_VERSION, connect, migrate
    conn = connect(path)
    assert migrate(conn) == SCHEMA_VERSION
    row = conn.execute("SELECT content, title, tags, pinned FROM notes").fetchone()
    assert tuple(row) == ("eski not", "", "", 0)
    assert conn.execute("SELECT username FROM users").fetchone()[0] == "eski"
    assert migrate(conn) == SCHEMA_VERSION  # ikinci çalıştırma bir şey bozmamalı
    conn.close()
    print("  migration OK")


def test_auth(app):
    c = app.test_client()
    assert c.get("/").status_code == 302  # girişsiz -> login
    assert c.get("/kayit").status_code == 404  # kayıt varsayılan kapalı
    token = csrf(c.get("/giris"))
    assert c.post("/giris", data={"username": "admin", "password": "admin12345"}).status_code == 400  # CSRF yok
    # açık yönlendirme engeli
    r = c.post("/giris?next=//evil.com", data={"_csrf": token, "username": "admin", "password": "admin12345"})
    assert r.headers["Location"] == "/", r.headers["Location"]
    # hatalı denemelerde kilit
    c2 = app.test_client()
    t2 = csrf(c2.get("/giris"))
    from pano.auth import create_user
    with app.app_context():
        create_user("kilit", "dogrusifre1")
    for _ in range(10):
        c2.post("/giris", data={"_csrf": t2, "username": "kilit", "password": "yanlis"})
    r = c2.post("/giris", data={"_csrf": t2, "username": "kilit", "password": "dogrusifre1"})
    assert r.status_code == 429, r.status_code
    # aynı IP'den başka kullanıcı hâlâ girebilir (kullanıcı kilidi IP+kullanıcı bazlı)
    Client(app)
    with app.app_context():
        from pano.db import execute
        execute("DELETE FROM login_attempts")
    print("  auth OK")


def test_settings(app):
    import pano.external as ext
    c = Client(app)
    original = ext.geocode
    ext.geocode = lambda name: {"name": "Konya", "lat": 37.87, "lon": 32.48}
    try:
        c.post("/ayarlar/profil", data={"display_name": "Samet", "city": "konya", "notify_daily": "1"})
    finally:
        ext.geocode = original
    with app.app_context():
        from pano.db import query_one
        u = query_one("SELECT * FROM users WHERE username = 'admin'")
        assert (u["display_name"], u["city"], u["lat"]) == ("Samet", "Konya", 37.87)
    h = c.text("/ayarlar/")
    assert "Samet" in h and "Telegram" in h
    # şifre değiştirme
    c.post("/ayarlar/sifre", data={"current": "yanlis", "new": "yenisifre123", "confirm": "yenisifre123"})
    c.post("/ayarlar/sifre", data={"current": "admin12345", "new": "yenisifre123", "confirm": "yenisifre123"})
    Client(app, "admin", "yenisifre123")  # yeni şifreyle giriş yapabilmeli
    c.post("/ayarlar/sifre", data={"current": "yenisifre123", "new": "admin12345", "confirm": "admin12345"})
    print("  settings OK")


def test_admin_users(app):
    c = Client(app)
    assert "Kullanıcılar" in c.text("/yonetim/kullanicilar")
    c.post("/yonetim/kullanicilar", data={"username": "ayse", "password": "ayse12345"})
    c.post("/yonetim/kullanicilar", data={"username": "x", "password": "kisa"})  # geçersiz
    ayse = Client(app, "ayse", "ayse12345")
    assert ayse.get("/yonetim/").status_code == 403
    assert ayse.get("/yonetim/yedek").status_code == 403
    with app.app_context():
        from pano.db import query_one
        uid = query_one("SELECT id FROM users WHERE username = 'ayse'")["id"]
        me = query_one("SELECT id FROM users WHERE username = 'admin'")["id"]
    c.post(f"/yonetim/kullanicilar/{uid}/sifre", data={"password": "yeniayse123"})
    Client(app, "ayse", "yeniayse123")
    c.post(f"/yonetim/kullanicilar/{me}/sil")  # kendini silemez
    c.post(f"/yonetim/kullanicilar/{uid}/sil")
    with app.app_context():
        from pano.db import query_one
        assert query_one("SELECT 1 FROM users WHERE id = ?", (me,))
        assert not query_one("SELECT 1 FROM users WHERE id = ?", (uid,))
    assert "Depolama" in c.text("/yonetim/")
    assert "Ev klasörünün" in c.text("/yonetim/?disk=1")
    print("  admin users OK")


def test_uploads_and_quota(app):
    c = Client(app)
    c.post("/notlar/yeni", data={"title": "ekli", "content": "x"})
    with app.app_context():
        from pano.db import query_one
        nid = query_one("SELECT id FROM notes WHERE title = 'ekli'")["id"]
    big = png_bytes()
    r = c.post("/dosya/yukle", data={"entity": "note", "entity_id": str(nid), "next": f"/notlar/{nid}",
                                     "file": (io.BytesIO(big), "foto.png")}, content_type="multipart/form-data")
    assert r.status_code == 302 and r.headers["Location"] == f"/notlar/{nid}"
    with app.app_context():
        from pano.db import query_one
        att = query_one("SELECT * FROM attachments WHERE entity = 'note' AND entity_id = ?", (nid,))
        assert att and att["mime"] == "image/jpeg" and att["size"] < len(big)
        with Image.open(os.path.join(app.config["UPLOAD_DIR"], att["filename"])) as img:
            assert max(img.size) == 1600, img.size
    for url in (f"/dosya/{att['id']}", f"/dosya/{att['id']}/kucuk"):
        r = c.get(url)
        assert r.status_code == 200
        r.close()  # Windows'ta açık dosya silinemez
    # başkası göremez
    from pano.auth import create_user
    with app.app_context():
        create_user("veli", "veli12345")
    veli = Client(app, "veli", "veli12345")
    assert veli.get(f"/dosya/{att['id']}").status_code == 404
    r = veli.post("/dosya/yukle", data={"entity": "note", "entity_id": str(nid), "file": (io.BytesIO(big), "a.png")},
                  content_type="multipart/form-data")
    assert r.status_code == 404
    # geçersiz dosya reddedilir
    c.post("/dosya/yukle", data={"entity": "note", "entity_id": str(nid), "file": (io.BytesIO(b"merhaba"), "a.txt")},
           content_type="multipart/form-data")
    # kota dolunca reddedilir
    old = app.config["STORAGE_QUOTA_MB"]
    app.config["STORAGE_QUOTA_MB"] = 0
    c.post("/dosya/yukle", data={"entity": "note", "entity_id": str(nid), "file": (io.BytesIO(big), "b.png")},
           content_type="multipart/form-data")
    app.config["STORAGE_QUOTA_MB"] = old
    with app.app_context():
        from pano.db import query_one
        assert query_one("SELECT COUNT(*) AS n FROM attachments WHERE entity_id = ?", (nid,))["n"] == 1
    # not silinince ek dosyaları da silinir
    path = os.path.join(app.config["UPLOAD_DIR"], att["filename"])
    assert os.path.exists(path)
    c.post(f"/notlar/{nid}/sil")
    assert not os.path.exists(path)
    print("  uploads/quota OK")


def test_backup_restore(app):
    c = Client(app)
    c.post("/notlar/yeni", data={"title": "yedekteki not", "content": "önemli"})
    with app.app_context():
        from pano.db import query_one
        nid = query_one("SELECT id FROM notes WHERE title = 'yedekteki not'")["id"]
    c.post("/dosya/yukle", data={"entity": "note", "entity_id": str(nid), "file": (io.BytesIO(png_bytes(800, 600)), "f.png")},
           content_type="multipart/form-data")
    assert "Yedek indir" in c.text("/yonetim/yedek")

    r = c.post("/yonetim/yedek/indir", data={"mode": "full"})
    assert r.status_code == 200 and r.mimetype == "application/zip"
    full = r.get_data()
    r.close()
    with zipfile.ZipFile(io.BytesIO(full)) as zf:
        names = zf.namelist()
        assert "pano.db" in names and "manifest.json" in names
        assert any(n.startswith("uploads/") for n in names)
        assert json.loads(zf.read("manifest.json"))["include_files"] is True
    r = c.post("/yonetim/yedek/indir", data={"mode": "db"})
    with zipfile.ZipFile(io.BytesIO(r.get_data())) as zf:
        assert not any(n.startswith("uploads/") for n in zf.namelist())
    r.close()

    # veriyi sil, yedekten geri yükle
    c.post(f"/notlar/{nid}/sil")
    assert "yedekteki not" not in c.text("/notlar/")
    r = c.post("/yonetim/yedek/geri-yukle", data={"confirm": "EVET", "file": (io.BytesIO(full), "yedek.zip")},
               content_type="multipart/form-data")
    assert r.status_code == 302 and "/giris" in r.headers["Location"], r.headers.get("Location")
    c = Client(app)  # oturum sıfırlandı, tekrar giriş
    assert "yedekteki not" in c.text("/notlar/")
    with app.app_context():
        from pano.db import query_one
        att = query_one("SELECT * FROM attachments WHERE entity_id = ?", (nid,))
    r = c.get(f"/dosya/{att['id']}")
    assert r.status_code == 200
    r.close()

    # onaysız ve geçersiz dosya reddedilir
    r = c.post("/yonetim/yedek/geri-yukle", data={"confirm": "", "file": (io.BytesIO(full), "yedek.zip")},
               content_type="multipart/form-data")
    assert "/yonetim/yedek" in r.headers["Location"]
    r = c.post("/yonetim/yedek/geri-yukle", data={"confirm": "EVET", "file": (io.BytesIO(b"zip degil"), "x.zip")},
               content_type="multipart/form-data")
    assert "/yonetim/yedek" in r.headers["Location"]
    assert "yedekteki not" in c.text("/notlar/")
    print("  backup/restore OK")


def test_cleanup(app):
    c = Client(app)
    orphan = os.path.join(app.config["UPLOAD_DIR"], "999", "sahipsiz.jpg")
    os.makedirs(os.path.dirname(orphan), exist_ok=True)
    with open(orphan, "wb") as f:
        f.write(b"x" * 1000)
    c.post("/yonetim/temizlik")
    assert not os.path.exists(orphan)
    print("  cleanup OK")


def test_cron(app):
    import pano.telegram as tg
    c = app.test_client()
    assert c.get("/cron/herhangi/gunluk").status_code == 404  # CRON_SECRET yok
    os.environ["CRON_SECRET"] = "gizli123"
    os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"
    sent = []
    original = tg._call
    tg._call = lambda method, params=None, files=None: sent.append((method, params, files)) or {"ok": True}
    try:
        assert c.get("/cron/yanlis/gunluk").status_code == 404
        with app.app_context():
            from pano.db import execute
            execute("UPDATE users SET telegram_chat_id = '42', notify_daily = 1 WHERE username = 'admin'")
            execute("INSERT INTO bills (user_id, name, amount, due_date) SELECT id, 'Elektrik <b>', 450, date('now')"
                    " FROM users WHERE username = 'admin'")
        r = c.get("/cron/gizli123/gunluk")
        assert r.status_code == 200 and r.json["sent"] >= 1, r.json
        msg = [p for m, p, f in sent if m == "sendMessage"][-1]["text"]
        assert "Elektrik &lt;b&gt;" in msg and "bugün" in msg, msg
        assert c.get("/cron/gizli123/gunluk").json["skipped"] >= 1  # aynı gün ikinci kez gönderilmez
        r = c.get("/cron/gizli123/yedek")
        assert r.status_code == 200 and r.json["sent"] == 1, r.json
        doc = [f for m, p, f in sent if m == "sendDocument"][-1]["document"]
        assert zipfile.ZipFile(io.BytesIO(doc[1])).namelist()[0] == "pano.db"
    finally:
        tg._call = original
        del os.environ["CRON_SECRET"]
        del os.environ["TELEGRAM_BOT_TOKEN"]
    print("  cron OK")


def test_pages(app):
    import pano.external as ext
    ext.weather = lambda lat, lon: {"temp": 17, "feels": 16, "wind": 4, "code": 1, "days": [
        {"date": "2026-09-25", "code": 1, "max": 21, "min": 10, "rain": 0},
        {"date": "2026-09-26", "code": 3, "max": 22, "min": 9, "rain": 10},
        {"date": "2026-09-27", "code": 80, "max": 22, "min": 12, "rain": 56},
        {"date": "2026-09-28", "code": 0, "max": 23, "min": 11, "rain": 0}]}
    ext.rates = lambda: {"USD": 48.85, "EUR": 55.52, "GBP": 64.6, "date": "2026-09-24"}
    c = Client(app)
    h = c.text("/")
    assert "Hava durumu" in h and "48,85" in h and "Yaklaşanlar" in h
    assert "Menü" in c.text("/menu")
    for _mod, endpoint, _t, _i, _g in __import__("pano.modules", fromlist=["MODULES"]).MODULES:
        with app.test_request_context():
            from flask import url_for
            url = url_for(endpoint)
        c.text(url)
    assert c.get("/sw.js").mimetype in ("application/javascript", "text/javascript")
    r = c.get("/static/manifest.webmanifest")
    assert r.status_code == 200 and json.loads(r.get_data())["start_url"] == "/"
    r.close()
    assert "bağlantısı yok" in c.text("/cevrimdisi")
    assert c.get("/olmayan-sayfa").status_code == 404
    print("  pages OK")


if __name__ == "__main__":
    test_migration_from_demo_db()
    app = make_app()
    test_auth(app)
    test_settings(app)
    test_admin_users(app)
    test_uploads_and_quota(app)
    test_backup_restore(app)
    test_cleanup(app)
    test_cron(app)
    if "--no-pages" not in sys.argv:
        test_pages(app)
    print("OK")
