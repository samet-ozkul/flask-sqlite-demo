"""🎟️ Bilet Cüzdanı: konser, maç, tiyatro, sinema, uçak, otobüs, tren, müze biletleri tek yerde.

- Bilet dosyaları (PDF ya da görsel, birden çok) ekler sisteminde (entity 'ticket'), kullanıcı kotasıyla
- "Yaklaşan" tarih sırasıyla büyük kartlar; tarihi geçenler kendiliğinden "Geçmiş"e düşer, elle arşivlenebilir de.
  Geçmiş biletler kendiliğinden silinmez. Telegram'dan tarihsiz gelen taslaklar en üstte "tarih gir" rozetiyle durur
- 🎫 Bilet ekranı (/biletler/<id>/goster): kapıda göstermek için sade sayfa; rezervasyon kodu büyük ve kopyalanabilir,
  barkod/QR içeriği girildiyse büyük QR (segno), görseller tam genişlikte, PDF'ler için "Aç"
- Fiyat istenirse Harcamalar'a da işlenir (kategori seçilmezse etkinlik Eğlence, yolculuk Ulaşım; tarih eklenme günü)
- Telegram'dan PDF / fotoğraf: "🎟️ Bilet cüzdanına ekle" (bot_commands); yapay zekâ açıksa bilgiler biletten okunur
- Telegram (cron /hatirlatma, her biri bir kez): bir gün önce 19:00'dan sonra kısa "yarın" mesajı; etkinlik günü saatli
  biletlerde başlangıçtan 3 saat (uçak/otobüs/tren 4 saat) önce, saatsizlerde 09:00'dan sonra özet + bilet dosyaları
  belge olarak. Tarih ya da saat değişince mesajlar yeniden kurulur; başlangıcı geçmiş bilete mesaj gitmez
"""
import io
import os
import re
import urllib.parse
from datetime import datetime, time, timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from werkzeug.datastructures import FileStorage

from .. import ai, assistant, automation, quota, telegram, trash
from .. import todo_reminders as todo
from ..auth import login_required
from ..db import execute, get_db, owned_or_404, query, query_one
from ..storage import attachments_for, save_attachment, upload_dir
from ..totp import qr_svg
from ..utils import TZ, fmt_date, fmt_money, form_bool, form_choice, form_date, form_str, parse_date, today_str
from .expenses import CATEGORIES as EXPENSE_CATEGORIES, category_icon, form_amount, valid_amount

bp = Blueprint("tickets", __name__, url_prefix="/biletler")

# tür -> (ad, ikon, harcama kategorisi)
KINDS = {
    "concert": ("Konser", "🎵", "Eğlence"),
    "theatre": ("Tiyatro", "🎭", "Eğlence"),
    "cinema": ("Sinema", "🎬", "Eğlence"),
    "sport": ("Maç", "⚽", "Eğlence"),
    "flight": ("Uçak", "✈️", "Ulaşım"),
    "bus": ("Otobüs", "🚌", "Ulaşım"),
    "train": ("Tren", "🚆", "Ulaşım"),
    "museum": ("Müze / sergi", "🏛️", "Eğlence"),
    "other": ("Diğer", "🎟️", "Eğlence"),
}
TRAVEL = ("flight", "bus", "train")
LEAD_HOURS = 3            # saatli biletin etkinlik günü mesajı başlangıçtan kaç saat önce
TRAVEL_LEAD_HOURS = 4     # uçak / otobüs / tren
EVE_TIME = "19:00"        # bir gün önceki kısa hatırlatma bu saatten sonra
MAX_UPLOAD = 10           # bir seferde yüklenen en fazla dosya
SEND_FILES = 5            # Telegram'a belge olarak gönderilen en fazla dosya
SEND_FILE_MAX = 10 * 1024 * 1024  # bundan büyük dosya Telegram'a gönderilmez, bilet ekranı linki yeter
BARCODE_MAX = 1000
MAPS_URL = "https://www.google.com/maps/search/?api=1&query="
FIELDS = ("kind", "title", "starts_on", "starts_at", "venue", "address", "seat", "booking_code", "holder", "price",
          "barcode_text", "note")
DRAFT_NAMES = ("", "telegram", "resim", "belge", "image", "photo", "document")  # anlamsız dosya adları başlık olmaz


def now_local():
    """Yerel şimdi. Testlerde todo_reminders.now_local taklit edilir."""
    return todo.now_local()


# ---------- Yardımcılar ----------
def kind_icon(kind):
    return KINDS.get(kind, KINDS["other"])[1]


def maps_url(t):
    """Adres (yoksa yer adı) için Google Haritalar araması."""
    place = t["address"] or t["venue"]
    return MAPS_URL + urllib.parse.quote(place) if place else None


def starts(t):
    """Başlangıç (yerel, saat dilimli); tarih ya da saat yoksa None."""
    d = parse_date(t["starts_on"])
    if d is None or not t["starts_at"]:
        return None
    hour, minute = (int(x) for x in t["starts_at"].split(":"))
    return datetime.combine(d, time(hour, minute), tzinfo=TZ)


def when_text(starts_on, starts_at):
    return fmt_date(starts_on, True) + (f" {starts_at}" if starts_at else "")


def is_past(t, day):
    """Geçmiş mi: elle arşivlenmiş ya da tarihi geçmiş (etkinlik günü boyunca yaklaşanlarda kalır)."""
    return bool(t["archived"]) or (t["starts_on"] is not None and t["starts_on"] < day.isoformat())


def badge(t, day):
    """(metin, css sınıfı): bugün / yarın / X gün kaldı; tarihsiz taslakta 'tarih gir'."""
    d = parse_date(t["starts_on"])
    if d is None:
        return "📝 tarih gir", "overdue"
    left = (d - day).days
    at = f" {t['starts_at']}" if t["starts_at"] else ""
    if left == 0:
        return "Bugün" + at, "good"
    if left == 1:
        return "Yarın" + at, "soon"
    return f"{left} gün kaldı", "soon" if left <= 7 else "later"


def _expense_live(t):
    if not t["expense_id"]:
        return None
    return query_one("SELECT id FROM expenses WHERE id = ? AND user_id = ?", (t["expense_id"], t["user_id"]))


def _render_ctx(**kw):
    return dict(kinds=KINDS, kind_icon=kind_icon, expense_categories=EXPENSE_CATEGORIES, category_icon=category_icon,
                ai_ready=assistant.available(g.user), **kw)


def _form_values():
    """(değerler, hata)"""
    v = {
        "kind": form_choice("kind", KINDS, "other"),
        "title": form_str("title", 150),
        "starts_on": form_date("starts_on"),
        "starts_at": todo.parse_time(request.form.get("starts_at")),
        "venue": form_str("venue", 150),
        "address": form_str("address", 300),
        "seat": form_str("seat", 150),
        "booking_code": form_str("booking_code", 60),
        "holder": form_str("holder", 150),
        "price": None,
        "barcode_text": form_str("barcode_text", BARCODE_MAX),
        "note": form_str("note", 1000),
    }
    if form_str("price", 30):
        price = form_amount("price")
        if not valid_amount(price):
            return v, "Fiyat 0'dan büyük bir sayı olmalı (ör. 1.250) ya da boş bırakın."
        v["price"] = round(price, 2)
    if not v["title"]:
        return v, "Başlık gerekli."
    if not v["starts_on"]:
        return v, "Tarih gerekli."
    return v, None


# ---------- Kayıt (web ve Telegram ortak) ----------
def add_ticket(user_id, v):
    return execute(
        f"INSERT INTO tickets (user_id, {', '.join(FIELDS)}) VALUES (?, {', '.join('?' * len(FIELDS))})",
        (user_id, *(v.get(f) if f in ("starts_on", "starts_at", "price") else (v.get(f) or "") for f in FIELDS)),
    ).lastrowid


def add_expense(ticket, category=None):
    """Bilet fiyatını harcamalara işler (tarih bugün: satın alma günü); harcamanın id'sini döner."""
    category = category or KINDS[ticket["kind"]][2]
    note = f"Bilet: {ticket['title']}"[:200]
    db = get_db()
    expense_id = db.execute("INSERT INTO expenses (user_id, amount, category, note, date) VALUES (?, ?, ?, ?, ?)",
                            (ticket["user_id"], ticket["price"], category, note, today_str())).lastrowid
    db.execute("UPDATE tickets SET expense_id = ? WHERE id = ?", (expense_id, ticket["id"]))
    db.commit()
    automation.fire("expense_added", ticket["user_id"], **{"tutar": ticket["price"], "kategori": category, "not": note})
    return expense_id


def save_files(user_id, ticket_id, files):
    """Bilete dosya ekler (fotoğraf küçültülür, PDF en fazla 3 MB, kota kontrollü). (eklenen, [hata])"""
    saved, errors = 0, []
    for f in [f for f in files if f and f.filename][:MAX_UPLOAD]:
        try:
            save_attachment(f, user_id, "ticket", ticket_id)
            saved += 1
        except ValueError as e:  # QuotaError da ValueError
            errors.append(f"{f.filename}: {e}")
    return saved, errors


def values_from(info, caption="", filename=""):
    """Yapay zekânın okuduğu bilgilerden (ya da hiç yoksa taslak olarak) bilet alanları. Taslağın tarihi boş kalır;
    başlığı açıklamadan, o da yoksa anlamlı dosya adından."""
    v = {f: "" for f in FIELDS}
    v.update(kind="other", starts_on=None, starts_at=None, price=None)
    if info:
        v.update(kind=info["kind"], title=info["title"], starts_on=info["date"], starts_at=info["time"],
                 venue=info["venue"], address=info["address"], seat=info["seat"], booking_code=info["booking_code"],
                 holder=info["holder"], price=info["price"], note=caption[:1000])
    if not v["title"]:
        stem = os.path.splitext(os.path.basename(filename or ""))[0][:150]
        v["title"] = caption[:150] or (stem if stem.lower() not in DRAFT_NAMES else "") \
            or f"Telegram'dan bilet {now_local().strftime('%d.%m')}"
    return v


def card(info, escape):
    """Yapay zekânın okuduğu bilet (Telegram onay mesajı)."""
    lines = [f"{kind_icon(info['kind'])} <b>{escape(info['title'])}</b> · {KINDS[info['kind']][0]}",
             "📅 " + (when_text(info["date"], info["time"]) if info["date"] else "<i>tarih okunamadı, web'den gir</i>")]
    if info["venue"] or info["address"]:
        lines.append("📍 " + escape(" · ".join(x for x in (info["venue"], info["address"]) if x)))
    for icon, key in (("💺", "seat"), ("🔖", "booking_code"), ("👤", "holder")):
        if info[key]:
            lines.append(f"{icon} {escape(info[key])}")
    if info["price"]:
        lines.append(f"💰 {fmt_money(info['price'])}")
    lines.append("Doğruysa kaydet; düzeltilecek bir şey varsa “Düzenle”: kaydedip web'de açarım.")
    return "\n".join(lines)


# ---------- Rotalar ----------
@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    day = now_local().date()
    rows = query("SELECT t.*, (SELECT COUNT(*) FROM attachments a WHERE a.entity = 'ticket' AND a.entity_id = t.id)"
                 " AS files FROM tickets t WHERE t.user_id = ?", (uid,))
    upcoming = sorted((r for r in rows if not is_past(r, day)),
                      key=lambda r: (r["starts_on"] is not None, r["starts_on"] or "", r["starts_at"] or "99:99", r["id"]))
    past = sorted((r for r in rows if is_past(r, day)),
                  key=lambda r: (r["starts_on"] or "", r["starts_at"] or "", r["id"]), reverse=True)
    return render_template("tickets/index.html", **_render_ctx(
        upcoming=upcoming, past=past, past_tab=request.args.get("gecmis") == "1",
        badge=lambda t: badge(t, day), today=day.isoformat()))


@bp.route("/yeni", methods=["POST"])
@login_required
def create():
    uid = g.user["id"]
    v, error = _form_values()
    if error:
        flash(error, "error")
        return redirect(url_for(".index"))
    ticket_id = add_ticket(uid, v)
    text = f"{kind_icon(v['kind'])} {v['title']} eklendi."
    if form_bool("add_expense") and v["price"]:
        add_expense(query_one("SELECT * FROM tickets WHERE id = ?", (ticket_id,)),
                    form_choice("expense_category", EXPENSE_CATEGORIES, None))
        text += " Harcamalara da işlendi."
    saved, errors = save_files(uid, ticket_id, request.files.getlist("files"))
    if saved:
        text += f" {saved} dosya eklendi."
    flash(text, "success")
    for e in errors:
        flash(e, "error")
    return redirect(url_for(".detail", ticket_id=ticket_id) if errors else url_for(".index"))


@bp.route("/oku", methods=["POST"])
@login_required
def read():
    """Bilet görselini ya da PDF'ini yapay zekâyla okur; bileti dosyasıyla ekler ve kontrol için sayfasını açar."""
    uid = g.user["id"]
    if not assistant.available(g.user):
        flash("Yapay zekâ kapalı. Ayarlar → Yapay zekâ bölümünden açabilirsin.", "warning")
        return redirect(url_for(".index"))
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Dosya seçilmedi.", "warning")
        return redirect(url_for(".index"))
    data = f.read()
    try:
        quota.check_file(uid, len(data), f.filename)  # yükleme kapalı ya da dosya çok büyükse hiç okutma
        info = assistant.read_ticket(data, f.mimetype or "")
    except (ai.AIError, ValueError) as e:
        flash(f"Bilet okunamadı: {str(e)[:200]}", "error")
        return redirect(url_for(".index"))
    if not info["is_ticket"]:
        flash("Bu bir bilet gibi görünmüyor; bilgileri okuyamadım. Bileti “＋ Yeni bilet” ile elle ekleyebilirsin.", "warning")
        return redirect(url_for(".index"))
    ticket_id = add_ticket(uid, values_from(info))
    _saved, errors = save_files(uid, ticket_id, [FileStorage(io.BytesIO(data), filename=f.filename)])
    flash(f"{kind_icon(info['kind'])} Bilet okundu ve eklendi; bilgileri kontrol et, gerekirse düzelt.", "success")
    for e in errors:
        flash(e, "error")
    return redirect(url_for(".detail", ticket_id=ticket_id))


@bp.route("/<int:ticket_id>")
@login_required
def detail(ticket_id):
    t = owned_or_404("tickets", ticket_id, g.user["id"])
    day = now_local().date()
    return render_template("tickets/detail.html", **_render_ctx(
        t=t, files=attachments_for("ticket", ticket_id), expense=_expense_live(t), past=is_past(t, day),
        badge=badge(t, day), maps=maps_url(t)))


@bp.route("/<int:ticket_id>/duzenle", methods=["POST"])
@login_required
def update(ticket_id):
    uid = g.user["id"]
    t = owned_or_404("tickets", ticket_id, uid)
    v, error = _form_values()
    if error:
        flash(error, "error")
        return redirect(url_for(".detail", ticket_id=ticket_id))
    execute(f"UPDATE tickets SET {', '.join(f + ' = ?' for f in FIELDS)} WHERE id = ? AND user_id = ?",
            (*(v[f] for f in FIELDS), ticket_id, uid))
    text = "Bilet güncellendi."
    if form_bool("add_expense") and v["price"] and not _expense_live(t):
        add_expense(query_one("SELECT * FROM tickets WHERE id = ?", (ticket_id,)),
                    form_choice("expense_category", EXPENSE_CATEGORIES, None))
        text += " Harcamalara da işlendi."
    flash(text, "success")
    return redirect(url_for(".detail", ticket_id=ticket_id))


@bp.route("/<int:ticket_id>/arsiv", methods=["POST"])
@login_required
def archive(ticket_id):
    uid = g.user["id"]
    t = owned_or_404("tickets", ticket_id, uid)
    execute("UPDATE tickets SET archived = 1 - archived WHERE id = ? AND user_id = ?", (ticket_id, uid))
    if t["archived"]:
        flash(f"{t['title']} arşivden çıkarıldı.", "success")
    else:
        flash(f"📦 {t['title']} geçmişe taşındı; hatırlatma gelmez.", "success")
    return redirect(url_for(".detail", ticket_id=ticket_id))


@bp.route("/<int:ticket_id>/sil", methods=["POST"])
@login_required
def delete(ticket_id):
    uid = g.user["id"]
    t = owned_or_404("tickets", ticket_id, uid)
    trash.move(uid, "tickets", f"{kind_icon(t['kind'])} {t['title']}", ("tickets", ticket_id), entity="ticket")
    flash(trash.notice(t["title"]), "success")
    return redirect(url_for(".index"))


@bp.route("/<int:ticket_id>/goster")
@login_required
def show(ticket_id):
    """🎫 Bilet ekranı: kapıda göstermek için sade, büyük."""
    t = owned_or_404("tickets", ticket_id, g.user["id"])
    files = attachments_for("ticket", ticket_id)
    qr = None
    if t["barcode_text"]:
        try:
            qr = qr_svg(t["barcode_text"])
        except ValueError:  # segno: içerik QR'a sığmıyor
            qr = None
    return render_template("tickets/show.html", t=t, icon=kind_icon(t["kind"]), qr=qr, maps=maps_url(t),
                           badge=badge(t, now_local().date()) if t["starts_on"] else None,
                           images=[a for a in files if a["mime"] != "application/pdf"],
                           pdfs=[a for a in files if a["mime"] == "application/pdf"])


# ---------- Telegram: etkinlik günü ve bir gün önce akşam (cron /hatirlatma) ----------
def lead(t):
    return timedelta(hours=TRAVEL_LEAD_HOURS if t["kind"] in TRAVEL else LEAD_HOURS)


def day_key(t):
    """Etkinlik günü mesajının hangi tarih + saat için gönderildiği (değişince yeniden gönderilir)."""
    return f"{t['starts_on']} {t['starts_at'] or ''}".strip()


def pending(now):
    """[(tür, bilet)] — 'day': etkinlik günü (saatliyse başlangıçtan 3-4 saat önce, saatsizse varsayılan saatten sonra;
    başlangıç geçtiyse gönderilmez) ya da 'eve': bir gün önce EVE_TIME'dan sonra. Arşivlenenlere gitmez."""
    t = now.date()
    hm = now.strftime("%H:%M")
    tomorrow = (t + timedelta(days=1)).isoformat()
    out = []
    for r in query(
        "SELECT k.*, u.telegram_chat_id AS chat_id FROM tickets k JOIN users u ON u.id = k.user_id"
        " WHERE u.telegram_chat_id IS NOT NULL AND k.archived = 0 AND k.starts_on BETWEEN ? AND ?",
        (t.isoformat(), tomorrow),
    ):
        start = starts(r)
        if r["day_sent_for"] != day_key(r):
            if start:
                due = start - lead(r) <= now <= start
            else:
                due = r["starts_on"] == t.isoformat() and hm >= todo.DEFAULT_DUE_TIME
            if due:
                out.append(("day", r))
                continue
        if r["eve_sent_for"] != r["starts_on"] and r["starts_on"] == tomorrow and hm >= EVE_TIME:
            out.append(("eve", r))
    return out


def mark_sent(ticket, kind):
    """Gün mesajı gidince akşam mesajı da gitmiş sayılır (gece kalkan uçakta sıralama bozulmasın)."""
    if kind == "day":
        execute("UPDATE tickets SET day_sent_for = ?, eve_sent_for = ? WHERE id = ?",
                (day_key(ticket), ticket["starts_on"], ticket["id"]))
    else:
        execute("UPDATE tickets SET eve_sent_for = ? WHERE id = ?", (ticket["starts_on"], ticket["id"]))


def files_to_send(ticket):
    """(gönderilecek ekler, gönderilmeyen sayısı): çok büyükler ve SEND_FILES'tan fazlası link olarak kalır."""
    rows = attachments_for("ticket", ticket["id"])
    small = [a for a in rows if a["size"] <= SEND_FILE_MAX]
    return small[:SEND_FILES], len(rows) - len(small[:SEND_FILES])


def send_file(chat_id, ticket, att):
    """Bir bilet dosyasını Telegram'a belge olarak gönderir (dosya adı tırnak/satır sonu içermesin)."""
    with open(os.path.join(upload_dir(), att["filename"]), "rb") as f:
        data = f.read()
    ext = ".pdf" if att["mime"] == "application/pdf" else ".jpg"
    base = re.sub(r'["\\\r\n]', "", os.path.splitext(att["original_name"] or "")[0])[:60] or f"bilet-{ticket['id']}"
    telegram.send_document(chat_id, base + ext, data, caption=f"{kind_icon(ticket['kind'])} {ticket['title']}"[:200],
                           mime=att["mime"])


def _left_text(delta):
    """'3 sa', '2 sa 40 dk', '25 dk'"""
    hours, minutes = divmod(max(0, int(delta.total_seconds() // 60)), 60)
    return " ".join(p for p in (f"{hours} sa" if hours else "", f"{minutes} dk" if minutes or not hours else "") if p)


def message(kind, t, now, url, escape, skipped=0):
    icon, title = kind_icon(t["kind"]), escape(t["title"])
    when = when_text(t["starts_on"], t["starts_at"])
    if kind == "eve":
        lines = [f"{icon} <b>Yarın:</b> {title}", "📅 " + when + (f" · {escape(t['venue'])}" if t["venue"] else ""),
                 f'<a href="{escape(url)}">🎫 Bilet ekranı</a>']
        return "\n".join(lines)
    rel = "Bugün" if t["starts_on"] == now.date().isoformat() else "Yarın"
    start = starts(t)
    lines = [f"{icon} <b>{rel}:</b> {title}",
             f"📅 {when}" + (f" · ⏳ {_left_text(start - now)} kaldı" if start else "")]
    place = " · ".join(x for x in (t["venue"], t["address"]) if x)
    if place:
        lines.append(f'📍 {escape(place)} · <a href="{escape(maps_url(t))}">Haritada aç</a>')
    if t["seat"]:
        lines.append(f"💺 {escape(t['seat'])}")
    if t["booking_code"]:
        lines.append(f"🔖 Rezervasyon: <code>{escape(t['booking_code'])}</code>")
    if t["holder"]:
        lines.append(f"👤 {escape(t['holder'])}")
    if t["note"]:
        lines.append(f"<i>{escape(t['note'][:300])}</i>")
    if skipped:
        lines.append(f"📎 {skipped} dosya burada gönderilmedi (çok büyük ya da çok sayıda); bilet ekranından aç.")
    lines.append(f'<a href="{escape(url)}">🎫 Bilet ekranı</a>')
    return "\n".join(lines)
