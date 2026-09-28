"""Bütçe limitleri ve uyarıları, aylık rapor, Excel/CSV dışa aktarma.

Çalıştır: .venv/Scripts/python tests/test_budget_report.py
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

app = make_app()
os.environ["CRON_SECRET"] = "gizli"
os.environ["TELEGRAM_BOT_TOKEN"] = "123:test"

import pano.external as ext  # noqa: E402
import pano.telegram as tg  # noqa: E402
import pano.todo_reminders as todo  # noqa: E402
from pano import budgets  # noqa: E402
from pano.auth import create_user  # noqa: E402
from pano.db import execute, query_one  # noqa: E402
from pano.utils import TZ, today  # noqa: E402

CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {})) or {"message_id": 1}
ext.weather = lambda lat, lon: None
ext.rates = lambda: {"USD": 48.85, "EUR": 55.52, "GBP": 64.6, "date": "2026-09-24"}
NOW = [datetime(2026, 9, 25, 10, 0, tzinfo=TZ)]
todo.now_local = lambda: NOW[0]

C = Client(app)
RAW = app.test_client()
with app.app_context():
    execute("UPDATE users SET telegram_chat_id = '100' WHERE username = 'admin'")
    with app.test_request_context():
        SECRET = tg.webhook_secret()


def sent():
    return [p["text"] for m, p in CALLS if m == "sendMessage"]


def add_expense(amount, category, day, note=""):
    with app.app_context():
        execute("INSERT INTO expenses (user_id, amount, category, note, date) VALUES (1, ?, ?, ?, ?)",
                (amount, category, note, day))


def cron(path="hatirlatma"):
    r = RAW.get(f"/cron/gizli/{path}")
    assert r.status_code == 200, r.status_code
    return r.json


def test_budget_page():
    assert "Aylık bütçe" in C.text("/harcamalar/butce")
    C.post("/harcamalar/butce", data={"limit:*": "5.000", "limit:Market": "1.000", "limit:Yemek": ""})
    with app.app_context():
        assert budgets.budgets_of(1) == {"*": 5000.0, "Market": 1000.0}
    C.post("/harcamalar/butce", data={"limit:Market": "1.000", "limit:*": "0"})  # 0 -> limit kaldırılır
    with app.app_context():
        assert budgets.budgets_of(1) == {"Market": 1000.0}
    C.post("/harcamalar/butce", data={"limit:*": "5.000", "limit:Market": "1.000"})
    print("  budget page OK")


def test_budget_alerts():
    add_expense(850, "Market", "2026-09-10")
    with app.app_context():
        st = {r["category"]: r for r in budgets.month_status(1, 2026, 9)}
    assert round(st["Market"]["pct"]) == 85 and st["Market"]["level"] == 80 and st["*"]["level"] is None
    h = C.text("/harcamalar/?ay=2026-09")
    assert "🎯 Bütçe" in h and "%85" in h and "meter warn" in h
    assert cron()["budget_alerts"] == 1
    assert "Market bütçesi %85 doldu" in sent()[-1] and "850 ₺ / 1.000 ₺" in sent()[-1]
    assert cron()["budget_alerts"] == 0  # aynı seviye tekrar gönderilmez
    add_expense(200, "Market", "2026-09-20")
    assert cron()["budget_alerts"] == 1 and "Market bütçesi aşıldı" in sent()[-1]
    assert cron()["budget_alerts"] == 0
    add_expense(4000, "Ev", "2026-09-21")  # toplam 5.050 / 5.000
    assert cron()["budget_alerts"] == 1 and "Aylık toplam bütçe aşıldı" in sent()[-1]
    # Yeni ay: sıfırdan
    NOW[0] = datetime(2026, 10, 3, 10, 0, tzinfo=TZ)
    add_expense(900, "Market", "2026-10-02")
    assert cron()["budget_alerts"] == 1 and "%90" in sent()[-1]
    print("  budget alerts OK")


def test_monthly_report():
    with app.app_context():
        execute("INSERT INTO habits (user_id, name, icon) VALUES (1, 'Su iç', '💧')")
        hid = query_one("SELECT id FROM habits WHERE name = 'Su iç'")["id"]
        for d in ("2026-09-01", "2026-09-02", "2026-09-03"):
            execute("INSERT INTO habit_logs (habit_id, date) VALUES (?, ?)", (hid, d))
        execute("INSERT INTO bills (user_id, name, amount, due_date, paid, paid_at) VALUES (1, 'Su', 120, '2026-09-15', 1, '2026-09-14')")
        execute("INSERT INTO subscriptions (user_id, name, amount, currency, cycle, next_date) VALUES (1, 'Netflix', 229.99, 'TRY', 'monthly', '2026-10-20')")
    NOW[0] = datetime(2026, 10, 1, 8, 0, tzinfo=TZ)
    r = cron("gunluk?force=1")
    assert r.get("reports") == 1, r
    report = sent()[-1]
    assert "Eylül 2026 raporu" in report and "5.050 ₺" in report
    assert "🛒 Market: 1.050 ₺" in report and "🚨 Market" in report
    assert "Ödenen fatura: 1" in report and "Abonelikler: 1 adet" in report and "Su iç: 3/30 gün" in report
    assert cron("gunluk?force=1").get("reports") is None  # rapor ayda bir
    print("  monthly report OK")


def test_bot_commands():
    def say(text):
        RAW.post("/telegram/webhook", data=json.dumps({"message": {"message_id": 1, "chat": {"id": 100}, "text": text}}),
                 content_type="application/json", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
    say("/rapor")
    assert "raporu" in sent()[-1]
    say("/rapor bu ay")
    t = today()
    from pano.utils import MONTHS_TR
    assert f"{MONTHS_TR[t.month - 1]} {t.year} raporu" in sent()[-1]
    say("/harcama 50 market")
    assert "🎯 Market bütçesi:" in sent()[-1] and "🎯 Toplam bütçesi:" in sent()[-1]
    print("  bot /rapor and budget line OK")


def test_export():
    add_expense(1234.5, "Market", "2026-09-11", "=HYPERLINK(\"x\")")
    r = C.get("/harcamalar/disa-aktar?ay=2026-09")
    assert r.status_code == 200 and "harcamalar-2026-09.csv" in r.headers["Content-Disposition"]
    text = r.get_data().decode("utf-8")
    assert text.startswith("﻿") and "Tarih;Kategori;Tutar (TL);Not" in text
    # Tırnak içeren alan CSV kuralıyla tırnaklanır; formül önüne ' eklenir
    assert "11.09.2026;Market;1234,50;\"'=HYPERLINK(\"\"x\"\")\"" in text and "\r\n" in text
    assert "2026-10" not in text and "02.10.2026" not in text
    all_text = C.get("/harcamalar/disa-aktar?ay=tumu").get_data().decode("utf-8")
    assert "02.10.2026" in all_text and "11.09.2026" in all_text
    with app.app_context():
        create_user("veli", "veli12345")
    veli = Client(app, "veli", "veli12345")
    assert "Market" not in veli.get("/harcamalar/disa-aktar?ay=tumu").get_data().decode("utf-8")
    assert "⬇️ Excel" in C.text("/harcamalar/")
    print("  export OK")


if __name__ == "__main__":
    test_budget_page()
    test_budget_alerts()
    test_monthly_report()
    test_bot_commands()
    test_export()
    print("OK")
