"""📇 Kişiler: iletişim hatırlatıcı ("dedemi 3 haftadır aramadın").

- Her kişiye isteğe bağlı görüşme sıklığı (haftada bir … 6 ayda bir); vade = son görüşme
  (hiç yoksa eklendiği gün) + sıklık. Liste en çok gecikmiş olandan başlar.
- 📞 Aradım / 💬 Mesajlaştık / ☕ Görüştük tek dokunuşla kayıt ekler; geçmişe dönük kayıt da girilir.
  Son görüşme her zaman kayıtların en yenisidir (kayıt silinince geri hesaplanır).
- Vadesi gelen kişi için 09:00'dan sonra Telegram'dan bir kez dürtme (cron /hatirlatma, günde bir kontrol):
  "✅ Aradım" kaydeder, "⏰ Yarın hatırlat" ertesi gün yeniden sorar. Gecikenler günlük özette de yazar.
- Doğum günü (yıl bilinmeyebilir: '--MM-DD') yaklaşanlarda ve takvimde görünür; 29 Şubat artık yıl
  olmayan yıllarda 28 Şubat sayılır.
"""
import re
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import trash
from ..auth import login_required
from ..db import execute, owned_or_404, query, query_one
from ..utils import (MONTHS_TR, TZ, fold, form_choice, form_int, form_str, parse_date, redirect_back, today)
from .specialdays import occurrence

bp = Blueprint("contacts", __name__, url_prefix="/kisiler")

RELATIONS = ["Aile", "Arkadaş", "İş", "Komşu", "Diğer"]
RELATION_ICONS = {"aile": "👪", "arkadas": "🧑‍🤝‍🧑", "is": "💼", "komsu": "🏠"}
EVERY_OPTIONS = [(7, "Haftada bir"), (14, "2 haftada bir"), (30, "Ayda bir"), (60, "2 ayda bir"),
                 (90, "3 ayda bir"), (180, "6 ayda bir")]
KINDS = {"call": ("📞", "Arama"), "message": ("💬", "Mesajlaşma"), "visit": ("☕", "Yüz yüze"), "other": ("📌", "Diğer")}
QUICK = [("call", "📞 Aradım"), ("message", "💬 Mesajlaştık"), ("visit", "☕ Görüştük")]
SOON_DAYS = 3
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
NOTE_MAX = 1000


def relation_icon(relation):
    return RELATION_ICONS.get(fold(relation), "👤")


def relation_suggestions(user_id):
    """Datalist: önerilenler + kullanıcının yazdığı diğer ilişkiler."""
    out = list(RELATIONS)
    seen = {fold(r) for r in out}
    for r in query("SELECT DISTINCT relation FROM contacts WHERE user_id = ? AND relation != '' ORDER BY relation",
                   (user_id,)):
        if fold(r["relation"]) not in seen:
            seen.add(fold(r["relation"]))
            out.append(r["relation"])
    return out


def every_label(days):
    return dict(EVERY_OPTIONS).get(days, f"{days} günde bir") if days else ""


# ---------- Telefon ----------
def tel_number(phone):
    """tel: bağlantısı için sadece rakamlar ve baştaki +."""
    s = re.sub(r"[^\d+]", "", phone or "")
    return s[0] + s[1:].replace("+", "") if s else ""


def whatsapp_number(phone):
    """wa.me için uluslararası biçim (sadece rakam): '0532 123 45 67' -> '905321234567',
    '+90 532…' -> '90532…', '00 49…' -> '49…', '532 123 45 67' -> '90532…'. Çok kısaysa None."""
    raw = (phone or "").strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 7:
        return None
    if raw.startswith("+"):
        return digits
    if digits.startswith("00"):
        return digits[2:]
    if digits.startswith("0"):
        return "90" + digits[1:]
    if len(digits) == 10 and digits.startswith("5"):
        return "90" + digits
    return digits


# ---------- Doğum günü ----------
def parse_birthday(value):
    """'1966-03-12' -> (1966, 3, 12); '--03-12' (yıl bilinmiyor) -> (None, 3, 12); boş/geçersiz -> None."""
    m = re.fullmatch(r"(\d{4}|-)-(\d{2})-(\d{2})", value or "")
    if not m:
        return None
    year = int(m.group(1)) if m.group(1) != "-" else None
    month, day = int(m.group(2)), int(m.group(3))
    try:
        date(year or 2000, month, day)  # 2000 artık yıl: 29 Şubat geçerli
    except ValueError:
        return None
    return year, month, day


def birthday_on(contact, year):
    """O yılki doğum günü (29 Şubat artık yıl değilse 28 Şubat); doğum günü yoksa None."""
    b = parse_birthday(contact["birthday"])
    return occurrence(b[1], b[2], year) if b else None


def next_birthday(contact, from_date=None):
    t = from_date or today()
    d = birthday_on(contact, t.year)
    if d is None:
        return None
    return d if d >= t else birthday_on(contact, t.year + 1)


def age_on(contact, on):
    """O gün kaçıncı yaşı (yıl biliniyorsa)."""
    b = parse_birthday(contact["birthday"])
    if not b or not b[0] or on.year <= b[0]:
        return None
    return on.year - b[0]


def birthday_title(contact, on):
    """'🎂 Dede doğum günü (60. yaş)' metninin ikonsuz hali."""
    age = age_on(contact, on)
    return f"{contact['name']} doğum günü" + (f" ({age}. yaş)" if age else "")


def birthday_text(contact):
    """Sayfada gösterilen: '12 Mart 1966' ya da '12 Mart'."""
    b = parse_birthday(contact["birthday"])
    if not b:
        return ""
    return f"{b[2]} {MONTHS_TR[b[1] - 1]}" + (f" {b[0]}" if b[0] else "")


# ---------- Vade ----------
def _created_date(row):
    """created_at (UTC) -> yerel tarih."""
    try:
        dt = datetime.fromisoformat(str(row["created_at"]).replace(" ", "T"))
    except ValueError:
        return today()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ).date()


def status(row, t=None):
    """{due, left, since, text, cls}. due/left: sıklık yoksa None; since: son görüşmeden beri gün."""
    t = t or today()
    last = parse_date(row["last_contact_at"])
    since = (t - last).days if last else None
    due = left = None
    if row["contact_every"]:
        due = (last or _created_date(row)) + timedelta(days=row["contact_every"])
        left = (due - t).days
    if left is not None and left <= 0:
        text, cls = (f"{since} gündür görüşülmedi" if last else "hiç kaydedilmedi"), "overdue"
    elif left is not None:
        text, cls = ("yarın" if left == 1 else f"{left} gün kaldı"), ("soon" if left <= SOON_DAYS else "")
    elif last:
        text, cls = ("bugün görüşüldü" if since == 0 else f"{since} gün önce görüşüldü"), ""
    else:
        text, cls = "hiç kaydedilmedi", ""
    return {"due": due, "left": left, "since": since, "text": text, "cls": cls}


def sort_key(item):
    """En çok gecikmiş en üstte; sıklığı olmayanlar sonda, ada göre."""
    row, st = item
    return (st["left"] is None, st["left"] or 0, fold(row["name"]))


def with_status(rows, t=None):
    t = t or today()
    return sorted(((r, status(r, t)) for r in rows), key=sort_key)


def overdue(user_id, t=None):
    """Vadesi gelmiş, ertelenmemiş kişiler [(satır, durum)] en çok gecikmiş önce (günlük özet)."""
    t = t or today()
    rows = query("SELECT * FROM contacts WHERE user_id = ? AND contact_every IS NOT NULL"
                 " AND (snooze_until IS NULL OR snooze_until <= ?)", (user_id, t.isoformat()))
    return [(r, st) for r, st in with_status(rows, t) if st["left"] <= 0]


def since_text(st):
    """Kısa gecikme metni: '23 gün' / 'kayıt yok'."""
    return f"{st['since']} gün" if st["since"] is not None else "kayıt yok"


# ---------- Kayıtlar ----------
def refresh_last(contact_id):
    """Son görüşme = kayıtların en yenisi."""
    execute("UPDATE contacts SET last_contact_at = (SELECT MAX(date) FROM contact_logs WHERE contact_id = ?)"
            " WHERE id = ?", (contact_id, contact_id))


def add_log(contact, kind, day, note=""):
    execute("INSERT INTO contact_logs (contact_id, user_id, date, kind, note) VALUES (?, ?, ?, ?, ?)",
            (contact["id"], contact["user_id"], day, kind, note))
    refresh_last(contact["id"])


def snooze(contact_id, until):
    """'Yarın hatırlat': o güne kadar dürtme yok; nudged_for sıfırlanır ki o gün yeniden gelsin."""
    execute("UPDATE contacts SET snooze_until = ?, nudged_for = NULL WHERE id = ?", (until, contact_id))


def _form_values():
    """(değerler, hata). Doğum günü <input type=date>; 'yılı bilmiyorum' işaretliyse '--MM-DD'."""
    every = form_int("contact_every")
    v = {
        "name": form_str("name", 100),
        "relation": form_str("relation", 40),
        "phone": form_str("phone", 30),
        "email": form_str("email", 120),
        "address": form_str("address", 300),
        "note": form_str("note", NOTE_MAX),
        "contact_every": every if every in dict(EVERY_OPTIONS) else None,
        "birthday": None,
    }
    if not v["name"]:
        return v, "Ad boş olamaz."
    if v["email"] and not EMAIL_RE.match(v["email"]):
        return v, "E-posta adresi geçersiz."
    raw = (request.form.get("birthday") or "").strip()
    if raw:
        d = parse_date(raw)
        if d is None:
            return v, "Doğum günü geçersiz."
        if request.form.get("no_year"):
            v["birthday"] = f"--{d.month:02d}-{d.day:02d}"
        elif d > today():
            return v, "Doğum tarihi ileride olamaz."
        else:
            v["birthday"] = d.isoformat()
    return v, None


def _shown_birthday(contact):
    """Düzenleme formundaki tarih (yıl yoksa 2000: 29 Şubat da gösterilebilir)."""
    b = parse_birthday(contact["birthday"])
    return date(b[0] or 2000, b[1], b[2]).isoformat() if b else ""


@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    t = today()
    items = with_status(query("SELECT * FROM contacts WHERE user_id = ?", (uid,)), t)
    # Sekmeler: kayıtlı ilişkiler (önce öneri sırası, sonra diğerleri); "aile" ile "Aile" aynı sekme
    groups = {}
    for r, _st in items:
        if r["relation"]:
            groups.setdefault(fold(r["relation"]), [r["relation"], 0])[1] += 1
    order = [fold(x) for x in RELATIONS]
    tabs = sorted(groups.items(), key=lambda kv: (order.index(kv[0]) if kv[0] in order else len(order), kv[0]))
    selected = fold(request.args.get("iliski", ""))
    if selected not in groups:
        selected = ""
    return render_template(
        "contacts/index.html", contacts=[(r, st) for r, st in items if not selected or fold(r["relation"]) == selected],
        total=len(items), tabs=tabs, selected=selected, late=sum(1 for _r, st in items if st["cls"] == "overdue"),
        relations=relation_suggestions(uid), every_options=EVERY_OPTIONS, quick=QUICK, relation_icon=relation_icon,
        every_label=every_label, whatsapp=whatsapp_number, tel=tel_number, birthday_text=birthday_text,
        today=t.isoformat(),
    )


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    v, error = _form_values()
    last = parse_date(request.form.get("last_contact_at"))
    if not error and last and last > today():
        error = "Son görüşme ileri bir tarih olamaz."
    if error:
        flash(error, "error")
        return redirect(url_for(".index"))
    contact_id = execute(
        "INSERT INTO contacts (user_id, name, relation, phone, email, birthday, address, note, contact_every)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (g.user["id"], v["name"], v["relation"], v["phone"], v["email"], v["birthday"], v["address"], v["note"],
         v["contact_every"]),
    ).lastrowid
    # "Son görüşme" biliniyorsa ilk kayıt olarak eklenir (vade ondan hesaplanır)
    if last:
        add_log(owned_or_404("contacts", contact_id, g.user["id"]), "other", last.isoformat())
    flash(f"{relation_icon(v['relation'])} {v['name']} eklendi.", "success")
    return redirect(url_for(".index"))


@bp.route("/<int:contact_id>", methods=["GET", "POST"])
@login_required
def detail(contact_id):
    uid = g.user["id"]
    contact = owned_or_404("contacts", contact_id, uid)
    if request.method == "POST":
        v, error = _form_values()
        if error:
            flash(error, "error")
            return redirect(url_for(".detail", contact_id=contact_id))
        execute(
            "UPDATE contacts SET name = ?, relation = ?, phone = ?, email = ?, birthday = ?, address = ?, note = ?,"
            " contact_every = ? WHERE id = ? AND user_id = ?",
            (v["name"], v["relation"], v["phone"], v["email"], v["birthday"], v["address"], v["note"],
             v["contact_every"], contact_id, uid),
        )
        flash("Kişi güncellendi.", "success")
        return redirect(url_for(".detail", contact_id=contact_id))
    t = today()
    logs = query("SELECT * FROM contact_logs WHERE contact_id = ? ORDER BY date DESC, id DESC", (contact_id,))
    nxt = next_birthday(contact, t)
    return render_template(
        "contacts/detail.html", c=contact, st=status(contact, t), logs=logs, kinds=KINDS, quick=QUICK,
        relations=relation_suggestions(uid), every_options=EVERY_OPTIONS, relation_icon=relation_icon, every_label=every_label,
        whatsapp=whatsapp_number(contact["phone"]), tel=tel_number(contact["phone"]),
        birthday=birthday_text(contact), next_bday=nxt, bday_days=(nxt - t).days if nxt else None,
        bday_age=age_on(contact, nxt) if nxt else None, shown_birthday=_shown_birthday(contact),
        no_year=bool(contact["birthday"] and contact["birthday"].startswith("--")),
        snoozed=contact["snooze_until"] if contact["snooze_until"] and contact["snooze_until"] > t.isoformat() else None,
        today=t.isoformat(),
    )


@bp.route("/<int:contact_id>/sil", methods=["POST"])
@login_required
def delete(contact_id):
    contact = owned_or_404("contacts", contact_id, g.user["id"])
    trash.move(g.user["id"], "contacts", f"📇 {contact['name']}", ("contacts", contact_id),
               children=[("contact_logs", "contact_id = ?")])
    flash(trash.notice(contact["name"]), "success")
    return redirect(url_for(".index"))


@bp.route("/<int:contact_id>/kayit", methods=["POST"])
@login_required
def add_entry(contact_id):
    """Hızlı butonlar (bugün) ve geçmişe dönük kayıt (tarih + not)."""
    contact = owned_or_404("contacts", contact_id, g.user["id"])
    kind = form_choice("kind", KINDS, "call")
    raw = (request.form.get("date") or "").strip()
    d = parse_date(raw) if raw else today()
    if d is None:
        flash("Geçersiz tarih.", "warning")
        return redirect_back("contacts.detail", contact_id=contact_id)
    if d > today():
        flash("İleri tarihli kayıt eklenemez.", "warning")
        return redirect_back("contacts.detail", contact_id=contact_id)
    add_log(contact, kind, d.isoformat(), form_str("note", 300))
    st = status(owned_or_404("contacts", contact_id, g.user["id"]))
    after = f" Sonraki: {st['left']} gün sonra." if st["left"] and st["left"] > 0 else ""
    flash(f"{KINDS[kind][0]} {contact['name']} için kayıt eklendi.{after}", "success")
    return redirect_back("contacts.detail", contact_id=contact_id)


@bp.route("/<int:contact_id>/kayit/<int:log_id>/sil", methods=["POST"])
@login_required
def delete_entry(contact_id, log_id):
    owned_or_404("contacts", contact_id, g.user["id"])
    if query_one("SELECT 1 FROM contact_logs WHERE id = ? AND contact_id = ?", (log_id, contact_id)) is None:
        abort(404)
    execute("DELETE FROM contact_logs WHERE id = ? AND contact_id = ?", (log_id, contact_id))
    refresh_last(contact_id)
    flash("Kayıt silindi.", "success")
    return redirect(url_for(".detail", contact_id=contact_id))


# ---------- Telegram dürtmesi (cron /hatirlatma) ----------
def pending(now):
    """[(kişi, durum)] — vadesi gelmiş, ertelenmemiş ve bu vade için henüz dürtülmemiş kişiler."""
    t = now.date()
    out = []
    for r in query("SELECT c.*, u.telegram_chat_id AS chat_id FROM contacts c JOIN users u ON u.id = c.user_id"
                   " WHERE u.telegram_chat_id IS NOT NULL AND c.contact_every IS NOT NULL"
                   " AND (c.snooze_until IS NULL OR c.snooze_until <= ?)", (t.isoformat(),)):
        st = status(r, t)
        if st["left"] <= 0 and r["nudged_for"] != st["due"].isoformat():
            out.append((r, st))
    out.sort(key=sort_key)
    return out


def mark_nudged(contact_id, due):
    execute("UPDATE contacts SET nudged_for = ? WHERE id = ?", (due.isoformat(), contact_id))


def nudge_buttons(contact_id):
    return [[("✅ Aradım", f"ct:{contact_id}"), ("⏰ Yarın hatırlat", f"ctz:{contact_id}")]]


def nudge_message(row, st, url, escape):
    name = f"<b>{escape(row['name'])}</b>"
    head = f"📞 {name} ile {st['since']} gündür görüşmedin" if st["since"] is not None \
        else f"📞 {name} ile henüz görüşme kaydın yok"
    detail = [f"{every_label(row['contact_every'])} görüşmek istiyordun"]
    if row["phone"]:
        detail.append(f"☎️ {escape(row['phone'])}")
    lines = [head, " · ".join(detail)]
    if row["note"]:
        lines.append(f"<i>{escape(row['note'][:300])}</i>")
    lines.append(f'<a href="{escape(url)}">Kişi sayfası →</a>')
    return "\n".join(lines)
