"""Kullanım: geri dönüşüm kutusu, tema seçimi, pano düzeni.

Çalıştır: .venv/Scripts/python tests/test_usage.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

from PIL import Image  # noqa: E402

app = make_app()
os.environ["CRON_SECRET"] = "gizli"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.external as external  # noqa: E402
import pano.telegram as tg  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query_one  # noqa: E402

tg._call = lambda method, params=None, files=None: {"message_id": 1}
WEATHER_CALLS = []
external.weather = lambda lat, lon: WEATHER_CALLS.append(1) or None
external.rates = lambda: None
external.gold_gram_try = lambda: None

with app.app_context():
    AYSE = create_user("ayse", "ayse12345")
ADMIN = Client(app)
AYSE_C = Client(app, "ayse", "ayse12345")
RAW = app.test_client()


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def run(sql, args=()):
    with app.app_context():
        execute(sql, args)


def count(table, where="1=1", args=()):
    return one(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", args)["n"]


def last_trash():
    return one("SELECT * FROM trash ORDER BY id DESC")


def png():
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(buf, "PNG")
    buf.seek(0)
    return buf


def test_trash_restore():
    # Not + ek dosyası
    ADMIN.post("/notlar/yeni", data={"title": "Kira sözleşmesi", "content": "Mart'ta yenilenecek"})
    nid = one("SELECT id FROM notes WHERE title = 'Kira sözleşmesi'")["id"]
    ADMIN.post("/dosya/yukle", data={"entity": "note", "entity_id": str(nid), "file": (png(), "sozlesme.png")},
               content_type="multipart/form-data")
    att = one("SELECT * FROM attachments WHERE entity = 'note' AND entity_id = ?", (nid,))
    path = os.path.join(app.config["UPLOAD_DIR"], att["filename"])
    r = ADMIN.post(f"/notlar/{nid}/sil", follow_redirects=True)
    page = r.get_data(as_text=True)
    assert "Not çöp kutusuna taşındı" in page and 'href="/cop-kutusu/"' in page  # bildirimde bağlantı
    assert one("SELECT id FROM notes WHERE id = ?", (nid,)) is None and os.path.exists(path)
    # Temizlik kutudaki dosyayı sahipsiz saymaz
    with app.app_context():
        from pano.storage import cleanup_orphans
        cleanup_orphans()
    assert os.path.exists(path)
    box = ADMIN.text("/cop-kutusu/")
    assert "📝 Kira sözleşmesi" in box and "30 gün sonra kalıcı silinir" in box
    # Başkası göremez, geri getiremez
    tid = last_trash()["id"]
    assert "Kira" not in AYSE_C.text("/cop-kutusu/")
    AYSE_C.post(f"/cop-kutusu/{tid}/geri")
    AYSE_C.post(f"/cop-kutusu/{tid}/sil")
    assert one("SELECT id FROM notes WHERE id = ?", (nid,)) is None and os.path.exists(path)
    # Geri getir: aynı id, ek de yerinde
    ADMIN.post(f"/cop-kutusu/{tid}/geri")
    assert one("SELECT title FROM notes WHERE id = ?", (nid,))["title"] == "Kira sözleşmesi"
    assert one("SELECT id FROM attachments WHERE id = ?", (att["id"],)) and count("trash") == 0
    assert ADMIN.get(f"/dosya/{att['id']}").status_code == 200

    # Liste + maddeler
    ADMIN.post("/listeler/yeni", data={"name": "Market", "kind": "shopping"})
    lid = one("SELECT id FROM lists WHERE name = 'Market'")["id"]
    ADMIN.post(f"/listeler/{lid}/ekle", data={"text": "süt, ekmek, yumurta"})
    assert count("list_items", "list_id = ?", (lid,)) == 3
    ADMIN.post(f"/listeler/{lid}/sil")
    assert count("lists", "id = ?", (lid,)) == 0 and count("list_items", "list_id = ?", (lid,)) == 0
    ADMIN.post(f"/cop-kutusu/{last_trash()['id']}/geri")
    assert count("list_items", "list_id = ?", (lid,)) == 3

    # Tek madde; listesi kalıcı silinmişse geri getirilemez ve kutuda kalır
    item = one("SELECT id FROM list_items WHERE text = 'süt'")["id"]
    ADMIN.post(f"/listeler/madde/{item}/sil")
    item_tid = last_trash()["id"]
    assert "☑️ süt (Market)" in ADMIN.text("/cop-kutusu/")
    ADMIN.post(f"/listeler/{lid}/sil")
    ADMIN.post(f"/cop-kutusu/{last_trash()['id']}/sil")  # listeyi kalıcı sil
    r = ADMIN.post(f"/cop-kutusu/{item_tid}/geri", follow_redirects=True)
    assert "Geri getirilemedi" in r.get_data(as_text=True) and one("SELECT id FROM trash WHERE id = ?", (item_tid,))

    # Araç + kayıtları, hedef + hareketleri, alışkanlık + günleri
    ADMIN.post("/arac/yeni", data={"name": "Egea", "plate": "42 ABC 1"})
    vid = one("SELECT id FROM vehicles WHERE name = 'Egea'")["id"]
    run("INSERT INTO vehicle_logs (vehicle_id, kind, date, km, amount) VALUES (?, 'fuel', '2026-09-01', 1000, 1500)", (vid,))
    ADMIN.post(f"/arac/{vid}/sil")
    assert count("vehicle_logs", "vehicle_id = ?", (vid,)) == 0
    ADMIN.post(f"/cop-kutusu/{last_trash()['id']}/geri")
    assert count("vehicle_logs", "vehicle_id = ?", (vid,)) == 1

    run("INSERT INTO goals (user_id, name, target) VALUES (1, 'Tatil', 20000)")
    gid = one("SELECT id FROM goals WHERE name = 'Tatil'")["id"]
    run("INSERT INTO goal_entries (goal_id, amount, date) VALUES (?, 5000, '2026-09-01')", (gid,))
    ADMIN.post(f"/hedefler/{gid}/sil")
    assert count("goals", "id = ?", (gid,)) == 0
    ADMIN.post(f"/cop-kutusu/{last_trash()['id']}/geri")
    assert count("goal_entries", "goal_id = ?", (gid,)) == 1

    # Ortak harcama grubu: üyeler, harcamalar, paylar birlikte döner
    run("INSERT INTO split_groups (owner_id, name) VALUES (1, 'Ev arkadaşları')")
    gid2 = one("SELECT id FROM split_groups WHERE name = 'Ev arkadaşları'")["id"]
    run("INSERT INTO split_members (group_id, user_id, name) VALUES (?, 1, 'Ben'), (?, NULL, 'Can')", (gid2, gid2))
    m1, m2 = [r["id"] for r in (one("SELECT id FROM split_members WHERE name = ?", (n,)) for n in ("Ben", "Can"))]
    run("INSERT INTO split_expenses (group_id, payer_id, amount, date) VALUES (?, ?, 300, '2026-09-01')", (gid2, m1))
    sid = one("SELECT id FROM split_expenses WHERE group_id = ?", (gid2,))["id"]
    run("INSERT INTO split_shares (expense_id, member_id, share) VALUES (?, ?, 150), (?, ?, 150)", (sid, m1, sid, m2))
    run("INSERT INTO split_settlements (group_id, from_id, to_id, amount, date) VALUES (?, ?, ?, 50, '2026-09-02')",
        (gid2, m2, m1))
    ADMIN.post(f"/ortak/{gid2}/sil")
    assert count("split_groups", "id = ?", (gid2,)) == 0 and count("split_shares", "expense_id = ?", (sid,)) == 0
    ADMIN.post(f"/cop-kutusu/{last_trash()['id']}/geri")
    assert (count("split_members", "group_id = ?", (gid2,)), count("split_expenses", "group_id = ?", (gid2,)),
            count("split_shares", "expense_id = ?", (sid,)), count("split_settlements", "group_id = ?", (gid2,))) == (2, 1, 2, 1)
    assert "Ev arkadaşları" in ADMIN.text(f"/ortak/{gid2}")

    # Paylaşılan etkinliği sadece sahibi silebilir; geri gelir
    ADMIN.post("/etkinlikler/yeni", data={"title": "Piknik", "date": "2026-10-11", "shared": "1"})
    eid = one("SELECT id FROM events WHERE title = 'Piknik'")["id"]
    assert AYSE_C.post(f"/etkinlikler/{eid}/sil").status_code == 403
    ADMIN.post(f"/etkinlikler/{eid}/sil")
    ADMIN.post(f"/cop-kutusu/{last_trash()['id']}/geri")
    assert count("events", "id = ?", (eid,)) == 1
    print("  trash restore OK")


def test_trash_purge_and_empty():
    for title in ("Eski", "Yeni"):
        ADMIN.post("/notlar/yeni", data={"title": title, "content": "x"})
        ADMIN.post(f"/notlar/{one('SELECT id FROM notes WHERE title = ?', (title,))['id']}/sil")
    run("UPDATE trash SET deleted_at = datetime('now', '-31 days') WHERE label = '📝 Eski'")
    RAW.get("/cron/gizli/gunluk")
    labels = [r for r in ADMIN.text("/cop-kutusu/").split("📝 ")[1:]]
    assert not any(x.startswith("Eski") for x in labels) and any(x.startswith("Yeni") for x in labels)
    ADMIN.post("/cop-kutusu/bosalt")
    assert count("trash", "user_id = 1") == 0 and "Çöp kutusu boş" in ADMIN.text("/cop-kutusu/")
    print("  purge/empty OK")


def test_theme():
    assert "data-theme" not in ADMIN.text("/")  # varsayılan: cihaza göre
    ADMIN.post("/ayarlar/tema", data={"theme": "dark"})
    assert '<html lang="tr" data-theme="dark">' in ADMIN.text("/")
    ADMIN.post("/ayarlar/tema", data={"theme": "light"})
    assert 'data-theme="light"' in ADMIN.text("/ayarlar/")
    ADMIN.post("/ayarlar/tema", data={"theme": "mor"})  # geçersiz -> otomatik
    assert one("SELECT theme FROM users WHERE id = 1")["theme"] == "auto"
    # Menüdeki buton sıradaki temayı gönderir ve bulunulan sayfaya döner
    page = ADMIN.text("/notlar/")
    assert 'name="theme" value="dark"' in page and "🌓 Tema: otomatik" in page and 'name="next" value="/notlar/"' in page
    r = ADMIN.post("/ayarlar/tema", data={"theme": "dark", "next": "/notlar/?etiket=is"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/notlar/?etiket=is"), r.headers["Location"]
    assert "data-theme" not in AYSE_C.text("/")  # başkasını etkilemez
    # Açık kalınca koyu değişkenler uygulanmaz (CSS yapısı)
    css = open(os.path.join(app.static_folder, "style.css"), encoding="utf-8").read()
    assert ':root:not([data-theme="light"])' in css and ':root[data-theme="dark"]' in css
    print("  theme OK")


def test_layout():
    home = ADMIN.text("/")
    assert home.index("Hava durumu") < home.index("Yaklaşanlar") < home.index("Hızlı not")
    settings = ADMIN.text("/ayarlar/")
    assert 'id="pano-duzeni"' in settings and 'name="order" value="weather"' in settings
    # Hızlı notu başa al, hava durumu ve kurları gizle
    order = ["quick_note", "upcoming", "weather", "rates", "today", "todos", "money", "quick_expense", "modules", "yok"]
    shown = ["quick_note", "upcoming", "today", "todos", "quick_expense", "modules", "yok"]
    ADMIN.post("/ayarlar/pano", data={"order": order, "show": shown})
    assert one("SELECT dashboard_cards FROM users WHERE id = 1")["dashboard_cards"] == \
        "quick_note,upcoming,today,todos,quick_expense,modules"
    WEATHER_CALLS.clear()
    home = ADMIN.text("/")
    assert "<h2>Hava durumu" not in home and "Grafik ve alarm" not in home and not WEATHER_CALLS  # gizli kartın verisi çekilmez
    assert home.index("Hızlı not") < home.index("Yaklaşanlar")
    # Ayarlarda gizliler sonda, işaretsiz
    settings = ADMIN.text("/ayarlar/")
    assert settings.index('value="quick_note"') < settings.index('value="weather"')
    # Varlık/hedef kartı: veri varsa görünür
    run("DELETE FROM goals WHERE user_id = 1")
    ADMIN.post("/ayarlar/pano", data={"order": ["money"], "show": ["money"]})
    assert "Varlıklar ve hedefler" not in ADMIN.text("/")
    run("INSERT INTO goals (user_id, name, target) VALUES (1, 'Araba', 100000)")
    home = ADMIN.text("/")
    assert "Varlıklar ve hedefler" in home and "Araba" in home
    # Hepsi gizli -> boş durum; varsayılana dön
    ADMIN.post("/ayarlar/pano", data={"order": order})
    assert "Panoda hiç kart seçili değil" in ADMIN.text("/")
    ADMIN.post("/ayarlar/pano", data={"reset": "1"})
    assert one("SELECT dashboard_cards FROM users WHERE id = 1")["dashboard_cards"] is None
    assert "Hava durumu" in ADMIN.text("/")
    print("  layout OK")


if __name__ == "__main__":
    test_trash_restore()
    test_trash_purge_and_empty()
    test_theme()
    test_layout()
    print("OK")
