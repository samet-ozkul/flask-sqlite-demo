"""Belge tarayıcı: fotoğraflardan PDF, sıra/döndürme/EXIF, modlar, hedefler (indir, not, Aktar, Telegram), sınırlar.

Çalıştır: .venv/Scripts/python tests/test_scanner.py
"""
import html
import io
import os
import re
import sys
import time
import warnings
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, csrf, make_app  # noqa: E402

from PIL import Image, ImageDraw, ImageStat  # noqa: E402

app = make_app()
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.storage as storage  # noqa: E402
import pano.telegram as tg  # noqa: E402
import pano.todo_reminders as todo  # noqa: E402
from pano.db import execute, query, query_one  # noqa: E402
from pano.modules import scanner  # noqa: E402
from pano.utils import TZ  # noqa: E402

CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {}, files)) or {"message_id": 1}
todo.now_local = lambda: datetime(2026, 10, 1, 14, 35, tzinfo=TZ)

ADMIN = Client(app)
MB = 1024 * 1024


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def rows(sql, args=()):
    with app.app_context():
        return query(sql, args)


def run(sql, args=()):
    with app.app_context():
        execute(sql, args)


def text(r):
    return html.unescape(r.get_data(as_text=True))


# ---------- Örnek resimler ----------
def photo(size=(300, 200), fmt="JPEG", orientation=None, **kw):
    """Mavi zemin, sol üst köşede kırmızı işaret (döndürmeyi anlamak için)."""
    img = Image.new("RGB", size, (40, 60, 200))
    ImageDraw.Draw(img).rectangle((0, 0, size[0] // 4, size[1] // 4), fill=(230, 20, 20))
    buf = io.BytesIO()
    if orientation:
        exif = Image.Exif()
        exif[0x0112] = orientation
        kw["exif"] = exif.tobytes()
    img.save(buf, fmt, **({"quality": 90} if fmt == "JPEG" else {}), **kw)
    return buf.getvalue()


def document_photo(size=(600, 800)):
    """Gri kâğıt (185) üzerinde koyu yazı satırları (70)."""
    img = Image.new("L", size, 185)
    d = ImageDraw.Draw(img)
    for y in range(80, size[1] - 80, 30):
        d.rectangle((60, y, size[0] - 60, y + 10), fill=70)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=95)
    return buf.getvalue()


def noise_photo(size=(1754, 1240)):
    img = Image.merge("RGB", [Image.effect_noise(size, 60) for _ in range(3)])
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)
    return buf.getvalue()


# ---------- PDF çözümleme ----------
IMAGE_OBJ = re.compile(rb"/Width (\d+)\n/Height (\d+)\n/Filter /DCTDecode\n/BitsPerComponent 8\n"
                       rb"/ColorSpace /(\w+)\n/Length (\d+)\n>>stream\n")


def pdf_pages(data):
    """[{w, h, space, box, img}] — sayfa sırasıyla (Pillow sayfa resimlerini sırayla yazar)."""
    assert data[:5] == b"%PDF-", data[:20]
    count = len(re.findall(rb"/Type /Page\b", data))  # /Pages sayılmaz
    assert re.search(rb"/Count %d\b" % count, data)
    boxes = [(float(a), float(b)) for a, b in re.findall(rb"/MediaBox \[ 0 0 ([\d.]+) ([\d.]+) \]", data)]
    out = []
    for m, box in zip(IMAGE_OBJ.finditer(data), boxes):
        start, length = m.end(), int(m.group(4))
        out.append({"w": int(m.group(1)), "h": int(m.group(2)), "space": m.group(3).decode(), "box": box,
                    "img": Image.open(io.BytesIO(data[start:start + length]))})
    assert len(out) == count == len(boxes)
    return out


def red_corner(img):
    """Kırmızı işaretin bulunduğu köşe: tl / tr / bl / br."""
    img = img.convert("RGB")
    w, h = img.size
    for name, (x, y) in {"tl": (3, 3), "tr": (w - 4, 3), "bl": (3, h - 4), "br": (w - 4, h - 4)}.items():
        r, g, b = img.getpixel((x, y))
        if r > 150 and g < 100 and b < 100:
            return name
    return None


def post(client, files, follow=False, headers=None, **extra):
    data = {"files": [(io.BytesIO(content), name) for name, content in files], **extra}
    return client.post("/tara/pdf", data=data, content_type="multipart/form-data", follow_redirects=follow,
                       headers=headers or {})


def upload_files():
    base = app.config["UPLOAD_DIR"]
    return sorted(os.path.join(r, n) for r, _d, names in os.walk(base) for n in names) if os.path.isdir(base) else []


# ---------- Testler ----------
def test_page_and_access():
    page = text(ADMIN.get("/tara/"))
    assert 'capture="environment"' in page and 'accept="image/*" multiple' in page and "scanner.js?v=2" in page
    assert 'value="Tarama 01.10.2026 14-35"' in page
    assert "fotoğraflar kaydedilmez, sadece seçtiğin hedefe PDF gider" in page
    assert 'value="telegram"' not in page  # Telegram bağlı değil
    assert "Belge Tara" in text(ADMIN.get("/menu")) and "Araçlar" in text(ADMIN.get("/menu"))
    # Girişsiz erişilemez
    raw = app.test_client()
    r = raw.get("/tara/")
    assert r.status_code == 302 and "/giris" in r.headers["Location"]
    token = csrf(raw.get("/giris"))
    r = raw.post("/tara/pdf", data={"_csrf": token, "files": [(io.BytesIO(photo()), "a.jpg")]},
                 content_type="multipart/form-data")
    assert r.status_code == 302 and "/giris" in r.headers["Location"]
    print("  page/access OK")


def test_download_order_rotation():
    before = upload_files()
    r = post(ADMIN, [("a.jpg", photo((300, 200))), ("b.png", photo((200, 400), "PNG")),
                     ("c.webp", photo((250, 250), "WEBP"))],
             title="Fatura: Ekim/2026 ğüşİ", mode="color", target="download", rotations="0,90,0", dl="abc123xyz")
    assert r.status_code == 200 and r.mimetype == "application/pdf"
    cd = r.headers["Content-Disposition"]
    assert cd.startswith("attachment") and "filename*=UTF-8''Fatura%20Ekim%202026%20%C4%9F%C3%BC%C5%9F%C4%B0.pdf" in cd, cd
    assert r.headers["Cache-Control"] == "no-store"
    assert "tara_dl=abc123xyz" in r.headers["Set-Cookie"] and "Path=/tara/" in r.headers["Set-Cookie"]
    data = r.data
    assert "Fatura".encode("utf-16-be") in data  # PDF başlığı (metadata)
    pages = pdf_pages(data)
    assert [p["box"] for p in pages] == [(144.0, 96.0), (192.0, 96.0), (120.0, 120.0)]  # 150 dpi, sıra korunur
    assert [(p["w"], p["h"]) for p in pages] == [(300, 200), (400, 200), (250, 250)]
    assert all(p["space"] == "DeviceRGB" for p in pages)
    assert red_corner(pages[0]["img"]) == "tl" and red_corner(pages[1]["img"]) == "tr"  # 90° saat yönünde
    # Sıra değişince sayfalar da değişir; 180 / 270 derece
    data = post(ADMIN, [("c.webp", photo((250, 250), "WEBP")), ("a.jpg", photo((300, 200))),
                        ("a2.jpg", photo((300, 200)))], mode="color", rotations="0,180,270").data
    pages = pdf_pages(data)
    assert [(p["w"], p["h"]) for p in pages] == [(250, 250), (300, 200), (200, 300)]
    assert red_corner(pages[1]["img"]) == "br" and red_corner(pages[2]["img"]) == "bl"
    # Varsayılan başlık ve geçersiz döndürme değeri
    r = post(ADMIN, [("a.jpg", photo())], rotations="45,x")
    assert 'filename="Tarama 01.10.2026 14-35.pdf"' in r.headers["Content-Disposition"]
    assert not any("tara_dl" in c for c in r.headers.getlist("Set-Cookie"))
    assert pdf_pages(r.data)[0]["box"] == (144.0, 96.0)
    assert upload_files() == before  # fotoğraflar ve PDF diske yazılmaz
    print("  download/order/rotation OK")


def test_exif_and_large():
    # EXIF 6: telefonda dik tutulup çekilmiş; kayıtlı 300×200, gösterimde 200×300 ve işaret sağ üstte
    page = pdf_pages(post(ADMIN, [("dik.jpg", photo((300, 200), orientation=6))], mode="color").data)[0]
    assert (page["w"], page["h"]) == (200, 300) and red_corner(page["img"]) == "tr"
    # EXIF + kullanıcı döndürmesi birlikte
    page = pdf_pages(post(ADMIN, [("dik.jpg", photo((300, 200), orientation=6))], mode="color", rotations="90").data)[0]
    assert (page["w"], page["h"]) == (300, 200) and red_corner(page["img"]) == "br"
    # Büyük fotoğraf: uzun kenar 1754 px (A4 150 dpi)
    page = pdf_pages(post(ADMIN, [("buyuk.jpg", photo((4000, 3000)))], mode="color").data)[0]
    assert page["w"] == 1754 and abs(page["h"] - 1315.5) <= 1 and red_corner(page["img"]) == "tl"
    assert abs(page["box"][0] - 841.92) < 0.01
    page = pdf_pages(post(ADMIN, [("dikey.png", photo((1000, 2500), "PNG"))], mode="gray").data)[0]
    assert page["h"] == 1754 and page["w"] in (701, 702) and page["space"] == "DeviceGray"
    # Saydam PNG beyaz zemine oturur
    buf = io.BytesIO()
    Image.new("RGBA", (100, 100), (0, 0, 0, 0)).save(buf, "PNG")
    page = pdf_pages(post(ADMIN, [("saydam.png", buf.getvalue())], mode="color").data)[0]
    assert ImageStat.Stat(page["img"]).mean[0] > 245
    print("  exif/large OK")


def test_modes():
    def paper_and_text(mode):
        page = pdf_pages(post(ADMIN, [("belge.jpg", document_photo())], mode=mode).data)[0]
        img = page["img"].convert("L")
        paper = ImageStat.Stat(img.crop((10, 10, 590, 70))).mean[0]
        ink = ImageStat.Stat(img.crop((80, 82, 520, 88))).mean[0]
        return page["space"], paper, ink

    space, paper, ink = paper_and_text("color")
    assert space == "DeviceRGB" and 175 < paper < 195 and 60 < ink < 85
    space, paper, ink = paper_and_text("gray")
    assert space == "DeviceGray" and 175 < paper < 195 and 60 < ink < 85
    space, paper, ink = paper_and_text("doc")
    assert space == "DeviceGray" and paper > 240 and ink < 40, (paper, ink)  # kâğıt beyaz, yazı koyu
    # Geçersiz mod -> belge (varsayılan)
    assert pdf_pages(post(ADMIN, [("a.jpg", photo())], mode="xyz").data)[0]["space"] == "DeviceGray"
    print("  modes OK")


def test_note():
    r = post(ADMIN, [("s1.jpg", document_photo()), ("s2.jpg", document_photo()), ("s3.png", photo((200, 300), "PNG"))],
             title="Kira sözleşmesi", target="note")
    note = one("SELECT * FROM notes ORDER BY id DESC LIMIT 1")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/notlar/{note['id']}")
    assert note["title"] == "Kira sözleşmesi" and note["content"] == "📄 3 sayfalık tarama" and note["user_id"] == 1
    att = one("SELECT * FROM attachments WHERE entity = 'note' AND entity_id = ?", (note["id"],))
    assert att["mime"] == "application/pdf" and att["original_name"] == "Kira sözleşmesi.pdf" and att["thumb"] is None
    with open(os.path.join(app.config["UPLOAD_DIR"], att["filename"]), "rb") as f:
        assert len(pdf_pages(f.read())) == 3
    page = text(ADMIN.get(r.headers["Location"]))
    assert "PDF yeni nota eklendi (3 sayfa" in page and "Kira sözleşmesi.pdf" in page
    # 3 MB'ı aşan tarama bir kez küçültülüp yeniden denenir
    noisy = [(f"g{i}.jpg", noise_photo()) for i in range(4)]
    r = post(ADMIN, noisy, title="Gürültü", mode="color", target="note", follow=True)
    assert "sığması için kalite düşürüldü" in text(r), text(r)[:3000]
    note = one("SELECT * FROM notes ORDER BY id DESC LIMIT 1")
    att = one("SELECT * FROM attachments WHERE entity = 'note' AND entity_id = ?", (note["id"],))
    assert note["title"] == "Gürültü" and att["size"] <= storage.PDF_MAX_BYTES
    with open(os.path.join(app.config["UPLOAD_DIR"], att["filename"]), "rb") as f:
        pages = pdf_pages(f.read())
    assert len(pages) == 4 and pages[0]["w"] == 1240 and abs(pages[0]["box"][0] - 841.92) < 0.5  # sayfa yine A4 boyunda
    # Küçültülünce de sığmazsa not oluşmaz, anlaşılır hata
    count = one("SELECT COUNT(*) AS n FROM notes")["n"]
    original = storage.PDF_MAX_BYTES
    storage.PDF_MAX_BYTES = 100_000
    try:
        page = text(post(ADMIN, noisy[:2], mode="color", target="note", follow=True))
    finally:
        storage.PDF_MAX_BYTES = original
    assert "not eki en fazla" in page and "PDF indir ya da Aktar'a koy" in page
    assert one("SELECT COUNT(*) AS n FROM notes")["n"] == count
    # Kota doluysa not geri silinir
    app.config["STORAGE_QUOTA_MB"] = 0
    try:
        page = text(post(ADMIN, [("a.jpg", photo())], target="note", follow=True))
    finally:
        app.config["STORAGE_QUOTA_MB"] = 350
    assert "Nota eklenemedi" in page and one("SELECT COUNT(*) AS n FROM notes")["n"] == count
    print("  note OK")


def test_transfer():
    r = post(ADMIN, [("a.jpg", photo()), ("b.jpg", photo())], title="Aktarılacak ğ", target="transfer",
             headers={"User-Agent": "Mozilla/5.0 (Linux; Android 14)"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/aktar/")
    row = one("SELECT * FROM transfers WHERE filename = 'Aktarılacak ğ.pdf'")
    assert row and row["mime"] == "application/pdf" and row["device"] == "📱 Android" and row["user_id"] == 1
    assert 3590 < row["expires_at"] - time.time() <= 3600
    with open(os.path.join(app.config["UPLOAD_DIR"], "_aktarma", "1", row["stored_name"]), "rb") as f:
        assert len(pdf_pages(f.read())) == 2
    assert "PDF aktarma kutusuna kondu (2 sayfa" in text(ADMIN.get("/aktar/"))
    print("  transfer OK")


def test_telegram():
    page = text(post(ADMIN, [("a.jpg", photo())], target="telegram", follow=True))
    assert "Önce Ayarlar'dan Telegram'ı bağla" in page and not CALLS
    run("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
    assert 'value="telegram"' in text(ADMIN.get("/tara/"))
    page = text(post(ADMIN, [("a.jpg", photo()), ("b.jpg", photo())], title="Makbuz", target="telegram", follow=True))
    method, params, files = CALLS[-1]
    assert method == "sendDocument" and params["chat_id"] == "100" and params["caption"] == "📄 Makbuz · 2 sayfa"
    name, data, mime = files["document"]
    assert name == "Makbuz.pdf" and mime == "application/pdf" and len(pdf_pages(data)) == 2
    assert "PDF Telegram'a gönderildi (2 sayfa" in page

    def fail(method, params=None, files=None):
        raise tg.TelegramError("Bad Request: chat not found")
    tg._call, ok_call = fail, tg._call
    try:
        assert "Gönderilemedi: Bad Request" in text(post(ADMIN, [("a.jpg", photo())], target="telegram", follow=True))
    finally:
        tg._call = ok_call
    scanner.TELEGRAM_MAX_BYTES = 100
    try:
        sent = len(CALLS)
        assert "en fazla 50 MB" in text(post(ADMIN, [("a.jpg", photo())], target="telegram", follow=True))
        assert len(CALLS) == sent
    finally:
        scanner.TELEGRAM_MAX_BYTES = 50 * MB
    print("  telegram OK")


def test_limits():
    notes = one("SELECT COUNT(*) AS n FROM notes")["n"]
    small = photo((10, 10), "PNG")
    page = text(post(ADMIN, [(f"s{i}.png", small) for i in range(21)], target="note", follow=True))
    assert "En fazla 20 sayfa olabilir; 21 sayfa seçildi" in page
    assert len(pdf_pages(post(ADMIN, [(f"s{i}.png", small) for i in range(20)]).data)) == 20
    assert "Önce fotoğraf çek" in text(post(ADMIN, [], follow=True))
    # Resim olmayan, bozuk ve HEIC dosya: hangi sayfa olduğu söylenir, PDF/not oluşmaz
    page = text(post(ADMIN, [("a.jpg", photo()), ("not.txt", b"merhaba")], target="note", follow=True))
    assert "2. sayfa (not.txt): resim değil" in page
    good = photo((400, 300))
    page = text(post(ADMIN, [("bozuk.jpg", good[:len(good) // 2])], follow=True))
    assert "1. sayfa (bozuk.jpg): resim bozuk, okunamadı" in page
    page = text(post(ADMIN, [("rastgele.jpg", os.urandom(2000))], follow=True))
    assert "resim açılamadı (bozuk ya da desteklenmeyen biçim)" in page
    page = text(post(ADMIN, [("IMG_1.heic", b"\x00\x00\x00\x18ftypheic" + b"\x00" * 100)], follow=True))
    assert "HEIC biçimi desteklenmiyor" in page
    assert one("SELECT COUNT(*) AS n FROM notes")["n"] == notes
    # Dev boyutlu resim (decompression bomb) reddedilir; sınır storage modülünden gelir
    assert Image.MAX_IMAGE_PIXELS == 60_000_000
    Image.MAX_IMAGE_PIXELS = 100_000
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            page = text(post(ADMIN, [("dev.png", photo((400, 300), "PNG"))], follow=True))
    finally:
        Image.MAX_IMAGE_PIXELS = 60_000_000
    assert "1. sayfa (dev.png): resim çok büyük (400×300)" in page
    # İstek 60 MB'ı aşamaz
    assert app.view_functions["scanner.make_pdf"].large_upload_bytes == 60 * MB
    r = post(ADMIN, [("koca.jpg", b"\xff\xd8" + b"0" * (61 * MB))], follow=True, headers={"Referer": "/tara/"})
    assert r.status_code == 200 and "Dosya çok büyük" in text(r)
    print("  limits OK")


if __name__ == "__main__":
    test_page_and_access()
    test_download_order_rotation()
    test_exif_and_large()
    test_modes()
    test_note()
    test_transfer()
    test_telegram()
    test_limits()
    print("OK")
