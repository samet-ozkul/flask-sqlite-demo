"""Davetiye: oluşturma ve doğrulama, kapak fotoğrafı (kırpma, kota), herkese açık sayfa (girişsiz; güvenlik başlıkları,
OG etiketleri, HTML kaçışı, .ics), LCV (üç durum, kişi sayısı sınırı, özel soru), aynı tarayıcıdan (çerez) ve kişisel
düzenleme linkiyle yanıt değiştirme, son tarihten sonra ret, aynı isimle ikinci yanıt reddi, kontenjan, kişiye özel
linkler (yanıt bekleyenler, sadece davetliler, WhatsApp metni kodlaması), davetliler görünürlüğünün üç modu, sayılar /
CSV / yanıt silme, takvim etkinliği (oluşma, güncellenme, silinme), Telegram toplu bildirim (10 dakika) ve cron mesajları
(LCV özeti, bir gün önce; iki kez gitmez), bal tuzağı ve IP sınırı, link önizlemesi, kullanıcı yalıtımı, çöp kutusu ve
geri getirme, olmayan kod 404.

Çalıştır: .venv/Scripts/python tests/test_invites.py
"""
import hashlib
import io
import os
import re
import sys
from datetime import datetime, timedelta
from urllib.parse import quote, unquote

from markupsafe import escape
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, csrf, make_app  # noqa: E402

app = make_app()
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"
os.environ["CRON_SECRET"] = "gizli"

import pano.telegram as tg  # noqa: E402
import pano.todo_reminders as todo  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query, query_one  # noqa: E402
from pano.modules import invites  # noqa: E402
from pano.quota import summary  # noqa: E402
from pano.utils import TZ  # noqa: E402

CALLS = []
FAIL = [False]


def fake_call(method, params=None, files=None):
    if FAIL[0]:
        raise tg.TelegramError("bağlantı yok")
    CALLS.append((method, params or {}))
    return {"message_id": 1}


tg._call = fake_call
START = datetime(2026, 10, 5, 12, 0, tzinfo=TZ)   # Pazartesi 12:00
NOW = [START]
todo.now_local = lambda: NOW[0]

with app.app_context():
    AYSE_ID = create_user("ayse", "ayse12345")
    MEHMET_ID = create_user("mehmet", "mehmet12345")   # Telegram'ı bağlı değil
    execute("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
    execute("UPDATE users SET telegram_chat_id = '200' WHERE id = ?", (AYSE_ID,))
    execute("INSERT INTO notes (user_id, title, content) VALUES (1, 'GIZLI-NOT', 'gizli not içeriği')")
ADMIN = Client(app)
AYSE = Client(app, "ayse", "ayse12345")
MEHMET = Client(app, "mehmet", "mehmet12345")
RAW = app.test_client()

TITLE = "Elif'in 5. yaş günü"
BASE = {"title": TITLE, "host": "Ayşe & Mehmet", "starts_on": "2026-10-17", "starts_at": "15:00", "ends_at": "18:00",
        "place": "Neşeli Çocuk Kafe", "address": "Atatürk Cad. No: 5, Konya",
        "description": "Pasta 16:00'da\r\nHediye getirmeyin 🙂", "cover_emoji": "🎂", "accent": "pink",
        "rsvp_deadline": "2026-10-15", "max_per_response": "6", "capacity": "", "question": "", "show_guests": "counts",
        "allow_maybe": "1", "ask_count": "1", "calendar": "1"}
CHECKS = ("allow_maybe", "ask_count", "invite_only", "notify", "calendar")


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


def h(value):
    """Şablonun HTML kaçışıyla aynı ("'" -> &#39;)."""
    return str(escape(value))


def form_data(**fields):
    data = {**BASE, **fields}
    for key in CHECKS:
        if not data.get(key):
            data.pop(key, None)   # işaretsiz kutu formda hiç gönderilmez
    return data


def create(client, **fields):
    return client.post("/davetiye/yeni", data=form_data(**fields))


def edit(client, invite_id, **fields):
    return client.post(f"/davetiye/{invite_id}/duzenle", data=form_data(**fields))


def made(resp):
    assert resp.status_code == 302, (resp.status_code, text(resp)[:800])
    return invite(int(re.search(r"/davetiye/(\d+)$", resp.headers["Location"]).group(1)))


def invite(invite_id):
    return one("SELECT * FROM invites WHERE id = ?", (invite_id,))


def answers(inv):
    return rows("SELECT * FROM invite_responses WHERE invite_id = ? ORDER BY id", (inv["id"],))


def answer_of(inv, name):
    return one("SELECT * FROM invite_responses WHERE invite_id = ? AND name = ?", (inv["id"], name))


def png(width=2400, height=1600, color=(200, 30, 90)):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "PNG")
    buf.seek(0)
    return buf


class Visitor:
    """Girişsiz ziyaretçi: kendi çerezleri (oturum + yanıt çerezi) ve IP'si."""

    def __init__(self, ip="127.0.0.1"):
        self.c = app.test_client()
        self.env = {"REMOTE_ADDR": ip}
        self.token = None

    def open(self, code, ua=None, **params):
        query_string = "&".join(f"{k}={v}" for k, v in params.items() if v)
        r = self.c.get(f"/d/{code}" + (f"?{query_string}" if query_string else ""), environ_base=self.env,
                       headers={"User-Agent": ua} if ua else {})
        self.token = csrf(r) or self.token   # form yoksa (kapalı, süre doldu) önceki anahtar geçerli
        return r

    def answer(self, code, name="Ahmet", status="yes", count=None, note="", k=None, y=None, **extra):
        self.open(code, k=k, y=y)
        data = {"_csrf": self.token or "", "name": name, "status": status, "note": note, **extra}
        if count is not None:
            data["count"] = str(count)
        for key, value in (("k", k), ("y", y)):
            if value:
                data[key] = value
        return self.c.post(f"/d/{code}", data=data, environ_base=self.env)

    def cookie(self, code):
        c = self.c.get_cookie(f"davetiye_{code}", path=f"/d/{code}")
        return c.value if c else None


def ok(resp):
    assert resp.status_code == 303, (resp.status_code, text(resp)[:1500])
    return resp


def clear():
    # Önceki testlerin davetiyeleri cron'da mesaj üretmesin
    run("UPDATE invites SET notify = 0, deadline_sent_for = rsvp_deadline, eve_sent_for = starts_on")
    CALLS.clear()
    FAIL[0] = False
    NOW[0] = START


def sent():
    return [p for m, p in CALLS if m == "sendMessage"]


def cron():
    r = RAW.get("/cron/gizli/hatirlatma")
    assert r.status_code == 200, text(r)
    return r.json


def assert_public_headers(r):
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp and "base-uri 'none'" in csp
    assert "form-action 'self'" in csp and "img-src 'self'" in csp and "script-src" not in csp
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Robots-Tag"] == "noindex, nofollow" and r.headers["Cache-Control"] == "no-store"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    page = text(r)
    assert '<meta name="robots" content="noindex, nofollow">' in page and "<script" not in page


# ---------- Oluşturma ----------
def test_create_validation():
    clear()
    page = ADMIN.text("/davetiye/")
    assert "Henüz davetiye yok" in page and "/davetiye/yeni" in page
    page = ADMIN.text("/davetiye/yeni")
    for part in ('name="title"', 'name="starts_on"', 'name="starts_at"', 'name="rsvp_deadline"', 'name="capacity"',
                 'name="question"', 'name="show_guests"', 'value="pink" checked', 'name="photo"',
                 'enctype="multipart/form-data"', "🎉 Davetiyeyi oluştur", 'name="calendar" value="1" checked',
                 'name="allow_maybe" value="1" checked', 'name="notify" value="1" checked'):
        assert part in page, part
    cases = [
        ({"title": "  "}, "Başlığı yaz"),
        ({"starts_on": ""}, "Etkinliğin tarihini seç"),
        ({"starts_on": "2026-10-04", "rsvp_deadline": ""}, "Etkinlik tarihi geçmişte olamaz"),
        ({"starts_at": ""}, "Başlangıç saatini yaz"),
        ({"ends_at": "25:00"}, "Bitiş saati geçersiz"),
        ({"ends_at": "15.00"}, "Bitiş saati başlangıçla aynı olamaz"),
        ({"rsvp_deadline": "2026-10-18"}, "LCV son tarihi etkinlik gününden sonra olamaz"),
        ({"rsvp_deadline": "2026-10-04"}, "LCV son tarihi geçmişte olamaz"),
        ({"max_per_response": "0"}, "“Yanıt başına en fazla kişi” 1 ile 20 arasında olmalı"),
        ({"max_per_response": "21"}, "“Yanıt başına en fazla kişi” 1 ile 20 arasında olmalı"),
        ({"capacity": "0"}, "Kontenjan 1 ile 5000 arasında"),
        ({"capacity": "çok"}, "Kontenjan 1 ile 5000 arasında"),
    ]
    for case, message in cases:
        r = create(ADMIN, **{"title": "Yazdığım başlık", **case})
        page = text(r)
        assert r.status_code == 400 and message in page, (message, re.findall(r'class="flash[^"]*"[^>]*>([^<]*)', page))
        if case.get("title", "x").strip():
            assert 'value="Yazdığım başlık"' in page   # yazılanlar kaybolmaz
    assert one("SELECT COUNT(*) AS n FROM invites")["n"] == 0
    # Kişi sayısı sorulmuyorsa geçersiz sınır önemsiz
    inv = made(create(ADMIN, title="Sorusuz", ask_count="", max_per_response="abc", calendar=""))
    assert (inv["ask_count"], inv["max_per_response"], inv["event_id"]) == (0, 6, None)
    run("DELETE FROM invites")
    # Geçerli: boşluklar sadeleşir, emoji boşsa 🎉, açıklama satır sonları korunur, takvime özel etkinlik
    inv = made(create(ADMIN, title="  Elif'in   5. yaş günü ", cover_emoji="", capacity="40"))
    assert inv["title"] == TITLE and inv["cover_emoji"] == "🎉" and inv["description"] == "Pasta 16:00'da\nHediye getirmeyin 🙂"
    assert (inv["starts_on"], inv["starts_at"], inv["ends_at"], inv["rsvp_deadline"], inv["accent"], inv["show_guests"]) == (
        "2026-10-17", "15:00", "18:00", "2026-10-15", "pink", "counts")
    assert (inv["allow_maybe"], inv["ask_count"], inv["max_per_response"], inv["capacity"], inv["invite_only"],
            inv["notify"], inv["closed"]) == (1, 1, 6, 40, 0, 0, 0)
    assert re.fullmatch(r"[abcdefghjkmnpqrstuvwxyz23456789]{7}", inv["code"]) and inv["photo"] is None
    e = one("SELECT * FROM events WHERE id = ?", (inv["event_id"],))
    assert (e["user_id"], e["shared"], e["date"], e["time"], e["remind_before"]) == (1, 0, "2026-10-17", "15:00", None)
    assert e["title"] == "🎉 " + TITLE and e["place"] == "Neşeli Çocuk Kafe"
    for part in (f"Davetiye: http://localhost/d/{inv['code']}", "Ev sahibi: Ayşe & Mehmet", "Saat: 15:00–18:00",
                 "Adres: Atatürk Cad. No: 5, Konya"):
        assert part in e["note"], part
    assert "Takvimine de eklendi" in ADMIN.text(f"/davetiye/{inv['id']}")
    page = ADMIN.text(f"/davetiye/{inv['id']}")
    url = f"http://localhost/d/{inv['code']}"
    for part in (url, 'class="qr"', "📋 Kopyala", "invites.js", "QR (PNG)", "WhatsApp'ta paylaş", "Henüz yanıt yok",
                 f"/etkinlikler/{inv['event_id']}", "📅 Takvimde", "17 Ekim Cumartesi · 15:00–18:00",
                 "LCV son günü: 15 Ekim Perşembe", "kontenjan 40", "Yanıtlar açık", "⏸️ Yanıtları kapat"):
        assert part in page, part
    share = (f"🎉 {TITLE}\n📅 17 Ekim Cumartesi · 15:00–18:00\n📍 Neşeli Çocuk Kafe\n"
             f"Katılıp katılamayacağını buradan yazar mısın? {url}")
    assert f'href="https://wa.me/?text={quote(share, safe="")}"' in page
    assert "%C4%B1" in quote(share, safe="") and "%0A" in quote(share, safe="")   # ı ve satır sonu kodlanır
    assert TITLE + "</h1>" not in page and h(TITLE) in page
    page = ADMIN.text("/davetiye/")
    for part in (h(TITLE), "Henüz yanıt yok", "17 Ekim Cumartesi · 15:00–18:00", "📍 Neşeli Çocuk Kafe", url,
                 "Yanıtlar açık", "🎟️ 0/40"):
        assert part in page, part
    # Düzenleme formu kayıttaki değerlerle
    page = ADMIN.text(f"/davetiye/{inv['id']}/duzenle")
    for part in ('value="2026-10-17"', 'value="15:00"', 'value="18:00"', 'value="40"', 'value="2026-10-15"',
                 'name="calendar" value="1" checked', "Kaydet"):
        assert part in page, part
    # Geçmiş tarihli davetiyenin başka alanı düzeltilebilir (tarih değişmediyse)
    run("UPDATE invites SET starts_on = '2026-10-01', rsvp_deadline = '2026-09-30' WHERE id = ?", (inv["id"],))
    r = edit(ADMIN, inv["id"], title="Düzeltilmiş", starts_on="2026-10-01", rsvp_deadline="2026-09-30")
    assert r.status_code == 302 and invite(inv["id"])["title"] == "Düzeltilmiş"
    assert "sona erdi" in ADMIN.text(f"/davetiye/{inv['id']}")
    run("DELETE FROM invites")
    run("DELETE FROM events")
    print("  create/validation OK")


def test_photo():
    clear()
    r = ADMIN.post("/davetiye/yeni", data={**form_data(title="Fotoğraflı"), "photo": (png(), "kapak.png")})
    inv = made(r)
    code = inv["code"]
    img = Image.open(io.BytesIO(inv["photo"]))
    assert img.format == "JPEG" and img.size == (1200, 630) and not img.getexif()
    with app.app_context():
        assert summary(1)["usage"]["invites"] == len(inv["photo"])   # kotaya sayılır
    r = RAW.get(f"/d/{code}/kapak.jpg")
    assert r.status_code == 200 and r.headers["Content-Type"] == "image/jpeg" and r.get_data() == inv["photo"]
    assert r.headers["Cache-Control"] == "private, max-age=86400" and r.headers["X-Robots-Tag"] == "noindex, nofollow"
    page = text(RAW.get(f"/d/{code}"))
    v = invites.photo_version(inv)
    assert f'<meta property="og:image" content="http://localhost/d/{code}/kapak.jpg?v={v}">' in page
    assert f'<img src="/d/{code}/kapak.jpg?v={v}"' in page and "summary_large_image" in page
    assert 'class="emoji"' not in page
    # Küçük fotoğraf büyütülmez, oran korunur
    r = ADMIN.post(f"/davetiye/{inv['id']}/duzenle", data={**form_data(title="Fotoğraflı"), "photo": (png(600, 600), "k.png")})
    assert r.status_code == 302
    assert Image.open(io.BytesIO(invite(inv["id"])["photo"])).size == (600, 315)
    # Geçersiz dosya: kayıt değişmez
    r = ADMIN.post(f"/davetiye/{inv['id']}/duzenle",
                   data={**form_data(title="Bozuk"), "photo": (io.BytesIO(b"resim degil"), "x.png")})
    assert r.status_code == 400 and "Fotoğraf okunamadı" in text(r) and invite(inv["id"])["title"] == "Fotoğraflı"
    # Kaldır
    edit(ADMIN, inv["id"], title="Fotoğraflı", remove_photo="1")
    assert invite(inv["id"])["photo"] is None and RAW.get(f"/d/{code}/kapak.jpg").status_code == 404
    page = text(RAW.get(f"/d/{code}"))
    assert "og:image" not in page and 'class="emoji"' in page and "🎂" in page
    # Yönetici sınırları: yükleme kapalı / alan dolu
    run("UPDATE users SET upload_max_mb = 0 WHERE id = ?", (MEHMET_ID,))
    r = MEHMET.post("/davetiye/yeni", data={**form_data(title="M"), "photo": (png(), "k.png")})
    assert r.status_code == 400 and "dosya yükleme kapalı" in text(r)
    run("UPDATE users SET upload_max_mb = NULL, quota_mb = 0 WHERE id = ?", (MEHMET_ID,))
    r = MEHMET.post("/davetiye/yeni", data={**form_data(title="M"), "photo": (png(), "k.png")})
    assert r.status_code == 400 and "Depolama alanın doldu" in text(r)
    run("UPDATE users SET quota_mb = NULL WHERE id = ?", (MEHMET_ID,))
    assert one("SELECT COUNT(*) AS n FROM invites WHERE user_id = ?", (MEHMET_ID,))["n"] == 0
    print("  photo OK")


# ---------- Herkese açık sayfa ----------
def test_public_page():
    clear()
    inv = made(create(ADMIN))
    code = inv["code"]
    r = app.test_client().get(f"/d/{code}")
    page = text(r)
    assert r.status_code == 200 and "Set-Cookie" in r.headers   # formu açan ziyaretçinin oturumuna CSRF anahtarı
    assert_public_headers(r)
    for part in (h(TITLE), "💌 Ayşe &amp; Mehmet seni davet ediyor", "📅 17 Ekim Cumartesi", "🕒 15:00–18:00",
                 "📍 Neşeli Çocuk Kafe", "Atatürk Cad. No: 5, Konya", "Pasta 16:00", "Hediye getirmeyin",
                 f'href="https://www.google.com/maps/search/?api=1&amp;query={quote("Atatürk Cad. No: 5, Konya")}"',
                 f'href="/d/{code}/davetiye.ics"', "Son yanıt günü: <b>15 Ekim Perşembe</b>", 'name="name"',
                 'value="yes"', 'value="maybe"', 'value="no"', 'name="count"', 'max="6"', 'name="note"',
                 'name="website"', 'name="_csrf"', "💌 Yanıtı gönder", "#be185d", "#f472b6", 'class="emoji"', "🎂"):
        assert part in page, part
    for leak in ("GIZLI-NOT", "topbar", "admin", "Önizleme", 'name="answer"', "Kimler geliyor"):
        assert leak not in page, leak
    # OG etiketleri: başlık ve "tarih · yer"; fotoğraf yoksa og:image yok
    assert f'<meta property="og:title" content="{h(TITLE)}">' in page
    assert '<meta property="og:description" content="17 Ekim Cumartesi · 15:00–18:00 · Neşeli Çocuk Kafe">' in page
    assert f'<meta property="og:url" content="http://localhost/d/{code}">' in page and "og:image" not in page
    # Adreste büyük harf: küçük harfli adrese (yanıt çerezinin yolu ona bağlı); anahtarlar korunur
    r = RAW.get(f"/d/{code.upper()}")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/d/{code}")
    assert RAW.get(f"/d/{code.upper()}?k=abc").headers["Location"].endswith(f"/d/{code}?k=abc")
    # Olmayan kod: başlıklı aynı 404
    missing = RAW.get("/d/yokbuyok")
    assert missing.status_code == 404 and missing.headers["Cache-Control"] == "no-store"
    assert missing.headers["X-Robots-Tag"] == "noindex, nofollow" and missing.headers["Referrer-Policy"] == "no-referrer"
    for url in ("/d/" + "x" * 300, "/d/yokbuyok/davetiye.ics", "/d/yokbuyok/kapak.jpg"):
        assert RAW.get(url).status_code == 404, url
    # Sahibi önizler: şerit, gönderme kapalı (sunucuda da reddedilir), görüntülenme sayılmaz
    page = text(ADMIN.get(f"/d/{code}"))
    assert "Önizleme: bu senin davetiyen" in page and f"/davetiye/{inv['id']}" in page
    assert "<button class=\"btn\" disabled>" in page
    r = ADMIN.post(f"/d/{code}", data={"name": "Ben", "status": "yes"})
    assert r.status_code == 403 and "önizlemede yanıt gönderilemez" in text(r) and not answers(inv)
    # .ics: UTC saatler, yer, açıklama, 2 saat önce uyarı
    r = RAW.get(f"/d/{code}/davetiye.ics")
    body = text(r)
    assert r.status_code == 200 and r.headers["Content-Type"].startswith("text/calendar")
    assert "attachment" in r.headers["Content-Disposition"] and r.headers["Cache-Control"] == "no-store"
    for part in ("BEGIN:VEVENT", f"UID:invite-{inv['id']}@kisisel-pano", "DTSTART:20261017T120000Z",
                 "DTEND:20261017T150000Z", f"SUMMARY:{TITLE}", "LOCATION:Neşeli Çocuk Kafe\\, Atatürk Cad. No: 5\\, Konya",
                 "TRIGGER:-PT2H", f"URL:http://localhost/d/{code}"):
        assert part in body, part
    assert body.endswith("END:VCALENDAR\r\n")
    # Bitiş saati yoksa 3 saat; gece yarısını geçen bitiş ertesi gün
    edit(ADMIN, inv["id"], ends_at="")
    body = text(RAW.get(f"/d/{code}/davetiye.ics"))
    assert "DTEND:20261017T150000Z" in body and "🕒 15:00<" in text(RAW.get(f"/d/{code}"))
    edit(ADMIN, inv["id"], starts_at="21:00", ends_at="02:00")
    assert "DTEND:20261017T230000Z" in text(RAW.get(f"/d/{code}/davetiye.ics"))
    with app.app_context():
        assert invites.ends(invite(inv["id"])) == datetime(2026, 10, 18, 2, 0)
    print("  public page OK")


def test_escape():
    clear()
    inv = made(create(ADMIN, title="<script>alert(1)</script> parti", host="<b>Ev</b>", place="<img src=x onerror=alert(1)>",
                      address="A & B \"sokak\"", description="<i>açıklama</i>", question="<u>Alerji?</u>",
                      cover_emoji="<s>", show_guests="names", notify="1"))
    code = inv["code"]
    r = RAW.get(f"/d/{code}")
    page = text(r)
    assert_public_headers(r)
    for part in ("&lt;script&gt;alert(1)&lt;/script&gt; parti", "&lt;b&gt;Ev&lt;/b&gt;", "&lt;img src=x onerror=alert(1)&gt;",
                 "&lt;i&gt;açıklama&lt;/i&gt;", "&lt;u&gt;Alerji?&lt;/u&gt;", "&lt;s&gt;", "A &amp; B &#34;sokak&#34;"):
        assert part in page, part
    for raw_ in ("<b>Ev", "<img src=x", "<i>açıklama", "<u>Alerji", "<s>"):
        assert raw_ not in page, raw_
    assert '<meta property="og:title" content="&lt;script&gt;' in page
    ok(Visitor().answer(code, name="<i>Kötü</i> \"isim\"", note="<b>not</b>", answer="<script>x</script>"))
    page = text(RAW.get(f"/d/{code}"))
    assert "&lt;i&gt;Kötü&lt;/i&gt; &#34;isim&#34;" in page and "<i>Kötü" not in page
    page = ADMIN.text(f"/davetiye/{inv['id']}")
    assert "&lt;i&gt;Kötü&lt;/i&gt;" in page and "&lt;b&gt;not&lt;/b&gt;" in page and "&lt;script&gt;x" in page
    assert "<script>x" not in page and "<script>alert(1)" not in page
    msg = sent()[-1]["text"]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in msg and "&lt;i&gt;Kötü&lt;/i&gt;" in msg and "<i>Kötü" not in msg
    # Yönetim sayfaları girişsiz açılmaz
    for url in ("/davetiye/", "/davetiye/yeni", f"/davetiye/{inv['id']}", f"/davetiye/{inv['id']}/yanitlar.csv",
                f"/davetiye/{inv['id']}/qr.png"):
        assert RAW.get(url).status_code == 302, url
    print("  escape OK")


# ---------- LCV ----------
def test_rsvp_flow():
    clear()
    inv = made(create(ADMIN, question="Yemek tercihin / alerjin?"))
    code = inv["code"]
    # CSRF anahtarı olmadan (formu açmadan ya da başka siteden) yanıt verilemez
    assert app.test_client().post(f"/d/{code}", data={"name": "X", "status": "yes"}).status_code == 400
    stranger = app.test_client()
    stranger.get(f"/d/{code}")
    assert stranger.post(f"/d/{code}", data={"_csrf": "yanlis", "name": "X", "status": "yes"}).status_code == 400
    page = text(RAW.get(f"/d/{code}"))
    assert 'name="answer"' in page and "Yemek tercihin / alerjin?" in page
    # Doğrulama; hata sonrası yazılanlar korunur
    a = Visitor()
    for kw, message in (({"name": "  "}, "Adını yaz"), ({"status": ""}, "Gelip gelemeyeceğini seç"),
                        ({"status": "evet"}, "Gelip gelemeyeceğini seç"), ({"count": 7}, "Kişi sayısı 1 ile 6 arasında"),
                        ({"count": 0}, "Kişi sayısı 1 ile 6 arasında"), ({"count": "iki"}, "Kişi sayısı 1 ile 6")):
        r = a.answer(code, **{"name": "Ahmet", "note": "Yazdığım not", **kw})
        assert r.status_code == 400 and message in text(r), (kw, text(r)[:300])
        assert "Yazdığım not" in text(r)
    assert not answers(inv)
    # Geliyorum (3 kişi), özel soru
    r = ok(a.answer(code, name="  Ahmet   Yılmaz ", count=3, note="Biraz geç kalırız\r\nkusura bakmayın",
                    answer="  Fıstık  alerjisi "))
    assert r.headers["Location"].endswith(f"/d/{code}?ok=1")
    cookie = [x for x in r.headers.getlist("Set-Cookie") if x.startswith(f"davetiye_{code}=")][0]
    for part in ("HttpOnly", "SameSite=Lax", f"Path=/d/{code}", "Max-Age=31536000"):
        assert part in cookie, part
    token = a.cookie(code)
    row = answer_of(inv, "Ahmet Yılmaz")
    assert (row["status"], row["count"], row["note"], row["answer"], row["name_key"]) == (
        "yes", 3, "Biraz geç kalırız\nkusura bakmayın", "Fıstık alerjisi", "ahmet yilmaz")
    assert len(token) == 22 and row["edit_token_hash"] == hashlib.sha256(token.encode()).hexdigest()   # düz saklanmaz
    assert len(row["ip_hash"]) == 32 and "127.0.0.1" not in row["ip_hash"] and row["guest_id"] is None
    page = text(a.c.get(f"/d/{code}?ok=1"))
    for part in ("Teşekkürler, yanıtın kaydedildi", "Yanıtını değiştirmek için bu sayfaya tekrar gel",
                 "✅ Yanıtın: Geliyorum</b> · 3 kişi", "❓ Fıstık alerjisi", f"http://localhost/d/{code}?y={token}",
                 "kişisel linkini kaydet", "💾 Yanıtımı güncelle", 'value="Ahmet Yılmaz"', 'value="3"',
                 'value="yes" required checked'):
        assert part in page, part
    assert "Teşekkürler" not in text(a.c.get(f"/d/{code}"))   # teşekkür sadece yönlendirmede
    # Aynı tarayıcı yanıtını değiştirir (yeni satır açılmaz); değişmeden gönderince sayaç artmaz
    rev = invite(inv["id"])["rev"]
    ok(a.answer(code, name="Ahmet Yılmaz", status="no", count=3, note="Maalesef"))
    row = answer_of(inv, "Ahmet Yılmaz")
    assert (row["status"], row["count"], row["note"], row["answer"]) == ("no", 0, "Maalesef", "")
    assert len(answers(inv)) == 1 and invite(inv["id"])["rev"] == rev + 1
    ok(a.answer(code, name="Ahmet Yılmaz", status="no", note="Maalesef"))
    assert invite(inv["id"])["rev"] == rev + 1
    assert "❌ Yanıtın: Gelemiyorum" in text(a.c.get(f"/d/{code}"))
    # Kişisel düzenleme linki: başka cihazda yanıtı açar, değiştirir; o tarayıcı da bundan sonra tanınır
    b = Visitor("10.0.0.9")
    r = b.open(code, y=token)
    page = text(r)
    assert r.status_code == 200 and "❌ Yanıtın: Gelemiyorum" in page and f'name="y" value="{token}"' in page
    assert b.cookie(code) == token
    ok(b.answer(code, name="Ahmet Yılmaz", status="maybe", y=token))
    assert answer_of(inv, "Ahmet Yılmaz")["status"] == "maybe" and len(answers(inv)) == 1
    assert "🤔 Yanıtın: Belki" in text(b.c.get(f"/d/{code}"))
    r = Visitor().open(code, y="uydurma-belirtec")
    assert r.status_code == 403 and "Bu yanıt linki geçersiz" in text(r) and 'name="name"' not in text(r)
    assert_public_headers(r)
    v = Visitor()
    v.open(code)   # form anahtarı ortak linkten
    r = v.answer(code, name="Biri", y="x" * 100)
    assert r.status_code == 403 and "Bu yanıt linki geçersiz" in text(r) and not answer_of(inv, "Biri")
    # Aynı isimle ikinci yanıt reddedilir (Türkçe harf ve büyük/küçük duyarsız)
    for same in ("ahmet yılmaz", "AHMET YILMAZ", "Ahmet  Yilmaz"):
        r = Visitor("10.0.0.2").answer(code, name=same)
        assert r.status_code == 409 and "adıyla yanıt var; değiştirmek için yanıt linkini kullan" in text(r), same
    ok(Visitor("10.0.0.2").answer(code, name="Ayşe", count=2))
    # Kendi yanıtını başkasının adına çeviremez
    r = a.answer(code, name="AYŞE", status="no")
    assert r.status_code == 409 and answer_of(inv, "Ahmet Yılmaz")["status"] == "maybe"
    # "Belki" kapalıysa seçilemez; kişi sayısı sorulmuyorsa her "Geliyorum" 1 kişi
    q = made(create(ADMIN, title="Sade", allow_maybe="", ask_count="", calendar=""))
    page = text(RAW.get(f"/d/{q['code']}"))
    assert 'value="maybe"' not in page and 'name="count"' not in page and 'name="answer"' not in page
    r = Visitor().answer(q["code"], name="Can", status="maybe")
    assert r.status_code == 400 and "Gelip gelemeyeceğini seç" in text(r)
    ok(Visitor().answer(q["code"], name="Can", count=5, answer="yok sayılır"))
    assert (answer_of(q, "Can")["count"], answer_of(q, "Can")["answer"]) == (1, "")
    print("  rsvp flow OK")


def test_deadline_and_state():
    clear()
    inv = made(create(ADMIN, title="Son tarihli", calendar=""))
    code = inv["code"]
    a = Visitor()
    ok(a.answer(code, name="Ali", count=2))
    NOW[0] = datetime(2026, 10, 15, 23, 59, tzinfo=TZ)   # son gün sonuna kadar açık
    ok(a.answer(code, name="Ali", count=1))
    NOW[0] = datetime(2026, 10, 16, 0, 0, tzinfo=TZ)
    page = text(a.c.get(f"/d/{code}"))
    assert "Yanıt süresi doldu (son gün: 15 Ekim Perşembe)" in page and "💾 Yanıtımı güncelle" not in page
    assert "✅ Yanıtın: Geliyorum" in page and "kişisel linkini kaydet" not in page
    r = a.answer(code, name="Ali", count=3)
    assert r.status_code == 409 and "Yanıt süresi doldu" in text(r) and answer_of(inv, "Ali")["count"] == 1
    a.c.delete_cookie(f"davetiye_{code}", path=f"/d/{code}")   # yeni yanıt (form yok; önceki anahtar geçerli)
    r = a.answer(code, name="Yeni", status="no")
    assert r.status_code == 409 and not answer_of(inv, "Yeni")
    assert "LCV süresi doldu" in ADMIN.text(f"/davetiye/{inv['id']}")
    # Etkinlik bitince "sona erdi"; geçmişe düşer
    NOW[0] = datetime(2026, 10, 17, 18, 0, tzinfo=TZ)
    page = text(RAW.get(f"/d/{code}"))
    assert "Bu etkinlik sona erdi" in page and 'name="name"' not in page
    page = ADMIN.text("/davetiye/")
    assert "Geçmiş" in page and "Sona erdi" in page
    # Son tarih yoksa etkinlik başlayana kadar
    NOW[0] = START
    b = made(create(ADMIN, title="Son tarihsiz", rsvp_deadline="", starts_on="2026-10-06", calendar=""))
    NOW[0] = datetime(2026, 10, 6, 14, 59, tzinfo=TZ)
    ok(Visitor().answer(b["code"], name="Veli"))
    NOW[0] = datetime(2026, 10, 6, 15, 0, tzinfo=TZ)
    page = text(RAW.get(f"/d/{b['code']}"))
    assert "Etkinlik başladı; artık yanıt verilemez" in page and 'name="name"' not in page
    # Elle kapat / aç
    NOW[0] = START
    ADMIN.post(f"/davetiye/{b['id']}/durum")
    assert invite(b["id"])["closed"] == 1 and "Yanıtlar kapatıldı" in ADMIN.text(f"/davetiye/{b['id']}")
    page = text(RAW.get(f"/d/{b['code']}"))
    assert "Yanıtlar kapatıldı; artık yanıt verilemez" in page and 'name="name"' not in page
    c = Visitor()
    c.open(code)   # anahtar için
    r = c.answer(b["code"], name="Zeki")
    assert r.status_code == 409 and "Yanıtlar kapatıldı" in text(r)
    ADMIN.post(f"/davetiye/{b['id']}/durum")
    assert invite(b["id"])["closed"] == 0 and "Yanıtlar açıldı" in ADMIN.text(f"/davetiye/{b['id']}")
    ok(Visitor().answer(b["code"], name="Zeki"))
    print("  deadline/state OK")


def test_capacity():
    clear()
    inv = made(create(ADMIN, title="Kontenjanlı", capacity="5", calendar=""))
    code = inv["code"]
    a, b, c = Visitor("10.2.0.1"), Visitor("10.2.0.2"), Visitor("10.2.0.3")
    ok(a.answer(code, name="Ali", count=3))
    assert "🎟️ 2 kişilik yer kaldı" in text(c.open(code))
    r = b.answer(code, name="Banu", count=3)
    assert r.status_code == 409 and "Kontenjanda sadece 2 kişilik yer kaldı" in text(r) and "“Belki” ya da" in text(r)
    ok(b.answer(code, name="Banu", count=2))
    page = text(c.open(code))
    assert "Kontenjan doldu: “Geliyorum” yanıtı alınmıyor" in page and re.search(r'value="yes" required\s+disabled', page)
    r = c.answer(code, name="Cem", count=1)
    assert r.status_code == 409 and "Kontenjan doldu: artık “Geliyorum” yanıtı alınmıyor" in text(r)
    ok(c.answer(code, name="Cem", status="maybe"))
    ok(c.answer(code, name="Cem", status="no"))
    # Kendi yeri korunur: azaltıp tekrar artırabilir, fazlasını alamaz
    ok(a.answer(code, name="Ali", count=2))
    ok(a.answer(code, name="Ali", count=3))
    r = a.answer(code, name="Ali", count=4)
    assert r.status_code == 409 and "Kontenjanda sadece 3 kişilik yer kaldı" in text(r)
    assert 'max="3"' in text(a.open(code))   # kendi 3 kişisi + boş yer
    # Kontenjan sonradan düşürülse de mevcut yanıt aynı sayıyla güncellenebilir
    edit(ADMIN, inv["id"], title="Kontenjanlı", capacity="4", calendar="")
    ok(a.answer(code, name="Ali", count=3, note="Not ekledim"))
    assert answer_of(inv, "Ali")["note"] == "Not ekledim"
    # "Belki" kapalıysa sadece "Gelemiyorum" önerilir
    q = made(create(ADMIN, title="Tek kişilik", capacity="1", allow_maybe="", calendar=""))
    ok(Visitor().answer(q["code"], name="X"))
    r = Visitor().answer(q["code"], name="Y")
    assert r.status_code == 409 and "“Gelemiyorum” seçebilirsin" in text(r) and "Belki" not in text(r).split("alert")[1][:200]
    print("  capacity OK")


# ---------- Kişiye özel linkler ----------
def test_guests():
    clear()
    inv = made(create(ADMIN, calendar=""))
    code, iid = inv["code"], inv["id"]
    page = ADMIN.text(f"/davetiye/{iid}")
    assert "Kişiye özel davet linkleri" in page and "＋ Davetli ekle" in page
    r = ADMIN.post(f"/davetiye/{iid}/davetli", data={"guests": "Ahmet ve ailesi, 0532 123 45 67\n  \nAyşe Teyze\n"
                                                                "Mehmet 0505 111 22 33\nMasa 12"})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/davetiye/{iid}#davetliler")
    guests = rows("SELECT * FROM invite_guests WHERE invite_id = ? ORDER BY id", (iid,))
    assert [(x["label"], x["phone"]) for x in guests] == [("Ahmet ve ailesi", "0532 123 45 67"), ("Ayşe Teyze", ""),
                                                         ("Mehmet", "0505 111 22 33"), ("Masa 12", "")]
    assert all(len(x["token"]) == 16 for x in guests)
    tok = {x["label"]: x["token"] for x in guests}
    page = ADMIN.text(f"/davetiye/{iid}")
    assert "4 kişiye özel link hazır" in page and "0/4 yanıt verdi" in page
    assert "Yanıt bekleyenler:</b> Ahmet ve ailesi, Ayşe Teyze, Mehmet, Masa 12" in page
    # WhatsApp: telefonu olana o numaraya, Türkçe metin ve link UTF-8 yüzde kodlamasıyla
    link = f"http://localhost/d/{code}?k={tok['Ahmet ve ailesi']}"
    message = (f"Merhaba Ahmet, “{TITLE}” için davetlisin 🎉 17 Ekim Cumartesi · 15:00–18:00 · Neşeli Çocuk Kafe. "
               f"Katılıp katılamayacağını buradan yazar mısın? {link}")
    wa = re.search(r'href="(https://wa\.me/905321234567\?text=[^"]+)"', page).group(1)
    assert wa == f"https://wa.me/905321234567?text={quote(message, safe='')}"
    encoded = wa.split("?text=")[1]
    assert " " not in encoded and "&" not in encoded and "?" not in encoded and "%3Fk%3D" in encoded
    assert "Merhaba%20Ahmet%2C" in encoded and "%C4%B1" in encoded and "%F0%9F%8E%89" in encoded   # ı ve 🎉
    assert unquote(encoded) == message
    assert "https://wa.me/905051112233?text=Merhaba%20Mehmet%2C" in page
    assert re.search(r'href="https://wa\.me/\?text=Merhaba%20Ay%C5%9Fe%2C', page)   # telefonsuz: kişiyi kendisi seçer
    # Davetli kendi linkinden: ad etikete göre dolu; yanıtı o linke bağlanır
    ahmet = Visitor()
    page = text(ahmet.open(code, k=tok["Ahmet ve ailesi"]))
    assert 'value="Ahmet ve ailesi"' in page and f'name="k" value="{tok["Ahmet ve ailesi"]}"' in page
    r = ok(ahmet.answer(code, name="Ahmet ve ailesi", count=4, k=tok["Ahmet ve ailesi"]))
    assert r.headers["Location"].endswith(f"/d/{code}?ok=1&k={tok['Ahmet ve ailesi']}")
    row = answer_of(inv, "Ahmet ve ailesi")
    assert row["guest_id"] == guests[0]["id"] and row["count"] == 4
    page = text(ahmet.c.get(f"/d/{code}?ok=1&k={tok['Ahmet ve ailesi']}"))
    assert "Teşekkürler" in page and "Bu link sana özel" in page and "kişisel linkini kaydet" not in page
    # Başka cihazdan aynı linkle düzenlenir (çerez gerekmez)
    other = Visitor("10.3.0.1")
    page = text(other.open(code, k=tok["Ahmet ve ailesi"]))
    assert "✅ Yanıtın: Geliyorum</b> · 4 kişi" in page and "💾 Yanıtımı güncelle" in page
    ok(other.answer(code, name="Ahmet ve ailesi", status="no", k=tok["Ahmet ve ailesi"]))
    assert answer_of(inv, "Ahmet ve ailesi")["status"] == "no" and len(answers(inv)) == 1
    page = ADMIN.text(f"/davetiye/{iid}")
    assert "1/4 yanıt verdi" in page and "Yanıt bekleyenler:</b> Ayşe Teyze, Mehmet, Masa 12" in page
    assert "🔑 Ahmet ve ailesi" in page
    # Geçersiz / iptal edilmiş link
    r = RAW.get(f"/d/{code}?k=uydurma")
    assert r.status_code == 403 and "kişiye özel link geçersiz" in text(r) and 'name="name"' not in text(r)
    assert_public_headers(r)
    v = Visitor()
    v.open(code)   # form anahtarı ortak linkten
    r = v.answer(code, name="Kim", k="uydurma")
    assert r.status_code == 403 and "kişiye özel link geçersiz" in text(r) and not answer_of(inv, "Kim")
    # Yanıt veren davetlinin linki iptal edilemez; vermeyenin edilir
    ADMIN.post(f"/davetiye/{iid}/davetli/{guests[0]['id']}/sil")
    assert one("SELECT 1 FROM invite_guests WHERE id = ?", (guests[0]["id"],))
    assert "Bu davetli yanıt verdi; link iptal edilemez" in ADMIN.text(f"/davetiye/{iid}")
    ADMIN.post(f"/davetiye/{iid}/davetli/{guests[3]['id']}/sil")
    assert not one("SELECT 1 FROM invite_guests WHERE id = ?", (guests[3]["id"],))
    assert RAW.get(f"/d/{code}?k={tok['Masa 12']}").status_code == 403
    # Yanıt silinince davetli yeniden yanıt verebilir
    ADMIN.post(f"/davetiye/{iid}/yanit/{row['id']}/sil")
    assert "Kişiye özel linki yeniden yanıt verebilir" in ADMIN.text(f"/davetiye/{iid}")
    ok(Visitor().answer(code, name="Ahmet", count=2, k=tok["Ahmet ve ailesi"]))
    # Sadece davetliler: ortak link yeni yanıt almaz; davetli linki ve kendi yanıtı (çerez) çalışır
    edit(ADMIN, iid, invite_only="1", calendar="")
    page = text(RAW.get(f"/d/{code}"))
    assert "sadece kişiye özel linklerle yanıt verilebilir" in page and 'name="name"' not in page
    v = Visitor()
    v.open(code, k=tok["Ayşe Teyze"])   # anahtar için
    r = v.answer(code, name="Davetsiz")
    assert r.status_code == 403 and "sadece kişiye özel linklerle" in text(r) and not answer_of(inv, "Davetsiz")
    ayse = Visitor()
    ok(ayse.answer(code, name="Ayşe Teyze", status="maybe", k=tok["Ayşe Teyze"]))
    page = text(ayse.c.get(f"/d/{code}"))   # aynı tarayıcı ortak linkten de kendi yanıtını görür ve değiştirir
    assert "🤔 Yanıtın: Belki" in page and "💾 Yanıtımı güncelle" in page
    ok(ayse.answer(code, name="Ayşe Teyze", status="yes", count=1))
    assert answer_of(inv, "Ayşe Teyze")["status"] == "yes"
    # Sınır ve boş form
    ADMIN.post(f"/davetiye/{iid}/davetli", data={"guests": "  \n "})
    assert "Her satıra bir davetli yaz" in ADMIN.text(f"/davetiye/{iid}")
    ADMIN.post(f"/davetiye/{iid}/davetli", data={"guests": "\n".join(f"Kişi {i}" for i in range(200))})
    assert "en fazla 200 kişiye özel link" in ADMIN.text(f"/davetiye/{iid}")
    ADMIN.post(f"/davetiye/{iid}/davetli", data={"guests": "X" * 100})
    assert one("SELECT label FROM invite_guests WHERE invite_id = ? ORDER BY id DESC", (iid,))["label"] == "X" * 60
    with app.app_context():
        assert invites.parse_guest_line("Ali; +90 532 000 11 22") == ("Ali", "+90 532 000 11 22")
        assert invites.parse_guest_line("Oda 101") == ("Oda 101", "")
    print("  guests OK")


def test_visibility():
    clear()
    made_ = {mode: made(create(ADMIN, title=f"Görünürlük {mode}", show_guests=mode, calendar=""))
             for mode in ("counts", "names", "none")}
    for inv in made_.values():
        ok(Visitor().answer(inv["code"], name="Ahmet", count=3))
        ok(Visitor().answer(inv["code"], name="Ayşe"))
        ok(Visitor().answer(inv["code"], name="Can", status="maybe"))
        ok(Visitor().answer(inv["code"], name="Deniz", status="no"))
    page = text(RAW.get(f"/d/{made_['counts']['code']}"))
    assert "Kimler geliyor?" in page and "✅ 4 kişi geliyor · 🤔 1 belki · ❌ 1 gelemiyor" in page
    assert "Gelenler:" not in page and "Ahmet" not in page and "Yanıtını sadece davet eden görür" in page
    page = text(RAW.get(f"/d/{made_['names']['code']}"))
    assert "✅ 4 kişi geliyor" in page and "<b>Gelenler:</b> Ahmet (+2), Ayşe" in page and "<b>Belki:</b> Can" in page
    assert "Deniz" not in page and "adın bu sayfada da yazar" in page   # gelemeyenlerin adı yazılmaz
    page = text(RAW.get(f"/d/{made_['none']['code']}"))
    assert "Kimler geliyor?" not in page and "4 kişi geliyor" not in page and "Ahmet" not in page
    print("  visibility OK")


def test_detail_csv_delete():
    clear()
    inv = made(create(ADMIN, title="Yemek", question="Alerjin var mı?", calendar=""))
    iid, code = inv["id"], inv["code"]
    ok(Visitor().answer(code, name="Ali", count=2, note="Geliyoruz", answer="Fıstık"))
    ok(Visitor().answer(code, name='=HYPERLINK("http://x")', status="no"))
    ok(Visitor().answer(code, name="Veli", status="maybe", note="+90 bakacağım"))
    page = ADMIN.text(f"/davetiye/{iid}")
    for part in ("2 kişi geliyor · 1 belki · 1 gelemiyor", "<td>Ali</td>", "✅ Geliyorum", "🤔 Belki", "❌ Gelemiyorum",
                 "Geliyoruz", "Fıstık", "Alerjin var mı?", "⬇️ CSV", '<div class="value">2 kişi</div>', "1 yanıt"):
        assert part in page, part
    page = ADMIN.text("/davetiye/")
    assert "2 kişi geliyor · 1 belki · 1 gelemiyor" in page
    r = ADMIN.get(f"/davetiye/{iid}/yanitlar.csv")
    body = text(r)
    assert r.headers["Content-Type"].startswith("text/csv") and f"davetiye-{code}.csv" in r.headers["Content-Disposition"]
    lines = body.lstrip("\ufeff").split("\r\n")
    assert body.startswith("\ufeff") and lines[0] == "Zaman;İsim;Durum;Kişi;Not;Alerjin var mı?;Davetli"
    assert re.fullmatch(r"\d\d\.\d\d\.\d{4} \d\d:\d\d;Ali;Geliyorum;2;Geliyoruz;Fıstık;", lines[1]), lines[1]
    assert "'=HYPERLINK(" in lines[2] and ";Gelemiyorum;;" in lines[2]   # Excel formülü çalışmasın
    assert lines[3].endswith(";Veli;Belki;;'+90 bakacağım;;")
    assert lines[4] == "Toplam;3 yanıt;2 kişi geliyor · 1 belki · 1 gelemiyor;2"
    # Tek yanıt silme; başka davetiyenin yanıtı bu adresten silinemez
    veli = answer_of(inv, "Veli")["id"]
    ADMIN.post(f"/davetiye/{iid}/yanit/{veli}/sil")
    assert not answer_of(inv, "Veli") and "“Veli” yanıtı silindi" in ADMIN.text(f"/davetiye/{iid}")
    other = made(create(ADMIN, title="Başka", calendar=""))
    ok(Visitor().answer(other["code"], name="Zeki"))
    zid = answer_of(other, "Zeki")["id"]
    assert ADMIN.post(f"/davetiye/{iid}/yanit/{zid}/sil").status_code == 404 and answer_of(other, "Zeki")
    # QR PNG
    r = ADMIN.get(f"/davetiye/{iid}/qr.png")
    assert r.status_code == 200 and r.headers["Content-Type"] == "image/png"
    assert f"davetiye-{code}-qr.png" in r.headers["Content-Disposition"] and r.get_data()[:4] == b"\x89PNG"
    r.close()
    print("  detail/csv/delete OK")


# ---------- Takvim ----------
def test_calendar_event():
    clear()
    inv = made(create(ADMIN, title="Takvimli"))
    iid, eid = inv["id"], inv["event_id"]
    assert "Takvimli" in ADMIN.text("/etkinlikler/") and "Takvimli" not in AYSE.text("/etkinlikler/")
    run("UPDATE events SET pre_sent_at = CURRENT_TIMESTAMP WHERE id = ?", (eid,))
    # Tarih / saat / yer değişince etkinlik güncellenir (aynı kayıt)
    edit(ADMIN, iid, title="Takvimli 2", starts_on="2026-10-20", starts_at="19:30", place="Ev")
    e = one("SELECT * FROM events WHERE id = ?", (eid,))
    assert invite(iid)["event_id"] == eid and (e["title"], e["date"], e["time"], e["place"]) == (
        "🎂 Takvimli 2", "2026-10-20", "19:30", "Ev")
    assert e["pre_sent_at"] is None
    # İşaret kaldırılınca etkinlik silinir; yeniden işaretlenince yenisi
    edit(ADMIN, iid, title="Takvimli 2", starts_on="2026-10-20", calendar="")
    assert invite(iid)["event_id"] is None and not one("SELECT 1 FROM events WHERE id = ?", (eid,))
    assert 'name="calendar" value="1" checked' not in ADMIN.text(f"/davetiye/{iid}/duzenle")
    edit(ADMIN, iid, title="Takvimli 2", starts_on="2026-10-20")
    eid = invite(iid)["event_id"]
    assert eid and one("SELECT date FROM events WHERE id = ?", (eid,))["date"] == "2026-10-20"
    # Etkinlik takvimden elle silindiyse rozet çıkmaz, kaydedince yeniden eklenir
    run("DELETE FROM events WHERE id = ?", (eid,))
    assert "📅 Takvimde" not in ADMIN.text(f"/davetiye/{iid}")
    edit(ADMIN, iid, title="Takvimli 2", starts_on="2026-10-20")
    new_eid = invite(iid)["event_id"]
    assert new_eid != eid and one("SELECT 1 FROM events WHERE id = ?", (new_eid,))
    assert "📅 Takvimde" in ADMIN.text(f"/davetiye/{iid}")
    print("  calendar event OK")


# ---------- Telegram ----------
def test_telegram_batch():
    clear()
    inv = made(create(ADMIN, notify="1", calendar=""))
    iid, code = inv["id"], inv["code"]
    ahmet = Visitor()
    ok(ahmet.answer(code, name="Ahmet", count=3))
    msgs = sent()
    assert len(msgs) == 1 and msgs[0]["chat_id"] == "100"
    assert f"🎉 <b>“{TITLE}”</b>: Ahmet geliyor (+2) · toplam 3 kişi geliyor" in msgs[0]["text"]
    assert f'href="http://localhost/davetiye/{iid}"' in msgs[0]["text"]
    # 10 dakika dolmadan gelenler birikir; cron süre dolunca toplu gönderir
    NOW[0] = START + timedelta(minutes=3)
    ok(Visitor().answer(code, name="Ayşe", status="no"))
    ok(Visitor().answer(code, name="Can", status="maybe"))
    assert len(sent()) == 1
    NOW[0] = START + timedelta(minutes=8)
    assert cron()["invite_notices"] == 0 and len(sent()) == 1
    NOW[0] = START + timedelta(minutes=10)
    assert cron()["invite_notices"] == 1
    assert "Ayşe gelemiyor, Can belki gelir · toplam 3 kişi geliyor, 1 belki" in sent()[-1]["text"]
    assert cron()["invite_notices"] == 0 and len(sent()) == 2
    # Değişen yanıt: "yanıtını değiştirdi"; 10 dakika geçtiyse yanıtla birlikte hemen gider
    NOW[0] = START + timedelta(minutes=21)
    ok(ahmet.answer(code, name="Ahmet", status="no"))
    assert len(sent()) == 3 and "Ahmet yanıtını değiştirdi: gelemiyor · toplam 0 kişi geliyor, 1 belki" in sent()[-1]["text"]
    # Bildirim kapalıysa mesaj yok; yeniden açılınca eski yanıtlar "yeni" sayılmaz
    edit(ADMIN, iid, notify="", calendar="")
    NOW[0] = START + timedelta(minutes=40)
    ok(Visitor().answer(code, name="Deniz"))
    assert len(sent()) == 3 and cron()["invite_notices"] == 0
    edit(ADMIN, iid, notify="1", calendar="")
    assert invite(iid)["notified_rev"] == invite(iid)["rev"]
    NOW[0] = START + timedelta(minutes=60)
    assert cron()["invite_notices"] == 0 and len(sent()) == 3
    # Telegram hatası yanıtı bozmaz; silinen yanıt bildirilmez
    FAIL[0] = True
    ok(Visitor().answer(code, name="Emre"))
    FAIL[0] = False
    assert answer_of(inv, "Emre")
    NOW[0] = START + timedelta(minutes=75)
    ok(Visitor().answer(code, name="Fatma"))
    assert "Fatma geliyor" in sent()[-1]["text"]
    count = len(sent())
    NOW[0] = START + timedelta(minutes=80)
    ok(Visitor().answer(code, name="Gül"))
    ADMIN.post(f"/davetiye/{iid}/yanit/{answer_of(inv, 'Gül')['id']}/sil")
    NOW[0] = START + timedelta(minutes=95)
    assert cron()["invite_notices"] == 0 and len(sent()) == count
    # Telegram'ı bağlı olmayan sahibi: mesaj yok
    m = made(create(MEHMET, title="Mehmet'in daveti", notify="1", calendar=""))
    ok(Visitor().answer(m["code"], name="A"))
    assert len(sent()) == count and cron()["invite_notices"] == 0
    assert "Telegram bağlı değil" in MEHMET.text(f"/davetiye/{m['id']}")
    print("  telegram batch OK")


def test_cron_summaries():
    clear()
    inv = made(create(ADMIN, title="Mangal", starts_on="2026-10-08", rsvp_deadline="2026-10-06", calendar=""))
    iid, code = inv["id"], inv["code"]
    ADMIN.post(f"/davetiye/{iid}/davetli", data={"guests": "Ahmet ve ailesi\nBerk"})
    tok = {x["label"]: x["token"] for x in rows("SELECT * FROM invite_guests WHERE invite_id = ?", (iid,))}
    ok(Visitor().answer(code, name="Ahmet", count=3, k=tok["Ahmet ve ailesi"]))
    ok(Visitor().answer(code, name="Ayşe"))
    ok(Visitor().answer(code, name="Can", status="maybe"))
    ok(Visitor().answer(code, name="Deniz", status="no"))
    m = made(create(MEHMET, title="Telegramsız", starts_on="2026-10-08", rsvp_deadline="2026-10-06", calendar=""))
    # LCV son günü geçince, varsayılan saatten sonra bir kez özet
    NOW[0] = datetime(2026, 10, 6, 23, 0, tzinfo=TZ)
    assert cron()["invite_summaries"] == 0
    NOW[0] = datetime(2026, 10, 7, 8, 59, tzinfo=TZ)
    assert cron()["invite_summaries"] == 0
    NOW[0] = datetime(2026, 10, 7, 9, 0, tzinfo=TZ)
    data = cron()
    assert data["invite_summaries"] == 1 and not data["errors"], data
    msg = sent()[-1]
    assert msg["chat_id"] == "100"
    for part in ("📋 <b>LCV süresi doldu:</b> “Mangal” · 4 kişi geliyor, 1 belki, 1 gelemiyor",
                 "📅 8 Ekim Perşembe · 15:00–18:00", "✅ Gelenler: Ahmet (+2), Ayşe", "🤔 Belki: Can",
                 "❌ Gelemeyenler: Deniz", "⏳ Yanıt vermeyen davetliler: Berk", f"/davetiye/{iid}"):
        assert part in msg["text"], part
    assert cron()["invite_summaries"] == 0 and invite(iid)["deadline_sent_for"] == "2026-10-06"
    assert invite(m["id"])["deadline_sent_for"] is None   # Telegram'ı yok
    # Bir gün önce akşam 19:00'dan sonra bir kez "Yarın"
    NOW[0] = datetime(2026, 10, 7, 18, 59, tzinfo=TZ)
    assert cron()["invite_summaries"] == 0
    NOW[0] = datetime(2026, 10, 7, 19, 0, tzinfo=TZ)
    assert cron()["invite_summaries"] == 1
    text_ = sent()[-1]["text"]
    assert text_.startswith("🎂 <b>Yarın:</b> “Mangal” · 15:00–18:00 · Neşeli Çocuk Kafe · 4 kişi geliyor, 1 belki")
    assert "✅ Gelenler: Ahmet (+2), Ayşe" in text_ and "⏳ Yanıt vermeyen davetliler: Berk" in text_
    assert cron()["invite_summaries"] == 0 and invite(iid)["eve_sent_for"] == "2026-10-08"
    # Tarih değişince yeniden kurulur
    NOW[0] = START
    edit(ADMIN, iid, title="Mangal", starts_on="2026-10-10", rsvp_deadline="2026-10-06", calendar="")
    NOW[0] = datetime(2026, 10, 9, 20, 0, tzinfo=TZ)
    assert cron()["invite_summaries"] == 1 and "Yarın:" in sent()[-1]["text"]
    assert cron()["invite_summaries"] == 0
    # Gönderilemeyen özet sonraki çağrıda yeniden denenir
    NOW[0] = START
    f = made(create(ADMIN, title="Hatalı", starts_on="2026-10-20", rsvp_deadline="2026-10-12", calendar=""))
    NOW[0] = datetime(2026, 10, 13, 10, 0, tzinfo=TZ)
    FAIL[0] = True
    data = cron()
    assert data["invite_summaries"] == 0 and data["errors"] and invite(f["id"])["deadline_sent_for"] is None
    FAIL[0] = False
    assert cron()["invite_summaries"] == 1 and invite(f["id"])["deadline_sent_for"] == "2026-10-12"
    # Cron uzun süre çalışmadıysa bitmiş etkinliğin özeti gönderilmez, sadece işaretlenir
    NOW[0] = START
    s = made(create(ADMIN, title="Eski", starts_on="2026-10-14", rsvp_deadline="2026-10-12", calendar=""))
    NOW[0] = datetime(2026, 10, 15, 10, 0, tzinfo=TZ)
    count = len(sent())
    assert cron()["invite_summaries"] == 0 and len(sent()) == count
    assert invite(s["id"])["deadline_sent_for"] == "2026-10-12"
    print("  cron summaries OK")


# ---------- Kötüye kullanım ----------
def test_honeypot_and_ip_limit():
    clear()
    inv = made(create(ADMIN, title="Sınır", notify="1", calendar=""))
    code = inv["code"]
    r = Visitor().answer(code, name="Robot", website="http://spam.example")
    assert r.status_code == 400 and "Yanıt kaydedilemedi" in text(r)
    assert not answers(inv) and not sent()
    # Davetiye başına IP başına saatte 20 yeni yanıt
    for i in range(20):
        ok(Visitor("10.9.9.9").answer(code, name=f"Kişi {i}", status="no"))
    r = Visitor("10.9.9.9").answer(code, name="Yirmi bir", status="no")
    assert r.status_code == 429 and "çok fazla yanıt verildi" in text(r)
    ok(Visitor("10.9.9.8").answer(code, name="Başka IP", status="no"))
    q = made(create(ADMIN, title="Başka davetiye", calendar=""))
    ok(Visitor("10.9.9.9").answer(q["code"], name="Aynı IP", status="no"))   # sınır davetiye başına
    hashes = {r_["ip_hash"] for r_ in answers(inv)}
    assert len(hashes) == 2 and not any("10.9.9" in x for x in hashes)   # IP düz saklanmaz
    run("UPDATE invite_responses SET created_at = datetime('now', '-61 minutes') WHERE invite_id = ?", (inv["id"],))
    ok(Visitor("10.9.9.9").answer(code, name="Bir saat sonra", status="no"))
    print("  honeypot/ip limit OK")


def test_link_preview_views():
    clear()
    inv = made(create(ADMIN, title="Görüntülenme", calendar=""))
    code = inv["code"]
    for ua in ("WhatsApp/2.23.20.0 A", "TelegramBot (like TwitterBot)", "facebookexternalhit/1.1"):
        r = RAW.get(f"/d/{code}", headers={"User-Agent": ua})
        assert r.status_code == 200 and 'property="og:title"' in text(r)   # önizleme OG etiketlerini görür
    ADMIN.get(f"/d/{code}")   # sahibi sayılmaz
    assert RAW.head(f"/d/{code}").status_code == 200
    assert invite(inv["id"])["views"] == 0
    RAW.get(f"/d/{code}", headers={"User-Agent": "Mozilla/5.0 (iPhone)"})
    assert invite(inv["id"])["views"] == 1
    v = Visitor()
    r = ok(v.answer(code, name="Ali"))   # formu açtı: +1
    v.c.get(r.headers["Location"])        # yanıt sonrası yönlendirme sayılmaz
    assert invite(inv["id"])["views"] == 2
    assert "sen ve link önizlemeleri sayılmaz" in ADMIN.text(f"/davetiye/{inv['id']}")
    print("  link preview/views OK")


# ---------- Yalıtım, çöp ----------
def test_isolation():
    clear()
    inv = made(create(ADMIN, title="Gizli davet Zeytin", place="Zeytinlik", description="zeytinyağlı"))
    iid = inv["id"]
    ok(Visitor().answer(inv["code"], name="Ali"))
    rid = answer_of(inv, "Ali")["id"]
    ADMIN.post(f"/davetiye/{iid}/davetli", data={"guests": "Veli"})
    gid = one("SELECT id FROM invite_guests WHERE invite_id = ?", (iid,))["id"]
    for url in (f"/davetiye/{iid}", f"/davetiye/{iid}/duzenle", f"/davetiye/{iid}/yanitlar.csv", f"/davetiye/{iid}/qr.png"):
        assert AYSE.get(url).status_code == 404, url
    for url in (f"/davetiye/{iid}/durum", f"/davetiye/{iid}/sil", f"/davetiye/{iid}/davetli",
                f"/davetiye/{iid}/yanit/{rid}/sil", f"/davetiye/{iid}/davetli/{gid}/sil", f"/davetiye/{iid}/duzenle"):
        assert AYSE.post(url, data=form_data(title="Hacker", guests="Hacker")).status_code == 404, url
    i2 = invite(iid)
    assert i2["closed"] == 0 and i2["title"] == "Gizli davet Zeytin" and len(answers(i2)) == 1
    assert one("SELECT COUNT(*) AS n FROM invite_guests WHERE invite_id = ?", (iid,))["n"] == 1
    assert "Zeytin" not in AYSE.text("/davetiye/") and "Zeytin" in ADMIN.text("/davetiye/")
    # Arama: sadece kendi davetiyeleri (başlık, yer, açıklama)
    for q in ("zeytin", "ZEYTİNLİK", "zeytinyagli"):
        assert "Gizli davet Zeytin" in ADMIN.text(f"/ara/?q={q}"), q
        assert "Gizli davet Zeytin" not in AYSE.text(f"/ara/?q={q}"), q
    assert "1 kişi geliyor" in ADMIN.text("/ara/?q=zeytin")
    # Ayşe'nin davetiyesi ona ait; kullanıcı silinince davetiyeleri ve yanıtları da gider
    a = made(create(AYSE, title="Ayşe'nin daveti"))
    assert a["user_id"] == AYSE_ID and ADMIN.get(f"/davetiye/{a['id']}").status_code == 404
    assert one("SELECT user_id FROM events WHERE id = ?", (a["event_id"],))["user_id"] == AYSE_ID
    ok(Visitor().answer(a["code"], name="Biri"))
    ADMIN.post(f"/yonetim/kullanicilar/{AYSE_ID}/sil")
    assert invite(a["id"]) is None and RAW.get(f"/d/{a['code']}").status_code == 404
    assert one("SELECT COUNT(*) AS n FROM invite_responses WHERE invite_id = ?", (a["id"],))["n"] == 0
    assert RAW.get(f"/d/{inv['code']}").status_code == 200
    print("  isolation OK")


def test_trash_restore():
    clear()
    r = ADMIN.post("/davetiye/yeni", data={**form_data(title="Çöpe gidecek"), "photo": (png(), "k.png")})
    inv = made(r)
    iid, code, eid, photo = inv["id"], inv["code"], inv["event_id"], inv["photo"]
    ADMIN.post(f"/davetiye/{iid}/davetli", data={"guests": "Ali\nVeli"})
    tokens = [x["token"] for x in rows("SELECT token FROM invite_guests WHERE invite_id = ? ORDER BY id", (iid,))]
    ok(Visitor().answer(code, name="Ali", count=2, k=tokens[0]))
    me = Visitor()
    ok(me.answer(code, name="Can", status="maybe"))
    r = ADMIN.post(f"/davetiye/{iid}/sil")
    assert r.status_code == 302 and "çöp kutusuna taşındı" in ADMIN.text("/davetiye/")
    assert invite(iid) is None and not one("SELECT 1 FROM events WHERE id = ?", (eid,))   # takvimden de kalktı
    for table in ("invite_guests", "invite_responses"):
        assert one(f"SELECT COUNT(*) AS n FROM {table} WHERE invite_id = ?", (iid,))["n"] == 0, table
    item = one("SELECT * FROM trash WHERE module = 'invites' ORDER BY id DESC LIMIT 1")
    assert item["label"] == "🎂 Çöpe gidecek" and '"$blob"' in item["payload"]   # fotoğraf base64 olarak
    # Silinmiş ve olmayan kod dışarıdan aynı 404; davetli linki, kapak ve .ics de açılmaz
    deleted, missing = RAW.get(f"/d/{code}"), RAW.get("/d/hicyokbu")
    assert deleted.status_code == missing.status_code == 404 and text(deleted) == text(missing)
    for url in (f"/d/{code}?k={tokens[1]}", f"/d/{code}/kapak.jpg", f"/d/{code}/davetiye.ics"):
        assert RAW.get(url).status_code == 404, url
    with app.test_request_context():
        assert code in invites._trashed_codes()   # kutudayken kod başkasına verilmez
    ADMIN.post(f"/cop-kutusu/{item['id']}/geri")
    inv = invite(iid)
    assert inv and inv["code"] == code and bytes(inv["photo"]) == bytes(photo) and inv["event_id"] == eid
    assert one("SELECT title FROM events WHERE id = ?", (eid,))["title"] == "🎂 Çöpe gidecek"
    assert len(answers(inv)) == 2 and answer_of(inv, "Ali")["guest_id"]
    assert "Bu link sana özel" in text(RAW.get(f"/d/{code}?k={tokens[0]}"))
    assert "🤔 Yanıtın: Belki" in text(me.c.get(f"/d/{code}"))   # çerezle tanınmaya devam eder
    ok(Visitor().answer(code, name="Veli", k=tokens[1]))
    assert RAW.get(f"/d/{code}/kapak.jpg").get_data() == bytes(photo)
    print("  trash/restore OK")


if __name__ == "__main__":
    test_create_validation()
    test_photo()
    test_public_page()
    test_escape()
    test_rsvp_flow()
    test_deadline_and_state()
    test_capacity()
    test_guests()
    test_visibility()
    test_detail_csv_delete()
    test_calendar_event()
    test_telegram_batch()
    test_cron_summaries()
    test_honeypot_and_ip_limit()
    test_link_preview_views()
    test_isolation()
    test_trash_restore()
    print("OK")
