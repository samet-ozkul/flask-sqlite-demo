"""💳 Taksitler: kredi kartı ekstre günleri ve taksitli alışveriş takibi.

- Gizlilik: kart sadece kullanıcının verdiği adla tutulur ("Bonus", "World"); kart numarası, son 4 hane, CVV, son
  kullanma tarihi istenmez ve saklanmaz (adda uzun rakam dizisi reddedilir).
- Ekstre dönemi 'YYYY-MM': o ayın hesap kesim günü (ayda o gün yoksa ayın son günü; 31'i kesimli kartın Şubat
  ekstresi 28/29'unda kesilir) ekstrenin kesim tarihi, son ödeme = kesim + due_days. Kesim günü dahil o güne kadarki
  alışveriş o ekstreye, sonraki günlerdeki bir sonrakine düşer (Türkiye'de bankalar kesim gününü döneme dahil sayar;
  takas gecikmesiyle sonraki ekstreye kayarsa "ilk taksit" elle değiştirilebilir, ertelemeli kampanyalar için de).
- Taksit = toplam / taksit sayısı, kuruşa aşağı yuvarlanır; artan kuruşlar son taksite eklenir (toplam birebir tutar).
  Taksitler satır satır saklanmaz, alışverişten hesaplanır; ekstre tutarı o döneme düşen taksitlerin toplamı.
- Erken kapama: kapatma gününün ekstresine (ilk ekstreden önce olamaz) kalan taksitlerin hepsi tek seferde eklenir.
- "✅ Ödendi" card_statements'ta (tarih ve tutar). Kart eklenmeden önce son ödemesi geçmiş ekstreler ödenmiş sayılır:
  geçmişte başlamış bir taksit girilince eski taksitler borç ve gecikme görünmez.
- Harcamalara işleme: 'full' alış gününe toplam tutar (alışveriş düzenlenince güncellenir), 'monthly' ekstre ödenince
  o ekstredeki taksitler ödeme gününe (ödeme geri alınınca silinir), 'none' işlenmez.
- Telegram (cron /hatirlatma, 09:00 sonrası): ödenmemiş ekstre için son ödemeden remind_days gün önce ve son gün birer
  kez (reminded_for 'tür:son ödeme'); "✅ Ödendi" butonu ekstreyi toplam tutarla kapatır. Kullanılmayan (pasif) kart
  hatırlatılmaz, yaklaşanlarda ve takvimde görünmez; kalan borcu toplamda sayılır.
"""
import calendar
import re
from datetime import date, timedelta

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from .. import automation, trash
from ..auth import login_required
from ..db import execute, get_db, owned_or_404, query, query_one
from ..utils import (MONTHS_TR, MONTHS_TR_SHORT, fmt_date, fmt_money, form_choice, form_date, form_int, form_str,
                     local_dt, parse_date, redirect_back, today, today_str)
from .expenses import CATEGORIES as EXPENSE_CATEGORIES, category_icon, form_amount, valid_amount

bp = Blueprint("installments", __name__, url_prefix="/taksitler")

COLORS = {
    "blue": ("Mavi", "🔵"), "green": ("Yeşil", "🟢"), "purple": ("Mor", "🟣"), "orange": ("Turuncu", "🟠"),
    "red": ("Kırmızı", "🔴"), "yellow": ("Sarı", "🟡"), "brown": ("Kahverengi", "🟤"), "black": ("Siyah", "⚫"),
}
COUNT_OPTIONS = [1, 2, 3, 4, 5, 6, 9, 12, 18, 24, 36]
MAX_COUNT = 36
DEFAULT_DUE_DAYS = 10
MAX_DUE_DAYS = 28     # en kısa ay kadar: bir ekstrenin son ödemesi sonraki ekstrenin kesimini geçmez
REMIND_OPTIONS = [(0, "Sadece son gün"), (1, "1 gün önce"), (2, "2 gün önce"), (3, "3 gün önce"), (5, "5 gün önce"),
                  (7, "1 hafta önce")]
DEFAULT_REMIND = 3
EXPENSE_MODES = {"full": "Tamamı alış gününde", "monthly": "Her taksit, ödendiği ay", "none": "Harcamalara işleme"}
DEFAULT_CATEGORY = "Diğer"
MAX_DEFER = 3         # ilk taksit, alış gününün ekstresinden en fazla bu kadar ay sonraya (ertelemeli kampanya)
CHART_MONTHS = 12
LATE_DAYS = 30        # yaklaşanlarda geciken ekstre en fazla bu kadar gün görünür
PAST_SHOWN = 24       # kart sayfasında gösterilen en fazla geçmiş ekstre
NAME_MAX, TITLE_MAX, NOTE_MAX = 40, 100, 500
MAX_ID = 2 ** 62      # formdan / Telegram'dan gelen id'ler (SQLite tam sayısını taşırmasın)
PAID_STATES = ("paid", "assumed")
STATES = {"paid": ("✅ Ödendi", "good"), "assumed": ("✅ Ödenmiş sayılır", "good"), "late": ("⚠️ Gecikti", "overdue"),
          "due": ("📄 Ödeme bekliyor", "soon"), "open": ("🛒 Dönem açık", "later"), "future": ("⏳ Bekliyor", "later"),
          "empty": ("Taksit yok", "later")}
CARD_NUMBER_RE = re.compile(r"\d")


# ---------- Dönem ----------
def period_of(d):
    return f"{d.year:04d}-{d.month:02d}"


def parse_period(value):
    """'YYYY-MM' -> (yıl, ay); geçersizse None."""
    value = value or ""
    if len(value) != 7 or value[4] != "-" or not (value[:4] + value[5:]).isdigit():
        return None
    y, m = int(value[:4]), int(value[5:])
    return (y, m) if 2000 <= y <= 2100 and 1 <= m <= 12 else None


def shift(period, months):
    """'2026-11' + 2 -> '2027-01'."""
    y, m = parse_period(period)
    n = y * 12 + m - 1 + months
    return f"{n // 12:04d}-{n % 12 + 1:02d}"


def period_label(period, short=False):
    """'2026-10' -> 'Ekim 2026' (short: 'Eki 2026')."""
    y, m = parse_period(period)
    return f"{(MONTHS_TR_SHORT if short else MONTHS_TR)[m - 1]} {y}"


def statement_on(card, period):
    """Ekstrenin kesim tarihi: dönem ayının kesim günü; ayda o gün yoksa ayın son günü."""
    y, m = parse_period(period)
    return date(y, m, min(card["statement_day"], calendar.monthrange(y, m)[1]))


def due_on(card, period):
    return statement_on(card, period) + timedelta(days=card["due_days"])


def period_for(card, d):
    """Alışverişin düştüğü ekstre: kesim günü dahil o ayın ekstresi, kesimden sonraki günlerde bir sonraki ay."""
    p = period_of(d)
    return p if d <= statement_on(card, p) else shift(p, 1)


def day_month(d):
    """15 Ekim"""
    return f"{d.day} {MONTHS_TR[d.month - 1]}"


def left_words(days):
    """Son ödemeye: 0 -> 'bugün', 1 -> 'yarın', 5 -> '5 gün', -3 -> '3 gün gecikti'."""
    if days < 0:
        return f"{-days} gün gecikti"
    return {0: "bugün", 1: "yarın"}.get(days, f"{days} gün")


# ---------- Taksitler ----------
def split_cents(total, count):
    """Taksitler kuruş olarak: toplam / taksit sayısı aşağı yuvarlanır, artan kuruşlar son taksite eklenir."""
    cents = round(total * 100)
    each = cents // count
    return [each] * (count - 1) + [cents - each * (count - 1)]


def split(total, count):
    """1000 / 3 -> [333.33, 333.33, 333.34] (toplam birebir tutar)."""
    return [c / 100 for c in split_cents(total, count)]


def schedule(purchase, card):
    """Alışverişin ekstrelere dağılımı: [{'period', 'amount', 'nos': (ilk, son taksit)}]. i. taksit ilk ekstreden
    i-1 ay sonraki ekstrede; erken kapandıysa kapatma gününün ekstresinden itibaren kalanlar orada tek satır."""
    first = purchase["first_statement"]
    closed = parse_date(purchase["closed_early_on"])
    target = max(period_for(card, closed), first) if closed else None
    out = []
    for i, cents in enumerate(split_cents(purchase["total"], purchase["count"]), start=1):
        period = shift(first, i - 1)
        if target and period >= target:
            if out and out[-1]["period"] == target:
                out[-1]["cents"] += cents
                out[-1]["nos"] = (out[-1]["nos"][0], i)
                continue
            period = target
        out.append({"period": period, "cents": cents, "nos": (i, i)})
    for e in out:
        e["amount"] = e.pop("cents") / 100
    return out


def entry_label(entry, count):
    """'Tek çekim', 'Taksit 3/12', 'Taksit 5–12/12 (erken kapama)'."""
    a, b = entry["nos"]
    if count == 1:
        return "Tek çekim"
    if a == b:
        return f"Taksit {a}/{count}"
    return f"Taksit {a}–{b}/{count} (erken kapama)"


def count_text(count):
    return "Tek çekim" if count == 1 else f"{count} taksit"


def color_icon(color):
    return COLORS[color][1] if color in COLORS else "💳"


# ---------- Hesap (kartlar + alışverişler + ekstre kayıtları) ----------
def load(user_id):
    """{'cards': {id: kart}, 'purchases': [...], 'rows': {(kart, dönem): ekstre kaydı},
    'plans': {alışveriş: schedule}, 'lines': {(kart, dönem): [(alışveriş, taksit)]}}"""
    cards = {c["id"]: c for c in query("SELECT * FROM credit_cards WHERE user_id = ?"
                                       " ORDER BY active DESC, name COLLATE NOCASE, id", (user_id,))}
    purchases = [p for p in query("SELECT * FROM card_purchases WHERE user_id = ? ORDER BY purchased_on, id", (user_id,))
                 if p["card_id"] in cards]
    rows = {(r["card_id"], r["period"]): r for r in query("SELECT * FROM card_statements WHERE user_id = ?", (user_id,))}
    plans, lines = {}, {}
    for p in purchases:
        plans[p["id"]] = schedule(p, cards[p["card_id"]])
        for e in plans[p["id"]]:
            lines.setdefault((p["card_id"], e["period"]), []).append((p, e))
    return {"cards": cards, "purchases": purchases, "rows": rows, "plans": plans, "lines": lines}


def tracked_from(card):
    """Kartın eklendiği gün (yerel, ISO): bundan önce son ödemesi geçmiş ekstreler ödenmiş sayılır."""
    return local_dt(card["created_at"], "%Y-%m-%d")


def state_of(book, card, period, t):
    """'paid' / 'assumed' (kart eklenmeden önce) / 'late' (son ödeme geçti) / 'due' (kesildi, ödeme bekliyor) /
    'open' (dönem sürüyor: kesim günü dahil alışveriş eklenir) / 'future' (sonraki dönemler)."""
    row = book["rows"].get((card["id"], period))
    due = due_on(card, period)
    if row and row["paid_on"]:
        return "paid"
    if due.isoformat() < tracked_from(card):
        return "assumed"
    if due < t:
        return "late"
    if statement_on(card, period) < t:
        return "due"
    return "open" if period == period_for(card, t) else "future"


def statement(book, card, period, t=None):
    """Bir ekstre: kesim ve son ödeme tarihi, taksitleri, toplamı ve durumu (tutarı yoksa kesilmiş ekstre 'empty')."""
    t = t or today()
    entries = book["lines"].get((card["id"], period), [])
    amount = round(sum(e["amount"] for _p, e in entries), 2)
    state = state_of(book, card, period, t)
    if not amount and state in ("late", "due"):
        state = "empty"
    cut = statement_on(card, period)
    return {"card": card, "period": period, "label": period_label(period), "cut": cut, "is_cut": cut < t,
            "due": due_on(card, period), "entries": entries, "amount": amount,
            "row": book["rows"].get((card["id"], period)), "state": state, "paid": state in PAID_STATES}


def card_periods(book, card):
    return sorted({p for cid, p in book["lines"] if cid == card["id"]})


def progress(book, purchase, t=None):
    """{'plan': [taksit + ekstre durumu], 'paid': ödenen taksit sayısı, 'remaining': kalan tutar, 'done', 'next'}"""
    t = t or today()
    card = book["cards"][purchase["card_id"]]
    plan, paid, remaining = [], 0, 0
    for e in book["plans"][purchase["id"]]:
        state = state_of(book, card, e["period"], t)
        if state in PAID_STATES:
            paid += e["nos"][1] - e["nos"][0] + 1
        else:
            remaining += e["amount"]
        plan.append({**e, "state": state, "label": entry_label(e, purchase["count"]), "due": due_on(card, e["period"]),
                     "period_label": period_label(e["period"], short=True)})
    return {"plan": plan, "paid": paid, "remaining": round(remaining, 2), "done": paid >= purchase["count"],
            "next": next((r for r in plan if r["state"] not in PAID_STATES), None),
            "percent": round(paid * 100 / purchase["count"])}


def next_statement(book, card, t=None):
    """Sıradaki ekstre: kesilmiş, ödenmemiş, son ödemesi gelmemiş ekstre; yoksa açık dönem (önceden ödendiyse sonraki)."""
    t = t or today()
    current = period_for(card, t)
    for p in (shift(current, -1), current, shift(current, 1)):
        st = statement(book, card, p, t)
        if st["paid"] or (p < current and st["state"] != "due"):
            continue
        return st
    return statement(book, card, shift(current, 2), t)


def late_statements(book, card, t=None):
    """Son ödemesi geçmiş, ödenmemiş (tutarı olan) ekstreler."""
    t = t or today()
    return [st for st in (statement(book, card, p, t) for p in card_periods(book, card)) if st["state"] == "late"]


def card_debt(book, card, t=None):
    """Kartın ödenmemiş taksitleri (kesilmiş ve gelecek; limitten düşen tutar)."""
    t = t or today()
    return round(sum(e["amount"] for (cid, p), items in book["lines"].items() if cid == card["id"]
                     and state_of(book, card, p, t) not in PAID_STATES for _p, e in items), 2)


def monthly_load(book, t=None, months=CHART_MONTHS):
    """Önümüzdeki aylarda ödenecek taksitler, son ödeme ayına göre (ödenmiş ekstreler hariç):
    [{'month', 'label', 'short', 'total', 'parts': [(kart, tutar)]}] kart sırası korunur."""
    t = t or today()
    keys = [shift(period_of(t), i) for i in range(months)]
    sums = {k: {} for k in keys}
    for (card_id, period), items in book["lines"].items():
        card = book["cards"][card_id]
        month = period_of(due_on(card, period))
        if month in sums and state_of(book, card, period, t) not in PAID_STATES:
            sums[month][card_id] = sums[month].get(card_id, 0) + sum(e["amount"] for _p, e in items)
    out = []
    for k in keys:
        parts = [(c, round(sums[k][cid], 2)) for cid, c in book["cards"].items() if sums[k].get(cid)]
        out.append({"month": k, "label": period_label(k), "short": period_label(k, short=True),
                    "total": round(sum(v for _c, v in parts), 2), "parts": parts})
    return out


def upcoming_statements(user_id, t, until):
    """Yaklaşanlar: etkin kartların ödenmemiş ekstreleri, son ödemesi `until` gününe kadar (gecikenler LATE_DAYS gün)."""
    book = load(user_id)
    since = t - timedelta(days=LATE_DAYS)
    out = []
    for cid, p in sorted(book["lines"]):
        card = book["cards"][cid]
        if card["active"]:
            st = statement(book, card, p, t)
            if st["amount"] > 0 and not st["paid"] and since <= st["due"] <= until:
                out.append(st)
    return out


def due_events(user_id, start, end):
    """Takvim: etkin kartların start..end arasında son ödemesi olan (tutarlı) ekstreleri; ödendiyse 'paid'."""
    book = load(user_id)
    return [st for st in (statement(book, book["cards"][cid], p) for cid, p in sorted(book["lines"])
                          if book["cards"][cid]["active"])
            if st["amount"] > 0 and start <= st["due"] <= end]


# ---------- Harcamalar ----------
def _expense_note(purchase, card_name, label):
    return f"{purchase['title']} ({card_name}, {label})"[:200]


def sync_expense(purchase, card):
    """'full': toplam tutar alış gününe tek harcama (varsa güncellenir); diğer biçimlerde o harcama silinir.
    'added' / 'updated' / 'removed' / None döner."""
    db = get_db()
    live = None
    if purchase["expense_id"]:
        live = db.execute("SELECT id FROM expenses WHERE id = ? AND user_id = ?",
                          (purchase["expense_id"], purchase["user_id"])).fetchone()
    note = _expense_note(purchase, card["name"], count_text(purchase["count"]).lower())
    result = None
    if purchase["expense_mode"] == "full":
        if live:
            db.execute("UPDATE expenses SET amount = ?, category = ?, note = ?, date = ? WHERE id = ?",
                       (purchase["total"], purchase["category"], note, purchase["purchased_on"], live["id"]))
            result = "updated"
        else:
            expense_id = db.execute("INSERT INTO expenses (user_id, amount, category, note, date) VALUES (?, ?, ?, ?, ?)",
                                    (purchase["user_id"], purchase["total"], purchase["category"], note,
                                     purchase["purchased_on"])).lastrowid
            db.execute("UPDATE card_purchases SET expense_id = ? WHERE id = ?", (expense_id, purchase["id"]))
            result = "added"
    else:
        if live:
            db.execute("DELETE FROM expenses WHERE id = ?", (live["id"],))
            result = "removed"
        db.execute("UPDATE card_purchases SET expense_id = NULL WHERE id = ?", (purchase["id"],))
    db.commit()
    if result == "added":
        automation.fire("expense_added", purchase["user_id"],
                        **{"tutar": purchase["total"], "kategori": purchase["category"], "not": note})
    return result


def _ensure_row(db, card, period):
    db.execute("INSERT OR IGNORE INTO card_statements (user_id, card_id, period) VALUES (?, ?, ?)",
               (card["user_id"], card["id"], period))


def pay(st, paid_on, amount=None):
    """Ekstreyi ödendi işaretler (web ve Telegram ortak); tutar verilmezse ekstre toplamı. 'monthly' alışverişlerin
    bu ekstredeki taksitleri ödeme gününe Harcamalar'a eklenir. Eklenen harcama sayısını döner."""
    card = st["card"]
    db = get_db()
    added = []
    for p, e in st["entries"]:
        if p["expense_mode"] == "monthly":
            note = _expense_note(p, card["name"], entry_label(e, p["count"]))
            expense_id = db.execute("INSERT INTO expenses (user_id, amount, category, note, date) VALUES (?, ?, ?, ?, ?)",
                                    (card["user_id"], e["amount"], p["category"], note, paid_on)).lastrowid
            added.append((expense_id, e["amount"], p["category"], note))
    _ensure_row(db, card, st["period"])
    db.execute("UPDATE card_statements SET paid_on = ?, paid_amount = ?, expense_ids = ?"
               " WHERE card_id = ? AND period = ?",
               (paid_on, st["amount"] if amount is None else amount, ",".join(str(a[0]) for a in added), card["id"],
                st["period"]))
    db.commit()
    for _id, value, category, note in added:
        automation.fire("expense_added", card["user_id"], **{"tutar": value, "kategori": category, "not": note})
    return len(added)


def unpay(st):
    """Ödemeyi geri alır; o ödemeyle Harcamalar'a eklenen taksitler silinir. Silinen harcama sayısını döner."""
    row = st["row"]
    db = get_db()
    dropped = 0
    for raw in (row["expense_ids"] or "").split(","):
        if raw.isdigit():
            dropped += db.execute("DELETE FROM expenses WHERE id = ? AND user_id = ?",
                                  (int(raw), st["card"]["user_id"])).rowcount
    db.execute("UPDATE card_statements SET paid_on = NULL, paid_amount = NULL, expense_ids = '' WHERE id = ?",
               (row["id"],))
    db.commit()
    return dropped


# ---------- Form ----------
def _owned_card(card_id, user_id):
    if not card_id or not 0 < card_id < MAX_ID:
        return None
    return query_one("SELECT * FROM credit_cards WHERE id = ? AND user_id = ?", (card_id, user_id))


def suggested_color(user_id):
    """Yeni kart için henüz kullanılmayan ilk renk (grafikte kartlar ayrışsın)."""
    used = {r["color"] for r in query("SELECT color FROM credit_cards WHERE user_id = ?", (user_id,))}
    return next((c for c in COLORS if c not in used), next(iter(COLORS)))


def _card_values(user_id):
    """(değerler, hata)."""
    raw_limit = form_str("limit_amount", 50)
    due_raw = form_str("due_days", 10)
    v = {
        "name": " ".join(form_str("name", NAME_MAX).split()),
        "color": form_choice("color", COLORS, suggested_color(user_id)),
        "statement_day": form_int("statement_day"),
        "due_days": form_int("due_days") if due_raw else DEFAULT_DUE_DAYS,
        "limit_amount": form_amount("limit_amount") if raw_limit else None,
        "remind_days": form_int("remind_days"),
    }
    if v["remind_days"] not in dict(REMIND_OPTIONS):
        v["remind_days"] = DEFAULT_REMIND
    if not v["name"]:
        return v, "Kart adı gerekli (ör. Bonus, Maximum, Maaş kartı)."
    if len(CARD_NUMBER_RE.findall(v["name"])) >= 10:
        return v, "Kart adına kart numarası yazma; “Bonus”, “Maaş kartı” gibi bir ad yeter."
    if v["statement_day"] is None or not 1 <= v["statement_day"] <= 31:
        return v, "Hesap kesim günü 1-31 arasında olmalı."
    if v["due_days"] is None or not 1 <= v["due_days"] <= MAX_DUE_DAYS:
        return v, f"Son ödeme, kesimden 1-{MAX_DUE_DAYS} gün sonra olmalı (çoğu kartta 10)."
    if raw_limit and not valid_amount(v["limit_amount"]):
        return v, "Limit 0'dan büyük bir sayı olmalı (bilmiyorsan boş bırak)."
    return v, None


def blank_card(user_id):
    return {"name": "", "color": suggested_color(user_id), "statement_day": None, "due_days": DEFAULT_DUE_DAYS,
            "limit_amount": None, "remind_days": DEFAULT_REMIND}


def _purchase_values(user_id, allow_card_id=None):
    """(değerler, kart). Kart kullanıcının etkin kartıysa (düzenlemede mevcut kartı pasif de olabilir)."""
    card = _owned_card(form_int("card_id"), user_id)
    if card and not card["active"] and card["id"] != allow_card_id:
        card = None
    count = form_int("count")
    if count == 0:   # "Diğer…"
        count = form_int("count_other")
    v = {
        "card_id": card["id"] if card else None,
        "title": form_str("title", TITLE_MAX),
        "merchant": form_str("merchant", TITLE_MAX),
        "category": form_choice("category", EXPENSE_CATEGORIES, DEFAULT_CATEGORY),
        "purchased_on": form_date("purchased_on") or today_str(),
        "total": form_amount("total"),
        "count": count,
        "first_statement": form_str("first_statement", 10),
        "expense_mode": form_choice("expense_mode", EXPENSE_MODES, "full"),
        "note": form_str("note", NOTE_MAX),
    }
    return v, card


def _check_plan(v, card):
    """Taksit planı için gereken alanlar; ilk ekstre boşsa ya da önerilenle aynıysa alış gününden hesaplanır.
    v['first_statement'] ve v['first_auto'] yazılır. Hata metni ya da None."""
    if card is None:
        return "Kart seç (önce bir kart ekle)."
    if v["purchased_on"] > today_str():
        return "Alış tarihi ileri bir gün olamaz."
    natural = period_for(card, parse_date(v["purchased_on"]))
    v["first_auto"] = natural
    raw = v["first_statement"]
    if not raw or raw == form_str("first_auto", 10):
        v["first_statement"] = natural
    elif not parse_period(raw):
        return "İlk ekstre YYYY-AA biçiminde olmalı (ör. 2026-11)."
    elif raw < natural:
        return f"İlk taksit, alış gününün ekstresinden ({period_label(natural)}) önceki bir ekstreye düşemez."
    elif raw > shift(natural, MAX_DEFER):
        return f"İlk taksit en fazla {MAX_DEFER} ay ertelenebilir ({period_label(shift(natural, MAX_DEFER))})."
    if not valid_amount(v["total"]):
        return "Toplam tutar 0'dan büyük bir sayı olmalı (ör. 12.000)."
    if v["count"] is None or not 1 <= v["count"] <= MAX_COUNT:
        return f"Taksit sayısı 1-{MAX_COUNT} arasında olmalı."
    if round(v["total"] * 100) < v["count"]:
        return "Taksit tutarı 1 kuruştan az olamaz."
    return None


def blank_purchase(card=None):
    return {"card_id": card["id"] if card else None, "title": "", "merchant": "", "category": DEFAULT_CATEGORY,
            "purchased_on": today_str(), "total": None, "count": 1, "expense_mode": "full", "note": "",
            "first_statement": period_for(card, today()) if card else ""}


def _merchants(user_id):
    return [r["merchant"] for r in query(
        "SELECT merchant FROM card_purchases WHERE user_id = ? AND merchant != '' GROUP BY merchant"
        " ORDER BY MAX(id) DESC LIMIT 30", (user_id,))]


def _ctx(**kw):
    return dict(colors=COLORS, color_icon=color_icon, count_options=COUNT_OPTIONS, max_count=MAX_COUNT,
                remind_options=REMIND_OPTIONS, expense_modes=EXPENSE_MODES, expense_categories=EXPENSE_CATEGORIES,
                category_icon=category_icon, states=STATES, count_text=count_text, period_label=period_label,
                entry_label=entry_label, max_due_days=MAX_DUE_DAYS, **kw)


def _form_cards(user_id, current=None):
    """Alışveriş formundaki kartlar: etkinler (düzenlenen alışverişin kartı pasif olsa da)."""
    return [c for c in query("SELECT * FROM credit_cards WHERE user_id = ? ORDER BY name COLLATE NOCASE, id",
                             (user_id,)) if c["active"] or c["id"] == current]


# ---------- Rotalar: özet ----------
@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    t = today()
    book = load(uid)
    progresses = {p["id"]: progress(book, p, t) for p in book["purchases"]}
    ongoing = sorted((p for p in book["purchases"] if not progresses[p["id"]]["done"]),
                     key=lambda p: (progresses[p["id"]]["next"]["period"], -p["id"]))
    finished = sorted((p for p in book["purchases"] if progresses[p["id"]]["done"]),
                      key=lambda p: (p["purchased_on"], p["id"]), reverse=True)
    active = [c for c in book["cards"].values() if c["active"]]
    overview = []
    for c in active:
        debt = card_debt(book, c, t)
        overview.append({"card": c, "next": next_statement(book, c, t), "late": late_statements(book, c, t), "debt": debt,
                         "percent": round(debt * 100 / c["limit_amount"]) if c["limit_amount"] else None})
    chart = monthly_load(book, t)
    in_chart = {c["id"] for m in chart for c, _v in m["parts"]}
    total_left = round(sum(pr["remaining"] for pr in progresses.values()), 2)
    summary = [f"{len(active)} kart"] if book["cards"] else []
    if ongoing:
        summary.append(f"{len(ongoing)} devam eden alışveriş")
    if total_left:
        summary.append(f"kalan {fmt_money(total_left)}")
    return render_template(
        "installments/index.html", **_ctx(
            book=book, overview=overview, inactive=[c for c in book["cards"].values() if not c["active"]],
            ongoing=ongoing, finished=finished, progresses=progresses, chart=chart,
            chart_max=max((m["total"] for m in chart), default=0), total_left=total_left,
            legend=[c for c in book["cards"].values() if c["id"] in in_chart],
            late_count=sum(len(o["late"]) for o in overview), summary=" · ".join(summary),
            card_form=blank_card(uid), t=t))


# ---------- Rotalar: kart ----------
@bp.route("/kart/yeni", methods=["POST"])
@login_required
def card_create():
    uid = g.user["id"]
    v, error = _card_values(uid)
    if error:
        flash(error, "error")
        return redirect_back(".index")
    card_id = execute(
        "INSERT INTO credit_cards (user_id, name, color, statement_day, due_days, limit_amount, remind_days)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (uid, v["name"], v["color"], v["statement_day"], v["due_days"], v["limit_amount"], v["remind_days"]),
    ).lastrowid
    flash(f"{color_icon(v['color'])} {v['name']} eklendi · hesap kesim ayın {v['statement_day']}. günü, son ödeme "
          f"{v['due_days']} gün sonra.", "success")
    return redirect_back(".index", _anchor=f"kart-{card_id}")


@bp.route("/kart/<int:card_id>", methods=["GET", "POST"])
@login_required
def card_page(card_id):
    uid = g.user["id"]
    card = owned_or_404("credit_cards", card_id, uid)
    if request.method == "POST":
        v, error = _card_values(uid)
        if error:
            flash(error, "error")
            return redirect(url_for(".card_page", card_id=card_id))
        execute("UPDATE credit_cards SET name = ?, color = ?, statement_day = ?, due_days = ?, limit_amount = ?,"
                " remind_days = ? WHERE id = ? AND user_id = ?",
                (v["name"], v["color"], v["statement_day"], v["due_days"], v["limit_amount"], v["remind_days"],
                 card_id, uid))
        moved = 0
        if v["statement_day"] != card["statement_day"]:
            # Kesim günü değişti: ilk ekstresi kendiliğinden hesaplanmış alışverişler yeni güne göre
            new = {**dict(card), "statement_day": v["statement_day"]}
            db = get_db()
            for p in query("SELECT * FROM card_purchases WHERE card_id = ?", (card_id,)):
                d = parse_date(p["purchased_on"])
                if p["first_statement"] == period_for(card, d) != period_for(new, d):
                    db.execute("UPDATE card_purchases SET first_statement = ? WHERE id = ?",
                               (period_for(new, d), p["id"]))
                    moved += 1
            db.commit()
        note = f" {moved} alışverişin ilk ekstresi yeni kesim gününe göre değişti." if moved else ""
        flash("Kart güncellendi." + note, "success")
        return redirect(url_for(".card_page", card_id=card_id))
    t = today()
    book = load(uid)
    current = period_for(card, t)
    statements = [statement(book, card, p, t) for p in sorted(set(card_periods(book, card)) | {current})]
    # Üstte: açık dönem ve sonrası, kesilip ödeme bekleyen ve geciken; altta (katlı) geçmiş ekstreler, yeniden eskiye
    upcoming = [st for st in statements if st["period"] >= current or st["state"] in ("due", "late")]
    past = [st for st in statements if st not in upcoming][::-1][:PAST_SHOWN]
    purchases = [p for p in book["purchases"] if p["card_id"] == card_id]
    progresses = {p["id"]: progress(book, p, t) for p in purchases}
    purchases.sort(key=lambda p: (progresses[p["id"]]["done"], p["purchased_on"], p["id"]))
    debt = card_debt(book, card, t)
    return render_template("installments/card.html", **_ctx(
        card=card, upcoming=upcoming, past=past, purchases=purchases, progresses=progresses, debt=debt, t=t,
        percent=round(debt * 100 / card["limit_amount"]) if card["limit_amount"] else None))


@bp.route("/kart/<int:card_id>/durum", methods=["POST"])
@login_required
def card_toggle(card_id):
    uid = g.user["id"]
    card = owned_or_404("credit_cards", card_id, uid)
    execute("UPDATE credit_cards SET active = 1 - active WHERE id = ? AND user_id = ?", (card_id, uid))
    if card["active"]:
        flash(f"{card['name']} kullanılmıyor olarak işaretlendi; hatırlatma gelmez, yeni alışverişte seçilmez.",
              "success")
    else:
        flash(f"{card['name']} yeniden etkin.", "success")
    return redirect_back(".card_page", card_id=card_id)


@bp.route("/kart/<int:card_id>/sil", methods=["POST"])
@login_required
def card_delete(card_id):
    uid = g.user["id"]
    card = owned_or_404("credit_cards", card_id, uid)
    trash.move(uid, "installments", f"💳 {card['name']} (kart, alışverişleri ve ekstreleriyle)", ("credit_cards", card_id),
               children=[("card_purchases", "card_id = ?"), ("card_statements", "card_id = ?")])
    flash(trash.notice(f"{card['name']} kartı"), "success")
    return redirect(url_for(".index"))


# ---------- Rotalar: ekstre ----------
def _statement_or_404(card_id, period, user_id):
    card = owned_or_404("credit_cards", card_id, user_id)
    if not parse_period(period):
        abort(404)
    return statement(load(user_id), card, period)


@bp.route("/kart/<int:card_id>/<period>")
@login_required
def statement_page(card_id, period):
    st = _statement_or_404(card_id, period, g.user["id"])
    monthly = sum(1 for p, _e in st["entries"] if p["expense_mode"] == "monthly")
    return render_template("installments/statement.html", **_ctx(
        st=st, card=st["card"], prev=shift(period, -1), nxt=shift(period, 1), monthly=monthly,
        current=period_for(st["card"], today())))


@bp.route("/kart/<int:card_id>/<period>/ode", methods=["POST"])
@login_required
def statement_pay(card_id, period):
    st = _statement_or_404(card_id, period, g.user["id"])
    day = form_date("paid_on") or today_str()
    raw = form_str("paid_amount", 50)
    amount = form_amount("paid_amount") if raw else st["amount"]
    if st["paid"]:
        flash("Bu ekstre zaten ödenmiş.", "warning")
    elif not st["amount"]:
        flash("Bu ekstrede taksit yok.", "warning")
    elif day > today_str():
        flash("Ödeme tarihi ileri bir gün olamaz.", "error")
    elif not valid_amount(amount):
        flash("Ödenen tutar 0'dan büyük bir sayı olmalı.", "error")
    else:
        added = pay(st, day, round(amount, 2))
        text = f"✅ {st['card']['name']} · {st['label']} ekstresi ödendi ({fmt_money(amount)})."
        if added:
            text += f" {added} taksit Harcamalar'a işlendi."
        flash(text, "success")
    return redirect_back(".statement_page", card_id=card_id, period=period)


@bp.route("/kart/<int:card_id>/<period>/geri-al", methods=["POST"])
@login_required
def statement_unpay(card_id, period):
    st = _statement_or_404(card_id, period, g.user["id"])
    if st["state"] != "paid":
        flash("Geri alınacak ödeme yok.", "warning")
    else:
        dropped = unpay(st)
        note = f" Harcamalar'a işlenen {dropped} taksit silindi." if dropped else ""
        flash(f"↩️ {st['label']} ödemesi geri alındı.{note}", "success")
    return redirect_back(".statement_page", card_id=card_id, period=period)


# ---------- Rotalar: alışveriş ----------
@bp.route("/alisveris/yeni", methods=["GET", "POST"])
@login_required
def purchase_new():
    uid = g.user["id"]
    cards = _form_cards(uid)
    if request.method == "POST":
        v, card = _purchase_values(uid)
        error = _check_plan(v, card) or (None if v["title"] else "Açıklama gerekli (ör. Buzdolabı).")
        if error:
            flash(error, "error")
            return render_template("installments/new.html", **_ctx(
                p=v, cards=cards, first_auto=v.get("first_auto", ""), merchants=_merchants(uid))), 400
        purchase_id = execute(
            "INSERT INTO card_purchases (user_id, card_id, title, merchant, category, purchased_on, total, count,"
            " first_statement, expense_mode, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, card["id"], v["title"], v["merchant"], v["category"], v["purchased_on"], round(v["total"], 2),
             v["count"], v["first_statement"], v["expense_mode"], v["note"]),
        ).lastrowid
        result = sync_expense(query_one("SELECT * FROM card_purchases WHERE id = ?", (purchase_id,)), card)
        text = (f"💳 {v['title']} eklendi · {count_text(v['count']).lower()}, ilk taksit "
                f"{period_label(v['first_statement'])} ekstresinde.")
        if result == "added":
            text += " Harcamalar'a da işlendi."
        flash(text, "success")
        return redirect(url_for(".purchase", purchase_id=purchase_id))
    card = _owned_card(request.args.get("kart", type=int), uid)
    if card is None or not card["active"]:
        card = cards[0] if cards else None
    p = blank_purchase(card)
    return render_template("installments/new.html", **_ctx(p=p, cards=cards, first_auto=p["first_statement"],
                                                           merchants=_merchants(uid)))


@bp.route("/alisveris/<int:purchase_id>", methods=["GET", "POST"])
@login_required
def purchase(purchase_id):
    uid = g.user["id"]
    row = owned_or_404("card_purchases", purchase_id, uid)
    if request.method == "POST":
        v, card = _purchase_values(uid, allow_card_id=row["card_id"])
        error = _check_plan(v, card) or (None if v["title"] else "Açıklama gerekli (ör. Buzdolabı).")
        if not error and row["closed_early_on"] and row["closed_early_on"] < v["purchased_on"]:
            error = "Alış tarihi, erken kapama gününden sonra olamaz."
        if error:
            flash(error, "error")
            return redirect(url_for(".purchase", purchase_id=purchase_id))
        execute("UPDATE card_purchases SET card_id = ?, title = ?, merchant = ?, category = ?, purchased_on = ?,"
                " total = ?, count = ?, first_statement = ?, expense_mode = ?, note = ? WHERE id = ? AND user_id = ?",
                (card["id"], v["title"], v["merchant"], v["category"], v["purchased_on"], round(v["total"], 2),
                 v["count"], v["first_statement"], v["expense_mode"], v["note"], purchase_id, uid))
        result = sync_expense(query_one("SELECT * FROM card_purchases WHERE id = ?", (purchase_id,)), card)
        note = {"added": " Harcamalar'a işlendi.", "updated": " Harcama kaydı da güncellendi.",
                "removed": " Alış günü eklenen harcama kaydı silindi."}.get(result, "")
        flash("Alışveriş güncellendi." + note, "success")
        return redirect(url_for(".purchase", purchase_id=purchase_id))
    t = today()
    book = load(uid)
    card = book["cards"][row["card_id"]]
    expense = query_one("SELECT id FROM expenses WHERE id = ? AND user_id = ?", (row["expense_id"], uid)) \
        if row["expense_id"] else None
    pr = progress(book, row, t)
    return render_template("installments/purchase.html", **_ctx(
        p=row, card=card, pr=pr, expense=expense, cards=_form_cards(uid, row["card_id"]), t=t,
        first_auto=period_for(card, parse_date(row["purchased_on"])), merchants=_merchants(uid),
        close_target=period_label(max(period_for(card, t), row["first_statement"]))))


@bp.route("/alisveris/<int:purchase_id>/kapat", methods=["POST"])
@login_required
def purchase_close(purchase_id):
    """Erken kapama: kalan taksitler kapatma gününün ekstresine tek seferde eklenir."""
    uid = g.user["id"]
    row = owned_or_404("card_purchases", purchase_id, uid)
    card = owned_or_404("credit_cards", row["card_id"], uid)
    day = form_date("closed_on") or today_str()
    target = max(period_for(card, parse_date(day)), row["first_statement"])
    if row["closed_early_on"]:
        flash("Bu alışveriş zaten erken kapatılmış.", "warning")
    elif day > today_str():
        flash("Kapatma tarihi ileri bir gün olamaz.", "error")
    elif day < row["purchased_on"]:
        flash("Kapatma tarihi alış gününden önce olamaz.", "error")
    elif target >= shift(row["first_statement"], row["count"] - 1):
        flash("Son taksit zaten bu ekstrede ya da öncesinde; erken kapatılacak taksit yok.", "warning")
    else:
        execute("UPDATE card_purchases SET closed_early_on = ? WHERE id = ? AND user_id = ?", (day, purchase_id, uid))
        flash(f"🔒 {row['title']} erken kapatıldı: kalan taksitler {period_label(target)} ekstresine eklendi.", "success")
    return redirect(url_for(".purchase", purchase_id=purchase_id))


@bp.route("/alisveris/<int:purchase_id>/kapat-geri-al", methods=["POST"])
@login_required
def purchase_reopen(purchase_id):
    uid = g.user["id"]
    row = owned_or_404("card_purchases", purchase_id, uid)
    execute("UPDATE card_purchases SET closed_early_on = NULL WHERE id = ? AND user_id = ?", (purchase_id, uid))
    if row["closed_early_on"]:
        flash("↩️ Erken kapama geri alındı; taksitler eski planına döndü.", "success")
    return redirect(url_for(".purchase", purchase_id=purchase_id))


@bp.route("/alisveris/<int:purchase_id>/sil", methods=["POST"])
@login_required
def purchase_delete(purchase_id):
    uid = g.user["id"]
    row = owned_or_404("card_purchases", purchase_id, uid)
    card = owned_or_404("credit_cards", row["card_id"], uid)
    trash.move(uid, "installments", f"💳 {row['title']} ({card['name']} · {fmt_money(row['total'])})",
               ("card_purchases", purchase_id))
    flash(trash.notice(row["title"]), "success")
    return redirect_back(".index")


@bp.route("/plan", methods=["POST"])
@login_required
def plan_preview():
    """Formdaki bilgilerle taksit planı, kaydetmeden (installments.js): {ok, first_auto, rows, total, error}."""
    uid = g.user["id"]
    v, card = _purchase_values(uid, allow_card_id=form_int("card_id"))
    error = _check_plan(v, card)
    if error:
        return jsonify(ok=False, error=error, first_auto=v.get("first_auto"))
    fake = {"first_statement": v["first_statement"], "total": v["total"], "count": v["count"], "closed_early_on": None}
    rows = [{"no": entry_label(e, v["count"]), "period": period_label(e["period"], short=True),
             "due": fmt_date(due_on(card, e["period"])), "amount": fmt_money(e["amount"])} for e in schedule(fake, card)]
    return jsonify(ok=True, first_auto=v["first_auto"], first=v["first_statement"], rows=rows,
                   total=fmt_money(round(v["total"], 2)), each=fmt_money(split(v["total"], v["count"])[0]))


# ---------- Telegram hatırlatması (cron /hatirlatma) ----------
def pending(now):
    """[(tür, ekstre, chat_id)] — 'pre' (son ödemeye remind_days gün ya da daha az kaldı), 'day' (son ödeme günü).
    Etkin kartların ödenmemiş, tutarlı ekstreleri; her son ödeme tarihi için ikisi de birer kez."""
    t = now.date()
    out = []
    for u in query("SELECT DISTINCT c.user_id, u.telegram_chat_id AS chat_id FROM credit_cards c"
                   " JOIN users u ON u.id = c.user_id WHERE u.telegram_chat_id IS NOT NULL AND c.active = 1"
                   " ORDER BY c.user_id"):
        book = load(u["user_id"])
        for card in book["cards"].values():
            if not card["active"]:
                continue
            current = period_for(card, t)
            for p in (shift(current, -2), shift(current, -1), current):
                st = statement(book, card, p, t)
                if st["amount"] <= 0 or st["paid"]:
                    continue
                left = (st["due"] - t).days
                sent = st["row"]["reminded_for"] if st["row"] else None
                key = st["due"].isoformat()
                if left == 0 and sent != f"day:{key}":
                    out.append(("day", st, u["chat_id"]))
                elif 0 < left <= card["remind_days"] and sent not in (f"pre:{key}", f"day:{key}"):
                    out.append(("pre", st, u["chat_id"]))
    return out


def mark_sent(st, kind):
    db = get_db()
    _ensure_row(db, st["card"], st["period"])
    db.execute("UPDATE card_statements SET reminded_for = ? WHERE card_id = ? AND period = ?",
               (f"{kind}:{st['due'].isoformat()}", st["card"]["id"], st["period"]))
    db.commit()


def buttons(st):
    return [[("✅ Ödendi", f"ins:pay:{st['card']['id']}:{st['period']}")]]


def message(kind, st, t, url, escape):
    """'💳 Bonus ekstresi: 3.250 ₺ · son ödeme 15 Ekim (3 gün)' + dönem ve taksit sayısı."""
    head = f"💳 <b>{escape(st['card']['name'])}</b> ekstresi: <b>{fmt_money(st['amount'])}</b> · son ödeme "
    if kind == "day":
        head += f"<b>bugün</b> ({day_month(st['due'])})"
    else:
        head += f"{day_month(st['due'])} ({left_words((st['due'] - t).days)})"
    n = len(st["entries"])
    lines = [head, f"📄 {st['label']} ekstresi · {n} kalem · kesim {fmt_date(st['cut'])}",
             f'<a href="{escape(url)}">Ekstre →</a>']
    return "\n".join(lines)


def paid_message(st, added, url, escape):
    text = f"✅ <b>{escape(st['card']['name'])}</b> · {st['label']} ekstresi ödendi ({fmt_money(st['amount'])})."
    if added:
        text += f"\n💸 {added} taksit Harcamalar'a işlendi."
    return text + f'\n<a href="{escape(url)}">Ekstre →</a>'


# ---------- Bot: /taksit ----------
def bot_message(user_id, t, index_url, statement_url, escape, months=3):
    """/taksit: sıradaki (ve geciken) ekstreler, önümüzdeki `months` ayın taksit yükü, kalan borç."""
    book = load(user_id)
    if not book["cards"]:
        return ("💳 Henüz kart yok. Panoda Taksitler'den kart ekle: sadece bir ad, kesim günü ve son ödeme günü "
                f'(kart numarası istenmez).\n<a href="{escape(index_url)}">Taksitler →</a>')
    lines = ["💳 <b>Taksitler</b>", "", "<b>Sıradaki ekstreler</b>"]
    for card in (c for c in book["cards"].values() if c["active"]):
        for st in late_statements(book, card, t) + [next_statement(book, card, t)]:
            if st["state"] == "late":
                when = f"⚠️ son ödeme {fmt_date(st['due'])} ({left_words((st['due'] - t).days)})"
            elif st["state"] == "due":
                when = f"son ödeme {fmt_date(st['due'])} ({left_words((st['due'] - t).days)})"
            else:
                when = f"kesim {fmt_date(st['cut'])} · son ödeme {fmt_date(st['due'])}"
            link = statement_url(card["id"], st["period"])
            lines.append(f'• {color_icon(card["color"])} <a href="{escape(link)}">{escape(card["name"])}</a> '
                         f"{period_label(st['period'], short=True)}: <b>{fmt_money(st['amount'])}</b> · {when}")
    lines += ["", f"<b>Önümüzdeki {months} ay</b>"]
    for m in monthly_load(book, t, months):
        parts = ", ".join(f"{escape(c['name'])} {fmt_money(v)}" for c, v in m["parts"]) if len(m["parts"]) > 1 else ""
        lines.append(f"• {m['label']}: <b>{fmt_money(m['total'])}</b>" + (f" ({parts})" if parts else ""))
    left = round(sum(progress(book, p, t)["remaining"] for p in book["purchases"]), 2)
    lines += ["", f"Kalan toplam borç: <b>{fmt_money(left)}</b>", f'<a href="{escape(index_url)}">Taksitler →</a>']
    return "\n".join(lines)
