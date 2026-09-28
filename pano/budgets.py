"""Aylık bütçe limitleri: kategori bazında ve toplam ('*').

Harcamalar sayfasında doluluk çubukları; cron /hatirlatma %80 ve %100'de Telegram uyarısı gönderir
(her ay, her kategori ve her seviye için bir kez; durum app_state'te).
"""
from .db import get_db, query, query_one
from .utils import fmt_money, month_bounds

TOTAL = "*"
LEVELS = (80, 100)


def budgets_of(user_id):
    return {r["category"]: r["amount"] for r in query("SELECT category, amount FROM budgets WHERE user_id = ?", (user_id,))}


def save(user_id, limits):
    """limits: {kategori: tutar|None}; None/0 olan limit kaldırılır."""
    db = get_db()
    for category, amount in limits.items():
        if amount and amount > 0:
            db.execute("INSERT OR REPLACE INTO budgets (user_id, category, amount) VALUES (?, ?, ?)",
                       (user_id, category, round(amount, 2)))
        else:
            db.execute("DELETE FROM budgets WHERE user_id = ? AND category = ?", (user_id, category))
    db.commit()


def month_status(user_id, year, month):
    """[{category, label, limit, spent, pct, level}] — önce toplam, sonra kategoriler (doluluğa göre)."""
    limits = budgets_of(user_id)
    if not limits:
        return []
    start, end = month_bounds(year, month)
    spent = {r["category"]: r["s"] for r in query(
        "SELECT category, SUM(amount) AS s FROM expenses WHERE user_id = ? AND date >= ? AND date < ? GROUP BY category",
        (user_id, start, end))}
    total = sum(spent.values())
    rows = []
    for category, limit in limits.items():
        used = total if category == TOTAL else spent.get(category, 0)
        pct = used * 100 / limit if limit else 0
        rows.append({
            "category": category,
            "label": "Toplam" if category == TOTAL else category,
            "limit": limit, "spent": round(used, 2), "pct": pct,
            "level": 100 if pct >= 100 else 80 if pct >= 80 else None,
        })
    rows.sort(key=lambda r: (r["category"] != TOTAL, -r["pct"]))
    return rows


def status_line(user_id, category, year, month):
    """Tek kategori için 'Market bütçesi: 2.550 / 3.000 ₺ (%85)' ya da None."""
    for r in month_status(user_id, year, month):
        if r["category"] == category:
            return f"{r['label']} bütçesi: {fmt_money(r['spent'])} / {fmt_money(r['limit'])} (%{round(r['pct'])})"
    return None


# ---------- Uyarılar ----------
def _state_key(user_id, category, year, month):
    return f"budget:{user_id}:{category}:{year:04d}-{month:02d}"


def pending_alerts(now):
    """[(kullanıcı, satır)] — bu ay yeni bir seviyeye (%80 / %100) ulaşan bütçeler."""
    out = []
    users = query("SELECT DISTINCT u.* FROM users u JOIN budgets b ON b.user_id = u.id"
                  " WHERE u.telegram_chat_id IS NOT NULL")
    for user in users:
        for row in month_status(user["id"], now.year, now.month):
            if row["level"] is None:
                continue
            state = query_one("SELECT value FROM app_state WHERE key = ?",
                              (_state_key(user["id"], row["category"], now.year, now.month),))
            if state is None or int(state["value"]) < row["level"]:
                out.append((user, row))
    return out


def mark_alert(user_id, row, now):
    db = get_db()
    db.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)",
               (_state_key(user_id, row["category"], now.year, now.month), str(row["level"])))
    db.commit()


def alert_message(row, url, escape):
    name = "Aylık toplam bütçe" if row["category"] == TOTAL else f"{escape(row['label'])} bütçesi"
    amounts = f"{fmt_money(row['spent'])} / {fmt_money(row['limit'])}"
    if row["level"] >= 100:
        head = f"🚨 <b>{name} aşıldı</b>"
    else:
        head = f"⚠️ <b>{name} %{round(row['pct'])} doldu</b>"
    return f"{head}\n{amounts}\n<a href=\"{escape(url)}\">Harcamalar →</a>"
