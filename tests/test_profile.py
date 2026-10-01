"""Profil sayfası: adres/link doğrulama, açık/kapalı, herkese açık sayfa (sızıntı, kaçış, noindex, başlıklar),
vCard, fotoğraf (EXIF, boyut), QR, ziyaret sayacı, kullanıcı silinince 404.

Çalıştır: .venv/Scripts/python tests/test_profile.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

from PIL import Image  # noqa: E402

app = make_app()

from pano.auth import create_user  # noqa: E402
from pano.db import execute, query_one  # noqa: E402
from pano.modules import profile  # noqa: E402

with app.app_context():
    AYSE_ID = create_user("ayse", "ayse12345")
    execute("UPDATE users SET display_name = 'Şule Çiçek' WHERE id = 1")
    execute("UPDATE users SET display_name = 'Şule Çiçek' WHERE id = ?", (AYSE_ID,))
    # Sayfadan asla sızmaması gereken veriler
    execute("INSERT INTO notes (user_id, title, content) VALUES (1, 'GIZLI-NOT', 'gizli not içeriği')")
    execute("INSERT INTO expenses (user_id, amount, category, note, date) VALUES (1, 999, 'Market', 'GIZLI-HARCAMA',"
            " '2026-10-01')")
ADMIN = Client(app)
AYSE = Client(app, "ayse", "ayse12345")
RAW = app.test_client()

BASE = {"slug": "sule-cicek", "enabled": "1", "noindex": "1", "display_name": "Şule Çiçek",
        "headline": "Yazılım geliştirici · Konya", "bio": "", "accent": "teal", "email": "", "phone": "",
        "location": "", "link_label": [], "link_url": []}


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def save(client, **fields):
    data = {**BASE, **fields}
    for key in ("enabled", "noindex", "remove_photo"):
        if not data.get(key):
            data.pop(key, None)   # işaretsiz kutu formda hiç gönderilmez
    return client.post("/profil-sayfasi/", data=data, content_type="multipart/form-data")


def flashed(resp):
    return resp.get_data(as_text=True)


def test_slug():
    assert profile.slugify("Şule Çağrı Öztürk") == "sule-cagri-ozturk"
    assert profile.slugify("İSMAİL IŞIK!") == "ismail-isik" and profile.slugify("  Gönül -- Ünlü ") == "gonul-unlu"
    # İlk açılışta addan öneri (Türkçe harfler dönüştürülmüş)
    assert 'value="sule-cicek"' in ADMIN.text("/profil-sayfasi/")
    for bad in ("ab", "a" * 31, "-abc", "abc-", "ab_c", "a b c", "ab.cd"):
        r = save(ADMIN, slug=bad)
        assert r.status_code == 400 and "küçük harf (a-z)" in flashed(r), bad
    r = save(ADMIN, slug="Şule Çiçek!")
    assert r.status_code == 400 and "Öneri: “sule-cicek”" in flashed(r)
    assert 'value="Şule Çiçek!"' in flashed(r)   # yazılanlar kaybolmaz
    for reserved in ("admin", "api", "static", "giris"):
        r = save(ADMIN, slug=reserved)
        assert r.status_code == 400 and "ayrılmış" in flashed(r), reserved
    assert one("SELECT COUNT(*) AS n FROM public_profiles")["n"] == 0
    # Büyük harf ve Türkçe harfler kayıtta dönüştürülür
    assert save(ADMIN, slug="ŞULE-ÇİÇEK", enabled="").status_code == 302
    assert one("SELECT slug FROM public_profiles WHERE user_id = 1")["slug"] == "sule-cicek"
    # Başkasının adresi alınamaz; öneri boşta olanı verir
    r = save(AYSE, slug="sule-cicek")
    assert r.status_code == 400 and "başka biri tarafından alınmış" in flashed(r)
    assert 'value="sule-cicek-2"' in AYSE.text("/profil-sayfasi/")
    assert save(ADMIN, slug="sule-cicek", enabled="").status_code == 302   # kendi adresini yeniden kaydedebilir
    print("  slug OK")


def test_links():
    for bad in ("javascript:alert(1)", "JavaScript:alert(1)", " javascript:alert(1)", "data:text/html,<script>x</script>",
                "vbscript:msgbox(1)", "java\tscript:alert(1)", "https://", "file:///etc/passwd", "mailto:"):
        r = save(ADMIN, link_label=["Kötü"], link_url=[bad])
        assert r.status_code == 400 and "linki geçersiz" in flashed(r), bad
    r = save(ADMIN, link_label=["Etiket"], link_url=[""])
    assert r.status_code == 400 and "adresi boş" in flashed(r)
    r = save(ADMIN, link_label=[f"L{i}" for i in range(13)], link_url=[f"https://ornek.com/{i}" for i in range(13)])
    assert r.status_code == 400 and "En fazla 12" in flashed(r)
    labels = ["Web", "", "Instagram", "", "Ara", "<b>kalın</b>"]
    urls = ["https://ornek.com", "", "instagram.com/sule", "mailto:sule@ornek.com", "tel:+905321234567", "https://x.com/s"]
    assert save(ADMIN, link_label=labels, link_url=urls, enabled="").status_code == 302
    links = profile._links(one("SELECT links FROM public_profiles WHERE user_id = 1")["links"])
    assert [l["url"] for l in links] == ["https://ornek.com", "https://instagram.com/sule", "mailto:sule@ornek.com",
                                         "tel:+905321234567", "https://x.com/s"]
    assert [l["label"] for l in links][:3] == ["Web", "Instagram", "sule@ornek.com"]   # boş satır atlanır, etiket türetilir
    # Elle bozulmuş kayıt bile sayfaya güvensiz link çıkaramaz
    assert profile._links('[{"label": "x", "url": "javascript:alert(1)"}, {"label": "y"}]') == []
    assert profile._links("bozuk json") == []
    print("  links OK")


def test_toggle_and_404():
    missing = RAW.get("/p/olmayan-profil")
    disabled = RAW.get("/p/sule-cicek")   # şu an kapalı
    assert missing.status_code == disabled.status_code == 404
    assert missing.get_data() == disabled.get_data()   # kapalı ile olmayan aynı görünür
    assert AYSE.get("/p/sule-cicek").status_code == 404
    assert RAW.get("/p/sule-cicek/kisi.vcf").status_code == 404
    # Sahibi kapalıyken önizleyebilir
    page = ADMIN.text("/p/sule-cicek")
    assert "Sayfa kapalı: şu an sadece sen görüyorsun" in page
    assert "Kapalı" in ADMIN.text("/profil-sayfasi/")
    assert save(ADMIN, link_label=["Web"], link_url=["https://ornek.com"]).status_code == 302
    r = RAW.get("/p/sule-cicek")
    assert r.status_code == 200 and "Yayında" in ADMIN.text("/profil-sayfasi/")
    assert RAW.get("/p/SULE-CICEK").status_code == 200   # büyük harfle yazılan adres de açılır
    print("  toggle/404 OK")


def test_public_page():
    save(ADMIN, bio="Merhaba <script>alert('x')</script>\nİkinci satır", email="sule@ornek.com",
         phone="0532 123 45 67", location="Meram, Konya",
         link_label=["Web", "Mail", "<b>kalın</b>"], link_url=["https://ornek.com", "mailto:sule@ornek.com", "https://x.com/s"])
    r = RAW.get("/p/sule-cicek")
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and "Set-Cookie" not in r.headers   # girişsiz: oturum/CSRF çerezi yok
    assert "Şule Çiçek" in page and "Yazılım geliştirici · Konya" in page and "📍 Meram, Konya" in page
    # Kaçış: kullanıcı metni HTML olarak çalışmaz; sayfada hiç betik yok
    assert "&lt;script&gt;alert(&#39;x&#39;)&lt;/script&gt;" in page and "<script" not in page
    assert "&lt;b&gt;kalın&lt;/b&gt;" in page and "<b>kalın" not in page
    assert "Merhaba &lt;script&gt;alert(&#39;x&#39;)&lt;/script&gt;\nİkinci satır" in page   # satır sonu korunur (pre-line)
    # Linkler ve iletişim butonları
    assert '<a href="https://ornek.com" rel="noopener noreferrer" target="_blank">Web</a>' in page
    assert '<a href="mailto:sule@ornek.com" rel="noopener noreferrer">Mail</a>' in page
    assert 'href="tel:+905321234567"' in page and "📞 Ara" in page
    assert 'href="https://wa.me/905321234567" target="_blank" rel="noopener noreferrer"' in page and "💬 WhatsApp" in page
    assert 'href="mailto:sule@ornek.com">✉️ E-posta' in page and "📇 Rehbere ekle" in page and "Kişisel Pano" in page
    assert "--accent: #0f766e" in page   # seçilen vurgu rengi
    # Panoya ait hiçbir şey yok: menü, kullanıcı adı, CSRF, diğer kayıtlar
    for leak in ("GIZLI-NOT", "GIZLI-HARCAMA", "gizli not", "_csrf", "topbar", "bottomnav", "Çıkış", "admin",
                 "/profil-sayfasi", "Düzenle"):
        assert leak not in page, leak
    # Arama motoru: varsayılan noindex
    assert '<meta name="robots" content="noindex, nofollow">' in page and r.headers["X-Robots-Tag"] == "noindex, nofollow"
    # Güvenlik başlıkları
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'none'" in r.headers["Content-Security-Policy"] and "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    # Başka kullanıcı girişliyken de sadece profil görünür
    other = AYSE.text("/p/sule-cicek")
    assert "ayse" not in other and "Düzenle" not in other
    # noindex kapatılınca meta da kalkar
    save(ADMIN, noindex="")
    r = RAW.get("/p/sule-cicek")
    assert "noindex" not in r.get_data(as_text=True) and "X-Robots-Tag" not in r.headers
    # Boş alan gösterilmez
    save(ADMIN, noindex="1")
    page = RAW.get("/p/sule-cicek").get_data(as_text=True)
    assert "📞" not in page and "WhatsApp" not in page and "✉️" not in page and "📍" not in page
    print("  public page OK")


def test_vcard():
    save(ADMIN, display_name="Şule Çiçek Öztürk", headline="Geliştirici; Konya, TR", email="sule@ornek.com",
         phone="+90 532 123 45 67", location="Meram, Konya", bio="Satır 1, virgül\nSatır 2; noktalı \\ ters " + "ğ" * 60)
    r = RAW.get("/p/sule-cicek/kisi.vcf")
    assert r.status_code == 200 and r.headers["Content-Type"] == "text/vcard; charset=utf-8"
    assert r.headers["Content-Disposition"] == 'attachment; filename="sule-cicek.vcf"'
    raw = r.get_data()
    assert raw.count(b"\r\n") > 5 and b"\n" not in raw.replace(b"\r\n", b"")   # satır sonları CRLF
    assert all(len(line) <= 75 for line in raw.split(b"\r\n"))   # uzun satırlar katlanır
    text = raw.decode("utf-8").replace("\r\n ", "")   # katlamayı aç
    lines = text.split("\r\n")
    assert lines[0] == "BEGIN:VCARD" and lines[1] == "VERSION:3.0" and lines[-2] == "END:VCARD"
    assert "N:Öztürk;Şule Çiçek;;;" in lines and "FN:Şule Çiçek Öztürk" in lines
    assert "TITLE:Geliştirici\\; Konya\\, TR" in lines
    assert "TEL;TYPE=CELL:+905321234567" in lines and "EMAIL;TYPE=INTERNET:sule@ornek.com" in lines
    assert "URL:http://localhost/p/sule-cicek" in lines and "ADR:;;;Meram\\, Konya;;;" in lines
    assert "NOTE:Satır 1\\, virgül\\nSatır 2\\; noktalı \\\\ ters " + "ğ" * 60 in lines
    print("  vcard OK")


def jpeg_with_exif(size=(1200, 800)):
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x010F] = "GizliKamera"     # Make
    exif[0x0132] = "2026:01:01 10:00:00"
    Image.new("RGB", size, "red").save(buf, "JPEG", exif=exif.tobytes())
    assert Image.open(io.BytesIO(buf.getvalue())).getexif()[0x010F] == "GizliKamera"
    return buf.getvalue()


def test_photo():
    r = save(ADMIN, photo=(io.BytesIO(jpeg_with_exif()), "foto.jpg"))
    assert r.status_code == 302
    data = bytes(one("SELECT photo FROM public_profiles WHERE user_id = 1")["photo"])
    img = Image.open(io.BytesIO(data))
    assert img.format == "JPEG" and img.size == (400, 400)   # kare kırpıldı, küçültüldü
    assert not img.getexif() and "exif" not in img.info and b"GizliKamera" not in data   # EXIF silindi
    assert not os.path.isdir(os.path.join(app.config["UPLOAD_DIR"], "1"))   # diske yazılmaz
    r = RAW.get("/p/sule-cicek/foto.jpg")
    assert r.status_code == 200 and r.data == data and r.headers["Content-Type"] == "image/jpeg"
    assert r.headers["Cache-Control"] == "public, max-age=3600" and r.headers["X-Content-Type-Options"] == "nosniff"
    page = RAW.get("/p/sule-cicek").get_data(as_text=True)
    assert "/p/sule-cicek/foto.jpg?v=" in page and 'property="og:image"' in page
    # Küçük resim büyütülmez; saydam PNG beyaz zemine oturur
    buf = io.BytesIO()
    Image.new("RGBA", (120, 80), (0, 0, 255, 0)).save(buf, "PNG")
    save(ADMIN, photo=(io.BytesIO(buf.getvalue()), "kucuk.png"))
    assert Image.open(io.BytesIO(RAW.get("/p/sule-cicek/foto.jpg").data)).size == (80, 80)
    # Resim olmayan dosya reddedilir, eski fotoğraf durur
    r = save(ADMIN, photo=(io.BytesIO(b"<html>degil</html>"), "kotu.jpg"))
    assert r.status_code == 400 and "Fotoğraf okunamadı" in flashed(r)
    assert one("SELECT photo IS NOT NULL AS x FROM public_profiles WHERE user_id = 1")["x"] == 1
    # Kaldır
    assert save(ADMIN, remove_photo="1").status_code == 302
    assert one("SELECT photo FROM public_profiles WHERE user_id = 1")["photo"] is None
    assert RAW.get("/p/sule-cicek/foto.jpg").status_code == 404
    save(ADMIN, avatar_emoji="👩‍💻")
    assert "👩‍💻" in RAW.get("/p/sule-cicek").get_data(as_text=True)
    print("  photo OK")


def test_qr():
    page = ADMIN.text("/profil-sayfasi/")
    assert "<svg" in page and 'class="qr"' in page and "http://localhost/p/sule-cicek" in page
    r = ADMIN.get("/profil-sayfasi/qr.png")
    assert r.status_code == 200 and r.headers["Content-Type"] == "image/png"
    assert "attachment" in r.headers["Content-Disposition"] and "profil-sule-cicek-qr.png" in r.headers["Content-Disposition"]
    img = Image.open(io.BytesIO(r.data))
    assert img.format == "PNG" and img.size[0] >= 600 and img.size[0] == img.size[1]
    r.close()
    assert AYSE.get("/profil-sayfasi/qr.png").status_code == 404   # profili yok
    assert RAW.get("/profil-sayfasi/").status_code == 302 and RAW.get("/profil-sayfasi/qr.png").status_code == 302
    print("  qr OK")


def test_views():
    execute_sql("UPDATE public_profiles SET views = 0 WHERE user_id = 1")
    RAW.get("/p/sule-cicek")
    RAW.get("/p/sule-cicek")
    RAW.head("/p/sule-cicek")
    assert one("SELECT views FROM public_profiles WHERE user_id = 1")["views"] == 2
    ADMIN.get("/p/sule-cicek")   # sahibi saymaz
    assert one("SELECT views FROM public_profiles WHERE user_id = 1")["views"] == 2
    AYSE.get("/p/sule-cicek")
    assert one("SELECT views FROM public_profiles WHERE user_id = 1")["views"] == 3
    assert "👁️ 3 ziyaret" in ADMIN.text("/profil-sayfasi/")
    print("  views OK")


def execute_sql(sql, args=()):
    with app.app_context():
        execute(sql, args)


def test_user_delete():
    assert save(AYSE, slug="ayse-sayfa", display_name="Ayşe").status_code == 302
    assert RAW.get("/p/ayse-sayfa").status_code == 200
    ADMIN.post(f"/yonetim/kullanicilar/{AYSE_ID}/sil")
    assert one("SELECT COUNT(*) AS n FROM public_profiles WHERE user_id = ?", (AYSE_ID,))["n"] == 0
    assert RAW.get("/p/ayse-sayfa").status_code == 404 and RAW.get("/p/ayse-sayfa/kisi.vcf").status_code == 404
    assert RAW.get("/p/sule-cicek").status_code == 200   # diğer profil durur
    print("  user delete OK")


if __name__ == "__main__":
    test_slug()
    test_links()
    test_toggle_and_404()
    test_public_page()
    test_vcard()
    test_photo()
    test_qr()
    test_views()
    test_user_delete()
    print("OK")
