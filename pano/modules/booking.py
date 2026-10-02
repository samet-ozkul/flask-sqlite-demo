"""📅 Randevu Sayfası: Calendly benzeri; müsait saatlerini belirlersin, başkaları herkese açık linkten boş bir saat seçip
randevu ister.

- Yönetim /randevu/ (giriş gerekli); herkese açık sayfa /r/<adres> (girişsiz, betiksiz): tür → gün ve boş saat → form
- Boş saat = haftalık aralıklar − kapalı günler − en az önceden süresi − ufuk − bekleyen/onaylı randevular (tamponla)
  − (ayar açıksa) takvimdeki saatli kayıtlar (bitişi bilinmediği için 1 saat dolu sayılır; tüm gün kayıtları sayılmaz)
- Saatler uygulamanın yerel saati (APP_TZ, Türkiye); gönderimde saat sunucuda yeniden kontrol edilir, aynı başlangıca
  ikinci aktif randevu veritabanında da engellenir (kısmi UNIQUE index + BEGIN IMMEDIATE)
- Onay gerekiyorsa talep "bekliyor" gelir: panodan ya da Telegram butonuyla (bk:ok / bk:no) onay/red. Onaylanan randevu
  takvime özel etkinlik olarak eklenir (1 saat önce hatırlatmalı; hatırlatmayı etkinliklerin cron'u gönderir), iptal
  edilince etkinlik de silinir
- Ziyaretçi /r/i/<anahtar> bağlantısından durumu görür, .ics ile takvimine ekler, iptal eder. E-posta gönderilemediği
  için bağlantıyı kaydetmesi söylenir; adres/link/numara sadece onaylanan randevuda gösterilir
- Kötüye kullanım: bal tuzağı alanı, IP başına saatte 5 talep (IP düz saklanmaz, SECRET_KEY ile HMAC), sayfa başına aynı
  anda en fazla 20 bekleyen talep, uzunluk sınırları. CSRF: uygulamanın oturum anahtarı (formu açan ziyaretçiye oturum
  çerezi gider; JS gerekmez); burada anahtar boş da olamaz, form açılmadan gönderilen istek reddedilir
- Kapalı sayfa, olmayan adres ve pasif tür dışarıdan aynı 404; sahibi kapalıyken önizleyebilir (talep gönderemez)
"""
import hashlib
import hmac
import io
import json
import secrets
import sqlite3
from datetime import datetime, time, timedelta, timezone

import segno
from flask import (Blueprint, abort, current_app, flash, g, make_response, redirect, render_template, request,
                   send_file, url_for)

from .. import telegram, trash
from .. import todo_reminders as todo
from ..auth import login_required
from ..calendar_events import _fold as ics_fold, _ics_escape as ics_escape, events_between
from ..db import execute, get_db, owned_or_404, query, query_one
from ..totp import qr_svg
from ..utils import (MONTHS_TR, TZ, WEEKDAYS_TR, fold, form_bool, form_choice, form_int, form_str, parse_date,
                     redirect_back)
from .profile import (EMAIL_RE, PHONE_RE, RESERVED as PROFILE_RESERVED, SLUG_MAX, SLUG_MIN, SLUG_RE, clean_link_url,
                      slugify, tel_number)

# Yönetim /randevu altında, herkese açık sayfa /r/ altında: tek blueprint iki ayrı kökte (profile.py gibi)
bp = Blueprint("booking", __name__)

DURATIONS = (15, 30, 45, 60, 90)
BUFFERS = (0, 5, 10, 15, 30, 60)
STEPS = (15, 30, 60)
# anahtar -> (ikon, ad, ayrıntı alanının adı, ziyaretçinin gördüğü ad)
LOCATIONS = {
    "in_person": ("📍", "Yüz yüze", "Adres", "Yüz yüze"),
    "phone": ("📞", "Telefon", "Aranacak numara", "Telefon · sen ararsın"),
    "online": ("💻", "Online", "Görüşme linki", "Online görüşme"),
    "callback": ("📲", "Ben ararım", "", "Telefon · ben ararım"),
}
STATUSES = {"pending": ("⏳", "Onay bekliyor"), "confirmed": ("✅", "Onaylandı"), "rejected": ("❌", "Reddedildi"),
            "cancelled": ("🚫", "İptal edildi")}
ALREADY = {"pending": "onay bekliyor", "confirmed": "onaylanmış", "rejected": "reddedilmiş", "cancelled": "iptal edilmiş"}
ACTIVE = ("pending", "confirmed")
MAX_TYPES = 5
MAX_PENDING = 20                     # sayfa başına aynı anda bekleyen (saati gelmemiş) talep
IP_LIMIT = 5                         # IP başına saatte talep
NOTICE_MAX_HOURS = 14 * 24
HORIZON_MAX_DAYS = 90
WINDOW_DAYS = 7                      # herkese açık sayfada bir seferde gösterilen gün
CALENDAR_BUSY = timedelta(hours=1)   # takvimdeki saatli kaydın bitişi bilinmez: başladığı andan 1 saat dolu
REMIND_BEFORE = 60                   # onaylanan randevunun etkinliği: 1 saat önce + zamanı gelince hatırlat
TOKEN_BYTES = 16                     # 22 karakter, 128 bit
DB_FORMAT = "%Y-%m-%d %H:%M"         # yerel saat, saat dilimsiz
SLOT_FORMAT = "%Y-%m-%dT%H:%M"       # adresteki ?t=
LIMITS = {"name": 80, "contact": 120, "note": 500}
PAGE_LIMITS = {"title": 80, "description": 500}
TYPE_NAME_MAX, DETAIL_MAX, BLOCK_NOTE_MAX = 60, 200, 60
DEFAULTS = {"enabled": 0, "description": "", "min_notice_hours": 2, "horizon_days": 30, "slot_step": 30,
            "needs_approval": 1, "busy_from_calendar": 1}
DEFAULT_WEEKLY = {d: [("09:00", "12:00"), ("13:00", "17:00")] for d in range(5)}   # Pzt–Cum
QR_PNG_SCALE = 20
# Profil adreslerinde ayrılmış kelimeler + bu modülün yolları ve taklitte sık kullanılanlar
RESERVED = PROFILE_RESERVED | {
    "randevu", "randevular", "rezervasyon", "takvim", "talep", "iptal", "onay", "onayla", "yeni", "sil", "duzenle",
    "login", "logout", "signin", "signup", "hesap", "account", "dogrula", "verify", "odeme", "banka",
}
TABS = {"randevular": "📥 Randevular", "ayarlar": "⚙️ Sayfa ve saatler", "turler": "🗂️ Görüşme türleri"}
PUBLIC_ENDPOINTS = ("booking.public", "booking.slots", "booking.request_form", "booking.manage",
                    "booking.visitor_cancel", "booking.ics")
# Sayfalarda betik yok; kullanıcı metni kaçışlansa da ek önlem: dış kaynak ve gömme kapalı, form sadece bu siteye
CSP = ("default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'")


class SlotTaken(Exception):
    """Seçilen saat bu arada doldu."""


# ---------- Saat ----------
def now_local():
    """Şu an, yerel saat ve saat dilimsiz (randevu saatleri de öyle saklanır). todo.now_local testte taklit edilir."""
    return todo.now_local().astimezone(TZ).replace(tzinfo=None)


def parse_dt(value):
    try:
        return datetime.strptime(value or "", DB_FORMAT)
    except ValueError:
        return None


def long_date(d):
    """'5 Ekim 2026 Pazartesi'"""
    return f"{d.day} {MONTHS_TR[d.month - 1]} {d.year} {WEEKDAYS_TR[d.weekday()]}"


def day_label(d, today):
    text = f"{d.day} {MONTHS_TR[d.month - 1]} {WEEKDAYS_TR[d.weekday()]}"
    if d == today:
        return "Bugün · " + text
    if d == today + timedelta(days=1):
        return "Yarın · " + text
    return text


def when_text(b):
    """'5 Ekim 2026 Pazartesi · 10:00–10:30'"""
    start, end = parse_dt(b["start_at"]), parse_dt(b["end_at"])
    return f"{long_date(start)} · {start:%H:%M}–{end:%H:%M}"


def tz_note():
    return "Saatler Türkiye saatidir (GMT+3)." if TZ.key == "Europe/Istanbul" else f"Saatler {TZ.key} saatidir."


# ---------- Adres (slug) ----------
def slug_error(slug, user_id):
    """Adres uygunsa None, değilse hata metni (profil adresleriyle aynı kurallar; randevu sayfaları arasında tekil)."""
    if not (SLUG_MIN <= len(slug) <= SLUG_MAX) or not SLUG_RE.match(slug):
        return (f"Adres {SLUG_MIN}-{SLUG_MAX} karakter olmalı; küçük harf (a-z), rakam ve tire içerebilir, "
                "tireyle başlayıp bitemez.")
    if slug in RESERVED:
        return f"“{slug}” ayrılmış bir kelime; başka bir adres seç."
    if query_one("SELECT 1 FROM booking_pages WHERE slug = ? AND user_id != ?", (slug, user_id)):
        return f"“{slug}” adresi başka biri tarafından alınmış."
    return None


def suggest_slug(user):
    """Profil adresi, yoksa ad ya da kullanıcı adı; alınmışsa sonuna -2, -3... eklenir."""
    profile = query_one("SELECT slug FROM public_profiles WHERE user_id = ?", (user["id"],))
    base = (profile["slug"] if profile else "") or slugify(user["display_name"]) or slugify(user["username"])
    if len(base) < SLUG_MIN or base in RESERVED:
        base = f"randevu-{user['id']}"
    for n in range(1, 100):
        slug = base if n == 1 else f"{base[:SLUG_MAX - len(str(n)) - 1].rstrip('-')}-{n}"
        if slug_error(slug, user["id"]) is None:
            return slug
    return f"randevu-{user['id']}"


# ---------- Haftalık saatler ----------
def _weekly(raw):
    """Kayıttaki JSON -> {gün: [('09:00', '12:00'), ...]} (0 = Pazartesi); bozuk satır yok sayılır."""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    out = {}
    for key, ranges in (data.items() if isinstance(data, dict) else ()):
        if not (str(key).isdigit() and int(key) < 7 and isinstance(ranges, list)):
            continue
        clean = sorted((r[0], r[1]) for r in ranges if isinstance(r, list) and len(r) == 2
                       and all(isinstance(x, str) and todo.parse_time(x) == x for x in r) and r[0] < r[1])
        if clean:
            out[int(key)] = clean[:2]
    return out


def weekly_rows(weekly):
    """Kayıttaki saatler -> formdaki 7 satır."""
    rows = []
    for d in range(7):
        ranges = weekly.get(d, [])
        (s1, e1), (s2, e2) = (list(ranges) + [("", ""), ("", "")])[:2]
        rows.append({"day": d, "name": WEEKDAYS_TR[d], "open": bool(ranges), "s1": s1, "e1": e1, "s2": s2, "e2": e2})
    return rows


def _weekly_form():
    """Formdaki ham satırlar (hata sonrası yazılanlar kaybolmasın)."""
    return [{"day": d, "name": WEEKDAYS_TR[d], "open": form_bool(f"open_{d}"),
             **{k: request.form.get(f"{k}_{d}", "").strip()[:5] for k in ("s1", "e1", "s2", "e2")}} for d in range(7)]


def parse_weekly(rows):
    """Form satırları -> {gün: [(başlangıç, bitiş)]}; hatalıysa ValueError. Kapalı günün saatleri yok sayılır."""
    out = {}
    for row in rows:
        if not row["open"]:
            continue
        name, ranges = row["name"], []
        for s_key, e_key in (("s1", "e1"), ("s2", "e2")):
            raw_s, raw_e = row[s_key], row[e_key]
            if not raw_s and not raw_e:
                continue
            if not raw_s or not raw_e:
                raise ValueError(f"{name}: saat aralığının başlangıcını ve bitişini yaz (ör. 09:00 – 12:00).")
            start, end = todo.parse_time(raw_s), todo.parse_time(raw_e)
            if start is None or end is None:
                raise ValueError(f"{name}: saat geçersiz (ör. 09:00).")
            if start >= end:
                raise ValueError(f"{name}: başlangıç saati bitişten önce olmalı ({start} – {end}).")
            ranges.append((start, end))
        if not ranges:
            raise ValueError(f"{name} açık ama saat aralığı yok; saat yaz ya da günü kapat.")
        ranges.sort()
        if len(ranges) == 2 and ranges[1][0] < ranges[0][1]:
            raise ValueError(f"{name}: iki saat aralığı çakışıyor.")
        out[row["day"]] = ranges
    return out


# ---------- Kayıtlar ----------
def _page(row):
    return {**dict(row), "weekly": _weekly(row["weekly_hours"])} if row else None


def get_page(user_id):
    return _page(query_one("SELECT * FROM booking_pages WHERE user_id = ?", (user_id,)))


def get_types(user_id, active_only=False):
    return query("SELECT * FROM booking_types WHERE user_id = ?" + (" AND active = 1" if active_only else "")
                 + " ORDER BY sort, id", (user_id,))


def get_blocks(user_id, today):
    """Bitmemiş kapalı günler."""
    return query("SELECT * FROM booking_blocks WHERE user_id = ? AND end_date >= ? ORDER BY start_date, id",
                 (user_id, today.isoformat()))


def public_url(slug):
    return url_for("booking.public", slug=slug, _external=True)


def manage_url(token):
    return url_for("booking.manage", token=token, _external=True)


def is_email(contact):
    return "@" in contact


def contact_href(contact):
    return "mailto:" + contact if is_email(contact) else "tel:" + tel_number(contact)


def view(b):
    """Şablon ve mesajlar için hazır alanlar."""
    icon, label = STATUSES[b["status"]]
    loc = LOCATIONS.get(b["location_kind"], ("🗓️", "", "", ""))
    return {**dict(b), "start": parse_dt(b["start_at"]), "end": parse_dt(b["end_at"]), "when": when_text(b),
            "status_icon": icon, "status_label": label, "loc_icon": loc[0], "loc_label": loc[1], "loc_public": loc[3],
            "contact_href": contact_href(b["contact"]), "contact_icon": "✉️" if is_email(b["contact"]) else "📞",
            "detail_tel": "tel:" + tel_number(b["location_detail"]) if b["location_kind"] == "phone" else "",
            "detail_link": b["location_detail"] if b["location_kind"] == "online"
            and b["location_detail"].lower().startswith(("http://", "https://")) else ""}


def booking_lists(user_id, now):
    """(bekleyen, yaklaşan, geçmiş): geçmiş = saati geçenler ile reddedilen / iptal edilenler (en yeni 50)."""
    t = now.strftime(DB_FORMAT)
    pending = query("SELECT * FROM bookings WHERE user_id = ? AND status = 'pending' AND start_at > ? ORDER BY start_at",
                    (user_id, t))
    upcoming = query("SELECT * FROM bookings WHERE user_id = ? AND status = 'confirmed' AND end_at > ?"
                     " ORDER BY start_at", (user_id, t))
    past = query("SELECT * FROM bookings WHERE user_id = ? AND (status IN ('rejected', 'cancelled')"
                 " OR (status = 'pending' AND start_at <= ?) OR (status = 'confirmed' AND end_at <= ?))"
                 " ORDER BY start_at DESC, id DESC LIMIT 50", (user_id, t, t))
    return [view(b) for b in pending], [view(b) for b in upcoming], [view(b) for b in past]


# ---------- Boş saatler ----------
def _busy(page, first, last):
    """first..last günlerindeki dolu aralıklar: [(başlangıç, bitiş, tampon_dk)]."""
    uid = page["user_id"]
    out = []
    for r in query("SELECT start_at, end_at, buffer_min FROM bookings WHERE user_id = ? AND status IN ('pending', 'confirmed')"
                   " AND start_at < ? AND end_at > ?",
                   (uid, (last + timedelta(days=2)).isoformat(), (first - timedelta(days=1)).isoformat())):
        start, end = parse_dt(r["start_at"]), parse_dt(r["end_at"])
        if start and end:
            out.append((start, end, r["buffer_min"]))
    if page["busy_from_calendar"]:
        # Onaylanan randevunun kendi etkinliği tekrar sayılmaz (randevu süresi ve tamponuyla zaten yukarıda)
        linked = {str(r["event_id"]) for r in query(
            "SELECT event_id FROM bookings WHERE user_id = ? AND event_id IS NOT NULL", (uid,))}
        for ev in events_between(uid, first - timedelta(days=1), last):
            hm = todo.parse_time(ev["time"]) if ev["time"] else None
            if hm is None or ev["done"] or (ev["kind"] == "event" and ev["uid"].split("-")[1] in linked):
                continue   # tüm gün kayıtları ve tamamlananlar dolu sayılmaz
            start = datetime.combine(ev["date"], time.fromisoformat(hm))
            out.append((start, start + CALENDAR_BUSY, 0))
    return out


def _conflicts(start, end, buffer_min, busy):
    """İki randevu arasında en az ikisinin tamponundan büyüğü kadar boşluk kalmalı."""
    for b_start, b_end, b_buffer in busy:
        gap = timedelta(minutes=max(buffer_min, b_buffer))
        if start < b_end + gap and b_start < end + gap:
            return True
    return False


def slots_between(page, btype, first, last, now):
    """Ufka düşen first..last günleri ve boş başlangıç saatleri: [(gün, [datetime, ...])] (boş gün boş listeyle)."""
    today = now.date()
    first = max(first, today)
    last = min(last, today + timedelta(days=page["horizon_days"] - 1))
    if first > last:
        return []
    earliest = now + timedelta(hours=page["min_notice_hours"])
    busy = _busy(page, first, last)
    blocks = query("SELECT start_date, end_date FROM booking_blocks WHERE user_id = ? AND end_date >= ? AND start_date <= ?",
                   (page["user_id"], first.isoformat(), last.isoformat()))
    duration = timedelta(minutes=btype["duration_min"])
    step = timedelta(minutes=page["slot_step"] if page["slot_step"] in STEPS else 30)
    days, day = [], first
    while day <= last:
        free = []
        iso = day.isoformat()
        if not any(b["start_date"] <= iso <= b["end_date"] for b in blocks):
            for s, e in page["weekly"].get(day.weekday(), []):
                t, end = datetime.combine(day, time.fromisoformat(s)), datetime.combine(day, time.fromisoformat(e))
                while t + duration <= end:
                    if t >= earliest and not _conflicts(t, t + duration, btype["buffer_min"], busy):
                        free.append(t)
                    t += step
        days.append((day, free))
        day += timedelta(days=1)
    return days


def availability(page, btype, now):
    """Ufuktaki bütün günler (bugün dahil horizon_days gün)."""
    return slots_between(page, btype, now.date(), now.date() + timedelta(days=page["horizon_days"] - 1), now)


def is_free(page, btype, start, now):
    days = slots_between(page, btype, start.date(), start.date(), now)
    return bool(days) and start in days[0][1]


# ---------- Talep ----------
def clean_contact(raw, phone_only=False):
    """Telefon ya da e-posta; geçersizse ValueError. phone_only: 'ben ararım' türünde sadece telefon."""
    value = " ".join((raw or "").split())
    if not value:
        raise ValueError("Telefon numaranı ya da e-posta adresini yaz.")
    if is_email(value):
        if phone_only:
            raise ValueError("Bu görüşmede seni arayacağız; telefon numaranı yaz.")
        if not EMAIL_RE.match(value):
            raise ValueError("E-posta adresi geçersiz.")
        return value
    if not phone_ok(value):
        raise ValueError("Telefon numarası geçersiz (ör. 0532 123 45 67)" + ("." if phone_only else
                                                                           " ya da e-posta adresi yaz."))
    return value


def phone_ok(phone):
    return bool(PHONE_RE.match(phone)) and 7 <= sum(ch.isdigit() for ch in phone) <= 15


def ip_hash(ip):
    """IP düz saklanmaz: SECRET_KEY ile HMAC (saatlik sınır için aynı IP'yi tanımaya yeter)."""
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(key, f"booking-ip:{ip or ''}".encode(), hashlib.sha256).hexdigest()[:32]


def _add_event(db, b):
    """Onaylanan randevu kullanıcının takvimine: özel (paylaşılmayan), hatırlatmalı etkinlik. id döner."""
    start, end = parse_dt(b["start_at"]), parse_dt(b["end_at"])
    loc = LOCATIONS.get(b["location_kind"], ("", "Randevu", "", ""))[1]
    place = loc + (f": {b['location_detail']}" if b["location_detail"] else "")
    lines = [f"{b['type_name']} · {start:%H:%M}–{end:%H:%M}", f"İletişim: {b['contact']}"]
    if b["note"]:
        lines.append(b["note"])
    return db.execute(
        "INSERT INTO events (user_id, title, date, time, place, note, shared, remind_before) VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
        (b["user_id"], f"Randevu: {b['name']}"[:150], start.date().isoformat(), start.strftime("%H:%M"), place[:150],
         "\n".join(lines)[:1000], REMIND_BEFORE)).lastrowid


def create_booking(page, btype, start, name, contact, note, ip_digest):
    """Yeni talep (onay gerekmiyorsa onaylı ve takvimde); satırı döner. Saat bu arada dolduysa SlotTaken."""
    end = start + timedelta(minutes=btype["duration_min"])
    status = "pending" if page["needs_approval"] else "confirmed"
    db = get_db()
    if db.in_transaction:
        db.commit()
    db.execute("BEGIN IMMEDIATE")   # aynı anda gelen iki talep aynı boş saati görmesin
    try:
        if not is_free(page, btype, start, now_local()):
            raise SlotTaken
        booking_id = db.execute(
            "INSERT INTO bookings (user_id, type_id, type_name, location_kind, location_detail, buffer_min, start_at,"
            " end_at, name, contact, note, status, manage_token, ip_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (page["user_id"], btype["id"], btype["name"], btype["location_kind"], btype["location_detail"],
             btype["buffer_min"], start.strftime(DB_FORMAT), end.strftime(DB_FORMAT), name, contact, note, status,
             secrets.token_urlsafe(TOKEN_BYTES), ip_digest)).lastrowid
        b = db.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        if status == "confirmed":
            db.execute("UPDATE bookings SET event_id = ? WHERE id = ?", (_add_event(db, b), booking_id))
        db.commit()
    except (SlotTaken, sqlite3.IntegrityError):   # kısmi UNIQUE index: aynı başlangıca ikinci aktif randevu
        db.rollback()
        raise SlotTaken
    return db.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()


def decide(b, action, by="owner"):
    """Onay ('confirm') / red ('reject') / iptal ('cancel'): web, Telegram ve ziyaretçi aynı yolu kullanır.
    (değişti_mi, mesaj) döner. Durum tek sorguda değişir: aynı anda basılan iki buton iki etkinlik açmasın."""
    db = get_db()
    if action == "cancel":
        if b["status"] not in ACTIVE:
            return False, f"Bu randevu zaten {ALREADY[b['status']]}."
        if db.execute("UPDATE bookings SET status = 'cancelled', cancelled_by = ?, event_id = NULL"
                      " WHERE id = ? AND status IN ('pending', 'confirmed')", (by, b["id"])).rowcount != 1:
            db.commit()
            return False, "Randevu bu arada değişti; sayfayı yenile."
        if b["event_id"]:
            db.execute("DELETE FROM events WHERE id = ? AND user_id = ?", (b["event_id"], b["user_id"]))
        db.commit()
        return True, "🚫 Randevu iptal edildi" + ("; takviminden de silindi." if b["event_id"] else ".")
    if b["status"] != "pending":
        return False, f"Bu randevu zaten {ALREADY[b['status']]}."
    if action == "reject":
        if db.execute("UPDATE bookings SET status = 'rejected' WHERE id = ? AND status = 'pending'",
                      (b["id"],)).rowcount != 1:
            db.commit()
            return False, "Randevu bu arada değişti; sayfayı yenile."
        db.commit()
        return True, "❌ Randevu talebi reddedildi."
    if parse_dt(b["start_at"]) <= now_local():
        return False, "Randevu saati geçmiş; onaylanamaz."
    if db.execute("UPDATE bookings SET status = 'confirmed' WHERE id = ? AND status = 'pending'",
                  (b["id"],)).rowcount != 1:
        db.commit()
        return False, "Randevu bu arada değişti; sayfayı yenile."
    db.execute("UPDATE bookings SET event_id = ? WHERE id = ?", (_add_event(db, b), b["id"]))
    db.commit()
    return True, "✅ Randevu onaylandı ve takvimine eklendi."


# ---------- Telegram ----------
def decision_buttons(booking_id):
    return [[("✅ Onayla", f"bk:ok:{booking_id}"), ("❌ Reddet", f"bk:no:{booking_id}")]]


def status_head(b, new=False):
    if new:
        return ("📅 <b>Yeni randevu talebi</b> · onayını bekliyor" if b["status"] == "pending"
                else "📅 <b>Yeni randevu</b> · takvimine eklendi")
    return {"pending": "📅 <b>Randevu talebi</b> · onayını bekliyor",
            "confirmed": "✅ <b>Randevu onaylandı</b> · takvimine eklendi",
            "rejected": "❌ <b>Randevu talebi reddedildi</b>",
            "cancelled": "🚫 <b>Randevu iptal edildi</b>" + (" · ziyaretçi iptal etti" if b["cancelled_by"] == "visitor"
                                                            else "")}[b["status"]]


def owner_message(b, url, escape, head, extra=""):
    """Sahibine giden mesaj: kim, ne zaman, tür, iletişim, not (HTML kaçışlı)."""
    v = view(b)
    lines = [head, f"👤 <b>{escape(b['name'])}</b>", f"🗓️ {v['when']}",
             f"{v['loc_icon']} {escape(b['type_name'])} · {v['loc_label']}", f"{v['contact_icon']} {escape(b['contact'])}"]
    if b["note"]:
        lines.append(f"📝 <i>{escape(b['note'])}</i>")
    if extra:
        lines.append(extra)
    lines.append(f'<a href="{escape(url)}">Randevular →</a>')
    return "\n".join(lines)


def _notify(user_id, text, buttons=None):
    """Telegram bağlıysa sahibine mesaj; hata sayfayı bozmaz."""
    owner = query_one("SELECT telegram_chat_id FROM users WHERE id = ?", (user_id,))
    if not (owner and owner["telegram_chat_id"] and telegram.enabled()):
        return
    try:
        telegram.send_message(owner["telegram_chat_id"], text, buttons=buttons)
    except telegram.TelegramError as e:
        current_app.logger.warning("Randevu bildirimi gönderilemedi (kullanıcı %s): %s", user_id, e)
    except Exception:  # bildirim yan etkidir; talep yine kaydedilmiş olmalı
        current_app.logger.exception("Randevu bildirimi hatası")


def notify_new(b):
    pending = b["status"] == "pending"
    # Butonlar ancak webhook kuruluysa çalışır; değilse panoya yönlendirilir
    buttons = decision_buttons(b["id"]) if pending and telegram.webhook_active() else None
    extra = "<i>Onaylamak ya da reddetmek için panoyu aç.</i>" if pending and not buttons else ""
    _notify(b["user_id"], owner_message(b, url_for("booking.index", _external=True), telegram.escape,
                                        status_head(b, new=True), extra), buttons)


# ---------- .ics ----------
def build_ics(b, title, url, now_utc):
    """Ziyaretçinin takvimine tek randevu (UTC saatlerle, 1 saat önce uyarı)."""
    def utc(dt):
        return dt.replace(tzinfo=TZ).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    v = view(b)
    summary = f"{b['type_name']} · {title}" if title else b["type_name"]
    location = b["location_detail"] or v["loc_public"]
    description = v["loc_public"] + (f": {b['location_detail']}" if b["location_detail"] else "") + "\n" + url
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Kisisel Pano//TR", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "BEGIN:VEVENT", f"UID:booking-{b['id']}@kisisel-pano", f"DTSTAMP:{now_utc.strftime('%Y%m%dT%H%M%SZ')}",
             f"DTSTART:{utc(v['start'])}", f"DTEND:{utc(v['end'])}", f"SUMMARY:{ics_escape(summary)}",
             f"LOCATION:{ics_escape(location)}", f"DESCRIPTION:{ics_escape(description)}", f"URL:{url}",
             "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{ics_escape(summary)}", "TRIGGER:-PT1H", "END:VALARM",
             "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(ics_fold(line) for line in lines) + "\r\n"


# ---------- Yönetim ----------
def _defaults(user):
    name = user["display_name"]
    return {**DEFAULTS, "slug": suggest_slug(user), "title": f"{name} ile randevu" if name else "Randevu al",
            "rows": weekly_rows(DEFAULT_WEEKLY)}


def _page_form_values():
    """Hata sonrası formu kullanıcının yazdıklarıyla yeniden doldurmak için."""
    f = request.form
    return {**{k: f.get(k, "") for k in ("slug", "title", "description", "min_notice_hours", "horizon_days",
                                          "slot_step")},
            **{k: form_bool(k) for k in ("enabled", "needs_approval", "busy_from_calendar")}, "rows": _weekly_form()}


def _parse_page(uid):
    """Formdan sayfa ayarları; (değerler, hata). Büyük harf ve Türkçe harfler adreste dönüştürülür (Ş -> s)."""
    raw_slug = request.form.get("slug", "").strip()
    v = {"slug": fold(raw_slug), "title": form_str("title", PAGE_LIMITS["title"]),
         "description": form_str("description", PAGE_LIMITS["description"]).replace("\r\n", "\n"),
         "min_notice_hours": form_int("min_notice_hours"), "horizon_days": form_int("horizon_days"),
         "slot_step": form_int("slot_step"),
         **{k: form_bool(k) for k in ("enabled", "needs_approval", "busy_from_calendar")}}
    error = slug_error(v["slug"], uid)
    if error:
        hint = slugify(raw_slug)
        if hint != v["slug"] and slug_error(hint, uid) is None:
            error += f" Öneri: “{hint}”"
        return v, error
    if v["min_notice_hours"] is None or not 0 <= v["min_notice_hours"] <= NOTICE_MAX_HOURS:
        return v, f"“En az kaç saat önceden” 0 ile {NOTICE_MAX_HOURS} arasında olmalı."
    if v["horizon_days"] is None or not 1 <= v["horizon_days"] <= HORIZON_MAX_DAYS:
        return v, f"“Kaç gün ileriye kadar” 1 ile {HORIZON_MAX_DAYS} arasında olmalı."
    if v["slot_step"] not in STEPS:
        v["slot_step"] = DEFAULTS["slot_step"]
    try:
        v["weekly"] = parse_weekly(_weekly_form())
    except ValueError as e:
        return v, str(e)
    return v, None


def _render_index(tab, status=200, **ctx):
    uid = g.user["id"]
    page = get_page(uid)
    tab = tab if tab in TABS else ("randevular" if page else "ayarlar")
    now = now_local()
    types = get_types(uid)
    if tab == "randevular":
        ctx["pending"], ctx["upcoming"], ctx["past"] = booking_lists(uid, now)
    elif tab == "ayarlar":
        ctx.setdefault("form", {**page, "rows": weekly_rows(page["weekly"])} if page else _defaults(g.user))
        ctx["blocks"] = get_blocks(uid, now.date())
        ctx.setdefault("block_form", {})
    else:
        ctx.setdefault("new_type", {"duration_min": 30, "location_kind": "online", "buffer_min": 0})
        ctx.setdefault("editing", None)
    counts = {"randevular": query_one("SELECT COUNT(*) AS n FROM bookings WHERE user_id = ? AND status = 'pending'"
                                      " AND start_at > ?", (uid, now.strftime(DB_FORMAT)))["n"],
              "turler": len(types)}
    url = public_url(page["slug"]) if page else None
    return render_template(
        "booking/index.html", tab=tab, tabs=TABS, counts=counts, page=page, types=types, url=url,
        qr=qr_svg(url) if url else None, base_url=request.url_root + "r/", locations=LOCATIONS, durations=DURATIONS,
        buffers=BUFFERS, steps=STEPS, max_types=MAX_TYPES, today=now.date().isoformat(),
        has_hours=bool(page and page["weekly"]), telegram_ready=telegram.enabled() and bool(g.user["telegram_chat_id"]),
        notice_max=NOTICE_MAX_HOURS, horizon_max=HORIZON_MAX_DAYS, **ctx), status


@bp.route("/randevu/")
@login_required
def index():
    return _render_index(request.args.get("tab", ""))


@bp.route("/randevu/ayarlar", methods=["POST"])
@login_required
def save_settings():
    uid = g.user["id"]
    v, error = _parse_page(uid)
    if error:
        flash(error, "error")
        return _render_index("ayarlar", status=400, form=_page_form_values())
    fields = ("slug", "enabled", "title", "description", "min_notice_hours", "horizon_days", "slot_step",
              "needs_approval", "busy_from_calendar")
    weekly = json.dumps({str(d): [list(r) for r in ranges] for d, ranges in v["weekly"].items()})
    db = get_db()
    try:
        db.execute(
            f"INSERT INTO booking_pages (user_id, {', '.join(fields)}, weekly_hours) VALUES (?{', ?' * (len(fields) + 1)})"
            f" ON CONFLICT(user_id) DO UPDATE SET {', '.join(f'{k} = excluded.{k}' for k in (*fields, 'weekly_hours'))},"
            " updated_at = CURRENT_TIMESTAMP",
            (uid, *[v[k] for k in fields], weekly))
    except sqlite3.IntegrityError:   # aynı anda başkası aynı adresi aldıysa
        db.rollback()
        flash(f"“{v['slug']}” adresi başka biri tarafından alınmış.", "error")
        return _render_index("ayarlar", status=400, form=_page_form_values())
    db.commit()
    flash("📅 Randevu sayfası kaydedildi. " + ("Sayfan yayında." if v["enabled"] else
                                              "Sayfa kapalı; “Sayfa yayında” işaretlenince herkes görebilir."), "success")
    return redirect(url_for(".index", tab="ayarlar"))


@bp.route("/randevu/kapali/yeni", methods=["POST"])
@login_required
def block_add():
    start, end = parse_date(request.form.get("start_date")), parse_date(request.form.get("end_date"))
    note = form_str("note", BLOCK_NOTE_MAX)
    end = end or start
    today = now_local().date()
    error = None
    if start is None:
        error = "Kapatılacak günü seç."
    elif end < start:
        error = "Bitiş tarihi başlangıçtan önce olamaz."
    elif (end - start).days > 366:
        error = "En fazla bir yıllık aralık kapatılabilir."
    elif end < today:
        error = "Geçmiş günler kapatılamaz."
    if error:
        flash(error, "error")
        return _render_index("ayarlar", status=400, block_form={k: request.form.get(k, "")
                                                                for k in ("start_date", "end_date", "note")})
    execute("INSERT INTO booking_blocks (user_id, start_date, end_date, note) VALUES (?, ?, ?, ?)",
            (g.user["id"], start.isoformat(), end.isoformat(), note))
    flash("🚫 Kapalı gün eklendi: o günlerde randevu alınamaz. Önceden alınmış randevular yerinde durur.", "success")
    return redirect(url_for(".index", tab="ayarlar", _anchor="kapali"))


@bp.route("/randevu/kapali/<int:block_id>/sil", methods=["POST"])
@login_required
def block_delete(block_id):
    owned_or_404("booking_blocks", block_id, g.user["id"])
    execute("DELETE FROM booking_blocks WHERE id = ? AND user_id = ?", (block_id, g.user["id"]))
    flash("Kapalı gün kaldırıldı.", "success")
    return redirect(url_for(".index", tab="ayarlar", _anchor="kapali"))


def _parse_type():
    """Formdan görüşme türü; (değerler, hata)."""
    v = {"name": form_str("name", TYPE_NAME_MAX), "duration_min": form_int("duration_min"),
         "location_kind": form_choice("location_kind", LOCATIONS, "online"),
         "location_detail": form_str("location_detail", DETAIL_MAX), "buffer_min": form_int("buffer_min") or 0}
    kind, detail = v["location_kind"], v["location_detail"]
    if not v["name"]:
        return v, "Görüşme türüne bir ad ver (ör. Tanışma görüşmesi)."
    if v["duration_min"] not in DURATIONS:
        return v, "Süre 15, 30, 45, 60 ya da 90 dakika olabilir."
    if v["buffer_min"] not in BUFFERS:
        return v, "Aradaki boşluk geçersiz."
    if kind == "callback":
        v["location_detail"] = ""
    elif kind == "in_person" and not detail:
        return v, "Yüz yüze görüşme için adres yaz."
    elif kind == "phone" and not phone_ok(detail):
        return v, "Telefonla görüşme için aranacak numarayı yaz (ör. 0532 123 45 67)."
    elif kind == "online" and detail:
        try:
            url = clean_link_url(detail)
        except ValueError:
            url = ""
        if not url.lower().startswith(("http://", "https://")):
            return v, "Görüşme linki geçersiz: https:// ile başlamalı (ör. https://meet.google.com/abc-defg-hij)."
        v["location_detail"] = url
    return v, None


@bp.route("/randevu/tur/yeni", methods=["POST"])
@login_required
def type_create():
    uid = g.user["id"]
    v, error = _parse_type()
    if not error and len(get_types(uid)) >= MAX_TYPES:
        error = f"En fazla {MAX_TYPES} görüşme türü eklenebilir."
    if error:
        flash(error, "error")
        return _render_index("turler", status=400, new_type=v)
    sort = query_one("SELECT COALESCE(MAX(sort), 0) + 1 AS n FROM booking_types WHERE user_id = ?", (uid,))["n"]
    execute("INSERT INTO booking_types (user_id, name, duration_min, location_kind, location_detail, buffer_min, sort)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)", (uid, v["name"], v["duration_min"], v["location_kind"], v["location_detail"],
                                              v["buffer_min"], sort))
    flash(f"🗂️ “{v['name']}” eklendi.", "success")
    return redirect(url_for(".index", tab="turler"))


@bp.route("/randevu/tur/<int:type_id>", methods=["POST"])
@login_required
def type_edit(type_id):
    owned_or_404("booking_types", type_id, g.user["id"])
    v, error = _parse_type()
    if error:
        flash(error, "error")
        return _render_index("turler", status=400, editing={**v, "id": type_id})
    execute("UPDATE booking_types SET name = ?, duration_min = ?, location_kind = ?, location_detail = ?, buffer_min = ?"
            " WHERE id = ? AND user_id = ?", (v["name"], v["duration_min"], v["location_kind"], v["location_detail"],
                                              v["buffer_min"], type_id, g.user["id"]))
    flash("🗂️ Görüşme türü kaydedildi. Alınmış randevuların saati ve süresi değişmez.", "success")
    return redirect(url_for(".index", tab="turler"))


@bp.route("/randevu/tur/<int:type_id>/durum", methods=["POST"])
@login_required
def type_toggle(type_id):
    t = owned_or_404("booking_types", type_id, g.user["id"])
    execute("UPDATE booking_types SET active = 1 - active WHERE id = ? AND user_id = ?", (type_id, g.user["id"]))
    flash(f"⏸️ “{t['name']}” sayfada gösterilmiyor." if t["active"] else f"▶️ “{t['name']}” yeniden sayfada.",
          "success")
    return redirect(url_for(".index", tab="turler"))


@bp.route("/randevu/tur/<int:type_id>/sil", methods=["POST"])
@login_required
def type_delete(type_id):
    t = owned_or_404("booking_types", type_id, g.user["id"])
    trash.move(g.user["id"], "booking", f"📅 {t['name']} ({t['duration_min']} dk)", ("booking_types", type_id))
    flash(trash.notice(t["name"]), "success")
    return redirect(url_for(".index", tab="turler"))


@bp.route("/randevu/<int:booking_id>/<any(onayla, reddet, iptal):action>", methods=["POST"])
@login_required
def decide_web(booking_id, action):
    b = owned_or_404("bookings", booking_id, g.user["id"])
    ok, message = decide(b, {"onayla": "confirm", "reddet": "reject", "iptal": "cancel"}[action])
    flash(message, "success" if ok else "warning")
    return redirect_back("booking.index")


@bp.route("/randevu/qr.png")
@login_required
def qr_png():
    """Afiş, kartvizit, e-posta imzası için büyük QR."""
    page = get_page(g.user["id"])
    if page is None:
        abort(404)
    buf = io.BytesIO()
    segno.make(public_url(page["slug"]), error="m").save(buf, kind="png", scale=QR_PNG_SCALE, border=4)
    buf.seek(0)
    return send_file(buf, mimetype="image/png", as_attachment=True, download_name=f"randevu-{page['slug']}-qr.png")


# ---------- Herkese açık sayfa ----------
def _is_owner(page):
    return g.user is not None and g.user["id"] == page["user_id"]


def _visible_or_404(slug):
    """Açık sayfa; kapalıysa sadece sahibine görünür. Kapalı ile olmayan dışarıdan aynı 404."""
    page = _page(query_one("SELECT * FROM booking_pages WHERE slug = ?", (fold(slug),))) if len(slug) <= 64 else None
    if page is None or not (page["enabled"] or _is_owner(page)):
        abort(404)
    return page


def _type_or_404(page, type_id):
    """Sayfadaki aktif tür; pasif ya da başkasının türü dışarıdan aynı 404."""
    btype = query_one("SELECT * FROM booking_types WHERE id = ? AND user_id = ? AND active = 1",
                      (type_id, page["user_id"]))
    if btype is None:
        abort(404)
    return btype


def _booking_or_404(token):
    b = query_one("SELECT * FROM bookings WHERE manage_token = ?", (token,)) if len(token) <= 64 else None
    if b is None:
        abort(404)
    return b


def _require_form_key():
    """Uygulamanın CSRF kontrolü, oturumda da formda da anahtar yoksa isteği geçirir (ilk ziyaret); herkese açık
    formlarda anahtar zorunlu: form açılmadan (ya da başka siteden) doğrudan gönderilen istek reddedilir."""
    if not request.form.get("_csrf"):
        abort(400, "Geçersiz form anahtarı. Sayfayı yenileyip tekrar deneyin.")


def _respond(template, status=200, **ctx):
    resp = make_response(render_template(template, tz_note=tz_note(), locations=LOCATIONS, **ctx), status)
    resp.headers["Content-Security-Policy"] = CSP
    return resp


@bp.after_request
def _public_headers(resp):
    """Herkese açık sayfalar (404 dahil): önbelleğe alınmaz, dizine eklenmez, gömülemez; adres/anahtar başka siteye
    (Referer) sızmaz."""
    if request.endpoint in PUBLIC_ENDPOINTS:
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@bp.route("/r/<slug>")
def public(slug):
    page = _visible_or_404(slug)
    return _respond("booking/public.html", page=page, owner=_is_owner(page),
                    types=get_types(page["user_id"], active_only=True))


@bp.route("/r/<slug>/<int:type_id>")
def slots(slug, type_id):
    page = _visible_or_404(slug)
    btype = _type_or_404(page, type_id)
    now = now_local()
    today = now.date()
    days = availability(page, btype, now)
    window = timedelta(days=WINDOW_DAYS)
    start = parse_date(request.args.get("bas"))
    if start is None:
        start = today
        first_free = next((d for d, free in days if free), None)
        if first_free and first_free >= start + window:   # ilk haftada boş saat yoksa ilk boş güne atla
            start = first_free
    start = min(max(start, today), days[-1][0])
    shown = [(d, free) for d, free in days if start <= d < start + window]
    return _respond("booking/slots.html", page=page, btype=btype, owner=_is_owner(page), today=today,
                    days=[(day_label(d, today), d, free) for d, free in shown], slot_format=SLOT_FORMAT,
                    any_free=any(free for _d, free in days),
                    prev_start=max(start - window, today).isoformat() if start > today else None,
                    next_start=(start + window).isoformat() if days and start + window <= days[-1][0] else None)


@bp.route("/r/<slug>/<int:type_id>/talep", methods=["GET", "POST"])
def request_form(slug, type_id):
    page = _visible_or_404(slug)
    btype = _type_or_404(page, type_id)
    if request.method == "POST":
        _require_form_key()
    raw = request.values.get("t", "")
    try:
        start = datetime.strptime(raw, SLOT_FORMAT)
    except ValueError:
        return redirect(url_for(".slots", slug=page["slug"], type_id=type_id))
    end = start + timedelta(minutes=btype["duration_min"])
    form = {k: request.form.get(k, "") for k in LIMITS}
    ctx = {"page": page, "btype": btype, "owner": _is_owner(page), "slot": raw, "form": form,
           "when": f"{long_date(start)} · {start:%H:%M}–{end:%H:%M}", "error": None, "taken": False}

    def fail(message, status=400, taken=False):
        return _respond("booking/form.html", status, **{**ctx, "error": message, "taken": taken})

    if request.method == "GET":
        if not is_free(page, btype, start, now_local()):
            return fail("Bu saat artık boş değil; başka bir saat seç.", 409, taken=True)
        return _respond("booking/form.html", **ctx)
    if request.form.get("website"):   # bal tuzağı: insanlar görmez, robotlar doldurur
        return fail("Talep gönderilemedi.")
    if not page["enabled"]:
        return fail("Sayfa kapalı: önizlemede talep gönderilemez. Önce sayfayı yayına al.")
    name = " ".join(form_str("name", LIMITS["name"]).split())
    if not name:
        return fail("Adını yaz.")
    try:
        contact = clean_contact(form_str("contact", LIMITS["contact"]), phone_only=btype["location_kind"] == "callback")
    except ValueError as e:
        return fail(str(e))
    note = form_str("note", LIMITS["note"]).replace("\r\n", "\n")
    digest = ip_hash(request.remote_addr)
    if query_one("SELECT COUNT(*) AS n FROM bookings WHERE ip_hash = ? AND created_at >= datetime('now', '-1 hour')",
                 (digest,))["n"] >= IP_LIMIT:
        return fail("Kısa sürede çok fazla talep gönderildi; bir saat sonra tekrar dene.", 429)
    if query_one("SELECT COUNT(*) AS n FROM bookings WHERE user_id = ? AND status = 'pending' AND start_at > ?",
                 (page["user_id"], now_local().strftime(DB_FORMAT)))["n"] >= MAX_PENDING:
        return fail("Şu an onay bekleyen çok fazla talep var; daha sonra tekrar dene.", 429)
    try:
        b = create_booking(page, btype, start, name, contact, note, digest)
    except SlotTaken:
        return fail("Bu saat az önce doldu; başka bir saat seç.", 409, taken=True)
    notify_new(b)
    return redirect(url_for(".manage", token=b["manage_token"], yeni=1), code=303)


def _render_manage(b, status=200, error=None):
    page = query_one("SELECT slug, enabled, title FROM booking_pages WHERE user_id = ?", (b["user_id"],))
    v = view(b)
    return _respond("booking/manage.html", status, b=v, page=page, url=manage_url(b["manage_token"]),
                    new=request.args.get("yeni") == "1", error=error,
                    can_cancel=b["status"] in ACTIVE and v["start"] > now_local())


@bp.route("/r/i/<token>")
def manage(token):
    """Ziyaretçinin bağlantısı: durum, takvime ekle, iptal."""
    return _render_manage(_booking_or_404(token))


@bp.route("/r/i/<token>/iptal", methods=["POST"])
def visitor_cancel(token):
    b = _booking_or_404(token)
    _require_form_key()
    if not (b["status"] in ACTIVE and parse_dt(b["start_at"]) > now_local()):
        return _render_manage(b, 400, "Bu randevu artık iptal edilemez.")
    if not form_bool("confirm"):
        return _render_manage(b, 400, "İptal etmek için “Evet, iptal etmek istiyorum” kutusunu işaretle.")
    ok, _message = decide(b, "cancel", by="visitor")
    if ok:
        b = query_one("SELECT * FROM bookings WHERE id = ?", (b["id"],))
        _notify(b["user_id"], owner_message(b, url_for(".index", _external=True), telegram.escape, status_head(b)))
    return redirect(url_for(".manage", token=token), code=303)


@bp.route("/r/i/<token>/randevu.ics")
def ics(token):
    b = _booking_or_404(token)
    if b["status"] != "confirmed":
        abort(404)
    page = query_one("SELECT title FROM booking_pages WHERE user_id = ?", (b["user_id"],))
    body = build_ics(b, page["title"] if page else "", manage_url(token), datetime.now(timezone.utc))
    resp = make_response(body.encode("utf-8"))
    resp.headers["Content-Type"] = "text/calendar; charset=utf-8"
    resp.headers["Content-Disposition"] = 'attachment; filename="randevu.ics"'
    return resp
