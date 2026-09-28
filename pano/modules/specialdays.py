"""🎂 Önemli Günler: doğum günleri, yıldönümleri ve her yıl tekrar eden diğer tarihler.

Seçilen gün sayısı kadar önce ve o gün 09:00'da Telegram'dan hatırlatılır (cron /hatirlatma);
pano "Yaklaşanlar" listesinde 30 gün önceden görünür. 29 Şubat artık yıl olmayan yıllarda 28 Şubat sayılır.
"""
import calendar
from datetime import date

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .. import trash
from ..auth import login_required
from ..db import execute, owned_or_404, query
from ..utils import MONTHS_TR, form_choice, form_int, form_str, parse_date, today

bp = Blueprint("specialdays", __name__, url_prefix="/onemli-gunler")

KINDS = {"birthday": ("Doğum günü", "🎂"), "anniversary": ("Yıldönümü", "💍"), "other": ("Diğer", "📌")}
NOTIFY_OPTIONS = [(0, "Sadece o gün"), (1, "1 gün önce"), (3, "3 gün önce"), (7, "1 hafta önce"),
                  (14, "2 hafta önce"), (30, "1 ay önce")]


def occurrence(month, day, year):
    """O yılki tarih (29 Şubat artık yıl değilse 28 Şubat)."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def next_occurrence(row, from_date=None):
    t = from_date or today()
    d = occurrence(row["month"], row["day"], t.year)
    return d if d >= t else occurrence(row["month"], row["day"], t.year + 1)


def ordinal_text(row, on):
    """'60. yaş' / '10. yıl' — yıl biliniyorsa."""
    if not row["year"] or on.year <= row["year"]:
        return ""
    n = on.year - row["year"]
    return f"{n}. yaş" if row["kind"] == "birthday" else f"{n}. yıl"


def upcoming_rows(user_id, within_days=None):
    """[(satır, sonraki_tarih, kalan_gün, sıra_metni)] en yakından uzağa."""
    t = today()
    out = []
    for r in query("SELECT * FROM special_days WHERE user_id = ?", (user_id,)):
        nxt = next_occurrence(r, t)
        days = (nxt - t).days
        if within_days is None or days <= within_days:
            out.append((r, nxt, days, ordinal_text(r, nxt)))
    out.sort(key=lambda x: (x[2], x[0]["name"]))
    return out


def _form_values():
    """(değerler, hata). Tarih <input type=date>; "yılı bilinmiyor" işaretliyse yıl saklanmaz."""
    d = parse_date(request.form.get("date"))
    v = {
        "name": form_str("name", 100),
        "kind": form_choice("kind", KINDS, "birthday"),
        "notify_days": form_int("notify_days"),
        "note": form_str("note", 500),
    }
    if v["notify_days"] not in dict(NOTIFY_OPTIONS):
        v["notify_days"] = 7
    if not v["name"]:
        return v, "Ad boş olamaz."
    if d is None:
        return v, "Tarih seçin."
    v.update(month=d.month, day=d.day, year=None if request.form.get("no_year") else d.year)
    return v, None


@bp.route("/")
@login_required
def index():
    return render_template("specialdays/index.html", rows=upcoming_rows(g.user["id"]), kinds=KINDS,
                           notify_options=NOTIFY_OPTIONS, months=MONTHS_TR)


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    v, error = _form_values()
    if error:
        flash(error, "error")
        return redirect(url_for(".index"))
    execute(
        "INSERT INTO special_days (user_id, name, kind, month, day, year, notify_days, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (g.user["id"], v["name"], v["kind"], v["month"], v["day"], v["year"], v["notify_days"], v["note"]),
    )
    flash(f"{KINDS[v['kind']][1]} {v['name']} eklendi.", "success")
    return redirect(url_for(".index"))


@bp.route("/<int:day_id>", methods=["GET", "POST"])
@login_required
def edit(day_id):
    row = owned_or_404("special_days", day_id, g.user["id"])
    if request.method == "POST":
        v, error = _form_values()
        if error:
            flash(error, "error")
            return redirect(url_for(".edit", day_id=day_id))
        # Tarih değiştiyse bu yılın hatırlatmaları yeniden gönderilebilsin
        reset = (v["month"], v["day"]) != (row["month"], row["day"])
        execute(
            "UPDATE special_days SET name = ?, kind = ?, month = ?, day = ?, year = ?, notify_days = ?, note = ?"
            + (", pre_sent_year = NULL, day_sent_year = NULL" if reset else "") + " WHERE id = ? AND user_id = ?",
            (v["name"], v["kind"], v["month"], v["day"], v["year"], v["notify_days"], v["note"], day_id, g.user["id"]),
        )
        flash("Güncellendi.", "success")
        return redirect(url_for(".index"))
    shown = date(row["year"] or 2000, row["month"], row["day"])  # 2000 artık yıl: 29 Şubat da gösterilebilir
    return render_template("specialdays/edit.html", row=row, shown=shown.isoformat(), kinds=KINDS,
                           notify_options=NOTIFY_OPTIONS)


@bp.route("/<int:day_id>/sil", methods=["POST"])
@login_required
def delete(day_id):
    row = owned_or_404("special_days", day_id, g.user["id"])
    trash.move(g.user["id"], "specialdays", f"🎂 {row['name']}", ("special_days", day_id))
    flash(trash.notice(row["name"]), "success")
    return redirect(url_for(".index"))


# ---------- Telegram hatırlatması (cron /hatirlatma) ----------
def pending(now):
    """[(tür, satır, tarih, kalan_gün)] — tür 'pre' (yaklaşıyor) ya da 'day' (bugün)."""
    t = now.date()
    out = []
    for r in query("SELECT s.*, u.telegram_chat_id AS chat_id FROM special_days s JOIN users u ON u.id = s.user_id"
                   " WHERE u.telegram_chat_id IS NOT NULL"):
        nxt = next_occurrence(r, t)
        days = (nxt - t).days
        if days == 0 and r["day_sent_year"] != nxt.year:
            out.append(("day", r, nxt, days))
        elif 0 < days <= r["notify_days"] and r["pre_sent_year"] != nxt.year:
            out.append(("pre", r, nxt, days))
    return out


def mark_sent(day_id, kind, year):
    column = "day_sent_year" if kind == "day" else "pre_sent_year"
    execute(f"UPDATE special_days SET {column} = ? WHERE id = ?", (year, day_id))


def message(kind, row, on, days, url, escape):
    label, icon = KINDS[row["kind"]]
    extra = ordinal_text(row, on)
    extra = f" ({extra})" if extra else ""
    when = "Bugün" if kind == "day" else ("Yarın" if days == 1 else f"{days} gün sonra")
    date_text = f"{on.day} {MONTHS_TR[on.month - 1]}"
    lines = [f"{icon} <b>{when}:</b> {escape(row['name'])}{escape(extra)}", date_text]
    if row["note"]:
        lines.append(f"<i>{escape(row['note'])}</i>")
    lines.append(f'<a href="{escape(url)}">Önemli günler →</a>')
    return "\n".join(lines)
