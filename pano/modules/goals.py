"""🏁 Hedefler: birikim hedefleri, para ekle / çek, aylık plan ve tahmini bitiş.

- Birikim = goal_entries.amount toplamı; eksi kayıt hedeften para çekildiği anlamına gelir.
- Birikim hiçbir zaman eksiye düşmez (fazla çekme ya da buna yol açan kayıt silme reddedilir).
- Birikim hedefe ulaşınca done_at otomatik dolar, tekrar altına inerse temizlenir.
- goal_entries'in user_id'si yoktur; sahiplik her zaman bağlı olduğu hedef üzerinden kontrol edilir.

Diğer modüller için: summary(user_id) -> aktif hedeflerin özeti (pano kartı vb.).
"""
import math

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import trash
from ..auth import login_required
from ..db import execute, owned_or_404, query, query_one
from ..utils import (MONTHS_TR, add_months, fmt_money, form_choice, form_date, form_float, form_str,
                     parse_date, redirect_back, today, today_str)

bp = Blueprint("goals", __name__, url_prefix="/hedefler")

ICONS = ["🏁", "✈️", "🏠", "🚗", "📱", "🎓", "💍", "🎁", "🏖️", "💻"]
DEFAULT_ICON = "🏁"
PACE_MONTHS = 3                 # tahmini bitiş: son kaç aydaki net katkının ortalaması
MAX_AMOUNT = 1e12               # bundan büyük tutarlar yazım hatasıdır
MAX_PROJECTION_MONTHS = 1200    # 100 yıldan uzun tahmin gösterilmez


# ---------- Hesaplama yardımcıları ----------
def _r(value):
    return round(float(value or 0), 2)


def _valid_amount(value):
    return value is not None and math.isfinite(value) and abs(value) <= MAX_AMOUNT


def _saved_by_goal(user_id, goal_id=None, since=None, until=None):
    """{goal_id: net tutar} — sadece kullanıcının kendi hedefleri. since hariç, until dahil."""
    sql = ("SELECT e.goal_id, COALESCE(SUM(e.amount), 0) AS total FROM goal_entries e"
           " JOIN goals g ON g.id = e.goal_id WHERE g.user_id = ?")
    args = [user_id]
    if goal_id is not None:
        sql += " AND e.goal_id = ?"
        args.append(goal_id)
    if since:
        sql += " AND e.date > ?"
        args.append(since)
    if until:
        sql += " AND e.date <= ?"
        args.append(until)
    sql += " GROUP BY e.goal_id"
    return {r["goal_id"]: _r(r["total"]) for r in query(sql, args)}


def _saved(user_id, goal_id):
    return _saved_by_goal(user_id, goal_id).get(goal_id, 0.0)


def _pace_window(t):
    """Tahmin penceresi: (t - PACE_MONTHS ay, t]."""
    return add_months(t, -PACE_MONTHS).isoformat(), t.isoformat()


def months_left(deadline, t=None):
    """Hedef tarihine kalan ay (yukarı yuvarlanır, en az 1). Tarih yoksa None."""
    d = parse_date(deadline)
    if d is None:
        return None
    t = t or today()
    n = (d.year - t.year) * 12 + (d.month - t.month)
    if n >= 0 and add_months(t, n) < d:
        n += 1
    return max(1, n)


_ONES = {1: "de", 2: "de", 3: "te", 4: "te", 5: "te", 6: "da", 7: "de", 8: "de", 9: "da"}
_TENS = {1: "da", 2: "de", 3: "da", 4: "ta", 5: "de", 6: "ta", 7: "te", 8: "de", 9: "da"}


def loc_suffix(n):
    """Sayıya gelen bulunma eki: 2027'de, 2030'da, 2033'te, 2040'ta, 2100'de."""
    if n % 10:
        return _ONES[n % 10]
    if n // 10 % 10:
        return _TENS[n // 10 % 10]
    return "de"  # yüz / bin


def _projection(remaining, recent_net, t):
    """Son PACE_MONTHS aydaki net katkının aylık ortalamasıyla tahmini bitiş."""
    if recent_net <= 0:
        return {"per_month": None, "finish": None, "text": f"Son {PACE_MONTHS} ayda katkı yok"}
    per_month = _r(recent_net / PACE_MONTHS)
    n = max(1, math.ceil(remaining / (recent_net / PACE_MONTHS) - 1e-9))
    if n > MAX_PROJECTION_MONTHS:
        return {"per_month": per_month, "finish": None, "text": "Bu hızla 100 yıldan uzun sürer"}
    d = add_months(t, n)
    return {"per_month": per_month, "finish": d,
            "text": f"Bu hızla ~{MONTHS_TR[d.month - 1]} {d.year}'{loc_suffix(d.year)} tamamlanır"}


def _numbers(goal, saved, t):
    target = _r(goal["target"])
    remaining = max(0.0, _r(target - saved))
    pct = saved * 100 / target if target > 0 else 0.0
    months = months_left(goal["deadline"], t) if goal["deadline"] else None
    deadline = parse_date(goal["deadline"])
    done = goal["done_at"] is not None
    return {
        "goal": goal,
        "saved": _r(saved),
        "pct": round(pct, 1),
        "remaining": remaining,
        "months_left": months,
        "monthly_needed": _r(remaining / months) if months else None,
        "overdue": bool(deadline and deadline < t and not done),
        "done": done,
    }


def _card(goal, saved, recent_net, t):
    c = _numbers(goal, saved, t)
    c["bar"] = round(min(100.0, max(0.0, c["pct"])), 1)
    c["pct_label"] = int(c["pct"]) if not c["done"] else max(100, int(c["pct"]))
    c["extra"] = max(0.0, _r(saved - goal["target"]))
    if c["done"]:
        c.update(per_month=None, finish=None, projection="", proj_late=False)
    else:
        p = _projection(c["remaining"], recent_net, t)
        deadline = parse_date(goal["deadline"])
        c.update(per_month=p["per_month"], finish=p["finish"], projection=p["text"],
                 proj_late=bool(deadline and p["finish"] and p["finish"] > deadline and not c["overdue"]))
    return c


def _sync_done(goal, saved):
    """done_at'i birikimle eşitler. Durum değiştiyse 'done' / 'undone', değişmediyse None."""
    reached = _r(saved) >= _r(goal["target"])
    if reached and goal["done_at"] is None:
        execute("UPDATE goals SET done_at = ? WHERE id = ?", (today_str(), goal["id"]))
        return "done"
    if not reached and goal["done_at"] is not None:
        execute("UPDATE goals SET done_at = NULL WHERE id = ?", (goal["id"],))
        return "undone"
    return None


def _sync_and_flash(user_id, goal_id):
    goal = query_one("SELECT * FROM goals WHERE id = ? AND user_id = ?", (goal_id, user_id))
    if goal is None:
        return
    change = _sync_done(goal, _saved(user_id, goal_id))
    if change == "done":
        flash(f"🎉 Tebrikler! {goal['icon']} {goal['name']} hedefine ulaştın.", "success")
    elif change == "undone":
        flash(f"{goal['icon']} {goal['name']} yeniden aktif hedeflere taşındı.", "warning")


def _heal(user_id):
    """Dışarıdan eklenen kayıtlar yüzünden done_at birikimle uyuşmuyorsa sessizce düzeltir."""
    saved = _saved_by_goal(user_id)
    for goal in query("SELECT id, target, done_at FROM goals WHERE user_id = ?", (user_id,)):
        _sync_done(goal, saved.get(goal["id"], 0.0))


def summary(user_id):
    """Aktif (tamamlanmamış) hedefler; önce tarihliler (yakın tarih önce), sonra tarihsizler, sonra id.

    [{"goal": row, "saved": float, "pct": float, "remaining": float, "monthly_needed": float|None}]
    """
    t = today()
    goals = query("SELECT * FROM goals WHERE user_id = ? AND done_at IS NULL"
                  " ORDER BY deadline IS NULL, deadline, id", (user_id,))
    saved = _saved_by_goal(user_id)
    out = []
    for goal in goals:
        c = _numbers(goal, saved.get(goal["id"], 0.0), t)
        out.append({k: c[k] for k in ("goal", "saved", "pct", "remaining", "monthly_needed")})
    return out


def _form_values():
    """(değerler, hata mesajı | None)"""
    raw_deadline = (request.form.get("deadline") or "").strip()
    v = {
        "name": form_str("name", 60),
        "target": form_float("target"),
        "deadline": form_date("deadline"),
        "note": form_str("note", 500),
    }
    if not v["name"]:
        return v, "Hedef adı boş olamaz."
    if v["target"] is None:
        return v, "Hedef tutarını gir (ör. 12.000)."
    if not _valid_amount(v["target"]) or v["target"] <= 0:
        return v, "Hedef tutarı sıfırdan büyük geçerli bir sayı olmalı."
    if raw_deadline and v["deadline"] is None:
        return v, "Geçersiz hedef tarihi."
    v["target"] = _r(v["target"])
    if v["target"] <= 0:
        return v, "Hedef tutarı sıfırdan büyük geçerli bir sayı olmalı."
    return v, None


# ---------- Rotalar ----------
@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    t = today()
    _heal(uid)
    goals = query("SELECT * FROM goals WHERE user_id = ? ORDER BY deadline IS NULL, deadline, id", (uid,))
    saved = _saved_by_goal(uid)
    since, until = _pace_window(t)
    recent = _saved_by_goal(uid, since=since, until=until)
    cards = [_card(gl, saved.get(gl["id"], 0.0), recent.get(gl["id"], 0.0), t) for gl in goals]
    active = [c for c in cards if not c["done"]]
    completed = sorted((c for c in cards if c["done"]), key=lambda c: (c["goal"]["done_at"], c["goal"]["id"]),
                       reverse=True)
    needed = [c["monthly_needed"] for c in active if c["monthly_needed"] is not None]
    totals = {
        "saved": _r(sum(c["saved"] for c in active)),
        "target": _r(sum(c["goal"]["target"] for c in active)),
        "remaining": _r(sum(c["remaining"] for c in active)),
        "monthly": _r(sum(needed)) if needed else None,
    }
    return render_template("goals/index.html", active=active, completed=completed, totals=totals,
                           icons=ICONS, pace_months=PACE_MONTHS)


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    v, error = _form_values()
    if error:
        flash(error, "warning")
        return redirect_back("goals.index")
    icon = form_choice("icon", ICONS, DEFAULT_ICON)
    execute(
        "INSERT INTO goals (user_id, name, icon, target, deadline, note) VALUES (?, ?, ?, ?, ?, ?)",
        (g.user["id"], v["name"], icon, v["target"], v["deadline"], v["note"]),
    )
    flash(f"{icon} {v['name']} hedefi eklendi.", "success")
    return redirect_back("goals.index")


@bp.route("/<int:goal_id>")
@login_required
def detail(goal_id):
    uid = g.user["id"]
    t = today()
    goal = owned_or_404("goals", goal_id, uid)
    if _sync_done(goal, _saved(uid, goal_id)):
        goal = owned_or_404("goals", goal_id, uid)
    rows = query("SELECT * FROM goal_entries WHERE goal_id = ? ORDER BY date, id", (goal_id,))
    balance, entries = 0.0, []
    for e in rows:
        balance = _r(balance + e["amount"])
        entries.append({"row": e, "balance": balance})
    entries.reverse()  # en yeni üstte
    since, until = _pace_window(t)
    recent = _r(sum(e["amount"] for e in rows if since < e["date"] <= until))
    card = _card(goal, balance, recent, t)
    return render_template("goals/detail.html", goal=goal, c=card, entries=entries, icons=ICONS,
                           self_url=url_for("goals.detail", goal_id=goal_id), pace_months=PACE_MONTHS)


@bp.route("/<int:goal_id>/duzenle", methods=["POST"])
@login_required
def edit(goal_id):
    uid = g.user["id"]
    goal = owned_or_404("goals", goal_id, uid)
    v, error = _form_values()
    if error:
        flash(error, "warning")
        return redirect(url_for("goals.detail", goal_id=goal_id))
    icon = form_choice("icon", ICONS, goal["icon"])
    execute(
        "UPDATE goals SET name = ?, icon = ?, target = ?, deadline = ?, note = ? WHERE id = ? AND user_id = ?",
        (v["name"], icon, v["target"], v["deadline"], v["note"], goal_id, uid),
    )
    flash("Hedef güncellendi.", "success")
    _sync_and_flash(uid, goal_id)
    return redirect(url_for("goals.detail", goal_id=goal_id))


@bp.route("/<int:goal_id>/sil", methods=["POST"])
@login_required
def delete(goal_id):
    uid = g.user["id"]
    goal = owned_or_404("goals", goal_id, uid)
    trash.move(uid, "goals", f"{goal['icon']} {goal['name']}", ("goals", goal_id),
               children=[("goal_entries", "goal_id = ?")])
    flash(trash.notice(f"{goal['icon']} {goal['name']}"), "success")
    return redirect(url_for("goals.index"))


@bp.route("/<int:goal_id>/kayit", methods=["POST"])
@login_required
def add_entry(goal_id):
    """Para ekle (kind=add) ya da çek (kind=withdraw). Eksi tutar da çekme sayılır."""
    uid = g.user["id"]
    goal = owned_or_404("goals", goal_id, uid)
    amount = form_float("amount")
    if not _valid_amount(amount) or _r(amount) == 0:
        flash("Geçerli bir tutar gir (ör. 500 ya da 1.250,50).", "warning")
        return redirect_back("goals.index")
    withdraw = request.form.get("kind") == "withdraw" or amount < 0
    amount = _r(abs(amount))

    raw_date = (request.form.get("date") or "").strip()
    d = parse_date(raw_date) if raw_date else today()
    if d is None:
        flash("Geçersiz tarih.", "warning")
        return redirect_back("goals.index")
    if d > today():
        flash("İleri tarihli kayıt eklenemez.", "warning")
        return redirect_back("goals.index")

    saved = _saved(uid, goal_id)
    if withdraw and amount > saved:
        flash(f"{goal['name']} hedefinde {fmt_money(saved)} var; bundan fazlası çekilemez.", "error")
        return redirect_back("goals.index")

    execute("INSERT INTO goal_entries (goal_id, amount, date, note) VALUES (?, ?, ?, ?)",
            (goal_id, -amount if withdraw else amount, d.isoformat(), form_str("note", 200)))
    verb = "çekildi" if withdraw else "eklendi"
    flash(f"{goal['icon']} {goal['name']}: {fmt_money(amount)} {verb}.", "success")
    _sync_and_flash(uid, goal_id)
    return redirect_back("goals.index")


@bp.route("/<int:goal_id>/kayit/<int:entry_id>/sil", methods=["POST"])
@login_required
def delete_entry(goal_id, entry_id):
    uid = g.user["id"]
    owned_or_404("goals", goal_id, uid)
    entry = query_one("SELECT * FROM goal_entries WHERE id = ? AND goal_id = ?", (entry_id, goal_id))
    if entry is None:
        abort(404)
    if _r(_saved(uid, goal_id) - entry["amount"]) < 0:
        flash("Bu kayıt silinirse birikim eksiye düşer. Önce para çekme kayıtlarını sil.", "error")
        return redirect_back("goals.detail", goal_id=goal_id)
    execute("DELETE FROM goal_entries WHERE id = ? AND goal_id = ?", (entry_id, goal_id))
    flash("Kayıt silindi.", "success")
    _sync_and_flash(uid, goal_id)
    return redirect_back("goals.detail", goal_id=goal_id)
