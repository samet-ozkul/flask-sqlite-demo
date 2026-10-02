"""Kullanıcı kotaları: yöneticinin koyduğu depolama alanı ve dosya başına boyut sınırı.

Çalıştır: .venv/Scripts/python tests/test_quota.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

from PIL import Image  # noqa: E402

app = make_app()

from pano import quota  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import query_one  # noqa: E402

with app.app_context():
    AYSE = create_user("ayse", "ayse12345")
ADMIN = Client(app)
AYSE_C = Client(app, "ayse", "ayse12345")
KB = 1024


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def noise_png(side):
    """Sıkışmayan (gürültü) PNG: boyutu kabaca side² × 3 bayt."""
    buf = io.BytesIO()
    Image.frombytes("RGB", (side, side), os.urandom(side * side * 3)).save(buf, "PNG")
    return buf.getvalue()


def summary(uid):
    with app.app_context():
        return quota.summary(uid)


def set_limits(quota_mb="", upload_max_mb="", client=ADMIN):
    return client.post(f"/yonetim/kullanicilar/{AYSE}/sinirlar", data={"quota_mb": quota_mb, "upload_max_mb": upload_max_mb},
                       follow_redirects=True)


def note_with_upload(data, name="foto.png"):
    AYSE_C.post("/notlar/yeni", data={"title": "Ekli", "content": "x"})
    nid = one("SELECT id FROM notes WHERE user_id = ? ORDER BY id DESC", (AYSE,))["id"]
    r = AYSE_C.post("/dosya/yukle", data={"entity": "note", "entity_id": str(nid), "file": (io.BytesIO(data), name),
                                          "next": f"/notlar/{nid}"}, content_type="multipart/form-data", follow_redirects=True)
    return nid, r.get_data(as_text=True)


def transfer_upload(size):
    r = AYSE_C.post("/aktar/yeni", data={"files": [(io.BytesIO(os.urandom(size)), "dosya.bin")]},
                    content_type="multipart/form-data", follow_redirects=True)
    return r.get_data(as_text=True)


def test_admin_sets_limits():
    assert summary(AYSE)["quota_mb"] is None  # varsayılan: sınır yok
    page = set_limits("2", "1").get_data(as_text=True)
    assert "ayse: alan 2 MB, dosya başına 1 MB" in page
    assert (one("SELECT quota_mb, upload_max_mb FROM users WHERE id = ?", (AYSE,))["quota_mb"]) == 2
    # Doğrulama: negatif / sayı değil / genel kotadan büyük
    for bad, msg in (("-1", "pozitif tam sayı"), ("abc", "pozitif tam sayı"), ("99999", "en fazla 350 MB")):
        assert msg in set_limits(bad, "1").get_data(as_text=True), bad
    assert one("SELECT quota_mb FROM users WHERE id = ?", (AYSE,))["quota_mb"] == 2  # değişmedi
    # Yönetici olmayan sınır koyamaz
    set_limits("", "", client=AYSE_C)
    assert one("SELECT quota_mb FROM users WHERE id = ?", (AYSE,))["quota_mb"] == 2
    # Yönetim sayfasında kullanım ve form
    users = ADMIN.text("/yonetim/kullanicilar")
    assert f'action="/yonetim/kullanicilar/{AYSE}/sinirlar"' in users and "/ 2 MB" in users and "dosya başına 1 MB" in users
    print("  admin limits OK")


def test_file_size_and_space():
    # Dosya başına 1 MB: ~1,4 MB'lık fotoğraf reddedilir, küçük olan eklenir (küçültülmüş hali kotaya sayılır)
    _nid, page = note_with_upload(noise_png(700))
    assert "en fazla 1 MB yükleyebilirsin" in page
    assert one("SELECT COUNT(*) AS n FROM attachments WHERE user_id = ?", (AYSE,))["n"] == 0
    _nid, page = note_with_upload(noise_png(200))
    assert one("SELECT COUNT(*) AS n FROM attachments WHERE user_id = ?", (AYSE,))["n"] == 1
    # Aktar: 1 MB sınırı ve 2 MB alan
    assert "en fazla 1 MB" in transfer_upload(1100 * KB)
    transfer_upload(800 * KB)
    transfer_upload(800 * KB)
    page = transfer_upload(800 * KB)
    assert "Depolama alanın doldu" in page and "/ 2 MB" in page
    s = summary(AYSE)
    assert s["usage"]["transfer"] == 1600 * KB and s["usage"]["attachments"] > 0 and s["percent"] >= 80
    # Ayarlar sayfasında kendi kullanımı
    settings = AYSE_C.text("/ayarlar/")
    assert 'id="depolama"' in settings and "/ 2 MB kullanılıyor" in settings and "Tek dosya en fazla 1 MB" in settings
    # Aktar sayfası ipucu: modül sınırı (25 MB) yerine yönetici sınırı
    assert "her biri en fazla 1 MB" in AYSE_C.text("/aktar/")
    AYSE_C.post("/aktar/temizle")
    print("  size/space OK")


def test_trash_counts():
    before = summary(AYSE)["usage"]
    nid = one("SELECT n.id FROM notes n JOIN attachments a ON a.entity = 'note' AND a.entity_id = n.id"
              " WHERE n.user_id = ?", (AYSE,))["id"]
    AYSE_C.post(f"/notlar/{nid}/sil")
    after = summary(AYSE)["usage"]
    assert after["attachments"] == 0 and after["trash"] == before["attachments"] and after["total"] == before["total"]
    AYSE_C.post("/cop-kutusu/bosalt")
    assert summary(AYSE)["usage"]["total"] == 0
    print("  trash OK")


def test_upload_disabled():
    set_limits("", "0")
    assert "Hesabında dosya yükleme kapalı" in note_with_upload(noise_png(50))[1]
    assert "Hesabında dosya yükleme kapalı" in transfer_upload(10 * KB)
    assert "hesabında dosya yükleme kapalı" in AYSE_C.text("/aktar/")
    with app.app_context():
        from pano.modules.scanner import ScanError, inbox_add
        try:
            inbox_add(AYSE, noise_png(50), "s.png")
            raise AssertionError("tarama kutusu kabul etti")
        except ScanError as e:
            assert "yükleme kapalı" in str(e)
    # Belge Tara'ya yüklenen fotoğraf da (saklanmasa bile) dosya sınırına takılır
    r = AYSE_C.post("/tara/pdf", data={"files": [(io.BytesIO(noise_png(50)), "a.png")], "target": "download"},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "yükleme kapalı" in r.get_data(as_text=True)
    # Profil fotoğrafı
    r = AYSE_C.post("/profil-sayfasi/", data={"slug": "ayse", "display_name": "Ayşe", "photo": (io.BytesIO(noise_png(50)), "p.png")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "yükleme kapalı" in r.get_data(as_text=True)
    assert one("SELECT photo FROM public_profiles WHERE user_id = ?", (AYSE,)) is None or \
        one("SELECT photo FROM public_profiles WHERE user_id = ?", (AYSE,))["photo"] is None
    assert "🚫 Hesabında dosya yükleme kapalı" in AYSE_C.text("/ayarlar/")
    # Sınırı kaldırınca yine yüklenir
    set_limits("", "")
    note_with_upload(noise_png(200))
    assert one("SELECT COUNT(*) AS n FROM attachments WHERE user_id = ?", (AYSE,))["n"] == 1
    # Yöneticinin kendi sınırı yok: etkilenmez
    assert summary(1)["quota_mb"] is None
    print("  disabled OK")


def test_defaults_for_new_users():
    os.environ["DEFAULT_QUOTA_MB"] = "50"
    os.environ["DEFAULT_UPLOAD_MAX_MB"] = "5"
    try:
        ADMIN.post("/yonetim/kullanicilar", data={"username": "veli", "password": "veli12345"})
        ADMIN.post("/yonetim/kullanicilar", data={"username": "zeynep", "password": "zeynep12345", "quota_mb": "10",
                                                   "upload_max_mb": ""})
        ADMIN.post("/yonetim/kullanicilar", data={"username": "yonetici2", "password": "yonetici12345", "is_admin": "1"})
        get = lambda n: one("SELECT quota_mb, upload_max_mb FROM users WHERE username = ?", (n,))  # noqa: E731
        assert tuple(get("veli")) == (50, 5)
        assert tuple(get("zeynep")) == (10, None)  # formda girilen, varsayılanın yerine
        assert tuple(get("yonetici2")) == (None, None)  # yöneticiye varsayılan sınır konmaz
        assert 'value="50"' in ADMIN.text("/yonetim/kullanicilar")  # yeni kullanıcı formunda varsayılan dolu
    finally:
        os.environ.pop("DEFAULT_QUOTA_MB")
        os.environ.pop("DEFAULT_UPLOAD_MAX_MB")
    print("  defaults OK")


if __name__ == "__main__":
    test_admin_sets_limits()
    test_file_size_and_space()
    test_trash_counts()
    test_upload_disabled()
    test_defaults_for_new_users()
    print("OK")
