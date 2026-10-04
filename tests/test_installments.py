"""Taksitler: kart ve alışveriş doğrulaması, ekstre dönemi (kesim gününden önce / sonra, 31'inde kesim, yıl geçişi),
taksit yuvarlaması, ekstre toplamı, 12 aylık yük, kalan borç ve ilerleme, erken kapama, ödendi / geri al, harcamalara
işleme (üç biçim), Telegram hatırlatması ve ✅ Ödendi butonu, /taksit, yaklaşanlar / takvim / arama, kullanıcı yalıtımı,
çöp kutusu, formlarda kart numarası alanı olmaması.

Telegram ve saat taklit edilir; dış servis çağrılmaz.
Çalıştır: .venv/Scripts/python tests/test_installments.py
"""
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
from pano.modules import installments as ins  # noqa: E402
from pano.utils import MONTHS_TR, TZ, fmt_money, today  # noqa: E402

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
UPDATE_ID = [0]


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


def count(table, user_id=None):
    if user_id is None:
        return one(f"SELECT COUNT(*) AS n FROM {table}")["n"]
    return one(f"SELECT COUNT(*) AS n FROM {table} WHERE user_id = ?", (user_id,))["n"]


def card(cid):
    return one("SELECT * FROM credit_cards WHERE id = ?", (cid,))


def purchase(pid):
    return one("SELECT * FROM card_purchases WHERE id = ?", (pid,))


def book(user_id=1):
    with app.test_request_context():
        return ins.load(user_id)


def stmt(cid, period, t=None, user_id=1):
    b = book(user_id)
    return ins.statement(b, b["cards"][cid], period, t or T)


def reset():
    for table in ("card_statements", "card_purchases", "credit_cards", "expenses"):
        run(f"DELETE FROM {table}")


def add_card(client, name, day, due=10, **fields):
    """Formdan kart; yeni kaydın id'si (eklenmediyse None)."""
    data = {"name": name, "statement_day": str(day), "due_days": str(due)}
    data.update({k: str(v) for k, v in fields.items()})
    before = one("SELECT MAX(id) AS m FROM credit_cards")["m"] or 0
    client.post("/taksitler/kart/yeni", data=data, follow_redirects=True)  # bildirim burada okunur
    row = one("SELECT id FROM credit_cards WHERE id > ? ORDER BY id DESC LIMIT 1", (before,))
    return row["id"] if row else None


def add_purchase(client, card_id, title, total, n=1, purchased_on=None, **fields):
    data = {"card_id": str(card_id), "title": title, "total": str(total), "count": str(n),
            "purchased_on": purchased_on or T.isoformat()}
    data.update({k: str(v) for k, v in fields.items()})
    before = one("SELECT MAX(id) AS m FROM card_purchases")["m"] or 0
    client.post("/taksitler/alisveris/yeni", data=data, follow_redirects=True)
    row = one("SELECT id FROM card_purchases WHERE id > ? ORDER BY id DESC LIMIT 1", (before,))
    return row["id"] if row else None


def edit_purchase(client, pid, **changes):
    p = purchase(pid)
    data = {"card_id": p["card_id"], "title": p["title"], "merchant": p["merchant"], "category": p["category"],
            "purchased_on": p["purchased_on"], "total": str(p["total"]), "count": str(p["count"]),
            "first_statement": p["first_statement"], "first_auto": p["first_statement"],
            "expense_mode": p["expense_mode"], "note": p["note"]}
    data.update({k: str(v) for k, v in changes.items()})
    return client.post(f"/taksitler/alisveris/{pid}", data=data, follow_redirects=True).get_data(as_text=True)


def old_card(cid):
    """Kart çoktandır panoda: geçmiş ekstreler 'ödenmiş sayılmaz' (gecikme görünür)."""
    run("UPDATE credit_cards SET created_at = '2020-01-01 00:00:00' WHERE id = ?", (cid,))


def calls(method):
    return [p for m, p in CALLS if m == method]


def sent():
    return [(p["chat_id"], p["text"], p.get("reply_markup")) for p in calls("sendMessage")]


def buttons(markup):
    return [b["callback_data"] for r in json.loads(markup or "{}").get("inline_keyboard", []) for b in r]


def button_texts(markup):
    return [b["text"] for r in json.loads(markup or "{}").get("inline_keyboard", []) for b in r]


def cron(path="hatirlatma"):
    r = RAW.get(f"/cron/gizli/{path}")
    assert r.status_code == 200, r.status_code
    return r.json


def _post_update(update):
    UPDATE_ID[0] += 1
    r = RAW.post("/telegram/webhook", data=json.dumps({"update_id": UPDATE_ID[0], **update}),
                 content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    assert r.status_code == 200


def press(data, chat=100):
    _post_update({"callback_query": {"id": "cb", "data": data, "from": {"id": chat},
                                     "message": {"message_id": 7, "chat": {"id": chat}}}})


def say(text, chat=100):
    _post_update({"message": {"message_id": UPDATE_ID[0] + 1, "chat": {"id": chat}, "text": text}})
    return calls("sendMessage")[-1]["text"]


def day_month(d):
    return f"{d.day} {MONTHS_TR[d.month - 1]}"


# ---------- Hesap ----------
def test_periods():
    c15 = {"statement_day": 15, "due_days": 10}
    assert ins.period_for(c15, date(2026, 10, 14)) == "2026-10"
    assert ins.period_for(c15, date(2026, 10, 15)) == "2026-10"     # kesim günü dahil
    assert ins.period_for(c15, date(2026, 10, 16)) == "2026-11"     # kesimden sonra -> sonraki ekstre
    assert ins.statement_on(c15, "2026-10") == date(2026, 10, 15) and ins.due_on(c15, "2026-10") == date(2026, 10, 25)
    # 31'inde kesim: ayda o gün yoksa ayın son günü (Şubat 28/29, Nisan 30)
    c31 = {"statement_day": 31, "due_days": 10}
    assert ins.statement_on(c31, "2026-02") == date(2026, 2, 28) and ins.statement_on(c31, "2028-02") == date(2028, 2, 29)
    assert ins.due_on(c31, "2026-02") == date(2026, 3, 10) and ins.statement_on(c31, "2026-04") == date(2026, 4, 30)
    assert [ins.period_for(c31, date(2026, m, d)) for m, d in ((1, 31), (2, 1), (2, 28), (3, 1), (4, 30), (5, 1))] \
        == ["2026-01", "2026-02", "2026-02", "2026-03", "2026-04", "2026-05"]
    c30 = {"statement_day": 30, "due_days": 5}
    assert ins.period_for(c30, date(2027, 2, 28)) == "2027-02" and ins.period_for(c30, date(2027, 3, 1)) == "2027-03"
    # Yıl geçişi
    assert ins.period_for(c15, date(2026, 12, 15)) == "2026-12" and ins.period_for(c15, date(2026, 12, 20)) == "2027-01"
    assert ins.due_on({"statement_day": 25, "due_days": 10}, "2026-12") == date(2027, 1, 4)
    assert ins.shift("2026-11", 2) == "2027-01" and ins.shift("2027-01", -1) == "2026-12"
    assert ins.shift("2026-01", -13) == "2024-12" and ins.shift("2026-12", 12) == "2027-12"
    assert ins.period_label("2026-10") == "Ekim 2026" and ins.period_label("2027-01", short=True) == "Oca 2027"
    assert ins.parse_period("2026-10") == (2026, 10)
    for bad in ("2026-13", "2026-00", "26-10", "2026/10", "abcd-ef", "", None, "2026-1", "1999-12", "2026-1x"):
        assert ins.parse_period(bad) is None, bad
    assert [ins.left_words(n) for n in (0, 1, 3, -2)] == ["bugün", "yarın", "3 gün", "2 gün gecikti"]
    print("  periods OK")


def test_split_and_schedule():
    assert ins.split(1000, 3) == [333.33, 333.33, 333.34]
    assert ins.split(2000, 3) == [666.66, 666.66, 666.68]
    assert ins.split(100, 1) == [100.0] and ins.split(10, 3) == [3.33, 3.33, 3.34]
    for total, n in ((1234.56, 12), (99999.99, 36), (0.36, 36), (12000, 9), (7.01, 2)):
        parts = ins.split(total, n)
        assert len(parts) == n and round(sum(parts), 2) == total and min(parts) > 0, (total, n)
        assert len(set(parts[:-1])) <= 1 and parts[-1] >= parts[0]   # fark son taksitte
    c15 = {"statement_day": 15, "due_days": 10}
    p = {"first_statement": "2026-11", "total": 1200, "count": 12, "closed_early_on": None}
    s = ins.schedule(p, c15)
    assert [e["period"] for e in s] == [ins.shift("2026-11", i) for i in range(12)] and s[-1]["period"] == "2027-10"
    assert all(e["amount"] == 100 for e in s) and s[3]["nos"] == (4, 4)
    # Erken kapama: kapatma gününün ekstresine kalanların hepsi
    closed = ins.schedule({**p, "closed_early_on": "2027-02-10"}, c15)
    assert [(e["period"], e["amount"], e["nos"]) for e in closed] == [
        ("2026-11", 100, (1, 1)), ("2026-12", 100, (2, 2)), ("2027-01", 100, (3, 3)), ("2027-02", 900, (4, 12))]
    assert [(e["period"], e["amount"], e["nos"]) for e in ins.schedule({**p, "closed_early_on": "2027-02-16"}, c15)][-1] \
        == ("2027-03", 800, (5, 12))   # kesimden sonra kapatılınca sonraki ekstre
    assert [(e["period"], e["amount"], e["nos"]) for e in ins.schedule({**p, "closed_early_on": "2026-10-01"}, c15)] \
        == [("2026-11", 1200, (1, 12))]   # ilk ekstreden önce kapatma: hepsi ilk ekstrede
    p3 = {"first_statement": "2026-11", "total": 1000, "count": 3, "closed_early_on": "2026-12-01"}
    assert [(e["period"], e["amount"]) for e in ins.schedule(p3, c15)] == [("2026-11", 333.33), ("2026-12", 666.67)]
    assert ins.entry_label({"nos": (1, 1)}, 1) == "Tek çekim" and ins.entry_label({"nos": (3, 3)}, 12) == "Taksit 3/12"
    assert ins.entry_label({"nos": (5, 12)}, 12) == "Taksit 5–12/12 (erken kapama)"
    assert ins.count_text(1) == "Tek çekim" and ins.count_text(12) == "12 taksit"
    print("  split/schedule OK")


# ---------- Doğrulama ----------
def test_card_validation():
    reset()
    page = ADMIN.text("/taksitler/")
    assert "Henüz kart yok" in page and "Kart numarası, son 4 hane, CVV" in page
    for bad, msg in (({"name": ""}, "Kart adı gerekli"), ({"statement_day": "0"}, "1-31"),
                     ({"statement_day": "32"}, "1-31"), ({"statement_day": "abc"}, "1-31"),
                     ({"statement_day": ""}, "1-31"), ({"due_days": "0"}, "1-28"),
                     ({"due_days": "29"}, "1-28"), ({"due_days": "x"}, "1-28"), ({"limit_amount": "-5"}, "Limit"),
                     ({"limit_amount": "abc"}, "Limit"), ({"name": "4543 1234 5678 9012"}, "kart numarası"),
                     ({"name": "Bonus 4543123456789012"}, "kart numarası")):
        data = {"name": "Hatalı", "statement_day": "15", "due_days": "10", **bad}
        r = ADMIN.post("/taksitler/kart/yeni", data=data, follow_redirects=True)
        assert msg in r.get_data(as_text=True), bad
    assert count("credit_cards") == 0
    cid = add_card(ADMIN, "Bonus", 15, 10, limit_amount="50.000", remind_days=5, color="green")
    c = card(cid)
    assert (c["user_id"], c["name"], c["statement_day"], c["due_days"], c["limit_amount"], c["remind_days"], c["color"],
            c["active"]) == (1, "Bonus", 15, 10, 50000, 5, "green", 1)
    # Varsayılanlar: son ödeme boşsa 10 gün, bilinmeyen hatırlatma 3 gün, bilinmeyen renk -> kullanılmayan ilk renk
    wid = add_card(ADMIN, "  World   kart ", 31, "", remind_days=99, color="pembe")
    w = card(wid)
    assert (w["name"], w["due_days"], w["remind_days"], w["color"], w["limit_amount"])         == ("World kart", 10, 3, "blue", None)
    assert add_card(ADMIN, "Maaş kartı 2024", 1, 28) is not None   # birkaç rakam serbest
    page = ADMIN.text("/taksitler/")
    assert "Bonus" in page and "World kart" in page and "kesim ayın 15. günü" in page
    # Düzenleme: doğrulama aynı, kayıt değişmez
    ADMIN.post(f"/taksitler/kart/{cid}", data={"name": "Bonus", "statement_day": "40", "due_days": "10"})
    assert card(cid)["statement_day"] == 15
    r = ADMIN.post(f"/taksitler/kart/{cid}", data={"name": "Bonus Gold", "statement_day": "20", "due_days": "12",
                                                  "limit_amount": "", "remind_days": "7", "color": "red"},
                   follow_redirects=True)
    assert "Kart güncellendi" in r.get_data(as_text=True)
    c = card(cid)
    assert (c["name"], c["statement_day"], c["due_days"], c["limit_amount"], c["remind_days"], c["color"]) \
        == ("Bonus Gold", 20, 12, None, 7, "red")
    print("  card validation OK")


def test_purchase_validation():
    reset()
    cid = add_card(ADMIN, "Bonus", 15, 10)
    ayse_card = add_card(AYSE_C, "Ayşenin kartı", 10)
    natural = ins.period_for(card(cid), T)
    for bad, msg in (({"card_id": "999999"}, "Kart seç"), ({"card_id": str(ayse_card)}, "Kart seç"),
                     ({"card_id": "9" * 40}, "Kart seç"), ({"title": ""}, "Açıklama gerekli"),
                     ({"total": "0"}, "Toplam tutar"), ({"total": "-5"}, "Toplam tutar"),
                     ({"total": "abc"}, "Toplam tutar"), ({"total": ""}, "Toplam tutar"), ({"count": "37"}, "1-36"),
                     ({"count": "0", "count_other": "37"}, "1-36"),
                     ({"count": "0", "count_other": ""}, "1-36"), ({"count": "0", "count_other": "0"}, "1-36"),
                     ({"count": "abc"}, "1-36"), ({"purchased_on": D(1)}, "ileri bir gün"),
                     ({"total": "0,05", "count": "6"}, "1 kuruştan"),
                     ({"first_statement": ins.shift(natural, -1)}, "önceki bir ekstreye"),
                     ({"first_statement": ins.shift(natural, 4)}, "en fazla 3 ay"),
                     ({"first_statement": "2026-13"}, "YYYY-AA")):
        data = {"card_id": str(cid), "title": "Hatalı alışveriş", "total": "1.000", "count": "3",
                "purchased_on": T.isoformat(), **bad}
        r = ADMIN.post("/taksitler/alisveris/yeni", data=data)
        page = r.get_data(as_text=True)
        assert r.status_code == 400 and msg in page, (bad, r.status_code)
        if bad.get("title") != "":
            assert 'value="Hatalı alışveriş"' in page   # yazılanlar kaybolmaz
    assert count("card_purchases") == 0
    # Geçerliler: "Diğer…" ile 7 taksit, ilk ekstre boş -> alış gününden, 3 ay ertelemeli kampanya
    pid = add_purchase(ADMIN, cid, "Koltuk", "7.000", 0, count_other=7, merchant="IKEA", category="Ev", note="Faizsiz")
    p = purchase(pid)
    assert (p["user_id"], p["card_id"], p["title"], p["merchant"], p["category"], p["total"], p["count"],
            p["first_statement"], p["expense_mode"], p["note"], p["closed_early_on"]) \
        == (1, cid, "Koltuk", "IKEA", "Ev", 7000, 7, natural, "full", "Faizsiz", None)
    later = add_purchase(ADMIN, cid, "Telefon", "30.000", 12, first_statement=ins.shift(natural, 3), expense_mode="none")
    assert purchase(later)["first_statement"] == ins.shift(natural, 3)
    # Önerilenle aynı gönderilen ilk ekstre (JS yokken formdaki eski öneri) alış gününden yeniden hesaplanır
    auto = add_purchase(ADMIN, cid, "Kulaklık", "900", 3, first_statement=ins.shift(natural, 2),
                        first_auto=ins.shift(natural, 2))
    assert purchase(auto)["first_statement"] == natural
    # Bilinmeyen kategori -> Diğer; bilinmeyen işleme biçimi -> tamamı
    x = add_purchase(ADMIN, cid, "Hediye", "100", 1, category="Yok", expense_mode="garip")
    assert (purchase(x)["category"], purchase(x)["expense_mode"]) == ("Diğer", "full")
    # Pasif karta yeni alışveriş eklenmez; düzenlemede mevcut kartı kalabilir
    ADMIN.post(f"/taksitler/kart/{cid}/durum")
    r = ADMIN.post("/taksitler/alisveris/yeni", data={"card_id": str(cid), "title": "Pasif", "total": "10", "count": "1"})
    assert r.status_code == 400 and "Kart seç" in r.get_data(as_text=True)
    assert "Alışveriş güncellendi" in edit_purchase(ADMIN, x, title="Hediye kutusu")
    assert purchase(x)["title"] == "Hediye kutusu"
    ADMIN.post(f"/taksitler/kart/{cid}/durum")
    # Düzenlemede de doğrulama: kayıt değişmez
    edit_purchase(ADMIN, pid, total="0")
    edit_purchase(ADMIN, pid, count="40")
    edit_purchase(ADMIN, pid, title="")
    assert (purchase(pid)["total"], purchase(pid)["count"], purchase(pid)["title"]) == (7000, 7, "Koltuk")
    # Kesim günü değişince ilk ekstresi kendiliğinden hesaplanmış alışverişler kayar, elle seçilen kalmaz
    reset()
    buy = T - timedelta(days=30)
    if buy.day == 1:
        buy -= timedelta(days=1)
    cid = add_card(ADMIN, "Bonus", buy.day, 10)
    auto = add_purchase(ADMIN, cid, "Otomatik", "300", 3, buy.isoformat())
    manual = add_purchase(ADMIN, cid, "Elle", "300", 3, buy.isoformat(),
                          first_statement=ins.shift(ins.period_of(buy), 2))
    assert purchase(auto)["first_statement"] == ins.period_of(buy)   # kesim günü alındı -> o ayın ekstresi
    r = ADMIN.post(f"/taksitler/kart/{cid}", data={"name": "Bonus", "statement_day": str(buy.day - 1), "due_days": "10"},
                   follow_redirects=True)
    assert "1 alışverişin ilk ekstresi" in r.get_data(as_text=True)
    assert purchase(auto)["first_statement"] == ins.shift(ins.period_of(buy), 1)
    assert purchase(manual)["first_statement"] == ins.shift(ins.period_of(buy), 2)
    print("  purchase validation OK")


def test_plan_preview():
    reset()
    cid = add_card(ADMIN, "Bonus", 15, 10)
    natural = ins.period_for(card(cid), T)
    j = ADMIN.post("/taksitler/plan", data={"card_id": str(cid), "total": "1.000", "count": "3",
                                            "purchased_on": T.isoformat()}).json
    assert j["ok"] and j["first_auto"] == natural == j["first"] and j["total"] == "1.000 ₺"
    assert [r["amount"] for r in j["rows"]] == ["333,33 ₺", "333,33 ₺", "333,34 ₺"]
    assert [r["no"] for r in j["rows"]] == ["Taksit 1/3", "Taksit 2/3", "Taksit 3/3"]
    assert j["rows"][0]["period"] == ins.period_label(natural, short=True)
    # Elle seçilen ilk ekstre (öneriden farklı) korunur; açıklama gerekmez
    j = ADMIN.post("/taksitler/plan", data={"card_id": str(cid), "total": "500", "count": "1", "first_auto": natural,
                                            "first_statement": ins.shift(natural, 1)}).json
    assert j["ok"] and j["first"] == ins.shift(natural, 1) and j["rows"][0]["no"] == "Tek çekim"
    j = ADMIN.post("/taksitler/plan", data={"card_id": str(cid), "total": "", "count": "3"}).json
    assert not j["ok"] and "Toplam tutar" in j["error"] and j["first_auto"] == natural  # öneri tutarsız da gelir
    j = AYSE_C.post("/taksitler/plan", data={"card_id": str(cid), "total": "100", "count": "1"}).json
    assert not j["ok"] and "Kart seç" in j["error"]
    assert ADMIN.post("/taksitler/plan", data={"card_id": str(cid)}).status_code == 200
    print("  plan preview OK")


# ---------- Ekstre, yük, ilerleme ----------
def test_statement_totals():
    reset()
    cid = add_card(ADMIN, "Bonus", 15, 10)
    c = card(cid)
    d1 = T - timedelta(days=40)
    tel = add_purchase(ADMIN, cid, "Telefon", "1.000", 3, d1.isoformat(), merchant="Teknosa")
    add_purchase(ADMIN, cid, "Market", "600", 1, d1.isoformat())
    add_purchase(ADMIN, cid, "Kulaklık", "250,50", 1, D(-5))
    first = ins.period_for(c, d1)
    assert purchase(tel)["first_statement"] == first
    expected = {first: 933.33, ins.shift(first, 1): 333.33, ins.shift(first, 2): 333.34}
    late = ins.period_for(c, T - timedelta(days=5))
    expected[late] = round(expected.get(late, 0) + 250.5, 2)
    b = book()
    assert {p: ins.statement(b, c, p, T)["amount"] for p in expected} == expected
    assert ins.statement(b, c, ins.shift(first, -1), T)["amount"] == 0
    page = ADMIN.text(f"/taksitler/kart/{cid}/{first}")
    assert "Telefon" in page and "Taksit 1/3" in page and "Market" in page and "Tek çekim" in page
    assert fmt_money(expected[first]) in page and "Teknosa" in page
    assert "Taksit 3/3" in ADMIN.text(f"/taksitler/kart/{cid}/{ins.shift(first, 2)}")
    # Kart sayfası: ekstreler ve alışverişler
    page = ADMIN.text(f"/taksitler/kart/{cid}")
    assert ins.period_label(first) in page and "Telefon" in page and "Kulaklık" in page
    # Geçersiz dönem 404
    for bad in ("2026-13", "abc", "2026-1"):
        assert ADMIN.get(f"/taksitler/kart/{cid}/{bad}").status_code == 404, bad
    print("  statement totals OK")


def test_load_and_progress():
    reset()
    bonus = add_card(ADMIN, "Bonus", 15, 10, limit_amount="20.000", color="green")
    world = add_card(ADMIN, "World", 1, 10, color="purple")
    b_card = card(bonus)
    buy = T - timedelta(days=120)
    fridge = add_purchase(ADMIN, bonus, "Buzdolabı", "12.000", 12, buy.isoformat(), merchant="Arçelik", category="Ev")
    add_purchase(ADMIN, world, "Televizyon", "6.000", 6)
    shoes = add_purchase(ADMIN, bonus, "Ayakkabı", "1.500", 1)
    # Kart bugün eklendi: son ödemesi bugünden önce olan taksitler ödenmiş sayılır
    first = purchase(fridge)["first_statement"]
    paid = sum(1 for i in range(12) if ins.due_on(b_card, ins.shift(first, i)) < T)
    assert 2 <= paid <= 5, paid
    b = book()
    pr = ins.progress(b, purchase(fridge), T)
    assert pr["paid"] == paid and pr["remaining"] == 1000 * (12 - paid) and not pr["done"]
    assert [r["state"] for r in pr["plan"][:paid]] == ["assumed"] * paid and pr["next"]["nos"] == (paid + 1, paid + 1)
    # Açık dönem bir tane; sonrakiler "bekliyor"
    assert pr["plan"][-1]["state"] == "future" and [r["state"] for r in pr["plan"]].count("open") == 1
    assert pr["plan"][paid]["state"] in ("due", "open")
    total_left = 1000 * (12 - paid) + 6000 + 1500
    page = ADMIN.text("/taksitler/")
    assert f"{paid}/12 ödendi" in page and f"kalan {fmt_money(1000 * (12 - paid))}" in page
    assert "Buzdolabı" in page and "12 taksit" in page and fmt_money(total_left) in page
    assert "3 devam eden alışveriş" in page
    # 12 aylık yük: son ödeme ayına göre, kart kırılımıyla; kalan taksitlerin hepsi bu 12 ayda
    chart = ins.monthly_load(b, T)
    assert len(chart) == 12 and chart[0]["month"] == ins.period_of(T)
    assert round(sum(m["total"] for m in chart), 2) == total_left
    assert all(round(sum(v for _c, v in m["parts"]), 2) == m["total"] for m in chart)
    assert any({c["name"] for c, _v in m["parts"]} == {"Bonus", "World"} for m in chart)
    assert "Önümüzdeki 12 ay taksit yükü" in page and 'class="seg c-green"' in page and 'class="seg c-purple"' in page
    # Limit doluluğu: Bonus'un ödenmemiş taksitleri / limit
    debt = 1000 * (12 - paid) + 1500
    assert f"Limit doluluğu %{round(debt * 100 / 20000)}" in page
    assert f"%{round(debt * 100 / 20000)}" in ADMIN.text(f"/taksitler/kart/{bonus}")
    # Sıradaki ekstre: açık dönem ya da kesilip son ödemesi gelmemiş olan
    nxt = ins.next_statement(b, b_card, T)
    assert not nxt["paid"] and nxt["due"] >= T and nxt["state"] in ("due", "open")
    # Bu dönemin ekstresi ödenince tek çekim biter, buzdolabı bir taksit ilerler
    current = ins.period_for(b_card, T)
    ADMIN.post(f"/taksitler/kart/{bonus}/{current}/ode")
    b = book()
    assert ins.progress(b, purchase(shoes), T)["done"]
    in_current = any(e["period"] == current for e in b["plans"][fridge])
    assert ins.progress(b, purchase(fridge), T)["paid"] == paid + (1 if in_current else 0)
    page = ADMIN.text("/taksitler/")
    finished = page.split("Biten alışverişler", 1)[1]
    assert "Ayakkabı" in finished and "Buzdolabı" not in finished
    assert "2 devam eden alışveriş" in page
    # Geciken: kart çoktandır panodaysa geçmiş ödenmemiş ekstreler gecikmiş görünür
    old_card(bonus)
    b = book()
    late = ins.late_statements(b, card(bonus), T)
    assert late and all(st["state"] == "late" and st["due"] < T and st["amount"] > 0 for st in late)
    page = ADMIN.text("/taksitler/")
    assert "ekstresi</a>" in page and "ödenmedi" in page and "danger-text" in page
    assert ins.progress(b, purchase(fridge), T)["remaining"] > 1000 * (12 - paid - 1)
    print("  load/progress OK")


def test_early_close():
    reset()
    cid = add_card(ADMIN, "Bonus", 15, 10)
    c = card(cid)
    buy = T - timedelta(days=60)
    fridge = add_purchase(ADMIN, cid, "Buzdolabı", "12.000", 12, buy.isoformat())
    single = add_purchase(ADMIN, cid, "Market", "100", 1)
    first = purchase(fridge)["first_statement"]
    before = ins.progress(book(), purchase(fridge), T)["remaining"]
    target = max(ins.period_for(c, T), first)
    for data, msg in (({"closed_on": D(1)}, "ileri bir gün"), ({"closed_on": (buy - timedelta(days=1)).isoformat()},
                                                                 "alış gününden önce")):
        r = ADMIN.post(f"/taksitler/alisveris/{fridge}/kapat", data=data, follow_redirects=True)
        assert msg in r.get_data(as_text=True) and purchase(fridge)["closed_early_on"] is None
    r = ADMIN.post(f"/taksitler/alisveris/{fridge}/kapat", data={"closed_on": T.isoformat()}, follow_redirects=True)
    assert "erken kapatıldı" in r.get_data(as_text=True) and purchase(fridge)["closed_early_on"] == T.isoformat()
    b = book()
    plan = b["plans"][fridge]
    last = plan[-1]
    assert last["period"] == target and last["nos"][1] == 12 and len(plan) == last["nos"][0]
    assert round(sum(e["amount"] for e in plan), 2) == 12000
    st = ins.statement(b, c, target, T)
    assert st["amount"] == round(last["amount"] + 100 * (target == ins.period_for(c, T)), 2)
    assert ins.progress(b, purchase(fridge), T)["remaining"] == before   # toplam borç değişmez, öne gelir
    page = ADMIN.text(f"/taksitler/alisveris/{fridge}")
    assert "Erken kapandı" in page and "(erken kapama)" in page and "Erken kapamayı geri al" in page
    assert "Taksit" in ADMIN.text(f"/taksitler/kart/{cid}/{target}")
    # İkinci kez kapatılmaz; geri alınınca eski plan
    r = ADMIN.post(f"/taksitler/alisveris/{fridge}/kapat", follow_redirects=True)
    assert "zaten erken kapatılmış" in r.get_data(as_text=True)
    ADMIN.post(f"/taksitler/alisveris/{fridge}/kapat-geri-al")
    assert purchase(fridge)["closed_early_on"] is None and len(book()["plans"][fridge]) == 12
    # Tek çekimde kapatılacak taksit yok
    r = ADMIN.post(f"/taksitler/alisveris/{single}/kapat", follow_redirects=True)
    assert "erken kapatılacak taksit yok" in r.get_data(as_text=True) and purchase(single)["closed_early_on"] is None
    print("  early close OK")


def test_pay_and_undo():
    reset()
    cid = add_card(ADMIN, "Bonus", 15, 10)
    add_purchase(ADMIN, cid, "Telefon", "3.000", 3)
    add_purchase(ADMIN, cid, "Market", "1.500", 1)
    period = ins.period_for(card(cid), T)
    url = f"/taksitler/kart/{cid}/{period}"
    assert stmt(cid, period)["amount"] == 2500
    page = ADMIN.text(url)
    assert "Ödendi olarak işaretle" in page and 'value="2.500"' in page
    for data, msg in (({"paid_on": D(1)}, "ileri bir gün"), ({"paid_amount": "-1"}, "0&#39;dan büyük"),
                      ({"paid_amount": "abc"}, "0&#39;dan büyük")):
        r = ADMIN.post(f"{url}/ode", data=data, follow_redirects=True)
        assert msg in r.get_data(as_text=True), data
    assert one("SELECT 1 AS x FROM card_statements WHERE card_id = ? AND paid_on IS NOT NULL", (cid,)) is None
    r = ADMIN.post(f"{url}/ode", follow_redirects=True)
    assert "ekstresi ödendi (2.500 ₺)" in r.get_data(as_text=True)
    row = one("SELECT * FROM card_statements WHERE card_id = ? AND period = ?", (cid, period))
    assert (row["user_id"], row["paid_on"], row["paid_amount"], row["expense_ids"]) == (1, T.isoformat(), 2500, "")
    assert stmt(cid, period)["state"] == "paid"
    assert "zaten ödenmiş" in ADMIN.post(f"{url}/ode", follow_redirects=True).get_data(as_text=True)
    page = ADMIN.text(url)
    assert "✅ Ödendi" in page and "Ödemeyi geri al" in page and "Ödendi olarak işaretle" not in page
    # Sıradaki ekstre bir sonraki döneme geçer
    b = book()
    assert ins.next_statement(b, b["cards"][cid], T)["period"] == ins.shift(period, 1)
    # Geri al
    r = ADMIN.post(f"{url}/geri-al", follow_redirects=True)
    assert "ödemesi geri alındı" in r.get_data(as_text=True)
    row = one("SELECT * FROM card_statements WHERE card_id = ? AND period = ?", (cid, period))
    assert row["paid_on"] is None and row["paid_amount"] is None and stmt(cid, period)["state"] == "open"
    assert "Geri alınacak ödeme yok" in ADMIN.post(f"{url}/geri-al", follow_redirects=True).get_data(as_text=True)
    # Farklı tutar ve geçmiş tarih
    ADMIN.post(f"{url}/ode", data={"paid_amount": "2.000", "paid_on": D(-1)})
    row = one("SELECT * FROM card_statements WHERE card_id = ? AND period = ?", (cid, period))
    assert (row["paid_on"], row["paid_amount"]) == (D(-1), 2000)
    assert "Ekstre toplamı şu an 2.500 ₺" in ADMIN.text(url)
    # Taksiti olmayan ekstre ödenmez
    empty = ins.shift(period, 20)
    r = ADMIN.post(f"/taksitler/kart/{cid}/{empty}/ode", follow_redirects=True)
    assert "taksit yok" in r.get_data(as_text=True)
    assert one("SELECT 1 AS x FROM card_statements WHERE period = ? AND paid_on IS NOT NULL", (empty,)) is None
    print("  pay/undo OK")


def test_expense_modes():
    reset()
    cid = add_card(ADMIN, "Bonus", 15, 10)
    # 'full': alış gününe toplam tutar; düzenlenince güncellenir, biçim değişince silinir
    full = add_purchase(ADMIN, cid, "Buzdolabı", "12.000", 12, D(-3), category="Ev", merchant="Arçelik")
    e = one("SELECT * FROM expenses WHERE id = ?", (purchase(full)["expense_id"],))
    assert (e["user_id"], e["amount"], e["category"], e["date"]) == (1, 12000, "Ev", D(-3))
    assert e["note"] == "Buzdolabı (Bonus, 12 taksit)"
    page = edit_purchase(ADMIN, full, total="11.000", purchased_on=D(-2))
    assert "Harcama kaydı da güncellendi" in page
    e = one("SELECT * FROM expenses WHERE id = ?", (e["id"],))
    assert (e["amount"], e["date"]) == (11000, D(-2)) and count("expenses") == 1
    assert "💸 harcamalarda" in ADMIN.text(f"/taksitler/alisveris/{full}")
    page = edit_purchase(ADMIN, full, expense_mode="monthly")
    assert "harcama kaydı silindi" in page and count("expenses") == 0 and purchase(full)["expense_id"] is None
    edit_purchase(ADMIN, full, expense_mode="full")
    assert count("expenses") == 1 and purchase(full)["expense_id"]
    # Harcamalardan elle silinse de düzenlemede yeniden eklenir (kopya oluşmaz)
    run("DELETE FROM expenses")
    edit_purchase(ADMIN, full, note="yeni not")
    assert count("expenses") == 1
    # 'none': hiç işlenmez
    gift = add_purchase(ADMIN, cid, "Hediye", "300", 1, expense_mode="none")
    assert purchase(gift)["expense_id"] is None and count("expenses") == 1
    # 'monthly': ekstre ödenince o ekstredeki taksit ödeme gününe; ödeme geri alınınca silinir
    phone = add_purchase(ADMIN, cid, "Telefon", "900", 3, expense_mode="monthly", category="Diğer")
    assert count("expenses") == 1
    period = ins.period_for(card(cid), T)
    page = ADMIN.text(f"/taksitler/kart/{cid}/{period}")
    assert "seçili 1 alışverişin" in page
    r = ADMIN.post(f"/taksitler/kart/{cid}/{period}/ode", data={"paid_on": D(-1)}, follow_redirects=True)
    assert "1 taksit Harcamalar&#39;a işlendi" in r.get_data(as_text=True)
    added = rows("SELECT * FROM expenses WHERE note LIKE 'Telefon%'")
    assert len(added) == 1
    assert (added[0]["amount"], added[0]["date"], added[0]["category"], added[0]["note"]) \
        == (300, D(-1), "Diğer", "Telefon (Bonus, Taksit 1/3)")
    row = one("SELECT * FROM card_statements WHERE card_id = ? AND period = ?", (cid, period))
    assert row["expense_ids"] == str(added[0]["id"])
    assert count("expenses") == 2   # 'full' ve 'none' alışverişler ödemede eklenmez
    assert "Bu ödemeyle Harcamalar'a işlenen 1 taksit de silinir" in ADMIN.text(f"/taksitler/kart/{cid}/{period}")
    r = ADMIN.post(f"/taksitler/kart/{cid}/{period}/geri-al", follow_redirects=True)
    assert "1 taksit silindi" in r.get_data(as_text=True)
    assert not rows("SELECT * FROM expenses WHERE note LIKE 'Telefon%'") and count("expenses") == 1
    # Sonraki ekstre: 2. taksit
    nxt = ins.shift(period, 1)
    ADMIN.post(f"/taksitler/kart/{cid}/{nxt}/ode")
    assert [r["note"] for r in rows("SELECT note FROM expenses WHERE note LIKE 'Telefon%'")]         == ["Telefon (Bonus, Taksit 2/3)"]
    assert purchase(phone)["expense_id"] is None
    print("  expense modes OK")


# ---------- Telegram ----------
def test_telegram_reminders():
    reset()
    run("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
    run("UPDATE users SET telegram_chat_id = '200' WHERE id = ?", (AYSE,))
    run("INSERT OR REPLACE INTO app_state (key, value) VALUES ('telegram_webhook', 'https://x/telegram/webhook')")
    cut = T - timedelta(days=7)                 # ekstre bir hafta önce kesildi, son ödeme 3 gün sonra
    buy = (cut - timedelta(days=2)).isoformat()
    period = ins.period_of(cut)
    a = add_card(ADMIN, "Bonus", cut.day, 10, remind_days=3)
    assert ins.due_on(card(a), period) == T + timedelta(days=3)
    add_purchase(ADMIN, a, "Market", "3.250", 1, buy)
    assert stmt(a, period)["amount"] == 3250
    z = add_card(ADMIN, "Gününde", cut.day, 10, remind_days=0)            # sadece son gün
    add_purchase(ADMIN, z, "Kitap", "100", 1, buy)
    p = add_card(ADMIN, "Pasif", cut.day, 10)
    add_purchase(ADMIN, p, "Eski", "100", 1, buy)
    ADMIN.post(f"/taksitler/kart/{p}/durum")
    add_card(ADMIN, "Boş", cut.day, 10)                                   # tutarı olmayan ekstre hatırlatılmaz
    q = add_card(ADMIN, "Ödenmiş", cut.day, 10)
    add_purchase(ADMIN, q, "Fatura", "100", 1, buy)
    ADMIN.post(f"/taksitler/kart/{q}/{period}/ode")
    y = add_card(AYSE_C, "Ayşe kartı", cut.day, 10)
    add_purchase(AYSE_C, y, "Ayşenin alışverişi", "500", 1, buy)

    def texts(n):
        return [m[1] for m in sent()[n:]]

    NOW[0] = at(0, 8, 30)
    assert cron()["installments_sent"] == 0      # 09:00'dan önce gönderilmez
    NOW[0] = at(0, 9, 5)
    n = len(sent())
    assert cron()["installments_sent"] == 2
    due = T + timedelta(days=3)
    chat, text, markup = next(m for m in sent()[n:] if "Bonus" in m[1])
    assert chat == "100" and text.startswith(
        f"💳 <b>Bonus</b> ekstresi: <b>3.250 ₺</b> · son ödeme {day_month(due)} (3 gün)"), text
    assert f"/taksitler/kart/{a}/{period}" in text and ins.period_label(period) in text
    assert buttons(markup) == [f"ins:pay:{a}:{period}"] and button_texts(markup) == ["✅ Ödendi"]
    chat, text, _m = next(m for m in sent()[n:] if "Ayşe kartı" in m[1])
    assert chat == "200" and "500 ₺" in text
    assert not any(x in t for t in texts(n) for x in ("Gününde", "Pasif", "Boş", "Ödenmiş"))
    assert cron()["installments_sent"] == 0      # aynı mesaj iki kez gitmez
    for day in (1, 2):
        NOW[0] = at(day, 9, 30)
        assert cron()["installments_sent"] == 0, day
    # Son ödeme günü: Telegram'ı bağlı olmayana gitmez, webhook yoksa buton yok
    run("UPDATE users SET telegram_chat_id = NULL WHERE id = ?", (AYSE,))
    run("DELETE FROM app_state WHERE key = 'telegram_webhook'")
    NOW[0] = at(3, 9, 30)
    n = len(sent())
    assert cron()["installments_sent"] == 2
    msgs = sent()[n:]
    assert sorted(t.split("</b>")[0] for t in texts(n)) == ["💳 <b>Bonus", "💳 <b>Gününde"]
    bonus = next(t for t in texts(n) if "Bonus" in t)
    assert f"son ödeme <b>bugün</b> ({day_month(due)})" in bonus
    assert all(m[2] is None for m in msgs)
    assert cron()["installments_sent"] == 0
    NOW[0] = at(4, 9, 30)
    assert cron()["installments_sent"] == 0      # gecikince yeniden gönderilmez
    # Son ödeme tarihi değişince yeni tarihe göre yeniden kurulur
    ADMIN.post(f"/taksitler/kart/{a}", data={"name": "Bonus", "statement_day": str(cut.day), "due_days": "12",
                                            "remind_days": "3"})
    n = len(sent())
    assert cron()["installments_sent"] == 1 and "(yarın)" in texts(n)[0]
    # Ödenince gitmez
    ADMIN.post(f"/taksitler/kart/{a}/{period}/ode")
    NOW[0] = at(5, 9, 30)
    assert cron()["installments_sent"] == 0      # son ödeme günü ama ödendi
    run("INSERT INTO app_state (key, value) VALUES ('telegram_webhook', 'https://x/telegram/webhook')")
    run("UPDATE users SET telegram_chat_id = '200' WHERE id = ?", (AYSE,))
    NOW[0] = at(0)
    print("  telegram reminders OK")


def test_telegram_button():
    reset()
    run("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
    run("UPDATE users SET telegram_chat_id = '200' WHERE id = ?", (AYSE,))
    a = add_card(ADMIN, "Bonus", 15, 10)
    add_purchase(ADMIN, a, "Market", "3.250", 1, expense_mode="none")
    add_purchase(ADMIN, a, "Telefon", "900", 3, expense_mode="monthly")
    period = ins.period_for(card(a), T)
    y = add_card(AYSE_C, "Ayşe kartı", 15, 10)
    add_purchase(AYSE_C, y, "Ayşenin alışverişi", "500", 1)
    # Başkası basamaz
    press(f"ins:pay:{a}:{period}", chat=200)
    assert calls("answerCallbackQuery")[-1]["text"] == "Ekstre bulunamadı, kart silinmiş olabilir."
    press(f"ins:pay:{y}:{period}", chat=100)
    assert calls("answerCallbackQuery")[-1]["text"] == "Ekstre bulunamadı, kart silinmiş olabilir."
    assert count("card_statements") == 0
    # Bozuk veri, bağlı olmayan sohbet
    for data in (f"ins:pay:abc:{period}", f"ins:pay:{a}:2026-13", f"ins:xyz:{a}:{period}", "ins:pay", "ins",
                 f"ins:pay:{'9' * 40}:{period}", f"ins:pay:{a}:"):
        press(data)
        assert calls("answerCallbackQuery")[-1]["text"] == "Ekstre bulunamadı, kart silinmiş olabilir.", data
    press(f"ins:pay:{a}:{period}", chat=999)
    assert calls("answerCallbackQuery")[-1]["text"] == "Bu sohbet panoya bağlı değil."
    assert count("card_statements") == 0
    # Tutarı olmayan ekstre
    press(f"ins:pay:{a}:{ins.shift(period, 10)}")
    assert calls("answerCallbackQuery")[-1]["text"] == "Bu ekstrede taksit yok." and count("card_statements") == 0
    # ✅ Ödendi: toplam tutarla bugün; 'monthly' taksit Harcamalar'a
    press(f"ins:pay:{a}:{period}")
    row = one("SELECT * FROM card_statements WHERE card_id = ? AND period = ?", (a, period))
    assert (row["paid_on"], row["paid_amount"]) == (T.isoformat(), 3550)
    assert calls("answerCallbackQuery")[-1]["text"] == "✅ Ödendi"
    edit = calls("editMessageText")[-1]
    assert f"<b>Bonus</b> · {ins.period_label(period)} ekstresi ödendi (3.550 ₺)" in edit["text"]
    assert "1 taksit Harcamalar'a işlendi" in edit["text"] and f"/taksitler/kart/{a}/{period}" in edit["text"]
    assert edit["message_id"] == 7 and buttons(edit.get("reply_markup")) == []
    assert [r["amount"] for r in rows("SELECT amount FROM expenses WHERE note LIKE 'Telefon%'")] == [300]
    # Çift dokunuş: ikinci kez ödenmez, harcama kopyalanmaz
    press(f"ins:pay:{a}:{period}")
    assert calls("answerCallbackQuery")[-1]["text"] == "Bu ekstre zaten ödenmiş."
    assert "günü ödendi" in calls("editMessageText")[-1]["text"]
    assert count("expenses", 1) == 1
    # Ayşe kendi ekstresini kapatabilir
    press(f"ins:pay:{y}:{period}", chat=200)
    assert one("SELECT paid_amount FROM card_statements WHERE card_id = ?", (y,))["paid_amount"] == 500
    print("  telegram button OK")


def test_bot_command():
    reset()
    run("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
    run("UPDATE users SET telegram_chat_id = '200' WHERE id = ?", (AYSE,))
    assert "Henüz kart yok" in say("/taksit")
    bonus = add_card(ADMIN, "Bonus", 15, 10, color="green")
    world = add_card(ADMIN, "World", 1, 10, color="purple")
    add_purchase(ADMIN, bonus, "Buzdolabı", "12.000", 12)
    add_purchase(ADMIN, world, "Televizyon", "6.000", 6)
    y = add_card(AYSE_C, "Ayşe kartı", 10)
    add_purchase(AYSE_C, y, "Ayşenin alışverişi", "500", 1)
    text = say("/taksit")
    assert "Sıradaki ekstreler" in text and "Önümüzdeki 3 ay" in text and "Kalan toplam borç: <b>18.000 ₺</b>" in text
    assert "🟢" in text and ">Bonus</a>" in text and ">World</a>" in text and f"/taksitler/kart/{bonus}/" in text
    for i in range(3):
        assert f"• {ins.period_label(ins.shift(ins.period_of(T), i))}: " in text
    assert "Ayşe" not in text and "Taksitler →" in text
    other = say("/taksit", chat=200)
    assert "Ayşe kartı" in other and "Bonus" not in other and "Kalan toplam borç: <b>500 ₺</b>" in other
    import pano.bot_commands as bc
    assert "taksit" in dict(bc.COMMANDS) and "/taksit" in bc.HELP and "/taksit" in say("/yardim")
    print("  bot command OK")


# ---------- Entegrasyon ----------
def test_integrations():
    reset()
    run("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
    cut = T - timedelta(days=7)
    period = ins.period_of(cut)
    a = add_card(ADMIN, "Bonus", cut.day, 10)                 # son ödeme 3 gün sonra
    add_purchase(ADMIN, a, "Buzdolabı", "3.250", 1, (cut - timedelta(days=2)).isoformat(), merchant="Teknosa",
                 note="Beyaz, A+++")
    old = add_card(ADMIN, "Eski kart", cut.day, 10)
    prev_cut = ins.statement_on(card(old), ins.shift(period, -1))
    add_purchase(ADMIN, old, "Mont", "800", 1, (prev_cut - timedelta(days=2)).isoformat())
    old_card(old)                                             # geçen ayın ekstresi ödenmemiş: gecikti
    pas = add_card(ADMIN, "Pasif kart", cut.day, 10)
    add_purchase(ADMIN, pas, "Pasif alışveriş", "50", 1, (cut - timedelta(days=2)).isoformat())
    ADMIN.post(f"/taksitler/kart/{pas}/durum", follow_redirects=True)  # bildirim burada okunur
    y = add_card(AYSE_C, "Ayşe kartı", cut.day, 10)
    add_purchase(AYSE_C, y, "Ayşenin çantası", "500", 1, (cut - timedelta(days=2)).isoformat())

    # Pano (yaklaşanlar): ödenmemiş ekstrelerin son ödemesi (gecikenler dahil); pasif kart ve başkasınınki yok
    home = ADMIN.text("/")
    assert "Bonus ekstresi son ödeme" in home and "Eski kart ekstresi son ödeme" in home
    assert "Pasif kart" not in home and "Ayşe kartı" not in home
    assert "Ayşe kartı ekstresi son ödeme" in AYSE_C.text("/") and "Bonus" not in AYSE_C.text("/")
    # Günlük özet de yaklaşanlardan
    n = len(sent())
    cron("gunluk")
    daily = next(t for c, t, _m in sent()[n:] if c == "100" and "Günaydın" in t)  # ayın 1. günü aylık rapor da gelir
    assert "💳 Bonus ekstresi son ödeme" in daily and "3.250 ₺" in daily

    # Takvim: son ödeme günleri; ödenince tamamlandı
    ADMIN.post(f"/taksitler/kart/{a}/{period}/ode", follow_redirects=True)
    assert "Bonus ekstresi" not in ADMIN.text("/")
    with app.test_request_context():
        evs = [e for e in events_between(1, T - timedelta(days=40), T + timedelta(days=40)) if e["kind"] == "card-due"]
    titles = {e["title"]: e for e in evs}
    assert set(titles) == {"Bonus ekstresi son ödeme", "Eski kart ekstresi son ödeme"}
    assert titles["Bonus ekstresi son ödeme"]["done"] and not titles["Eski kart ekstresi son ödeme"]["done"]
    assert titles["Bonus ekstresi son ödeme"]["date"] == T + timedelta(days=3)
    assert all(e["url"].startswith("/taksitler/kart/") and e["icon"] == "💳" for e in evs)
    assert "Bonus ekstresi son ödeme" in ADMIN.text(f"/takvim/?ay={D(3)[:7]}")
    assert "Ayşe kartı" not in ADMIN.text(f"/takvim/?ay={D(3)[:7]}")

    # Arama: açıklama, mağaza, not; Türkçe harf duyarsız; başkası bulamaz
    assert "Buzdolabı" in ADMIN.text("/ara/?q=teknosa") and "Buzdolabı" in ADMIN.text("/ara/?q=buzdolabi")
    assert "Buzdolabı" in ADMIN.text("/ara/?q=beyaz")
    found = ADMIN.text("/ara/?q=teknosa")
    assert "Bonus · Teknosa · Tek çekim · 3.250 ₺" in found and "/taksitler/alisveris/" in found
    assert "Ayşenin çantası" not in ADMIN.text("/ara/?q=canta") and "Buzdolabı" not in AYSE_C.text("/ara/?q=buzdolabi")
    print("  integrations OK")


def test_isolation():
    reset()
    cid = add_card(ADMIN, "Gizli kart", 15, 10)
    pid = add_purchase(ADMIN, cid, "Buzdolabı", "12.000", 12)
    period = ins.period_for(card(cid), T)
    ADMIN.post(f"/taksitler/kart/{cid}/{period}/ode")
    before = (dict(card(cid)), dict(purchase(pid)), count("card_statements"))
    for url in (f"/taksitler/kart/{cid}", f"/taksitler/kart/{cid}/{period}", f"/taksitler/alisveris/{pid}"):
        assert AYSE_C.get(url).status_code == 404, url
    for path, data in ((f"/taksitler/kart/{cid}", {"name": "Çalındı", "statement_day": "1", "due_days": "5"}),
                       (f"/taksitler/kart/{cid}/durum", {}), (f"/taksitler/kart/{cid}/sil", {}),
                       (f"/taksitler/kart/{cid}/{period}/ode", {}), (f"/taksitler/kart/{cid}/{period}/geri-al", {}),
                       (f"/taksitler/alisveris/{pid}", {"card_id": str(cid), "title": "X", "total": "1", "count": "1"}),
                       (f"/taksitler/alisveris/{pid}/kapat", {}), (f"/taksitler/alisveris/{pid}/kapat-geri-al", {}),
                       (f"/taksitler/alisveris/{pid}/sil", {})):
        assert AYSE_C.post(path, data=data).status_code == 404, path
    assert (dict(card(cid)), dict(purchase(pid)), count("card_statements")) == before
    page = AYSE_C.text("/taksitler/")
    assert "Gizli kart" not in page and "Buzdolabı" not in page and "Henüz kart yok" in page
    # Ayşe kendi kartına admin'in alışverişini taşıyamaz, admin'in kartına alışveriş ekleyemez
    r = AYSE_C.post("/taksitler/alisveris/yeni", data={"card_id": str(cid), "title": "X", "total": "10", "count": "1"})
    assert r.status_code == 400 and count("card_purchases", AYSE) == 0
    assert ADMIN.get("/taksitler/kart/999999").status_code == 404
    assert ADMIN.get("/taksitler/alisveris/999999").status_code == 404
    print("  isolation OK")


def test_trash():
    reset()
    cid = add_card(ADMIN, "Bonus", 15, 10)
    pid = add_purchase(ADMIN, cid, "Buzdolabı", "12.000", 12, expense_mode="none")
    other = add_purchase(ADMIN, cid, "Market", "100", 1, expense_mode="none")
    period = ins.period_for(card(cid), T)
    ADMIN.post(f"/taksitler/kart/{cid}/{period}/ode")
    # Alışveriş: çöpe ve geri
    r = ADMIN.post(f"/taksitler/alisveris/{pid}/sil", follow_redirects=True)
    assert "çöp kutusuna taşındı" in r.get_data(as_text=True) and purchase(pid) is None
    item = one("SELECT * FROM trash WHERE module = 'installments' ORDER BY id DESC LIMIT 1")
    assert item["label"] == "💳 Buzdolabı (Bonus · 12.000 ₺)" and item["user_id"] == 1
    assert "Buzdolabı" not in ADMIN.text("/taksitler/")
    AYSE_C.post(f"/cop-kutusu/{item['id']}/geri")
    assert purchase(pid) is None
    ADMIN.post(f"/cop-kutusu/{item['id']}/geri")
    assert purchase(pid)["title"] == "Buzdolabı" and "Buzdolabı" in ADMIN.text("/taksitler/")
    # Kart: alışverişleri ve ekstre kayıtlarıyla
    r = ADMIN.post(f"/taksitler/kart/{cid}/sil", follow_redirects=True)
    assert "Bonus kartı çöp kutusuna taşındı" in r.get_data(as_text=True)
    assert card(cid) is None and purchase(pid) is None and purchase(other) is None and count("card_statements") == 0
    item = one("SELECT * FROM trash WHERE module = 'installments' ORDER BY id DESC LIMIT 1")
    assert item["label"].startswith("💳 Bonus")
    ADMIN.post(f"/cop-kutusu/{item['id']}/geri")
    assert card(cid)["name"] == "Bonus" and purchase(pid) and purchase(other)
    row = one("SELECT paid_on FROM card_statements WHERE card_id = ? AND period = ?", (cid, period))
    assert row["paid_on"] == T.isoformat()
    # Kartı silinmiş alışveriş geri getirilemez (kart önce getirilmeli)
    ADMIN.post(f"/taksitler/alisveris/{other}/sil")
    lone = one("SELECT id FROM trash WHERE module = 'installments' ORDER BY id DESC LIMIT 1")["id"]
    ADMIN.post(f"/taksitler/kart/{cid}/sil")
    r = ADMIN.post(f"/cop-kutusu/{lone}/geri", follow_redirects=True)
    assert "Geri getirilemedi" in r.get_data(as_text=True) and purchase(other) is None
    print("  trash OK")


def test_privacy_and_pages():
    reset()
    cid = add_card(ADMIN, "Bonus", 15, 10, card_number="4543123456789012", cvv="123", expiry="12/29")
    pid = add_purchase(ADMIN, cid, "Buzdolabı", "12.000", 12, D(-70))
    old_card(cid)
    period = ins.period_for(card(cid), T)
    cols = {r["name"] for r in rows("PRAGMA table_info(credit_cards)")} | \
        {r["name"] for r in rows("PRAGMA table_info(card_purchases)")}
    assert not any(x in c for c in cols for x in ("number", "cvv", "cvc", "expir", "last4", "pan"))
    assert "4543123456789012" not in json.dumps([dict(r) for r in rows("SELECT * FROM credit_cards")])
    pages = ["/taksitler/", f"/taksitler/kart/{cid}", "/taksitler/alisveris/yeni", f"/taksitler/alisveris/{pid}",
             f"/taksitler/kart/{cid}/{period}", f"/taksitler/kart/{cid}/{ins.shift(period, -2)}",
             f"/taksitler/kart/{cid}/{ins.shift(period, 5)}", f"/taksitler/kart/{cid}/{ins.shift(period, 30)}",
             f"/taksitler/alisveris/yeni?kart={cid}", "/taksitler/alisveris/yeni?kart=abc"]
    for url in pages:
        html = ADMIN.text(url)
        names = re.findall(r'name="([^"]+)"', html)
        assert not [n for n in names if re.search(r"card.?num|kart.?no|cvv|cvc|^pan$|expir|son.?kullanma|last.?4|son.?4",
                                                  n, re.I)], (url, names)
        assert 'autocomplete="cc-' not in html, url
    assert "Kart numarası, son 4 hane, CVV" in ADMIN.text(f"/taksitler/kart/{cid}")
    assert "Kart bilgisi istenmez" in ADMIN.text("/taksitler/alisveris/yeni")
    assert "Kart bilgisi istenmez" in ADMIN.text(f"/taksitler/alisveris/{pid}")
    # Kart yokken alışveriş formu kart eklemeye yönlendirir
    assert "Önce bir kart ekle" in AYSE_C.text("/taksitler/alisveris/yeni")
    # Geçmiş (gecikmiş) ve gelecek ekstre sayfaları farklı durumlarda açılır
    assert "Gecikti" in ADMIN.text(f"/taksitler/kart/{cid}/{ins.shift(purchase(pid)['first_statement'], 0)}")
    print("  privacy/pages OK")


if __name__ == "__main__":
    test_periods()
    test_split_and_schedule()
    test_card_validation()
    test_purchase_validation()
    test_plan_preview()
    test_statement_totals()
    test_load_and_progress()
    test_early_close()
    test_pay_and_undo()
    test_expense_modes()
    test_telegram_reminders()
    test_telegram_button()
    test_bot_command()
    test_integrations()
    test_isolation()
    test_trash()
    test_privacy_and_pages()
    print("OK")
