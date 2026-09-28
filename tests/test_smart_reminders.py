"""Tekrarlayan işler, erteleme, fatura "Ödendi" ve ilaç "Aldım" hatırlatmaları.

Çalıştır: .venv/Scripts/python tests/test_smart_reminders.py
"""
import json
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

app = make_app()
os.environ["CRON_SECRET"] = "gizli"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.scheduled as sched  # noqa: E402
import pano.telegram as tg  # noqa: E402
import pano.todo_reminders as todo  # noqa: E402
from pano.db import execute, query, query_one  # noqa: E402
from pano.utils import TZ, today  # noqa: E402

CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {})) or {"message_id": 5}
NOW = [datetime(2026, 9, 25, 10, 0, tzinfo=TZ)]
todo.now_local = lambda: NOW[0]
sched.now_local = lambda: NOW[0]

with app.test_request_context():
    SECRET = tg.webhook_secret()
RAW = app.test_client()
C = Client(app)
with app.app_context():
    execute("UPDATE users SET telegram_chat_id = '100' WHERE username = 'admin'")
    execute("INSERT INTO app_state (key, value) VALUES ('telegram_webhook', 'https://x/telegram/webhook')")
ADMIN = 1


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def rows(sql, args=()):
    with app.app_context():
        return query(sql, args)


def cron():
    r = RAW.get("/cron/gizli/hatirlatma")
    assert r.status_code == 200, r.status_code
    return r.json


def press(data):
    cq = {"id": "c", "data": data, "from": {"id": 100}, "message": {"message_id": 5, "chat": {"id": 100}}}
    r = RAW.post("/telegram/webhook", data=json.dumps({"callback_query": cq}), content_type="application/json",
                 headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    assert r.status_code == 200


def sent():
    return [p for m, p in CALLS if m == "sendMessage"]


def keyboard(p):
    return [b for row in json.loads(p.get("reply_markup", '{"inline_keyboard": []}'))["inline_keyboard"] for b in row]


def test_next_repeat_date():
    t = today()
    with app.app_context():
        assert todo.next_repeat_date(t.isoformat(), "daily") == (t + timedelta(days=1)).isoformat()
        # 3 gün gecikmiş günlük iş bugün tamamlanınca sonraki yarın olur (geçmişe düşmez)
        assert todo.next_repeat_date((t - timedelta(days=3)).isoformat(), "daily") == (t + timedelta(days=1)).isoformat()
        assert todo.next_repeat_date(t.isoformat(), "weekly") == (t + timedelta(days=7)).isoformat()
        nxt = date.fromisoformat(todo.next_repeat_date(t.isoformat(), "weekdays"))
        assert nxt.weekday() < 5 and nxt > t
        assert todo._advance(date(2026, 9, 25), "weekdays") == date(2026, 9, 28)  # Cuma -> Pazartesi
        assert todo._advance(date(2026, 1, 31), "monthly") == date(2026, 2, 28)
    print("  next_repeat_date OK")


def test_repeat_web_and_telegram():
    C.post("/listeler/yeni", data={"name": "Ev işleri", "kind": "todo"})
    t = today()
    C.post("/listeler/1/ekle", data={"text": "Çöpü at", "due_date": t.isoformat(), "due_time": "20:00",
                                     "remind": "0", "repeat": "weekly"})
    first = one("SELECT * FROM list_items WHERE text = 'Çöpü at'")
    assert first["repeat"] == "weekly"
    assert "🔁 Her hafta" in C.text("/listeler/1")
    C.post(f"/listeler/madde/{first['id']}/isaretle")
    items = rows("SELECT * FROM list_items WHERE text = 'Çöpü at' ORDER BY id")
    assert len(items) == 2 and items[0]["done"] == 1 and items[0]["spawned_id"] == items[1]["id"]
    assert (items[1]["due_date"], items[1]["due_time"], items[1]["repeat"], items[1]["done"]) == \
        ((t + timedelta(days=7)).isoformat(), "20:00", "weekly", 0)
    # Geri alınca oluşturulan sonraki silinir
    C.post(f"/listeler/madde/{first['id']}/isaretle")
    assert len(rows("SELECT * FROM list_items WHERE text = 'Çöpü at'")) == 1
    # Telegram'dan tamamlayınca da sonraki oluşur
    press(f"done:{first['id']}")
    assert len(rows("SELECT * FROM list_items WHERE text = 'Çöpü at' AND done = 0")) == 1
    assert "sonraki eklendi" in [p for m, p in CALLS if m == "answerCallbackQuery"][-1]["text"]
    # Tarihsiz maddede tekrar kaydedilmez
    C.post("/listeler/1/ekle", data={"text": "Tarihsiz", "repeat": "daily"})
    assert one("SELECT repeat FROM list_items WHERE text = 'Tarihsiz'")["repeat"] is None
    # Düzenleme sayfasında tekrar değiştirilebilir
    current = one("SELECT * FROM list_items WHERE text = 'Çöpü at' AND done = 0")
    assert 'name="repeat"' in C.text(f"/listeler/madde/{current['id']}")
    C.post(f"/listeler/madde/{current['id']}",
           data={"text": "Çöpü at", "due_date": current["due_date"], "due_time": "20:00", "remind": "0", "repeat": "daily"})
    assert one("SELECT repeat FROM list_items WHERE id = ?", (current["id"],))["repeat"] == "daily"
    print("  repeat OK")


def test_snooze():
    C.post("/listeler/1/ekle", data={"text": "Rapor", "due_date": "2026-09-25", "due_time": "11:30", "remind": "60"})
    item = one("SELECT * FROM list_items WHERE text = 'Rapor'")
    NOW[0] = datetime(2026, 9, 25, 10, 31, tzinfo=TZ)
    cron()
    msg = [p for p in sent() if "Rapor" in p["text"]][-1]
    datas = [b["callback_data"] for b in keyboard(msg)]
    assert datas == [f"done:{item['id']}", f"snz:{item['id']}:1h", f"snz:{item['id']}:1d"]
    press(f"snz:{item['id']}:1h")
    it = one("SELECT * FROM list_items WHERE id = ?", (item["id"],))
    assert (it["due_date"], it["due_time"], it["remind_before"], it["pre_sent_at"]) == ("2026-09-25", "11:31", 0, None)
    edit = [p for m, p in CALLS if m == "editMessageReplyMarkup"][-1]
    assert "Ertelendi: 11:31" in edit["reply_markup"]
    NOW[0] = datetime(2026, 9, 25, 11, 0, tzinfo=TZ)
    before = len(sent())
    cron()
    assert len(sent()) == before  # 0 dk önce: erken mesaj yok
    NOW[0] = datetime(2026, 9, 25, 11, 32, tzinfo=TZ)
    cron()
    assert "Zamanı geldi" in sent()[-1]["text"] and "Rapor" in sent()[-1]["text"]
    press(f"snz:{item['id']}:1d")
    it = one("SELECT * FROM list_items WHERE id = ?", (item["id"],))
    assert (it["due_date"], it["due_time"], it["due_sent_at"]) == ("2026-09-26", "11:31", None)
    print("  snooze OK")


def test_bills():
    C.post("/faturalar/yeni", data={"name": "Elektrik", "amount": "845,50", "due_date": "2026-09-26",
                                    "recurring": "1", "remind": "1"})
    C.post("/faturalar/yeni", data={"name": "Sessiz", "amount": "10", "due_date": "2026-09-26"})  # remind yok
    C.post("/faturalar/yeni", data={"name": "Eski", "amount": "5", "due_date": "2026-09-20", "remind": "1"})
    bill = one("SELECT * FROM bills WHERE name = 'Elektrik'")
    assert bill["remind"] == 1 and one("SELECT remind FROM bills WHERE name = 'Sessiz'")["remind"] == 0
    NOW[0] = datetime(2026, 9, 25, 8, 0, tzinfo=TZ)
    r = cron()
    assert r["bills_sent"] == 0 and r["stale"] >= 1  # 09:00'dan önce gönderilmez; eski fatura sessizce kapanır
    assert one("SELECT due_sent_at FROM bills WHERE name = 'Eski'")["due_sent_at"]
    NOW[0] = datetime(2026, 9, 25, 9, 5, tzinfo=TZ)
    assert cron()["bills_sent"] == 1
    msg = sent()[-1]
    assert "Yarın son gün" in msg["text"] and "845,50" in msg["text"]
    assert keyboard(msg)[0]["callback_data"] == f"bill:{bill['id']}"
    assert cron()["bills_sent"] == 0
    NOW[0] = datetime(2026, 9, 26, 9, 1, tzinfo=TZ)
    assert cron()["bills_sent"] == 1 and "Bugün son gün" in sent()[-1]["text"]
    assert cron()["bills_sent"] == 0

    # ✅ Ödendi: ödenir, sonraki ay eklenir, harcama oluşur; Geri al hepsini geri alır
    press(f"bill:{bill['id']}")
    assert one("SELECT paid FROM bills WHERE id = ?", (bill["id"],))["paid"] == 1
    nxt = one("SELECT * FROM bills WHERE name = 'Elektrik' AND paid = 0")
    assert nxt["due_date"] == "2026-10-26" and nxt["remind"] == 1
    assert one("SELECT amount FROM expenses WHERE note = 'Elektrik'")["amount"] == 845.5
    press(f"billu:{bill['id']}")
    assert one("SELECT paid FROM bills WHERE id = ?", (bill["id"],))["paid"] == 0
    assert not one("SELECT 1 FROM bills WHERE id = ?", (nxt["id"],))
    assert not one("SELECT 1 FROM expenses WHERE note = 'Elektrik'")
    # Tarih değişince hatırlatmalar sıfırlanır
    C.post(f"/faturalar/{bill['id']}", data={"name": "Elektrik", "amount": "845,50", "due_date": "2026-09-30",
                                             "recurring": "1", "remind": "1"})
    b = one("SELECT pre_sent_at, due_sent_at FROM bills WHERE id = ?", (bill["id"],))
    assert (b["pre_sent_at"], b["due_sent_at"]) == (None, None)
    print("  bills OK")


def test_meds():
    C.post("/saglik/ilac/yeni", data={"name": "D vitamini", "dose": "1000 IU", "times": "09:00, 21:00", "notify": "1"})
    C.post("/saglik/ilac/yeni", data={"name": "Sessiz ilaç", "times": "09:00"})  # notify yok
    med = one("SELECT * FROM medications WHERE name = 'D vitamini'")
    assert med["notify"] == 1 and one("SELECT notify FROM medications WHERE name = 'Sessiz ilaç'")["notify"] == 0
    NOW[0] = datetime(2026, 9, 27, 8, 59, tzinfo=TZ)
    assert cron()["meds_sent"] == 0
    NOW[0] = datetime(2026, 9, 27, 9, 10, tzinfo=TZ)
    assert cron()["meds_sent"] == 1
    msg = sent()[-1]
    assert "D vitamini" in msg["text"] and "09:00" in msg["text"]
    log = one("SELECT * FROM med_logs WHERE med_id = ? AND slot = '09:00'", (med["id"],))
    assert log["date"] == "2026-09-27" and keyboard(msg)[0]["callback_data"] == f"med:{log['id']}"
    assert cron()["meds_sent"] == 0
    press(f"med:{log['id']}")
    assert one("SELECT taken_at FROM med_logs WHERE id = ?", (log["id"],))["taken_at"]
    press(f"medu:{log['id']}")
    assert one("SELECT taken_at FROM med_logs WHERE id = ?", (log["id"],))["taken_at"] is None
    # 2 saatlik pencere dışında gönderilmez
    NOW[0] = datetime(2026, 9, 27, 23, 30, tzinfo=TZ)
    assert cron()["meds_sent"] == 0
    NOW[0] = datetime(2026, 9, 27, 21, 5, tzinfo=TZ)
    assert cron()["meds_sent"] == 1
    # Web'den işaretleme (bugünün tarihi)
    C.post(f"/saglik/ilac/{med['id']}/aldim", data={"slot": "09:00"})
    assert one("SELECT taken_at FROM med_logs WHERE med_id = ? AND date = ? AND slot = '09:00'",
               (med["id"], today().isoformat()))["taken_at"]
    assert C.post(f"/saglik/ilac/{med['id']}/aldim", data={"slot": "13:00"}).status_code == 302
    h = C.text("/saglik/?tab=ilac")
    assert "Bugünkü program" in h and "Son 7 gün" in h and "🔔" in h
    print("  meds OK")


def test_yap_repeat():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pano.bot_commands as bc
    with app.app_context():
        assert bc.parse_repeat_phrase("çöpü at pazartesi her hafta") == ("çöpü at pazartesi", "weekly")
        assert bc.parse_repeat_phrase("spor HER GÜN") == ("spor", "daily")
        assert bc.parse_repeat_phrase("rapor hafta içi") == ("rapor", "weekdays")
        assert bc.parse_repeat_phrase("sadece metin") == ("sadece metin", None)
    cq = {"message": {"message_id": 1, "chat": {"id": 100}, "text": "/yap su iç 10:00 her gün"}}
    RAW.post("/telegram/webhook", data=json.dumps(cq), content_type="application/json",
             headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    item = one("SELECT * FROM list_items WHERE text = 'su iç'")
    assert item["repeat"] == "daily" and item["due_time"] == "10:00"
    assert "Her gün" in sent()[-1]["text"]
    print("  /yap repeat OK")


if __name__ == "__main__":
    test_next_repeat_date()
    test_repeat_web_and_telegram()
    test_snooze()
    test_bills()
    test_meds()
    test_yap_repeat()
    print("OK")
