"""Yapılacaklar Telegram hatırlatmaları.

Çalıştır: .venv/Scripts/python tests/test_todo_reminders.py
"""
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
from pano.utils import TZ  # noqa: E402

SENT = []
tg._call = lambda method, params=None, files=None: SENT.append((method, params)) or {"message_id": 1}

NOW = [datetime(2026, 9, 25, 10, 0, tzinfo=TZ)]
todo.now_local = lambda: NOW[0]


def run_cron(c):
    r = c.get("/cron/gizli/hatirlatma")
    assert r.status_code == 200, r.status_code
    return r.json


def messages():
    return [(p["chat_id"], p["text"]) for m, p in SENT if m == "sendMessage"]


def item(item_id):
    with app.app_context():
        return query_one("SELECT * FROM list_items WHERE id = ?", (item_id,))


def test_parsers():
    assert todo.parse_time("9:5") is None
    assert todo.parse_time("9:05") == "09:05"
    assert todo.parse_time("14.30") == "14:30"
    assert todo.parse_time("1430") == "14:30"
    assert todo.parse_time("24:00") is None and todo.parse_time("") is None
    assert todo.parse_remind("60") == 60 and todo.parse_remind("0") == 0
    assert todo.parse_remind("") is None and todo.parse_remind("7") is None
    print("  parsers OK")


def test_flow():
    c = Client(app)
    raw = app.test_client()
    with app.app_context():
        execute("UPDATE users SET telegram_chat_id = '100' WHERE username = 'admin'")
    c.post("/listeler/yeni", data={"name": "İş", "kind": "todo"})

    # Form: tarih + saat + 1 saat önce
    c.post("/listeler/1/ekle", data={"text": "Rapor <gönder>", "due_date": "2026-09-25", "due_time": "11:30", "remind": "60"})
    it = item(1)
    assert (it["due_date"], it["due_time"], it["remind_before"]) == ("2026-09-25", "11:30", 60)
    # Tarih yoksa saat/hatırlatma kaydedilmez
    c.post("/listeler/1/ekle", data={"text": "Tarihsiz", "due_time": "11:00", "remind": "60"})
    assert (item(2)["due_time"], item(2)["remind_before"]) == (None, None)
    # Liste sayfası saati ve zili gösterir
    h = c.text("/listeler/1")
    assert "🕒 11:30" in h and "🔔 1 saat önce" in h and 'name="remind"' in h

    # 10:00 — henüz erken
    assert run_cron(raw)["sent"] == 0
    # 10:31 — 1 saat öncesi penceresi: "yaklaşıyor"
    NOW[0] = datetime(2026, 9, 25, 10, 31, tzinfo=TZ)
    assert run_cron(raw)["sent"] == 1
    chat, text = messages()[-1]
    assert chat == "100" and "Yaklaşıyor" in text and "Rapor &lt;gönder&gt;" in text and "Bugün 11:30" in text, text
    assert "/listeler/1" in text
    # Tekrar çağrılınca aynı mesaj gelmez
    assert run_cron(raw)["sent"] == 0
    # 11:30 — "zamanı geldi"
    NOW[0] = datetime(2026, 9, 25, 11, 30, tzinfo=TZ)
    assert run_cron(raw)["sent"] == 1 and "Zamanı geldi" in messages()[-1][1]
    assert run_cron(raw)["sent"] == 0

    # Düzenleyince (saat değişince) yeniden gönderilebilir
    c.post("/listeler/madde/1", data={"text": "Rapor <gönder>", "due_date": "2026-09-25", "due_time": "12:00", "remind": "0"})
    it = item(1)
    assert it["due_time"] == "12:00" and it["remind_before"] == 0 and it["due_sent_at"] is None
    NOW[0] = datetime(2026, 9, 25, 11, 50, tzinfo=TZ)
    assert run_cron(raw)["sent"] == 0  # "zamanında" seçili: önceden mesaj yok
    NOW[0] = datetime(2026, 9, 25, 12, 2, tzinfo=TZ)
    assert run_cron(raw)["sent"] == 1

    # Saatsiz madde varsayılan saatte (09:00) hatırlatılır
    c.post("/listeler/1/ekle", data={"text": "Fatura öde", "due_date": "2026-09-26", "remind": "0"})
    NOW[0] = datetime(2026, 9, 26, 8, 59, tzinfo=TZ)
    assert run_cron(raw)["sent"] == 0
    NOW[0] = datetime(2026, 9, 26, 9, 1, tzinfo=TZ)
    assert run_cron(raw)["sent"] == 1 and "Fatura öde" in messages()[-1][1]

    # 1 gün önce: önceki gün aynı saatte "yaklaşıyor", mesajda "Yarın"
    c.post("/listeler/1/ekle", data={"text": "Sunum", "due_date": "2026-09-28", "due_time": "10:00", "remind": "1440"})
    NOW[0] = datetime(2026, 9, 27, 10, 5, tzinfo=TZ)
    assert run_cron(raw)["sent"] == 1 and "Yarın 10:00" in messages()[-1][1]

    # Tamamlanan maddeye mesaj gitmez
    c.post("/listeler/1/ekle", data={"text": "Bitti", "due_date": "2026-09-27", "due_time": "11:00", "remind": "0"})
    done_id = query_one_id("Bitti")
    c.post(f"/listeler/madde/{done_id}/isaretle")
    NOW[0] = datetime(2026, 9, 27, 11, 5, tzinfo=TZ)
    before = len(messages())
    run_cron(raw)
    assert len(messages()) == before

    # Cron uzun süre çalışmadıysa: 6 saatten eski "zamanı geldi" gönderilmez, sessizce kapanır
    c.post("/listeler/1/ekle", data={"text": "Eski", "due_date": "2026-09-27", "due_time": "01:00", "remind": "60"})
    old_id = query_one_id("Eski")
    NOW[0] = datetime(2026, 9, 27, 12, 0, tzinfo=TZ)
    before = len(messages())
    res = run_cron(raw)
    assert res["stale"] >= 1 and len(messages()) == before and item(old_id)["due_sent_at"] is not None
    print("  flow OK")


def query_one_id(text):
    with app.app_context():
        return query_one("SELECT id FROM list_items WHERE text = ?", (text,))["id"]


def test_recipients_and_permissions():
    c = Client(app)
    raw = app.test_client()
    with app.app_context():
        create_user("ayse", "ayse12345")
        create_user("veli", "veli12345")
        execute("UPDATE users SET telegram_chat_id = '200' WHERE username = 'ayse'")
    c.post("/listeler/yeni", data={"name": "Ev", "kind": "todo", "shared": "1"})
    c.post("/listeler/yeni", data={"name": "Özel", "kind": "todo"})
    with app.app_context():
        ev = query_one("SELECT id FROM lists WHERE name = 'Ev'")["id"]
        ozel = query_one("SELECT id FROM lists WHERE name = 'Özel'")["id"]
    ayse = Client(app, "ayse", "ayse12345")
    veli = Client(app, "veli", "veli12345")
    # Paylaşılan listede maddeyi ekleyen kişiye (Ayşe) gider
    ayse.post(f"/listeler/{ev}/ekle", data={"text": "Çiçekleri sula", "due_date": "2026-09-29", "due_time": "18:00", "remind": "0"})
    # Telegram'ı olmayan Veli'nin maddesi liste sahibine (admin) gider
    veli.post(f"/listeler/{ev}/ekle", data={"text": "Çöpü at", "due_date": "2026-09-29", "due_time": "18:00", "remind": "0"})
    NOW[0] = datetime(2026, 9, 29, 18, 1, tzinfo=TZ)
    run_cron(raw)
    got = {text.split("</b> ")[1].split("\n")[0]: chat for chat, text in messages()[-2:]}
    assert got == {"Çiçekleri sula": "200", "Çöpü at": "100"}, got

    # Kimsenin Telegram'ı yoksa gönderilmez, sayılır
    with app.app_context():
        execute("UPDATE users SET telegram_chat_id = NULL")
    c.post(f"/listeler/{ev}/ekle", data={"text": "Sessiz", "due_date": "2026-09-29", "due_time": "19:00", "remind": "0"})
    NOW[0] = datetime(2026, 9, 29, 19, 1, tzinfo=TZ)
    assert run_cron(raw)["no_telegram"] >= 1

    # Düzenleme yetkisi: ekleyen ve liste sahibi düzenler; başkası 403; erişemeyen 404
    cicek = query_one_id("Çiçekleri sula")
    assert ayse.get(f"/listeler/madde/{cicek}").status_code == 200
    assert c.get(f"/listeler/madde/{cicek}").status_code == 200
    assert veli.get(f"/listeler/madde/{cicek}").status_code == 403
    assert veli.post(f"/listeler/madde/{cicek}", data={"text": "x"}).status_code == 403
    c.post(f"/listeler/{ozel}/ekle", data={"text": "Gizli iş", "due_date": "2026-09-30"})
    gizli = query_one_id("Gizli iş")
    assert ayse.get(f"/listeler/madde/{gizli}").status_code == 404
    print("  recipients/permissions OK")


def test_cron_guards():
    raw = app.test_client()
    assert raw.get("/cron/yanlis/hatirlatma").status_code == 404
    token = os.environ.pop("TELEGRAM_BOT_TOKEN")
    assert raw.get("/cron/gizli/hatirlatma").status_code == 400
    os.environ["TELEGRAM_BOT_TOKEN"] = token
    print("  guards OK")


def test_migration_adds_columns():
    with app.app_context():
        cols = {r["name"] for r in __import__("pano.db", fromlist=["query"]).query("PRAGMA table_info(list_items)")}
    assert {"due_time", "remind_before", "pre_sent_at", "due_sent_at"} <= cols
    print("  migration OK")


if __name__ == "__main__":
    test_migration_adds_columns()
    test_parsers()
    test_flow()
    test_recipients_and_permissions()
    test_cron_guards()
    print("OK")
