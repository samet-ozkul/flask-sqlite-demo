"""Haftalık değerlendirme: hafta sınırları ve varsayılan hafta, haftanın sayıları (önceki haftaya göre değişim dahil),
form kaydetme/güncelleme/doğrulama, geçen haftanın öncelikleri, geçmiş ve grafik, kullanıcı yalıtımı, pazar Telegram
mesajı, wk puan butonu, pazartesi günlük özeti, /hafta, arama, silme ve geri getirme.

Saat taklit edilir (todo_reminders.now_local); Telegram ve dış servisler çağrılmaz.
Çalıştır: .venv/Scripts/python tests/test_weekly.py
"""
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

app = make_app()
os.environ["CRON_SECRET"] = "gizli"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.external as external  # noqa: E402
import pano.telegram as tg  # noqa: E402
import pano.todo_reminders as todo  # noqa: E402
from pano import utils  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query, query_one  # noqa: E402
from pano.modules import weekly as wk  # noqa: E402
from pano.utils import TZ  # noqa: E402

CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {})) or {"message_id": 1}
SUNDAY = datetime(2026, 10, 4, 21, 0, tzinfo=TZ)        # 28 Eyl – 4 Eki haftasının pazarı
WEDNESDAY = datetime(2026, 9, 30, 12, 0, tzinfo=TZ)
NOW = [SUNDAY]
todo.now_local = lambda: NOW[0]
external.weather = lambda lat, lon: None
W, P = date(2026, 9, 28), date(2026, 9, 21)  # bu hafta ve önceki hafta

with app.app_context():
    AYSE = create_user("ayse", "ayse12345")
    BOS = create_user("bos", "bos12345")
    CAN = create_user("can", "can12345")
    execute("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
    execute("UPDATE users SET telegram_chat_id = '200' WHERE id = ?", (AYSE,))
    execute("UPDATE users SET telegram_chat_id = '400' WHERE id = ?", (CAN,))
with app.test_request_context():
    SECRET = tg.webhook_secret()
ADMIN = Client(app)
AYSE_C = Client(app, "ayse", "ayse12345")
BOS_C = Client(app, "bos", "bos12345")
CAN_C = Client(app, "can", "can12345")
RAW = app.test_client()


def run(sql, args=()):
    with app.app_context():
        return execute(sql, args).lastrowid


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def review(uid, day):
    return one("SELECT * FROM weekly_reviews WHERE user_id = ? AND week_start = ?", (uid, day))


def utc(local):
    """'2026-09-28 00:30' (yerel) -> UTC 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.fromisoformat(local).replace(tzinfo=TZ).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def numbers(uid, start, now):
    with app.app_context():
        return wk.numbers(uid, start, now)


def sent():
    return [p for m, p in CALLS if m == "sendMessage"]


def prompts():
    return [p for p in sent() if "Haftalık değerlendirme zamanı" in p["text"]]


def buttons(params):
    markup = json.loads(params.get("reply_markup") or "{}")
    return [b["callback_data"] for row in markup.get("inline_keyboard", []) for b in row]


def cron():
    r = RAW.get("/cron/gizli/hatirlatma")
    assert r.status_code == 200, r.status_code
    return r.json


def say(text, chat=100):
    RAW.post("/telegram/webhook", data=json.dumps({"message": {"message_id": 1, "chat": {"id": chat}, "text": text}}),
             content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})


def press(data, chat=100):
    RAW.post("/telegram/webhook", data=json.dumps({"callback_query": {
        "id": "cb", "data": data, "from": {"id": chat}, "message": {"message_id": 7, "chat": {"id": chat}}}}),
        content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})


def flashed(r):
    return r.get_data(as_text=True)


# ---------- Veri ----------
def seed():
    # Harcamalar: önceki hafta 800, bu hafta 1.000 (Market 800), sonraki pazartesi sayılmaz
    for day, amount, cat in (("2026-09-21", 400, "Market"), ("2026-09-27", 400, "Yemek"), ("2026-09-28", 300, "Market"),
                             ("2026-09-30", 500, "Market"), ("2026-10-02", 200, "Yemek"), ("2026-10-05", 999, "Market")):
        run("INSERT INTO expenses (user_id, amount, category, date) VALUES (1, ?, ?, ?)", (amount, cat, day))
    run("INSERT INTO expenses (user_id, amount, category, note, date) VALUES (?, 99999, 'Gizli', 'Gizli', '2026-09-29')",
        (AYSE,))

    # Alışkanlıklar: Su her gün; Kitap çarşamba eklendi (5 günde 2); arşivdeki Koşu bu hafta 1 kez; Eski hiç
    su = run("INSERT INTO habits (user_id, name, icon, created_at) VALUES (1, 'Su', '💧', '2026-09-01 07:00:00')")
    kitap = run("INSERT INTO habits (user_id, name, icon, created_at) VALUES (1, 'Kitap', '📚', ?)", (utc("2026-09-30 10:00"),))
    kosu = run("INSERT INTO habits (user_id, name, icon, active, created_at) VALUES (1, 'Koşu', '🏃', 0, '2026-01-01 07:00:00')")
    run("INSERT INTO habits (user_id, name, active, created_at) VALUES (1, 'Eski', 0, '2026-01-01 07:00:00')")
    for habit, days in ((su, ["2026-09-27"] + [f"2026-{d}" for d in ("09-28", "09-29", "09-30", "10-01", "10-02", "10-03",
                                                                   "10-04", "10-05")]),
                        (kitap, ["2026-09-30", "2026-10-01"]), (kosu, ["2026-09-29"])):
        for day in days:
            run("INSERT INTO habit_logs (habit_id, date) VALUES (?, ?)", (habit, day))
    ayse_h = run("INSERT INTO habits (user_id, name) VALUES (?, 'Ayşe yoga')", (AYSE,))
    run("INSERT INTO habit_logs (habit_id, date) VALUES (?, '2026-09-29')", (ayse_h,))

    # Görevler (UTC done_at, yerel güne göre): pazartesi 00:30 ve pazar 23:50 bu haftada; başkasının maddesi sayılmaz
    todo_list = run("INSERT INTO lists (user_id, name, kind, shared) VALUES (1, 'İşler', 'todo', 1)")
    shop = run("INSERT INTO lists (user_id, name, kind) VALUES (1, 'Market', 'shopping')")
    for list_id, by, local in ((todo_list, 1, "2026-09-28 00:30"), (todo_list, 1, "2026-10-02 12:00"),
                               (todo_list, 1, "2026-10-04 23:50"), (todo_list, 1, "2026-09-27 23:30"),
                               (todo_list, 1, "2026-10-05 00:10"), (todo_list, AYSE, "2026-10-01 10:00"),
                               (shop, 1, "2026-10-01 18:00")):
        run("INSERT INTO list_items (list_id, text, done, done_at, created_by) VALUES (?, 'iş', 1, ?, ?)",
            (list_id, utc(local), by))

    # Günlük: 3 gün (biri sadece metin), ortalama 4,5; önceki haftanın ve boş satır sayılmaz
    for day, mood, text in (("2026-09-27", 1, ""), ("2026-09-28", 4, ""), ("2026-09-29", 5, ""),
                            ("2026-10-01", None, "Sadece not"), ("2026-10-03", None, "")):
        run("INSERT INTO journal (user_id, date, mood, text) VALUES (1, ?, ?, ?)", (day, mood, text))
    run("INSERT INTO journal (user_id, date, mood) VALUES (?, '2026-09-30', 1)", (AYSE,))

    # Zaman takibi: Web 3 sa, projesiz 1 sa + pazartesi 00:15'te başlayan 15 dk; pazar gecesi başlayan önceki haftada
    web = run("INSERT INTO time_projects (user_id, name, color) VALUES (1, 'Web', 'blue')")
    for project, start, end in ((web, "2026-09-29 09:00", "2026-09-29 12:00"), (None, "2026-10-03 20:00", "2026-10-03 21:00"),
                                (None, "2026-09-28 00:15", "2026-09-28 00:30"), (web, "2026-09-27 23:30", "2026-09-28 00:30")):
        run("INSERT INTO time_entries (user_id, project_id, started_at, ended_at) VALUES (1, ?, ?, ?)",
            (project, utc(start), utc(end)))

    # İzleme / okuma
    for kind, title, status, finished in (("book", "Kürk Mantolu Madonna", "done", "2026-10-01"),
                                          ("movie", "Eski film", "done", "2026-09-27"), ("series", "Dizi", "doing", None)):
        run("INSERT INTO watchlist (user_id, kind, title, status, finished_at) VALUES (1, ?, ?, ?, ?)",
            (kind, title, status, finished))

    # Kanban: "Bitti" sütunundaki kendi kartı sayılır
    board = run("INSERT INTO boards (user_id, name, shared) VALUES (1, 'Proje', 1)")
    todo_col = run("INSERT INTO board_columns (board_id, name, position) VALUES (?, 'Yapılacak', 0)", (board,))
    done_col = run("INSERT INTO board_columns (board_id, name, position) VALUES (?, 'Bitti', 1)", (board,))
    for col, by, local in ((done_col, 1, "2026-10-02 10:00"), (todo_col, 1, "2026-10-02 11:00"),
                           (done_col, 1, "2026-09-27 23:00"), (done_col, AYSE, "2026-10-01 10:00")):
        run("INSERT INTO cards (board_id, column_id, title, created_by, updated_at) VALUES (?, ?, 'kart', ?, ?)",
            (board, col, by, utc(local)))

    # Faturalar: bu hafta ödenen 450 ₺; yaklaşan (gerçek bugünden 2 gün sonra) Doğalgaz
    run("INSERT INTO bills (user_id, name, amount, due_date, paid, paid_at) VALUES (1, 'Elektrik', 450, '2026-09-30', 1, '2026-09-30')")
    run("INSERT INTO bills (user_id, name, amount, due_date, paid, paid_at) VALUES (1, 'Su', 120, '2026-09-27', 1, '2026-09-27')")
    run("INSERT INTO bills (user_id, name, amount, due_date) VALUES (1, 'Doğalgaz', 700, ?)",
        ((utils.today() + timedelta(days=2)).isoformat(),))


# ---------- Testler ----------
def test_week_bounds():
    assert wk.week_start(date(2026, 10, 4)) == W and wk.week_start(W) == W and wk.week_start(date(2026, 10, 5)) == date(2026, 10, 5)
    t = date(2026, 10, 4)
    assert wk.pick_week("2026-09-30", t) == W and wk.pick_week("2026-09-21", t) == P
    assert wk.pick_week("2026-10-12", t) == W and wk.pick_week("abc", t) == W and wk.pick_week(None, t) == W
    with app.app_context():
        assert wk.week_label(W) == "28 Eyl – 4 Eki" and wk.week_label(date(2025, 12, 29)) == "29 Ara 2025 – 4 Oca 2026"
        assert wk.week_label(date(2025, 9, 29)) == "29 Eyl – 5 Eki 2025"
    assert wk.week_status(W, t) == "Bu hafta · değerlendirme günü"
    assert wk.week_status(W, date(2026, 10, 3)) == "Bu hafta (devam ediyor)" and wk.week_status(P, t) == "Geçen hafta"
    # Pazar 23:59 hâlâ bu hafta (değerlendirme günü); gelecek haftaya geçilmez
    NOW[0] = datetime(2026, 10, 4, 23, 59, tzinfo=TZ)
    page = ADMIN.text("/haftalik/")
    assert "<h2>28 Eyl – 4 Eki</h2>" in page and "Bu hafta · değerlendirme günü" in page and "Sonraki ›</a>" not in page
    assert "hafta=2026-09-21" in page and "Geçen haftayı" not in page
    # Pazartesi 00:01 yerel (UTC'de hâlâ pazar): yeni hafta, geçen hafta değerlendirilmediyse hatırlatılır
    NOW[0] = datetime(2026, 10, 5, 0, 1, tzinfo=TZ)
    assert NOW[0].astimezone(timezone.utc).date() == date(2026, 10, 4)
    page = ADMIN.text("/haftalik/")
    assert "<h2>5 Eki – 11 Eki</h2>" in page and "Bu hafta (devam ediyor)" in page
    assert "Geçen haftayı (28 Eyl – 4 Eki) değerlendirmedin" in page
    assert "<h2>28 Eyl – 4 Eki</h2>" in ADMIN.text("/haftalik/?hafta=2026-10-04")  # haftanın herhangi bir günü
    # Çarşamba: devam eden hafta; geçen hafta notu sadece pazartesi
    NOW[0] = WEDNESDAY
    page = ADMIN.text("/haftalik/")
    assert "<h2>28 Eyl – 4 Eki</h2>" in page and "Bu hafta (devam ediyor)" in page and "Geçen haftayı" not in page
    # Geçmiş hafta: "Geçen hafta" ve sonraki hafta bağlantısı
    NOW[0] = SUNDAY
    page = ADMIN.text("/haftalik/?hafta=2026-09-23")
    assert "<h2>21 Eyl – 27 Eyl</h2>" in page and "Geçen hafta" in page and "Sonraki ›</a>" in page
    assert "hafta=2026-09-28" in page and "Önümüzdeki 7 gün" not in page
    print("  week bounds OK")


def test_numbers():
    n = numbers(1, W, SUNDAY)
    e = n["expenses"]
    assert e["total"] == 1000 and e["count"] == 3 and e["top"] == ("Market", 800) and e["top_pct"] == 80
    assert e["prev"] == 800 and e["change_pct"] == 25 and e["change"] > 0 and e["cmp_label"] == "önceki haftaya göre"
    h = n["habits"]
    assert (h["done"], h["expected"], h["rate"]) == (10, 19, 53) and h["best"]["name"] == "Su"
    assert [(s["name"], s["done"], s["expected"]) for s in h["habits"]] == [("Su", 7, 7), ("Kitap", 2, 5), ("Koşu", 1, 7)]
    assert n["tasks"] == {"todo": 3, "shopping": 1}
    assert n["journal"]["days"] == 3 and n["journal"]["avg"] == 4.5
    tm = n["time"]
    assert tm["total"] == 3 * 3600 + 3600 + 900 and tm["count"] == 3 and tm["top"]["name"] == "Web"
    assert n["watch"]["words"] == [(1, "kitap")] and n["watch"]["titles"] == [("📚", "Kürk Mantolu Madonna")]
    assert n["cards"] == 1 and n["bills"] == {"count": 1, "total": 450} and not n["empty"]
    # Devam eden hafta (çarşamba): önceki haftanın aynı günleriyle (pazartesi – çarşamba), alışkanlıklar bugüne kadar
    n = numbers(1, W, WEDNESDAY)
    assert n["expenses"]["prev"] == 400 and n["expenses"]["change_pct"] == 100
    assert n["expenses"]["cmp_label"] == "önceki haftanın aynı günlerine göre"
    assert (n["habits"]["done"], n["habits"]["expected"]) == (5, 7) and n["habits"]["best"]["name"] == "Su"
    # Önceki hafta: kendinden önceki hafta verisi yok -> değişim yok
    p = numbers(1, P, SUNDAY)
    assert p["expenses"]["total"] == 800 and p["expenses"]["change"] is None and p["tasks"] == {"todo": 1, "shopping": 0}
    assert p["bills"] == {"count": 1, "total": 120} and p["watch"]["words"] == [(1, "film")] and p["cards"] == 1
    assert p["time"]["total"] == 3600 and p["journal"]["days"] == 1
    # Başkasının verisi karışmaz
    a = numbers(AYSE, W, SUNDAY)
    assert a["expenses"]["total"] == 99999 and a["journal"]["days"] == 1 and a["habits"]["done"] == 1
    assert a["tasks"] is None and a["cards"] is None and a["time"] is None
    assert numbers(BOS, W, SUNDAY)["empty"]
    # Sayfa
    page = ADMIN.text("/haftalik/?hafta=2026-09-28")
    for text in ("1.000 ₺", "▲ %25", "önceki haftaya göre: 800 ₺", "Market · 800 ₺ (%80)", "%53", "10/19 gün",
                 "💧 Su 7/7", "📚 Kitap 2/5", "Kürk Mantolu Madonna", "4 sa 15 dk", "🔵 Web · 3 sa", "Biten kanban kartı",
                 "450 ₺", "ortalama ruh hali 😄 4,5", "Önümüzdeki 7 gün", "Doğalgaz"):
        assert text in page, text
    assert "99.999" not in page and "Gizli" not in page and "Ayşe yoga" not in page
    assert "Bu hafta için kayıt yok" in BOS_C.text("/haftalik/")
    print("  numbers OK")


def test_form():
    r = ADMIN.post("/haftalik/kaydet", data={"hafta": "2026-09-30", "score": "4", "went_well": "Spor düzenli",
                                            "hard": "Uyku az", "learned": "Erken yat",
                                            "priority": ["Kitap bitir", "Doktora git", "Şarj aleti al"]})
    assert r.status_code == 302 and r.headers["Location"].endswith("/haftalik/?hafta=2026-09-28")
    row = review(1, "2026-09-28")
    assert (row["score"], row["went_well"], row["hard"], row["learned"]) == (4, "Spor düzenli", "Uyku az", "Erken yat")
    assert json.loads(row["priorities"]) == [{"text": "Kitap bitir", "done": False}, {"text": "Doktora git", "done": False},
                                             {"text": "Şarj aleti al", "done": False}]
    assert "Şarj" in row["priorities"]  # Türkçe harfler kaçışsız (arama için)
    # Güncelleme: aynı satır, boş öncelik atlanır, metin silinebilir
    ADMIN.post("/haftalik/kaydet", data={"hafta": "2026-09-28", "score": "5", "went_well": "Spor çok iyi", "hard": "",
                                        "learned": "", "priority": ["Kitap bitir", "", "Yeni iş"]})
    row = review(1, "2026-09-28")
    assert row["score"] == 5 and row["went_well"] == "Spor çok iyi" and row["hard"] == "" and row["learned"] == ""
    assert [p["text"] for p in json.loads(row["priorities"])] == ["Kitap bitir", "Yeni iş"]
    assert one("SELECT COUNT(*) AS n FROM weekly_reviews WHERE user_id = 1")["n"] == 1
    # Puan seçilmeden kaydedilirse eski puan korunur
    ADMIN.post("/haftalik/kaydet", data={"hafta": "2026-09-28", "went_well": "Spor çok iyi", "priority": ["Kitap bitir", "Yeni iş"]})
    assert review(1, "2026-09-28")["score"] == 5
    # Doğrulama: puan 1–5 dışı, 3'ten fazla öncelik
    for bad in ("0", "6", "abc", "-1"):
        r = ADMIN.post("/haftalik/kaydet", data={"hafta": "2026-09-28", "score": bad, "went_well": "bozuk"},
                       follow_redirects=True)
        assert "1 ile 5 arasında" in flashed(r), bad
    r = ADMIN.post("/haftalik/kaydet", data={"hafta": "2026-09-28", "score": "3", "priority": ["a", "b", "c", "d"]},
                   follow_redirects=True)
    assert "en fazla 3 öncelik" in flashed(r)
    row = review(1, "2026-09-28")
    assert row["score"] == 5 and row["went_well"] == "Spor çok iyi" and len(json.loads(row["priorities"])) == 2
    # Boş form değerlendirmesi olmayan haftaya kaydedilmez; gelecek hafta ve geçersiz tarih reddedilir
    r = ADMIN.post("/haftalik/kaydet", data={"hafta": "2026-09-14", "priority": ["", "", ""]}, follow_redirects=True)
    assert "Bir puan seç" in flashed(r) and review(1, "2026-09-14") is None
    for raw in ("2026-10-05", "abc", ""):
        r = ADMIN.post("/haftalik/kaydet", data={"hafta": raw, "score": "3"}, follow_redirects=True)
        assert "geçmiş haftalar" in flashed(r), raw
    assert review(1, "2026-10-05") is None
    # Sayfada form dolu gelir
    page = ADMIN.text("/haftalik/")
    assert 'name="score" value="5" checked' in page and "Spor çok iyi" in page and 'value="Yeni iş"' in page
    assert page.count('name="priority"') == 3 and "Sil</button>" in page
    print("  form OK")


def test_last_week_priorities():
    # Önceki haftanın değerlendirmesi: bu haftanın öncelikleri
    ADMIN.post("/haftalik/kaydet", data={"hafta": "2026-09-21", "score": "3", "went_well": "Taşınma bitti",
                                        "priority": ["Kitap bitir", "Doktora git", "Spor <3"]})
    page = ADMIN.text("/haftalik/?hafta=2026-09-28")
    card = page.split("📌 Bu haftanın öncelikleri", 1)[1].split("</section>", 1)[0]
    assert "0/3 · %0" in card and "Geçen hafta (21 Eyl – 27 Eyl)" in card and "Spor &lt;3" in card
    assert card.count('type="checkbox" name="done"') == 3
    r = ADMIN.post("/haftalik/oncelikler", data={"hafta": "2026-09-28", "done": ["0", "2", "7", "x"]}, follow_redirects=True)
    assert "2/3 yapıldı (%67)" in flashed(r)
    assert [p["done"] for p in json.loads(review(1, "2026-09-21")["priorities"])] == [True, False, True]
    card = ADMIN.text("/haftalik/").split("📌 Bu haftanın öncelikleri", 1)[1].split("</section>", 1)[0]
    assert "2/3 · %67" in card and 'value="0" data-autosubmit checked' in card and 'value="1" data-autosubmit >' in card
    # Metni değişmeyen önceliğin işareti korunur, değişeninki sıfırlanır
    ADMIN.post("/haftalik/kaydet", data={"hafta": "2026-09-21", "score": "3", "went_well": "Taşınma bitti",
                                        "priority": ["Kitap bitir", "Doktor & diş", "Spor <3"]})
    items = json.loads(review(1, "2026-09-21")["priorities"])
    assert [(p["text"], p["done"]) for p in items] == [("Kitap bitir", True), ("Doktor & diş", False), ("Spor <3", True)]
    # Önceki haftası değerlendirilmemiş hafta ve başkası: 404
    assert ADMIN.post("/haftalik/oncelikler", data={"hafta": "2026-09-21"}).status_code == 404
    assert AYSE_C.post("/haftalik/oncelikler", data={"hafta": "2026-09-28", "done": ["1"]}).status_code == 404
    assert [p["done"] for p in json.loads(review(1, "2026-09-21")["priorities"])] == [True, False, True]
    print("  last week priorities OK")


def test_history():
    with app.app_context():
        wk.save_review(1, date(2026, 8, 31), score=2, texts={"went_well": "Tatil dönüşü"})
        wk.save_review(1, date(2026, 9, 7), texts={"hard": "Yoğun"})
        chart = wk.chart(1, SUNDAY.date())
        rows = wk.history(1)
    assert len(chart) == 12 and chart[0]["start"] == date(2026, 7, 13) and chart[-1]["start"] == W and chart[-1]["current"]
    assert [c["score"] for c in chart[-5:]] == [2, None, None, 3, 5] and chart[-1]["emoji"] == "😄"
    assert chart[0]["month"] == "Tem" and chart[3]["month"] == "Ağu" and chart[4]["month"] == ""
    assert [r["row"]["week_start"] for r in rows] == ["2026-09-28", "2026-09-21", "2026-09-07", "2026-08-31"]
    assert rows[1]["rate"] == (2, 3, 67) and rows[0]["rate"] == (0, 2, 0) and rows[2]["rate"] is None
    page = ADMIN.text("/haftalik/")
    hist = page.split("📈 Son 12 hafta", 1)[1].split("</section>", 1)[0]
    assert hist.count('class="bar') == 12 and hist.count("height: ") == 3 and "height: 90px" in hist
    assert "🎯 öncelikler 2/3 (%67)" in hist and "🎯 2 öncelik" in hist  # bu haftanınkiler henüz gelecek hafta
    assert 'title="31 Ağu – 6 Eyl: 😕 Pek iyi değil"' in hist and 'title="14 Eyl – 20 Eyl"' in hist
    listing = hist.split('<ul class="rows">', 1)[1]
    assert listing.index("28 Eyl – 4 Eki") < listing.index("21 Eyl – 27 Eyl") < listing.index("31 Ağu – 6 Eyl")
    assert "Taşınma bitti" in listing and "Tatil dönüşü" in listing and "14 Eyl – 20 Eyl" not in listing
    print("  history OK")


def test_isolation():
    page = AYSE_C.text("/haftalik/")
    assert "Spor çok iyi" not in page and "Taşınma bitti" not in page and "Henüz değerlendirme yok" in page
    assert "Bu haftanın öncelikleri" not in page
    assert AYSE_C.post("/haftalik/2026-09-28/sil").status_code == 404 and review(1, "2026-09-28") is not None
    AYSE_C.post("/haftalik/kaydet", data={"hafta": "2026-09-28", "score": "1", "went_well": "Ayşe haftası"})
    assert review(AYSE, "2026-09-28")["score"] == 1 and review(1, "2026-09-28")["score"] == 5
    assert "Ayşe haftası" not in ADMIN.text("/haftalik/") and "Ayşe haftası" in AYSE_C.text("/haftalik/")
    print("  isolation OK")


def test_search():
    page = ADMIN.text("/ara/?q=spor cok")
    assert "Haftalık Değerlendirme" in page and "Spor çok iyi" in page and "/haftalik/?hafta=2026-09-28" in page
    assert "28 Eyl – 4 Eki haftası 😄" in page
    page = ADMIN.text("/ara/?q=doktor dis")  # öncelik metni
    assert "21 Eyl – 27 Eyl haftası" in page and "Doktor &amp; diş" in page
    assert "Spor çok iyi" not in AYSE_C.text("/ara/?q=spor cok")
    assert "Ayşe haftası" not in ADMIN.text("/ara/?q=ayse haftasi")
    print("  search OK")


def test_bot_week():
    NOW[0] = SUNDAY
    CALLS.clear()
    say("/hafta", chat=100)
    text = sent()[-1]["text"]
    for part in ("🗓️ <b>Bu hafta</b> · 28 Eyl – 4 Eki · değerlendirme günü", "1.000 ₺", "▲ %25 önceki haftaya göre",
                 "🔥 Alışkanlık: %53 (10/19) · en iyi 💧 Su", "✅ 3 görev tamamlandı", "📓 Günlük: 3 gün",
                 "⏱️ Zaman takibi: 4 sa 15 dk · en çok Web", "🎬 Bitirilen: 1 kitap", "🗂️ 1 kart bitti",
                 "📌 <b>Bu haftanın öncelikleri</b> (2/3)", "✅ 1) Kitap bitir", "☐ 2) Doktor &amp; diş", "✅ 3) Spor &lt;3",
                 "/haftalik/"):
        assert part in text, (part, text)
    say("/hafta", chat=200)
    text = sent()[-1]["text"]
    assert "99.999" in text and "1.000 ₺" not in text and "öncelik yazılmamış" in text
    from pano.bot_commands import COMMANDS, HELP
    assert "hafta" in dict(COMMANDS) and "/hafta" in HELP
    print("  bot /hafta OK")


def test_daily_monday():
    # Pazartesi günlük özetinde geçen hafta yazılan öncelikler (HTML kaçışlı)
    NOW[0] = datetime(2026, 9, 28, 8, 0, tzinfo=TZ)
    CALLS.clear()
    say("/bugun", chat=100)
    text = sent()[-1]["text"]
    assert "📌 <b>Bu haftanın öncelikleri:</b> 1) Kitap bitir 2) Doktor &amp; diş 3) Spor &lt;3" in text, text
    say("/bugun", chat=200)  # Ayşe'nin geçen haftası yok
    assert "Bu haftanın öncelikleri" not in sent()[-1]["text"]
    NOW[0] = datetime(2026, 9, 29, 8, 0, tzinfo=TZ)  # salı
    say("/bugun", chat=100)
    assert "Bu haftanın öncelikleri" not in sent()[-1]["text"]
    NOW[0] = datetime(2026, 10, 5, 8, 0, tzinfo=TZ)  # sonraki pazartesi: 28 Eyl haftasının öncelikleri
    say("/bugun", chat=100)
    assert "📌 <b>Bu haftanın öncelikleri:</b> 1) Kitap bitir 2) Yeni iş" in sent()[-1]["text"]
    NOW[0] = SUNDAY
    print("  daily monday OK")


def test_telegram_prompt():
    # Ayarlar: saat boşsa varsayılan 20:00; Ayşe 21:00; Can kapalı
    r = ADMIN.post("/haftalik/hatirlatma", data={"on": "1", "time": ""}, follow_redirects=True)
    assert "20:00" in flashed(r) and one("SELECT weekly_prompt FROM users WHERE id = 1")["weekly_prompt"] == "20:00"
    AYSE_C.post("/haftalik/hatirlatma", data={"on": "1", "time": "21:00"})
    assert one("SELECT weekly_prompt FROM users WHERE id = ?", (AYSE,))["weekly_prompt"] == "21:00"
    assert 'name="on" value="1" checked' in ADMIN.text("/haftalik/")
    run("INSERT INTO expenses (user_id, amount, category, date) VALUES (1, 150, 'Market', '2026-10-06')")
    CALLS.clear()
    # Cumartesi ve pazar saatten önce gitmez
    NOW[0] = datetime(2026, 10, 10, 21, 0, tzinfo=TZ)
    assert cron()["weekly_asked"] == 0
    NOW[0] = datetime(2026, 10, 11, 19, 59, tzinfo=TZ)
    assert cron()["weekly_asked"] == 0 and not prompts()
    # 20:05: yöneticiye bir kez (webhook kurulu değil: butonsuz, form linkli)
    NOW[0] = datetime(2026, 10, 11, 20, 5, tzinfo=TZ)
    assert cron()["weekly_asked"] == 1
    (msg,) = prompts()
    assert msg["chat_id"] == "100" and not buttons(msg)
    for part in ("🗓️ <b>Haftalık değerlendirme zamanı</b>", "5 Eki – 11 Eki", "1.149 ₺", "▲ %15 önceki haftaya göre",
                 "Formu doldur →", "/haftalik/?hafta=2026-10-05", "yazmayı unutma"):
        assert part in msg["text"], (part, msg["text"])
    assert "Gizli" not in msg["text"]
    NOW[0] = datetime(2026, 10, 11, 20, 10, tzinfo=TZ)
    assert cron()["weekly_asked"] == 0 and len(prompts()) == 1
    # 21:05: Ayşe'ye (kendi verisiyle); kapalı olan Can'a hiç
    NOW[0] = datetime(2026, 10, 11, 21, 5, tzinfo=TZ)
    assert cron()["weekly_asked"] == 1 and [p["chat_id"] for p in prompts()] == ["100", "200"]
    assert "1.149" not in prompts()[-1]["text"]
    assert cron()["weekly_asked"] == 0
    with app.app_context():
        assert query_one("SELECT value FROM app_state WHERE key = 'weekly_ask:1'")["value"] == "2026-10-05"
    # Sonraki pazar, webhook kuruluyken: puan butonları; haftayı puanlamış olana gitmez
    run("INSERT OR REPLACE INTO app_state (key, value) VALUES ('telegram_webhook', 'https://x/telegram/webhook')")
    run("INSERT INTO weekly_reviews (user_id, week_start, score) VALUES (?, '2026-10-12', 3)", (AYSE,))
    NOW[0] = datetime(2026, 10, 18, 22, 0, tzinfo=TZ)
    assert cron()["weekly_asked"] == 1
    msg = prompts()[-1]
    assert msg["chat_id"] == "100" and buttons(msg) == [f"wk:2026-10-12:{n}" for n in range(1, 6)]
    assert "Bir emojiye dokun" in msg["text"]
    # Kapatma
    ADMIN.post("/haftalik/hatirlatma", data={"time": "20:00"})
    assert one("SELECT weekly_prompt FROM users WHERE id = 1")["weekly_prompt"] is None
    run("DELETE FROM app_state WHERE key = 'weekly_ask:1'")
    assert cron()["weekly_asked"] == 0
    NOW[0] = SUNDAY
    print("  telegram prompt OK")


def test_score_button():
    NOW[0] = datetime(2026, 10, 11, 20, 30, tzinfo=TZ)
    CALLS.clear()
    press("wk:2026-10-05:4", chat=100)
    assert review(1, "2026-10-05")["score"] == 4
    edit = [p for m, p in CALLS if m == "editMessageText"][-1]
    assert "Kaydedildi" in edit["text"] and "🙂 İyi" in edit["text"] and "/haftalik/?hafta=2026-10-05" in edit["text"]
    # Başka kullanıcının sohbetinden: sadece kendi haftasına yazar; bağlı olmayan sohbet hiçbir şey yapamaz
    press("wk:2026-10-05:1", chat=200)
    assert review(1, "2026-10-05")["score"] == 4 and review(AYSE, "2026-10-05")["score"] == 1
    count = one("SELECT COUNT(*) AS n FROM weekly_reviews")["n"]
    press("wk:2026-10-05:2", chat=999)
    answers = [p for m, p in CALLS if m == "answerCallbackQuery"]
    assert "bağlı değil" in answers[-1]["text"] and one("SELECT COUNT(*) AS n FROM weekly_reviews")["n"] == count
    # Geçersiz: puan, gelecek hafta, pazartesi olmayan gün, 4 haftadan eski, bozuk veri
    for data in ("wk:2026-10-05:9", "wk:2026-10-12:3", "wk:2026-10-06:3", "wk:2026-08-31:3", "wk:abc:3", "wk:2026-10-05"):
        press(data, chat=100)
    assert review(1, "2026-10-05")["score"] == 4 and review(1, "2026-10-12") is None
    assert review(1, "2026-10-06") is None and review(1, "2026-08-31")["score"] == 2
    assert "artık kaydedilemiyor" in [p for m, p in CALLS if m == "answerCallbackQuery"][-3]["text"]
    # Dört hafta öncesine kadar yazılır
    press("wk:2026-09-07:3", chat=100)
    assert review(1, "2026-09-07")["score"] == 3 and review(1, "2026-09-07")["hard"] == "Yoğun"
    # Metni olan haftada sadece puan değişir
    press("wk:2026-09-28:2", chat=100)
    row = review(1, "2026-09-28")
    assert row["score"] == 2 and row["went_well"] == "Spor çok iyi" and "Yeni iş" in row["priorities"]
    NOW[0] = SUNDAY
    print("  score button OK")


def test_delete_restore():
    r = ADMIN.post("/haftalik/2026-09-30/sil", follow_redirects=True)
    assert "çöp kutusuna taşındı" in flashed(r) and review(1, "2026-09-28") is None
    with app.app_context():
        item = query("SELECT * FROM trash WHERE user_id = 1 AND module = 'weekly'")[-1]
    assert "28 Eyl – 4 Eki" in item["label"]
    assert "Spor çok iyi" not in ADMIN.text("/haftalik/")
    ADMIN.post(f"/cop-kutusu/{item['id']}/geri")
    row = review(1, "2026-09-28")
    assert row is not None and row["went_well"] == "Spor çok iyi" and row["score"] == 2
    assert "Spor çok iyi" in ADMIN.text("/haftalik/")
    assert ADMIN.post("/haftalik/2026-08-03/sil").status_code == 404
    print("  delete/restore OK")


def test_login_required():
    r = app.test_client().get("/haftalik/")
    assert r.status_code == 302 and "/giris" in r.headers["Location"]
    print("  login required OK")


if __name__ == "__main__":
    seed()
    test_week_bounds()
    test_numbers()
    test_form()
    test_last_week_priorities()
    test_history()
    test_isolation()
    test_search()
    test_bot_week()
    test_daily_monday()
    test_telegram_prompt()
    test_score_button()
    test_delete_restore()
    test_login_required()
    print("OK")
