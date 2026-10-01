"""🚚 Siparişler: internetten verilen siparişler, kargo takibi ve iade süresi.

- Durum: sipariş verildi → kargoda → teslim alındı → iade sürecinde → iade edildi (ya da iptal)
- Teslim alınınca iade son günü hesaplanır: teslim + iade süresi (Türkiye'de cayma hakkı 14 gün,
  mağaza farklı verebilir; 0 = iade takibi yok). Teslim tarihi düzeltilince yeniden hesaplanır.
- Kargo takip butonu: girilmiş takip linki; yoksa takip numarasıyla Google araması
  (firmaların takip adresleri değişebildiği için uydurulmaz).
- Telegram (cron /hatirlatma, 09:00 sonrası): beklenen teslim günü, iade için son 2 gün ve son gün.
  Her olay o tarih için bir kez gönderilir (sent_flags); tarih değişince yeni tarihe göre kurulur.
"""
from datetime import date, timedelta
from urllib.parse import urlencode

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .. import automation, trash
from ..auth import login_required
from ..db import execute, owned_or_404, query, query_one
from ..utils import (fmt_date, fmt_money, fold, form_bool, form_choice, form_date, form_int, form_str, parse_date,
                     parse_number, redirect_back, today, today_str)
from .expenses import CATEGORIES, DEFAULT_CATEGORY, category_icon, valid_amount
from .links import valid_url

bp = Blueprint("orders", __name__, url_prefix="/siparisler")

STATUSES = {
    "ordered": ("Sipariş verildi", "🛒"),
    "shipped": ("Kargoda", "🚚"),
    "delivered": ("Teslim alındı", "📦"),
    "returning": ("İade sürecinde", "↩️"),
    "returned": ("İade edildi", "✅"),
    "cancelled": ("İptal edildi", "✖️"),
}
WAITING = ("ordered", "shipped")
DELIVERED = ("delivered", "returning", "returned")  # teslim tarihi olan durumlar
# Durum butonları: yeni durum -> (etiket, hangi durumlardayken basılabilir)
ACTIONS = {
    "shipped": ("🚚 Kargoya verildi", ("ordered",)),
    "delivered": ("📦 Teslim aldım", WAITING),
    "returning": ("↩️ İade başlattım", ("delivered",)),
    "returned": ("✅ İade tamamlandı", ("returning",)),
    "cancelled": ("✖️ İptal", WAITING),
}
TABS = {"bekleyen": "Bekleyenler", "teslim": "Teslim alınanlar", "iade": "İadeler", "tumu": "Tümü"}
STORES = ["Trendyol", "Hepsiburada", "Amazon", "n11", "Getir", "Yemeksepeti", "Çiçeksepeti", "Diğer"]
CARRIERS = ["Yurtiçi", "Aras", "MNG", "PTT", "Sürat", "Trendyol Express", "HepsiJET", "UPS", "DHL"]
DEFAULT_RETURN_DAYS = 14  # mesafeli satışta cayma hakkı
MAX_RETURN_DAYS = 365
URL_MAX = 2000
# Formdan kaydedilen sütunlar (INSERT/UPDATE aynı sırayı kullanır)
FIELDS = ("store", "item", "amount", "ordered_on", "expected_on", "delivered_on", "carrier", "tracking_no",
          "tracking_url", "order_url", "status", "return_days", "return_by", "note")


def return_by(delivered_on, return_days):
    """İade son günü (teslim + süre); teslim alınmadıysa ya da süre 0 ise None."""
    d = parse_date(delivered_on)
    return (d + timedelta(days=return_days)).isoformat() if d and return_days else None


def return_left(order, t=None):
    """İade için kalan gün (son gün bugünse 0, geçtiyse eksi); teslim alınmış durumda değilse None."""
    if order["status"] != "delivered" or not order["return_by"]:
        return None
    return (date.fromisoformat(order["return_by"]) - (t or today())).days


def returnable(order, t=None):
    left = return_left(order, t)
    return left is not None and left >= 0


def tracking_link(order):
    """Kargo takip adresi: girilmiş link; yoksa takip numarasıyla Google araması; ikisi de yoksa None."""
    if order["tracking_url"]:
        return order["tracking_url"]
    if not order["tracking_no"]:
        return None
    words = [order["carrier"]] if order["carrier"] else []
    if "kargo" not in fold(order["carrier"]):
        words.append("kargo")
    return "https://www.google.com/search?" + urlencode({"q": " ".join(words + ["takip", order["tracking_no"]])})


def actions_for(order):
    """[(yeni durum, etiket)] bu durumda basılabilecek butonlar."""
    return [(s, label) for s, (label, allowed) in ACTIONS.items() if order["status"] in allowed]


def quick_actions(order, t=None):
    """Listedeki kısa yol butonları: para işi olmayan adımlar; iade başlatma sadece süre içindeyse."""
    return [(s, label) for s, label in actions_for(order)
            if s in ("shipped", "delivered") or (s == "returning" and returnable(order, t))]


def label_of(order):
    return " · ".join(x for x in (order["store"], order["item"]) if x)


def _tab_of(status):
    if status in WAITING:
        return "bekleyen"
    return {"delivered": "teslim", "returning": "iade", "returned": "iade"}.get(status, "tumu")


def _linked_expense(order):
    """Harcamalara eklenen kayıt (silinmişse None)."""
    if not order["expense_id"]:
        return None
    return query_one("SELECT * FROM expenses WHERE id = ? AND user_id = ?", (order["expense_id"], order["user_id"]))


def _form_values(order=None):
    """(değerler, hata). Teslim alınmış bir durumdaysa teslim tarihi (boşsa bugün) ve iade son günü hesaplanır."""
    raw_amount = form_str("amount", 30)
    days = form_int("return_days")
    v = {
        "store": form_str("store", 60),
        "item": form_str("item", 200),
        "amount": parse_number(raw_amount),
        "ordered_on": form_date("ordered_on") or (order["ordered_on"] if order else today_str()),
        "expected_on": form_date("expected_on"),
        "carrier": form_str("carrier", 40),
        "tracking_no": form_str("tracking_no", 60),
        "tracking_url": form_str("tracking_url", URL_MAX),
        "order_url": form_str("order_url", URL_MAX),
        "status": form_choice("status", STATUSES, order["status"] if order else "ordered"),
        "return_days": DEFAULT_RETURN_DAYS if days is None else days,
        "note": form_str("note", 1000),
        "delivered_on": None,
    }
    if v["status"] in DELIVERED:
        v["delivered_on"] = form_date("delivered_on") or (order["delivered_on"] if order else None) or today_str()
    v["return_by"] = return_by(v["delivered_on"], v["return_days"])
    return v, _validate(v, raw_amount)


def _validate(v, raw_amount):
    if not v["item"]:
        return "Ürün adı gerekli."
    if raw_amount and not valid_amount(v["amount"]):
        return "Geçerli bir tutar girin (ör. 349,90) ya da boş bırakın."
    if not 0 <= v["return_days"] <= MAX_RETURN_DAYS:
        return f"İade süresi 0-{MAX_RETURN_DAYS} gün arasında olmalı."
    for field, label in (("tracking_url", "Takip linki"), ("order_url", "Sipariş sayfası linki")):
        if v[field] and not valid_url(v[field]):
            return f"{label} http:// veya https:// ile başlamalı."
    if v["expected_on"] and v["expected_on"] < v["ordered_on"]:
        return "Beklenen teslim, sipariş tarihinden önce olamaz."
    if v["delivered_on"] and v["delivered_on"] < v["ordered_on"]:
        return "Teslim tarihi, sipariş tarihinden önce olamaz."
    if v["delivered_on"] and v["delivered_on"] > today_str():
        return "Teslim tarihi ileri bir gün olamaz."
    return None


def _add_expense(uid, v):
    """Siparişi sipariş tarihiyle harcamalara ekler; harcamanın id'sini döner."""
    category = form_choice("expense_category", CATEGORIES, DEFAULT_CATEGORY)
    note = label_of(v)[:200]
    expense_id = execute("INSERT INTO expenses (user_id, amount, category, note, date) VALUES (?, ?, ?, ?, ?)",
                         (uid, v["amount"], category, note, v["ordered_on"])).lastrowid
    automation.fire("expense_added", uid, **{"tutar": v["amount"], "kategori": category, "not": note})
    return expense_id


def _form_context(uid):
    """Ekleme/düzenleme formunun önerileri: sabit mağazalar + daha önce yazılanlar."""
    used = [r["store"] for r in query("SELECT DISTINCT store FROM orders WHERE user_id = ? AND store != ''"
                                      " ORDER BY store", (uid,))]
    return {"stores": STORES + [s for s in used if s not in STORES], "carriers": CARRIERS, "statuses": STATUSES,
            "categories": CATEGORIES, "category_icon": category_icon, "default_return_days": DEFAULT_RETURN_DAYS}


@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    tab = request.args.get("sekme", "bekleyen")
    if tab not in TABS:
        tab = "bekleyen"
    rows = query("SELECT * FROM orders WHERE user_id = ? ORDER BY ordered_on DESC, id DESC", (uid,))
    t = today()
    waiting = sorted((r for r in rows if r["status"] in WAITING),
                     key=lambda r: (r["expected_on"] is None, r["expected_on"] or "", r["ordered_on"]))
    delivered = [r for r in rows if r["status"] == "delivered"]
    # İade süresi devam edenler (son günü en yakın olan) üstte, sonra en son teslim alınanlar
    open_returns = sorted((r for r in delivered if returnable(r, t)), key=lambda r: r["return_by"])
    closed = sorted((r for r in delivered if not returnable(r, t)), key=lambda r: r["delivered_on"] or "", reverse=True)
    groups = {
        "bekleyen": waiting,
        "teslim": open_returns + closed,
        "iade": sorted((r for r in rows if r["status"] in ("returning", "returned")), key=lambda r: r["status"] != "returning"),
        "tumu": rows,
    }
    month = t.isoformat()[:7]
    summary = {
        "waiting": len(waiting),
        "returnable": len(open_returns),
        "month_total": round(sum(r["amount"] or 0 for r in rows if r["ordered_on"][:7] == month
                                 and r["status"] not in ("returned", "cancelled")), 2),
    }
    return render_template(
        "orders/index.html", tab=tab, items=groups[tab], counts={k: len(v) for k, v in groups.items()},
        total=len(rows), summary=summary, TABS=TABS, today=t, quick_actions=quick_actions, tracking_link=tracking_link,
        return_left=return_left, **_form_context(uid),
    )


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    uid = g.user["id"]
    v, error = _form_values()
    if error:
        flash(error, "error")
        return redirect(url_for(".index"))
    expense_id = None
    if form_bool("add_expense"):
        if v["amount"]:
            expense_id = _add_expense(uid, v)
        else:
            flash("Tutar girilmediği için harcamalara eklenmedi.", "warning")
    execute(f"INSERT INTO orders (user_id, expense_id, {', '.join(FIELDS)}) VALUES ({', '.join('?' * (len(FIELDS) + 2))})",
            (uid, expense_id, *(v[f] for f in FIELDS)))
    flash(f"🚚 {v['item']} eklendi" + (" ve harcamalara işlendi." if expense_id else "."), "success")
    return redirect(url_for(".index", sekme=_tab_of(v["status"])))


@bp.route("/<int:order_id>", methods=["GET", "POST"])
@login_required
def edit(order_id):
    uid = g.user["id"]
    order = owned_or_404("orders", order_id, uid)
    expense = _linked_expense(order)
    if request.method == "POST":
        v, error = _form_values(order)
        if error:
            flash(error, "error")
            return redirect(url_for(".edit", order_id=order_id))
        expense_id = expense["id"] if expense else None
        if form_bool("add_expense") and not expense:
            if v["amount"]:
                expense_id = _add_expense(uid, v)
            else:
                flash("Tutar girilmediği için harcamalara eklenmedi.", "warning")
        execute(f"UPDATE orders SET {', '.join(f + ' = ?' for f in FIELDS)}, expense_id = ? WHERE id = ? AND user_id = ?",
                (*(v[f] for f in FIELDS), expense_id, order_id, uid))
        added = expense_id and not expense
        flash("Sipariş güncellendi" + (" ve harcamalara işlendi." if added else "."), "success")
        return redirect(url_for(".edit", order_id=order_id))
    return render_template(
        "orders/edit.html", o=order, expense=expense, actions=actions_for(order), track=tracking_link(order),
        left=return_left(order), back_tab=_tab_of(order["status"]), **_form_context(uid),
    )


@bp.route("/<int:order_id>/durum", methods=["POST"])
@login_required
def set_status(order_id):
    uid = g.user["id"]
    order = owned_or_404("orders", order_id, uid)
    status = request.form.get("status")
    if status not in ACTIONS or order["status"] not in ACTIONS[status][1]:
        flash("Bu sipariş için bu adım uygun değil.", "warning")
        return redirect_back("orders.edit", order_id=order_id)
    item = order["item"]
    if status == "delivered":
        t = today_str()
        by = return_by(t, order["return_days"])
        execute("UPDATE orders SET status = ?, delivered_on = ?, return_by = ? WHERE id = ? AND user_id = ?",
                (status, t, by, order_id, uid))
        text = f"📦 {item} teslim alındı." + (f" İade için son gün: {fmt_date(by, True)}." if by else "")
    else:
        execute("UPDATE orders SET status = ? WHERE id = ? AND user_id = ?", (status, order_id, uid))
        text = {"shipped": f"🚚 {item} kargoya verildi.", "returning": f"↩️ {item} için iade başlatıldı.",
                "returned": f"✅ {item} iadesi tamamlandı.", "cancelled": f"✖️ {item} iptal edildi."}[status]
        # Para geri geldiyse harcama kaydı da kaldırılabilir (çöp kutusundan geri gelir)
        expense = _linked_expense(order) if status in ("returned", "cancelled") and form_bool("drop_expense") else None
        if expense:
            trash.move(uid, "expenses", f"💸 {expense['note'] or expense['category']} · {fmt_money(expense['amount'])}",
                       ("expenses", expense["id"]))
            execute("UPDATE orders SET expense_id = NULL WHERE id = ?", (order_id,))
            text += " Harcama kaydı çöp kutusuna taşındı."
    flash(text, "success")
    return redirect_back("orders.edit", order_id=order_id)


@bp.route("/<int:order_id>/sil", methods=["POST"])
@login_required
def delete(order_id):
    order = owned_or_404("orders", order_id, g.user["id"])
    trash.move(g.user["id"], "orders", f"🚚 {label_of(order)}", ("orders", order_id))
    flash(trash.notice(order["item"]), "success")
    return redirect(url_for(".index", sekme=_tab_of(order["status"])))


# ---------- Telegram hatırlatması (cron /hatirlatma) ----------
def _flags(value):
    """'delivery:2026-10-03,return2:2026-10-15' -> {'delivery': '2026-10-03', 'return2': '2026-10-15'}"""
    return dict(p.split(":", 1) for p in (value or "").split(",") if ":" in p)


def pending(now):
    """[(tür, sipariş, tarih)] — 'delivery' (bugün gelmesi bekleniyor), 'return2' (iade için son 2 gün),
    'return0' (iade için son gün). Her tür o tarih için bir kez gönderilir; iade edilen/iptal olanlara gönderilmez."""
    t = now.date()
    ts = t.isoformat()
    out = []
    for o in query("SELECT o.*, u.telegram_chat_id AS chat_id FROM orders o JOIN users u ON u.id = o.user_id"
                   " WHERE u.telegram_chat_id IS NOT NULL AND ((o.status IN ('ordered', 'shipped') AND o.expected_on = ?)"
                   " OR (o.status = 'delivered' AND o.return_by BETWEEN ? AND ?))",
                   (ts, ts, (t + timedelta(days=2)).isoformat())):
        sent = _flags(o["sent_flags"])
        if o["status"] == "delivered":
            kind = {2: "return2", 0: "return0"}.get(return_left(o, t))
            if kind and sent.get(kind) != o["return_by"]:
                out.append((kind, o, o["return_by"]))
        elif sent.get("delivery") != o["expected_on"]:
            out.append(("delivery", o, o["expected_on"]))
    return out


def mark_sent(order, kind, on):
    flags = _flags(order["sent_flags"])
    flags[kind] = on
    execute("UPDATE orders SET sent_flags = ? WHERE id = ?",
            (",".join(f"{k}:{v}" for k, v in flags.items()), order["id"]))


def message(kind, order, url, escape):
    store = f" ({escape(order['store'])})" if order["store"] else ""
    if kind == "delivery":
        lines = [f"📦 <b>Bugün gelmesi bekleniyor:</b> {escape(order['item'])}{store}"]
        cargo = " · ".join(x for x in (order["carrier"], order["tracking_no"]) if x)
        if cargo:
            lines.append(f"🚚 {escape(cargo)}")
        link = tracking_link(order)
        if link:
            lines.append(f'<a href="{escape(link)}">Kargo takip →</a>')
    else:
        head = "Bugün iade için son gün:" if kind == "return0" else "İade için son 2 gün:"
        lines = [f"↩️ <b>{head}</b> {escape(order['item'])}{store}",
                 f"Son gün: {fmt_date(order['return_by'], True)} · teslim {fmt_date(order['delivered_on'])}"]
        if order["order_url"]:
            lines.append(f'<a href="{escape(order["order_url"])}">Sipariş sayfası →</a>')
    if order["note"]:
        lines.append(f"<i>{escape(order['note'])}</i>")
    lines.append(f'<a href="{escape(url)}">Siparişler →</a>')
    return "\n".join(lines)
