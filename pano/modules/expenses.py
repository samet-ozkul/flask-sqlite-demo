"""💸 Harcamalar: kasada 5 saniyede giriş, aylık özet, kategori dağılımı (sadece TL).

Diğer modüller (faturalar, araç) "Faturalar" / "Yakıt" kategorisiyle doğrudan
expenses tablosuna satır ekler; bu yüzden CATEGORIES sabit tutulur.
"""
import calendar
import math
from datetime import date, timedelta
from itertools import groupby

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from ..auth import login_required
from ..db import execute, owned_or_404, query, query_one
from ..utils import (add_months, fmt_money, form_date, form_str, month_bounds, parse_number,
                     redirect_back, today, today_str)

bp = Blueprint("expenses", __name__, url_prefix="/harcamalar")

CATEGORIES = ["Market", "Yemek", "Ulaşım", "Yakıt", "Faturalar", "Sağlık", "Giyim", "Ev",
              "Eğlence", "Eğitim", "Hediye", "Diğer"]
CATEGORY_ICONS = {
    "Market": "🛒", "Yemek": "🍽️", "Ulaşım": "🚌", "Yakıt": "⛽", "Faturalar": "🧾", "Sağlık": "💊",
    "Giyim": "👕", "Ev": "🏠", "Eğlence": "🎬", "Eğitim": "📚", "Hediye": "🎁", "Diğer": "📌",
}
DEFAULT_CATEGORY = "Diğer"
MAX_AMOUNT = 1e10
def parse_amount(value):
    """Türkçe tutar ("1.234,56", "1.000") -> float. Binlik ayırıcı işi utils.parse_number'da."""
    return parse_number(value)


def form_amount(name):
    return parse_amount(request.form.get(name))


def valid_amount(value):
    """Zorunlu tutar kontrolü: sayı, sonlu ve 0'dan büyük (para modülleri ortak kullanır)."""
    return value is not None and math.isfinite(value) and 0 < value < MAX_AMOUNT


def category_icon(name):
    return CATEGORY_ICONS.get(name, "🏷️")


def normalize_category(value):
    """Boşsa 'Diğer'; sabit kategorilerden biriyse onun yazımı ('market' -> 'Market')."""
    value = (value or "").strip()[:40]
    if not value:
        return DEFAULT_CATEGORY
    for c in CATEGORIES:
        if c.casefold() == value.casefold():
            return c
    return value


def parse_month(value):
    """'YYYY-MM' -> (yıl, ay); geçersiz/boşsa bu ay."""
    try:
        y, m = (int(p) for p in (value or "").split("-"))
        if 2000 <= y <= 2100 and 1 <= m <= 12:
            return y, m
    except ValueError:
        pass
    t = today()
    return t.year, t.month


def month_key(d):
    return f"{d.year:04d}-{d.month:02d}"


def month_summary(user_id, year, month):
    """Ayın harcamaları, toplamı, günlük ortalaması, önceki aya göre değişimi ve kategori dağılımı."""
    start, end = month_bounds(year, month)
    rows = query(
        "SELECT * FROM expenses WHERE user_id = ? AND date >= ? AND date < ? ORDER BY date DESC, id DESC",
        (user_id, start, end),
    )
    total = round(sum(r["amount"] for r in rows), 2)

    prev = add_months(date(year, month, 1), -1)
    p_start, p_end = month_bounds(prev.year, prev.month)
    prev_total = round(query_one(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM expenses WHERE user_id = ? AND date >= ? AND date < ?",
        (user_id, p_start, p_end),
    )["s"], 2)

    t = today()
    days = t.day if (year, month) == (t.year, t.month) else calendar.monthrange(year, month)[1]

    per_cat = {}
    for r in rows:
        per_cat[r["category"]] = per_cat.get(r["category"], 0) + r["amount"]
    categories = sorted(((c, round(v, 2)) for c, v in per_cat.items()), key=lambda kv: (-kv[1], kv[0]))

    days_list = []
    for d, group in groupby(rows, key=lambda r: r["date"]):
        items = list(group)
        days_list.append({"date": d, "entries": items, "total": round(sum(i["amount"] for i in items), 2)})

    return {
        "rows": rows,
        "count": len(rows),
        "total": total,
        "prev_total": prev_total,
        "change": (total - prev_total) * 100 / prev_total if prev_total else None,
        "days": days,
        "daily_avg": total / days if days else 0,
        "categories": categories,
        "days_list": days_list,
    }


def _chip_categories(user_id):
    """Son 90 günde en çok kullanılanlar önde, sonra kalan sabit kategoriler."""
    since = (today() - timedelta(days=90)).isoformat()
    used = [r["category"] for r in query(
        "SELECT category, COUNT(*) AS n FROM expenses WHERE user_id = ? AND date >= ?"
        " GROUP BY category ORDER BY n DESC, category LIMIT 16",
        (user_id, since),
    )]
    return used + [c for c in CATEGORIES if c not in used]


def _all_categories(user_id):
    custom = [r["category"] for r in query(
        "SELECT DISTINCT category FROM expenses WHERE user_id = ? ORDER BY category", (user_id,)
    ) if r["category"] not in CATEGORIES]
    return CATEGORIES + custom


def _month_args(iso_date):
    """Kayıt başka bir aya aitse listeyi o ayda aç."""
    key = iso_date[:7]
    return {} if key == today_str()[:7] else {"ay": key}


@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    year, month = parse_month(request.args.get("ay"))
    summary = month_summary(uid, year, month)
    first = date(year, month, 1)
    prev, nxt = add_months(first, -1), add_months(first, 1)
    t = today()
    is_current = (year, month) == (t.year, t.month)
    today_total = None
    if is_current:
        today_total = query_one(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM expenses WHERE user_id = ? AND date = ?",
            (uid, t.isoformat()),
        )["s"]
    total = summary["total"]
    bars = [
        (f"{category_icon(c)} {c}", v, f"{fmt_money(v)} · %{round(v * 100 / total) if total else 0}")
        for c, v in summary["categories"]
    ]
    return render_template(
        "expenses/index.html",
        s=summary, bars=bars, year=year, month=month, prev=prev, nxt=nxt,
        prev_key=month_key(prev), next_key=month_key(nxt), is_current=is_current,
        today_total=today_total, chips=_chip_categories(uid), categories=_all_categories(uid),
        icon=category_icon,
    )


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    amount = form_amount("amount")
    if not valid_amount(amount):
        flash("Geçerli bir tutar girin (ör. 125 ya da 1.234,56).", "error")
        return redirect_back("expenses.index")
    # Hızlı formdaki "yeni kategori" kutusu seçili çipin önüne geçer
    category = normalize_category(form_str("category_new", 40) or form_str("category", 40))
    day = form_date("date") or today_str()
    execute(
        "INSERT INTO expenses (user_id, amount, category, note, date) VALUES (?, ?, ?, ?, ?)",
        (g.user["id"], amount, category, form_str("note", 200), day),
    )
    flash(f"{fmt_money(amount)} · {category} eklendi.", "success")
    return redirect_back("expenses.index", **_month_args(day))


@bp.route("/<int:expense_id>", methods=["GET", "POST"])
@login_required
def edit(expense_id):
    uid = g.user["id"]
    row = owned_or_404("expenses", expense_id, uid)
    if request.method == "POST":
        amount = form_amount("amount")
        if not valid_amount(amount):
            flash("Geçerli bir tutar girin (ör. 125 ya da 1.234,56).", "error")
            return redirect(url_for(".edit", expense_id=expense_id))
        day = form_date("date") or row["date"]
        execute(
            "UPDATE expenses SET amount = ?, category = ?, note = ?, date = ? WHERE id = ? AND user_id = ?",
            (amount, normalize_category(form_str("category", 40)), form_str("note", 200), day, expense_id, uid),
        )
        flash("Harcama güncellendi.", "success")
        return redirect(url_for(".index", ay=day[:7]))
    return render_template("expenses/edit.html", e=row, categories=_all_categories(uid))


@bp.route("/<int:expense_id>/sil", methods=["POST"])
@login_required
def delete(expense_id):
    row = owned_or_404("expenses", expense_id, g.user["id"])
    execute("DELETE FROM expenses WHERE id = ? AND user_id = ?", (expense_id, g.user["id"]))
    flash("Harcama silindi.", "success")
    return redirect(url_for(".index", ay=row["date"][:7]))
