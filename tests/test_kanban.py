"""Kanban: pano/sütun/kart, şablonlar, taşıma ve sıra numaraları, paylaşım, yaklaşanlar/takvim, arama, çöp kutusu.

Çalıştır: .venv/Scripts/python tests/test_kanban.py
"""
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

app = make_app()

from pano.auth import create_user  # noqa: E402
from pano.db import query, query_one  # noqa: E402
from pano.utils import today  # noqa: E402

with app.app_context():
    create_user("ayse", "ayse12345")
ADMIN = Client(app)
AYSE = Client(app, "ayse", "ayse12345")
JSON = {"Accept": "application/json"}


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def rows(sql, args=()):
    with app.app_context():
        return query(sql, args)


def board_id(name):
    return one("SELECT id FROM boards WHERE name = ?", (name,))["id"]


def column_id(board, name):
    return one("SELECT id FROM board_columns WHERE board_id = ? AND name = ?", (board, name))["id"]


def column_names(board):
    return [r["name"] for r in rows("SELECT name FROM board_columns WHERE board_id = ? ORDER BY position", (board,))]


def card_id(title):
    return one("SELECT id FROM cards WHERE title = ?", (title,))["id"]


def order(column):
    """[(başlık, position)] sütundaki sırayla."""
    return [(r["title"], r["position"]) for r in
            rows("SELECT title, position FROM cards WHERE column_id = ? ORDER BY position, id", (column,))]


def add(client, board, column, title):
    r = client.post(f"/kanban/{board}/kart", data={"column_id": column, "title": title})
    assert r.status_code == 302, r.status_code
    return card_id(title)


def move(client, card, column, position=None):
    data = {"column_id": column}
    if position is not None:
        data["position"] = position
    return client.post(f"/kanban/kart/{card}/tasi", data=data, headers=JSON)


def test_boards():
    ADMIN.post("/kanban/yeni", data={"name": "Ev işleri"})  # şablon yoksa varsayılan
    ADMIN.post("/kanban/yeni", data={"name": "Yan proje", "template": "ideas", "shared": "1"})
    ADMIN.post("/kanban/yeni", data={"name": "Haftam", "template": "week"})
    ADMIN.post("/kanban/yeni", data={"name": "Garip", "template": "yok-boyle"})
    r = ADMIN.post("/kanban/yeni", data={"name": "  "}, follow_redirects=True)
    assert "Pano adı boş olamaz" in r.get_data(as_text=True)
    assert one("SELECT COUNT(*) AS n FROM boards")["n"] == 4
    assert column_names(board_id("Ev işleri")) == ["Yapılacak", "Yapılıyor", "Bitti"]
    assert column_names(board_id("Yan proje")) == ["Fikir", "Planlandı", "Yapılıyor", "Bitti"]
    assert column_names(board_id("Haftam")) == ["Bekleyenler", "Bu hafta", "Bugün", "Bitti"]
    assert column_names(board_id("Garip")) == ["Yapılacak", "Yapılıyor", "Bitti"]
    assert [r["position"] for r in rows("SELECT position FROM board_columns WHERE board_id = ? ORDER BY id",
                                        (board_id("Yan proje"),))] == [0, 1, 2, 3]
    assert one("SELECT shared FROM boards WHERE name = 'Yan proje'")["shared"] == 1
    assert one("SELECT shared FROM boards WHERE name = 'Ev işleri'")["shared"] == 0

    page = ADMIN.text("/kanban/")
    assert "Ev işleri" in page and "Yan proje" in page and "4 sütun" in page and "paylaşılan" in page
    page = ADMIN.text(f"/kanban/{board_id('Ev işleri')}")
    assert "Yapılacak" in page and "✓ bitti" in page and "kanban.js" in page and "Pano ayarları" in page
    # Sahibi ad ve paylaşımı değiştirir
    ADMIN.post(f"/kanban/{board_id('Garip')}/ayarlar", data={"name": "Okul", "shared": "1"})
    assert one("SELECT shared FROM boards WHERE name = 'Okul'")["shared"] == 1
    ADMIN.post(f"/kanban/{board_id('Okul')}/ayarlar", data={"name": "Okul"})
    assert one("SELECT shared FROM boards WHERE name = 'Okul'")["shared"] == 0
    print("  boards OK")


def test_cards():
    b = board_id("Ev işleri")
    todo, doing = column_id(b, "Yapılacak"), column_id(b, "Yapılıyor")
    a = add(ADMIN, b, todo, "Çamaşır")
    r = ADMIN.post(f"/kanban/{b}/kart", data={"column_id": todo, "title": "Bulaşık"})
    assert r.headers["Location"].endswith(f"/kanban/{b}#kart-{card_id('Bulaşık')}")
    add(ADMIN, b, todo, "Süpürge")
    assert order(todo) == [("Çamaşır", 0), ("Bulaşık", 1), ("Süpürge", 2)]
    ADMIN.post(f"/kanban/{b}/kart", data={"column_id": todo, "title": ""})
    assert one("SELECT COUNT(*) AS n FROM cards")["n"] == 3
    # Başka panonun sütununa kart eklenemez
    other = column_id(board_id("Haftam"), "Bugün")
    assert ADMIN.post(f"/kanban/{b}/kart", data={"column_id": other, "title": "Yanlış"}).status_code == 400
    assert ADMIN.post(f"/kanban/{b}/kart", data={"title": "Sütunsuz"}).status_code == 400

    # Kart sayfası: not, son tarih, renk, sütun
    page = ADMIN.text(f"/kanban/kart/{a}")
    assert "Çamaşır" in page and "Kırmızı" in page and "Bitti (bitti)" in page
    ADMIN.post(f"/kanban/kart/{a}", data={"title": "Çamaşır yıka", "note": "Beyazlar ayrı\n40 derece",
                                          "due_date": "2026-12-01", "color": "blue", "column_id": todo})
    c = one("SELECT * FROM cards WHERE id = ?", (a,))
    assert (c["title"], c["note"], c["due_date"], c["color"]) == ("Çamaşır yıka", "Beyazlar ayrı\n40 derece",
                                                                   "2026-12-01", "blue")
    assert c["column_id"] == todo and c["position"] == 0 and c["created_by"] == 1
    ADMIN.post(f"/kanban/kart/{a}", data={"title": "Çamaşır yıka", "color": "pembe", "column_id": todo})
    assert one("SELECT color, due_date FROM cards WHERE id = ?", (a,))["color"] == ""  # geçersiz renk -> yok
    ADMIN.post(f"/kanban/kart/{a}", data={"title": "", "column_id": todo})
    assert one("SELECT title FROM cards WHERE id = ?", (a,))["title"] == "Çamaşır yıka"  # boş başlık reddedilir
    # Sütun değişince yeni sütunun sonuna gider, eski sütun yeniden numaralanır
    add(ADMIN, b, doing, "Ütü")
    ADMIN.post(f"/kanban/kart/{a}", data={"title": "Çamaşır yıka", "color": "green", "note": "Beyazlar ayrı",
                                          "column_id": doing})
    assert order(doing) == [("Ütü", 0), ("Çamaşır yıka", 1)]
    assert order(todo) == [("Bulaşık", 0), ("Süpürge", 1)]
    # Başka panonun sütunu seçilemez (yok sayılır)
    ADMIN.post(f"/kanban/kart/{a}", data={"title": "Çamaşır yıka", "color": "green", "note": "Beyazlar ayrı",
                                          "column_id": other})
    assert one("SELECT column_id FROM cards WHERE id = ?", (a,))["column_id"] == doing
    page = ADMIN.text(f"/kanban/{b}")
    assert "c-green" in page and "Not var" in page and f'id="kart-{a}"' in page
    print("  cards OK")


def test_move():
    b = board_id("Ev işleri")
    todo, doing, done = column_id(b, "Yapılacak"), column_id(b, "Yapılıyor"), column_id(b, "Bitti")
    add(ADMIN, b, todo, "Cam sil")
    assert [t for t, _p in order(todo)] == ["Bulaşık", "Süpürge", "Cam sil"]
    # Aynı sütunda: sondakini başa al
    r = move(ADMIN, card_id("Cam sil"), todo, 0)
    assert r.status_code == 200 and r.json == {"ok": True, "column_id": todo, "position": 0}
    assert order(todo) == [("Cam sil", 0), ("Bulaşık", 1), ("Süpürge", 2)]
    # Aynı sütunda: baştakini ortaya
    move(ADMIN, card_id("Cam sil"), todo, 1)
    assert order(todo) == [("Bulaşık", 0), ("Cam sil", 1), ("Süpürge", 2)]
    # Sütunlar arası: iki sütun da 0..n yeniden numaralanır
    r = move(ADMIN, card_id("Bulaşık"), doing, 1)
    assert r.json["position"] == 1
    assert order(todo) == [("Cam sil", 0), ("Süpürge", 1)]
    assert order(doing) == [("Ütü", 0), ("Bulaşık", 1), ("Çamaşır yıka", 2)]
    # Sıra sınırları: çok büyük -> sona, negatif -> başa, boş -> sona
    assert move(ADMIN, card_id("Ütü"), doing, 99).json["position"] == 2
    assert order(doing) == [("Bulaşık", 0), ("Çamaşır yıka", 1), ("Ütü", 2)]
    assert move(ADMIN, card_id("Ütü"), todo, -5).json["position"] == 0
    assert order(todo) == [("Ütü", 0), ("Cam sil", 1), ("Süpürge", 2)]
    assert move(ADMIN, card_id("Ütü"), done).json["position"] == 0
    assert order(done) == [("Ütü", 0)] and order(todo) == [("Cam sil", 0), ("Süpürge", 1)]
    # Başka panonun sütununa taşınamaz; kart yerinde kalır
    other = column_id(board_id("Haftam"), "Bugün")
    r = move(ADMIN, card_id("Ütü"), other, 0)
    assert r.status_code == 400 and "aynı panodaki" in r.json["error"]
    r = move(ADMIN, card_id("Ütü"), 999999, 0)
    assert r.status_code == 400
    assert one("SELECT column_id, board_id FROM cards WHERE title = 'Ütü'")["column_id"] == done
    assert order(other) == []

    # JS'siz menü: ↑ ↓ ← → formla, panoya (karta) döner
    s = card_id("Süpürge")
    r = ADMIN.post(f"/kanban/kart/{s}/tasi", data={"dir": "up"})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/kanban/{b}#kart-{s}")
    assert order(todo) == [("Süpürge", 0), ("Cam sil", 1)]
    ADMIN.post(f"/kanban/kart/{s}/tasi", data={"dir": "up"})  # zaten başta: değişmez
    assert order(todo) == [("Süpürge", 0), ("Cam sil", 1)]
    ADMIN.post(f"/kanban/kart/{s}/tasi", data={"dir": "down"})
    assert order(todo) == [("Cam sil", 0), ("Süpürge", 1)]
    ADMIN.post(f"/kanban/kart/{s}/tasi", data={"dir": "right"})  # sağdaki sütunun sonuna
    assert order(doing)[-1] == ("Süpürge", 2) and order(todo) == [("Cam sil", 0)]
    ADMIN.post(f"/kanban/kart/{s}/tasi", data={"dir": "left"})
    assert order(todo) == [("Cam sil", 0), ("Süpürge", 1)]
    r = ADMIN.post(f"/kanban/kart/{s}/tasi", data={"dir": "left"}, follow_redirects=True)
    assert "Bu yönde sütun yok" in r.get_data(as_text=True)
    assert ADMIN.post(f"/kanban/kart/{s}/tasi", data={"dir": "left"}, headers=JSON).status_code == 400
    print("  move OK")


def test_columns():
    b = board_id("Haftam")
    ADMIN.post(f"/kanban/{b}/sutun", data={"name": "Arşiv"})
    assert column_names(b) == ["Bekleyenler", "Bu hafta", "Bugün", "Bitti", "Arşiv"]
    ADMIN.post(f"/kanban/{b}/sutun", data={"name": ""})
    assert len(column_names(b)) == 5
    arsiv = column_id(b, "Arşiv")
    ADMIN.post(f"/kanban/sutun/{arsiv}", data={"name": "Sonra"})
    ADMIN.post(f"/kanban/sutun/{arsiv}", data={"name": " "})  # boş ad reddedilir
    assert column_names(b)[-1] == "Sonra"
    # Sola/sağa taşıma; kenarda değişmez
    ADMIN.post(f"/kanban/sutun/{arsiv}/tasi", data={"dir": "left"})
    assert column_names(b) == ["Bekleyenler", "Bu hafta", "Bugün", "Sonra", "Bitti"]
    ADMIN.post(f"/kanban/sutun/{arsiv}/tasi", data={"dir": "right"})
    ADMIN.post(f"/kanban/sutun/{arsiv}/tasi", data={"dir": "right"})
    assert column_names(b) == ["Bekleyenler", "Bu hafta", "Bugün", "Bitti", "Sonra"]
    first = column_id(b, "Bekleyenler")
    ADMIN.post(f"/kanban/sutun/{first}/tasi", data={"dir": "left"})
    assert column_names(b)[0] == "Bekleyenler"
    assert [r["position"] for r in rows("SELECT position FROM board_columns WHERE board_id = ? ORDER BY position",
                                        (b,))] == [0, 1, 2, 3, 4]
    # Boş sütun silinir
    ADMIN.post(f"/kanban/sutun/{arsiv}/sil")
    assert column_names(b) == ["Bekleyenler", "Bu hafta", "Bugün", "Bitti"]
    # Dolu sütun silinince kartları soldaki sütunun sonuna (sırası korunarak) geçer
    week, today_col = column_id(b, "Bu hafta"), column_id(b, "Bugün")
    add(ADMIN, b, week, "Fatura öde")
    add(ADMIN, b, today_col, "Spor")
    add(ADMIN, b, today_col, "Kitap oku")
    r = ADMIN.post(f"/kanban/sutun/{today_col}/sil", follow_redirects=True)
    assert "2 kart “Bu hafta” sütununa taşındı" in r.get_data(as_text=True)
    assert order(week) == [("Fatura öde", 0), ("Spor", 1), ("Kitap oku", 2)]
    assert column_names(b) == ["Bekleyenler", "Bu hafta", "Bitti"]
    # En soldaki silinince kartlar sağdakine geçer
    add(ADMIN, b, first, "Tatil planı")
    ADMIN.post(f"/kanban/sutun/{first}/sil")
    assert column_names(b) == ["Bu hafta", "Bitti"]
    assert order(week)[-1] == ("Tatil planı", 3)
    assert [r["position"] for r in rows("SELECT position FROM board_columns WHERE board_id = ? ORDER BY position",
                                        (b,))] == [0, 1]
    # Son sütun silinmez
    ADMIN.post(f"/kanban/sutun/{column_id(b, 'Bitti')}/sil")
    r = ADMIN.post(f"/kanban/sutun/{week}/sil", follow_redirects=True)
    assert "en az bir sütun kalmalı" in r.get_data(as_text=True)
    assert column_names(b) == ["Bu hafta"] and len(order(week)) == 4
    # Sütun sınırı
    for i in range(20):
        ADMIN.post(f"/kanban/{b}/sutun", data={"name": f"S{i}"})
    assert len(column_names(b)) == 12
    print("  columns OK")


def test_access():
    private, shared = board_id("Ev işleri"), board_id("Yan proje")
    pcard = card_id("Cam sil")
    pcol = column_id(private, "Yapılacak")
    # Özel pano: başkası hiçbir şeyini göremez (404)
    assert "Ev işleri" not in AYSE.text("/kanban/")
    assert AYSE.get(f"/kanban/{private}").status_code == 404
    assert AYSE.get(f"/kanban/kart/{pcard}").status_code == 404
    assert AYSE.post(f"/kanban/kart/{pcard}", data={"title": "x"}).status_code == 404
    assert move(AYSE, pcard, pcol, 0).status_code == 404
    assert AYSE.post(f"/kanban/kart/{pcard}/sil").status_code == 404
    assert AYSE.post(f"/kanban/{private}/kart", data={"column_id": pcol, "title": "x"}).status_code == 404
    assert AYSE.post(f"/kanban/{private}/sutun", data={"name": "x"}).status_code == 404
    assert AYSE.post(f"/kanban/sutun/{pcol}", data={"name": "x"}).status_code == 404
    assert AYSE.post(f"/kanban/sutun/{pcol}/tasi", data={"dir": "right"}).status_code == 404
    assert AYSE.post(f"/kanban/sutun/{pcol}/sil").status_code == 404
    assert AYSE.post(f"/kanban/{private}/ayarlar", data={"name": "x"}).status_code == 404
    assert AYSE.post(f"/kanban/{private}/sil").status_code == 404
    assert one("SELECT title FROM cards WHERE id = ?", (pcard,))["title"] == "Cam sil"

    # Paylaşılan pano: herkes görür, kart ekler/düzenler/taşır, sütun ekler
    page = AYSE.text("/kanban/")
    assert "Yan proje" in page and "👤 admin" in page
    page = AYSE.text(f"/kanban/{shared}")
    assert "Fikir" in page and "Pano ayarları" not in page
    idea, plan = column_id(shared, "Fikir"), column_id(shared, "Planlandı")
    k = add(AYSE, shared, idea, "Logo çiz")
    assert one("SELECT created_by FROM cards WHERE id = ?", (k,))["created_by"] == 2
    assert move(AYSE, k, plan, 0).json["ok"]
    AYSE.post(f"/kanban/kart/{k}", data={"title": "Logo çiz", "note": "SVG", "column_id": plan})
    assert one("SELECT note FROM cards WHERE id = ?", (k,))["note"] == "SVG"
    assert "Ekleyen: ayse" in ADMIN.text(f"/kanban/kart/{k}")
    AYSE.post(f"/kanban/{shared}/sutun", data={"name": "Test"})
    assert "Test" in column_names(shared)
    AYSE.post(f"/kanban/sutun/{column_id(shared, 'Test')}/sil")
    assert "Test" not in column_names(shared)
    # Ama ayarları ve silmeyi sadece sahibi yapar
    assert AYSE.post(f"/kanban/{shared}/ayarlar", data={"name": "Benim"}).status_code == 403
    assert AYSE.post(f"/kanban/{shared}/sil").status_code == 403
    assert one("SELECT name, shared FROM boards WHERE id = ?", (shared,))["name"] == "Yan proje"
    # Ayşe'nin kendi panosu Admin'e görünmez
    AYSE.post("/kanban/yeni", data={"name": "Ayşe'nin işleri"})
    assert "Ayşe&#39;nin işleri" in AYSE.text("/kanban/")
    assert "Ayşe" not in ADMIN.text("/kanban/")
    print("  access OK")


def test_upcoming_and_calendar():
    shared = board_id("Yan proje")
    soon = (today() + timedelta(days=2)).isoformat()
    idea, done = column_id(shared, "Fikir"), column_id(shared, "Bitti")
    k1 = add(ADMIN, shared, idea, "Sunum hazırla")
    k2 = add(ADMIN, shared, done, "Teklif gönder")
    for k in (k1, k2):
        ADMIN.post(f"/kanban/kart/{k}", data={"title": one("SELECT title FROM cards WHERE id = ?", (k,))["title"],
                                              "due_date": soon, "column_id": one("SELECT column_id FROM cards WHERE id = ?",
                                                                                 (k,))["column_id"]})
    # Tek sütunlu panoda "bitti" sütunu yok: kart yaklaşanlarda görünür
    single = board_id("Haftam")
    week = column_id(single, "Bu hafta")
    for name in column_names(single)[1:]:
        ADMIN.post(f"/kanban/sutun/{column_id(single, name)}/sil")
    assert column_names(single) == ["Bu hafta"]
    s1 = card_id("Tatil planı")
    ADMIN.post(f"/kanban/kart/{s1}", data={"title": "Tatil planı", "due_date": soon, "column_id": week})
    # Özel panodaki kart: sadece sahibinde
    private = board_id("Ev işleri")
    c = card_id("Cam sil")
    ADMIN.post(f"/kanban/kart/{c}", data={"title": "Cam sil", "due_date": soon,
                                          "column_id": column_id(private, "Yapılacak")})
    ADMIN.text("/kanban/")  # birikmiş bildirimler panoyu yanıltmasın

    home = ADMIN.text("/")
    assert "Sunum hazırla" in home and "Tatil planı" in home and "Cam sil" in home
    assert "Teklif gönder" not in home  # bitti sütununda
    assert f"/kanban/{shared}#kart-{k1}" in home and "Yan proje · Fikir" in home
    cal = ADMIN.text(f"/takvim/?ay={soon[:7]}")
    assert "Sunum hazırla" in cal and "Tatil planı" in cal and "Teklif gönder" not in cal
    # Paylaşılan panonun kartı Ayşe'de de görünür, özel panonunki görünmez
    home = AYSE.text("/")
    assert "Sunum hazırla" in home and "Cam sil" not in home and "Tatil planı" not in home
    assert "Sunum hazırla" in AYSE.text(f"/takvim/?ay={soon[:7]}")
    # Bitti sütununa taşınınca yaklaşanlardan düşer; geri alınınca döner
    move(ADMIN, k1, done)
    assert "Sunum hazırla" not in ADMIN.text("/")
    assert "Sunum hazırla" not in ADMIN.text(f"/takvim/?ay={soon[:7]}")
    move(ADMIN, k1, idea)
    assert "Sunum hazırla" in ADMIN.text("/")
    # Bitti sütunu sola taşınınca en sağdaki yeni sütun "bitti" olur
    ADMIN.post(f"/kanban/sutun/{done}/tasi", data={"dir": "left"})
    home = ADMIN.text("/")
    assert "Teklif gönder" in home
    ADMIN.post(f"/kanban/sutun/{done}/tasi", data={"dir": "right"})
    assert "Teklif gönder" not in ADMIN.text("/")
    # Pano listesi: açık/toplam kart ve en yakın tarih
    page = ADMIN.text("/kanban/")
    open_n = one("SELECT COUNT(*) AS n FROM cards WHERE board_id = ? AND column_id != ?", (shared, done))["n"]
    total = one("SELECT COUNT(*) AS n FROM cards WHERE board_id = ?", (shared,))["n"]
    assert f"{open_n}/{total}" in page and "2 gün sonra" in page
    print("  upcoming/calendar OK")


def test_search():
    page = ADMIN.text("/ara/?q=sunum")
    assert "Kanban" in page and "Sunum hazırla" in page and "Yan proje · Fikir" in page
    assert "Sunum hazırla" in ADMIN.text("/ara/?q=yan+proje")  # pano adıyla
    assert "Logo çiz" in ADMIN.text("/ara/?q=svg")  # notla
    assert "Teklif gönder ✓" in ADMIN.text("/ara/?q=teklif")  # bitti sütununda
    assert "Sunum hazırla" in AYSE.text("/ara/?q=sunum")  # paylaşılan pano
    assert "Cam sil" not in AYSE.text("/ara/?q=cam")  # özel pano
    print("  search OK")


def test_trash():
    # Kart silme ve geri getirme
    k = card_id("Logo çiz")
    before = one("SELECT * FROM cards WHERE id = ?", (k,))
    r = AYSE.post(f"/kanban/kart/{k}/sil", follow_redirects=True)
    assert "çöp kutusuna taşındı" in r.get_data(as_text=True)
    assert one("SELECT id FROM cards WHERE id = ?", (k,)) is None
    t = one("SELECT * FROM trash WHERE user_id = 2 AND module = 'kanban'")
    assert "Logo çiz (Yan proje)" in t["label"]
    AYSE.post(f"/cop-kutusu/{t['id']}/geri")
    after = one("SELECT * FROM cards WHERE id = ?", (k,))
    assert after is not None and dict(after) == dict(before)

    # Pano silme (sadece sahibi) ve geri getirme: sütunlar ve kartlar aynı id'lerle döner
    b = board_id("Ev işleri")
    cols = [dict(r) for r in rows("SELECT * FROM board_columns WHERE board_id = ? ORDER BY id", (b,))]
    cards = [dict(r) for r in rows("SELECT * FROM cards WHERE board_id = ? ORDER BY id", (b,))]
    assert len(cols) == 3 and len(cards) >= 5
    r = ADMIN.post(f"/kanban/{b}/sil", follow_redirects=True)
    assert "“Ev işleri” panosu çöp kutusuna taşındı" in r.get_data(as_text=True)
    assert one("SELECT id FROM boards WHERE id = ?", (b,)) is None
    assert one("SELECT COUNT(*) AS n FROM board_columns WHERE board_id = ?", (b,))["n"] == 0
    assert one("SELECT COUNT(*) AS n FROM cards WHERE board_id = ?", (b,))["n"] == 0
    assert "Cam sil" not in ADMIN.text("/")
    t = one("SELECT * FROM trash WHERE user_id = 1 AND label LIKE '%Ev işleri panosu%'")
    r = ADMIN.post(f"/cop-kutusu/{t['id']}/geri", follow_redirects=True)
    assert "geri getirildi" in r.get_data(as_text=True)
    assert [dict(r) for r in rows("SELECT * FROM board_columns WHERE board_id = ? ORDER BY id", (b,))] == cols
    assert [dict(r) for r in rows("SELECT * FROM cards WHERE board_id = ? ORDER BY id", (b,))] == cards
    assert "Cam sil" in ADMIN.text(f"/kanban/{b}")
    print("  trash OK")


def test_menu():
    menu = ADMIN.text("/menu")
    assert "Araçlar</h2>" in menu and "Kanban" in menu
    assert menu.index("Kişisel</h2>") < menu.index("Araçlar</h2>")  # grup en sonda
    print("  menu OK")


if __name__ == "__main__":
    test_boards()
    test_cards()
    test_move()
    test_columns()
    test_access()
    test_upcoming_and_calendar()
    test_search()
    test_trash()
    test_menu()
    print("OK")
