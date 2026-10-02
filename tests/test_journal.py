"""Günlük ve ruh hali: sayfa, panodan tek dokunuş, akşam sorusu, bot komutu ve butonlar.

Çalıştır: .venv/Scripts/python tests/test_journal.py
"""
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

app = make_app()
os.environ["CRON_SECRET"] = "gizli"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.telegram as tg  # noqa: E402
import pano.todo_reminders as todo  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query_one  # noqa: E402
from pano.utils import TZ, today  # noqa: E402

CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {})) or {"message_id": 1}
NOW = [datetime(2026, 10, 5, 12, 0, tzinfo=TZ)]
todo.now_local = lambda: NOW[0]

with app.app_context():
    AYSE = create_user("ayse", "ayse12345")
    execute("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
    execute("UPDATE users SET telegram_chat_id = '200' WHERE id = ?", (AYSE,))
with app.test_request_context():
    SECRET = tg.webhook_secret()
ADMIN = Client(app)
AYSE_C = Client(app, "ayse", "ayse12345")
RAW = app.test_client()
T = today()


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def entry(day, user_id=1):
    return one("SELECT * FROM journal WHERE user_id = ? AND date = ?", (user_id, day))


def calls(method):
    return [p for m, p in CALLS if m == method]


def say(text, chat=100):
    RAW.post("/telegram/webhook", data=json.dumps({"message": {"message_id": 1, "chat": {"id": chat}, "text": text}}),
             content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})


def press(data, chat=100):
    RAW.post("/telegram/webhook", data=json.dumps({"callback_query": {
        "id": "cb", "data": data, "from": {"id": chat}, "message": {"message_id": 7, "chat": {"id": chat}}}}),
        content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})


def buttons(params):
    markup = json.loads(params.get("reply_markup") or "{}")
    return [b["callback_data"] for row in markup.get("inline_keyboard", []) for b in row]


def test_page():
    t = T.isoformat()
    ADMIN.post("/gunluk/", data={"date": t, "mood": "4", "text": "Yürüyüşe çıktım"})
    row = entry(t)
    assert (row["mood"], row["text"]) == (4, "Yürüyüşe çıktım")
    # Geleceğe yazılamaz; boş kayıt reddedilir; geçersiz ruh hali yok sayılır
    ADMIN.post("/gunluk/", data={"date": (T + timedelta(days=1)).isoformat(), "mood": "3"})
    assert entry((T + timedelta(days=1)).isoformat()) is None
    y = (T - timedelta(days=1)).isoformat()
    ADMIN.post("/gunluk/", data={"date": y})
    assert entry(y) is None
    ADMIN.post("/gunluk/", data={"date": y, "mood": "9", "text": "Dün"})
    assert entry(y)["mood"] is None and entry(y)["text"] == "Dün"
    # 1 ay önce bugün
    from pano.utils import add_months
    month_ago = add_months(T, -1).isoformat()
    ADMIN.post("/gunluk/", data={"date": month_ago, "mood": "5", "text": "Tatildeydim"})
    page = ADMIN.text("/gunluk/")
    assert "Geçmişte bugün" in page and "Tatildeydim" in page and "Yürüyüşe çıktım" in page
    assert "ortalama ruh hali 4" in page
    # Başka ay ve düzenlenecek gün
    assert "Tatildeydim" in ADMIN.text(f"/gunluk/?ay={month_ago[:7]}&gun={month_ago}")
    # Panodan tek dokunuş: metin korunur
    ADMIN.post("/gunluk/ruh-hali", data={"mood": "5", "next": "dashboard"})
    row = entry(t)
    assert (row["mood"], row["text"]) == (5, "Yürüyüşe çıktım")
    home = ADMIN.text("/")
    assert "Bugün nasıldı?" in home and 'class="habit-chip on" name="mood" value="5"' in home
    # Başkasının günlüğü görünmez; aramada çıkar
    assert "Yürüyüşe" not in AYSE_C.text("/gunluk/")
    assert "Yürüyüşe" in ADMIN.text("/ara/?q=yuruyuse") and "Yürüyüşe" not in AYSE_C.text("/ara/?q=yuruyuse")
    # Silme: Sil düğmesi kaydetme formunun içinde değil, kendi formuna bağlı (iç içe form olmasın)
    page = ADMIN.text(f"/gunluk/?gun={y}")
    save_form = page.split('<input type="hidden" name="date"', 1)[1].split("</form>", 1)[0]
    assert "<form" not in save_form and 'form="journal-delete"' in save_form
    assert f'id="journal-delete" method="post" action="/gunluk/{y}/sil"' in page
    ADMIN.post(f"/gunluk/{y}/sil")
    assert entry(y) is None
    print("  page OK")


def test_reminder():
    # Ayşe akşam 21:30'da sorulsun ister
    AYSE_C.post("/gunluk/hatirlatma", data={"on": "1", "time": "21:30"})
    assert one("SELECT journal_reminder FROM users WHERE id = ?", (AYSE,))["journal_reminder"] == "21:30"
    NOW[0] = datetime(2026, 10, 5, 21, 0, tzinfo=TZ)
    assert RAW.get("/cron/gizli/hatirlatma").json["journal_asked"] == 0
    NOW[0] = datetime(2026, 10, 5, 21, 35, tzinfo=TZ)
    # Webhook kurulu değilse butonsuz, siteye link veren mesaj
    assert RAW.get("/cron/gizli/hatirlatma").json["journal_asked"] == 1
    msg = calls("sendMessage")[-1]
    assert msg["chat_id"] == "200" and "Bugün nasıldı?" in msg["text"] and not buttons(msg) and "/gunluk" in msg["text"]
    assert RAW.get("/cron/gizli/hatirlatma").json["journal_asked"] == 0  # günde bir kez
    # Ertesi gün, webhook kuruluyken: emoji butonları
    with app.app_context():
        execute("INSERT OR REPLACE INTO app_state (key, value) VALUES ('telegram_webhook', 'https://x/telegram/webhook')")
    NOW[0] = datetime(2026, 10, 6, 22, 0, tzinfo=TZ)
    assert RAW.get("/cron/gizli/hatirlatma").json["journal_asked"] == 1
    assert buttons(calls("sendMessage")[-1]) == [f"jm:2026-10-06:{n}" for n in range(1, 6)]
    # O gün zaten yazdıysa sorulmaz
    with app.app_context():
        execute("INSERT INTO journal (user_id, date, mood) VALUES (?, '2026-10-07', 3)", (AYSE,))
    NOW[0] = datetime(2026, 10, 7, 22, 0, tzinfo=TZ)
    assert RAW.get("/cron/gizli/hatirlatma").json["journal_asked"] == 0
    # Kapatma
    AYSE_C.post("/gunluk/hatirlatma", data={"time": "21:30"})
    assert one("SELECT journal_reminder FROM users WHERE id = ?", (AYSE,))["journal_reminder"] is None
    print("  reminder OK")


def test_bot():
    t = T.isoformat()
    # Butonla ruh hali (bugün ve son 7 gün; gelecek ve çok eski reddedilir)
    press(f"jm:{t}:2", chat=200)
    assert entry(t, AYSE)["mood"] == 2
    assert "😕 Pek iyi değil" in calls("editMessageText")[-1]["text"]
    press(f"jm:{(T + timedelta(days=1)).isoformat()}:3", chat=200)
    press(f"jm:{(T - timedelta(days=30)).isoformat()}:3", chat=200)
    press(f"jm:{t}:9", chat=200)
    assert entry((T + timedelta(days=1)).isoformat(), AYSE) is None and entry(t, AYSE)["mood"] == 2
    # /gunluk metin: eklenir, başta emoji varsa ruh hali olur; ruh hali varsa buton sorulmaz
    say("/gunluk işte yoğun bir gündü", chat=200)
    say("/günlük 😄 akşam güzel geçti", chat=200)
    row = entry(t, AYSE)
    assert row["text"] == "işte yoğun bir gündü\nakşam güzel geçti" and row["mood"] == 5
    last = calls("sendMessage")[-1]
    assert "Günlüğe eklendi" in last["text"] and not buttons(last)
    # Ruh hali yokken /gunluk -> emoji butonları
    say("/gunluk", chat=100)
    with app.app_context():
        execute("DELETE FROM journal WHERE user_id = 1")
    say("/gunluk", chat=100)
    last = calls("sendMessage")[-1]
    assert "henüz yazmadın" in last["text"] and buttons(last)[0] == f"jm:{t}:1"
    # Düz yazı -> "📓 Günlük" seçeneği
    say("bugün çok yoruldum", chat=100)
    assert "tx:jr" in buttons(calls("sendMessage")[-1])
    press("tx:jr", chat=100)
    assert entry(t)["text"] == "bugün çok yoruldum"
    # Yardım metninde komut var
    say("/yardim")
    assert "/gunluk" in calls("sendMessage")[-1]["text"]
    print("  bot OK")


if __name__ == "__main__":
    test_page()
    test_reminder()
    test_bot()
    print("OK")
