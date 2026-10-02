"""Şifreli kasa: sunucu sadece şifreli veriyi saklar; erişim, sınırlar, parola değişimi, sıfırlama.

Şifreleme tarayıcıda (static/vault.js) yapıldığı için burada rastgele baytlarla "şifreli veri" taklit edilir.
Çalıştır: .venv/Scripts/python tests/test_vault.py
"""
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

app = make_app()

from pano.auth import create_user  # noqa: E402
from pano.db import query_one  # noqa: E402

with app.app_context():
    create_user("ayse", "ayse12345")
ADMIN = Client(app)
AYSE = Client(app, "ayse", "ayse12345")


def blob(n):
    return base64.b64encode(os.urandom(n)).decode()


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


KEY = {"salt": blob(16), "iterations": "600000", "wrapped_key": blob(60), "hint": "ilk okulum"}


def test_setup():
    page = ADMIN.text("/kasa/")
    assert 'id="v-setup"' in page and '<script type="application/json" id="vault-meta">null</script>' in page
    assert "vault.js" in page
    for bad in ({**KEY, "salt": "%%%"}, {**KEY, "iterations": "1000"}, {**KEY, "wrapped_key": "kisa"}):
        assert ADMIN.post("/kasa/kur", data=bad).status_code == 400
    r = ADMIN.post("/kasa/kur", data=KEY)
    assert r.status_code == 200 and r.json == {"ok": True}
    assert ADMIN.post("/kasa/kur", data=KEY).status_code == 409  # üzerine yazılamaz
    meta = json.loads(ADMIN.text("/kasa/").split('id="vault-meta">')[1].split("</script>")[0])
    assert meta == {"salt": KEY["salt"], "iterations": 600000, "wrapped_key": KEY["wrapped_key"], "hint": "ilk okulum"}
    print("  setup OK")


def test_items():
    assert AYSE.post("/kasa/kayit", data={"data": blob(40)}).status_code == 409  # kasası yok
    d1 = blob(80)
    r = ADMIN.post("/kasa/kayit", data={"data": d1})
    item = r.json["id"]
    assert r.status_code == 200 and r.json["updated_at"]
    assert d1 in ADMIN.text("/kasa/")
    assert ADMIN.post("/kasa/kayit", data={"data": "açık metin olamaz"}).status_code == 400
    assert ADMIN.post("/kasa/kayit", data={"data": blob(50_000)}).status_code == 400  # çok büyük
    # Güncelleme; başkası göremez, değiştiremez, silemez
    d2 = blob(90)
    assert ADMIN.post(f"/kasa/kayit/{item}", data={"data": d2}).status_code == 200
    assert one("SELECT data FROM vault_items WHERE id = ?", (item,))["data"] == d2
    assert d2 not in AYSE.text("/kasa/")
    assert AYSE.post(f"/kasa/kayit/{item}", data={"data": blob(40)}).status_code == 404
    assert AYSE.post(f"/kasa/kayit/{item}/sil").status_code == 404
    # Silme çöp kutusuna; geri getirilince aynı şifreli veri döner
    assert ADMIN.post(f"/kasa/kayit/{item}/sil").json == {"ok": True}
    assert one("SELECT id FROM vault_items WHERE id = ?", (item,)) is None
    tid = one("SELECT id FROM trash WHERE module = 'vault'")["id"]
    assert "Kasa kaydı (şifreli)" in ADMIN.text("/cop-kutusu/")
    ADMIN.post(f"/cop-kutusu/{tid}/geri")
    assert one("SELECT data FROM vault_items WHERE id = ?", (item,))["data"] == d2
    # Kasa içeriği genel aramaya girmez
    with app.test_request_context():
        from pano.search import search
        assert not [gr for gr in search(1, d2[:12]) if "Kasa" in gr["label"]]
    print("  items OK")


def test_change_passphrase():
    new = {"salt": blob(16), "iterations": "700000", "wrapped_key": blob(60), "hint": ""}
    r = ADMIN.post("/kasa/parola", data={**new, "password": "yanlis"})
    assert r.status_code == 403 and "Hesap şifresi hatalı" in r.json["error"]
    assert one("SELECT salt FROM vault_meta WHERE user_id = 1")["salt"] == KEY["salt"]
    assert ADMIN.post("/kasa/parola", data={**new, "password": "admin12345"}).json == {"ok": True}
    row = one("SELECT * FROM vault_meta WHERE user_id = 1")
    assert (row["salt"], row["iterations"], row["hint"]) == (new["salt"], 700000, "")
    assert AYSE.post("/kasa/parola", data={**new, "password": "ayse12345"}).status_code == 409
    print("  change passphrase OK")


def test_reset():
    ADMIN.post("/kasa/kayit", data={"data": blob(40)})
    item = one("SELECT id FROM vault_items ORDER BY id DESC")["id"]
    ADMIN.post(f"/kasa/kayit/{item}/sil")
    before = one("SELECT COUNT(*) AS n FROM vault_items")["n"]
    ADMIN.post("/kasa/sifirla", data={"password": "yanlis", "confirm": "SİL"})
    ADMIN.post("/kasa/sifirla", data={"password": "admin12345", "confirm": "evet"})
    assert one("SELECT COUNT(*) AS n FROM vault_items")["n"] == before and one("SELECT 1 x FROM vault_meta")
    r = ADMIN.post("/kasa/sifirla", data={"password": "admin12345", "confirm": "sil"}, follow_redirects=True)
    assert "Kasa sıfırlandı" in r.get_data(as_text=True)
    assert one("SELECT COUNT(*) AS n FROM vault_items WHERE user_id = 1")["n"] == 0
    assert one("SELECT 1 x FROM vault_meta WHERE user_id = 1") is None
    assert one("SELECT COUNT(*) AS n FROM trash WHERE module = 'vault'")["n"] == 0
    assert ADMIN.post("/kasa/kur", data=KEY).status_code == 200  # yeniden kurulabilir
    print("  reset OK")


def test_csrf_and_login():
    raw = app.test_client()
    assert raw.get("/kasa/").status_code == 302
    assert raw.post("/kasa/kayit", data={"data": blob(40)}).status_code == 400  # girişsiz ve anahtarsız: CSRF reddeder
    assert ADMIN.c.post("/kasa/kayit", data={"data": blob(40)}).status_code == 400  # CSRF anahtarı yok
    print("  csrf/login OK")


if __name__ == "__main__":
    test_setup()
    test_items()
    test_change_passphrase()
    test_reset()
    test_csrf_and_login()
    print("OK")
