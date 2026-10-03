"""🔁 Ödünç: "matkabı kime verdim?" — ödünç verilen ve alınan eşyalar.

- Yön: 'lent' (ben verdim, eşya bende değil) / 'borrowed' (ben aldım, başkasının eşyası bende).
  Eşya serbest yazılır ya da Ev Envanteri'nden seçilir (bağlanır: envanterde "📤 Ahmet'te (12 gündür)" rozeti ve
  eşyanın ödünç geçmişi görünür); kişi serbest yazılır ya da Kişiler'den seçilir (telefonu da gelir).
- inventory_id / contact_id için FK yok: envanterden ya da Kişiler'den silinen kayıt ödünçte adıyla kalır, çöpten geri
  getirilince bağlantı kendiliğinden döner (AUTOINCREMENT id'ler tekrar verilmez; birleştirmeler user_id'yi de denetler).
- "✅ Geri geldi / Geri verdim" kaydı "Geri gelenler"e taşır; yanlışlıkla basıldıysa geri alınır.
- Telefon varsa WhatsApp'tan hazır, kibar bir mesajla hatırlatma bağlantısı (metin gönderilmeden önce düzenlenebilir).
- Telegram (cron /hatirlatma, 09:00 sonrası, günde en fazla bir kez): beklenen dönüş günü gelince ve sonra haftada bir;
  dönüş tarihi yoksa seçilen aralıkla (7/14/30 gün). "⏰ 1 hafta sonra" sıradaki mesajı 7 gün sonraya alır.
"""
import re
from datetime import timedelta
from urllib.parse import quote

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import trash
from ..auth import login_required
from ..db import execute, owned_or_404, query, query_one
from ..storage import attachments_for, first_thumbs, save_attachment
from ..utils import (MONTHS_TR, fmt_date, fold, form_choice, form_date, form_int, form_str, parse_date, redirect_back,
                     safe_path, today, today_str)
from .profile import PHONE_RE, phone_intl

bp = Blueprint("loans", __name__, url_prefix="/odunc")

TABS = {"verdiklerim": ("📤", "Verdiklerim"), "aldiklarim": ("📥", "Aldıklarım"), "gelenler": ("✅", "Geri gelenler")}
TAB_DIRECTION = {"verdiklerim": "lent", "aldiklarim": "borrowed"}
DIRECTIONS = {"lent": "📤 Ben verdim", "borrowed": "📥 Ben aldım"}
REMIND_OPTIONS = [(7, "Haftada bir"), (14, "2 haftada bir"), (30, "Ayda bir"), (0, "Hatırlatma yok")]
DEFAULT_REMIND = 14
LATE_EVERY = 7       # dönüş günü geçtiyse kaç günde bir yeniden hatırlatılır
SNOOZE_DAYS = 7
NOTE_MAX = 1000
MAX_ID = 2 ** 62     # formdan gelen id'ler (SQLite tam sayısını taşırmasın)
# Envanter ve Kişiler bağlantısı hâlâ yaşıyor mu (silinmişse NULL; ad kayıtta durur)
LOAN_SELECT = ("SELECT l.*, i.id AS inv_live, c.id AS contact_live FROM loans l"
               " LEFT JOIN inventory i ON i.id = l.inventory_id AND i.user_id = l.user_id"
               " LEFT JOIN contacts c ON c.id = l.contact_id AND c.user_id = l.user_id")

_BACK_VOWELS, _FRONT_VOWELS = "aıouâû", "eiöüî"
_HARD = "fstkçşhp"   # sert ünsüzler: ek -te/-ta (Ahmet'te), yumuşaklar -de/-da (Can'da)


# ---------- Türkçe metin ----------
def suffix(name, case):
    """Ada Türkçe hâl eki: 'loc' Ahmet'te / Ayşe'de / Burak'ta, 'abl' Ahmet'ten, 'dat' Ahmet'e / Ayşe'ye / Burak'a."""
    word = (name or "").strip()
    letters = [c for c in word.replace("I", "ı").replace("İ", "i").lower() if c.isalpha()]
    vowel = next((c for c in reversed(letters) if c in _BACK_VOWELS + _FRONT_VOWELS), "e")
    a = "a" if vowel in _BACK_VOWELS else "e"
    last = letters[-1] if letters else ""
    if case == "dat":
        return f"{word}'{'y' if last in _BACK_VOWELS + _FRONT_VOWELS else ''}{a}"
    return f"{word}'{'t' if last in _HARD else 'd'}{a}" + ("n" if case == "abl" else "")


def since_words(days):
    """Ne zamandır: 0 -> 'bugünden beri', 1 -> 'dünden beri', 12 -> '12 gündür', 21 -> '3 haftadır', 75 -> '2 aydır'."""
    if days <= 0:
        return "bugünden beri"
    if days == 1:
        return "dünden beri"
    if days < 14:
        return f"{days} gündür"
    if days < 60:
        return f"{days // 7} haftadır"
    if days < 365:
        return f"{days // 30} aydır"
    return f"{days // 365} yıldır"


def duration_words(days):
    """Ne kadar kaldı: 0 -> 'aynı gün', 12 -> '12 gün', 21 -> '3 hafta', 75 -> '2 ay'."""
    if days <= 0:
        return "aynı gün"
    if days < 14:
        return f"{days} gün"
    if days < 60:
        return f"{days // 7} hafta"
    if days < 365:
        return f"{days // 30} ay"
    return f"{days // 365} yıl"


def _rel(d, t):
    """t gününe göre: 'bugün', 'yarın', 'dün', '5 gün sonra', '7 gün geçti'."""
    n = (d - t).days
    return {0: "bugün", 1: "yarın", -1: "dün"}.get(n, f"{n} gün sonra" if n > 0 else f"{-n} gün geçti")


def _lower_first(text):
    """Cümle içinde: 'Matkap' -> 'matkap', 'İp' -> 'ip'; 'TV', 'PS5' gibi kısaltmalar olduğu gibi kalır."""
    if len(text) > 1 and text[1].isupper():
        return text
    return {"I": "ı", "İ": "i"}.get(text[:1], text[:1].lower()) + text[1:]


# ---------- Durum ----------
def is_lent(row):
    return row["direction"] == "lent"


def days_out(row, t=None):
    """Eşya kaç gündür dışarıda; geri geldiyse kaç gün dışarıda kaldı."""
    t = t or today()
    end = parse_date(row["returned_on"]) or t
    return max(0, (end - (parse_date(row["given_on"]) or end)).days)


def held_text(row, t=None):
    """Satırdaki durum: '3 haftadır Ahmet'te' / 'Ahmet'ten · 3 haftadır sende' /
    geri geldiyse 'Ahmet'e verildi · 3 Eyl → 15 Eyl (12 gün)'."""
    n = days_out(row, t)
    person = row["person_name"]
    if row["returned_on"]:
        who = f"{suffix(person, 'dat')} verildi" if is_lent(row) else f"{suffix(person, 'abl')} alındı"
        return f"{who} · {fmt_date(row['given_on'])} → {fmt_date(row['returned_on'])} ({duration_words(n)})"
    if is_lent(row):
        return f"{since_words(n)} {suffix(person, 'loc')}"
    return f"{suffix(person, 'abl')} · {since_words(n)} sende"


def badge_text(row, t=None):
    """Envanterdeki rozet: '📤 Ahmet'te (12 gündür)' / '📥 Ahmet'ten (3 gündür)'."""
    since = since_words(days_out(row, t))
    if is_lent(row):
        return f"📤 {suffix(row['person_name'], 'loc')} ({since})"
    return f"📥 {suffix(row['person_name'], 'abl')} ({since})"


def is_overdue(row, t=None):
    return bool(row["due_on"] and not row["returned_on"] and row["due_on"] < (t or today()).isoformat())


def title_for(row):
    """Yaklaşanlar / takvim başlığı: 'Matkap Ahmet'ten geri alınacak' / 'Kitap Ayşe'ye geri verilecek'."""
    if is_lent(row):
        return f"{row['item_name']} {suffix(row['person_name'], 'abl')} geri alınacak"
    return f"{row['item_name']} {suffix(row['person_name'], 'dat')} geri verilecek"


def tab_for(row):
    if row["returned_on"]:
        return "gelenler"
    return "verdiklerim" if is_lent(row) else "aldiklarim"


def remind_label(days):
    return dict(REMIND_OPTIONS).get(days, f"{days} günde bir")


def next_reminder(row):
    """Sıradaki Telegram hatırlatmasının günü (yoksa ya da eşya döndüyse None). Ertelendiyse ertelenen gün;
    dönüş tarihi varsa o gün, sonra son mesajdan haftada bir; yoksa son mesajdan (hiç gitmediyse verildiği günden)
    remind_every_days gün sonra."""
    if row["returned_on"]:
        return None
    if row["snooze_until"]:
        return parse_date(row["snooze_until"])
    last = parse_date(row["reminded_on"])
    due = parse_date(row["due_on"])
    if due:
        return due if last is None or last < due else last + timedelta(days=LATE_EVERY)
    if row["remind_every_days"]:
        return (last or parse_date(row["given_on"]) or today()) + timedelta(days=row["remind_every_days"])
    return None


# ---------- WhatsApp ----------
def _when(d, t):
    """Mesajdaki tarih: 'bugün', 'dün', '12 Eylül'de' (bu yıl), '12.09.2025 tarihinde'."""
    if d == t:
        return "bugün"
    if d == t - timedelta(days=1):
        return "dün"
    if d.year == t.year:
        return suffix(f"{d.day} {MONTHS_TR[d.month - 1]}", "loc")
    return f"{d.day:02d}.{d.month:02d}.{d.year} tarihinde"


def whatsapp_text(row, t=None):
    """Hazır kibar mesaj: 'Merhaba Ahmet, 12 Eylül'de verdiğim matkap sende mi? Müsait olduğunda alabilir miyim? 🙂'."""
    t = t or today()
    when = _when(parse_date(row["given_on"]) or t, t)
    item = _lower_first(row["item_name"])
    if is_lent(row):
        return f"Merhaba {row['person_name']}, {when} verdiğim {item} sende mi? Müsait olduğunda alabilir miyim? 🙂"
    return (f"Merhaba {row['person_name']}, {when} senden aldığım {item} hâlâ bende; geri getirmek istiyorum, "
            f"ne zaman uygun? 🙂")


def whatsapp_link(row, t=None):
    """Telefon varsa (ve eşya hâlâ dışarıdaysa) wa.me bağlantısı; metin UTF-8 yüzde kodlamasıyla. Yoksa None."""
    number = phone_intl(row["phone"])
    if not number or row["returned_on"]:
        return None
    return f"https://wa.me/{number}?text={quote(whatsapp_text(row, t), safe='')}"


def valid_phone(phone):
    return bool(PHONE_RE.match(phone)) and 7 <= len(re.sub(r"\D", "", phone)) <= 15


# ---------- Kayıt ----------
def _owned(table, row_id, user_id):
    """Formdan / adresten gelen envanter ya da kişi id'si kullanıcınınsa satırı, değilse None."""
    if not row_id or not 0 < row_id < MAX_ID:
        return None
    return query_one(f"SELECT * FROM {table} WHERE id = ? AND user_id = ?", (row_id, user_id))


def get_loan(loan_id, user_id):
    return query_one(LOAN_SELECT + " WHERE l.id = ? AND l.user_id = ?", (loan_id, user_id))


def _form_values(user_id):
    """(değerler, hata). Envanterden / Kişiler'den seçilen başkasınınsa yok sayılır; ad (ve telefon) boşsa seçilen
    kaydınki kullanılır. Kişiler'den gelen telefon olduğu gibi kabul edilir (orada biçim denetimi yok)."""
    inv = _owned("inventory", form_int("inventory_id"), user_id)
    contact = _owned("contacts", form_int("contact_id"), user_id)
    every = form_int("remind_every_days")
    contact_phone = contact["phone"][:30] if contact else ""
    v = {
        "direction": form_choice("direction", DIRECTIONS, "lent"),
        "item_name": form_str("item_name", 150) or (inv["name"][:150] if inv else ""),
        "inventory_id": inv["id"] if inv else None,
        "person_name": form_str("person_name", 100) or (contact["name"][:100] if contact else ""),
        "contact_id": contact["id"] if contact else None,
        "phone": form_str("phone", 30) or contact_phone,
        "given_on": form_date("given_on") or today_str(),
        "due_on": form_date("due_on"),
        "remind_every_days": every if every in dict(REMIND_OPTIONS) else DEFAULT_REMIND,
        "note": form_str("note", NOTE_MAX),
    }
    if not v["item_name"]:
        return v, "Eşya adı gerekli (ya da Ev Envanteri'nden seç)."
    if not v["person_name"]:
        return v, "Kişi adı gerekli (ya da Kişiler'den seç)."
    if v["phone"] and v["phone"] != contact_phone and not valid_phone(v["phone"]):
        return v, "Telefon numarası geçersiz (ör. 0532 123 45 67 ya da +90 532 123 45 67)."
    if v["given_on"] > today_str():
        return v, "Tarih ileri bir gün olamaz."
    if v["due_on"] and v["due_on"] < v["given_on"]:
        return v, "Beklenen dönüş, verildiği / alındığı günden önce olamaz."
    return v, None


def insert(user_id, v):
    return execute(
        "INSERT INTO loans (user_id, direction, item_name, inventory_id, person_name, contact_id, phone, given_on,"
        " due_on, remind_every_days, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, v["direction"], v["item_name"], v["inventory_id"], v["person_name"], v["contact_id"], v["phone"],
         v["given_on"], v["due_on"], v["remind_every_days"], v["note"]),
    ).lastrowid


def set_returned(loan_id, day):
    execute("UPDATE loans SET returned_on = ?, snooze_until = NULL WHERE id = ?", (day, loan_id))


def returned_text(row):
    """'Matkap Ahmet'ten geri geldi.' / 'Kitap Ayşe'ye geri verildi.'"""
    if is_lent(row):
        return f"{row['item_name']} {suffix(row['person_name'], 'abl')} geri geldi."
    return f"{row['item_name']} {suffix(row['person_name'], 'dat')} geri verildi."


def _choices(user_id):
    """Formdaki seçim listeleri: Ev Envanteri eşyaları, Kişiler ve daha önce yazılan kişi adları."""
    items = query("SELECT id, name, location FROM inventory WHERE user_id = ?", (user_id,))
    people = query("SELECT id, name, phone FROM contacts WHERE user_id = ?", (user_id,))
    persons = [r["person_name"] for r in query(
        "SELECT person_name FROM loans WHERE user_id = ? GROUP BY person_name ORDER BY MAX(id) DESC LIMIT 50",
        (user_id,))]
    return {"inv_choices": sorted(items, key=lambda r: (fold(r["name"]), r["id"])),
            "contact_choices": sorted(people, key=lambda r: (fold(r["name"]), r["id"])), "persons": persons}


def blank(direction="lent", inv=None, contact=None):
    """Boş form (form alanları kayıt satırıyla aynı anahtarlar); envanter eşyası / kişi verilirse seçili."""
    return {"direction": direction, "item_name": inv["name"] if inv else "", "inventory_id": inv["id"] if inv else None,
            "person_name": contact["name"] if contact else "", "contact_id": contact["id"] if contact else None,
            "phone": contact["phone"] if contact else "", "given_on": today_str(), "due_on": None,
            "remind_every_days": DEFAULT_REMIND, "note": ""}


def _ctx(**kw):
    t = today()
    return dict(tabs=TABS, directions=DIRECTIONS, remind_options=REMIND_OPTIONS,
                held=lambda r: held_text(r, t), whatsapp=lambda r: whatsapp_link(r, t), is_lent=is_lent, **kw)


# ---------- Rotalar ----------
@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    tab = request.args.get("sekme")
    tab = tab if tab in TABS else "verdiklerim"
    open_rows = query(LOAN_SELECT + " WHERE l.user_id = ? AND l.returned_on IS NULL"
                      " ORDER BY l.due_on IS NULL, l.due_on, l.given_on, l.id", (uid,))
    lent = [r for r in open_rows if is_lent(r)]
    borrowed = [r for r in open_rows if not is_lent(r)]
    returned = query_one("SELECT COUNT(*) AS n FROM loans WHERE user_id = ? AND returned_on IS NOT NULL", (uid,))["n"]
    if tab == "gelenler":
        rows = query(LOAN_SELECT + " WHERE l.user_id = ? AND l.returned_on IS NOT NULL"
                     " ORDER BY l.returned_on DESC, l.id DESC LIMIT 200", (uid,))
    else:
        rows = lent if tab == "verdiklerim" else borrowed
    summary = []
    if lent:
        summary.append(f"{len(lent)} eşyan başkasında")
    if borrowed:
        summary.append(f"{len(borrowed)} emanet sende")
    late = sum(1 for r in open_rows if is_overdue(r))
    if late:
        summary.append(f"{late} gecikmiş")
    return render_template(
        "loans/index.html", **_ctx(
            tab=tab, rows=rows, counts={"verdiklerim": len(lent), "aldiklarim": len(borrowed), "gelenler": returned},
            total=len(open_rows) + returned, summary=" · ".join(summary),
            thumbs=first_thumbs("loan", [r["id"] for r in rows]), blank=blank(TAB_DIRECTION.get(tab, "lent")),
            **_choices(uid)))


@bp.route("/yeni", methods=["GET", "POST"])
@login_required
def new():
    """Form sayfası (?envanter=<id> / ?kisi=<id> ile o eşya ya da kişi seçili açılır) ve kayıt.
    Hata olursa form yazılanlarla yeniden gösterilir; başarıda `next` (envanter / kişi sayfası) ya da ilgili sekme."""
    uid = g.user["id"]
    if request.method == "POST":
        v, error = _form_values(uid)
        if error:
            flash(error, "error")
            return render_template("loans/new.html", **_ctx(l=v, back=safe_path(request.form.get("next")) or "",
                                                            **_choices(uid))), 400
        loan_id = insert(uid, v)
        photo = request.files.get("photo")
        if photo and photo.filename:
            try:
                save_attachment(photo, uid, "loan", loan_id)
            except ValueError as e:  # bozuk dosya ya da kota (QuotaError da ValueError)
                flash(f"Fotoğraf eklenemedi: {e}", "warning")
        what = f"{suffix(v['person_name'], 'dat')} verildi" if is_lent(v) else f"{suffix(v['person_name'], 'abl')} alındı"
        flash(f"🔁 {v['item_name']} {what}.", "success")
        return redirect_back(".index", sekme=tab_for({"returned_on": None, "direction": v["direction"]}))
    inv = _owned("inventory", request.args.get("envanter", type=int), uid)
    contact = _owned("contacts", request.args.get("kisi", type=int), uid)
    direction = "borrowed" if request.args.get("yon") == "aldim" else "lent"
    back = (url_for("inventory.edit", item_id=inv["id"]) if inv
            else url_for("contacts.detail", contact_id=contact["id"]) if contact else "")
    return render_template("loans/new.html", **_ctx(l=blank(direction, inv, contact), back=back, **_choices(uid)))


@bp.route("/<int:loan_id>", methods=["GET", "POST"])
@login_required
def edit(loan_id):
    uid = g.user["id"]
    loan = get_loan(loan_id, uid)
    if loan is None:
        abort(404)
    if request.method == "POST":
        v, error = _form_values(uid)
        if not error and loan["returned_on"] and v["given_on"] > loan["returned_on"]:
            error = "Tarih, geri geldiği günden sonra olamaz."
        if error:
            flash(error, "error")
            return redirect(url_for(".edit", loan_id=loan_id))
        # Dönüş tarihi ya da aralık değişince erteleme düşer: sıradaki hatırlatma yeni ayara göre hesaplanır
        reset = (v["due_on"], v["remind_every_days"]) != (loan["due_on"], loan["remind_every_days"])
        execute(
            "UPDATE loans SET direction = ?, item_name = ?, inventory_id = ?, person_name = ?, contact_id = ?, phone = ?,"
            " given_on = ?, due_on = ?, remind_every_days = ?, note = ?,"
            " snooze_until = CASE WHEN ? THEN NULL ELSE snooze_until END WHERE id = ? AND user_id = ?",
            (v["direction"], v["item_name"], v["inventory_id"], v["person_name"], v["contact_id"], v["phone"],
             v["given_on"], v["due_on"], v["remind_every_days"], v["note"], int(reset), loan_id, uid),
        )
        flash("Kayıt güncellendi.", "success")
        return redirect(url_for(".edit", loan_id=loan_id))
    nxt = next_reminder(loan) if g.user["telegram_chat_id"] else None
    return render_template("loans/edit.html", **_ctx(
        l=loan, tab=tab_for(loan), files=attachments_for("loan", loan_id), next_reminder=nxt, **_choices(uid)))


@bp.route("/<int:loan_id>/geri", methods=["POST"])
@login_required
def mark_returned(loan_id):
    uid = g.user["id"]
    loan = owned_or_404("loans", loan_id, uid)
    day = form_date("returned_on") or today_str()
    if loan["returned_on"]:
        flash("Zaten geri gelmiş olarak kayıtlı.", "warning")
    elif day > today_str():
        flash("Geri geliş tarihi ileri bir gün olamaz.", "error")
    elif day < loan["given_on"]:
        flash("Geri geliş, verildiği / alındığı günden önce olamaz.", "error")
    else:
        set_returned(loan_id, day)
        flash(f"✅ {returned_text(loan)}", "success")
    return redirect_back(".index", sekme=tab_for(loan))


@bp.route("/<int:loan_id>/ac", methods=["POST"])
@login_required
def reopen(loan_id):
    """Yanlışlıkla "geri geldi" denmişse: kayıt yeniden açık (hâlâ dışarıda) olur."""
    uid = g.user["id"]
    loan = owned_or_404("loans", loan_id, uid)
    execute("UPDATE loans SET returned_on = NULL WHERE id = ? AND user_id = ?", (loan_id, uid))
    flash(f"↩️ {loan['item_name']} yeniden {'Verdiklerim' if is_lent(loan) else 'Aldıklarım'} listesinde.", "success")
    return redirect_back(".index", sekme="gelenler")


@bp.route("/<int:loan_id>/sil", methods=["POST"])
@login_required
def delete(loan_id):
    uid = g.user["id"]
    loan = owned_or_404("loans", loan_id, uid)
    trash.move(uid, "loans", f"🔁 {loan['item_name']} ({loan['person_name']})", ("loans", loan_id), entity="loan")
    flash(trash.notice(loan["item_name"]), "success")
    return redirect(url_for(".index", sekme=tab_for(loan)))


# ---------- Ev Envanteri ve Kişiler ----------
def inventory_badges(user_id, ids, t=None):
    """{envanter_id: [{'id', 'text', 'cls'}]} — dışarıdaki (açık) ödünç kayıtları, envanter listesindeki rozetler."""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    out = {}
    for r in query(f"SELECT * FROM loans WHERE user_id = ? AND returned_on IS NULL AND inventory_id IN ({marks})"
                   " ORDER BY given_on, id", (user_id, *ids)):
        out.setdefault(r["inventory_id"], []).append(
            {"id": r["id"], "text": badge_text(r, t), "cls": "overdue" if is_overdue(r, t) else "soon"})
    return out


def inventory_history(user_id, item_id):
    """Eşyanın ödünç geçmişi: önce dışarıdakiler, sonra en son geri gelen."""
    return query("SELECT * FROM loans WHERE user_id = ? AND inventory_id = ?"
                 " ORDER BY returned_on IS NOT NULL, returned_on DESC, given_on DESC, id DESC", (user_id, item_id))


def for_contact(user_id, contact_id):
    """Kişideki (verdiklerim) ve kişiden aldığım, hâlâ dışarıda olan eşyalar."""
    return query("SELECT * FROM loans WHERE user_id = ? AND contact_id = ? AND returned_on IS NULL"
                 " ORDER BY direction DESC, given_on, id", (user_id, contact_id))


# ---------- Telegram hatırlatması (cron /hatirlatma) ----------
def pending(now):
    """Hatırlatma vakti gelmiş, hâlâ dışarıda olan kayıtlar (sahibinin Telegram'ı bağlıysa); aynı gün ikinci kez yok."""
    t = now.date()
    out = []
    for r in query("SELECT l.*, u.telegram_chat_id AS chat_id FROM loans l JOIN users u ON u.id = l.user_id"
                   " WHERE u.telegram_chat_id IS NOT NULL AND l.returned_on IS NULL"
                   " ORDER BY l.due_on IS NULL, l.due_on, l.given_on, l.id"):
        nxt = next_reminder(r)
        if nxt and nxt <= t and r["reminded_on"] != t.isoformat():
            out.append(r)
    return out


def mark_sent(loan_id, day):
    execute("UPDATE loans SET reminded_on = ?, snooze_until = NULL WHERE id = ?", (day.isoformat(), loan_id))


def snooze(loan_id, days=SNOOZE_DAYS):
    """'⏰ 1 hafta sonra': sıradaki hatırlatma bugünden `days` gün sonra. O günü döner."""
    until = (today() + timedelta(days=days)).isoformat()
    execute("UPDATE loans SET snooze_until = ? WHERE id = ?", (until, loan_id))
    return until


def buttons(row):
    done = "✅ Geri geldi" if is_lent(row) else "✅ Geri verdim"
    return [[(done, f"ln:ret:{row['id']}"), ("⏰ 1 hafta sonra", f"ln:snz:{row['id']}")]]


def message(row, t, url, escape):
    """'🔁 Matkap 3 haftadır Ahmet'te' / '🔁 Ahmet'ten aldığın Kitap — geri vermeyi unutma' + tarihler, not, WhatsApp."""
    item = f"<b>{escape(row['item_name'])}</b>"
    since = since_words(days_out(row, t))
    if is_lent(row):
        lines = [f"🔁 {item} {since} {escape(suffix(row['person_name'], 'loc'))}"]
        detail = [f"Verildi: {fmt_date(row['given_on'], True)}"]
    else:
        lines = [f"🔁 {escape(suffix(row['person_name'], 'abl'))} aldığın {item} — geri vermeyi unutma"]
        detail = [f"{since} sende"]
    due = parse_date(row["due_on"])
    if due:
        detail.append(f"Dönüş: {fmt_date(due, True)} ({_rel(due, t)})")
    lines.append("📅 " + " · ".join(detail))
    if row["note"]:
        lines.append(f"<i>{escape(row['note'][:300])}</i>")
    wa = whatsapp_link(row, t)
    if wa:
        lines.append(f'<a href="{escape(wa)}">💬 WhatsApp\'tan nazikçe hatırlat</a>')
    lines.append(f'<a href="{escape(url)}">Ödünç →</a>')
    return "\n".join(lines)


# ---------- Bot: /odunc ----------
def match_contact(user_id, word):
    """Kişiler'de aynı ad; yoksa ilk adı tutan tek kişi ('Ahmet' -> 'Ahmet Yılmaz'). Türkçe harf duyarsız."""
    key = fold(word)
    rows = query("SELECT * FROM contacts WHERE user_id = ? ORDER BY id", (user_id,))
    exact = [r for r in rows if fold(r["name"].strip()) == key]
    if exact:
        return exact[0]
    first = [r for r in rows if fold((r["name"].split() or [""])[0]) == key]
    return first[0] if len(first) == 1 else None


def match_inventory(user_id, name):
    key = fold(name.strip())
    return next((r for r in query("SELECT * FROM inventory WHERE user_id = ? ORDER BY id", (user_id,))
                 if fold(r["name"].strip()) == key), None)


def quick_add(user_id, text):
    """'/odunc matkap Ahmet' -> Ahmet'e matkap verildi (bugün). Son kelime kişi ('Ahmet'e' yazılırsa ek atılır), kalanı
    eşya; Kişiler'de ve envanterde eşleşen varsa bağlanır. Kaydın id'si; anlaşılamazsa None."""
    words = text.split()
    person = re.split(r"['’]", words[-1], maxsplit=1)[0][:100] if len(words) > 1 else ""
    if not person:
        return None
    item = " ".join(words[:-1])[:150]
    contact = match_contact(user_id, person)
    inv = match_inventory(user_id, item)
    return insert(user_id, {
        "direction": "lent", "item_name": inv["name"][:150] if inv else item, "inventory_id": inv["id"] if inv else None,
        "person_name": contact["name"][:100] if contact else person, "contact_id": contact["id"] if contact else None,
        "phone": contact["phone"][:30] if contact else "", "given_on": today_str(), "due_on": None,
        "remind_every_days": DEFAULT_REMIND, "note": ""})


def added_message(row, url, escape):
    lines = [f"🔁 <b>{escape(row['item_name'])}</b> {escape(suffix(row['person_name'], 'dat'))} verildi."]
    links = []
    if row["inv_live"]:
        links.append("📦 Ev Envanteri'ndeki eşyaya bağlandı")
    if row["contact_live"]:
        links.append(f"📇 Kişiler: {escape(row['person_name'])}")
    if links:
        lines.append(" · ".join(links))
    lines.append(f"⏰ Dönmezse {remind_label(row['remind_every_days']).lower()} hatırlatırım.")
    lines.append(f'<a href="{escape(url)}">Düzenle (dönüş tarihi, telefon, fotoğraf) →</a>')
    return "\n".join(lines)


def list_message(user_id, t, index_url, loan_url, escape, limit=25):
    """/odunc: dışarıdaki eşyalar (verdiklerim) ve bendeki emanetler (aldıklarım)."""
    rows = query("SELECT * FROM loans WHERE user_id = ? AND returned_on IS NULL"
                 " ORDER BY due_on IS NULL, due_on, given_on, id", (user_id,))
    if not rows:
        return ("🔁 Ödünçte eşya yok.\nKaydetmek için: <code>/odunc matkap Ahmet</code> (son kelime kişinin adı)\n"
                f'<a href="{escape(index_url)}">Ödünç →</a>')
    lines = ["🔁 <b>Ödünç</b>"]
    for direction, head in (("lent", "📤 <b>Verdiklerin</b>"), ("borrowed", "📥 <b>Aldıkların</b>")):
        part = [r for r in rows if r["direction"] == direction]
        if not part:
            continue
        lines.append(f"\n{head} ({len(part)})")
        for r in part[:limit]:
            due = parse_date(r["due_on"])
            late = f" · {'⚠️ ' if due < t else ''}dönüş {_rel(due, t)}" if due else ""
            lines.append(f'• <a href="{escape(loan_url(r["id"]))}">{escape(r["item_name"])}</a> — '
                         f"{escape(held_text(r, t))}{late}")
        if len(part) > limit:
            lines.append(f"… +{len(part) - limit}")
    lines.append(f'\n<a href="{escape(index_url)}">Ödünç →</a>')
    return "\n".join(lines)
