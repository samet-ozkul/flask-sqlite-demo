"""Varlıklar: değer hesabı, Türkçe sayı girişi, eksik fiyat, düzenleme/silme, geçmiş grafiği, sahiplik.

Çalıştır: .venv/Scripts/python tests/test_assets.py
"""
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

app = make_app()

import pano.external as ext  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query, query_one  # noqa: E402
from pano.modules import assets as mod  # noqa: E402
from pano.utils import fmt_money, today  # noqa: E402

# Ağ yok: kur ve altın fiyatı testten kontrol edilir
RATES = [{"USD": 40.0, "EUR": 45.0, "GBP": 50.0, "date": "2026-09-25"}]
GOLD = [None]
ext.rates = lambda: RATES[0]
ext.gold_gram_try = lambda: GOLD[0]

C = Client(app)
with app.app_context():
    ADMIN = query_one("SELECT id FROM users WHERE username = 'admin'")["id"]


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def rows(sql, args=()):
    with app.app_context():
        return query(sql, args)


def run(sql, args=()):
    with app.app_context():
        execute(sql, args)


def asset(name):
    return one("SELECT * FROM assets WHERE name = ?", (name,))


def count():
    return one("SELECT COUNT(*) AS n FROM assets")["n"]


def add(**data):
    return C.post("/varliklar/yeni", data=data, follow_redirects=True).get_data(as_text=True)


def totals(user_id=None):
    with app.app_context():
        return mod.total_value(user_id or ADMIN)


def values_by_name(tv):
    return {it["asset"]["name"]: (it["value"], it["source"]) for it in tv["items"]}


def snapshot_today():
    return one("SELECT total_try FROM asset_snapshots WHERE user_id = ? AND date = ?", (ADMIN, today().isoformat()))


def test_empty_page():
    for r in (RATES[0], None):
        RATES[0] = r
        h = C.text("/varliklar/")
        assert "Henüz varlık yok" in h and 'name="quantity"' in h and 'name="unit_price"' in h
        assert "Altın fiyatı otomatik gelmiyorsa" in h and "Nakit / mevduat" in h
    RATES[0] = {"USD": 40.0, "EUR": 45.0, "GBP": 50.0, "date": "2026-09-25"}
    assert C.get("/varliklar/", follow_redirects=False).status_code == 200
    # Giriş yapmadan erişilemez
    assert app.test_client().get("/varliklar/").status_code in (302, 401)
    assert one("SELECT COUNT(*) AS n FROM asset_snapshots")["n"] == 0  # varlık yokken kayıt yok
    print("  empty page OK")


def test_validation():
    before = count()
    for data in (
        {"name": "", "kind": "cash", "quantity": "100"},
        {"name": "Sıfır", "kind": "cash", "quantity": "0"},
        {"name": "Eksi", "kind": "cash", "quantity": "-5"},
        {"name": "Yazı", "kind": "cash", "quantity": "abc"},
        {"name": "Boş", "kind": "cash", "quantity": ""},
        {"name": "NaN", "kind": "cash", "quantity": "nan"},
        {"name": "Sonsuz", "kind": "cash", "quantity": "inf"},
        {"name": "Kötü fiyat", "kind": "gold", "quantity": "5", "unit_price": "abc"},
        {"name": "Sıfır fiyat", "kind": "other", "quantity": "1", "unit_price": "0"},
    ):
        h = add(**data)
        assert "flash error" in h, data
    assert count() == before
    print("  validation OK")


def test_create_and_values():
    h = add(name="Vadeli hesap", kind="cash", quantity="1.500,50")
    assert "Vadeli hesap eklendi" in h
    a = asset("Vadeli hesap")
    assert a["quantity"] == 1500.5 and a["currency"] == "TRY" and a["unit_price"] is None
    add(name="Dolar", kind="fx", currency="USD", quantity="1.000")
    add(name="Euro", kind="fx", currency="EUR", quantity="200")
    add(name="Bilezik", kind="gold", quantity="10", unit_price="4.000")      # canlı altın yok -> elle fiyat
    add(name="Araba", kind="other", quantity="1", unit_price="850.000")
    add(name="Cüzdan", kind="cash", quantity="250", unit_price="99", currency="USD")  # nakitte yok sayılır
    add(name="Geçersiz döviz", kind="fx", currency="JPY", quantity="10")    # bilinmeyen -> USD
    assert asset("Dolar")["quantity"] == 1000 and asset("Dolar")["currency"] == "USD"
    assert asset("Bilezik")["unit_price"] == 4000 and asset("Araba")["unit_price"] == 850000
    c = asset("Cüzdan")
    assert c["unit_price"] is None and c["currency"] == "TRY"
    assert asset("Geçersiz döviz")["currency"] == "USD"
    run("DELETE FROM assets WHERE name = 'Geçersiz döviz'")

    tv = totals()
    assert set(tv) == {"total", "missing", "items"} and tv["missing"] == 0
    assert all(set(it) == {"asset", "value", "source"} for it in tv["items"])
    v = values_by_name(tv)
    assert v["Vadeli hesap"] == (1500.5, "nakit")
    assert v["Dolar"] == (40000.0, "canlı kur")
    assert v["Euro"] == (9000.0, "canlı kur")
    assert v["Bilezik"] == (40000.0, "elle girilen fiyat")
    assert v["Araba"] == (850000.0, "elle girilen fiyat")
    assert v["Cüzdan"] == (250.0, "nakit")
    expected = 1500.5 + 40000 + 9000 + 40000 + 850000 + 250
    assert abs(tv["total"] - expected) < 0.001 and isinstance(tv["total"], float)

    # Canlı altın fiyatı gelince elle girilen fiyatın önüne geçer
    GOLD[0] = 5000.0
    v = values_by_name(totals())
    assert v["Bilezik"] == (50000.0, "canlı kur")
    GOLD[0] = None

    h = C.text("/varliklar/")
    assert fmt_money(expected) in h                         # toplam
    assert "1.000 $" in h and "200 €" in h and "10 gr" in h and "1 adet" in h and "1.500,50 ₺" in h
    assert "40.000 ₺" in h and "850.000 ₺" in h
    assert "1 $ = 40 ₺" in h and "1 gr = 4.000 ₺" in h
    assert "canlı kur" in h and "elle girilen fiyat" in h
    assert "Dağılım" in h and "bar-fill" in h and "Nakit / mevduat" in h and "Altın" in h
    assert "fiyatı bulunamadı" not in h
    # Değere göre sıralı: en değerli (Araba) önce
    listing = h.split("Varlıklarım", 1)[1]
    assert listing.index("Araba") < listing.index("Bilezik") < listing.index("Cüzdan")
    print("  create/values OK")


def test_snapshot():
    # index() bugünün kaydını yazdı
    snap = snapshot_today()
    assert snap is not None and abs(snap["total_try"] - totals()["total"]) < 0.001
    # Tekrar çağrı aynı günün satırını günceller, yeni satır açmaz
    with app.app_context():
        assert mod.record_snapshot(ADMIN) == totals()["total"]
    assert one("SELECT COUNT(*) AS n FROM asset_snapshots WHERE user_id = ?", (ADMIN,))["n"] == 1
    # Tek kayıtla grafik yok, açıklama var
    h = C.text("/varliklar/")
    assert "<polyline" not in h and "en az iki günlük kayıt" in h
    print("  snapshot OK")


def test_missing_prices():
    before = snapshot_today()["total_try"]
    # Altın: canlı fiyat yok, elle fiyat yok -> değer hesaplanamaz
    h = add(name="Çeyrek", kind="gold", quantity="3,5")
    assert "Canlı altın fiyatı alınamıyor" in h
    # Diğer: birim fiyat yok -> uyarı
    h = add(name="Tablo", kind="other", quantity="1")
    assert "Birim fiyat girilmediği için" in h
    tv = totals()
    v = values_by_name(tv)
    assert tv["missing"] == 2 and v["Çeyrek"] == (None, "fiyat yok") and v["Tablo"] == (None, "fiyat yok")
    assert abs(tv["total"] - before) < 0.001                 # eksikler toplama katılmaz
    h = C.text("/varliklar/")
    assert "2 varlığın fiyatı bulunamadı" in h and "fiyat yok" in h and "2 tanesi hariç" in h
    assert "3,5 gr" in h
    # Eksik varken bugünün kaydı yazılmaz / bozulmaz
    with app.app_context():
        assert mod.record_snapshot(ADMIN) is None
    assert snapshot_today()["total_try"] == before
    run("DELETE FROM assets WHERE name = 'Tablo'")

    # Canlı altın gelince Çeyrek de fiyatlanır
    GOLD[0] = 5000.0
    v = values_by_name(totals())
    assert v["Çeyrek"] == (17500.0, "canlı kur") and totals()["missing"] == 0

    # Kur alınamazsa: döviz eksik, sayfa yine açılır
    RATES[0] = None
    tv = totals()
    v = values_by_name(tv)
    assert tv["missing"] == 2 and v["Dolar"] == (None, "fiyat yok") and v["Euro"] == (None, "fiyat yok")
    r = C.get("/varliklar/")
    h = r.get_data(as_text=True)
    assert r.status_code == 200 and "2 varlığın fiyatı bulunamadı" in h
    # Kur yokken elle girilen birim fiyat yedek olarak kullanılır
    add(name="Sterlin", kind="fx", currency="GBP", quantity="10", unit_price="52")
    assert values_by_name(totals())["Sterlin"] == (520.0, "elle girilen fiyat")
    RATES[0] = {"USD": 40.0, "EUR": 45.0, "GBP": 50.0, "date": "2026-09-25"}
    assert values_by_name(totals())["Sterlin"] == (500.0, "canlı kur")
    run("DELETE FROM assets WHERE name IN ('Sterlin', 'Çeyrek')")
    GOLD[0] = None
    print("  missing prices OK")


def test_chart():
    t = today()
    run("INSERT OR REPLACE INTO asset_snapshots (user_id, date, total_try) VALUES (?, ?, ?)",
        (ADMIN, (t - timedelta(days=30)).isoformat(), 800000.0))
    run("INSERT OR REPLACE INTO asset_snapshots (user_id, date, total_try) VALUES (?, ?, ?)",
        (ADMIN, (t - timedelta(days=120)).isoformat(), 1.0))       # 90 günden eski: grafikte yok
    h = C.text("/varliklar/")
    current = totals()["total"]
    assert "<polyline" in h and "<polygon" in h and "Son 90 gün" in h
    assert fmt_money(800000.0) in h and fmt_money(current) in h and "2 kayıt" in h
    change = (current - 800000) * 100 / 800000
    from pano.utils import fmt_number
    assert f"%{fmt_number(abs(change), 1)}" in h and "▲" in h
    assert fmt_money(1.0) not in h

    # Birim testleri
    assert mod.history_chart([]) is None and mod.history_chart([("2026-09-01", 5.0)]) is None
    c = mod.history_chart([("2026-09-01", 100.0), ("2026-09-11", 150.0), ("2026-09-21", 50.0)])
    assert c["first"] == 100 and c["last"] == 50 and c["change"] == -50 and c["diff"] == -50
    assert c["low"] == 50 and c["high"] == 150 and c["count"] == 3
    xs = [float(p.split(",")[0]) for p in c["line"].split()]
    ys = [float(p.split(",")[1]) for p in c["line"].split()]
    assert xs[0] == 6.0 and xs[-1] == 594.0 and abs(xs[1] - 300.0) < 0.1      # tarihle orantılı
    assert ys[1] == 6.0 and ys[2] == 134.0                                     # en yüksek üstte
    flat = mod.history_chart([("2026-09-01", 10.0), ("2026-09-02", 10.0)])
    assert all(p.endswith(",70.0") for p in flat["line"].split()) and flat["change"] == 0
    assert mod.history_chart([("2026-09-01", 0.0), ("2026-09-02", 10.0)])["change"] is None
    print("  chart OK")


def test_edit_delete():
    a = asset("Vadeli hesap")
    h = C.text(f"/varliklar/{a['id']}")
    assert 'value="1.500,5"' in h and "Vadeli hesap" in h and "Şu anki değer" in h
    g_ = asset("Bilezik")
    h = C.text(f"/varliklar/{g_['id']}")
    assert 'value="4.000"' in h and "elle girilen fiyat" in h and "40.000 ₺" in h
    # Türkçe sayı ile güncelle
    r = C.post(f"/varliklar/{a['id']}", data={"name": "Vadeli hesap", "kind": "cash", "quantity": "2.000,25",
                                             "note": "Ziraat"}, follow_redirects=True)
    assert "güncellendi" in r.get_data(as_text=True)
    a2 = asset("Vadeli hesap")
    assert a2["quantity"] == 2000.25 and a2["note"] == "Ziraat" and a2["updated_at"] is not None
    # Tür değiştir: nakit -> sterlin
    c = asset("Cüzdan")
    C.post(f"/varliklar/{c['id']}", data={"name": "Cüzdan", "kind": "fx", "currency": "GBP", "quantity": "100"})
    c2 = asset("Cüzdan")
    assert (c2["kind"], c2["currency"], c2["quantity"]) == ("fx", "GBP", 100)
    assert values_by_name(totals())["Cüzdan"] == (5000.0, "canlı kur")
    # Geçersiz düzenleme reddedilir, kayıt değişmez
    r = C.post(f"/varliklar/{c['id']}", data={"name": "Cüzdan", "kind": "fx", "currency": "GBP", "quantity": "0"},
               follow_redirects=True)
    assert "flash error" in r.get_data(as_text=True) and asset("Cüzdan")["quantity"] == 100
    # Elle fiyatı silmek: altın fiyatı boş bırakılınca NULL
    C.post(f"/varliklar/{g_['id']}", data={"name": "Bilezik", "kind": "gold", "quantity": "10", "unit_price": ""})
    assert asset("Bilezik")["unit_price"] is None
    C.post(f"/varliklar/{g_['id']}", data={"name": "Bilezik", "kind": "gold", "quantity": "10", "unit_price": "4.100,5"})
    assert asset("Bilezik")["unit_price"] == 4100.5
    # Silme
    araba = asset("Araba")
    r = C.post(f"/varliklar/{araba['id']}/sil", follow_redirects=True)
    assert "Araba çöp kutusuna taşındı" in r.get_data(as_text=True) and asset("Araba") is None
    assert C.get(f"/varliklar/{araba['id']}").status_code == 404
    assert C.post(f"/varliklar/{araba['id']}/sil").status_code == 404
    print("  edit/delete OK")


def test_ownership():
    with app.app_context():
        create_user("veli", "veli12345")
        veli_id = query_one("SELECT id FROM users WHERE username = 'veli'")["id"]
    veli = Client(app, "veli", "veli12345")
    a = asset("Vadeli hesap")
    assert veli.get(f"/varliklar/{a['id']}").status_code == 404
    assert veli.post(f"/varliklar/{a['id']}", data={"name": "X", "kind": "cash", "quantity": "1"}).status_code == 404
    assert veli.post(f"/varliklar/{a['id']}/sil").status_code == 404
    assert asset("Vadeli hesap")["name"] == "Vadeli hesap"
    h = veli.text("/varliklar/")
    assert '<div class="title">Vadeli hesap' not in h and "Ziraat" not in h
    assert "Henüz varlık yok" in h and "<polyline" not in h
    tv = totals(veli_id)
    assert tv == {"total": 0.0, "missing": 0, "items": []}
    with app.app_context():
        assert mod.record_snapshot(veli_id) is None
    assert one("SELECT COUNT(*) AS n FROM asset_snapshots WHERE user_id = ?", (veli_id,))["n"] == 0
    # Veli'nin varlığı admin toplamına karışmaz
    before = totals()["total"]
    veli.post("/varliklar/yeni", data={"name": "Veli nakit", "kind": "cash", "quantity": "999"})
    assert totals(veli_id)["total"] == 999 and totals()["total"] == before
    assert "Veli nakit" not in C.text("/varliklar/")
    print("  ownership OK")


if __name__ == "__main__":
    test_empty_page()
    test_validation()
    test_create_and_values()
    test_snapshot()
    test_missing_prices()
    test_chart()
    test_edit_delete()
    test_ownership()
    print("OK")
