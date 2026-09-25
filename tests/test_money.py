"""Para modülleri (harcamalar, faturalar, abonelikler, borç/alacak) testleri.

Çalıştırma: .venv/Scripts/python tests/test_money.py
"""
import os
import sys
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import Client, make_app  # noqa: E402

import pano.external as external  # noqa: E402

# Testler ağa çıkmasın: varsayılan olarak kur yok
external.rates = lambda: None

from pano.auth import create_user  # noqa: E402
from pano.db import execute, query, query_one  # noqa: E402
from pano.modules import debts as debts_mod  # noqa: E402
from pano.modules import expenses as expenses_mod  # noqa: E402
from pano.modules import subscriptions as subs_mod  # noqa: E402
from pano.reminders import next_bill_date  # noqa: E402
from pano.utils import add_cycle, add_months, parse_number, today, today_str  # noqa: E402


def location(resp):
    return urlparse(resp.headers.get("Location", ""))


def month_key(d):
    return f"{d.year:04d}-{d.month:02d}"


def main():
    app = make_app()
    c = Client(app)
    with app.app_context():
        admin_id = query_one("SELECT id FROM users WHERE username = 'admin'")["id"]
        create_user("bob", "bobpass123")
    bob = Client(app, "bob", "bobpass123")

    t = today()
    ts = today_str()
    this_month = month_key(t)
    prev_month_date = add_months(date(t.year, t.month, 1), -1)
    prev_month = month_key(prev_month_date)

    # ---------- Türkçe sayı biçimi ----------
    assert parse_number("1.234,56") == 1234.56
    assert parse_number("12,5") == 12.5
    assert parse_number("1234.56") == 1234.56
    # Sadece binlik ayırıcılı yazım (money_field 1000'i "1.000" gösterir)
    pa = expenses_mod.parse_amount
    assert pa("1.234,56") == 1234.56 and pa("1.000") == 1000 and pa("12.500") == 12500
    assert pa("1.234.567") == 1234567 and pa("12.5") == 12.5 and pa("1234.56") == 1234.56
    assert pa("250,50 ₺") == 250.5 and pa("") is None and pa("abc") is None
    assert not expenses_mod.valid_amount(0)
    assert not expenses_mod.valid_amount(-5)
    assert not expenses_mod.valid_amount(None)
    assert not expenses_mod.valid_amount(float("nan"))
    assert expenses_mod.valid_amount(0.01)

    # ---------- Harcamalar ----------
    # Boş sayfalar
    c.text("/harcamalar/")
    c.text(f"/harcamalar/?ay={prev_month}")
    c.text("/harcamalar/?ay=bozuk")
    c.text("/harcamalar/?ay=2026-13")

    # Panodaki hızlı kutu: next=dashboard -> "/"
    r = c.post("/harcamalar/yeni", {"amount": "1.234,56", "category": "Market", "note": "Migros",
                                     "date": ts, "next": "dashboard"})
    assert r.status_code == 302 and location(r).path == "/", r.headers.get("Location")
    with app.app_context():
        row = query_one("SELECT * FROM expenses WHERE note = 'Migros'")
        assert row["amount"] == 1234.56 and row["category"] == "Market" and row["date"] == ts
        assert row["user_id"] == admin_id
        migros_id = row["id"]

    # Normal ekleme: harcamalar sayfasına döner
    r = c.post("/harcamalar/yeni", {"amount": "65,5", "category": "Market", "note": "Bakkal"})
    assert r.status_code == 302 and location(r).path == "/harcamalar/"
    c.post("/harcamalar/yeni", {"amount": "200", "category": "Yemek", "note": "Akşam yemeği"})
    # Küçük harfle yazılan sabit kategori düzeltilir; "yeni kategori" kutusu çipin önüne geçer
    c.post("/harcamalar/yeni", {"amount": "40", "category_new": "yemek"})
    c.post("/harcamalar/yeni", {"amount": "99,90", "category": "Market", "category_new": "Evcil hayvan"})
    # Kategorisiz -> Diğer
    c.post("/harcamalar/yeni", {"amount": "10"})
    # Geçersiz tutarlar reddedilir
    with app.app_context():
        before = query_one("SELECT COUNT(*) AS n FROM expenses")["n"]
    for bad in ("0", "-5", "abc", "", "0,00"):
        r = c.post("/harcamalar/yeni", {"amount": bad, "category": "Market"})
        assert r.status_code == 302
    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM expenses")["n"] == before

    # Geçen aya ait kayıt: o ayın listesine yönlendirir
    prev_day = prev_month_date.replace(day=15).isoformat()
    r = c.post("/harcamalar/yeni", {"amount": "500", "category": "Giyim", "note": "Mont", "date": prev_day})
    assert r.status_code == 302
    assert parse_qs(location(r).query).get("ay") == [prev_month], r.headers.get("Location")

    # Kategori toplamları
    with app.app_context():
        s = expenses_mod.month_summary(admin_id, t.year, t.month)
        cats = dict(s["categories"])
        assert abs(cats["Market"] - (1234.56 + 65.5)) < 1e-9, cats
        assert cats["Yemek"] == 240, cats
        assert cats["Evcil hayvan"] == 99.9, cats
        assert cats["Diğer"] == 10, cats
        assert "Giyim" not in cats
        assert s["categories"][0][0] == "Market"  # en büyük önce
        assert abs(s["total"] - (1234.56 + 65.5 + 200 + 40 + 99.9 + 10)) < 1e-9
        assert s["prev_total"] == 500
        assert s["change"] is not None and s["change"] > 0
        assert s["days"] == t.day
        assert len(s["days_list"]) == 1 and s["days_list"][0]["date"] == ts
        prev_s = expenses_mod.month_summary(admin_id, prev_month_date.year, prev_month_date.month)
        assert dict(prev_s["categories"]) == {"Giyim": 500}

    html = c.text("/harcamalar/")
    assert "1.300,06 ₺" in html  # Market toplamı
    assert "1.649,96 ₺" in html  # ay toplamı
    assert "Migros" in html and "Mont" not in html
    assert "Evcil hayvan" in html
    html = c.text(f"/harcamalar/?ay={prev_month}")
    assert "Mont" in html and "Migros" not in html and "Bu aya dön" in html
    next_month = month_key(add_months(date(t.year, t.month, 1), 1))
    assert f"ay={prev_month}" in c.text("/harcamalar/") and f"ay={next_month}" in c.text("/harcamalar/")

    # Düzenle / sil
    c.text(f"/harcamalar/{migros_id}")
    r = c.post(f"/harcamalar/{migros_id}", {"amount": "1.000", "category": "Ev", "note": "Migros", "date": ts})
    assert r.status_code == 302
    with app.app_context():
        row = query_one("SELECT * FROM expenses WHERE id = ?", (migros_id,))
        assert row["amount"] == 1000 and row["category"] == "Ev"
    r = c.post(f"/harcamalar/{migros_id}", {"amount": "0", "category": "Ev", "date": ts})
    with app.app_context():
        assert query_one("SELECT amount FROM expenses WHERE id = ?", (migros_id,))["amount"] == 1000

    # ---------- Faturalar ----------
    c.text("/faturalar/")
    c.text("/faturalar/?sekme=odenen")
    due = (t - timedelta(days=2)).isoformat()
    r = c.post("/faturalar/yeni", {"name": "Elektrik", "amount": "450,75", "due_date": due,
                                    "recurring": "1", "note": "Abone 123"})
    assert r.status_code == 302
    c.post("/faturalar/yeni", {"name": "Su", "due_date": (t + timedelta(days=10)).isoformat()})  # tutarsız
    c.post("/faturalar/yeni", {"name": "Tamir", "amount": "300", "due_date": ts})  # tek seferlik
    # Geçersizler
    c.post("/faturalar/yeni", {"name": "", "amount": "10", "due_date": ts})
    c.post("/faturalar/yeni", {"name": "X", "amount": "10", "due_date": ""})
    c.post("/faturalar/yeni", {"name": "Y", "amount": "-3", "due_date": ts})
    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM bills")["n"] == 3
        elektrik = query_one("SELECT * FROM bills WHERE name = 'Elektrik'")
        su = query_one("SELECT * FROM bills WHERE name = 'Su'")
        tamir = query_one("SELECT * FROM bills WHERE name = 'Tamir'")
        assert elektrik["amount"] == 450.75 and elektrik["recurring"] == 1
        assert su["amount"] is None
    html = c.text("/faturalar/")
    assert "750,75 ₺" in html  # bekleyen toplam (Su tutarsız)
    assert "Harcamalara da ekle" in html
    c.text(f"/faturalar/{elektrik['id']}")

    with app.app_context():
        exp_before = query_one("SELECT COUNT(*) AS n FROM expenses")["n"]
    r = c.post(f"/faturalar/{elektrik['id']}/ode", {"add_expense": "1"})
    assert r.status_code == 302
    nd = next_bill_date(due)
    with app.app_context():
        e = query_one("SELECT * FROM bills WHERE id = ?", (elektrik["id"],))
        assert e["paid"] == 1 and e["paid_at"] == ts
        nxt = query("SELECT * FROM bills WHERE name = 'Elektrik' AND due_date = ?", (nd,))
        assert len(nxt) == 1 and nxt[0]["paid"] == 0 and nxt[0]["recurring"] == 1
        assert nxt[0]["amount"] == 450.75 and nxt[0]["note"] == "Abone 123"
        assert nd == add_months(date.fromisoformat(due), 1).isoformat()
        exp = query("SELECT * FROM expenses WHERE category = 'Faturalar'")
        assert len(exp) == 1 and exp[0]["amount"] == 450.75 and exp[0]["note"] == "Elektrik"
        assert exp[0]["date"] == ts and exp[0]["user_id"] == admin_id
        assert query_one("SELECT COUNT(*) AS n FROM expenses")["n"] == exp_before + 1
    assert "Elektrik" in c.text("/faturalar/?sekme=odenen")

    # Tekrar ödeme denemesi hiçbir şey yapmaz
    c.post(f"/faturalar/{elektrik['id']}/ode", {"add_expense": "1"})
    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM expenses WHERE category = 'Faturalar'")["n"] == 1

    # Geri al: sonraki fatura silinmez; yeniden ödeyince kopya oluşmaz; kutu işaretsiz -> harcama yok
    r = c.post(f"/faturalar/{elektrik['id']}/geri-al", {"next": "/faturalar/?sekme=odenen"})
    assert r.status_code == 302 and location(r).path == "/faturalar/"
    with app.app_context():
        e = query_one("SELECT * FROM bills WHERE id = ?", (elektrik["id"],))
        assert e["paid"] == 0 and e["paid_at"] is None
        assert query_one("SELECT COUNT(*) AS n FROM bills WHERE name = 'Elektrik' AND due_date = ?", (nd,))["n"] == 1
    c.post(f"/faturalar/{elektrik['id']}/ode", {})
    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM bills WHERE name = 'Elektrik' AND due_date = ?", (nd,))["n"] == 1
        assert query_one("SELECT COUNT(*) AS n FROM expenses WHERE category = 'Faturalar'")["n"] == 1

    # Tek seferlik fatura: sonraki oluşmaz; tutarsız fatura: harcama eklenmez
    c.post(f"/faturalar/{tamir['id']}/ode", {})
    c.post(f"/faturalar/{su['id']}/ode", {"add_expense": "1"})
    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM bills WHERE name = 'Tamir'")["n"] == 1
        assert query_one("SELECT COUNT(*) AS n FROM expenses WHERE category = 'Faturalar'")["n"] == 1
    c.text("/faturalar/?sekme=odenen")

    # Düzenle
    r = c.post(f"/faturalar/{su['id']}", {"name": "Su (İSKİ)", "amount": "120,40", "due_date": ts})
    assert r.status_code == 302
    with app.app_context():
        s_row = query_one("SELECT * FROM bills WHERE id = ?", (su["id"],))
        assert s_row["name"] == "Su (İSKİ)" and s_row["amount"] == 120.4 and s_row["recurring"] == 0

    # ---------- Abonelikler ----------
    c.text("/abonelikler/")
    r = c.post("/abonelikler/yeni", {"name": "Netflix", "amount": "229,99", "currency": "TRY",
                                      "cycle": "monthly", "next_date": ts, "category": "Dizi / film",
                                      "active": "1"})
    assert r.status_code == 302
    c.post("/abonelikler/yeni", {"name": "Spotify", "amount": "10", "currency": "USD", "cycle": "monthly",
                                  "next_date": ts, "active": "1"})
    c.post("/abonelikler/yeni", {"name": "Alan adı", "amount": "1.200", "currency": "TRY", "cycle": "yearly",
                                  "next_date": ts, "active": "1"})
    c.post("/abonelikler/yeni", {"name": "Spor", "amount": "150", "currency": "TRY", "cycle": "weekly",
                                  "next_date": ts, "active": "1"})
    c.post("/abonelikler/yeni", {"name": "Eski", "amount": "50", "currency": "EUR", "cycle": "monthly",
                                  "next_date": ts})  # aktif değil
    c.post("/abonelikler/yeni", {"name": "Bedava", "amount": "0", "currency": "TRY", "cycle": "monthly",
                                  "next_date": ts, "active": "1"})  # reddedilir
    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM subscriptions")["n"] == 5
        assert query_one("SELECT active FROM subscriptions WHERE name = 'Eski'")["active"] == 0

    try_monthly = 229.99 + 1200 / 12 + 150 * 52 / 12  # = 979.99
    # Kur yok: para birimine göre ayrı toplamlar + not
    with app.app_context():
        active = query("SELECT * FROM subscriptions WHERE active = 1")
        tot = subs_mod.compute_totals(active, None)
        assert tot["monthly"] is None and tot["rates_missing"]
        by = {cur: m for cur, m, _y in tot["by_currency"]}
        assert abs(by["TRY"] - round(try_monthly, 2)) < 1e-9 and by["USD"] == 10, by
        assert "EUR" not in by
        assert [cur for cur, _m, _y in tot["by_currency"]] == ["TRY", "USD"]
    html = c.text("/abonelikler/")
    assert "979,99 ₺" in html and "10 $" in html and "kuru şu an alınamadı" in html
    assert "11.759,88 ₺" in html  # yıllık TL kısmı
    # Kur var: hepsi TL'ye çevrilir
    external.rates = lambda: {"USD": 40.0, "EUR": 45.0, "GBP": 50.0, "date": ts}
    try:
        with app.app_context():
            tot = subs_mod.compute_totals(active, external.rates())
            assert abs(tot["monthly"] - round(try_monthly + 400, 2)) < 1e-9 and tot["converted"]
            assert abs(tot["yearly"] - round((try_monthly + 400) * 12, 2)) < 1e-6
        html = c.text("/abonelikler/")
        assert "1.379,99 ₺" in html and "≈ 400 ₺" in html and "alınamadı" not in html
    finally:
        external.rates = lambda: None
    # Sadece TL varken kur hiç istenmez
    with app.app_context():
        tot = subs_mod.compute_totals([r for r in active if r["currency"] == "TRY"], None)
        assert abs(tot["monthly"] - round(try_monthly, 2)) < 1e-9 and not tot["rates_missing"]

    # Tarihi geçmiş aboneliğin tarihi ileri alınır
    past = (t - timedelta(days=40)).isoformat()
    past_week = (t - timedelta(days=15)).isoformat()
    with app.app_context():
        old_id = execute("INSERT INTO subscriptions (user_id, name, amount, cycle, next_date) VALUES (?, ?, ?, ?, ?)",
                         (admin_id, "Geçmiş", 50, "monthly", past)).lastrowid
        week_id = execute("INSERT INTO subscriptions (user_id, name, amount, cycle, next_date) VALUES (?, ?, ?, ?, ?)",
                          (admin_id, "Haftalık", 20, "weekly", past_week)).lastrowid
        paused_id = execute("INSERT INTO subscriptions (user_id, name, amount, cycle, next_date, active)"
                            " VALUES (?, ?, ?, ?, ?, 0)", (admin_id, "Durmuş", 20, "monthly", past)).lastrowid
    c.text("/abonelikler/")

    def expected(start, cycle):
        d = date.fromisoformat(start)
        while d < t:
            d = add_cycle(d, cycle)
        return d.isoformat()

    with app.app_context():
        got = query_one("SELECT next_date FROM subscriptions WHERE id = ?", (old_id,))["next_date"]
        assert got >= ts and got == expected(past, "monthly"), got
        got = query_one("SELECT next_date FROM subscriptions WHERE id = ?", (week_id,))["next_date"]
        assert got >= ts and got == expected(past_week, "weekly"), got
        assert query_one("SELECT next_date FROM subscriptions WHERE id = ?", (paused_id,))["next_date"] == past

    # Durum değiştir, düzenle
    with app.app_context():
        netflix = query_one("SELECT * FROM subscriptions WHERE name = 'Netflix'")
    c.text(f"/abonelikler/{netflix['id']}")
    c.post(f"/abonelikler/{netflix['id']}/durum")
    with app.app_context():
        assert query_one("SELECT active FROM subscriptions WHERE id = ?", (netflix["id"],))["active"] == 0
    assert "Durdurulanlar" in c.text("/abonelikler/")
    c.post(f"/abonelikler/{netflix['id']}/durum")
    r = c.post(f"/abonelikler/{netflix['id']}", {"name": "Netflix", "amount": "249,99", "currency": "TRY",
                                                  "cycle": "yearly", "next_date": ts, "active": "1"})
    assert r.status_code == 302
    with app.app_context():
        n = query_one("SELECT * FROM subscriptions WHERE id = ?", (netflix["id"],))
        assert n["active"] == 1 and n["amount"] == 249.99 and n["cycle"] == "yearly"

    # ---------- Borç / Alacak ----------
    c.text("/borclar/")
    for tab in ("alacaklarim", "borclarim", "kapananlar", "bilinmeyen"):
        c.text(f"/borclar/?sekme={tab}")
    c.post("/borclar/yeni", {"person": "Ahmet", "direction": "lent", "amount": "1.500", "currency": "TRY",
                              "date": ts, "due_date": (t + timedelta(days=5)).isoformat()})
    c.post("/borclar/yeni", {"person": "Ahmet", "direction": "borrowed", "amount": "200", "currency": "TRY"})
    c.post("/borclar/yeni", {"person": "Ayşe", "direction": "borrowed", "amount": "20", "currency": "USD",
                              "note": "Kitap"})
    c.post("/borclar/yeni", {"person": "Zeynep", "direction": "lent", "amount": "0", "currency": "TRY"})  # red
    c.post("/borclar/yeni", {"person": "", "direction": "lent", "amount": "5", "currency": "TRY"})  # red
    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM debts")["n"] == 3
        ahmet = query_one("SELECT * FROM debts WHERE person = 'Ahmet' AND direction = 'lent'")
        ayse = query_one("SELECT * FROM debts WHERE person = 'Ayşe'")
        assert ahmet["amount"] == 1500 and ahmet["settled"] == 0 and ahmet["date"] == ts
        assert ayse["date"] == ts and ayse["due_date"] is None and ayse["currency"] == "USD"
        people = {p["name"]: dict(p["net"])
                  for p in debts_mod.person_summary(query("SELECT * FROM debts WHERE settled = 0"))}
        assert people == {"Ahmet": {"TRY": 1300}, "Ayşe": {"USD": -20}}, people
    html = c.text("/borclar/")
    assert "1.500 ₺" in html and "+1.300 ₺" in html and "−20 $" in html
    html = c.text("/borclar/?sekme=borclarim")
    assert "Kitap" in html and "200 ₺" in html
    c.text(f"/borclar/{ahmet['id']}")

    r = c.post(f"/borclar/{ahmet['id']}/kapat", {"next": "/borclar/"})
    assert r.status_code == 302
    with app.app_context():
        d = query_one("SELECT * FROM debts WHERE id = ?", (ahmet["id"],))
        assert d["settled"] == 1 and d["settled_at"] == ts
    html = c.text("/borclar/?sekme=kapananlar")
    assert "Ahmet" in html and "Geri aç" in html
    r = c.post(f"/borclar/{ahmet['id']}/ac")
    assert r.status_code == 302
    with app.app_context():
        d = query_one("SELECT * FROM debts WHERE id = ?", (ahmet["id"],))
        assert d["settled"] == 0 and d["settled_at"] is None
    r = c.post(f"/borclar/{ahmet['id']}", {"person": "Ahmet Y.", "direction": "lent", "amount": "1.250,50",
                                            "currency": "EUR", "date": ts, "due_date": ""})
    assert r.status_code == 302 and parse_qs(location(r).query).get("sekme") == ["alacaklarim"]
    with app.app_context():
        d = query_one("SELECT * FROM debts WHERE id = ?", (ahmet["id"],))
        assert d["person"] == "Ahmet Y." and d["amount"] == 1250.5 and d["currency"] == "EUR"
        assert d["due_date"] is None

    # ---------- Sahiplik: başka kullanıcı 404 alır ----------
    with app.app_context():
        ids = {
            "expense": query_one("SELECT id FROM expenses WHERE user_id = ? LIMIT 1", (admin_id,))["id"],
            "bill": query_one("SELECT id FROM bills WHERE user_id = ? AND paid = 0 LIMIT 1", (admin_id,))["id"],
            "paid_bill": query_one("SELECT id FROM bills WHERE user_id = ? AND paid = 1 LIMIT 1", (admin_id,))["id"],
            "sub": netflix["id"],
            "debt": ahmet["id"],
        }
    for url in (f"/harcamalar/{ids['expense']}", f"/faturalar/{ids['bill']}",
                f"/abonelikler/{ids['sub']}", f"/borclar/{ids['debt']}"):
        assert bob.get(url).status_code == 404, url
    posts = [
        (f"/harcamalar/{ids['expense']}", {"amount": "1", "category": "Diğer", "date": ts}),
        (f"/harcamalar/{ids['expense']}/sil", {}),
        (f"/faturalar/{ids['bill']}", {"name": "Hack", "due_date": ts}),
        (f"/faturalar/{ids['bill']}/ode", {"add_expense": "1"}),
        (f"/faturalar/{ids['paid_bill']}/geri-al", {}),
        (f"/faturalar/{ids['bill']}/sil", {}),
        (f"/abonelikler/{ids['sub']}", {"name": "Hack", "amount": "1", "next_date": ts}),
        (f"/abonelikler/{ids['sub']}/durum", {}),
        (f"/abonelikler/{ids['sub']}/sil", {}),
        (f"/borclar/{ids['debt']}", {"person": "Hack", "direction": "lent", "amount": "1"}),
        (f"/borclar/{ids['debt']}/kapat", {}),
        (f"/borclar/{ids['debt']}/ac", {}),
        (f"/borclar/{ids['debt']}/sil", {}),
    ]
    for url, data in posts:
        assert bob.post(url, data).status_code == 404, url
    with app.app_context():
        assert query_one("SELECT COUNT(*) AS n FROM expenses WHERE id = ?", (ids["expense"],))["n"] == 1
        assert query_one("SELECT paid, name FROM bills WHERE id = ?", (ids["bill"],))["paid"] == 0
        assert query_one("SELECT paid FROM bills WHERE id = ?", (ids["paid_bill"],))["paid"] == 1
        assert query_one("SELECT active, name FROM subscriptions WHERE id = ?", (ids["sub"],))["name"] == "Netflix"
        assert query_one("SELECT settled, person FROM debts WHERE id = ?", (ids["debt"],))["person"] == "Ahmet Y."
        bob_id = query_one("SELECT id FROM users WHERE username = 'bob'")["id"]
        assert query_one("SELECT COUNT(*) AS n FROM expenses WHERE user_id = ?", (bob_id,))["n"] == 0
    # Bob'un sayfalarında admin'in verisi görünmez
    for url, secret in (("/harcamalar/", "Bakkal"), ("/faturalar/", "Abone 123"),
                        ("/faturalar/?sekme=odenen", "Tamir"), ("/abonelikler/", "Geçmiş"),
                        ("/borclar/", "Ahmet"), ("/borclar/?sekme=borclarim", "Ayşe")):
        assert secret not in bob.text(url), url
    # Bob kendi harcamasını ekleyebilir; admin'in toplamı değişmez
    bob.post("/harcamalar/yeni", {"amount": "7", "category": "Yemek"})
    with app.app_context():
        assert expenses_mod.month_summary(bob_id, t.year, t.month)["total"] == 7

    # ---------- Silme ----------
    with app.app_context():
        bakkal = query_one("SELECT id FROM expenses WHERE note = 'Bakkal'")["id"]
    r = c.post(f"/harcamalar/{bakkal}/sil")
    assert r.status_code == 302 and parse_qs(location(r).query).get("ay") == [this_month]
    c.post(f"/faturalar/{tamir['id']}/sil")
    c.post(f"/abonelikler/{old_id}/sil")
    c.post(f"/borclar/{ayse['id']}/sil")
    with app.app_context():
        assert query_one("SELECT 1 FROM expenses WHERE id = ?", (bakkal,)) is None
        assert query_one("SELECT 1 FROM bills WHERE id = ?", (tamir["id"],)) is None
        assert query_one("SELECT 1 FROM subscriptions WHERE id = ?", (old_id,)) is None
        assert query_one("SELECT 1 FROM debts WHERE id = ?", (ayse["id"],)) is None

    # Giriş yapmamış kullanıcı yönlendirilir
    anon = app.test_client()
    for url in ("/harcamalar/", "/faturalar/", "/abonelikler/", "/borclar/"):
        r = anon.get(url)
        assert r.status_code == 302 and "/giris" in r.headers["Location"], url

    print("OK")


if __name__ == "__main__":
    main()
