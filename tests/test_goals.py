"""Hedefler (birikim hedefleri) modülü testleri.

Çalıştırma: .venv/Scripts/python tests/test_goals.py
"""
import html as htmllib
import os
import re
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import Client, make_app  # noqa: E402

app = make_app()

from pano import utils  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query, query_one  # noqa: E402
from pano.modules import goals as goals_mod  # noqa: E402

T = utils.today()


def iso(days_ago):
    return (T - timedelta(days=days_ago)).isoformat()


def db(fn):
    with app.app_context():
        return fn()


def plain(page):
    """Etiketleri at, HTML kaçışlarını çöz, boşlukları tekle."""
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", page)))


def goal(gid):
    return db(lambda: query_one("SELECT * FROM goals WHERE id = ?", (gid,)))


def goal_by_name(name, user_id=None):
    return db(lambda: query_one("SELECT * FROM goals WHERE name = ? ORDER BY id DESC", (name,)))


def saved(gid):
    return db(lambda: round(query_one("SELECT COALESCE(SUM(amount), 0) AS s FROM goal_entries WHERE goal_id = ?",
                                      (gid,))["s"], 2))


def entries(gid):
    return db(lambda: query("SELECT * FROM goal_entries WHERE goal_id = ? ORDER BY id", (gid,)))


def count(table, where="1=1", args=()):
    return db(lambda: query_one(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", args)["n"])


def summary(user_id):
    return db(lambda: goals_mod.summary(user_id))


def location(resp):
    return resp.headers.get("Location", "")


c = Client(app)
admin_id = db(lambda: query_one("SELECT id FROM users WHERE username = 'admin'")["id"])

# ---------- Uygulama açılıyor, giriş zorunlu, boş sayfa ----------
assert c.get("/healthz").status_code == 200
anon = app.test_client()
r = anon.get("/hedefler/")
assert r.status_code == 302 and "/giris" in location(r), location(r)
page = c.text("/hedefler/")
assert "Henüz hedef yok" in page and "Yeni hedef" in page
assert summary(admin_id) == []

# ---------- Hedef oluşturma ----------
deadline5 = utils.add_months(T, 5).isoformat()
r = c.post("/hedefler/yeni", data={"name": "Tatil", "icon": "✈️", "target": "12.000",
                                   "deadline": deadline5, "note": "Yaz tatili"})
assert r.status_code == 302 and location(r).endswith("/hedefler/"), location(r)
tatil_row = goal_by_name("Tatil")
tatil = tatil_row["id"]
assert tatil_row["user_id"] == admin_id and tatil_row["target"] == 12000 and tatil_row["icon"] == "✈️"
assert tatil_row["deadline"] == deadline5 and tatil_row["note"] == "Yaz tatili" and tatil_row["done_at"] is None

# Türkçe sayı girişi
c.post("/hedefler/yeni", data={"name": "Telefon", "icon": "📱", "target": "45.999,90"})
telefon = goal_by_name("Telefon")["id"]
assert goal(telefon)["target"] == 45999.9 and goal(telefon)["deadline"] is None
c.post("/hedefler/yeni", data={"name": "Kitap", "icon": "<b>", "target": "1500,5"})  # geçersiz simge -> varsayılan
kitap = goal_by_name("Kitap")["id"]
assert goal(kitap)["target"] == 1500.5 and goal(kitap)["icon"] == goals_mod.DEFAULT_ICON

# Geçersiz hedefler reddedilir
before = count("goals")
for bad in [{"name": "  ", "target": "1000"},            # boş ad
            {"name": "Sıfır", "target": "0"},             # hedef <= 0
            {"name": "Eksi", "target": "-500"},
            {"name": "Boş", "target": ""},
            {"name": "Yazı", "target": "abc"},
            {"name": "NaN", "target": "nan"},
            {"name": "Sonsuz", "target": "inf"},
            {"name": "Tarih", "target": "1000", "deadline": "2026-13-45"}]:
    r = c.post("/hedefler/yeni", data=bad)
    assert r.status_code == 302, bad
assert count("goals") == before, "geçersiz hedef eklenmemeli"
r = c.post("/hedefler/yeni", data={"name": "Sıfır", "target": "0"}, follow_redirects=True)
assert "sıfırdan büyük" in r.get_data(as_text=True)

# XSS: ad kaçışlanır
c.post("/hedefler/yeni", data={"name": "<script>x</script>", "target": "10"})
xss = goal_by_name("<script>x</script>")["id"]
page = c.text("/hedefler/")
assert "<script>x</script>" not in page and "&lt;script&gt;" in page
c.post(f"/hedefler/{xss}/sil")
assert goal(xss) is None

# Sayfalar açılıyor
page = c.text("/hedefler/")
assert "Tatil" in page and "Telefon" in page and "Para ekle / çek" in page
page = c.text(f"/hedefler/{tatil}")
assert "Tatil" in page and "Henüz hareket yok" in page and "Düzenle" in page
assert c.get("/hedefler/999999").status_code == 404

# ---------- Para ekle / çek ----------
r = c.post(f"/hedefler/{tatil}/kayit", data={"amount": "2.000", "note": "İlk birikim"})
assert r.status_code == 302 and location(r).endswith("/hedefler/"), location(r)
assert saved(tatil) == 2000
e = entries(tatil)[-1]
assert e["amount"] == 2000 and e["date"] == T.isoformat() and e["note"] == "İlk birikim"

# Hedef 12.000, biriken 2.000, hedef tarih 5 ay sonra -> ayda 2.000 ₺
text = plain(c.text("/hedefler/"))
assert "ayda 2.000 ₺ biriktirmelisin" in text, text
assert "5 ay kaldı" in text and "Kalan 10.000 ₺" in text
assert goals_mod.months_left(deadline5, T) == 5
tatil_sum = [s for s in summary(admin_id) if s["goal"]["id"] == tatil][0]
assert tatil_sum["monthly_needed"] == 2000.0 and tatil_sum["remaining"] == 10000.0 and tatil_sum["saved"] == 2000.0

# Çekme: eksi kayıt
r = c.post(f"/hedefler/{tatil}/kayit", data={"amount": "500", "kind": "withdraw", "note": "Vize"})
assert r.status_code == 302 and saved(tatil) == 1500
assert entries(tatil)[-1]["amount"] == -500

# Birikimden fazla çekme reddedilir
n_before = len(entries(tatil))
r = c.post(f"/hedefler/{tatil}/kayit", data={"amount": "1.500,01", "kind": "withdraw"}, follow_redirects=True)
assert "bundan fazlası çekilemez" in r.get_data(as_text=True)
assert saved(tatil) == 1500 and len(entries(tatil)) == n_before

# "Ekle" ile eksi tutar da çekme sayılır (ve aynı kurala tabi)
c.post(f"/hedefler/{tatil}/kayit", data={"amount": "-100"})
assert saved(tatil) == 1400 and entries(tatil)[-1]["amount"] == -100
c.post(f"/hedefler/{tatil}/kayit", data={"amount": "-5000"})
assert saved(tatil) == 1400

# Sıfır / geçersiz tutar ve ileri tarih reddedilir
n_before = len(entries(tatil))
for bad in [{"amount": "0"}, {"amount": "0,00"}, {"amount": ""}, {"amount": "abc"}, {"amount": "nan"},
            {"amount": "inf"}, {"amount": "0", "kind": "withdraw"},
            {"amount": "100", "date": (T + timedelta(days=1)).isoformat()},
            {"amount": "100", "date": "2026-02-30"}]:
    r = c.post(f"/hedefler/{tatil}/kayit", data=bad)
    assert r.status_code == 302, bad
assert len(entries(tatil)) == n_before and saved(tatil) == 1400

# Geçmiş tarihli kayıt, Türkçe ondalık, next=detay sayfası
r = c.post(f"/hedefler/{tatil}/kayit", data={"amount": "600,00", "date": iso(3), "next": f"/hedefler/{tatil}"})
assert location(r).endswith(f"/hedefler/{tatil}"), location(r)
assert saved(tatil) == 2000 and entries(tatil)[-1]["date"] == iso(3)
r = c.post(f"/hedefler/{tatil}/kayit", data={"amount": "1", "next": "dashboard"})
assert location(r) in ("/", "http://localhost/"), location(r)
c.post(f"/hedefler/{tatil}/kayit", data={"amount": "1", "kind": "withdraw"})
assert saved(tatil) == 2000

page = plain(c.text(f"/hedefler/{tatil}"))
assert "İlk birikim" in page and "Vize" in page and "Para çekildi" in page and "Hareketler" in page
assert "−500 ₺" in page and "+2.000 ₺" in page and "Bakiye 2.000 ₺" in page

# ---------- done_at otomatik ----------
c.post(f"/hedefler/{kitap}/kayit", data={"amount": "1.000"})
assert goal(kitap)["done_at"] is None
r = c.post(f"/hedefler/{kitap}/kayit", data={"amount": "500,50"}, follow_redirects=True)
assert "Tebrikler" in r.get_data(as_text=True)
assert goal(kitap)["done_at"] == T.isoformat() and saved(kitap) == 1500.5
assert kitap not in [s["goal"]["id"] for s in summary(admin_id)]
page = c.text("/hedefler/")
assert "Tamamlananlar" in page
completed_part = page.split("Tamamlananlar", 1)[1]
assert f"/hedefler/{kitap}" in completed_part and "Kitap" in completed_part
assert "Tamamlandı 🎉" in c.text(f"/hedefler/{kitap}")

# Tekrar altına inince temizlenir
c.post(f"/hedefler/{kitap}/kayit", data={"amount": "0,50", "kind": "withdraw"})
assert goal(kitap)["done_at"] is None and saved(kitap) == 1500
assert kitap in [s["goal"]["id"] for s in summary(admin_id)]
assert "Tamamlananlar" not in c.text("/hedefler/")

# Hedef tutarı düzenlenince de güncellenir
c.post(f"/hedefler/{kitap}/duzenle", data={"name": "Kitap", "icon": "🎓", "target": "1.500"})
assert goal(kitap)["target"] == 1500 and goal(kitap)["done_at"] == T.isoformat() and goal(kitap)["icon"] == "🎓"
c.post(f"/hedefler/{kitap}/duzenle", data={"name": "Kitap", "icon": "🎓", "target": "2.000"})
assert goal(kitap)["target"] == 2000 and goal(kitap)["done_at"] is None

# Kayıt silinince de güncellenir; birikimi eksiye düşürecek silme reddedilir
c.post("/hedefler/yeni", data={"name": "Deneme", "target": "100"})
deneme = goal_by_name("Deneme")["id"]
c.post(f"/hedefler/{deneme}/kayit", data={"amount": "100"})
assert goal(deneme)["done_at"] == T.isoformat()
c.post(f"/hedefler/{deneme}/kayit", data={"amount": "100", "kind": "withdraw"})  # tamamını çekmek serbest
assert saved(deneme) == 0 and goal(deneme)["done_at"] is None
plus_id, minus_id = [e["id"] for e in entries(deneme)]
r = c.post(f"/hedefler/{deneme}/kayit/{plus_id}/sil", follow_redirects=True)
assert "eksiye düşer" in r.get_data(as_text=True)
assert len(entries(deneme)) == 2 and saved(deneme) == 0
r = c.post(f"/hedefler/{deneme}/kayit/{minus_id}/sil")
assert r.status_code == 302 and location(r).endswith(f"/hedefler/{deneme}"), location(r)
assert saved(deneme) == 100 and goal(deneme)["done_at"] == T.isoformat()
c.post(f"/hedefler/{deneme}/kayit/{plus_id}/sil")
assert entries(deneme) == [] and goal(deneme)["done_at"] is None
# Başka hedefin kaydı bu hedefin adresinden silinemez
tatil_entry = entries(tatil)[0]["id"]
assert c.post(f"/hedefler/{deneme}/kayit/{tatil_entry}/sil").status_code == 404
assert c.post(f"/hedefler/{deneme}/kayit/999999/sil").status_code == 404
assert len(entries(tatil)) > 0 and saved(tatil) == 2000

# Veritabanına doğrudan eklenen kayıtla bozulan done_at sayfa açılınca düzelir
db(lambda: execute("INSERT INTO goal_entries (goal_id, amount, date) VALUES (?, ?, ?)", (deneme, 150, iso(1))))
c.text("/hedefler/")
assert goal(deneme)["done_at"] is not None
c.post(f"/hedefler/{deneme}/sil")
assert goal(deneme) is None and count("goal_entries", "goal_id = ?", (deneme,)) == 0

# ---------- Tahmini bitiş (son 3 ayın ortalaması) ----------
assert [goals_mod.loc_suffix(y) for y in (2026, 2027, 2028, 2029, 2030, 2033, 2040, 2050, 2060, 2070, 2100, 2000)] == \
    ["da", "de", "de", "da", "da", "te", "ta", "de", "ta", "te", "de", "de"]
c.post("/hedefler/yeni", data={"name": "Araba", "icon": "🚗", "target": "12.000"})
araba = goal_by_name("Araba")["id"]
for days_ago, amount in [(10, 1000), (40, 1000), (70, 1000), (200, 5000)]:  # 200 gün önceki pencere dışında
    db(lambda: execute("INSERT INTO goal_entries (goal_id, amount, date) VALUES (?, ?, ?)",
                       (araba, amount, iso(days_ago))))
# biriken 8.000, kalan 4.000, son 3 ayda ayda 1.000 -> 4 ay sonra
finish = utils.add_months(T, 4)
expected = f"Bu hızla ~{utils.MONTHS_TR[finish.month - 1]} {finish.year}'{goals_mod.loc_suffix(finish.year)} tamamlanır"
text = plain(c.text("/hedefler/"))
assert expected in text, (expected, text)
detail_text = plain(c.text(f"/hedefler/{araba}"))
assert expected in detail_text and "ayda 1.000 ₺" in detail_text and "Bakiye 8.000 ₺" in detail_text
# Son 3 ayda katkı yoksa
c.post("/hedefler/yeni", data={"name": "Eski", "target": "10.000"})
eski = goal_by_name("Eski")["id"]
db(lambda: execute("INSERT INTO goal_entries (goal_id, amount, date) VALUES (?, ?, ?)", (eski, 3000, iso(200))))
assert "Son 3 ayda katkı yok" in plain(c.text(f"/hedefler/{eski}"))
assert "Son 3 ayda katkı yok" in plain(c.text(f"/hedefler/{telefon}"))  # hiç kayıt yok
# Net çekiş de katkı sayılmaz
db(lambda: execute("INSERT INTO goal_entries (goal_id, amount, date) VALUES (?, ?, ?)", (eski, -1000, iso(5))))
assert "Son 3 ayda katkı yok" in plain(c.text(f"/hedefler/{eski}"))

# Hedef tarihi geçmiş ve ulaşılmamış -> rozet
c.post("/hedefler/yeni", data={"name": "Gecikmiş", "target": "5.000", "deadline": iso(10)})
gecikmis = goal_by_name("Gecikmiş")["id"]
assert "Süre doldu" in c.text("/hedefler/") and "Süre doldu" in c.text(f"/hedefler/{gecikmis}")
g_sum = [s for s in summary(admin_id) if s["goal"]["id"] == gecikmis][0]
assert g_sum["monthly_needed"] == 5000.0  # en az 1 ay

# ---------- summary() biçimi ve sırası ----------
c.post("/hedefler/yeni", data={"name": "Ev", "icon": "🏠", "target": "100.000",
                               "deadline": utils.add_months(T, 2).isoformat()})
ev = goal_by_name("Ev")["id"]
c.post(f"/hedefler/{kitap}/kayit", data={"amount": "500"})  # Kitap 2.000'e ulaştı -> özet dışı
assert goal(kitap)["done_at"] is not None
s = summary(admin_id)
# tarihliler yakın tarih önce (Gecikmiş, Ev, Tatil), sonra tarihsizler id sırasıyla
assert [x["goal"]["id"] for x in s] == [gecikmis, ev, tatil, telefon, araba, eski], [x["goal"]["name"] for x in s]
for x in s:
    assert set(x) == {"goal", "saved", "pct", "remaining", "monthly_needed"}, x.keys()
    assert isinstance(x["saved"], float) and isinstance(x["pct"], float) and isinstance(x["remaining"], float)
    assert x["monthly_needed"] is None or isinstance(x["monthly_needed"], float)
    assert x["goal"]["done_at"] is None
by_id = {x["goal"]["id"]: x for x in s}
assert by_id[tatil]["pct"] == 16.7 and by_id[tatil]["monthly_needed"] == 2000.0
assert by_id[telefon]["monthly_needed"] is None and by_id[telefon]["saved"] == 0.0 and by_id[telefon]["pct"] == 0.0
assert by_id[telefon]["remaining"] == 45999.9
assert by_id[araba]["saved"] == 8000.0 and by_id[araba]["remaining"] == 4000.0 and by_id[araba]["monthly_needed"] is None
assert by_id[ev]["monthly_needed"] == 50000.0

# ---------- Düzenleme ----------
r = c.post(f"/hedefler/{tatil}/duzenle", data={"name": "Yaz tatili", "icon": "🏖️", "target": "15.000",
                                              "deadline": "", "note": ""})
assert r.status_code == 302 and location(r).endswith(f"/hedefler/{tatil}")
t2 = goal(tatil)
assert (t2["name"], t2["icon"], t2["target"], t2["deadline"], t2["note"]) == ("Yaz tatili", "🏖️", 15000, None, "")
r = c.post(f"/hedefler/{tatil}/duzenle", data={"name": "Bozuk", "icon": "zzz", "target": "0"}, follow_redirects=True)
assert "sıfırdan büyük" in r.get_data(as_text=True)
assert goal(tatil)["name"] == "Yaz tatili" and goal(tatil)["target"] == 15000
c.post(f"/hedefler/{tatil}/duzenle", data={"name": "Yaz tatili", "icon": "zzz", "target": "15.000"})
assert goal(tatil)["icon"] == "🏖️"  # geçersiz simge mevcut simgeyi korur
assert 'value="15.000"' in c.text(f"/hedefler/{tatil}")  # düzenleme formunda Türkçe biçim

# ---------- Sahiplik: ikinci kullanıcı ----------
ayse_id = db(lambda: create_user("ayse", "ayse12345"))
c2 = Client(app, "ayse", "ayse12345")
page = c2.text("/hedefler/")
assert "Yaz tatili" not in page and "Henüz hedef yok" in page
assert summary(ayse_id) == []
before_saved, before_n, before_goal = saved(tatil), len(entries(tatil)), dict(goal(tatil))
assert c2.get(f"/hedefler/{tatil}").status_code == 404
assert c2.post(f"/hedefler/{tatil}/kayit", data={"amount": "100"}).status_code == 404
assert c2.post(f"/hedefler/{tatil}/kayit", data={"amount": "100", "kind": "withdraw"}).status_code == 404
assert c2.post(f"/hedefler/{tatil}/duzenle", data={"name": "Hack", "target": "1"}).status_code == 404
assert c2.post(f"/hedefler/{tatil}/kayit/{tatil_entry}/sil").status_code == 404
assert c2.post(f"/hedefler/{tatil}/sil").status_code == 404
assert saved(tatil) == before_saved and len(entries(tatil)) == before_n and dict(goal(tatil)) == before_goal

c2.post("/hedefler/yeni", data={"name": "Ayşe'nin hedefi", "target": "1.000"})
ayse_goal = goal_by_name("Ayşe'nin hedefi")["id"]
assert goal(ayse_goal)["user_id"] == ayse_id
c2.post(f"/hedefler/{ayse_goal}/kayit", data={"amount": "250"})
assert [x["goal"]["id"] for x in summary(ayse_id)] == [ayse_goal]
assert ayse_goal not in [x["goal"]["id"] for x in summary(admin_id)]
assert "Ayşe" not in c.text("/hedefler/")
assert c.get(f"/hedefler/{ayse_goal}").status_code == 404
assert c.post(f"/hedefler/{ayse_goal}/kayit", data={"amount": "1"}).status_code == 404
ayse_entry = entries(ayse_goal)[0]["id"]
# kendi hedefinin adresiyle başkasının kaydı silinemez
assert c.post(f"/hedefler/{tatil}/kayit/{ayse_entry}/sil").status_code == 404
assert saved(ayse_goal) == 250

# ---------- Hedef silme ----------
r = c.post(f"/hedefler/{tatil}/sil")
assert r.status_code == 302 and location(r).endswith("/hedefler/")
assert goal(tatil) is None and count("goal_entries", "goal_id = ?", (tatil,)) == 0
assert c.get(f"/hedefler/{tatil}").status_code == 404
assert c.text("/hedefler/")  # hâlâ açılıyor

print("OK")
