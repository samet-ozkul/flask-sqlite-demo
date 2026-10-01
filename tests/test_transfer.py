"""Aktar: süreli metin/dosya, boyut sınırları, tek seferlik, güvenli sunum, temizlik, Telegram.

Küçük sınırlarla çalışır: TRANSFER_MAX_MB=1, TRANSFER_TOTAL_MB=3.
Çalıştır: .venv/Scripts/python tests/test_transfer.py
"""
import io
import json
import os
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["TRANSFER_MAX_MB"] = "1"
os.environ["TRANSFER_TOTAL_MB"] = "3"
from helpers import Client, make_app  # noqa: E402

from PIL import Image  # noqa: E402

app = make_app()
os.environ["CRON_SECRET"] = "gizli"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.telegram as tg  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query_one  # noqa: E402

CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {}, files)) or {"message_id": 1}
tg.download_file = lambda file_id: b"PK\x03\x04 telegram zip " + file_id.encode()

with app.app_context():
    create_user("ayse", "ayse12345")
    execute("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
with app.test_request_context():
    SECRET = tg.webhook_secret()
ADMIN = Client(app)
AYSE = Client(app, "ayse", "ayse12345")
RAW = app.test_client()
MB = 1024 * 1024


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def run(sql, args=()):
    with app.app_context():
        execute(sql, args)


def tdir(uid=1):
    return os.path.join(app.config["UPLOAD_DIR"], "_aktarma", str(uid))


def upload(client, files, **extra):
    data = {"files": [(io.BytesIO(content), name) for name, content in files], **extra}
    return client.post("/aktar/yeni", data=data, content_type="multipart/form-data", follow_redirects=True)


def png():
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "blue").save(buf, "PNG")
    return buf.getvalue()


def test_text():
    r = ADMIN.post("/aktar/yeni", data={"text": "https://ornek.com/sayfa?a=1", "minutes": "10"},
                   headers={"User-Agent": "Mozilla/5.0 (Linux; Android 14)"}, follow_redirects=True)
    page = r.get_data(as_text=True)
    assert "aktarma kutusuna kondu; 10 dakika sonra silinecek" in page
    row = one("SELECT * FROM transfers WHERE kind = 'text'")
    assert row["device"] == "📱 Android" and 590 < row["expires_at"] - time.time() <= 600
    assert 'href="https://ornek.com/sayfa?a=1"' in page and "📋 Kopyala" in page
    ADMIN.post("/aktar/yeni", data={"text": "javascript:alert(1)"})
    assert 'href="javascript:' not in ADMIN.text("/aktar/")  # sadece http(s) bağlantı olur
    assert ADMIN.post("/aktar/yeni", data={"text": "x" * 50_001}, follow_redirects=True).status_code == 200
    assert one("SELECT COUNT(*) AS n FROM transfers")["n"] == 2  # çok uzun metin eklenmedi
    # Başkası göremez; liste parçası önbelleğe alınmaz
    assert "ornek.com" not in AYSE.text("/aktar/")
    r = ADMIN.get("/aktar/liste")
    assert r.headers["Cache-Control"] == "no-store" and "ornek.com" in r.get_data(as_text=True)
    assert AYSE.post(f"/aktar/{row['id']}/sil").status_code == 404
    print("  text OK")


def test_files():
    r = upload(ADMIN, [("rapor ğüş.pdf", b"%PDF-1.4 icerik" * 100)])
    assert "1 kayıt aktarma kutusuna kondu" in r.get_data(as_text=True)
    f = one("SELECT * FROM transfers WHERE filename = 'rapor ğüş.pdf'")
    assert f["size"] == 1500 and len(f["stored_name"]) == 32 and os.path.isfile(os.path.join(tdir(), f["stored_name"]))
    r = ADMIN.get(f"/aktar/{f['id']}/indir")
    assert r.status_code == 200 and r.data == b"%PDF-1.4 icerik" * 100
    assert r.headers["Content-Type"] == "application/octet-stream" and r.headers["X-Content-Type-Options"] == "nosniff"
    assert "attachment" in r.headers["Content-Disposition"] and "filename*=UTF-8''rapor%20%C4%9F%C3%BC%C5%9F.pdf" in r.headers["Content-Disposition"]
    r.close()
    assert AYSE.get(f"/aktar/{f['id']}/indir").status_code == 404
    # Çok büyük dosya reddedilir ve diskte artık kalmaz
    before = set(os.listdir(tdir()))
    r = upload(ADMIN, [("buyuk.bin", b"0" * (MB + 10))])
    assert "en fazla 1 MB" in r.get_data(as_text=True) and set(os.listdir(tdir())) == before
    # Kişi başına toplam sınır
    for i in range(3):
        upload(ADMIN, [(f"parca{i}.bin", b"1" * (900 * 1024))])
    r = upload(ADMIN, [("fazla.bin", b"2" * (900 * 1024))])
    assert "Aktarma kutusu dolu" in r.get_data(as_text=True) and not one("SELECT 1 x FROM transfers WHERE filename = 'fazla.bin'")
    assert set(os.listdir(tdir())) == {r["stored_name"] for r in rows_files(1)}
    ADMIN.post("/aktar/temizle")
    assert one("SELECT COUNT(*) AS n FROM transfers WHERE user_id = 1")["n"] == 0 and os.listdir(tdir()) == []
    print("  files OK")


def rows_files(uid):
    with app.app_context():
        from pano.db import query
        return query("SELECT stored_name FROM transfers WHERE user_id = ? AND kind = 'file'", (uid,))


def test_once_and_preview():
    upload(ADMIN, [("foto.png", png()), ("sayfa.html", b"<script>alert(1)</script>")])
    img = one("SELECT * FROM transfers WHERE filename = 'foto.png'")
    html = one("SELECT * FROM transfers WHERE filename = 'sayfa.html'")
    page = ADMIN.text("/aktar/")
    assert f"/aktar/{img['id']}/onizleme" in page and f"/aktar/{html['id']}/onizleme" not in page
    r = ADMIN.get(f"/aktar/{img['id']}/onizleme")
    assert r.status_code == 200 and r.headers["Content-Type"] == "image/png" and "default-src 'none'" in r.headers["Content-Security-Policy"]
    r.close()
    assert ADMIN.get(f"/aktar/{html['id']}/onizleme").status_code == 404  # HTML tarayıcıda açılmaz
    r = ADMIN.get(f"/aktar/{html['id']}/indir")
    assert r.headers["Content-Type"] == "application/octet-stream" and "attachment" in r.headers["Content-Disposition"]
    r.close()
    # Tek seferlik: ilk indirmede silinir, önizlemesi yoktur
    upload(ADMIN, [("gizli.txt", b"tek sefer")], once="1")
    once = one("SELECT * FROM transfers WHERE filename = 'gizli.txt'")
    assert once["once"] == 1 and "tek seferlik" in ADMIN.text("/aktar/")
    r = ADMIN.get(f"/aktar/{once['id']}/indir")
    assert r.data == b"tek sefer"
    r.close()
    assert ADMIN.get(f"/aktar/{once['id']}/indir").status_code == 404
    assert not os.path.exists(os.path.join(tdir(), once["stored_name"]))
    print("  once/preview OK")


def test_expiry_and_cleanup():
    upload(ADMIN, [("eski.txt", b"eski")])
    old = one("SELECT * FROM transfers WHERE filename = 'eski.txt'")
    run("UPDATE transfers SET expires_at = ? WHERE id = ?", (time.time() - 1, old["id"]))
    assert ADMIN.get(f"/aktar/{old['id']}/indir").status_code == 404  # süresi dolan hemen erişilemez
    r = RAW.get("/cron/gizli/hatirlatma")
    assert r.json["transfers_purged"] >= 1 and not os.path.exists(os.path.join(tdir(), old["stored_name"]))
    # Kaydı olmayan eski dosya silinir, yeni yazılan (10 dk'dan genç) dokunulmaz
    stray_old, stray_new = os.path.join(tdir(), "eskiyetim"), os.path.join(tdir(), "yeniyetim")
    for path in (stray_old, stray_new):
        with open(path, "wb") as fh:
            fh.write(b"x")
    os.utime(stray_old, (time.time() - 3600, time.time() - 3600))
    ADMIN.text("/aktar/")
    assert not os.path.exists(stray_old) and os.path.exists(stray_new)
    os.remove(stray_new)
    # Genel temizlik ve yedek aktarma dosyalarına dokunmaz
    upload(ADMIN, [("kalsin.txt", b"kalsin")])
    keep = one("SELECT * FROM transfers WHERE filename = 'kalsin.txt'")
    with app.app_context():
        from pano.backup import make_backup_zip
        from pano.storage import cleanup_orphans
        cleanup_orphans()
        zip_path, tmp = make_backup_zip(include_files=True)
        names = zipfile.ZipFile(zip_path).namelist()
    assert os.path.exists(os.path.join(tdir(), keep["stored_name"])) and not any("_aktarma" in n for n in names)
    print("  expiry/cleanup OK")


def test_telegram():
    keep = one("SELECT * FROM transfers WHERE filename = 'kalsin.txt'")
    ADMIN.post(f"/aktar/{keep['id']}/telegram")
    method, params, files = CALLS[-1]
    assert method == "sendDocument" and params["chat_id"] == "100" and files["document"][0] == "kalsin.txt"
    assert files["document"][2] == "text/plain"

    def say(message):
        RAW.post("/telegram/webhook", data=json.dumps({"message": {"message_id": 1, "chat": {"id": 100}, **message}}),
                 content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})

    def press(data):
        RAW.post("/telegram/webhook", data=json.dumps({"callback_query": {
            "id": "cb", "data": data, "from": {"id": 100}, "message": {"message_id": 7, "chat": {"id": 100}}}}),
            content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})

    def buttons():
        params = [p for m, p, f in CALLS if m == "sendMessage"][-1]
        return [b["callback_data"] for row in json.loads(params["reply_markup"])["inline_keyboard"] for b in row]

    say({"text": "/aktar bilgisayarda açılacak not"})
    t = one("SELECT * FROM transfers WHERE text = 'bilgisayarda açılacak not'")
    assert t and t["device"] == "🤖 Telegram"
    # Fotoğraf/PDF dışı dosya: sadece Aktar seçeneği
    say({"document": {"file_id": "abc", "file_name": "proje.zip", "mime_type": "application/zip"}})
    assert buttons() == ["ph:tr", "ph:x"]
    press("ph:tr")
    f = one("SELECT * FROM transfers WHERE filename = 'proje.zip'")
    assert f and f["mime"] == "application/zip" and f["size"] == len(b"PK\x03\x04 telegram zip abc")
    # Fotoğrafta diğer seçeneklerin yanında Aktar da var; düz yazıda da
    say({"photo": [{"file_id": "p1"}]})
    assert "ph:tr" in buttons() and "ph:note" in buttons()
    say({"text": "bunu bilgisayara at"})
    assert "tx:tr" in buttons()
    press("tx:tr")
    assert one("SELECT 1 x FROM transfers WHERE text = 'bunu bilgisayara at'")
    print("  telegram OK")


if __name__ == "__main__":
    test_text()
    test_files()
    test_once_and_preview()
    test_expiry_and_cleanup()
    test_telegram()
    print("OK")
