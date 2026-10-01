"""Siparişler: ekleme/doğrulama, erişim, durumlar, iade son günü, harcama, kargo takip linki,
Telegram hatırlatmaları, yaklaşanlar/takvim/arama, sekmeler ve çöp kutusu.

Telegram ve saat taklit edilir; teslim tarihleri gerçek bugüne göre kurulur.
Çalıştır: .venv/Scripts/python tests/test_orders.py
"""
import os
import sys
from datetime import datetime, time, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

app = make_app()
os.environ["CRON_SECRET"] = "gizli"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.telegram as tg  # noqa: E402
import pano.todo_reminders as todo  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query, query_one  # noqa: E402
from pano.modules.orders import _flags, return_by, tracking_link  # noqa: E402
from pano.utils import TZ, fmt_money, today  # noqa: E402

CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {})) or {"message_id": 1}
T = today()
NOW = [datetime.combine(T, time(10, 0), tzinfo=TZ)]
todo.now_local = lambda: NOW[0]

with app.app_context():
    AYSE = create_user("ayse", "ayse12345")
    MEHMET = create_user("mehmet", "mehmet12345")  # Telegram'ı bağlı değil
    execute("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
    execute("UPDATE users SET telegram_chat_id = '200' WHERE id = ?", (AYSE,))
ADMIN = Client(app)
AYSE_C = Client(app, "ayse", "ayse12345")
MEHMET_C = Client(app, "mehmet", "mehmet12345")
RAW = app.test_client()


def d(days):
    return (T + timedelta(days=days)).isoformat()


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def rows(sql, args=()):
    with app.app_context():
        return query(sql, args)


def count(where="1 = 1", args=()):
    return one(f"SELECT COUNT(*) AS n FROM orders WHERE {where}", args)["n"]


def add(client, item, **data):
    """Sipariş ekler, satırı döner (eklenmediyse None)."""
    client.post("/siparisler/yeni", data={"store": "Trendyol", "item": item, **data})
    return one("SELECT * FROM orders WHERE item = ? ORDER BY id DESC", (item,))


def edit(client, order, **changes):
    o = dict(order)
    data = {k: ("" if o[k] is None else str(o[k])) for k in ("store", "item", "amount", "ordered_on", "expected_on",
                                                         "delivered_on", "carrier", "tracking_no", "tracking_url",
                                                         "order_url", "status", "return_days", "note")}
    data.update(changes)
    client.post(f"/siparisler/{order['id']}", data=data)
    return one("SELECT * FROM orders WHERE id = ?", (order["id"],))


def status(client, order, new, **extra):
    return client.post(f"/siparisler/{order['id']}/durum", data={"status": new, **extra})


def sent():
    return [(p["chat_id"], p["text"]) for m, p in CALLS if m == "sendMessage"]


def cron():
    r = RAW.get("/cron/gizli/hatirlatma")
    assert r.status_code == 200, r.status_code
    return r.json


def test_crud_and_validation():
    # Geçersizler reddedilir
    assert add(ADMIN, "Kötü link", tracking_url="javascript:alert(1)") is None
    assert add(ADMIN, "Kötü link", order_url="ftp://ornek.com/x") is None
    assert add(ADMIN, "Kötü link", tracking_url="https://") is None
    assert add(ADMIN, "Kötü tutar", amount="abc") is None
    assert add(ADMIN, "Uzun iade", return_days="400") is None
    assert add(ADMIN, "Geç sipariş", ordered_on=d(0), expected_on=d(-1)) is None
    assert add(ADMIN, "Gelecekte teslim", status="delivered", delivered_on=d(1)) is None
    ADMIN.post("/siparisler/yeni", data={"store": "Trendyol", "item": ""})
    assert count() == 0
    page = ADMIN.text("/siparisler/")
    assert "Takip linki http:// veya https:// ile başlamalı." in page and "Ürün adı gerekli." in page

    o = add(ADMIN, "Kulaklık", amount="1.249,90", expected_on=d(2), carrier="Yurtiçi", tracking_no="123456",
            order_url="https://www.trendyol.com/siparis/1", note="Siyah renk")
    assert o["amount"] == 1249.9 and o["ordered_on"] == d(0) and o["status"] == "ordered"
    assert o["return_days"] == 14 and o["return_by"] is None and o["delivered_on"] is None and o["expense_id"] is None
    page = ADMIN.text("/siparisler/")
    assert "Kulaklık" in page and "Yolda 1 sipariş" in page and "1.249,90 ₺" in page and "Beklenen" in page
    assert "Yurtiçi" in page and 'list="order-stores"' in page and '<option value="HepsiJET">' in page
    # Düzenleme; geçersiz link düzenlemede de reddedilir
    o = edit(ADMIN, o, note="Mavi renk", store="Hepsiburada")
    assert o["note"] == "Mavi renk" and o["store"] == "Hepsiburada"
    assert edit(ADMIN, o, tracking_url="data:text/html,x")["tracking_url"] == ""
    assert edit(ADMIN, o, item="")["item"] == "Kulaklık"
    # Mağaza önerisine daha önce yazılan mağaza da girer
    ADMIN.post("/siparisler/yeni", data={"store": "Koton", "item": "Gömlek"})
    assert '<option value="Koton">' in ADMIN.text("/siparisler/")

    # Erişim: başkası göremez, değiştiremez, silemez
    assert "Kulaklık" not in AYSE_C.text("/siparisler/")
    assert AYSE_C.get(f"/siparisler/{o['id']}").status_code == 404
    assert AYSE_C.post(f"/siparisler/{o['id']}", data={"item": "x"}).status_code == 404
    assert status(AYSE_C, o, "shipped").status_code == 404
    assert AYSE_C.post(f"/siparisler/{o['id']}/sil").status_code == 404
    assert one("SELECT status FROM orders WHERE id = ?", (o["id"],))["status"] == "ordered"
    print("  crud OK")


def test_tracking_link():
    assert tracking_link({"tracking_url": "", "tracking_no": "", "carrier": "Aras"}) is None
    assert tracking_link({"tracking_url": "https://kargo.example/t/9", "tracking_no": "9", "carrier": "Aras"}) == \
        "https://kargo.example/t/9"
    assert tracking_link({"tracking_url": "", "tracking_no": "A1", "carrier": "Aras"}) == \
        "https://www.google.com/search?q=Aras+kargo+takip+A1"
    assert tracking_link({"tracking_url": "", "tracking_no": "77", "carrier": "Sürat Kargo"}) == \
        "https://www.google.com/search?q=S%C3%BCrat+Kargo+takip+77"
    assert tracking_link({"tracking_url": "", "tracking_no": "5", "carrier": ""}) == \
        "https://www.google.com/search?q=kargo+takip+5"

    o = one("SELECT * FROM orders WHERE item = 'Kulaklık'")
    google = "https://www.google.com/search?q=Yurti%C3%A7i+kargo+takip+123456"
    assert google in ADMIN.text("/siparisler/") and google in ADMIN.text(f"/siparisler/{o['id']}")
    edit(ADMIN, o, tracking_url="https://kargo.example/takip/123456")
    page = ADMIN.text(f"/siparisler/{o['id']}")
    assert 'href="https://kargo.example/takip/123456"' in page and google not in page
    assert 'href="https://www.trendyol.com/siparis/1"' in page  # sipariş sayfası butonu
    g = one("SELECT * FROM orders WHERE item = 'Gömlek'")
    assert "📍 Kargo takip" not in ADMIN.text(f"/siparisler/{g['id']}")  # takip no da link de yok
    print("  tracking link OK")


def test_status_and_return_by():
    assert return_by("2026-10-01", 14) == "2026-10-15" and return_by("2026-10-01", 0) is None
    assert return_by(None, 14) is None
    o = add(ADMIN, "Ayakkabı", amount="899", ordered_on=d(-20), expected_on=d(-15))
    # Uygun olmayan adım reddedilir
    status(ADMIN, o, "returned")
    status(ADMIN, o, "returning")
    status(ADMIN, o, "bilinmeyen")
    assert one("SELECT status FROM orders WHERE id = ?", (o["id"],))["status"] == "ordered"
    status(ADMIN, o, "shipped")
    assert one("SELECT status FROM orders WHERE id = ?", (o["id"],))["status"] == "shipped"
    status(ADMIN, o, "shipped")  # ikinci kez olmaz
    # Teslim aldım: bugün + 14 gün
    r = status(ADMIN, o, "delivered", next="/siparisler/?sekme=bekleyen")
    assert r.status_code == 302 and r.headers["Location"].endswith("/siparisler/?sekme=bekleyen")
    o = one("SELECT * FROM orders WHERE id = ?", (o["id"],))
    assert o["status"] == "delivered" and o["delivered_on"] == d(0) and o["return_by"] == d(14)
    page = ADMIN.text("/siparisler/?sekme=teslim")
    assert "Ayakkabı" in page and "iade için 14 gün kaldı" in page
    # Teslim tarihi düzeltilir -> iade son günü yeniden hesaplanır; süre değişince de
    o = edit(ADMIN, o, delivered_on=d(-10))
    assert o["delivered_on"] == d(-10) and o["return_by"] == d(4)
    assert "iade için 4 gün kaldı" in ADMIN.text("/siparisler/?sekme=teslim")
    o = edit(ADMIN, o, return_days="30")
    assert o["return_by"] == d(20)
    assert edit(ADMIN, o, delivered_on=d(1))["delivered_on"] == d(-10)  # ileri tarih olmaz
    assert edit(ADMIN, o, delivered_on=d(-25))["delivered_on"] == d(-10)  # siparişten önce olmaz
    # İade süresi 0 = takip yok
    o = edit(ADMIN, o, return_days="0")
    assert o["return_by"] is None and "takip edilmiyor" in ADMIN.text(f"/siparisler/{o['id']}")
    o = edit(ADMIN, o, return_days="14")
    assert o["return_by"] == d(4)
    # İade başlattım -> iade tamamlandı
    status(ADMIN, o, "returning")
    assert one("SELECT status FROM orders WHERE id = ?", (o["id"],))["status"] == "returning"
    status(ADMIN, o, "cancelled")  # teslim alınmış sipariş iptal edilemez
    status(ADMIN, o, "returned")
    o = one("SELECT * FROM orders WHERE id = ?", (o["id"],))
    assert o["status"] == "returned" and o["return_by"] == d(4)
    # Durum formdan geri alınırsa teslim bilgisi temizlenir
    o = edit(ADMIN, o, status="shipped")
    assert o["delivered_on"] is None and o["return_by"] is None
    # Formdan "teslim alındı" seçilip tarih boş bırakılırsa bugün
    o = edit(ADMIN, o, status="delivered", delivered_on="")
    assert o["delivered_on"] == d(0) and o["return_by"] == d(14)
    # İptal
    c = add(ADMIN, "Kitap")
    status(ADMIN, c, "cancelled")
    assert one("SELECT status FROM orders WHERE id = ?", (c["id"],))["status"] == "cancelled"
    status(ADMIN, c, "delivered")
    assert one("SELECT status FROM orders WHERE id = ?", (c["id"],))["status"] == "cancelled"
    print("  status OK")


def test_expense():
    before = one("SELECT COUNT(*) AS n FROM expenses")["n"]
    o = add(ADMIN, "Mont", amount="499", add_expense="1", expense_category="Giyim", ordered_on=d(-1))
    e = one("SELECT * FROM expenses WHERE id = ?", (o["expense_id"],))
    assert e and e["amount"] == 499 and e["category"] == "Giyim" and e["note"] == "Trendyol · Mont"
    assert e["date"] == d(-1) and e["user_id"] == 1
    # Tutar yoksa eklenmez; geçersiz kategori -> Diğer
    assert add(ADMIN, "Hediye kutusu", add_expense="1")["expense_id"] is None
    x = add(ADMIN, "Çiçek", store="Çiçeksepeti", amount="350", add_expense="1", expense_category="Uydurma")
    assert one("SELECT category FROM expenses WHERE id = ?", (x["expense_id"],))["category"] == "Diğer"
    assert one("SELECT COUNT(*) AS n FROM expenses")["n"] == before + 2
    # Düzenlemede sonradan eklenir, ikinci kez eklenmez
    k = add(ADMIN, "Kablo", amount="120")
    assert k["expense_id"] is None
    k = edit(ADMIN, k, add_expense="1", expense_category="Ev")
    assert k["expense_id"] and one("SELECT category FROM expenses WHERE id = ?", (k["expense_id"],))["category"] == "Ev"
    first = k["expense_id"]
    k = edit(ADMIN, k, add_expense="1", amount="130")
    assert k["expense_id"] == first and one("SELECT COUNT(*) AS n FROM expenses")["n"] == before + 3
    assert "Harcamalara eklendi" in ADMIN.text(f"/siparisler/{k['id']}")
    # İade tamamlanınca harcama kaydı çöp kutusuna taşınabilir; iptalde istenmezse kalır
    status(ADMIN, o, "delivered")
    status(ADMIN, o, "returning")
    assert "Harcama kaydını da sil" in ADMIN.text(f"/siparisler/{o['id']}")
    status(ADMIN, o, "returned", drop_expense="1")
    assert one("SELECT id FROM expenses WHERE id = ?", (o["expense_id"],)) is None
    assert one("SELECT expense_id FROM orders WHERE id = ?", (o["id"],))["expense_id"] is None
    assert one("SELECT module FROM trash WHERE label LIKE '%Mont%'")["module"] == "expenses"
    status(ADMIN, x, "cancelled")
    assert one("SELECT id FROM expenses WHERE id = ?", (x["expense_id"],))
    # Başkasının siparişinden harcama eklenemez
    assert AYSE_C.post(f"/siparisler/{k['id']}", data={"item": "x", "add_expense": "1", "amount": "5"}).status_code == 404
    print("  expense OK")


def test_tabs_and_summary():
    c = MEHMET_C
    w1 = add(c, "Masa lambası", amount="100", expected_on=d(5))
    w2 = add(c, "Şarj aleti", amount="200", expected_on=d(1), status="shipped")
    expired = add(c, "Eski kazak", amount="300", ordered_on=d(-30), status="delivered", delivered_on=d(-20))
    active = add(c, "Yeni pantolon", amount="400", ordered_on=d(-5), status="delivered", delivered_on=d(-3))
    soon = add(c, "Termos", amount="50", ordered_on=d(-15), status="delivered", delivered_on=d(-12))
    returning = add(c, "Hatalı fare", amount="250", ordered_on=d(-9), status="returning", delivered_on=d(-8))
    returned = add(c, "Dar ceket", amount="700", ordered_on=d(-9), status="returned", delivered_on=d(-8))
    cancelled = add(c, "Vazgeçilen saat", amount="1000", expected_on=d(3))
    status(c, cancelled, "cancelled")
    assert w2["status"] == "shipped" and expired["return_by"] == d(-6) and active["return_by"] == d(11)
    assert soon["return_by"] == d(2) and returning["return_by"] == d(6) and returned["status"] == "returned"

    c.text("/siparisler/?sekme=tumu")  # birikmiş "eklendi" bildirimleri burada gösterilsin
    # Özet: yolda 2, iade süresi devam eden 2 (Termos, pantolon), bu ay (iade/iptal hariç)
    month = d(0)[:7]
    total = sum(o["amount"] for o in (w1, w2, expired, active, soon, returning)
                if o["ordered_on"][:7] == month)
    page = c.text("/siparisler/")
    assert f"Yolda 2 sipariş · iade süresi devam eden 2 · bu ay {fmt_money(total)}" in page
    # Bekleyenler: beklenen tarihi en yakın olan üstte
    assert "Şarj aleti" in page and "Masa lambası" in page and "Eski kazak" not in page
    assert page.index("Şarj aleti") < page.index("Masa lambası")
    assert "📦 Teslim aldım" in page and "🚚 Kargoya verildi" in page
    assert '<span class="pill-count">2</span>' in page and '<span class="pill-count">3</span>' in page
    # Teslim alınanlar: iade süresi devam edenler (son günü en yakın) üstte, süresi dolan altta
    page = c.text("/siparisler/?sekme=teslim")
    assert page.index("Termos") < page.index("Yeni pantolon") < page.index("Eski kazak")
    assert "iade için 2 gün kaldı" in page and "iade için 11 gün kaldı" in page and "iade süresi doldu" in page
    assert page.count("↩️ İade başlattım") == 2 and "Masa lambası" not in page  # süresi dolana buton yok
    # İadeler: süreçte olan üstte
    page = c.text("/siparisler/?sekme=iade")
    assert page.index("Hatalı fare") < page.index("Dar ceket") and "Termos" not in page
    # Tümü: iptal edilen de görünür; bilinmeyen sekme -> bekleyenler
    page = c.text("/siparisler/?sekme=tumu")
    assert "Vazgeçilen saat" in page and "İptal edildi" in page and "Dar ceket" in page
    assert "Vazgeçilen saat" not in c.text("/siparisler/?sekme=xyz")
    print("  tabs OK")


def test_upcoming_calendar_search():
    c = MEHMET_C
    c.text("/siparisler/")  # birikmiş bildirimler panoyu yanıltmasın
    home = c.text("/")
    assert "Şarj aleti bekleniyor" in home and "Masa lambası bekleniyor" in home
    assert "Termos iade son günü" in home
    assert "Yeni pantolon iade son günü" not in home  # 11 gün sonra: 7 günlük pencerenin dışında
    assert "Vazgeçilen saat" not in home and "Hatalı fare" not in home
    cal = c.text(f"/takvim/?ay={d(1)[:7]}") + c.text(f"/takvim/?ay={d(5)[:7]}") + c.text(f"/takvim/?ay={d(2)[:7]}")
    assert "Şarj aleti bekleniyor" in cal and "Masa lambası bekleniyor" in cal and "Termos iade son günü" in cal
    assert "Vazgeçilen saat" not in c.text(f"/takvim/?ay={d(3)[:7]}")  # iptal edilen takvimde yok
    assert "Yeni pantolon iade son günü" in c.text(f"/takvim/?ay={d(11)[:7]}")
    # Arama: mağaza, ürün, takip no, not
    o = one("SELECT * FROM orders WHERE item = 'Kulaklık'")
    for q in ("kulaklik", "hepsiburada", "123456", "mavi+renk"):
        assert "Kulaklık" in ADMIN.text(f"/ara/?q={q}"), q
    assert "Kulaklık" not in AYSE_C.text("/ara/?q=kulaklik")
    assert f"/siparisler/{o['id']}" in ADMIN.text("/ara/?q=kulaklik")
    print("  upcoming/calendar/search OK")


def test_cron():
    shipped = add(ADMIN, "Süpürge", store="Hepsiburada", expected_on=d(0), carrier="Aras", tracking_no="A1",
                  ordered_on=d(-3), status="shipped")
    two = add(ADMIN, "Kulaklık kılıfı", ordered_on=d(-14), status="delivered", delivered_on=d(-12),
              order_url="https://www.amazon.com.tr/siparis/9")
    last = add(ADMIN, "Lamba", ordered_on=d(-14), status="delivered", delivered_on=d(-14))
    add(ADMIN, "İade edilen", ordered_on=d(-14), status="returned", delivered_on=d(-14))
    add(ADMIN, "İadesi başlatılan", ordered_on=d(-14), status="returning", delivered_on=d(-12))
    gone = add(ADMIN, "İptal edilen", expected_on=d(0))
    status(ADMIN, gone, "cancelled")
    assert two["return_by"] == d(2) and last["return_by"] == d(0)
    add(AYSE_C, "Ayşe'nin paketi", store="Amazon", expected_on=d(0))

    # 09:00'dan önce gönderilmez
    NOW[0] = datetime.combine(T, time(8, 30), tzinfo=TZ)
    assert cron()["orders_sent"] == 0
    NOW[0] = datetime.combine(T, time(9, 5), tzinfo=TZ)
    before = len(sent())
    assert cron()["orders_sent"] == 4  # Mehmet'in Termos'u (Telegram yok) gönderilmez
    new = sent()[before:]
    texts = {chat: [t for c_, t in new if c_ == chat] for chat in ("100", "200")}
    assert len(texts["100"]) == 3 and len(texts["200"]) == 1
    delivery = next(t for t in texts["100"] if "Süpürge" in t)
    assert "Bugün gelmesi bekleniyor" in delivery and "Süpürge (Hepsiburada)" in delivery
    assert 'href="https://www.google.com/search?q=Aras+kargo+takip+A1"' in delivery and "Aras · A1" in delivery
    assert f"/siparisler/{shipped['id']}" in delivery
    r2 = next(t for t in texts["100"] if "Kulaklık kılıfı" in t)
    assert "İade için son 2 gün" in r2 and 'href="https://www.amazon.com.tr/siparis/9"' in r2
    r0 = next(t for t in texts["100"] if "Lamba" in t)
    assert "Bugün iade için son gün" in r0
    assert "Ayşe'nin paketi" in texts["200"][0] or "Ayşe&#x27;nin paketi" in texts["200"][0]
    assert not any("İade edilen" in t or "İadesi başlatılan" in t or "İptal edilen" in t for _, t in new)
    # Her biri bir kez
    assert cron()["orders_sent"] == 0 and len(sent()) == before + 4
    assert _flags(one("SELECT sent_flags FROM orders WHERE id = ?", (shipped["id"],))["sent_flags"]) == {"delivery": d(0)}
    # Teslim alındı ama iade süresi kısa (2 gün): yeni olay olarak yine bir kez
    edit(ADMIN, one("SELECT * FROM orders WHERE id = ?", (shipped["id"],)), status="delivered", delivered_on=d(0),
         return_days="2")
    flags = _flags(one("SELECT sent_flags FROM orders WHERE id = ?", (shipped["id"],))["sent_flags"])
    assert flags == {"delivery": d(0)}
    assert cron()["orders_sent"] == 1 and "İade için son 2 gün" in sent()[-1][1] and "Süpürge" in sent()[-1][1]
    flags = _flags(one("SELECT sent_flags FROM orders WHERE id = ?", (shipped["id"],))["sent_flags"])
    assert flags == {"delivery": d(0), "return2": d(2)}
    assert cron()["orders_sent"] == 0
    # Teslim tarihi düzeltilince yeni son güne göre yeniden kurulur (ertesi gün yine "son 2 gün")
    edit(ADMIN, one("SELECT * FROM orders WHERE id = ?", (two["id"],)), delivered_on=d(-11))
    NOW[0] = datetime.combine(T + timedelta(days=1), time(9, 30), tzinfo=TZ)
    before = len(sent())
    result = cron()
    new = [t for _, t in sent()[before:]]
    assert any("Kulaklık kılıfı" in t and "İade için son 2 gün" in t for t in new), new
    assert not any("Lamba" in t for t in new)  # son günü geçti
    # İade başlatılınca son gün hatırlatması gelmez
    status(ADMIN, one("SELECT * FROM orders WHERE id = ?", (shipped["id"],)), "returning")
    NOW[0] = datetime.combine(T + timedelta(days=2), time(9, 30), tzinfo=TZ)
    before = len(sent())
    cron()
    assert not any("Süpürge" in t for _, t in sent()[before:])
    assert result["errors"] == []
    NOW[0] = datetime.combine(T, time(10, 0), tzinfo=TZ)
    print("  cron OK")


def test_trash():
    o = add(ADMIN, "Silinecek bardak", amount="75", note="cam")
    r = ADMIN.post(f"/siparisler/{o['id']}/sil", follow_redirects=True)
    assert "çöp kutusuna taşındı" in r.get_data(as_text=True)
    assert one("SELECT id FROM orders WHERE id = ?", (o["id"],)) is None
    item = one("SELECT * FROM trash WHERE module = 'orders'")
    assert item["label"] == "🚚 Trendyol · Silinecek bardak"
    assert "Silinecek bardak" in ADMIN.text("/cop-kutusu/")
    ADMIN.post(f"/cop-kutusu/{item['id']}/geri")
    back = one("SELECT * FROM orders WHERE id = ?", (o["id"],))
    assert back and back["note"] == "cam" and back["amount"] == 75
    print("  trash OK")


def test_menu():
    assert "Siparişler" in ADMIN.text("/") and "/siparisler/" in ADMIN.text("/menu")
    print("  menu OK")


if __name__ == "__main__":
    test_crud_and_validation()
    test_tracking_link()
    test_status_and_return_by()
    test_expense()
    test_tabs_and_summary()
    test_upcoming_calendar_search()
    test_cron()
    test_trash()
    test_menu()
    print("OK")
