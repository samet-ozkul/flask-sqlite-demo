"""🎉 Davetiye: doğum günü, düğün, yemek... için herkese açık davetiye sayfası ve katılım yanıtı (LCV).

- Yönetim /davetiye/ (giriş gerekli); herkese açık sayfa /d/<kod> (girişsiz, betiksiz, mobil öncelikli): kapak (emoji ya
  da fotoğraf) ve seçilen renk teması, başlık, ev sahibi, tarih-saat, yer + Google Haritalar, açıklama, 📅 Takvimime ekle
  (.ics), LCV son tarihi ve yanıt formu
- Yanıt: ad, Geliyorum / Belki (açıksa) / Gelemiyorum, kişi sayısı ("Geliyorum"da, yanıt başına en fazla N),
  isteğe bağlı özel soru, not. LCV son gününün sonuna (son tarih yoksa etkinlik başlayana) kadar yanıt verilir ve
  değiştirilir. Yanıt verince tarayıcıya 1 yıllık HttpOnly çerez yazılır (sunucuda belirtecin SHA-256'sı): sayfaya dönen
  aynı tarayıcı yanıtını düzenler; başka cihaz için kişisel düzenleme linki (/d/<kod>?y=<belirteç>) gösterilir. Aynı
  isimle ikinci yanıt reddedilir (büyük/küçük ve Türkçe harf duyarsız; veritabanında da tekil)
- Kontenjan (isteğe bağlı): dolunca yeni "Geliyorum" ya da kişi sayısını artırma kabul edilmez; "Belki" ve "Gelemiyorum"
  edilir
- Kişiye özel linkler (/d/<kod>?k=<anahtar>): davetli başına; o linkin yanıtı aynı linkten düzenlenir, yanıt bekleyenler
  listelenir, telefonu olana hazır WhatsApp davet mesajı. "Sadece davetliler" açıksa ortak link yeni yanıt almaz
- Davetliler herkese açık sayfada ayara göre: hiç / sadece sayılar / isimler ("Gelenler: Ahmet (+2), Ayşe")
- "Takvimime ekle": sahibinin takvimine özel (paylaşılmayan, hatırlatmasız) etkinlik; davetiye düzenlenince
  güncellenir, işaret kaldırılınca ya da davetiye silinince kalkar; davetiye çöpten geri getirilince etkinlik de aynı
  id'yle geri gelir
- Telegram: "her yeni yanıtta haber ver" açıksa yeni/değişen yanıtlar en fazla 10 dakikada bir toplu (bekleyenleri cron
  da gönderir); cron /hatirlatma LCV son günü geçince (09:00'dan sonra) bir kez özet, etkinlikten bir gün önce 19:00'dan
  sonra bir kez "Yarın" mesajı (tarih değişince yeniden kurulur)
- Kötüye kullanım: bal tuzağı, davetiye başına IP başına saatte 20 yeni yanıt (IP düz saklanmaz, SECRET_KEY ile HMAC),
  davetiye başına en fazla 500 yanıt, uzunluk sınırları. CSRF anahtarı formu açan ziyaretçinin oturumuna konur
- Olmayan ve silinmiş (çöpteki) kod dışarıdan aynı 404; sahibi kendi davetiyesini önizler (yanıt gönderemez,
  görüntülenme sayılmaz). Link önizleme robotları (WhatsApp, Telegram...) OG etiketlerini görür ama görüntülenme
  sayılmaz; sayfa arama motorlarına ve önbelleğe kapalı
"""
import csv
import hashlib
import hmac
import io
import json
import re
import secrets
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import quote

import segno
from flask import (Blueprint, Response, abort, current_app, flash, g, make_response, redirect, render_template,
                   request, send_file, url_for)
from PIL import Image, ImageOps, UnidentifiedImageError

from .. import quota, telegram, trash
from .. import todo_reminders as todo
from ..auth import login_required
from ..calendar_events import _fold as ics_fold, _ics_escape as ics_escape
from ..db import execute, get_db, owned_or_404, query, query_one
from ..storage import quota_bytes, usage as disk_usage
from ..totp import qr_svg
from ..utils import (MONTHS_TR, TZ, WEEKDAYS_TR, fold, form_bool, form_choice, form_str, is_link_preview, local_dt,
                     parse_date, parse_number)
from .expenses import _csv_safe
from .profile import ACCENTS, PHONE_RE, phone_intl
from .shortlinks import AUTO_ALPHABET

# Yönetim /davetiye altında, herkese açık sayfa /d/ altında: tek blueprint iki ayrı kökte (polls.py gibi)
bp = Blueprint("invites", __name__)

# durum -> (ikon, düğme yazısı)
STATUSES = {"yes": ("✅", "Geliyorum"), "maybe": ("🤔", "Belki"), "no": ("❌", "Gelemiyorum")}
SHOW_GUESTS = {"counts": "Sadece sayılar (14 kişi geliyor · 3 belki)",
               "names": "İsimler de görünsün (Gelenler: Ahmet (+2), Ayşe…)",
               "none": "Hiç görünmesin (yanıtları sadece ben görürüm)"}
DEFAULT_ACCENT = "pink"
DEFAULT_EMOJI = "🎉"
EMOJI_HINTS = "🎉 🎂 🎈 💍 🍽️ 🎓 👶 🏡 🎄"
LIMITS = {"title": 120, "host": 80, "place": 120, "address": 300, "description": 2000, "question": 150,
          "cover_emoji": 16}
NAME_MAX, NOTE_MAX, ANSWER_MAX, LABEL_MAX, PHONE_MAX = 60, 500, 300, 60, 30
COUNT_MAX = 20                       # "yanıt başına en fazla kişi" ayarının üst sınırı
DEFAULT_PER_RESPONSE = 6
CAPACITY_MAX = 5000
MAX_GUESTS = 200                     # davetiye başına kişiye özel link
MAX_RESPONSES = 500                  # davetiye başına yanıt
IP_LIMIT = 20                        # davetiye başına, IP başına saatte yeni yanıt
NOTIFY_EVERY = timedelta(minutes=10)
DEFAULT_LENGTH = timedelta(hours=3)  # bitiş saati yoksa etkinlik bu kadar sürer sayılır
EVE_TIME = "19:00"                   # bir gün önceki "Yarın" mesajı bu saatten sonra
LIST_NAMES = 30                      # Telegram mesajında en fazla isim
COOKIE_DAYS = 365
TOKEN_BYTES = 16                     # düzenleme belirteci: 22 karakter, 128 bit
GUEST_BYTES = 12                     # davetli anahtarı: 16 karakter, 96 bit
CODE_LEN = 7
STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"   # son bildirim: yerel
COVER_SIZE = (1200, 630)             # WhatsApp/Telegram önizlemesinin oranı (1,91:1)
COVER_QUALITY = 80
QR_PNG_SCALE = 20
MAPS_URL = "https://www.google.com/maps/search/?api=1&query="
PUBLIC_ENDPOINTS = ("invites.public", "invites.ics", "invites.cover")
INVALID_GUEST = "Bu kişiye özel link geçersiz ya da iptal edilmiş; davet edenden yeni link iste."
INVALID_EDIT = "Bu yanıt linki geçersiz ya da yanıt silinmiş."
# Telefon numarası satırın sonunda: "Ahmet ve ailesi, 0532 123 45 67" / "Ayşe 05321234567"
GUEST_LINE = re.compile(r"^(.*?)[\s,;:|–-]*(\+?\d[\d\s().-]{5,})$")
# Sayfada betik yok; kullanıcı metni kaçışlansa da ek önlem: dış kaynak ve gömme kapalı, form sadece bu siteye
CSP = ("default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'")


# ---------- Saat ----------
def now_local():
    """Şu an, yerel saat ve saat dilimsiz. todo.now_local testte taklit edilir."""
    return todo.now_local().astimezone(TZ).replace(tzinfo=None)


def starts(inv):
    return datetime.combine(date.fromisoformat(inv["starts_on"]), time.fromisoformat(inv["starts_at"]))


def ends(inv):
    """Bitiş saati başlangıçtan önceyse ertesi gün (gece yarısını geçen düğün); bitiş yoksa başlangıç + 3 saat."""
    start = starts(inv)
    if not inv["ends_at"]:
        return start + DEFAULT_LENGTH
    end = datetime.combine(start.date(), time.fromisoformat(inv["ends_at"]))
    return end if end > start else end + timedelta(days=1)


def rsvp_until(inv):
    """Yanıtların kapandığı an: LCV son gününün sonu (etkinlik başlangıcını geçmez), son tarih yoksa başlangıç."""
    start = starts(inv)
    deadline = parse_date(inv["rsvp_deadline"])
    return min(start, datetime.combine(deadline + timedelta(days=1), time())) if deadline else start


def day_text(d, now=None):
    """'17 Ekim Cumartesi' (bu yıl değilse '17 Ekim 2027 Cumartesi')."""
    year = "" if d.year == (now or now_local()).year else f" {d.year}"
    return f"{d.day} {MONTHS_TR[d.month - 1]}{year} {WEEKDAYS_TR[d.weekday()]}"


def hours_text(inv):
    """'15:00–18:00' ya da '15:00'"""
    return inv["starts_at"] + (f"–{inv['ends_at']}" if inv["ends_at"] else "")


def when_text(inv, now=None):
    """'17 Ekim Cumartesi · 15:00–18:00'"""
    return f"{day_text(parse_date(inv['starts_on']), now)} · {hours_text(inv)}"


def state(inv, now=None):
    """LCV durumu: 'open', 'ended' (etkinlik bitti), 'closed' (elle kapatıldı), 'late' (son gün geçti / başladı)."""
    now = now or now_local()
    if now >= ends(inv):
        return "ended"
    if inv["closed"]:
        return "closed"
    if now >= rsvp_until(inv):
        return "late"
    return "open"


def state_error(inv, st):
    """Yanıt gönderilemeyen durumun metni."""
    if st == "ended":
        return "Bu etkinlik sona erdi; artık yanıt verilemez."
    if st == "closed":
        return "Yanıtlar kapatıldı; artık yanıt verilemez ya da değiştirilemez."
    if inv["rsvp_deadline"]:
        return (f"Yanıt süresi doldu (son gün: {day_text(parse_date(inv['rsvp_deadline']))}); artık yanıt verilemez "
                "ya da değiştirilemez.")
    return "Etkinlik başladı; artık yanıt verilemez ya da değiştirilemez."


# ---------- Kod ve anahtarlar ----------
def _trashed_codes():
    codes = set()
    for row in query("SELECT payload FROM trash WHERE module = 'invites'"):
        codes |= {r.get("code") for r in json.loads(row["payload"])["rows"].get("invites", [])}
    return codes


def new_code():
    """Karışmayan harflerden 7 karakter; çöpteki davetiyenin kodu da verilmez (geri getirilince çakışmasın)."""
    trashed = _trashed_codes()
    for _ in range(50):
        code = "".join(secrets.choice(AUTO_ALPHABET) for _ in range(CODE_LEN))
        if code not in trashed and not query_one("SELECT 1 FROM invites WHERE code = ?", (code,)):
            return code
    raise RuntimeError("Davetiye kodu üretilemedi")


def ip_hash(ip):
    """IP düz saklanmaz: SECRET_KEY ile HMAC (saatlik sınır için aynı IP'yi tanımaya yeter)."""
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(key, f"invite-ip:{ip or ''}".encode(), hashlib.sha256).hexdigest()[:32]


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def cookie_name(code):
    return f"davetiye_{code}"


def public_url(code, **params):
    return url_for("invites.public", code=code, _external=True, **params)


def guest_url(code, token):
    return public_url(code, k=token)


def edit_url(code, token):
    return public_url(code, y=token)


def maps_url(inv):
    """Adres (yoksa yer adı) için Google Haritalar araması."""
    place = inv["address"] or inv["place"]
    return MAPS_URL + quote(place) if place else None


def accent_colors(inv):
    """(açık tema rengi, koyu tema rengi): sayfaya sadece ACCENTS'teki değerler yazılır."""
    return ACCENTS.get(inv["accent"], ACCENTS[DEFAULT_ACCENT])[1:]


def photo_version(inv):
    """Kapak adresindeki ?v= (değişince tarayıcı ve önizleme önbelleği yenilenir)."""
    return re.sub(r"\D", "", inv["updated_at"] or "")


# ---------- Yanıtlar ----------
def plus(count):
    """'Ahmet (+2)': yanıtlayanla birlikte 3 kişi."""
    return f" (+{count - 1})" if count and count > 1 else ""


def tally(invite_id):
    """{'yes': gelen kişi, 'yes_responses', 'maybe', 'no', 'total'}; belki/gelemiyor yanıt sayısıdır."""
    return dict(query_one(
        "SELECT COALESCE(SUM(CASE WHEN status = 'yes' THEN count END), 0) AS yes,"
        " COALESCE(SUM(status = 'yes'), 0) AS yes_responses, COALESCE(SUM(status = 'maybe'), 0) AS maybe,"
        " COALESCE(SUM(status = 'no'), 0) AS no, COUNT(*) AS total FROM invite_responses WHERE invite_id = ?",
        (invite_id,)))


def tally_text(t):
    """'14 kişi geliyor · 3 belki · 2 gelemiyor'; hiç yanıt yoksa 'Henüz yanıt yok'."""
    if not t["total"]:
        return "Henüz yanıt yok"
    parts = [f"{t['yes']} kişi geliyor"]
    if t["maybe"]:
        parts.append(f"{t['maybe']} belki")
    if t["no"]:
        parts.append(f"{t['no']} gelemiyor")
    return " · ".join(parts)


def responses(invite_id):
    """Sahibinin tablosu (en eski önce): kişiye özel linkle verildiyse davetli etiketi de."""
    return query("SELECT r.*, g.label AS guest_label FROM invite_responses r"
                 " LEFT JOIN invite_guests g ON g.id = r.guest_id WHERE r.invite_id = ? ORDER BY r.id", (invite_id,))


def whatsapp_url(text, phone=""):
    """wa.me bağlantısı; numara yoksa WhatsApp kişi seçtirir. Metin UTF-8 yüzde kodlamasıyla (Türkçe harf, emoji, &)."""
    intl = phone_intl(phone) if phone else None
    return f"https://wa.me/{intl or ''}?text={quote(text, safe='')}"


def guest_message(inv, label, url):
    """'Merhaba Ahmet, “Elif'in 5. yaş günü” için davetlisin 🎉 17 Ekim Cumartesi · 15:00–18:00 · Çocuk Kafe.
    Katılıp katılamayacağını buradan yazar mısın? <link>' ("Ahmet ve ailesi" -> Ahmet)."""
    name = label.split()[0] if label.split() else ""
    hello = f"Merhaba {name}," if name else "Merhaba,"
    where = f" · {inv['place']}" if inv["place"] else ""
    return (f"{hello} “{inv['title']}” için davetlisin 🎉 {when_text(inv)}{where}. "
            f"Katılıp katılamayacağını buradan yazar mısın? {url}")


def share_message(inv, url):
    """Ortak link için hazır metin (grup mesajı)."""
    lines = [f"{inv['cover_emoji'] or DEFAULT_EMOJI} {inv['title']}", f"📅 {when_text(inv)}"]
    if inv["place"]:
        lines.append(f"📍 {inv['place']}")
    lines.append(f"Katılıp katılamayacağını buradan yazar mısın? {url}")
    return "\n".join(lines)


def guest_rows(inv):
    """Kişiye özel linkler: yanıt durumu, link, WhatsApp davet mesajı (telefon varsa doğrudan o numaraya)."""
    out = []
    for r in query("SELECT g.*, r.id AS response_id, r.status, r.count FROM invite_guests g"
                   " LEFT JOIN invite_responses r ON r.guest_id = g.id WHERE g.invite_id = ? ORDER BY g.id",
                   (inv["id"],)):
        url = guest_url(inv["code"], r["token"])
        out.append({**dict(r), "url": url, "has_phone": bool(phone_intl(r["phone"])),
                    "whatsapp": whatsapp_url(guest_message(inv, r["label"], url), r["phone"])})
    return out


def parse_guest_line(line):
    """'Ahmet ve ailesi, 0532 123 45 67' -> ('Ahmet ve ailesi', '0532 123 45 67'); telefon yoksa ('…', '')."""
    line = " ".join(line.split())
    m = GUEST_LINE.match(line)
    if m:
        label, phone = m.group(1).strip(" ,;:|–-"), m.group(2).strip()
        if label and PHONE_RE.match(phone) and 7 <= sum(ch.isdigit() for ch in phone) <= 15:
            return label[:LABEL_MAX], phone[:PHONE_MAX]
    return line[:LABEL_MAX], ""


# ---------- Telegram ----------
def _chat_of(user_id):
    owner = query_one("SELECT telegram_chat_id FROM users WHERE id = ?", (user_id,))
    return owner["telegram_chat_id"] if owner and owner["telegram_chat_id"] and telegram.enabled() else None


def coming_text(t):
    """'14 kişi geliyor, 3 belki'"""
    return f"{t['yes']} kişi geliyor" + (f", {t['maybe']} belki" if t["maybe"] else "")


def names_list(rows, escape, limit=LIST_NAMES):
    """'Ahmet (+2), Ayşe, Can' (fazlası '… ve 5 yanıt daha')."""
    text = ", ".join(escape(r["name"]) + plus(r["count"]) for r in rows[:limit])
    return text + (f" … ve {len(rows) - limit} yanıt daha" if len(rows) > limit else "")


def change_line(r, old_rev, escape):
    what = {"yes": f"geliyor{plus(r['count'])}", "maybe": "belki gelir", "no": "gelemiyor"}[r["status"]]
    if r["created_rev"] <= old_rev:   # önceki mesajda vardı: yanıtını değiştirdi
        return f"{escape(r['name'])} yanıtını değiştirdi: {what}"
    return f"{escape(r['name'])} {what}"


def changes_message(inv, changed, old_rev, url, escape):
    """'🎉 “Elif'in 5. yaş günü”: Ahmet geliyor (+2), Ayşe gelemiyor · toplam 14 kişi geliyor, 3 belki'"""
    t = tally(inv["id"])
    lines = ", ".join(change_line(r, old_rev, escape) for r in changed[:LIST_NAMES])
    more = f" ve {len(changed) - LIST_NAMES} yanıt daha" if len(changed) > LIST_NAMES else ""
    return "\n".join([f"🎉 <b>“{escape(inv['title'])}”</b>: {lines}{more} · toplam {coming_text(t)}",
                      f'<a href="{escape(url)}">Yanıtlar →</a>'])


def claim_changes(inv, now):
    """Bildirilmemiş değişiklik varsa ve son bildirimden 10 dakika geçtiyse bildirim hakkını alır: (önceki, yeni) sayaç
    ya da None. Hak tek sorguda alınır: aynı anda gelen iki yanıt (ya da yanıt ile cron) iki mesaj üretmesin."""
    db = get_db()
    row = db.execute("SELECT rev, notified_rev FROM invites WHERE id = ?", (inv["id"],)).fetchone()
    if row is None or row["rev"] <= row["notified_rev"]:
        return None
    claimed = db.execute(
        "UPDATE invites SET notified_at = ?, notified_rev = ? WHERE id = ? AND notified_rev = ?"
        " AND (notified_at IS NULL OR notified_at <= ?)",
        (now.strftime(STAMP_FORMAT), row["rev"], inv["id"], row["notified_rev"],
         (now - NOTIFY_EVERY).strftime(STAMP_FORMAT))).rowcount == 1
    db.commit()
    return (row["notified_rev"], row["rev"]) if claimed else None


def send_changes(inv, chat_id, now):
    """Sıra geldiyse yeni/değişen yanıtları tek mesajla gönderir; gönderdiyse True (TelegramError yukarı çıkar)."""
    claimed = claim_changes(inv, now)
    if not claimed:
        return False
    changed = query("SELECT * FROM invite_responses WHERE invite_id = ? AND rev > ? AND rev <= ? ORDER BY rev",
                    (inv["id"], *claimed))
    if not changed:   # değişen yanıtlar bu arada silindi
        return False
    telegram.send_message(chat_id, changes_message(inv, changed, claimed[0], url_for(
        "invites.detail", invite_id=inv["id"], _external=True), telegram.escape))
    return True


def notify_changes(invite_id):
    """Yanıttan sonra: "haber ver" açıksa ve sıra geldiyse sahibine toplu mesaj; hata sayfayı bozmaz."""
    inv = query_one("SELECT * FROM invites WHERE id = ?", (invite_id,))
    chat_id = _chat_of(inv["user_id"]) if inv and inv["notify"] else None
    if not chat_id:
        return
    try:
        send_changes(inv, chat_id, now_local())   # 10 dakika dolmadıysa bu yanıt sonraki mesaja (ya da cron'a) kalır
    except telegram.TelegramError as e:
        current_app.logger.warning("Davetiye bildirimi gönderilemedi (davetiye %s): %s", invite_id, e)
    except Exception:  # bildirim yan etkidir; yanıt yine kaydedilmiş olmalı
        current_app.logger.exception("Davetiye bildirimi hatası")


def pending_notices(now):
    """Cron: "haber ver" açık, bildirilmemiş değişikliği olan, son mesajdan 10 dakika geçmiş davetiyeler."""
    return query(
        "SELECT i.*, u.telegram_chat_id AS chat_id FROM invites i JOIN users u ON u.id = i.user_id"
        " WHERE i.notify = 1 AND u.telegram_chat_id IS NOT NULL AND i.rev > i.notified_rev"
        " AND (i.notified_at IS NULL OR i.notified_at <= ?)", ((now - NOTIFY_EVERY).strftime(STAMP_FORMAT),))


def pending_summaries(now):
    """Cron: [(tür, davetiye)] — 'deadline': LCV son günü geçti, varsayılan saatten (09:00) sonra bir kez (etkinlik de
    bittiyse gönderilmeden işaretlenir); 'eve': etkinlikten bir gün önce EVE_TIME'dan sonra bir kez. Son tarih ya da
    etkinlik günü değişince yeniden kurulur. Sadece Telegram'ı bağlı sahiplerin davetiyeleri."""
    today = now.date().isoformat()
    tomorrow = (now.date() + timedelta(days=1)).isoformat()
    hm = now.strftime("%H:%M")
    out = []
    for r in query(
            "SELECT i.*, u.telegram_chat_id AS chat_id FROM invites i JOIN users u ON u.id = i.user_id"
            " WHERE u.telegram_chat_id IS NOT NULL"
            " AND ((i.rsvp_deadline < ? AND i.deadline_sent_for IS NOT i.rsvp_deadline)"
            " OR (i.starts_on = ? AND i.eve_sent_for IS NOT i.starts_on))", (today, tomorrow)):
        if r["rsvp_deadline"] and r["rsvp_deadline"] < today and r["deadline_sent_for"] != r["rsvp_deadline"]:
            if now >= ends(r):
                mark_sent(r, "deadline")   # cron uzun süre çalışmadı: bitmiş etkinliğin özeti gönderilmez
            elif hm >= todo.DEFAULT_DUE_TIME:
                out.append(("deadline", r))
        if r["starts_on"] == tomorrow and r["eve_sent_for"] != r["starts_on"] and hm >= EVE_TIME:
            out.append(("eve", r))
    return out


def mark_sent(inv, kind):
    if kind == "deadline":
        execute("UPDATE invites SET deadline_sent_for = ? WHERE id = ?", (inv["rsvp_deadline"], inv["id"]))
    else:
        execute("UPDATE invites SET eve_sent_for = ? WHERE id = ?", (inv["starts_on"], inv["id"]))


def summary_message(kind, inv, url, escape):
    """'deadline': LCV süresi doldu özeti; 'eve': '🎉 Yarın: “…” · 15:00–18:00 · Çocuk Kafe · 14 kişi geliyor, 3 belki'
    + isim listeleri ve yanıt vermeyen davetliler."""
    t = tally(inv["id"])
    rows = responses(inv["id"])
    title = escape(inv["title"])
    if kind == "eve":
        place = f" · {escape(inv['place'])}" if inv["place"] else ""
        lines = [f"{escape(inv['cover_emoji'] or DEFAULT_EMOJI)} <b>Yarın:</b> “{title}” · {hours_text(inv)}{place}"
                 f" · {coming_text(t)}"]
    else:
        no = f", {t['no']} gelemiyor" if t["no"] else ""
        lines = [f"📋 <b>LCV süresi doldu:</b> “{title}” · {coming_text(t)}{no}", f"📅 {when_text(inv)}"]
    for status, label in (("yes", "✅ Gelenler"), ("maybe", "🤔 Belki"), ("no", "❌ Gelemeyenler")):
        group = [r for r in rows if r["status"] == status]
        if group:
            lines.append(f"{label}: {names_list(group, escape)}")
    if not rows:
        lines.append("Henüz yanıt yok.")
    waiting = [r["label"] for r in query(
        "SELECT g.label FROM invite_guests g WHERE g.invite_id = ? AND NOT EXISTS"
        " (SELECT 1 FROM invite_responses r WHERE r.guest_id = g.id) ORDER BY g.id", (inv["id"],))]
    if waiting:
        lines.append("⏳ Yanıt vermeyen davetliler: " + ", ".join(escape(x) for x in waiting[:LIST_NAMES])
                     + (f" … ve {len(waiting) - LIST_NAMES} davetli daha" if len(waiting) > LIST_NAMES else ""))
    lines.append(f'<a href="{escape(url)}">Yanıtlar →</a>')
    return "\n".join(lines)


# ---------- Takvim ----------
def _event_fields(inv):
    """(başlık, gün, saat, yer, not) — takvimdeki özel etkinlik."""
    lines = []
    if inv["host"]:
        lines.append(f"Ev sahibi: {inv['host']}")
    if inv["ends_at"]:
        lines.append(f"Saat: {hours_text(inv)}")
    if inv["address"]:
        lines.append(f"Adres: {inv['address']}")
    lines.append(f"Davetiye: {public_url(inv['code'])}")
    return (f"{inv['cover_emoji'] or DEFAULT_EMOJI} {inv['title']}"[:150], inv["starts_on"], inv["starts_at"],
            (inv["place"] or inv["address"])[:150], "\n".join(lines)[:1000])


def sync_event(db, inv, wanted):
    """Takvimdeki özel etkinliği davetiyeyle eşitler (commit etmez); etkinlik id'si döner, istenmiyorsa siler, None.
    Etkinlik takvimden elle silindiyse yeniden eklenir; elle verilen paylaşım ve hatırlatma korunur."""
    current = db.execute("SELECT * FROM events WHERE id = ? AND user_id = ?",
                         (inv["event_id"], inv["user_id"])).fetchone() if inv["event_id"] else None
    if not wanted:
        if current:
            db.execute("DELETE FROM events WHERE id = ?", (current["id"],))
        return None
    title, day, at, place, note = _event_fields(inv)
    if current:
        moved = (day, at) != (current["date"], current["time"])
        db.execute("UPDATE events SET title = ?, date = ?, time = ?, place = ?, note = ?"
                   + (", pre_sent_at = NULL, due_sent_at = NULL" if moved else "") + " WHERE id = ?",
                   (title, day, at, place, note, current["id"]))
        return current["id"]
    return db.execute("INSERT INTO events (user_id, title, date, time, place, note, shared, remind_before)"
                      " VALUES (?, ?, ?, ?, ?, ?, 0, NULL)", (inv["user_id"], title, day, at, place, note)).lastrowid


def calendar_event(inv):
    """Takvimdeki etkinlik (elle silindiyse None)."""
    if not inv["event_id"]:
        return None
    return query_one("SELECT id FROM events WHERE id = ? AND user_id = ?", (inv["event_id"], inv["user_id"]))


# ---------- .ics ----------
def build_ics(inv, url, now_utc):
    """Konuğun takvimine tek etkinlik (UTC saatlerle, 2 saat önce uyarı)."""
    def utc(dt):
        return dt.replace(tzinfo=TZ).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    location = ", ".join(x for x in (inv["place"], inv["address"]) if x)
    description = "\n".join(x for x in (inv["description"], f"Ev sahibi: {inv['host']}" if inv["host"] else "", url)
                            if x)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Kisisel Pano//TR", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "BEGIN:VEVENT", f"UID:invite-{inv['id']}@kisisel-pano", f"DTSTAMP:{now_utc.strftime('%Y%m%dT%H%M%SZ')}",
             f"DTSTART:{utc(starts(inv))}", f"DTEND:{utc(ends(inv))}", f"SUMMARY:{ics_escape(inv['title'])}",
             *([f"LOCATION:{ics_escape(location)}"] if location else []), f"DESCRIPTION:{ics_escape(description)}",
             f"URL:{url}", "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{ics_escape(inv['title'])}",
             "TRIGGER:-PT2H", "END:VALARM", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(ics_fold(line) for line in lines) + "\r\n"


# ---------- Kapak fotoğrafı ----------
def process_cover(file_storage, user_id, old_bytes=0):
    """1,91:1 oranında ortadan (biraz yukarıdan: yüzler kesilmesin) kırpılır, en fazla 1200×630 JPEG'e çevrilir
    (EXIF/GPS yazılmaz); bayt döner. Geçersizse ya da kota yetmiyorsa ValueError (QuotaError da ValueError)."""
    data = file_storage.read()
    if not data:
        raise ValueError("Fotoğraf dosyası boş.")
    quota.check_file(user_id, len(data), "Kapak fotoğrafı")
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))  # telefon fotoğrafı yan durmasın
        if img.mode != "RGB":
            rgba = img.convert("RGBA")
            img = Image.new("RGB", img.size, "white")
            img.paste(rgba, mask=rgba.split()[-1])
        width, height = COVER_SIZE
        scale = min(1, img.width / width, img.height / height)   # küçük fotoğraf büyütülmez
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        buf = io.BytesIO()
        ImageOps.fit(img, size, centering=(0.5, 0.4)).save(buf, "JPEG", quality=COVER_QUALITY, optimize=True,
                                                            progressive=True)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("Fotoğraf okunamadı; JPG, PNG ya da WEBP yükle.")
    out = buf.getvalue()
    quota.check_space(user_id, len(out) - old_bytes)   # eski kapağın yerine geçer
    if disk_usage()["used"] + len(out) > quota_bytes():
        raise ValueError("Depolama kotası doldu; fotoğraf kaydedilemedi.")
    return out


# ---------- Oluştur / düzenle ----------
COLUMNS = ("title", "host", "starts_on", "starts_at", "ends_at", "place", "address", "description", "cover_emoji",
           "accent", "rsvp_deadline", "allow_maybe", "ask_count", "max_per_response", "capacity", "question",
           "show_guests", "invite_only", "notify")
CHECKS = ("allow_maybe", "ask_count", "invite_only", "notify", "calendar")


def _int(text):
    n = parse_number(text)
    return int(n) if n is not None and n == int(n) else None


def _raw_form():
    """Formdaki ham değerler (hata sonrası yazılanlar kaybolmasın)."""
    f = request.form
    return {**{k: form_str(k, n) for k, n in LIMITS.items()},
            **{k: f.get(k, "").strip()[:10] for k in ("starts_on", "rsvp_deadline")},
            **{k: f.get(k, "").strip()[:5] for k in ("starts_at", "ends_at")},
            **{k: f.get(k, "").strip()[:6] for k in ("max_per_response", "capacity")},
            "accent": form_choice("accent", ACCENTS, DEFAULT_ACCENT),
            "show_guests": form_choice("show_guests", SHOW_GUESTS, "counts"),
            **{k: form_bool(k) for k in CHECKS}}


def _values_from(inv):
    """Kayıttaki davetiye -> form değerleri."""
    return {**{k: inv[k] if inv[k] is not None else "" for k in COLUMNS},
            "max_per_response": str(inv["max_per_response"]), "capacity": str(inv["capacity"] or ""),
            "calendar": 1 if calendar_event(inv) else 0}


def parse_invite(raw, inv=None):
    """Ham form -> kayıt değerleri; hatalıysa ValueError. Geçmiş tarih ve LCV son tarihi sadece değiştirildiyse
    reddedilir (geçmiş davetiyenin başka alanı düzeltilebilsin)."""
    v = {k: " ".join(raw[k].split()) for k in ("title", "host", "place", "address", "question", "cover_emoji")}
    v["description"] = raw["description"].replace("\r\n", "\n")
    v.update({k: raw[k] for k in ("accent", "show_guests", "allow_maybe", "ask_count", "invite_only", "notify")})
    if not v["title"]:
        raise ValueError("Başlığı yaz (ör. Elif'in 5. yaş günü).")
    v["cover_emoji"] = v["cover_emoji"] or DEFAULT_EMOJI
    today = now_local().date()
    day = parse_date(raw["starts_on"])
    if day is None:
        raise ValueError("Etkinliğin tarihini seç.")
    v["starts_on"] = day.isoformat()
    if day < today and (inv is None or v["starts_on"] != inv["starts_on"]):
        raise ValueError("Etkinlik tarihi geçmişte olamaz.")
    v["starts_at"] = todo.parse_time(raw["starts_at"])
    if v["starts_at"] is None:
        raise ValueError("Başlangıç saatini yaz (ör. 15:00).")
    v["ends_at"] = None
    if raw["ends_at"]:
        v["ends_at"] = todo.parse_time(raw["ends_at"])
        if v["ends_at"] is None:
            raise ValueError("Bitiş saati geçersiz (ör. 18:00); boş da bırakabilirsin.")
        if v["ends_at"] == v["starts_at"]:
            raise ValueError("Bitiş saati başlangıçla aynı olamaz; boş da bırakabilirsin.")
    v["rsvp_deadline"] = None
    if raw["rsvp_deadline"]:
        deadline = parse_date(raw["rsvp_deadline"])
        if deadline is None:
            raise ValueError("LCV son tarihi geçersiz.")
        if deadline > day:
            raise ValueError("LCV son tarihi etkinlik gününden sonra olamaz.")
        v["rsvp_deadline"] = deadline.isoformat()
        if deadline < today and (inv is None or v["rsvp_deadline"] != inv["rsvp_deadline"]):
            raise ValueError("LCV son tarihi geçmişte olamaz.")
    n = _int(raw["max_per_response"]) if raw["max_per_response"] else DEFAULT_PER_RESPONSE
    if n is None or not 1 <= n <= COUNT_MAX:
        if v["ask_count"]:
            raise ValueError(f"“Yanıt başına en fazla kişi” 1 ile {COUNT_MAX} arasında olmalı.")
        n = DEFAULT_PER_RESPONSE   # kişi sayısı sorulmuyorsa önemsiz
    v["max_per_response"] = n
    v["capacity"] = None
    if raw["capacity"]:
        v["capacity"] = _int(raw["capacity"])
        if v["capacity"] is None or not 1 <= v["capacity"] <= CAPACITY_MAX:
            raise ValueError(f"Kontenjan 1 ile {CAPACITY_MAX} arasında bir sayı olmalı (boş bırakırsan sınırsız).")
    return v


def _photo_upload(user_id, old_bytes=0):
    upload = request.files.get("photo")
    if not (upload and upload.filename):
        return None
    return process_cover(upload, user_id, old_bytes)


def _render_form(inv, values, status=200):
    return render_template(
        "invites/form.html", inv=inv, f=values, accents=ACCENTS, show_guests=SHOW_GUESTS, limits=LIMITS,
        emoji_hints=EMOJI_HINTS, count_max=COUNT_MAX, capacity_max=CAPACITY_MAX, today=now_local().date().isoformat(),
        photo_v=photo_version(inv) if inv else "",
        telegram_ready=telegram.enabled() and bool(g.user["telegram_chat_id"])), status


def _defaults():
    return {"title": "", "host": g.user["display_name"] or "", "starts_on": "", "starts_at": "", "ends_at": "",
            "place": "", "address": "", "description": "", "cover_emoji": DEFAULT_EMOJI, "accent": DEFAULT_ACCENT,
            "rsvp_deadline": "", "allow_maybe": 1, "ask_count": 1, "max_per_response": str(DEFAULT_PER_RESPONSE),
            "capacity": "", "question": "", "show_guests": "counts", "invite_only": 0, "calendar": 1,
            "notify": 1 if g.user["telegram_chat_id"] else 0}


# ---------- Yönetim ----------
@bp.route("/davetiye/")
@login_required
def index():
    now = now_local()
    rows = [dict(r) for r in query(
        "SELECT i.id, i.code, i.title, i.cover_emoji, i.starts_on, i.starts_at, i.ends_at, i.place, i.closed,"
        " i.invite_only, i.rsvp_deadline, i.capacity,"
        " COALESCE(SUM(CASE WHEN r.status = 'yes' THEN r.count END), 0) AS yes, COALESCE(SUM(r.status = 'maybe'), 0)"
        " AS maybe, COALESCE(SUM(r.status = 'no'), 0) AS no, COUNT(r.id) AS total FROM invites i"
        " LEFT JOIN invite_responses r ON r.invite_id = i.id WHERE i.user_id = ? GROUP BY i.id"
        " ORDER BY i.starts_on, i.starts_at, i.id", (g.user["id"],))]
    for r in rows:
        r["state"], r["summary"], r["when"] = state(r, now), tally_text(r), when_text(r, now)
    upcoming = [r for r in rows if r["state"] != "ended"]
    past = [r for r in reversed(rows) if r["state"] == "ended"]
    return render_template("invites/index.html", upcoming=upcoming, past=past, public_url=public_url)


@bp.route("/davetiye/yeni", methods=["GET", "POST"])
@login_required
def create():
    uid = g.user["id"]
    if request.method == "GET":
        return _render_form(None, _defaults())
    raw = _raw_form()
    try:
        v = parse_invite(raw)
        photo = _photo_upload(uid)
    except ValueError as e:
        flash(str(e), "error")
        return _render_form(None, raw, 400)
    db = get_db()
    invite_id = db.execute(
        f"INSERT INTO invites (user_id, code, {', '.join(COLUMNS)}, photo) VALUES (?, ?{', ?' * len(COLUMNS)}, ?)",
        (uid, new_code(), *[v[k] for k in COLUMNS], photo)).lastrowid
    if raw["calendar"]:
        inv = db.execute("SELECT * FROM invites WHERE id = ?", (invite_id,)).fetchone()
        db.execute("UPDATE invites SET event_id = ? WHERE id = ?", (sync_event(db, inv, True), invite_id))
    db.commit()
    flash("🎉 Davetiye hazır: linki paylaş" + (" ve kişiye özel linkleri aşağıdan oluştur." if v["invite_only"] else ".")
          + (" Takvimine de eklendi." if raw["calendar"] else ""), "success")
    return redirect(url_for(".detail", invite_id=invite_id))


@bp.route("/davetiye/<int:invite_id>/duzenle", methods=["GET", "POST"])
@login_required
def edit(invite_id):
    uid = g.user["id"]
    inv = owned_or_404("invites", invite_id, uid)
    if request.method == "GET":
        return _render_form(inv, _values_from(inv))
    raw = _raw_form()
    try:
        v = parse_invite(raw, inv)
        photo = _photo_upload(uid, len(inv["photo"]) if inv["photo"] else 0)
    except ValueError as e:
        flash(str(e), "error")
        return _render_form(inv, raw, 400)
    db = get_db()
    # "Haber ver" yeni açıldıysa eski yanıtlar "yeni" sayılmaz
    db.execute(
        f"UPDATE invites SET {', '.join(f'{k} = ?' for k in COLUMNS)},"
        " notified_rev = CASE WHEN ? = 1 AND notify = 0 THEN rev ELSE notified_rev END,"
        " updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
        (*[v[k] for k in COLUMNS], v["notify"], invite_id, uid))
    if photo:
        db.execute("UPDATE invites SET photo = ? WHERE id = ?", (photo, invite_id))
    elif form_bool("remove_photo"):
        db.execute("UPDATE invites SET photo = NULL WHERE id = ?", (invite_id,))
    inv = db.execute("SELECT * FROM invites WHERE id = ?", (invite_id,)).fetchone()
    db.execute("UPDATE invites SET event_id = ? WHERE id = ?", (sync_event(db, inv, raw["calendar"]), invite_id))
    db.commit()
    message = "🎉 Davetiye kaydedildi."
    if state(inv) == "ended":
        message += " Etkinlik zamanı geçmiş; davetiye sayfası “sona erdi” gösterir."
    elif inv["closed"]:
        message += " Yanıtlar kapalı; açmak için “▶️ Yanıtları aç”."
    flash(message, "success")
    return redirect(url_for(".detail", invite_id=invite_id))


@bp.route("/davetiye/<int:invite_id>")
@login_required
def detail(invite_id):
    inv = owned_or_404("invites", invite_id, g.user["id"])
    now = now_local()
    url = public_url(inv["code"])
    t = tally(invite_id)
    guests = guest_rows(inv)
    deadline = parse_date(inv["rsvp_deadline"])
    return render_template(
        "invites/detail.html", inv=inv, t=t, summary=tally_text(t), rows=responses(invite_id), guests=guests,
        waiting=[x for x in guests if not x["response_id"]], url=url, qr=qr_svg(url), state=state(inv, now),
        when=when_text(inv, now), deadline=day_text(deadline, now) if deadline else None, statuses=STATUSES,
        show_guests=SHOW_GUESTS, event=calendar_event(inv), maps=maps_url(inv), max_guests=MAX_GUESTS,
        share=whatsapp_url(share_message(inv, url)), photo_v=photo_version(inv),
        telegram_ready=telegram.enabled() and bool(g.user["telegram_chat_id"]))


@bp.route("/davetiye/<int:invite_id>/durum", methods=["POST"])
@login_required
def toggle(invite_id):
    inv = owned_or_404("invites", invite_id, g.user["id"])
    execute("UPDATE invites SET closed = ? WHERE id = ? AND user_id = ?", (0 if inv["closed"] else 1, invite_id,
                                                                            g.user["id"]))
    if inv["closed"]:
        late = state({**dict(inv), "closed": 0}) != "open"
        flash("▶️ Yanıtlar açıldı." + (" Ama LCV süresi doldu ya da etkinlik başladı; yanıt almak için son tarihi "
                                      "düzenle." if late else ""), "success")
    else:
        flash("⏸️ Yanıtlar kapatıldı: yeni yanıt verilemez, verilenler değiştirilemez. Davetiye görünmeye devam eder.",
              "success")
    return redirect(url_for(".detail", invite_id=invite_id))


@bp.route("/davetiye/<int:invite_id>/sil", methods=["POST"])
@login_required
def delete(invite_id):
    uid = g.user["id"]
    inv = owned_or_404("invites", invite_id, uid)
    # Sıra geri getirmede de korunur: davetliler yanıtlardan önce; takvim etkinliği de kutuya girer ve takvimden kalkar
    trash.move(uid, "invites", f"{inv['cover_emoji'] or DEFAULT_EMOJI} {inv['title'][:100]}", ("invites", invite_id),
               children=[("invite_guests", "invite_id = ?"), ("invite_responses", "invite_id = ?"),
                         ("events", "id = (SELECT e.id FROM events e JOIN invites i ON i.event_id = e.id"
                                    " AND i.user_id = e.user_id WHERE i.id = ?)")])
    if inv["event_id"]:
        execute("DELETE FROM events WHERE id = ? AND user_id = ?", (inv["event_id"], uid))
    flash(trash.notice("Davetiye"), "success")
    return redirect(url_for(".index"))


@bp.route("/davetiye/<int:invite_id>/yanit/<int:response_id>/sil", methods=["POST"])
@login_required
def response_delete(invite_id, response_id):
    """Tek yanıt kalıcı silinir (spam ya da yanlış yanıt); kişiye özel linkle verildiyse o link yeniden yanıt verir."""
    owned_or_404("invites", invite_id, g.user["id"])
    r = query_one("SELECT * FROM invite_responses WHERE id = ? AND invite_id = ?", (response_id, invite_id))
    if r is None:
        abort(404)
    execute("DELETE FROM invite_responses WHERE id = ?", (response_id,))
    again = " Kişiye özel linki yeniden yanıt verebilir." if r["guest_id"] else ""
    flash(f"🗑️ “{r['name']}” yanıtı silindi.{again}", "success")
    return redirect(url_for(".detail", invite_id=invite_id, _anchor="yanitlar"))


@bp.route("/davetiye/<int:invite_id>/davetli", methods=["POST"])
@login_required
def guest_create(invite_id):
    inv = owned_or_404("invites", invite_id, g.user["id"])
    lines = [parse_guest_line(x) for x in request.form.get("guests", "").splitlines()[:MAX_GUESTS + 1] if x.strip()]
    have = query_one("SELECT COUNT(*) AS n FROM invite_guests WHERE invite_id = ?", (invite_id,))["n"]
    if not lines:
        flash("Her satıra bir davetli yaz (ör. Ahmet ve ailesi, 0532 123 45 67).", "error")
    elif have + len(lines) > MAX_GUESTS:
        flash(f"Bir davetiyede en fazla {MAX_GUESTS} kişiye özel link olabilir (şu an {have}).", "error")
    else:
        db = get_db()
        db.executemany("INSERT INTO invite_guests (invite_id, label, phone, token) VALUES (?, ?, ?, ?)",
                       [(invite_id, label, phone, secrets.token_urlsafe(GUEST_BYTES)) for label, phone in lines])
        db.commit()
        flash(f"🔑 {len(lines)} kişiye özel link hazır; her davetli kendi linkinden yanıt verir ve değiştirir." +
              ("" if inv["invite_only"] else " “Sadece davetliler” kapalı: ortak link de yanıt almaya devam ediyor."),
              "success")
    return redirect(url_for(".detail", invite_id=invite_id, _anchor="davetliler"))


@bp.route("/davetiye/<int:invite_id>/davetli/<int:guest_id>/sil", methods=["POST"])
@login_required
def guest_delete(invite_id, guest_id):
    owned_or_404("invites", invite_id, g.user["id"])
    guest = query_one("SELECT * FROM invite_guests WHERE id = ? AND invite_id = ?", (guest_id, invite_id))
    if guest is None:
        abort(404)
    if query_one("SELECT 1 FROM invite_responses WHERE guest_id = ?", (guest_id,)):
        flash("Bu davetli yanıt verdi; link iptal edilemez. Önce yanıtını sil.", "warning")
    else:
        execute("DELETE FROM invite_guests WHERE id = ?", (guest_id,))
        flash(f"“{guest['label']}” linki iptal edildi: artık yanıt veremez.", "success")
    return redirect(url_for(".detail", invite_id=invite_id, _anchor="davetliler"))


@bp.route("/davetiye/<int:invite_id>/yanitlar.csv")
@login_required
def export(invite_id):
    """Excel'de doğrudan açılan CSV (UTF-8 BOM, ';'): yanıt başına satır, sonda toplam."""
    inv = owned_or_404("invites", invite_id, g.user["id"])
    rows = responses(invite_id)
    t = tally(invite_id)
    question = bool(inv["question"]) or any(r["answer"] for r in rows)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(["Zaman", "İsim", "Durum", "Kişi", "Not", *([_csv_safe(inv["question"] or "Cevap")] if question
                                                              else []), "Davetli"])
    for r in rows:
        writer.writerow([local_dt(r["updated_at"]), _csv_safe(r["name"]), STATUSES[r["status"]][1],
                         r["count"] if r["status"] == "yes" else "", _csv_safe(r["note"]),
                         *([_csv_safe(r["answer"])] if question else []), _csv_safe(r["guest_label"] or "")])
    writer.writerow(["Toplam", f"{t['total']} yanıt", tally_text(t), t["yes"]])
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="davetiye-{inv["code"]}.csv"'})


@bp.route("/davetiye/<int:invite_id>/qr.png")
@login_required
def qr_png(invite_id):
    """Basılı davetiye, afiş, masa kartı için büyük QR."""
    inv = owned_or_404("invites", invite_id, g.user["id"])
    buf = io.BytesIO()
    segno.make(public_url(inv["code"]), error="m").save(buf, kind="png", scale=QR_PNG_SCALE, border=4)
    buf.seek(0)
    return send_file(buf, mimetype="image/png", as_attachment=True, download_name=f"davetiye-{inv['code']}-qr.png")


# ---------- Herkese açık sayfa ----------
def _invite_or_404(code):
    """Olmayan ve silinmiş (çöpteki) davetiye dışarıdan aynı 404; adreste büyük/küçük harf fark etmez."""
    inv = query_one("SELECT * FROM invites WHERE code = ?", (fold(code),)) if len(code) <= 32 else None
    if inv is None:
        abort(404)
    return inv


def _is_owner(inv):
    return g.user is not None and g.user["id"] == inv["user_id"]


def _guest(inv, token):
    if not token or len(token) > 64:
        return None
    return query_one("SELECT * FROM invite_guests WHERE invite_id = ? AND token = ?", (inv["id"], token))


def _by_token(inv, token):
    if not token or len(token) > 64:
        return None
    return query_one("SELECT * FROM invite_responses WHERE invite_id = ? AND edit_token_hash = ?",
                     (inv["id"], token_hash(token)))


def _whose(inv, k, y):
    """(davetli, yanıt, belirteç, hata): kişiye özel linkte o linkin yanıtı, kişisel düzenleme linkinde o belirtecin,
    yoksa bu tarayıcının (çerez) yanıtı. belirteç: kişisel düzenleme linkini göstermek için (biliniyorsa)."""
    if k:
        guest = _guest(inv, k)
        if guest is None:
            return None, None, None, INVALID_GUEST
        return guest, query_one("SELECT * FROM invite_responses WHERE guest_id = ?", (guest["id"],)), None, None
    if y:
        response = _by_token(inv, y)
        return (None, response, y, None) if response else (None, None, None, INVALID_EDIT)
    cookie = request.cookies.get(cookie_name(inv["code"]), "")
    response = _by_token(inv, cookie)
    return None, response, cookie if response else None, None


def _render_public(inv, k="", y="", status=200, error=None, form=None, notice=False):
    now = now_local()
    owner = _is_owner(inv)
    guest, response, token, link_error = _whose(inv, k, y)
    if link_error:
        error, status = error or link_error, 403
    st = state(inv, now)
    t = tally(inv["id"])
    rows = responses(inv["id"]) if inv["show_guests"] == "names" else []
    can_answer = st == "open" and not link_error and (response is not None or guest is not None
                                                      or not inv["invite_only"])
    own_yes = response["count"] if response is not None and response["status"] == "yes" else 0
    left = max(0, inv["capacity"] - t["yes"]) if inv["capacity"] else None
    if form is None:
        form = ({k_: response[k_] for k_ in ("name", "status", "count", "note", "answer")} if response is not None
                else {"name": guest["label"] if guest else "", "status": "", "count": 1, "note": "", "answer": ""})
        form["count"] = form["count"] or 1
    deadline = parse_date(inv["rsvp_deadline"])
    light, dark = accent_colors(inv)
    resp = make_response(render_template(
        "invites/public.html", inv=inv, owner=owner, guest=guest, response=response, error=error, form=form,
        notice=notice and response is not None, state=st, can_answer=can_answer, t=t, statuses=STATUSES,
        k=k if guest else "", y=y if (y and response is not None) else "",
        edit_link=edit_url(inv["code"], token) if token and not guest else None,
        when=when_text(inv, now), day=day_text(parse_date(inv["starts_on"]), now), hours=hours_text(inv),
        deadline=day_text(deadline, now) if deadline else None, maps=maps_url(inv), accent_light=light,
        accent_dark=dark, url=public_url(inv["code"]), photo_v=photo_version(inv),
        full=left is not None and left <= 0 and not own_yes, left=left,
        count_max=max(1, min(inv["max_per_response"], own_yes + left)) if left is not None else inv["max_per_response"],
        coming=[r for r in rows if r["status"] == "yes"], maybe=[r for r in rows if r["status"] == "maybe"],
        plus=plus, state_error=state_error(inv, st) if st != "open" else None), status)
    resp.headers["Content-Security-Policy"] = CSP
    return resp


@bp.after_request
def _public_headers(resp):
    """Herkese açık sayfa (404 dahil): önbelleğe alınmaz (kapak fotoğrafı hariç), dizine eklenmez, gömülemez; adres ve
    anahtarlar başka siteye (Referer) sızmaz."""
    if request.endpoint in PUBLIC_ENDPOINTS:
        if request.endpoint != "invites.cover" or resp.status_code != 200:
            resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


def _set_cookie(resp, inv, token):
    resp.set_cookie(cookie_name(inv["code"]), token, max_age=COOKIE_DAYS * 86400, path=f"/d/{inv['code']}",
                    httponly=True, samesite="Lax", secure=bool(current_app.config.get("SESSION_COOKIE_SECURE")))


@bp.route("/d/<code>", methods=["GET", "POST"])
def public(code):
    inv = _invite_or_404(code)
    k, y = request.values.get("k", "").strip(), request.values.get("y", "").strip()
    if request.method == "POST":
        return _answer(inv, k, y)
    if code != inv["code"]:   # büyük harfli adres: yanıt çerezinin yolu küçük harfli adrese bağlı
        return redirect(url_for(".public", code=inv["code"], **({"k": k} if k else {}), **({"y": y} if y else {})))
    notice = request.args.get("ok") == "1"
    # Sahibi, yanıt sonrası yönlendirme, HEAD ve WhatsApp/Telegram gibi uygulamaların link önizlemesi sayılmaz
    if request.method == "GET" and not (_is_owner(inv) or notice or is_link_preview(request.user_agent.string)):
        execute("UPDATE invites SET views = views + 1 WHERE id = ?", (inv["id"],))
    resp = _render_public(inv, k, y, notice=notice)
    if y and not k and resp.status_code == 200 and request.cookies.get(cookie_name(inv["code"])) != y:
        _set_cookie(resp, inv, y)   # kişisel linkle açılan tarayıcı da bundan sonra tanınsın
    return resp


def _answer(inv, k, y):
    f = request.form
    form = {"name": f.get("name", "")[:NAME_MAX], "status": f.get("status", ""), "count": f.get("count", "")[:4],
            "note": f.get("note", "")[:NOTE_MAX], "answer": f.get("answer", "")[:ANSWER_MAX]}

    def fail(message, status=400):
        return _render_public(inv, k, y, status, error=message, form=form)

    if f.get("website"):   # bal tuzağı: insanlar görmez, robotlar doldurur
        return fail("Yanıt kaydedilemedi.")
    if _is_owner(inv):
        return fail("Bu senin davetiyen: önizlemede yanıt gönderilemez. Davetlilerin yanıtlarını davetiyenin "
                    "sayfasında görürsün.", 403)
    guest, response, _token, link_error = _whose(inv, k, y)
    if link_error:
        return fail(link_error, 403)
    st = state(inv)
    if st != "open":
        return fail(state_error(inv, st), 409)
    if response is None and guest is None and inv["invite_only"]:
        return fail("Bu davetiyeye sadece kişiye özel linklerle yanıt verilebilir; linkin yoksa davet edenden iste.",
                    403)
    name = " ".join(form["name"].split())
    if not name:
        return fail("Adını yaz.")
    status = form["status"]
    if status not in STATUSES or (status == "maybe" and not inv["allow_maybe"]):
        return fail("Gelip gelemeyeceğini seç.")
    count = 0
    if status == "yes":
        count = _int(form["count"]) if inv["ask_count"] and form["count"].strip() else 1
        if count is None or not 1 <= count <= (inv["max_per_response"] if inv["ask_count"] else 1):
            return fail(f"Kişi sayısı 1 ile {inv['max_per_response']} arasında olmalı (sen dahil).")
    note = form["note"].strip().replace("\r\n", "\n")
    answer = " ".join(form["answer"].split()) if inv["question"] else ""
    if response is not None and (name, status, count, note, answer) == tuple(
            response[x] for x in ("name", "status", "count", "note", "answer")):
        return _done(inv, k, y, None)   # değişiklik yok: bildirim de yok
    digest = ip_hash(request.remote_addr)
    if response is None and query_one(
            "SELECT COUNT(*) AS n FROM invite_responses WHERE invite_id = ? AND ip_hash = ?"
            " AND created_at >= datetime('now', '-1 hour')", (inv["id"], digest))["n"] >= IP_LIMIT:
        return fail("Bu bağlantıdan kısa sürede çok fazla yanıt verildi; bir saat sonra tekrar dene.", 429)
    new_token = secrets.token_urlsafe(TOKEN_BYTES) if response is None else None
    db = get_db()
    if db.in_transaction:
        db.commit()
    db.execute("BEGIN IMMEDIATE")   # aynı anda gelen iki yanıt (çift tıklama) kontrolleri birlikte geçemesin
    try:
        error = _conflict(db, inv["id"], guest, response, name, status, count)
        if error:
            db.rollback()
            return fail(*error)
        db.execute("UPDATE invites SET rev = rev + 1 WHERE id = ?", (inv["id"],))
        rev = db.execute("SELECT rev FROM invites WHERE id = ?", (inv["id"],)).fetchone()["rev"]
        if response is None:
            db.execute(
                "INSERT INTO invite_responses (invite_id, guest_id, name, name_key, status, count, note, answer,"
                " edit_token_hash, ip_hash, rev, created_rev) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (inv["id"], guest["id"] if guest else None, name, fold(name), status, count, note, answer,
                 token_hash(new_token), digest, rev, rev))
        else:
            db.execute("UPDATE invite_responses SET name = ?, name_key = ?, status = ?, count = ?, note = ?,"
                       " answer = ?, rev = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                       (name, fold(name), status, count, note, answer, rev, response["id"]))
        db.commit()
    except Exception:
        db.rollback()
        raise
    notify_changes(inv["id"])
    return _done(inv, k, y, new_token)


def _done(inv, k, y, new_token):
    params = {"k": k} if k else ({"y": y} if y else {})
    resp = redirect(url_for(".public", code=inv["code"], ok=1, **params), code=303)
    if new_token:
        _set_cookie(resp, inv, new_token)
    return resp


def _conflict(db, invite_id, guest, response, name, status, count):
    """Kilit altında son kontroller: (metin, durum kodu) ya da None. Davetiye satırı da yeniden okunur (kontenjan
    bu arada değişmiş olabilir)."""
    inv = db.execute("SELECT * FROM invites WHERE id = ?", (invite_id,)).fetchone()
    if response is not None:
        response = db.execute("SELECT * FROM invite_responses WHERE id = ?", (response["id"],)).fetchone()
        if response is None:
            return "Yanıtın bu arada silinmiş; sayfayı yenileyip yeniden yanıt ver.", 409
    else:
        if guest is not None:
            if db.execute("SELECT 1 FROM invite_guests WHERE id = ?", (guest["id"],)).fetchone() is None:
                return INVALID_GUEST, 403
            if db.execute("SELECT 1 FROM invite_responses WHERE guest_id = ?", (guest["id"],)).fetchone():
                return "Bu kişiye özel linkle az önce yanıt verildi; sayfayı yenile.", 409
        if db.execute("SELECT COUNT(*) AS n FROM invite_responses WHERE invite_id = ?",
                      (invite_id,)).fetchone()["n"] >= MAX_RESPONSES:
            return f"Bu davetiye en fazla {MAX_RESPONSES} yanıt alabilir.", 409
    if db.execute("SELECT 1 FROM invite_responses WHERE invite_id = ? AND name_key = ? AND id != ?",
                  (invite_id, fold(name), response["id"] if response else 0)).fetchone():
        return (f"“{name}” adıyla yanıt var; değiştirmek için yanıt linkini kullan (yanıtı veren tarayıcıda bu sayfayı "
                f"aç ya da kişisel düzenleme linkini). Sen değilsen adının yanına soyadının baş harfini ekle "
                f"(ör. {name} K.)."), 409
    if status == "yes" and inv["capacity"]:
        had = response["count"] if response is not None and response["status"] == "yes" else 0
        taken = db.execute("SELECT COALESCE(SUM(count), 0) AS n FROM invite_responses WHERE invite_id = ?"
                           " AND status = 'yes'", (invite_id,)).fetchone()["n"]
        left = inv["capacity"] - (taken - had)
        if count > had and count > left:
            other = "“Belki” ya da “Gelemiyorum”" if inv["allow_maybe"] else "“Gelemiyorum”"
            if left <= 0:
                return f"Kontenjan doldu: artık “Geliyorum” yanıtı alınmıyor. {other} seçebilirsin.", 409
            return f"Kontenjanda sadece {left} kişilik yer kaldı; kişi sayısını azalt ya da {other} seç.", 409
    return None


@bp.route("/d/<code>/davetiye.ics")
def ics(code):
    inv = _invite_or_404(code)
    body = build_ics(inv, public_url(inv["code"]), datetime.now(timezone.utc))
    resp = make_response(body.encode("utf-8"))
    resp.headers["Content-Type"] = "text/calendar; charset=utf-8"
    resp.headers["Content-Disposition"] = 'attachment; filename="davetiye.ics"'
    return resp


@bp.route("/d/<code>/kapak.jpg")
def cover(code):
    """Kapak fotoğrafı: sayfada ve link önizlemesinde (og:image). Adres ?v= ile değiştiği için bir gün önbellekte
    kalabilir."""
    inv = _invite_or_404(code)
    if inv["photo"] is None:
        abort(404)
    resp = make_response(bytes(inv["photo"]))
    resp.mimetype = "image/jpeg"
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp
