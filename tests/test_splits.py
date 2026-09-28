"""👥 Ortak Harcama modülü için uçtan uca testler.

Çalıştırma: .venv/Scripts/python tests/test_splits.py
"""
import os
import sys
from contextlib import closing

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import Client, make_app  # noqa: E402

app = make_app()
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.telegram as tg  # noqa: E402
from pano.db import connect  # noqa: E402
from pano.modules import splits  # noqa: E402

CALLS = []
FAIL = [False]


def fake_call(method, params=None, files=None):
    if FAIL[0]:
        raise tg.TelegramError("ağ hatası")
    CALLS.append((method, params or {}))
    return {"message_id": 1}


tg._call = fake_call

with app.app_context():
    from pano.auth import create_user
    AYSE_ID = create_user("ayse", "ayse12345")
    VELI_ID = create_user("veli", "veli12345")
    CAN_ID = create_user("can", "can12345")


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


ADMIN_ID = db_one("SELECT id FROM users WHERE username = 'admin'")["id"]
db_exec("UPDATE users SET display_name = 'Ayşe', telegram_chat_id = '555' WHERE id = ?", (AYSE_ID,))
db_exec("UPDATE users SET telegram_chat_id = '111' WHERE id = ?", (ADMIN_ID,))
db_exec("UPDATE users SET telegram_chat_id = '999' WHERE id = ?", (VELI_ID,))

admin = Client(app)
ayse = Client(app, "ayse", "ayse12345")
veli = Client(app, "veli", "veli12345")
can = Client(app, "can", "can12345")


def loc(resp):
    return resp.headers.get("Location", "")


def member_id(group_id, name):
    return db_one("SELECT id FROM split_members WHERE group_id = ? AND name = ?", (group_id, name))["id"]


def shares_of(expense_id):
    return {r["member_id"]: r["share"] for r in
            db_all("SELECT member_id, share FROM split_shares WHERE expense_id = ?", (expense_id,))}


def last_expense(group_id):
    return db_one("SELECT * FROM split_expenses WHERE group_id = ? ORDER BY id DESC LIMIT 1", (group_id,))


def create_group(client, name):
    r = client.post("/ortak/yeni", {"name": name})
    assert r.status_code == 302, r.status_code
    gid = db_one("SELECT id FROM split_groups WHERE name = ? ORDER BY id DESC LIMIT 1", (name,))["id"]
    assert f"/ortak/{gid}" in loc(r)
    return gid


def balances_by_name(group_id):
    with app.app_context():
        return {b["member"]["name"]: b for b in splits.balances(group_id)}


def plan_of(group_id):
    with app.app_context():
        return [(t["from"]["name"], t["to"]["name"], t["amount"]) for t in splits.settle_plan(group_id)]


STATE = {}


# =====================================================================
def test_split_equal():
    s = splits.split_equal(10000, [3, 1, 2])
    assert s == {1: 3334, 2: 3333, 3: 3333}, s
    assert sum(s.values()) == 10000
    s = splits.split_equal(5, [7, 8, 9])
    assert s == {7: 2, 8: 2, 9: 1} and sum(s.values()) == 5
    s = splits.split_equal(99999, list(range(1, 8)))
    assert sum(s.values()) == 99999 and max(s.values()) - min(s.values()) <= 1
    assert splits.split_equal(100, []) == {}


def test_groups_and_members():
    assert "Henüz grup yok" in admin.text("/ortak/")
    # Boş ad
    r = admin.post("/ortak/yeni", {"name": "  "}, follow_redirects=True)
    assert "Grup adı boş olamaz" in r.get_data(as_text=True)
    assert db_one("SELECT COUNT(*) AS n FROM split_groups")["n"] == 0

    gid = create_group(admin, "Ev")
    STATE["ev"] = gid
    g = db_one("SELECT * FROM split_groups WHERE id = ?", (gid,))
    assert g["owner_id"] == ADMIN_ID
    ms = db_all("SELECT * FROM split_members WHERE group_id = ?", (gid,))
    assert len(ms) == 1 and ms[0]["user_id"] == ADMIN_ID and ms[0]["name"] == "admin"

    # Uygulama kullanıcısı üye (ad = görünen ad)
    r = admin.post(f"/ortak/{gid}/uye", {"username": "ayse"})
    assert r.status_code == 302 and "sekme=uyeler" in loc(r)
    m = db_one("SELECT * FROM split_members WHERE group_id = ? AND user_id = ?", (gid, AYSE_ID))
    assert m and m["name"] == "Ayşe"
    # Hesabı olmayan kişi
    admin.post(f"/ortak/{gid}/uye", {"name": "Ali"})
    m = db_one("SELECT * FROM split_members WHERE group_id = ? AND name = 'Ali'", (gid,))
    assert m and m["user_id"] is None
    # Aynı kullanıcı / aynı ad tekrar eklenemez
    r = admin.post(f"/ortak/{gid}/uye", {"username": "ayse"}, follow_redirects=True)
    assert "zaten bu grupta" in r.get_data(as_text=True)
    r = admin.post(f"/ortak/{gid}/uye", {"name": "ALİ"}, follow_redirects=True)
    assert "zaten var" in r.get_data(as_text=True)
    r = admin.post(f"/ortak/{gid}/uye", {"username": "yokboyle"}, follow_redirects=True)
    assert "Böyle bir kullanıcı yok" in r.get_data(as_text=True)
    r = admin.post(f"/ortak/{gid}/uye", {}, follow_redirects=True)
    assert "adını yaz" in r.get_data(as_text=True)
    # can de üye (ekleyen/sahibi olmayan üye olarak 403 testleri için)
    admin.post(f"/ortak/{gid}/uye", {"username": "can"})
    assert db_one("SELECT COUNT(*) AS n FROM split_members WHERE group_id = ?", (gid,))["n"] == 4

    # Sayfalar
    for client in (admin, ayse, can):
        assert f'href="/ortak/{gid}"' in client.text("/ortak/")
        for tab in ("", "?sekme=harcamalar", "?sekme=bakiyeler", "?sekme=uyeler", "?sekme=yok"):
            client.text(f"/ortak/{gid}{tab}")
    text = admin.text(f"/ortak/{gid}?sekme=uyeler")
    assert "Üye ekle" in text and "Grubu sil" in text and "hesabı yok" in text and "@ayse" in text
    # Sahibi olmayan üye yönetim formlarını görmez
    text = ayse.text(f"/ortak/{gid}?sekme=uyeler")
    assert "Üye ekle" not in text and "Grubu sil" not in text
    # veli (üye değil) listede görmez
    text = veli.text("/ortak/")
    assert f'href="/ortak/{gid}"' not in text and "Henüz grup yok" in text


def test_equal_split_rounding():
    gid = STATE["ev"]
    admin_m, ayse_m, ali_m = member_id(gid, "admin"), member_id(gid, "Ayşe"), member_id(gid, "Ali")
    r = admin.post(f"/ortak/{gid}/harcama", {"payer": admin_m, "amount": "100", "note": "Market",
                                             "members": [admin_m, ayse_m, ali_m]})
    assert r.status_code == 302
    e = last_expense(gid)
    assert e["amount"] == 100 and e["created_by"] == ADMIN_ID and e["note"] == "Market"
    s = shares_of(e["id"])
    assert sorted(s.values()) == [33.33, 33.33, 33.34], s
    assert round(sum(s.values()) * 100) == 10000
    assert set(s) == {admin_m, ayse_m, ali_m}
    STATE["market"] = e["id"]
    text = admin.text(f"/ortak/{gid}")
    assert "Market" in text and "Senin payın" in text and "100 ₺" in text

    # Türkçe binlik ayırıcı: "1.000" -> 1000, iki kişiye
    admin.post(f"/ortak/{gid}/harcama", {"payer": admin_m, "amount": "1.000", "members": [admin_m, ayse_m]})
    e = last_expense(gid)
    assert e["amount"] == 1000 and sorted(shares_of(e["id"]).values()) == [500, 500]
    admin.post(f"/ortak/harcama/{e['id']}/sil")

    # Geçersiz girdiler
    before = db_one("SELECT COUNT(*) AS n FROM split_expenses")["n"]
    r = admin.post(f"/ortak/{gid}/harcama", {"payer": admin_m, "amount": "abc", "members": [admin_m]},
                   follow_redirects=True)
    assert "Tutar" in r.get_data(as_text=True)
    r = admin.post(f"/ortak/{gid}/harcama", {"payer": admin_m, "amount": "50"}, follow_redirects=True)
    assert "en az bir kişi" in r.get_data(as_text=True)
    r = admin.post(f"/ortak/{gid}/harcama", {"payer": 99999, "amount": "50", "members": [admin_m]},
                   follow_redirects=True)
    assert "Ödeyen kişiyi seç" in r.get_data(as_text=True)
    assert db_one("SELECT COUNT(*) AS n FROM split_expenses")["n"] == before


def test_scenario_balances_and_settle():
    gid = create_group(admin, "Tatil 2026")
    STATE["tatil"] = gid
    admin.post(f"/ortak/{gid}/uye", {"username": "ayse"})
    admin.post(f"/ortak/{gid}/uye", {"name": "Cem"})
    a, b, c = member_id(gid, "admin"), member_id(gid, "Ayşe"), member_id(gid, "Cem")
    admin.post(f"/ortak/{gid}/harcama", {"payer": a, "amount": "300", "note": "Otel", "members": [a, b, c]})
    # İkinci uygulama kullanıcısı üye harcama ekleyebilir
    r = ayse.post(f"/ortak/{gid}/harcama", {"payer": b, "amount": "90", "note": "Yemek", "members": [a, b, c]})
    assert r.status_code == 302
    assert last_expense(gid)["created_by"] == AYSE_ID

    bal = balances_by_name(gid)
    assert (bal["admin"]["paid"], bal["admin"]["owed"], bal["admin"]["net"]) == (300, 130, 170)
    assert (bal["Ayşe"]["paid"], bal["Ayşe"]["owed"], bal["Ayşe"]["net"]) == (90, 130, -40)
    assert (bal["Cem"]["paid"], bal["Cem"]["owed"], bal["Cem"]["net"]) == (0, 130, -130)
    assert plan_of(gid) == [("Cem", "admin", 130), ("Ayşe", "admin", 40)], plan_of(gid)

    text = admin.text(f"/ortak/{gid}?sekme=bakiyeler")
    assert "Kim kime ne kadar ödemeli" in text and "✓ Ödendi" in text and "130 ₺" in text
    assert 'name="amount" value="130.00"' in text and 'name="amount" value="40.00"' in text
    # Senin bakiyen (grup sayfası ve liste)
    assert "+170 ₺" in text and "alacaklısın" in text
    idx = ayse.text("/ortak/")
    assert f'href="/ortak/{gid}"' in idx and "−40 ₺" in idx and "borçlusun" in idx

    # Ödeşme: önerilen transfer butonundaki değerlerle
    r = ayse.post(f"/ortak/{gid}/odeme", {"from_id": b, "to_id": a, "amount": "40.00"})
    assert r.status_code == 302 and "sekme=bakiyeler" in loc(r)
    s = db_one("SELECT * FROM split_settlements WHERE group_id = ? ORDER BY id DESC LIMIT 1", (gid,))
    assert (s["from_id"], s["to_id"], s["amount"]) == (b, a, 40) and s["date"]
    STATE["ayse_settlement"] = s["id"]
    assert balances_by_name(gid)["Ayşe"]["net"] == 0
    assert plan_of(gid) == [("Cem", "admin", 130)]
    r = admin.post(f"/ortak/{gid}/odeme", {"from_id": c, "to_id": a, "amount": "130,00"})
    assert r.status_code == 302
    STATE["cem_settlement"] = db_one("SELECT id FROM split_settlements WHERE from_id = ?", (c,))["id"]
    bal = balances_by_name(gid)
    assert all(v["net"] == 0 for v in bal.values()), {k: v["net"] for k, v in bal.items()}
    assert bal["admin"]["received"] == 170 and bal["Cem"]["sent"] == 130
    assert plan_of(gid) == []
    text = admin.text(f"/ortak/{gid}?sekme=bakiyeler")
    assert "Herkes ödeşmiş" in text and "Cem → admin" in text
    assert "hesap denk" in ayse.text("/ortak/")

    # Geçersiz ödeme
    r = admin.post(f"/ortak/{gid}/odeme", {"from_id": a, "to_id": a, "amount": "10"}, follow_redirects=True)
    assert "kendine ödeme" in r.get_data(as_text=True)
    r = admin.post(f"/ortak/{gid}/odeme", {"from_id": a, "to_id": 99999, "amount": "10"}, follow_redirects=True)
    assert "kişiyi seç" in r.get_data(as_text=True)
    r = admin.post(f"/ortak/{gid}/odeme", {"from_id": a, "to_id": b, "amount": "-5"}, follow_redirects=True)
    assert "Tutar" in r.get_data(as_text=True)
    assert db_one("SELECT COUNT(*) AS n FROM split_settlements WHERE group_id = ?", (gid,))["n"] == 2

    # Ödeme silme: taraflardan biri ya da sahibi; Cem->admin ödemesini ayse silemez
    assert ayse.post(f"/ortak/odeme/{STATE['cem_settlement']}/sil").status_code == 403
    r = ayse.post(f"/ortak/odeme/{STATE['ayse_settlement']}/sil")
    assert r.status_code == 302
    assert db_one("SELECT 1 FROM split_settlements WHERE id = ?", (STATE["ayse_settlement"],)) is None
    assert balances_by_name(gid)["Ayşe"]["net"] == -40
    assert plan_of(gid) == [("Ayşe", "admin", 40)]
    ayse.post(f"/ortak/{gid}/odeme", {"from_id": b, "to_id": a, "amount": "40"})
    assert plan_of(gid) == []


def test_edit_delete_expense():
    gid = STATE["ev"]
    admin_m, ayse_m, ali_m = member_id(gid, "admin"), member_id(gid, "Ayşe"), member_id(gid, "Ali")
    ayse.post(f"/ortak/{gid}/harcama", {"payer": ayse_m, "amount": "60", "note": "Su", "members": [ayse_m, admin_m]})
    e = last_expense(gid)
    assert e["created_by"] == AYSE_ID and sorted(shares_of(e["id"]).values()) == [30, 30]

    # Ekleyen ve sahibi düzenleme sayfasını görür; diğer üye 403
    text = ayse.text(f"/ortak/harcama/{e['id']}")
    assert "Harcamayı düzenle" in text and 'value="60"' in text
    admin.text(f"/ortak/harcama/{e['id']}")
    assert can.get(f"/ortak/harcama/{e['id']}").status_code == 403
    assert can.post(f"/ortak/harcama/{e['id']}", {"payer": ayse_m, "amount": "1", "members": [ayse_m]}).status_code == 403
    assert can.post(f"/ortak/harcama/{e['id']}/sil").status_code == 403
    # Sahibi düzenler: 90, üç kişiye, ödeyen admin
    r = admin.post(f"/ortak/harcama/{e['id']}", {"payer": admin_m, "amount": "90", "note": "Su faturası",
                                                   "date": "2026-09-01", "members": [admin_m, ayse_m, ali_m]})
    assert r.status_code == 302
    e2 = db_one("SELECT * FROM split_expenses WHERE id = ?", (e["id"],))
    assert (e2["amount"], e2["payer_id"], e2["note"], e2["date"]) == (90, admin_m, "Su faturası", "2026-09-01")
    assert shares_of(e["id"]) == {admin_m: 30, ayse_m: 30, ali_m: 30}
    # Geçersiz düzenleme değiştirmez
    r = ayse.post(f"/ortak/harcama/{e['id']}", {"payer": admin_m, "amount": "0", "members": [admin_m]})
    assert r.status_code == 302 and db_one("SELECT amount FROM split_expenses WHERE id = ?", (e["id"],))["amount"] == 90
    # Ekleyen siler
    r = ayse.post(f"/ortak/harcama/{e['id']}/sil")
    assert r.status_code == 302
    assert db_one("SELECT 1 FROM split_expenses WHERE id = ?", (e["id"],)) is None
    assert shares_of(e["id"]) == {}


def test_member_removal():
    gid = STATE["ev"]
    ali_m, admin_m = member_id(gid, "Ali"), member_id(gid, "admin")
    # Ali'nin Market harcamasında payı var: çıkarılamaz, nedeni gösterilir
    r = admin.post(f"/ortak/uye/{ali_m}/sil", follow_redirects=True)
    text = r.get_data(as_text=True)
    assert "Ali çıkarılamaz" in text and "harcamada payı var" in text
    assert db_one("SELECT 1 FROM split_members WHERE id = ?", (ali_m,))
    assert "çıkarılamaz" in admin.text(f"/ortak/{gid}?sekme=uyeler")
    # Sahibi çıkarılamaz
    r = admin.post(f"/ortak/uye/{admin_m}/sil", follow_redirects=True)
    assert "Grup sahibi gruptan çıkarılamaz" in r.get_data(as_text=True)
    # Kaydı olmayan üye çıkarılır
    admin.post(f"/ortak/{gid}/uye", {"name": "Deniz"})
    deniz = member_id(gid, "Deniz")
    r = admin.post(f"/ortak/uye/{deniz}/sil")
    assert r.status_code == 302
    assert db_one("SELECT 1 FROM split_members WHERE id = ?", (deniz,)) is None
    # Ödemede adı geçen üye de çıkarılamaz (Tatil: Cem)
    tatil = STATE["tatil"]
    cem = member_id(tatil, "Cem")
    r = admin.post(f"/ortak/uye/{cem}/sil", follow_redirects=True)
    assert "ödemede yer alıyor" in r.get_data(as_text=True)


def test_access_rules():
    gid = STATE["ev"]
    ali_m, ayse_m = member_id(gid, "Ali"), member_id(gid, "Ayşe")
    expense_id = STATE["market"]
    settlement_id = db_one("SELECT id FROM split_settlements LIMIT 1")["id"]
    tatil = STATE["tatil"]
    # Üye olmayan: her yerde 404
    for url in (f"/ortak/{gid}", f"/ortak/{gid}?sekme=bakiyeler", f"/ortak/{gid}?sekme=uyeler",
                f"/ortak/harcama/{expense_id}", f"/ortak/{tatil}", "/ortak/999999"):
        assert veli.get(url).status_code == 404, url
    for url, data in ((f"/ortak/{gid}/harcama", {"payer": ali_m, "amount": "5", "members": [ali_m]}),
                      (f"/ortak/{gid}/odeme", {"from_id": ali_m, "to_id": ayse_m, "amount": "5"}),
                      (f"/ortak/{gid}/uye", {"name": "X"}),
                      (f"/ortak/uye/{ali_m}/sil", {}),
                      (f"/ortak/{gid}/ayarlar", {"name": "Hack"}),
                      (f"/ortak/{gid}/sil", {}),
                      (f"/ortak/harcama/{expense_id}", {"payer": ali_m, "amount": "5", "members": [ali_m]}),
                      (f"/ortak/harcama/{expense_id}/sil", {}),
                      (f"/ortak/odeme/{settlement_id}/sil", {})):
        assert veli.post(url, data).status_code == 404, url
    assert db_one("SELECT name FROM split_groups WHERE id = ?", (gid,))["name"] == "Ev"
    assert db_one("SELECT 1 FROM split_expenses WHERE id = ?", (expense_id,))

    # Üye ama sahibi değil: grup ve üye yönetimi 403
    ali_m = member_id(gid, "Ali")
    for client in (ayse, can):
        assert client.post(f"/ortak/{gid}/sil").status_code == 403
        assert client.post(f"/ortak/{gid}/ayarlar", {"name": "Yeni"}).status_code == 403
        assert client.post(f"/ortak/{gid}/uye", {"name": "Zeynep"}).status_code == 403
        assert client.post(f"/ortak/uye/{ali_m}/sil").status_code == 403
    assert db_one("SELECT name FROM split_groups WHERE id = ?", (gid,))["name"] == "Ev"
    assert not db_one("SELECT 1 FROM split_members WHERE name = 'Zeynep'")

    # Sahibi yeniden adlandırır
    r = admin.post(f"/ortak/{gid}/ayarlar", {"name": "Ev (Kadıköy)"})
    assert r.status_code == 302
    assert db_one("SELECT name FROM split_groups WHERE id = ?", (gid,))["name"] == "Ev (Kadıköy)"
    r = admin.post(f"/ortak/{gid}/ayarlar", {"name": ""}, follow_redirects=True)
    assert "boş olamaz" in r.get_data(as_text=True)


def test_telegram():
    gid = STATE["ev"]
    admin_m, ayse_m, can_m = member_id(gid, "admin"), member_id(gid, "Ayşe"), member_id(gid, "can")
    CALLS.clear()
    r = admin.post(f"/ortak/{gid}/harcama", {"payer": admin_m, "amount": "120", "note": "Market <b>&",
                                             "members": [admin_m, ayse_m, can_m]})
    assert r.status_code == 302
    sends = [p for m, p in CALLS if m == "sendMessage"]
    # Sadece Telegram'ı bağlı diğer uygulama kullanıcısı üye (ayse); ekleyen admin ve üye olmayan veli değil
    assert [p["chat_id"] for p in sends] == ["555"], sends
    text = sends[0]["text"]
    assert text == "👥 <b>Ev (Kadıköy)</b>: admin 120 ₺ ödedi (Market &lt;b&gt;&amp;). Senin payın: 40 ₺.", text

    # ayse eklerse admin'e gider; notsuz harcamada parantez yok. 10/3: artan kuruş ilk üyeye (admin)
    CALLS.clear()
    ayse.post(f"/ortak/{gid}/harcama", {"payer": ayse_m, "amount": "10", "members": [ayse_m, admin_m, can_m]})
    sends = [p for m, p in CALLS if m == "sendMessage"]
    assert [p["chat_id"] for p in sends] == ["111"], sends
    assert sends[0]["text"] == "👥 <b>Ev (Kadıköy)</b>: Ayşe 10 ₺ ödedi. Senin payın: 3,34 ₺.", sends[0]["text"]
    assert sorted(shares_of(last_expense(gid)["id"]).values()) == [3.33, 3.33, 3.34]

    # Bölüşüme dahil olmayan üyeye "payın 0 ₺" mesajı gitmez
    CALLS.clear()
    admin.post(f"/ortak/{gid}/harcama", {"payer": admin_m, "amount": "50", "members": [admin_m, can_m]})
    assert not [p for m, p in CALLS if m == "sendMessage"]

    # Telegram hatası isteği bozmaz
    FAIL[0] = True
    try:
        before = db_one("SELECT COUNT(*) AS n FROM split_expenses")["n"]
        r = admin.post(f"/ortak/{gid}/harcama", {"payer": admin_m, "amount": "30", "members": [admin_m, ayse_m]},
                       follow_redirects=True)
        assert r.status_code == 200 and "harcama eklendi" in r.get_data(as_text=True)
        assert db_one("SELECT COUNT(*) AS n FROM split_expenses")["n"] == before + 1
    finally:
        FAIL[0] = False

    # Beklenmedik hata (ör. send_message içinde) da isteği bozmaz
    original = tg.send_message
    tg.send_message = lambda *a, **k: 1 / 0
    try:
        r = admin.post(f"/ortak/{gid}/harcama", {"payer": admin_m, "amount": "5", "members": [admin_m, ayse_m]})
        assert r.status_code == 302
    finally:
        tg.send_message = original

    # Telegram kapalıysa hiç çağrı yapılmaz
    token = os.environ.pop("TELEGRAM_BOT_TOKEN")
    try:
        CALLS.clear()
        admin.post(f"/ortak/{gid}/harcama", {"payer": admin_m, "amount": "7", "members": [admin_m, ayse_m]})
        assert CALLS == []
    finally:
        os.environ["TELEGRAM_BOT_TOKEN"] = token


def test_delete_group_and_boot():
    gid = STATE["tatil"]
    assert db_one("SELECT COUNT(*) AS n FROM split_settlements WHERE group_id = ?", (gid,))["n"] > 0
    expense_ids = [r["id"] for r in db_all("SELECT id FROM split_expenses WHERE group_id = ?", (gid,))]
    r = admin.post(f"/ortak/{gid}/sil")
    assert r.status_code == 302 and loc(r).endswith("/ortak/")
    assert db_one("SELECT 1 FROM split_groups WHERE id = ?", (gid,)) is None
    for table in ("split_members", "split_expenses", "split_settlements"):
        assert db_one(f"SELECT COUNT(*) AS n FROM {table} WHERE group_id = ?", (gid,))["n"] == 0, table
    assert db_one(f"SELECT COUNT(*) AS n FROM split_shares WHERE expense_id IN ({','.join('?' * len(expense_ids))})",
                  expense_ids)["n"] == 0
    assert f'href="/ortak/{gid}"' not in ayse.text("/ortak/")
    # Uygulama açılıyor, menüde modül var
    assert admin.get("/").status_code == 200
    assert "Ortak Harcama" in admin.text("/ortak/")
    for client in (admin, ayse, can, veli):
        client.text("/ortak/")


if __name__ == "__main__":
    test_split_equal()
    test_groups_and_members()
    test_equal_split_rounding()
    test_scenario_balances_and_settle()
    test_edit_delete_expense()
    test_member_removal()
    test_access_rules()
    test_telegram()
    test_delete_group_and_boot()
    print("OK")
