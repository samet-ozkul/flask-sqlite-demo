"""🔧 Ev Bakımı: kombi, klima, filtreler, dedektör pilleri gibi periyodik ev işleri.

- Her işin periyodu (n gün/hafta/ay/yıl) ve sıradaki tarihi var; "✅ Yaptım" denince
  sıradaki tarih = yapılma tarihi + periyot (ay sonu taşmaz: 31 Ocak + 1 ay = 28/29 Şubat)
- Hazır şablonlar önerilen periyot ve uygun ayla (ör. kombi bakımı Ekim) topluca eklenir
- Yapılma geçmişi, harcanan tutar; istenirse tutar Harcamalar'a da işlenir, son kayıt geri alınabilir
- Telegram (cron /hatirlatma, 09:00 sonrası): X gün önce ve günü gelince birer kez, gecikmişse haftada bir.
  "⏰ 1 hafta ertele" sıradaki tarihi max(sıradaki, bugün) + 7 güne alır (yapılacaklardaki "Yarına" gibi):
  pano, takvim ve hatırlatma aynı tarihi gösterir; iş yapılınca sonraki tarih yine yapılma gününden sayılır.
"""
from datetime import date, timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .. import automation, trash
from ..auth import login_required
from ..db import execute, get_db, owned_or_404, query, query_one
from ..utils import (MONTHS_TR, add_months, fmt_date, fold, form_bool, form_choice, form_date, form_str, parse_date,
                     redirect_back, today, today_str)
from .expenses import CATEGORIES as EXPENSE_CATEGORIES, category_icon, form_amount, valid_amount

bp = Blueprint("homecare", __name__, url_prefix="/ev-bakimi")

CATEGORIES = {
    "heating": ("Isıtma / soğutma", "🔥"),
    "water": ("Su", "💧"),
    "electric": ("Elektrik", "⚡"),
    "safety": ("Güvenlik", "🧯"),
    "cleaning": ("Temizlik", "🧽"),
    "kitchen": ("Mutfak / beyaz eşya", "🍽️"),
    "garden": ("Bahçe", "🌿"),
    "insurance": ("Sigorta / belge", "📄"),
    "other": ("Diğer", "🔧"),
}
UNITS = {"day": "gün", "week": "hafta", "month": "ay", "year": "yıl"}
EVERY = {"day": "günde", "week": "haftada", "month": "ayda", "year": "yılda"}
REMIND_OPTIONS = [(0, "Sadece günü gelince"), (1, "1 gün önce"), (3, "3 gün önce"), (7, "1 hafta önce"),
                  (14, "2 hafta önce"), (30, "1 ay önce")]
DEFAULT_REMIND = 3
SOON_DAYS = 3        # rozet en az bu kadar gün kala turuncu olur (hatırlatma daha erkense o kadar)
LATE_EVERY = 7       # gecikmiş iş kaç günde bir yeniden hatırlatılır
SNOOZE_DAYS = 7
MAX_INTERVAL = 365
EXPENSE_CATEGORY = "Ev"
TASK_SELECT = ("SELECT t.*, (SELECT MAX(done_on) FROM home_task_logs l WHERE l.task_id = t.id) AS last_done"
               " FROM home_tasks t")

# Hazır şablonlar: anahtar -> (ad, ikon, kategori, sayı, birim, uygun ay ya da None, kaç gün önce hatırlatılsın)
TEMPLATES = {
    "kombi": ("Kombi bakımı", "🔥", "heating", 1, "year", 10, 7),
    "petek": ("Petek havası alma", "♨️", "heating", 1, "year", 10, 3),
    "klima_filtre": ("Klima filtresi temizliği", "❄️", "heating", 3, "month", None, 3),
    "klima_bakim": ("Klima bakımı", "❄️", "heating", 1, "year", 5, 7),
    "baca": ("Baca / şofben kontrolü", "🧱", "heating", 1, "year", 10, 7),
    "aritma_filtre": ("Su arıtma filtresi", "💧", "water", 6, "month", None, 7),
    "aritma_membran": ("Su arıtma membranı", "💧", "water", 2, "year", None, 7),
    "kirec": ("Batarya / duş başlığı kireci", "🚿", "water", 6, "month", None, 3),
    "dedektor": ("Duman / gaz dedektörü pili", "🚨", "safety", 1, "year", None, 3),
    "yangin_tupu": ("Yangın tüpü kontrolü", "🧯", "safety", 1, "year", None, 7),
    "bulasik": ("Bulaşık makinesi filtresi", "🍽️", "kitchen", 1, "month", None, 1),
    "camasir": ("Çamaşır makinesi kireç / tambur temizliği", "🧺", "kitchen", 3, "month", None, 3),
    "buzdolabi": ("Buzdolabı arkası / kondenser temizliği", "🧊", "kitchen", 6, "month", None, 3),
    "aspirator": ("Aspiratör filtresi", "🌀", "kitchen", 3, "month", None, 3),
    "dask": ("DASK yenileme", "📄", "insurance", 1, "year", None, 14),
    "konut": ("Konut sigortası yenileme", "🏠", "insurance", 1, "year", None, 14),
}


# ---------- Periyot ----------
def advance(d, n, unit):
    """d + n gün/hafta/ay/yıl. Ay ve yıl add_months ile: ayın son gününü aşmaz (31 Ocak + 1 ay = 28/29 Şubat)."""
    if unit == "day":
        return d + timedelta(days=n)
    if unit == "week":
        return d + timedelta(weeks=n)
    return add_months(d, n * 12 if unit == "year" else n)


def period_label(n, unit):
    """(1, 'year') -> 'Yılda bir', (3, 'month') -> '3 ayda bir', (1, 'day') -> 'Her gün'."""
    if n == 1:
        return "Her gün" if unit == "day" else f"{EVERY[unit].capitalize()} bir"
    return f"{n} {EVERY[unit]} bir"


def task_period(task):
    return period_label(task["interval_n"], task["interval_unit"])


def default_due(n, unit, last_done=None):
    """Tarih boş bırakılınca: son yapılma (yoksa bugün) + periyot."""
    return advance(parse_date(last_done) or today(), n, unit).isoformat()


def template_due(tpl, t):
    """Şablonun ilk tarihi: uygun ayı varsa o ayın 1'i (bu aydaysak bugün), yoksa bugün + periyot."""
    _name, _icon, _category, n, unit, month, _remind = tpl
    if not month:
        return advance(t, n, unit)
    if month == t.month:
        return t
    return date(t.year if month > t.month else t.year + 1, month, 1)


def warn_days(task):
    return max(task["remind_days"], SOON_DAYS)


# ---------- Yardımcılar ----------
def _form_count(name):
    """Küçük tam sayı alanı (periyot, gün sayısı); boş/geçersizse None."""
    raw = form_str(name, 6)
    return int(raw) if raw.isascii() and raw.isdigit() else None


def _form_values():
    """(değerler, hata). İkon boşsa kategorinin ikonu."""
    v = {
        "name": form_str("name", 100),
        "category": form_choice("category", CATEGORIES, "other"),
        "icon": form_str("icon", 8),
        "interval_n": _form_count("interval_n"),
        "interval_unit": form_choice("interval_unit", UNITS, "month"),
        "next_due": form_date("next_due"),
        "remind_days": _form_count("remind_days"),
        "notes": form_str("notes", 1000),
    }
    if v["remind_days"] not in dict(REMIND_OPTIONS):
        v["remind_days"] = DEFAULT_REMIND
    if not v["icon"]:
        v["icon"] = CATEGORIES[v["category"]][1]
    if not v["name"]:
        return v, "İşin adı gerekli."
    if v["interval_n"] is None or not 1 <= v["interval_n"] <= MAX_INTERVAL:
        return v, f"Periyot 1-{MAX_INTERVAL} arasında bir sayı olmalı (ör. 6 ayda bir)."
    return v, None


def _last_done(task_id):
    row = query_one("SELECT MAX(done_on) AS d FROM home_task_logs WHERE task_id = ?", (task_id,))
    return row["d"] if row else None


def _render_ctx(**kw):
    return dict(categories=CATEGORIES, units=UNITS, every=EVERY, remind_options=REMIND_OPTIONS,
                default_remind=DEFAULT_REMIND, max_interval=MAX_INTERVAL, period_label=period_label,
                warn_days=warn_days, **kw)


# ---------- Yaptım / geri al / ertele (web ve Telegram ortak) ----------
def complete(task, done_on, cost=None, note="", add_expense=False, expense_category=EXPENSE_CATEGORY):
    """İşi yapıldı olarak kaydeder: geçmişe satır, sıradaki tarih = yapılma + periyot (geriye dönük bir kayıt
    daha yeni bir kaydın önüne geçmez); istenirse tutar harcamalara da işlenir. {'next_due', 'expense_id'} döner."""
    last = _last_done(task["id"])
    base = max(done_on, last) if last else done_on
    next_due = advance(parse_date(base), task["interval_n"], task["interval_unit"]).isoformat()
    db = get_db()
    expense_id, label = None, task["name"] + (f": {note}" if note else "")
    if add_expense and cost:
        expense_id = db.execute("INSERT INTO expenses (user_id, amount, category, note, date) VALUES (?, ?, ?, ?, ?)",
                                (task["user_id"], cost, expense_category, label[:500], done_on)).lastrowid
    db.execute("INSERT INTO home_task_logs (task_id, done_on, cost, note, expense_id, prev_due) VALUES (?, ?, ?, ?, ?, ?)",
               (task["id"], done_on, cost, note, expense_id, task["next_due"]))
    db.execute("UPDATE home_tasks SET next_due = ? WHERE id = ?", (next_due, task["id"]))
    db.commit()
    if expense_id:
        automation.fire("expense_added", task["user_id"], **{"tutar": cost, "kategori": expense_category, "not": label})
    return {"next_due": next_due, "expense_id": expense_id}


def undo_last(task):
    """Son kaydı siler, sıradaki tarihi o kayıttan önceki haline döndürür; harcamaya eklendiyse o da silinir.
    (silinen kayıt, harcama_silindi_mi) — kayıt yoksa (None, False)."""
    log = query_one("SELECT * FROM home_task_logs WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task["id"],))
    if log is None:
        return None, False
    db = get_db()
    dropped = False
    if log["expense_id"]:
        dropped = db.execute("DELETE FROM expenses WHERE id = ? AND user_id = ?",
                             (log["expense_id"], task["user_id"])).rowcount > 0
    db.execute("DELETE FROM home_task_logs WHERE id = ?", (log["id"],))
    if log["prev_due"]:
        db.execute("UPDATE home_tasks SET next_due = ? WHERE id = ?", (log["prev_due"], task["id"]))
    db.commit()
    return log, dropped


def done_on_day(task_id, day):
    return query_one("SELECT 1 FROM home_task_logs WHERE task_id = ? AND done_on = ?", (task_id, day)) is not None


def snooze(task, days=SNOOZE_DAYS):
    """Sıradaki tarih max(sıradaki, bugün) + days olur. O tarih için ön hatırlatma gönderilmiş sayılır:
    bir sonraki mesaj yeni tarihin gününde gelir. Yeni tarihi döner."""
    t = today()
    new_due = (max(parse_date(task["next_due"]) or t, t) + timedelta(days=days)).isoformat()
    execute("UPDATE home_tasks SET next_due = ?, reminded_for = ?, reminded_on = ? WHERE id = ?",
            (new_due, new_due, t.isoformat(), task["id"]))
    return new_due


# ---------- Rotalar ----------
@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    cat = request.args.get("kategori")
    cat = cat if cat in CATEGORIES else None
    rows = query(TASK_SELECT + " WHERE t.user_id = ? ORDER BY t.next_due, t.name COLLATE NOCASE, t.id", (uid,))
    counts = {}
    for r in rows:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    shown = [r for r in rows if not cat or r["category"] == cat]
    t = today()
    ts, week = t.isoformat(), (t + timedelta(days=7)).isoformat()
    active = [r for r in rows if r["active"]]
    summary = [f"{len(active)} etkin iş"]
    overdue = sum(1 for r in active if r["next_due"] < ts)
    soon = sum(1 for r in active if ts <= r["next_due"] <= week)
    if overdue:
        summary.append(f"{overdue} gecikmiş")
    if soon:
        summary.append(f"{soon} iş 7 gün içinde")
    added = {fold(r["name"]) for r in rows}
    templates = [{"key": key, "name": tpl[0], "icon": tpl[1], "period": period_label(tpl[3], tpl[4]),
                  "month": MONTHS_TR[tpl[5] - 1] if tpl[5] else None, "due": template_due(tpl, t).isoformat(),
                  "added": fold(tpl[0]) in added} for key, tpl in TEMPLATES.items()]
    return render_template(
        "homecare/index.html", **_render_ctx(
            active=[r for r in shown if r["active"]], inactive=[r for r in shown if not r["active"]], total=len(rows),
            counts=counts, cat=cat, summary=" · ".join(summary), templates=templates))


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    v, error = _form_values()
    if error:
        flash(error, "error")
        return redirect(url_for(".index"))
    due = v["next_due"] or default_due(v["interval_n"], v["interval_unit"])
    execute(
        "INSERT INTO home_tasks (user_id, name, icon, category, interval_n, interval_unit, next_due, remind_days, notes)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (g.user["id"], v["name"], v["icon"], v["category"], v["interval_n"], v["interval_unit"], due,
         v["remind_days"], v["notes"]),
    )
    flash(f"{v['icon']} {v['name']} eklendi · sıradaki {fmt_date(due)}.", "success")
    return redirect(url_for(".index"))


@bp.route("/sablonlar", methods=["POST"])
@login_required
def add_templates():
    """İşaretlenen şablonları ekler; aynı adlı iş zaten varsa (pasif olsa da) atlanır."""
    uid = g.user["id"]
    keys = [k for k in dict.fromkeys(request.form.getlist("tpl")) if k in TEMPLATES]
    if not keys:
        flash("Eklemek için en az bir şablon seç.", "warning")
        return redirect(url_for(".index"))
    existing = {fold(r["name"]) for r in query("SELECT name FROM home_tasks WHERE user_id = ?", (uid,))}
    t = today()
    added, skipped = [], []
    db = get_db()
    for key in keys:
        tpl = TEMPLATES[key]
        name, icon, category, n, unit, _month, remind = tpl
        if fold(name) in existing:
            skipped.append(name)
            continue
        db.execute(
            "INSERT INTO home_tasks (user_id, name, icon, category, interval_n, interval_unit, next_due, remind_days)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, name, icon, category, n, unit, template_due(tpl, t).isoformat(), remind),
        )
        existing.add(fold(name))
        added.append(name)
    db.commit()
    note = f" Zaten listende olduğu için atlandı: {', '.join(skipped)}." if skipped else ""
    if added:
        flash(f"{len(added)} iş eklendi.{note}", "success")
    else:
        flash(f"Yeni iş eklenmedi.{note}", "warning")
    return redirect(url_for(".index"))


@bp.route("/<int:task_id>")
@login_required
def detail(task_id):
    uid = g.user["id"]
    task = owned_or_404("home_tasks", task_id, uid)
    logs = query(
        "SELECT l.*, e.id AS expense_live FROM home_task_logs l"
        " LEFT JOIN expenses e ON e.id = l.expense_id AND e.user_id = ?"
        " WHERE l.task_id = ? ORDER BY l.done_on DESC, l.id DESC", (uid, task_id),
    )
    last = max(logs, key=lambda r: r["id"]) if logs else None
    stats = {
        "count": len(logs),
        "last_done": logs[0]["done_on"] if logs else None,
        "total": round(sum(r["cost"] or 0 for r in logs), 2),
        "paid": sum(1 for r in logs if r["cost"]),
    }
    return render_template("homecare/detail.html", **_render_ctx(
        t=task, logs=logs, last=last, stats=stats, expense_categories=EXPENSE_CATEGORIES,
        expense_category=EXPENSE_CATEGORY, category_icon=category_icon))


@bp.route("/<int:task_id>/duzenle", methods=["POST"])
@login_required
def update(task_id):
    uid = g.user["id"]
    owned_or_404("home_tasks", task_id, uid)
    v, error = _form_values()
    if error:
        flash(error, "error")
        return redirect(url_for(".detail", task_id=task_id))
    due = v["next_due"] or default_due(v["interval_n"], v["interval_unit"], _last_done(task_id))
    execute(
        "UPDATE home_tasks SET name = ?, icon = ?, category = ?, interval_n = ?, interval_unit = ?, next_due = ?,"
        " remind_days = ?, notes = ? WHERE id = ? AND user_id = ?",
        (v["name"], v["icon"], v["category"], v["interval_n"], v["interval_unit"], due, v["remind_days"], v["notes"],
         task_id, uid),
    )
    flash("İş güncellendi.", "success")
    return redirect(url_for(".detail", task_id=task_id))


@bp.route("/<int:task_id>/yaptim", methods=["POST"])
@login_required
def done(task_id):
    task = owned_or_404("home_tasks", task_id, g.user["id"])
    done_on = form_date("done_on") or today_str()
    raw_cost = form_str("cost", 30)
    cost = form_amount("cost") if raw_cost else None
    if done_on > today_str():
        flash("Yapılma tarihi ileri bir gün olamaz.", "error")
        return redirect_back("homecare.detail", task_id=task_id)
    if raw_cost and not valid_amount(cost):
        flash("Tutar 0'dan büyük bir sayı olmalı (ör. 1.250) ya da boş bırakın.", "error")
        return redirect_back("homecare.detail", task_id=task_id)
    result = complete(task, done_on, round(cost, 2) if cost else None, form_str("note", 500),
                      add_expense=bool(form_bool("add_expense")),
                      expense_category=form_choice("expense_category", EXPENSE_CATEGORIES, EXPENSE_CATEGORY))
    text = f"✅ {task['name']} kaydedildi. Sıradaki: {fmt_date(result['next_due'], True)}."
    if result["expense_id"]:
        text += " Harcamalara da eklendi."
    flash(text, "success")
    return redirect_back("homecare.detail", task_id=task_id)


@bp.route("/<int:task_id>/geri-al", methods=["POST"])
@login_required
def undo(task_id):
    task = owned_or_404("home_tasks", task_id, g.user["id"])
    log, dropped = undo_last(task)
    if log is None:
        flash("Geri alınacak kayıt yok.", "warning")
    else:
        text = f"↩️ {fmt_date(log['done_on'])} kaydı geri alındı. Sıradaki tarih: {fmt_date(log['prev_due'], True)}."
        flash(text + (" Harcama kaydı da silindi." if dropped else ""), "success")
    return redirect(url_for(".detail", task_id=task_id))


@bp.route("/<int:task_id>/durum", methods=["POST"])
@login_required
def toggle(task_id):
    uid = g.user["id"]
    task = owned_or_404("home_tasks", task_id, uid)
    execute("UPDATE home_tasks SET active = 1 - active WHERE id = ? AND user_id = ?", (task_id, uid))
    if task["active"]:
        flash(f"{task['name']} durduruldu; hatırlatma gelmez.", "success")
    else:
        flash(f"{task['name']} yeniden etkin.", "success")
    return redirect_back("homecare.detail", task_id=task_id)


@bp.route("/<int:task_id>/sil", methods=["POST"])
@login_required
def delete(task_id):
    uid = g.user["id"]
    task = owned_or_404("home_tasks", task_id, uid)
    trash.move(uid, "homecare", f"{task['icon']} {task['name']}", ("home_tasks", task_id),
               children=[("home_task_logs", "task_id = ?")])
    flash(trash.notice(task["name"]), "success")
    return redirect(url_for(".index"))


# ---------- Telegram hatırlatması (cron /hatirlatma) ----------
def pending(now):
    """[(tür, iş, kalan_gün)] — 'pre' (X gün kaldı), 'day' (bugün), 'late' (gecikti).
    Bir tarih için ön hatırlatma ve gün mesajı birer kez gider; gecikmişse son mesajdan LATE_EVERY gün sonra
    yeniden. reminded_for sıradaki tarihten farklıysa (iş yapıldı, tarih değişti) o tarih için hiç gönderilmemiştir."""
    t = now.date()
    horizon = (t + timedelta(days=max(d for d, _label in REMIND_OPTIONS))).isoformat()
    out = []
    for r in query(
        "SELECT t.*, u.telegram_chat_id AS chat_id,"
        " (SELECT MAX(done_on) FROM home_task_logs l WHERE l.task_id = t.id) AS last_done"
        " FROM home_tasks t JOIN users u ON u.id = t.user_id"
        " WHERE u.telegram_chat_id IS NOT NULL AND t.active = 1 AND t.next_due <= ?", (horizon,)
    ):
        due = parse_date(r["next_due"])
        if due is None:
            continue
        left = (due - t).days
        last = parse_date(r["reminded_on"]) if r["reminded_for"] == r["next_due"] else None
        if left < 0:
            if last is None or last < due or (t - last).days >= LATE_EVERY:
                out.append(("late", r, left))
        elif left == 0:
            if last is None or last < t:
                out.append(("day", r, left))
        elif left <= r["remind_days"] and last is None:
            out.append(("pre", r, left))
    return out


def mark_sent(task, day):
    execute("UPDATE home_tasks SET reminded_for = ?, reminded_on = ? WHERE id = ?",
            (task["next_due"], day.isoformat(), task["id"]))


def buttons(task_id):
    return [[("✅ Yaptım", f"hm:done:{task_id}"), ("⏰ 1 hafta ertele", f"hm:snz:{task_id}")]]


def message(kind, task, left, url, escape):
    name = escape(task["name"])
    if kind == "day":
        head = f"{task['icon']} <b>Bugün:</b> {name}"
    elif kind == "late":
        head = f"{task['icon']} <b>{-left} gün gecikti:</b> {name}"
    else:
        when = "Yarın" if left == 1 else f"{left} gün sonra"
        head = f"{task['icon']} <b>{when}:</b> {name}"
    detail = [fmt_date(task["next_due"], True), task_period(task)]
    if task["last_done"]:
        detail.append(f"son yapılma {fmt_date(task['last_done'])}")
    lines = [head, "📅 " + " · ".join(detail)]
    if task["notes"]:
        lines.append(f"<i>{escape(task['notes'][:300])}</i>")
    lines.append(f'<a href="{escape(url)}">Ev Bakımı →</a>')
    return "\n".join(lines)
