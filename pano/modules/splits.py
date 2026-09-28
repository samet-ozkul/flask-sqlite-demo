"""👥 Ortak Harcama: ev, tatil, arkadaş grubu... kim ne ödedi, kim kime ne kadar ödemeli (Splitwise benzeri).

Erişim kuralı:
- Grubu oluşturan sahibidir ve otomatik olarak üyesidir.
- Üye ya uygulama kullanıcısıdır (user_id dolu) ya da hesabı olmayan bir kişidir (sadece ad, ör. "Ali").
- Grubu sahibi ve uygulama kullanıcısı olan üyeler görür; harcama ve ödeme ekleyebilir. Diğerleri 404 alır.
- Grubun adı, silinmesi, üye ekleme/çıkarma sadece sahibine açıktır (403).
- Harcamayı ekleyen ya da grup sahibi düzenler/siler.
- Ödeme (ödeşme) kaydını grup sahibi ya da iki taraftan birinin kullanıcısı siler.
- Harcamada ya da ödemede adı geçen üye çıkarılamaz (önce o kayıtlar silinmeli).

Hesap kuruş (tam sayı) ile yapılır: bir harcamanın payları toplamı her zaman tutara eşittir; artan
kuruşlar sıradaki ilk üyelere dağıtılır (100 TL / 3 = 33,34 + 33,33 + 33,33).

Bakiye (net) = ödediği − payı + gönderdiği ödemeler − aldığı ödemeler.
Artı: diğerleri ona borçlu (alacaklı). Eksi: o borçlu.

Diğer modüller için: balances(group_id), settle_plan(group_id), my_balance(group_id, user_id),
user_groups(user_id), group_or_404(group_id, user_id).
"""
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for

from .. import telegram
from ..auth import login_required
from ..db import execute, get_db, query, query_one
from ..utils import fmt_money, fold, form_date, form_int, form_str, today_str
from .expenses import form_amount, valid_amount

bp = Blueprint("splits", __name__, url_prefix="/ortak")

TABS = {"harcamalar": "Harcamalar", "bakiyeler": "Bakiyeler", "uyeler": "Üyeler"}
GROUP_NAME_MAX = 80
MEMBER_NAME_MAX = 60
NOTE_MAX = 200
MAX_MEMBERS = 50
EXPENSES_SHOWN = 300
SETTLEMENTS_SHOWN = 100
# 0,01 TL tolerans: hesap tam sayı kuruşla yapıldığından 1 kuruşun altı "denk" sayılır
TOLERANCE_CENTS = 1

_GROUP_SELECT = (
    "SELECT sg.*, u.username AS owner_username, u.display_name AS owner_display"
    " FROM split_groups sg JOIN users u ON u.id = sg.owner_id"
)
# Sahibi ya da uygulama kullanıcısı üyesi olan görür (iki parametre: user_id, user_id)
_ACCESS = (
    "(sg.owner_id = ? OR EXISTS (SELECT 1 FROM split_members am"
    " WHERE am.group_id = sg.id AND am.user_id = ?))"
)


# ---------- Hesap yardımcıları ----------
def cents(value):
    """TL -> kuruş (tam sayı)."""
    return int(round((value or 0) * 100))


def split_equal(total_cents, member_ids):
    """Kuruş tutarını üyelere eşit böler: {member_id: kuruş}. Toplam her zaman total_cents'e eşittir;
    artan kuruşlar id sırasına göre ilk üyelere verilir."""
    ids = sorted(set(member_ids))
    if not ids:
        return {}
    base, extra = divmod(total_cents, len(ids))
    return {mid: base + (1 if i < extra else 0) for i, mid in enumerate(ids)}


# ---------- Erişim / sorgu yardımcıları ----------
def group_or_404(group_id, user_id, owner_only=False):
    """Erişilebilir grubu döner. Erişim yoksa 404, owner_only iken sahibi değilse 403."""
    row = query_one(_GROUP_SELECT + " WHERE sg.id = ? AND " + _ACCESS, (group_id, user_id, user_id))
    if row is None:
        abort(404)
    if owner_only and row["owner_id"] != user_id:
        abort(403)
    return row


def user_groups(user_id):
    """Kullanıcının sahibi ya da üyesi olduğu gruplar (üye sayısı, harcama sayısı ve toplamıyla)."""
    return query(
        "SELECT sg.*, u.username AS owner_username, u.display_name AS owner_display,"
        " (SELECT COUNT(*) FROM split_members m WHERE m.group_id = sg.id) AS member_count,"
        " (SELECT COUNT(*) FROM split_expenses e WHERE e.group_id = sg.id) AS expense_count,"
        " (SELECT COALESCE(SUM(e.amount), 0) FROM split_expenses e WHERE e.group_id = sg.id) AS total"
        " FROM split_groups sg JOIN users u ON u.id = sg.owner_id"
        " WHERE " + _ACCESS + " ORDER BY sg.name COLLATE NOCASE, sg.id",
        (user_id, user_id),
    )


def members(group_id):
    return query(
        "SELECT m.*, u.username, u.display_name AS user_display"
        " FROM split_members m LEFT JOIN users u ON u.id = m.user_id"
        " WHERE m.group_id = ? ORDER BY m.id",
        (group_id,),
    )


def _sums(sql, group_id):
    return {r[0]: r[1] or 0 for r in query(sql, (group_id,))}


def _ledger(group_id):
    """Üye başına kuruş cinsinden (üye, ödediği, payı, gönderdiği, aldığı)."""
    paid = _sums(
        "SELECT payer_id, SUM(CAST(ROUND(amount * 100) AS INTEGER)) FROM split_expenses"
        " WHERE group_id = ? GROUP BY payer_id", group_id)
    owed = _sums(
        "SELECT s.member_id, SUM(CAST(ROUND(s.share * 100) AS INTEGER)) FROM split_shares s"
        " JOIN split_expenses e ON e.id = s.expense_id WHERE e.group_id = ? GROUP BY s.member_id", group_id)
    sent = _sums(
        "SELECT from_id, SUM(CAST(ROUND(amount * 100) AS INTEGER)) FROM split_settlements"
        " WHERE group_id = ? GROUP BY from_id", group_id)
    received = _sums(
        "SELECT to_id, SUM(CAST(ROUND(amount * 100) AS INTEGER)) FROM split_settlements"
        " WHERE group_id = ? GROUP BY to_id", group_id)
    return [(m, paid.get(m["id"], 0), owed.get(m["id"], 0), sent.get(m["id"], 0), received.get(m["id"], 0))
            for m in members(group_id)]


def balances(group_id):
    """[{"member": satır, "paid", "owed", "sent", "received", "net"}] — TL (float), üye ekleme sırasıyla.
    net > 0: diğerleri bu üyeye borçlu; net < 0: bu üye borçlu."""
    return [
        {"member": m, "paid": p / 100, "owed": o / 100, "sent": s / 100, "received": r / 100,
         "net": (p - o + s - r) / 100}
        for m, p, o, s, r in _ledger(group_id)
    ]


def settle_plan(group_id, rows=None):
    """En az transferle ödeşme önerisi: [{"from": üye, "to": üye, "amount": float}].
    Açgözlü yöntem: her adımda en çok borçlu olan, en çok alacaklı olana öder."""
    rows = balances(group_id) if rows is None else rows
    debtors = [[-cents(b["net"]), b["member"]] for b in rows if cents(b["net"]) <= -TOLERANCE_CENTS]
    creditors = [[cents(b["net"]), b["member"]] for b in rows if cents(b["net"]) >= TOLERANCE_CENTS]
    plan = []
    while debtors and creditors:
        debtors.sort(key=lambda x: (-x[0], x[1]["id"]))
        creditors.sort(key=lambda x: (-x[0], x[1]["id"]))
        debtor, creditor = debtors[0], creditors[0]
        amount = min(debtor[0], creditor[0])
        plan.append({"from": debtor[1], "to": creditor[1], "amount": amount / 100})
        debtor[0] -= amount
        creditor[0] -= amount
        debtors = [d for d in debtors if d[0] >= TOLERANCE_CENTS]
        creditors = [c for c in creditors if c[0] >= TOLERANCE_CENTS]
    return plan


def my_member(group_id, user_id):
    return query_one(
        "SELECT * FROM split_members WHERE group_id = ? AND user_id = ? ORDER BY id LIMIT 1", (group_id, user_id))


def my_balance(group_id, user_id, rows=None):
    """Kullanıcının gruptaki net bakiyesi (TL); grupta üye değilse None."""
    rows = balances(group_id) if rows is None else rows
    for b in rows:
        if b["member"]["user_id"] == user_id:
            return b["net"]
    return None


def member_usage(group_id):
    """{member_id: {"paid", "shares", "settlements", "text"}} — üye çıkarılabilir mi, neden olmaz."""
    rows = query(
        "SELECT m.id,"
        " (SELECT COUNT(*) FROM split_expenses e WHERE e.payer_id = m.id) AS paid,"
        " (SELECT COUNT(*) FROM split_shares s WHERE s.member_id = m.id) AS shares,"
        " (SELECT COUNT(*) FROM split_settlements t WHERE t.from_id = m.id OR t.to_id = m.id) AS settlements"
        " FROM split_members m WHERE m.group_id = ?",
        (group_id,),
    )
    out = {}
    for r in rows:
        parts = []
        if r["paid"]:
            parts.append(f"{r['paid']} harcamayı ödemiş")
        if r["shares"]:
            parts.append(f"{r['shares']} harcamada payı var")
        if r["settlements"]:
            parts.append(f"{r['settlements']} ödemede yer alıyor")
        out[r["id"]] = {"paid": r["paid"], "shares": r["shares"], "settlements": r["settlements"],
                        "text": ", ".join(parts)}
    return out


def _member_options(member_rows, uid):
    return [(m["id"], m["name"] + (" (sen)" if m["user_id"] == uid else "")) for m in member_rows]


def _detail_url(group_id, tab=None):
    return url_for("splits.detail", group_id=group_id, sekme=tab) if tab else url_for(
        "splits.detail", group_id=group_id)


# ---------- Telegram ----------
def _notify_expense(group, payer, amount, note, shares):
    """Harcamayı ekleyen dışındaki, Telegram'ı bağlı uygulama kullanıcısı üyelere haber verir.
    Hiçbir durumda istek başarısız olmaz."""
    if not telegram.enabled():
        return 0
    sent = 0
    try:
        rows = query(
            "SELECT m.id AS member_id, m.user_id, u.telegram_chat_id FROM split_members m"
            " JOIN users u ON u.id = m.user_id"
            " WHERE m.group_id = ? AND m.user_id != ?"
            " AND u.telegram_chat_id IS NOT NULL AND u.telegram_chat_id != '' ORDER BY m.id",
            (group["id"], g.user["id"]),
        )
        head = (f"👥 <b>{telegram.escape(group['name'])}</b>: {telegram.escape(payer['name'])} "
                f"{telegram.escape(fmt_money(amount))} ödedi")
        if note:
            head += f" ({telegram.escape(note)})"
        seen = set()
        for r in rows:
            if r["user_id"] in seen:
                continue
            seen.add(r["user_id"])
            share = shares.get(r["member_id"], 0) / 100
            if not share:
                continue  # bölüşüme dahil olmayana "payın 0 ₺" diye mesaj atma
            try:
                telegram.send_message(r["telegram_chat_id"],
                                      f"{head}. Senin payın: {telegram.escape(fmt_money(share))}.")
                sent += 1
            except telegram.TelegramError as e:
                current_app.logger.warning("Ortak harcama bildirimi gönderilemedi (kullanıcı %s): %s",
                                           r["user_id"], e)
    except Exception:  # bildirim yan etkidir; harcama kaydı zaten yapıldı
        current_app.logger.exception("Ortak harcama bildirimi hatası")
    return sent


# ---------- Form ----------
def _expense_form(member_rows):
    """(değerler, hata). Ödeyen ve paylaşanlar grubun üyeleri olmalı."""
    ids = {m["id"] for m in member_rows}
    payer_id = form_int("payer")
    amount = form_amount("amount")
    selected = set()
    for value in request.form.getlist("members"):
        try:
            mid = int(value)
        except (TypeError, ValueError):
            continue
        if mid in ids:
            selected.add(mid)
    if payer_id not in ids:
        return None, "Ödeyen kişiyi seç."
    if not valid_amount(amount) or cents(amount) < 1:
        return None, "Tutar 0'dan büyük bir sayı olmalı (ör. 1.500 ya da 250,50)."
    if not selected:
        return None, "Harcamayı paylaşacak en az bir kişi seç."
    total = cents(amount)
    return {
        "payer_id": payer_id,
        "amount": total / 100,
        "cents": total,
        "note": form_str("note", NOTE_MAX),
        "date": form_date("date") or today_str(),
        "shares": split_equal(total, selected),
    }, None


def _write_shares(db, expense_id, shares):
    db.execute("DELETE FROM split_shares WHERE expense_id = ?", (expense_id,))
    db.executemany(
        "INSERT INTO split_shares (expense_id, member_id, share) VALUES (?, ?, ?)",
        [(expense_id, mid, c / 100) for mid, c in shares.items()],
    )


def _expense_or_404(expense_id, user_id):
    """Harcama + grubu; erişim yoksa 404, ekleyen ya da grup sahibi değilse 403."""
    expense = query_one("SELECT * FROM split_expenses WHERE id = ?", (expense_id,))
    if expense is None:
        abort(404)
    group = group_or_404(expense["group_id"], user_id)
    if user_id not in (expense["created_by"], group["owner_id"]):
        abort(403)
    return expense, group


# ---------- Rotalar: gruplar ----------
@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    items, credit, debt = [], 0, 0
    for grp in user_groups(uid):
        net = my_balance(grp["id"], uid)
        items.append({"group": grp, "net": net})
        c = cents(net)
        if c > 0:
            credit += c
        elif c < 0:
            debt -= c
    return render_template("splits/index.html", items=items, credit=credit / 100, debt=debt / 100)


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    name = form_str("name", GROUP_NAME_MAX)
    if not name:
        flash("Grup adı boş olamaz.", "warning")
        return redirect(url_for("splits.index"))
    user = g.user
    db = get_db()
    group_id = db.execute("INSERT INTO split_groups (owner_id, name) VALUES (?, ?)", (user["id"], name)).lastrowid
    db.execute(
        "INSERT INTO split_members (group_id, user_id, name) VALUES (?, ?, ?)",
        (group_id, user["id"], (user["display_name"] or user["username"])[:MEMBER_NAME_MAX]),
    )
    db.commit()
    flash(f"“{name}” grubu oluşturuldu. Şimdi üyeleri ekleyebilirsin.", "success")
    return redirect(_detail_url(group_id, "uyeler"))


@bp.route("/<int:group_id>")
@login_required
def detail(group_id):
    uid = g.user["id"]
    group = group_or_404(group_id, uid)
    tab = request.args.get("sekme")
    tab = tab if tab in TABS else "harcamalar"
    rows = balances(group_id)
    member_rows = [b["member"] for b in rows]
    me = next((m for m in member_rows if m["user_id"] == uid), None)
    totals = query_one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total FROM split_expenses WHERE group_id = ?",
        (group_id,),
    )
    is_owner = group["owner_id"] == uid
    ctx = {
        "group": group, "tab": tab, "tabs": TABS, "is_owner": is_owner, "me": me,
        "members": member_rows, "member_options": _member_options(member_rows, uid),
        "balances": rows, "my_net": my_balance(group_id, uid, rows),
        "expense_count": totals["n"], "total": totals["total"],
    }
    if tab == "harcamalar":
        ctx["expenses"] = query(
            "SELECT e.*, pm.name AS payer_name, pm.user_id AS payer_user_id,"
            " cu.username AS creator_username, cu.display_name AS creator_display,"
            " (SELECT COUNT(*) FROM split_shares s WHERE s.expense_id = e.id) AS share_count,"
            " (SELECT s.share FROM split_shares s WHERE s.expense_id = e.id AND s.member_id = ?) AS my_share"
            " FROM split_expenses e JOIN split_members pm ON pm.id = e.payer_id"
            " LEFT JOIN users cu ON cu.id = e.created_by"
            " WHERE e.group_id = ? ORDER BY e.date DESC, e.id DESC LIMIT ?",
            (me["id"] if me else -1, group_id, EXPENSES_SHOWN),
        )
        ctx["shown_limit"] = EXPENSES_SHOWN
    elif tab == "bakiyeler":
        ctx["plan"] = settle_plan(group_id, rows)
        ctx["settlements"] = query(
            "SELECT t.*, f.name AS from_name, f.user_id AS from_user, tm.name AS to_name, tm.user_id AS to_user"
            " FROM split_settlements t JOIN split_members f ON f.id = t.from_id"
            " JOIN split_members tm ON tm.id = t.to_id"
            " WHERE t.group_id = ? ORDER BY t.date DESC, t.id DESC LIMIT ?",
            (group_id, SETTLEMENTS_SHOWN),
        )
        others = [m for m in member_rows if not me or m["id"] != me["id"]]
        ctx["default_to"] = others[0]["id"] if others else None
    else:
        ctx["usage"] = member_usage(group_id)
        ctx["candidates"] = query(
            "SELECT id, username, display_name FROM users WHERE id NOT IN"
            " (SELECT user_id FROM split_members WHERE group_id = ? AND user_id IS NOT NULL)"
            " ORDER BY username COLLATE NOCASE",
            (group_id,),
        ) if is_owner else []
        ctx["max_members"] = MAX_MEMBERS
    return render_template("splits/detail.html", **ctx)


@bp.route("/<int:group_id>/ayarlar", methods=["POST"])
@login_required
def update(group_id):
    group_or_404(group_id, g.user["id"], owner_only=True)
    name = form_str("name", GROUP_NAME_MAX)
    if not name:
        flash("Grup adı boş olamaz.", "warning")
        return redirect(_detail_url(group_id, "uyeler"))
    execute("UPDATE split_groups SET name = ? WHERE id = ?", (name, group_id))
    flash("Grup adı güncellendi.", "success")
    return redirect(_detail_url(group_id, "uyeler"))


@bp.route("/<int:group_id>/sil", methods=["POST"])
@login_required
def delete(group_id):
    group = group_or_404(group_id, g.user["id"], owner_only=True)
    db = get_db()
    db.execute("DELETE FROM split_shares WHERE expense_id IN (SELECT id FROM split_expenses WHERE group_id = ?)",
               (group_id,))
    db.execute("DELETE FROM split_settlements WHERE group_id = ?", (group_id,))
    db.execute("DELETE FROM split_expenses WHERE group_id = ?", (group_id,))
    db.execute("DELETE FROM split_members WHERE group_id = ?", (group_id,))
    db.execute("DELETE FROM split_groups WHERE id = ?", (group_id,))
    db.commit()
    flash(f"“{group['name']}” grubu silindi.", "success")
    return redirect(url_for("splits.index"))


# ---------- Rotalar: üyeler ----------
@bp.route("/<int:group_id>/uye", methods=["POST"])
@login_required
def add_member(group_id):
    group_or_404(group_id, g.user["id"], owner_only=True)
    back = redirect(_detail_url(group_id, "uyeler"))
    existing = members(group_id)
    if len(existing) >= MAX_MEMBERS:
        flash(f"Bir grupta en fazla {MAX_MEMBERS} üye olabilir.", "warning")
        return back
    username = form_str("username", 60)
    name = form_str("name", MEMBER_NAME_MAX)
    user = None
    if username:
        user = query_one("SELECT id, username, display_name FROM users WHERE username = ?", (username,))
        if user is None:
            flash("Böyle bir kullanıcı yok.", "error")
            return back
        if any(m["user_id"] == user["id"] for m in existing):
            flash(f"@{user['username']} zaten bu grupta.", "warning")
            return back
        name = name or (user["display_name"] or user["username"])[:MEMBER_NAME_MAX]
    elif not name:
        flash("Kişinin adını yaz ya da bir kullanıcı seç.", "warning")
        return back
    taken = {fold(m["name"]) for m in existing}
    if fold(name) in taken and user:
        name = f"{name} (@{user['username']})"[:MEMBER_NAME_MAX]
    if fold(name) in taken:
        flash(f"Grupta “{name}” adında biri zaten var; farklı bir ad yaz.", "warning")
        return back
    execute("INSERT INTO split_members (group_id, user_id, name) VALUES (?, ?, ?)",
            (group_id, user["id"] if user else None, name))
    flash(f"{name} gruba eklendi.", "success")
    return back


@bp.route("/uye/<int:member_id>/sil", methods=["POST"])
@login_required
def remove_member(member_id):
    member = query_one("SELECT * FROM split_members WHERE id = ?", (member_id,))
    if member is None:
        abort(404)
    group = group_or_404(member["group_id"], g.user["id"], owner_only=True)
    back = redirect(_detail_url(group["id"], "uyeler"))
    if member["user_id"] is not None and member["user_id"] == group["owner_id"]:
        flash("Grup sahibi gruptan çıkarılamaz.", "warning")
        return back
    usage = member_usage(group["id"]).get(member_id, {})
    if usage.get("text"):
        flash(f"{member['name']} çıkarılamaz: {usage['text']}. Önce bu kayıtları sil ya da düzenle.", "warning")
        return back
    execute("DELETE FROM split_members WHERE id = ?", (member_id,))
    flash(f"{member['name']} gruptan çıkarıldı.", "success")
    return back


# ---------- Rotalar: harcamalar ----------
@bp.route("/<int:group_id>/harcama", methods=["POST"])
@login_required
def add_expense(group_id):
    group = group_or_404(group_id, g.user["id"])
    member_rows = members(group_id)
    v, error = _expense_form(member_rows)
    if error:
        flash(error, "error")
        return redirect(_detail_url(group_id))
    db = get_db()
    expense_id = db.execute(
        "INSERT INTO split_expenses (group_id, payer_id, amount, note, date, created_by)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (group_id, v["payer_id"], v["amount"], v["note"], v["date"], g.user["id"]),
    ).lastrowid
    _write_shares(db, expense_id, v["shares"])
    db.commit()
    payer = next(m for m in member_rows if m["id"] == v["payer_id"])
    flash(f"{fmt_money(v['amount'])} harcama eklendi ({len(v['shares'])} kişiye bölündü).", "success")
    _notify_expense(group, payer, v["amount"], v["note"], v["shares"])
    return redirect(_detail_url(group_id))


@bp.route("/harcama/<int:expense_id>", methods=["GET", "POST"])
@login_required
def edit_expense(expense_id):
    uid = g.user["id"]
    expense, group = _expense_or_404(expense_id, uid)
    member_rows = members(group["id"])
    if request.method == "POST":
        v, error = _expense_form(member_rows)
        if error:
            flash(error, "error")
            return redirect(url_for("splits.edit_expense", expense_id=expense_id))
        db = get_db()
        db.execute(
            "UPDATE split_expenses SET payer_id = ?, amount = ?, note = ?, date = ? WHERE id = ?",
            (v["payer_id"], v["amount"], v["note"], v["date"], expense_id),
        )
        _write_shares(db, expense_id, v["shares"])
        db.commit()
        flash("Harcama güncellendi.", "success")
        return redirect(_detail_url(group["id"]))
    selected = {r["member_id"] for r in query("SELECT member_id FROM split_shares WHERE expense_id = ?",
                                              (expense_id,))}
    creator = query_one("SELECT username, display_name FROM users WHERE id = ?", (expense["created_by"],)) \
        if expense["created_by"] else None
    return render_template(
        "splits/expense_edit.html", expense=expense, group=group, members=member_rows,
        member_options=_member_options(member_rows, uid), selected=selected, creator=creator,
    )


@bp.route("/harcama/<int:expense_id>/sil", methods=["POST"])
@login_required
def delete_expense(expense_id):
    expense, group = _expense_or_404(expense_id, g.user["id"])
    db = get_db()
    db.execute("DELETE FROM split_shares WHERE expense_id = ?", (expense_id,))
    db.execute("DELETE FROM split_expenses WHERE id = ?", (expense_id,))
    db.commit()
    flash(f"{fmt_money(expense['amount'])} harcama silindi.", "success")
    return redirect(_detail_url(group["id"]))


# ---------- Rotalar: ödemeler (ödeşme) ----------
@bp.route("/<int:group_id>/odeme", methods=["POST"])
@login_required
def add_settlement(group_id):
    group_or_404(group_id, g.user["id"])
    back = redirect(_detail_url(group_id, "bakiyeler"))
    by_id = {m["id"]: m for m in members(group_id)}
    from_id, to_id = form_int("from_id"), form_int("to_id")
    amount = form_amount("amount")
    if from_id not in by_id or to_id not in by_id:
        flash("Ödeyen ve ödemeyi alan kişiyi seç.", "error")
        return back
    if from_id == to_id:
        flash("Kişi kendine ödeme yapamaz.", "error")
        return back
    if not valid_amount(amount) or cents(amount) < 1:
        flash("Tutar 0'dan büyük bir sayı olmalı (ör. 1.500 ya da 250,50).", "error")
        return back
    amount = cents(amount) / 100
    execute(
        "INSERT INTO split_settlements (group_id, from_id, to_id, amount, date) VALUES (?, ?, ?, ?, ?)",
        (group_id, from_id, to_id, amount, form_date("date") or today_str()),
    )
    flash(f"{by_id[from_id]['name']} → {by_id[to_id]['name']}: {fmt_money(amount)} ödeme kaydedildi.", "success")
    return back


@bp.route("/odeme/<int:settlement_id>/sil", methods=["POST"])
@login_required
def delete_settlement(settlement_id):
    uid = g.user["id"]
    row = query_one(
        "SELECT t.*, f.user_id AS from_user, tm.user_id AS to_user FROM split_settlements t"
        " JOIN split_members f ON f.id = t.from_id JOIN split_members tm ON tm.id = t.to_id WHERE t.id = ?",
        (settlement_id,),
    )
    if row is None:
        abort(404)
    group = group_or_404(row["group_id"], uid)
    if uid not in (group["owner_id"], row["from_user"], row["to_user"]):
        abort(403)
    execute("DELETE FROM split_settlements WHERE id = ?", (settlement_id,))
    flash("Ödeme kaydı silindi.", "success")
    return redirect(_detail_url(group["id"], "bakiyeler"))
