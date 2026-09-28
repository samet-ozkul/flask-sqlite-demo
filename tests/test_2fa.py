"""İki adımlı giriş (TOTP) ve yedek kodlar.

Çalıştır: .venv/Scripts/python tests/test_2fa.py
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, csrf, make_app  # noqa: E402

app = make_app()

from pano import totp  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import query_one  # noqa: E402


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def test_rfc6238_vectors():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # ASCII "12345678901234567890"
    assert totp._code_at(secret, 59 // 30, digits=8) == "94287082"
    assert totp._code_at(secret, 1111111109 // 30, digits=8) == "07081804"
    assert totp._code_at(secret, 1234567890 // 30, digits=8) == "89005924"
    assert totp.code_now(secret, at=59) == "287082"
    # ±1 adım tolerans, tekrar kullanım reddi, bozuk girdi
    now = 1_000_000_000
    step = totp.verify(secret, totp.code_now(secret, at=now - 30), at=now)
    assert step == now // 30 - 1
    assert totp.verify(secret, totp.code_now(secret, at=now - 90), at=now) is None
    assert totp.verify(secret, totp.code_now(secret, at=now), last_step=now // 30, at=now) is None
    assert totp.verify(secret, "12 34 5", at=now) is None and totp.verify(secret, "", at=now) is None
    code = totp.code_now(secret, at=now)
    assert totp.verify(secret, f"{code[:3]} {code[3:]}", at=now) == now // 30  # boşluklu yazım
    assert "otpauth://totp/" in totp.provisioning_uri(secret, "admin") and "issuer=" in totp.provisioning_uri(secret, "admin")
    assert totp.qr_svg("x").startswith("<svg")
    print("  RFC 6238 OK")


def login_password(client, username="admin", password="admin12345", remember=True):
    tok = csrf(client.get("/giris"))
    data = {"_csrf": tok, "username": username, "password": password}
    if remember:
        data["remember"] = "1"
    return client.post("/giris", data=data)


def test_setup_and_login():
    c = Client(app)
    assert "İki adımlı girişi kur" in c.text("/ayarlar/")
    r = c.post("/ayarlar/2fa")
    assert r.status_code == 302 and r.headers["Location"].endswith("/ayarlar/2fa")
    r = c.get("/ayarlar/2fa")
    page = r.get_data(as_text=True)
    assert "<svg" in page and r.headers["Cache-Control"] == "no-store"
    with c.c.session_transaction() as sess:
        secret = sess["2fa_setup"]
    assert " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)) in page
    c.post("/ayarlar/2fa/onayla", data={"code": "000000" if totp.code_now(secret) != "000000" else "111111"})
    assert one("SELECT totp_enabled FROM users WHERE id = 1")["totp_enabled"] == 0
    r = c.post("/ayarlar/2fa/onayla", data={"code": totp.code_now(secret)})
    codes = re.findall(r"<code>([a-z0-9]{4}-[a-z0-9]{4})</code>", r.get_data(as_text=True))
    assert len(codes) == 8 and r.headers["Cache-Control"] == "no-store"
    u = one("SELECT * FROM users WHERE id = 1")
    assert u["totp_enabled"] == 1 and u["totp_secret"] == secret
    assert "8 yedek kod kaldı" in c.text("/ayarlar/")

    # Şifre doğru -> doğrulama sayfası; kod girilmeden panoya girilemez
    fresh = app.test_client()
    r = login_password(fresh)
    assert r.status_code == 302 and "/giris/dogrulama" in r.headers["Location"]
    assert "/giris" in fresh.get("/").headers["Location"]
    tok = csrf(fresh.get("/giris/dogrulama"))
    r = fresh.post("/giris/dogrulama", data={"_csrf": tok, "code": "123456" if totp.code_now(secret) != "123456" else "654321"})
    assert "hatalı" in r.get_data(as_text=True)
    # Kurulumda kullanılan adımın kodu tekrar kabul edilmez; bir sonraki adımın kodu (±1 tolerans) kabul edilir
    next_code = totp.code_now(secret, at=time.time() + 30)
    r = fresh.post("/giris/dogrulama", data={"_csrf": tok, "code": next_code})
    assert r.status_code == 302 and r.headers["Location"] == "/"
    assert "Expires=" in r.headers.get("Set-Cookie", "")  # "Beni hatırla" korunur
    assert fresh.get("/").status_code == 200
    # Aynı kodla ikinci giriş: tekrar kullanım reddedilir
    again = app.test_client()
    login_password(again)
    tok = csrf(again.get("/giris/dogrulama"))
    r = again.post("/giris/dogrulama", data={"_csrf": tok, "code": next_code})
    assert "hatalı" in r.get_data(as_text=True)

    # Yedek kod: bir kez çalışır
    rc = app.test_client()
    login_password(rc)
    tok = csrf(rc.get("/giris/dogrulama"))
    r = rc.post("/giris/dogrulama", data={"_csrf": tok, "code": codes[0].upper()})
    assert r.status_code == 302 and r.headers["Location"] == "/"
    assert "7 yedek kod kaldı" in rc.get("/", follow_redirects=True).get_data(as_text=True)
    rc2 = app.test_client()
    login_password(rc2)
    tok = csrf(rc2.get("/giris/dogrulama"))
    assert "hatalı" in rc2.post("/giris/dogrulama", data={"_csrf": tok, "code": codes[0]}).get_data(as_text=True)
    print("  setup/login/recovery OK")
    return secret, codes


def test_limits_and_expiry():
    # 5 hatalı koddan sonra kilit
    cl = app.test_client()
    login_password(cl)
    tok = csrf(cl.get("/giris/dogrulama"))
    for _ in range(5):
        cl.post("/giris/dogrulama", data={"_csrf": tok, "code": "000001"})
    assert cl.post("/giris/dogrulama", data={"_csrf": tok, "code": "000001"}).status_code == 429
    with app.app_context():
        from pano.db import execute
        execute("DELETE FROM login_attempts")
    # 5 dakikayı geçen bekleyen doğrulama
    old = app.test_client()
    login_password(old)
    with old.session_transaction() as sess:
        sess["2fa_ts"] = time.time() - 600
    r = old.get("/giris/dogrulama")
    assert r.status_code == 302 and r.headers["Location"].endswith("/giris")
    # Doğrulama sayfasına doğrudan gelmek
    assert app.test_client().get("/giris/dogrulama").status_code == 302
    print("  limits/expiry OK")


def test_manage_and_admin_reset(secret):
    c = logged_in(secret)
    c.post("/ayarlar/2fa/kodlar", data={"password": "yanlis"})
    assert one("SELECT COUNT(*) AS n FROM recovery_codes WHERE user_id = 1 AND used_at IS NULL")["n"] == 7
    r = c.post("/ayarlar/2fa/kodlar", data={"password": "admin12345"})
    assert len(re.findall(r"<code>[a-z0-9]{4}-[a-z0-9]{4}</code>", r.get_data(as_text=True))) == 8
    c.post("/ayarlar/2fa/kapat", data={"password": "yanlis"})
    assert one("SELECT totp_enabled FROM users WHERE id = 1")["totp_enabled"] == 1
    c.post("/ayarlar/2fa/kapat", data={"password": "admin12345"})
    u = one("SELECT * FROM users WHERE id = 1")
    assert u["totp_enabled"] == 0 and u["totp_secret"] is None
    assert one("SELECT COUNT(*) AS n FROM recovery_codes WHERE user_id = 1")["n"] == 0
    assert login_password(app.test_client()).headers["Location"] == "/"  # artık kod istenmez

    # Yönetici başka bir kullanıcının 2FA'sını kapatabilir
    with app.app_context():
        from pano.db import execute
        uid = create_user("ayse", "ayse12345")
        execute("UPDATE users SET totp_enabled = 1, totp_secret = ? WHERE id = ?", (totp.new_secret(), uid))
    admin = Client(app)
    assert "🔐 2FA" in admin.text("/yonetim/kullanicilar")
    admin.post(f"/yonetim/kullanicilar/{uid}/2fa-kapat")
    assert one("SELECT totp_enabled FROM users WHERE id = ?", (uid,))["totp_enabled"] == 0
    print("  manage/admin reset OK")


def logged_in(secret):
    """2FA açık hesaba giriş yapmış istemci (Client yardımcısı kod adımını bilmiyor)."""
    client = Client.__new__(Client)
    client.c = app.test_client()
    login_password(client.c)
    tok = csrf(client.c.get("/giris/dogrulama"))
    with app.app_context():
        from pano.db import execute
        execute("UPDATE users SET totp_last_step = NULL WHERE id = 1")
    client.c.post("/giris/dogrulama", data={"_csrf": tok, "code": totp.code_now(secret)})
    client.token = csrf(client.c.get("/notlar/"))
    return client


if __name__ == "__main__":
    test_rfc6238_vectors()
    secret, _codes = test_setup_and_login()
    test_limits_and_expiry()
    test_manage_and_admin_reset(secret)
    print("OK")
