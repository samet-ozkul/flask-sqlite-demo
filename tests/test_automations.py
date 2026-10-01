"""Otomasyon: zamanlı kurallar, olaylar (harcama, fatura, liste, yapılacak), eylemler, güvenlik sınırları.

Çalıştır: .venv/Scripts/python tests/test_automations.py
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

app = make_app()
os.environ["CRON_SECRET"] = "gizli"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.telegram as tg  # noqa: E402
import pano.todo_reminders as todo  # noqa: E402
from pano import automation  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query, query_one  # noqa: E402
from pano.utils import TZ  # noqa: E402

CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {})) or {"message_id": 1}
NOW = [datetime(2026, 10, 15, 10, 0, tzinfo=TZ)]  # perşembe
todo.now_local = lambda: NOW[0]

with app.app_context():
    AYSE = create_user("ayse", "ayse12345")
    VELI = create_user("veli", "veli12345")  # Telegram'ı yok
    execute("UPDATE users SET telegram_chat_id = '100', display_name = 'Samet' WHERE id = 1")
    execute("UPDATE users SET telegram_chat_id = '200', display_name = 'Ayşe' WHERE id = ?", (AYSE,))
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


def sent():
    return [(p["chat_id"], p["text"]) for m, p in CALLS if m == "sendMessage"]


def cron():
    r = RAW.get("/cron/gizli/hatirlatma")
    assert r.status_code == 200, r.status_code
    return r.json["automations"]


def rule(name):
    return one("SELECT * FROM automations WHERE name = ?", (name,))


def new_rule(data, client=ADMIN):
    return client.post("/otomasyon/yeni", data=data)


def test_monthly_rent():
    page = ADMIN.text("/otomasyon/?ornek=kira")
    assert 'value="Kira"' in page and "{ay} kirası" in page
    new_rule({"name": "Kira", "trigger_type": "monthly", "day": "1", "time": "09:00", "action_type": "expense",
              "expense_amount": "15.000", "expense_category": "ev", "expense_note": "{ay} kirası"})
    r = rule("Kira")
    assert r and r["last_period"] == "2026-10"  # bu ayın 1'i geçti: hemen çalışmaz
    assert cron() == 0
    NOW[0] = datetime(2026, 11, 1, 8, 59, tzinfo=TZ)
    assert cron() == 0
    NOW[0] = datetime(2026, 11, 1, 9, 2, tzinfo=TZ)
    assert cron() == 1 and cron() == 0  # dönemde bir kez
    e = one("SELECT * FROM expenses WHERE note = 'Kasım kirası'")
    assert e and (e["amount"], e["category"], e["date"]) == (15000, "Ev", "2026-11-01")
    # Cron günlerce çalışmasa da ay içinde geç de olsa bir kez yapılır
    NOW[0] = datetime(2026, 12, 3, 14, 0, tzinfo=TZ)
    assert cron() == 1 and one("SELECT 1 x FROM expenses WHERE note = 'Aralık kirası'")
    assert "Her ayın 1. günü 09:00" in ADMIN.text("/otomasyon/")
    # 31'i olmayan ayda son gün
    tz = TZ
    assert automation.scheduled_at("monthly", {"day": 31, "time": "08:00"}, datetime(2027, 2, 10, tzinfo=tz)).day == 28
    print("  monthly OK")


def test_weekly_and_daily_notify():
    NOW[0] = datetime(2026, 10, 14, 12, 0, tzinfo=TZ)  # çarşamba
    new_rule({"name": "Pazartesi çöp", "trigger_type": "weekly", "weekday": "0", "time": "08:00",
              "action_type": "todo", "todo_list": "", "todo_text": "x"})
    assert rule("Pazartesi çöp") is None  # liste seçilmedi
    ADMIN.post("/listeler/yeni", data={"name": "Ev işleri", "kind": "todo", "shared": "1"})
    ev = one("SELECT id FROM lists WHERE name = 'Ev işleri'")["id"]
    new_rule({"name": "Pazartesi çöp", "trigger_type": "weekly", "weekday": "0", "time": "08:00",
              "action_type": "todo", "todo_list": str(ev), "todo_text": "Çöpü çıkar ({tarih})", "todo_due_today": "1"})
    assert cron() == 0  # bu haftanın pazartesisi geçti; çarşamba kurulan kural hemen çalışmaz
    NOW[0] = datetime(2026, 10, 19, 8, 5, tzinfo=TZ)
    assert cron() == 1
    item = one("SELECT * FROM list_items WHERE text = 'Çöpü çıkar (19.10.2026)'")
    assert item and item["due_date"] == "2026-10-19"
    # Her gün mesaj
    new_rule({"name": "Su", "trigger_type": "daily", "time": "10:00", "action_type": "notify", "notify_to": "me",
              "notify_text": "💧 Su içmeyi unutma <b>"})
    NOW[0] = datetime(2026, 10, 19, 10, 1, tzinfo=TZ)
    before = len(sent())
    cron()
    chat, text = sent()[-1]
    assert len(sent()) == before + 1 and chat == "100" and "⚙️ <b>Su</b>" in text and "&lt;b&gt;" in text
    print("  weekly/daily OK")


def test_list_count_shared():
    ADMIN.post("/listeler/yeni", data={"name": "Market", "kind": "shopping", "shared": "1"})
    market = one("SELECT id FROM lists WHERE name = 'Market'")["id"]
    assert 'value="' + str(market) + '" selected' in ADMIN.text("/otomasyon/?ornek=market")
    new_rule({"name": "Market kalabalık", "trigger_type": "list_count", "list_id": str(market), "count": "3",
              "action_type": "notify", "notify_to": "me", "notify_text": "🛒 {liste} listesinde {adet} ürün oldu"})
    before = len(sent())
    AYSE_C.post(f"/listeler/{market}/ekle", data={"text": "süt, ekmek"})
    assert len(sent()) == before
    AYSE_C.post(f"/listeler/{market}/ekle", data={"text": "yumurta, peynir"})  # 2 -> 4: eşik geçildi
    assert sent()[-1] == ("100", "⚙️ <b>Market kalabalık</b>\n🛒 Market listesinde 4 ürün oldu")
    n = len(sent())
    AYSE_C.post(f"/listeler/{market}/ekle", data={"text": "zeytin"})
    assert len(sent()) == n  # zaten eşiğin üstünde
    print("  list count OK")


def test_bill_paid_to_spouse():
    page = ADMIN.text("/otomasyon/?ornek=fatura")
    assert f'value="{AYSE}" selected' in page and "veli" not in page.lower().split("kime")[1][:500]
    new_rule({"name": "Fatura haberi", "trigger_type": "bill_paid", "action_type": "notify", "notify_to": str(AYSE),
              "notify_text": "🧾 {fatura} faturası ödendi ({tutar})."})
    run("INSERT INTO bills (user_id, name, amount, due_date) VALUES (1, 'Elektrik', 845.5, '2026-10-20')")
    bill = one("SELECT id FROM bills WHERE name = 'Elektrik'")["id"]
    ADMIN.post(f"/faturalar/{bill}/ode", data={"add_expense": "1"})
    chat, text = sent()[-1]
    assert chat == "200" and "Fatura haberi</b> · Samet" in text and "Elektrik faturası ödendi (845,50 ₺)" in text
    # Telegram'ı olmayan kişiye mesaj kuralı kurulamaz
    r = new_rule({"name": "Veliye", "trigger_type": "bill_paid", "action_type": "notify", "notify_to": str(VELI),
                  "notify_text": "x"})
    assert r.status_code == 400 and rule("Veliye") is None
    print("  bill paid OK")


def test_expense_conditions_and_loop_guard():
    new_rule({"name": "Büyük market", "trigger_type": "expense_added", "exp_category": "market", "exp_min": "1.000",
              "action_type": "note", "note_title": "Büyük harcama", "note_text": "{kategori}: {tutar} {not}"})
    for amount, cat in (("500", "Market"), ("1.500", "Market"), ("2.000", "Yemek")):
        ADMIN.post("/harcamalar/yeni", data={"amount": amount, "category": cat, "note": "haftalık"})
    notes = rows("SELECT content FROM notes WHERE title = 'Büyük harcama'")
    assert [n["content"] for n in notes] == ["Market: 1.500 ₺ haftalık"]
    # Otomasyonun eklediği harcama başka otomasyonu (ve kendini) tetiklemez
    new_rule({"name": "Her harcamada 1 TL", "trigger_type": "expense_added", "action_type": "expense",
              "expense_amount": "1", "expense_category": "Diğer", "expense_note": "otomatik"})
    before = one("SELECT COUNT(*) AS n FROM expenses")["n"]
    ADMIN.post("/harcamalar/yeni", data={"amount": "1.200", "category": "Market"})
    assert one("SELECT COUNT(*) AS n FROM expenses")["n"] == before + 2  # kendisi + 1 TL; döngü yok
    assert len(rows("SELECT 1 FROM notes WHERE title = 'Büyük harcama'")) == 2  # 1 TL'lik ek harcama not açmadı
    run("UPDATE automations SET enabled = 0 WHERE name IN ('Her harcamada 1 TL', 'Büyük market')")
    print("  expense conditions OK")


def test_todo_done_by_other():
    ev = one("SELECT id FROM lists WHERE name = 'Ev işleri'")["id"]
    new_rule({"name": "İş bitti", "trigger_type": "todo_done", "done_list": str(ev), "action_type": "notify",
              "notify_to": "me", "notify_text": "✅ {kim}: {is}"})
    ADMIN.post(f"/listeler/{ev}/ekle", data={"text": "Bulaşık"})
    item = one("SELECT id FROM list_items WHERE text = 'Bulaşık'")["id"]
    AYSE_C.post(f"/listeler/madde/{item}/isaretle")
    assert sent()[-1] == ("100", "⚙️ <b>İş bitti</b>\n✅ Ayşe: Bulaşık")
    n = len(sent())
    AYSE_C.post(f"/listeler/madde/{item}/isaretle")  # geri alma tetiklemez
    assert len(sent()) == n
    print("  todo done OK")


def test_manage():
    r = rule("Su")
    # Başkası göremez, değiştiremez
    assert "Su içmeyi" not in AYSE_C.text("/otomasyon/")
    for url in (f"/otomasyon/{r['id']}", f"/otomasyon/{r['id']}/sil", f"/otomasyon/{r['id']}/dene"):
        assert AYSE_C.post(url, data={"name": "x"}).status_code == 404
    # Başkasının özel listesi seçilemez
    AYSE_C.post("/listeler/yeni", data={"name": "Ayşe özel", "kind": "todo"})
    private = one("SELECT id FROM lists WHERE name = 'Ayşe özel'")["id"]
    assert new_rule({"name": "Sızma", "trigger_type": "todo_done", "done_list": str(private), "action_type": "notify",
                     "notify_text": "x"}).status_code == 400
    # Düzenleme formu dolu gelir; zaman değişince dönem yeniden hesaplanır
    page = ADMIN.text(f"/otomasyon/{r['id']}")
    assert 'value="Su"' in page and "Su içmeyi unutma" in page and 'value="10:00"' in page
    NOW[0] = datetime(2026, 10, 20, 9, 0, tzinfo=TZ)
    ADMIN.post(f"/otomasyon/{r['id']}", data={"name": "Su", "trigger_type": "daily", "time": "08:30",
                                              "action_type": "notify", "notify_to": "me", "notify_text": "💧"})
    assert rule("Su")["last_period"] == "2026-10-20" and cron() == 0
    # Dene: hemen çalışır, kayıtta görünür
    before = len(sent())
    r2 = ADMIN.post(f"/otomasyon/{r['id']}/dene", follow_redirects=True)
    assert "Mesaj gönderildi" in r2.get_data(as_text=True) and len(sent()) == before + 1
    assert "🧪 Deneme" in ADMIN.text("/otomasyon/")
    # Kapalı kural çalışmaz; açılınca kaçırdığı dönemi sonradan yapmaz
    ADMIN.post(f"/otomasyon/{r['id']}/ac-kapat")
    NOW[0] = datetime(2026, 10, 21, 9, 0, tzinfo=TZ)
    assert cron() == 0
    ADMIN.post(f"/otomasyon/{r['id']}/ac-kapat")
    assert cron() == 0 and rule("Su")["enabled"] == 1
    # Silme çöp kutusuna, geri gelir
    ADMIN.post(f"/otomasyon/{r['id']}/sil")
    assert rule("Su") is None
    ADMIN.post(f"/cop-kutusu/{one('SELECT id FROM trash WHERE module = ?', ('automations',))['id']}/geri")
    assert rule("Su")
    print("  manage OK")


def test_errors_and_cap():
    # Liste silinince eylem hata verir ama asıl işlem bozulmaz; hata kuralda görünür
    ADMIN.post("/listeler/yeni", data={"name": "Geçici", "kind": "shopping"})
    tmp = one("SELECT id FROM lists WHERE name = 'Geçici'")["id"]
    new_rule({"name": "Harcamada listeye", "trigger_type": "expense_added", "action_type": "shopping",
              "shopping_list": str(tmp), "shopping_text": "fiş, poşet"})
    ADMIN.post(f"/listeler/{tmp}/sil")
    ADMIN.post("/harcamalar/yeni", data={"amount": "77", "category": "Market", "note": "bozulmamalı"})
    assert one("SELECT 1 x FROM expenses WHERE note = 'bozulmamalı'")
    assert "erişilemiyor" in rule("Harcamada listeye")["last_error"]
    assert "⚠️ Seçili liste bulunamadı" in ADMIN.text("/otomasyon/")
    run("UPDATE automations SET enabled = 0 WHERE name = 'Harcamada listeye'")
    # Günlük sınır
    with app.test_request_context():
        r = automation.load(rule("Fatura haberi"))
        results = [automation.run(r, {"fatura": "x", "tutar": 1}, NOW[0])[0] for _ in range(automation.DAILY_CAP + 2)]
    ok_count = one("SELECT COUNT(*) AS n FROM automation_log WHERE automation_id = ? AND ok = 1", (r["id"],))["n"]
    assert ok_count == automation.DAILY_CAP and results[-1] is False
    assert "çalışma sınırı doldu" in one("SELECT detail FROM automation_log ORDER BY id DESC")["detail"]
    # Doldurma güvenli: bilinmeyen ve tehlikeli yer tutucular olduğu gibi kalır
    assert automation.fill("{tutar} {yok} {0.__class__}", {"tutar": "5 ₺"}) == "5 ₺ {yok} {0.__class__}"
    print("  errors/cap OK")


if __name__ == "__main__":
    test_monthly_rent()
    test_weekly_and_daily_notify()
    test_list_count_shared()
    test_bill_paid_to_spouse()
    test_expense_conditions_and_loop_guard()
    test_todo_done_by_other()
    test_manage()
    test_errors_and_cap()
    print("OK")
