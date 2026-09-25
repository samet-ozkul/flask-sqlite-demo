"""🧾 Faturalar: son ödeme tarihi, her ay tekrarlanan faturalar, ödenince harcamaya ekleme."""
from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from ..auth import login_required
from ..db import execute, get_db, owned_or_404, query, query_one
from ..reminders import next_bill_date
from ..utils import (fmt_date, form_bool, form_date, form_str, month_bounds, redirect_back,
                     today, today_str)
from .expenses import form_amount, valid_amount

bp = Blueprint("bills", __name__, url_prefix="/faturalar")

TABS = {"bekleyen": "Bekleyen", "odenen": "Ödenen"}


def _form_values():
    return {
        "name": form_str("name", 100),
        "amount": form_amount("amount"),
        "amount_raw": form_str("amount", 50),
        "due_date": form_date("due_date"),
        "recurring": form_bool("recurring"),
        "note": form_str("note", 500),
    }


def _validate(v):
    if not v["name"]:
        return "Fatura adı gerekli."
    if not v["due_date"]:
        return "Son ödeme tarihi gerekli."
    if v["amount_raw"] and not valid_amount(v["amount"]):
        return "Tutar 0'dan büyük bir sayı olmalı (bilinmiyorsa boş bırakın)."
    return None


def _stats(user_id, pending):
    t = today_str()
    known = [b["amount"] for b in pending if b["amount"] is not None]
    overdue = [b for b in pending if b["due_date"] < t]
    d = today()
    start, end = month_bounds(d.year, d.month)
    paid_month = query_one(
        "SELECT COALESCE(SUM(amount), 0) AS s, COUNT(*) AS n FROM bills"
        " WHERE user_id = ? AND paid = 1 AND paid_at >= ? AND paid_at < ?",
        (user_id, start, end),
    )
    return {
        "pending_count": len(pending),
        "pending_total": round(sum(known), 2),
        "unknown": len(pending) - len(known),
        "overdue": len(overdue),
        "overdue_total": round(sum(b["amount"] or 0 for b in overdue), 2),
        "paid_month": round(paid_month["s"], 2),
        "paid_month_count": paid_month["n"],
        "paid_count": query_one("SELECT COUNT(*) AS n FROM bills WHERE user_id = ? AND paid = 1",
                                (user_id,))["n"],
    }


@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    tab = request.args.get("sekme")
    tab = tab if tab in TABS else "bekleyen"
    pending = query("SELECT * FROM bills WHERE user_id = ? AND paid = 0 ORDER BY due_date, id", (uid,))
    paid = []
    if tab == "odenen":
        paid = query("SELECT * FROM bills WHERE user_id = ? AND paid = 1"
                     " ORDER BY paid_at DESC, id DESC LIMIT 50", (uid,))
    names = [r["name"] for r in query(
        "SELECT name FROM bills WHERE user_id = ? GROUP BY name ORDER BY MAX(id) DESC LIMIT 30", (uid,))]
    return render_template("bills/index.html", tab=tab, tabs=TABS, pending=pending, paid=paid,
                           stats=_stats(uid, pending), names=names)


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    v = _form_values()
    error = _validate(v)
    if error:
        flash(error, "error")
        return redirect_back("bills.index")
    execute(
        "INSERT INTO bills (user_id, name, amount, due_date, recurring, note) VALUES (?, ?, ?, ?, ?, ?)",
        (g.user["id"], v["name"], v["amount"], v["due_date"], v["recurring"], v["note"]),
    )
    flash(f"{v['name']} faturası eklendi.", "success")
    return redirect_back("bills.index")


@bp.route("/<int:bill_id>", methods=["GET", "POST"])
@login_required
def edit(bill_id):
    uid = g.user["id"]
    bill = owned_or_404("bills", bill_id, uid)
    if request.method == "POST":
        v = _form_values()
        error = _validate(v)
        if error:
            flash(error, "error")
            return redirect(url_for(".edit", bill_id=bill_id))
        execute(
            "UPDATE bills SET name = ?, amount = ?, due_date = ?, recurring = ?, note = ? WHERE id = ? AND user_id = ?",
            (v["name"], v["amount"], v["due_date"], v["recurring"], v["note"], bill_id, uid),
        )
        flash("Fatura güncellendi.", "success")
        return redirect(url_for(".index", sekme="odenen") if bill["paid"] else url_for(".index"))
    return render_template("bills/edit.html", bill=bill)


@bp.route("/<int:bill_id>/ode", methods=["POST"])
@login_required
def pay(bill_id):
    uid = g.user["id"]
    bill = owned_or_404("bills", bill_id, uid)
    if bill["paid"]:
        flash("Bu fatura zaten ödenmiş.", "warning")
        return redirect_back("bills.index")
    t = today_str()
    db = get_db()
    db.execute("UPDATE bills SET paid = 1, paid_at = ? WHERE id = ? AND user_id = ?", (t, bill_id, uid))
    messages = [f"{bill['name']} ödendi."]
    if bill["recurring"]:
        next_due = next_bill_date(bill["due_date"])
        exists = db.execute("SELECT 1 FROM bills WHERE user_id = ? AND name = ? AND due_date = ?",
                            (uid, bill["name"], next_due)).fetchone()
        if not exists:
            db.execute(
                "INSERT INTO bills (user_id, name, amount, due_date, recurring, note) VALUES (?, ?, ?, ?, 1, ?)",
                (uid, bill["name"], bill["amount"], next_due, bill["note"]),
            )
            messages.append(f"Sonraki fatura {fmt_date(next_due)} tarihine eklendi.")
    if bill["amount"] and form_bool("add_expense"):
        db.execute(
            "INSERT INTO expenses (user_id, amount, category, note, date) VALUES (?, ?, 'Faturalar', ?, ?)",
            (uid, bill["amount"], bill["name"], t),
        )
        messages.append("Harcamalara eklendi.")
    db.commit()
    flash(" ".join(messages), "success")
    return redirect_back("bills.index")


@bp.route("/<int:bill_id>/geri-al", methods=["POST"])
@login_required
def unpay(bill_id):
    uid = g.user["id"]
    owned_or_404("bills", bill_id, uid)
    execute("UPDATE bills SET paid = 0, paid_at = NULL WHERE id = ? AND user_id = ?", (bill_id, uid))
    flash("Ödeme geri alındı. Oluşturulan sonraki fatura ve varsa harcama kaydı silinmedi.", "warning")
    return redirect_back("bills.index")


@bp.route("/<int:bill_id>/sil", methods=["POST"])
@login_required
def delete(bill_id):
    uid = g.user["id"]
    bill = owned_or_404("bills", bill_id, uid)
    execute("DELETE FROM bills WHERE id = ? AND user_id = ?", (bill_id, uid))
    flash(f"{bill['name']} faturası silindi.", "success")
    return redirect(url_for(".index", sekme="odenen") if bill["paid"] else url_for(".index"))
