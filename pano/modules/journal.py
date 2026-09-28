"""📓 Günlük ve ruh hali: her gün bir satır ve bir emoji.

- Ay takviminde ruh haline göre renkler, "1 ay / 1 yıl önce bugün", aylık ortalama ve haftanın günlerine göre dağılım
- İsteğe bağlı akşam hatırlatması (Telegram): "📓 Bugün nasıldı?" + emoji butonları; bottan /gunluk metin
"""
import calendar as cal
from datetime import date, timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .. import todo_reminders as todo
from ..auth import login_required
from ..db import execute, get_db, query, query_one
from ..utils import MONTHS_TR, WEEKDAYS_TR_SHORT, add_months, form_int, form_str, redirect_back, today

bp = Blueprint("journal", __name__, url_prefix="/gunluk")

MOODS = {1: ("😞", "Kötü"), 2: ("😕", "Pek iyi değil"), 3: ("😐", "İdare eder"), 4: ("🙂", "İyi"), 5: ("😄", "Harika")}
TEXT_MAX = 4000


def save_entry(user_id, day, mood=None, text=None, append=False):
    """Günün kaydını oluşturur/günceller. mood/text None ise o alan korunur; append ile metin eklenir."""
    db = get_db()
    row = db.execute("SELECT * FROM journal WHERE user_id = ? AND date = ?", (user_id, day)).fetchone()
    if row is None:
        db.execute("INSERT INTO journal (user_id, date, mood, text, updated_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                   (user_id, day, mood, (text or "")[:TEXT_MAX]))
    else:
        new_text = row["text"]
        if text is not None:
            new_text = (row["text"] + "\n" + text).strip() if append and row["text"] else text
        db.execute("UPDATE journal SET mood = ?, text = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND date = ?",
                   (mood if mood is not None else row["mood"], new_text[:TEXT_MAX], user_id, day))
    db.commit()


def _parse_month(value):
    try:
        y, m = (int(p) for p in (value or "").split("-"))
        if 2000 <= y <= 2100 and 1 <= m <= 12:
            return y, m
    except ValueError:
        pass
    t = today()
    return t.year, t.month


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    uid = g.user["id"]
    t = today()
    if request.method == "POST":
        day = request.form.get("date") or t.isoformat()
        try:
            day = date.fromisoformat(day)
        except ValueError:
            day = t
        if day > t:
            flash("Geleceğe günlük yazılamaz.", "warning")
            return redirect(url_for(".index"))
        mood = form_int("mood")
        text = form_str("text", TEXT_MAX)
        if mood not in MOODS and not text and not query_one(
                "SELECT 1 FROM journal WHERE user_id = ? AND date = ?", (uid, day.isoformat())):
            flash("Bir ruh hali seç ya da birkaç satır yaz.", "warning")
            return redirect(url_for(".index"))
        save_entry(uid, day.isoformat(), mood if mood in MOODS else None, text)
        flash("Günlük kaydedildi.", "success")
        return redirect(url_for(".index", ay=day.strftime("%Y-%m")))

    year, month = _parse_month(request.args.get("ay"))
    first = date(year, month, 1)
    last = date(year, month, cal.monthrange(year, month)[1])
    entries = {r["date"]: r for r in query("SELECT * FROM journal WHERE user_id = ? AND date BETWEEN ? AND ?",
                                           (uid, first.isoformat(), last.isoformat()))}
    weeks = [[d if d.month == month else None for d in week]
             for week in cal.Calendar(firstweekday=0).monthdatescalendar(year, month)]
    moods = [e["mood"] for e in entries.values() if e["mood"]]

    # Geçmişte bugün
    memories = []
    for label, d in (("1 ay önce", add_months(t, -1)), ("1 yıl önce", add_months(t, -12)), ("2 yıl önce", add_months(t, -24))):
        row = query_one("SELECT * FROM journal WHERE user_id = ? AND date = ?", (uid, d.isoformat()))
        if row and (row["text"] or row["mood"]):
            memories.append((label, row))

    # Haftanın günlerine göre ortalama (son 180 gün)
    by_weekday = [[] for _ in range(7)]
    for r in query("SELECT date, mood FROM journal WHERE user_id = ? AND mood IS NOT NULL AND date >= ?",
                   (uid, (t - timedelta(days=180)).isoformat())):
        by_weekday[date.fromisoformat(r["date"]).weekday()].append(r["mood"])
    weekday_avg = [(WEEKDAYS_TR_SHORT[i], round(sum(v) / len(v), 1) if v else None) for i, v in enumerate(by_weekday)]

    today_entry = query_one("SELECT * FROM journal WHERE user_id = ? AND date = ?", (uid, t.isoformat()))
    edit_day = request.args.get("gun")
    edit_entry = None
    if edit_day:
        edit_entry = query_one("SELECT * FROM journal WHERE user_id = ? AND date = ?", (uid, edit_day))
    return render_template(
        "journal/index.html", moods=MOODS, entries=entries, weeks=weeks, year=year, month=month,
        month_name=MONTHS_TR[month - 1], prev=add_months(first, -1), nxt=add_months(first, 1), today=t,
        weekdays=WEEKDAYS_TR_SHORT, avg=round(sum(moods) / len(moods), 1) if moods else None, count=len(entries),
        memories=memories, weekday_avg=weekday_avg, has_weekday=any(v for _d, v in weekday_avg),
        today_entry=today_entry,
        edit_day=edit_day if edit_day else t.isoformat(), edit_entry=edit_entry if edit_day else today_entry,
        reminder=g.user["journal_reminder"],
    )


@bp.route("/ruh-hali", methods=["POST"])
@login_required
def quick_mood():
    """Panodaki emoji butonları: bugünün ruh hali (metne dokunmaz)."""
    mood = form_int("mood")
    if mood in MOODS:
        save_entry(g.user["id"], today().isoformat(), mood=mood)
        flash(f"{MOODS[mood][0]} Bugün: {MOODS[mood][1]}. Günlüğe birkaç satır da ekleyebilirsin.", "success")
    return redirect_back("journal.index")


@bp.route("/hatirlatma", methods=["POST"])
@login_required
def reminder():
    value = todo.parse_time(request.form.get("time")) if request.form.get("on") else None
    execute("UPDATE users SET journal_reminder = ? WHERE id = ?", (value, g.user["id"]))
    flash(f"Her akşam {value}'da Telegram'dan sorulacak." if value else "Günlük hatırlatması kapatıldı.", "success")
    return redirect(url_for(".index"))


@bp.route("/<day>/sil", methods=["POST"])
@login_required
def delete(day):
    execute("DELETE FROM journal WHERE user_id = ? AND date = ?", (g.user["id"], day[:10]))
    flash("Günlük kaydı silindi.", "success")
    return redirect(url_for(".index"))


# ---------- Telegram (cron /hatirlatma ve butonlar) ----------
def pending(now):
    """Hatırlatma saati gelmiş, bugün yazmamış ve bugün sorulmamış kullanıcılar."""
    t = now.date().isoformat()
    hm = now.strftime("%H:%M")
    out = []
    for u in query("SELECT * FROM users WHERE journal_reminder IS NOT NULL AND telegram_chat_id IS NOT NULL"):
        if hm < u["journal_reminder"]:
            continue
        if query_one("SELECT 1 FROM journal WHERE user_id = ? AND date = ?", (u["id"], t)):
            continue
        if query_one("SELECT 1 FROM app_state WHERE key = ? AND value = ?", (f"journal_ask:{u['id']}", t)):
            continue
        out.append(u)
    return out


def mark_asked(user_id, day):
    execute("INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)", (f"journal_ask:{user_id}", day))


def mood_buttons(day):
    return [[(emoji, f"jm:{day}:{n}") for n, (emoji, _label) in MOODS.items()]]


def ask_message(url, escape, interactive=True):
    if interactive:
        return ("📓 <b>Bugün nasıldı?</b>\nBir emojiye dokun; istersen <code>/gunluk bugün şöyle geçti...</code>"
                " diye de yazabilirsin.")
    return f'📓 <b>Bugün nasıldı?</b>\nGünlüğüne bir satır yazmayı unutma. <a href="{escape(url)}">Günlük →</a>'


def entry_text(day, row, escape):
    """Bot cevabı: '📓 28 Eyl: 😄 Harika' + metin."""
    from ..utils import fmt_date
    head = f"📓 <b>{fmt_date(day, True)}</b>"
    if row and row["mood"]:
        head += f": {MOODS[row['mood']][0]} {MOODS[row['mood']][1]}"
    lines = [head]
    if row and row["text"]:
        lines.append(f"<i>{escape(row['text'][:1500])}</i>")
    return "\n".join(lines)
