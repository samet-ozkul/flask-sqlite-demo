"""📅 Etkinlikler: aile/ev için ortak takvim kayıtları ("cumartesi 19:00 annemlerde yemek").

- shared=1 (varsayılan) etkinliği uygulamadaki herkes görür; takvim, pano ve telefon takvimi aboneliğinde çıkar
- Sadece ekleyen düzenler/siler; diğerleri görür
- Hatırlatma: yapılacaklardaki seçeneklerin aynısı; paylaşılan etkinlikte Telegram'ı bağlı herkese gider
"""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import todo_reminders as todo
from .. import trash
from ..auth import login_required
from ..db import execute, query, query_one
from ..utils import form_bool, form_date, form_str, now_local, today_str

bp = Blueprint("events", __name__, url_prefix="/etkinlikler")

_SELECT = ("SELECT e.*, u.username AS owner_username, u.display_name AS owner_display"
           " FROM events e JOIN users u ON u.id = e.user_id")


def visible_events(user_id, where="", args=()):
    """Kullanıcının kendi etkinlikleri + paylaşılanlar."""
    return query(_SELECT + " WHERE (e.user_id = ? OR e.shared = 1)" + where + " ORDER BY e.date, e.time IS NULL, e.time, e.id",
                 (user_id, *args))


def _event_or_404(event_id, user_id, owner_only=False):
    row = query_one(_SELECT + " WHERE e.id = ? AND (e.user_id = ? OR e.shared = 1)", (event_id, user_id))
    if row is None:
        abort(404)
    if owner_only and row["user_id"] != user_id:
        abort(403)
    return row


def _form_values():
    date = form_date("date")
    return {
        "title": form_str("title", 150),
        "date": date,
        "time": todo.parse_time(request.form.get("time")),
        "place": form_str("place", 150),
        "note": form_str("note", 1000),
        "shared": form_bool("shared"),
        "remind_before": todo.parse_remind(request.form.get("remind")) if date else None,
    }


def _validate(v):
    if not v["title"]:
        return "Başlık boş olamaz."
    if not v["date"]:
        return "Tarih seçin."
    return None


@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    now = now_local()
    t, hm = now.date().isoformat(), now.strftime("%H:%M")
    upcoming = [e for e in visible_events(uid, " AND e.date >= ?", (t,))
                if e["date"] > t or not e["time"] or e["time"] >= hm]
    past = query(_SELECT + " WHERE (e.user_id = ? OR e.shared = 1) AND e.date < ? ORDER BY e.date DESC, e.id DESC LIMIT 20",
                 (uid, t))
    return render_template("events/index.html", upcoming=upcoming, past=past,
                           remind_options=todo.REMIND_OPTIONS, remind_label=todo.remind_label,
                           default_remind=todo.DEFAULT_REMIND, default_time=todo.DEFAULT_DUE_TIME, today=today_str())


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    v = _form_values()
    error = _validate(v)
    if error:
        flash(error, "error")
        return redirect(url_for(".index"))
    execute(
        "INSERT INTO events (user_id, title, date, time, place, note, shared, remind_before) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (g.user["id"], v["title"], v["date"], v["time"], v["place"], v["note"], v["shared"], v["remind_before"]),
    )
    flash(f"📅 {v['title']} eklendi" + (" (herkes görebilir)." if v["shared"] else "."), "success")
    return redirect(url_for(".index"))


@bp.route("/<int:event_id>", methods=["GET", "POST"])
@login_required
def edit(event_id):
    uid = g.user["id"]
    event = _event_or_404(event_id, uid)
    if request.method == "POST":
        if event["user_id"] != uid:
            abort(403)
        v = _form_values()
        error = _validate(v)
        if error:
            flash(error, "error")
            return redirect(url_for(".edit", event_id=event_id))
        changed = (v["date"], v["time"], v["remind_before"]) != (event["date"], event["time"], event["remind_before"])
        execute(
            "UPDATE events SET title = ?, date = ?, time = ?, place = ?, note = ?, shared = ?, remind_before = ?"
            + (", pre_sent_at = NULL, due_sent_at = NULL" if changed else "") + " WHERE id = ? AND user_id = ?",
            (v["title"], v["date"], v["time"], v["place"], v["note"], v["shared"], v["remind_before"], event_id, uid),
        )
        flash("Etkinlik güncellendi.", "success")
        return redirect(url_for(".index"))
    return render_template("events/edit.html", e=event, is_owner=event["user_id"] == uid,
                           remind_options=todo.REMIND_OPTIONS, default_time=todo.DEFAULT_DUE_TIME)


@bp.route("/<int:event_id>/sil", methods=["POST"])
@login_required
def delete(event_id):
    event = _event_or_404(event_id, g.user["id"], owner_only=True)
    trash.move(g.user["id"], "events", f"📅 {event['title']} ({event['date']})", ("events", event_id))
    flash(trash.notice(event["title"]), "success")
    return redirect(url_for(".index"))


# ---------- Hatırlatma (cron /hatirlatma) ----------
def pending(now):
    """Yapılacaklardaki kuralların aynısı: ([(tür, etkinlik)], [eski etkinlikler])."""
    from datetime import timedelta
    t = now.date()
    rows = query(
        "SELECT * FROM events WHERE remind_before IS NOT NULL AND date BETWEEN ? AND ?"
        " AND (due_sent_at IS NULL OR (remind_before > 0 AND pre_sent_at IS NULL))",
        ((t - timedelta(days=2)).isoformat(), (t + timedelta(days=2)).isoformat()))
    to_send, stale = [], []
    for e in rows:
        when = todo.due_at({"due_date": e["date"], "due_time": e["time"]})
        if e["due_sent_at"] is None and now >= when:
            if now - when <= todo.LATE_WINDOW:
                to_send.append(("due", e))
            else:
                stale.append(e)  # cron uzun süre çalışmadıysa eski mesaj gönderilmez
        elif e["remind_before"] > 0 and e["pre_sent_at"] is None and \
                when - timedelta(minutes=e["remind_before"]) <= now < when:
            to_send.append(("pre", e))
    return to_send, stale


def mark_sent(event_id, kind):
    column = "due_sent_at" if kind == "due" else "pre_sent_at"
    extra = ", pre_sent_at = COALESCE(pre_sent_at, CURRENT_TIMESTAMP)" if kind == "due" else ""
    execute(f"UPDATE events SET {column} = CURRENT_TIMESTAMP{extra} WHERE id = ?", (event_id,))


def recipients(event):
    """Paylaşılan etkinlik: Telegram'ı bağlı herkes; değilse sadece ekleyen."""
    if event["shared"]:
        return [r["telegram_chat_id"] for r in query("SELECT telegram_chat_id FROM users WHERE telegram_chat_id IS NOT NULL")]
    row = query_one("SELECT telegram_chat_id FROM users WHERE id = ?", (event["user_id"],))
    return [row["telegram_chat_id"]] if row and row["telegram_chat_id"] else []


def message(kind, event, url, escape):
    from ..utils import fmt_date
    head = "⏰ <b>Yaklaşıyor:</b>" if kind == "pre" else "📅 <b>Etkinlik:</b>"
    when = fmt_date(event["date"], True) + (f" {event['time']}" if event["time"] else "")
    lines = [f"{head} {escape(event['title'])}", when + (f" · {escape(event['place'])}" if event["place"] else "")]
    if event["note"]:
        lines.append(f"<i>{escape(event['note'])}</i>")
    lines.append(f'<a href="{escape(url)}">Etkinlikler →</a>')
    return "\n".join(lines)
