"""💰 Varlıklar: nakit, döviz, altın ve diğer varlıkların güncel TL değeri, dağılımı ve 90 günlük geçmişi.

Değer hesabı:
- cash  (Nakit / mevduat): miktar zaten TL
- fx    (Döviz): miktar × canlı kur (external.rates); kur yoksa elle girilen birim fiyat
- gold  (Altın): gram × canlı gram fiyatı (GOLDAPI_KEY varsa), yoksa elle girilen gram fiyatı
- other (Diğer): adet × elle girilen birim fiyat (ör. aracın tahmini değeri, adet 1)
Fiyatı bulunamayan varlık "fiyat yok" gösterilir ve toplama katılmaz.

Diğer modüller için: total_value(user_id), record_snapshot(user_id) (cron günde bir çağırır).
"""
import math
from datetime import timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .. import external
from .. import trash
from ..auth import login_required
from ..db import execute, owned_or_404, query
from ..utils import (CURRENCIES, fmt_money, fmt_number, form_choice, form_float, form_str, parse_date,
                     today, today_str)

bp = Blueprint("assets", __name__, url_prefix="/varliklar")

KINDS = {"cash": "Nakit / mevduat", "fx": "Döviz", "gold": "Altın", "other": "Diğer"}
KIND_ICONS = {"cash": "💵", "fx": "💱", "gold": "🥇", "other": "📦"}
FX_CURRENCIES = {"USD": "$ Dolar", "EUR": "€ Euro", "GBP": "£ Sterlin"}
UNITS = {"cash": "₺", "gold": "gr", "other": "adet"}
HISTORY_DAYS = 90
MAX_NUMBER = 1e12

SOURCE_CASH = "nakit"
SOURCE_LIVE = "canlı kur"
SOURCE_MANUAL = "elle girilen fiyat"
SOURCE_MISSING = "fiyat yok"


# ---------- Değer hesabı ----------
def _prices(assets):
    """Sadece gerekenleri getirir: döviz varsa kur tablosu, altın varsa gram fiyatı."""
    kinds = {a["kind"] for a in assets}
    rate_table = external.rates() if "fx" in kinds else None
    gold = external.gold_gram_try() if "gold" in kinds else None
    return rate_table, gold


def asset_value(asset, rate_table=None, gold_price=None):
    """(TL değeri ya da None, kaynak metni)"""
    kind, qty = asset["kind"], asset["quantity"]
    if kind == "cash":
        return qty, SOURCE_CASH
    if kind == "fx":
        rate = (rate_table or {}).get(asset["currency"])
        if rate:
            return qty * rate, SOURCE_LIVE
    elif kind == "gold" and gold_price:
        return qty * gold_price, SOURCE_LIVE
    if asset["unit_price"]:
        return qty * asset["unit_price"], SOURCE_MANUAL
    return None, SOURCE_MISSING


def total_value(user_id):
    """{"total": float, "missing": int, "items": [{"asset": row, "value": float|None, "source": str}]}

    `total` sadece fiyatı bilinen varlıkların toplamıdır; `missing` fiyatı bulunamayan varlık sayısı.
    """
    assets = query("SELECT * FROM assets WHERE user_id = ? ORDER BY id", (user_id,))
    rate_table, gold = _prices(assets) if assets else (None, None)
    items, total, missing = [], 0.0, 0
    for a in assets:
        value, source = asset_value(a, rate_table, gold)
        if value is None:
            missing += 1
        else:
            total += value
        items.append({"asset": a, "value": value, "source": source})
    return {"total": round(total, 2), "missing": missing, "items": items}


def record_snapshot(user_id, result=None):
    """Bugünün toplamını asset_snapshots'a yazar (günde tek satır, üzerine yazar).

    Kullanıcının hiç varlığı yoksa ya da fiyatı bulunamayan varlık varsa yazmaz (grafik bozulmasın).
    Yazılan toplamı, yazılmadıysa None döner. `result` önceden hesaplanmış total_value() sonucudur.
    """
    result = result if result is not None else total_value(user_id)
    if not result["items"] or result["missing"]:
        return None
    execute("INSERT OR REPLACE INTO asset_snapshots (user_id, date, total_try) VALUES (?, ?, ?)",
            (user_id, today_str(), result["total"]))
    return result["total"]


# ---------- Biçimlendirme ----------
def plain_number(value, decimals=4):
    """Gereksiz sıfırsız Türkçe sayı: 1500 -> '1.500', 25.5 -> '25,5' (form değerleri için)."""
    if value is None:
        return ""
    s = fmt_number(round(value, decimals), decimals)
    if "," in s:
        s = s.rstrip("0").rstrip(",")
    return s


def unit_of(asset):
    if asset["kind"] == "fx":
        return CURRENCIES.get(asset["currency"], asset["currency"])
    return UNITS.get(asset["kind"], "")


def quantity_text(asset):
    """'1.500 $', '12.500,50 ₺', '25,5 gr', '1 adet'"""
    q = asset["quantity"]
    number = fmt_number(q) if asset["kind"] in ("cash", "fx") else plain_number(q, 3)
    return f"{number} {unit_of(asset)}"


def history_chart(points, width=600, height=140, pad=6):
    """[(tarih, değer)] -> sunucu tarafı SVG için çizgi/alan noktaları ve özet; 2 noktadan azsa None.

    Yatay eksen tarihle orantılıdır (atlanan günler boşluk olarak görünür).
    """
    points = [(parse_date(d), v) for d, v in points if parse_date(d) is not None and v is not None]
    if len(points) < 2:
        return None
    d0, d1 = points[0][0], points[-1][0]
    days = (d1 - d0).days or 1
    values = [v for _d, v in points]
    lo, hi = min(values), max(values)
    inner_w, inner_h = width - 2 * pad, height - 2 * pad

    def xy(d, v):
        x = pad + (d - d0).days / days * inner_w
        y = height / 2 if hi == lo else pad + (hi - v) / (hi - lo) * inner_h
        return f"{x:.1f},{y:.1f}"

    line = " ".join(xy(d, v) for d, v in points)
    area = f"{pad:.1f},{height:.1f} {line} {pad + inner_w:.1f},{height:.1f}"
    first, last = values[0], values[-1]
    return {
        "line": line, "area": area, "width": width, "height": height,
        "first": first, "last": last, "first_date": d0.isoformat(), "last_date": d1.isoformat(),
        "diff": last - first, "change": (last - first) * 100 / first if first else None,
        "low": lo, "high": hi, "count": len(points),
    }


# ---------- Form ----------
def _valid_number(n):
    return n is not None and math.isfinite(n) and 0 < n <= MAX_NUMBER


def _form_values():
    """(değerler, hata mesajı ya da None)"""
    kind = form_choice("kind", KINDS, "cash")
    raw_price = (request.form.get("unit_price") or "").strip()
    v = {
        "name": form_str("name", 100),
        "kind": kind,
        "currency": form_choice("currency", FX_CURRENCIES, "USD") if kind == "fx" else "TRY",
        "quantity": form_float("quantity"),
        "unit_price": form_float("unit_price") if kind != "cash" and raw_price else None,
        "note": form_str("note", 500),
    }
    if not v["name"]:
        return v, "Varlık adı gerekli."
    if not _valid_number(v["quantity"]):
        return v, "Miktar 0'dan büyük bir sayı olmalı (ör. 1.500 ya da 25,5)."
    if kind != "cash" and raw_price and not _valid_number(v["unit_price"]):
        return v, "Birim fiyat 0'dan büyük bir sayı olmalı (ör. 4.250 ya da 4.250,50)."
    return v, None


def _warn_if_unpriced(v):
    """Kaydedilen varlığın değeri hesaplanamayacaksa kullanıcıyı uyar."""
    if v["unit_price"]:
        return
    if v["kind"] == "other":
        flash("Birim fiyat girilmediği için bu varlığın değeri hesaplanamıyor; düzenleyip fiyat ekleyebilirsin.",
              "warning")
    elif v["kind"] == "gold" and not external.gold_gram_try():
        flash("Canlı altın fiyatı alınamıyor; değer için gram fiyatını elle gir.", "warning")


# ---------- Sayfalar ----------
@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    result = total_value(uid)
    total = result["total"]
    items = sorted(result["items"], key=lambda it: (it["value"] is None, -(it["value"] or 0)))

    by_kind = {}
    for it in result["items"]:
        if it["value"] is not None:
            by_kind[it["asset"]["kind"]] = by_kind.get(it["asset"]["kind"], 0) + it["value"]
    distribution = [
        (f"{KIND_ICONS[k]} {KINDS[k]}", by_kind[k],
         f"{fmt_money(by_kind[k])} · %{fmt_number(by_kind[k] * 100 / total, 1) if total else 0}")
        for k in KINDS if by_kind.get(k, 0) > 0
    ]

    # Önce bugünün kaydı, sonra grafik: sayfadaki toplam ile grafiğin son noktası aynı olsun
    record_snapshot(uid, result)
    start = (today() - timedelta(days=HISTORY_DAYS)).isoformat()
    history = query("SELECT date, total_try FROM asset_snapshots WHERE user_id = ? AND date >= ? ORDER BY date",
                    (uid, start))
    chart = history_chart([(r["date"], r["total_try"]) for r in history])

    return render_template(
        "assets/index.html", result=result, items=items, total=total, distribution=distribution,
        chart=chart, history_days=HISTORY_DAYS, kinds=KINDS, icons=KIND_ICONS, fx_currencies=FX_CURRENCIES,
        quantity_text=quantity_text, unit_of=unit_of, plain=plain_number, live=SOURCE_LIVE,
        manual=SOURCE_MANUAL,
    )


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    v, error = _form_values()
    if error:
        flash(error, "error")
        return redirect(url_for(".index"))
    execute(
        "INSERT INTO assets (user_id, name, kind, currency, quantity, unit_price, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (g.user["id"], v["name"], v["kind"], v["currency"], v["quantity"], v["unit_price"], v["note"]),
    )
    flash(f"{v['name']} eklendi.", "success")
    _warn_if_unpriced(v)
    return redirect(url_for(".index"))


@bp.route("/<int:asset_id>", methods=["GET", "POST"])
@login_required
def edit(asset_id):
    uid = g.user["id"]
    asset = owned_or_404("assets", asset_id, uid)
    if request.method == "POST":
        v, error = _form_values()
        if error:
            flash(error, "error")
            return redirect(url_for(".edit", asset_id=asset_id))
        execute(
            "UPDATE assets SET name = ?, kind = ?, currency = ?, quantity = ?, unit_price = ?, note = ?,"
            " updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            (v["name"], v["kind"], v["currency"], v["quantity"], v["unit_price"], v["note"], asset_id, uid),
        )
        flash(f"{v['name']} güncellendi.", "success")
        _warn_if_unpriced(v)
        return redirect(url_for(".index"))
    value, source = asset_value(asset, *_prices([asset]))
    return render_template(
        "assets/edit.html", asset=asset, value=value, source=source, kinds=KINDS, icons=KIND_ICONS,
        fx_currencies=FX_CURRENCIES, quantity_text=quantity_text, unit_of=unit_of, plain=plain_number,
        live=SOURCE_LIVE, manual=SOURCE_MANUAL,
    )


@bp.route("/<int:asset_id>/sil", methods=["POST"])
@login_required
def delete(asset_id):
    uid = g.user["id"]
    asset = owned_or_404("assets", asset_id, uid)
    trash.move(uid, "assets", f"💰 {asset['name']}", ("assets", asset_id))
    flash(trash.notice(asset["name"]), "success")
    return redirect(url_for(".index"))
