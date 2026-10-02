"""Ev Bakımı: periyodik işler, hazır şablonlar, ✅ Yaptım / geri al, Telegram hatırlatması ve butonları.

Telegram ve saat taklit edilir; dış servis çağrılmaz.
Çalıştır: .venv/Scripts/python tests/test_homecare.py
"""
import calendar
import json
import os
import re
import sys
from datetime import date, datetime, time, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

app = make_app()
os.environ["CRON_SECRET"] = "gizli"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.external as ext  # noqa: E402
import pano.telegram as tg  # noqa: E402
import pano.todo_reminders as todo  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.calendar_events import events_between  # noqa: E402
from pano.db import execute, query, query_one  # noqa: E402
from pano.modules import homecare as hc  # noqa: E402
from pano.utils import TZ, add_months, today  # noqa: E402

CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {})) or {"message_id": 7}
ext.weather = lambda lat, lon: None
ext.rates = lambda: None
T = today()
NOW = [datetime.combine(T, time(10, 0), tzinfo=TZ)]
todo.now_local = lambda: NOW[0]

with app.app_context():
    AYSE = create_user("ayse", "ayse12345")
with app.test_request_context():
    SECRET = tg.webhook_secret()
ADMIN = Client(app)
AYSE_C = Client(app, "ayse", "ayse12345")
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


def D(n):
    return (T + timedelta(days=n)).isoformat()


def at(n, hour=10, minute=0):
    return datetime.combine(T + timedelta(days=n), time(hour, minute), tzinfo=TZ)


def task_id(name, user_id=1):
    row = one("SELECT id FROM home_tasks WHERE name = ? AND user_id = ?", (name, user_id))
    return row["id"] if row else None


def task(tid):
    return one("SELECT * FROM home_tasks WHERE id = ?", (tid,))


def logs(tid):
    return rows("SELECT * FROM home_task_logs WHERE task_id = ? ORDER BY id", (tid,))


def count(user_id=1):
    return one("SELECT COUNT(*) AS n FROM home_tasks WHERE user_id = ?", (user_id,))["n"]


def add(client, name, **fields):
    data = {"name": name, "interval_n": "1", "interval_unit": "year", "category": "other"}
    data.update({k: str(v) for k, v in fields.items()})
    client.post("/ev-bakimi/yeni", data=data, follow_redirects=True)  # bildirim burada okunur
    return task_id(name, AYSE if client is AYSE_C else 1)


def reset():
    run("DELETE FROM home_tasks")


def task_rows(client, url="/ev-bakimi/"):
    """Sayfadaki iş satırları (bildirim, form ve şablon listesi hariç)."""
    page = client.text(url)
    return page.split('<ul class="rows">', 1)[1] if '<ul class="rows">' in page else ""


def row_of(html, name):
    return next(li for li in re.findall(r"<li>(.*?)</li>", html, re.S) if name in li)


def calls(method):
    return [p for m, p in CALLS if m == method]


def sent():
    return [(p["chat_id"], p["text"], p.get("reply_markup")) for p in calls("sendMessage")]


def buttons(markup):
    return [b["callback_data"] for row in json.loads(markup or "{}").get("inline_keyboard", []) for b in row]


def cron(path="hatirlatma"):
    r = RAW.get(f"/cron/gizli/{path}")
    assert r.status_code == 200, r.status_code
    return r.json


def press(data, chat=100):
    RAW.post("/telegram/webhook", data=json.dumps({"callback_query": {
        "id": "cb", "data": data, "from": {"id": chat}, "message": {"message_id": 7, "chat": {"id": chat}}}}),
        content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})


def test_period():
    adv = hc.advance
    assert adv(date(2026, 1, 31), 1, "month") == date(2026, 2, 28)       # ay sonu taşmaz
    assert adv(date(2028, 1, 31), 1, "month") == date(2028, 2, 29)       # artık yıl
    assert adv(date(2026, 3, 31), 1, "month") == date(2026, 4, 30)
    assert adv(date(2026, 8, 31), 6, "month") == date(2027, 2, 28)
    assert adv(date(2026, 11, 15), 3, "month") == date(2027, 2, 15)      # yıl atlar
    assert adv(date(2028, 2, 29), 1, "year") == date(2029, 2, 28)
    assert adv(date(2026, 10, 2), 2, "year") == date(2028, 10, 2)
    assert adv(date(2026, 12, 28), 1, "week") == date(2027, 1, 4)
    assert adv(date(2026, 10, 2), 2, "week") == date(2026, 10, 16)
    assert adv(date(2026, 10, 25), 10, "day") == date(2026, 11, 4)
    assert hc.period_label(1, "year") == "Yılda bir" and hc.period_label(3, "month") == "3 ayda bir"
    assert hc.period_label(1, "day") == "Her gün" and hc.period_label(2, "week") == "2 haftada bir"
    assert hc.period_label(1, "week") == "Haftada bir" and hc.period_label(2, "year") == "2 yılda bir"
    # Şablonun ilk tarihi: uygun ayı varsa o ayın 1'i (o aydaysak bugün), yoksa bugün + periyot
    kombi, klima = hc.TEMPLATES["kombi"], hc.TEMPLATES["klima_bakim"]
    assert hc.template_due(kombi, date(2026, 10, 2)) == date(2026, 10, 2)
    assert hc.template_due(kombi, date(2026, 3, 5)) == date(2026, 10, 1)
    assert hc.template_due(kombi, date(2026, 11, 5)) == date(2027, 10, 1)
    assert hc.template_due(klima, date(2026, 10, 2)) == date(2027, 5, 1)
    assert hc.template_due(hc.TEMPLATES["klima_filtre"], date(2026, 11, 30)) == date(2027, 2, 28)
    assert hc.template_due(hc.TEMPLATES["aritma_membran"], date(2026, 10, 2)) == date(2028, 10, 2)
    print("  period OK")


def test_crud():
    assert "Henüz iş yok" in ADMIN.text("/ev-bakimi/")
    kid = add(ADMIN, "Kombi bakımı", category="heating", interval_n=1, interval_unit="year", next_due=D(20),
              remind_days=7, notes="Servis: 0332 111 22 33")
    k = task(kid)
    assert (k["icon"], k["category"], k["interval_n"], k["interval_unit"], k["next_due"], k["remind_days"], k["active"]) \
        == ("🔥", "heating", 1, "year", D(20), 7, 1)
    # Tarih boşsa bugün + periyot; geçersiz hatırlatma -> 3 gün; bilinmeyen kategori -> Diğer; ikon verilirse o
    sid = add(ADMIN, "Saksı toprağı", category="yok", icon="🪴", interval_n=2, interval_unit="week", remind_days=99)
    s = task(sid)
    assert (s["category"], s["icon"], s["next_due"], s["remind_days"]) == ("other", "🪴", D(14), 3)
    # Reddedilenler: ad yok, periyot 0 / çok büyük / sayı değil / taşan sayı
    before = count()
    for bad in ({"name": ""}, {"interval_n": "0"}, {"interval_n": "1000"}, {"interval_n": "abc"},
                {"interval_n": "1e999"}, {"interval_n": "-3"}):
        ADMIN.post("/ev-bakimi/yeni", data={"name": "Hatalı", "interval_n": "1", "interval_unit": "month", **bad})
    assert count() == before
    page = ADMIN.text("/ev-bakimi/")
    assert "İşin adı gerekli" in page or "Periyot 1-365" in page

    page = task_rows(ADMIN)
    assert "Kombi bakımı" in page and "Yılda bir" in page and "2 haftada bir" in page and "🪴" in page
    detail = ADMIN.text(f"/ev-bakimi/{kid}")
    assert "Servis: 0332 111 22 33" in detail and "Isıtma / soğutma · Yılda bir" in detail
    assert "Henüz kayıt yok" in detail

    # Düzenleme: ikon boşsa yeni kategorinin ikonu, tarih boşsa (kayıt yokken) bugün + periyot
    ADMIN.post(f"/ev-bakimi/{sid}/duzenle", data={"name": "Saksı toprağı", "category": "garden", "icon": "",
                                                   "interval_n": "1", "interval_unit": "month", "remind_days": "1"})
    s = task(sid)
    assert (s["category"], s["icon"], s["interval_unit"], s["next_due"], s["remind_days"]) \
        == ("garden", "🌿", "month", add_months(T, 1).isoformat(), 1)
    ADMIN.post(f"/ev-bakimi/{sid}/duzenle", data={"name": "", "interval_n": "1"})  # ad boşsa değişmez
    assert task(sid)["name"] == "Saksı toprağı"

    # Kategoriye göre süzme
    garden = task_rows(ADMIN, "/ev-bakimi/?kategori=garden")
    assert "Saksı toprağı" in garden and "Kombi bakımı" not in garden
    assert 'kategori=heating' in ADMIN.text("/ev-bakimi/") and 'kategori=water' not in ADMIN.text("/ev-bakimi/")
    assert "Kombi bakımı" in task_rows(ADMIN, "/ev-bakimi/?kategori=bilinmeyen")  # bilinmeyen -> tümü

    # Pasif işler ayrı ve katlanmış listede; pasife alınınca rozet / Yaptım yok
    ADMIN.post(f"/ev-bakimi/{sid}/durum")
    assert task(sid)["active"] == 0
    html = task_rows(ADMIN)
    active_part, passive_part = html.split("Pasif işler", 1)
    assert "Saksı toprağı" not in active_part and "Saksı toprağı" in passive_part
    assert '<details class="passive">' in ADMIN.text("/ev-bakimi/")
    ADMIN.post(f"/ev-bakimi/{sid}/durum")
    assert task(sid)["active"] == 1

    # Başkası göremez, değiştiremez
    assert "Henüz iş yok" in AYSE_C.text("/ev-bakimi/")
    assert AYSE_C.get(f"/ev-bakimi/{kid}").status_code == 404
    for path in ("duzenle", "yaptim", "geri-al", "durum", "sil"):
        assert AYSE_C.post(f"/ev-bakimi/{kid}/{path}", data={"name": "X", "interval_n": "1"}).status_code == 404
    k2 = task(kid)
    assert (k2["name"], k2["next_due"], k2["active"]) == ("Kombi bakımı", D(20), 1) and not logs(kid)
    print("  crud OK")


def test_templates():
    before = count()
    # "Kombi bakımı" elle eklenmişti: şablondan tekrar eklenmez; bilinmeyen anahtar yok sayılır
    r = ADMIN.post("/ev-bakimi/sablonlar", data={"tpl": ["kombi", "klima_bakim", "aritma_filtre", "bulasik", "yok-boyle",
                                                          "bulasik"]}, follow_redirects=True)
    page = r.get_data(as_text=True)
    assert "3 iş eklendi" in page and "atlandı: Kombi bakımı" in page
    assert count() == before + 3
    assert one("SELECT COUNT(*) AS n FROM home_tasks WHERE user_id = 1 AND name = 'Kombi bakımı'")["n"] == 1
    klima = task(task_id("Klima bakımı"))
    assert (klima["icon"], klima["category"], klima["interval_n"], klima["interval_unit"], klima["remind_days"]) \
        == ("❄️", "heating", 1, "year", 7)
    assert klima["next_due"] == hc.template_due(hc.TEMPLATES["klima_bakim"], T).isoformat()
    assert task(task_id("Su arıtma filtresi"))["next_due"] == add_months(T, 6).isoformat()
    bulasik = task(task_id("Bulaşık makinesi filtresi"))
    assert (bulasik["next_due"], bulasik["remind_days"], bulasik["category"]) == (add_months(T, 1).isoformat(), 1, "kitchen")
    # İkinci kez: sadece yeniler eklenir
    ADMIN.post("/ev-bakimi/sablonlar", data={"tpl": ["klima_bakim", "dask"]})
    assert count() == before + 4 and task_id("DASK yenileme")
    assert one("SELECT COUNT(*) AS n FROM home_tasks WHERE user_id = 1 AND name = 'Klima bakımı'")["n"] == 1
    # Hepsi zaten varsa ya da hiçbiri seçilmediyse uyarı
    assert "Yeni iş eklenmedi" in ADMIN.post("/ev-bakimi/sablonlar", data={"tpl": ["dask"]},
                                              follow_redirects=True).get_data(as_text=True)
    assert "en az bir şablon" in ADMIN.post("/ev-bakimi/sablonlar", follow_redirects=True).get_data(as_text=True)
    assert count() == before + 4
    # Listede olanlar şablon listesinde işaretli ve seçilemez
    page = ADMIN.text("/ev-bakimi/")
    tpl_html = page.split('class="tpl-list"', 1)[1].split("</ul>", 1)[0]
    assert re.search(r'value="dask" disabled', tpl_html) and re.search(r'value="konut" >', tpl_html)
    assert "✓ listende" in tpl_html
    # Büyük/küçük harf ve Türkçe harf farkı da aynı ad sayılır
    add(ADMIN, "KONUT SİGORTASI YENİLEME")
    ADMIN.post("/ev-bakimi/sablonlar", data={"tpl": ["konut"]})
    assert task_id("KONUT SİGORTASI YENİLEME") and not task_id("Konut sigortası yenileme")
    # Başka kullanıcı kendi listesine aynı şablonları ekleyebilir
    AYSE_C.post("/ev-bakimi/sablonlar", data={"tpl": ["kombi", "dask"]})
    assert count(AYSE) == 2 and task_id("Kombi bakımı", AYSE)
    assert count() == before + 5
    print("  templates OK")


def test_done_and_undo():
    kid = task_id("Kombi bakımı")
    exp_before = one("SELECT COUNT(*) AS n FROM expenses")["n"]
    # Tutarlı, notlu, geçmişe dönük; harcamalara da eklenir
    r = ADMIN.post(f"/ev-bakimi/{kid}/yaptim", data={"done_on": D(-2), "cost": "1.250,50", "note": "Filtre değişti",
                                                    "add_expense": "1", "expense_category": "Ev"}, follow_redirects=True)
    page = r.get_data(as_text=True)
    assert "Kombi bakımı kaydedildi" in page and "Harcamalara da eklendi" in page
    (log,) = logs(kid)
    assert (log["done_on"], log["cost"], log["note"], log["prev_due"]) == (D(-2), 1250.5, "Filtre değişti", D(20))
    exp = one("SELECT * FROM expenses WHERE id = ?", (log["expense_id"],))
    assert (exp["user_id"], exp["amount"], exp["category"], exp["date"]) == (1, 1250.5, "Ev", D(-2))
    assert exp["note"] == "Kombi bakımı: Filtre değişti"
    first_next = add_months(T - timedelta(days=2), 12).isoformat()
    assert task(kid)["next_due"] == first_next
    # İkinci kayıt: tarih boş (bugün), tutarsız, harcamasız -> sıradaki bugün + 1 yıl
    ADMIN.post(f"/ev-bakimi/{kid}/yaptim", data={"note": "Basınç kontrolü"})
    assert len(logs(kid)) == 2 and logs(kid)[1]["done_on"] == T.isoformat() and logs(kid)[1]["cost"] is None
    assert task(kid)["next_due"] == add_months(T, 12).isoformat()
    # Tutar var ama "harcamalara ekle" işaretsiz -> harcama yok
    sid = task_id("Saksı toprağı")
    ADMIN.post(f"/ev-bakimi/{sid}/yaptim", data={"cost": "80"})
    assert logs(sid)[0]["cost"] == 80 and logs(sid)[0]["expense_id"] is None
    assert one("SELECT COUNT(*) AS n FROM expenses")["n"] == exp_before + 1
    # Ayrıntı sayfası: geçmiş, toplam harcanan, harcama bağlantısı, son kaydı geri al
    detail = ADMIN.text(f"/ev-bakimi/{kid}")
    assert "Filtre değişti" in detail and "Basınç kontrolü" in detail and "1.250,50 ₺" in detail
    assert "💸 harcamalarda" in detail and "Son kaydı geri al" in detail and "2 kayıt" in detail
    assert detail.index("Basınç kontrolü") < detail.index("Filtre değişti")  # yeni kayıt üstte
    # Reddedilenler: ileri tarih, bozuk / sıfır tutar
    for bad in ({"done_on": D(1)}, {"cost": "abc"}, {"cost": "0"}, {"cost": "-5"}):
        ADMIN.post(f"/ev-bakimi/{kid}/yaptim", data=bad)
    assert len(logs(kid)) == 2
    assert "ileri bir gün olamaz" in ADMIN.post(f"/ev-bakimi/{kid}/yaptim", data={"done_on": D(3)},
                                                follow_redirects=True).get_data(as_text=True)
    # Geriye dönük eski bir kayıt daha yeni kaydın önüne geçmez (sıradaki tarih değişmez)
    ADMIN.post(f"/ev-bakimi/{kid}/yaptim", data={"done_on": D(-400), "note": "Eski kayıt"})
    assert len(logs(kid)) == 3 and task(kid)["next_due"] == add_months(T, 12).isoformat()
    # Düzenlemede tarih boşsa son yapılma + yeni periyot
    k = task(kid)
    ADMIN.post(f"/ev-bakimi/{kid}/duzenle", data={"name": k["name"], "category": k["category"], "icon": k["icon"],
                                                   "interval_n": "6", "interval_unit": "month", "remind_days": "7",
                                                   "notes": k["notes"]})
    assert task(kid)["next_due"] == add_months(T, 6).isoformat()
    ADMIN.post(f"/ev-bakimi/{kid}/duzenle", data={"name": k["name"], "category": k["category"], "icon": k["icon"],
                                                   "interval_n": "1", "interval_unit": "year", "remind_days": "7",
                                                   "next_due": add_months(T, 12).isoformat(), "notes": k["notes"]})

    # Geri al: en son girilen kayıt silinir, sıradaki tarih o kayıttan önceki haline döner
    ADMIN.post(f"/ev-bakimi/{kid}/geri-al")
    assert [r["note"] for r in logs(kid)] == ["Filtre değişti", "Basınç kontrolü"]
    assert task(kid)["next_due"] == add_months(T, 12).isoformat()
    ADMIN.post(f"/ev-bakimi/{kid}/geri-al")
    assert task(kid)["next_due"] == first_next and len(logs(kid)) == 1
    r = ADMIN.post(f"/ev-bakimi/{kid}/geri-al", follow_redirects=True)
    assert "Harcama kaydı da silindi" in r.get_data(as_text=True)
    assert not logs(kid) and task(kid)["next_due"] == D(20)
    assert one("SELECT 1 FROM expenses WHERE id = ?", (log["expense_id"],)) is None
    assert "Geri alınacak kayıt yok" in ADMIN.post(f"/ev-bakimi/{kid}/geri-al", follow_redirects=True).get_data(as_text=True)

    # Ay sonu: 31 Ocak'ta yapılan aylık iş -> Şubat'ın son günü
    jan31 = date(T.year, 1, 31) if date(T.year, 1, 31) <= T else date(T.year - 1, 1, 31)
    mid = add(ADMIN, "Aylık kontrol", interval_n=1, interval_unit="month", next_due=jan31.isoformat())
    ADMIN.post(f"/ev-bakimi/{mid}/yaptim", data={"done_on": jan31.isoformat()})
    assert task(mid)["next_due"] == date(jan31.year, 2, calendar.monthrange(jan31.year, 2)[1]).isoformat()
    # Haftalık iş
    wid = add(ADMIN, "Haftalık iş", interval_n=1, interval_unit="week")
    assert task(wid)["next_due"] == D(7)
    ADMIN.post(f"/ev-bakimi/{wid}/yaptim", data={"done_on": D(-1)})
    assert task(wid)["next_due"] == D(6)
    # Listedeki hızlı "Yaptım": bugün, listeye geri döner
    r = ADMIN.post(f"/ev-bakimi/{wid}/yaptim", data={"next": "/ev-bakimi/?kategori=other"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/ev-bakimi/?kategori=other")
    assert task(wid)["next_due"] == D(7) and logs(wid)[-1]["done_on"] == T.isoformat()
    print("  done/undo OK")


def test_badges():
    reset()
    add(ADMIN, "Gecikmiş iş", next_due=D(-3), remind_days=1)
    add(ADMIN, "Yakın iş", next_due=D(2), remind_days=0)
    add(ADMIN, "Sigorta işi", next_due=D(20), remind_days=30)
    add(ADMIN, "Uzak iş", next_due=D(60), remind_days=7)
    page = ADMIN.text("/ev-bakimi/")
    assert "4 etkin iş · 1 gecikmiş · 1 iş 7 gün içinde" in page
    html = task_rows(ADMIN)
    assert 'badge overdue' in row_of(html, "Gecikmiş iş") and "3 gün geçti" in row_of(html, "Gecikmiş iş")
    assert 'badge soon' in row_of(html, "Yakın iş") and "2 gün sonra" in row_of(html, "Yakın iş")
    assert 'badge soon' in row_of(html, "Sigorta işi")  # 30 gün önce hatırlatılan iş 20 gün kala turuncu
    assert 'badge later' in row_of(html, "Uzak iş")
    # Sıralama: sıradaki tarihe göre
    assert html.index("Gecikmiş iş") < html.index("Yakın iş") < html.index("Sigorta işi") < html.index("Uzak iş")
    assert 'badge overdue' in ADMIN.text(f"/ev-bakimi/{task_id('Gecikmiş iş')}")
    print("  badges OK")


def test_telegram_reminders():
    reset()
    run("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
    run("UPDATE users SET telegram_chat_id = '200' WHERE id = ?", (AYSE,))
    run("INSERT OR REPLACE INTO app_state (key, value) VALUES ('telegram_webhook', 'https://x/telegram/webhook')")
    pre = add(ADMIN, "Klima filtresi", interval_n=3, interval_unit="month", next_due=D(3), remind_days=3,
              notes="Model X filtre")
    add(ADMIN, "Petek havası", next_due=D(5), remind_days=3)
    day = add(ADMIN, "Bulaşık filtresi", interval_n=1, interval_unit="month", next_due=D(0), remind_days=1)
    add(ADMIN, "Dedektör pili", next_due=D(-2), remind_days=7)
    add(ADMIN, "Sessiz iş", next_due=D(1), remind_days=0)  # önceden hatırlatma yok, sadece günü gelince
    paused = add(ADMIN, "Durdurulan iş", next_due=D(0))
    ADMIN.post(f"/ev-bakimi/{paused}/durum")
    add(AYSE_C, "Ayşe işi", next_due=D(0))

    def new_after(n):
        return sent()[n:]

    # 09:00'dan önce gönderilmez
    NOW[0] = at(0, 8, 30)
    assert cron()["homecare_sent"] == 0
    NOW[0] = at(0, 9, 5)
    n = len(sent())
    assert cron()["homecare_sent"] == 4
    msgs = new_after(n)
    by_name = {name: m for name in ("Klima filtresi", "Bulaşık filtresi", "Dedektör pili", "Ayşe işi")
               for m in msgs if name in m[1]}
    assert len(by_name) == 4
    chat, text, markup = by_name["Klima filtresi"]
    assert chat == "100" and "3 gün sonra:" in text and "3 ayda bir" in text and "Model X filtre" in text
    assert f"/ev-bakimi/{pre}" in text
    assert buttons(markup) == [f"hm:done:{pre}", f"hm:snz:{pre}"]
    assert "Bugün:" in by_name["Bulaşık filtresi"][1] and "2 gün gecikti:" in by_name["Dedektör pili"][1]
    assert by_name["Ayşe işi"][0] == "200" and all(m[0] == "100" for name, m in by_name.items() if name != "Ayşe işi")
    assert not any("Petek" in m[1] or "Sessiz" in m[1] or "Durdurulan" in m[1] for m in msgs)
    # Aynı mesaj iki kez gitmez
    assert cron()["homecare_sent"] == 0

    # Ertesi gün: sadece günü gelen "Sessiz iş" (önceden hatırlatma yok); gecikenlere hemen tekrar gitmez
    NOW[0] = at(1, 9, 30)
    n = len(sent())
    assert cron()["homecare_sent"] == 1 and "Bugün:" in sent()[-1][1] and "Sessiz iş" in sent()[-1][1]
    NOW[0] = at(2, 9, 30)
    assert cron()["homecare_sent"] == 1 and "3 gün sonra:" in sent()[-1][1] and "Petek havası" in sent()[-1][1]
    NOW[0] = at(3, 9, 30)
    assert cron()["homecare_sent"] == 1 and "Bugün:" in sent()[-1][1] and "Klima filtresi" in sent()[-1][1]
    NOW[0] = at(5, 9, 30)
    assert cron()["homecare_sent"] == 1 and "Petek havası" in sent()[-1][1]
    NOW[0] = at(6, 9, 30)
    assert cron()["homecare_sent"] == 0
    # Gecikmiş iş: son mesajdan 7 gün sonra yeniden (haftada bir)
    NOW[0] = at(7, 9, 30)
    n = len(sent())
    assert cron()["homecare_sent"] == 3
    texts = [m[1] for m in new_after(n)]
    assert any("9 gün gecikti:" in t and "Dedektör pili" in t for t in texts)
    assert any("7 gün gecikti:" in t and "Bulaşık filtresi" in t for t in texts)
    assert any("Ayşe işi" in t for t in texts)
    assert cron()["homecare_sent"] == 0
    # Webhook yoksa buton gönderilmez
    run("DELETE FROM app_state WHERE key = 'telegram_webhook'")
    NOW[0] = at(8, 9, 30)
    assert cron()["homecare_sent"] == 1 and "Sessiz iş" in sent()[-1][1] and sent()[-1][2] is None
    run("INSERT INTO app_state (key, value) VALUES ('telegram_webhook', 'https://x/telegram/webhook')")
    NOW[0] = at(9, 9, 30)
    assert cron()["homecare_sent"] == 0
    NOW[0] = at(10, 9, 30)  # günü 3. gün gelmişti
    assert cron()["homecare_sent"] == 1 and "7 gün gecikti:" in sent()[-1][1] and "Klima filtresi" in sent()[-1][1]
    NOW[0] = at(12, 9, 30)
    assert cron()["homecare_sent"] == 1 and "Petek havası" in sent()[-1][1]
    NOW[0] = at(13, 9, 30)
    assert cron()["homecare_sent"] == 0
    NOW[0] = at(14, 9, 30)
    assert cron()["homecare_sent"] == 3
    # İş yapılınca (sıradaki tarih değişince) yeni tarih için hatırlatmalar yeniden kurulur
    ADMIN.post(f"/ev-bakimi/{day}/yaptim", data={"done_on": D(-1)})  # aylık: sıradaki ~1 ay sonra
    assert task(day)["next_due"] == add_months(T - timedelta(days=1), 1).isoformat()
    due = date.fromisoformat(task(day)["next_due"])
    NOW[0] = datetime.combine(due - timedelta(days=1), time(9, 30), tzinfo=TZ)
    n = len(sent())
    cron()
    assert any("Yarın:" in m[1] and "Bulaşık filtresi" in m[1] for m in new_after(n))
    NOW[0] = at(0)
    print("  telegram reminders OK")


def test_telegram_buttons():
    reset()
    run("INSERT OR REPLACE INTO app_state (key, value) VALUES ('telegram_webhook', 'https://x/telegram/webhook')")
    a = add(ADMIN, "Su arıtma filtresi", interval_n=6, interval_unit="month", next_due=D(3))
    b = add(ADMIN, "Yangın tüpü", next_due=D(-10))
    c = add(ADMIN, "Konut sigortası", next_due=D(2), remind_days=30)
    x = add(AYSE_C, "Ayşe filtresi", next_due=D(0))

    # ✅ Yaptım: bugünle kaydeder, sıradaki tarihi yazar; çift dokunuşta ikinci kayıt olmaz
    press(f"hm:done:{a}")
    assert [r["done_on"] for r in logs(a)] == [T.isoformat()]
    assert task(a)["next_due"] == add_months(T, 6).isoformat()
    assert calls("answerCallbackQuery")[-1]["text"] == "✅ Kaydedildi"
    edit = calls("editMessageText")[-1]
    assert "Su arıtma filtresi" in edit["text"] and "yapıldı" in edit["text"] and "Sıradaki" in edit["text"]
    assert edit["message_id"] == 7 and buttons(edit["reply_markup"]) == []  # butonlar kalkar
    press(f"hm:done:{a}")
    assert len(logs(a)) == 1 and "zaten" in calls("answerCallbackQuery")[-1]["text"]

    # ⏰ 1 hafta ertele: gecikmiş iş bugünden, yaklaşan iş kendi tarihinden 7 gün sonraya
    press(f"hm:snz:{b}")
    assert task(b)["next_due"] == D(7) and not logs(b)
    assert calls("answerCallbackQuery")[-1]["text"] == "⏰ 1 hafta ertelendi" and "ertelendi" in calls("editMessageText")[-1]["text"]
    press(f"hm:snz:{c}")
    assert task(c)["next_due"] == D(9)
    # Ertelenen işe ön hatırlatma hemen gelmez (30 gün önce hatırlatılsa da); mesaj yeni tarihin gününde gelir
    NOW[0] = at(0, 9, 30)
    n = len(sent())
    assert cron()["homecare_sent"] == 1 and sent()[-1][0] == "200"  # sadece Ayşe'nin bugünkü işi
    NOW[0] = at(7, 9, 30)
    assert cron()["homecare_sent"] == 2  # Yangın tüpü bugün + Ayşe'nin işi 7 gündür gecikmiş
    assert any("Bugün:" in t and "Yangın tüpü" in t for c, t, _m in sent()[-2:] if c == "100")
    NOW[0] = at(9, 9, 30)
    assert cron()["homecare_sent"] == 1 and "Bugün:" in sent()[-1][1] and "Konut sigortası" in sent()[-1][1]
    assert len(sent()) == n + 4
    NOW[0] = at(0)

    # Başkasının işine dokunulamaz
    before = (task(a)["next_due"], len(logs(a)), task(x)["next_due"])
    press(f"hm:done:{a}", chat=200)
    assert calls("answerCallbackQuery")[-1]["text"] == "İş bulunamadı, silinmiş olabilir."
    press(f"hm:snz:{a}", chat=200)
    press(f"hm:done:{x}", chat=100)
    press(f"hm:snz:{x}", chat=100)
    assert (task(a)["next_due"], len(logs(a)), task(x)["next_due"]) == before and not logs(x)
    # Bozuk veri ve bağlı olmayan sohbet
    for data in ("hm:xyz:1", "hm:done:abc", "hm:done", "hm"):
        press(data)
    press(f"hm:done:{a}", chat=999)
    assert calls("answerCallbackQuery")[-1]["text"] == "Bu sohbet panoya bağlı değil."
    assert len(logs(a)) == 1
    # Ayşe kendi işini tamamlayabilir
    press(f"hm:done:{x}", chat=200)
    assert len(logs(x)) == 1 and task(x)["next_due"] == add_months(T, 12).isoformat()
    print("  telegram buttons OK")


def test_integrations():
    reset()
    run("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
    add(ADMIN, "Su arıtma filtresi", interval_n=6, interval_unit="month", next_due=D(2), notes="Model AquaPro 5")
    add(ADMIN, "Yangın tüpü", next_due=D(-5))
    add(ADMIN, "Konut sigortası", next_due=D(20), remind_days=30)
    add(ADMIN, "Uzak iş", next_due=D(20), remind_days=3)
    paused = add(ADMIN, "Durdurulan iş", next_due=D(1))
    ADMIN.post(f"/ev-bakimi/{paused}/durum", follow_redirects=True)
    add(AYSE_C, "Ayşenin filtresi", next_due=D(1))

    # Pano: gecikenler, 7 gün içindekiler ve hatırlatma süresi içindekiler; pasif yok
    home = ADMIN.text("/")
    assert "Su arıtma filtresi" in home and "Yangın tüpü" in home and "Konut sigortası" in home
    assert "Uzak iş" not in home and "Durdurulan iş" not in home and "Ayşenin filtresi" not in home
    assert "Ayşenin filtresi" in AYSE_C.text("/") and "Yangın tüpü" not in AYSE_C.text("/")

    # Takvim: etkin işlerin sıradaki tarihi
    with app.test_request_context():
        evs = [e for e in events_between(1, T - timedelta(days=10), T + timedelta(days=30)) if e["kind"] == "home"]
    assert {e["title"] for e in evs} == {"Su arıtma filtresi", "Yangın tüpü", "Konut sigortası", "Uzak iş"}
    assert all(e["url"].startswith("/ev-bakimi/") for e in evs)
    assert next(e for e in evs if e["title"] == "Su arıtma filtresi")["detail"] == "6 ayda bir"
    assert "Uzak iş" in ADMIN.text(f"/takvim/?ay={D(20)[:7]}")
    assert "Ayşenin filtresi" not in ADMIN.text(f"/takvim/?ay={D(1)[:7]}")

    # Arama: ad ve not, Türkçe harf duyarsız; başkasınınki görünmez
    assert "Su arıtma filtresi" in ADMIN.text("/ara/?q=aquapro")
    assert "Yangın tüpü" in ADMIN.text("/ara/?q=yangin tupu")
    assert "Durdurulan iş (pasif)" in ADMIN.text("/ara/?q=durdurulan")
    assert "Ayşenin filtresi" not in ADMIN.text("/ara/?q=filtresi")
    assert "Su arıtma" not in AYSE_C.text("/ara/?q=aquapro")

    # Günlük özet: bugün / gecikmiş / 3 gün içindekiler "Yaklaşanlar"da
    n = len(sent())
    cron("gunluk")
    daily = dict((c, t) for c, t, _m in sent()[n:])
    assert "Yangın tüpü" in daily["100"] and "5 gün geçti" in daily["100"] and "Su arıtma filtresi" in daily["100"]
    assert "Konut sigortası" not in daily["100"] and "Durdurulan iş" not in daily["100"]
    print("  integrations OK")


def test_trash():
    tid = add(ADMIN, "Silinecek iş", next_due=D(4), notes="geri gelsin")
    ADMIN.post(f"/ev-bakimi/{tid}/yaptim", data={"note": "bir kez yapıldı", "done_on": D(-1)})
    log_ids = [r["id"] for r in logs(tid)]
    r = ADMIN.post(f"/ev-bakimi/{tid}/sil", follow_redirects=True)
    assert "çöp kutusuna taşındı" in r.get_data(as_text=True)
    assert task(tid) is None and not logs(tid)
    item = one("SELECT * FROM trash WHERE module = 'homecare' ORDER BY id DESC LIMIT 1")
    assert item["label"] == "🔧 Silinecek iş" and item["user_id"] == 1
    assert "Silinecek iş" in ADMIN.text("/cop-kutusu/")
    # Başkası geri getiremez
    AYSE_C.post(f"/cop-kutusu/{item['id']}/geri")
    assert task(tid) is None
    ADMIN.post(f"/cop-kutusu/{item['id']}/geri")
    t = task(tid)
    assert t and t["notes"] == "geri gelsin" and [r["id"] for r in logs(tid)] == log_ids
    assert "bir kez yapıldı" in ADMIN.text(f"/ev-bakimi/{tid}")
    print("  trash OK")


if __name__ == "__main__":
    test_period()
    test_crud()
    test_templates()
    test_done_and_undo()
    test_badges()
    test_telegram_reminders()
    test_telegram_buttons()
    test_integrations()
    test_trash()
    print("OK")
