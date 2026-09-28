"""Birleşik takvim, ICS aboneliği ve genel arama.

Çalıştır: .venv/Scripts/python tests/test_calendar_search.py
"""
import json
import os
import re
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

app = make_app()
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.telegram as tg  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.calendar_events import _fold, events_between  # noqa: E402
from pano.db import execute, query_one  # noqa: E402
from pano.search import search, snippet  # noqa: E402
from pano.utils import today  # noqa: E402

CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {})) or {"message_id": 1}
C = Client(app)
RAW = app.test_client()
T = today()
D = lambda n: (T + timedelta(days=n)).isoformat()  # noqa: E731


def run(sql, args=()):
    with app.app_context():
        return execute(sql, args)


def setup_data():
    run("INSERT INTO bills (user_id, name, amount, due_date) VALUES (1, 'Elektrik, su; doğalgaz', 845.5, ?)", (D(2),))
    run("INSERT INTO bills (user_id, name, amount, due_date, paid) VALUES (1, 'Ödenmiş', 10, ?, 1)", (D(3),))
    run("INSERT INTO subscriptions (user_id, name, amount, cycle, next_date) VALUES (1, 'Netflix', 229.99, 'weekly', ?)", (D(1),))
    run("INSERT INTO appointments (user_id, title, place, starts_at) VALUES (1, 'Diş', 'ADSM', ?)", (D(1) + "T14:30",))
    run("INSERT INTO lists (user_id, name, kind) VALUES (1, 'İş', 'todo')")
    run("INSERT INTO list_items (list_id, text, due_date, due_time) VALUES (1, 'Rapor gönder', ?, '09:15')", (D(0),))
    run("INSERT INTO list_items (list_id, text, due_date, done) VALUES (1, 'Bitmiş iş', ?, 1)", (D(0),))
    run("INSERT INTO vehicles (user_id, name, plate, inspection_date) VALUES (1, 'Egea', '42 ABC 1', ?)", (D(4),))
    run("INSERT INTO warranties (user_id, product, brand, warranty_until) VALUES (1, 'Şarj cihazı', 'Anker', ?)", (D(5),))
    d = T + timedelta(days=6)
    run("INSERT INTO special_days (user_id, name, kind, month, day, year) VALUES (1, 'Annem', 'birthday', ?, ?, 1966)",
        (d.month, d.day))


def test_events():
    with app.test_request_context():
        evs = events_between(1, T, T + timedelta(days=13))
    kinds = [e["kind"] for e in evs]
    assert kinds.count("sub") == 2, kinds  # haftalık abonelik 2 hafta içinde 2 kez
    titles = {e["title"] for e in evs}
    assert {"Elektrik, su; doğalgaz son gün", "Diş", "Rapor gönder", "Egea muayene", "Şarj cihazı garantisi bitiyor",
            "Annem"} <= titles
    assert "Bitmiş iş" not in titles  # tamamlanan yapılacak takvimde yok
    assert next(e for e in evs if e["title"] == "Ödenmiş son gün")["done"] is True
    appt = next(e for e in evs if e["kind"] == "appt")
    assert appt["time"] == "14:30" and appt["date"] == T + timedelta(days=1)
    assert [e["date"] for e in evs] == sorted(e["date"] for e in evs)
    print("  events OK")


def test_calendar_page():
    h = C.text("/takvim/")
    assert "month-grid" in h and "Rapor gönder" in h and "🕒 09:15" in h and f'id="g-{T.isoformat()}"' in h
    nxt = C.text(f"/takvim/?ay={(T.replace(day=1) + timedelta(days=32)).strftime('%Y-%m')}")
    assert "month-grid" in nxt
    assert "📅" in C.text("/menu") and 'href="/ara/"' in C.text("/menu")
    print("  calendar page OK")


def test_ics_feed():
    assert RAW.get("/takvim/yanlisanahtaryanlisanahtar.ics").status_code == 404
    C.post("/ayarlar/takvim")
    token = query_one_value("SELECT calendar_token FROM users WHERE id = 1")
    assert token and len(token) >= 20
    assert "webcal://" in C.text("/ayarlar/")
    r = RAW.get(f"/takvim/{token}.ics")  # giriş gerektirmez
    assert r.status_code == 200 and r.mimetype == "text/calendar"
    ics = r.get_data().decode("utf-8")
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.rstrip().endswith("END:VCALENDAR")
    unfolded = ics.replace("\r\n ", "")
    assert "SUMMARY:🧾 Elektrik\\, su\\; doğalgaz son gün" in unfolded
    tomorrow = (T + timedelta(days=1)).strftime("%Y%m%d")
    assert f"DTSTART:{tomorrow}T113000Z" in unfolded  # 14:30 İstanbul = 11:30 UTC
    in_two = (T + timedelta(days=2)).strftime("%Y%m%d")
    assert f"DTSTART;VALUE=DATE:{in_two}" in unfolded  # saatsiz fatura: tüm gün
    assert f"DTSTART:{T.strftime('%Y%m%d')}T061500Z" in unfolded  # 09:15 yapılacak
    assert "URL:http://localhost/listeler/1" in unfolded
    assert all(len(line.encode("utf-8")) <= 75 for line in ics.split("\r\n")), "satır katlama"
    assert unfolded.count("BEGIN:VEVENT") >= 8
    # Yeni adres eskisini geçersiz kılar; kapatınca hiçbiri çalışmaz
    C.post("/ayarlar/takvim")
    new_token = query_one_value("SELECT calendar_token FROM users WHERE id = 1")
    assert new_token != token and RAW.get(f"/takvim/{token}.ics").status_code == 404
    assert RAW.get(f"/takvim/{new_token}.ics").status_code == 200
    C.post("/ayarlar/takvim", data={"action": "revoke"})
    assert RAW.get(f"/takvim/{new_token}.ics").status_code == 404
    print("  ics feed OK")


def test_fold():
    line = "SUMMARY:" + "ğ" * 60
    folded = _fold(line)
    assert all(len(p.encode("utf-8")) <= 75 for p in folded.split("\r\n"))
    assert folded.replace("\r\n ", "") == line
    print("  fold OK")


def query_one_value(sql):
    with app.app_context():
        return query_one(sql)[0]


def test_search():
    run("INSERT INTO notes (user_id, title, content) VALUES (1, 'Ev', 'Şarj aleti balkondaki mavi kutuda duruyor')")
    run("INSERT INTO inventory (user_id, name, location) VALUES (1, 'Matkap', 'Balkon dolabı')")
    with app.app_context():
        create_user("veli", "veli12345")
        execute("INSERT INTO notes (user_id, title, content) VALUES (2, 'Veli', 'şarj gizli')")
    with app.test_request_context():
        groups = {g["label"]: g for g in search(1, "SARJ")}
        assert set(groups) >= {"Notlar", "Garanti"} and groups["Notlar"]["count"] == 1  # Veli'nin notu yok
        only = search(1, "balkon matkap")
        assert [g["label"] for g in only] == ["Ev Envanteri"]
        assert search(1, "") == [] and search(1, "yokboylebirsey") == []
    long = "a " * 50 + "hedef kelime burada " + "b " * 50
    snip = snippet(long, ["hedef"])
    assert snip.startswith("…") and "hedef" in snip and snip.endswith("…")
    h = C.text("/ara/?q=şarj")
    assert "Şarj cihazı" in h and "Şarj aleti" in h and "sonuç" in h
    assert "bulunamadı" in C.text("/ara/?q=zzzz")
    assert 'class="search-link"' in C.text("/")
    # Bot /ara
    run("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
    with app.test_request_context():
        secret = tg.webhook_secret()
    RAW.post("/telegram/webhook", data=json.dumps({"message": {"message_id": 1, "chat": {"id": 100}, "text": "/ara matkap"}}),
             content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": secret})
    text = [p["text"] for m, p in CALLS if m == "sendMessage"][-1]
    assert "Matkap" in text and "Ev Envanteri" in text and re.search(r'href="http://localhost/envanter/\d+"', text)
    print("  search OK")


if __name__ == "__main__":
    setup_data()
    test_events()
    test_calendar_page()
    test_ics_feed()
    test_fold()
    test_search()
    print("OK")
