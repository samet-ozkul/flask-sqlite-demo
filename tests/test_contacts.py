"""Kişiler: iletişim hatırlatıcı — vade, hızlı kayıt, doğum günü, Telegram dürtmesi ve butonları.

Telegram ve saat taklit edilir; dış servis çağrılmaz.
Çalıştır: .venv/Scripts/python tests/test_contacts.py
"""
import json
import os
import sys
from datetime import date, datetime, time, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

app = make_app()
os.environ["CRON_SECRET"] = "gizli"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.external as ext  # noqa: E402
import pano.telegram as tg  # noqa: E402
import pano.todo_reminders as todo  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query, query_one  # noqa: E402
from pano.modules import contacts as ct  # noqa: E402
from pano.utils import TZ, today  # noqa: E402

CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {})) or {"message_id": 1}
ext.weather = lambda lat, lon: None
ext.rates = lambda: None
T = today()
NOW = [datetime.combine(T, time(10, 0), tzinfo=TZ)]
todo.now_local = lambda: NOW[0]

with app.app_context():
    AYSE = create_user("ayse", "ayse12345")
    execute("UPDATE users SET telegram_chat_id = '100' WHERE id = 1")
    execute("UPDATE users SET telegram_chat_id = '200' WHERE id = ?", (AYSE,))
with app.test_request_context():
    SECRET = tg.webhook_secret()
ADMIN = Client(app)
AYSE_C = Client(app, "ayse", "ayse12345")
RAW = app.test_client()


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def rows(sql, args=()):
    with app.app_context():
        return query(sql, args)


def run(sql, args=()):
    with app.app_context():
        execute(sql, args)


def cid(name, user_id=1):
    return one("SELECT id FROM contacts WHERE name = ? AND user_id = ?", (name, user_id))["id"]


def contact(contact_id):
    return one("SELECT * FROM contacts WHERE id = ?", (contact_id,))


def ago(n):
    return (T - timedelta(days=n)).isoformat()


def add(client, name, **fields):
    client.post("/kisiler/yeni", data={"name": name, **fields})
    return cid(name, AYSE if client is AYSE_C else 1)


def calls(method):
    return [p for m, p in CALLS if m == method]


def press(data, chat=100):
    RAW.post("/telegram/webhook", data=json.dumps({"callback_query": {
        "id": "cb", "data": data, "from": {"id": chat}, "message": {"message_id": 7, "chat": {"id": chat}}}}),
        content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})


def buttons(params):
    markup = json.loads(params.get("reply_markup") or "{}")
    return [b["callback_data"] for row in markup.get("inline_keyboard", []) for b in row]


def cron(path="hatirlatma"):
    r = RAW.get(f"/cron/gizli/{path}")
    assert r.status_code == 200, r.status_code
    return r.json


def contact_list(client, url="/kisiler/"):
    """Sayfadaki kişi satırları (bildirim ve formlar hariç)."""
    page = client.text(url)
    return page.split('class="rows contact-rows"', 1)[1] if 'class="rows contact-rows"' in page else ""


def test_crud():
    assert "Henüz kişi yok" in ADMIN.text("/kisiler/")
    dede = add(ADMIN, "Dede", relation="Aile", phone="0532 123 45 67", email="dede@example.com",
               birthday="1946-03-12", contact_every="14", address="Meram, Konya", note="Kulağı az duyuyor")
    c = contact(dede)
    assert (c["relation"], c["phone"], c["birthday"], c["contact_every"]) == ("Aile", "0532 123 45 67", "1946-03-12", 14)
    assert c["last_contact_at"] is None and c["user_id"] == 1
    # Geçersiz sıklık -> hatırlatma yok; yılı bilinmeyen doğum günü '--MM-DD'
    ali = add(ADMIN, "Komşu Ali", relation="komşu", contact_every="999", birthday="2000-07-04", no_year="1")
    assert contact(ali)["contact_every"] is None and contact(ali)["birthday"] == "--07-04"
    # Reddedilenler: ad yok, e-posta bozuk, doğum tarihi ileride, son görüşme ileride
    before = one("SELECT COUNT(*) AS n FROM contacts")["n"]
    ADMIN.post("/kisiler/yeni", data={"name": "", "phone": "123"})
    ADMIN.post("/kisiler/yeni", data={"name": "Bozuk", "email": "abc"})
    ADMIN.post("/kisiler/yeni", data={"name": "Gelecek", "birthday": (T + timedelta(days=3)).isoformat()})
    ADMIN.post("/kisiler/yeni", data={"name": "Gelecek2", "last_contact_at": (T + timedelta(days=1)).isoformat()})
    assert one("SELECT COUNT(*) AS n FROM contacts")["n"] == before
    # "Son görüşme" verilirse ilk kayıt olur, vade ondan sayılır
    can = add(ADMIN, "Can", relation="Arkadaş", contact_every="30", last_contact_at=ago(10))
    assert contact(can)["last_contact_at"] == ago(10)
    assert one("SELECT kind FROM contact_logs WHERE contact_id = ?", (can,))["kind"] == "other"

    page = ADMIN.text(f"/kisiler/{dede}")
    assert "Kulağı az duyuyor" in page and "12 Mart 1946" in page and "Meram, Konya" in page and "2 haftada bir" in page
    # Düzenleme (yıl bilinmiyor işaretlenince yıl düşer; düzenleme formunda 2000 yılıyla gösterilir)
    ADMIN.post(f"/kisiler/{dede}", data={"name": "Dedem", "relation": "Aile", "phone": "0532 123 45 67",
                                         "email": "dede@example.com", "birthday": "1946-03-12", "no_year": "1",
                                         "contact_every": "7", "note": "Kulağı az duyuyor"})
    c = contact(dede)
    assert (c["name"], c["birthday"], c["contact_every"], c["address"]) == ("Dedem", "--03-12", 7, "")
    assert 'value="2000-03-12"' in ADMIN.text(f"/kisiler/{dede}")
    ADMIN.post(f"/kisiler/{dede}", data={"name": "", "contact_every": "7"})  # ad boşsa değişmez
    assert contact(dede)["name"] == "Dedem"
    ADMIN.post(f"/kisiler/{dede}", data={"name": "Dede", "relation": "Aile", "phone": "0532 123 45 67",
                                         "email": "dede@example.com", "birthday": "1946-03-12", "contact_every": "14",
                                         "note": "Kulağı az duyuyor"})
    assert contact(dede)["birthday"] == "1946-03-12"

    # Erişim: başkasının kişisi görünmez, düzenlenemez, kaydı eklenemez/silinemez
    annem = add(AYSE_C, "Annem", relation="Aile", contact_every="7")
    log = one("SELECT id FROM contact_logs WHERE contact_id = ?", (can,))["id"]
    assert "Dede" not in contact_list(AYSE_C) and "Annem" in contact_list(AYSE_C)
    assert "Annem" not in contact_list(ADMIN)
    assert AYSE_C.get(f"/kisiler/{dede}").status_code == 404
    assert AYSE_C.post(f"/kisiler/{dede}", data={"name": "X"}).status_code == 404
    assert AYSE_C.post(f"/kisiler/{dede}/kayit", data={"kind": "call"}).status_code == 404
    assert AYSE_C.post(f"/kisiler/{can}/kayit/{log}/sil").status_code == 404
    assert AYSE_C.post(f"/kisiler/{annem}/kayit/{log}/sil").status_code == 404  # başka kişinin kaydı
    assert AYSE_C.post(f"/kisiler/{dede}/sil").status_code == 404
    assert contact(dede)["name"] == "Dede" and one("SELECT 1 FROM contact_logs WHERE id = ?", (log,))
    print("  crud OK")


def test_status_and_order():
    # Saf hesap: created_at UTC'dir; 22:30 UTC İstanbul'da ertesi gün
    row = {"name": "X", "last_contact_at": None, "contact_every": 7, "created_at": "2026-09-30 22:30:00"}
    st = ct.status(row, date(2026, 10, 8))
    assert (st["due"], st["left"], st["text"], st["cls"]) == (date(2026, 10, 8), 0, "hiç kaydedilmedi", "overdue")
    st = ct.status(row, date(2026, 10, 5))
    assert (st["left"], st["text"], st["cls"]) == (3, "3 gün kaldı", "soon")
    assert ct.status(row, date(2026, 10, 7))["text"] == "yarın"
    assert ct.status({**row, "contact_every": None}, date(2026, 10, 5))["text"] == "hiç kaydedilmedi"
    st = ct.status({**row, "last_contact_at": "2026-09-01", "contact_every": 14}, date(2026, 9, 24))
    assert (st["since"], st["left"], st["text"]) == (23, -9, "23 gündür görüşülmedi")
    assert ct.status({**row, "last_contact_at": "2026-09-20", "contact_every": None}, date(2026, 9, 24))["text"] \
        == "4 gün önce görüşüldü"
    assert ct.status({**row, "last_contact_at": "2026-09-30", "contact_every": 30}, date(2026, 10, 1))["text"] \
        == "29 gün kaldı"

    # Sayfa: en çok gecikmiş en üstte, sıklığı olmayanlar sonda
    dede, can, ali = cid("Dede"), cid("Can"), cid("Komşu Ali")
    ADMIN.post(f"/kisiler/{dede}/kayit", data={"kind": "call", "date": ago(23)})          # 14 günde bir: -9
    teyze = add(ADMIN, "Ayşe teyze", relation="Aile", contact_every="7", last_contact_at=ago(12))  # -5
    eski = add(ADMIN, "Eski dost", relation="Arkadaş", contact_every="7")                 # hiç: -3
    run("UPDATE contacts SET created_at = datetime('now', '-10 days') WHERE id = ?", (eski,))
    run("UPDATE contact_logs SET date = ? WHERE contact_id = ?", (ago(27), can))
    run("UPDATE contacts SET last_contact_at = ? WHERE id = ?", (ago(27), can))           # 30 günde bir: 3 kaldı
    html = contact_list(ADMIN)
    order = [html.index(f"/kisiler/{i}\"") for i in (dede, teyze, eski, can, ali)]
    assert order == sorted(order), order
    assert "23 gündür görüşülmedi" in html and "12 gündür görüşülmedi" in html and "3 gün kaldı" in html
    assert html.count("hiç kaydedilmedi") == 2 and 'badge overdue' in html and 'badge soon' in html
    assert "3 kişiyle görüşme vakti geldi" in ADMIN.text("/kisiler/")
    # İlişki sekmeleri ("komşu" yazılmış olsa da sekme var); filtre
    page = ADMIN.text("/kisiler/")
    assert "?iliski=Aile" in page and "?iliski=Arkada" in page and "?iliski=kom" in page
    aile = contact_list(ADMIN, "/kisiler/?iliski=aile")
    assert "Dede" in aile and "Ayşe teyze" in aile and "Can" not in aile and "Komşu Ali" not in aile
    assert "Komşu Ali" in contact_list(ADMIN, "/kisiler/?iliski=Kom%C5%9Fu")
    assert "Can" in contact_list(ADMIN, "/kisiler/?iliski=yok")  # bilinmeyen ilişki -> tümü
    print("  status/order OK")


def test_quick_logs():
    can = cid("Can")
    # Hızlı butonlar: bugün kaydı, son görüşme bugün, listeye geri döner
    r = ADMIN.post(f"/kisiler/{can}/kayit", data={"kind": "call", "next": "/kisiler/?iliski=arkadas"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/kisiler/?iliski=arkadas")
    assert contact(can)["last_contact_at"] == T.isoformat()
    for kind in ("message", "visit", "bogus"):
        ADMIN.post(f"/kisiler/{can}/kayit", data={"kind": kind})
    kinds = [r["kind"] for r in rows("SELECT kind FROM contact_logs WHERE contact_id = ? AND date = ? ORDER BY id",
                                     (can, T.isoformat()))]
    assert kinds == ["call", "message", "visit", "call"]  # bilinmeyen tür -> arama
    assert "30 gün kaldı" in contact_list(ADMIN)
    # Geçmişe dönük kayıt son görüşmeyi geri almaz; ileri tarih reddedilir
    ADMIN.post(f"/kisiler/{can}/kayit", data={"kind": "visit", "date": ago(1), "note": "Kahve içtik"})
    assert contact(can)["last_contact_at"] == T.isoformat()
    ADMIN.post(f"/kisiler/{can}/kayit", data={"kind": "call", "date": (T + timedelta(days=2)).isoformat()})
    assert not one("SELECT 1 FROM contact_logs WHERE contact_id = ? AND date > ?", (can, T.isoformat()))
    page = ADMIN.text(f"/kisiler/{can}")
    assert "Kahve içtik" in page and "dün" in page and "Yüz yüze" in page
    # Bugünkü kayıtlar silinince son görüşme dün olur
    for r in rows("SELECT id FROM contact_logs WHERE contact_id = ? AND date = ?", (can, T.isoformat())):
        ADMIN.post(f"/kisiler/{can}/kayit/{r['id']}/sil")
    assert contact(can)["last_contact_at"] == ago(1)
    print("  quick logs OK")


def test_links():
    for raw, expected in [("0532 123 45 67", "905321234567"), ("+90 (532) 123-45-67", "905321234567"),
                          ("905321234567", "905321234567"), ("532 123 45 67", "905321234567"),
                          ("0049 151 2345678", "491512345678"), ("+44 20 7946 0958", "442079460958"),
                          ("0332 350 00 00", "903323500000"), ("12", None), ("", None)]:
        assert ct.whatsapp_number(raw) == expected, (raw, ct.whatsapp_number(raw))
    assert ct.tel_number("+90 (532) 123-45-67") == "+905321234567" and ct.tel_number("0532 123 45 67") == "05321234567"
    html = contact_list(ADMIN)
    assert 'href="https://wa.me/905321234567"' in html and 'href="tel:05321234567"' in html
    assert 'href="mailto:dede@example.com"' in html
    page = ADMIN.text(f"/kisiler/{cid('Dede')}")
    assert "wa.me/905321234567" in page and "📞 Aradım" in page and "💬 Mesajlaştık" in page and "☕ Görüştük" in page
    # Telefonu olmayan kişide bağlantı yok
    assert "wa.me" not in ADMIN.text(f"/kisiler/{cid('Komşu Ali')}")
    print("  links OK")


def test_birthdays():
    from pano.calendar_events import events_between
    from pano.reminders import upcoming
    soon = T + timedelta(days=5)
    nene = add(ADMIN, "Nene", relation="Aile", birthday=f"1944-{soon.month:02d}-{soon.day:02d}")
    far = T + timedelta(days=40)
    add(ADMIN, "Uzak", birthday=f"1992-{far.month:02d}-{far.day:02d}")  # 1992 artık yıl: 29 Şubat da olur
    add(ADMIN, "Yılsız", birthday=f"2000-{soon.month:02d}-{soon.day:02d}", no_year="1")
    age = soon.year - 1944
    with app.test_request_context():
        items = upcoming(1)
    titles = [i["title"] for i in items]
    assert f"Nene doğum günü ({age}. yaş)" in titles and "Yılsız doğum günü" in titles
    assert not any(t.startswith("Uzak") for t in titles)
    item = next(i for i in items if i["title"].startswith("Nene"))
    assert item["icon"] == "🎂" and item["date"] == soon.isoformat() and item["url"] == f"/kisiler/{nene}"
    ADMIN.text("/kisiler/")  # "eklendi" bildirimi panoyu yanıltmasın
    assert f"Nene doğum günü ({age}. yaş)" in ADMIN.text("/")
    assert f"Nene doğum günü ({age}. yaş)" in ADMIN.text(f"/takvim/?ay={soon.strftime('%Y-%m')}")
    assert "Nene doğum günü" not in AYSE_C.text("/")
    with app.test_request_context():
        assert not any(i["title"].startswith("Nene") for i in upcoming(AYSE))

    # 29 Şubat: artık yıl olmayan yılda 28 Şubat
    leyla = add(ADMIN, "Leyla", birthday="2000-02-29")
    row = contact(leyla)
    assert ct.birthday_on(row, 2027) == date(2027, 2, 28) and ct.birthday_on(row, 2028) == date(2028, 2, 29)
    assert ct.next_birthday(row, date(2027, 3, 1)) == date(2028, 2, 29)
    assert ct.next_birthday(row, date(2027, 2, 28)) == date(2027, 2, 28)
    assert ct.age_on(row, date(2027, 2, 28)) == 27 and ct.birthday_title(row, date(2028, 2, 29)) == \
        "Leyla doğum günü (28. yaş)"
    assert ct.parse_birthday("--02-29") == (None, 2, 29) and ct.parse_birthday("2001-02-29") is None
    assert ct.parse_birthday("--13-01") is None and ct.parse_birthday("") is None
    with app.test_request_context():
        evs = [e for e in events_between(1, date(2027, 1, 1), date(2028, 12, 31)) if e["title"].startswith("Leyla")]
    assert [(e["date"], e["title"]) for e in evs] == [(date(2027, 2, 28), "Leyla doğum günü (27. yaş)"),
                                                      (date(2028, 2, 29), "Leyla doğum günü (28. yaş)")]
    assert evs[0]["icon"] == "🎂" and evs[0]["uid"] != evs[1]["uid"]
    assert "Leyla doğum günü (27. yaş)" in ADMIN.text("/takvim/?ay=2027-02")
    # Yılı bilinmeyen 29 Şubat da her yıl görünür
    run("UPDATE contacts SET birthday = '--02-29' WHERE id = ?", (leyla,))
    with app.test_request_context():
        evs = [e for e in events_between(1, date(2027, 2, 1), date(2027, 3, 31)) if e["title"].startswith("Leyla")]
    assert [(e["date"], e["title"]) for e in evs] == [(date(2027, 2, 28), "Leyla doğum günü")]
    print("  birthdays OK")


def test_nudge():
    run("DELETE FROM contacts")
    run("DELETE FROM app_state WHERE key = 'contacts_nudge'")
    run("INSERT OR REPLACE INTO app_state (key, value) VALUES ('telegram_webhook', 'https://x/telegram/webhook')")
    dede = add(ADMIN, "Dede", relation="Aile", phone="0532 123 45 67", contact_every="14", last_contact_at=ago(23))
    add(ADMIN, "Can", contact_every="30", last_contact_at=ago(3))     # vadesi gelmedi
    add(ADMIN, "Ali", last_contact_at=ago(300))                         # sıklık yok
    annem = add(AYSE_C, "Annem", contact_every="7")                     # hiç kaydedilmedi
    run("UPDATE contacts SET created_at = datetime('now', '-9 days') WHERE id = ?", (annem,))

    # Varsayılan saatten önce gönderilmez
    NOW[0] = datetime.combine(T, time(8, 30), tzinfo=TZ)
    assert cron()["contacts_nudged"] == 0
    NOW[0] = datetime.combine(T, time(9, 5), tzinfo=TZ)
    before = len(calls("sendMessage"))
    assert cron()["contacts_nudged"] == 2
    new = calls("sendMessage")[before:]
    to_admin = next(p for p in new if p["chat_id"] == "100")
    assert "📞 <b>Dede</b> ile 23 gündür görüşmedin" in to_admin["text"] and "0532 123 45 67" in to_admin["text"]
    assert "2 haftada bir" in to_admin["text"] and f"/kisiler/{dede}" in to_admin["text"]
    assert buttons(to_admin) == [f"ct:{dede}", f"ctz:{dede}"]
    to_ayse = next(p for p in new if p["chat_id"] == "200")
    assert "<b>Annem</b> ile henüz görüşme kaydın yok" in to_ayse["text"] and buttons(to_ayse) == [f"ct:{annem}",
                                                                                                    f"ctz:{annem}"]
    assert contact(dede)["nudged_for"] == (T + timedelta(days=-9)).isoformat()
    # Bir kez: aynı gün tekrar bakılmaz; bakılsa da aynı vade için gönderilmez
    assert cron()["contacts_nudged"] == 0
    run("DELETE FROM app_state WHERE key = 'contacts_nudge'")
    assert cron()["contacts_nudged"] == 0

    # ⏰ Yarın hatırlat: bugün sessiz, yarın yeniden
    press(f"ctz:{dede}")
    c = contact(dede)
    assert c["snooze_until"] == (T + timedelta(days=1)).isoformat() and c["nudged_for"] is None
    assert "yarın yeniden hatırlatırım" in calls("editMessageText")[-1]["text"]
    assert calls("answerCallbackQuery")[-1]["text"].startswith("⏰")
    run("DELETE FROM app_state WHERE key = 'contacts_nudge'")
    assert cron()["contacts_nudged"] == 0
    assert "hatırlatılacak" in contact_list(ADMIN)
    NOW[0] = datetime.combine(T + timedelta(days=1), time(9, 10), tzinfo=TZ)
    assert cron()["contacts_nudged"] == 1 and calls("sendMessage")[-1]["chat_id"] == "100"
    assert "24 gündür" in calls("sendMessage")[-1]["text"]

    # Başkasının kişisine basılamaz
    press(f"ct:{dede}", chat=200)
    assert "bulunamadı" in calls("answerCallbackQuery")[-1]["text"]
    assert contact(dede)["last_contact_at"] == ago(23)
    # ✅ Aradım: bugünlük arama kaydı, son görüşme bugün
    press(f"ct:{dede}")
    c = contact(dede)
    assert c["last_contact_at"] == T.isoformat()
    assert one("SELECT kind FROM contact_logs WHERE contact_id = ? AND date = ?", (dede, T.isoformat()))["kind"] == "call"
    edited = calls("editMessageText")[-1]["text"]
    assert "<b>Dede</b> ile görüşme kaydedildi" in edited and "Sıradaki" in edited
    press("ct:abc")
    assert "bulunamadı" in calls("answerCallbackQuery")[-1]["text"]
    # Ertesi gün: Dede'nin vadesi ileri gitti, Annem bu vade için zaten dürtüldü
    NOW[0] = datetime.combine(T + timedelta(days=2), time(9, 10), tzinfo=TZ)
    assert cron()["contacts_nudged"] == 0
    NOW[0] = datetime.combine(T, time(10, 0), tzinfo=TZ)
    print("  nudge OK")


def test_daily_summary():
    run("DELETE FROM contacts WHERE user_id = 1")
    add(ADMIN, "Dede", contact_every="14", last_contact_at=ago(23))
    add(ADMIN, "Ayşe teyze", contact_every="7", last_contact_at=ago(12))
    add(ADMIN, "Can", contact_every="30", last_contact_at=ago(2))
    ertelenen = add(ADMIN, "Ertelenen", contact_every="7", last_contact_at=ago(40))
    run("UPDATE contacts SET snooze_until = ? WHERE id = ?", ((T + timedelta(days=1)).isoformat(), ertelenen))

    def daily(chat="100"):
        before = len(calls("sendMessage"))
        cron("gunluk?force=1")
        return next(p["text"] for p in calls("sendMessage")[before:] if p["chat_id"] == chat and "Günaydın" in p["text"])

    text = daily()
    assert "<b>📇 Aranacaklar:</b> Dede (23 gün), Ayşe teyze (12 gün)" in text
    assert "Can" not in text and "Ertelenen" not in text
    assert "Aranacaklar" in daily("200")  # Ayşe'nin annesi (kayıt yok)
    assert "Annem (kayıt yok)" in daily("200") and "Dede" not in daily("200")
    for i in range(5):
        add(ADMIN, f"Kişi{i}", contact_every="7", last_contact_at=ago(8 + i))
    line = next(x for x in daily().split("\n") if "Aranacaklar" in x)
    assert line.count("(") == 5 and line.endswith("+2") and line.index("Dede") < line.index("Ayşe teyze")
    run("DELETE FROM contacts WHERE user_id = 1")
    assert "Aranacaklar" not in daily()
    print("  daily summary OK")


def test_search_and_trash():
    dede = add(ADMIN, "Dede", relation="Aile", phone="0532 123 45 67", email="dede@example.com",
               note="Bastonunu unutma")
    ADMIN.post(f"/kisiler/{dede}/kayit", data={"kind": "call", "date": ago(3)})
    ADMIN.post(f"/kisiler/{dede}/kayit", data={"kind": "visit"})
    for q in ("dede", "bastonunu", "05321234567", "0532+123", "example.com"):
        page = ADMIN.text(f"/ara/?q={q}")
        assert "Kişiler" in page and f"/kisiler/{dede}" in page, q
    assert f"/kisiler/{dede}" not in AYSE_C.text("/ara/?q=dede")

    # Silme: kişi ve kayıtları çöp kutusuna; geri gelince aynı id'lerle
    logs = [r["id"] for r in rows("SELECT id FROM contact_logs WHERE contact_id = ? ORDER BY id", (dede,))]
    r = ADMIN.post(f"/kisiler/{dede}/sil")
    assert r.status_code == 302
    assert contact(dede) is None and not rows("SELECT 1 FROM contact_logs WHERE contact_id = ?", (dede,))
    item = one("SELECT * FROM trash WHERE module = 'contacts' ORDER BY id DESC")
    assert item["label"] == "📇 Dede" and "çöp kutusuna taşındı" in ADMIN.text("/kisiler/")
    assert "📇 Dede" not in AYSE_C.text("/cop-kutusu/")
    ADMIN.post(f"/cop-kutusu/{item['id']}/geri")
    assert contact(dede)["name"] == "Dede" and contact(dede)["last_contact_at"] == T.isoformat()
    assert [r["id"] for r in rows("SELECT id FROM contact_logs WHERE contact_id = ? ORDER BY id", (dede,))] == logs
    print("  search/trash OK")


if __name__ == "__main__":
    test_crud()
    test_status_and_order()
    test_quick_logs()
    test_links()
    test_birthdays()
    test_nudge()
    test_daily_summary()
    test_search_and_trash()
    print("OK")
