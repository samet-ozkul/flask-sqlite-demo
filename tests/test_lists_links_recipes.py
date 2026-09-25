"""Listeler, Sonra Bak (linkler) ve Tarifler modülleri için uçtan uca testler.

Çalıştırma: .venv/Scripts/python tests/test_lists_links_recipes.py
"""
import io
import os
import sys
from contextlib import closing

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import Client, make_app  # noqa: E402

app = make_app()
with app.app_context():
    from pano.auth import create_user
    AYSE_ID = create_user("ayse", "ayse12345")
    VELI_ID = create_user("veli", "veli12345")

from pano.db import connect  # noqa: E402


def db_all(sql, args=()):
    with closing(connect(app.config["DATABASE"])) as conn:
        return conn.execute(sql, args).fetchall()


def db_one(sql, args=()):
    rows = db_all(sql, args)
    return rows[0] if rows else None


def db_exec(sql, args=()):
    with closing(connect(app.config["DATABASE"])) as conn:
        conn.execute(sql, args)
        conn.commit()


def T(name):
    """Liste sayfasındaki satır başlığı (placeholder metinleriyle karışmasın)."""
    return f'class="title">{name}<'


def loc(resp):
    return resp.headers.get("Location", "")


admin = Client(app)
ayse = Client(app, "ayse", "ayse12345")
veli = Client(app, "veli", "veli12345")
ADMIN_ID = db_one("SELECT id FROM users WHERE username = 'admin'")["id"]


# =====================================================================
# Tarifler (önce: admin'in hiç alışveriş listesi yokken otomatik liste)
# =====================================================================
def test_recipes():
    assert "Henüz tarif yok" in admin.text("/tarifler/")
    assert "Tarif adı" in admin.text("/tarifler/yeni")

    # başlıksız kaydedilmez
    r = admin.post("/tarifler/yeni", {"title": "", "ingredients": "tuz"})
    assert r.status_code == 200 and "Tarif adı gerekli" in r.get_data(as_text=True)
    assert db_one("SELECT COUNT(*) AS n FROM recipes")["n"] == 0

    r = admin.post("/tarifler/yeni", {
        "title": "Menemen", "servings": "2 kişilik", "tags": "Kahvaltı, pratik, kahvaltı",
        "ingredients": "3 domates\n2 biber\n\n# Üzeri için\n- 4 yumurta\nTuz\n",
        "steps": "1. Doğra\n2. **Pişir**\n<script>alert(1)</script>",
    })
    assert r.status_code == 302, r.status_code
    rec = db_one("SELECT * FROM recipes WHERE title = 'Menemen'")
    rid = rec["id"]
    assert loc(r).endswith(f"/tarifler/{rid}")
    assert rec["tags"] == "kahvaltı, pratik" and rec["user_id"] == ADMIN_ID

    html = admin.text(f"/tarifler/{rid}")
    assert "<strong>Pişir</strong>" in html
    assert "<script>alert(1)</script>" not in html  # md XSS güvenli
    assert "Malzemeleri alışveriş listesine ekle" in html
    assert html.count('name="ing"') == 4 and html.count("checked>") >= 4
    assert "Üzeri için" in html
    assert "Alışveriş” adında yeni bir liste oluşturulacak" in html
    assert 'name="entity" value="recipe"' in html  # ek yükleme formu

    # liste, arama, etiket
    assert "Menemen" in admin.text("/tarifler/")
    assert "Menemen" in admin.text("/tarifler/?q=domates")
    assert "Menemen" in admin.text("/tarifler/?tag=kahvaltı")
    assert "Menemen" not in admin.text("/tarifler/?q=pizza")

    # düzenle
    assert "Tarifi düzenle" in admin.text(f"/tarifler/{rid}/duzenle")
    r = admin.post(f"/tarifler/{rid}/duzenle", {
        "title": "Menemen (soğanlı)", "servings": "3 kişilik", "tags": "kahvaltı",
        "ingredients": "3 domates\n2 biber\n1 soğan\n# Üzeri için\n4 yumurta\nTuz",
        "steps": "Pişir",
    })
    assert r.status_code == 302
    rec = db_one("SELECT * FROM recipes WHERE id = ?", (rid,))
    assert rec["title"] == "Menemen (soğanlı)" and rec["servings"] == "3 kişilik" and rec["updated_at"]

    # alışveriş listesine ekle: hiç liste yok -> "Alışveriş" oluşur. Sadece seçilenler (0, 2, başlık=3 atlanır, 4)
    assert db_one("SELECT COUNT(*) AS n FROM lists")["n"] == 0
    r = admin.post(f"/tarifler/{rid}/alisveris", {"ing": ["0", "2", "3", "4"], "list_id": ""})
    assert r.status_code == 302 and loc(r).endswith(f"/tarifler/{rid}")
    lst = db_one("SELECT * FROM lists WHERE user_id = ?", (ADMIN_ID,))
    assert lst["name"] == "Alışveriş" and lst["kind"] == "shopping" and lst["shared"] == 0
    items = [r["text"] for r in db_all("SELECT text FROM list_items WHERE list_id = ? ORDER BY id", (lst["id"],))]
    assert items == ["3 domates", "1 soğan", "4 yumurta"], items
    assert db_one("SELECT created_by FROM list_items WHERE list_id = ?", (lst["id"],))["created_by"] == ADMIN_ID

    # tekrar: mevcut listeyi kullanır, olanları atlar, yeni liste açmaz
    r = admin.post(f"/tarifler/{rid}/alisveris", {"ing": ["0", "1", "2", "4", "5"]})
    assert r.status_code == 302
    assert db_one("SELECT COUNT(*) AS n FROM lists")["n"] == 1
    items = [r["text"] for r in db_all("SELECT text FROM list_items WHERE list_id = ? ORDER BY id", (lst["id"],))]
    assert items == ["3 domates", "1 soğan", "4 yumurta", "2 biber", "Tuz"], items
    assert "zaten listedeydi" in admin.text(f"/tarifler/{rid}")

    # hiçbiri seçilmedi
    r = admin.post(f"/tarifler/{rid}/alisveris", {"list_id": str(lst["id"])})
    assert r.status_code == 302
    assert "Eklenecek malzeme seçilmedi" in admin.text(f"/tarifler/{rid}")

    # detay sayfası artık listeyi seçenek olarak gösterir
    html = admin.text(f"/tarifler/{rid}")
    assert f'<option value="{lst["id"]}"' in html

    # yapılacaklar listesine malzeme eklenemez
    todo_id = db_exec_insert("INSERT INTO lists (user_id, name, kind) VALUES (?, 'İşler', 'todo')", (ADMIN_ID,))
    r = admin.post(f"/tarifler/{rid}/alisveris", {"ing": ["0"], "list_id": str(todo_id)})
    assert r.status_code == 400, r.status_code
    db_exec("DELETE FROM lists WHERE id = ?", (todo_id,))

    # sahiplik: başka kullanıcı göremez/değiştiremez
    assert ayse.get(f"/tarifler/{rid}").status_code == 404
    assert ayse.get(f"/tarifler/{rid}/duzenle").status_code == 404
    assert ayse.post(f"/tarifler/{rid}/duzenle", {"title": "hack"}).status_code == 404
    assert ayse.post(f"/tarifler/{rid}/alisveris", {"ing": ["0"]}).status_code == 404
    assert ayse.post(f"/tarifler/{rid}/sil").status_code == 404
    assert "Menemen" not in ayse.text("/tarifler/")

    # ayse kendi tarifini admin'in paylaşılmayan listesine ekleyemez
    r = ayse.post("/tarifler/yeni", {"title": "Ayşe'nin çorbası", "ingredients": "mercimek\nsoğan"})
    ayse_rid = db_one("SELECT id FROM recipes WHERE user_id = ?", (AYSE_ID,))["id"]
    assert ayse.post(f"/tarifler/{ayse_rid}/alisveris", {"ing": ["0"], "list_id": str(lst["id"])}).status_code == 404
    # ...ama liste paylaşılınca ekleyebilir (created_by = ayse)
    db_exec("UPDATE lists SET shared = 1 WHERE id = ?", (lst["id"],))
    assert "(admin)" in ayse.text(f"/tarifler/{ayse_rid}")
    r = ayse.post(f"/tarifler/{ayse_rid}/alisveris", {"ing": ["0", "1"], "list_id": str(lst["id"])})
    assert r.status_code == 302
    row = db_one("SELECT * FROM list_items WHERE list_id = ? AND text = 'mercimek'", (lst["id"],))
    assert row and row["created_by"] == AYSE_ID
    db_exec("UPDATE lists SET shared = 0 WHERE id = ?", (lst["id"],))

    # fotoğraf ekle -> listede küçük resim; silince ek de silinir
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(buf, "PNG")
    buf.seek(0)
    r = admin.post("/dosya/yukle", {"entity": "recipe", "entity_id": str(rid), "next": f"/tarifler/{rid}",
                                    "file": (buf, "menemen.png")}, content_type="multipart/form-data")
    assert r.status_code == 302, r.status_code
    att = db_one("SELECT * FROM attachments WHERE entity = 'recipe' AND entity_id = ?", (rid,))
    assert att is not None
    assert f"/dosya/{att['id']}/kucuk" in admin.text("/tarifler/")
    assert f"/dosya/{att['id']}/kucuk" in admin.text(f"/tarifler/{rid}")
    path = os.path.join(app.config["UPLOAD_DIR"], att["filename"])
    assert os.path.exists(path)

    r = admin.post(f"/tarifler/{rid}/sil")
    assert r.status_code == 302 and loc(r).endswith("/tarifler/")
    assert db_one("SELECT * FROM recipes WHERE id = ?", (rid,)) is None
    assert db_one("SELECT * FROM attachments WHERE entity = 'recipe' AND entity_id = ?", (rid,)) is None
    assert not os.path.exists(path)
    assert admin.get(f"/tarifler/{rid}").status_code == 404

    # temizlik: sonraki testler için
    db_exec("DELETE FROM list_items")
    db_exec("DELETE FROM lists")
    db_exec("DELETE FROM recipes")


def db_exec_insert(sql, args=()):
    with closing(connect(app.config["DATABASE"])) as conn:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.lastrowid


# =====================================================================
# Listeler
# =====================================================================
def test_lists():
    assert "Henüz liste yok" in admin.text("/listeler/")

    # boş ad reddedilir
    admin.post("/listeler/yeni", {"name": "  ", "kind": "shopping"})
    assert db_one("SELECT COUNT(*) AS n FROM lists")["n"] == 0

    r = admin.post("/listeler/yeni", {"name": "Market", "kind": "shopping", "shared": "1"})
    assert r.status_code == 302
    market = db_one("SELECT * FROM lists WHERE name = 'Market'")
    assert loc(r).endswith(f"/listeler/{market['id']}")
    assert market["kind"] == "shopping" and market["shared"] == 1 and market["user_id"] == ADMIN_ID
    mid = market["id"]

    admin.post("/listeler/yeni", {"name": "Ev işleri", "kind": "todo"})
    ev = db_one("SELECT * FROM lists WHERE name = 'Ev işleri'")
    assert ev["kind"] == "todo" and ev["shared"] == 0
    eid = ev["id"]

    # geçersiz tür -> todo
    admin.post("/listeler/yeni", {"name": "Garip", "kind": "xxx"})
    garip = db_one("SELECT * FROM lists WHERE name = 'Garip'")
    assert garip["kind"] == "todo"

    html = admin.text("/listeler/")
    assert T("Market") in html and T("Ev işleri") in html and "paylaşılan" in html

    # sayfalar
    html = admin.text(f"/listeler/{mid}")
    assert "süt, ekmek, yumurta" in html and 'name="qty"' in html and "Liste ayarları" in html
    html = admin.text(f"/listeler/{eid}")
    assert 'name="due_date"' in html and 'name="qty"' not in html

    # çoklu ekleme (virgül + satır sonu, boşlar ve tekrarlar atlanır)
    r = admin.post(f"/listeler/{mid}/ekle", {"text": "süt, ekmek\nyumurta,, \n - peynir ,Süt", "qty": "2"})
    assert r.status_code == 302 and loc(r).endswith(f"/listeler/{mid}")
    items = db_all("SELECT * FROM list_items WHERE list_id = ? ORDER BY id", (mid,))
    assert [i["text"] for i in items] == ["süt", "ekmek", "yumurta", "peynir"], [i["text"] for i in items]
    assert all(i["qty"] == "2" and i["created_by"] == ADMIN_ID and i["done"] == 0 for i in items)
    assert "4 madde eklendi" in admin.text(f"/listeler/{mid}")

    # boş madde
    admin.post(f"/listeler/{mid}/ekle", {"text": " , \n"})
    assert db_one("SELECT COUNT(*) AS n FROM list_items WHERE list_id = ?", (mid,))["n"] == 4

    # yapılacaklar: virgül bölmez, due_date saklanır; tarihli maddeler önce
    admin.post(f"/listeler/{eid}/ekle", {"text": "Çamaşır, bulaşık"})
    admin.post(f"/listeler/{eid}/ekle", {"text": "Faturayı öde", "due_date": "2026-09-30"})
    admin.post(f"/listeler/{eid}/ekle", {"text": "Halı yıkat", "due_date": "bozuk"})
    todo = db_all("SELECT * FROM list_items WHERE list_id = ? ORDER BY id", (eid,))
    assert [t["text"] for t in todo] == ["Çamaşır, bulaşık", "Faturayı öde", "Halı yıkat"]
    assert todo[1]["due_date"] == "2026-09-30" and todo[0]["due_date"] is None and todo[2]["due_date"] is None
    admin.get(f"/listeler/{eid}")  # bekleyen flash mesajlarını tüket
    html = admin.text(f"/listeler/{eid}")
    assert html.index("Faturayı öde") < html.index("Çamaşır, bulaşık") < html.index("Halı yıkat")

    # panodan ekleme: next=dashboard
    r = admin.post(f"/listeler/{mid}/ekle", {"text": "zeytin", "next": "dashboard"})
    assert r.status_code == 302 and loc(r) in ("/", "http://localhost/"), loc(r)
    # açık yönlendirme engellenir
    r = admin.post(f"/listeler/{mid}/ekle", {"text": "bal", "next": "//evil.example.com/"})
    assert loc(r).endswith(f"/listeler/{mid}")

    # işaretle / geri al
    sut = db_one("SELECT id FROM list_items WHERE list_id = ? AND text = 'süt'", (mid,))["id"]
    r = admin.post(f"/listeler/madde/{sut}/isaretle")
    assert r.status_code == 302 and loc(r).endswith(f"/listeler/{mid}")
    row = db_one("SELECT * FROM list_items WHERE id = ?", (sut,))
    assert row["done"] == 1 and row["done_at"]
    html = admin.text(f"/listeler/{mid}")
    assert "Tamamlananlar" in html and "Tamamlananları temizle" in html
    assert html.index("ekmek") < html.index("Tamamlananlar") < html.rindex("süt")
    r = admin.post(f"/listeler/madde/{sut}/isaretle", {"next": "/listeler/"})
    assert loc(r).endswith("/listeler/")
    row = db_one("SELECT * FROM list_items WHERE id = ?", (sut,))
    assert row["done"] == 0 and row["done_at"] is None
    r = admin.post(f"/listeler/madde/{sut}/isaretle", {"next": "dashboard"})
    assert loc(r) in ("/", "http://localhost/")
    assert db_one("SELECT done FROM list_items WHERE id = ?", (sut,))["done"] == 1

    # madde sil
    bal = db_one("SELECT id FROM list_items WHERE list_id = ? AND text = 'bal'", (mid,))["id"]
    assert admin.post(f"/listeler/madde/{bal}/sil").status_code == 302
    assert db_one("SELECT * FROM list_items WHERE id = ?", (bal,)) is None

    # ------- paylaşılan liste: ikinci kullanıcı -------
    html = ayse.text("/listeler/")
    assert T("Market") in html and "👤 admin" in html     # sahibinin adı görünür
    assert T("Ev işleri") not in html and T("Garip") not in html
    html = ayse.text(f"/listeler/{mid}")
    assert "ekmek" in html and "Liste ayarları" not in html and "Tamamlananları temizle" not in html

    # ekleyebilir
    r = ayse.post(f"/listeler/{mid}/ekle", {"text": "domates, biber"})
    assert r.status_code == 302
    dom = db_one("SELECT * FROM list_items WHERE list_id = ? AND text = 'domates'", (mid,))
    assert dom["created_by"] == AYSE_ID
    assert "👤 ayse" in admin.text(f"/listeler/{mid}")   # sahibi kimin eklediğini görür
    # işaretleyebilir
    ekmek = db_one("SELECT id FROM list_items WHERE list_id = ? AND text = 'ekmek'", (mid,))["id"]
    assert ayse.post(f"/listeler/madde/{ekmek}/isaretle").status_code == 302
    assert db_one("SELECT done FROM list_items WHERE id = ?", (ekmek,))["done"] == 1
    # başkasının maddesini silemez, kendisininkini silebilir
    assert ayse.post(f"/listeler/madde/{ekmek}/sil").status_code == 403
    assert ayse.post(f"/listeler/madde/{dom['id']}/sil").status_code == 302
    assert db_one("SELECT * FROM list_items WHERE id = ?", (dom["id"],)) is None
    # sahibi başkasının eklediğini silebilir
    biber = db_one("SELECT id FROM list_items WHERE list_id = ? AND text = 'biber'", (mid,))["id"]
    assert admin.post(f"/listeler/madde/{biber}/sil").status_code == 302

    # liste ayarları / silme / temizleme sadece sahibine
    assert ayse.post(f"/listeler/{mid}/ayarlar", {"name": "Hack", "shared": "1"}).status_code == 403
    assert ayse.post(f"/listeler/{mid}/sil").status_code == 403
    assert ayse.post(f"/listeler/{mid}/temizle").status_code == 403
    m = db_one("SELECT * FROM lists WHERE id = ?", (mid,))
    assert m["name"] == "Market" and m["shared"] == 1
    assert db_one("SELECT COUNT(*) AS n FROM list_items WHERE list_id = ? AND done = 1", (mid,))["n"] == 2

    # paylaşılmayan liste: ikinci kullanıcı için her şey 404
    item_e = db_one("SELECT id FROM list_items WHERE list_id = ?", (eid,))["id"]
    assert ayse.get(f"/listeler/{eid}").status_code == 404
    assert ayse.post(f"/listeler/{eid}/ekle", {"text": "x"}).status_code == 404
    assert ayse.post(f"/listeler/madde/{item_e}/isaretle").status_code == 404
    assert ayse.post(f"/listeler/madde/{item_e}/sil").status_code == 404
    assert ayse.post(f"/listeler/{eid}/ayarlar", {"name": "x"}).status_code == 404
    assert ayse.post(f"/listeler/{eid}/sil").status_code == 404
    assert ayse.post(f"/listeler/{eid}/temizle").status_code == 404
    assert ayse.get("/listeler/99999").status_code == 404
    assert db_one("SELECT COUNT(*) AS n FROM list_items WHERE list_id = ?", (eid,))["n"] == 3
    assert db_one("SELECT done FROM list_items WHERE id = ?", (item_e,))["done"] == 0

    # ayse'nin kendi listesi admin'e görünmez
    ayse.post("/listeler/yeni", {"name": "Ayşe özel", "kind": "todo"})
    aid = db_one("SELECT id FROM lists WHERE user_id = ?", (AYSE_ID,))["id"]
    assert T("Ayşe özel") not in admin.text("/listeler/")
    assert admin.get(f"/listeler/{aid}").status_code == 404

    # tamamlananları temizle (sahibi)
    r = admin.post(f"/listeler/{mid}/temizle")
    assert r.status_code == 302
    assert db_one("SELECT COUNT(*) AS n FROM list_items WHERE list_id = ? AND done = 1", (mid,))["n"] == 0
    assert db_one("SELECT COUNT(*) AS n FROM list_items WHERE list_id = ? AND done = 0", (mid,))["n"] > 0

    # ayarlar: yeniden adlandır + paylaşımı kapat -> ayse artık göremez
    r = admin.post(f"/listeler/{mid}/ayarlar", {"name": "Haftalık market"})
    assert r.status_code == 302
    m = db_one("SELECT * FROM lists WHERE id = ?", (mid,))
    assert m["name"] == "Haftalık market" and m["shared"] == 0
    assert ayse.get(f"/listeler/{mid}").status_code == 404
    assert T("Haftalık market") not in ayse.text("/listeler/")
    admin.post(f"/listeler/{mid}/ayarlar", {"name": ""})
    assert db_one("SELECT name FROM lists WHERE id = ?", (mid,))["name"] == "Haftalık market"

    # tamamlananlar bölümü en fazla 30 madde gösterir
    for n in range(35):
        db_exec("INSERT INTO list_items (list_id, text, done, done_at) VALUES (?, ?, 1, CURRENT_TIMESTAMP)",
                (eid, f"eski-is-{n:02d}"))
    html = admin.text(f"/listeler/{eid}")
    assert html.count("eski-is-") == 30 and "Son 30 tanesi" in html

    # listeyi sil (maddeleriyle)
    r = admin.post(f"/listeler/{eid}/sil")
    assert r.status_code == 302 and loc(r).endswith("/listeler/")
    assert db_one("SELECT * FROM lists WHERE id = ?", (eid,)) is None
    assert db_one("SELECT COUNT(*) AS n FROM list_items WHERE list_id = ?", (eid,))["n"] == 0

    # hatırlatıcılar lists.detail(list_id) kullanır
    with app.test_request_context():
        from flask import url_for
        assert url_for("lists.detail", list_id=5) == "/listeler/5"
        assert url_for("lists.add_item", list_id=5) == "/listeler/5/ekle"
        assert url_for("lists.toggle_item", item_id=7) == "/listeler/madde/7/isaretle"


# =====================================================================
# Sonra Bak (linkler)
# =====================================================================
def test_links():
    assert "Henüz link yok" in admin.text("/linkler/")

    # doğrulama
    for bad in ("", "example.com", "ftp://example.com/x", "javascript:alert(1)", "http://"):
        r = admin.post("/linkler/yeni", {"url": bad, "title": "Kötü"})
        assert r.status_code == 200, (bad, r.status_code)
        body = r.get_data(as_text=True)
        assert "http:// veya https://" in body or "Link adresi gerekli" in body, bad
    assert db_one("SELECT COUNT(*) AS n FROM links")["n"] == 0

    # başlıksız -> alan adı
    r = admin.post("/linkler/yeni", {"url": "https://www.example.com/yazi?a=1", "tags": "Okuma, Teknik",
                                     "note": "hafta sonu"})
    assert r.status_code == 302 and loc(r).endswith("/linkler/")
    l1 = db_one("SELECT * FROM links WHERE url = 'https://www.example.com/yazi?a=1'")
    assert l1["title"] == "example.com" and l1["tags"] == "okuma, teknik" and l1["is_read"] == 0
    assert l1["user_id"] == ADMIN_ID
    admin.post("/linkler/yeni", {"url": "http://video.test/izle", "title": "Güzel video", "tags": "video"})
    l2 = db_one("SELECT * FROM links WHERE url = 'http://video.test/izle'")

    # tekrar eklenen link çoğalmaz
    admin.post("/linkler/yeni", {"url": "http://video.test/izle"})
    assert db_one("SELECT COUNT(*) AS n FROM links")["n"] == 2

    html = admin.text("/linkler/")
    assert "example.com" in html and "Güzel video" in html and "hafta sonu" in html
    assert 'href="https://www.example.com/yazi?a=1"' in html and 'rel="noopener noreferrer"' in html

    # okundu işaretle: varsayılan filtreden kaybolur
    r = admin.post(f"/linkler/{l2['id']}/okundu", {"next": "/linkler/?durum=okunmamis"})
    assert r.status_code == 302 and loc(r).endswith("/linkler/?durum=okunmamis")
    assert db_one("SELECT is_read FROM links WHERE id = ?", (l2["id"],))["is_read"] == 1
    assert "Güzel video" not in admin.text("/linkler/")
    assert "Güzel video" in admin.text("/linkler/?durum=okundu")
    assert "example.com/yazi" not in admin.text("/linkler/?durum=okundu")
    html = admin.text("/linkler/?durum=tumu")
    assert "Güzel video" in html and "example.com/yazi" in html
    assert "Güzel video" not in admin.text("/linkler/?durum=bozuk")  # geçersiz -> okunmamış

    # okunmuş link tekrar eklenirse okunmamışa döner
    admin.post("/linkler/yeni", {"url": "http://video.test/izle"})
    assert db_one("SELECT is_read FROM links WHERE id = ?", (l2["id"],))["is_read"] == 0
    admin.post(f"/linkler/{l2['id']}/okundu")
    admin.post(f"/linkler/{l2['id']}/okundu")
    assert db_one("SELECT is_read FROM links WHERE id = ?", (l2["id"],))["is_read"] == 0

    # arama / etiket
    html = admin.text("/linkler/?q=hafta")
    assert "example.com/yazi" in html and "Güzel video" not in html
    html = admin.text("/linkler/?tag=video&durum=tumu")
    assert "Güzel video" in html and "example.com/yazi" not in html

    # düzenle
    assert "Linki düzenle" in admin.text(f"/linkler/{l1['id']}")
    r = admin.post(f"/linkler/{l1['id']}", {"url": "https://example.com/yazi-2", "title": "Uzun yazı",
                                           "tags": "okuma", "note": "", "is_read": "1"})
    assert r.status_code == 302
    row = db_one("SELECT * FROM links WHERE id = ?", (l1["id"],))
    assert row["url"] == "https://example.com/yazi-2" and row["title"] == "Uzun yazı" and row["is_read"] == 1
    r = admin.post(f"/linkler/{l1['id']}", {"url": "bozuk", "title": "x"})
    assert r.status_code == 200 and "http:// veya https://" in r.get_data(as_text=True)
    assert db_one("SELECT url FROM links WHERE id = ?", (l1["id"],))["url"] == "https://example.com/yazi-2"

    # paylaşım hedefi: GET kaydetmez, formu doldurur
    before = db_one("SELECT COUNT(*) AS n FROM links")["n"]
    html = admin.text("/linkler/paylas?title=&text=Harika+bir+yaz%C4%B1+https%3A%2F%2Fblog.example.org%2Fpost-1")
    assert 'value="https://blog.example.org/post-1"' in html, html
    assert 'value="Harika bir yazı"' in html
    html = admin.text("/linkler/paylas?url=https%3A%2F%2Fnews.test%2Fa&title=Haber+ba%C5%9Fl%C4%B1%C4%9F%C4%B1&text=bak+buna")
    assert 'value="https://news.test/a"' in html and 'value="Haber başlığı"' in html and "bak buna" in html
    html = admin.text("/linkler/paylas?title=Ba%C5%9Fl%C4%B1k&text=https%3A%2F%2Fx.test%2Fy.")
    assert 'value="https://x.test/y"' in html and 'value="Başlık"' in html
    assert "Kaydet" in admin.text("/linkler/paylas")
    assert db_one("SELECT COUNT(*) AS n FROM links")["n"] == before
    # paylaşım formundan kaydet
    r = admin.post("/linkler/yeni", {"url": "https://blog.example.org/post-1", "title": "Harika bir yazı"})
    assert r.status_code == 302 and db_one("SELECT COUNT(*) AS n FROM links")["n"] == before + 1
    # giriş yapmamış kullanıcı giriş sayfasına yönlenir, parametreler korunur
    anon = app.test_client()
    r = anon.get("/linkler/paylas?url=https%3A%2F%2Fa.test")
    assert r.status_code == 302 and "/giris" in loc(r) and "paylas" in loc(r)

    # sahiplik
    assert "Güzel video" not in ayse.text("/linkler/?durum=tumu")
    assert ayse.get(f"/linkler/{l2['id']}").status_code == 404
    assert ayse.post(f"/linkler/{l2['id']}", {"url": "https://evil.test"}).status_code == 404
    assert ayse.post(f"/linkler/{l2['id']}/okundu").status_code == 404
    assert ayse.post(f"/linkler/{l2['id']}/sil").status_code == 404
    assert db_one("SELECT url FROM links WHERE id = ?", (l2["id"],))["url"] == "http://video.test/izle"
    # ayse aynı URL'yi kendi hesabına ekleyebilir
    ayse.post("/linkler/yeni", {"url": "http://video.test/izle"})
    assert db_one("SELECT COUNT(*) AS n FROM links WHERE url = 'http://video.test/izle'")["n"] == 2

    # sil
    r = admin.post(f"/linkler/{l2['id']}/sil")
    assert r.status_code == 302
    assert db_one("SELECT * FROM links WHERE id = ?", (l2["id"],)) is None


def test_pages_render():
    """Tüm sayfalar her kullanıcı için 200 döner."""
    lid = db_exec_insert("INSERT INTO lists (user_id, name, kind, shared) VALUES (?, 'Ortak', 'shopping', 1)",
                         (ADMIN_ID,))
    db_exec("INSERT INTO list_items (list_id, text, qty, created_by) VALUES (?, 'un', '1 kg', ?)", (lid, ADMIN_ID))
    rid = db_exec_insert("INSERT INTO recipes (user_id, title, ingredients) VALUES (?, 'Kek', 'un')", (VELI_ID,))
    kid = db_exec_insert("INSERT INTO links (user_id, url, title) VALUES (?, 'https://k.test', 'K')", (VELI_ID,))
    for client in (admin, ayse, veli):
        for url in ("/listeler/", f"/listeler/{lid}", "/linkler/", "/linkler/?durum=okundu",
                    "/linkler/?durum=tumu", "/linkler/paylas", "/tarifler/", "/tarifler/yeni",
                    "/tarifler/?q=x"):
            client.text(url)
    veli.text(f"/tarifler/{rid}")
    veli.text(f"/tarifler/{rid}/duzenle")
    veli.text(f"/linkler/{kid}")
    # veli'nin tarifinde paylaşılan "Ortak" listesi seçenek olarak çıkar
    assert "Ortak (admin)" in veli.text(f"/tarifler/{rid}")


if __name__ == "__main__":
    test_recipes()
    test_lists()
    test_links()
    test_pages_render()
    print("OK")
