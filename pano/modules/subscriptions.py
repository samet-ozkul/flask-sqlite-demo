"""🔁 Abonelikler: yenilenme tarihi, döngü (haftalık/aylık/yıllık), aylık/yıllık toplam maliyet."""
from datetime import timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .. import external
from ..auth import login_required
from ..db import execute, owned_or_404, query
from ..reminders import advance_subscriptions
from ..utils import (CURRENCIES, form_bool, form_choice, form_date, form_str, redirect_back,
                     today)
from .expenses import form_amount, valid_amount

bp = Blueprint("subscriptions", __name__, url_prefix="/abonelikler")

CYCLES = {"weekly": "Haftalık", "monthly": "Aylık", "yearly": "Yıllık"}
CYCLE_UNITS = {"weekly": "hafta", "monthly": "ay", "yearly": "yıl"}
# Aylık karşılık çarpanı: haftalık × 52 / 12, yıllık / 12
CYCLE_FACTORS = {"weekly": 52 / 12, "monthly": 1.0, "yearly": 1 / 12}
CATEGORY_SUGGESTIONS = ["Dizi / film", "Müzik", "Yazılım", "Bulut depolama", "Oyun", "Spor salonu",
                        "Haber / dergi", "Telefon / internet", "Sigorta", "Alan adı / hosting", "Diğer"]


def monthly_amount(row):
    return row["amount"] * CYCLE_FACTORS.get(row["cycle"], 1.0)


def _currency_order(code):
    keys = list(CURRENCIES)
    return keys.index(code) if code in keys else len(keys)


def compute_totals(rows, rate_table):
    """Aktif aboneliklerin aylık ve yıllık karşılığı.

    Hepsi TL ise ya da kur tablosu tüm yabancı para birimlerini içeriyorsa monthly/yearly TL'dir.
    Kur yoksa monthly/yearly None olur ve by_currency [(para birimi, aylık, yıllık)] gösterilir.
    """
    per_cur = {}
    for r in rows:
        per_cur[r["currency"]] = per_cur.get(r["currency"], 0) + monthly_amount(r)
    by_currency = [(c, round(v, 2), round(v * 12, 2))
                   for c, v in sorted(per_cur.items(), key=lambda kv: _currency_order(kv[0]))]
    foreign = [c for c in per_cur if c != "TRY"]
    monthly = None
    if not foreign:
        monthly = per_cur.get("TRY", 0)
    elif rate_table and all(rate_table.get(c) for c in foreign):
        monthly = sum(external.to_try(v, c, rate_table) for c, v in per_cur.items())
    return {
        "monthly": round(monthly, 2) if monthly is not None else None,
        "yearly": round(monthly * 12, 2) if monthly is not None else None,
        "by_currency": by_currency,
        "converted": bool(foreign) and monthly is not None,
        "rates_missing": bool(foreign) and monthly is None,
    }


def _form_values():
    return {
        "name": form_str("name", 100),
        "amount": form_amount("amount"),
        "currency": form_choice("currency", CURRENCIES, "TRY"),
        "cycle": form_choice("cycle", CYCLES, "monthly"),
        "next_date": form_date("next_date"),
        "category": form_str("category", 50),
        "note": form_str("note", 500),
        "active": form_bool("active"),
    }


def _validate(v):
    if not v["name"]:
        return "Abonelik adı gerekli."
    if not valid_amount(v["amount"]):
        return "Tutar 0'dan büyük bir sayı olmalı (ör. 149,99)."
    if not v["next_date"]:
        return "Sonraki ödeme tarihi gerekli."
    return None


def _categories(user_id):
    used = [r["category"] for r in query(
        "SELECT DISTINCT category FROM subscriptions WHERE user_id = ? AND category != '' ORDER BY category",
        (user_id,))]
    return used + [c for c in CATEGORY_SUGGESTIONS if c not in used]


@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    advance_subscriptions(uid)
    active = query("SELECT * FROM subscriptions WHERE user_id = ? AND active = 1"
                   " ORDER BY next_date, name COLLATE NOCASE", (uid,))
    inactive = query("SELECT * FROM subscriptions WHERE user_id = ? AND active = 0"
                     " ORDER BY name COLLATE NOCASE", (uid,))
    # Kur sadece yabancı para birimli abonelik varsa istenir (ağ çağrısı)
    rate_table = external.rates() if any(r["currency"] != "TRY" for r in active) else None
    totals = compute_totals(active, rate_table)
    approx = {}
    if rate_table:
        for r in active:
            if r["currency"] != "TRY" and rate_table.get(r["currency"]):
                approx[r["id"]] = external.to_try(r["amount"], r["currency"], rate_table)
    week_later = (today() + timedelta(days=7)).isoformat()
    due_soon = sum(1 for r in active if r["next_date"] <= week_later)
    return render_template(
        "subscriptions/index.html", active=active, inactive=inactive, totals=totals, rates=rate_table,
        approx=approx, due_soon=due_soon, cycles=CYCLES, units=CYCLE_UNITS, categories=_categories(uid),
    )


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    v = _form_values()
    error = _validate(v)
    if error:
        flash(error, "error")
        return redirect_back("subscriptions.index")
    execute(
        "INSERT INTO subscriptions (user_id, name, amount, currency, cycle, next_date, category, note, active)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (g.user["id"], v["name"], v["amount"], v["currency"], v["cycle"], v["next_date"], v["category"],
         v["note"], v["active"]),
    )
    flash(f"{v['name']} aboneliği eklendi.", "success")
    return redirect_back("subscriptions.index")


@bp.route("/<int:sub_id>", methods=["GET", "POST"])
@login_required
def edit(sub_id):
    uid = g.user["id"]
    sub = owned_or_404("subscriptions", sub_id, uid)
    if request.method == "POST":
        v = _form_values()
        error = _validate(v)
        if error:
            flash(error, "error")
            return redirect(url_for(".edit", sub_id=sub_id))
        execute(
            "UPDATE subscriptions SET name = ?, amount = ?, currency = ?, cycle = ?, next_date = ?, category = ?,"
            " note = ?, active = ? WHERE id = ? AND user_id = ?",
            (v["name"], v["amount"], v["currency"], v["cycle"], v["next_date"], v["category"], v["note"],
             v["active"], sub_id, uid),
        )
        flash("Abonelik güncellendi.", "success")
        return redirect(url_for(".index"))
    return render_template("subscriptions/edit.html", sub=sub, cycles=CYCLES, categories=_categories(uid))


@bp.route("/<int:sub_id>/durum", methods=["POST"])
@login_required
def toggle(sub_id):
    uid = g.user["id"]
    sub = owned_or_404("subscriptions", sub_id, uid)
    execute("UPDATE subscriptions SET active = 1 - active WHERE id = ? AND user_id = ?", (sub_id, uid))
    if sub["active"]:
        flash(f"{sub['name']} durduruldu.", "success")
    else:
        flash(f"{sub['name']} yeniden etkin.", "success")
    return redirect_back("subscriptions.index")


@bp.route("/<int:sub_id>/sil", methods=["POST"])
@login_required
def delete(sub_id):
    uid = g.user["id"]
    sub = owned_or_404("subscriptions", sub_id, uid)
    execute("DELETE FROM subscriptions WHERE id = ? AND user_id = ?", (sub_id, uid))
    flash(f"{sub['name']} aboneliği silindi.", "success")
    return redirect(url_for(".index"))
