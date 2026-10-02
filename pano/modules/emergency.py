"""🆘 Acil Durum Kartı: ilk yardım ekibi için tıbbi bilgiler (iPhone "Tıbbi Kimlik" benzeri).

- Yönetim /acil-durum/ (giriş gerekli); herkese açık sayfa /acil/<anahtar> (girişsiz, betiksiz, iki dilli etiketler)
- Anahtar tahmin edilemez (secrets.token_urlsafe; tıbbi bilgi olduğu için okunur bir adres değil). 🔄 Yenilenince
  eski bağlantı, QR, duvar kâğıdı ve cüzdan kartı açılmaz. Kapalı kart ile olmayan kart dışarıdan aynı 404;
  sahibi kapalıyken de önizler, ziyareti sayılmaz
- Sayfada sadece emergency_cards satırındaki alanlar görünür; kullanıcı adı dahil panodaki başka hiçbir veri okunmaz
- Başkası açınca sayaç artar (link önizleme robotları sayılmaz); Telegram bağlıysa en fazla 30 dakikada bir "kartın görüntülendi" bildirimi
- Kilit ekranı duvar kâğıdı (1080×2340 PNG, Pillow + segno) ve yazdırılabilir cüzdan kartı (85,6 × 54 mm, ön + arka)
- İlaçlar Sağlık modülündeki aktif ilaçlardan, acil kişiler Kişiler'den doldurulabilir (kaydetmeden önce kontrol edilir)
"""
import io
import json
import secrets
from datetime import timedelta, timezone
from functools import lru_cache
from itertools import zip_longest

import segno
from flask import (Blueprint, abort, current_app, flash, g, make_response, redirect, render_template, request,
                   send_file, url_for)
from PIL import Image, ImageDraw, ImageFont

from .. import telegram, trash
from .. import todo_reminders as todo
from ..auth import login_required
from ..db import get_db, query, query_one
from ..totp import qr_svg
from ..utils import form_bool, form_choice, form_str, is_link_preview, parse_date, today
from .profile import PHONE_RE, tel_number

# Yönetim /acil-durum altında, herkese açık sayfa /acil/ altında: tek blueprint iki ayrı kökte
bp = Blueprint("emergency", __name__)

# anahtar -> formdaki / sayfadaki yazılış ('' = bilinmiyor, sayfada gösterilmez)
BLOOD_TYPES = {"": "Bilinmiyor", "0+": "0 Rh+", "0-": "0 Rh−", "A+": "A Rh+", "A-": "A Rh−", "B+": "B Rh+",
               "B-": "B Rh−", "AB+": "AB Rh+", "AB-": "AB Rh−"}
ORGAN_DONOR = {"": "Belirtilmedi", "yes": "Evet, organ bağışçısıyım", "no": "Hayır"}
ORGAN_PUBLIC = {"yes": "Evet · Yes", "no": "Hayır · No"}
LIMITS = {"full_name": 80, "allergies": 500, "conditions": 500, "medications": 1000, "notes": 500}
MULTILINE = ("allergies", "conditions", "medications", "notes")
MAX_CONTACTS = 5
CONTACT_LIMITS = {"name": 60, "relation": 30, "phone": 30}
FIELDS = ("enabled", "full_name", "birth_date", "blood_type", "allergies", "conditions", "medications",
          "organ_donor", "notes")
TOKEN_BYTES = 16          # 22 karakter, 128 bit
NOTIFY_EVERY = timedelta(minutes=30)
DB_FORMAT = "%Y-%m-%d %H:%M:%S"   # UTC, CURRENT_TIMESTAMP ile aynı (localdt filtresi okur)
PICKS_MAX = 300           # Kişiler'den seçme listesi

# Duvar kâğıdı: telefon dikey ekranı; üst ~%35 kilit ekranı saatine boş bırakılır
WALL_W, WALL_H = 1080, 2340
WALL_TOP = 0.35
WALL_BG, WALL_RED, WALL_TEXT, WALL_MUTED, WALL_WARN = "#0f1117", "#c62828", "#ffffff", "#a3a8b8", "#fbbf24"
# Türkçe harfleri (ş ğ ı İ ö ü ç) çizebilen ilk bulunan TrueType (PythonAnywhere: DejaVu, Windows: Arial / Segoe UI)
_DEJAVU = "/usr/share/fonts/truetype/dejavu/"
BOLD_FONTS = (_DEJAVU + "DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf", _DEJAVU + "DejaVuSans.ttf",
              "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf")
REGULAR_FONTS = (_DEJAVU + "DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf",
                 _DEJAVU + "DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf")


# ---------- Yardımcılar ----------
def new_token():
    return secrets.token_urlsafe(TOKEN_BYTES)


def phone_ok(phone):
    return bool(PHONE_RE.match(phone)) and 7 <= sum(ch.isdigit() for ch in phone) <= 15


def age_of(birth, on):
    """Tam yaş; doğum tarihi yoksa ya da ileri tarihliyse None."""
    if birth is None:
        return None
    years = on.year - birth.year - ((on.month, on.day) < (birth.month, birth.day))
    return years if years >= 0 else None


def blood_intl(key):
    """Uluslararası yazılış: '0-' -> 'O−' (yurt dışında 0 yerine O harfi kullanılır)."""
    return key.replace("0", "O").replace("-", "−") if key in BLOOD_TYPES else ""


def parse_contacts(names, relations, phones):
    """Formdaki satırlar -> [{name, relation, phone}] (sırası korunur, boş satır atlanır); hatalıysa ValueError."""
    out = []
    for name, relation, phone in zip_longest(names, relations, phones, fillvalue=""):
        name = name.strip()[:CONTACT_LIMITS["name"]]
        relation = relation.strip()[:CONTACT_LIMITS["relation"]]
        phone = phone.strip()[:CONTACT_LIMITS["phone"]]
        if not (name or relation or phone):
            continue
        who = name or relation
        if not phone:
            raise ValueError(f"“{who}” için telefon numarası yaz.")
        if not phone_ok(phone):
            raise ValueError(f"“{who or phone}” telefon numarası geçersiz "
                             "(ör. 0532 123 45 67 ya da +90 532 123 45 67).")
        if not who:
            raise ValueError(f"{phone} numarası için ad ya da yakınlık yaz.")
        out.append({"name": name, "relation": relation, "phone": phone})
    if len(out) > MAX_CONTACTS:
        raise ValueError(f"En fazla {MAX_CONTACTS} acil kişi eklenebilir.")
    return out


def _contacts(raw):
    """Kayıttaki JSON; bozuk satır sayfaya hiç çıkmaz."""
    try:
        items = json.loads(raw or "[]")
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    return [{"name": str(k.get("name", "")), "relation": str(k.get("relation", "")), "phone": k["phone"]}
            for k in items if isinstance(k, dict) and isinstance(k.get("phone"), str) and k["phone"]][:MAX_CONTACTS]


def display(c):
    """Sayfa, cüzdan kartı ve duvar kâğıdı için hazır metinler; boş alan boş kalır (gösterilmez)."""
    birth = parse_date(c["birth_date"])
    blood = c["blood_type"] if c["blood_type"] in BLOOD_TYPES else ""
    return {
        "age": age_of(birth, today()),
        "birth": birth.strftime("%d.%m.%Y") if birth else "",
        "blood": BLOOD_TYPES[blood] if blood else "",
        "blood_intl": blood_intl(blood) if blood.startswith("0") else "",   # A/B/AB her yerde aynı yazılır
        "organ": ORGAN_PUBLIC.get(c["organ_donor"], ""),
        "contacts": [{**k, "tel": tel_number(k["phone"])} for k in c["contacts"]],
    }


def health_medications(user_id):
    """Sağlık modülündeki aktif ilaçlar: satır başına 'ad doz'."""
    rows = query("SELECT name, dose FROM medications WHERE user_id = ? AND active = 1 ORDER BY name COLLATE NOCASE",
                 (user_id,))
    return [f"{r['name']} {r['dose']}".strip() for r in rows]


def contact_picks(user_id):
    """Kişiler modülünden telefonlu kayıtlar (acil kişi satırını doldurmak için)."""
    return [dict(r) for r in query(
        "SELECT name, relation, phone FROM contacts WHERE user_id = ? AND phone != '' ORDER BY name COLLATE NOCASE"
        " LIMIT ?", (user_id, PICKS_MAX))]


# ---------- Ortak ----------
def _load(where, args):
    row = query_one(f"SELECT * FROM emergency_cards WHERE {where}", args)
    return {**dict(row), "contacts": _contacts(row["contacts"])} if row else None


def get_card(user_id):
    return _load("user_id = ?", (user_id,))


def public_url(token):
    return url_for("emergency.public", token=token, _external=True)


def _is_owner(c):
    return g.user is not None and g.user["id"] == c["user_id"]


def _visible_or_404(token):
    """Açık kart; kapalıysa sadece sahibine görünür. Kapalı ile olmayan dışarıdan aynı 404."""
    c = _load("token = ?", (token,)) if len(token) <= 64 else None
    if c is None or not (c["enabled"] or _is_owner(c)):
        abort(404)
    return c


def _owned_or_404():
    c = get_card(g.user["id"])
    if c is None:
        abort(404)
    return c


def _utc(dt):
    return dt.astimezone(timezone.utc).strftime(DB_FORMAT)


# ---------- Yönetim ----------
def _defaults(user):
    return {"enabled": 0, "full_name": user["display_name"] or "", "birth_date": "", "blood_type": "",
            "allergies": "", "conditions": "", "medications": "", "organ_donor": "", "notes": "", "contacts": []}


def _form_values():
    """Hata sonrası formu kullanıcının yazdıklarıyla yeniden doldurmak için."""
    f = request.form
    rows = zip_longest(f.getlist("contact_name"), f.getlist("contact_relation"), f.getlist("contact_phone"),
                       fillvalue="")
    contacts = [{"name": n, "relation": r, "phone": p} for n, r, p in rows if n or r or p]
    return {**{k: f.get(k, "") for k in (*LIMITS, "birth_date", "blood_type", "organ_donor")},
            "enabled": form_bool("enabled"), "contacts": contacts}


def _parse():
    """Formdan kart; (değerler, hata) döner."""
    v = {k: form_str(k, n) for k, n in LIMITS.items()}
    for k in MULTILINE:
        v[k] = v[k].replace("\r\n", "\n")
    v.update(enabled=form_bool("enabled"), blood_type=form_choice("blood_type", BLOOD_TYPES, ""),
             organ_donor=form_choice("organ_donor", ORGAN_DONOR, ""))
    raw_birth = request.form.get("birth_date", "").strip()
    birth = parse_date(raw_birth)
    v["birth_date"] = birth.isoformat() if birth else None
    if raw_birth and (birth is None or birth.year < 1900 or birth > today()):
        return v, "Doğum tarihi geçersiz."
    try:
        v["contacts"] = parse_contacts(request.form.getlist("contact_name"), request.form.getlist("contact_relation"),
                                       request.form.getlist("contact_phone"))
    except ValueError as e:
        return v, str(e)
    return v, None


def _render(form, current, status=200):
    url = public_url(current["token"]) if current else None
    return render_template(
        "emergency/index.html", form=form, current=current, url=url, qr=qr_svg(url) if url else None,
        blood_types=BLOOD_TYPES, organ_options=ORGAN_DONOR, max_contacts=MAX_CONTACTS,
        picks=contact_picks(g.user["id"]), telegram_ready=telegram.enabled() and bool(g.user["telegram_chat_id"]),
    ), status


@bp.route("/acil-durum/", methods=["GET", "POST"])
@login_required
def index():
    uid = g.user["id"]
    current = get_card(uid)
    if request.method == "GET":
        form = dict(current or _defaults(g.user))
        if request.args.get("ilaclar"):
            # Sağlık'taki aktif ilaçlarla formu doldurur; kaydetmez, kullanıcı kontrol edip kaydeder
            meds = health_medications(uid)
            if meds:
                form["medications"] = "\n".join(meds)[:LIMITS["medications"]]
                flash(f"🩺 Sağlık'tan {len(meds)} aktif ilaç getirildi; kontrol edip Kaydet'e bas.", "info")
            else:
                flash("🩺 Sağlık modülünde aktif ilaç yok.", "warning")
        return _render(form, current)
    v, error = _parse()
    if error:
        flash(error, "error")
        return _render(_form_values(), current, status=400)
    values = [v[k] for k in FIELDS] + [json.dumps(v["contacts"], ensure_ascii=False)]
    db = get_db()
    db.execute(
        f"INSERT INTO emergency_cards (user_id, token, {', '.join(FIELDS)}, contacts)"
        f" VALUES (?, ?{', ?' * (len(FIELDS) + 1)})"
        f" ON CONFLICT(user_id) DO UPDATE SET {', '.join(f'{k} = excluded.{k}' for k in (*FIELDS, 'contacts'))},"
        " updated_at = CURRENT_TIMESTAMP",
        (uid, new_token(), *values))   # anahtar sadece ilk kayıtta yazılır
    db.commit()
    flash("🆘 Acil durum kartı kaydedildi. " + (
        "Kart yayında." if v["enabled"] else "Kart kapalı; “Kart yayında” işaretlenince bağlantı ve QR çalışır."),
        "success")
    return redirect(url_for(".index"))


@bp.route("/acil-durum/baglanti-yenile", methods=["POST"])
@login_required
def regenerate():
    c = _owned_or_404()
    db = get_db()
    db.execute("UPDATE emergency_cards SET token = ? WHERE id = ?", (new_token(), c["id"]))
    db.commit()
    flash("🔄 Yeni bağlantı oluşturuldu. Eski bağlantı, QR, duvar kâğıdı ve cüzdan kartı artık açılmaz; "
          "duvar kâğıdını yeniden indir, kartı yeniden yazdır.", "success")
    return redirect(url_for(".index"))


@bp.route("/acil-durum/sil", methods=["POST"])
@login_required
def delete():
    c = _owned_or_404()
    label = "🆘 Acil durum kartı" + (f" ({c['full_name']})" if c["full_name"] else "")
    trash.move(g.user["id"], "emergency", label, ("emergency_cards", c["id"]))
    flash(trash.notice("Acil durum kartı"), "success")
    return redirect(url_for(".index"))


@bp.route("/acil-durum/kart")
@login_required
def wallet():
    """Yazdırılabilir cüzdan kartı (kredi kartı boyutu, ön ve arka yüz yan yana)."""
    c = _owned_or_404()
    url = public_url(c["token"])
    return render_template("emergency/card.html", c=c, d=display(c), url=url, qr=qr_svg(url))


# ---------- Kilit ekranı duvar kâğıdı ----------
@lru_cache(maxsize=32)
def _font(size, bold=False):
    for path in BOLD_FONTS if bold else REGULAR_FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _fit(draw, text, font, width):
    """Tek satıra sığmayan metin sonuna … eklenerek kısaltılır."""
    text = " ".join(text.split())
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _wrap(draw, text, font, width, max_lines=2):
    """Kelime kelime satırlara böler; sığmayan kısım son satırda … ile kısaltılır."""
    words = text.split()
    lines = []
    while words and len(lines) < max_lines:
        line = words.pop(0)
        while words and draw.textlength(line + " " + words[0], font=font) <= width:
            line += " " + words.pop(0)
        lines.append(line)
    if words:
        lines[-1] += " " + " ".join(words)
    return [_fit(draw, line, font, width) for line in lines]


def build_wallpaper(c, url):
    """Koyu zeminli 1080×2340 PNG (bayt): şerit, ad, kan grubu, alerji, ilk acil kişi, büyük QR."""
    d = display(c)
    img = Image.new("RGB", (WALL_W, WALL_H), WALL_BG)
    draw = ImageDraw.Draw(img)
    margin = 70
    inner = WALL_W - 2 * margin
    mid = WALL_W / 2
    y = int(WALL_H * WALL_TOP)

    def lines(text, size, fill, bold=False, max_lines=1, gap=1.22):
        nonlocal y
        font = _font(size, bold)
        for line in _wrap(draw, text, font, inner, max_lines):
            draw.text((mid, y), line, font=font, fill=fill, anchor="ma")
            y += int(size * gap)

    band = 124
    draw.rounded_rectangle((margin, y, WALL_W - margin, y + band), radius=30, fill=WALL_RED)
    draw.text((mid, y + band / 2), _fit(draw, "ACİL DURUM · EMERGENCY", _font(60, True), inner - 40),
              font=_font(60, True), fill=WALL_TEXT, anchor="mm")
    y += band + 44
    if c["full_name"]:
        lines(c["full_name"], 76, WALL_TEXT, bold=True, max_lines=2)
        y += 14
    if d["blood"]:
        lines("KAN GRUBU · BLOOD TYPE", 34, WALL_MUTED)
        lines(d["blood"] + (f"  ({d['blood_intl']})" if d["blood_intl"] else ""), 84, "#f87171", bold=True, gap=1.15)
        y += 14
    allergy = ", ".join(line.strip() for line in c["allergies"].splitlines() if line.strip())  # 2 satıra sığdırılır
    if allergy:
        lines("ALERJİ · ALLERGY", 34, WALL_WARN, bold=True)   # ⚠ gibi simgeler Arial'de yok: düz metin
        lines(allergy, 46, WALL_WARN, max_lines=2)
        y += 14
    if d["contacts"]:
        k = d["contacts"][0]
        who = k["name"] or k["relation"]
        if k["name"] and k["relation"]:
            who += f" ({k['relation']})"
        lines("ACİL DURUMDA ARAYIN · ICE", 34, WALL_MUTED)
        lines(who, 46, WALL_TEXT)
        lines(k["phone"], 54, WALL_TEXT, bold=True)
    # QR kalan yere sığacak kadar büyük; alt kısım (fener / kamera kısayolları) boş kalır
    y += 30
    caption_h, bottom = 120, 170
    target = max(300, min(560, WALL_H - bottom - caption_h - y))
    qr = segno.make(url, error="m")
    scale = max(1, target // qr.symbol_size(scale=1, border=4)[0])   # tam sayı ölçek: modüller net kalsın
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=scale, border=4, dark="#000000", light="#ffffff")
    buf.seek(0)
    code = Image.open(buf).convert("RGB")
    pad = 16
    left = (WALL_W - code.width) // 2
    draw.rounded_rectangle((left - pad, y - pad, left + code.width + pad, y + code.height + pad), radius=28,
                           fill="#ffffff")
    img.paste(code, (left, y))
    y += code.height + pad + 34
    lines("Tıbbi bilgiler için okutun", 44, WALL_TEXT, bold=True)
    lines("Scan for medical info", 38, WALL_MUTED)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


@bp.route("/acil-durum/duvar-kagidi.png")
@login_required
def wallpaper():
    c = _owned_or_404()
    resp = send_file(io.BytesIO(build_wallpaper(c, public_url(c["token"]))), mimetype="image/png",
                     as_attachment=True, download_name="acil-durum-duvar-kagidi.png")
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


# ---------- Herkese açık sayfa ----------
def _record_view(c):
    """Sayaç ve son görüntülenme; Telegram bağlıysa en fazla 30 dakikada bir bildirim (hata sayfayı bozmaz)."""
    now = todo.now_local()
    db = get_db()
    db.execute("UPDATE emergency_cards SET views = views + 1, last_viewed_at = ? WHERE id = ?", (_utc(now), c["id"]))
    owner = query_one("SELECT telegram_chat_id FROM users WHERE id = ?", (c["user_id"],))
    chat_id = owner["telegram_chat_id"] if owner else None
    claimed = False
    if chat_id and telegram.enabled():
        # Bildirim hakkı tek sorguda alınır: aynı anda gelen iki ziyaret iki mesaj üretmesin
        claimed = db.execute(
            "UPDATE emergency_cards SET notified_at = ? WHERE id = ? AND (notified_at IS NULL OR notified_at <= ?)",
            (_utc(now), c["id"], _utc(now - NOTIFY_EVERY))).rowcount == 1
    db.commit()
    if not claimed:
        return
    text = (f"🆘 <b>Acil durum kartın görüntülendi</b> ({now:%H:%M})\n"
            f"Biri bağlantını ya da QR kodunu açtı · toplam {c['views'] + 1} görüntülenme. "
            "Beklemiyorsan 🔄 Bağlantıyı yenile ile eski QR'ı geçersiz kılabilirsin. "
            "<i>30 dakika içindeki yeni görüntülenmeler için tekrar mesaj gelmez.</i>\n"
            f'<a href="{telegram.escape(url_for(".index", _external=True))}">Acil Durum Kartı →</a>')
    try:
        telegram.send_message(chat_id, text)
    except telegram.TelegramError as e:
        current_app.logger.warning("Acil durum kartı bildirimi gönderilemedi (kullanıcı %s): %s", c["user_id"], e)
    except Exception:  # bildirim yan etkidir; sayfa yine açılmalı
        current_app.logger.exception("Acil durum kartı bildirimi hatası")


@bp.route("/acil/<token>")
def public(token):
    c = _visible_or_404(token)
    owner = _is_owner(c)
    # WhatsApp/Telegram gibi uygulamaların link önizlemesi görüntülenme sayılmaz, bildirim de göndermez
    if not owner and request.method == "GET" and not is_link_preview(request.user_agent.string):
        _record_view(c)
    resp = make_response(render_template("emergency/public.html", c=c, d=display(c), owner=owner))
    resp.headers["Cache-Control"] = "no-store"
    # Sayfada betik yok; kullanıcı metni kaçışlansa da ek önlem olarak dış kaynak, form, gömme kapalı
    resp.headers["Content-Security-Policy"] = ("default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
                                               "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp
