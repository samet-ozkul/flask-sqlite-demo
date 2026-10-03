"""Bilet Cüzdanı: ekleme, dosyalar (kota), bilet ekranı ve QR, yaklaşan/geçmiş, harcama, takvim/pano/arama,
Telegram etkinlik günü mesajları ve dosyaları, Telegram'dan PDF/fotoğrafla ekleme (yapay zekâ açık/kapalı),
PDF'ten yazı çıkarma, biletten okuma, erişim ve çöp kutusu.

Telegram, yapay zekâ ve saat taklit edilir; dış servis çağrılmaz.
Çalıştır: .venv/Scripts/python tests/test_tickets.py
"""
import io
import json
import os
import sys
import urllib.parse
import zlib
from datetime import datetime, time, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

from PIL import Image  # noqa: E402

app = make_app()
os.environ["CRON_SECRET"] = "gizli"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"
os.environ["AI_PROVIDER"] = "mistral"
os.environ["AI_API_KEY"] = "k"
for _k in ("AI_MODEL", "AI_BASE_URL"):
    os.environ.pop(_k, None)

import pano.ai as ai  # noqa: E402
import pano.assistant as assistant  # noqa: E402
import pano.external as ext  # noqa: E402
import pano.telegram as tg  # noqa: E402
import pano.todo_reminders as todo  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.calendar_events import events_between  # noqa: E402
from pano.db import execute, query, query_one  # noqa: E402
from pano.modules import tickets as tk  # noqa: E402
from pano.pdftext import pdf_text  # noqa: E402
from pano.totp import qr_svg  # noqa: E402
from pano.utils import TZ, today  # noqa: E402

CALLS = []   # (method, params, files)
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {}, files)) or {"message_id": 7}
FILES = {}   # Telegram file_id -> bayt
tg.download_file = lambda file_id: FILES[file_id]
ext.weather = lambda lat, lon: None
ext.rates = lambda: None
T = today()
NOW = [datetime.combine(T, time(10, 0), tzinfo=TZ)]
todo.now_local = lambda: NOW[0]

with app.app_context():
    AYSE = create_user("ayse", "ayse12345")
    execute("UPDATE users SET telegram_chat_id = '100', ai_enabled = 1 WHERE id = 1")
    execute("UPDATE users SET telegram_chat_id = '200' WHERE id = ?", (AYSE,))
with app.test_request_context():
    SECRET = tg.webhook_secret()
ADMIN = Client(app)
AYSE_C = Client(app, "ayse", "ayse12345")
RAW = app.test_client()
UPDATE = [0]


# ---------- Yardımcılar ----------
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


def at(n, hour, minute=0):
    return datetime.combine(T + timedelta(days=n), time(hour, minute), tzinfo=TZ)


def png(w=400, h=600, color=(255, 255, 255)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def _pdf(objects):
    out = bytearray(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
    for num, body in objects:
        out += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"
    return bytes(out + b"trailer << /Root 1 0 R >>\n%%EOF\n")


def _stream(data, compress=True, extra=b""):
    if compress:
        data, extra = zlib.compress(data), extra + b" /Filter /FlateDecode"
    return b"<< /Length " + str(len(data)).encode() + extra + b" >>\nstream\n" + data + b"\nendstream"


def ticket_pdf(simple_lines=("E-BILET \\(Ucak\\)", "PNR: XK7Q2L", "\\334cret 1.249,90 TL"),
               rows=(("Yolcu", "ŞEYMA ÖZKUL"), ("Uçuş", "TK2124 İstanbul → Ankara"), ("Koltuk", "14C · Kapı B12")),
               objstm=False):
    """Bilet benzeri PDF: Helvetica (WinAnsi, kaçışlı metin) + ToUnicode tablolu Type0 yazı tipi (sıkıştırılmış).
    Satır başlığının ilk harfi ayrı yazılıp sıkı Td ile devam eder (harf aralığı ayarı: boşluk sayılmamalı),
    değer aynı satırda uzak bir Tm'de ve kelimeler TJ içinde büyük boşlukla (kelime arası) yazılır."""
    chars = sorted({ch for row in rows for part in row for ch in part if ch != " "})
    code = {ch: i + 1 for i, ch in enumerate(chars)}

    def hexs(text):
        return "<" + "".join(f"{code[ch]:04X}" for ch in text) + ">"

    cmap = ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n1 begincodespacerange <0000> <FFFF>"
            f" endcodespacerange\n{len(chars)} beginbfchar\n"
            + "".join(f"<{code[ch]:04X}> <{ch.encode('utf-16-be').hex().upper()}>\n" for ch in chars)
            + "endbfchar\nendcmap\nend end\n")
    content = "BT /F1 14 Tf 50 800 Td\n" + "\n0 -18 Td\n".join(f"({line}) Tj" for line in simple_lines) + "\nET\n"
    y = 700
    for label, value in rows:
        content += (f"BT /F2 10 Tf 1 0 0 1 50 {y} Tm {hexs(label[0])} Tj 5.8 0 Td {hexs(label[1:])} Tj ET\n"
                    f"BT /F2 10 Tf 1 0 0 1 200 {y} Tm [{' -400 '.join(hexs(w) for w in value.split())}] TJ ET\n")
        y -= 16
    fonts = [(4, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"),
             (5, b"<< /Type /Font /Subtype /Type0 /BaseFont /ABCDEF+Arial /Encoding /Identity-H"
                 b" /DescendantFonts [8 0 R] /ToUnicode 7 0 R >>"),
             (8, b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /ABCDEF+Arial /DW 600 >>")]
    objs = [(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
            (2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
            (3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R /F2 5 0 R >> >>"
                b" /Contents 6 0 R >>"),
            (6, _stream(content.encode("ascii"))),
            (7, _stream(cmap.encode(), compress=False))]
    if objstm:  # yazı tipi sözlükleri sıkıştırılmış nesne akışında (PDF 1.5+)
        bodies = [b for _n, b in fonts]
        offsets, pos = [], 0
        for b in bodies:
            offsets.append(pos)
            pos += len(b) + 1
        header = " ".join(f"{n} {o}" for (n, _b), o in zip(fonts, offsets)).encode() + b"\n"
        objs.append((9, _stream(header + b"\n".join(bodies), extra=f" /Type /ObjStm /N {len(fonts)} /First {len(header)}".encode())))
    else:
        objs += fonts
    return _pdf(objs)


def image_pdf():
    """Taranmış gibi: sadece resim, yazı yok."""
    buf = io.BytesIO()
    Image.new("RGB", (300, 400), "white").save(buf, "PDF")
    return buf.getvalue()


def tid_of(title, user_id=1):
    row = one("SELECT id FROM tickets WHERE title = ? AND user_id = ? ORDER BY id DESC", (title, user_id))
    return row["id"] if row else None


def ticket(tid):
    return one("SELECT * FROM tickets WHERE id = ?", (tid,))


def atts(tid):
    return rows("SELECT * FROM attachments WHERE entity = 'ticket' AND entity_id = ? ORDER BY id", (tid,))


def count(user_id=1):
    return one("SELECT COUNT(*) AS n FROM tickets WHERE user_id = ?", (user_id,))["n"]


def add(client, title, files=None, follow=False, **fields):
    data = {"title": title, "kind": "concert", "starts_on": D(5)}
    data.update({k: str(v) for k, v in fields.items()})
    if files:
        data["files"] = [(io.BytesIO(b), name) for name, b in files]
    r = client.post("/biletler/yeni", data=data, content_type="multipart/form-data", follow_redirects=follow)
    return (tid_of(title, AYSE if client is AYSE_C else 1), r)


def reset():
    run("DELETE FROM tickets")
    run("DELETE FROM attachments WHERE entity = 'ticket'")


def calls(method):
    return [(p, f) for m, p, f in CALLS if m == method]


def sent():
    return [(p["chat_id"], p["text"], p.get("reply_markup")) for p, _f in calls("sendMessage")]


def docs():
    """[(chat_id, dosya_adı, bayt, tür, açıklama)]"""
    return [(p["chat_id"], *f["document"], p.get("caption")) for p, f in calls("sendDocument")]


def buttons(markup):
    return [b["callback_data"] for row in json.loads(markup or "{}").get("inline_keyboard", []) for b in row]


def last(method):
    return calls(method)[-1][0]


def cron(path="hatirlatma"):
    r = RAW.get(f"/cron/gizli/{path}")
    assert r.status_code == 200, r.status_code
    return r.json


def say(message, chat=100):
    UPDATE[0] += 1
    RAW.post("/telegram/webhook", data=json.dumps({"update_id": UPDATE[0], "message": {
        "message_id": UPDATE[0], "chat": {"id": chat}, **message}}),
        content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})


def press(data, chat=100):
    UPDATE[0] += 1
    RAW.post("/telegram/webhook", data=json.dumps({"update_id": UPDATE[0], "callback_query": {
        "id": "cb", "data": data, "from": {"id": chat}, "message": {"message_id": 7, "chat": {"id": chat}}}}),
        content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})


def disk(att):
    with open(os.path.join(app.config["UPLOAD_DIR"], att["filename"]), "rb") as f:
        return f.read()


# ---------- Testler ----------
def test_pdf_text():
    for objstm in (False, True):
        text = pdf_text(ticket_pdf(objstm=objstm))
        lines = text.splitlines()
        assert lines[:3] == ["E-BILET (Ucak)", "PNR: XK7Q2L", "Ücret 1.249,90 TL"], lines  # kaçış ve sekizlik kod
        # ToUnicode ile Türkçe harfler; sıkı Td harf aralığıdır (boşluk yok), uzak Tm ve TJ'deki büyük boşluk kelime arası
        assert lines[3:] == ["Yolcu ŞEYMA ÖZKUL", "Uçuş TK2124 İstanbul → Ankara", "Koltuk 14C · Kapı B12"], lines
    # Yazısı olmayan (taranmış), bozuk, şifreli ve PDF olmayan girdi: boş, hata yok
    assert pdf_text(image_pdf()) == ""
    assert pdf_text(b"%PDF-1.4\n1 0 obj << /Length 5 /Filter /FlateDecode >>\nstream\nbozuk\nendstream endobj") == ""
    assert pdf_text(b"%PDF-1.4 /Encrypt 5 0 R") == "" and pdf_text(png()) == "" and pdf_text(b"") == ""
    print("  pdf text OK")


def test_crud():
    assert "Yaklaşan bilet yok" in ADMIN.text("/biletler/")
    tid, _r = add(ADMIN, "Tarkan Konseri", starts_on=D(5), starts_at="21:00", venue="Harbiye Açıkhava",
                  address="Harbiye, Taşkışla Cd., İstanbul", seat="Blok A · Sıra 5 · Koltuk 12", booking_code="BTX-778812",
                  holder="Samet + 1", note="Kapılar 19:30'da açılıyor")
    t = ticket(tid)
    assert (t["kind"], t["starts_on"], t["starts_at"], t["venue"], t["seat"], t["booking_code"], t["holder"], t["price"],
            t["archived"], t["day_sent_for"]) == ("concert", D(5), "21:00", "Harbiye Açıkhava", "Blok A · Sıra 5 · Koltuk 12",
                                                  "BTX-778812", "Samet + 1", None, 0, None)
    # Saatsiz, bilinmeyen tür -> Diğer; saat "9.5" gibi bozuksa saatsiz
    oid, _r = add(ADMIN, "Müze kartı", kind="yok", starts_at="25:99")
    assert (ticket(oid)["kind"], ticket(oid)["starts_at"]) == ("other", None)
    # Reddedilenler: başlık yok, tarih yok / bozuk, fiyat bozuk / sıfır
    before = count()
    for bad in ({"title": ""}, {"starts_on": ""}, {"starts_on": "2026-13-45"}, {"price": "abc"}, {"price": "0"},
                {"price": "-5"}, {"price": "1e999"}):
        ADMIN.post("/biletler/yeni", data={"title": "Hatalı", "kind": "concert", "starts_on": D(3), **bad})
    assert count() == before
    page = ADMIN.post("/biletler/yeni", data={"title": "", "starts_on": D(3)}, follow_redirects=True).get_data(as_text=True)
    assert "Başlık gerekli" in page
    assert "Tarih gerekli" in ADMIN.post("/biletler/yeni", data={"title": "X"}, follow_redirects=True).get_data(as_text=True)
    assert "Fiyat 0" in ADMIN.post("/biletler/yeni", data={"title": "X", "starts_on": D(1), "price": "abc"},
                                   follow_redirects=True).get_data(as_text=True)

    page = ADMIN.text("/biletler/")
    assert "Tarkan Konseri" in page and "Harbiye Açıkhava" in page and "🎵" in page and "5 gün kaldı" in page
    detail = ADMIN.text(f"/biletler/{tid}")
    assert "BTX-778812" in detail and "Kapılar 19:30" in detail and "Haritada aç" in detail and "Bilet ekranı" in detail
    # Düzenleme
    r = ADMIN.post(f"/biletler/{tid}/duzenle", data={"title": "Tarkan Konseri", "kind": "concert", "starts_on": D(6),
                                                     "starts_at": "20:30", "venue": "Harbiye", "seat": "A-12"})
    assert r.status_code == 302
    t = ticket(tid)
    assert (t["starts_on"], t["starts_at"], t["venue"], t["seat"], t["booking_code"], t["note"]) == (D(6), "20:30", "Harbiye",
                                                                                                    "A-12", "", "")
    ADMIN.post(f"/biletler/{tid}/duzenle", data={"title": "", "starts_on": D(6)})  # başlık boşsa değişmez
    assert ticket(tid)["title"] == "Tarkan Konseri"
    print("  crud OK")


def test_files_and_quota():
    pdf = ticket_pdf()
    tid, r = add(ADMIN, "Uçuş TK2124", kind="flight", starts_on=D(3), starts_at="07:45",
                 files=[("eticket.pdf", pdf), ("ekran.png", png())])
    assert r.status_code == 302 and r.headers["Location"].endswith("/biletler/")
    a = atts(tid)
    assert [x["mime"] for x in a] == ["application/pdf", "image/jpeg"] and a[0]["thumb"] is None and a[1]["thumb"]
    assert disk(a[0]) == pdf and a[0]["original_name"] == "eticket.pdf"
    assert "📎 2 dosya" in ADMIN.text("/biletler/")
    detail = ADMIN.text(f"/biletler/{tid}")
    assert f"/dosya/{a[0]['id']}" in detail and f"/dosya/{a[1]['id']}/kucuk" in detail and 'value="ticket"' in detail
    # Var olan bilete ek dosya (ortak yükleme yolu, entity 'ticket')
    ADMIN.post("/dosya/yukle", data={"entity": "ticket", "entity_id": str(tid), "file": (io.BytesIO(png(200, 200)), "kapi.png"),
                                     "next": f"/biletler/{tid}"}, content_type="multipart/form-data")
    assert len(atts(tid)) == 3
    # Geçersiz dosya: bilet eklenir, dosya reddedilir, sayfasına yönlenir
    bad, r = add(ADMIN, "Bozuk dosyalı", files=[("bilet.txt", b"merhaba")], follow=True)
    assert bad and not atts(bad) and "Sadece fotoğraf" in r.get_data(as_text=True)
    # Kota: yükleme kapalı ve alan dolu
    run("UPDATE users SET upload_max_mb = 0 WHERE id = ?", (AYSE,))
    nid, r = add(AYSE_C, "Ayşe sinema", kind="cinema", files=[("bilet.pdf", pdf)], follow=True)
    assert nid and not atts(nid) and "Hesabında dosya yükleme kapalı" in r.get_data(as_text=True)
    run("UPDATE users SET upload_max_mb = NULL, quota_mb = 1 WHERE id = ?", (AYSE,))
    run("INSERT INTO attachments (user_id, entity, entity_id, filename, mime, size) VALUES (?, 'note', 999, 'x', 'image/jpeg', ?)",
        (AYSE, 1024 * 1024))
    nid2, r = add(AYSE_C, "Ayşe tiyatro", kind="theatre", files=[("bilet.pdf", pdf)], follow=True)
    assert not atts(nid2) and "Depolama alanın doldu" in r.get_data(as_text=True)
    run("DELETE FROM attachments WHERE user_id = ? AND entity = 'note'", (AYSE,))
    run("UPDATE users SET quota_mb = NULL WHERE id = ?", (AYSE,))
    nid3, _r = add(AYSE_C, "Ayşe maç", kind="sport", files=[("bilet.pdf", pdf)])
    assert len(atts(nid3)) == 1
    # Başkasının biletine dosya eklenemez, dosyası açılamaz
    r = AYSE_C.post("/dosya/yukle", data={"entity": "ticket", "entity_id": str(tid), "file": (io.BytesIO(png()), "x.png")},
                    content_type="multipart/form-data")
    assert r.status_code == 404 and len(atts(tid)) == 3
    assert AYSE_C.get(f"/dosya/{a[0]['id']}").status_code == 404 and AYSE_C.get(f"/dosya/{a[1]['id']}/kucuk").status_code == 404
    assert ADMIN.get(f"/dosya/{a[0]['id']}").data == pdf
    print("  files/quota OK")


def test_show_and_qr():
    code = "M1OZKUL/SEYMA         EXK7Q2L ISTESBTK 2124 287Y014C0042 100"
    tid, _r = add(ADMIN, "İstanbul → Ankara TK2124", kind="flight", starts_on=D(3), starts_at="07:45",
                  venue="İstanbul Havalimanı", address="Tayakadın, Terminal Cd. No:1, Arnavutköy", seat="14C · Kapı B12",
                  booking_code="XK7Q2L", holder="Şeyma Özkul", barcode_text=code,
                  files=[("biniş.png", png()), ("eticket.pdf", ticket_pdf())])
    page = ADMIN.text(f"/biletler/{tid}/goster")
    with app.app_context():
        assert qr_svg(code) in page  # barkod metni büyük QR olarak
    assert 'id="pass-code">XK7Q2L<' in page and 'data-copy="pass-code"' in page and "14C · Kapı B12" in page
    assert "Şeyma Özkul" in page and "parlaklığını" in page and "data-wakelock" in page and "tickets.js" in page
    assert tk.MAPS_URL + urllib.parse.quote("Tayakadın, Terminal Cd. No:1, Arnavutköy") in page.replace("&amp;", "&")
    a = atts(tid)
    assert f'<img src="/dosya/{a[0]["id"]}"' in page and "📄 Aç: eticket.pdf" in page and f'href="/dosya/{a[1]["id"]}"' in page
    assert "3 gün kaldı" in page
    # Barkodsuz, dosyasız bilet: QR yok, yönlendirme notu
    bare, _r = add(ADMIN, "Sade bilet", venue="Salon")
    page = ADMIN.text(f"/biletler/{bare}/goster")
    assert '<svg' not in page and "Bu bilette dosya ya da kod yok" in page
    # Adres yoksa harita yer adıyla aranır
    assert tk.MAPS_URL + "Salon" in page.replace("&amp;", "&")
    print("  show/qr OK")


def test_upcoming_and_past():
    reset()
    NOW[0] = at(0, 10)
    old, _r = add(ADMIN, "Dünkü maç", kind="sport", starts_on=D(-1))
    today_id, _r = add(ADMIN, "Bugünkü konser", starts_on=D(0), starts_at="21:00")
    tomorrow, _r = add(ADMIN, "Yarınki tiyatro", kind="theatre", starts_on=D(1))
    later, _r = add(ADMIN, "Uzak uçuş", kind="flight", starts_on=D(10))
    gone, _r = add(ADMIN, "İptal olan", starts_on=D(4))
    ADMIN.post(f"/biletler/{gone}/arsiv")
    run("INSERT INTO tickets (user_id, kind, title) VALUES (1, 'other', 'Telegram taslağı')")
    page = ADMIN.text("/biletler/")
    up = page.split('class="tk-cards"', 1)[1]
    assert "Dünkü maç" not in up and "İptal olan" not in up
    assert up.index("Telegram taslağı") < up.index("Bugünkü konser") < up.index("Yarınki tiyatro") < up.index("Uzak uçuş")
    assert "📝 tarih gir" in up and "Bugün 21:00" in up and ">Yarın<" in up and "10 gün kaldı" in up
    assert 'class="badge good"' in up and 'class="badge later"' in up
    past = ADMIN.text("/biletler/?gecmis=1").split('<ul class="rows">', 1)[1]
    assert "Dünkü maç" in past and "İptal olan" in past and "📦 arşivde" in past and "Bugünkü konser" not in past
    assert past.index("İptal olan") < past.index("Dünkü maç")  # yeni tarih üstte
    # Tarih taklidi: iki gün sonra yarınki tiyatro ve bugünkü konser geçmişe düşer, uzak uçuşa 8 gün kalır
    NOW[0] = at(2, 9)
    page = ADMIN.text("/biletler/")
    up = page.split('class="tk-cards"', 1)[1]
    assert "Yarınki tiyatro" not in up and "Bugünkü konser" not in up and "8 gün kaldı" in up
    past = ADMIN.text("/biletler/?gecmis=1")
    assert "Yarınki tiyatro" in past and "Bugünkü konser" in past
    assert "geçmiş" in ADMIN.text(f"/biletler/{tomorrow}")
    # Arşivden çıkarma; arşivlenen ama tarihi gelmemiş bilet geri yaklaşanlara
    r = ADMIN.post(f"/biletler/{gone}/arsiv", follow_redirects=True)
    assert "arşivden çıkarıldı" in r.get_data(as_text=True) and ticket(gone)["archived"] == 0
    assert "İptal olan" in ADMIN.text("/biletler/").split('class="tk-cards"', 1)[1]
    NOW[0] = at(0, 10)
    assert old and today_id and later
    print("  upcoming/past OK")


def test_expense():
    exp_before = one("SELECT COUNT(*) AS n FROM expenses")["n"]
    cid, r = add(ADMIN, "Duman Konseri", price="1.250,50", add_expense="1", follow=True)
    assert "Harcamalara da işlendi" in r.get_data(as_text=True)
    t = ticket(cid)
    e = one("SELECT * FROM expenses WHERE id = ?", (t["expense_id"],))
    assert (t["price"], e["amount"], e["category"], e["date"], e["note"], e["user_id"]) == \
        (1250.5, 1250.5, "Eğlence", T.isoformat(), "Bilet: Duman Konseri", 1)
    fid, _r = add(ADMIN, "Ankara treni", kind="train", price="450", add_expense="1")
    assert one("SELECT category FROM expenses WHERE id = ?", (ticket(fid)["expense_id"],))["category"] == "Ulaşım"
    gid, _r = add(ADMIN, "Hediye bilet", kind="museum", price="300", add_expense="1", expense_category="Hediye")
    assert one("SELECT category FROM expenses WHERE id = ?", (ticket(gid)["expense_id"],))["category"] == "Hediye"
    # Fiyatsız işaret / işaretsiz fiyat: harcama yok
    nid, _r = add(ADMIN, "Ücretsiz etkinlik", add_expense="1")
    pid, _r = add(ADMIN, "Sonra işlenecek", price="99,90")
    assert ticket(nid)["expense_id"] is None and ticket(pid)["expense_id"] is None and ticket(pid)["price"] == 99.9
    assert one("SELECT COUNT(*) AS n FROM expenses")["n"] == exp_before + 3
    # Düzenlerken işlenir; bağlı harcama durdukça ikinci kez işlenmez
    form = {"title": "Sonra işlenecek", "kind": "cinema", "starts_on": D(5), "price": "99,90", "add_expense": "1"}
    ADMIN.post(f"/biletler/{pid}/duzenle", data=form)
    eid = ticket(pid)["expense_id"]
    assert eid and one("SELECT category, amount FROM expenses WHERE id = ?", (eid,))[:] == ("Eğlence", 99.9)
    ADMIN.post(f"/biletler/{pid}/duzenle", data=form)
    assert ticket(pid)["expense_id"] == eid and one("SELECT COUNT(*) AS n FROM expenses")["n"] == exp_before + 4
    detail = ADMIN.text(f"/biletler/{pid}")
    assert "Harcamalara işlendi" in detail and f"/harcamalar/{eid}" in detail and 'name="add_expense"' not in detail
    # Harcama silinirse yeniden işlenebilir
    run("DELETE FROM expenses WHERE id = ?", (eid,))
    assert 'name="add_expense"' in ADMIN.text(f"/biletler/{pid}")
    print("  expense OK")


def test_integrations():
    reset()
    NOW[0] = at(0, 10)
    a, _r = add(ADMIN, "Galatasaray - Fenerbahçe", kind="sport", starts_on=D(1), starts_at="20:00", venue="RAMS Park",
                booking_code="PSS-99812")
    add(ADMIN, "Uzak festival", starts_on=D(20), venue="Kilyos")
    gone, _r = add(ADMIN, "Arşivli maç", kind="sport", starts_on=D(2))
    ADMIN.post(f"/biletler/{gone}/arsiv")
    add(AYSE_C, "Ayşenin operası", kind="theatre", starts_on=D(1), venue="AKM")
    # Takvim: saatli; arşivli tamamlandı; başkasınınki yok
    with app.test_request_context():
        evs = [e for e in events_between(1, T, T + timedelta(days=30)) if e["kind"] == "ticket"]
    by = {e["title"]: e for e in evs}
    assert set(by) == {"Galatasaray - Fenerbahçe", "Uzak festival", "Arşivli maç"}
    assert by["Galatasaray - Fenerbahçe"]["time"] == "20:00" and by["Galatasaray - Fenerbahçe"]["icon"] == "⚽"
    assert by["Galatasaray - Fenerbahçe"]["url"] == f"/biletler/{a}" and by["Arşivli maç"]["done"]
    assert "Galatasaray - Fenerbahçe" in ADMIN.text(f"/takvim/?ay={D(1)[:7]}")
    # Pano: yaklaşanlar (kısa vade, arşivsiz)
    home = ADMIN.text("/")
    assert "Galatasaray - Fenerbahçe" in home and "20:00 · RAMS Park" in home
    assert "Uzak festival" not in home and "Arşivli maç" not in home and "Ayşenin operası" not in home
    assert "Ayşenin operası" in AYSE_C.text("/")
    # Arama: başlık, yer, rezervasyon kodu (Türkçe harf duyarsız); başkasınınki yok
    assert "Galatasaray - Fenerbahçe" in ADMIN.text("/ara/?q=fenerbahce")
    assert "Galatasaray - Fenerbahçe" in ADMIN.text("/ara/?q=rams park")
    assert "Galatasaray - Fenerbahçe" in ADMIN.text("/ara/?q=pss-99812")
    assert "Ayşenin operası" not in ADMIN.text("/ara/?q=opera") and "Ayşenin operası" in AYSE_C.text("/ara/?q=akm")
    # Günlük özet
    n = len(sent())
    cron("gunluk")
    daily = dict((c, t) for c, t, _m in sent()[n:])
    assert "Galatasaray - Fenerbahçe" in daily["100"] and "yarın" in daily["100"]
    print("  integrations OK")


def test_telegram_reminders():
    reset()
    NOW[0] = at(0, 8, 30)
    pdf, img = ticket_pdf(), png(300, 300, (10, 120, 200))
    concert, _r = add(ADMIN, "Tarkan Konseri", starts_on=D(0), starts_at="21:00", venue="Harbiye Açıkhava",
                      address="Harbiye, İstanbul", seat="Blok A · Koltuk 12", booking_code="BTX-7788", holder="Samet + 1",
                      note="Kapılar 19:30", files=[("bilet.pdf", pdf), ("ekran.png", img)])
    run("UPDATE attachments SET original_name = 'ekran \"görüntüsü\".png' WHERE entity = 'ticket' AND original_name = 'ekran.png'")
    flight, _r = add(ADMIN, "İstanbul → Ankara TK2124", kind="flight", starts_on=D(0), starts_at="14:00")
    museum, _r = add(ADMIN, "Topkapı Sarayı", kind="museum", starts_on=D(0))
    theatre, _r = add(ADMIN, "Hamlet", kind="theatre", starts_on=D(1), starts_at="20:00", venue="Zorlu PSM")
    gone, _r = add(ADMIN, "Arşivli konser", starts_on=D(0), starts_at="20:00")
    ADMIN.post(f"/biletler/{gone}/arsiv")
    add(ADMIN, "Kaçan sinema", kind="cinema", starts_on=D(0), starts_at="08:00")  # başlangıç geçti: mesaj yok
    redeye, _r = add(ADMIN, "Gece uçuşu", kind="flight", starts_on=D(1), starts_at="01:30")
    add(AYSE_C, "Ayşe otobüs", kind="bus", starts_on=D(0), starts_at="23:00")
    run("INSERT INTO users (username, password_hash) VALUES ('telegramsiz', 'x')")
    run("INSERT INTO tickets (user_id, kind, title, starts_on) SELECT id, 'other', 'Telegramsız', ? FROM users"
        " WHERE username = 'telegramsiz'", (D(0),))

    def new_after(n):
        return sent()[n:]

    assert cron()["tickets_sent"] == 0  # 08:30: saatsiz 09:00'ı, uçak 10:00'ı bekler
    NOW[0] = at(0, 9, 5)
    n = len(sent())
    assert cron()["tickets_sent"] == 1
    (chat, text, _m), = new_after(n)
    assert chat == "100" and "🏛️ <b>Bugün:</b> Topkapı Sarayı" in text and "kaldı" not in text
    assert f"/biletler/{museum}/goster" in text and "Bilet ekranı" in text
    NOW[0] = at(0, 9, 55)
    assert cron()["tickets_sent"] == 0
    NOW[0] = at(0, 10, 0)  # uçak: 4 saat önce
    assert cron()["tickets_sent"] == 1 and "✈️ <b>Bugün:</b>" in sent()[-1][1] and "⏳ 4 sa kaldı" in sent()[-1][1]
    assert cron()["tickets_sent"] == 0  # iki kez gitmez
    NOW[0] = at(0, 17, 55)
    assert cron()["tickets_sent"] == 0
    NOW[0] = at(0, 18, 20)  # konser: 3 saat önce (cron gecikse de pencere içinde)
    n, nd = len(sent()), len(docs())
    assert cron()["tickets_sent"] == 1
    (chat, text, _m), = new_after(n)
    assert "🎵 <b>Bugün:</b> Tarkan Konseri" in text and "21:00 · ⏳ 2 sa 40 dk kaldı" in text
    assert "📍 Harbiye Açıkhava · Harbiye, İstanbul" in text
    assert tk.MAPS_URL + urllib.parse.quote("Harbiye, İstanbul") in text.replace("&amp;", "&")  # HTML'de &amp;
    assert "💺 Blok A · Koltuk 12" in text and "<code>BTX-7788</code>" in text and "👤 Samet + 1" in text
    assert "Kapılar 19:30" in text and f"/biletler/{concert}/goster" in text and "gönderilmedi" not in text
    files = docs()[nd:]
    a = atts(concert)
    assert [(c, name, mime) for c, name, _data, mime, _cap in files] == \
        [("100", "bilet.pdf", "application/pdf"), ("100", "ekran görüntüsü.jpg", "image/jpeg")]  # tırnaklar atılır
    assert files[0][2] == pdf and files[1][2] == disk(a[1]) and files[0][4] == "🎵 Tarkan Konseri"
    # 19:00: yarınki tiyatro ve gece uçuşu için "yarın", Ayşe'nin otobüsü (23:00 - 4 saat) kendi sohbetine
    NOW[0] = at(0, 19, 0)
    n = len(sent())
    assert cron()["tickets_sent"] == 3
    msgs = new_after(n)
    eve = [t for c, t, _m in msgs if "<b>Yarın:</b>" in t]
    assert len(eve) == 2 and any("Hamlet" in t and "20:00 · Zorlu PSM" in t for t in eve)
    assert any("Gece uçuşu" in t for t in eve) and all(c == "100" for c, t, _m in msgs if "Yarın" in t)
    assert [c for c, t, _m in msgs if "Ayşe otobüs" in t] == ["200"]
    assert cron()["tickets_sent"] == 0
    NOW[0] = at(0, 21, 30)  # gece uçuşu: 01:30 - 4 saat (tarih yarın)
    n = len(sent())
    assert cron()["tickets_sent"] == 1 and "✈️ <b>Yarın:</b> Gece uçuşu" in sent()[-1][1] and "4 sa kaldı" in sent()[-1][1]
    assert "Arşivli" not in "".join(t for _c, t, _m in sent()) and "Kaçan sinema" not in "".join(t for _c, t, _m in sent())
    assert "Telegramsız" not in "".join(t for _c, t, _m in sent())
    # Ertesi gün: tiyatro 17:00'da (20:00 - 3 saat); gece uçuşu yeniden gitmez
    NOW[0] = at(1, 9, 30)
    assert cron()["tickets_sent"] == 0
    NOW[0] = at(1, 17, 0)
    assert cron()["tickets_sent"] == 1 and "🎭 <b>Bugün:</b> Hamlet" in sent()[-1][1]
    # Tarih değişince hatırlatmalar yeniden kurulur
    ADMIN.post(f"/biletler/{theatre}/duzenle", data={"title": "Hamlet", "kind": "theatre", "starts_on": D(3),
                                                     "starts_at": "20:00", "venue": "Zorlu PSM"})
    NOW[0] = at(2, 19, 30)
    assert cron()["tickets_sent"] == 1 and "<b>Yarın:</b> Hamlet" in sent()[-1][1]
    NOW[0] = at(3, 17, 30)
    assert cron()["tickets_sent"] == 1 and "<b>Bugün:</b> Hamlet" in sent()[-1][1]
    # Büyük dosya Telegram'a gönderilmez, sadece link (mesajda not)
    big, _r = add(ADMIN, "Büyük PDF'li", starts_on=D(4), files=[("buyuk.pdf", pdf)])
    tk.SEND_FILE_MAX, old_max = 100, tk.SEND_FILE_MAX
    NOW[0] = at(4, 9, 30)
    nd = len(docs())
    assert cron()["tickets_sent"] == 1 and "1 dosya burada gönderilmedi" in sent()[-1][1] and len(docs()) == nd
    tk.SEND_FILE_MAX = old_max
    # Telegram hatası: işaretlenmez, sonraki çağrıda yeniden denenir
    late, _r = add(ADMIN, "Hata denemesi", starts_on=D(5))

    def fail(method, params=None, files=None):
        raise tg.TelegramError("ağ yok")

    tg._call, ok_call = fail, tg._call
    NOW[0] = at(5, 9, 30)
    res = cron()
    assert res["tickets_sent"] == 0 and any("bilet" in e for e in res["errors"]) and ticket(late)["day_sent_for"] is None
    tg._call = ok_call
    assert cron()["tickets_sent"] == 1
    NOW[0] = at(0, 10)
    assert flight and gone and redeye and big
    print("  telegram reminders OK")


READ = []      # read_ticket'a gelen (bayt, tür)
INFO = [None]  # sıradaki cevap (dict ya da Exception)


def fake_read(data, mime=""):
    READ.append((data, mime))
    if isinstance(INFO[0], Exception):
        raise INFO[0]
    return dict(INFO[0])


TICKET = {"is_ticket": True, "kind": "concert", "title": "Duman Konseri", "date": D(12), "time": "21:30",
          "venue": "KüçükÇiftlik Park", "address": "Maçka, İstanbul", "seat": "Ayakta · Kapı 2", "booking_code": "BLT-5521",
          "holder": "Samet Özkul", "price": 1100.0}


def test_telegram_add():
    reset()
    real_read = assistant.read_ticket
    assistant.read_ticket = fake_read
    run("UPDATE users SET ai_enabled = 1 WHERE id = 1")
    # PDF + yapay zekâ: bilgiler okunur, onaylanınca bilet ve dosya kaydedilir
    FILES["pdf1"] = ticket_pdf()
    INFO[0] = TICKET
    say({"document": {"file_id": "pdf1", "file_name": "duman-eticket.pdf", "mime_type": "application/pdf"}})
    prompt = last("sendMessage")
    assert "ph:tk" in buttons(prompt.get("reply_markup"))
    press("ph:tk")
    assert READ[-1] == (FILES["pdf1"], "application/pdf")
    card = last("editMessageText")
    assert "Bilet okundu" in card["text"] and "Duman Konseri" in card["text"] and "BLT-5521" in card["text"]
    assert "Ayakta · Kapı 2" in card["text"] and "1.100 ₺" in card["text"]
    assert buttons(card["reply_markup"]) == ["tk:ok", "tk:web", "tk:x"]
    press("tk:ok")
    tid = tid_of("Duman Konseri")
    t = ticket(tid)
    assert (t["kind"], t["starts_on"], t["starts_at"], t["venue"], t["address"], t["seat"], t["booking_code"], t["holder"],
            t["price"], t["expense_id"]) == ("concert", D(12), "21:30", "KüçükÇiftlik Park", "Maçka, İstanbul",
                                             "Ayakta · Kapı 2", "BLT-5521", "Samet Özkul", 1100.0, None)
    (att,) = atts(tid)
    assert att["mime"] == "application/pdf" and att["original_name"] == "duman-eticket.pdf"
    done = last("editMessageText")["text"]
    assert "Bilet cüzdanına eklendi" in done and f"/biletler/{tid}/goster" in done and f"/biletler/{tid}" in done
    press("tk:ok")  # ikinci dokunuş: süre doldu, ikinci bilet yok
    assert "Süre doldu" in last("editMessageText")["text"] and count() == 1

    # Fotoğraf + açıklama, "✏️ Düzenle (web)": kaydedilir, düzenleme bağlantısı; açıklama not olur
    FILES["ph1"] = png()
    INFO[0] = {**TICKET, "title": "Hamlet", "kind": "theatre", "price": None}
    say({"photo": [{"file_id": "ph1-small"}, {"file_id": "ph1"}], "caption": "Annemle gidiyoruz"})
    assert "ph:tk" in buttons(last("sendMessage").get("reply_markup"))
    press("ph:tk")
    assert READ[-1] == (FILES["ph1"], "image/jpeg")
    press("tk:web")
    hid = tid_of("Hamlet")
    assert ticket(hid)["note"] == "Annemle gidiyoruz" and atts(hid)[0]["mime"] == "image/jpeg"
    assert "Düzenle" in last("editMessageText")["text"] and f"/biletler/{hid}" in last("editMessageText")["text"]

    # Bilet değil: "Yine de ekle" taslak (tarihsiz) ekler; başlık açıklamadan
    FILES["ph2"] = png()
    INFO[0] = {**TICKET, "is_ticket": False, "title": ""}
    say({"photo": [{"file_id": "ph2"}], "caption": "Kedi"})
    press("ph:tk")
    assert "bilet gibi görünmüyor" in last("editMessageText")["text"]
    assert buttons(last("editMessageText")["reply_markup"]) == ["tk:raw", "tk:x"]
    press("tk:raw")
    kid = tid_of("Kedi")
    assert ticket(kid)["starts_on"] is None and len(atts(kid)) == 1 and "tamamla" in last("editMessageText")["text"]
    # Vazgeç: bilet yok
    FILES["ph3"] = png()
    say({"photo": [{"file_id": "ph3"}], "caption": "Vazgeçilecek"})
    press("ph:tk")
    press("tk:x")
    assert "Vazgeçildi" in last("editMessageText")["text"] and tid_of("Vazgeçilecek") is None

    # Okunamazsa (taranmış PDF / sağlayıcı hatası) dosya taslak bilete eklenir, uyarı yazılır
    FILES["pdf2"] = image_pdf()
    INFO[0] = ai.AIError("PDF'teki yazı okunamadı")
    say({"document": {"file_id": "pdf2", "file_name": "THY_eticket.pdf", "mime_type": "application/pdf"}})
    press("ph:tk")
    eid = tid_of("THY_eticket")
    assert eid and ticket(eid)["starts_on"] is None and len(atts(eid)) == 1
    assert "Bilgileri okuyamadım" in last("editMessageText")["text"]

    # Albüm: iki fotoğraf tek soruda, ikisi de aynı bilete
    FILES["al1"], FILES["al2"] = png(), png(200, 300)
    INFO[0] = {**TICKET, "title": "İki kişilik maç", "kind": "sport"}
    say({"photo": [{"file_id": "al1"}], "media_group_id": "g1"})
    say({"photo": [{"file_id": "al2"}], "media_group_id": "g1"})
    press("ph:tk")
    press("tk:ok")
    assert len(atts(tid_of("İki kişilik maç"))) == 2 and "(2 dosya)" in last("editMessageText")["text"]

    # Yapay zekâ kapalı: okunmaz, dosya taslak bilete eklenir; başlık dosya adından ("telegram.jpg" gibi adlar değil)
    run("UPDATE users SET ai_enabled = 0 WHERE id = 1")
    reads = len(READ)
    INFO[0] = AssertionError("yapay zekâ çağrılmamalı")
    FILES["pdf3"] = ticket_pdf()
    say({"document": {"file_id": "pdf3", "file_name": "Biletix-Sezen-Aksu.pdf", "mime_type": "application/pdf"}})
    press("ph:tk")
    sid = tid_of("Biletix-Sezen-Aksu")
    assert sid and ticket(sid)["starts_on"] is None and len(atts(sid)) == 1 and len(READ) == reads
    assert "Başlığı ve tarihi tamamla" in last("editMessageText")["text"]
    FILES["ph4"] = png()
    say({"photo": [{"file_id": "ph4"}]})
    press("ph:tk")
    pid = one("SELECT id, title FROM tickets ORDER BY id DESC LIMIT 1")
    assert pid["title"].startswith("Telegram'dan bilet") and len(atts(pid["id"])) == 1
    # Taslak listede en üstte, tamamlanınca normal bilet
    page = ADMIN.text("/biletler/")
    assert "📝 tarih gir" in page and "Telegram'dan geldi" in ADMIN.text(f"/biletler/{sid}")
    ADMIN.post(f"/biletler/{sid}/duzenle", data={"title": "Sezen Aksu", "kind": "concert", "starts_on": D(30)})
    assert ticket(sid)["starts_on"] == D(30) and ticket(sid)["title"] == "Sezen Aksu"

    # Dosya eklenemezse (yükleme kapalı) taslak bırakılmaz
    run("UPDATE users SET upload_max_mb = 0 WHERE id = ?", (AYSE,))
    before = count(AYSE)
    FILES["ay1"] = png()
    say({"photo": [{"file_id": "ay1"}], "caption": "Ayşe bileti"}, chat=200)
    press("ph:tk", chat=200)
    assert "Eklenemedi" in last("editMessageText")["text"] and count(AYSE) == before
    run("UPDATE users SET upload_max_mb = NULL WHERE id = ?", (AYSE,))
    run("UPDATE users SET ai_enabled = 1 WHERE id = 1")
    assistant.read_ticket = real_read
    print("  telegram add OK")


def openai_reply(payload):
    return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}, "finish_reason": "stop"}]}


def test_read_ticket():
    requests, replies = [], []

    def fake_post(url, headers, body):
        requests.append(body)
        return replies.pop(0)

    real_post, ai._post = ai._post, fake_post
    raw = {"is_ticket": True, "kind": "flight", "title": "İstanbul → Ankara TK2124", "date": D(3), "time": "7:45",
           "venue": "İstanbul Havalimanı", "address": "", "seat": "14C", "booking_code": "XK7Q2L", "holder": "Şeyma Özkul",
           "price": "1249.9"}
    # PDF: yazısı çıkarılıp metin olarak gönderilir (resim yok)
    replies.append(openai_reply(raw))
    with app.app_context():
        r = assistant.read_ticket(ticket_pdf(), "application/pdf")
    assert (r["is_ticket"], r["kind"], r["date"], r["time"], r["price"], r["seat"]) == (True, "flight", D(3), "07:45", 1249.9, "14C")
    content = requests[-1]["messages"][1]["content"]
    assert isinstance(content, str) and "PNR: XK7Q2L" in content and "Yolcu ŞEYMA ÖZKUL" in content
    assert "flight" in json.dumps(requests[-1]["response_format"])
    # Fotoğraf: JPEG'e çevrilip resim olarak
    replies.append(openai_reply({**raw, "kind": "uzay", "price": None, "time": None, "date": "bozuk"}))
    with app.app_context():
        r = assistant.read_ticket(png(), "image/png")
    assert requests[-1]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert (r["kind"], r["price"], r["time"], r["date"]) == ("other", None, None, None)
    # Başlıksız "bilet" bilet sayılmaz; taranmış PDF sağlayıcıya hiç gitmez
    replies.append(openai_reply({**raw, "title": "  "}))
    with app.app_context():
        assert assistant.read_ticket(png())["is_ticket"] is False
    n = len(requests)
    try:
        with app.app_context():
            assistant.read_ticket(image_pdf(), "application/pdf")
        raise AssertionError("taranmış PDF hata vermeli")
    except ai.AIError as e:
        assert "okunamadı" in str(e) and len(requests) == n

    # Web: "📷 Biletten oku"
    assert "Biletten oku" in ADMIN.text("/biletler/")
    replies.append(openai_reply(raw))
    r = ADMIN.post("/biletler/oku", data={"file": (io.BytesIO(ticket_pdf()), "eticket.pdf")},
                   content_type="multipart/form-data")
    tid = tid_of("İstanbul → Ankara TK2124")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/biletler/{tid}")
    t = ticket(tid)
    assert (t["kind"], t["starts_on"], t["starts_at"], t["booking_code"], t["price"]) == ("flight", D(3), "07:45", "XK7Q2L", 1249.9)
    assert atts(tid)[0]["mime"] == "application/pdf"
    assert "Bilet okundu ve eklendi" in ADMIN.text(f"/biletler/{tid}")
    before = count()
    replies.append(openai_reply({**raw, "is_ticket": False}))
    r = ADMIN.post("/biletler/oku", data={"file": (io.BytesIO(png()), "kedi.png")}, content_type="multipart/form-data",
                   follow_redirects=True)
    assert "bilet gibi görünmüyor" in r.get_data(as_text=True) and count() == before
    r = ADMIN.post("/biletler/oku", data={"file": (io.BytesIO(image_pdf()), "tarama.pdf")}, content_type="multipart/form-data",
                   follow_redirects=True)
    assert "Bilet okunamadı" in r.get_data(as_text=True) and count() == before
    # Yapay zekâ kapalıysa düğme yok, adres uyarır
    run("UPDATE users SET ai_enabled = 0 WHERE id = 1")
    assert "Biletten oku" not in ADMIN.text("/biletler/")
    r = ADMIN.post("/biletler/oku", data={"file": (io.BytesIO(png()), "a.png")}, content_type="multipart/form-data",
                   follow_redirects=True)
    assert "Yapay zekâ kapalı" in r.get_data(as_text=True) and count() == before
    run("UPDATE users SET ai_enabled = 1 WHERE id = 1")
    assert not replies
    ai._post = real_post
    print("  read ticket OK")


def test_access():
    tid, _r = add(ADMIN, "Gizli bilet", booking_code="SECRET1", files=[("b.pdf", ticket_pdf())])
    assert AYSE_C.get(f"/biletler/{tid}").status_code == 404
    assert AYSE_C.get(f"/biletler/{tid}/goster").status_code == 404
    for path in ("duzenle", "arsiv", "sil"):
        assert AYSE_C.post(f"/biletler/{tid}/{path}", data={"title": "X", "starts_on": D(1)}).status_code == 404
    t = ticket(tid)
    assert (t["title"], t["archived"]) == ("Gizli bilet", 0) and len(atts(tid)) == 1
    assert AYSE_C.get(f"/dosya/{atts(tid)[0]['id']}").status_code == 404
    assert "Gizli bilet" not in AYSE_C.text("/biletler/") and "SECRET1" not in AYSE_C.text("/ara/?q=secret1")
    print("  access OK")


def test_trash():
    tid, _r = add(ADMIN, "Silinecek bilet", kind="train", starts_on=D(6), seat="Vagon 3",
                  files=[("b.pdf", ticket_pdf()), ("c.png", png())])
    before = [(a["id"], a["filename"]) for a in atts(tid)]
    r = ADMIN.post(f"/biletler/{tid}/sil", follow_redirects=True)
    assert "çöp kutusuna taşındı" in r.get_data(as_text=True)
    assert ticket(tid) is None and not atts(tid)
    assert all(os.path.exists(os.path.join(app.config["UPLOAD_DIR"], f)) for _i, f in before)  # dosyalar diskte durur
    item = one("SELECT * FROM trash WHERE module = 'tickets' ORDER BY id DESC LIMIT 1")
    assert item["label"] == "🚆 Silinecek bilet" and item["user_id"] == 1
    assert "Silinecek bilet" in ADMIN.text("/cop-kutusu/")
    AYSE_C.post(f"/cop-kutusu/{item['id']}/geri")  # başkası geri getiremez
    assert ticket(tid) is None
    ADMIN.post(f"/cop-kutusu/{item['id']}/geri")
    assert ticket(tid)["seat"] == "Vagon 3" and [(a["id"], a["filename"]) for a in atts(tid)] == before
    page = ADMIN.text(f"/biletler/{tid}/goster")
    assert f"/dosya/{before[0][0]}" in page and f"/dosya/{before[1][0]}" in page
    assert ADMIN.get(f"/dosya/{before[0][0]}").status_code == 200
    print("  trash OK")


if __name__ == "__main__":
    test_pdf_text()
    test_crud()
    test_files_and_quota()
    test_show_and_qr()
    test_upcoming_and_past()
    test_expense()
    test_integrations()
    test_telegram_reminders()
    test_telegram_add()
    test_read_ticket()
    test_access()
    test_trash()
    print("OK")
