"""Telegram mesaj butonları (✅ Tamamlandı) ve webhook.

Çalıştır: .venv/Scripts/python tests/test_telegram_buttons.py
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

app = make_app()
os.environ["CRON_SECRET"] = "gizli"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.telegram as tg  # noqa: E402
import pano.todo_reminders as todo  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query_one  # noqa: E402
from pano.utils import TZ  # noqa: E402

CALLS = []


def fake_call(method, params=None, files=None):
    CALLS.append((method, params or {}))
    if method == "getUpdates":
        raise tg.TelegramError("Conflict: can't use getUpdates method while webhook is active")
    if method == "getWebhookInfo":
        return {"url": "https://x/telegram/webhook", "pending_update_count": 0}
    return {"message_id": 77}


tg._call = fake_call
todo.now_local = lambda: datetime(2026, 9, 25, 12, 0, tzinfo=TZ)

with app.test_request_context():
    SECRET = tg.webhook_secret()


def calls(method):
    return [p for m, p in CALLS if m == method]


def hook(c, update, secret=SECRET):
    headers = {"X-Telegram-Bot-Api-Secret-Token": secret} if secret is not None else {}
    return c.post("/telegram/webhook", data=json.dumps(update), content_type="application/json", headers=headers)


def item(item_id):
    with app.app_context():
        return query_one("SELECT * FROM list_items WHERE id = ?", (item_id,))


def test_webhook_setup():
    admin = Client(app)
    # Yerelde (http) kurulamaz
    admin.post("/yonetim/telegram/webhook", data={"action": "set"})
    assert not calls("setWebhook")
    # HTTPS üzerinden kurulur, gizli anahtar gönderilir
    https = app.test_client()
    r = https.get("/giris", base_url="https://localhost")
    import re
    tok = re.search(r'name="_csrf" value="(\w+)"', r.get_data(as_text=True)).group(1)
    https.post("/giris", data={"_csrf": tok, "username": "admin", "password": "admin12345"}, base_url="https://localhost")
    tok = re.search(r'name="_csrf" value="(\w+)"', https.get("/yonetim/", base_url="https://localhost").get_data(as_text=True)).group(1)
    https.post("/yonetim/telegram/webhook", data={"_csrf": tok, "action": "set"}, base_url="https://localhost")
    p = calls("setWebhook")[-1]
    assert p["url"] == "https://localhost/telegram/webhook" and p["secret_token"] == SECRET
    with app.app_context():
        assert tg.webhook_active()
    h = admin.text("/yonetim/")
    assert "Mesaj butonları" in h and "Açık" in h
    print("  webhook setup OK")


def test_webhook_security():
    raw = app.test_client()
    assert hook(raw, {"message": {"text": "/start x", "chat": {"id": 1}}}, secret=None).status_code == 403
    assert hook(raw, {"message": {"text": "/start x", "chat": {"id": 1}}}, secret="yanlis").status_code == 403
    assert hook(raw, {}).status_code == 200  # doğru anahtarla boş güncelleme sorun çıkarmaz
    print("  webhook security OK")


def test_link_via_webhook():
    c = Client(app)
    c.post("/ayarlar/telegram/kod")
    with app.app_context():
        code = query_one("SELECT telegram_link_code FROM users WHERE username = 'admin'")["telegram_link_code"]
    assert code
    # Webhook açıkken "doğrula" getUpdates'e gitmez, henüz bağlanmadı uyarısı verir
    before = len(calls("getUpdates"))
    c.post("/ayarlar/telegram/dogrula")
    assert len(calls("getUpdates")) == before
    # Bota "/start KOD" gelince bağlanır
    raw = app.test_client()
    assert hook(raw, {"update_id": 1, "message": {"text": f"/start {code}", "chat": {"id": 555}}}).status_code == 200
    with app.app_context():
        u = query_one("SELECT telegram_chat_id, telegram_link_code FROM users WHERE username = 'admin'")
    assert (u["telegram_chat_id"], u["telegram_link_code"]) == ("555", None)
    assert "bağlandı" in calls("sendMessage")[-1]["text"]
    r = c.post("/ayarlar/telegram/dogrula", follow_redirects=True)
    assert "Telegram bağlandı" in r.get_data(as_text=True)
    # Yanlış kod bağlamaz
    hook(raw, {"message": {"text": "/start yanliskod", "chat": {"id": 999}}})
    with app.app_context():
        assert not query_one("SELECT 1 FROM users WHERE telegram_chat_id = '999'")
    print("  link via webhook OK")


def test_reminder_has_button_and_done():
    c = Client(app)
    raw = app.test_client()
    c.post("/listeler/yeni", data={"name": "İş", "kind": "todo"})
    c.post("/listeler/1/ekle", data={"text": "Rapor", "due_date": "2026-09-25", "due_time": "11:55", "remind": "0"})
    assert raw.get("/cron/gizli/hatirlatma").json["sent"] == 1
    sent = calls("sendMessage")[-1]
    markup = json.loads(sent["reply_markup"])
    assert markup["inline_keyboard"][0][0] == {"text": "✅ Tamamlandı", "callback_data": "done:1"}

    # Butona basınca iş tamamlanır, buton "Geri al"a döner
    cq = {"id": "cb1", "data": "done:1", "from": {"id": 555}, "message": {"message_id": 77, "chat": {"id": 555}}}
    hook(raw, {"callback_query": cq})
    assert item(1)["done"] == 1
    assert calls("answerCallbackQuery")[-1]["text"] == "✅ Tamamlandı"
    edit = calls("editMessageReplyMarkup")[-1]
    assert edit["message_id"] == 77 and "undo:1" in edit["reply_markup"]
    # İkinci kez basmak sorun çıkarmaz
    hook(raw, {"callback_query": cq})
    assert item(1)["done"] == 1
    # Geri al
    hook(raw, {"callback_query": {**cq, "id": "cb2", "data": "undo:1"}})
    assert item(1)["done"] == 0 and "done:1" in calls("editMessageReplyMarkup")[-1]["reply_markup"]
    print("  button done/undo OK")


def test_callback_permissions():
    raw = app.test_client()
    with app.app_context():
        create_user("ayse", "ayse12345")
        execute("UPDATE users SET telegram_chat_id = '600' WHERE username = 'ayse'")
    # Bağlı olmayan sohbet
    hook(raw, {"callback_query": {"id": "x1", "data": "done:1", "from": {"id": 42},
                                  "message": {"message_id": 1, "chat": {"id": 42}}}})
    assert item(1)["done"] == 0 and "bağlı değil" in calls("answerCallbackQuery")[-1]["text"]
    # Başkasının özel listesindeki madde
    hook(raw, {"callback_query": {"id": "x2", "data": "done:1", "from": {"id": 600},
                                  "message": {"message_id": 2, "chat": {"id": 600}}}})
    assert item(1)["done"] == 0 and "bulunamadı" in calls("answerCallbackQuery")[-1]["text"]
    # Paylaşılan listede başka kullanıcı da tamamlayabilir
    with app.app_context():
        execute("UPDATE lists SET shared = 1 WHERE id = 1")
    hook(raw, {"callback_query": {"id": "x3", "data": "done:1", "from": {"id": 600},
                                  "message": {"message_id": 3, "chat": {"id": 600}}}})
    assert item(1)["done"] == 1
    # Bozuk veri
    assert hook(raw, {"callback_query": {"id": "x4", "data": "sil:abc", "from": {"id": 600}}}).status_code == 200
    print("  callback permissions OK")


def test_no_buttons_without_webhook():
    admin = Client(app)
    admin.post("/yonetim/telegram/webhook", data={"action": "delete"})
    with app.app_context():
        assert not tg.webhook_active()
    admin.post("/listeler/1/ekle", data={"text": "Butonsuz", "due_date": "2026-09-25", "due_time": "11:58", "remind": "0"})
    assert app.test_client().get("/cron/gizli/hatirlatma").json["sent"] == 1
    assert "reply_markup" not in calls("sendMessage")[-1]
    print("  no buttons without webhook OK")


if __name__ == "__main__":
    test_webhook_setup()
    test_webhook_security()
    test_link_via_webhook()
    test_reminder_has_button_and_done()
    test_callback_permissions()
    test_no_buttons_without_webhook()
    print("OK")
