"""Zaman takibi: sayaç (tek çalışan), elle kayıt ve süre ayrıştırma, düzenleme/silme, erişim, rapor,
kazanç, CSV, Telegram komutları, unutulan sayaç uyarısı ve çöp kutusu.

Saat taklit edilir (todo_reminders.now_local); Telegram çağrılmaz.
Çalıştır: .venv/Scripts/python tests/test_timetrack.py
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
from pano.modules import timetrack as tt  # noqa: E402
from pano.utils import TZ  # noqa: E402

CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {})) or {"message_id": 1}
NOW = [datetime(2026, 10, 7, 10, 0, tzinfo=TZ)]  # Çarşamba; hafta 5–11 Ekim
todo.now_local = lambda: NOW[0]

with app.app_context():
    AYSE = create_user("ayse", "ayse12345")
    MEHMET = create_user("mehmet", "mehmet12345")
    execute("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
    execute("INSERT INTO app_state (key, value) VALUES ('telegram_webhook', 'https://x/telegram/webhook')")
with app.test_request_context():
    SECRET = tg.webhook_secret()
ADMIN = Client(app)
AYSE_C = Client(app, "ayse", "ayse12345")
MEHMET_C = Client(app, "mehmet", "mehmet12345")
RAW = app.test_client()


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def rows(sql, args=()):
    with app.app_context():
        return query(sql, args)


def run(sql, args=()):
    with app.app_context():
        execute(sql, args)


def sent():
    return [p["text"] for m, p in CALLS if m == "sendMessage"]


def last_buttons():
    params = [p for m, p in CALLS if m == "sendMessage"][-1]
    if "reply_markup" not in params:
        return []
    return [b["callback_data"] for row in json.loads(params["reply_markup"])["inline_keyboard"] for b in row]


def callback_texts():
    return [p.get("text", "") for m, p in CALLS if m == "answerCallbackQuery"]


def say(text):
    RAW.post("/telegram/webhook", data=json.dumps({"message": {"message_id": 1, "chat": {"id": 100}, "text": text}}),
             content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})


def press(data):
    RAW.post("/telegram/webhook", data=json.dumps({"callback_query": {
        "id": "cb", "data": data, "from": {"id": 100}, "message": {"message_id": 7, "chat": {"id": 100}}}}),
        content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})


def utc(local_text):
    """'2026-10-05 00:30' (yerel) -> UTC metin."""
    return tt.to_db(datetime.fromisoformat(local_text).replace(tzinfo=TZ))


def running_count(uid=1):
    return one("SELECT COUNT(*) AS n FROM time_entries WHERE user_id = ? AND ended_at IS NULL", (uid,))["n"]


def test_parse():
    p = tt.parse_duration
    assert p("1:30") == 5400 and p("90 dk") == 5400 and p("90dk") == 5400 and p("90 dakika") == 5400
    assert p("2 saat") == 7200 and p("2 SAAT") == 7200 and p("2s") == 7200 and p("1,5 sa") == 5400
    assert p("1 sa 15 dk") == 4500 and p("1 saat 30") == 5400 and p("45") == 2700
    assert p("") is None and p("abc") is None and p("1,5") is None and p("2 gün") is None
    assert tt.fmt_duration(12300) == "3 sa 25 dk" and tt.fmt_duration(3600) == "1 sa" and tt.fmt_duration(59) == "0 dk"
    assert tt.fmt_duration(1500) == "25 dk" and tt.fmt_clock(3725) == "1:02:05"
    print("  parse OK")


def test_projects():
    ADMIN.post("/zaman/projeler/yeni", data={"name": "Web  sitesi", "color": "blue", "hourly_rate": "500"})
    ADMIN.post("/zaman/projeler/yeni", data={"name": "Danışmanlık", "color": "kotu", "hourly_rate": ""})
    r = ADMIN.post("/zaman/projeler/yeni", data={"name": "web SİTESİ"}, follow_redirects=True)  # aynı ad
    assert "zaten var" in r.get_data(as_text=True)
    ADMIN.post("/zaman/projeler/yeni", data={"name": "Eksi", "hourly_rate": "-5"})  # geçersiz ücret
    ADMIN.post("/zaman/projeler/yeni", data={"name": ""})
    assert one("SELECT COUNT(*) AS n FROM time_projects")["n"] == 2
    web = one("SELECT * FROM time_projects WHERE name = 'Web sitesi'")
    assert web["color"] == "blue" and web["hourly_rate"] == 500
    dan = one("SELECT * FROM time_projects WHERE name = 'Danışmanlık'")
    assert dan["color"] == "" and dan["hourly_rate"] is None
    page = ADMIN.text("/zaman/projeler")
    assert "🔵" in page and "500 ₺ / saat" in page
    # Düzenleme ve arşiv
    ADMIN.post(f"/zaman/proje/{dan['id']}", data={"name": "Danışmanlık", "color": "green", "hourly_rate": "1.000"})
    assert one("SELECT hourly_rate, color FROM time_projects WHERE id = ?", (dan["id"],))["hourly_rate"] == 1000
    ADMIN.post(f"/zaman/proje/{dan['id']}/arsiv")
    assert one("SELECT archived FROM time_projects WHERE id = ?", (dan["id"],))["archived"] == 1
    ADMIN.text("/zaman/projeler")  # "arşivlendi" bildirimi sayaç sayfasını yanıltmasın
    assert "Danışmanlık" not in ADMIN.text("/zaman/")  # arşivdeki proje sayaçta seçilmez
    ADMIN.post(f"/zaman/proje/{dan['id']}/arsiv")
    assert "🟢 Danışmanlık" in ADMIN.text("/zaman/")
    # Başkası göremez, değiştiremez
    assert "500 ₺ / saat" not in AYSE_C.text("/zaman/projeler") and "Danışmanlık" not in AYSE_C.text("/zaman/")
    for url in (f"/zaman/proje/{web['id']}", f"/zaman/proje/{web['id']}/arsiv", f"/zaman/proje/{web['id']}/sil"):
        assert AYSE_C.post(url, data={"name": "x"}).status_code == 404
    assert AYSE_C.get(f"/zaman/proje/{web['id']}").status_code == 404
    print("  projects OK")


def test_timer():
    web = one("SELECT id FROM time_projects WHERE name = 'Web sitesi'")["id"]
    NOW[0] = datetime(2026, 10, 7, 9, 0, tzinfo=TZ)
    ADMIN.post("/zaman/baslat", data={"project_id": str(web), "note": "ana sayfa"})
    first = one("SELECT * FROM time_entries WHERE user_id = 1 AND ended_at IS NULL")
    assert first["project_id"] == web and first["note"] == "ana sayfa" and first["started_at"] == "2026-10-07 06:00:00"
    NOW[0] = datetime(2026, 10, 7, 9, 45, 30, tzinfo=TZ)
    page = ADMIN.text("/zaman/")
    assert 'data-started="' in page and "0:45:30" in page and "timetrack.js" in page and "⏹️ Durdur" in page
    # Yenisi başlarken önceki durur: aynı anda tek çalışan sayaç
    r = ADMIN.post("/zaman/baslat", data={"project_id": "", "note": "e-posta"}, follow_redirects=True)
    assert "önceki sayaç durdu: 45 dk" in r.get_data(as_text=True)
    assert running_count() == 1
    assert one("SELECT ended_at FROM time_entries WHERE id = ?", (first["id"],))["ended_at"] == "2026-10-07 06:45:30"
    second = one("SELECT * FROM time_entries WHERE user_id = 1 AND ended_at IS NULL")
    assert second["project_id"] is None and second["note"] == "e-posta"
    NOW[0] = datetime(2026, 10, 7, 10, 0, tzinfo=TZ)
    r = ADMIN.post("/zaman/durdur", follow_redirects=True)
    assert "Sayaç durdu: 14 dk · Projesiz" in r.get_data(as_text=True) and running_count() == 0
    assert "Çalışan sayaç yok" in ADMIN.post("/zaman/durdur", follow_redirects=True).get_data(as_text=True)
    page = ADMIN.text("/zaman/")
    assert "timetrack.js" not in page and "▶️ Başlat" in page and "ana sayfa" in page and "45 dk" in page and "1 sa" in page
    # Aynı işe devam
    ADMIN.post(f"/zaman/{first['id']}/devam")
    again = one("SELECT * FROM time_entries WHERE user_id = 1 AND ended_at IS NULL")
    assert again["project_id"] == web and again["note"] == "ana sayfa"
    ADMIN.post("/zaman/durdur")
    # Erişim: başkasının projesiyle başlatılamaz, kaydı görülemez/değiştirilemez
    assert AYSE_C.post("/zaman/baslat", data={"project_id": str(web)}).status_code == 404
    assert running_count(AYSE) == 0
    for url in (f"/zaman/{first['id']}", f"/zaman/{first['id']}/sil", f"/zaman/{first['id']}/devam"):
        assert AYSE_C.post(url, data={"start": "08:00", "end": "09:00"}).status_code == 404
    assert AYSE_C.get(f"/zaman/{first['id']}").status_code == 404
    assert "ana sayfa" not in AYSE_C.text("/zaman/") and "ana sayfa" not in AYSE_C.text("/zaman/rapor")
    print("  timer OK")


def test_manual_and_edit():
    web = one("SELECT id FROM time_projects WHERE name = 'Web sitesi'")["id"]
    run("DELETE FROM time_entries")
    NOW[0] = datetime(2026, 10, 7, 15, 0, tzinfo=TZ)

    def add(**data):
        before = one("SELECT COALESCE(MAX(id), 0) AS m FROM time_entries")["m"]
        r = ADMIN.post("/zaman/yeni", data={"date": "2026-10-06", "project_id": str(web), **data}, follow_redirects=True)
        return one("SELECT * FROM time_entries WHERE id > ?", (before,)), r.get_data(as_text=True)

    e, _ = add(start="09:00", end="10:30", note="tasarım")
    assert (e["started_at"], e["ended_at"]) == ("2026-10-06 06:00:00", "2026-10-06 07:30:00")
    e, _ = add(start="13:00", duration="90 dk")
    assert e["ended_at"] == "2026-10-06 11:30:00"
    e, _ = add(end="18:00", duration="1:30")
    assert e["started_at"] == "2026-10-06 13:30:00"
    e, _ = add(duration="2 saat")  # geçmiş gün, sadece süre: 09:00'da başlar
    assert (e["started_at"], e["ended_at"]) == ("2026-10-06 06:00:00", "2026-10-06 08:00:00")
    e, _ = add(date="2026-10-07", duration="45")  # bugün, sadece süre: şu anda biter
    assert (e["started_at"], e["ended_at"]) == ("2026-10-07 11:15:00", "2026-10-07 12:00:00")
    # Gece yarısı geçişi: 23:00 – 01:00 ertesi güne biter, başladığı güne yazılır
    e, _ = add(start="23:00", end="01:00", note="gece")
    assert (e["started_at"], e["ended_at"]) == ("2026-10-06 20:00:00", "2026-10-06 22:00:00")
    assert tt.fmt_duration(7200) in ADMIN.text("/zaman/rapor") and "(+1 gün)" in ADMIN.text("/zaman/rapor")
    # Hatalı girişler
    count = one("SELECT COUNT(*) AS n FROM time_entries")["n"]
    for data, message in ((dict(start="09:00"), "süreyi yaz"), (dict(duration="abc"), "anlayamadım"),
                          (dict(start="09:00", duration="25 saat"), "en fazla 24 saat"),
                          (dict(date="2026-10-08", start="09:00", end="10:00"), "İleri bir zaman"),
                          (dict(start="09:00", end="09:00"), "sıfırdan uzun")):
        _, page = add(**data)
        assert message in page, (data, message)
    assert one("SELECT COUNT(*) AS n FROM time_entries")["n"] == count

    # Düzenleme: değişmeyen saatin saniyesi korunur
    run("UPDATE time_entries SET started_at = '2026-10-06 06:00:17', ended_at = '2026-10-06 07:30:42' WHERE note = 'tasarım'")
    e = one("SELECT * FROM time_entries WHERE note = 'tasarım'")
    page = ADMIN.text(f"/zaman/{e['id']}")
    assert 'value="09:00"' in page and 'value="10:30"' in page and 'value="2026-10-06"' in page
    ADMIN.post(f"/zaman/{e['id']}", data={"date": "2026-10-06", "start": "09:00", "end": "11:00", "project_id": "",
                                          "note": "tasarım 2"})
    e = one("SELECT * FROM time_entries WHERE id = ?", (e["id"],))
    assert (e["started_at"], e["ended_at"], e["project_id"], e["note"]) == \
        ("2026-10-06 06:00:17", "2026-10-06 08:00:00", None, "tasarım 2")
    # Bitiş boşaltılıp süre yazılırsa süre kullanılır; hatalıysa değişmez
    ADMIN.post(f"/zaman/{e['id']}", data={"date": "2026-10-06", "start": "09:00", "end": "", "duration": "2 saat"})
    assert one("SELECT ended_at FROM time_entries WHERE id = ?", (e["id"],))["ended_at"] == "2026-10-06 08:00:17"
    r = ADMIN.post(f"/zaman/{e['id']}", data={"date": "2026-10-06", "start": "09:00", "end": "", "duration": "x"},
                   follow_redirects=True)
    assert "anlayamadım" in r.get_data(as_text=True)
    # Raporda düzenleye basıp kaydedince rapora dönülür
    r = ADMIN.post(f"/zaman/{e['id']}", data={"date": "2026-10-06", "start": "09:00", "end": "11:00",
                                              "next": "/zaman/rapor?aralik=ay"})
    assert r.headers["Location"].endswith("/zaman/rapor?aralik=ay")
    r = ADMIN.post(f"/zaman/{e['id']}", data={"date": "2026-10-06", "start": "09:00", "end": "11:00",
                                              "next": "https://kotu.example/"})
    assert r.headers["Location"].endswith("/zaman/")

    # Çalışan sayaç düzenlenirken sadece başlangıç değişir; uyarı bayrağı sıfırlanır
    ADMIN.post("/zaman/baslat", data={"note": "uzun"})
    cur = one("SELECT * FROM time_entries WHERE ended_at IS NULL")
    run("UPDATE time_entries SET long_warned = 1 WHERE id = ?", (cur["id"],))
    assert "bitiş durdurunca yazılır" in ADMIN.text(f"/zaman/{cur['id']}")
    ADMIN.post(f"/zaman/{cur['id']}", data={"date": "2026-10-07", "start": "14:00", "note": "uzun iş"})
    cur = one("SELECT * FROM time_entries WHERE id = ?", (cur["id"],))
    assert (cur["started_at"], cur["ended_at"], cur["long_warned"], cur["note"]) == \
        ("2026-10-07 11:00:00", None, 0, "uzun iş")
    r = ADMIN.post(f"/zaman/{cur['id']}", data={"date": "2026-10-07", "start": "16:00"}, follow_redirects=True)
    assert "ileri bir saat" in r.get_data(as_text=True)

    # Silme: çöp kutusuna gider; çalışan sayaç silinirken durdurulur
    ADMIN.post(f"/zaman/{cur['id']}/sil")
    assert one("SELECT 1 x FROM time_entries WHERE id = ?", (cur["id"],)) is None and running_count() == 0
    trash_row = one("SELECT * FROM trash WHERE module = 'timetrack' ORDER BY id DESC")
    assert "uzun iş" not in trash_row["label"] and "1 sa" in trash_row["label"]
    ADMIN.post(f"/cop-kutusu/{trash_row['id']}/geri")
    back = one("SELECT * FROM time_entries WHERE id = ?", (cur["id"],))
    assert back["ended_at"] == "2026-10-07 12:00:00" and running_count() == 0
    print("  manual/edit OK")


def test_report_and_csv():
    with app.app_context():
        a = execute("INSERT INTO time_projects (user_id, name, color, hourly_rate) VALUES (?, 'Müşteri A', 'red', 200)",
                    (MEHMET,)).lastrowid
        b = execute("INSERT INTO time_projects (user_id, name, color) VALUES (?, 'İç iş', 'green')", (MEHMET,)).lastrowid
    data = [
        (None, "pazar gecesi", "2026-10-04 23:30", "2026-10-05 00:30"),   # geçen haftaya (başladığı gün) yazılır
        (a, "pazartesi", "2026-10-05 00:30", "2026-10-05 02:00"),          # 1,5 sa — haftanın ilk dakikaları
        (b, "=1+1", "2026-10-06 09:00", "2026-10-06 11:15"),               # 2 sa 15 dk
        (a, "çalışıyor", "2026-10-07 08:00", None),                        # şu ana kadar: 2 sa
        (None, "gelecek hafta", "2026-10-12 00:10", "2026-10-12 01:00"),   # 50 dk, bu haftaya girmez
        (a, "eylül", "2026-09-30 10:00", "2026-09-30 11:00"),              # geçen ay: 1 sa
    ]
    for project, note, start, end in data:
        run("INSERT INTO time_entries (user_id, project_id, note, started_at, ended_at) VALUES (?, ?, ?, ?, ?)",
            (MEHMET, project, note, utc(start), utc(end) if end else None))
    NOW[0] = datetime(2026, 10, 7, 10, 0, tzinfo=TZ)
    with app.test_request_context():
        week = tt.summarize(tt.entries_between(MEHMET, tt.parse_date("2026-10-05"), tt.parse_date("2026-10-12")), NOW[0])
    assert week["total"] == 5 * 3600 + 45 * 60 and week["count"] == 3 and week["earning"] == 700
    assert [(p["name"], p["seconds"], p["earning"]) for p in week["projects"]] == \
        [("Müşteri A", 3.5 * 3600, 700), ("İç iş", 2.25 * 3600, None)]

    page = MEHMET_C.text("/zaman/rapor")  # varsayılan: bu hafta
    assert "5 Eki – 11 Eki" in page and "5 sa 45 dk" in page and "700 ₺" in page
    assert "🔴 Müşteri A" in page and "3 sa 30 dk · 700 ₺" in page and "2 sa 15 dk" in page
    assert "Gün gün" in page and "5 Eki, Pzt" in page and "11 Eki, Paz" in page
    assert "pazar gecesi" not in page and "gelecek hafta" not in page and "çalışıyor" in page
    page = MEHMET_C.text("/zaman/rapor?aralik=ay")
    assert "Ekim 2026" in page and "7 sa 35 dk" in page and "Gün gün" not in page and "gelecek hafta" in page
    page = MEHMET_C.text("/zaman/rapor?aralik=gecen-ay")
    assert "Eylül 2026" in page and "1 sa" in page and "200 ₺" in page and "pazartesi" not in page
    page = MEHMET_C.text("/zaman/rapor?aralik=ozel&bas=2026-10-04&bit=2026-10-04")
    assert "pazar gecesi" in page and "pazartesi" not in page and "Gün gün" in page
    page = MEHMET_C.text("/zaman/rapor?aralik=ozel&bas=2026-10-06&bit=2026-10-05")  # ters sıra düzeltilir
    assert "pazartesi" in page and "=1+1" in page and "çalışıyor" not in page
    assert "5 Eki – 11 Eki" in MEHMET_C.text("/zaman/rapor?aralik=ozel&bas=yanlis")  # geçersizse bu hafta
    # Ana sayfa: bugün ve bu hafta (çalışan sayaç dahil)
    page = MEHMET_C.text("/zaman/")
    assert "0:00:00" not in page and "2:00:00" in page and "5 sa 45 dk" in page and "400 ₺" in page

    # CSV: Harcamalar'daki biçim
    r = MEHMET_C.get("/zaman/disa-aktar?aralik=hafta")
    body = r.get_data(as_text=True)
    assert r.mimetype == "text/csv" and "zaman-2026-10-05_2026-10-11.csv" in r.headers["Content-Disposition"]
    lines = body.split("\r\n")
    assert lines[0] == "﻿Tarih;Başlangıç;Bitiş;Süre (saat);Proje;Not;Tutar (TL)"
    assert lines[1] == "05.10.2026;00:30;02:00;1,50;Müşteri A;pazartesi;300,00"
    assert lines[2] == "06.10.2026;09:00;11:15;2,25;İç iş;'=1+1;"
    assert lines[3] == "07.10.2026;08:00;;2,00;Müşteri A;çalışıyor;400,00" and lines[4] == ""
    r = MEHMET_C.get("/zaman/disa-aktar?aralik=gecen-ay")
    assert "zaman-2026-09.csv" in r.headers["Content-Disposition"] and "eylül" in r.get_data(as_text=True)
    assert "pazartesi" not in ADMIN.get("/zaman/disa-aktar?aralik=hafta").get_data(as_text=True)
    print("  report/csv OK")


def test_telegram():
    run("DELETE FROM time_entries WHERE user_id = 1")
    web = one("SELECT id FROM time_projects WHERE name = 'Web sitesi'")["id"]
    NOW[0] = datetime(2026, 10, 8, 9, 0, tzinfo=TZ)
    say("/baslat web SİTESİ ana sayfa")
    cur = one("SELECT * FROM time_entries WHERE user_id = 1 AND ended_at IS NULL")
    assert cur["project_id"] == web and cur["note"] == "ana sayfa"
    assert "Sayaç başladı" in sent()[-1] and "🔵 Web sitesi · ana sayfa" in sent()[-1]
    assert last_buttons() == [f"tt:{cur['id']}"]
    # Proje adı eşleşmezse metnin tamamı not; önceki sayaç durur
    NOW[0] = datetime(2026, 10, 8, 10, 30, tzinfo=TZ)
    say("/başlat toplantı notları")
    second = one("SELECT * FROM time_entries WHERE user_id = 1 AND ended_at IS NULL")
    assert second["project_id"] is None and second["note"] == "toplantı notları" and running_count() == 1
    assert "Önceki sayaç durdu: 1 sa 30 dk" in sent()[-1]
    NOW[0] = datetime(2026, 10, 8, 11, 0, tzinfo=TZ)
    say("/zaman")
    text = sent()[-1]
    assert "Çalışıyor: 30 dk" in text and "başlangıç 10:30" in text and "Bugün: 2 sa" in text and "750 ₺" in text
    assert last_buttons() == [f"tt:{second['id']}"]
    say("/durdur")
    assert "Sayaç durdu: 30 dk" in sent()[-1] and "Bugün toplam: 2 sa" in sent()[-1] and running_count() == 0
    say("/durdur")
    assert "Çalışan sayaç yok" in sent()[-1]
    say("/zaman")
    assert "Çalışan sayaç yok" in sent()[-1] and last_buttons() == []
    # Eski mesajdaki Durdur butonu yeni sayaca dokunmaz
    say("/baslat")
    third = one("SELECT * FROM time_entries WHERE user_id = 1 AND ended_at IS NULL")
    press(f"tt:{cur['id']}")
    assert callback_texts()[-1] == "Bu sayaç zaten durmuş." and running_count() == 1
    NOW[0] = datetime(2026, 10, 8, 11, 20, tzinfo=TZ)
    press(f"tt:{third['id']}")
    assert callback_texts()[-1] == "⏹️ Durdu: 20 dk" and running_count() == 0
    edited = [p for m, p in CALLS if m == "editMessageText"][-1]
    assert "Sayaç durdu: 20 dk" in edited["text"] and f"/zaman/{third['id']}" in edited["text"]
    # Başkasının kaydı
    with app.app_context():
        foreign = execute("INSERT INTO time_entries (user_id, started_at) VALUES (?, '2026-10-08 05:00:00')",
                          (AYSE,)).lastrowid
    press(f"tt:{foreign}")
    assert callback_texts()[-1].startswith("Kayıt bulunamadı")
    assert one("SELECT ended_at FROM time_entries WHERE id = ?", (foreign,))["ended_at"] is None
    run("DELETE FROM time_entries WHERE id = ?", (foreign,))
    # Komutlar menüde ve yardımda
    from pano.bot_commands import COMMANDS, HELP
    assert {"baslat", "durdur", "zaman"} <= {c for c, _ in COMMANDS} and "/baslat" in HELP
    print("  telegram OK")


def cron():
    r = RAW.get("/cron/gizli/hatirlatma")
    assert r.status_code == 200, r.status_code
    return r.json


def test_long_running():
    NOW[0] = datetime(2026, 10, 9, 8, 0, tzinfo=TZ)
    ADMIN.post("/zaman/baslat", data={"note": "unutulan"})
    entry = one("SELECT * FROM time_entries WHERE user_id = 1 AND ended_at IS NULL")
    MEHMET_C.post("/zaman/durdur")
    MEHMET_C.post("/zaman/baslat")  # Telegram'ı bağlı değil: uyarı yok
    NOW[0] = datetime(2026, 10, 9, 17, 55, tzinfo=TZ)
    assert cron()["timers_warned"] == 0
    NOW[0] = datetime(2026, 10, 9, 18, 5, tzinfo=TZ)
    before = len(sent())
    assert cron()["timers_warned"] == 1
    text = sent()[-1]
    assert len(sent()) == before + 1 and "Sayaç 10 saattir çalışıyor — unuttun mu?" in text and "unutulan" in text
    assert "09.10.2026 08:00" in text and last_buttons() == [f"tt:{entry['id']}"]
    assert one("SELECT long_warned FROM time_entries WHERE id = ?", (entry["id"],))["long_warned"] == 1
    NOW[0] = datetime(2026, 10, 9, 20, 0, tzinfo=TZ)
    assert cron()["timers_warned"] == 0  # bir kez
    press(f"tt:{entry['id']}")
    assert callback_texts()[-1] == "⏹️ Durdu: 12 sa" and running_count() == 0
    # Webhook yoksa buton gönderilmez
    run("DELETE FROM app_state WHERE key = 'telegram_webhook'")
    ADMIN.post("/zaman/baslat")
    NOW[0] = datetime(2026, 10, 10, 7, 0, tzinfo=TZ)
    assert cron()["timers_warned"] == 1 and last_buttons() == []
    ADMIN.post("/zaman/durdur")
    run("INSERT INTO app_state (key, value) VALUES ('telegram_webhook', 'https://x/telegram/webhook')")
    print("  long running OK")


def test_trash():
    web = one("SELECT * FROM time_projects WHERE name = 'Web sitesi'")
    ADMIN.post("/zaman/baslat", data={"project_id": str(web["id"])})
    n = one("SELECT COUNT(*) AS n FROM time_entries WHERE project_id = ?", (web["id"],))["n"]
    assert n >= 2
    r = ADMIN.post(f"/zaman/proje/{web['id']}/sil", follow_redirects=True)
    assert "çöp kutusuna taşındı" in r.get_data(as_text=True)
    assert one("SELECT 1 x FROM time_projects WHERE id = ?", (web["id"],)) is None
    assert one("SELECT COUNT(*) AS n FROM time_entries WHERE project_id = ?", (web["id"],))["n"] == 0
    assert running_count() == 0
    item = one("SELECT * FROM trash WHERE module = 'timetrack' ORDER BY id DESC")
    assert "Web sitesi" in item["label"]
    ADMIN.post(f"/cop-kutusu/{item['id']}/geri")
    assert one("SELECT name FROM time_projects WHERE id = ?", (web["id"],))["name"] == "Web sitesi"
    assert one("SELECT COUNT(*) AS n FROM time_entries WHERE project_id = ?", (web["id"],))["n"] == n
    assert running_count() == 0  # geri gelen kayıt çalışmaya devam etmez
    print("  trash OK")


def test_menu():
    page = ADMIN.text("/menu")
    assert "Zaman Takibi" in page and "Araçlar" in page
    print("  menu OK")


if __name__ == "__main__":
    test_parse()
    test_projects()
    test_timer()
    test_manual_and_edit()
    test_report_and_csv()
    test_telegram()
    test_long_running()
    test_trash()
    test_menu()
    print("OK")
