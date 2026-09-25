"""Araç, garanti ve ev envanteri modülleri için testler.

Çalıştırma (proje kökünden): .venv/Scripts/python tests/test_car_warranty_inventory.py
"""
import io
import os
import re
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import Client, make_app  # noqa: E402

app = make_app()

from PIL import Image  # noqa: E402

from pano.auth import create_user  # noqa: E402
from pano.db import query, query_one  # noqa: E402
from pano.modules.car import average_consumption  # noqa: E402
from pano.modules.inventory import fold  # noqa: E402
from pano.utils import today  # noqa: E402

T = today()


def iso(days):
    return (T + timedelta(days=days)).isoformat()


def db_all(sql, args=()):
    with app.app_context():
        return [dict(r) for r in query(sql, args)]


def db_one(sql, args=()):
    with app.app_context():
        row = query_one(sql, args)
        return dict(row) if row else None


def png_bytes(color=(200, 40, 40)):
    buf = io.BytesIO()
    Image.new("RGB", (80, 50), color).save(buf, "PNG")
    return buf.getvalue()


def listing(client, url):
    """Sayfadaki kayıt listesi kısmı (başlık, form örnek metinleri ve datalist hariç); liste yoksa ''.

    "İçermiyor" kontrolleri sayfa alt başlığı ya da "ör. Matkap" gibi örnek metinlerle karışmasın.
    """
    html = client.text(url)
    for marker in ('<ul class="rows">', '<div class="grid two">'):
        if marker in html:
            return html[html.index(marker):]
    return ""


def upload(client, entity, entity_id, next_url, name="fatura.png"):
    return client.post(
        "/dosya/yukle",
        data={"entity": entity, "entity_id": str(entity_id), "next": next_url,
              "file": (io.BytesIO(png_bytes()), name)},
        content_type="multipart/form-data",
    )


def att_paths(row):
    base = app.config["UPLOAD_DIR"]
    return [os.path.join(base, n) for n in (row["filename"], row["thumb"]) if n]


def created_id(resp, prefix):
    assert resp.status_code == 302, resp.status_code
    loc = resp.headers["Location"]
    assert prefix in loc, loc
    return int(loc.rstrip("/").split("/")[-1])


c = Client(app)
admin_id = db_one("SELECT id FROM users WHERE username = 'admin'")["id"]

# ---------- Rotalar kayıtlı ----------
endpoints = {r.endpoint for r in app.url_map.iter_rules()}
for ep in ["car.index", "car.create", "car.detail", "car.update", "car.delete", "car.add_log", "car.delete_log",
           "warranty.index", "warranty.create", "warranty.detail", "warranty.delete",
           "inventory.index", "inventory.create", "inventory.edit", "inventory.qty", "inventory.delete"]:
    assert ep in endpoints, ep

# ---------- Boş sayfalar ----------
for url in ["/arac/", "/garanti/", "/garanti/?f=expired", "/garanti/?f=all", "/garanti/?f=bozuk",
            "/envanter/", "/envanter/?q=x&loc=y&cat=z"]:
    c.text(url)
assert "Henüz araç yok" in c.text("/arac/")
assert "Henüz ürün yok" in c.text("/garanti/")
assert "Henüz eşya yok" in c.text("/envanter/")

# Giriş yapmamış kullanıcı yönlendirilir
anon = app.test_client()
for url in ["/arac/", "/garanti/", "/envanter/", "/arac/1", "/garanti/1", "/envanter/1"]:
    r = anon.get(url)
    assert r.status_code == 302 and "/giris" in r.headers["Location"], url

# ======================================================================
# ARAÇ
# ======================================================================
assert average_consumption([(1000, 40), (1500, 35), (2000, 30)]) == 6.5
assert average_consumption([(2000, 30), (1000, 40), (1500, 35)]) == 6.5  # sıra önemsiz
assert average_consumption([(1000, 40)]) is None
assert average_consumption([(1000, 40), (1000, 30)]) is None  # mesafe yok
assert average_consumption([(1000, 40), (None, 30), (1500, None)]) is None

# Ad zorunlu
r = c.post("/arac/yeni", data={"name": "  ", "plate": "x"})
assert r.status_code == 302 and db_one("SELECT COUNT(*) n FROM vehicles")["n"] == 0

r = c.post("/arac/yeni", data={
    "name": "Corolla", "plate": "34 abc 123", "inspection_date": iso(10), "insurance_date": iso(-3),
    "casco_date": iso(200), "service_date": "", "service_km": "120.000", "note": "Lastik 205/55 R16",
})
vid = created_id(r, "/arac/")
v = db_one("SELECT * FROM vehicles WHERE id = ?", (vid,))
assert v["user_id"] == admin_id and v["plate"] == "34 ABC 123" and v["service_km"] == 120000
assert v["inspection_date"] == iso(10) and v["service_date"] is None

html = c.text("/arac/")
assert "Corolla" in html and "34 ABC 123" in html
assert 'badge soon' in html and 'badge overdue' in html and 'badge later' in html
html = c.text(f"/arac/{vid}")
assert "Corolla" in html and "Lastik 205/55 R16" in html and "Henüz kayıt yok" in html
assert "Ruhsat" in html and 'name="entity" value="vehicle"' in html  # ekler bölümü

# Düzenle
r = c.post(f"/arac/{vid}/duzenle", data={
    "name": "Corolla 2", "plate": "34 abc 123", "inspection_date": iso(10), "insurance_date": iso(40),
    "casco_date": "", "service_date": iso(5), "service_km": "120000", "note": "",
})
assert r.status_code == 302
v = db_one("SELECT * FROM vehicles WHERE id = ?", (vid,))
assert v["name"] == "Corolla 2" and v["insurance_date"] == iso(40) and v["casco_date"] is None
assert v["service_date"] == iso(5) and v["service_km"] == 120000
r = c.post(f"/arac/{vid}/duzenle", data={"name": ""})
assert db_one("SELECT name FROM vehicles WHERE id = ?", (vid,))["name"] == "Corolla 2"

# Yakıt kayıtları (harcamaya eklemeden) -> tüketim 6,5 L/100km
for km, lt, amount, days in [("1.000", "40", "1600", -20), ("1500", "35,0", "1400", -10), ("2000", "30", "1200", -1)]:
    r = c.post(f"/arac/{vid}/kayit", data={"kind": "fuel", "date": iso(days), "km": km, "liters": lt,
                                           "amount": amount, "note": ""})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/arac/{vid}")
logs = db_all("SELECT * FROM vehicle_logs WHERE vehicle_id = ? ORDER BY km", (vid,))
assert [(l["km"], l["liters"]) for l in logs] == [(1000, 40.0), (1500, 35.0), (2000, 30.0)]
assert db_one("SELECT COUNT(*) n FROM expenses")["n"] == 0  # onay kutusu işaretsiz

html = c.text(f"/arac/{vid}")
assert "6,5 L/100km" in html, "ortalama tüketim"
assert "118.000 km" in html, "bakıma kalan km"
assert "2.000" in html  # son km

# "Harcamalara da ekle" -> expenses satırı
r = c.post(f"/arac/{vid}/kayit", data={"kind": "service", "date": iso(0), "km": "", "amount": "2.500,50",
                                       "liters": "99", "note": "Yağ değişimi", "add_expense": "1"})
assert r.status_code == 302
svc = db_one("SELECT * FROM vehicle_logs WHERE vehicle_id = ? AND kind = 'service'", (vid,))
assert svc["amount"] == 2500.5 and svc["liters"] is None and svc["km"] is None  # litre sadece yakıtta
exp = db_one("SELECT * FROM expenses WHERE user_id = ?", (admin_id,))
assert exp and exp["amount"] == 2500.5 and exp["category"] == "Ulaşım" and exp["date"] == iso(0)
assert exp["note"].startswith("Corolla 2 - Bakım"), exp["note"]

r = c.post(f"/arac/{vid}/kayit", data={"kind": "fuel", "date": iso(0), "km": "2400", "amount": "900",
                                       "liters": "", "add_expense": "on"})
fuel_exp = db_one("SELECT * FROM expenses WHERE category = 'Yakıt'")
assert fuel_exp and fuel_exp["amount"] == 900 and fuel_exp["note"] == "Corolla 2 - Yakıt"

# Tutarsız kayıt: onay kutusu olsa bile harcama eklenmez
n_exp = db_one("SELECT COUNT(*) n FROM expenses")["n"]
c.post(f"/arac/{vid}/kayit", data={"kind": "other", "date": iso(0), "km": "2450", "add_expense": "1"})
assert db_one("SELECT COUNT(*) n FROM expenses")["n"] == n_exp

# Boş kayıt reddedilir, geçersiz tür 'fuel' olur, negatif tutar reddedilir
n_logs = db_one("SELECT COUNT(*) n FROM vehicle_logs")["n"]
c.post(f"/arac/{vid}/kayit", data={"kind": "fuel", "date": iso(0)})
c.post(f"/arac/{vid}/kayit", data={"kind": "fuel", "date": iso(0), "amount": "-5"})
assert db_one("SELECT COUNT(*) n FROM vehicle_logs")["n"] == n_logs
c.post(f"/arac/{vid}/kayit", data={"kind": "hack", "date": "bozuk", "note": "not"})
bad = db_one("SELECT * FROM vehicle_logs ORDER BY id DESC LIMIT 1")
assert bad["kind"] == "fuel" and bad["date"] == iso(0) and bad["note"] == "not"

html = c.text(f"/arac/{vid}")
assert "Yağ değişimi" in html and "Bakım" in html and "Yakıt" in html and "Diğer" in html
assert "2.450" in html  # son km
# Bu yılın toplamı (tüm kayıtlar bu yıl içinde olabilir ya da olmayabilir; yıl sınırını hesapla)
year_total = sum(l["amount"] or 0 for l in db_all("SELECT * FROM vehicle_logs WHERE vehicle_id = ?", (vid,))
                 if l["date"][:4] == str(T.year))
from pano.utils import fmt_money  # noqa: E402
assert fmt_money(year_total) in html, fmt_money(year_total)
# En yeni kayıt en üstte
assert html.index("Yağ değişimi") < html.index("1.600")

# Kayıt sil
c.post(f"/arac/{vid}/kayit/{bad['id']}/sil")
assert db_one("SELECT 1 x FROM vehicle_logs WHERE id = ?", (bad["id"],)) is None
assert c.post(f"/arac/{vid}/kayit/99999/sil").status_code == 404

# Araç belgesi yükle, sonra aracı sil -> kayıtlar ve ekler de gider
r = c.post("/arac/yeni", data={"name": "Motosiklet"})
vid2 = created_id(r, "/arac/")
c.post(f"/arac/{vid2}/kayit", data={"kind": "fuel", "date": iso(0), "km": "500", "liters": "5"})
r = upload(c, "vehicle", vid2, f"/arac/{vid2}", "ruhsat.png")
assert r.status_code == 302
att = db_one("SELECT * FROM attachments WHERE entity = 'vehicle' AND entity_id = ?", (vid2,))
assert att and all(os.path.exists(p) for p in att_paths(att))
assert f"/dosya/{att['id']}/kucuk" in c.text("/arac/")  # listede küçük resim
assert "ruhsat.png" in c.text(f"/arac/{vid2}")
r = c.post(f"/arac/{vid2}/sil")
assert r.status_code == 302
assert db_one("SELECT 1 x FROM vehicles WHERE id = ?", (vid2,)) is None
assert db_one("SELECT COUNT(*) n FROM vehicle_logs WHERE vehicle_id = ?", (vid2,))["n"] == 0
assert db_one("SELECT 1 x FROM attachments WHERE id = ?", (att["id"],)) is None
assert not any(os.path.exists(p) for p in att_paths(att))
assert c.get(f"/arac/{vid2}").status_code == 404

# ======================================================================
# GARANTİ
# ======================================================================
r = c.post("/garanti/yeni", data={"product": ""})
assert db_one("SELECT COUNT(*) n FROM warranties")["n"] == 0

# Ay yardımcısı: alış + 24 ay
r = c.post("/garanti/yeni", data={
    "product": "Bulaşık makinesi", "brand": "Bosch SMS46", "store": "Teknosa", "serial_no": "SN-778",
    "purchase_date": "2026-01-31", "warranty_until": "", "warranty_months": "24", "price": "18.999,90",
    "note": "Servis 444 0 000",
})
wid = created_id(r, "/garanti/")  # detay sayfasına yönlendirir (fatura yüklemek için)
w = db_one("SELECT * FROM warranties WHERE id = ?", (wid,))
assert w["warranty_until"] == "2028-01-31" and w["price"] == 18999.9 and w["user_id"] == admin_id

# Ay sonu taşması: 31 Ocak + 1 ay = 28 Şubat
r = c.post("/garanti/yeni", data={"product": "Kulaklık", "purchase_date": "2027-01-31", "warranty_months": "1"})
w_short = db_one("SELECT * FROM warranties WHERE id = ?", (created_id(r, "/garanti/"),))
assert w_short["warranty_until"] == "2027-02-28"

# Bitiş verilmişse ay alanı dikkate alınmaz; alış tarihi yoksa hesaplanmaz
r = c.post("/garanti/yeni", data={"product": "Tost makinesi", "purchase_date": "2026-01-01",
                                  "warranty_until": iso(3), "warranty_months": "24"})
w_soon = db_one("SELECT * FROM warranties WHERE id = ?", (created_id(r, "/garanti/"),))
assert w_soon["warranty_until"] == iso(3)
r = c.post("/garanti/yeni", data={"product": "Süresiz Masa", "purchase_date": "", "warranty_months": "24"})
w_none = db_one("SELECT * FROM warranties WHERE id = ?", (created_id(r, "/garanti/"),))
assert w_none["warranty_until"] is None and w_none["purchase_date"] is None
r = c.post("/garanti/yeni", data={"product": "Eski Telefon", "purchase_date": "2018-01-01",
                                  "warranty_until": "2020-01-01", "price": ""})
w_old = db_one("SELECT * FROM warranties WHERE id = ?", (created_id(r, "/garanti/"),))
assert w_old["price"] is None

# Filtre sekmeleri
active = listing(c, "/garanti/")
assert all(p in active for p in ["Bulaşık makinesi", "Kulaklık", "Tost makinesi", "Süresiz Masa"])
assert "Eski Telefon" not in active
assert 'badge soon' in active  # 3 gün kalan
# bitiş tarihine göre sıralı (NULL en sonda)
assert (active.index("Tost makinesi") < active.index("Kulaklık") < active.index("Bulaşık makinesi")
        < active.index("Süresiz Masa"))
expired = listing(c, "/garanti/?f=expired")
assert "Eski Telefon" in expired and "Bulaşık makinesi" not in expired and "badge overdue" in expired
every = listing(c, "/garanti/?f=all")
assert all(p in every for p in ["Bulaşık makinesi", "Eski Telefon", "Süresiz Masa", "Kulaklık"])

# Arama (ürün / marka / mağaza / seri no, Türkçe harf duyarsız)
s = listing(c, "/garanti/?f=all&q=bosch")
assert "Bulaşık makinesi" in s and "Eski Telefon" not in s and "Kulaklık" not in s
assert "Bulaşık makinesi" in c.text("/garanti/?q=teknosa")
assert "Bulaşık makinesi" in c.text("/garanti/?q=sn-778")
assert "Bulaşık makinesi" in c.text("/garanti/?q=BULASIK")
s = listing(c, "/garanti/?f=all&q=SÜRESİZ")
assert "Süresiz Masa" in s and "Kulaklık" not in s
assert "Aramanla eşleşen ürün yok" in c.text("/garanti/?q=yokboyle")

# Detay / düzenle
html = c.text(f"/garanti/{wid}")
assert "Bulaşık makinesi" in html and "SN-778" in html and "24 ay" in html and "18.999,90" in html
assert 'name="entity" value="warranty"' in html
r = c.post(f"/garanti/{wid}", data={"product": "Bulaşık makinesi", "brand": "Bosch", "purchase_date": "2026-01-31",
                                    "warranty_until": "", "warranty_months": "36", "price": "19000"})
assert r.status_code == 302 and r.headers["Location"].endswith(f"/garanti/{wid}")
w = db_one("SELECT * FROM warranties WHERE id = ?", (wid,))
assert w["warranty_until"] == "2029-01-31" and w["price"] == 19000 and w["brand"] == "Bosch" and w["store"] == ""
c.post(f"/garanti/{wid}", data={"product": ""})
assert db_one("SELECT product FROM warranties WHERE id = ?", (wid,))["product"] == "Bulaşık makinesi"

# Tutar gidiş-dönüşü: 2500 formda "2.500" görünür, değiştirmeden kaydedince 2500 kalmalı
c.post(f"/garanti/{wid}", data={"product": "Bulaşık makinesi", "price": "2.500", "warranty_until": "2029-01-31"})
assert db_one("SELECT price FROM warranties WHERE id = ?", (wid,))["price"] == 2500
shown = re.search(r'name="price" value="([^"]*)"', c.text(f"/garanti/{wid}")).group(1)
assert shown == "2.500", shown
c.post(f"/garanti/{wid}", data={"product": "Bulaşık makinesi", "price": shown, "warranty_until": "2029-01-31"})
assert db_one("SELECT price FROM warranties WHERE id = ?", (wid,))["price"] == 2500

# Fatura fotoğrafı yükle -> listede küçük resim; garantiyi silince ek ve dosyalar da silinir
r = upload(c, "warranty", wid, f"/garanti/{wid}")
assert r.status_code == 302 and r.headers["Location"].endswith(f"/garanti/{wid}")
att = db_one("SELECT * FROM attachments WHERE entity = 'warranty' AND entity_id = ?", (wid,))
assert att and att["user_id"] == admin_id and att["mime"] == "image/jpeg"
paths = att_paths(att)
assert len(paths) == 2 and all(os.path.exists(p) for p in paths)
assert f"/dosya/{att['id']}/kucuk" in c.text("/garanti/")
assert "fatura.png" in c.text(f"/garanti/{wid}")
assert c.get(f"/dosya/{att['id']}/kucuk").status_code == 200
r = c.post(f"/garanti/{wid}/sil")
assert r.status_code == 302
assert db_one("SELECT 1 x FROM warranties WHERE id = ?", (wid,)) is None
assert db_one("SELECT COUNT(*) n FROM attachments WHERE entity = 'warranty' AND entity_id = ?", (wid,))["n"] == 0
assert not any(os.path.exists(p) for p in paths)
assert c.get(f"/garanti/{wid}").status_code == 404

# ======================================================================
# EV ENVANTERİ
# ======================================================================
c.post("/envanter/yeni", data={"name": "", "location": "x"})
assert db_one("SELECT COUNT(*) n FROM inventory")["n"] == 0

for name, loc, cat, qty, note in [
    ("Matkap", "Balkon dolabı", "Alet", "1", "Mavi çanta"),
    ("Şarj aleti", "Salon", "Elektronik", "2", ""),
    ("Tornavida seti", "Balkon dolabı", "Alet", "", "uçlar yanında"),
    ("Yedek anahtar", "", "", "-4", ""),
]:
    r = c.post("/envanter/yeni", data={"name": name, "location": loc, "category": cat, "quantity": qty,
                                       "note": note, "next": "/envanter/?loc=Salon"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/envanter/?loc=Salon")
items = {r["name"]: r for r in db_all("SELECT * FROM inventory WHERE user_id = ?", (admin_id,))}
assert items["Tornavida seti"]["quantity"] == 1  # varsayılan 1
assert items["Yedek anahtar"]["quantity"] == 0  # negatif olamaz
mid = items["Matkap"]["id"]

html = c.text("/envanter/")
assert all(n in html for n in items) and 'list="inv-locations"' in html
assert '<option value="Balkon dolabı">' in html and '<option value="Elektronik">' in html  # datalist
assert "Balkon dolabı (2)" in html  # yer filtresi seçeneği

s = listing(c, "/envanter/?q=matkap")
assert "Matkap" in s and "Şarj aleti" not in s and "Tornavida" not in s
s = listing(c, "/envanter/?q=SARJ")
assert "Şarj aleti" in s and "Matkap" not in s
s = listing(c, "/envanter/?q=balkon")  # yere göre arama
assert "Matkap" in s and "Tornavida seti" in s and "Şarj aleti" not in s
s = listing(c, "/envanter/?q=mavi")  # nota göre arama
assert "Matkap" in s and "Tornavida seti" not in s
s = listing(c, "/envanter/?q=elektronik")  # kategoriye göre arama
assert "Şarj aleti" in s and "Matkap" not in s
s = listing(c, "/envanter/?loc=Salon")
assert "Şarj aleti" in s and "Matkap" not in s
s = listing(c, "/envanter/?cat=Alet")
assert "Matkap" in s and "Tornavida seti" in s and "Şarj aleti" not in s and "Yedek anahtar" not in s
s = c.text("/envanter/?cat=Alet&loc=Salon")
assert "Bulunamadı" in s and listing(c, "/envanter/?cat=Alet&loc=Salon") == ""
s = listing(c, "/envanter/?cat=Alet&q=torna")
assert "Tornavida seti" in s and "Matkap" not in s

# Adet +/- (0'ın altına inmez), filtreli sayfaya geri döner
def qty_of(item_id):
    return db_one("SELECT quantity FROM inventory WHERE id = ?", (item_id,))["quantity"]


r = c.post(f"/envanter/{mid}/adet", data={"d": "1", "next": "/envanter/?q=matkap"})
assert r.status_code == 302 and r.headers["Location"].endswith("/envanter/?q=matkap")
assert qty_of(mid) == 2
for _ in range(4):
    c.post(f"/envanter/{mid}/adet", data={"d": "-1"})
assert qty_of(mid) == 0
c.post(f"/envanter/{mid}/adet", data={"d": "7"})  # geçersiz adım yok sayılır
assert qty_of(mid) == 0
c.post(f"/envanter/{mid}/adet", data={"d": "1"})
assert qty_of(mid) == 1
r = c.post(f"/envanter/{mid}/adet", data={"d": "1", "next": "//evil.example"})  # açık yönlendirme yok
assert "evil" not in r.headers["Location"]

# Düzenle
html = c.text(f"/envanter/{mid}")
assert "Matkap" in html and 'name="entity" value="inventory"' in html
r = c.post(f"/envanter/{mid}", data={"name": "Darbeli matkap", "location": "Garaj", "category": "Alet",
                                     "quantity": "3", "note": ""})
assert r.status_code == 302
it = db_one("SELECT * FROM inventory WHERE id = ?", (mid,))
assert it["name"] == "Darbeli matkap" and it["location"] == "Garaj" and it["quantity"] == 3
assert "Garaj" in c.text("/envanter/?loc=Garaj")

# Fotoğraf + sil
upload(c, "inventory", mid, f"/envanter/{mid}", "matkap.png")
att = db_one("SELECT * FROM attachments WHERE entity = 'inventory' AND entity_id = ?", (mid,))
assert att and f"/dosya/{att['id']}/kucuk" in c.text("/envanter/")
paths = att_paths(att)
c.post(f"/envanter/{mid}/sil")
assert db_one("SELECT 1 x FROM inventory WHERE id = ?", (mid,)) is None
assert db_one("SELECT 1 x FROM attachments WHERE id = ?", (att["id"],)) is None
assert not any(os.path.exists(p) for p in paths)

# ======================================================================
# SAHİPLİK İZOLASYONU
# ======================================================================
with app.app_context():
    other_id = create_user("ali", "alipass123")
c2 = Client(app, "ali", "alipass123")

w_id = w_soon["id"]
inv_id = items["Şarj aleti"]["id"]
log_id = db_one("SELECT id FROM vehicle_logs WHERE vehicle_id = ? ORDER BY id LIMIT 1", (vid,))["id"]
n_logs = db_one("SELECT COUNT(*) n FROM vehicle_logs")["n"]

# Diğer kullanıcının listeleri boş
assert listing(c2, "/arac/") == "" and "Henüz araç yok" in c2.text("/arac/")
assert listing(c2, "/garanti/?f=all") == "" and "Tost makinesi" not in c2.text("/garanti/?f=all")
html = c2.text("/envanter/")
assert listing(c2, "/envanter/") == "" and "Şarj aleti" not in html and '<option value="Balkon' not in html

for method, url, data in [
    ("get", f"/arac/{vid}", None),
    ("post", f"/arac/{vid}/duzenle", {"name": "Çalındı"}),
    ("post", f"/arac/{vid}/kayit", {"kind": "fuel", "date": iso(0), "km": "9999", "liters": "10", "amount": "5",
                                    "add_expense": "1"}),
    ("post", f"/arac/{vid}/kayit/{log_id}/sil", {}),
    ("post", f"/arac/{vid}/sil", {}),
    ("get", f"/garanti/{w_id}", None),
    ("post", f"/garanti/{w_id}", {"product": "Çalındı"}),
    ("post", f"/garanti/{w_id}/sil", {}),
    ("get", f"/envanter/{inv_id}", None),
    ("post", f"/envanter/{inv_id}", {"name": "Çalındı"}),
    ("post", f"/envanter/{inv_id}/adet", {"d": "1"}),
    ("post", f"/envanter/{inv_id}/sil", {}),
]:
    r = c2.get(url) if method == "get" else c2.post(url, data=data)
    assert r.status_code == 404, (method, url, r.status_code)

# Başkasının kaydına ek yüklenemez
assert upload(c2, "warranty", w_id, "/garanti/").status_code == 404

# Hiçbir şey değişmedi
assert db_one("SELECT name FROM vehicles WHERE id = ?", (vid,))["name"] == "Corolla 2"
assert db_one("SELECT COUNT(*) n FROM vehicle_logs")["n"] == n_logs
assert db_one("SELECT COUNT(*) n FROM expenses WHERE user_id = ?", (other_id,))["n"] == 0
assert db_one("SELECT product FROM warranties WHERE id = ?", (w_id,))["product"] == "Tost makinesi"
assert db_one("SELECT * FROM inventory WHERE id = ?", (inv_id,))["quantity"] == 2
assert db_one("SELECT name FROM inventory WHERE id = ?", (inv_id,))["name"] == "Şarj aleti"

# Kendi aracı üzerinden başkasının kaydını silmeye çalışmak da 404
r = c2.post("/arac/yeni", data={"name": "Ali'nin arabası"})
other_vid = created_id(r, "/arac/")
assert db_one("SELECT user_id FROM vehicles WHERE id = ?", (other_vid,))["user_id"] == other_id
assert c2.post(f"/arac/{other_vid}/kayit/{log_id}/sil").status_code == 404
assert db_one("SELECT 1 x FROM vehicle_logs WHERE id = ?", (log_id,)) is not None
# ...ve admin de Ali'nin aracına kayıt ekleyemez
assert c.post(f"/arac/{other_vid}/kayit", data={"kind": "fuel", "date": iso(0), "km": "10"}).status_code == 404
assert c.get(f"/arac/{other_vid}").status_code == 404
assert "Ali&#39;nin arabası" not in c.text("/arac/") and "Ali'nin arabası" not in c.text("/arac/")

# Yardımcı
assert fold("IŞIK Şarj İğne") == "isik sarj igne"

print("OK")
