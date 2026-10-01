"""⚙️ Otomasyon sayfası: kuralları kur, aç/kapat, dene. Motor: pano/automation.py"""
import json

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .. import automation as auto
from .. import todo_reminders as todo
from .. import trash
from ..auth import login_required
from ..db import execute, owned_or_404, query
from ..utils import form_bool, form_choice, form_int, form_str, parse_number
from .lists import accessible_lists

bp = Blueprint("automations", __name__, url_prefix="/otomasyon")

# Hazır örnekler: ?ornek=... formu doldurur (liste/kişi seçimi kullanıcının verisinden)
EXAMPLES = {
    "kira": {"name": "Kira", "trigger_type": "monthly", "day": "1", "time": "09:00", "action_type": "expense",
             "expense_amount": "15.000", "expense_category": "Ev", "expense_note": "{ay} kirası"},
    "market": {"name": "Market listesi kalabalık", "trigger_type": "list_count", "count": "10", "action_type": "notify",
               "notify_text": "🛒 {liste} listesinde {adet} ürün oldu, markete gitme vakti."},
    "fatura": {"name": "Fatura ödendi haberi", "trigger_type": "bill_paid", "action_type": "notify",
               "notify_text": "🧾 {fatura} faturası ödendi ({tutar})."},
    "cuma": {"name": "Haftalık harcama kontrolü", "trigger_type": "weekly", "weekday": "4", "time": "18:00",
             "action_type": "notify", "notify_text": "💸 Hafta bitti, bu haftanın harcamalarına bir göz at."},
}


def _people(uid):
    """Mesaj gönderilebilecek diğer kullanıcılar (Telegram'ı bağlı olanlar)."""
    return query("SELECT id, username, display_name FROM users WHERE id != ? AND telegram_chat_id IS NOT NULL"
                 " ORDER BY display_name, username", (uid,))


def _example(key, uid):
    form = dict(EXAMPLES.get(key, {}))
    if not form:
        return {}
    shopping = accessible_lists(uid, "shopping")
    if key == "market" and shopping:
        form["list_id"] = str(shopping[0]["id"])
    if key == "fatura":
        people = _people(uid)
        form["notify_to"] = str(people[0]["id"]) if people else "me"
    return form


def _list_ok(uid, raw, kind=None):
    """Formdaki liste id'si erişilebilir bir listeyse int, değilse None."""
    if not str(raw or "").isdigit():
        return None
    return int(raw) if any(l["id"] == int(raw) for l in accessible_lists(uid, kind)) else None


def _parse(uid):
    """Formdan kural; (değerler, hata) döner."""
    f = request.form
    v = {"name": form_str("name", 80), "trigger_type": form_choice("trigger_type", auto.TRIGGERS, "daily"),
         "action_type": form_choice("action_type", auto.ACTIONS, "notify"), "tconf": {}, "aconf": {}}
    t, a, tc, ac = v["trigger_type"], v["action_type"], v["tconf"], v["aconf"]
    if not v["name"]:
        return v, "Kurala bir ad ver."

    if t in auto.SCHEDULED:
        tc["time"] = todo.parse_time(f.get("time")) or "09:00"
        if t == "weekly":
            wd = form_int("weekday")
            tc["weekday"] = wd if wd in range(7) else 0
        if t == "monthly":
            day = form_int("day")
            if day not in range(1, 32):
                return v, "Ayın günü 1 ile 31 arasında olmalı."
            tc["day"] = day
    elif t == "expense_added":
        tc["category"] = form_str("exp_category", 40)
        if (f.get("exp_min") or "").strip():
            amount = parse_number(f.get("exp_min"))
            if amount is None or amount < 0:
                return v, "En az tutar geçerli bir sayı olmalı."
            tc["min_amount"] = amount
    elif t == "list_count":
        tc["list_id"] = _list_ok(uid, f.get("list_id"))
        if tc["list_id"] is None:
            return v, "Takip edilecek listeyi seç."
        count = form_int("count")
        if count not in range(1, 501):
            return v, "Ürün sayısı 1 ile 500 arasında olmalı."
        tc["count"] = count
    elif t == "todo_done" and (f.get("done_list") or "").strip():
        tc["list_id"] = _list_ok(uid, f.get("done_list"))
        if tc["list_id"] is None:
            return v, "Seçilen liste bulunamadı."

    if a == "notify":
        to = f.get("notify_to") or "me"
        if to != "me" and not any(str(p["id"]) == to for p in _people(uid)):
            return v, "Mesaj gidecek kişi Telegram'ı bağlamış bir kullanıcı olmalı."
        ac["to"] = to if to == "me" else int(to)
        ac["text"] = form_str("notify_text", 1000)
        if not ac["text"]:
            return v, "Gönderilecek mesajı yaz."
    elif a == "expense":
        amount = parse_number(f.get("expense_amount"))
        if amount is None or not (0 < amount < 10_000_000):
            return v, "Harcama tutarını gir (ör. 15.000)."
        ac.update(amount=round(amount, 2), category=form_str("expense_category", 40) or "Diğer",
                  note=form_str("expense_note", 200))
    elif a in ("todo", "shopping"):
        ac["list_id"] = _list_ok(uid, f.get(f"{a}_list"), "todo" if a == "todo" else "shopping")
        if ac["list_id"] is None:
            return v, "Eklenecek listeyi seç."
        ac["text"] = form_str(f"{a}_text", 500)
        if not ac["text"]:
            return v, "Eklenecek metni yaz."
        if a == "todo":
            ac["due_today"] = bool(form_bool("todo_due_today"))
    elif a == "note":
        ac.update(title=form_str("note_title", 200), text=form_str("note_text", 5000))
        if not (ac["title"] or ac["text"]):
            return v, "Notun başlığını ya da içeriğini yaz."
    return v, None


def _form_from_rule(rule):
    """Düzenleme formu için kuralı form alanlarına çevirir."""
    form = {"name": rule["name"], "trigger_type": rule["trigger_type"], "action_type": rule["action_type"]}
    tc, ac = rule["tconf"], rule["aconf"]
    form.update({k: str(tc[k]) for k in ("time", "weekday", "day", "count", "list_id") if k in tc})
    if rule["trigger_type"] == "expense_added":
        form.update(exp_category=tc.get("category", ""), exp_min=str(tc.get("min_amount", "") or ""))
    if rule["trigger_type"] == "todo_done":
        form["done_list"] = str(tc.get("list_id") or "")
    a = rule["action_type"]
    if a == "notify":
        form.update(notify_to=str(ac.get("to", "me")), notify_text=ac.get("text", ""))
    elif a == "expense":
        form.update(expense_amount=str(ac.get("amount", "")).replace(".", ","), expense_category=ac.get("category", ""),
                    expense_note=ac.get("note", ""))
    elif a in ("todo", "shopping"):
        form.update({f"{a}_list": str(ac.get("list_id", "")), f"{a}_text": ac.get("text", "")})
        if a == "todo" and ac.get("due_today"):
            form["todo_due_today"] = "1"
    elif a == "note":
        form.update(note_title=ac.get("title", ""), note_text=ac.get("text", ""))
    return form


def _options(lists):
    return [(l["id"], ("🛒 " if l["kind"] == "shopping" else "☑️ ") + l["name"]) for l in lists]


def _render(form, rule=None, status=200):
    uid = g.user["id"]
    people = _people(uid)
    rules = [auto.load(r) for r in query("SELECT * FROM automations WHERE user_id = ? ORDER BY id", (uid,))]
    logs = query("SELECT l.*, a.name FROM automation_log l JOIN automations a ON a.id = l.automation_id"
                 " WHERE l.user_id = ? ORDER BY l.id DESC LIMIT 20", (uid,))
    return render_template(
        "automations/index.html", rules=rules, logs=logs, form=form, editing=rule, examples=EXAMPLES,
        triggers=auto.TRIGGERS, actions=auto.ACTIONS, weekday_options=list(enumerate(auto.WEEKDAYS)),
        placeholders=auto.PLACEHOLDERS, common=auto.COMMON_PLACEHOLDERS, people=people,
        people_options=[(p["id"], p["display_name"] or p["username"]) for p in people],
        all_list_options=_options(accessible_lists(uid)), todo_list_options=_options(accessible_lists(uid, "todo")),
        shopping_list_options=_options(accessible_lists(uid, "shopping")), describe_trigger=auto.describe_trigger,
        describe_action=auto.describe_action, last_run_text=auto.last_run_text,
    ), status


def _rule_or_404(rule_id):
    return auto.load(owned_or_404("automations", rule_id, g.user["id"]))


@bp.route("/")
@login_required
def index():
    form = _example(request.args.get("ornek", ""), g.user["id"]) or {"trigger_type": "daily", "time": "09:00",
                                                                     "action_type": "notify"}
    return _render(form)


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    uid = g.user["id"]
    v, error = _parse(uid)
    if error:
        flash(error, "error")
        return _render(request.form, status=400)
    execute("INSERT INTO automations (user_id, name, trigger_type, trigger_config, action_type, action_config, last_period)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uid, v["name"], v["trigger_type"], json.dumps(v["tconf"], ensure_ascii=False), v["action_type"],
             json.dumps(v["aconf"], ensure_ascii=False),
             auto.initial_period(v["trigger_type"], v["tconf"], todo.now_local())))
    flash(f"⚙️ “{v['name']}” kuralı kuruldu.", "success")
    return redirect(url_for(".index"))


@bp.route("/<int:rule_id>", methods=["GET", "POST"])
@login_required
def edit(rule_id):
    rule = _rule_or_404(rule_id)
    if request.method == "GET":
        return _render(_form_from_rule(rule), rule)
    v, error = _parse(g.user["id"])
    if error:
        flash(error, "error")
        return _render(request.form, rule, status=400)
    schedule_changed = (v["trigger_type"], v["tconf"]) != (rule["trigger_type"], rule["tconf"])
    last_period = auto.initial_period(v["trigger_type"], v["tconf"], todo.now_local()) if schedule_changed \
        else rule["last_period"]
    execute("UPDATE automations SET name = ?, trigger_type = ?, trigger_config = ?, action_type = ?, action_config = ?,"
            " last_period = ? WHERE id = ?",
            (v["name"], v["trigger_type"], json.dumps(v["tconf"], ensure_ascii=False), v["action_type"],
             json.dumps(v["aconf"], ensure_ascii=False), last_period, rule_id))
    flash("Kural güncellendi.", "success")
    return redirect(url_for(".index"))


@bp.route("/<int:rule_id>/ac-kapat", methods=["POST"])
@login_required
def toggle(rule_id):
    rule = _rule_or_404(rule_id)
    on = 0 if rule["enabled"] else 1
    # Yeniden açılan zamanlı kural kapalıyken kaçırdığı dönemi sonradan çalıştırmasın
    last_period = auto.initial_period(rule["trigger_type"], rule["tconf"], todo.now_local()) if on else rule["last_period"]
    execute("UPDATE automations SET enabled = ?, last_period = ? WHERE id = ?", (on, last_period, rule_id))
    flash(f"“{rule['name']}” " + ("açıldı." if on else "kapatıldı."), "success")
    return redirect(url_for(".index"))


@bp.route("/<int:rule_id>/dene", methods=["POST"])
@login_required
def test_run(rule_id):
    """Eylemi şimdi bir kez yapar (örnek değerlerle); sonuç kayıtlarda '🧪 Deneme' olarak görünür."""
    rule = _rule_or_404(rule_id)
    sample = {"tutar": 250.0, "kategori": "Market", "not": "deneme", "fatura": "Elektrik", "liste": "Market",
              "adet": 10, "is": "Çöpü at", "kim": g.user["display_name"] or g.user["username"]}
    payload = {k: sample[k] for k in auto.PLACEHOLDERS[rule["trigger_type"]]}
    ok, detail = auto.run(rule, payload, todo.now_local(), test=True)
    flash(("✅ " if ok else "⚠️ ") + detail, "success" if ok else "error")
    return redirect(url_for(".index"))


@bp.route("/<int:rule_id>/sil", methods=["POST"])
@login_required
def delete(rule_id):
    rule = _rule_or_404(rule_id)
    trash.move(g.user["id"], "automations", f"⚙️ {rule['name']}", ("automations", rule_id),
               children=[("automation_log", "automation_id = ?")])
    flash(trash.notice(f"“{rule['name']}” kuralı"), "success")
    return redirect(url_for(".index"))
