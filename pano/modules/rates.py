"""💱 Kurlar: güncel kur, son 30 gün grafiği ve kur alarmları.

Kaynak Avrupa Merkez Bankası referans kurudur (Frankfurter); iş günlerinde günde bir kez
güncellenir, bu yüzden alarm en geç ertesi iş günü tetiklenir. Gram altın sadece GOLDAPI_KEY
ayarlıysa vardır. Alarm tetiklenince Telegram'dan mesaj gelir ve alarm kapanır (tekrar kurulabilir).
"""
from flask import Blueprint, flash, g, redirect, render_template, url_for

from .. import external
from ..auth import login_required
from ..db import execute, owned_or_404, query
from ..utils import fmt_number, form_choice, form_float

bp = Blueprint("rates", __name__, url_prefix="/kurlar")

CURRENCY_NAMES = {"USD": "$ Dolar", "EUR": "€ Euro", "GBP": "£ Sterlin", "XAU": "Gram altın"}
DIRECTIONS = {"above": "üstüne çıkınca", "below": "altına inince"}


def current_values():
    """{'USD': 48.85, ..., 'XAU': 4210.5 (varsa)} ve kur tarihi."""
    table = external.rates() or {}
    values = {c: table[c] for c in ("USD", "EUR", "GBP") if c in table}
    gold = external.gold_gram_try()
    if gold:
        values["XAU"] = gold
    return values, table.get("date")


def sparkline(points, width=220, height=44):
    """[(tarih, değer)] -> SVG polyline noktaları (JS yok)."""
    values = [v for _d, v in points]
    if len(values) < 2:
        return None
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    step = width / (len(values) - 1)
    return " ".join(f"{i * step:.1f},{height - 4 - (v - lo) / span * (height - 8):.1f}" for i, v in enumerate(values))


@bp.route("/")
@login_required
def index():
    values, rate_date = current_values()
    history = external.rate_history(30) or {}
    cards = []
    for code, name in CURRENCY_NAMES.items():
        if code not in values:
            continue
        points = history.get(code) or []
        change = None
        if len(points) >= 2 and points[0][1]:
            change = (points[-1][1] - points[0][1]) * 100 / points[0][1]
        cards.append({"code": code, "name": name, "value": values[code], "line": sparkline(points),
                      "change": change, "low": min((p[1] for p in points), default=None),
                      "high": max((p[1] for p in points), default=None)})
    alerts = query("SELECT * FROM rate_alerts WHERE user_id = ? ORDER BY active DESC, id DESC", (g.user["id"],))
    return render_template("rates/index.html", cards=cards, rate_date=rate_date, alerts=alerts,
                           names=CURRENCY_NAMES, directions=DIRECTIONS, values=values)


@bp.route("/alarm", methods=["POST"])
@login_required
def create_alert():
    currency = form_choice("currency", CURRENCY_NAMES, "USD")
    direction = form_choice("direction", DIRECTIONS, "above")
    threshold = form_float("threshold")
    if threshold is None or threshold <= 0:
        flash("Geçerli bir kur değeri girin (ör. 50 ya da 49,75).", "error")
        return redirect(url_for(".index"))
    execute("INSERT INTO rate_alerts (user_id, currency, direction, threshold) VALUES (?, ?, ?, ?)",
            (g.user["id"], currency, direction, threshold))
    flash(f"Alarm kuruldu: {CURRENCY_NAMES[currency]} {fmt_number(threshold)} ₺ {DIRECTIONS[direction]}.", "success")
    return redirect(url_for(".index"))


@bp.route("/alarm/<int:alert_id>/durum", methods=["POST"])
@login_required
def toggle_alert(alert_id):
    alert = owned_or_404("rate_alerts", alert_id, g.user["id"])
    execute("UPDATE rate_alerts SET active = ?, triggered_at = NULL, triggered_value = NULL WHERE id = ?",
            (0 if alert["active"] else 1, alert_id))
    return redirect(url_for(".index"))


@bp.route("/alarm/<int:alert_id>/sil", methods=["POST"])
@login_required
def delete_alert(alert_id):
    owned_or_404("rate_alerts", alert_id, g.user["id"])
    execute("DELETE FROM rate_alerts WHERE id = ? AND user_id = ?", (alert_id, g.user["id"]))
    return redirect(url_for(".index"))


# ---------- Cron /hatirlatma ----------
def triggered():
    """Tetiklenen aktif alarmlar: [(alarm, güncel_değer, chat_id)]. Kur alınamazsa boş."""
    alerts = query("SELECT a.*, u.telegram_chat_id AS chat_id FROM rate_alerts a JOIN users u ON u.id = a.user_id"
                   " WHERE a.active = 1 AND u.telegram_chat_id IS NOT NULL")
    if not alerts:
        return []  # hiç alarm yoksa kur için ağa hiç çıkma
    values, _ = current_values()
    out = []
    for a in alerts:
        value = values.get(a["currency"])
        if value is None:
            continue
        if (a["direction"] == "above" and value >= a["threshold"]) or (a["direction"] == "below" and value <= a["threshold"]):
            out.append((a, value, a["chat_id"]))
    return out


def mark_triggered(alert_id, value):
    execute("UPDATE rate_alerts SET active = 0, triggered_at = CURRENT_TIMESTAMP, triggered_value = ? WHERE id = ?",
            (value, alert_id))


def message(alert, value, url, escape):
    return (f"💱 <b>{escape(CURRENCY_NAMES[alert['currency']])} {fmt_number(value)} ₺</b> oldu\n"
            f"Alarm: {fmt_number(alert['threshold'])} ₺ {DIRECTIONS[alert['direction']]}\n"
            f'<a href="{escape(url)}">Kurlar →</a>')
