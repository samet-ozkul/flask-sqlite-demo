"""Randevu sayfası: ayar doğrulaması (adres, haftalık saatler, kurallar, görüşme türleri, kapalı günler), boş saat üretimi
(haftalık aralıklar, kapalı günler, en az önceden, ufuk, adım, mevcut randevu + tampon, takvimdeki saatli kayıt, tüm gün
kaydı sayılmaz), herkese açık akış (girişsiz) ve güvenlik başlıkları, HTML kaçışı, aynı saate ikinci talep, bal tuzağı,
IP ve bekleyen talep sınırı, onaylı/onaysız akış, web'den ve Telegram butonundan onay/red (başka kullanıcı yapamaz),
takvim etkinliği ve hatırlatması, ziyaretçi bağlantısı (durum, .ics, iptal), kapalı sayfa 404 / sahibin önizlemesi,
kullanıcı yalıtımı.

Çalıştır: .venv/Scripts/python tests/test_booking.py
"""
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, csrf, make_app  # noqa: E402

app = make_app()
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"
os.environ["CRON_SECRET"] = "gizli"

import pano.telegram as tg  # noqa: E402
import pano.todo_reminders as todo  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query, query_one  # noqa: E402
from pano.modules import booking  # noqa: E402
from pano.utils import TZ  # noqa: E402

CALLS = []


def fake_call(method, params=None, files=None):
    CALLS.append((method, params or {}))
    return {"message_id": 1}


tg._call = fake_call
START = datetime(2026, 10, 5, 8, 0, tzinfo=TZ)   # Pazartesi 08:00
NOW = [START]
todo.now_local = lambda: NOW[0]

with app.app_context():
    AYSE_ID = create_user("ayse", "ayse12345")
    execute("UPDATE users SET display_name = 'Şule Çiçek', telegram_chat_id = '100' WHERE id = 1")
    execute("UPDATE users SET display_name = 'Ayşe Kaya', telegram_chat_id = '200' WHERE id = ?", (AYSE_ID,))
    # Sayfadan asla sızmaması gereken veriler
    execute("INSERT INTO notes (user_id, title, content) VALUES (1, 'GIZLI-NOT', 'gizli not içeriği')")
    execute("INSERT INTO app_state (key, value) VALUES ('telegram_webhook', 'https://localhost/telegram/webhook')")
with app.test_request_context():
    SECRET = tg.webhook_secret()
ADMIN = Client(app)
AYSE = Client(app, "ayse", "ayse12345")
RAW = app.test_client()

WEEK = {}
for _d in range(7):   # Pzt–Cum açık; hafta sonu kapalı ama saatleri formda yine gelir (yok sayılmalı)
    WEEK.update({f"open_{_d}": "1" if _d < 5 else "", f"s1_{_d}": "09:00", f"e1_{_d}": "12:00",
                 f"s2_{_d}": "13:00", f"e2_{_d}": "17:00"})
BASE = {"slug": "sule-cicek", "enabled": "1", "title": "Şule ile görüşme", "description": "Kısa tanışma",
        "min_notice_hours": "2", "horizon_days": "30", "slot_step": "30", "needs_approval": "1",
        "busy_from_calendar": "1", **WEEK}
CHECKS = ("enabled", "needs_approval", "busy_from_calendar", *[f"open_{d}" for d in range(7)])
TYPE = {"name": "Tanışma", "duration_min": "30", "location_kind": "online",
        "location_detail": "https://meet.example.com/abc", "buffer_min": "0"}
MON, TUE, WED, THU, FRI = (f"2026-10-0{d}" for d in range(5, 10))
FULL_DAY = ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "13:00", "13:30", "14:00", "14:30", "15:00", "15:30",
            "16:00", "16:30"]
IDS = {}
UPDATE_ID = [1000]


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def rows(sql, args=()):
    with app.app_context():
        return query(sql, args)


def run(sql, args=()):
    with app.app_context():
        return execute(sql, args)


def text(resp):
    return resp.get_data(as_text=True)


def save(client, **fields):
    data = {**BASE, **fields}
    for key in CHECKS:
        if not data.get(key):
            data.pop(key, None)   # işaretsiz kutu formda hiç gönderilmez
    return client.post("/randevu/ayarlar", data=data)


def add_type(client, **fields):
    return client.post("/randevu/tur/yeni", data={**TYPE, **fields})


def free(tid, uid=1):
    """{'YYYY-MM-DD': ['09:00', ...]} — ufuktaki her gün."""
    with app.test_request_context():
        page = booking.get_page(uid)
        btype = query_one("SELECT * FROM booking_types WHERE id = ?", (tid,))
        return {d.isoformat(): [t.strftime("%H:%M") for t in s]
                for d, s in booking.availability(page, btype, booking.now_local())}


def open_form(client, tid, t, env=None, slug="sule-cicek"):
    r = client.get(f"/r/{slug}/{tid}/talep?t={t}", environ_base=env or {})
    return r, csrf(r)


def send(client, tid, t, token, env=None, slug="sule-cicek", **fields):
    data = {"_csrf": token or "", "t": t, "name": "Ali Veli", "contact": "0532 123 45 67", "note": "", **fields}
    return client.post(f"/r/{slug}/{tid}/talep", data=data, environ_base=env or {})


def ask(tid, t, client=None, env=None, slug="sule-cicek", **fields):
    """Ziyaretçi: formu açar (CSRF çerezi alır), gönderir."""
    client = client or app.test_client()
    _r, token = open_form(client, tid, t, env, slug)
    return send(client, tid, t, token, env, slug, **fields)


def token_of(resp):
    assert resp.status_code == 303, (resp.status_code, text(resp)[:300])
    return re.search(r"/r/i/([\w-]+)\?yeni=1", resp.headers["Location"]).group(1)


def bk(token):
    return one("SELECT * FROM bookings WHERE manage_token = ?", (token,))


def clear():
    run("DELETE FROM bookings")
    run("DELETE FROM events")
    run("DELETE FROM appointments")
    run("DELETE FROM booking_blocks")
    CALLS.clear()
    NOW[0] = START


def sent(method="sendMessage"):
    return [p for m, p in CALLS if m == method]


def press(chat_id, data):
    """Telegram'da mesaj butonuna basılması (webhook)."""
    UPDATE_ID[0] += 1
    update = {"update_id": UPDATE_ID[0], "callback_query": {
        "id": f"cb{UPDATE_ID[0]}", "data": data, "from": {"id": int(chat_id)},
        "message": {"message_id": 7, "chat": {"id": int(chat_id)}}}}
    return RAW.post("/telegram/webhook", data=json.dumps(update), content_type="application/json",
                    headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})


def assert_public_headers(r, form=True):
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp and "base-uri 'none'" in csp
    assert "form-action 'self'" in csp and "script-src" not in csp
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Robots-Tag"] == "noindex, nofollow" and r.headers["Cache-Control"] == "no-store"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    page = text(r)
    assert '<meta name="robots" content="noindex, nofollow">' in page and "<script" not in page


# ---------- Ayarlar ----------
def test_settings_validation():
    page = ADMIN.text("/randevu/")
    assert 'value="sule-cicek"' in page and 'value="Şule Çiçek ile randevu"' in page   # addan öneri
    assert "Sayfan" not in page and 'action="/randevu/ayarlar"' in page   # sayfa yokken ayarlar sekmesi açılır
    for bad in ("ab", "a" * 31, "-abc", "abc-", "ab_c", "a b c"):
        r = save(ADMIN, slug=bad)
        assert r.status_code == 400 and "küçük harf (a-z)" in text(r), bad
    r = save(ADMIN, slug="Şule Çiçek!")
    assert r.status_code == 400 and "Öneri: “sule-cicek”" in text(r) and 'value="Şule Çiçek!"' in text(r)
    for reserved in ("admin", "randevu", "giris", "iptal"):
        r = save(ADMIN, slug=reserved)
        assert r.status_code == 400 and "ayrılmış" in text(r), reserved
    cases = [
        ({"s1_0": "12:00", "e1_0": "09:00"}, "Pazartesi: başlangıç saati bitişten önce olmalı"),
        ({"s1_0": "10:00", "e1_0": "10:00"}, "Pazartesi: başlangıç saati bitişten önce olmalı"),
        ({"s1_1": "", "e1_1": "", "s2_1": "", "e2_1": ""}, "Salı açık ama saat aralığı yok"),
        ({"s2_2": "11:00"}, "Çarşamba: iki saat aralığı çakışıyor"),
        ({"e1_3": ""}, "Perşembe: saat aralığının başlangıcını ve bitişini yaz"),
        ({"s1_4": "25:00"}, "Cuma: saat geçersiz"),
        ({"min_notice_hours": "-1"}, "“En az kaç saat önceden” 0 ile 336"),
        ({"min_notice_hours": "abc"}, "“En az kaç saat önceden”"),
        ({"horizon_days": "0"}, "“Kaç gün ileriye kadar” 1 ile 90"),
        ({"horizon_days": "91"}, "“Kaç gün ileriye kadar”"),
    ]
    for fields, message in cases:
        r = save(ADMIN, title="Yazdığım başlık", **fields)
        assert r.status_code == 400 and message in text(r), (fields, message)
        assert 'value="Yazdığım başlık"' in text(r)   # yazılanlar kaybolmaz
    r = save(ADMIN, s1_0="12:00", e1_0="09:00")
    assert 'name="s1_0" value="12:00"' in text(r)
    assert one("SELECT COUNT(*) AS n FROM booking_pages")["n"] == 0
    # Kayıt: büyük/Türkçe harf dönüştürülür; kapalı günün saatleri yok sayılır; ikinci aralık isteğe bağlı;
    # iki aralık sıralanır, uç uca değebilir
    r = save(ADMIN, slug="ŞULE-ÇİÇEK", enabled="", s2_0="", e2_0="", s1_2="13:00", e1_2="17:00", s2_2="09:00",
             e2_2="13:00", slot_step="45")
    assert r.status_code == 302
    p = one("SELECT * FROM booking_pages WHERE user_id = 1")
    assert p["slug"] == "sule-cicek" and p["enabled"] == 0 and p["needs_approval"] == 1 and p["busy_from_calendar"] == 1
    assert p["min_notice_hours"] == 2 and p["horizon_days"] == 30 and p["slot_step"] == 30   # bilinmeyen adım -> 30
    weekly = json.loads(p["weekly_hours"])
    assert sorted(weekly) == ["0", "1", "2", "3", "4"] and weekly["0"] == [["09:00", "12:00"]]
    assert weekly["2"] == [["09:00", "13:00"], ["13:00", "17:00"]]
    assert weekly["1"] == [["09:00", "12:00"], ["13:00", "17:00"]]
    # Başkasının adresi alınamaz; öneri boşta olanı verir
    r = save(AYSE, slug="sule-cicek")
    assert r.status_code == 400 and "başka biri tarafından alınmış" in text(r)
    assert 'value="ayse-kaya"' in AYSE.text("/randevu/")
    assert save(ADMIN).status_code == 302   # kendi adresini yeniden kaydedebilir
    p = one("SELECT * FROM booking_pages WHERE user_id = 1")
    assert p["enabled"] == 1 and json.loads(p["weekly_hours"])["2"] == [["09:00", "12:00"], ["13:00", "17:00"]]
    page = ADMIN.text("/randevu/?tab=ayarlar")
    assert 'name="open_0" value="1" checked' in page and 'name="open_5" value="1" checked' not in page
    assert 'name="e2_4" value="17:00"' in page and 'name="s1_5" value=""' in page   # kapalı günün saati boş gelir
    print("  settings OK")


def test_types_and_blocks():
    cases = [
        ({"name": ""}, "ad ver"),
        ({"duration_min": "20"}, "Süre 15, 30, 45, 60 ya da 90"),
        ({"buffer_min": "7"}, "Aradaki boşluk geçersiz"),
        ({"location_kind": "in_person", "location_detail": ""}, "Yüz yüze görüşme için adres yaz"),
        ({"location_kind": "phone", "location_detail": "abc"}, "aranacak numarayı yaz"),
        ({"location_kind": "online", "location_detail": "javascript:alert(1)"}, "Görüşme linki geçersiz"),
    ]
    for fields, message in cases:
        r = add_type(ADMIN, **fields)
        assert r.status_code == 400 and message in text(r), (fields, message)
    r = add_type(ADMIN, name="Yazdığım tür", duration_min="20")
    assert 'value="Yazdığım tür"' in text(r)
    assert one("SELECT COUNT(*) AS n FROM booking_types")["n"] == 0
    assert add_type(ADMIN).status_code == 302
    assert add_type(ADMIN, name="Yüz yüze", location_kind="in_person", location_detail="Mevlana Cad. 5, Konya",
                    buffer_min="15").status_code == 302
    assert add_type(ADMIN, name="Telefon", location_kind="phone", location_detail="0532 000 11 22").status_code == 302
    assert add_type(ADMIN, name="Geri arama", location_kind="callback", location_detail="yok sayılır").status_code == 302
    assert add_type(ADMIN, name="Uzun", duration_min="60", location_detail="meet.example.com/uzun").status_code == 302
    r = add_type(ADMIN, name="Altıncı")
    assert r.status_code == 400 and "En fazla 5 görüşme türü" in text(r)
    for name in ("Tanışma", "Yüz yüze", "Telefon", "Geri arama", "Uzun"):
        IDS[name] = one("SELECT id FROM booking_types WHERE user_id = 1 AND name = ?", (name,))["id"]
    assert one("SELECT location_detail FROM booking_types WHERE id = ?", (IDS["Geri arama"],))["location_detail"] == ""
    assert one("SELECT location_detail FROM booking_types WHERE id = ?",
               (IDS["Uzun"],))["location_detail"] == "https://meet.example.com/uzun"   # şemasız adrese https://
    page = ADMIN.text("/randevu/?tab=turler")
    assert "En fazla 5 görüşme türü eklenebilir" in page and "Mevlana Cad. 5, Konya" in page
    # Düzenleme: hatada yazılanlar formda kalır
    uid = IDS["Uzun"]
    r = ADMIN.post(f"/randevu/tur/{uid}", data={**TYPE, "name": "Uzun görüşme", "duration_min": "33"})
    assert r.status_code == 400 and 'value="Uzun görüşme"' in text(r) and "Süre 15" in text(r)
    assert ADMIN.post(f"/randevu/tur/{uid}", data={**TYPE, "name": "Uzun görüşme", "duration_min": "90"}).status_code == 302
    assert one("SELECT duration_min, name FROM booking_types WHERE id = ?", (uid,))["duration_min"] == 90
    # Başka kullanıcı düzenleyemez, gizleyemez, silemez
    assert AYSE.post(f"/randevu/tur/{uid}", data=TYPE).status_code == 404
    assert AYSE.post(f"/randevu/tur/{uid}/durum").status_code == 404
    assert AYSE.post(f"/randevu/tur/{uid}/sil").status_code == 404
    # Silme -> çöp kutusu
    r = ADMIN.post(f"/randevu/tur/{uid}/sil", follow_redirects=True)
    assert "çöp kutusuna taşındı" in text(r) and not one("SELECT 1 FROM booking_types WHERE id = ?", (uid,))
    assert one("SELECT label FROM trash WHERE module = 'booking'")["label"] == "📅 Uzun görüşme (90 dk)"
    # Kapalı günler
    for fields, message in (({"start_date": ""}, "Kapatılacak günü seç"),
                            ({"start_date": "2026-10-09", "end_date": "2026-10-08"}, "Bitiş tarihi başlangıçtan önce"),
                            ({"start_date": "2026-10-01", "end_date": "2026-10-04"}, "Geçmiş günler kapatılamaz"),
                            ({"start_date": "2026-10-10", "end_date": "2027-12-01"}, "En fazla bir yıllık")):
        r = ADMIN.post("/randevu/kapali/yeni", data=fields)
        assert r.status_code == 400 and message in text(r), message
    assert ADMIN.post("/randevu/kapali/yeni", data={"start_date": "2026-10-20", "note": "Bayram"}).status_code == 302
    k = one("SELECT * FROM booking_blocks WHERE user_id = 1")
    assert (k["start_date"], k["end_date"], k["note"]) == ("2026-10-20", "2026-10-20", "Bayram")   # tek gün
    assert "Bayram" in ADMIN.text("/randevu/?tab=ayarlar")
    assert AYSE.post(f"/randevu/kapali/{k['id']}/sil").status_code == 404
    assert ADMIN.post(f"/randevu/kapali/{k['id']}/sil").status_code == 302
    assert not one("SELECT 1 FROM booking_blocks")
    print("  types/blocks OK")


# ---------- Boş saatler ----------
def test_slots():
    clear()
    t = IDS["Tanışma"]
    s = free(t)
    assert len(s) == 30 and min(s) == MON and max(s) == "2026-11-03"   # bugün dahil 30 gün
    assert s[MON] == FULL_DAY[2:]                      # 08:00 + en az 2 saat -> 10:00'dan itibaren
    assert s[TUE] == FULL_DAY and s["2026-10-10"] == [] and s["2026-10-11"] == []   # hafta sonu kapalı
    # En az önceden ve ufuk
    save(ADMIN, min_notice_hours="0", horizon_days="3")
    s = free(t)
    assert sorted(s) == [MON, TUE, WED] and s[MON] == FULL_DAY
    save(ADMIN, min_notice_hours="25")
    s = free(t)
    assert s[MON] == [] and s[TUE] == FULL_DAY         # 25 saat sonrası: salı 09:00 dahil
    save(ADMIN, min_notice_hours="26")
    assert free(t)[TUE] == FULL_DAY[2:]
    # Saat adımı ve süre (bitiş aralığa sığmalı)
    save(ADMIN, slot_step="60")
    assert free(t)[TUE] == ["09:00", "10:00", "11:00", "13:00", "14:00", "15:00", "16:00"]
    save(ADMIN)
    run("UPDATE booking_types SET duration_min = 45 WHERE id = ?", (t,))
    assert free(t)[TUE][:6] == ["09:00", "09:30", "10:00", "10:30", "11:00", "13:00"] and free(t)[TUE][-1] == "16:00"
    run("UPDATE booking_types SET duration_min = 30 WHERE id = ?", (t,))
    # Kapalı günler (tek gün ve aralık)
    ADMIN.post("/randevu/kapali/yeni", data={"start_date": WED})
    ADMIN.post("/randevu/kapali/yeni", data={"start_date": THU, "end_date": FRI, "note": "İzin"})
    s = free(t)
    assert s[WED] == s[THU] == s[FRI] == [] and s[TUE] == FULL_DAY and s["2026-10-12"] == FULL_DAY
    run("DELETE FROM booking_blocks")
    # Mevcut randevu + tampon: iki randevu arasında ikisinin tamponundan büyüğü kadar boşluk
    run("INSERT INTO bookings (user_id, type_id, type_name, start_at, end_at, buffer_min, name, contact, status,"
        " manage_token) VALUES (1, ?, 'Tanışma', ?, ?, 0, 'X', '0532 123 45 67', 'pending', 'tok-a')",
        (t, f"{TUE} 10:00", f"{TUE} 10:30"))
    run("INSERT INTO bookings (user_id, type_id, type_name, start_at, end_at, buffer_min, name, contact, status,"
        " manage_token) VALUES (1, ?, 'Yüz yüze', ?, ?, 30, 'Y', '0532 123 45 67', 'confirmed', 'tok-b')",
        (t, f"{TUE} 14:00", f"{TUE} 14:30"))
    s = free(t)[TUE]
    assert "10:00" not in s and "09:30" in s and "10:30" in s               # tampon yok: uç uca olabilir
    assert "13:00" in s and "15:00" in s and not {"13:30", "14:00", "14:30"} & set(s)   # mevcut randevunun 30 dk'sı
    s = free(IDS["Yüz yüze"])[TUE]   # 15 dk tamponlu tür
    assert "09:00" in s and "11:00" in s and not {"09:30", "10:00", "10:30"} & set(s)
    run("UPDATE bookings SET status = 'rejected' WHERE manage_token = 'tok-a'")   # reddedilen saati tutmaz
    run("UPDATE bookings SET status = 'cancelled' WHERE manage_token = 'tok-b'")
    assert free(t)[TUE] == FULL_DAY
    # Takvimdeki saatli kayıtlar (1 saat dolu); tüm gün ve tamamlanan sayılmaz
    run("INSERT INTO events (user_id, title, date, time, shared) VALUES (1, 'Diş', ?, '10:00', 0)", (WED,))
    run("INSERT INTO events (user_id, title, date, time, shared) VALUES (1, 'Tüm gün', ?, NULL, 0)", (THU,))
    run("INSERT INTO events (user_id, title, date, time, shared) VALUES (?, 'Ailece', ?, '15:00', 1)", (AYSE_ID, WED))
    run("INSERT INTO events (user_id, title, date, time, shared) VALUES (?, 'Ayşe özel', ?, '13:00', 0)", (AYSE_ID, WED))
    run("INSERT INTO appointments (user_id, title, starts_at) VALUES (1, 'Kontrol', ?)", (f"{FRI}T15:00",))
    run("INSERT INTO appointments (user_id, title, starts_at, done) VALUES (1, 'Bitti', ?, 1)", (f"{FRI}T09:00",))
    s = free(t)
    assert "09:30" in s[WED] and "11:00" in s[WED] and not {"10:00", "10:30"} & set(s[WED])
    assert not {"15:00", "15:30"} & set(s[WED]) and "13:00" in s[WED]   # paylaşılan etkinlik takvimde; özel olan değil
    assert s[THU] == FULL_DAY                                            # tüm gün kaydı sayılmaz
    assert not {"15:00", "15:30"} & set(s[FRI]) and "09:00" in s[FRI]   # tamamlanan randevu sayılmaz
    save(ADMIN, busy_from_calendar="")
    s = free(t)
    assert s[WED] == FULL_DAY and s[FRI] == FULL_DAY
    save(ADMIN)
    clear()
    print("  slots OK")


# ---------- Herkese açık akış ----------
def test_public_flow():
    clear()
    t = IDS["Tanışma"]
    ADMIN.post(f"/randevu/tur/{IDS['Telefon']}/durum")   # gizli tür sayfada görünmez
    r = RAW.get("/r/sule-cicek")
    page = text(r)
    assert r.status_code == 200 and "Set-Cookie" not in r.headers   # formsuz sayfa: oturum çerezi yok
    assert_public_headers(r)
    for part in ("Şule ile görüşme", "Kısa tanışma", "Tanışma", "30 dk", "Online görüşme", "Yüz yüze", "Geri arama",
                 "Telefon · ben ararım", "Saatler Türkiye saatidir", f"/r/sule-cicek/{t}"):
        assert part in page, part
    for leak in ("meet.example.com", "Mevlana", "0532 000 11 22", "GIZLI-NOT", "admin", "_csrf", "topbar", "Düzenle",
                 f"/r/sule-cicek/{IDS['Telefon']}"):
        assert leak not in page, leak
    assert RAW.get("/r/SULE-CICEK").status_code == 200   # adreste büyük/küçük harf fark etmez
    # Gün ve saatler
    r = RAW.get(f"/r/sule-cicek/{t}")
    page = text(r)
    assert r.status_code == 200 and "Set-Cookie" not in r.headers
    assert_public_headers(r)
    assert "Bugün · 5 Ekim Pazartesi" in page and "Yarın · 6 Ekim Salı" in page and "11 Ekim Pazar" in page
    assert f"/r/sule-cicek/{t}/talep?t=2026-10-05T10:00" in page and "2026-10-05T09:30" not in page
    assert "12 Ekim" not in page and "bas=2026-10-12" in page and "Sonraki günler" in page and "Önceki günler" not in page
    assert "Boş saat yok" in page and "Saatler Türkiye saatidir" in page
    page = text(RAW.get(f"/r/sule-cicek/{t}?bas=2026-10-12"))
    assert "12 Ekim Pazartesi" in page and "bas=2026-10-05" in page and "Önceki günler" in page
    page = text(RAW.get(f"/r/sule-cicek/{t}?bas=2027-05-01"))   # ufuk dışı: son haftaya
    assert "3 Kasım Salı" in page and "Sonraki günler" not in page
    assert RAW.get(f"/r/sule-cicek/{t}/talep?t=bozuk").status_code == 302
    # Form: oturum çerezi + CSRF, bal tuzağı alanı
    visitor = app.test_client()
    r, token = open_form(visitor, t, "2026-10-06T10:00")
    page = text(r)
    assert r.status_code == 200 and token and "Set-Cookie" in r.headers
    assert_public_headers(r)
    assert "6 Ekim 2026 Salı · 10:00–10:30" in page and 'name="website"' in page and "📨 Randevu iste" in page
    # CSRF anahtarı olmadan (başka siteden) gönderilemez
    assert app.test_client().post(f"/r/sule-cicek/{t}/talep", data={"t": "2026-10-06T10:00", "name": "X",
                                                                     "contact": "0532 123 45 67"}).status_code == 400
    r = send(visitor, t, "2026-10-06T10:00", token, note="Proje hakkında")
    tok = token_of(r)
    b = bk(tok)
    assert (b["status"], b["start_at"], b["end_at"], b["name"], b["contact"]) == (
        "pending", "2026-10-06 10:00", "2026-10-06 10:30", "Ali Veli", "0532 123 45 67")
    assert b["type_name"] == "Tanışma" and b["location_detail"] == "https://meet.example.com/abc" and b["event_id"] is None
    assert len(tok) == 22 and len(b["ip_hash"]) == 32 and "127.0.0.1" not in b["ip_hash"]
    # Sahibine Telegram: kim, ne zaman, tür, iletişim, not + onay butonları
    msgs = sent()
    assert len(msgs) == 1 and msgs[0]["chat_id"] == "100"
    for part in ("Yeni randevu talebi", "<b>Ali Veli</b>", "6 Ekim 2026 Salı · 10:00–10:30", "Tanışma · Online",
                 "0532 123 45 67", "<i>Proje hakkında</i>", "/randevu/"):
        assert part in msgs[0]["text"], part
    buttons = json.loads(msgs[0]["reply_markup"])["inline_keyboard"][0]
    assert [b_["callback_data"] for b_ in buttons] == [f"bk:ok:{b['id']}", f"bk:no:{b['id']}"]
    # Ziyaretçi bağlantısı
    r = visitor.get(f"/r/i/{tok}?yeni=1")
    page = text(r)
    assert r.status_code == 200
    assert_public_headers(r)
    for part in ("Randevu talebin alındı", "Onay bekliyor", f"/r/i/{tok}", "Bu sayfanın adresini kaydet",
                 "Görüşme linki randevu onaylanınca burada görünür", "Randevuyu iptal et", "Proje hakkında"):
        assert part in page, part
    assert "meet.example.com" not in page and "Takvimime ekle" not in page
    assert visitor.get(f"/r/i/{tok}/randevu.ics").status_code == 404   # onaylanmadan takvim dosyası yok
    # Dolan saat artık sunulmaz; aynı saate ikinci talep reddedilir
    assert "10:00" not in free(t)[TUE]
    a, b2 = app.test_client(), app.test_client()
    _r, tok_a = open_form(a, t, "2026-10-06T10:30")
    _r, tok_b = open_form(b2, t, "2026-10-06T10:30")
    token_of(send(a, t, "2026-10-06T10:30", tok_a))
    r = send(b2, t, "2026-10-06T10:30", tok_b, name="İkinci")
    assert r.status_code == 409 and "Bu saat az önce doldu" in text(r) and "Boş saatleri gör" in text(r)
    assert 'name="name"' not in text(r)
    r = b2.get(f"/r/sule-cicek/{t}/talep?t=2026-10-06T10:30")
    assert r.status_code == 409 and "artık boş değil" in text(r)
    # Sunulmayan saat elle yazılsa da sunucu reddeder (en az önceden süresi içinde, hafta sonu, aralık dışı)
    c = app.test_client()
    _r, tok_c = open_form(c, t, "2026-10-06T11:00")
    for bad in ("2026-10-05T09:00", "2026-10-10T10:00", "2026-10-06T12:00", "2026-10-06T09:10", "2027-01-05T10:00"):
        r = send(c, t, bad, tok_c)
        assert r.status_code == 409 and "Bu saat az önce doldu" in text(r), bad
    assert one("SELECT COUNT(*) AS n FROM bookings")["n"] == 2
    # Veritabanı da aynı başlangıca ikinci aktif randevuyu kabul etmez (reddedilen/iptal olan sayılmaz)
    insert = ("INSERT INTO bookings (user_id, type_name, start_at, end_at, name, contact, status, manage_token)"
              " VALUES (1, 'X', '2026-10-06 10:00', '2026-10-06 10:30', 'X', 'x@ornek.com', ?, ?)")
    try:
        run(insert, ("confirmed", "dup-1"))
        raise AssertionError("aynı saate ikinci aktif randevu eklendi")
    except sqlite3.IntegrityError:
        pass
    run(insert, ("rejected", "dup-2"))
    run(insert, ("cancelled", "dup-3"))
    ADMIN.post(f"/randevu/tur/{IDS['Telefon']}/durum")
    print("  public flow OK")


def test_escape_and_validation():
    clear()
    t = IDS["Tanışma"]
    r = ask(t, "2026-10-07T10:00", name="<script>alert(1)</script>", contact="ali@example.com",
            note="<b>kalın</b> & not\r\nikinci satır")
    tok = token_of(r)
    page = text(RAW.get(f"/r/i/{tok}"))
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "<script" not in page
    assert "&lt;b&gt;kalın&lt;/b&gt; &amp; not\nikinci satır" in page and "<b>kalın" not in page
    admin_page = ADMIN.text("/randevu/")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in admin_page and "<script>alert(1)" not in admin_page
    assert 'href="mailto:ali@example.com"' in admin_page and "&lt;b&gt;kalın" in admin_page
    msg = sent()[-1]["text"]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in msg and "<script>" not in msg and "&lt;b&gt;kalın&lt;/b&gt; &amp; not" in msg
    assert "✉️ ali@example.com" in msg
    # Doğrulama; yazılanlar kaybolmaz
    for fields, message in (({"name": "  "}, "Adını yaz"), ({"contact": ""}, "Telefon numaranı ya da e-posta"),
                            ({"contact": "abc"}, "Telefon numarası geçersiz"), ({"contact": "12 34"}, "Telefon numarası geçersiz"),
                            ({"contact": "a@b"}, "E-posta adresi geçersiz")):
        r = ask(t, "2026-10-07T11:00", note="Notum", **fields)
        assert r.status_code == 400 and message in text(r), message
        assert "Notum</textarea>" in text(r)
    r = ask(IDS["Geri arama"], "2026-10-07T11:00", contact="ali@example.com")
    assert r.status_code == 400 and "seni arayacağız; telefon numaranı yaz" in text(r)
    tok = token_of(ask(IDS["Geri arama"], "2026-10-07T11:00", name="A" * 200, contact="0532   123 45 67",
                       note="n" * 900))
    b = bk(tok)
    assert len(b["name"]) == 80 and len(b["note"]) == 500 and b["contact"] == "0532 123 45 67"   # uzunluk sınırı
    assert one("SELECT COUNT(*) AS n FROM bookings")["n"] == 2
    print("  escape/validation OK")


def test_abuse_limits():
    clear()
    t = IDS["Tanışma"]
    # Bal tuzağı: insanlar görmez, robot doldurur -> kaydedilmez, bildirim yok
    r = ask(t, "2026-10-06T09:00", website="http://spam.example")
    assert r.status_code == 400 and "Talep gönderilemedi" in text(r)
    assert one("SELECT COUNT(*) AS n FROM bookings")["n"] == 0 and not sent()
    # IP başına saatte 5 talep
    env = {"REMOTE_ADDR": "10.1.2.3"}
    for hm in ("09:00", "09:30", "10:00", "10:30", "11:00"):
        token_of(ask(t, f"2026-10-06T{hm}", env=env))
    r = ask(t, "2026-10-06T11:30", env=env)
    assert r.status_code == 429 and "çok fazla talep" in text(r)
    token_of(ask(t, "2026-10-06T11:30", env={"REMOTE_ADDR": "10.9.9.9"}))   # başka IP etkilenmez
    hashes = {r_["ip_hash"] for r_ in rows("SELECT ip_hash FROM bookings")}
    assert len(hashes) == 2 and not any("10.1.2.3" in h or "10.9.9.9" in h for h in hashes)   # IP düz saklanmaz
    run("UPDATE bookings SET created_at = datetime('now', '-61 minutes')")   # bir saat geçince yeniden
    token_of(ask(t, "2026-10-06T13:00", env=env))
    # Sayfa başına aynı anda en fazla 20 bekleyen talep (saati geçmişler sayılmaz)
    run("DELETE FROM bookings")
    for i in range(19):
        run("INSERT INTO bookings (user_id, type_name, start_at, end_at, name, contact, manage_token) VALUES"
            " (1, 'X', ?, ?, 'X', '0532 123 45 67', ?)", (f"2026-10-2{i // 9} 0{i % 9}:00", "2026-10-29 23:00", f"p{i}"))
    run("INSERT INTO bookings (user_id, type_name, start_at, end_at, name, contact, manage_token) VALUES"
        " (1, 'X', '2026-10-01 10:00', '2026-10-01 10:30', 'X', '0532 123 45 67', 'eski')")
    token_of(ask(t, "2026-10-07T09:00"))   # 20. bekleyen
    r = ask(t, "2026-10-07T09:30")
    assert r.status_code == 429 and "onay bekleyen çok fazla talep" in text(r)
    clear()
    print("  abuse limits OK")


# ---------- Onay ----------
def test_approval_web():
    clear()
    t = IDS["Tanışma"]
    tok = token_of(ask(t, "2026-10-06T10:00"))
    b = bk(tok)
    page = ADMIN.text("/randevu/")
    assert "⏳ Bekleyen" in page and "Ali Veli" in page and "✅ Onayla" in page and "6 Ekim 2026 Salı · 10:00–10:30" in page
    assert 'href="tel:+905321234567"' in page and '<span class="pill-count">1</span>' in page
    # Başka kullanıcı onaylayamaz, reddedemez, iptal edemez
    for action in ("onayla", "reddet", "iptal"):
        assert AYSE.post(f"/randevu/{b['id']}/{action}").status_code == 404
    assert bk(tok)["status"] == "pending" and "Ali Veli" not in AYSE.text("/randevu/")
    r = ADMIN.post(f"/randevu/{b['id']}/onayla", follow_redirects=True)
    assert "Randevu onaylandı ve takvimine eklendi" in text(r)
    b = bk(tok)
    assert b["status"] == "confirmed" and b["event_id"]
    e = one("SELECT * FROM events WHERE id = ?", (b["event_id"],))
    assert (e["user_id"], e["shared"], e["date"], e["time"], e["remind_before"]) == (1, 0, TUE, "10:00", 60)
    assert e["title"] == "Randevu: Ali Veli" and e["place"] == "Online: https://meet.example.com/abc"
    assert "Tanışma · 10:00–10:30" in e["note"] and "İletişim: 0532 123 45 67" in e["note"]
    assert "Randevu: Ali Veli" in ADMIN.text("/etkinlikler/") and "Randevu: Ali Veli" not in AYSE.text("/etkinlikler/")
    # Kendi etkinliği tekrar dolu sayılmaz (1 saat değil, randevu süresi kadar)
    s = free(t)[TUE]
    assert "10:00" not in s and "10:30" in s
    r = ADMIN.post(f"/randevu/{b['id']}/onayla", follow_redirects=True)
    assert "Bu randevu zaten onaylanmış" in text(r)
    assert one("SELECT COUNT(*) AS n FROM events")["n"] == 1
    page = ADMIN.text("/randevu/")
    assert "📅 Yaklaşan" in page and f"/etkinlikler/{b['event_id']}" in page and "İptal et" in page
    # Ziyaretçi onayı görür; link ve takvim dosyası artık var
    page = text(RAW.get(f"/r/i/{tok}"))
    assert "Onaylandı" in page and "Takvimime ekle" in page and 'href="https://meet.example.com/abc"' in page
    r = RAW.get(f"/r/i/{tok}/randevu.ics")
    body = text(r)
    assert r.status_code == 200 and r.headers["Content-Type"].startswith("text/calendar")
    assert "attachment" in r.headers["Content-Disposition"] and r.headers["Cache-Control"] == "no-store"
    for part in ("BEGIN:VEVENT", "DTSTART:20261006T070000Z", "DTEND:20261006T073000Z",
                 "SUMMARY:Tanışma · Şule ile görüşme", "LOCATION:https://meet.example.com/abc", "TRIGGER:-PT1H"):
        assert part in body, part
    assert "\r\n" in body and body.endswith("END:VCALENDAR\r\n")
    # Hatırlatma mevcut etkinlik sisteminden gelir (cron /hatirlatma, 1 saat önce)
    CALLS.clear()
    NOW[0] = datetime(2026, 10, 6, 9, 5, tzinfo=TZ)
    assert RAW.get("/cron/gizli/hatirlatma").json["events_sent"] == 1
    msg = sent()[-1]
    assert msg["chat_id"] == "100" and "Yaklaşıyor" in msg["text"] and "Randevu: Ali Veli" in msg["text"]
    NOW[0] = START
    # Red: etkinlik yok, saat yeniden boş
    tok2 = token_of(ask(t, "2026-10-06T11:00"))
    b2 = bk(tok2)
    r = ADMIN.post(f"/randevu/{b2['id']}/reddet", follow_redirects=True)
    assert "Randevu talebi reddedildi" in text(r)
    assert bk(tok2)["status"] == "rejected" and bk(tok2)["event_id"] is None and "11:00" in free(t)[TUE]
    page = text(RAW.get(f"/r/i/{tok2}"))
    assert "Reddedildi" in page and "Yeni bir saat seç" in page and "Randevuyu iptal et" not in page
    assert RAW.get(f"/r/i/{tok2}/randevu.ics").status_code == 404
    assert "🗂️ Geçmiş" in ADMIN.text("/randevu/")
    # Sahibi iptal eder: etkinlik de silinir
    r = ADMIN.post(f"/randevu/{b['id']}/iptal", follow_redirects=True)
    assert "Randevu iptal edildi; takviminden de silindi" in text(r)
    b = bk(tok)
    assert b["status"] == "cancelled" and b["cancelled_by"] == "owner" and b["event_id"] is None
    assert one("SELECT COUNT(*) AS n FROM events")["n"] == 0 and "10:00" in free(t)[TUE]
    page = text(RAW.get(f"/r/i/{tok}"))
    assert "İptal edildi" in page and "Randevu iptal edildi." in page and "Takvimime ekle" not in page
    # Saati geçmiş talep onaylanamaz
    run("INSERT INTO bookings (user_id, type_name, start_at, end_at, name, contact, manage_token) VALUES"
        " (1, 'X', '2026-10-05 07:00', '2026-10-05 07:30', 'Geç', '0532 123 45 67', 'gec')")
    late = bk("gec")
    r = ADMIN.post(f"/randevu/{late['id']}/onayla", follow_redirects=True)
    assert "Randevu saati geçmiş" in text(r) and bk("gec")["status"] == "pending"
    assert "Yanıtlanmadı" in ADMIN.text("/randevu/")
    clear()
    print("  approval (web) OK")


def test_no_approval():
    clear()
    save(ADMIN, needs_approval="")
    t = IDS["Yüz yüze"]
    r, _token = open_form(app.test_client(), t, "2026-10-07T10:00")
    assert "✅ Randevuyu al" in text(r) and "Randevun hemen kesinleşir" in text(r)
    tok = token_of(ask(t, "2026-10-07T10:00"))
    b = bk(tok)
    assert b["status"] == "confirmed" and b["event_id"]
    e = one("SELECT * FROM events WHERE id = ?", (b["event_id"],))
    assert e["place"] == "Yüz yüze: Mevlana Cad. 5, Konya" and e["date"] == WED and e["time"] == "10:00"
    msg = sent()[-1]
    assert "Yeni randevu</b> · takvimine eklendi" in msg["text"] and "reply_markup" not in msg
    page = text(RAW.get(f"/r/i/{tok}?yeni=1"))
    assert "Randevun alındı" in page and "Onaylandı" in page and "Mevlana Cad. 5, Konya" in page and "Takvimime ekle" in page
    save(ADMIN)
    clear()
    print("  no approval OK")


def test_telegram_buttons():
    clear()
    t = IDS["Telefon"]
    tok = token_of(ask(t, "2026-10-08T10:00"))
    b = bk(tok)
    CALLS.clear()
    # Başka kullanıcının sohbetinden basılamaz
    press("200", f"bk:ok:{b['id']}")
    assert sent("answerCallbackQuery")[-1]["text"] == "Randevu bulunamadı." and bk(tok)["status"] == "pending"
    press("100", "bk:ok:abc")
    assert sent("answerCallbackQuery")[-1]["text"] == "Randevu bulunamadı."
    press("100", f"bk:ok:{b['id']}")
    b = bk(tok)
    assert b["status"] == "confirmed" and one("SELECT 1 FROM events WHERE id = ?", (b["event_id"],))
    assert "Randevu onaylandı" in sent("answerCallbackQuery")[-1]["text"]
    edit = sent("editMessageText")[-1]
    assert "✅ <b>Randevu onaylandı</b>" in edit["text"] and json.loads(edit["reply_markup"])["inline_keyboard"] == []
    # Eski butona yeniden basmak bir şey değiştirmez
    press("100", f"bk:no:{b['id']}")
    assert "zaten onaylanmış" in sent("answerCallbackQuery")[-1]["text"] and bk(tok)["status"] == "confirmed"
    assert one("SELECT COUNT(*) AS n FROM events")["n"] == 1
    # Ziyaretçi onaylanınca aranacak numarayı görür
    assert 'href="tel:+905320001122"' in text(RAW.get(f"/r/i/{tok}"))
    # Red butonu
    tok2 = token_of(ask(t, "2026-10-08T11:00"))
    press("100", f"bk:no:{bk(tok2)['id']}")
    assert bk(tok2)["status"] == "rejected" and "reddedildi" in sent("editMessageText")[-1]["text"]
    # Webhook kurulu değilse buton gönderilmez, panoya yönlendirilir
    run("DELETE FROM app_state WHERE key = 'telegram_webhook'")
    CALLS.clear()
    token_of(ask(t, "2026-10-08T13:00"))
    msg = sent()[-1]
    assert "reply_markup" not in msg and "panoyu aç" in msg["text"]
    run("INSERT INTO app_state (key, value) VALUES ('telegram_webhook', 'https://localhost/telegram/webhook')")
    # Telegram hatası talebi bozmaz
    def boom(method, params=None, files=None):
        raise tg.TelegramError("Bad Gateway")

    original = tg._call
    tg._call = boom
    try:
        token_of(ask(t, "2026-10-08T14:00"))
    finally:
        tg._call = original
    clear()
    print("  telegram buttons OK")


# ---------- Ziyaretçi bağlantısı ----------
def test_visitor_cancel():
    clear()
    t = IDS["Tanışma"]
    visitor = app.test_client()
    tok = token_of(ask(t, "2026-10-09T10:00", client=visitor))
    b = bk(tok)
    ADMIN.post(f"/randevu/{b['id']}/onayla")
    event_id = bk(tok)["event_id"]
    CALLS.clear()
    token = csrf(visitor.get(f"/r/i/{tok}"))
    r = visitor.post(f"/r/i/{tok}/iptal", data={"_csrf": token})
    assert r.status_code == 400 and "kutusunu işaretle" in text(r) and bk(tok)["status"] == "confirmed"
    assert app.test_client().post(f"/r/i/{tok}/iptal", data={"confirm": "1"}).status_code == 400   # CSRF'siz olmaz
    r = visitor.post(f"/r/i/{tok}/iptal", data={"_csrf": token, "confirm": "1"})
    assert r.status_code == 303 and r.headers["Location"].endswith(f"/r/i/{tok}")
    b = bk(tok)
    assert b["status"] == "cancelled" and b["cancelled_by"] == "visitor" and b["event_id"] is None
    assert not one("SELECT 1 FROM events WHERE id = ?", (event_id,)) and "10:00" in free(t)[FRI]
    msg = sent()[-1]
    assert msg["chat_id"] == "100" and "Randevu iptal edildi</b> · ziyaretçi iptal etti" in msg["text"]
    assert "Ali Veli" in msg["text"]
    page = text(visitor.get(f"/r/i/{tok}"))
    assert "Randevuyu sen iptal ettin" in page and "Randevuyu iptal et<" not in page and "Yeni bir saat seç" in page
    assert visitor.get(f"/r/i/{tok}/randevu.ics").status_code == 404
    r = visitor.post(f"/r/i/{tok}/iptal", data={"_csrf": token, "confirm": "1"})
    assert r.status_code == 400 and "artık iptal edilemez" in text(r)
    assert "ziyaretçi iptal etti" in ADMIN.text("/randevu/")
    # Saati geçmiş randevu iptal edilemez
    run("INSERT INTO bookings (user_id, type_name, start_at, end_at, name, contact, status, manage_token) VALUES"
        " (1, 'X', '2026-10-05 07:00', '2026-10-05 07:30', 'Geç', '0532 123 45 67', 'confirmed', 'gecmis')")
    page = text(visitor.get("/r/i/gecmis"))
    assert "Onaylandı" in page and "Randevuyu iptal et" not in page and "Takvimime ekle" not in page
    assert visitor.post("/r/i/gecmis/iptal", data={"_csrf": token, "confirm": "1"}).status_code == 400
    # Olmayan / çok uzun anahtar
    missing = RAW.get("/r/i/olmayan-anahtar-123456")
    assert missing.status_code == 404 and missing.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert missing.headers["Cache-Control"] == "no-store"
    assert RAW.get("/r/i/" + "x" * 300).status_code == 404
    assert RAW.get("/r/i/olmayan/randevu.ics").status_code == 404
    clear()
    print("  visitor cancel OK")


# ---------- Kapalı sayfa, yalıtım ----------
def test_disabled_and_isolation():
    clear()
    t = IDS["Tanışma"]
    save(ADMIN, enabled="")
    missing = RAW.get("/r/olmayan-adres")
    disabled = RAW.get("/r/sule-cicek")
    assert missing.status_code == disabled.status_code == 404 and missing.get_data() == disabled.get_data()
    assert disabled.headers["X-Robots-Tag"] == "noindex, nofollow" and disabled.headers["Cache-Control"] == "no-store"
    assert RAW.get(f"/r/sule-cicek/{t}").status_code == 404
    assert RAW.get(f"/r/sule-cicek/{t}/talep?t=2026-10-06T10:00").status_code == 404
    assert AYSE.get("/r/sule-cicek").status_code == 404   # başka kullanıcı da göremez
    page = ADMIN.text("/r/sule-cicek")   # sahibi önizler
    assert "Sayfa kapalı: şu an sadece sen görüyorsun" in page and "Tanışma" in page
    r = ADMIN.get(f"/r/sule-cicek/{t}/talep?t=2026-10-06T10:00")
    assert r.status_code == 200 and "Sayfa kapalı" in text(r)
    r = ADMIN.post(f"/r/sule-cicek/{t}/talep", data={"t": "2026-10-06T10:00", "name": "Ben", "contact": "0532 123 45 67"})
    assert r.status_code == 400 and "önizlemede talep gönderilemez" in text(r)
    assert one("SELECT COUNT(*) AS n FROM bookings")["n"] == 0
    assert "Kapalı" in ADMIN.text("/randevu/")
    save(ADMIN)
    # Gizli tür ve başkasının türü dışarıdan aynı 404
    ADMIN.post(f"/randevu/tur/{t}/durum")
    hidden = RAW.get(f"/r/sule-cicek/{t}")
    assert hidden.status_code == 404 and hidden.get_data() == missing.get_data()
    ADMIN.post(f"/randevu/tur/{t}/durum")
    # Ayşe'nin kendi sayfası ve türü
    assert save(AYSE, slug="ayse-kaya", title="Ayşe ile").status_code == 302
    assert add_type(AYSE, name="Ayşe görüşmesi", location_detail="").status_code == 302
    ayse_type = one("SELECT id FROM booking_types WHERE user_id = ?", (AYSE_ID,))["id"]
    assert RAW.get(f"/r/sule-cicek/{ayse_type}").status_code == 404
    assert RAW.get(f"/r/ayse-kaya/{t}").status_code == 404
    page = text(RAW.get("/r/ayse-kaya"))
    assert "Ayşe görüşmesi" in page and "Tanışma" not in page and "Şule" not in page
    CALLS.clear()
    tok = token_of(ask(ayse_type, "2026-10-06T10:00", slug="ayse-kaya", name="Mehmet"))
    assert bk(tok)["user_id"] == AYSE_ID and sent()[-1]["chat_id"] == "200"
    assert "10:00" in free(t)[TUE]   # başkasının randevusu benim saatimi doldurmaz
    assert "Mehmet" not in ADMIN.text("/randevu/") and "Mehmet" in AYSE.text("/randevu/")
    page = AYSE.text("/randevu/")
    assert "sule-cicek" not in page and '<div class="title">Tanışma' not in AYSE.text("/randevu/?tab=turler")
    assert '<div class="title">Tanışma' in ADMIN.text("/randevu/?tab=turler")
    press("100", f"bk:ok:{bk(tok)['id']}")   # Şule, Ayşe'nin talebini onaylayamaz
    assert bk(tok)["status"] == "pending" and sent("answerCallbackQuery")[-1]["text"] == "Randevu bulunamadı."
    press("200", f"bk:ok:{bk(tok)['id']}")
    assert bk(tok)["status"] == "confirmed"
    assert one("SELECT user_id FROM events WHERE id = ?", (bk(tok)["event_id"],))["user_id"] == AYSE_ID
    # QR ve yönetim sayfaları
    page = ADMIN.text("/randevu/")
    assert '<svg' in page and 'class="qr"' in page and "📋 Kopyala" in page and "booking.js" in page
    assert "http://localhost/r/sule-cicek" in page
    r = ADMIN.get("/randevu/qr.png")
    assert r.status_code == 200 and r.headers["Content-Type"] == "image/png" and "randevu-sule-cicek-qr.png" in \
        r.headers["Content-Disposition"]
    r.close()
    for url in ("/randevu/", "/randevu/qr.png", "/randevu/?tab=turler"):
        assert RAW.get(url).status_code == 302, url   # girişsiz -> giriş sayfası
    # Kullanıcı silinince sayfası ve randevuları da gider
    ADMIN.post(f"/yonetim/kullanicilar/{AYSE_ID}/sil")
    assert RAW.get("/r/ayse-kaya").status_code == 404 and bk(tok) is None
    assert RAW.get(f"/r/i/{tok}").status_code == 404 and RAW.get("/r/sule-cicek").status_code == 200
    print("  disabled/isolation OK")


if __name__ == "__main__":
    test_settings_validation()
    test_types_and_blocks()
    test_slots()
    test_public_flow()
    test_escape_and_validation()
    test_abuse_limits()
    test_approval_web()
    test_no_approval()
    test_telegram_buttons()
    test_visitor_cancel()
    test_disabled_and_isolation()
    print("OK")
