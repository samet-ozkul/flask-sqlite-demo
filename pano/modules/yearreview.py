"""📊 Yıl Özeti: bir yılın tüm modüllerdeki kayıtlarından derlenen özet ("Spotify Wrapped" gibi).

- /yil-ozeti/?yil=2026: öne çıkan 4-6 kart, modül modül bölümler (verisi olmayan bölüm görünmez) ve
  GitHub benzeri etkinlik ısı haritası (her gün eklenen/tamamlanan kayıt sayısı; sunucuda HTML/CSS).
- Yıl seçicide sadece verisi olan yıllar; varsayılan Ocak'ta geçen yıl, diğer aylarda bu yıl.
- Sadece kullanıcının kendi kayıtları sayılır: paylaşılan liste/panoda başkasının eklediği ya da başkasına
  atanan maddeler, etkinlikler ve ortak harcamalar katılmaz.
- UTC tutulan zamanlar (created_at, done_at, sayaç kayıtları) yıl sınırında tam, ısı haritasında APP_TZ'nin
  bugünkü farkıyla yerel güne çevrilir (Türkiye'de yaz/kış saati yok).
- "📨 Telegram'a gönder" kısa metni kullanıcının kendi sohbetine yollar. Cron /gunluk 1 Ocak'ta geçen yılın
  özetini Telegram'ı bağlı ve verisi olan herkese bir kez gönderir (durum app_state'te: yearreview:<id>:<yıl>).
- Panoda 15 Aralık – 31 Ocak arası "📊 Yıl özetin hazır" şeridi (banner_year).
Sorgular bölüm başına bir-iki toplu sorgudur; gün gün döngüyle sorgu atılmaz (CPU kısıtlı).
"""
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .. import telegram
from .. import todo_reminders as todo
from ..auth import login_required
from ..db import get_db, query, query_one
from ..utils import MONTHS_TR, MONTHS_TR_SHORT, TZ, add_months, fmt_money, fmt_number, fold, local_dt, parse_date
from .contacts import KINDS as CONTACT_KINDS
from .expenses import category_icon
from .habits import longest_streak
from .health import KINDS as HEALTH_KINDS
from .journal import MOODS
from .kanban import IS_DONE
from .places import CATEGORIES as PLACE_CATEGORIES
from .timetrack import entries_between, fmt_duration, summarize
from .watchlist import KINDS as WATCH_KINDS

bp = Blueprint("yearreview", __name__, url_prefix="/yil-ozeti")

HIGHLIGHTS_MAX = 6
TOP_N = 5
CITIES_SHOWN = 12
WATCH_WORDS = {"book": "kitap", "movie": "film", "series": "dizi"}
SECTIONS = ("expenses", "bills", "tasks", "habits", "journal", "watch", "places", "time", "car", "health", "goals",
            "misc")

# Paylaşılan listede sadece kendi eklediği (ya da ekleyeni bilinmeyen eski) ve başkasına atanmamış maddeler
OWN_DONE_ITEMS = ("list_items i JOIN lists l ON l.id = i.list_id WHERE l.user_id = :u AND i.done = 1"
                  " AND (i.created_by IS NULL OR i.created_by = :u) AND (i.assignee_id IS NULL OR i.assignee_id = :u)")
# Kendi panosunun "bitti" sütunundaki kendi kartları (bitiş zamanı tutulmuyor: son taşınma/düzenleme anı)
OWN_DONE_CARDS = ("cards c JOIN boards b ON b.id = c.board_id WHERE b.user_id = :u AND " + IS_DONE
                  + " AND (c.created_by IS NULL OR c.created_by = :u)")

# Etkinlik kaynakları (ısı haritası, verisi olan yıllar): (tablo ve koşul, tarih sütunu, sütun türü)
# "gun": yerel 'YYYY-MM-DD...' metni; "utc": UTC zaman damgası (yerel güne çevrilir)
ACTIVITY = [
    ("expenses WHERE user_id = :u", "date", "gun"),
    ("habit_logs l JOIN habits h ON h.id = l.habit_id WHERE h.user_id = :u", "l.date", "gun"),
    ("journal WHERE user_id = :u AND (mood IS NOT NULL OR text != '')", "date", "gun"),
    ("bills WHERE user_id = :u AND paid = 1", "paid_at", "gun"),
    ("watchlist WHERE user_id = :u AND status = 'done'", "finished_at", "gun"),
    ("health_metrics WHERE user_id = :u", "measured_at", "gun"),
    ("vehicle_logs l JOIN vehicles v ON v.id = l.vehicle_id WHERE v.user_id = :u", "l.date", "gun"),
    ("goal_entries e JOIN goals g ON g.id = e.goal_id WHERE g.user_id = :u", "e.date", "gun"),
    ("contact_logs WHERE user_id = :u", "date", "gun"),
    ("orders WHERE user_id = :u", "ordered_on", "gun"),
    ("notes WHERE user_id = :u", "created_at", "utc"),
    ("recipes WHERE user_id = :u", "created_at", "utc"),
    ("cities WHERE user_id = :u", "created_at", "utc"),
    ("places WHERE user_id = :u", "created_at", "utc"),
    ("time_entries WHERE user_id = :u", "started_at", "utc"),
    (OWN_DONE_ITEMS, "i.done_at", "utc"),
    (OWN_DONE_CARDS, "c.updated_at", "utc"),
]


# ---------- Zaman ----------
def now_local():
    """Testlerde todo_reminders.now_local taklit edilir."""
    return todo.now_local()


def default_year(t):
    """Ocak'ta geçen yıl (yılbaşı özeti), diğer aylarda bu yıl."""
    return t.year - 1 if t.month == 1 else t.year


def _utc(d):
    """Yerel günün başlangıcı -> UTC 'YYYY-MM-DD HH:MM:SS' (CURRENT_TIMESTAMP alanlarıyla karşılaştırma)."""
    return datetime.combine(d, time(0, 0), tzinfo=TZ).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _offset():
    """UTC zamanı yerel güne çeviren SQLite date() düzeltmesi, ör. '+10800 seconds'."""
    return f"{int(now_local().utcoffset().total_seconds()):+d} seconds"


def _params(user_id, year):
    """Sorgu parametreleri: yerel yıl sınırları (:a, :b) ve aynı anların UTC karşılıkları (:ua, :ub)."""
    first, nxt = date(year, 1, 1), date(year + 1, 1, 1)
    return {"u": user_id, "y": str(year), "a": first.isoformat(), "b": nxt.isoformat(),
            "ua": _utc(first), "ub": _utc(nxt), "off": _offset()}


def _activity_sql(ranged):
    """Tüm kaynaklardaki kayıtların yerel günü (d), UNION ALL; ranged ise sadece :a–:b yılı."""
    parts = []
    for source, col, kind in ACTIVITY:
        day = f"substr({col}, 1, 10)" if kind == "gun" else f"date({col}, :off)"
        sql = f"SELECT {day} AS d FROM {source}"
        if ranged:
            lo, hi = (":a", ":b") if kind == "gun" else (":ua", ":ub")
            sql += f" AND {col} >= {lo} AND {col} < {hi}"
        parts.append(sql)
    return " UNION ALL ".join(parts)


def data_years(user_id, t):
    """Verisi olan yıllar (eskiden yeniye, bu yıldan sonrası yok); hiç veri yoksa [bu yıl]."""
    rows = query(f"SELECT DISTINCT substr(d, 1, 4) AS y FROM ({_activity_sql(False)}) WHERE d IS NOT NULL",
                 {"u": user_id, "off": _offset()})
    years = sorted({int(r["y"]) for r in rows if (r["y"] or "").isdigit() and 2000 <= int(r["y"]) <= t.year})
    return years or [t.year]


def pick_year(raw, years, t):
    """İstenen yıl verisi olan yıllardan biriyse o; değilse (geçersiz, gelecek, verisiz) varsayılan yıl."""
    try:
        year = int(raw or "")
    except ValueError:
        year = None
    if year in years:
        return year
    default = default_year(t)
    return default if default in years else years[-1]


def has_data(user_id, year):
    return query_one(f"SELECT 1 FROM ({_activity_sql(True)}) LIMIT 1", _params(user_id, year)) is not None


# ---------- Bölümler (her biri veri yoksa None) ----------
def _expenses(p, year, t):
    rows = query("SELECT substr(date, 1, 7) AS m, category, COUNT(*) AS n, SUM(amount) AS s FROM expenses"
                 " WHERE user_id = :u AND date >= :a AND date < :b GROUP BY m, category", p)
    if not rows:
        return None
    per_cat, per_month = defaultdict(float), defaultdict(float)
    for r in rows:
        per_cat[r["category"]] += r["s"]
        per_month[int(r["m"][5:7])] += r["s"]
    total = round(sum(per_cat.values()), 2)
    top = sorted(((c, round(v, 2)) for c, v in per_cat.items()), key=lambda kv: (-kv[1], kv[0]))[:TOP_N]
    month, month_total = min(per_month.items(), key=lambda kv: (-kv[1], kv[0]))
    # Aylık ortalama: ilk harcama ayından yıl sonuna (devam eden yılda bu aya) kadar
    last_month = 12 if year < t.year else max(t.month, max(per_month))
    months = last_month - min(per_month) + 1
    biggest = query_one("SELECT amount, category, note, date FROM expenses WHERE user_id = :u AND date >= :a"
                        " AND date < :b ORDER BY amount DESC, date, id LIMIT 1", p)
    # Geçen yılla karşılaştırma: biten yılda tüm yıl, devam eden yılda geçen yılın aynı dönemi (1 Ocak – bugün)
    ongoing = year == t.year
    if ongoing:
        cut, prev_cut = (t + timedelta(days=1)).isoformat(), (add_months(t, -12) + timedelta(days=1)).isoformat()
    else:
        cut, prev_cut = p["b"], p["a"]
    cmp = query_one("SELECT COALESCE(SUM(CASE WHEN date >= :a AND date < :cut THEN amount END), 0) AS cur,"
                    " COALESCE(SUM(CASE WHEN date < :pcut THEN amount END), 0) AS prev"
                    " FROM expenses WHERE user_id = :u AND date >= :pa AND date < :b",
                    {**p, "cut": cut, "pcut": prev_cut, "pa": date(year - 1, 1, 1).isoformat()})
    prev = round(cmp["prev"], 2)
    change = (cmp["cur"] - prev) * 100 / prev if prev else None
    return {
        "total": total, "count": sum(r["n"] for r in rows), "avg": round(total / months, 2), "months": months,
        "top": top, "categories": len(per_cat),
        "bars": [(f"{category_icon(c)} {c}", v, f"{fmt_money(v)} · %{round(v * 100 / total) if total else 0}")
                 for c, v in top],
        "top_month": {"name": MONTHS_TR[month - 1], "total": round(month_total, 2), "key": f"{year:04d}-{month:02d}"},
        "biggest": biggest, "prev": prev,
        "change": change, "change_pct": abs(round(change)) if change is not None else None,
        "cmp_label": "geçen yılın aynı dönemine göre" if ongoing else f"{year - 1} yılına göre",
    }


def _bills(p):
    row = query_one("SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS s FROM bills"
                    " WHERE user_id = :u AND paid = 1 AND paid_at >= :a AND paid_at < :b", p)
    return {"count": row["n"], "total": round(row["s"], 2)} if row["n"] else None


def _tasks(p):
    counts = {r["kind"]: r["n"] for r in query(
        f"SELECT l.kind, COUNT(*) AS n FROM {OWN_DONE_ITEMS} AND i.done_at >= :ua AND i.done_at < :ub GROUP BY l.kind", p)}
    return {"todo": counts.get("todo", 0), "shopping": counts.get("shopping", 0)} if counts else None


def _habits(p, year, t):
    rows = query("SELECT h.id, h.name, h.icon, h.created_at, l.date FROM habit_logs l JOIN habits h ON h.id = l.habit_id"
                 " WHERE h.user_id = :u AND l.date >= :a AND l.date < :b", p)
    habits, done = {}, defaultdict(set)
    for r in rows:
        d = parse_date(r["date"])
        if d:
            habits[r["id"]] = r
            done[r["id"]].add(d)
    if not done:
        return None
    first, end = date(year, 1, 1), min(date(year, 12, 31), t)
    stats = []
    for habit_id, days in done.items():
        h = habits[habit_id]
        # Başarı oranının paydası: yılın (ya da alışkanlığın başladığı günün) başından yıl sonuna / bugüne
        created = parse_date(local_dt(h["created_at"], "%Y-%m-%d")) or first
        span = (end - max(first, min([created] + list(days)))).days + 1
        stats.append({"icon": h["icon"], "name": h["name"], "count": len(days), "streak": longest_streak(days),
                      "rate": min(100, round(100 * len(days) / span)) if span > 0 else 100})
    stats.sort(key=lambda s: (-s["count"], -s["streak"], fold(s["name"])))
    best = max(stats, key=lambda s: s["streak"])  # eşitlikte en çok yapılan
    return {
        "marks": sum(s["count"] for s in stats), "days": len(set().union(*done.values())),
        "streak": best, "steady": stats[0], "count": len(stats),
        "bars": [(f"{s['icon']} {s['name']}", s["count"], f"{s['count']} gün · %{s['rate']}") for s in stats[:6]],
    }


def _journal(p):
    rows = query("SELECT date, mood FROM journal WHERE user_id = :u AND date >= :a AND date < :b"
                 " AND (mood IS NOT NULL OR text != '')", p)
    if not rows:
        return None
    moods = Counter(r["mood"] for r in rows if r["mood"] in MOODS)
    rated = sum(moods.values())
    top = max(moods, key=lambda m: (moods[m], m)) if moods else None  # eşitlikte daha iyi olan
    return {
        "days": len(rows), "streak": longest_streak({d for d in (parse_date(r["date"]) for r in rows) if d}),
        "bars": [(f"{MOODS[m][0]} {MOODS[m][1]}", moods[m], f"{moods[m]} gün") for m in sorted(MOODS, reverse=True)
                 if moods[m]],
        "avg": round(sum(m * n for m, n in moods.items()) / rated, 1) if rated else None,
        "top": MOODS[top] if top else None,
    }


def watch_label(kinds):
    """['book', 'movie'] -> 'kitap & film'"""
    words = [WATCH_WORDS[k] for k in WATCH_WORDS if k in kinds]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " & " + words[-1]


def _watch(p):
    rows = query("SELECT kind, title, creator, rating FROM watchlist WHERE user_id = :u AND status = 'done'"
                 " AND finished_at >= :a AND finished_at < :b ORDER BY rating IS NULL, rating DESC, finished_at, id", p)
    if not rows:
        return None
    counts = Counter(r["kind"] for r in rows)
    return {
        "total": len(rows), "label": watch_label(counts),
        "kinds": [(WATCH_KINDS[k][1], WATCH_KINDS[k][0], counts[k]) for k in WATCH_WORDS if counts[k]],
        "words": [(counts[k], WATCH_WORDS[k]) for k in WATCH_WORDS if counts[k]],
        "top": [r for r in rows if r["rating"]][:TOP_N], "icons": {k: icon for k, (_l, icon) in WATCH_KINDS.items()},
    }


def _places(p):
    # Ziyaret tarihi (ay/gün) varsa o yıl, yoksa o yıl eklenmiş "gezdim" şehirleri
    cities = query("SELECT name, country FROM cities WHERE user_id = :u AND status = 'visited'"
                   " AND (substr(visited_on, 1, 4) = :y OR (COALESCE(visited_on, '') = ''"
                   " AND created_at >= :ua AND created_at < :ub))"
                   " ORDER BY COALESCE(NULLIF(visited_on, ''), substr(created_at, 1, 10)), id", p)
    cats = query("SELECT category, COUNT(*) AS n FROM places WHERE user_id = :u AND status = 'visited'"
                 " AND created_at >= :ua AND created_at < :ub GROUP BY category ORDER BY n DESC, category", p)
    if not cities and not cats:
        return None
    return {
        "cities": cities[:CITIES_SHOWN], "city_count": len(cities),
        "countries": sorted({c["country"] for c in cities if c["country"]}, key=fold),
        "places": sum(r["n"] for r in cats),
        "place_cats": [(PLACE_CATEGORIES.get(r["category"], PLACE_CATEGORIES["other"])[1],
                        PLACE_CATEGORIES.get(r["category"], PLACE_CATEGORIES["other"])[0], r["n"]) for r in cats],
    }


def _time(user_id, year, now):
    rows = entries_between(user_id, date(year, 1, 1), date(year + 1, 1, 1))
    if not rows:
        return None
    s = summarize(rows, now)
    return {
        "total": s["total"], "count": s["count"], "earning": s["earning"],
        "days": len({local_dt(r["started_at"], "%Y-%m-%d") for r in rows}),
        "top": s["projects"][0] if s["projects"] else None,
        "bars": [(f"{pr['icon']} {pr['name']}", pr["seconds"], fmt_duration(pr["seconds"])) for pr in s["projects"][:TOP_N]],
    }


def _car(p):
    kinds = {r["kind"]: r for r in query(
        "SELECT l.kind, COUNT(*) AS n, COALESCE(SUM(l.amount), 0) AS s, COALESCE(SUM(l.liters), 0) AS lt"
        " FROM vehicle_logs l JOIN vehicles v ON v.id = l.vehicle_id"
        " WHERE v.user_id = :u AND l.date >= :a AND l.date < :b GROUP BY l.kind", p)}
    if not kinds:
        return None
    # Yapılan km: araç başına yılın son km'si − yıldan önceki son km (yoksa yılın ilk km'si)
    km = 0
    for r in query("SELECT l.vehicle_id, MIN(l.km) AS lo, MAX(l.km) AS hi,"
                   " (SELECT MAX(x.km) FROM vehicle_logs x WHERE x.vehicle_id = l.vehicle_id AND x.date < :a) AS before"
                   " FROM vehicle_logs l JOIN vehicles v ON v.id = l.vehicle_id"
                   " WHERE v.user_id = :u AND l.date >= :a AND l.date < :b AND l.km IS NOT NULL GROUP BY l.vehicle_id", p):
        km += r["hi"] - (r["before"] if r["before"] is not None and r["before"] <= r["hi"] else r["lo"])
    fuel = kinds.get("fuel")
    return {
        "km": km, "fuel": round(fuel["s"], 2) if fuel else 0, "liters": round(fuel["lt"], 1) if fuel else 0,
        "fuel_count": fuel["n"] if fuel else 0,
        "service": kinds["service"]["n"] if "service" in kinds else 0,
        "repair": kinds["repair"]["n"] if "repair" in kinds else 0,
        "total": round(sum(r["s"] for r in kinds.values()), 2),
    }


def _health(p):
    counts = {r["kind"]: r["n"] for r in query(
        "SELECT kind, COUNT(*) AS n FROM health_metrics WHERE user_id = :u AND measured_at >= :a AND measured_at < :b"
        " GROUP BY kind", p)}
    if not counts:
        return None
    weights = [r["value1"] for r in query(
        "SELECT value1 FROM health_metrics WHERE user_id = :u AND kind = 'weight' AND measured_at >= :a"
        " AND measured_at < :b ORDER BY measured_at, id", p)]
    return {
        "count": sum(counts.values()),
        "kinds": [(k["icon"], k["label"], counts[key]) for key, k in HEALTH_KINDS.items() if key in counts],
        "first": weights[0] if weights else None, "last": weights[-1] if weights else None,
        "low": min(weights) if weights else None, "high": max(weights) if weights else None,
        "change": round(weights[-1] - weights[0], 1) if len(weights) > 1 else None,
    }


def _goals(p):
    done = query("SELECT name, icon, target FROM goals WHERE user_id = :u AND done_at >= :a AND done_at < :b"
                 " ORDER BY done_at, id", p)
    saved = query_one("SELECT COUNT(*) AS n, COALESCE(SUM(e.amount), 0) AS s FROM goal_entries e"
                      " JOIN goals g ON g.id = e.goal_id WHERE g.user_id = :u AND e.date >= :a AND e.date < :b", p)
    if not done and not saved["n"]:
        return None
    return {"done": done, "saved": round(saved["s"], 2), "entries": saved["n"]}


def _misc(p):
    """Kısa sayılar: notlar, tarifler, görüşmeler, kanban, siparişler (tek sorgu)."""
    row = query_one(
        "SELECT (SELECT COUNT(*) FROM notes WHERE user_id = :u AND created_at >= :ua AND created_at < :ub) AS notes,"
        " (SELECT COUNT(*) FROM recipes WHERE user_id = :u AND created_at >= :ua AND created_at < :ub) AS recipes,"
        " (SELECT COUNT(*) FROM contact_logs WHERE user_id = :u AND date >= :a AND date < :b) AS talks,"
        " (SELECT COUNT(DISTINCT contact_id) FROM contact_logs WHERE user_id = :u AND date >= :a AND date < :b) AS people,"
        f" (SELECT COUNT(*) FROM {OWN_DONE_CARDS} AND c.updated_at >= :ua AND c.updated_at < :ub) AS cards,"
        " (SELECT COUNT(*) FROM orders WHERE user_id = :u AND ordered_on >= :a AND ordered_on < :b"
        " AND status != 'cancelled') AS orders,"
        " (SELECT COALESCE(SUM(amount), 0) FROM orders WHERE user_id = :u AND ordered_on >= :a AND ordered_on < :b"
        " AND status NOT IN ('cancelled', 'returned')) AS orders_total", p)
    items = []
    if row["notes"]:
        items.append({"key": "notes", "icon": "📝", "value": row["notes"], "label": "not", "endpoint": "notes.index"})
    if row["recipes"]:
        items.append({"key": "recipes", "icon": "🍲", "value": row["recipes"], "label": "yeni tarif",
                      "endpoint": "recipes.index"})
    if row["talks"]:
        items.append({"key": "talks", "icon": CONTACT_KINDS["call"][0], "value": row["talks"], "label": "görüşme",
                      "sub": f"{row['people']} kişiyle", "endpoint": "contacts.index"})
    if row["cards"]:
        items.append({"key": "cards", "icon": "🗂️", "value": row["cards"], "label": "kart bitti",
                      "endpoint": "kanban.index"})
    if row["orders"]:
        items.append({"key": "orders", "icon": "🚚", "value": row["orders"], "label": "sipariş",
                      "sub": fmt_money(round(row["orders_total"], 2)) if row["orders_total"] else None,
                      "endpoint": "orders.index"})
    return items or None


def heatmap(p, year, t):
    """GitHub benzeri ızgara: sütunlar haftalar (pazartesi üstte); hücre sınıfı h0–h4 yoğunluk, hx yıl dışı/gelecek."""
    counts = {r["d"]: r["n"] for r in query(
        f"SELECT d, COUNT(*) AS n FROM ({_activity_sql(True)}) WHERE d >= :a AND d < :b GROUP BY d", p)
        if r["d"] <= t.isoformat()}
    first, last = date(year, 1, 1), date(year, 12, 31)
    start = first - timedelta(days=first.weekday())
    cols = (last - start).days // 7 + 1
    peak = max(counts.values(), default=0)
    cells = []
    for i in range(cols * 7):
        d = start + timedelta(days=i)
        if d.year != year or d > t:
            cells.append(("hx", ""))
            continue
        n = counts.get(d.isoformat(), 0)
        label = f"{d.day} {MONTHS_TR_SHORT[d.month - 1]}"
        cells.append((f"h{-(-4 * n // peak)}" if n else "h0", f"{label}: {n} kayıt" if n else label))
    per_month = Counter()
    for d, n in counts.items():
        per_month[int(d[5:7])] += n
    best_day = min(counts.items(), key=lambda kv: (-kv[1], kv[0])) if counts else None
    best_month = min(per_month.items(), key=lambda kv: (-kv[1], kv[0])) if per_month else None
    return {
        "cols": cols, "cells": cells, "total": sum(counts.values()), "active_days": len(counts),
        "months": [((date(year, m, 1) - start).days // 7 + 1, MONTHS_TR_SHORT[m - 1]) for m in range(1, 13)],
        "best_day": (parse_date(best_day[0]), best_day[1]) if best_day else None,
        "best_month": (MONTHS_TR[best_month[0] - 1], best_month[1]) if best_month else None,
    }


def _num(n):
    return fmt_number(n, 0)


def _kg(value, sign=False):
    text = fmt_number(value, 1)
    return f"+{text}" if sign and value > 0 else text


def _highlights(r):
    """Öne çıkan kartlar: her aday bu kullanıcı için ne kadar dikkat çekici olduğuna göre puanlanır
    (değer / alışılmış yıllık miktar); en yüksek HIGHLIGHTS_MAX tanesi, puan sırasıyla."""
    c = []

    def add(score, icon, value, label, endpoint):
        if score > 0:
            c.append((score, icon, value, label, endpoint))

    if r["expenses"]:  # yılın parası hep ilgi çeker: az kayıtta da öne çıkar
        add(0.5 + r["expenses"]["count"] / 120, "💸", fmt_money(round(r["expenses"]["total"])), "harcama", "expenses.index")
    if r["habits"]:
        add(r["habits"]["days"] / 120, "🔥", f"{_num(r['habits']['days'])} gün", "alışkanlık", "habits.index")
    if r["tasks"] and r["tasks"]["todo"]:
        add(r["tasks"]["todo"] / 50, "✅", _num(r["tasks"]["todo"]), "görev tamamlandı", "lists.index")
    if r["watch"]:
        add(r["watch"]["total"] / 10, "🎬", _num(r["watch"]["total"]), r["watch"]["label"], "watchlist.index")
    if r["places"] and r["places"]["city_count"]:
        add(r["places"]["city_count"] / 3, "🗺️", _num(r["places"]["city_count"]), "yeni şehir", "places.index")
    if r["time"]:
        hours = r["time"]["total"] / 3600
        add(hours / 150, "⏱️", f"{_num(hours)} saat" if hours >= 1 else fmt_duration(r["time"]["total"]), "çalışma",
            "timetrack.index")
    if r["journal"]:
        add(r["journal"]["days"] / 100, "📓", f"{_num(r['journal']['days'])} gün", "günlük yazıldı", "journal.index")
    if r["goals"] and r["goals"]["done"]:
        add(len(r["goals"]["done"]), "🏁", _num(len(r["goals"]["done"])), "hedef tamamlandı", "goals.index")
    if r["car"] and r["car"]["km"]:
        add(r["car"]["km"] / 8000, "🚗", f"{_num(r['car']['km'])} km", "yol", "car.index")
    if r["health"] and r["health"]["change"]:
        add(abs(r["health"]["change"]) / 3, "⚖️", f"{_kg(r['health']['change'], True)} kg", "kilo değişimi",
            "health.index")
    if r["bills"]:
        add(r["bills"]["count"] / 24, "🧾", _num(r["bills"]["count"]), "fatura ödendi", "bills.index")
    refs = {"notes": 40, "recipes": 10, "talks": 40, "cards": 40, "orders": 20}
    for item in r["misc"] or []:
        add(item["value"] / refs[item["key"]], item["icon"], _num(item["value"]), item["label"], item["endpoint"])
    c.sort(key=lambda x: -x[0])
    return [{"icon": i, "value": v, "label": lb, "endpoint": ep} for _s, i, v, lb, ep in c[:HIGHLIGHTS_MAX]]


def build(user_id, year, now):
    """Yılın tüm özeti (sayfa, Telegram metni ve cron ortak kullanır)."""
    t = now.date()
    p = _params(user_id, year)
    r = {
        "year": year, "ongoing": year == t.year,
        "expenses": _expenses(p, year, t), "bills": _bills(p), "tasks": _tasks(p), "habits": _habits(p, year, t),
        "journal": _journal(p), "watch": _watch(p), "places": _places(p), "time": _time(user_id, year, now),
        "car": _car(p), "health": _health(p), "goals": _goals(p), "misc": _misc(p), "heat": heatmap(p, year, t),
    }
    r["highlights"] = _highlights(r)
    r["empty"] = not r["heat"]["total"] and not any(r[k] for k in SECTIONS)
    return r


# ---------- Kısa metin (Telegram) ----------
def _arrow(change):
    return "▲" if change > 0 else "▼" if change < 0 else "="


def summary_text(r, url, escape, title=None):
    """Özetin kısa metin hali (Telegram HTML; kullanıcı verisi escape ile)."""
    year = r["year"]
    lines = [title or f"📊 <b>{year} yıl özetin" + (" (şu ana kadar)" if r["ongoing"] else "") + "</b>", ""]
    e = r["expenses"]
    if e:
        change = f" ({_arrow(e['change'])} %{e['change_pct']} {e['cmp_label']})" if e["change"] is not None else ""
        lines.append(f"💸 Harcama: <b>{fmt_money(e['total'])}</b>{change}")
        c, v = e["top"][0]
        lines.append(f"    En çok: {category_icon(c)} {escape(c)} · {fmt_money(v)} · en pahalı ay {e['top_month']['name']}")
    if r["bills"]:
        lines.append(f"🧾 {r['bills']['count']} fatura ödendi · {fmt_money(r['bills']['total'])}")
    if r["tasks"]:
        parts = []
        if r["tasks"]["todo"]:
            parts.append(f"✅ {_num(r['tasks']['todo'])} görev tamamlandı")
        if r["tasks"]["shopping"]:
            parts.append(f"🛒 {_num(r['tasks']['shopping'])} alışveriş maddesi")
        lines.append(" · ".join(parts))
    h = r["habits"]
    if h:
        s = h["streak"]
        lines.append(f"🔥 Alışkanlık: {_num(h['days'])} gün · en uzun seri {s['streak']} gün ({s['icon']} {escape(s['name'])})")
    j = r["journal"]
    if j:
        lines.append(f"📓 Günlük: {_num(j['days'])} gün" + (f" · en sık {j['top'][0]} {j['top'][1].lower()}" if j["top"] else ""))
    w = r["watch"]
    if w:
        lines.append("🎬 Bitirilen: " + ", ".join(f"{n} {word}" for n, word in w["words"]))
    pl = r["places"]
    if pl and pl["city_count"]:
        names = ", ".join(escape(c["name"]) for c in pl["cities"][:5]) + ("…" if pl["city_count"] > 5 else "")
        lines.append(f"🗺️ {pl['city_count']} şehir: {names}")
    elif pl:
        lines.append(f"🗺️ {pl['places']} yer gezildi")
    tm = r["time"]
    if tm:
        lines.append(f"⏱️ Zaman takibi: {fmt_duration(tm['total'])}"
                     + (f" · en çok {escape(tm['top']['name'])}" if tm["top"] else ""))
    car = r["car"]
    if car and (car["km"] or car["fuel"]):
        parts = ([f"{_num(car['km'])} km"] if car["km"] else []) + ([f"yakıt {fmt_money(car['fuel'])}"] if car["fuel"] else [])
        lines.append("🚗 " + " · ".join(parts))
    hl = r["health"]
    if hl and hl["change"] is not None:
        lines.append(f"⚖️ Kilo: {_kg(hl['first'])} → {_kg(hl['last'])} kg ({_kg(hl['change'], True)})")
    gl = r["goals"]
    if gl:
        parts = ([f"{len(gl['done'])} hedef tamamlandı"] if gl["done"] else []) \
            + ([f"{fmt_money(gl['saved'])} biriktirildi"] if gl["saved"] > 0 else [])
        if parts:
            lines.append("🏁 " + " · ".join(parts))
    if r["misc"]:
        lines.append(" · ".join(f"{m['icon']} {_num(m['value'])} {m['label']}" for m in r["misc"]))
    heat = r["heat"]
    if heat["active_days"]:
        lines.append(f"📅 {_num(heat['active_days'])} gün aktif"
                     + (f" · en yoğun ay {heat['best_month'][0]}" if heat["best_month"] else ""))
    lines += ["", f'<a href="{escape(url)}">Yıl özetini aç →</a>']
    return "\n".join(lines)


# ---------- Yılbaşı mesajı (cron /gunluk) ve pano şeridi ----------
def _state_key(user_id, year):
    return f"yearreview:{user_id}:{year}"


def pending_new_year(now):
    """1 Ocak'ta [(kullanıcı, geçen yıl)]: Telegram'ı bağlı, geçen yıl verisi olan ve henüz gönderilmemişler.
    1 Ocak dışında boş liste."""
    if (now.month, now.day) != (1, 1):
        return []
    year = now.year - 1
    out = []
    for user in query("SELECT * FROM users WHERE telegram_chat_id IS NOT NULL ORDER BY id"):
        if query_one("SELECT 1 FROM app_state WHERE key = ?", (_state_key(user["id"], year),)):
            continue
        if has_data(user["id"], year):
            out.append((user, year))
    return out


def new_year_message(user, year, now, url, escape):
    return summary_text(build(user["id"], year, now), url, escape, title=f"🎉 <b>{year} yılın özeti hazır</b>")


def mark_sent(user_id, year):
    db = get_db()
    db.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)",
               (_state_key(user_id, year), now_local().date().isoformat()))
    db.commit()


def banner_year(user_id):
    """Panodaki "📊 Yıl özetin hazır" şeridi: 15 Aralık – 31 Ocak arası o yıl (Ocak'ta geçen yıl); verisi yoksa None."""
    t = now_local().date()
    if (t.month, t.day) >= (12, 15):
        year = t.year
    elif t.month == 1:
        year = t.year - 1
    else:
        return None
    return year if has_data(user_id, year) else None


# ---------- Rotalar ----------
def _telegram_linked():
    return bool(g.user["telegram_chat_id"]) and telegram.enabled()


@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    now = now_local()
    t = now.date()
    years = data_years(uid, t)
    year = pick_year(request.args.get("yil"), years, t)
    return render_template("yearreview/index.html", r=build(uid, year, now), years=years,
                           telegram_linked=_telegram_linked(), weekdays=["Pzt", "", "Çar", "", "Cum", "", ""],
                           duration=fmt_duration, kg=_kg, arrow=_arrow, today=t)


@bp.route("/gonder", methods=["POST"])
@login_required
def send():
    """Özetin kısa metnini kullanıcının kendi Telegram'ına gönderir."""
    uid = g.user["id"]
    now = now_local()
    year = pick_year(request.form.get("yil"), data_years(uid, now.date()), now.date())
    back = url_for(".index", yil=year)
    if not _telegram_linked():
        flash("Önce Ayarlar → Telegram bölümünden hesabını bağla.", "warning")
        return redirect(back)
    r = build(uid, year, now)
    if r["empty"]:
        flash(f"{year} için gönderilecek bir özet yok.", "warning")
        return redirect(back)
    try:
        telegram.send_message(g.user["telegram_chat_id"],
                              summary_text(r, url_for(".index", yil=year, _external=True), telegram.escape))
        flash(f"📨 {year} yıl özeti Telegram'a gönderildi.", "success")
    except telegram.TelegramError as e:
        flash(f"Gönderilemedi: {e}", "error")
    return redirect(back)
