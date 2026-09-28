"""Önemli günler ve kur alarmları.

Çalıştır: .venv/Scripts/python tests/test_specialdays_rates.py
"""
import os
import sys
from datetime import date, datetime, timedelta

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
from pano.modules import specialdays as sd  # noqa: E402
from pano.utils import TZ, today  # noqa: E402

CALLS = []
tg._call = lambda method, params=None, files=None: CALLS.append((method, params or {})) or {"message_id": 1}
NOW = [datetime(2026, 9, 25, 10, 0, tzinfo=TZ)]
todo.now_local = lambda: NOW[0]
RATES = [{"USD": 48.85, "EUR": 55.52, "GBP": 64.6, "date": "2026-09-24"}]
ext.rates = lambda: RATES[0]
ext.rate_history = lambda days=30: {"USD": [["2026-08-26", 47.0], ["2026-09-24", 48.85]], "EUR": [], "GBP": []}
ext.gold_gram_try = lambda: None

C = Client(app)
RAW = app.test_client()
with app.app_context():
    execute("UPDATE users SET telegram_chat_id = '100' WHERE username = 'admin'")


def one(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


def cron():
    r = RAW.get("/cron/gizli/hatirlatma")
    assert r.status_code == 200
    return r.json


def sent():
    return [p["text"] for m, p in CALLS if m == "sendMessage"]


def test_occurrence():
    row = {"month": 2, "day": 29, "year": 2000, "kind": "birthday"}
    assert sd.occurrence(2, 29, 2027) == date(2027, 2, 28) and sd.occurrence(2, 29, 2028) == date(2028, 2, 29)
    assert sd.next_occurrence(row, date(2027, 3, 1)) == date(2028, 2, 29)
    assert sd.next_occurrence({"month": 9, "day": 25}, date(2026, 9, 25)) == date(2026, 9, 25)
    assert sd.next_occurrence({"month": 9, "day": 24}, date(2026, 9, 25)) == date(2027, 9, 24)
    assert sd.ordinal_text(row, date(2026, 2, 28)) == "26. yaş"
    assert sd.ordinal_text({"year": 2016, "kind": "anniversary"}, date(2026, 6, 1)) == "10. yıl"
    assert sd.ordinal_text({"year": None, "kind": "birthday"}, date(2026, 6, 1)) == ""
    print("  occurrence OK")


def test_crud_and_upcoming():
    t = today()
    soon = t + timedelta(days=5)
    C.post("/onemli-gunler/yeni", data={"name": "Annemin doğum günü", "kind": "birthday",
                                        "date": soon.replace(year=1966).isoformat(), "notify_days": "7"})
    C.post("/onemli-gunler/yeni", data={"name": "Evlilik", "kind": "anniversary",
                                        "date": (t + timedelta(days=200)).isoformat(), "no_year": "1", "notify_days": "3"})
    C.post("/onemli-gunler/yeni", data={"name": "", "date": t.isoformat()})  # ad yok -> reddedilir
    anne = one("SELECT * FROM special_days WHERE name = 'Annemin doğum günü'")
    assert (anne["month"], anne["day"], anne["year"], anne["notify_days"]) == (soon.month, soon.day, 1966, 7)
    assert one("SELECT year FROM special_days WHERE name = 'Evlilik'")["year"] is None
    assert one("SELECT COUNT(*) AS n FROM special_days")["n"] == 2
    h = C.text("/onemli-gunler/")
    assert "Annemin doğum günü" in h and f"{soon.year - 1966}. yaş" in h and "5 gün" in h
    # Panoda yaklaşanlar
    assert "Annemin doğum günü" in C.text("/")
    # Düzenleme ve sahiplik
    assert "Annemin" in C.text(f"/onemli-gunler/{anne['id']}")
    with app.app_context():
        create_user("veli", "veli12345")
    veli = Client(app, "veli", "veli12345")
    assert veli.get(f"/onemli-gunler/{anne['id']}").status_code == 404
    assert veli.post(f"/onemli-gunler/{anne['id']}/sil").status_code == 404
    print("  crud/upcoming OK")


def test_day_reminders():
    with app.app_context():
        execute("DELETE FROM special_days")
        execute("INSERT INTO special_days (user_id, name, kind, month, day, year, notify_days) VALUES (1, 'Ali', 'birthday', 10, 2, 1990, 7)")
        execute("INSERT INTO special_days (user_id, name, kind, month, day, notify_days) VALUES (1, 'Sessiz', 'other', 10, 30, 0)")
    NOW[0] = datetime(2026, 9, 25, 8, 30, tzinfo=TZ)
    assert cron()["days_sent"] == 0  # 09:00'dan önce yok
    NOW[0] = datetime(2026, 9, 25, 9, 5, tzinfo=TZ)
    assert cron()["days_sent"] == 1
    assert "7 gün sonra" in sent()[-1] and "Ali" in sent()[-1] and "36. yaş" in sent()[-1]
    assert cron()["days_sent"] == 0  # bu yıl tekrar yok
    NOW[0] = datetime(2026, 10, 2, 9, 1, tzinfo=TZ)
    assert cron()["days_sent"] == 1 and "Bugün" in sent()[-1]
    assert cron()["days_sent"] == 0
    # Ertesi yıl yeniden
    NOW[0] = datetime(2027, 9, 26, 9, 1, tzinfo=TZ)
    assert cron()["days_sent"] == 1 and "37. yaş" in sent()[-1]
    # notify_days=0: sadece o gün
    NOW[0] = datetime(2026, 10, 29, 9, 1, tzinfo=TZ)
    assert cron()["days_sent"] == 0
    NOW[0] = datetime(2026, 10, 30, 9, 1, tzinfo=TZ)
    assert cron()["days_sent"] == 1 and "Sessiz" in sent()[-1]
    print("  day reminders OK")


def test_rates_page_and_alerts():
    h = C.text("/kurlar/")
    assert "48,85" in h and "<polyline" in h and "Kur alarmları" in h
    C.post("/kurlar/alarm", data={"currency": "USD", "direction": "above", "threshold": "50"})
    C.post("/kurlar/alarm", data={"currency": "EUR", "direction": "below", "threshold": "55,00"})
    C.post("/kurlar/alarm", data={"currency": "USD", "direction": "above", "threshold": "abc"})  # geçersiz
    alerts = {a["currency"]: a for a in query_all("SELECT * FROM rate_alerts")}
    assert len(alerts) == 2 and alerts["EUR"]["threshold"] == 55.0
    assert cron()["rate_alerts"] == 0
    RATES[0] = {"USD": 50.1, "EUR": 55.52, "GBP": 64.6, "date": "2026-09-25"}
    assert cron()["rate_alerts"] == 1
    assert "Dolar 50,10 ₺" in sent()[-1] and "üstüne çıkınca" in sent()[-1]
    usd = one("SELECT * FROM rate_alerts WHERE currency = 'USD'")
    assert usd["active"] == 0 and usd["triggered_value"] == 50.1
    assert cron()["rate_alerts"] == 0  # tek sefer
    RATES[0] = {"USD": 50.1, "EUR": 54.9, "GBP": 64.6, "date": "2026-09-26"}
    assert cron()["rate_alerts"] == 1 and "altına inince" in sent()[-1]
    # Tekrar kur
    C.post(f"/kurlar/alarm/{usd['id']}/durum")
    assert one("SELECT active FROM rate_alerts WHERE id = ?", (usd["id"],))["active"] == 1
    assert "Tetiklendi" in C.text("/kurlar/")
    # Kur alınamazsa sayfa çalışır, alarm tetiklenmez
    RATES[0] = None
    assert "alınamadı" in C.text("/kurlar/") and cron()["rate_alerts"] == 0
    # Başkası silemez
    veli = Client(app, "veli", "veli12345")
    assert veli.post(f"/kurlar/alarm/{usd['id']}/sil").status_code == 404
    print("  rates/alerts OK")


def query_all(sql, args=()):
    with app.app_context():
        return query(sql, args)


if __name__ == "__main__":
    test_occurrence()
    test_crud_and_upcoming()
    test_day_reminders()
    test_rates_page_and_alerts()
    print("OK")
