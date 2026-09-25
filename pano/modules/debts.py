"""🤝 Borç / Alacak: kime ne verdim, kimden ne aldım; vade, kapatma, kişi bazında net durum."""
from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from ..auth import login_required
from ..db import execute, owned_or_404, query, query_one
from ..utils import (CURRENCIES, form_choice, form_date, form_str, redirect_back, today_str)
from .expenses import form_amount, valid_amount

bp = Blueprint("debts", __name__, url_prefix="/borclar")

TABS = {"alacaklarim": "Alacaklarım", "borclarim": "Borçlarım", "kapananlar": "Kapananlar"}
TAB_DIRECTION = {"alacaklarim": "lent", "borclarim": "borrowed"}
DIRECTIONS = {"lent": "Alacak — ben verdim", "borrowed": "Borç — ben aldım"}


def _currency_order(code):
    keys = list(CURRENCIES)
    return keys.index(code) if code in keys else len(keys)


def currency_totals(rows):
    """[(para birimi, toplam)] — TL önce."""
    totals = {}
    for r in rows:
        totals[r["currency"]] = totals.get(r["currency"], 0) + r["amount"]
    return [(c, round(v, 2)) for c, v in sorted(totals.items(), key=lambda kv: _currency_order(kv[0]))]


def person_summary(rows):
    """Açık kayıtlardan kişi başına net durum: + bana borçlu, − ben borçluyum (para birimine göre)."""
    people = {}
    for r in rows:
        name = r["person"].strip()
        p = people.setdefault(name.casefold(), {"name": name, "count": 0, "net": {}})
        sign = 1 if r["direction"] == "lent" else -1
        p["net"][r["currency"]] = p["net"].get(r["currency"], 0) + sign * r["amount"]
        p["count"] += 1
    result = sorted(people.values(), key=lambda p: p["name"].casefold())
    for p in result:
        p["net"] = [(c, round(v, 2)) for c, v in sorted(p["net"].items(), key=lambda kv: _currency_order(kv[0]))]
    return result


def _tab_for(row):
    if row["settled"]:
        return "kapananlar"
    return "alacaklarim" if row["direction"] == "lent" else "borclarim"


def _form_values():
    return {
        "person": form_str("person", 100),
        "direction": form_choice("direction", DIRECTIONS, "lent"),
        "amount": form_amount("amount"),
        "currency": form_choice("currency", CURRENCIES, "TRY"),
        "date": form_date("date") or today_str(),
        "due_date": form_date("due_date"),
        "note": form_str("note", 500),
    }


def _validate(v):
    if not v["person"]:
        return "Kişi adı gerekli."
    if not valid_amount(v["amount"]):
        return "Tutar 0'dan büyük bir sayı olmalı (ör. 1.500 ya da 250,50)."
    return None


@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    tab = request.args.get("sekme")
    tab = tab if tab in TABS else "alacaklarim"
    open_rows = query(
        "SELECT * FROM debts WHERE user_id = ? AND settled = 0"
        " ORDER BY due_date IS NULL, due_date, date DESC, id DESC", (uid,))
    lent = [r for r in open_rows if r["direction"] == "lent"]
    borrowed = [r for r in open_rows if r["direction"] == "borrowed"]
    settled_count = query_one("SELECT COUNT(*) AS n FROM debts WHERE user_id = ? AND settled = 1", (uid,))["n"]
    if tab == "kapananlar":
        rows = query("SELECT * FROM debts WHERE user_id = ? AND settled = 1"
                     " ORDER BY settled_at DESC, id DESC LIMIT 100", (uid,))
    else:
        rows = lent if tab == "alacaklarim" else borrowed
    persons = [r["person"] for r in query(
        "SELECT person FROM debts WHERE user_id = ? GROUP BY person ORDER BY MAX(id) DESC LIMIT 50", (uid,))]
    return render_template(
        "debts/index.html", tab=tab, tabs=TABS, rows=rows, lent=lent, borrowed=borrowed,
        settled_count=settled_count, lent_totals=currency_totals(lent),
        borrowed_totals=currency_totals(borrowed), people=person_summary(open_rows),
        persons=persons, directions=DIRECTIONS, default_direction=TAB_DIRECTION.get(tab, "lent"),
    )


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    v = _form_values()
    error = _validate(v)
    if error:
        flash(error, "error")
        return redirect_back("debts.index")
    execute(
        "INSERT INTO debts (user_id, person, direction, amount, currency, date, due_date, note)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (g.user["id"], v["person"], v["direction"], v["amount"], v["currency"], v["date"], v["due_date"],
         v["note"]),
    )
    flash("Alacak kaydedildi." if v["direction"] == "lent" else "Borç kaydedildi.", "success")
    return redirect_back("debts.index", sekme="alacaklarim" if v["direction"] == "lent" else "borclarim")


@bp.route("/<int:debt_id>", methods=["GET", "POST"])
@login_required
def edit(debt_id):
    uid = g.user["id"]
    debt = owned_or_404("debts", debt_id, uid)
    if request.method == "POST":
        v = _form_values()
        error = _validate(v)
        if error:
            flash(error, "error")
            return redirect(url_for(".edit", debt_id=debt_id))
        execute(
            "UPDATE debts SET person = ?, direction = ?, amount = ?, currency = ?, date = ?, due_date = ?, note = ?"
            " WHERE id = ? AND user_id = ?",
            (v["person"], v["direction"], v["amount"], v["currency"], v["date"], v["due_date"], v["note"],
             debt_id, uid),
        )
        flash("Kayıt güncellendi.", "success")
        return redirect(url_for(".index", sekme=_tab_for({"settled": debt["settled"], "direction": v["direction"]})))
    return render_template("debts/edit.html", debt=debt, directions=DIRECTIONS, tab=_tab_for(debt))


@bp.route("/<int:debt_id>/kapat", methods=["POST"])
@login_required
def settle(debt_id):
    uid = g.user["id"]
    debt = owned_or_404("debts", debt_id, uid)
    execute("UPDATE debts SET settled = 1, settled_at = ? WHERE id = ? AND user_id = ?",
            (today_str(), debt_id, uid))
    flash(f"{debt['person']} kaydı kapatıldı.", "success")
    return redirect_back("debts.index", sekme=_tab_for(debt))


@bp.route("/<int:debt_id>/ac", methods=["POST"])
@login_required
def reopen(debt_id):
    uid = g.user["id"]
    debt = owned_or_404("debts", debt_id, uid)
    execute("UPDATE debts SET settled = 0, settled_at = NULL WHERE id = ? AND user_id = ?", (debt_id, uid))
    flash(f"{debt['person']} kaydı yeniden açıldı.", "success")
    return redirect_back("debts.index", sekme="kapananlar")


@bp.route("/<int:debt_id>/sil", methods=["POST"])
@login_required
def delete(debt_id):
    uid = g.user["id"]
    debt = owned_or_404("debts", debt_id, uid)
    execute("DELETE FROM debts WHERE id = ? AND user_id = ?", (debt_id, uid))
    flash("Kayıt silindi.", "success")
    return redirect(url_for(".index", sekme=_tab_for(debt)))
