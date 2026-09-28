"""Telegram bot komutları.

Çalıştır: .venv/Scripts/python tests/test_bot_commands.py
"""
import io
import json
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import make_app  # noqa: E402

from PIL import Image  # noqa: E402

app = make_app()
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.bot_commands as bc  # noqa: E402
import pano.telegram as tg  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query, query_one  # noqa: E402
from pano.utils import today  # noqa: E402

CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {})) or {"message_id": 9}
buf = io.BytesIO()
Image.new("RGB", (800, 600), (10, 120, 200)).save(buf, "PNG")
tg.download_file = lambda file_id: buf.getvalue()

with app.test_request_context():
    SECRET = tg.webhook_secret()
RAW = app.test_client()
UPDATE_ID = [0]


def send(text=None, chat=100, **extra):
    UPDATE_ID[0] += 1
    msg = {"message_id": UPDATE_ID[0], "chat": {"id": chat}, **extra}
    if text is not None:
        msg["text"] = text
    r = RAW.post("/telegram/webhook", data=json.dumps({"update_id": UPDATE_ID[0], "message": msg}),
                 content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    assert r.status_code == 200


def press(data, chat=100, message_id=9):
    UPDATE_ID[0] += 1
    cq = {"id": f"cb{UPDATE_ID[0]}", "data": data, "from": {"id": chat},
          "message": {"message_id": message_id, "chat": {"id": chat}}}
    r = RAW.post("/telegram/webhook", data=json.dumps({"update_id": UPDATE_ID[0], "callback_query": cq}),
                 content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    assert r.status_code == 200


def last(method="sendMessage"):
    return [p for m, p in CALLS if m == method][-1]


def buttons(params):
    return [b for row in json.loads(params.get("reply_markup", '{"inline_keyboard": []}'))["inline_keyboard"] for b in row]


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def rows(sql, args=()):
    with app.app_context():
        return query(sql, args)


with app.app_context():
    execute("UPDATE users SET telegram_chat_id = '100' WHERE username = 'admin'")
    create_user("ayse", "ayse12345")
    execute("UPDATE users SET telegram_chat_id = '200' WHERE username = 'ayse'")
ADMIN = one("SELECT id FROM users WHERE username = 'admin'")["id"]
AYSE = one("SELECT id FROM users WHERE username = 'ayse'")["id"]


def test_help_and_unlinked():
    send("/yardim")
    assert "/harcama" in last()["text"]
    send("/bilinmeyen")
    assert "Kişisel Pano komutları" in last()["text"]
    send("/harcama 10", chat=999)
    assert "bağlı değil" in last()["text"]
    assert not rows("SELECT 1 FROM expenses WHERE amount = 10")
    print("  help/unlinked OK")


def test_expense():
    send("/harcama 250 market öğle yemeği")
    e = one("SELECT * FROM expenses ORDER BY id DESC LIMIT 1")
    assert (e["user_id"], e["amount"], e["category"], e["note"], e["date"]) == (ADMIN, 250, "Market", "öğle yemeği", today().isoformat())
    msg = last()
    assert "250 ₺" in msg["text"] and "Bu ay toplam" in msg["text"]
    assert buttons(msg)[0]["callback_data"] == f"exu:{e['id']}"
    send("/harcama 1.250,50 kira")
    assert one("SELECT amount, category, note FROM expenses ORDER BY id DESC LIMIT 1")[:] == (1250.5, "Diğer", "kira")
    send("/harcama 45tl YAKIT")
    assert one("SELECT amount, category FROM expenses ORDER BY id DESC LIMIT 1")[:] == (45, "Yakıt")
    send("/harcama@sametpano_bot 12 tl ulasim")
    assert one("SELECT amount, category FROM expenses ORDER BY id DESC LIMIT 1")[:] == (12, "Ulaşım")
    n = len(rows("SELECT 1 FROM expenses"))
    send("/harcama elli lira")
    assert "anlayamadım" in last()["text"] and len(rows("SELECT 1 FROM expenses")) == n
    send("/harcama")
    assert "Bu ay:" in last()["text"] and "Market" in last()["text"]
    # Geri al: sadece kendi harcaması
    press(f"exu:{e['id']}", chat=200)
    assert one("SELECT 1 FROM expenses WHERE id = ?", (e["id"],))
    press(f"exu:{e['id']}")
    assert not one("SELECT 1 FROM expenses WHERE id = ?", (e["id"],))
    assert "silindi" in last("editMessageText")["text"]
    print("  expense OK")


def test_note():
    send("/not toplantıda <bütçe> konuşulacak")
    n = one("SELECT * FROM notes ORDER BY id DESC LIMIT 1")
    assert n["user_id"] == ADMIN and n["content"] == "toplantıda <bütçe> konuşulacak"
    assert "Not kaydedildi" in last()["text"]
    print("  note OK")


def test_shopping_and_list():
    send("/ekle süt, ekmek")
    lst = one("SELECT * FROM lists WHERE user_id = ? AND kind = 'shopping'", (ADMIN,))
    assert lst["name"] == "Alışveriş"
    assert [r["text"] for r in rows("SELECT text FROM list_items WHERE list_id = ? ORDER BY id", (lst["id"],))] == ["süt", "ekmek"]
    assert "2 açık ürün" in last()["text"]
    send("/ekle alisveris: yumurta")
    assert one("SELECT list_id FROM list_items WHERE text = 'yumurta'")["list_id"] == lst["id"]
    # Ayşe'nin paylaşılan listesine isimle ekleme
    with app.app_context():
        ev = execute("INSERT INTO lists (user_id, name, kind, shared) VALUES (?, 'Ev', 'shopping', 1)", (AYSE,)).lastrowid
        gizli = execute("INSERT INTO lists (user_id, name, kind, shared) VALUES (?, 'Gizli', 'shopping', 0)", (AYSE,)).lastrowid
    send("/ekle ev: çay")
    assert one("SELECT list_id FROM list_items WHERE text = 'çay'")["list_id"] == ev
    send("/ekle gizli: şeker")  # erişemediği liste: varsayılan listeye düşer
    assert one("SELECT list_id FROM list_items WHERE text = 'gizli: şeker'")["list_id"] == lst["id"]

    # /liste: dokununca işaretlenir, liste yeniden çizilir
    send("/liste alışveriş")
    msg = last()
    labels = {b["text"]: b["callback_data"] for b in buttons(msg)}
    assert "☐ süt" in labels and "Alışveriş" in msg["text"]
    press(labels["☐ süt"])
    assert one("SELECT done FROM list_items WHERE text = 'süt'")["done"] == 1
    edited = last("editMessageText")
    assert "✅ süt" in [b["text"] for b in buttons(edited)]
    press(labels["☐ süt"])  # geri al
    assert one("SELECT done FROM list_items WHERE text = 'süt'")["done"] == 0
    # başkasının özel listesindeki madde işaretlenemez
    with app.app_context():
        secret_item = execute("INSERT INTO list_items (list_id, text) VALUES (?, 'x')", (gizli,)).lastrowid
    press(f"li:{secret_item}")
    assert one("SELECT done FROM list_items WHERE id = ?", (secret_item,))["done"] == 0
    send("/liste yokboyle")
    assert "bulamadım" in last()["text"]
    send("/liste")
    assert "açık madde" in last()["text"]
    print("  shopping/list OK")


def test_parse_when():
    t = today()
    with app.app_context():
        assert bc.parse_when("fatura öde yarın 14:00") == ("fatura öde", (t + timedelta(days=1)).isoformat(), "14:00")
        assert bc.parse_when("ara BUGÜN") == ("ara", t.isoformat(), None)
        assert bc.parse_when("doktor saat 9.30 yarın") == ("doktor", (t + timedelta(days=1)).isoformat(), "09:30")
        assert bc.parse_when("doktor yarın saat 9.30") == ("doktor", (t + timedelta(days=1)).isoformat(), "09:30")
        task, d, tm = bc.parse_when("sunum 12.10")
        assert task == "sunum" and d.endswith("-10-12") and tm is None
        task, d, _ = bc.parse_when("toplantı cuma")
        assert task == "toplantı" and 1 <= (bc.today().fromisoformat(d) - t).days <= 7
        assert bc.parse_when("sadece metin") == ("sadece metin", None, None)
        assert bc.parse_when("süt 2 litre") == ("süt 2 litre", None, None)
    print("  parse_when OK")


def test_todo():
    send("/yap faturayı öde yarın 14:00")
    item = one("SELECT i.*, l.name AS list_name FROM list_items i JOIN lists l ON l.id = i.list_id"
               " WHERE i.text = 'faturayı öde'")
    tomorrow = (today() + timedelta(days=1)).isoformat()
    assert (item["due_date"], item["due_time"], item["remind_before"], item["list_name"]) == (tomorrow, "14:00", 0, "Yapılacaklar")
    msg = last()
    assert "zamanı gelince" in msg["text"] and buttons(msg)[0]["callback_data"] == f"done:{item['id']}"
    send("/yap kitap oku")
    assert one("SELECT due_date, remind_before FROM list_items WHERE text = 'kitap oku'")[:] == (None, None)
    send("/yap yarın")
    assert "unuttun" in last()["text"]
    print("  todo OK")


def test_link():
    send("https://example.com/yazi harika bir yazı")
    row = one("SELECT * FROM links ORDER BY id DESC LIMIT 1")
    assert (row["url"], row["title"], row["user_id"]) == ("https://example.com/yazi", "harika bir yazı", ADMIN)
    send("şuna bak: https://example.com/yazi")
    assert "zaten kayıtlıydı" in last()["text"] and len(rows("SELECT 1 FROM links")) == 1
    send("https://örnek.com")
    assert one("SELECT title FROM links ORDER BY id DESC LIMIT 1")["title"] == "örnek.com"
    print("  link OK")


def test_plain_text():
    send("annemi ara yarın")
    msg = last()
    choices = {b["callback_data"] for b in buttons(msg)}
    assert {"tx:note", "tx:shop", "tx:todo", "tx:x"} <= choices and "tx:exp" not in choices
    press("tx:todo")
    assert one("SELECT due_date FROM list_items WHERE text = 'annemi ara'")["due_date"] == (today() + timedelta(days=1)).isoformat()
    press("tx:todo")  # ikinci kez: bekleyen metin yok
    assert "Süre doldu" in last("editMessageText")["text"]
    send("85 kahve")
    assert "tx:exp" in {b["callback_data"] for b in buttons(last())}
    press("tx:exp")
    assert one("SELECT amount, note FROM expenses ORDER BY id DESC LIMIT 1")[:] == (85, "kahve")
    send("bir şey")
    press("tx:x")
    assert "Vazgeçildi" in last("editMessageText")["text"]
    print("  plain text OK")


def test_photo():
    photo = [{"file_id": "small", "width": 90}, {"file_id": "big", "width": 1280}]
    send(photo=photo, caption="Buzdolabı faturası")
    msg = last()
    assert "nereye" in msg["text"] and "ph:new" in {b["callback_data"] for b in buttons(msg)}
    press("ph:new")
    w = one("SELECT * FROM warranties ORDER BY id DESC LIMIT 1")
    assert w["product"] == "Buzdolabı faturası" and w["user_id"] == ADMIN
    att = one("SELECT * FROM attachments WHERE entity = 'warranty' AND entity_id = ?", (w["id"],))
    assert att and att["mime"] == "image/jpeg"
    assert "Eklendi" in last("editMessageText")["text"]
    # Var olan garantiye ekleme; başkasının garantisine ekleme yok
    with app.app_context():
        other = execute("INSERT INTO warranties (user_id, product) VALUES (?, 'Ayşe TV')", (AYSE,)).lastrowid
    send(photo=photo)
    press(f"ph:w:{other}")
    assert not one("SELECT 1 FROM attachments WHERE entity = 'warranty' AND entity_id = ?", (other,))
    send(photo=photo)
    press(f"ph:w:{w['id']}")
    assert len(rows("SELECT 1 FROM attachments WHERE entity = 'warranty' AND entity_id = ?", (w["id"],))) == 2
    # PDF belge nota eklenir
    send(document={"file_id": "doc", "file_name": "fis.pdf", "mime_type": "application/pdf"})
    tg.download_file = lambda file_id: b"%PDF-1.4 test"
    press("ph:note")
    n = one("SELECT * FROM notes ORDER BY id DESC LIMIT 1")
    assert n["title"] == "Fotoğraf" and one("SELECT mime FROM attachments WHERE entity = 'note' AND entity_id = ?", (n["id"],))["mime"] == "application/pdf"
    print("  photo OK")


def test_today():
    send("/bugun")
    assert "Günaydın" in last()["text"]
    print("  today OK")


if __name__ == "__main__":
    test_help_and_unlinked()
    test_expense()
    test_note()
    test_shopping_and_list()
    test_parse_when()
    test_todo()
    test_link()
    test_plain_text()
    test_photo()
    test_today()
    print("OK")
