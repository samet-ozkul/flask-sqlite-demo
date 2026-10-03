"""Ödünç: ekleme / doğrulama (iki yön), Ev Envanteri ve Kişiler bağlantısı, geri geldi -> geçmiş, kaç gündür,
gecikme rozeti, WhatsApp linki, fotoğraf + kota, Telegram hatırlatması ve butonları, /odunc komutu,
yaklaşanlar / takvim / arama, kullanıcı yalıtımı, çöp kutusu.

Telegram ve saat taklit edilir; dış servis çağrılmaz.
Çalıştır: .venv/Scripts/python tests/test_loans.py
"""
import io
import json
import os
import re
import sys
from datetime import date, datetime, time, timedelta
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

from PIL import Image  # noqa: E402

app = make_app()
os.environ["CRON_SECRET"] = "gizli"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.external as ext  # noqa: E402
import pano.telegram as tg  # noqa: E402
import pano.todo_reminders as todo  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.calendar_events import events_between  # noqa: E402
from pano.db import execute, query, query_one  # noqa: E402
from pano.modules import loans as ln  # noqa: E402
from pano.utils import TZ, today  # noqa: E402

CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {})) or {"message_id": 7}
ext.weather = lambda lat, lon: None
ext.rates = lambda: None
T = today()
NOW = [datetime.combine(T, time(10, 0), tzinfo=TZ)]
todo.now_local = lambda: NOW[0]

with app.app_context():
    AYSE = create_user("ayse", "ayse12345")
with app.test_request_context():
    SECRET = tg.webhook_secret()
ADMIN = Client(app)
AYSE_C = Client(app, "ayse", "ayse12345")
RAW = app.test_client()
UPDATE_ID = [0]


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


def at(n, hour=10, minute=0):
    return datetime.combine(T + timedelta(days=n), time(hour, minute), tzinfo=TZ)


def loan(lid):
    return one("SELECT * FROM loans WHERE id = ?", (lid,))


def count(user_id=1):
    return one("SELECT COUNT(*) AS n FROM loans WHERE user_id = ?", (user_id,))["n"]


def add(client, item, person, **fields):
    """Formdan kayıt; yeni kaydın id'si (eklenmediyse None)."""
    data = {"item_name": item, "person_name": person, "direction": "lent"}
    data.update({k: str(v) for k, v in fields.items()})
    before = one("SELECT MAX(id) AS m FROM loans")["m"] or 0
    client.post("/odunc/yeni", data=data, follow_redirects=True)  # bildirim burada okunur
    row = one("SELECT id FROM loans WHERE id > ? ORDER BY id DESC LIMIT 1", (before,))
    return row["id"] if row else None


def reset():
    run("DELETE FROM loans")


def section(client, url):
    """Sayfadaki kayıt listesi (form ve sekmeler hariç); liste yoksa ''."""
    page = client.text(url)
    marker = '<ul class="rows loan-rows">'
    return page.split(marker, 1)[1].split("</ul>", 1)[0] if marker in page else ""


def row_of(html, text):
    return next(li for li in re.findall(r"<li>(.*?)</li>", html, re.S) if text in li)


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (900, 600), (200, 80, 30)).save(buf, "PNG")
    return buf.getvalue()


def calls(method):
    return [p for m, p in CALLS if m == method]


def sent():
    return [(p["chat_id"], p["text"], p.get("reply_markup")) for p in calls("sendMessage")]


def buttons(markup):
    return [b["callback_data"] for r in json.loads(markup or "{}").get("inline_keyboard", []) for b in r]


def button_texts(markup):
    return [b["text"] for r in json.loads(markup or "{}").get("inline_keyboard", []) for b in r]


def cron(path="hatirlatma"):
    r = RAW.get(f"/cron/gizli/{path}")
    assert r.status_code == 200, r.status_code
    return r.json


def _post_update(update):
    UPDATE_ID[0] += 1
    r = RAW.post("/telegram/webhook", data=json.dumps({"update_id": UPDATE_ID[0], **update}),
                 content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    assert r.status_code == 200


def press(data, chat=100):
    _post_update({"callback_query": {"id": "cb", "data": data, "from": {"id": chat},
                                     "message": {"message_id": 7, "chat": {"id": chat}}}})


def say(text, chat=100):
    _post_update({"message": {"message_id": UPDATE_ID[0] + 1, "chat": {"id": chat}, "text": text}})
    return calls("sendMessage")[-1]["text"]


def test_text():
    s = ln.suffix
    assert (s("Ahmet", "loc"), s("Ayşe", "loc"), s("Burak", "loc"), s("Can", "loc"), s("Oğuz", "loc")) \
        == ("Ahmet'te", "Ayşe'de", "Burak'ta", "Can'da", "Oğuz'da")
    assert (s("Ahmet", "abl"), s("Ali", "abl"), s("Burak", "abl")) == ("Ahmet'ten", "Ali'den", "Burak'tan")
    assert (s("Ahmet", "dat"), s("Ayşe", "dat"), s("Burak", "dat"), s("Ahmet Yılmaz", "dat")) \
        == ("Ahmet'e", "Ayşe'ye", "Burak'a", "Ahmet Yılmaz'a")
    assert s("ILGAZ", "loc") == "ILGAZ'da" and s("İLKER", "dat") == "İLKER'e"
    assert [s(f"12 {m}", "loc") for m in ("Ocak", "Nisan", "Mayıs", "Ağustos", "Eylül", "Aralık")] \
        == ["12 Ocak'ta", "12 Nisan'da", "12 Mayıs'ta", "12 Ağustos'ta", "12 Eylül'de", "12 Aralık'ta"]
    w = ln.since_words
    assert [w(n) for n in (0, 1, 12, 13, 14, 21, 59, 60, 200, 400)] == [
        "bugünden beri", "dünden beri", "12 gündür", "13 gündür", "2 haftadır", "3 haftadır", "8 haftadır", "2 aydır",
        "6 aydır", "1 yıldır"]
    assert [ln.duration_words(n) for n in (0, 5, 21, 90)] == ["aynı gün", "5 gün", "3 hafta", "3 ay"]
    row = {"given_on": D(-21), "returned_on": None, "person_name": "Ahmet", "direction": "lent"}
    assert ln.days_out(row) == 21 and ln.held_text(row) == "3 haftadır Ahmet'te"
    assert ln.held_text({**row, "direction": "borrowed"}) == "Ahmet'ten · 3 haftadır sende"
    assert ln.days_out({**row, "returned_on": D(-9)}) == 12
    assert ln.held_text({**row, "returned_on": D(-9)}).startswith("Ahmet'e verildi · ")
    assert ln.held_text({**row, "returned_on": D(-9)}).endswith("(12 gün)")
    assert ln.badge_text({**row, "given_on": D(-12)}) == "📤 Ahmet'te (12 gündür)"
    print("  text OK")


def test_crud():
    assert "Ödünç verdiğin eşya yok" in ADMIN.text("/odunc/")
    mid = add(ADMIN, "Matkap", "Ahmet", phone="0532 123 45 67", given_on=D(-21), due_on=D(5), remind_every_days=7,
              note="Uçlarıyla birlikte")
    m = loan(mid)
    assert (m["user_id"], m["direction"], m["item_name"], m["person_name"], m["phone"], m["given_on"], m["due_on"],
            m["remind_every_days"], m["note"], m["returned_on"], m["inventory_id"], m["contact_id"]) \
        == (1, "lent", "Matkap", "Ahmet", "0532 123 45 67", D(-21), D(5), 7, "Uçlarıyla birlikte", None, None, None)
    sid = add(ADMIN, "Kamp sandalyesi", "Ayşe", direction="borrowed", given_on=D(-2))
    s = loan(sid)
    assert (s["direction"], s["due_on"], s["remind_every_days"], s["phone"]) == ("borrowed", None, 14, "")
    # Varsayılanlar: tarih boşsa bugün, bilinmeyen yön -> verdim, geçersiz aralık -> 14, "hiç" -> 0
    did = add(ADMIN, "Tencere", "Ece", direction="yok", remind_every_days=99)
    assert (loan(did)["given_on"], loan(did)["direction"], loan(did)["remind_every_days"]) == (T.isoformat(), "lent", 14)
    nid = add(ADMIN, "Şemsiye", "Oğuz", remind_every_days=0, phone="+90 (555) 111-22-33")
    assert loan(nid)["remind_every_days"] == 0
    # Reddedilenler: eşya / kişi yok, bozuk telefon, ileri tarih, dönüş verilişten önce; form yazılanlarla geri gelir
    before = count()
    for bad, msg in (({"item_name": ""}, "Eşya adı gerekli"), ({"person_name": " "}, "Kişi adı gerekli"),
                     ({"phone": "abc"}, "Telefon numarası geçersiz"), ({"phone": "12"}, "Telefon numarası geçersiz"),
                     ({"given_on": D(1)}, "ileri bir gün olamaz"),
                     ({"given_on": D(-3), "due_on": D(-5)}, "önce olamaz")):
        r = ADMIN.post("/odunc/yeni", data={"item_name": "Hatalı eşya", "person_name": "Biri", **bad})
        page = r.get_data(as_text=True)
        assert r.status_code == 400 and msg in page, (bad, r.status_code)
        if bad.get("item_name") != "":
            assert 'value="Hatalı eşya"' in page  # yazılanlar kaybolmaz
    assert count() == before

    # Sekmeler: verdiklerim / aldıklarım; satırda kaç gündür, dönüş rozeti, hızlı buton
    lent_html = section(ADMIN, "/odunc/")
    assert "Matkap" in lent_html and "Kamp sandalyesi" not in lent_html
    mrow = row_of(lent_html, "Matkap")
    assert "3 haftadır Ahmet&#39;te" in mrow and "5 gün sonra" in mrow and "badge later" in mrow
    assert "✅ Geri geldi" in mrow and f"/odunc/{mid}/geri" in mrow
    assert lent_html.index("Matkap") < lent_html.index("Tencere")  # dönüş tarihi olanlar önce
    borrowed_html = section(ADMIN, "/odunc/?sekme=aldiklarim")
    srow = row_of(borrowed_html, "Kamp sandalyesi")
    assert "Ayşe&#39;den · 2 gündür sende" in srow and "✅ Geri verdim" in srow and "Matkap" not in borrowed_html
    page = ADMIN.text("/odunc/")
    assert "3 eşyan başkasında · 1 emanet sende" in page
    assert 'Verdiklerim <span class="pill-count">3</span>' in page and 'Aldıklarım <span class="pill-count">1</span>' in page
    assert "Matkap" in section(ADMIN, "/odunc/?sekme=bilinmeyen")  # bilinmeyen sekme -> verdiklerim

    # Gecikmiş rozet kırmızı
    ADMIN.post(f"/odunc/{mid}", data={"item_name": "Matkap", "person_name": "Ahmet", "direction": "lent",
                                      "phone": "0532 123 45 67", "given_on": D(-21), "due_on": D(-3),
                                      "remind_every_days": "7", "note": "Uçlarıyla birlikte"})
    assert loan(mid)["due_on"] == D(-3)
    mrow = row_of(section(ADMIN, "/odunc/"), "Matkap")
    assert "badge overdue" in mrow and "3 gün geçti" in mrow
    assert "1 gecikmiş" in ADMIN.text("/odunc/")
    detail = ADMIN.text(f"/odunc/{mid}")
    assert "badge overdue" in detail and "Uçlarıyla birlikte" in detail and "3 haftadır Ahmet&#39;te" in detail
    # Düzenlemede de doğrulama: kayıt değişmez
    ADMIN.post(f"/odunc/{mid}", data={"item_name": "", "person_name": "Ahmet"})
    assert loan(mid)["item_name"] == "Matkap"
    print("  crud OK")


def test_return_and_history():
    mid = one("SELECT id FROM loans WHERE item_name = 'Matkap'")["id"]
    sid = one("SELECT id FROM loans WHERE item_name = 'Kamp sandalyesi'")["id"]
    # İleri tarih ve verilişten önce reddedilir
    ADMIN.post(f"/odunc/{mid}/geri", data={"returned_on": D(1)})
    ADMIN.post(f"/odunc/{mid}/geri", data={"returned_on": D(-30)})
    assert loan(mid)["returned_on"] is None
    r = ADMIN.post(f"/odunc/{mid}/geri", data={"next": "/odunc/"}, follow_redirects=True)
    assert "Matkap Ahmet&#39;ten geri geldi" in r.get_data(as_text=True)
    assert loan(mid)["returned_on"] == T.isoformat()
    assert "Zaten geri gelmiş" in ADMIN.post(f"/odunc/{mid}/geri", follow_redirects=True).get_data(as_text=True)
    # Geçmişe dönük: dün geri verdim
    r = ADMIN.post(f"/odunc/{sid}/geri", data={"returned_on": D(-1)}, follow_redirects=True)
    assert "Kamp sandalyesi Ayşe&#39;ye geri verildi" in r.get_data(as_text=True) and loan(sid)["returned_on"] == D(-1)
    assert "Matkap" not in section(ADMIN, "/odunc/") and "Kamp" not in section(ADMIN, "/odunc/?sekme=aldiklarim")
    hist = section(ADMIN, "/odunc/?sekme=gelenler")
    assert hist.index("Matkap") < hist.index("Kamp sandalyesi")  # en son geri gelen üstte
    assert "Ahmet&#39;e verildi" in row_of(hist, "Matkap") and "(3 hafta)" in row_of(hist, "Matkap")
    assert "Ayşe&#39;den alındı" in row_of(hist, "Kamp") and "(1 gün)" in row_of(hist, "Kamp")
    assert "↩️ Geri al" in row_of(hist, "Matkap") and "wa.me" not in hist and "badge" not in row_of(hist, "Matkap")
    # Yanlışlıkla işaretlendiyse geri al
    ADMIN.post(f"/odunc/{sid}/ac")
    assert loan(sid)["returned_on"] is None and "Kamp sandalyesi" in section(ADMIN, "/odunc/?sekme=aldiklarim")
    print("  return/history OK")


def test_whatsapp():
    row = {"direction": "lent", "item_name": "Matkap", "person_name": "Ahmet", "given_on": "2026-09-12",
           "returned_on": None, "phone": "0532 123 45 67"}
    t = date(2026, 10, 3)
    assert ln.whatsapp_text(row, t) == \
        "Merhaba Ahmet, 12 Eylül'de verdiğim matkap sende mi? Müsait olduğunda alabilir miyim? 🙂"
    assert ln.whatsapp_text({**row, "direction": "borrowed"}, t) == \
        "Merhaba Ahmet, 12 Eylül'de senden aldığım matkap hâlâ bende; geri getirmek istiyorum, ne zaman uygun? 🙂"
    assert "2 Ocak'ta verdiğim" in ln.whatsapp_text({**row, "given_on": "2026-01-02"}, t)
    assert "12.09.2025 tarihinde verdiğim" in ln.whatsapp_text({**row, "given_on": "2025-09-12"}, t)
    assert ", bugün verdiğim" in ln.whatsapp_text({**row, "given_on": "2026-10-03"}, t)
    assert ", dün verdiğim" in ln.whatsapp_text({**row, "given_on": "2026-10-02"}, t)
    assert "verdiğim TV sende" in ln.whatsapp_text({**row, "item_name": "TV"}, t)       # kısaltma olduğu gibi
    assert ln.whatsapp_text({**row, "person_name": "Ahmet Yılmaz"}, t).startswith("Merhaba Ahmet, ")  # sadece ilk ad
    assert "verdiğim ip merdiveni" in ln.whatsapp_text({**row, "item_name": "İp merdiveni"}, t)
    link = ln.whatsapp_link(row, t)
    assert link.startswith("https://wa.me/905321234567?text=") and " " not in link
    assert "%C4%9F" in link and "%F0%9F%99%82" in link and "%27" in link  # ğ, 🙂, kesme işareti kodlu
    assert link.isascii()
    assert parse_qs(urlsplit(link).query)["text"] == [ln.whatsapp_text(row, t)]
    assert ln.whatsapp_link({**row, "phone": "+49 151 2345678"}, t).startswith("https://wa.me/491512345678?text=")
    assert ln.whatsapp_link({**row, "phone": ""}, t) is None and ln.whatsapp_link({**row, "phone": "12"}, t) is None
    assert ln.whatsapp_link({**row, "returned_on": "2026-10-01"}, t) is None

    # Sayfada: telefonlu açık kayıtta bağlantı, telefonsuzda yok
    wid = add(ADMIN, "Merdiven", "Hakan", phone="0544 999 88 77", given_on=D(-4))
    html = section(ADMIN, "/odunc/")
    href = re.search(r'href="(https://wa\.me/[^"]+)"', row_of(html, "Merdiven")).group(1).replace("&amp;", "&")
    assert href.startswith("https://wa.me/905449998877?text=")
    expected = ln.whatsapp_text(loan(wid))
    assert parse_qs(urlsplit(href).query)["text"] == [expected] and "Merhaba Hakan, " in expected
    assert "wa.me" not in row_of(html, "Tencere")
    assert "💬 WhatsApp'tan nazikçe hatırlat" in ADMIN.text(f"/odunc/{wid}")
    print("  whatsapp OK")


def test_inventory_link():
    r = ADMIN.post("/envanter/yeni", data={"name": "Darbeli matkap", "location": "Balkon dolabı"})
    assert r.status_code == 302
    inv = one("SELECT id FROM inventory WHERE name = 'Darbeli matkap'")["id"]
    # Eşya sayfasından "Ödünç ver": form o eşya seçili açılır, kayıttan sonra eşya sayfasına dönülür
    item_page = ADMIN.text(f"/envanter/{inv}")
    assert f"/odunc/yeni?envanter={inv}" in item_page and "hiç ödünç verilmedi" in item_page
    form = ADMIN.text(f"/odunc/yeni?envanter={inv}")
    assert 'value="Darbeli matkap"' in form
    assert re.search(rf'<option value="{inv}" data-name="Darbeli matkap"\s+selected>', form)
    assert f'name="next" value="/envanter/{inv}"' in form
    # JS yokken ad boş gönderilse de envanterdeki adla bağlanır
    r = ADMIN.post("/odunc/yeni", data={"inventory_id": str(inv), "item_name": "", "person_name": "Mehmet",
                                        "given_on": D(-12), "next": f"/envanter/{inv}"})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/envanter/{inv}")
    lid = one("SELECT id FROM loans WHERE inventory_id = ?", (inv,))["id"]
    assert (loan(lid)["item_name"], loan(lid)["person_name"]) == ("Darbeli matkap", "Mehmet")
    # Envanter listesinde ve eşya sayfasında rozet / geçmiş
    listing = ADMIN.text("/envanter/").split('<ul class="rows">', 1)[1]
    assert "📤 Mehmet&#39;te (12 gündür)" in listing and f"/odunc/{lid}" in listing
    item_page = ADMIN.text(f"/envanter/{inv}")
    assert "12 gündür Mehmet&#39;te" in item_page and f"/odunc/{lid}/geri" in item_page
    assert "Ev Envanteri'ndeki eşya" in ADMIN.text(f"/odunc/{lid}")
    # Gecikince rozet kırmızı
    run("UPDATE loans SET due_on = ? WHERE id = ?", (D(-1), lid))
    assert "badge overdue" in row_of(ADMIN.text("/envanter/").split('<ul class="rows">', 1)[1], "Darbeli matkap")
    # Geri gelince rozet kalkar, geçmişte kalır
    ADMIN.post(f"/odunc/{lid}/geri", data={"next": f"/envanter/{inv}"})
    assert "📤 Mehmet" not in ADMIN.text("/envanter/")
    item_page = ADMIN.text(f"/envanter/{inv}")
    assert "Mehmet&#39;e verildi" in item_page and "(12 gün)" in item_page
    # Başkasının envanter eşyası seçilemez (yok sayılır)
    r = AYSE_C.post("/odunc/yeni", data={"inventory_id": str(inv), "item_name": "", "person_name": "Ali"})
    assert r.status_code == 400 and "Eşya adı gerekli" in r.get_data(as_text=True)
    aid = add(AYSE_C, "Kendi matkabım", "Ali", inventory_id=inv)
    assert loan(aid)["inventory_id"] is None and loan(aid)["user_id"] == AYSE
    assert AYSE_C.get(f"/odunc/yeni?envanter={inv}").status_code == 200
    assert "Darbeli matkap" not in AYSE_C.text(f"/odunc/yeni?envanter={inv}")
    # Envanterden eşya silinince ödünç kaydı adıyla kalır; eşya geri getirilince bağlantı döner
    lid2 = add(ADMIN, "", "Selin", inventory_id=inv)
    assert loan(lid2)["inventory_id"] == inv
    ADMIN.post(f"/envanter/{inv}/sil")
    assert one("SELECT 1 AS x FROM inventory WHERE id = ?", (inv,)) is None
    assert loan(lid)["item_name"] == "Darbeli matkap" and loan(lid2)["item_name"] == "Darbeli matkap"
    page = ADMIN.text(f"/odunc/{lid2}")
    assert "Darbeli matkap" in page and "Ev Envanteri'ndeki eşya" not in page
    assert "Darbeli matkap" in section(ADMIN, "/odunc/")
    item = one("SELECT id FROM trash WHERE module = 'inventory' ORDER BY id DESC LIMIT 1")
    ADMIN.post(f"/cop-kutusu/{item['id']}/geri")
    assert "Ev Envanteri'ndeki eşya" in ADMIN.text(f"/odunc/{lid2}")
    assert "📤 Selin&#39;de (bugünden beri)" in ADMIN.text("/envanter/")
    print("  inventory OK")


def test_contact_link():
    ADMIN.post("/kisiler/yeni", data={"name": "Ahmet Yılmaz", "phone": "0555 111 22 33", "relation": "Arkadaş"})
    cid = one("SELECT id FROM contacts WHERE name = 'Ahmet Yılmaz'")["id"]
    form = ADMIN.text(f"/odunc/yeni?kisi={cid}")
    assert 'value="Ahmet Yılmaz"' in form and 'value="0555 111 22 33"' in form
    assert f'name="next" value="/kisiler/{cid}"' in form and 'data-phone="0555 111 22 33" selected' in form
    # Kişi seçilip ad / telefon boş bırakılırsa Kişiler'dekiler kullanılır
    r = ADMIN.post("/odunc/yeni", data={"item_name": "Çadır", "contact_id": str(cid), "person_name": "", "phone": "",
                                        "given_on": D(-21), "next": f"/kisiler/{cid}"})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/kisiler/{cid}")
    lid = one("SELECT id FROM loans WHERE item_name = 'Çadır'")["id"]
    assert (loan(lid)["person_name"], loan(lid)["phone"], loan(lid)["contact_id"]) == ("Ahmet Yılmaz", "0555 111 22 33", cid)
    bid = add(ADMIN, "Kitap", "", direction="borrowed", contact_id=cid, given_on=D(-1))
    # Kişi sayfasında o kişideki eşyalar ve "Ödünç ver"
    page = ADMIN.text(f"/kisiler/{cid}")
    assert "Onda:" in page and f"/odunc/{lid}" in page and "(3 haftadır)" in page
    assert "Ondan aldığın:" in page and f"/odunc/{bid}" in page and "(dünden beri)" in page
    assert f"/odunc/yeni?kisi={cid}" in page
    assert "📇 Kişi sayfası" in ADMIN.text(f"/odunc/{lid}")
    # Kişiler'deki telefon biçimi denetlenmez (oradan geldiği gibi kabul)
    ADMIN.post("/kisiler/yeni", data={"name": "Bekçi", "phone": "dahili 12"})
    kid = one("SELECT id FROM contacts WHERE name = 'Bekçi'")["id"]
    assert loan(add(ADMIN, "Merdiven", "", contact_id=kid))["phone"] == "dahili 12"
    # Başkasının kişisi seçilemez
    assert loan(add(AYSE_C, "Top", "Can", contact_id=cid))["contact_id"] is None
    assert "Ahmet Yılmaz" not in AYSE_C.text(f"/odunc/yeni?kisi={cid}")
    # Kişi silinince ödünç kaydı adıyla kalır
    ADMIN.post(f"/kisiler/{cid}/sil")
    assert loan(lid)["person_name"] == "Ahmet Yılmaz" and "📇 Kişi sayfası" not in ADMIN.text(f"/odunc/{lid}")
    print("  contacts OK")


def test_photo_and_quota():
    r = ADMIN.post("/odunc/yeni", data={"item_name": "Bisiklet", "person_name": "Kerem",
                                        "photo": (io.BytesIO(png_bytes()), "bisiklet.png")},
                   content_type="multipart/form-data")
    assert r.status_code == 302
    lid = one("SELECT id FROM loans WHERE item_name = 'Bisiklet'")["id"]
    att = one("SELECT * FROM attachments WHERE entity = 'loan' AND entity_id = ?", (lid,))
    assert att and att["user_id"] == 1 and att["mime"] == "image/jpeg"
    assert f"/dosya/{att['id']}/kucuk" in section(ADMIN, "/odunc/")
    assert f"/dosya/{att['id']}" in ADMIN.text(f"/odunc/{lid}")
    # Sonradan ek yükleme (mevcut ek sistemi); başkası yükleyemez
    r = ADMIN.post("/dosya/yukle", data={"entity": "loan", "entity_id": str(lid), "next": f"/odunc/{lid}",
                                         "file": (io.BytesIO(png_bytes()), "ikinci.png")},
                   content_type="multipart/form-data")
    assert r.status_code == 302 and one("SELECT COUNT(*) AS n FROM attachments WHERE entity = 'loan'")["n"] == 2
    r = AYSE_C.post("/dosya/yukle", data={"entity": "loan", "entity_id": str(lid),
                                          "file": (io.BytesIO(png_bytes()), "x.png")}, content_type="multipart/form-data")
    assert r.status_code == 404
    # Bozuk dosya ya da kota: kayıt yine eklenir, uyarı gösterilir
    r = ADMIN.post("/odunc/yeni", data={"item_name": "Pompa", "person_name": "Kerem",
                                        "photo": (io.BytesIO(b"resim degil"), "pompa.png")},
                   content_type="multipart/form-data", follow_redirects=True)
    assert "Fotoğraf eklenemedi" in r.get_data(as_text=True) and one("SELECT 1 AS x FROM loans WHERE item_name = 'Pompa'")
    run("UPDATE users SET upload_max_mb = 0 WHERE id = 1")
    r = ADMIN.post("/odunc/yeni", data={"item_name": "Kask", "person_name": "Kerem",
                                        "photo": (io.BytesIO(png_bytes()), "kask.png")},
                   content_type="multipart/form-data", follow_redirects=True)
    run("UPDATE users SET upload_max_mb = NULL WHERE id = 1")
    kid = one("SELECT id FROM loans WHERE item_name = 'Kask'")["id"]
    assert "dosya yükleme kapalı" in r.get_data(as_text=True)
    assert one("SELECT 1 AS x FROM attachments WHERE entity = 'loan' AND entity_id = ?", (kid,)) is None
    print("  photo/quota OK")


def test_trash():
    lid = one("SELECT id FROM loans WHERE item_name = 'Bisiklet'")["id"]
    att_ids = [r["id"] for r in rows("SELECT id FROM attachments WHERE entity = 'loan' AND entity_id = ?", (lid,))]
    assert len(att_ids) == 2
    # Başkası silemez
    assert AYSE_C.post(f"/odunc/{lid}/sil").status_code == 404 and loan(lid)
    r = ADMIN.post(f"/odunc/{lid}/sil", follow_redirects=True)
    assert "çöp kutusuna taşındı" in r.get_data(as_text=True)
    assert loan(lid) is None and not rows("SELECT id FROM attachments WHERE entity = 'loan' AND entity_id = ?", (lid,))
    item = one("SELECT * FROM trash WHERE module = 'loans' ORDER BY id DESC LIMIT 1")
    assert item["label"] == "🔁 Bisiklet (Kerem)" and item["user_id"] == 1
    assert "Bisiklet" in ADMIN.text("/cop-kutusu/")
    AYSE_C.post(f"/cop-kutusu/{item['id']}/geri")
    assert loan(lid) is None
    ADMIN.post(f"/cop-kutusu/{item['id']}/geri")
    assert loan(lid)["item_name"] == "Bisiklet"
    assert [r["id"] for r in rows("SELECT id FROM attachments WHERE entity = 'loan' AND entity_id = ? ORDER BY id",
                                  (lid,))] == att_ids
    assert "Bisiklet" in section(ADMIN, "/odunc/")
    print("  trash OK")


def test_isolation():
    lid = one("SELECT id FROM loans WHERE item_name = 'Bisiklet'")["id"]
    before = dict(loan(lid))
    assert AYSE_C.get(f"/odunc/{lid}").status_code == 404
    for path, data in (("", {"item_name": "Çalındı", "person_name": "X"}), ("/geri", {}), ("/ac", {}), ("/sil", {})):
        assert AYSE_C.post(f"/odunc/{lid}{path}", data=data).status_code == 404, path
    assert dict(loan(lid)) == before
    html = AYSE_C.text("/odunc/")
    assert "Bisiklet" not in html and "Kerem" not in html and "Kendi matkabım" in html
    assert "Bisiklet" not in AYSE_C.text("/ara/?q=bisiklet")
    assert ADMIN.get("/odunc/999999").status_code == 404
    print("  isolation OK")


def test_integrations():
    reset()
    add(ADMIN, "Matkap", "Ahmet", given_on=D(-21), due_on=D(2), note="Bosch, mavi çanta")
    add(ADMIN, "Şemsiye", "Oğuz", given_on=D(-10), due_on=D(-3))
    add(ADMIN, "Kitap", "Ayşe", direction="borrowed", given_on=D(-5), due_on=D(1))
    add(ADMIN, "Uzak eşya", "Can", due_on=D(20))
    gone = add(ADMIN, "Dönen eşya", "Ece", given_on=D(-5), due_on=D(1))
    ADMIN.post(f"/odunc/{gone}/geri", follow_redirects=True)  # bildirim burada okunur
    add(AYSE_C, "Ayşenin çadırı", "Ali", due_on=D(1))

    # Pano (yaklaşanlar): gecikenler ve 7 gün içindekiler; geri gelen ve başkasınınki yok
    home = ADMIN.text("/")
    assert "Matkap Ahmet&#39;ten geri alınacak" in home and "Şemsiye Oğuz&#39;dan geri alınacak" in home
    assert "Kitap Ayşe&#39;ye geri verilecek" in home
    assert "Uzak eşya" not in home and "Dönen eşya" not in home and "Ayşenin çadırı" not in home
    assert "Ayşenin çadırı" in AYSE_C.text("/") and "Matkap" not in AYSE_C.text("/")

    # Takvim: dışarıdaki eşyaların beklenen dönüş günü
    with app.test_request_context():
        evs = [e for e in events_between(1, T - timedelta(days=10), T + timedelta(days=30)) if e["kind"] == "loan"]
    assert {e["title"] for e in evs} == {"Matkap Ahmet'ten geri alınacak", "Şemsiye Oğuz'dan geri alınacak",
                                         "Kitap Ayşe'ye geri verilecek", "Uzak eşya Can'dan geri alınacak"}
    assert all(e["url"].startswith("/odunc/") and e["icon"] == "🔁" for e in evs)
    assert "Uzak eşya" in ADMIN.text(f"/takvim/?ay={D(20)[:7]}")
    assert "Ayşenin çadırı" not in ADMIN.text(f"/takvim/?ay={D(1)[:7]}")

    # Arama: eşya, kişi, not; Türkçe harf duyarsız; geri gelen ✓
    assert "Matkap" in ADMIN.text("/ara/?q=bosch") and "Şemsiye" in ADMIN.text("/ara/?q=semsiye")
    assert "Matkap" in ADMIN.text("/ara/?q=ahmet") and "3 haftadır Ahmet&#39;te" in ADMIN.text("/ara/?q=ahmet")
    assert "Dönen eşya ✓" in ADMIN.text("/ara/?q=donen")
    assert "Ayşenin çadırı" not in ADMIN.text("/ara/?q=cadir") and "Matkap" not in AYSE_C.text("/ara/?q=matkap")

    # Günlük özet: yaklaşanlarda (3 gün içi ve gecikenler)
    run("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
    n = len(sent())
    cron("gunluk")
    daily = dict((c, t) for c, t, _m in sent()[n:])["100"]
    assert "Matkap Ahmet'ten geri alınacak" in daily and "Şemsiye Oğuz'dan geri alınacak" in daily
    assert "Uzak eşya" not in daily
    print("  integrations OK")


def test_telegram_reminders():
    reset()
    run("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
    run("UPDATE users SET telegram_chat_id = '200' WHERE id = ?", (AYSE,))
    run("INSERT OR REPLACE INTO app_state (key, value) VALUES ('telegram_webhook', 'https://x/telegram/webhook')")
    a = add(ADMIN, "Matkap", "Ahmet", given_on=D(-21), due_on=D(0), phone="0532 123 45 67", note="Uçlarıyla")
    add(ADMIN, "Merdiven", "Ali", given_on=D(-5), due_on=D(3))
    c = add(ADMIN, "Kitap", "Can", given_on=D(-20), remind_every_days=14)
    add(ADMIN, "Tencere", "Ece", given_on=D(-3), remind_every_days=7)
    add(ADMIN, "Şemsiye", "Oğuz", given_on=D(-40), remind_every_days=0)       # hatırlatma yok
    f = add(ADMIN, "Kamp sandalyesi", "Ayşe", direction="borrowed", given_on=D(-10), due_on=D(-2))
    g = add(ADMIN, "Dönen eşya", "Ece", given_on=D(-5), due_on=D(-1))
    ADMIN.post(f"/odunc/{g}/geri", follow_redirects=True)
    h = add(AYSE_C, "Ayşenin çadırı", "Ali", due_on=D(0))

    def names(n):
        return [m[1] for m in sent()[n:]]

    # 09:00'dan önce gönderilmez
    NOW[0] = at(0, 8, 30)
    assert cron()["loans_sent"] == 0
    NOW[0] = at(0, 9, 5)
    n = len(sent())
    assert cron()["loans_sent"] == 4
    msgs = {m[1].split("\n")[0]: m for m in sent()[n:]}
    chat, text, markup = next(m for m in sent()[n:] if "Matkap" in m[1])
    assert chat == "100" and text.startswith("🔁 <b>Matkap</b> 3 haftadır Ahmet'te")
    assert "Dönüş:" in text and "(bugün)" in text and "<i>Uçlarıyla</i>" in text and f"/odunc/{a}" in text
    assert 'href="https://wa.me/905321234567?text=' in text and "WhatsApp'tan nazikçe hatırlat" in text
    assert buttons(markup) == [f"ln:ret:{a}", f"ln:snz:{a}"] and button_texts(markup) == ["✅ Geri geldi", "⏰ 1 hafta sonra"]
    _chat, text, markup = next(m for m in sent()[n:] if "Kamp" in m[1])
    assert text.startswith("🔁 Ayşe'den aldığın <b>Kamp sandalyesi</b> — geri vermeyi unutma")
    assert "(2 gün geçti)" in text and "10 gündür sende" in text and "wa.me" not in text
    assert buttons(markup) == [f"ln:ret:{f}", f"ln:snz:{f}"] and button_texts(markup)[0] == "✅ Geri verdim"
    assert any(t.startswith("🔁 <b>Kitap</b> 2 haftadır Can'da") for t in names(n))
    chat, text, _markup = next(m for m in sent()[n:] if "Ayşenin çadırı" in m[1])
    assert chat == "200" and f"/odunc/{h}" in text
    assert not any(x in t for t in names(n) for x in ("Merdiven", "Tencere", "Şemsiye", "Dönen"))
    assert len(msgs) == 4
    # Aynı gün iki kez gitmez
    assert cron()["loans_sent"] == 0
    # Sonraki günler: dönüş günü gelince; dönüş tarihi yoksa aralıkla; gecikmişse haftada bir
    expected = {1: [], 2: [], 3: ["Merdiven"], 4: ["Tencere"], 5: [], 6: [],
                7: ["Matkap", "Kamp sandalyesi", "Ayşenin çadırı"], 8: [], 10: ["Merdiven"], 11: ["Tencere"],
                13: [], 14: ["Matkap", "Kitap", "Kamp sandalyesi", "Ayşenin çadırı"]}
    for day, items in expected.items():
        NOW[0] = at(day, 9, 30)
        n = len(sent())
        assert cron()["loans_sent"] == len(items), (day, names(n))
        assert sorted(next(i for i in ("Matkap", "Merdiven", "Kitap", "Tencere", "Kamp sandalyesi", "Ayşenin çadırı")
                           if i in t) for t in names(n)) == sorted(items), (day, names(n))
        assert cron()["loans_sent"] == 0
        if day == 7:
            assert any("Matkap" in t and "(7 gün geçti)" in t for t in names(n))
    # Dönüş tarihi değişince yeni tarihe göre (erteleme de düşer)
    ADMIN.post(f"/odunc/{c}", data={"item_name": "Kitap", "person_name": "Can", "direction": "lent",
                                    "given_on": D(-20), "due_on": D(16), "remind_every_days": "14"})
    NOW[0] = at(15, 9, 30)
    assert cron()["loans_sent"] == 0
    NOW[0] = at(16, 9, 30)
    n = len(sent())
    assert cron()["loans_sent"] == 1 and "Kitap" in names(n)[0] and "(bugün)" in names(n)[0]
    # Webhook yoksa buton yok; Telegram'ı bağlı olmayana gitmez (Matkap, Kamp: 14+7; Merdiven: 10+7; Tencere: 11+7)
    run("DELETE FROM app_state WHERE key = 'telegram_webhook'")
    run("UPDATE users SET telegram_chat_id = NULL WHERE id = ?", (AYSE,))
    NOW[0] = at(21, 9, 30)
    n = len(sent())
    assert cron()["loans_sent"] == 4 and all(m[2] is None for m in sent()[n:]), names(n)
    assert not any("Ayşenin" in t or "<b>Kitap" in t for t in names(n))
    run("INSERT INTO app_state (key, value) VALUES ('telegram_webhook', 'https://x/telegram/webhook')")
    run("UPDATE users SET telegram_chat_id = '200' WHERE id = ?", (AYSE,))
    NOW[0] = at(0)
    print("  telegram reminders OK")


def test_telegram_buttons():
    reset()
    run("INSERT OR REPLACE INTO app_state (key, value) VALUES ('telegram_webhook', 'https://x/telegram/webhook')")
    a = add(ADMIN, "Matkap", "Ahmet", given_on=D(-21), due_on=D(-1))
    b = add(ADMIN, "Kitap", "Can", given_on=D(-31), remind_every_days=30)
    f = add(ADMIN, "Kamp sandalyesi", "Ayşe", direction="borrowed", given_on=D(-10))
    x = add(AYSE_C, "Ayşenin çadırı", "Ali", due_on=D(0))

    # ✅ Geri geldi: bugünle kapatır; çift dokunuşta değişmez
    press(f"ln:ret:{a}")
    assert loan(a)["returned_on"] == T.isoformat()
    assert calls("answerCallbackQuery")[-1]["text"] == "✅ Kaydedildi"
    edit = calls("editMessageText")[-1]
    assert "Matkap Ahmet'ten geri geldi" in edit["text"] and f"/odunc/{a}" in edit["text"]
    assert edit["message_id"] == 7 and buttons(edit["reply_markup"]) == []
    press(f"ln:ret:{a}")
    assert "Zaten" in calls("answerCallbackQuery")[-1]["text"] and loan(a)["returned_on"] == T.isoformat()
    press(f"ln:snz:{a}")  # geri gelmiş kayıt ertelenmez
    assert loan(a)["snooze_until"] is None
    press(f"ln:ret:{f}")
    assert loan(f)["returned_on"] == T.isoformat() and "Kamp sandalyesi Ayşe'ye geri verildi" in calls("editMessageText")[-1]["text"]

    # ⏰ 1 hafta sonra: 30 günlük aralık beklenmez, mesaj 7 gün sonra gelir; sonra yine aralıkla
    NOW[0] = at(0, 9, 30)
    n = len(sent())
    cron()
    assert [m[1].split("</b>")[0] for m in sent()[n:] if m[0] == "100"] == ["🔁 <b>Kitap"]
    press(f"ln:snz:{b}")
    assert loan(b)["snooze_until"] == D(7)
    assert calls("answerCallbackQuery")[-1]["text"] == "⏰ 1 hafta sonra hatırlatırım"
    assert "yeniden hatırlatırım" in calls("editMessageText")[-1]["text"]
    for day, expect in ((6, 0), (7, 1), (8, 0), (36, 0), (37, 1)):
        NOW[0] = at(day, 9, 30)
        n = len(sent())
        cron()
        assert sum(1 for m in sent()[n:] if "Kitap" in m[1]) == expect, day
    assert loan(b)["snooze_until"] is None
    NOW[0] = at(0)

    # Başkasının kaydına dokunulamaz
    press(f"ln:ret:{b}", chat=200)
    assert calls("answerCallbackQuery")[-1]["text"] == "Kayıt bulunamadı, silinmiş olabilir."
    press(f"ln:snz:{b}", chat=200)
    press(f"ln:ret:{x}", chat=100)
    press(f"ln:snz:{x}", chat=100)
    assert loan(b)["returned_on"] is None and loan(b)["snooze_until"] is None
    assert loan(x)["returned_on"] is None and loan(x)["snooze_until"] is None
    # Bozuk veri ve bağlı olmayan sohbet
    for data in ("ln:xyz:1", "ln:ret:abc", "ln:ret", "ln", f"ln:ret:{'9' * 40}"):
        press(data)
    press(f"ln:ret:{b}", chat=999)
    assert calls("answerCallbackQuery")[-1]["text"] == "Bu sohbet panoya bağlı değil."
    assert loan(b)["returned_on"] is None
    # Ayşe kendi kaydını kapatabilir
    press(f"ln:ret:{x}", chat=200)
    assert loan(x)["returned_on"] == T.isoformat()
    print("  telegram buttons OK")


def test_bot_command():
    reset()
    run("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
    run("UPDATE users SET telegram_chat_id = '200' WHERE id = ?", (AYSE,))
    assert "Ödünçte eşya yok" in say("/odunc")
    ADMIN.post("/envanter/yeni", data={"name": "Matkap", "location": "Garaj"})
    inv = one("SELECT id FROM inventory WHERE name = 'Matkap' AND user_id = 1")["id"]
    ADMIN.post("/kisiler/yeni", data={"name": "Ahmet Yılmaz", "phone": "0532 123 45 67"})
    cid = one("SELECT id FROM contacts WHERE name = 'Ahmet Yılmaz'")["id"]
    # /odunc matkap Ahmet: son kelime kişi; Kişiler'de ilk adla tek eşleşme ve envanterde aynı ad -> bağlanır
    reply = say("/odunc MATKAP ahmet")
    lid = one("SELECT MAX(id) AS m FROM loans")["m"]
    r = loan(lid)
    assert (r["user_id"], r["direction"], r["item_name"], r["inventory_id"], r["person_name"], r["contact_id"],
            r["phone"], r["given_on"], r["due_on"], r["remind_every_days"]) \
        == (1, "lent", "Matkap", inv, "Ahmet Yılmaz", cid, "0532 123 45 67", T.isoformat(), None, 14)
    assert "<b>Matkap</b> Ahmet Yılmaz'a verildi" in reply and "Ev Envanteri" in reply and f"/odunc/{lid}" in reply
    assert "2 haftada bir hatırlatırım" in reply
    # Ek yazılırsa atılır; eşleşme yoksa serbest metin
    say("/odunc kamp sandalyesi Mehmet'e")
    r = loan(one("SELECT MAX(id) AS m FROM loans")["m"])
    assert (r["item_name"], r["person_name"], r["inventory_id"], r["contact_id"], r["phone"]) \
        == ("kamp sandalyesi", "Mehmet", None, None, "")
    # İlk ad birden çok kişide varsa bağlanmaz
    ADMIN.post("/kisiler/yeni", data={"name": "Ali Kaya"})
    ADMIN.post("/kisiler/yeni", data={"name": "Ali Veli"})
    say("/odunc top Ali")
    r = loan(one("SELECT MAX(id) AS m FROM loans")["m"])
    assert (r["person_name"], r["contact_id"]) == ("Ali", None)
    # Eksik: tek kelime -> örnek, kayıt yok
    before = count()
    assert "Örnek" in say("/odunc matkap") and count() == before
    # Liste: verdiklerim ve aldıklarım, kimde ve kaç gündür, gecikme
    add(ADMIN, "Kitap", "Ayşe", direction="borrowed", given_on=D(-3), due_on=D(-1))
    text = say("/odunc")
    assert "Verdiklerin</b> (3)" in text and "Aldıkların</b> (1)" in text
    assert "bugünden beri Mehmet'te" in text and "Ayşe'den · 3 gündür sende" in text and "⚠️ dönüş dün" in text
    assert f"/odunc/{lid}" in text and text.index("Verdiklerin") < text.index("Aldıkların")
    # Başkasının listesi ayrı
    assert "Ödünçte eşya yok" in say("/odunc", chat=200)
    say("/odunc tencere Ali", chat=200)
    r = loan(one("SELECT MAX(id) AS m FROM loans")["m"])
    assert r["user_id"] == AYSE and r["contact_id"] is None  # admin'in kişileriyle eşleşmez
    assert "Matkap" not in say("/odunc", chat=200)
    # Yardım ve komut listesi
    assert "/odunc matkap Ahmet" in say("/yardim")
    import pano.bot_commands as bc
    assert "odunc" in dict(bc.COMMANDS)
    print("  bot command OK")


if __name__ == "__main__":
    test_text()
    test_crud()
    test_return_and_history()
    test_whatsapp()
    test_inventory_link()
    test_contact_link()
    test_photo_and_quota()
    test_trash()
    test_isolation()
    test_integrations()
    test_telegram_reminders()
    test_telegram_buttons()
    test_bot_command()
    print("OK")
