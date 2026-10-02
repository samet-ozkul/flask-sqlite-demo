"""Yıl özeti: bölüm sayıları, geçen yıla göre değişim, yıl seçici ve varsayılan yıl, ısı haritası, başka
kullanıcının verisi, boş kullanıcı, Telegram'a gönder, 1 Ocak cron mesajı ve pano şeridi.

Saat taklit edilir (todo_reminders.now_local); Telegram ve dış servisler çağrılmaz.
Çalıştır: .venv/Scripts/python tests/test_yearreview.py
"""
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

app = make_app()
os.environ["CRON_SECRET"] = "gizli"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.external as external  # noqa: E402
import pano.telegram as tg  # noqa: E402
import pano.todo_reminders as todo  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query_one  # noqa: E402
from pano.modules import yearreview as yr  # noqa: E402
from pano.utils import TZ  # noqa: E402

CALLS = []
FAIL_CHATS = set()  # bu sohbetlere giden yıl özeti mesajı hata verir


def fake_call(method, params=None, files=None):
    params = params or {}
    if params.get("chat_id") in FAIL_CHATS and "yılın özeti" in params.get("text", ""):
        raise tg.TelegramError("bot engellendi")
    CALLS.append((method, params))
    return {"message_id": 1}


tg._call = fake_call
NOW = [datetime(2026, 10, 2, 12, 0, tzinfo=TZ)]
todo.now_local = lambda: NOW[0]
external.weather = lambda lat, lon: None
external.rates = lambda: None
external.gold_gram_try = lambda: None

with app.app_context():
    AYSE = create_user("ayse", "ayse12345")
    BOS = create_user("bos", "bos12345")
    CAN = create_user("can", "can12345")
    execute("UPDATE users SET telegram_chat_id = '100', display_name = 'Samet' WHERE id = 1")
    execute("UPDATE users SET telegram_chat_id = '200' WHERE id = ?", (AYSE,))
    execute("UPDATE users SET telegram_chat_id = '300' WHERE id = ?", (BOS,))
ADMIN = Client(app)
AYSE_C = Client(app, "ayse", "ayse12345")
BOS_C = Client(app, "bos", "bos12345")
CAN_C = Client(app, "can", "can12345")

ACT = {}  # yönetici: yıl -> {yerel gün: kayıt sayısı} (ısı haritası beklentisi)


def run(sql, args=()):
    with app.app_context():
        return execute(sql, args).lastrowid


def act(day, uid):
    if uid == 1:
        ACT.setdefault(day[:4], Counter())[day[:10]] += 1


def utc(local):
    """'2026-03-05 01:30' (yerel) -> UTC 'YYYY-MM-DD HH:MM:SS'."""
    dt = datetime.fromisoformat(local).replace(tzinfo=TZ)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def sent():
    return [(p["chat_id"], p["text"]) for m, p in CALLS if m == "sendMessage"]


def reviews():
    return [(c, t) for c, t in sent() if "yılın özeti hazır" in t]


def cron():
    r = app.test_client().get("/cron/gizli/gunluk")
    assert r.status_code == 200, r.status_code
    return r.json


# ---------- Veri ----------
def expense(day, amount, category, note="", uid=1):
    run("INSERT INTO expenses (user_id, amount, category, note, date) VALUES (?, ?, ?, ?, ?)",
        (uid, amount, category, note, day))
    act(day, uid)


def seed():
    # Harcamalar: 2022, 2024, 2025, 2026 (2023 yok) ve ileri tarihli 2027
    expense("2022-04-04", 300, "Market")
    expense("2024-05-05", 1200, "Market")
    for day, amount, cat in (("2025-03-10", 1000, "Market"), ("2025-06-01", 500, "Yemek"), ("2025-12-15", 900, "Ev")):
        expense(day, amount, cat)
    for day, amount, cat, note in (("2026-01-05", 300, "Market", ""), ("2026-03-12", 700, "Market", ""),
                                   ("2026-03-20", 400, "Yemek", ""), ("2026-05-01", 200, "Ulaşım", ""),
                                   ("2026-07-07", 100, "Giyim", ""), ("2026-08-08", 50, "Ev", ""),
                                   ("2026-09-30", 2500, "Eğlence", "Konser")):
        expense(day, amount, cat, note)
    expense("2027-02-02", 50, "Market")

    # Faturalar (ödenen: paid_at yerel gün)
    for day, amount in (("2026-03-01", 450), ("2026-04-01", 300), ("2025-05-05", 100)):
        run("INSERT INTO bills (user_id, name, amount, due_date, paid, paid_at) VALUES (1, 'Elektrik', ?, ?, 1, ?)",
            (amount, day, day))
        act(day, 1)
    run("INSERT INTO bills (user_id, name, amount, due_date) VALUES (1, 'Su', 99, '2026-11-01')")

    # Alışkanlıklar: en uzun seri 10 gün (Su & çay, 1–10 Mart); 2025 sonu – 2026 başı seri yıl sınırında bölünür
    su = run("INSERT INTO habits (user_id, name, icon, created_at) VALUES (1, 'Su & çay', '💧', '2025-12-01 07:00:00')")
    kitap = run("INSERT INTO habits (user_id, name, icon, created_at) VALUES (1, 'Kitap', '📚', '2026-03-01 07:00:00')")
    su_days = ([f"2025-12-{d}" for d in (28, 29, 30, 31)] + ["2026-01-01", "2026-01-02"]
               + [f"2026-03-{d:02d}" for d in range(1, 11)] + ["2026-04-01", "2026-04-02", "2026-04-03"])
    for day in su_days:
        run("INSERT INTO habit_logs (habit_id, date) VALUES (?, ?)", (su, day))
        act(day, 1)
    for day in ("2026-03-05", "2026-03-06", "2026-03-07", "2026-06-01"):
        run("INSERT INTO habit_logs (habit_id, date) VALUES (?, ?)", (kitap, day))
        act(day, 1)

    # Günlük: 5 gün (biri sadece metin), en uzun seri 3; boş satır sayılmaz
    for day, mood, text in (("2026-02-01", 5, ""), ("2026-02-02", 5, ""), ("2026-02-03", 4, ""),
                            ("2026-02-05", 2, "Zor gün"), ("2026-07-01", None, "Sadece not")):
        run("INSERT INTO journal (user_id, date, mood, text) VALUES (1, ?, ?, ?)", (day, mood, text))
        act(day, 1)
    run("INSERT INTO journal (user_id, date, mood, text) VALUES (1, '2026-08-01', NULL, '')")

    # Zaman takibi (yerel saatler; ilk kayıt UTC'de 2025'te ama yerelde 1 Ocak 2026)
    web = run("INSERT INTO time_projects (user_id, name, color) VALUES (1, 'Web', 'blue')")
    book = run("INSERT INTO time_projects (user_id, name, color) VALUES (1, 'Kitap yazımı', 'green')")
    for project, start, end in ((None, "2026-01-01 01:00", "2026-01-01 01:30"), (web, "2026-02-10 09:00", "2026-02-10 12:00"),
                                (web, "2026-05-05 10:00", "2026-05-05 11:30"), (book, "2026-06-06 20:00", "2026-06-06 21:00"),
                                (None, "2026-08-08 08:00", "2026-08-08 08:30"), (web, "2025-12-31 23:30", "2026-01-01 00:00")):
        run("INSERT INTO time_entries (user_id, project_id, started_at, ended_at) VALUES (1, ?, ?, ?)",
            (project, utc(start), utc(end)))
        act(start, 1)

    # İzleme / okuma
    for kind, title, rating, status, finished in (
            ("book", "Kürk Mantolu Madonna", 5, "done", "2026-03-03"), ("movie", "Babam ve Oğlum", 4, "done", "2026-04-04"),
            ("series", "Leyla ile Mecnun", None, "done", "2026-05-05"), ("book", "Tutunamayanlar", 3, "done", "2026-08-08"),
            ("book", "Saatleri Ayarlama Enstitüsü", None, "doing", None), ("movie", "Eski film", 2, "done", "2025-12-12")):
        run("INSERT INTO watchlist (user_id, kind, title, rating, status, finished_at) VALUES (1, ?, ?, ?, ?, ?)",
            (kind, title, rating, status, finished))
        if finished:
            act(finished, 1)

    # Harita: 2026'da gezilen Konya (ziyaret ayı) ve İzmir (tarihsiz, 2026'da eklendi); Roma 2025 ziyareti
    for name, country, status, visited, created in (
            ("Konya", "Türkiye", "visited", "2026-05", "2026-01-10 09:00"), ("İzmir", "Türkiye", "visited", None, "2026-06-01 09:00"),
            ("Paris", "Fransa", "wish", None, "2026-02-01 09:00"), ("Roma", "İtalya", "visited", "2025-07-15", "2026-02-02 09:00")):
        run("INSERT INTO cities (user_id, name, country, status, visited_on, created_at) VALUES (1, ?, ?, ?, ?, ?)",
            (name, country, status, visited, utc(created)))
        act(created, 1)
    for name, category, status, created in (("Kafe X", "cafe", "visited", "2026-04-01 10:00"),
                                            ("Müze", "sight", "wish", "2026-04-02 10:00")):
        run("INSERT INTO places (user_id, name, category, status, created_at) VALUES (1, ?, ?, ?, ?)",
            (name, category, status, utc(created)))
        act(created, 1)

    # Sağlık: 2026'nın ilk ve son kilosu 80 → 76,3
    for kind, v1, v2, at in (("weight", 82, None, "2025-12-01T08:00"), ("weight", 80, None, "2026-01-10T08:00"),
                             ("weight", 78.5, None, "2026-05-10T08:00"), ("weight", 76.3, None, "2026-09-01T08:00"),
                             ("bp", 120, 80, "2026-02-02T09:00")):
        run("INSERT INTO health_metrics (user_id, kind, value1, value2, measured_at) VALUES (1, ?, ?, ?, ?)", (kind, v1, v2, at))
        act(at, 1)

    # Listeler: kendi paylaşılan listesi; başkasının eklediği ve başkasına atanan maddeler sayılmaz
    todo_list = run("INSERT INTO lists (user_id, name, kind, shared) VALUES (1, 'İşler', 'todo', 1)")
    shop = run("INSERT INTO lists (user_id, name, kind) VALUES (1, 'Market', 'shopping')")
    for list_id, by, assignee, done_local, counted in (
            (todo_list, 1, None, "2026-02-01 13:00", True), (todo_list, 1, None, "2026-02-01 14:00", True),
            (todo_list, 1, 1, "2026-07-15 09:00", True), (todo_list, None, None, "2026-01-01 00:30", True),
            (todo_list, 1, None, "2025-11-11 13:00", True), (todo_list, AYSE, None, "2026-03-03 10:00", False),
            (todo_list, 1, AYSE, "2026-03-04 10:00", False), (shop, 1, None, "2026-05-05 18:00", True),
            (shop, 1, None, "2026-05-06 18:00", True)):
        run("INSERT INTO list_items (list_id, text, done, done_at, created_by, assignee_id) VALUES (?, 'iş', 1, ?, ?, ?)",
            (list_id, utc(done_local), by, assignee))
        if counted:
            act(done_local, 1)
    run("INSERT INTO list_items (list_id, text, created_by) VALUES (?, 'açık iş', 1)", (todo_list,))
    ayse_list = run("INSERT INTO lists (user_id, name) VALUES (?, 'Ayşe işleri')", (AYSE,))
    run("INSERT INTO list_items (list_id, text, done, done_at, created_by) VALUES (?, 'x', 1, ?, ?)",
        (ayse_list, utc("2026-03-03 10:00"), AYSE))

    # Kanban: "Bitti" sütunundaki kendi kartları
    board = run("INSERT INTO boards (user_id, name, shared) VALUES (1, 'Proje', 1)")
    todo_col = run("INSERT INTO board_columns (board_id, name, position) VALUES (?, 'Yapılacak', 0)", (board,))
    done_col = run("INSERT INTO board_columns (board_id, name, position) VALUES (?, 'Bitti', 1)", (board,))
    for col, by, updated, counted in ((done_col, 1, "2026-03-10 10:00", True), (done_col, 1, "2026-04-10 10:00", True),
                                      (todo_col, 1, "2026-04-11 10:00", False), (done_col, AYSE, "2026-04-12 10:00", False)):
        run("INSERT INTO cards (board_id, column_id, title, created_by, updated_at) VALUES (?, ?, 'kart', ?, ?)",
            (board, col, by, utc(updated)))
        if counted:
            act(updated, 1)

    # Notlar (biri UTC'de 4 Mart, yerelde 5 Mart), tarif
    for local in ("2026-03-05 01:30", "2026-04-01 13:00", "2026-04-01 14:00", "2025-06-06 10:00"):
        run("INSERT INTO notes (user_id, content, created_at) VALUES (1, 'not', ?)", (utc(local),))
        act(local, 1)
    run("INSERT INTO recipes (user_id, title, created_at) VALUES (1, 'Mercimek', ?)", (utc("2026-05-05 13:00"),))
    act("2026-05-05", 1)

    # Kişiler: 4 görüşme, 2 kişi
    dede = run("INSERT INTO contacts (user_id, name) VALUES (1, 'Dede')")
    teyze = run("INSERT INTO contacts (user_id, name) VALUES (1, 'Teyze')")
    for contact, day, kind in ((dede, "2026-02-02", "call"), (dede, "2026-03-03", "call"), (dede, "2026-04-04", "visit"),
                               (teyze, "2026-04-05", "message")):
        run("INSERT INTO contact_logs (contact_id, user_id, date, kind) VALUES (?, 1, ?, ?)", (contact, day, kind))
        act(day, 1)

    # Siparişler: iptal edilen sayılmaz
    for item, amount, day, status in (("Kulaklık", 500, "2026-02-14", "delivered"), ("Kitap", 100, "2026-06-06", "ordered"),
                                      ("İptal", 300, "2026-07-07", "cancelled")):
        run("INSERT INTO orders (user_id, item, amount, ordered_on, status) VALUES (1, ?, ?, ?, ?)", (item, amount, day, status))
        act(day, 1)

    # Hedefler: 1 tamamlanan, net 3.000 ₺ birikim
    tatil = run("INSERT INTO goals (user_id, name, icon, target, done_at) VALUES (1, 'Tatil', '✈️', 1000, '2026-06-01')")
    araba = run("INSERT INTO goals (user_id, name, target) VALUES (1, 'Araba', 50000)")
    for goal, amount, day in ((tatil, 600, "2026-02-01"), (tatil, 500, "2026-05-01"), (tatil, -100, "2026-06-15"),
                              (araba, 2000, "2026-08-01")):
        run("INSERT INTO goal_entries (goal_id, amount, date) VALUES (?, ?, ?)", (goal, amount, day))
        act(day, 1)

    # Araç: yıldan önceki son km 10.000, yılın son km'si 15.000
    car = run("INSERT INTO vehicles (user_id, name) VALUES (1, 'Araba')")
    for kind, day, km, amount, liters in (("fuel", "2025-12-01", 10000, 1400, 40), ("fuel", "2026-02-01", 10500, 1500, 40),
                                          ("service", "2026-06-01", 13000, 3000, None),
                                          ("fuel", "2026-09-01", 15000, 1600, 42.5)):
        run("INSERT INTO vehicle_logs (vehicle_id, kind, date, km, amount, liters) VALUES (?, ?, ?, ?, ?, ?)",
            (car, kind, day, km, amount, liters))
        act(day, 1)

    # Ayşe'nin verisi yöneticinin özetine karışmamalı
    expense("2026-05-05", 99999, "Gizli kategori", "Gizli", uid=AYSE)
    h = run("INSERT INTO habits (user_id, name) VALUES (?, 'Ayşe koşu')", (AYSE,))
    run("INSERT INTO habit_logs (habit_id, date) VALUES (?, '2026-03-02')", (h,))
    run("INSERT INTO journal (user_id, date, mood) VALUES (?, '2026-03-02', 1)", (AYSE,))
    run("INSERT INTO cities (user_id, name, status, visited_on) VALUES (?, 'Berlin', 'visited', '2026-03')", (AYSE,))
    run("INSERT INTO watchlist (user_id, kind, title, status, finished_at) VALUES (?, 'book', 'Ayşe kitabı', 'done', '2026-03-02')",
        (AYSE,))
    run("INSERT INTO health_metrics (user_id, kind, value1, measured_at) VALUES (?, 'weight', 60, '2026-03-02T08:00')", (AYSE,))
    run("INSERT INTO notes (user_id, content) VALUES (?, 'Ayşe notu')", (AYSE,))

    # Can: Telegram'ı bağlı değil, tek harcama
    expense("2026-04-04", 75, "Market", uid=CAN)


def build(uid, year):
    with app.app_context():
        return yr.build(uid, year, NOW[0])


# ---------- Testler ----------
def test_numbers():
    r = build(1, 2026)
    assert r["ongoing"] and not r["empty"]
    e = r["expenses"]
    assert e["total"] == 4250 and e["count"] == 7 and e["categories"] == 6
    assert e["avg"] == 425 and e["months"] == 10  # Ocak – Ekim
    assert e["top"] == [("Eğlence", 2500), ("Market", 1000), ("Yemek", 400), ("Ulaşım", 200), ("Giyim", 100)]
    assert e["top_month"] == {"name": "Eylül", "total": 2500, "key": "2026-09"}
    assert e["biggest"]["amount"] == 2500 and e["biggest"]["note"] == "Konser"
    # Devam eden yıl: geçen yılın 1 Ocak – 2 Ekim dönemiyle (1.500 ₺; Aralık'taki 900 ₺ hariç)
    assert e["prev"] == 1500 and e["change_pct"] == 183 and e["cmp_label"] == "geçen yılın aynı dönemine göre"
    e25 = build(1, 2025)["expenses"]
    assert e25["total"] == 2400 and e25["prev"] == 1200 and e25["change_pct"] == 100 and e25["cmp_label"] == "2024 yılına göre"
    assert e25["months"] == 10 and e25["avg"] == 240  # Mart – Aralık
    assert build(1, 2022)["expenses"]["change"] is None  # 2021 verisi yok

    assert r["bills"] == {"count": 2, "total": 750}
    assert r["tasks"] == {"todo": 4, "shopping": 2}
    h = r["habits"]
    assert h["marks"] == 19 and h["days"] == 16 and h["count"] == 2
    assert h["streak"]["streak"] == 10 and h["streak"]["name"] == "Su & çay"
    assert h["steady"]["name"] == "Su & çay" and h["steady"]["count"] == 15 and h["steady"]["rate"] == 5  # 15/275 gün
    assert build(1, 2025)["habits"]["streak"]["streak"] == 4  # yıl sınırında bölünür
    j = r["journal"]
    assert j["days"] == 5 and j["streak"] == 3 and j["avg"] == 4.0 and j["top"] == ("😄", "Harika")
    assert [n for _l, n, _t in j["bars"]] == [2, 1, 1]
    w = r["watch"]
    assert w["total"] == 4 and w["label"] == "kitap, film & dizi" and w["words"] == [(2, "kitap"), (1, "film"), (1, "dizi")]
    assert [x["title"] for x in w["top"]] == ["Kürk Mantolu Madonna", "Babam ve Oğlum", "Tutunamayanlar"]
    pl = r["places"]
    assert [c["name"] for c in pl["cities"]] == ["Konya", "İzmir"] and pl["countries"] == ["Türkiye"] and pl["places"] == 1
    assert [c["name"] for c in build(1, 2025)["places"]["cities"]] == ["Roma"]
    tm = r["time"]
    assert tm["total"] == 23400 and tm["count"] == 5 and tm["days"] == 5  # 6 sa 30 dk
    assert [(label, v) for label, v, _t in tm["bars"]] == [("🔵 Web", 16200), ("🟢 Kitap yazımı", 3600), ("⏱️ Projesiz", 3600)]
    car = r["car"]
    assert car["km"] == 5000 and car["fuel"] == 3100 and car["liters"] == 82.5 and car["fuel_count"] == 2
    assert car["service"] == 1 and car["repair"] == 0 and car["total"] == 6100
    hl = r["health"]
    assert hl["count"] == 4 and hl["first"] == 80 and hl["last"] == 76.3 and hl["change"] == -3.7
    assert hl["low"] == 76.3 and hl["high"] == 80
    gl = r["goals"]
    assert [x["name"] for x in gl["done"]] == ["Tatil"] and gl["saved"] == 3000
    misc = {m["key"]: m for m in r["misc"]}
    assert {k: m["value"] for k, m in misc.items()} == {"notes": 3, "recipes": 1, "talks": 4, "cards": 2, "orders": 2}
    assert misc["talks"]["sub"] == "2 kişiyle" and misc["orders"]["sub"] == "600 ₺"

    # Öne çıkanlar: 4-6 kart, harcama ve kilo değişimi aralarında
    values = [x["value"] for x in r["highlights"]]
    assert 4 <= len(values) <= 6 and "4.250 ₺" in values and "-3,7 kg" in values, values
    print("  numbers OK")


def test_heatmap():
    r = build(1, 2026)
    heat = r["heat"]
    expected = {d: n for d, n in ACT["2026"].items() if d <= "2026-10-02"}
    assert heat["active_days"] == len(expected) and heat["total"] == sum(expected.values())
    best = min(expected.items(), key=lambda kv: (-kv[1], kv[0]))
    assert heat["best_day"] == (date.fromisoformat(best[0]), best[1])
    assert heat["cols"] == 53 and len(heat["cells"]) == 53 * 7  # 2026: 1 Ocak perşembe
    assert heat["months"][0] == (1, "Oca") and heat["months"][2] == (9, "Mar")
    page = ADMIN.text("/yil-ozeti/?yil=2026")
    grid = page.split('class="heat-grid">', 1)[1].split("</div>", 1)[0]
    assert len(re.findall(r'class="h[1-4]"', grid)) == len(expected)
    assert len(re.findall(r'class="h[0-4]"', grid)) == 275  # 1 Ocak – 2 Ekim; gelecek günler boş
    assert "1 Oca: " in grid  # UTC'de 2025'te olan kayıtlar yerel güne yazıldı
    # Devam eden yılda bugün işaretli ve telefonda ızgara oraya kaydırılır
    assert heat["today"] == 275 + 2 and 'data-scroll-to="[data-today]"' in page  # 2026 pazartesi 29 Aralık'tan başlar
    assert len(re.findall(r"data-today", grid)) == 1 and 'title="2 Eki' in grid.split("data-today", 1)[0][-80:]
    # Biten yıl: yılın tüm günleri
    page = ADMIN.text("/yil-ozeti/?yil=2025")
    grid = page.split('class="heat-grid">', 1)[1].split("</div>", 1)[0]
    assert len(re.findall(r'class="h[0-4]"', grid)) == 365
    assert len(re.findall(r'class="h[1-4]"', grid)) == len(ACT["2025"])
    assert "data-today" not in grid and "data-scroll-to" not in page and build(1, 2025)["heat"]["today"] is None
    print("  heatmap OK")


def years_in(page):
    nav = page.split('<nav class="tabs" aria-label="Yıl">', 1)[1].split("</nav>", 1)[0]
    return re.findall(r"yil=(\d{4})", nav)


def test_page_and_years():
    page = ADMIN.text("/yil-ozeti/")
    assert "📊 2026 Yıl Özeti (şu ana kadar)" in page
    assert years_in(page) == ["2026", "2025", "2024", "2022"]  # verisiz 2023 ve ileri tarihli 2027 yok
    for text in ("4.250 ₺", "Konser", "Su &amp; çay", "Kürk Mantolu Madonna", "Konya", "76,3", "Eylül", "▲ %183",
                 "6 sa 30 dk", "5.000 km", "Tatil", "kitap, film &amp; dizi", "Samet", "📨 Telegram'a gönder"):
        assert text in page, text
    # Başkasının verisi sayılmaz
    for text in ("Gizli", "99.999", "Berlin", "Ayşe koşu", "Ayşe kitabı"):
        assert text not in page, text
    page = ADMIN.text("/yil-ozeti/?yil=2025")
    assert "📊 2025 Yıl Özeti" in page and "şu ana kadar" not in page and "2.400 ₺" in page and "▲ %100" in page
    # Geçersiz, gelecek ya da verisiz yıl -> varsayılan
    for raw in ("2030", "2027", "abc", "2023", ""):
        assert "📊 2026 Yıl Özeti (şu ana kadar)" in ADMIN.text(f"/yil-ozeti/?yil={raw}"), raw
    # Ayşe kendi verisini görür, yöneticininkini görmez
    page = AYSE_C.text("/yil-ozeti/")
    assert "99.999" in page and "Berlin" in page and "Konser" not in page and "4.250" not in page
    assert years_in(page) == ["2026"]
    print("  page/years OK")


def test_default_year():
    assert yr.default_year(date(2027, 1, 31)) == 2026 and yr.default_year(date(2027, 2, 1)) == 2027
    assert yr.default_year(date(2026, 12, 31)) == 2026
    assert yr.pick_year(None, [2027], date(2027, 1, 10)) == 2027  # geçen yıl verisi yoksa en son yıl
    NOW[0] = datetime(2027, 1, 15, 10, 0, tzinfo=TZ)
    page = ADMIN.text("/yil-ozeti/")
    assert "📊 2026 Yıl Özeti<" in page and years_in(page)[0] == "2027"
    NOW[0] = datetime(2027, 2, 10, 10, 0, tzinfo=TZ)
    assert "📊 2027 Yıl Özeti (şu ana kadar)" in ADMIN.text("/yil-ozeti/")
    NOW[0] = datetime(2026, 10, 2, 12, 0, tzinfo=TZ)
    print("  default year OK")


def test_empty_user():
    page = BOS_C.text("/yil-ozeti/")
    assert "özetlenecek kayıt yok" in page and 'class="heat-grid"' not in page
    assert years_in(page) == ["2026"] and "Telegram'a gönder" not in page
    r = build(BOS, 2026)
    assert r["empty"] and r["highlights"] == [] and r["heat"]["total"] == 0
    with app.app_context():
        assert yr.data_years(BOS, date(2026, 10, 2)) == [2026] and not yr.has_data(BOS, 2026)
    print("  empty user OK")


def test_send_telegram():
    CALLS.clear()
    r = ADMIN.post("/yil-ozeti/gonder", data={"yil": "2026"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/yil-ozeti/?yil=2026")
    (chat, text), = sent()
    assert chat == "100"
    for part in ("2026 yıl özetin (şu ana kadar)", "4.250 ₺", "▲ %183", "Eğlence", "Su &amp; çay", "Konya, İzmir",
                 "2 kitap, 1 film, 1 dizi", "6 sa 30 dk", "5.000 km", "80 → 76,3 kg (-3,7)", "1 hedef tamamlandı",
                 "/yil-ozeti/?yil=2026"):
        assert part in text, (part, text)
    assert "Gizli" not in text and "<b>" in text
    assert "gönderildi" in ADMIN.text("/yil-ozeti/?yil=2026")
    # Geçmiş yıl
    ADMIN.post("/yil-ozeti/gonder", data={"yil": "2025"})
    assert "2025 yıl özetin</b>" in sent()[-1][1] and "2.400 ₺" in sent()[-1][1]
    # Telegram bağlı değilse düğme yerine ayarlara bağlantı; POST gönderim yapmaz
    page = CAN_C.text("/yil-ozeti/")
    assert "/ayarlar/#telegram" in page and "Telegram'a gönder</button>" not in page
    CALLS.clear()
    r = CAN_C.post("/yil-ozeti/gonder", data={"yil": "2026"}, follow_redirects=True)
    assert "Telegram bölümünden" in r.get_data(as_text=True) and not sent()
    # Telegram hatası kullanıcıya gösterilir
    def broken(method, params=None, files=None):
        raise tg.TelegramError("ağ yok")

    tg._call = broken
    r = ADMIN.post("/yil-ozeti/gonder", data={"yil": "2026"}, follow_redirects=True)
    assert "Gönderilemedi: ağ yok" in r.get_data(as_text=True)
    tg._call = fake_call
    print("  telegram send OK")


def test_new_year_cron():
    CALLS.clear()
    # 2 Ocak'ta değil, sadece 1 Ocak'ta
    NOW[0] = datetime(2026, 12, 31, 8, 0, tzinfo=TZ)
    assert cron()["year_reviews"] == 0 and not reviews()
    NOW[0] = datetime(2027, 1, 1, 8, 0, tzinfo=TZ)
    FAIL_CHATS.add("200")  # Ayşe'ye gönderim hata verir: diğerleri durmaz
    result = cron()
    assert result["year_reviews"] == 1 and any("ayse yıl özeti" in err for err in result["errors"]), result
    (chat, text), = reviews()
    assert chat == "100" and "🎉 <b>2026 yılın özeti hazır</b>" in text and "4.250 ₺" in text
    assert "/yil-ozeti/?yil=2026" in text and "Gizli" not in text
    # İkinci çağrı: yöneticiye tekrar gitmez, hata alan Ayşe'ye bu kez gider (kendi verisiyle); verisiz kullanıcıya hiç
    FAIL_CHATS.clear()
    assert cron()["year_reviews"] == 1
    chats = [c for c, _t in reviews()]
    assert chats == ["100", "200"], chats
    ayse_text = reviews()[-1][1]
    assert "99.999" in ayse_text and "4.250" not in ayse_text
    assert cron()["year_reviews"] == 0 and len(reviews()) == 2
    with app.app_context():
        assert query_one("SELECT value FROM app_state WHERE key = 'yearreview:1:2026'")["value"] == "2027-01-01"
    # Günlük özeti kapatan kullanıcıya gitmez
    run("DELETE FROM app_state WHERE key = 'yearreview:1:2026'")
    run("UPDATE users SET notify_daily = 0 WHERE id = 1")
    assert cron()["year_reviews"] == 0 and len(reviews()) == 2
    run("UPDATE users SET notify_daily = 1 WHERE id = 1")
    run("INSERT INTO app_state (key, value) VALUES ('yearreview:1:2026', '2027-01-01')")
    # 2 Ocak: henüz almamış (yeni bağlanan) kullanıcıya da gitmez
    run("UPDATE users SET telegram_chat_id = '400' WHERE id = ?", (CAN,))
    NOW[0] = datetime(2027, 1, 2, 8, 0, tzinfo=TZ)
    assert cron()["year_reviews"] == 0 and len(reviews()) == 2
    run("UPDATE users SET telegram_chat_id = NULL WHERE id = ?", (CAN,))
    NOW[0] = datetime(2026, 10, 2, 12, 0, tzinfo=TZ)
    print("  new year cron OK")


def test_dashboard_banner():
    NOW[0] = datetime(2026, 10, 2, 12, 0, tzinfo=TZ)
    assert "yıl özetin hazır" not in ADMIN.text("/")
    NOW[0] = datetime(2026, 12, 20, 12, 0, tzinfo=TZ)
    assert "📊 2026 yıl özetin hazır" in ADMIN.text("/") and "/yil-ozeti/?yil=2026" in ADMIN.text("/")
    assert "yıl özetin hazır" not in BOS_C.text("/")  # verisi yok
    NOW[0] = datetime(2027, 1, 20, 12, 0, tzinfo=TZ)
    assert "📊 2026 yıl özetin hazır" in ADMIN.text("/")
    NOW[0] = datetime(2027, 2, 1, 12, 0, tzinfo=TZ)
    assert "yıl özetin hazır" not in ADMIN.text("/")
    NOW[0] = datetime(2026, 10, 2, 12, 0, tzinfo=TZ)
    print("  dashboard banner OK")


def test_login_required():
    r = app.test_client().get("/yil-ozeti/")
    assert r.status_code == 302 and "/giris" in r.headers["Location"]
    assert app.test_client().post("/yil-ozeti/gonder").status_code in (302, 400)
    print("  login required OK")


if __name__ == "__main__":
    seed()
    test_numbers()
    test_heatmap()
    test_page_and_years()
    test_default_year()
    test_empty_user()
    test_send_telegram()
    test_new_year_cron()
    test_dashboard_banner()
    test_login_required()
    print("OK")
