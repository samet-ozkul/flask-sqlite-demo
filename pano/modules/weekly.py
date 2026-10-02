"""🗓️ Haftalık Değerlendirme: her hafta sonu kısa bir gözden geçirme.

- /haftalik/?hafta=2026-09-28: hafta pazartesi–pazar (yerel saat); adres haftanın herhangi bir günüyle de açılır.
  Varsayılan bugünün haftası: pazartesi–cumartesi "devam ediyor", pazar değerlendirme günü. Gelecek haftaya geçilmez.
- Haftanın sayıları (verisi olanlar): harcama ve önceki haftaya göre değişim (devam eden haftada önceki haftanın aynı
  günleriyle), alışkanlık başarı oranı, tamamlanan görevler, günlük ve ortalama ruh hali, zaman takibi, bitirilen
  kitap/film, biten kanban kartları, ödenen faturalar; bu haftada önümüzdeki 7 günün yaklaşanları. Sorgular Yıl
  Özeti'ninkiler (aynı UTC/yerel gün ayrımıyla, haftalık aralıkta); sadece kullanıcının kendi kayıtları.
- Değerlendirme: puan (günlükteki 5 emoji), iyi giden / zorlayan / öğrendiklerim ve gelecek haftanın en fazla 3
  önceliği. Geçen hafta yazılan öncelikler bu haftanın sayfasında işaretlenir; tamamlanma oranı geçmişte görünür.
- Telegram: pazar günü seçilen saatten sonra bir kez (app_state weekly_ask:<id> = hafta başı) sayılar, form linki ve
  puan butonları (wk:<hafta başı>:<1-5>); pazartesi günlük özetinde "📌 Bu haftanın öncelikleri"; bottan /hafta.
"""
import json
from collections import Counter, defaultdict
from datetime import timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import telegram, trash
from .. import todo_reminders as todo
from ..auth import login_required
from ..db import execute, get_db, query, query_one
from ..reminders import upcoming
from ..utils import MONTHS_TR_SHORT, fmt_money, fmt_number, fold, form_int, form_str, local_dt, parse_date
from .expenses import category_icon
from .journal import MOODS
from .timetrack import entries_between, fmt_duration, summarize
from .watchlist import KINDS as WATCH_KINDS
from .yearreview import OWN_DONE_CARDS, WATCH_WORDS, _bills, _journal, _tasks, range_params, watch_label

bp = Blueprint("weekly", __name__, url_prefix="/haftalik")

SCORES = MOODS  # haftanın puanı 1–5: günlükteki ruh hali emojileri
QUESTIONS = {"went_well": "Bu hafta iyi giden neydi?", "hard": "Ne zorladı?", "learned": "Ne öğrendim / not"}
TEXT_MAX = 2000
PRIORITIES = 3          # gelecek haftanın en fazla bu kadar önceliği
PRIORITY_MAX = 200
CHART_WEEKS = 12
HISTORY_SHOWN = 20
UPCOMING_DAYS = 7
BUTTON_WEEKS = 4        # Telegram puan butonu en fazla bu kadar önceki haftaya yazar
DEFAULT_PROMPT = "20:00"
SECTIONS = ("expenses", "habits", "tasks", "journal", "time", "watch", "cards", "bills")


# ---------- Hafta ----------
def now_local():
    """Testlerde todo_reminders.now_local taklit edilir."""
    return todo.now_local()


def week_start(d):
    """Günün haftasının pazartesisi."""
    return d - timedelta(days=d.weekday())


def week_label(start):
    """'28 Eyl – 4 Eki'; yıl değiştiren ya da bu yıldan olmayan haftada yıllarla."""
    end = start + timedelta(days=6)
    first, last = (f"{d.day} {MONTHS_TR_SHORT[d.month - 1]}" for d in (start, end))
    if start.year != end.year:
        first, last = f"{first} {start.year}", f"{last} {end.year}"
    elif end.year != now_local().year:
        last += f" {end.year}"
    return f"{first} – {last}"


def pick_week(raw, t):
    """İstenen günün haftası; geçersizse ya da gelecekteyse bugünün haftası."""
    current = week_start(t)
    d = parse_date(raw)
    if d is None or d.year < 2000:
        return current
    return min(week_start(d), current)


def week_status(start, t):
    """Hafta başlığının yanındaki rozet metni."""
    current = week_start(t)
    if start == current:
        return "Bu hafta · değerlendirme günü" if t.weekday() == 6 else "Bu hafta (devam ediyor)"
    if start == current - timedelta(days=7):
        return "Geçen hafta"
    return ""


def can_score(start, t):
    """Telegram puan butonu: bu hafta ya da son BUTTON_WEEKS hafta (hafta başı pazartesi olmalı)."""
    current = week_start(t)
    return start.weekday() == 0 and current - timedelta(weeks=BUTTON_WEEKS) <= start <= current


# ---------- Haftanın sayıları (her biri veri yoksa None) ----------
def _expenses(p, start, t):
    rows = query("SELECT category, COUNT(*) AS n, SUM(amount) AS s FROM expenses"
                 " WHERE user_id = :u AND date >= :a AND date < :b GROUP BY category", p)
    if not rows:
        return None
    total = round(sum(r["s"] for r in rows), 2)
    top = min(rows, key=lambda r: (-r["s"], r["category"]))
    # Önceki haftayla: biten haftada tüm hafta, devam eden haftada önceki haftanın aynı günleri (pazartesi – bugün)
    nxt = start + timedelta(days=7)
    cut = min(nxt, t + timedelta(days=1))
    cmp = query_one("SELECT COALESCE(SUM(CASE WHEN date >= :a AND date < :cut THEN amount END), 0) AS cur,"
                    " COALESCE(SUM(CASE WHEN date < :pcut THEN amount END), 0) AS prev"
                    " FROM expenses WHERE user_id = :u AND date >= :pa AND date < :b",
                    {**p, "cut": cut.isoformat(), "pcut": (cut - timedelta(days=7)).isoformat(),
                     "pa": (start - timedelta(days=7)).isoformat()})
    prev = round(cmp["prev"], 2)
    change = (cmp["cur"] - prev) * 100 / prev if prev else None
    return {
        "total": total, "count": sum(r["n"] for r in rows),
        "top": (top["category"], round(top["s"], 2)), "top_pct": round(top["s"] * 100 / total) if total else 0,
        "prev": prev, "change": change, "change_pct": abs(round(change)) if change is not None else None,
        "cmp_label": "önceki haftanın aynı günlerine göre" if cut < nxt else "önceki haftaya göre",
    }


def _habits(p, start, t):
    """Başarı oranı = işaretlenen gün / beklenen gün. Beklenen: aktif (ya da bu hafta işaretlenmiş) her alışkanlık için
    haftanın (alışkanlık hafta içinde eklendiyse eklendiği günün) başından hafta sonuna, devam eden haftada bugüne."""
    end = min(start + timedelta(days=6), t)
    logs = defaultdict(set)
    for r in query("SELECT l.habit_id, l.date FROM habit_logs l JOIN habits h ON h.id = l.habit_id"
                   " WHERE h.user_id = :u AND l.date >= :a AND l.date < :b", p):
        d = parse_date(r["date"])
        if d and d <= end:
            logs[r["habit_id"]].add(d)
    stats = []
    for h in query("SELECT * FROM habits WHERE user_id = :u ORDER BY id", p):
        days = logs.get(h["id"], set())
        if not h["active"] and not days:
            continue
        created = parse_date(local_dt(h["created_at"], "%Y-%m-%d")) or start
        expected = (end - max(start, min([created] + list(days)))).days + 1
        if expected <= 0:
            continue
        stats.append({"icon": h["icon"], "name": h["name"], "done": len(days), "expected": expected,
                      "rate": round(100 * len(days) / expected)})
    if not stats:
        return None
    stats.sort(key=lambda s: (-s["rate"], -s["done"], fold(s["name"])))
    done, expected = sum(s["done"] for s in stats), sum(s["expected"] for s in stats)
    return {"done": done, "expected": expected, "rate": round(100 * done / expected), "habits": stats,
            "best": stats[0] if stats[0]["done"] else None}


def _time(user_id, start, now):
    rows = entries_between(user_id, start, start + timedelta(days=7))
    if not rows:
        return None
    s = summarize(rows, now)
    if s["total"] < 60:   # bir dakikadan kısa (yanlışlıkla başlatıp durdurulmuş) kayıtlar "0 dk" satırı göstermesin
        return None
    return {"total": s["total"], "count": s["count"], "top": s["projects"][0] if s["projects"] else None}


def _watch(p):
    rows = query("SELECT kind, title FROM watchlist WHERE user_id = :u AND status = 'done'"
                 " AND finished_at >= :a AND finished_at < :b ORDER BY finished_at, id", p)
    counts = Counter(r["kind"] for r in rows if r["kind"] in WATCH_WORDS)
    if not counts:
        return None
    return {"total": sum(counts.values()), "label": watch_label(counts),
            "words": [(counts[k], WATCH_WORDS[k]) for k in WATCH_WORDS if counts[k]],
            "titles": [(WATCH_KINDS[r["kind"]][1], r["title"]) for r in rows if r["kind"] in WATCH_WORDS]}


def _cards(p):
    """Kendi panolarında "bitti" sütununa geçen kendi kartları (son taşınma/düzenleme anı bu haftada)."""
    return query_one(f"SELECT COUNT(*) AS n FROM {OWN_DONE_CARDS} AND c.updated_at >= :ua AND c.updated_at < :ub",
                     p)["n"] or None


def numbers(user_id, start, now):
    """Haftanın sayıları (sayfa, pazar mesajı ve /hafta ortak kullanır)."""
    t = now.date()
    p = range_params(user_id, start, start + timedelta(days=7))
    n = {"expenses": _expenses(p, start, t), "habits": _habits(p, start, t), "tasks": _tasks(p),
         "journal": _journal(p), "time": _time(user_id, start, now), "watch": _watch(p), "cards": _cards(p),
         "bills": _bills(p)}
    n["empty"] = not any(n[k] for k in SECTIONS)
    return n


def coming_up(user_id):
    """Önümüzdeki 7 günün yaklaşanları (bugün dahil; gecikenler panoda zaten görünür)."""
    return [it for it in upcoming(user_id, days=UPCOMING_DAYS, long_days=UPCOMING_DAYS)
            if 0 <= it["days"] <= UPCOMING_DAYS]


def arrow(change):
    return "▲" if change > 0 else "▼" if change < 0 else "="


def mood_emoji(avg):
    """Ortalama ruh hali -> en yakın emoji (4,5 -> 😄)."""
    return MOODS[min(5, max(1, int(avg + 0.5)))][0]


def numbers_lines(n, escape):
    """Sayıların kısa metin hali (Telegram HTML; kullanıcı verisi escape ile)."""
    lines = []
    e = n["expenses"]
    if e:
        change = f" ({arrow(e['change'])} %{e['change_pct']} {e['cmp_label']})" if e["change"] is not None else ""
        c, v = e["top"]
        lines.append(f"💸 Harcama: <b>{fmt_money(e['total'])}</b>{change}")
        lines.append(f"    En çok: {category_icon(c)} {escape(c)} · {fmt_money(v)}")
    h = n["habits"]
    if h:
        best = f" · en iyi {h['best']['icon']} {escape(h['best']['name'])}" if h["best"] else ""
        lines.append(f"🔥 Alışkanlık: %{h['rate']} ({h['done']}/{h['expected']}){best}")
    if n["tasks"] and n["tasks"]["todo"]:
        lines.append(f"✅ {n['tasks']['todo']} görev tamamlandı")
    j = n["journal"]
    if j:
        lines.append(f"📓 Günlük: {j['days']} gün"
                     + (f" · ort. ruh hali {mood_emoji(j['avg'])} {fmt_number(j['avg'], 1)}" if j["avg"] else ""))
    tm = n["time"]
    if tm:
        lines.append(f"⏱️ Zaman takibi: {fmt_duration(tm['total'])}"
                     + (f" · en çok {escape(tm['top']['name'])}" if tm["top"] else ""))
    w = n["watch"]
    if w:
        lines.append("🎬 Bitirilen: " + ", ".join(f"{k} {word}" for k, word in w["words"]))
    if n["cards"]:
        lines.append(f"🗂️ {n['cards']} kart bitti")
    if n["bills"]:
        lines.append(f"🧾 {n['bills']['count']} fatura ödendi · {fmt_money(n['bills']['total'])}")
    return lines


# ---------- Değerlendirme kayıtları ----------
def parse_priorities(raw):
    """Veritabanındaki JSON -> [{"text", "done"}] (bozuk ya da boşsa [])."""
    try:
        items = json.loads(raw or "[]")
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    return [{"text": str(p["text"])[:PRIORITY_MAX], "done": bool(p.get("done"))}
            for p in items if isinstance(p, dict) and p.get("text")][:PRIORITIES]


def _dump(items):
    return json.dumps(items, ensure_ascii=False)


def priority_rate(items):
    """(yapılan, toplam, yüzde); öncelik yoksa None."""
    if not items:
        return None
    done = sum(1 for p in items if p["done"])
    return done, len(items), round(100 * done / len(items))


def get_review(user_id, start):
    return query_one("SELECT * FROM weekly_reviews WHERE user_id = ? AND week_start = ?", (user_id, start.isoformat()))


def review_view(row):
    """Şablon ve metinler için: satır + hafta, puan emojisi, öncelikler ve tamamlanma oranı."""
    start = parse_date(row["week_start"])
    items = parse_priorities(row["priorities"])
    return {"row": row, "start": start, "label": week_label(start), "score": row["score"],
            "emoji": SCORES[row["score"]][0] if row["score"] in SCORES else None,
            "priorities": items, "rate": priority_rate(items)}


def save_review(user_id, start, score=None, texts=None, priorities=None):
    """Haftanın değerlendirmesini oluşturur/günceller; None verilen alanlar korunur.
    priorities: metin listesi; metni değişmeyen önceliğin "yapıldı" işareti korunur."""
    db = get_db()
    row = db.execute("SELECT * FROM weekly_reviews WHERE user_id = ? AND week_start = ?",
                     (user_id, start.isoformat())).fetchone()
    fields = {"score": score, **(texts or {})}
    if priorities is not None:
        done = {fold(p["text"]) for p in parse_priorities(row["priorities"] if row else "") if p["done"]}
        fields["priorities"] = _dump([{"text": text, "done": fold(text) in done} for text in priorities])
    fields = {k: v for k, v in fields.items() if v is not None}
    if row is None:
        cols = ["user_id", "week_start", *fields]
        db.execute(f"INSERT INTO weekly_reviews ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                   (user_id, start.isoformat(), *fields.values()))
    elif fields:
        db.execute(f"UPDATE weekly_reviews SET {', '.join(f'{k} = ?' for k in fields)}, updated_at = CURRENT_TIMESTAMP"
                   " WHERE id = ?", (*fields.values(), row["id"]))
    db.commit()


def mark_priorities(review, done):
    """Öncelikleri işaretler; done: yapılanların sıra numaraları (0'dan)."""
    items = parse_priorities(review["priorities"])
    for i, p in enumerate(items):
        p["done"] = i in done
    execute("UPDATE weekly_reviews SET priorities = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (_dump(items), review["id"]))
    return items


def week_priorities(user_id, start):
    """Haftanın öncelikleri: bir önceki haftanın değerlendirmesinde "gelecek hafta" için yazılanlar."""
    row = get_review(user_id, start - timedelta(days=7))
    return parse_priorities(row["priorities"]) if row else []


def chart(user_id, t):
    """Son CHART_WEEKS haftanın puanları (eskiden yeniye); değerlendirilmemiş haftada score None."""
    current = week_start(t)
    first = current - timedelta(weeks=CHART_WEEKS - 1)
    scores = {r["week_start"]: r["score"] for r in query(
        "SELECT week_start, score FROM weekly_reviews WHERE user_id = ? AND week_start >= ? AND week_start <= ?",
        (user_id, first.isoformat(), current.isoformat()))}
    out, prev_month = [], None
    for i in range(CHART_WEEKS):
        d = first + timedelta(weeks=i)
        score = scores.get(d.isoformat())
        rated = score in SCORES
        out.append({"start": d, "score": score if rated else None, "emoji": SCORES[score][0] if rated else "",
                    "day": d.day, "month": MONTHS_TR_SHORT[d.month - 1] if d.month != prev_month else "",
                    "title": week_label(d) + (f": {SCORES[score][0]} {SCORES[score][1]}" if rated else ""),
                    "current": d == current})
        prev_month = d.month
    return out


def history(user_id, limit=HISTORY_SHOWN):
    """Son değerlendirmeler (yeniden eskiye)."""
    return [review_view(r) for r in query(
        "SELECT * FROM weekly_reviews WHERE user_id = ? ORDER BY week_start DESC LIMIT ?", (user_id, limit))]


# ---------- Rotalar ----------
def _form_week(t):
    """Formdaki hafta (herhangi bir günü); geçersizse ya da gelecekteyse None."""
    d = parse_date(request.form.get("hafta"))
    if d is None or d.year < 2000 or week_start(d) > week_start(t):
        return None
    return week_start(d)


@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    now = now_local()
    t = now.date()
    start = pick_week(request.args.get("hafta"), t)
    current = week_start(t)
    prev = start - timedelta(days=7)
    review = get_review(uid, start)
    last = get_review(uid, prev)  # geçen haftanın değerlendirmesi: bu haftanın öncelikleri onda
    priorities = [p["text"] for p in parse_priorities(review["priorities"])] if review else []
    return render_template(
        "weekly/index.html", start=start, end=start + timedelta(days=6), label=week_label(start),
        status=week_status(start, t), review_day=start == current and t.weekday() == 6, current=current,
        prev=prev, nxt=start + timedelta(days=7) if start < current else None,
        n=numbers(uid, start, now), coming=coming_up(uid) if start == current else None,
        review=review_view(review) if review else None, last=review_view(last) if last else None,
        priorities=priorities + [""] * (PRIORITIES - len(priorities)),
        missed=t.weekday() == 0 and start == current and last is None, prev_label=week_label(prev),
        chart=chart(uid, t), history=history(uid), scores=SCORES, questions=QUESTIONS,
        reminder=g.user["weekly_prompt"], default_prompt=DEFAULT_PROMPT,
        telegram_linked=bool(g.user["telegram_chat_id"]) and telegram.enabled(),
        duration=fmt_duration, arrow=arrow, mood_emoji=mood_emoji, category_icon=category_icon,
    )


@bp.route("/kaydet", methods=["POST"])
@login_required
def save():
    uid = g.user["id"]
    start = _form_week(now_local().date())
    if start is None:
        flash("Sadece bu hafta ya da geçmiş haftalar değerlendirilebilir.", "warning")
        return redirect(url_for(".index"))
    back = url_for(".index", hafta=start.isoformat())
    raw_score = (request.form.get("score") or "").strip()
    score = form_int("score") if raw_score else None
    if raw_score and score not in SCORES:
        flash("Haftanın puanı 1 ile 5 arasında olmalı.", "error")
        return redirect(back)
    priorities = [p.strip()[:PRIORITY_MAX] for p in request.form.getlist("priority") if p.strip()]
    if len(priorities) > PRIORITIES:
        flash(f"Gelecek hafta için en fazla {PRIORITIES} öncelik yazabilirsin.", "error")
        return redirect(back)
    texts = {k: form_str(k, TEXT_MAX) for k in QUESTIONS}
    if score is None and not any(texts.values()) and not priorities and get_review(uid, start) is None:
        flash("Bir puan seç ya da birkaç satır yaz.", "warning")
        return redirect(back)
    save_review(uid, start, score, texts, priorities)
    flash("Haftalık değerlendirme kaydedildi.", "success")
    return redirect(back)


@bp.route("/oncelikler", methods=["POST"])
@login_required
def priorities_done():
    """Bu haftanın (geçen hafta yazılan) önceliklerini işaretler."""
    start = _form_week(now_local().date())
    last = get_review(g.user["id"], start - timedelta(days=7)) if start else None
    if last is None or not parse_priorities(last["priorities"]):
        abort(404)
    items = mark_priorities(last, {int(v) for v in request.form.getlist("done") if v.isdigit()})
    made, total, pct = priority_rate(items)
    flash(f"Öncelikler güncellendi: {made}/{total} yapıldı (%{pct}).", "success")
    return redirect(url_for(".index", hafta=start.isoformat()))


@bp.route("/<day>/sil", methods=["POST"])
@login_required
def delete(day):
    uid = g.user["id"]
    d = parse_date(day)
    review = get_review(uid, week_start(d)) if d else None
    if review is None:
        abort(404)
    start = week_start(d)
    trash.move(uid, "weekly", f"🗓️ {week_label(start)} haftalık değerlendirmesi", ("weekly_reviews", review["id"]))
    flash(trash.notice(f"{week_label(start)} haftasının değerlendirmesi"), "success")
    return redirect(url_for(".index", hafta=start.isoformat()))


@bp.route("/hatirlatma", methods=["POST"])
@login_required
def reminder():
    value = (todo.parse_time(request.form.get("time")) or DEFAULT_PROMPT) if request.form.get("on") else None
    execute("UPDATE users SET weekly_prompt = ? WHERE id = ?", (value, g.user["id"]))
    flash(f"Her pazar {value}'dan sonra Telegram'dan hatırlatılacak." if value
          else "Haftalık değerlendirme hatırlatması kapatıldı.", "success")
    return redirect(url_for(".index"))


# ---------- Telegram (cron /hatirlatma, /gunluk ve bot) ----------
def pending(now):
    """Pazar günü hatırlatma saati gelmiş, bu haftaya puan vermemiş ve bu hafta sorulmamış kullanıcılar:
    [(kullanıcı, hafta başı)]. Pazar dışında boş liste."""
    if now.weekday() != 6:
        return []
    start = week_start(now.date())
    hm = now.strftime("%H:%M")
    out = []
    for u in query("SELECT * FROM users WHERE weekly_prompt IS NOT NULL AND telegram_chat_id IS NOT NULL ORDER BY id"):
        if hm < u["weekly_prompt"]:
            continue
        if query_one("SELECT 1 FROM app_state WHERE key = ? AND value = ?", (f"weekly_ask:{u['id']}", start.isoformat())):
            continue
        if query_one("SELECT 1 FROM weekly_reviews WHERE user_id = ? AND week_start = ? AND score IS NOT NULL",
                     (u["id"], start.isoformat())):
            continue
        out.append((u, start))
    return out


def mark_asked(user_id, start):
    execute("INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)", (f"weekly_ask:{user_id}", start.isoformat()))


def score_buttons(start):
    return [[(emoji, f"wk:{start.isoformat()}:{n}") for n, (emoji, _label) in SCORES.items()]]


def prompt_message(user_id, start, now, url, escape, interactive=True):
    """Pazar mesajı: haftanın kısa sayıları, form linki (butonlar ayrıca)."""
    lines = ["🗓️ <b>Haftalık değerlendirme zamanı</b>", week_label(start), ""]
    lines += numbers_lines(numbers(user_id, start, now), escape) or ["Bu hafta kayıt yok; yine de nasıl geçtiğini yaz."]
    lines.append("")
    lines.append("Haftan nasıldı? Bir emojiye dokun; iyi gideni, zorlayanı ve gelecek haftanın 3 önceliğini formda yaz."
                 if interactive else "İyi gideni, zorlayanı ve gelecek haftanın 3 önceliğini yazmayı unutma.")
    lines.append(f'<a href="{escape(url)}">Formu doldur →</a>')
    return "\n".join(lines)


def saved_message(start, score, url, escape):
    """Puan butonuna basılınca mesajın yeni hali."""
    emoji, label = SCORES[score]
    return (f"✅ <b>Kaydedildi</b>: {week_label(start)} · {emoji} {label}\n"
            f'İyi gideni, zorlayanı ve gelecek haftanın 3 önceliğini yazmak için <a href="{escape(url)}">formu doldur →</a>')


def priority_lines(items, escape):
    return [f"{'✅' if p['done'] else '☐'} {i}) {escape(p['text'])}" for i, p in enumerate(items, 1)]


def week_text(user_id, now, url, escape):
    """/hafta: bu haftanın kısa sayıları ve öncelikleri."""
    start = week_start(now.date())
    lines = [f"🗓️ <b>Bu hafta</b> · {week_label(start)}"
             + (" · değerlendirme günü" if now.weekday() == 6 else " (devam ediyor)"), ""]
    lines += numbers_lines(numbers(user_id, start, now), escape) or ["Bu hafta henüz kayıt yok."]
    items = week_priorities(user_id, start)
    if items:
        made, total, _pct = priority_rate(items)
        lines += ["", f"📌 <b>Bu haftanın öncelikleri</b> ({made}/{total})"] + priority_lines(items, escape)
    else:
        lines += ["", "📌 Bu hafta için öncelik yazılmamış; pazar günü değerlendirmede gelecek haftanınkileri yaz."]
    lines += ["", f'<a href="{escape(url)}">Haftalık değerlendirme →</a>']
    return "\n".join(lines)


def daily_line(user_id, escape):
    """Pazartesi günlük özetindeki satır: geçen hafta yazılan öncelikler; pazartesi değilse ya da yoksa None."""
    t = now_local().date()
    if t.weekday() != 0:
        return None
    items = week_priorities(user_id, week_start(t))
    if not items:
        return None
    return "📌 <b>Bu haftanın öncelikleri:</b> " + " ".join(f"{i}) {escape(p['text'])}" for i, p in enumerate(items, 1))
