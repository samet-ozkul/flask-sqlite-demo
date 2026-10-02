"""🌐 Herkese açık profil sayfası: Linktree / dijital kartvizit benzeri tek sayfa.

- Yönetim /profil-sayfasi/ (giriş gerekli); herkese açık sayfa /p/<adres> (girişsiz; menü, kullanıcı bilgisi, CSRF yok)
- Sayfada sadece public_profiles satırındaki alanlar görünür; panodaki başka hiçbir veri (not, harcama...) okunmaz
- Fotoğraf ortadan kare kırpılır, en fazla 400×400 JPEG, EXIF/GPS'siz; uploads'ta değil veritabanında durur
  (storage.cleanup_orphans tablosuz dosyaları siler), yedeğe kendiliğinden girer
- Linkler sadece http(s)://, mailto:, tel:; arama motorları varsayılan olarak dizine eklemez (noindex)
- QR kod (segno; kartvizit için büyük PNG) ve "📇 Rehbere ekle" (vCard 3.0)
- Kapalı profil ile olmayan profil dışarıdan aynı 404; sahibi kapalıyken de önizleyebilir, ziyareti sayılmaz
"""
import io
import json
import re
import sqlite3
import unicodedata
from urllib.parse import urlsplit

import segno
from flask import Blueprint, abort, flash, g, make_response, redirect, render_template, request, send_file, url_for
from PIL import Image, ImageOps, UnidentifiedImageError

from ..auth import login_required
from ..db import get_db, query_one
from ..storage import quota_bytes, usage
from ..totp import qr_svg
from ..utils import fold, form_bool, form_choice, form_str

# Yönetim /profil-sayfasi altında, herkese açık sayfa /p/ altında: tek blueprint iki ayrı kökte
# olduğu için url_prefix yerine yollar tam yazılır.
bp = Blueprint("profile", __name__)

SLUG_MIN, SLUG_MAX = 3, 30
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
# Karışıklık ya da taklit olmasın diye alınamayan adresler
RESERVED = {
    "admin", "administrator", "api", "static", "giris", "cikis", "kayit", "yonetim", "yonetici", "ayarlar",
    "pano", "kisisel-pano", "profil", "profil-sayfasi", "www", "root", "destek", "yardim", "help", "support",
    "cron", "telegram", "bot", "dosya", "sifre", "sifremi-unuttum", "guvenlik", "sistem", "system", "test",
    "null", "undefined",
}
MAX_LINKS = 12
LINK_SCHEMES = ("http://", "https://", "mailto:", "tel:")
BARE_DOMAIN_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(:\d+)?([/?#]|$)")   # "instagram.com/kullanici"
EMAIL_RE = re.compile(r"^[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[^@\s<>\"',;]+$")
PHONE_RE = re.compile(r"^\+?[0-9 ().-]{7,30}$")
LIMITS = {"display_name": 60, "headline": 100, "bio": 500, "avatar_emoji": 16, "email": 120, "phone": 30,
          "location": 80}
PHOTO_PX = 400
PHOTO_QUALITY = 80
QR_PNG_SCALE = 20   # kartvizit baskısı için büyük (~700 px)
# anahtar: (ad, açık tema rengi, koyu tema rengi) — sayfaya sadece buradaki değerler yazılır
ACCENTS = {
    "indigo": ("Mor", "#4f46e5", "#818cf8"),
    "blue": ("Mavi", "#2563eb", "#60a5fa"),
    "teal": ("Turkuaz", "#0f766e", "#2dd4bf"),
    "green": ("Yeşil", "#15803d", "#4ade80"),
    "orange": ("Turuncu", "#c2410c", "#fb923c"),
    "red": ("Kırmızı", "#dc2626", "#f87171"),
    "pink": ("Pembe", "#be185d", "#f472b6"),
    "slate": ("Gri", "#475569", "#94a3b8"),
}
DEFAULT_ACCENT = "indigo"
# Herkese açık sayfada fotoğraf bayt'ları gereksiz yere okunmasın
COLUMNS = ("user_id, slug, enabled, display_name, headline, bio, avatar_emoji, accent, email, phone, location, links,"
           " noindex, views, updated_at, photo IS NOT NULL AS has_photo")


# ---------- Adres (slug) ----------
def slugify(text):
    """Serbest metinden adres önerisi: 'Şule Çağrı Öztürk' -> 'sule-cagri-ozturk'."""
    text = unicodedata.normalize("NFKD", fold(text)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:SLUG_MAX].strip("-")


def slug_error(slug, user_id):
    """Adres uygunsa None, değilse hata metni."""
    if not (SLUG_MIN <= len(slug) <= SLUG_MAX) or not SLUG_RE.match(slug):
        return (f"Adres {SLUG_MIN}-{SLUG_MAX} karakter olmalı; küçük harf (a-z), rakam ve tire içerebilir, "
                "tireyle başlayıp bitemez.")
    if slug in RESERVED:
        return f"“{slug}” ayrılmış bir kelime; başka bir adres seç."
    if query_one("SELECT 1 FROM public_profiles WHERE slug = ? AND user_id != ?", (slug, user_id)):
        return f"“{slug}” adresi başka biri tarafından alınmış."
    return None


def suggest_slug(user):
    """Addan (yoksa kullanıcı adından) boşta olan bir adres; gerekirse sonuna -2, -3... eklenir."""
    base = slugify(user["display_name"]) or slugify(user["username"])
    if len(base) < SLUG_MIN or base in RESERVED:
        base = f"profil-{user['id']}"
    for n in range(1, 100):
        slug = base if n == 1 else f"{base[:SLUG_MAX - len(str(n)) - 1].rstrip('-')}-{n}"
        if slug_error(slug, user["id"]) is None:
            return slug
    return f"profil-{user['id']}"


# ---------- Link, telefon ----------
def clean_link_url(raw):
    """Güvenli link adresi; değilse ValueError. Şemasız 'ornek.com/sayfa' adresine https:// eklenir."""
    url = (raw or "").strip()
    if not url or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise ValueError(url)
    low = url.lower()
    if not low.startswith(LINK_SCHEMES):
        if not BARE_DOMAIN_RE.match(low):
            raise ValueError(url)   # javascript:, data:, vbscript: ... buraya düşer
        url, low = "https://" + url, "https://" + low
    if low.startswith(("http://", "https://")):
        if not urlsplit(url).hostname:
            raise ValueError(url)
    elif not url.split(":", 1)[1]:
        raise ValueError(url)
    return url


def _default_label(url):
    return re.sub(r"^(https?://(www\.)?|mailto:|tel:)", "", url, flags=re.I).rstrip("/")[:60]


def parse_links(labels, urls):
    """Formdaki satırlar -> [{label, url}] (sırası korunur, boş satırlar atlanır); hatalıysa ValueError."""
    links = []
    for label, raw in zip(labels, urls):
        label, raw = label.strip()[:60], raw.strip()[:500]
        if not raw:
            if label:
                raise ValueError(f"“{label}” linkinin adresi boş.")
            continue
        try:
            url = clean_link_url(raw)
        except ValueError:
            raise ValueError(f"“{label or raw[:40]}” linki geçersiz: adres http://, https://, mailto: ya da tel: "
                             "ile başlamalı.")
        links.append({"label": label or _default_label(url), "url": url})
    if len(links) > MAX_LINKS:
        raise ValueError(f"En fazla {MAX_LINKS} link eklenebilir.")
    return links


def _links(raw):
    """Kayıttaki JSON; bozuk ya da güvensiz satır sayfaya hiç çıkmaz."""
    try:
        items = json.loads(raw or "[]")
    except ValueError:
        return []
    return [{"label": str(l.get("label", "")), "url": str(l["url"])} for l in items if isinstance(l, dict)
            and isinstance(l.get("url"), str) and l["url"].lower().startswith(LINK_SCHEMES)][:MAX_LINKS]


def phone_intl(phone):
    """Ülke koduyla rakamlar (WhatsApp için): '0532 123 45 67' -> '905321234567'; anlaşılamazsa None.
    Ülke kodu yazılmamış 0'la başlayan 11 haneli numara Türkiye (+90) sayılır."""
    raw = (phone or "").strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+"):
        intl = digits
    elif digits.startswith("00"):
        intl = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        intl = "90" + digits[1:]
    elif digits.startswith("5") and len(digits) == 10:
        intl = "90" + digits
    elif digits.startswith("90") and len(digits) == 12:
        intl = digits
    else:
        return None
    return intl if 8 <= len(intl) <= 15 else None


def tel_number(phone):
    intl = phone_intl(phone)
    return "+" + intl if intl else re.sub(r"[^\d+]", "", phone or "")


def contacts(p):
    """Herkese açık sayfadaki iletişim butonları: [(ikon, yazı, adres, yeni_sekme)]; boş alan gösterilmez."""
    out = []
    if p["phone"]:
        out.append(("📞", "Ara", "tel:" + tel_number(p["phone"]), False))
        intl = phone_intl(p["phone"])
        if intl:
            out.append(("💬", "WhatsApp", f"https://wa.me/{intl}", True))
    if p["email"]:
        out.append(("✉️", "E-posta", "mailto:" + p["email"], False))
    return out


# ---------- Fotoğraf ----------
def process_photo(file_storage, user_id=None):
    """Ortadan kare kırpar, en fazla 400×400 JPEG'e çevirir (EXIF/GPS yazılmaz); bayt döner, geçersizse ValueError."""
    from .. import quota
    data = file_storage.read()
    if not data:
        raise ValueError("Fotoğraf dosyası boş.")
    if user_id is not None:
        quota.check_file(user_id, len(data), "Fotoğraf")
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))  # telefon fotoğrafı yan durmasın
        if img.mode != "RGB":
            rgba = img.convert("RGBA")
            img = Image.new("RGB", img.size, "white")
            img.paste(rgba, mask=rgba.split()[-1])
        side = min(PHOTO_PX, *img.size)
        buf = io.BytesIO()
        ImageOps.fit(img, (side, side)).save(buf, "JPEG", quality=PHOTO_QUALITY, optimize=True)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("Fotoğraf okunamadı; JPG, PNG ya da WEBP yükle.")
    out = buf.getvalue()
    if user_id is not None:
        old = query_one("SELECT COALESCE(length(photo), 0) AS n FROM public_profiles WHERE user_id = ?", (user_id,))
        quota.check_space(user_id, len(out) - (old["n"] if old else 0))  # eski fotoğrafın yerine geçer
    if usage()["used"] + len(out) > quota_bytes():
        raise ValueError("Depolama kotası doldu; fotoğraf kaydedilemedi.")
    return out


# ---------- vCard ----------
def _vesc(text):
    """vCard 3.0 metin kaçışı: ters bölü, noktalı virgül, virgül ve satır sonu."""
    return (text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n"))


def _fold(line):
    """75 bayttan uzun satır CRLF + boşlukla katlanır (RFC 2425); UTF-8 karakter ortadan bölünmez."""
    parts, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > (75 if not parts else 74):
            parts.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    parts.append(cur)
    return "\r\n ".join(parts)


def build_vcard(p, url):
    name = p["display_name"].strip() or p["slug"]
    given, _, family = name.rpartition(" ")
    if not given:
        given, family = family, ""
    lines = ["BEGIN:VCARD", "VERSION:3.0", f"N:{_vesc(family)};{_vesc(given)};;;", f"FN:{_vesc(name)}"]
    if p["headline"]:
        lines.append(f"TITLE:{_vesc(p['headline'])}")
    if p["phone"]:
        lines.append(f"TEL;TYPE=CELL:{tel_number(p['phone'])}")
    if p["email"]:
        lines.append(f"EMAIL;TYPE=INTERNET:{_vesc(p['email'])}")
    lines.append(f"URL:{url}")
    if p["location"]:
        lines.append(f"ADR:;;;{_vesc(p['location'])};;;")   # konum şehir alanına
    if p["bio"]:
        lines.append(f"NOTE:{_vesc(p['bio'])}")
    lines.append("END:VCARD")
    return "".join(_fold(line) + "\r\n" for line in lines)


# ---------- Ortak ----------
def _load(where, args):
    row = query_one(f"SELECT {COLUMNS} FROM public_profiles WHERE {where}", args)
    return {**dict(row), "links": _links(row["links"])} if row else None


def get_profile(user_id):
    return _load("user_id = ?", (user_id,))


def public_url(slug):
    return url_for("profile.public", slug=slug, _external=True)


def _is_owner(p):
    return g.user is not None and g.user["id"] == p["user_id"]


def _visible_or_404(slug):
    """Açık profil; kapalıysa sadece sahibine görünür. Kapalı ile olmayan dışarıdan aynı 404."""
    p = _load("slug = ?", (slug.lower(),))
    if p is None or not (p["enabled"] or _is_owner(p)):
        abort(404)
    return p


def _public_headers(resp, p):
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if p["noindex"]:
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


# ---------- Yönetim ----------
def _defaults(user):
    return {"slug": suggest_slug(user), "enabled": 0, "noindex": 1, "display_name": user["display_name"],
            "headline": "", "bio": "", "avatar_emoji": "", "accent": DEFAULT_ACCENT, "email": "", "phone": "",
            "location": "", "links": []}


def _form_values():
    """Hata sonrası formu kullanıcının yazdıklarıyla yeniden doldurmak için."""
    f = request.form
    links = [{"label": l, "url": u} for l, u in zip(f.getlist("link_label"), f.getlist("link_url")) if l or u]
    return {**{k: f.get(k, "") for k in ("slug", "accent", *LIMITS)}, "enabled": form_bool("enabled"),
            "noindex": form_bool("noindex"), "links": links}


def _parse(uid):
    """Formdan profil; (değerler, hata) döner. Büyük harf ve Türkçe harfler adreste dönüştürülür (Ş -> s)."""
    v = {k: form_str(k, n) for k, n in LIMITS.items()}
    v["bio"] = v["bio"].replace("\r\n", "\n")
    raw_slug = request.form.get("slug", "").strip()
    v.update(slug=fold(raw_slug), enabled=form_bool("enabled"), noindex=form_bool("noindex"),
             accent=form_choice("accent", ACCENTS, DEFAULT_ACCENT))
    error = slug_error(v["slug"], uid)
    if error:
        hint = slugify(raw_slug)
        if hint != v["slug"] and slug_error(hint, uid) is None:
            error += f" Öneri: “{hint}”"
        return v, error
    if v["email"] and not EMAIL_RE.match(v["email"]):
        return v, "E-posta adresi geçersiz."
    if v["phone"] and not (PHONE_RE.match(v["phone"]) and 7 <= len(re.sub(r"\D", "", v["phone"])) <= 15):
        return v, "Telefon numarası geçersiz (ör. 0532 123 45 67 ya da +90 532 123 45 67)."
    try:
        v["links"] = parse_links(request.form.getlist("link_label"), request.form.getlist("link_url"))
    except ValueError as e:
        return v, str(e)
    return v, None


def _render(form, current, status=200):
    url = public_url(current["slug"]) if current else None
    return render_template("profile/edit.html", form=form, current=current, url=url, qr=qr_svg(url) if url else None,
                           base_url=request.host_url + "p/", accents=ACCENTS, default_accent=DEFAULT_ACCENT,
                           max_links=MAX_LINKS), status


@bp.route("/profil-sayfasi/", methods=["GET", "POST"])
@login_required
def edit():
    uid = g.user["id"]
    current = get_profile(uid)
    if request.method == "GET":
        return _render(current or _defaults(g.user), current)
    v, error = _parse(uid)
    photo = None
    upload = request.files.get("photo")
    if not error and upload and upload.filename:
        try:
            photo = process_photo(upload, uid)
        except ValueError as e:
            error = str(e)
    if error:
        flash(error, "error")
        return _render(_form_values(), current, status=400)
    fields = ("slug", "enabled", "display_name", "headline", "bio", "avatar_emoji", "accent", "email", "phone",
              "location", "noindex")
    values = [v[k] for k in fields] + [json.dumps(v["links"], ensure_ascii=False)]
    db = get_db()
    try:
        db.execute(
            f"INSERT INTO public_profiles (user_id, {', '.join(fields)}, links) VALUES (?{', ?' * (len(fields) + 1)})"
            f" ON CONFLICT(user_id) DO UPDATE SET {', '.join(f'{k} = excluded.{k}' for k in (*fields, 'links'))},"
            " updated_at = CURRENT_TIMESTAMP",
            (uid, *values))
    except sqlite3.IntegrityError:   # aynı anda başkası aynı adresi aldıysa
        db.rollback()
        flash(f"“{v['slug']}” adresi başka biri tarafından alınmış.", "error")
        return _render(_form_values(), current, status=400)
    if photo:
        db.execute("UPDATE public_profiles SET photo = ? WHERE user_id = ?", (photo, uid))
    elif form_bool("remove_photo"):
        db.execute("UPDATE public_profiles SET photo = NULL WHERE user_id = ?", (uid,))
    db.commit()
    flash("🌐 Profil kaydedildi. " + ("Sayfan yayında." if v["enabled"]
                                     else "Sayfa kapalı; “Sayfa yayında” işaretlenince herkes görebilir."), "success")
    return redirect(url_for(".edit"))


@bp.route("/profil-sayfasi/qr.png")
@login_required
def qr_png():
    """Kartvizit / baskı için büyük QR."""
    p = get_profile(g.user["id"])
    if p is None:
        abort(404)
    buf = io.BytesIO()
    segno.make(public_url(p["slug"]), error="m").save(buf, kind="png", scale=QR_PNG_SCALE, border=4)
    buf.seek(0)
    return send_file(buf, mimetype="image/png", as_attachment=True, download_name=f"profil-{p['slug']}-qr.png")


# ---------- Herkese açık sayfa ----------
@bp.route("/p/<slug>")
def public(slug):
    p = _visible_or_404(slug)
    owner = _is_owner(p)
    if not owner and request.method == "GET":
        db = get_db()
        db.execute("UPDATE public_profiles SET views = views + 1 WHERE user_id = ?", (p["user_id"],))
        db.commit()
    light, dark = ACCENTS.get(p["accent"], ACCENTS[DEFAULT_ACCENT])[1:]
    resp = make_response(render_template(
        "profile/public.html", p=p, owner=owner, name=p["display_name"] or p["slug"], url=public_url(p["slug"]),
        contacts=contacts(p), accent_light=light, accent_dark=dark))
    resp.headers["Cache-Control"] = "no-cache"
    # Sayfada betik yok; kullanıcı metni kaçışlansa da ek önlem olarak dış kaynak, form, gömme kapalı
    resp.headers["Content-Security-Policy"] = ("default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
                                               "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    return _public_headers(resp, p)


@bp.route("/p/<slug>/foto.jpg")
def photo(slug):
    p = _visible_or_404(slug)
    row = query_one("SELECT photo FROM public_profiles WHERE user_id = ? AND photo IS NOT NULL", (p["user_id"],))
    if row is None:
        abort(404)
    resp = make_response(bytes(row["photo"]))
    resp.mimetype = "image/jpeg"
    # Kapalı profili sadece sahibi önizler: paylaşılan önbelleklerde kalmasın
    resp.headers["Cache-Control"] = "public, max-age=3600" if p["enabled"] else "private, no-store"
    return _public_headers(resp, p)


@bp.route("/p/<slug>/kisi.vcf")
def vcard(slug):
    p = _visible_or_404(slug)
    resp = make_response(build_vcard(p, public_url(p["slug"])).encode("utf-8"))
    resp.headers["Content-Type"] = "text/vcard; charset=utf-8"
    resp.headers["Content-Disposition"] = f'attachment; filename="{p["slug"]}.vcf"'
    return _public_headers(resp, p)
