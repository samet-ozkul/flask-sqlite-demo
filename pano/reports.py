"""Aylık rapor (Telegram): her ayın 1'inde günlük özetle birlikte geçen ay gönderilir; bottan /rapor.

İçerik: harcama toplamı ve geçen aya göre değişim, en çok harcanan kategoriler, bütçe sonuçları,
ödenen faturalar, aboneliklerin aylık karşılığı, alışkanlık başarı oranları.
"""
import calendar

from . import budgets, external
from .db import query, query_one
from .utils import MONTHS_TR, fmt_money, month_bounds


def monthly_report(user, year, month, url, escape):
    from .modules.expenses import category_icon, month_summary
    from .modules.subscriptions import compute_totals

    uid = user["id"]
    s = month_summary(uid, year, month)
    start, end = month_bounds(year, month)
    lines = [f"📊 <b>{MONTHS_TR[month - 1]} {year} raporu</b>", ""]

    # Harcamalar
    lines.append(f"💸 <b>Harcama: {fmt_money(s['total'])}</b> ({s['count']} kayıt)")
    if s["change"] is not None:
        arrow = "▲" if s["change"] > 0 else "▼" if s["change"] < 0 else "="
        lines.append(f"Önceki aya göre {arrow} %{abs(round(s['change']))} ({fmt_money(s['prev_total'])})")
    if s["total"]:
        lines.append(f"Günlük ortalama: {fmt_money(s['daily_avg'])}")
        for c, v in s["categories"][:5]:
            lines.append(f"{category_icon(c)} {escape(c)}: {fmt_money(v)} (%{round(v * 100 / s['total'])})")

    # Bütçeler
    status = budgets.month_status(uid, year, month)
    if status:
        lines += ["", "🎯 <b>Bütçe</b>"]
        for r in status:
            mark = "🚨" if r["pct"] >= 100 else "⚠️" if r["pct"] >= 80 else "✅"
            lines.append(f"{mark} {escape(r['label'])}: {fmt_money(r['spent'])} / {fmt_money(r['limit'])} (%{round(r['pct'])})")

    # Faturalar
    paid = query_one("SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS s FROM bills"
                     " WHERE user_id = ? AND paid = 1 AND paid_at >= ? AND paid_at < ?", (uid, start, end))
    overdue = query_one("SELECT COUNT(*) AS n FROM bills WHERE user_id = ? AND paid = 0 AND due_date < ?",
                        (uid, end))["n"]
    if paid["n"] or overdue:
        lines += ["", f"🧾 Ödenen fatura: {paid['n']} · {fmt_money(paid['s'])}"
                  + (f" · ödenmemiş: {overdue}" if overdue else "")]

    # Abonelikler
    subs = query("SELECT * FROM subscriptions WHERE user_id = ? AND active = 1", (uid,))
    if subs:
        totals = compute_totals(subs, external.rates() if any(r["currency"] != "TRY" for r in subs) else None)
        if totals["monthly"] is not None:
            lines.append(f"🔁 Abonelikler: {len(subs)} adet · aylık {fmt_money(totals['monthly'])}")

    # Alışkanlıklar
    days_in_month = calendar.monthrange(year, month)[1]
    habits = query(
        "SELECT h.icon, h.name, (SELECT COUNT(*) FROM habit_logs l WHERE l.habit_id = h.id AND l.date >= ? AND l.date < ?) AS n"
        " FROM habits h WHERE h.user_id = ? AND h.active = 1 ORDER BY h.id", (start, end, uid))
    if habits:
        lines += ["", "🔥 <b>Alışkanlıklar</b>"]
        lines += [f"{h['icon']} {escape(h['name'])}: {h['n']}/{days_in_month} gün (%{round(h['n'] * 100 / days_in_month)})"
                  for h in habits]

    lines += ["", f'<a href="{escape(url)}">Harcamaları aç →</a>']
    return "\n".join(lines)
