"""🔗 Kısa Link & QR: kendi adresinle kısa link, Wi-Fi misafir kartı ve serbest QR.

- Yönetim /kisa-link/ (giriş gerekli); kısa adres /k/<kod> girişsiz açılır, 302 ile hedefe gider, tıklanma sayılır
  (link önizleme robotları sayılmaz)
- Kod tüm kullanıcılar arasında tekil; boşsa karışmayan harflerden (0/o/1/l/i yok) 6 karakter, elle yazılırsa
  küçük harf a-z, rakam ve tire (Türkçe ve büyük harfler dönüştürülür: Ş -> s); adreste büyük/küçük harf fark etmez
- Pasif, süresi dolmuş ve olmayan kod dışarıdan aynı 404; /k/<kod>/onizle hedefi gösterir (betiksiz, sayılmaz)
- Hedef sadece http(s)://; bu sitenin /k/ adresine giden link reddedilir (yönlendirme döngüsü olmasın)
- Çöp kutusundaki linkin kodu orada durduğu sürece başkasına verilmez (geri getirince çakışmasın)
- Wi-Fi kartı: standart WIFI: QR'ı (telefon kamerasıyla okutunca ağa bağlanır) ve yazdırılabilir misafir kartı;
  şifre sunucuda düz metin durur (Şifreli Kasa değildir)
- Serbest QR: metin / web adresi / telefon; hiçbir şey kaydedilmez
"""
import io
import json
import posixpath
import re
import secrets
import sqlite3
import unicodedata
from urllib.parse import unquote, urlsplit

import segno
from flask import Blueprint, abort, flash, g, make_response, redirect, render_template, request, send_file, url_for

from .. import trash
from ..auth import login_required
from ..db import execute, get_db, owned_or_404, query, query_one
from ..totp import qr_svg
from ..utils import fold, form_bool, form_choice, form_date, form_str, is_link_preview, redirect_back, today_str
from .links import domain_of
from .profile import PHONE_RE, RESERVED as PROFILE_RESERVED, clean_link_url, tel_number

# Yönetim /kisa-link altında, kısa adres /k/ altında: tek blueprint iki ayrı kökte (profile.py gibi)
bp = Blueprint("shortlinks", __name__)

CODE_MIN, CODE_MAX = 2, 40
CODE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
AUTO_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # 0/o ve 1/l/i birbirine karışmasın
AUTO_LEN = 6
# Profil adreslerinde ayrılmış kelimeler + bu modülün yolları ve taklitte sık kullanılanlar
RESERVED = PROFILE_RESERVED | {
    "kisa-link", "kisalt", "kisa", "link", "linkler", "onizle", "qr", "wifi", "yeni", "sil", "duzenle",
    "login", "logout", "signin", "signup", "hesap", "account", "dogrula", "verify", "odeme", "banka",
}
TARGET_MAX = 2000
TITLE_MAX = 120
QR_PNG_SCALE = 20    # baskı için büyük (kısa adreste ~660 px)
QR_PNG_PX = 1000     # serbest QR'da uzun metin dev resim olmasın
FREE_QR_MAX = 1000
TABS = {"linkler": "🔗 Kısa linkler", "wifi": "📶 Wi-Fi", "qr": "▦ Serbest QR"}
SECURITY = {"WPA": "WPA / WPA2 / WPA3", "WEP": "WEP (eski)", "nopass": "Açık ağ (şifresiz)"}
FREE_KINDS = {"text": "Metin", "url": "Web adresi", "tel": "Telefon"}
PUBLIC_ENDPOINTS = ("shortlinks.go", "shortlinks.preview")


class LinkError(ValueError):
    """Kullanıcıya gösterilecek doğrulama hatası."""


# ---------- Kod ----------
def _slug(text):
    """Öneri için: 'Yaz Kampanyası!' -> 'yaz-kampanyasi'."""
    text = unicodedata.normalize("NFKD", fold(text)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:CODE_MAX].strip("-")


def _trashed_owner(code):
    """Kod çöp kutusundaki bir kısa linkin mi? Öyleyse sahibinin id'si."""
    for row in query("SELECT user_id, payload FROM trash WHERE module = 'shortlinks'"):
        if any(r.get("code") == code for r in json.loads(row["payload"])["rows"].get("short_links", [])):
            return row["user_id"]
    return None


def code_error(code, user_id, link_id=None):
    """Kod uygunsa None, değilse hata metni. link_id: düzenlenen link (kendi kodu çakışma sayılmaz)."""
    if not (CODE_MIN <= len(code) <= CODE_MAX) or not CODE_RE.match(code):
        return (f"Kod {CODE_MIN}-{CODE_MAX} karakter olmalı; küçük harf (a-z), rakam ve tire içerebilir, "
                "tireyle başlayıp bitemez.")
    if code in RESERVED:
        return f"“{code}” ayrılmış bir kelime; başka bir kod seç."
    row = query_one("SELECT id, user_id FROM short_links WHERE code = ?", (code,))
    if row and row["id"] != link_id:
        return (f"“{code}” kodunu başka bir linkinde kullanıyorsun." if row["user_id"] == user_id
                else f"“{code}” kodu başka biri tarafından alınmış.")
    owner = _trashed_owner(code)
    if owner is None:
        return None
    if owner == user_id:
        return f"“{code}” kodu çöp kutusundaki bir linkinde; önce onu geri getir ya da kalıcı sil."
    return f"“{code}” kodu başka biri tarafından alınmış."


def suggest_code(raw, user_id, link_id=None):
    """Hatalı ya da alınmış koda boşta bir öneri: 'Yaz Kampanyası' -> 'yaz-kampanyasi', alınmışsa '-2', '-3'..."""
    base = _slug(raw)
    if len(base) < CODE_MIN:
        return None
    for n in range(1, 50):
        code = base if n == 1 else f"{base[:CODE_MAX - len(str(n)) - 1].rstrip('-')}-{n}"
        if code_error(code, user_id, link_id) is None:
            return code
    return None


def random_code(user_id=None):
    for _ in range(50):
        code = "".join(secrets.choice(AUTO_ALPHABET) for _ in range(AUTO_LEN))
        if code_error(code, user_id) is None:
            return code
    raise LinkError("Kod üretilemedi; tekrar dene.")


def pick_code(raw, user_id, link_id=None):
    """Formdaki kod -> geçerli kod (boşsa rastgele); hatalıysa öneriyle LinkError."""
    raw = (raw or "").strip()
    if not raw:
        return random_code(user_id)
    code = fold(raw)
    error = code_error(code, user_id, link_id)
    if error:
        hint = suggest_code(raw, user_id, link_id)
        if hint and hint != code:
            error += f" Öneri: “{hint}”"
        raise LinkError(error)
    return code


# ---------- Hedef adres ----------
def _is_own_short_link(parts):
    """Hedef bu sitenin /k/ adresi mi? Yol çözülerek bakılır ('//K/./abc', '/%6b/abc' de yakalanır)."""
    if (parts.hostname or "").rstrip(".") != urlsplit(request.host_url).hostname:
        return False
    path = posixpath.normpath(re.sub(r"/+", "/", "/" + unquote(parts.path).replace("\\", "/"))).lower()
    return path == "/k" or path.startswith("/k/")


def clean_target(raw):
    """Güvenli hedef adres; değilse LinkError. Şemasız 'ornek.com/sayfa' adresine https:// eklenir."""
    raw = (raw or "").strip()
    if not raw:
        raise LinkError("Hedef adres gerekli.")
    try:
        url = clean_link_url(raw)   # javascript:, data:, boşluklu, hostname'siz adresler burada düşer
    except ValueError:
        url = ""
    if not url.lower().startswith(("http://", "https://")):
        raise LinkError("Hedef adres geçersiz: http:// ya da https:// ile başlamalı (ör. https://ornek.com/sayfa).")
    if len(url) > TARGET_MAX:
        raise LinkError(f"Hedef adres en fazla {TARGET_MAX} karakter olabilir.")
    if _is_own_short_link(urlsplit(url)):
        raise LinkError("Hedef bu sitedeki bir kısa link olamaz (yönlendirme döngüsü olur); asıl adresi yaz.")
    return url


# ---------- Ortak ----------
def short_url(code):
    return url_for("shortlinks.go", code=code, _external=True)


def is_expired(link):
    """Son kullanma günü dahil açılır, ertesi gün kapanır."""
    return bool(link["expires_at"]) and link["expires_at"] < today_str()


def create_link(user_id, target, code="", title="", expires_at=None):
    """Yeni kısa link (web ve Telegram /kisalt); satırı döner. Hatalıysa LinkError."""
    target = clean_target(target)
    code = pick_code(code, user_id)
    if expires_at and expires_at < today_str():
        raise LinkError("Son kullanma tarihi geçmişte olamaz.")
    db = get_db()
    try:
        link_id = db.execute(
            "INSERT INTO short_links (user_id, code, target, title, expires_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, code, target, (title or "").strip()[:TITLE_MAX], expires_at)).lastrowid
    except sqlite3.IntegrityError:   # aynı anda başkası aynı kodu aldıysa
        db.rollback()
        raise LinkError(f"“{code}” kodu az önce alındı; başka bir kod dene.")
    db.commit()
    return query_one("SELECT * FROM short_links WHERE id = ?", (link_id,))


def _png(data, filename):
    """İndirilecek büyük QR (baskı için); uzun içerikte de en fazla ~1000 px."""
    qr = segno.make(data, error="m")
    scale = max(4, min(QR_PNG_SCALE, QR_PNG_PX // qr.symbol_size(border=4)[0]))
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=scale, border=4)
    buf.seek(0)
    return send_file(buf, mimetype="image/png", as_attachment=True, download_name=filename)


# ---------- Wi-Fi ----------
def _wesc(text):
    """WIFI: içeriğinde ters bölü, noktalı virgül, virgül, iki nokta ve çift tırnak ters bölüyle kaçışlanır."""
    return re.sub(r'([\\;,:"])', r"\\\1", text)


def wifi_payload(card):
    """Standart Wi-Fi QR içeriği: 'WIFI:T:WPA;S:<ağ>;P:<şifre>;H:true;;' (açık ağda P, görünür ağda H yok)."""
    parts = [f"T:{card['security']}", f"S:{_wesc(card['ssid'])}"]
    if card["security"] != "nopass":
        parts.append(f"P:{_wesc(card['password'])}")
    if card["hidden"]:
        parts.append("H:true")
    return "WIFI:" + ";".join(parts) + ";;"


def _wifi_parse():
    """Formdan Wi-Fi kartı; (değerler, hata) döner. Şifre olduğu gibi alınır (baştaki/sondaki boşluk da şifredir)."""
    v = {"title": form_str("title", 60), "ssid": form_str("ssid", 64), "password": request.form.get("password", "")[:64],
         "security": form_choice("security", SECURITY, "WPA"), "hidden": form_bool("is_hidden")}
    if v["security"] == "nopass":
        v["password"] = ""
    if not v["ssid"]:
        return v, "Ağ adı (SSID) gerekli."
    if len(v["ssid"].encode("utf-8")) > 32:
        return v, "Ağ adı en fazla 32 bayt olabilir (Türkçe harfler 2 bayt sayılır)."
    pw = v["password"]
    if v["security"] == "WPA" and not (8 <= len(pw) <= 63 or re.fullmatch(r"[0-9a-fA-F]{64}", pw)):
        return v, "WPA şifresi 8-63 karakter olmalı."
    if v["security"] == "WEP" and not pw:
        return v, "WEP şifresi gerekli."
    v["title"] = v["title"] or v["ssid"]
    return v, None


# ---------- Serbest QR ----------
def free_qr_data(kind, text):
    """QR'a yazılacak içerik; hatalıysa LinkError. Telefon 'tel:+90...' olur, web adresine gerekirse https:// eklenir."""
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        raise LinkError("QR'a dönüştürülecek bir şey yaz.")
    if len(text) > FREE_QR_MAX:
        raise LinkError(f"En fazla {FREE_QR_MAX} karakter yazılabilir.")
    if kind == "url":
        try:
            url = clean_link_url(text)
        except ValueError:
            url = ""
        if not url.lower().startswith(("http://", "https://")):
            raise LinkError("Web adresi geçersiz (ör. https://ornek.com).")
        return url
    if kind == "tel":
        if not (PHONE_RE.match(text) and 7 <= len(re.sub(r"\D", "", text)) <= 15):
            raise LinkError("Telefon numarası geçersiz (ör. 0532 123 45 67 ya da +90 532 123 45 67).")
        return "tel:" + tel_number(text)
    return text


# ---------- Yönetim ----------
def _render_index(tab, status=200, **ctx):
    uid = g.user["id"]
    tab = tab if tab in TABS else "linkler"
    counts = {"linkler": query_one("SELECT COUNT(*) AS n FROM short_links WHERE user_id = ?", (uid,))["n"],
              "wifi": query_one("SELECT COUNT(*) AS n FROM wifi_cards WHERE user_id = ?", (uid,))["n"]}
    if tab == "linkler":
        ctx.setdefault("form", {})
        ctx["links"] = query("SELECT * FROM short_links WHERE user_id = ? ORDER BY id DESC", (uid,))
    elif tab == "wifi":
        ctx.setdefault("wifi_values", {"security": "WPA"})
        ctx["cards"] = query("SELECT * FROM wifi_cards WHERE user_id = ? ORDER BY id DESC", (uid,))
    else:
        ctx.setdefault("qr_form", {"kind": "text", "data": ""})
    return render_template(
        "shortlinks/index.html", tab=tab, tabs=TABS, counts=counts, base_url=request.url_root + "k/",
        short_url=short_url, domain=domain_of, is_expired=is_expired, security=SECURITY, free_kinds=FREE_KINDS,
        code_min=CODE_MIN, code_max=CODE_MAX, **ctx), status


def _link_form():
    """Hata sonrası formu kullanıcının yazdıklarıyla yeniden doldurmak için."""
    f = {k: request.form.get(k, "").strip() for k in ("target", "code", "title", "expires_at")}
    f["active"] = form_bool("active")
    return f


@bp.route("/kisa-link/")
@login_required
def index():
    return _render_index(request.args.get("tab", "linkler"))


@bp.route("/kisa-link/yeni", methods=["POST"])
@login_required
def create():
    f = _link_form()
    try:
        link = create_link(g.user["id"], f["target"], f["code"], f["title"], form_date("expires_at"))
    except LinkError as e:
        flash(str(e), "error")
        return _render_index("linkler", status=400, form=f)
    flash(f"🔗 Kısa link hazır: {short_url(link['code'])}", "success")
    return redirect(url_for(".index"))


def _render_detail(link, form, status=200):
    url = short_url(link["code"])
    return render_template("shortlinks/detail.html", link=link, form=form, url=url, qr=qr_svg(url),
                           domain=domain_of, expired=is_expired(link), base_url=request.url_root + "k/",
                           code_min=CODE_MIN, code_max=CODE_MAX), status


@bp.route("/kisa-link/<int:link_id>", methods=["GET", "POST"])
@login_required
def detail(link_id):
    uid = g.user["id"]
    link = owned_or_404("short_links", link_id, uid)
    if request.method == "GET":
        return _render_detail(link, dict(link))
    f = _link_form()
    expires_at = form_date("expires_at")
    try:
        target = clean_target(f["target"])
        # Boş bırakılan ya da aynı kalan kod değişmez (basılmış QR'lar bozulmasın)
        code = link["code"] if fold(f["code"]) in ("", link["code"]) else pick_code(f["code"], uid, link_id)
        if expires_at and expires_at != link["expires_at"] and expires_at < today_str():
            raise LinkError("Son kullanma tarihi geçmişte olamaz.")
    except LinkError as e:
        flash(str(e), "error")
        return _render_detail(link, f, status=400)
    db = get_db()
    try:
        db.execute("UPDATE short_links SET target = ?, code = ?, title = ?, expires_at = ?, active = ?,"
                   " updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
                   (target, code, f["title"][:TITLE_MAX], expires_at, f["active"], link_id, uid))
    except sqlite3.IntegrityError:
        db.rollback()
        flash(f"“{code}” kodu az önce alındı; başka bir kod dene.", "error")
        return _render_detail(link, f, status=400)
    db.commit()
    if code != link["code"]:
        flash(f"Kod değişti: eski kısa adres ({short_url(link['code'])}) ve basılmış QR'lar artık açılmaz.", "warning")
    flash("🔗 Kısa link kaydedildi.", "success")
    return redirect(url_for(".detail", link_id=link_id))


@bp.route("/kisa-link/<int:link_id>/durum", methods=["POST"])
@login_required
def toggle(link_id):
    link = owned_or_404("short_links", link_id, g.user["id"])
    execute("UPDATE short_links SET active = 1 - active, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            (link_id, g.user["id"]))
    flash("⏸️ Link durduruldu: kısa adres artık açılmaz." if link["active"] else "▶️ Link yeniden açıldı.", "success")
    return redirect_back("shortlinks.index")


@bp.route("/kisa-link/<int:link_id>/sil", methods=["POST"])
@login_required
def delete(link_id):
    link = owned_or_404("short_links", link_id, g.user["id"])
    trash.move(g.user["id"], "shortlinks", f"🔗 {link['title'] or '/k/' + link['code']}", ("short_links", link_id))
    flash(trash.notice("Kısa link"), "success")
    return redirect(url_for(".index"))


@bp.route("/kisa-link/<int:link_id>/qr.png")
@login_required
def link_qr(link_id):
    link = owned_or_404("short_links", link_id, g.user["id"])
    return _png(short_url(link["code"]), f"kisa-link-{link['code']}-qr.png")


# ---------- Wi-Fi kartları ----------
@bp.route("/kisa-link/wifi/yeni", methods=["POST"])
@login_required
def wifi_create():
    v, error = _wifi_parse()
    if error:
        flash(error, "error")
        return _render_index("wifi", status=400, wifi_values=v)
    card_id = execute("INSERT INTO wifi_cards (user_id, title, ssid, password, security, hidden) VALUES (?, ?, ?, ?, ?, ?)",
                      (g.user["id"], v["title"], v["ssid"], v["password"], v["security"], v["hidden"])).lastrowid
    flash("📶 Wi-Fi kartı hazır; yazdırıp misafirlerin göreceği yere koyabilirsin.", "success")
    return redirect(url_for(".wifi_card", card_id=card_id))


def _render_card(card, form, status=200):
    return render_template("shortlinks/wifi_card.html", card=card, form=form, qr=qr_svg(wifi_payload(card)),
                           security=SECURITY, open_edit=status != 200), status


@bp.route("/kisa-link/wifi/<int:card_id>/kart")
@login_required
def wifi_card(card_id):
    card = owned_or_404("wifi_cards", card_id, g.user["id"])
    return _render_card(card, dict(card))


@bp.route("/kisa-link/wifi/<int:card_id>", methods=["POST"])
@login_required
def wifi_edit(card_id):
    card = owned_or_404("wifi_cards", card_id, g.user["id"])
    v, error = _wifi_parse()
    if error:
        flash(error, "error")
        return _render_card(card, v, status=400)
    execute("UPDATE wifi_cards SET title = ?, ssid = ?, password = ?, security = ?, hidden = ? WHERE id = ? AND user_id = ?",
            (v["title"], v["ssid"], v["password"], v["security"], v["hidden"], card_id, g.user["id"]))
    flash("📶 Wi-Fi kartı kaydedildi.", "success")
    return redirect(url_for(".wifi_card", card_id=card_id))


@bp.route("/kisa-link/wifi/<int:card_id>/qr.png")
@login_required
def wifi_qr(card_id):
    card = owned_or_404("wifi_cards", card_id, g.user["id"])
    return _png(wifi_payload(card), f"wifi-{_slug(card['title']) or card_id}-qr.png")


@bp.route("/kisa-link/wifi/<int:card_id>/sil", methods=["POST"])
@login_required
def wifi_delete(card_id):
    card = owned_or_404("wifi_cards", card_id, g.user["id"])
    trash.move(g.user["id"], "shortlinks", f"📶 {card['title']}", ("wifi_cards", card_id))
    flash(trash.notice("Wi-Fi kartı"), "success")
    return redirect(url_for(".index", tab="wifi"))


# ---------- Serbest QR (kaydedilmez) ----------
def _free_form():
    return {"kind": form_choice("kind", FREE_KINDS, "text"), "data": request.form.get("data", "")}


def _qr_error(e):
    return str(e) if isinstance(e, LinkError) else "Metin QR koda sığmayacak kadar uzun; kısalt."


@bp.route("/kisa-link/qr", methods=["POST"])
@login_required
def free_qr():
    form = _free_form()
    try:
        data = free_qr_data(form["kind"], form["data"])
        svg = qr_svg(data)
    except (LinkError, segno.DataOverflowError) as e:
        flash(_qr_error(e), "error")
        return _render_index("qr", status=400, qr_form=form)
    return _render_index("qr", qr_form=form, qr=svg, qr_data=data)


@bp.route("/kisa-link/qr.png", methods=["POST"])
@login_required
def free_qr_png():
    form = _free_form()
    try:
        return _png(free_qr_data(form["kind"], form["data"]), "qr.png")
    except (LinkError, segno.DataOverflowError) as e:
        flash(_qr_error(e), "error")
        return redirect(url_for(".index", tab="qr"))


# ---------- Herkese açık kısa adres ----------
def _live_or_404(code):
    """Açık ve süresi dolmamış link. Pasif, süresi dolmuş ve olmayan kod dışarıdan aynı 404."""
    link = query_one("SELECT * FROM short_links WHERE code = ?", (fold(code),))
    if link is None or not link["active"] or is_expired(link):
        abort(404)
    return link


@bp.after_request
def _public_headers(resp):
    """Kısa adres ve önizleme (404 dahil): önbelleğe alınmaz, dizine eklenmez, hedefe kısa adres sızmaz."""
    if request.endpoint in PUBLIC_ENDPOINTS:
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@bp.route("/k/<code>")
def go(code):
    link = _live_or_404(code)
    # HEAD istekleri ve WhatsApp/Telegram gibi uygulamaların link önizlemesi tıklanma sayılmaz
    if request.method == "GET" and not is_link_preview(request.user_agent.string):
        db = get_db()
        db.execute("UPDATE short_links SET clicks = clicks + 1, last_click_at = CURRENT_TIMESTAMP WHERE id = ?",
                   (link["id"],))
        db.commit()
    return redirect(link["target"], code=302)


@bp.route("/k/<code>/onizle")
def preview(code):
    """Tıklamadan önce nereye gidildiğini gösterir; tıklanma sayılmaz."""
    link = _live_or_404(code)
    resp = make_response(render_template("shortlinks/preview.html", link=link, domain=domain_of(link["target"]),
                                         url=short_url(link["code"])))
    # Sayfada betik yok; ek önlem olarak dış kaynak, form, gömme kapalı (profile/public.html gibi)
    resp.headers["Content-Security-Policy"] = ("default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
                                               "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    return resp
