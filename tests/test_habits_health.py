"""Alışkanlıklar + Sağlık modülleri testleri.

Çalıştırma: .venv/Scripts/python tests/test_habits_health.py
"""
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import Client, make_app  # noqa: E402

app = make_app()

from pano import utils  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query, query_one  # noqa: E402
from pano.modules import habits as habits_mod  # noqa: E402
from pano.modules import health as health_mod  # noqa: E402

T = utils.today()


def iso(days_ago):
    return (T - timedelta(days=days_ago)).isoformat()


def db(fn):
    with app.app_context():
        return fn()


def logs(habit_id):
    return db(lambda: {r["date"] for r in query("SELECT date FROM habit_logs WHERE habit_id = ?", (habit_id,))})


def count(table, where="1=1", args=()):
    return db(lambda: query_one(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", args)["n"])


def location(resp):
    return resp.headers.get("Location", "")


c = Client(app)
admin_id = db(lambda: query_one("SELECT id FROM users WHERE username = 'admin'")["id"])

# ---------- Uygulama açılıyor, boş sayfalar ----------
assert c.get("/healthz").status_code == 200
assert "Henüz aktif alışkanlık yok" in c.text("/aliskanliklar/")
for url in ["/saglik/", "/saglik/?tab=olcum", "/saglik/?tab=ilac", "/saglik/?tab=randevu", "/saglik/?tab=xyz"]:
    c.text(url)
assert "Henüz ölçüm yok" in c.text("/saglik/")

# ---------- Alışkanlık oluşturma ----------
r = c.post("/aliskanliklar/yeni", data={"name": "Su içmek", "icon": "💧"})
assert r.status_code == 302
r = c.post("/aliskanliklar/yeni", data={"name": "Kitap", "icon": "<script>"})  # geçersiz simge -> varsayılan
r = c.post("/aliskanliklar/yeni", data={"name": "  ", "icon": "💧"})  # boş ad reddedilir
rows = db(lambda: query("SELECT * FROM habits WHERE user_id = ? ORDER BY id", (admin_id,)))
assert [h["name"] for h in rows] == ["Su içmek", "Kitap"], [h["name"] for h in rows]
assert rows[0]["icon"] == "💧" and rows[1]["icon"] == habits_mod.DEFAULT_ICON
water, book = rows[0]["id"], rows[1]["id"]

html = c.text("/aliskanliklar/")
assert "Su içmek" in html and "habit-chip" in html and "Son 30 gün" in html

# ---------- İşaretle / kaldır ----------
r = c.post(f"/aliskanliklar/{water}/isaretle")
assert r.status_code == 302 and location(r).endswith("/aliskanliklar/")
assert logs(water) == {iso(0)}
assert "habit-chip on" in c.text("/aliskanliklar/")
r = c.post(f"/aliskanliklar/{water}/isaretle")
assert logs(water) == set()

# pano için next=dashboard
r = c.post(f"/aliskanliklar/{water}/isaretle", data={"next": "dashboard"})
assert location(r) in ("/", "http://localhost/"), location(r)
assert logs(water) == {iso(0)}
c.post(f"/aliskanliklar/{water}/isaretle")  # geri al

# geçmiş tarih (60 gün içinde) kabul
c.post(f"/aliskanliklar/{water}/isaretle", data={"date": iso(5)})
assert logs(water) == {iso(5)}
c.post(f"/aliskanliklar/{water}/isaretle", data={"date": iso(60)})
assert iso(60) in logs(water)
# ileri tarih, 60 günden eski ve geçersiz tarih reddedilir
c.post(f"/aliskanliklar/{water}/isaretle", data={"date": (T + timedelta(days=1)).isoformat()})
c.post(f"/aliskanliklar/{water}/isaretle", data={"date": iso(61)})
c.post(f"/aliskanliklar/{water}/isaretle", data={"date": "dün"})
assert logs(water) == {iso(5), iso(60)}, logs(water)
r = c.post(f"/aliskanliklar/{water}/isaretle", data={"date": (T + timedelta(days=3)).isoformat()},
           follow_redirects=True)
assert "İleri bir tarih işaretlenemez" in r.get_data(as_text=True)

# takvimden işaretleme ayı korur (next)
ay = f"{T.year}-{T.month:02d}"
r = c.post(f"/aliskanliklar/{water}/isaretle", data={"date": iso(0), "next": f"/aliskanliklar/{water}?ay={ay}"})
assert location(r).endswith(f"/aliskanliklar/{water}?ay={ay}")
db(lambda: execute("DELETE FROM habit_logs WHERE habit_id = ?", (water,)))

# ---------- Seri hesabı ----------
assert habits_mod.current_streak(set(), T) == 0
d = lambda n: T - timedelta(days=n)  # noqa: E731
assert habits_mod.current_streak({d(2), d(1), d(0)}, T) == 3
assert habits_mod.current_streak({d(1)}, T) == 1                       # sadece dün -> 1
assert habits_mod.current_streak({d(2)}, T) == 0                       # dün yoksa seri bitti
assert habits_mod.current_streak({d(0), d(1), d(3), d(4), d(5)}, T) == 2  # boşluklu
assert habits_mod.current_streak({d(1), d(2), d(4)}, T) == 2           # bugün henüz yok, dünden geriye
assert habits_mod.longest_streak({d(0), d(1), d(3), d(4), d(5)}) == 3
assert habits_mod.longest_streak(set()) == 0

# DB üzerinden: su = bugün-2, bugün-1, bugün -> 3 ; kitap = sadece dün -> 1
for n in (2, 1, 0):
    db(lambda n=n: execute("INSERT INTO habit_logs (habit_id, date) VALUES (?, ?)", (water, iso(n))))
db(lambda: execute("INSERT INTO habit_logs (habit_id, date) VALUES (?, ?)", (book, iso(1))))
r = c.post("/aliskanliklar/yeni", data={"name": "Yürüyüş", "icon": "🏃"})
walk = db(lambda: query_one("SELECT id FROM habits WHERE name = 'Yürüyüş'")["id"])
for n in (0, 1, 3, 4, 5, 6):  # bugün+dün, boşluk, 4 günlük eski seri
    db(lambda n=n: execute("INSERT INTO habit_logs (habit_id, date) VALUES (?, ?)", (walk, iso(n))))


def status():
    with app.test_request_context():
        return {s["habit"]["name"]: (s["done"], s["streak"]) for s in habits_mod.today_status(admin_id)}


st = status()
assert st == {"Su içmek": (True, 3), "Kitap": (False, 1), "Yürüyüş": (True, 2)}, st
with app.app_context():  # istek bağlamı olmadan da çalışır (Telegram/cron)
    assert len(habits_mod.today_status(admin_id)) == 3

html = c.text(f"/aliskanliklar/{walk}")
assert "En uzun seri" in html and "Toplam" in html
assert '<div class="value">4</div>' in html  # en uzun seri: 3,4,5,6 günleri
assert '<div class="value">6</div>' in html  # toplam gün

# ---------- Takvim ve ay gezinmesi ----------
assert f'{utils.MONTHS_TR[T.month - 1]} {T.year}' in html
assert html.count('name="date" value="') == T.day  # bu ay: sadece bugüne kadarki günler dokunulabilir
for wd in utils.WEEKDAYS_TR_SHORT:
    assert f'<div class="h">{wd}</div>' in html
prev = utils.add_months(T.replace(day=1), -1)
html = c.text(f"/aliskanliklar/{walk}?ay={prev.year}-{prev.month:02d}")
assert f'{utils.MONTHS_TR[prev.month - 1]} {prev.year}' in html and "Bu aya dön" in html
c.text(f"/aliskanliklar/{walk}?ay=2025-01")
c.text(f"/aliskanliklar/{walk}?ay=2000-01")
html = c.text(f"/aliskanliklar/{walk}?ay=bozuk")
assert f'{utils.MONTHS_TR[T.month - 1]} {T.year}' in html
nxt = utils.add_months(T.replace(day=1), 1)
html = c.text(f"/aliskanliklar/{walk}?ay={nxt.year}-{nxt.month:02d}")  # gelecek ay: salt okunur
assert 'name="date" value="' not in html

# ---------- Düzenle / arşivle / sil ----------
c.post(f"/aliskanliklar/{book}/duzenle", data={"name": "Kitap okumak", "icon": "📚"})
row = db(lambda: query_one("SELECT * FROM habits WHERE id = ?", (book,)))
assert row["name"] == "Kitap okumak" and row["icon"] == "📚"
c.post(f"/aliskanliklar/{book}/duzenle", data={"name": "", "icon": "📚"})
assert db(lambda: query_one("SELECT name FROM habits WHERE id = ?", (book,)))["name"] == "Kitap okumak"

c.post(f"/aliskanliklar/{book}/arsiv")
assert db(lambda: query_one("SELECT active FROM habits WHERE id = ?", (book,)))["active"] == 0
assert "Kitap okumak" not in status()
html = c.text("/aliskanliklar/")
assert "Arşiv" in html and "Arşivden çıkar" in html
c.text(f"/aliskanliklar/{book}")
c.post(f"/aliskanliklar/{book}/arsiv")
assert "Kitap okumak" in status()

c.post(f"/aliskanliklar/{walk}/sil")
assert count("habits", "id = ?", (walk,)) == 0 and count("habit_logs", "habit_id = ?", (walk,)) == 0

# ---------- Sağlık: ölçümler ----------
def metric_count(kind=None):
    if kind:
        return count("health_metrics", "user_id = ? AND kind = ?", (admin_id, kind))
    return count("health_metrics", "user_id = ?", (admin_id,))


now_s = utils.now_local().strftime("%Y-%m-%dT%H:%M")
c.post("/saglik/olcum/yeni", data={"kind": "weight", "value1": "72,5", "measured_at": iso(3) + "T08:00"})
c.post("/saglik/olcum/yeni", data={"kind": "weight", "value1": "73.4", "measured_at": now_s})
c.post("/saglik/olcum/yeni", data={"kind": "pulse", "value1": "68"})  # tarih boş -> şimdi
assert metric_count("weight") == 2 and metric_count("pulse") == 1
pulse_row = db(lambda: query_one("SELECT * FROM health_metrics WHERE kind = 'pulse'"))
assert len(pulse_row["measured_at"]) == 16 and pulse_row["measured_at"][10] == "T"
assert pulse_row["value2"] is None

# tansiyon: value2 zorunlu
r = c.post("/saglik/olcum/yeni", data={"kind": "bp", "value1": "120"}, follow_redirects=True)
assert "değeri de girin" in r.get_data(as_text=True)
assert metric_count("bp") == 0
c.post("/saglik/olcum/yeni", data={"kind": "bp", "value1": "80", "value2": "120"})  # ters sıra
assert metric_count("bp") == 0
c.post("/saglik/olcum/yeni", data={"kind": "bp", "value1": "120", "value2": "80", "note": "sabah"})
c.post("/saglik/olcum/yeni", data={"kind": "bp", "value1": "130", "value2": "85"})
assert metric_count("bp") == 2

# aralık dışı değerler
before = metric_count()
for data in [
    {"kind": "weight", "value1": "500"}, {"kind": "weight", "value1": "10"},
    {"kind": "pulse", "value1": "300"}, {"kind": "sugar", "value1": "5"}, {"kind": "sugar", "value1": "800"},
    {"kind": "bp", "value1": "300", "value2": "80"}, {"kind": "bp", "value1": "120", "value2": "30"},
    {"kind": "weight", "value1": "abc"}, {"kind": "weight", "value1": ""}, {"kind": "xx", "value1": "70"},
    {"kind": "weight", "value1": "70", "measured_at": (T + timedelta(days=2)).isoformat() + "T10:00"},
    {"kind": "weight", "value1": "70", "measured_at": "yarın"},
]:
    r = c.post("/saglik/olcum/yeni", data=data)
    assert r.status_code == 302 and "tab=olcum" in location(r), location(r)
assert metric_count() == before, "aralık dışı değer kaydedilmemeli"
r = c.post("/saglik/olcum/yeni", data={"kind": "weight", "value1": "500"}, follow_redirects=True)
assert "20–400 kg" in r.get_data(as_text=True)

html = c.text("/saglik/")
assert "Son ölçüm" in html and "Son 30 gün ortalama" in html
assert "<polyline" in html and "currentColor" in html
assert "130/85" in html and "+0,9 kg" in html
assert "+10/+5 mmHg" in html
c.text("/saglik/?tab=olcum&tur=bp")

sp = health_mod._sparkline([1, 2, 3])
assert sp and len(sp["lines"]) == 1 and len(sp["lines"][0].split()) == 3
assert health_mod._sparkline([5]) is None
assert health_mod._sparkline([5, 5])["lines"][0] == f"4.0,{health_mod.SPARK_H / 2:.1f} 296.0,{health_mod.SPARK_H / 2:.1f}"

mid = db(lambda: query_one("SELECT id FROM health_metrics WHERE kind = 'pulse'")["id"])
c.post(f"/saglik/olcum/{mid}/sil")
assert metric_count("pulse") == 0

# ---------- İlaçlar ----------
assert health_mod.normalize_times("8:00; 20.30, 08:00 13") == ("08:00, 13:00, 20:30", None)
assert health_mod.normalize_times("") == ("", None)
assert health_mod.normalize_times("25:00")[0] is None
assert health_mod.normalize_times("08:75")[0] is None
assert health_mod.normalize_times("sabah")[0] is None

c.post("/saglik/ilac/yeni", data={"name": "D vitamini", "dose": "1 damla", "times": "20.00, 8"})
med = db(lambda: query_one("SELECT * FROM medications WHERE user_id = ?", (admin_id,)))
assert med["times"] == "08:00, 20:00", med["times"]
r = c.post("/saglik/ilac/yeni", data={"name": "Hatalı", "times": "25:00"}, follow_redirects=True)
assert "Geçersiz saat" in r.get_data(as_text=True)
c.post("/saglik/ilac/yeni", data={"name": "", "times": "08:00"})
assert count("medications", "user_id = ?", (admin_id,)) == 1

html = c.text("/saglik/?tab=ilac")
assert "D vitamini" in html and "08:00" in html and "Bugünkü program" in html
c.text(f"/saglik/ilac/{med['id']}")
c.post(f"/saglik/ilac/{med['id']}", data={"name": "D3 vitamini", "dose": "2 damla", "times": "9:30"})
med = db(lambda: query_one("SELECT * FROM medications WHERE id = ?", (med["id"],)))
assert med["name"] == "D3 vitamini" and med["times"] == "09:30"
c.post(f"/saglik/ilac/{med['id']}", data={"name": "D3 vitamini", "times": "99:99"})  # reddedilir
assert db(lambda: query_one("SELECT times FROM medications WHERE id = ?", (med["id"],)))["times"] == "09:30"

c.post(f"/saglik/ilac/{med['id']}/durum")
assert db(lambda: query_one("SELECT active FROM medications WHERE id = ?", (med["id"],)))["active"] == 0
html = c.text("/saglik/?tab=ilac")
assert "Kullanmadığım ilaçlar" in html and "Tekrar başla" in html
c.post(f"/saglik/ilac/{med['id']}/durum")
assert db(lambda: query_one("SELECT active FROM medications WHERE id = ?", (med["id"],)))["active"] == 1

# ---------- Randevular ----------
future = (T + timedelta(days=2)).isoformat() + "T14:30"
past = iso(10) + "T09:00"
c.post("/saglik/randevu/yeni", data={"title": "Diş kontrolü", "place": "Klinik", "starts_at": future})
c.post("/saglik/randevu/yeni", data={"title": "Eski muayene", "starts_at": past})
c.post("/saglik/randevu/yeni", data={"title": "Tarihsiz", "starts_at": ""})
c.post("/saglik/randevu/yeni", data={"title": "", "starts_at": future})
assert count("appointments", "user_id = ?", (admin_id,)) == 2
appt = db(lambda: query_one("SELECT * FROM appointments WHERE title = 'Diş kontrolü'"))
assert appt["starts_at"] == future and appt["done"] == 0

html = c.text("/saglik/?tab=randevu")
up_part, _, past_part = html.partition("Geçmiş")
assert "Diş kontrolü" in up_part and "2 gün sonra" in up_part
assert "Eski muayene" in past_part and "Diş kontrolü" not in past_part

r = c.post(f"/saglik/randevu/{appt['id']}/tamam")
assert location(r).endswith("tab=randevu")
assert db(lambda: query_one("SELECT done FROM appointments WHERE id = ?", (appt["id"],)))["done"] == 1
html = c.text("/saglik/?tab=randevu")
assert "Geri al" in html and "Yaklaşan randevu yok" in html
c.post(f"/saglik/randevu/{appt['id']}/tamam")  # geri al
assert db(lambda: query_one("SELECT done FROM appointments WHERE id = ?", (appt["id"],)))["done"] == 0
r = c.post(f"/saglik/randevu/{appt['id']}/tamam", data={"next": "dashboard"})
assert location(r) in ("/", "http://localhost/")
c.post(f"/saglik/randevu/{appt['id']}/tamam")

c.text(f"/saglik/randevu/{appt['id']}")
c.post(f"/saglik/randevu/{appt['id']}", data={"title": "Diş", "place": "", "starts_at": future.replace("14:30", "16:00")})
appt = db(lambda: query_one("SELECT * FROM appointments WHERE id = ?", (appt["id"],)))
assert appt["title"] == "Diş" and appt["starts_at"].endswith("T16:00")

# ---------- Sahiplik: ikinci kullanıcı ----------
with app.app_context():
    create_user("ayse", "ayse12345")
c2 = Client(app, "ayse", "ayse12345")
before_logs = logs(water)
assert "Su içmek" not in c2.text("/aliskanliklar/")
assert c2.get(f"/aliskanliklar/{water}").status_code == 404
assert c2.post(f"/aliskanliklar/{water}/isaretle").status_code == 404
assert c2.post(f"/aliskanliklar/{water}/isaretle", data={"date": iso(7)}).status_code == 404
assert c2.post(f"/aliskanliklar/{water}/duzenle", data={"name": "hack", "icon": "💧"}).status_code == 404
assert c2.post(f"/aliskanliklar/{water}/arsiv").status_code == 404
assert c2.post(f"/aliskanliklar/{water}/sil").status_code == 404
assert logs(water) == before_logs
row = db(lambda: query_one("SELECT * FROM habits WHERE id = ?", (water,)))
assert row["name"] == "Su içmek" and row["active"] == 1
with app.test_request_context():
    ayse_id = query_one("SELECT id FROM users WHERE username = 'ayse'")["id"]
    assert habits_mod.today_status(ayse_id) == []

html = c2.text("/saglik/")
assert "Henüz ölçüm yok" in html
assert "D3 vitamini" not in c2.text("/saglik/?tab=ilac")
html = c2.text("/saglik/?tab=randevu")
assert "Yaklaşan randevu yok" in html and "Eski muayene" not in html and "Geçmiş" not in html
bp_id = db(lambda: query_one("SELECT id FROM health_metrics WHERE kind = 'bp'")["id"])
assert c2.post(f"/saglik/olcum/{bp_id}/sil").status_code == 404
assert c2.get(f"/saglik/ilac/{med['id']}").status_code == 404
assert c2.post(f"/saglik/ilac/{med['id']}", data={"name": "x"}).status_code == 404
assert c2.post(f"/saglik/ilac/{med['id']}/durum").status_code == 404
assert c2.post(f"/saglik/ilac/{med['id']}/sil").status_code == 404
assert c2.get(f"/saglik/randevu/{appt['id']}").status_code == 404
assert c2.post(f"/saglik/randevu/{appt['id']}/tamam").status_code == 404
assert c2.post(f"/saglik/randevu/{appt['id']}/sil").status_code == 404
assert metric_count("bp") == 2
assert count("medications", "id = ?", (med["id"],)) == 1
assert db(lambda: query_one("SELECT done FROM appointments WHERE id = ?", (appt["id"],)))["done"] == 0

# ikinci kullanıcı kendi verisini ekleyebiliyor, admin görmüyor
c2.post("/aliskanliklar/yeni", data={"name": "Meditasyon", "icon": "🧘"})
assert "Meditasyon" not in c.text("/aliskanliklar/")

# ---------- Silmeler ----------
c.post(f"/saglik/ilac/{med['id']}/sil")
assert count("medications", "id = ?", (med["id"],)) == 0
c.post(f"/saglik/randevu/{appt['id']}/sil")
assert count("appointments", "id = ?", (appt["id"],)) == 0

# giriş yapmadan erişim yok
anon = app.test_client()
for url in ["/aliskanliklar/", f"/aliskanliklar/{water}", "/saglik/", "/saglik/?tab=ilac"]:
    r = anon.get(url)
    assert r.status_code == 302 and "/giris" in location(r)

print("OK")
