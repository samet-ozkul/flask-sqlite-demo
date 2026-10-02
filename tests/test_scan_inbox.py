"""Telegram'dan belge tarama: "📄 Taramaya ekle", /tara modu, albüm, bottan PDF, web sayfasında kutudaki sayfalar.

Çalıştır: .venv/Scripts/python tests/test_scan_inbox.py
"""
import io
import json
import os
import re
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

from PIL import Image  # noqa: E402

app = make_app()
os.environ["CRON_SECRET"] = "gizli"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.telegram as tg  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query_one  # noqa: E402


def jpeg(w, h, color="white"):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG", quality=80)
    return buf.getvalue()


FILES = {}  # Telegram file_id -> bayt
CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {}, files)) or {"message_id": 77}
tg.download_file = lambda file_id: FILES[file_id]

with app.app_context():
    create_user("ayse", "ayse12345")
    execute("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
with app.test_request_context():
    SECRET = tg.webhook_secret()
ADMIN = Client(app)
AYSE = Client(app, "ayse", "ayse12345")
RAW = app.test_client()


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def count():
    return one("SELECT COUNT(*) AS n FROM scan_inbox WHERE user_id = 1")["n"]


def say(message):
    RAW.post("/telegram/webhook", data=json.dumps({"message": {"message_id": 1, "chat": {"id": 100}, **message}}),
             content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})


def press(data):
    RAW.post("/telegram/webhook", data=json.dumps({"callback_query": {
        "id": "cb", "data": data, "from": {"id": 100}, "message": {"message_id": 7, "chat": {"id": 100}}}}),
        content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})


def last(method):
    return [p for m, p, f in CALLS if m == method][-1]


def buttons(params):
    return [b["callback_data"] for row in json.loads(params.get("reply_markup") or '{"inline_keyboard": []}')["inline_keyboard"]
            for b in row]


def photo(file_id, data, **extra):
    FILES[file_id] = data
    say({"photo": [{"file_id": file_id + "-kucuk"}, {"file_id": file_id}], **extra})


def pdf_pages(data):
    text = data.decode("latin1")
    return [tuple(float(x) for x in m.split()[2:4]) for m in re.findall(r"/MediaBox\s*\[([^\]]+)\]", text)]


def inbox_dir():
    return os.path.join(app.config["UPLOAD_DIR"], "_tarama", "1")


def test_prompt_and_album():
    photo("p1", jpeg(600, 800))
    prompt = last("sendMessage")
    assert "ph:sc" in buttons(prompt)
    press("ph:sc")
    assert count() == 1
    edit = last("editMessageText")
    assert "Tarama kutusu: 1 sayfa" in edit["text"] and buttons(edit)[:2] == ["scn:pdf:color", "scn:pdf:bw"]
    # Albüm: üç fotoğraf tek soruda toplanır, seçim hepsine uygulanır
    before = len([1 for m, _p, _f in CALLS if m == "sendMessage"])
    for i in range(3):
        photo(f"a{i}", jpeg(600, 800), media_group_id="g1")
    assert len([1 for m, _p, _f in CALLS if m == "sendMessage"]) == before + 1
    assert "3 dosya geldi" in last("editMessageText")["text"]
    press("ph:sc")
    assert count() == 4 and "Tarama kutusu: 4 sayfa" in last("editMessageText")["text"]
    print("  prompt/album OK")


def test_scan_mode_and_pdf():
    say({"text": "/tara"})
    status = last("sendMessage")
    assert "tarama modu açık" in status["text"] and "4 sayfa" in status["text"]
    sends = len([1 for m, _p, _f in CALLS if m == "sendMessage"])
    photo("m1", jpeg(800, 600))
    photo("m2", jpeg(600, 800), media_group_id="g2")
    # Sormadan kutuya; yeni soru yok, aynı durum mesajı güncellenir
    assert len([1 for m, _p, _f in CALLS if m == "sendMessage"]) == sends
    assert count() == 6 and "6 sayfa" in last("editMessageText")["text"]
    # Resim olmayan belge tarama modunda da normal soruyu alır
    FILES["z"] = b"PK\x03\x04 zip"
    say({"document": {"file_id": "z", "file_name": "a.zip", "mime_type": "application/zip"}})
    assert buttons(last("sendMessage")) == ["ph:tr", "ph:x"]
    # PDF yap: belge olarak sohbete gelir, kutu boşalır, mod kapanır
    press("scn:pdf:color")
    doc = [f for m, _p, f in CALLS if m == "sendDocument"][-1]["document"]
    name, data, mime = doc
    assert name.startswith("Tarama ") and name.endswith(".pdf") and mime == "application/pdf"
    assert data.startswith(b"%PDF") and len(pdf_pages(data)) == 6
    assert count() == 0 and os.listdir(inbox_dir()) == []
    assert "PDF gönderildi: 6 sayfa" in last("editMessageText")["text"]
    sends = len([1 for m, _p, _f in CALLS if m == "sendMessage"])
    photo("after", jpeg(600, 800))  # mod kapandı: yeniden soru gelir
    assert len([1 for m, _p, _f in CALLS if m == "sendMessage"]) == sends + 1 and "ph:sc" in buttons(last("sendMessage"))
    say({"text": "/tara bitti"})  # kutu boşken
    assert "Tarama kutusu boş" in last("sendMessage")["text"]
    print("  scan mode/pdf OK")


def test_web_order():
    press("ph:x")
    photo("w1", jpeg(800, 600))   # yatay -> 384x288 pt
    press("ph:sc")
    photo("w2", jpeg(600, 800))   # dikey -> 288x384 pt
    press("ph:sc")
    rows = [dict(r) for r in rows_inbox()]
    a, b = rows[0]["id"], rows[1]["id"]
    page = ADMIN.text("/tara/")
    data = json.loads(page.split('id="s-inbox">', 1)[1].split("</script>", 1)[0])
    assert [d["id"] for d in data] == [a, b] and "Telegram'dan gelen 2 sayfa" in page
    r = ADMIN.get(f"/tara/kutu/{a}")
    assert r.status_code == 200 and r.headers["Content-Type"] == "image/jpeg" and r.headers["X-Content-Type-Options"] == "nosniff"
    r.close()
    assert AYSE.get(f"/tara/kutu/{a}").status_code == 404 and "s-inbox-card" not in AYSE.text("/tara/")
    # Sıra: kutudan b, yüklenen kare, kutudan a; ortadaki 90° döndürülmez (kare)
    r = ADMIN.post("/tara/pdf", data={"order": f"i{b},f,i{a}", "rotations": "0,0,0", "mode": "color", "target": "download",
                                      "files": [(io.BytesIO(jpeg(500, 500)), "kare.jpg")]},
                   content_type="multipart/form-data")
    assert r.status_code == 200 and r.mimetype == "application/pdf"
    assert pdf_pages(r.data) == [(288.0, 384.0), (240.0, 240.0), (384.0, 288.0)]
    r.close()
    assert count() == 0  # PDF'e giren sayfalar kutudan silindi
    # JS yoksa (order yok): önce kutudakiler, sonra yüklenen
    photo("w3", jpeg(800, 600))
    press("ph:sc")
    r = ADMIN.post("/tara/pdf", data={"mode": "gray", "target": "download", "files": [(io.BytesIO(jpeg(600, 800)), "d.jpg")]},
                   content_type="multipart/form-data")
    assert pdf_pages(r.data) == [(384.0, 288.0), (288.0, 384.0)]
    r.close()
    # Sadece kutudaki sayfalarla da PDF yapılır; başkasının kutu id'si yok sayılır
    photo("w4", jpeg(600, 800))
    press("ph:sc")
    mine = rows_inbox()[0]["id"]
    r = ADMIN.post("/tara/pdf", data={"order": f"i{mine},i99999", "target": "transfer"}, follow_redirects=True)
    assert "PDF aktarma kutusuna kondu (1 sayfa" in r.get_data(as_text=True)
    print("  web order OK")


def colorspaces(data):
    return set(re.findall(r"/ColorSpace\s*/(Device\w+)", data.decode("latin1")))


def last_pdf():
    return [f for m, _p, f in CALLS if m == "sendDocument"][-1]["document"][1]


def test_colors():
    # Varsayılan (📄 PDF yap ve /tara bitti) orijinal renk; siyah-beyaz sadece istenince
    for action, expected in (("scn:pdf:color", {"DeviceRGB"}), ("scn:pdf:bw", {"DeviceGray"}),
                             ("scn:pdf:doc", {"DeviceRGB"})):  # doc: eski mesajdaki "📄 PDF yap" düğmesi
        photo("c-" + action, jpeg(300, 400, (200, 40, 40)))
        press("ph:sc")
        press(action)
        assert colorspaces(last_pdf()) == expected, (action, colorspaces(last_pdf()))
    for command, expected in (("/tara bitti", {"DeviceRGB"}), ("/tara siyah", {"DeviceGray"})):
        photo("c-" + command, jpeg(300, 400, (40, 40, 200)))
        press("ph:sc")
        say({"text": command})
        assert colorspaces(last_pdf()) == expected, (command, colorspaces(last_pdf()))
    # Web: görünüm seçilmezse orijinal renk; sayfada da varsayılan seçili
    assert 'value="color" checked' in ADMIN.text("/tara/")
    r = ADMIN.post("/tara/pdf", data={"target": "download", "files": [(io.BytesIO(jpeg(300, 400, (30, 160, 60))), "y.jpg")]},
                   content_type="multipart/form-data")
    assert colorspaces(r.data) == {"DeviceRGB"}
    r.close()
    print("  colors OK")


def rows_inbox():
    with app.app_context():
        from pano.db import query
        return query("SELECT * FROM scan_inbox WHERE user_id = 1 ORDER BY id")


def test_limits_and_cleanup():
    with app.app_context():
        from pano.modules.scanner import inbox_add
        for i in range(20):
            inbox_add(1, jpeg(60, 80), f"s{i}.jpg")
    say({"text": "/tara"})
    photo("fazla", jpeg(60, 80))
    assert "en fazla 20 sayfa" in last("editMessageText")["text"] and count() == 20
    say({"text": "/tara iptal"})
    assert count() == 0 and "20 sayfa silindi" in last("sendMessage")["text"]
    # Resim olmayan dosya kutuya girmez
    with app.app_context():
        from pano.modules.scanner import ScanError, inbox_add
        try:
            inbox_add(1, b"not an image", "x.txt")
            raise AssertionError("resim olmayan kabul edildi")
        except ScanError:
            pass
    # Süresi dolan sayfa cron'da silinir; genel temizlik ve yedek kutuya dokunmaz
    photo("e1", jpeg(60, 80))
    press("ph:sc")
    photo("k1", jpeg(60, 80))
    press("ph:sc")
    old, keep = [dict(r) for r in rows_inbox()]
    with app.app_context():
        execute("UPDATE scan_inbox SET expires_at = ? WHERE id = ?", (time.time() - 1, old["id"]))
    assert RAW.get("/cron/gizli/hatirlatma").json["scan_pages_purged"] == 1
    assert not os.path.exists(os.path.join(inbox_dir(), old["stored_name"]))
    with app.app_context():
        from pano.backup import make_backup_zip
        from pano.storage import cleanup_orphans
        cleanup_orphans()
        zip_path, _tmp = make_backup_zip(include_files=True)
        names = zipfile.ZipFile(zip_path).namelist()
    assert os.path.exists(os.path.join(inbox_dir(), keep["stored_name"])) and not any("_tarama" in n for n in names)
    # Kutuyu web'den boşaltma
    ADMIN.post("/tara/kutu/bosalt")
    assert count() == 0
    print("  limits/cleanup OK")


def test_album_other_targets():
    # Albüm "Nota ekle" ile de hepsini ekler
    for i in range(2):
        photo(f"n{i}", jpeg(100, 100), media_group_id="g3")
    press("ph:note")
    note = one("SELECT id FROM notes ORDER BY id DESC")
    assert one("SELECT COUNT(*) AS n FROM attachments WHERE entity = 'note' AND entity_id = ?", (note["id"],))["n"] == 2
    assert "2 dosya" in last("editMessageText")["text"]
    print("  album note OK")


if __name__ == "__main__":
    test_prompt_and_album()
    test_scan_mode_and_pdf()
    test_web_order()
    test_limits_and_cleanup()
    test_album_other_targets()
    test_colors()
    print("OK")
