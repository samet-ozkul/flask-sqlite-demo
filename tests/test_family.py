"""Aile: paylaşılan etkinlikler (ortak takvim) ve iş atama.

Çalıştır: .venv/Scripts/python tests/test_family.py
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
from pano.db import execute, query, query_one  # noqa: E402
from pano.utils import TZ, today  # noqa: E402

CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {})) or {"message_id": 1}
NOW = [datetime(2026, 10, 1, 10, 0, tzinfo=TZ)]
todo.now_local = lambda: NOW[0]

with app.app_context():
    AYSE = create_user("ayse", "ayse12345")
    VELI = create_user("veli", "veli12345")
    execute("UPDATE users SET display_name = 'Ayşe', telegram_chat_id = '200' WHERE id = ?", (AYSE,))
    execute("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
with app.test_request_context():
    SECRET = tg.webhook_secret()
ADMIN = Client(app)
AYSE_C = Client(app, "ayse", "ayse12345")
VELI_C = Client(app, "veli", "veli12345")
RAW = app.test_client()


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def rows(sql, args=()):
    with app.app_context():
        return query(sql, args)


def sent():
    return [(p["chat_id"], p["text"]) for m, p in CALLS if m == "sendMessage"]


def cron():
    r = RAW.get("/cron/gizli/hatirlatma")
    assert r.status_code == 200, r.status_code
    return r.json


def test_events():
    ADMIN.post("/etkinlikler/yeni", data={"title": "Annemlerde yemek", "date": "2026-10-03", "time": "19:00",
                                          "place": "Meram", "remind": "60", "shared": "1"})
    ADMIN.post("/etkinlikler/yeni", data={"title": "Özel iş", "date": "2026-10-03", "remind": "0"})  # paylaşılmayan
    ADMIN.post("/etkinlikler/yeni", data={"title": "", "date": "2026-10-03"})  # başlıksız reddedilir
    shared = one("SELECT * FROM events WHERE title = 'Annemlerde yemek'")
    private = one("SELECT * FROM events WHERE title = 'Özel iş'")
    assert (shared["shared"], shared["time"], shared["remind_before"]) == (1, "19:00", 60)
    assert private["shared"] == 0 and one("SELECT COUNT(*) AS n FROM events")["n"] == 2
    # Görünürlük: paylaşılan herkese, özel sadece sahibine
    assert "Annemlerde yemek" in AYSE_C.text("/etkinlikler/") and "Özel iş" not in AYSE_C.text("/etkinlikler/")
    assert AYSE_C.get(f"/etkinlikler/{private['id']}").status_code == 404
    assert "sadece ekleyen" in AYSE_C.text(f"/etkinlikler/{shared['id']}")
    assert AYSE_C.post(f"/etkinlikler/{shared['id']}", data={"title": "x", "date": "2026-10-03"}).status_code == 403
    assert AYSE_C.post(f"/etkinlikler/{shared['id']}/sil").status_code == 403
    # Takvim, arama ve panoda görünür
    assert "Annemlerde yemek" in AYSE_C.text("/takvim/?ay=2026-10")
    assert "Annemlerde yemek" in AYSE_C.text("/ara/?q=annem")
    # Hatırlatma: 1 saat önce ve zamanında; paylaşılan -> Telegram'ı bağlı herkes, özel -> sadece sahibi
    NOW[0] = datetime(2026, 10, 3, 18, 5, tzinfo=TZ)
    assert cron().get("events_sent") == 2  # admin + ayşe
    chats = {c for c, t in sent()[-2:]}
    assert chats == {"100", "200"} and all("Yaklaşıyor" in t for c, t in sent()[-2:])
    NOW[0] = datetime(2026, 10, 3, 19, 1, tzinfo=TZ)
    assert cron().get("events_sent") == 2
    assert cron().get("events_sent") == 0
    # Özel etkinlik (saat yok, 09:00'da) sadece sahibine gitmişti
    NOW[0] = datetime(2026, 10, 3, 9, 1, tzinfo=TZ)
    execute_reset = "UPDATE events SET due_sent_at = NULL, pre_sent_at = NULL WHERE id = ?"
    with app.app_context():
        execute(execute_reset, (private["id"],))
        execute("UPDATE events SET remind_before = NULL WHERE id = ?", (shared["id"],))
    before = len(sent())
    cron()
    assert [c for c, t in sent()[before:]] == ["100"]
    print("  events OK")


def test_assignment():
    ADMIN.post("/listeler/yeni", data={"name": "Ev işleri", "kind": "todo", "shared": "1"})
    ADMIN.post("/listeler/yeni", data={"name": "Özel", "kind": "todo"})
    ev = one("SELECT id FROM lists WHERE name = 'Ev işleri'")["id"]
    ozel = one("SELECT id FROM lists WHERE name = 'Özel'")["id"]
    assert 'name="assignee"' in ADMIN.text(f"/listeler/{ev}") and 'name="assignee"' not in ADMIN.text(f"/listeler/{ozel}")
    before = len(sent())
    ADMIN.post(f"/listeler/{ev}/ekle", data={"text": "Çöpü at", "due_date": "2026-10-05", "due_time": "20:00",
                                             "remind": "0", "assignee": str(AYSE)})
    item = one("SELECT * FROM list_items WHERE text = 'Çöpü at'")
    assert item["assignee_id"] == AYSE
    new = sent()[before:]
    assert new and new[-1][0] == "200" and "sana bir iş atadı" in new[-1][1] and "Çöpü at" in new[-1][1]
    # Kendine atamada bildirim yok; özel listede atama yok sayılır
    before = len(sent())
    ADMIN.post(f"/listeler/{ev}/ekle", data={"text": "Kendi işim", "assignee": "1"})
    ADMIN.post(f"/listeler/{ozel}/ekle", data={"text": "Özel iş", "assignee": str(AYSE)})
    assert len(sent()) == before and one("SELECT assignee_id FROM list_items WHERE text = 'Özel iş'")["assignee_id"] is None
    # Geçersiz kullanıcı yok sayılır
    ADMIN.post(f"/listeler/{ev}/ekle", data={"text": "Hayalet", "assignee": "999"})
    assert one("SELECT assignee_id FROM list_items WHERE text = 'Hayalet'")["assignee_id"] is None
    # Ayşe: "Bana atananlar"da görür, listede "➡️ Ayşe" rozeti
    assert "Bana atananlar" in AYSE_C.text("/listeler/") and "Çöpü at" in AYSE_C.text("/listeler/")
    assert "➡️ Ayşe" in ADMIN.text(f"/listeler/{ev}")
    # Hatırlatma atanan kişiye gider
    NOW[0] = datetime(2026, 10, 5, 20, 1, tzinfo=TZ)
    before = len(sent())
    cron()
    assert [c for c, t in sent()[before:] if "Çöpü at" in t] == ["200"]
    # Düzenlemede başkasına atama -> yeni kişiye bildirim
    with app.app_context():
        execute("UPDATE users SET telegram_chat_id = '300' WHERE id = ?", (VELI,))
    before = len(sent())
    ADMIN.post(f"/listeler/madde/{item['id']}", data={"text": "Çöpü at", "due_date": "2026-10-06", "remind": "0",
                                                      "assignee": str(VELI)})
    assert one("SELECT assignee_id FROM list_items WHERE id = ?", (item["id"],))["assignee_id"] == VELI
    assert [c for c, t in sent()[before:]] == ["300"]
    # Başkasına atanan iş benim panomda çıkmaz
    t = today().isoformat()
    ADMIN.post(f"/listeler/{ev}/ekle", data={"text": "Veliye bugün", "due_date": t, "assignee": str(VELI)})
    ADMIN.post(f"/listeler/{ev}/ekle", data={"text": "Bana bugün", "due_date": t})
    ADMIN.text("/listeler/")  # "… eklendi" bildirimleri burada gösterilsin, panoda görünüp testi yanıltmasın
    home = ADMIN.text("/")
    assert "Bana bugün" in home and "Veliye bugün" not in home
    assert "Veliye bugün" in VELI_C.text("/")
    print("  assignment OK")


def test_bot():
    def say(text):
        RAW.post("/telegram/webhook", data=json.dumps({"message": {"message_id": 1, "chat": {"id": 100}, "text": text}}),
                 content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    say("/yap bulaşıkları yıka yarın 21:00 @ayse")
    item = one("SELECT i.*, l.shared FROM list_items i JOIN lists l ON l.id = i.list_id WHERE i.text = 'bulaşıkları yıka'")
    assert item and item["assignee_id"] == AYSE and item["shared"] == 1  # başkasına atanan iş paylaşılan listeye
    assert any("➡️ Ayşe" in t for c, t in sent() if c == "100")
    say("/etkinlik piknik pazar 11:00")
    ev = one("SELECT * FROM events WHERE title = 'piknik'")
    assert ev and ev["shared"] == 1 and ev["time"] == "11:00" and ev["remind_before"] == 0
    say("/etkinlik")
    assert "Örnek" in sent()[-1][1]
    print("  bot OK")


if __name__ == "__main__":
    test_events()
    test_assignment()
    test_bot()
    print("OK")
