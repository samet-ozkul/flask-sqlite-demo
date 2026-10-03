"""Anket: oluşturma ve doğrulama (2–20 seçenek, çoklu seçimde en fazla N, tarih anketi, bitiş), herkese açık oylama
(girişsiz; CSRF anahtarı GET'te oturuma konur), bir kişi bir oy (çerez, oturum, IP, isim), IP saatlik sınırı ve bal
tuzağı, sonuç görünürlüğünün üç modu, kapanma (elle ve bitişte cron; mesaj bir kez), kişiye özel linkler, oy geldikten
sonra düzenleme kuralı, sonuçlar / CSV / oy silme, güvenlik başlıkları ve HTML kaçışı, link önizlemesi, Telegram toplu
bildirim (10 dakika), kullanıcı yalıtımı, çöp kutusu ve geri getirme, olmayan/silinmiş kod 404.

Çalıştır: .venv/Scripts/python tests/test_polls.py
"""
import hashlib
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, csrf, make_app  # noqa: E402

app = make_app()
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"
os.environ["CRON_SECRET"] = "gizli"

import pano.telegram as tg  # noqa: E402
import pano.todo_reminders as todo  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query, query_one  # noqa: E402
from pano.modules import polls  # noqa: E402
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

BASE = {"question": "Akşam ne yiyelim?", "description": "", "kind": "single", "max_choices": "",
        "results_visibility": "after_vote", "closes_at": ""}
CHECKS = ("require_name", "one_per_ip", "invite_only", "notify")


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


def form_data(opts=(), dates=(), **fields):
    data = {**BASE, **fields}
    for key in CHECKS:
        if not data.get(key):
            data.pop(key, None)   # işaretsiz kutu formda hiç gönderilmez
    data["opt"] = list(opts)
    if dates:
        data["opt_date"] = [d for d, _t in dates]
        data["opt_time"] = [t for _d, t in dates]
    return data


def create(client, opts=("Pizza", "Döner", "Mantı"), dates=(), **fields):
    return client.post("/anket/yeni", data=form_data(opts, dates, **fields))


def edit(client, poll_id, opts=(), dates=(), **fields):
    return client.post(f"/anket/{poll_id}/duzenle", data=form_data(opts, dates, **fields))


def made(resp):
    """Oluşturulan anketin satırı."""
    assert resp.status_code == 302, (resp.status_code, text(resp)[:800])
    poll_id = int(re.search(r"/anket/(\d+)$", resp.headers["Location"]).group(1))
    return poll(poll_id)


def poll(poll_id):
    return one("SELECT * FROM polls WHERE id = ?", (poll_id,))


def opts(p):
    """{'Pizza': id} — tarih seçeneklerinde '2026-10-07' ya da '2026-10-08 19:00'."""
    return {(r["text"] or f"{r['option_date']} {r['option_time'] or ''}".strip()): r["id"]
            for r in rows("SELECT * FROM poll_options WHERE poll_id = ?", (p["id"],))}


def votes(p):
    return one("SELECT COUNT(*) AS n FROM poll_votes WHERE poll_id = ?", (p["id"],))["n"]


class Visitor:
    """Girişsiz ziyaretçi: kendi çerezleri (oturum + oy çerezi) ve IP'si."""

    def __init__(self, ip="127.0.0.1"):
        self.c = app.test_client()
        self.env = {"REMOTE_ADDR": ip}
        self.token = None

    def open(self, code, d=None, ua=None):
        r = self.c.get(f"/a/{code}" + (f"?d={d}" if d else ""), environ_base=self.env,
                       headers={"User-Agent": ua} if ua else {})
        self.token = csrf(r) or self.token   # form yoksa (oy verilmiş, kapalı) önceki anahtar geçerli
        return r

    def vote(self, code, choices, name=None, d=None, **extra):
        self.open(code, d)
        data = {"_csrf": self.token or "", "o": [str(c) for c in choices], **extra}
        if name is not None:
            data["name"] = name
        if d:
            data["d"] = d
        return self.c.post(f"/a/{code}", data=data, environ_base=self.env)


def ok(resp):
    assert resp.status_code == 303, (resp.status_code, text(resp)[:800])
    return resp


def clear():
    run("UPDATE polls SET notify = 0")   # önceki testlerin anketleri cron'da bildirim üretmesin
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
    assert "form-action 'self'" in csp and "script-src" not in csp
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Robots-Tag"] == "noindex, nofollow" and r.headers["Cache-Control"] == "no-store"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    page = text(r)
    assert '<meta name="robots" content="noindex, nofollow">' in page and "<script" not in page


# ---------- Oluşturma ----------
def test_create_validation():
    clear()
    page = ADMIN.text("/anket/")
    assert "Henüz anket yok" in page and "/anket/yeni" in page
    page = ADMIN.text("/anket/yeni")
    assert 'name="question"' in page and 'value="single" checked' in page and "🗳️ Anketi oluştur" in page
    assert page.count('name="opt"') == 20 and page.count('name="opt_date"') == 20 and "Daha fazla satır" in page
    assert 'data-show-for="kind=date"' in page and 'data-show-for="kind=multi"' in page
    cases = [
        ({"question": "  "}, "Soruyu yaz"),
        ({"opts": ("Tek", "", " ")}, "En az 2 seçenek yaz"),
        ({"opts": [f"S{i}" for i in range(21)]}, "En fazla 20 seçenek olabilir"),
        ({"opts": ("Çay", "kahve", "ÇAY")}, "Aynı seçenek iki kez yazılmış: “ÇAY”"),
        ({"kind": "multi", "max_choices": "1"}, "“En fazla kaç seçim” 2 ile 3 arasında olmalı"),
        ({"kind": "multi", "max_choices": "5"}, "“En fazla kaç seçim” 2 ile 3 arasında olmalı"),
        ({"kind": "multi", "max_choices": "abc"}, "“En fazla kaç seçim” 2 ile 3 arasında olmalı"),
        ({"kind": "date"}, "En az 2 tarih yaz"),
        ({"kind": "date", "dates": [("2026-10-09", "")]}, "En az 2 tarih yaz"),
        ({"kind": "date", "dates": [("2026-10-04", ""), ("2026-10-09", "")]},
         "1. satır: geçmiş bir gün seçilemez (4 Ekim 2026)"),
        ({"kind": "date", "dates": [("2026-10-09", ""), ("", "19:00")]}, "2. satır: tarih seç (saat tek başına yazılamaz)"),
        ({"kind": "date", "dates": [("2026-10-09", "25:00"), ("2026-10-10", "")]}, "1. satır: saat geçersiz"),
        ({"kind": "date", "dates": [("2026-10-09", "19:00"), ("2026-10-09", "19.00")]},
         "Aynı tarih ve saat iki kez yazılmış: 9 Ekim 2026 Cuma · 19:00"),
        ({"closes_at": "2026-10-05T11:59"}, "Bitiş zamanı geçmişte olamaz"),
        ({"closes_at": "yarın akşam"}, "Bitiş zamanı geçersiz"),
    ]
    for case, message in cases:
        case = {"question": "Yazdığım soru", **case}
        r = create(ADMIN, **case)
        page = text(r)
        assert r.status_code == 400 and message in page, (message, re.findall(r'class="flash[^"]*">([^<]*)', page))
        if case["question"].strip():
            assert 'value="Yazdığım soru"' in page   # yazılanlar kaybolmaz
    r = create(ADMIN, question="Yazdığım soru", kind="date", dates=[("2026-10-09", "19:00"), ("2026-10-04", "")])
    assert 'value="2026-10-09"' in text(r) and 'value="19:00"' in text(r) and 'value="date" checked' in text(r)
    assert one("SELECT COUNT(*) AS n FROM polls")["n"] == 0
    # Tek seçim: boşluklar sadeleşir, boş satırlar atlanır, sıra korunur
    p = made(create(ADMIN, question="  Akşam   ne yiyelim? ", description="Bu akşam\r\nsaat 20:00",
                    opts=("Pizza", "  Döner  kebap ", "", "Mantı"), require_name="1"))
    assert (p["question"], p["description"], p["kind"], p["require_name"], p["results_visibility"]) == (
        "Akşam ne yiyelim?", "Bu akşam\nsaat 20:00", "single", 1, "after_vote")
    assert re.fullmatch(r"[abcdefghjkmnpqrstuvwxyz23456789]{7}", p["code"]) and p["closed"] == 0
    assert [r_["text"] for r_ in rows("SELECT text FROM poll_options WHERE poll_id = ? ORDER BY sort", (p["id"],))] == [
        "Pizza", "Döner kebap", "Mantı"]
    page = ADMIN.text(f"/anket/{p['id']}")
    for part in (f"http://localhost/a/{p['code']}", 'class="qr"', "📋 Kopyala", "polls.js", "QR'ı PNG indir",
                 "Henüz oy yok", "Bir kişi bir oy nasıl sağlanıyor", "gizli pencere", "Sadece davetliler"):
        assert part in page, part
    # Çoklu seçim: seçenek sayısı kadar sınır = sınırsız
    assert made(create(ADMIN, question="Filmler", kind="multi", max_choices="3", opts=("A", "B", "C")))["max_choices"] is None
    assert made(create(ADMIN, question="Filmler 2", kind="multi", max_choices="2", opts=("A", "B", "C", "D")))[
        "max_choices"] == 2
    assert made(create(ADMIN, question="Filmler 3", kind="multi", opts=("A", "B")))["max_choices"] is None
    # Tarih anketi: isim her zaman istenir, tarihler kronolojik, aynı güne farklı saat olur; metin seçenekleri yok sayılır
    d = made(create(ADMIN, question="Toplantı günü?", kind="date", opts=("yok sayılır",),
                    dates=[("2026-10-09", "21:00"), ("2026-10-07", ""), ("", ""), ("2026-10-09", "9:30")]))
    assert d["kind"] == "date" and d["require_name"] == 1
    assert [(r_["option_date"], r_["option_time"], r_["text"]) for r_ in rows(
        "SELECT * FROM poll_options WHERE poll_id = ? ORDER BY sort", (d["id"],))] == [
        ("2026-10-07", None, ""), ("2026-10-09", "09:30", ""), ("2026-10-09", "21:00", "")]
    # Bitiş: yerel saat
    c = made(create(ADMIN, question="Bitişli", closes_at="2026-10-06T18:30"))
    assert c["closes_at"] == "2026-10-06 18:30"
    page = ADMIN.text("/anket/")
    assert "Bitişli" in page and "bitiş 6 Ekim 2026 Salı 18:30" in page and "Toplantı günü?" in page
    assert 'value="2026-10-06T18:30"' in ADMIN.text(f"/anket/{c['id']}/duzenle")
    run("DELETE FROM polls")
    print("  create/validation OK")


# ---------- Herkese açık oylama ----------
def test_public_vote():
    clear()
    p = made(create(ADMIN, question="Akşam ne yiyelim?", description="Saat 20:00", opts=("Pizza", "Döner", "Mantı")))
    code, ids = p["code"], opts(p)
    r = RAW.get(f"/a/{code}")
    page = text(r)
    assert r.status_code == 200 and "Set-Cookie" in r.headers   # formu açan ziyaretçinin oturumuna CSRF anahtarı
    assert_public_headers(r)
    for part in ("Akşam ne yiyelim?", "Saat 20:00", "Pizza", "Döner", "Mantı", 'type="radio"', "🗳️ Oy ver",
                 'name="website"', "Sonuçlar oy verince görünür", "Her tarayıcı bir kez oy verir", 'name="_csrf"'):
        assert part in page, part
    for leak in ('name="name"', 'class="results"', "Bu senin anketin", "GIZLI-NOT", "admin", "topbar"):
        assert leak not in page, leak
    # CSRF anahtarı olmadan (formu açmadan ya da başka siteden) oy verilemez
    assert app.test_client().post(f"/a/{code}", data={"o": str(ids["Pizza"])}).status_code == 400
    stranger = app.test_client()
    stranger.get(f"/a/{code}")
    assert stranger.post(f"/a/{code}", data={"_csrf": "yanlis", "o": str(ids["Pizza"])}).status_code == 400
    assert votes(p) == 0
    a = Visitor()
    r = ok(a.vote(code, [ids["Pizza"]]))
    assert r.headers["Location"].endswith(f"/a/{code}?ok=1")
    cookie = [h for h in r.headers.getlist("Set-Cookie") if h.startswith(f"anket_{code}=")][0]
    for part in ("HttpOnly", "SameSite=Lax", f"Path=/a/{code}", "Max-Age=31536000"):
        assert part in cookie, part
    token = a.c.get_cookie(f"anket_{code}", path=f"/a/{code}").value
    v = one("SELECT * FROM poll_votes WHERE poll_id = ?", (p["id"],))
    assert len(token) == 22 and v["voter_hash"] == hashlib.sha256(token.encode()).hexdigest()   # çerez düz saklanmaz
    assert len(v["ip_hash"]) == 32 and "127.0.0.1" not in v["ip_hash"] and v["session_hash"] and v["voter_name"] == ""
    assert [r_["option_id"] for r_ in rows("SELECT option_id FROM poll_vote_choices WHERE vote_id = ?", (v["id"],))] == [
        ids["Pizza"]]
    page = text(a.c.get(f"/a/{code}?ok=1"))
    for part in ("Teşekkürler, oyun kaydedildi", "Oyunu verdin</b>: Pizza", 'class="results"', "1 oy", "%100", "🏆 Pizza"):
        assert part in page, part
    assert "🗳️ Oy ver" not in page and 'name="_csrf"' not in page
    page = text(a.c.get(f"/a/{code}"))   # teşekkür sadece yönlendirmede
    assert "Teşekkürler" not in page and "Oyunu verdin" in page
    # Aynı tarayıcıdan ikinci oy reddedilir; verilen oy değiştirilemez
    r = a.vote(code, [ids["Döner"]])
    assert r.status_code == 409 and "Bu tarayıcıdan zaten oy verildi" in text(r)
    # Oy çerezi silinse de aynı tarayıcı oturumu (ör. çift tıklama) ikinci oyu veremez
    a.c.delete_cookie(f"anket_{code}", path=f"/a/{code}")
    assert "Oyunu verdin" in text(a.c.get(f"/a/{code}"))
    r = a.vote(code, [ids["Döner"]])
    assert r.status_code == 409 and "Bu tarayıcıdan zaten oy verildi" in text(r)
    assert votes(p) == 1
    # Çerezsiz yeni istemci (başka tarayıcı / cihaz) oy verebilir
    ok(Visitor().vote(code, [ids["Döner"]]))
    assert votes(p) == 2
    # Doğrulama: tek seçimde tam bir seçenek; başka anketin seçeneği sayılmaz
    other = made(create(ADMIN, question="Başka", opts=("X", "Y")))
    c = Visitor()
    for choices in ([], [ids["Pizza"], ids["Döner"]], [opts(other)["X"]], ["abc"]):
        r = c.vote(code, choices)
        assert r.status_code == 400 and "Bir seçenek seç" in text(r), choices
    r = c.vote(code, [ids["Mantı"], ids["Mantı"]])   # aynı seçenek iki kez gönderilse de tek sayılır
    ok(r)
    assert votes(p) == 3 and one("SELECT COUNT(*) AS n FROM poll_vote_choices c JOIN poll_votes v ON v.id = c.vote_id"
                                 " WHERE v.poll_id = ?", (p["id"],))["n"] == 3
    # Hata sonrası seçim korunur
    r = Visitor().vote(code, [ids["Döner"]], website="robot")
    assert r.status_code == 400 and re.search(rf'value="{ids["Döner"]}"\s+checked', text(r))
    assert not re.search(rf'value="{ids["Pizza"]}"\s+checked', text(r))
    # Adreste büyük harf: küçük harfli adrese yönlendirilir (oy çerezinin yolu ona bağlı); davet anahtarı korunur
    r = RAW.get(f"/a/{code.upper()}")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/a/{code}")
    assert RAW.get(f"/a/{code.upper()}?d=abc").headers["Location"].endswith(f"/a/{code}?d=abc")
    # Sahibi: şerit, sonuçlar her zaman; kendi linkinden verdiği oy sayılır
    page = text(ADMIN.get(f"/a/{code}"))
    assert "Bu senin anketin" in page and 'class="results"' in page and "3 oy" in page and "🗳️ Oy ver" in page
    assert "sen ve oy verenler" in page
    ok(ADMIN.post(f"/a/{code}", data={"o": str(ids["Mantı"])}))
    assert votes(p) == 4 and "Oyunu verdin</b>: Mantı" in text(ADMIN.get(f"/a/{code}"))
    print("  public vote OK")


def test_ip_and_name():
    clear()
    p = made(create(ADMIN, question="Toplantı nerede?", opts=("Ofis", "Kafe"), one_per_ip="1", require_name="1"))
    code, ids = p["code"], opts(p)
    page = text(RAW.get(f"/a/{code}"))
    assert 'name="name"' in page and "Adın sadece anketi oluşturana görünür" in page
    a = Visitor("10.0.0.1")
    for name in ("", "   "):
        r = a.vote(code, [ids["Ofis"]], name=name)
        assert r.status_code == 400 and "Adını yaz" in text(r)
    ok(a.vote(code, [ids["Ofis"]], name="  Şule   Çiçek "))
    v = one("SELECT * FROM poll_votes WHERE poll_id = ?", (p["id"],))
    assert v["voter_name"] == "Şule Çiçek" and v["name_key"] == "sule cicek"
    # Aynı internet bağlantısından ikinci oy (başka tarayıcı da olsa)
    r = Visitor("10.0.0.1").vote(code, [ids["Kafe"]], name="Ahmet")
    assert r.status_code == 409 and "Bu internet bağlantısından (IP) zaten oy verilmiş" in text(r)
    # Aynı isimle ikinci oy: büyük/küçük ve Türkçe harf duyarsız
    for same in ("şule çiçek", "SULE CICEK", "ŞULE ÇİÇEK", "sule  cicek"):
        r = Visitor("10.0.0.2").vote(code, [ids["Kafe"]], name=same)
        assert r.status_code == 409 and "adıyla zaten oy verilmiş" in text(r), same
        assert "soyadının baş harfini ekle" in text(r) and 'name="name"' in text(r)
    ok(Visitor("10.0.0.2").vote(code, [ids["Kafe"]], name="Şule K."))
    assert votes(p) == 2
    # Kural kapalı ankette aynı IP'den farklı tarayıcılar oy verebilir; isim istenmiyorsa isim alanı yok
    q = made(create(ADMIN, question="IP serbest", opts=("A", "B")))
    ok(Visitor("10.0.0.1").vote(q["code"], [opts(q)["A"]], name="yok sayılır"))
    ok(Visitor("10.0.0.1").vote(q["code"], [opts(q)["B"]]))
    assert votes(q) == 2 and not one("SELECT 1 FROM poll_votes WHERE poll_id = ? AND voter_name != ''", (q["id"],))
    print("  ip/name OK")


def test_ip_limit_and_honeypot():
    clear()
    p = made(create(ADMIN, question="Sınır", opts=("A", "B"), notify="1"))
    code, ids = p["code"], opts(p)
    # Bal tuzağı: insanlar görmez, robot doldurur -> kaydedilmez, bildirim yok
    r = Visitor().vote(code, [ids["A"]], website="http://spam.example")
    assert r.status_code == 400 and "Oy kaydedilemedi" in text(r)
    assert votes(p) == 0 and not sent()
    # Anket başına IP başına saatte 30 oy
    for _ in range(30):
        ok(Visitor("10.1.1.1").vote(code, [ids["A"]]))
    r = Visitor("10.1.1.1").vote(code, [ids["B"]])
    assert r.status_code == 429 and "çok fazla oy verildi" in text(r)
    ok(Visitor("10.1.1.2").vote(code, [ids["B"]]))   # başka IP etkilenmez
    q = made(create(ADMIN, question="Başka anket", opts=("A", "B")))
    ok(Visitor("10.1.1.1").vote(q["code"], [opts(q)["A"]]))   # sınır anket başına
    hashes = {r_["ip_hash"] for r_ in rows("SELECT ip_hash FROM poll_votes WHERE poll_id = ?", (p["id"],))}
    assert len(hashes) == 2 and not any("10.1.1" in h for h in hashes)   # IP düz saklanmaz
    run("UPDATE poll_votes SET created_at = datetime('now', '-61 minutes') WHERE poll_id = ?", (p["id"],))
    ok(Visitor("10.1.1.1").vote(code, [ids["B"]]))   # bir saat geçince yeniden
    assert votes(p) == 32
    print("  ip limit/honeypot OK")


def test_visibility():
    clear()
    made_polls = {vis: made(create(ADMIN, question=f"Görünürlük {vis}", opts=("Evet", "Hayır"), results_visibility=vis))
                  for vis in ("always", "after_vote", "owner")}
    # Herkes, her zaman: oy vermeden de
    p = made_polls["always"]
    ok(Visitor().vote(p["code"], [opts(p)["Evet"]]))
    page = text(RAW.get(f"/a/{p['code']}"))
    assert 'class="results"' in page and "%100" in page and "🗳️ Oy ver" in page
    # Oy verdikten sonra (kapanınca herkes)
    p = made_polls["after_vote"]
    page = text(RAW.get(f"/a/{p['code']}"))
    assert 'class="results"' not in page and "Sonuçlar oy verince görünür" in page
    v = Visitor()
    ok(v.vote(p["code"], [opts(p)["Hayır"]]))
    assert 'class="results"' in text(v.c.get(f"/a/{p['code']}"))
    assert 'class="results"' not in text(RAW.get(f"/a/{p['code']}"))
    ADMIN.post(f"/anket/{p['id']}/durum")
    page = text(RAW.get(f"/a/{p['code']}"))
    assert "Anket kapandı" in page and 'class="results"' in page and "%100" in page and "🗳️ Oy ver" not in page
    # Sadece ben: oy veren de, kapanınca da göremez; sahibi görür
    p = made_polls["owner"]
    page = text(RAW.get(f"/a/{p['code']}"))
    assert 'class="results"' not in page and "Sonuçlar oy verince" not in page
    v = Visitor()
    ok(v.vote(p["code"], [opts(p)["Evet"]]))
    page = text(v.c.get(f"/a/{p['code']}?ok=1"))
    assert "Oyunu verdin" in page and "Sonuçları sadece anketi oluşturan görüyor" in page
    assert 'class="results"' not in page and "%100" not in page
    page = text(ADMIN.get(f"/a/{p['code']}"))
    assert 'class="results"' in page and "Sonuçları sadece sen görüyorsun" in page
    ADMIN.post(f"/anket/{p['id']}/durum")
    page = text(RAW.get(f"/a/{p['code']}"))
    assert "Anket kapandı" in page and "Sonuçları sadece anketi oluşturan görüyor" in page
    assert 'class="results"' not in page and "%100" not in page
    assert "1 oy" in ADMIN.text(f"/anket/{p['id']}")
    print("  visibility OK")


def test_closing():
    clear()
    p = made(create(ADMIN, question="Kapanış", opts=("Pizza", "Döner"), closes_at="2026-10-05T13:00"))
    code, ids = p["code"], opts(p)
    assert "⏳ Son oy: 5 Ekim 2026 Pazartesi 13:00" in text(RAW.get(f"/a/{code}"))
    for choice in ("Pizza", "Pizza", "Döner"):
        ok(Visitor().vote(code, [ids[choice]]))
    # Elle kapat / yeniden aç
    r = ADMIN.post(f"/anket/{p['id']}/durum")
    assert r.status_code == 302 and poll(p["id"])["closed"] == 1
    assert "Anket kapatıldı" in ADMIN.text(f"/anket/{p['id']}")
    page = text(RAW.get(f"/a/{code}"))
    assert "Anket kapandı" in page and "🗳️ Oy ver" not in page and 'name="_csrf"' not in page
    v = Visitor()
    v.token = csrf(v.c.get(f"/a/{made(create(ADMIN, question='Anahtar için', opts=('A', 'B')))['code']}"))
    r = v.vote(code, [ids["Pizza"]])
    assert r.status_code == 409 and "Anket kapandı; artık oy verilemez" in text(r)
    assert "Kapandı" in ADMIN.text("/anket/")
    ADMIN.post(f"/anket/{p['id']}/durum")
    assert poll(p["id"])["closed"] == 0 and poll(p["id"])["closes_at"] == "2026-10-05 13:00"   # gelecekteki bitiş kalır
    assert not sent()   # elle kapatınca mesaj yok
    # Bitiş zamanı geçince sayfa hemen kapalı (cron beklemeden); cron bir kez mesaj gönderip kapalı işaretler
    t = made(create(ADMIN, question="Berabere", opts=("Çay", "Kahve"), closes_at="2026-10-05T12:30"))
    for choice in ("Çay", "Kahve"):
        ok(Visitor().vote(t["code"], [opts(t)[choice]]))
    e = made(create(ADMIN, question="Boş kalan", opts=("A", "B"), closes_at="2026-10-05T12:30"))
    d = made(create(ADMIN, question="Hangi gün?", kind="date", closes_at="2026-10-05T12:30",
                    dates=[("2026-10-07", ""), ("2026-10-08", "19:00")]))
    ok(Visitor().vote(d["code"], list(opts(d).values()), name="Ali"))
    ok(Visitor().vote(d["code"], [opts(d)["2026-10-08 19:00"]], name="Veli"))
    m = made(create(MEHMET, question="Mehmet'in anketi", opts=("A", "B"), closes_at="2026-10-05T12:30"))
    NOW[0] = START + timedelta(hours=1, minutes=1)
    page = text(RAW.get(f"/a/{code}"))
    assert "Anket kapandı</b> (5 Ekim 2026 Pazartesi 13:00)" in page and "🗳️ Oy ver" not in page
    assert 'class="results"' in page   # oy verdikten sonra modunda kapanınca herkes görür
    r = v.vote(code, [ids["Pizza"]])
    assert r.status_code == 409 and "Anket kapandı" in text(r)
    assert poll(p["id"])["closed"] == 0
    data = cron()
    assert data["polls_closed"] == 5 and not data["errors"], data
    assert all(poll(x["id"])["closed"] == 1 for x in (p, t, e, d, m))
    msgs = {m_["text"].split("\n")[0]: m_ for m_ in sent()}
    assert len(sent()) == 4 and all(m_["chat_id"] == "100" for m_ in sent())   # Mehmet'in Telegram'ı yok
    text_of = {k.split("“")[1].split("”")[0]: v_["text"] for k, v_ in msgs.items()}
    assert "🔒 <b>Anket kapandı:</b> “Kapanış”" in text_of["Kapanış"]
    assert "🏆 Kazanan: Pizza (%67) · toplam 3 oy" in text_of["Kapanış"] and f"/anket/{p['id']}" in text_of["Kapanış"]
    assert "🏆 Berabere: Çay, Kahve (%50) · toplam 2 oy" in text_of["Berabere"]
    assert "Hiç oy verilmedi." in text_of["Boş kalan"]
    assert "🏆 En uygun: 8 Ekim 2026 Perşembe · 19:00 (2/2 kişi) · toplam 2 oy" in text_of["Hangi gün?"]
    data = cron()   # ikinci çağrı: mesaj yok
    assert data["polls_closed"] == 0 and len(sent()) == 4
    # Yeniden açınca geçmiş bitiş kaldırılır (yoksa cron hemen yeniden kapatırdı)
    ADMIN.post(f"/anket/{p['id']}/durum")
    assert (poll(p["id"])["closed"], poll(p["id"])["closes_at"]) == (0, None)
    assert "Bitiş zamanı geçtiği için kaldırıldı" in ADMIN.text(f"/anket/{p['id']}")
    assert "🗳️ Oy ver" in text(RAW.get(f"/a/{code}"))
    assert cron()["polls_closed"] == 0
    # Gönderilemeyen kapanış mesajı sonraki çağrıda yeniden denenir
    f = made(create(ADMIN, question="Hatalı", opts=("A", "B"), closes_at="2026-10-05T13:30"))
    NOW[0] = START + timedelta(hours=2)
    FAIL[0] = True
    data = cron()
    assert data["polls_closed"] == 0 and data["errors"] and poll(f["id"])["closed"] == 0
    FAIL[0] = False
    assert cron()["polls_closed"] == 1 and poll(f["id"])["closed"] == 1
    print("  closing OK")


# ---------- Kişiye özel linkler ----------
def test_invites():
    clear()
    p = made(create(ADMIN, question="Kim gelecek?", opts=("Evet", "Hayır"), invite_only="1", require_name="1"))
    code, ids, pid = p["code"], opts(p), p["id"]
    page = ADMIN.text(f"/anket/{pid}")
    assert "Kişiye özel linkler" in page and "Linkleri oluştur" in page and "🔑 Sadece davetliler" in page
    r = ADMIN.post(f"/anket/{pid}/davet", data={"labels": "Ahmet\n  \n  Ayşe   Kaya \n", "count": "1"})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/anket/{pid}#davet")
    invites = rows("SELECT * FROM poll_invites WHERE poll_id = ? ORDER BY id", (pid,))
    assert [i["label"] for i in invites] == ["Ahmet", "Ayşe Kaya", ""] and all(len(i["token"]) == 16 for i in invites)
    tok = {i["label"] or "Davetli 3": i["token"] for i in invites}
    page = ADMIN.text(f"/anket/{pid}")
    assert "3 kişiye özel link hazır" in page and "0/3 oy verdi" in page and "📋 Hepsini kopyala" in page
    for name, t in tok.items():
        assert f"{name}: http://localhost/a/{code}?d={t}" in page, name
    # Ortak link oy almaz
    page = text(RAW.get(f"/a/{code}"))
    assert "sadece kişiye özel linklerle oy verilebilir" in page and "🗳️ Oy ver" not in page
    ahmet = Visitor()
    page = text(ahmet.open(code, d=tok["Ahmet"]))
    assert "🗳️ Oy ver" in page and 'value="Ahmet"' in page and f'name="d" value="{tok["Ahmet"]}"' in page
    assert "Bu link sana özel" in page
    r = ahmet.vote(code, [ids["Evet"]], name="Birisi")   # anahtarsız: ortak link
    assert r.status_code == 403 and "sadece kişiye özel linklerle" in text(r)
    # Kişiye özel link bir kez oy verir
    r = ok(ahmet.vote(code, [ids["Evet"]], name="Ahmet", d=tok["Ahmet"]))
    assert "ok=1" in r.headers["Location"] and f"d={tok['Ahmet']}" in r.headers["Location"]
    inv = one("SELECT * FROM poll_invites WHERE token = ?", (tok["Ahmet"],))
    vote = one("SELECT * FROM poll_votes WHERE poll_id = ?", (pid,))
    assert inv["used_at"] and vote["invite_id"] == inv["id"] and vote["voter_name"] == "Ahmet"
    page = text(ahmet.c.get(f"/a/{code}?ok=1&d={tok['Ahmet']}"))
    assert "Teşekkürler" in page and "Bu linkle oy verildi</b>: Evet" in page
    other = Visitor()
    page = text(other.open(code, d=tok["Ahmet"]))   # başka cihazdan da
    assert "Bu linkle oy verildi" in page and "🗳️ Oy ver" not in page
    other.open(code, d=tok["Ayşe Kaya"])   # form anahtarı için
    r = other.vote(code, [ids["Hayır"]], name="Ahmet 2", d=tok["Ahmet"])
    assert r.status_code == 409 and "Bu kişiye özel linkle zaten oy verildi" in text(r)
    # Aynı telefondan başka davetli: davet linkinde çerez/IP kontrolü yok
    ok(ahmet.vote(code, [ids["Hayır"]], name="Ayşe Kaya", d=tok["Ayşe Kaya"]))
    assert votes(p) == 2 and "2/3 oy verdi" in ADMIN.text(f"/anket/{pid}")
    # İptal edilen link çalışmaz
    third = one("SELECT id FROM poll_invites WHERE token = ?", (tok["Davetli 3"],))["id"]
    ADMIN.post(f"/anket/{pid}/davet/{third}/sil")
    assert one("SELECT 1 FROM poll_invites WHERE id = ?", (third,)) is None
    assert "Kişiye özel link iptal edildi" in ADMIN.text(f"/anket/{pid}")
    r = RAW.get(f"/a/{code}?d={tok['Davetli 3']}")
    assert r.status_code == 403 and "geçersiz ya da iptal edilmiş" in text(r) and "🗳️ Oy ver" not in text(r)
    assert_public_headers(r)
    r = other.vote(code, [ids["Evet"]], name="Üçüncü", d=tok["Davetli 3"])
    assert r.status_code == 403 and votes(p) == 2
    assert RAW.get(f"/a/{code}?d=uydurma-anahtar").status_code == 403
    assert RAW.get(f"/a/{code}?d=" + "x" * 100).status_code == 403
    # Kullanılmış link iptal edilemez
    ADMIN.post(f"/anket/{pid}/davet/{inv['id']}/sil")
    assert one("SELECT 1 FROM poll_invites WHERE id = ?", (inv["id"],))
    assert "Bu linkle oy verilmiş; iptal edilemez" in ADMIN.text(f"/anket/{pid}")
    # Oy silinince kişiye özel link yeniden kullanılabilir
    ADMIN.post(f"/anket/{pid}/oy/{vote['id']}/sil")
    assert one("SELECT used_at FROM poll_invites WHERE id = ?", (inv["id"],))["used_at"] is None
    assert "Kişiye özel linki yeniden oy verebilir" in ADMIN.text(f"/anket/{pid}")
    ok(Visitor().vote(code, [ids["Hayır"]], name="Ahmet", d=tok["Ahmet"]))
    # Sınırlar ve boş form
    ADMIN.post(f"/anket/{pid}/davet", data={"labels": "", "count": ""})
    assert "Her satıra bir kişi yaz" in ADMIN.text(f"/anket/{pid}")
    ADMIN.post(f"/anket/{pid}/davet", data={"count": "500"})
    assert "en fazla 100 kişiye özel link" in ADMIN.text(f"/anket/{pid}")
    assert one("SELECT COUNT(*) AS n FROM poll_invites WHERE poll_id = ?", (pid,))["n"] == 2
    ADMIN.post(f"/anket/{pid}/davet", data={"labels": "X" * 100})
    assert one("SELECT label FROM poll_invites WHERE poll_id = ? ORDER BY id DESC", (pid,))["label"] == "X" * 40
    # "Sadece davetliler" kapalıyken de davet linki tek kullanımlık; ortak link oy alır
    q = made(create(ADMIN, question="Karma", opts=("A", "B")))
    page = ADMIN.text(f"/anket/{q['id']}")
    assert "Kişiye özel linkler" not in page and "Bir kişi bir oy" in page
    ADMIN.post(f"/anket/{q['id']}/davet", data={"labels": "Can"})
    assert "ortak link de oy almaya devam ediyor" in ADMIN.text(f"/anket/{q['id']}")
    can = one("SELECT token FROM poll_invites WHERE poll_id = ?", (q["id"],))["token"]
    ok(Visitor().vote(q["code"], [opts(q)["A"]], d=can))
    ok(Visitor().vote(q["code"], [opts(q)["B"]]))
    v = Visitor()
    v.open(q["code"])   # form anahtarı ortak linkten
    r = v.vote(q["code"], [opts(q)["B"]], d=can)
    assert r.status_code == 409 and "Bu kişiye özel linkle zaten oy verildi" in text(r)
    print("  invites OK")


# ---------- Düzenleme ----------
def test_edit_rules():
    clear()
    p = made(create(ADMIN, question="Düzenle", opts=("A", "B", "C")))
    pid = p["id"]
    page = ADMIN.text(f"/anket/{pid}/duzenle")
    assert 'value="A"' in page and "🔒 Bu ankete" not in page and 'value="single" checked' in page
    # Oy yokken her şey değişir: seçenekler yeniden yazılır, tür değişebilir
    r = edit(ADMIN, pid, opts=("C", "a", "X"), question="Düzenlendi", kind="multi", max_choices="2")
    assert r.status_code == 302
    p = poll(pid)
    assert (p["question"], p["kind"], p["max_choices"]) == ("Düzenlendi", "multi", 2)
    assert [r_["text"] for r_ in rows("SELECT text FROM poll_options WHERE poll_id = ? ORDER BY sort", (pid,))] == [
        "C", "a", "X"]
    r = edit(ADMIN, pid, opts=("C",), question="Yarım")
    assert r.status_code == 400 and "En az 2 seçenek" in text(r) and poll(pid)["question"] == "Düzenlendi"
    # Oy geldikten sonra: tür, sınır, isim kuralı ve mevcut seçenekler değişmez; sadece yeni seçenek eklenir
    ids = opts(p)
    ok(Visitor().vote(p["code"], [ids["C"], ids["X"]]))
    vote_id = one("SELECT id FROM poll_votes WHERE poll_id = ?", (pid,))["id"]
    page = ADMIN.text(f"/anket/{pid}/duzenle")
    assert "🔒 Bu ankete 1 oy verildi" in page and "<li>C</li><li>a</li><li>X</li>" in page
    assert 'value="C"' not in page and 'name="kind"' not in page and "Yeni seçenekler" in page
    assert page.count('name="opt"') == 17 and 'name="opt_date"' not in page and "en fazla 2 seçim" in page
    before = [dict(r_) for r_ in rows("SELECT * FROM poll_options WHERE poll_id = ? ORDER BY sort", (pid,))]
    r = edit(ADMIN, pid, opts=("Y", "x "), question="Yeni soru")
    assert r.status_code == 400 and "Aynı seçenek iki kez yazılmış: “x”" in text(r) and 'value="Y"' in text(r)
    r = edit(ADMIN, pid, opts=[f"Z{i}" for i in range(18)], question="Yeni soru")
    assert r.status_code == 400 and "En fazla 20 seçenek" in text(r)
    r = edit(ADMIN, pid, opts=("Y",), question="Yeni soru", kind="date", max_choices="3", require_name="1",
             results_visibility="always")
    assert r.status_code == 302 and "1 yeni seçenek eklendi" in ADMIN.text(f"/anket/{pid}")
    p = poll(pid)
    assert (p["question"], p["kind"], p["max_choices"], p["require_name"], p["results_visibility"]) == (
        "Yeni soru", "multi", 2, 0, "always")
    after = [dict(r_) for r_ in rows("SELECT * FROM poll_options WHERE poll_id = ? ORDER BY sort", (pid,))]
    assert after[:3] == before and after[3]["text"] == "Y" and after[3]["after_vote_id"] == vote_id
    assert edit(ADMIN, pid, question="Sadece soru").status_code == 302 and len(opts(poll(pid))) == 4   # ekleme şart değil
    # Bu arada oy gelse de yeniden yazma yerine ekleme olur (kilit kontrolü kayıt anında)
    q = made(create(ADMIN, question="Yarış", opts=("A", "B")))
    ok(Visitor().vote(q["code"], [opts(q)["A"]]))
    r = edit(ADMIN, q["id"], opts=("Tamamen", "Başka"), question="Yarış")
    assert r.status_code == 302
    assert sorted(opts(poll(q["id"]))) == ["A", "B", "Başka", "Tamamen"]
    # Oy silinince düzenleme yeniden serbest
    for v in rows("SELECT id FROM poll_votes WHERE poll_id = ?", (pid,)):
        ADMIN.post(f"/anket/{pid}/oy/{v['id']}/sil")
    page = ADMIN.text(f"/anket/{pid}/duzenle")
    assert "🔒 Bu ankete" not in page and 'value="Y"' in page and 'value="multi" checked' in page
    print("  edit rules OK")


# ---------- Sonuçlar ----------
def test_results_csv_delete():
    clear()
    p = made(create(ADMIN, question="Hangi diziler?", kind="multi", max_choices="2", require_name="1",
                    opts=("Dark", "Lost", "Fargo")))
    code, ids, pid = p["code"], opts(p), p["id"]
    page = text(RAW.get(f"/a/{code}"))
    assert 'type="checkbox"' in page and "En fazla 2 seçenek işaretleyebilirsin" in page
    r = Visitor().vote(code, list(ids.values()), name="Ali")
    assert r.status_code == 400 and "En fazla 2 seçenek işaretleyebilirsin" in text(r)
    r = Visitor().vote(code, [], name="Ali")
    assert r.status_code == 400 and "En az bir seçenek seç" in text(r)
    ok(Visitor().vote(code, [ids["Dark"], ids["Lost"]], name="Ali"))
    ok(Visitor().vote(code, [ids["Dark"]], name='=HYPERLINK("http://x")'))
    ok(Visitor().vote(code, [ids["Fargo"], ids["Dark"]], name="Veli"))
    page = ADMIN.text(f"/anket/{pid}")
    for part in ("Kim neye oy verdi", "<td>Ali</td>", "<td>Dark, Lost</td>", "<td>Dark, Fargo</td>", "🏆 Dark",
                 "3 oy · %100", "1 oy · %33", "Yüzde, oy verenlerin kaçının", "⬇️ CSV"):
        assert part in page, part
    r = ADMIN.get(f"/anket/{pid}/sonuclar.csv")
    body = text(r)
    assert r.headers["Content-Type"].startswith("text/csv") and f"anket-{code}.csv" in r.headers["Content-Disposition"]
    lines = body.lstrip("\ufeff").split("\r\n")
    assert body.startswith("\ufeff") and lines[0] == "Zaman;İsim;Dark;Lost;Fargo"
    assert re.fullmatch(r"\d\d\.\d\d\.\d{4} \d\d:\d\d;Ali;✓;✓;", lines[1]), lines[1]
    assert "'=HYPERLINK(" in lines[2] and lines[2].endswith(";✓;;")   # Excel formülü çalışmasın
    assert lines[3].endswith(";Veli;✓;;✓") and lines[4] == "Toplam;3 oy;3;1;1"
    # Tek oy silme (spam); başka anketin oyu bu adresten silinemez
    veli = one("SELECT id FROM poll_votes WHERE poll_id = ? AND voter_name = 'Veli'", (pid,))["id"]
    ADMIN.post(f"/anket/{pid}/oy/{veli}/sil")
    assert votes(p) == 2 and "Oy silindi" in ADMIN.text(f"/anket/{pid}")
    assert one("SELECT COUNT(*) AS n FROM poll_vote_choices WHERE vote_id = ?", (veli,))["n"] == 0
    q = made(create(ADMIN, question="Başka", opts=("A", "B")))
    ok(Visitor().vote(q["code"], [opts(q)["A"]]))
    qv = one("SELECT id FROM poll_votes WHERE poll_id = ?", (q["id"],))["id"]
    assert ADMIN.post(f"/anket/{pid}/oy/{qv}/sil").status_code == 404 and votes(q) == 1
    # İsimsiz ankette oylar katlanır listede
    page = ADMIN.text(f"/anket/{q['id']}")
    assert "İsimsiz oylar" in page and "Kim neye oy verdi" not in page
    # Tarih anketi: isim × tarih ızgarası, en uygun gün vurgusu; hiçbiri uymuyor da bir oy
    d = made(create(ADMIN, question="Toplantı günü?", kind="date",
                    dates=[("2026-10-07", ""), ("2026-10-08", "19:00"), ("2026-10-09", "")]))
    dids = opts(d)
    page = text(RAW.get(f"/a/{d['code']}"))
    for part in ("7 Ekim 2026 Çarşamba", "8 Ekim 2026 Perşembe · 19:00", 'type="checkbox"', 'name="name"',
                 "Hiçbiri uymuyorsa", "Uygun olduğun günlerin hepsini işaretle"):
        assert part in page, part
    ok(Visitor().vote(d["code"], [dids["2026-10-07"], dids["2026-10-08 19:00"]], name="Ali"))
    ok(Visitor().vote(d["code"], [dids["2026-10-08 19:00"]], name="Ayşe"))
    can = Visitor()
    ok(can.vote(d["code"], [], name="Can"))
    assert "Oyunu verdin</b>: hiçbir gün uygun değil" in text(can.c.get(f"/a/{d['code']}"))
    page = ADMIN.text(f"/anket/{d['id']}")
    for part in ("pl-grid", "8 Eki Per 19:00", "7 Eki Çar", "⭐ 8 Ekim 2026 Perşembe · 19:00",
                 "En uygun:</b> 8 Ekim 2026 Perşembe · 19:00 (2/3 kişi)", "2 kişi · %67", '<th class="best">2</th>'):
        assert part in page, part
    assert page.count('class="yes') == 3
    # Oy geldikten sonra eklenen tarih: önceki oy verenlere sorulmadı
    assert edit(ADMIN, d["id"], dates=[("2026-10-10", "")], question="Toplantı günü?").status_code == 302
    page = ADMIN.text(f"/anket/{d['id']}")
    assert page.count('title="Bu tarih bu oydan sonra eklendi"') == 3 and "10 Eki Cmt" in page
    ok(Visitor().vote(d["code"], [opts(poll(d["id"]))["2026-10-10"]], name="Deniz"))
    assert ADMIN.text(f"/anket/{d['id']}").count('title="Bu tarih bu oydan sonra eklendi"') == 3
    lines = text(ADMIN.get(f"/anket/{d['id']}/sonuclar.csv")).lstrip("\ufeff").split("\r\n")
    assert lines[0].startswith("Zaman;İsim;7 Ekim 2026 Çarşamba;8 Ekim 2026 Perşembe · 19:00")
    # QR PNG
    r = ADMIN.get(f"/anket/{pid}/qr.png")
    assert r.status_code == 200 and r.headers["Content-Type"] == "image/png"
    assert f"anket-{code}-qr.png" in r.headers["Content-Disposition"] and r.get_data()[:4] == b"\x89PNG"
    r.close()
    print("  results/csv/delete OK")


# ---------- Güvenlik ----------
def test_escape_and_headers():
    clear()
    p = made(create(ADMIN, question="<script>alert(1)</script> ne?", description="<b>kalın</b> & not",
                    opts=("<img src=x onerror=alert(1)>", "Normal"), require_name="1", notify="1",
                    results_visibility="always"))
    code, ids = p["code"], opts(p)
    r = RAW.get(f"/a/{code}")
    page = text(r)
    assert_public_headers(r)
    assert "&lt;script&gt;alert(1)&lt;/script&gt; ne?" in page and "&lt;b&gt;kalın&lt;/b&gt; &amp; not" in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page and "<img" not in page and "<b>kalın" not in page
    ok(Visitor().vote(code, [ids["<img src=x onerror=alert(1)>"]], name="<i>Kötü</i> \"isim\""))
    page = ADMIN.text(f"/anket/{p['id']}")
    assert "&lt;i&gt;Kötü&lt;/i&gt; &#34;isim&#34;" in page and "<i>Kötü" not in page and "<script>alert(1)" not in page
    msg = sent()[-1]["text"]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in msg and "<script>" not in msg and "&lt;img src=x" in msg
    # Hata sayfası, oy sonrası sayfa ve 404 de başlıklı
    r = Visitor().vote(code, [], name="X")
    assert r.status_code == 400
    assert_public_headers(r)
    v = Visitor()
    r = ok(v.vote(code, [ids["Normal"]], name="Y"))
    assert r.headers["Cache-Control"] == "no-store" and r.headers["Referrer-Policy"] == "no-referrer"
    assert_public_headers(v.c.get(f"/a/{code}?ok=1"))
    missing = RAW.get("/a/yokbuyok")
    assert missing.status_code == 404 and missing.headers["Cache-Control"] == "no-store"
    assert missing.headers["X-Robots-Tag"] == "noindex, nofollow" and missing.headers["Referrer-Policy"] == "no-referrer"
    assert RAW.get("/a/" + "x" * 300).status_code == 404
    assert RAW.post("/a/yokbuyok", data={"o": "1"}).status_code in (400, 404)
    # Yönetim sayfaları girişsiz açılmaz
    for url in ("/anket/", "/anket/yeni", f"/anket/{p['id']}", f"/anket/{p['id']}/sonuclar.csv"):
        assert RAW.get(url).status_code == 302, url
    print("  escape/headers OK")


def test_link_preview_views():
    clear()
    p = made(create(ADMIN, question="Görüntülenme", opts=("A", "B")))
    code = p["code"]
    for ua in ("WhatsApp/2.23.20.0 A", "TelegramBot (like TwitterBot)", "facebookexternalhit/1.1"):
        assert RAW.get(f"/a/{code}", headers={"User-Agent": ua}).status_code == 200
    ADMIN.get(f"/a/{code}")   # sahibi sayılmaz
    assert RAW.head(f"/a/{code}").status_code == 200
    assert poll(p["id"])["views"] == 0
    RAW.get(f"/a/{code}", headers={"User-Agent": "Mozilla/5.0 (iPhone)"})
    assert poll(p["id"])["views"] == 1
    v = Visitor()
    r = ok(v.vote(code, [opts(p)["A"]]))   # formu açtı: +1
    v.c.get(r.headers["Location"])          # oy sonrası yönlendirme sayılmaz
    assert poll(p["id"])["views"] == 2
    assert "sen ve link önizlemeleri sayılmaz" in ADMIN.text(f"/anket/{p['id']}")
    print("  link preview/views OK")


# ---------- Telegram ----------
def test_telegram_batch():
    clear()
    p = made(create(ADMIN, question="Akşam ne yiyelim?", opts=("Pizza", "Döner"), notify="1"))
    code, ids, pid = p["code"], opts(p), p["id"]
    ok(Visitor().vote(code, [ids["Pizza"]]))
    msgs = sent()
    assert len(msgs) == 1 and msgs[0]["chat_id"] == "100"
    for part in ("🗳️ <b>“Akşam ne yiyelim?”</b> anketine 1 yeni oy · önde: Pizza (%100)", "Toplam 1 oy",
                 f'href="http://localhost/anket/{pid}"'):
        assert part in msgs[0]["text"], part
    # 10 dakika dolmadan gelen oylar birikir; cron süre dolunca toplu gönderir
    NOW[0] = START + timedelta(minutes=3)
    ok(Visitor().vote(code, [ids["Döner"]]))
    ok(Visitor().vote(code, [ids["Pizza"]]))
    assert len(sent()) == 1
    NOW[0] = START + timedelta(minutes=8)
    assert cron()["poll_notices"] == 0 and len(sent()) == 1
    NOW[0] = START + timedelta(minutes=10)
    assert cron()["poll_notices"] == 1
    assert "anketine 2 yeni oy · önde: Pizza (%67)" in sent()[-1]["text"] and "Toplam 3 oy" in sent()[-1]["text"]
    assert cron()["poll_notices"] == 0 and len(sent()) == 2
    # Son mesajdan 10 dakika geçmeden gelen oy bekler (cron gönderir); geçtiyse oyla birlikte hemen gider
    NOW[0] = START + timedelta(minutes=15)
    ok(Visitor().vote(code, [ids["Döner"]]))
    assert len(sent()) == 2
    NOW[0] = START + timedelta(minutes=21)
    assert cron()["poll_notices"] == 1
    assert len(sent()) == 3 and "anketine 1 yeni oy · berabere: Pizza, Döner (%50)" in sent()[-1]["text"]
    NOW[0] = START + timedelta(minutes=32)
    ok(Visitor().vote(code, [ids["Pizza"]]))
    assert len(sent()) == 4 and "anketine 1 yeni oy · önde: Pizza (%60)" in sent()[-1]["text"]
    # Bildirim kapalıysa mesaj yok; yeniden açılınca eski oylar "yeni" sayılmaz
    assert edit(ADMIN, pid, question="Akşam ne yiyelim?").status_code == 302 and poll(pid)["notify"] == 0
    NOW[0] = START + timedelta(minutes=50)
    ok(Visitor().vote(code, [ids["Pizza"]]))
    assert len(sent()) == 4 and cron()["poll_notices"] == 0
    assert edit(ADMIN, pid, question="Akşam ne yiyelim?", notify="1").status_code == 302
    assert poll(pid)["notified_vote_id"] == one("SELECT MAX(id) AS m FROM poll_votes WHERE poll_id = ?", (pid,))["m"]
    NOW[0] = START + timedelta(minutes=60)
    assert cron()["poll_notices"] == 0 and len(sent()) == 4
    # Telegram hatası oyu bozmaz
    FAIL[0] = True
    ok(Visitor().vote(code, [ids["Döner"]]))
    FAIL[0] = False
    assert votes(p) == 7
    # Kapatınca bekleyen bildirim düşer (kapanış durumu zaten belli)
    NOW[0] = START + timedelta(minutes=80)
    ok(Visitor().vote(code, [ids["Döner"]]))
    NOW[0] = START + timedelta(minutes=85)
    ok(Visitor().vote(code, [ids["Döner"]]))
    count = len(sent())
    ADMIN.post(f"/anket/{pid}/durum")
    NOW[0] = START + timedelta(minutes=120)
    assert cron()["poll_notices"] == 0 and len(sent()) == count
    # Telegram'ı bağlı olmayan sahibi: mesaj yok
    m = made(create(MEHMET, question="Mehmet sorar", opts=("A", "B"), notify="1"))
    ok(Visitor().vote(m["code"], [opts(m)["A"]]))
    assert len(sent()) == count and cron()["poll_notices"] == 0
    assert "Telegram bağlı değil" in MEHMET.text(f"/anket/{m['id']}")
    print("  telegram batch OK")


# ---------- Çöp kutusu ----------
def test_trash_restore():
    clear()
    p = made(create(ADMIN, question="Çöpe gidecek", opts=("A", "B"), invite_only="1", require_name="1"))
    pid, code = p["id"], p["code"]
    ADMIN.post(f"/anket/{pid}/davet", data={"labels": "Ali\nVeli"})
    tokens = [r_["token"] for r_ in rows("SELECT token FROM poll_invites WHERE poll_id = ? ORDER BY id", (pid,))]
    ok(Visitor().vote(code, [opts(p)["A"]], name="Ali", d=tokens[0]))
    r = ADMIN.post(f"/anket/{pid}/sil")
    assert r.status_code == 302 and "çöp kutusuna taşındı" in ADMIN.text("/anket/")
    assert poll(pid) is None
    for table in ("poll_options", "poll_invites", "poll_votes"):
        assert one(f"SELECT COUNT(*) AS n FROM {table} WHERE poll_id = ?", (pid,))["n"] == 0, table
    item = one("SELECT * FROM trash WHERE module = 'polls' ORDER BY id DESC LIMIT 1")
    assert item["label"] == "🗳️ Çöpe gidecek"
    # Silinmiş ve olmayan kod dışarıdan aynı 404; davet linki de açılmaz
    deleted, missing = RAW.get(f"/a/{code}"), RAW.get("/a/hicyokbu")
    assert deleted.status_code == missing.status_code == 404 and text(deleted) == text(missing)
    assert RAW.get(f"/a/{code}?d={tokens[1]}").status_code == 404
    with app.test_request_context():
        assert code in polls._trashed_codes()   # kutudayken kod başkasına verilmez
    ADMIN.post(f"/cop-kutusu/{item['id']}/geri")
    p = poll(pid)
    assert p and p["code"] == code and votes(p) == 1 and len(opts(p)) == 2
    assert one("SELECT COUNT(*) AS n FROM poll_vote_choices c JOIN poll_votes v ON v.id = c.vote_id"
               " WHERE v.poll_id = ?", (pid,))["n"] == 1
    assert "Bu linkle oy verildi" in text(RAW.get(f"/a/{code}?d={tokens[0]}"))
    ok(Visitor().vote(code, [opts(p)["B"]], name="Veli", d=tokens[1]))
    assert "Ali" in ADMIN.text(f"/anket/{pid}")
    print("  trash/restore OK")


# ---------- Yalıtım ----------
def test_isolation():
    clear()
    p = made(create(ADMIN, question="Gizli anket Zeytin", description="zeytinyağlı", opts=("Siyah", "Yeşil")))
    pid = p["id"]
    ok(Visitor().vote(p["code"], [opts(p)["Siyah"]]))
    vid = one("SELECT id FROM poll_votes WHERE poll_id = ?", (pid,))["id"]
    ADMIN.post(f"/anket/{pid}/davet", data={"labels": "Ali"})
    iid = one("SELECT id FROM poll_invites WHERE poll_id = ?", (pid,))["id"]
    for url in (f"/anket/{pid}", f"/anket/{pid}/duzenle", f"/anket/{pid}/sonuclar.csv", f"/anket/{pid}/qr.png"):
        assert AYSE.get(url).status_code == 404, url
    for url in (f"/anket/{pid}/durum", f"/anket/{pid}/sil", f"/anket/{pid}/davet", f"/anket/{pid}/oy/{vid}/sil",
                f"/anket/{pid}/davet/{iid}/sil", f"/anket/{pid}/duzenle"):
        assert AYSE.post(url, data=form_data(("A", "B"), labels="Hacker")).status_code == 404, url
    p2 = poll(pid)
    assert p2["closed"] == 0 and p2["question"] == "Gizli anket Zeytin" and votes(p2) == 1
    assert one("SELECT COUNT(*) AS n FROM poll_invites WHERE poll_id = ?", (pid,))["n"] == 1
    assert "Zeytin" not in AYSE.text("/anket/") and "Zeytin" in ADMIN.text("/anket/")
    # Arama: sadece kendi anketleri (soru, açıklama, seçenekler)
    for q in ("zeytin", "ZEYTİNYAĞLI", "yesil"):
        assert "Gizli anket Zeytin" in ADMIN.text(f"/ara/?q={q}"), q
        assert "Gizli anket Zeytin" not in AYSE.text(f"/ara/?q={q}"), q
    # Ayşe'nin anketi Ayşe'ye ait; kullanıcı silinince anketleri de gider
    a = made(create(AYSE, question="Ayşe'nin anketi", opts=("A", "B")))
    assert a["user_id"] == AYSE_ID and ADMIN.get(f"/anket/{a['id']}").status_code == 404
    ok(Visitor().vote(a["code"], [opts(a)["A"]]))
    ADMIN.post(f"/yonetim/kullanicilar/{AYSE_ID}/sil")
    assert poll(a["id"]) is None and RAW.get(f"/a/{a['code']}").status_code == 404
    assert one("SELECT COUNT(*) AS n FROM poll_votes WHERE poll_id = ?", (a["id"],))["n"] == 0
    assert RAW.get(f"/a/{p['code']}").status_code == 200
    print("  isolation OK")


if __name__ == "__main__":
    test_create_validation()
    test_public_vote()
    test_ip_and_name()
    test_ip_limit_and_honeypot()
    test_visibility()
    test_closing()
    test_invites()
    test_edit_rules()
    test_results_csv_delete()
    test_escape_and_headers()
    test_link_preview_views()
    test_telegram_batch()
    test_trash_restore()
    test_isolation()
    print("OK")
