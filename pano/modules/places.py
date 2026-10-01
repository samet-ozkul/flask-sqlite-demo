"""🗺️ Harita: gezdiğim / gitmek istediğim şehirler, şehir notları, yerler (yemek, gezilecek...) ve park yeri.

- Harita tarayıcıda Leaflet + OpenStreetMap döşemeleriyle çizilir (CDN; sunucu dışarıya bağlanmaz).
  Kütüphane yüklenemezse sayfa liste olarak çalışır. Çizim ve konum kodu: static/places.js
- Şehir eklerken aday listesi Open-Meteo geocoding'den gelir (PythonAnywhere izin listesinde).
- Yerin konumu haritaya dokunarak, tarayıcının konumuyla ya da Google / Apple / OpenStreetMap linki
  yapıştırarak verilir. Kısa linkler (maps.app.goo.gl) koordinat içermez ve sunucu onları açamaz.
  Konumsuz yer de olur: listede görünür, haritada görünmez.
- Park yeri kullanıcı başına tek kayıttır; her "Park ettim" üzerine yazar. Telegram'a konum gönderilince
  park yeri ya da yer olarak kaydetmek sorulur (bot_commands.py).
"""
import math
import re
import urllib.parse
from datetime import date, datetime, timezone

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .. import external, trash
from .. import todo_reminders as todo
from ..auth import login_required
from ..db import execute, owned_or_404, query, query_one
from ..utils import MONTHS_TR, fold, form_choice, form_int, form_str, local_dt, redirect_back

bp = Blueprint("places", __name__, url_prefix="/harita")

# anahtar -> (ad, ikon, grup başlığı)
CATEGORIES = {
    "food": ("Yemek", "🍽️", "Yemek yerleri"),
    "cafe": ("Kafe", "☕", "Kafeler"),
    "sight": ("Gezilecek", "🏛️", "Gezilecek yerler"),
    "stay": ("Konaklama", "🏨", "Konaklama"),
    "shop": ("Alışveriş", "🛍️", "Alışveriş"),
    "nature": ("Doğa", "🌳", "Doğa"),
    "other": ("Diğer", "📍", "Diğer yerler"),
}
STATUSES = {"visited": ("Gezdim", "✅"), "wish": ("Gitmek istiyorum", "🔖")}
NEAR_CITY_KM = 30           # Telegram'dan gelen konum bu mesafedeki şehre bağlanır
HOME_VIEW = {"lat": 39.0, "lon": 35.0, "zoom": 5}  # konum yoksa Türkiye

# ---------- Harita linki çözümleme ----------
URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
NUM = r"(-?\d{1,3}(?:\.\d+)?)"
PAIR_RE = re.compile(rf"^\s*(?:loc:)?\s*{NUM}\s*,\s*\+?{NUM}")
GEO_RE = re.compile(rf"\bgeo:{NUM},{NUM}", re.IGNORECASE)
# Apple "Koordinatları kopyala": 37.87461° N, 32.49321° E (Türkçe: K/G, D/B; virgüllü ondalık da olur)
DEG_RE = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*°\s*([NSKG])\s*[,;]?\s*(\d{1,3}(?:[.,]\d+)?)\s*°\s*([EWDB])", re.IGNORECASE)
PIN_RE = re.compile(r"!3d(-?\d{1,3}\.\d+)!4d(-?\d{1,3}\.\d+)")              # Google yer sayfası: asıl nokta
AT_RE = re.compile(r"@(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)")                   # Google: harita merkezi
PATH_PAIR_RE = re.compile(r"/(?:place|search)/(-?\d{1,3}\.\d+),\s*\+?(-?\d{1,3}\.\d+)")
OSM_HASH_RE = re.compile(r"map=\d{1,2}(?:\.\d+)?/(-?\d{1,3}\.\d+)/(-?\d{1,3}\.\d+)")
PAIR_PARAMS = ("query", "q", "ll", "sll", "coordinate", "destination", "daddr", "center")
SHORT_HOSTS = ("maps.app.goo.gl", "goo.gl", "g.co", "share.google", "maps.apple", "osm.org")


def _pair(lat, lon):
    """Geçerli koordinat çifti (6 basamak) ya da None."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return round(lat, 6), round(lon, 6)


def _from_url(raw):
    parts = urllib.parse.urlsplit(raw)
    path = urllib.parse.unquote(parts.path)
    params = urllib.parse.parse_qs(parts.query)
    if "yandex" in (parts.hostname or ""):  # Yandex Haritalar sırayı tersine yazar: boylam,enlem
        for key in ("pt", "ll"):
            m = PAIR_RE.match((params.get(key) or [""])[0])
            if m and _pair(m.group(2), m.group(1)):
                return _pair(m.group(2), m.group(1))
        return None
    m = PIN_RE.search(path)
    if m:
        return _pair(*m.groups())
    if params.get("mlat") and params.get("mlon"):
        return _pair(params["mlat"][0], params["mlon"][0])
    for key in PAIR_PARAMS:
        for value in params.get(key, []):
            m = PAIR_RE.match(value)
            if m and _pair(*m.groups()):
                return _pair(*m.groups())
    for rx, text in ((PATH_PAIR_RE, path), (AT_RE, path), (OSM_HASH_RE, urllib.parse.unquote(parts.fragment))):
        m = rx.search(text)
        if m and _pair(*m.groups()):
            return _pair(*m.groups())
    return None


def parse_map_link(text):
    """Google Maps / Apple Haritalar / OpenStreetMap linkinden ya da '37.87, 32.48' yazısından (lat, lon).

    Paylaşım metninin içindeki link de bulunur ("Mevlana Müzesi https://..."). Bulunamazsa None.
    """
    text = (text or "").strip()
    found = URL_IN_TEXT_RE.search(text)
    if found:
        return _from_url(found.group(0).rstrip(").,;!?"))
    m = DEG_RE.search(text)
    if m:
        lat, ns, lon, ew = m.groups()
        lat, lon = float(lat.replace(",", ".")), float(lon.replace(",", "."))
        return _pair(-lat if ns.upper() in "SG" else lat, -lon if ew.upper() in "WB" else lon)
    m = GEO_RE.search(text) or PAIR_RE.match(text)
    return _pair(*m.groups()) if m else None


def is_short_link(text):
    found = URL_IN_TEXT_RE.search(text or "")
    host = (urllib.parse.urlsplit(found.group(0)).hostname or "").lower() if found else ""
    return host in SHORT_HOSTS


def link_problem(text):
    """Çözülemeyen link için kullanıcıya açıklama."""
    if is_short_link(text):
        return ("Kısa linkler (maps.app.goo.gl gibi) koordinat içermiyor ve sunucu onları açamıyor. Linki tarayıcıda açıp "
                "adres çubuğundaki uzun linki yapıştır ya da Google Maps'te noktaya basılı tutup çıkan koordinatı kopyala.")
    return "Linkte koordinat bulunamadı. Haritaya dokunarak ya da “📍 Şu anki konumum” ile konum verebilirsin."


def directions_url(lat, lon):
    return f"https://www.google.com/maps/dir/?api=1&destination={lat},{lon}"


def osm_url(lat, lon):
    return f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=17/{lat}/{lon}"


def distance_km(lat1, lon1, lat2, lon2):
    """İki nokta arası kuş uçuşu mesafe (km)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


# ---------- Tarih ----------
def parse_visit(raw):
    """'2024-07', '2024-07-15', '07.2024', '15.07.2024' -> 'YYYY-MM' ya da 'YYYY-MM-DD'; anlaşılmazsa None."""
    raw = (raw or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", raw)
    if m:
        year, month, day = m.groups()
    else:
        m = re.fullmatch(r"(?:(\d{1,2})[./])?(\d{1,2})[./](\d{4})", raw)
        if not m:
            return None
        day, month, year = m.groups()
    try:
        d = date(int(year), int(month), int(day or 1))
    except ValueError:
        return None
    if not 1900 <= d.year <= 2100:
        return None
    return d.isoformat() if day else d.isoformat()[:7]


def visit_label(value):
    """'2024-07' -> 'Temmuz 2024', '2024-07-15' -> '15 Temmuz 2024'."""
    parts = (value or "").split("-")
    if len(parts) < 2 or not all(p.isdigit() for p in parts) or not 1 <= int(parts[1]) <= 12:
        return value or ""
    label = f"{MONTHS_TR[int(parts[1]) - 1]} {parts[0]}"
    return f"{int(parts[2])} {label}" if len(parts) > 2 else label


def ago(value):
    """SQLite UTC zamanı -> 'az önce', '25 dk önce', '3 saat önce', '2 gün önce'."""
    try:
        dt = datetime.fromisoformat(str(value).replace(" ", "T")).replace(tzinfo=timezone.utc)
    except ValueError:
        return ""
    minutes = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
    if minutes < 1:
        return "az önce"
    if minutes < 60:
        return f"{minutes} dk önce"
    if minutes < 48 * 60:
        return f"{minutes // 60} saat önce"
    return f"{minutes // 1440} gün önce"


# ---------- Ortak ----------
def _common():
    return {"categories": CATEGORIES, "statuses": STATUSES, "directions_url": directions_url, "osm_url": osm_url,
            "visit_label": visit_label}


def _cities(uid):
    """Kullanıcının şehirleri: önce gezilenler (son gidilen önce), sonra gidilecekler; ad sırası Türkçe."""
    rows = sorted(query("SELECT * FROM cities WHERE user_id = ?", (uid,)), key=lambda c: fold(c["name"]))
    rows.sort(key=lambda c: c["visited_on"] or "", reverse=True)
    rows.sort(key=lambda c: c["status"] != "visited")
    return rows


def _parking(uid):
    return query_one("SELECT * FROM parking WHERE user_id = ?", (uid,))


def _home_view():
    u = g.user
    return {"lat": u["lat"], "lon": u["lon"], "zoom": 12} if u["lat"] is not None else HOME_VIEW


def _map_data(cities=(), places=(), parking=None, view=None, max_zoom=12, counts=None):
    """Sayfaya gömülen harita verisi (sadece konumu olanlar). Adlar JS'te textContent ile basılır."""
    counts = counts or {}
    return {
        "cities": [{"name": c["name"], "country": c["country"], "lat": c["lat"], "lon": c["lon"], "status": c["status"],
                    "count": counts.get(c["id"], 0), "url": url_for(".city", city_id=c["id"])}
                   for c in cities if c["lat"] is not None],
        "places": [{"name": p["name"], "icon": CATEGORIES[p["category"]][1], "category": CATEGORIES[p["category"]][0],
                    "status": p["status"], "rating": p["rating"], "lat": p["lat"], "lon": p["lon"],
                    "url": url_for(".edit", place_id=p["id"])}
                   for p in places if p["lat"] is not None],
        "parking": {"lat": parking["lat"], "lon": parking["lon"], "note": parking["note"],
                    "saved": local_dt(parking["saved_at"])} if parking else None,
        "view": view or _home_view(),
        "max_zoom": max_zoom,
    }


def _form_location(current=(None, None)):
    """Formdaki konum: yapıştırılan link/koordinat önce, sonra haritadan ya da tarayıcıdan gelen gizli lat/lon.
    Formda gizli konum alanı yoksa (şehir düzenleme) mevcut konum korunur."""
    link_text = form_str("maplink", 2000)
    if link_text:
        pair = parse_map_link(link_text)
        if pair:
            return pair
        flash(link_problem(link_text), "warning")
    if "lat" not in request.form:
        return current
    return _pair(request.form.get("lat"), request.form.get("lon")) or (None, None)


def _own_city_id(uid, raw):
    city_id = int(raw) if str(raw or "").isdigit() else None
    if city_id and query_one("SELECT 1 FROM cities WHERE id = ? AND user_id = ?", (city_id, uid)):
        return city_id
    return None


def _place_values(uid):
    rating = form_int("rating")
    v = {
        "name": form_str("name", 100),
        "category": form_choice("category", CATEGORIES, "other"),
        "status": form_choice("status", STATUSES, "wish"),
        "rating": rating if rating in (1, 2, 3, 4, 5) else None,
        "city_id": _own_city_id(uid, request.form.get("city_id")),
        "address": form_str("address", 200),
        "note": form_str("note", 2000),
    }
    v["lat"], v["lon"] = _form_location()
    return v


def _city_values():
    raw = form_str("visited_on", 20)
    v = {"status": form_choice("status", STATUSES, "visited"), "visited_on": parse_visit(raw),
         "note": form_str("note", 5000), "country": form_str("country", 60)}
    if raw and not v["visited_on"]:
        flash("Tarih anlaşılamadı (ör. 2024-07 ya da 15.07.2024); boş bırakıldı.", "warning")
    return v


def _place_label(p):
    return f"{CATEGORIES[p['category']][1]} {p['name']}"


# ---------- Ana sayfa ----------
@bp.route("/")
@login_required
def index():
    uid = g.user["id"]
    category = request.args.get("kategori", "")
    category = category if category in CATEGORIES else ""
    status = request.args.get("durum", "")
    status = status if status in STATUSES else ""
    cities = _cities(uid)
    sql, args = "SELECT * FROM places WHERE user_id = ?", [uid]
    if category:
        sql += " AND category = ?"
        args.append(category)
    if status:
        sql += " AND status = ?"
        args.append(status)
    places = sorted(query(sql, args), key=lambda p: (p["status"] != "visited", fold(p["name"])))
    counts = {}
    for p in places:
        if p["city_id"]:
            counts[p["city_id"]] = counts.get(p["city_id"], 0) + 1
    shown = [c for c in cities if not status or c["status"] == status]
    visited = [c for c in cities if c["status"] == "visited"]
    total = query_one("SELECT COUNT(*) AS n, COALESCE(SUM(status = 'visited'), 0) AS v FROM places WHERE user_id = ?", (uid,))
    stats = {
        "cities": len(visited), "wish_cities": len(cities) - len(visited),
        "countries": len({fold(c["country"]) for c in visited if c["country"]}),
        "places": total["n"], "visited_places": total["v"],
    }
    parking = _parking(uid)
    return render_template(
        "places/index.html", cities=shown, loose=[p for p in places if p["city_id"] is None], counts=counts,
        stats=stats, parking=parking, parked_ago=ago(parking["saved_at"]) if parking else "",
        category=category, status=status, has_any=bool(cities) or total["n"] > 0,
        data=_map_data(shown, places, parking, counts=counts), **_common())


# ---------- Şehirler ----------
@bp.route("/sehir/yeni", methods=["GET", "POST"])
@login_required
def city_new():
    uid = g.user["id"]
    if request.method == "POST":
        q, pick = form_str("q", 100), request.form.get("pick", "")
        if pick.isdigit():
            name, country = form_str(f"name_{pick}", 100), form_str(f"country_{pick}", 60)
            coords = _pair(request.form.get(f"lat_{pick}"), request.form.get(f"lon_{pick}")) or (None, None)
        else:  # listede yok: sadece adıyla (haritada görünmez, sonra linkle konum verilebilir)
            name, country, coords = q, form_str("country", 60), (None, None)
        if not name:
            flash("Şehir adı gerekli.", "warning")
            return redirect(url_for(".city_new"))
        same = next((c for c in query("SELECT id, name, country FROM cities WHERE user_id = ?", (uid,))
                     if fold(c["name"]) == fold(name) and fold(c["country"]) == fold(country)), None)
        if same:
            flash(f"{name} zaten haritanda.", "warning")
            return redirect(url_for(".city", city_id=same["id"]))
        v = _city_values()
        city_id = execute(
            "INSERT INTO cities (user_id, name, country, lat, lon, status, visited_on, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, name, country, *coords, v["status"], v["visited_on"], v["note"]),
        ).lastrowid
        flash(f"🏙️ {name} eklendi. Şimdi yemek yerlerini, gezilecek yerleri ekleyebilirsin.", "success")
        return redirect(url_for(".city", city_id=city_id))
    q = request.args.get("q", "").strip()[:100]
    candidates, error = [], None
    if q:
        candidates = external.geocode_many(q)
        if candidates is None:
            candidates, error = [], "Konum servisine şu an ulaşılamadı; şehri sadece adıyla ekleyebilirsin."
    return render_template("places/city_new.html", q=q, candidates=candidates, error=error, **_common())


@bp.route("/sehir/<int:city_id>")
@login_required
def city(city_id):
    uid = g.user["id"]
    c = owned_or_404("cities", city_id, uid)
    places = sorted(query("SELECT * FROM places WHERE city_id = ? AND user_id = ?", (city_id, uid)),
                    key=lambda p: (p["status"] != "visited", -(p["rating"] or 0), fold(p["name"])))
    groups = [(key, CATEGORIES[key], [p for p in places if p["category"] == key]) for key in CATEGORIES]
    view = {"lat": c["lat"], "lon": c["lon"], "zoom": 12} if c["lat"] is not None else None
    return render_template(
        "places/city.html", city=c, places=places, groups=[grp for grp in groups if grp[2]], cities=_cities(uid),
        data=_map_data([c], places, view=view, max_zoom=15, counts={city_id: len(places)}), **_common())


@bp.route("/sehir/<int:city_id>/duzenle", methods=["POST"])
@login_required
def city_edit(city_id):
    uid = g.user["id"]
    c = owned_or_404("cities", city_id, uid)
    v = _city_values()
    lat, lon = _form_location((c["lat"], c["lon"]))
    execute("UPDATE cities SET name = ?, country = ?, lat = ?, lon = ?, status = ?, visited_on = ?, note = ?"
            " WHERE id = ? AND user_id = ?",
            (form_str("name", 100) or c["name"], v["country"], lat, lon, v["status"], v["visited_on"], v["note"],
             city_id, uid))
    flash("Şehir güncellendi.", "success")
    return redirect(url_for(".city", city_id=city_id))


@bp.route("/sehir/<int:city_id>/sil", methods=["POST"])
@login_required
def city_delete(city_id):
    uid = g.user["id"]
    c = owned_or_404("cities", city_id, uid)
    n = query_one("SELECT COUNT(*) AS n FROM places WHERE city_id = ?", (city_id,))["n"]
    # Yerleri de kutuya girer; geri getirilince şehirle birlikte döner
    trash.move(uid, "places", f"🏙️ {c['name']}" + (f" ({n} yer)" if n else ""), ("cities", city_id),
               children=[("places", "city_id = ?")])
    flash(trash.notice(c["name"] + (f" ve {n} yer" if n else "")), "success")
    return redirect(url_for(".index"))


# ---------- Yerler ----------
@bp.route("/yer/yeni", methods=["GET", "POST"])
@login_required
def create():
    uid = g.user["id"]
    if request.method == "POST":
        v = _place_values(uid)
        if not v["name"]:
            flash("Yerin adı gerekli.", "warning")
            return redirect(url_for(".create", sehir=v["city_id"]))
        execute(
            "INSERT INTO places (user_id, city_id, name, category, status, rating, lat, lon, address, note)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, v["city_id"], v["name"], v["category"], v["status"], v["rating"], v["lat"], v["lon"], v["address"],
             v["note"]),
        )
        where = "" if v["lat"] is not None else " Konumu yok: listede görünür, haritada görünmez."
        flash(f"{_place_label(v)} eklendi.{where}", "success")
        return redirect(url_for(".city", city_id=v["city_id"]) if v["city_id"] else url_for(".index"))
    city_id = _own_city_id(uid, request.args.get("sehir"))
    c = query_one("SELECT * FROM cities WHERE id = ?", (city_id,)) if city_id else None
    view = {"lat": c["lat"], "lon": c["lon"], "zoom": 13} if c and c["lat"] is not None else None
    return render_template("places/place.html", place=None, city_id=city_id, cities=_cities(uid),
                           data=_map_data(view=view), **_common())


@bp.route("/yer/<int:place_id>", methods=["GET", "POST"])
@login_required
def edit(place_id):
    uid = g.user["id"]
    p = owned_or_404("places", place_id, uid)
    if request.method == "POST":
        v = _place_values(uid)
        execute(
            "UPDATE places SET city_id = ?, name = ?, category = ?, status = ?, rating = ?, lat = ?, lon = ?, address = ?,"
            " note = ? WHERE id = ? AND user_id = ?",
            (v["city_id"], v["name"] or p["name"], v["category"], v["status"], v["rating"], v["lat"], v["lon"],
             v["address"], v["note"], place_id, uid),
        )
        flash("Yer güncellendi.", "success")
        return redirect(url_for(".city", city_id=v["city_id"]) if v["city_id"] else url_for(".index"))
    c = query_one("SELECT * FROM cities WHERE id = ?", (p["city_id"],)) if p["city_id"] else None
    if p["lat"] is not None:
        view = {"lat": p["lat"], "lon": p["lon"], "zoom": 16}
    else:
        view = {"lat": c["lat"], "lon": c["lon"], "zoom": 13} if c and c["lat"] is not None else None
    return render_template("places/place.html", place=p, city=c, city_id=p["city_id"], cities=_cities(uid),
                           data=_map_data(view=view), **_common())


@bp.route("/yer/<int:place_id>/durum", methods=["POST"])
@login_required
def toggle_status(place_id):
    p = owned_or_404("places", place_id, g.user["id"])
    new = "wish" if p["status"] == "visited" else "visited"
    execute("UPDATE places SET status = ? WHERE id = ?", (new, place_id))
    flash(f"{_place_label(p)}: {STATUSES[new][1]} {STATUSES[new][0]}", "success")
    return redirect_back("places.index")


@bp.route("/yer/<int:place_id>/sil", methods=["POST"])
@login_required
def delete(place_id):
    uid = g.user["id"]
    p = owned_or_404("places", place_id, uid)
    trash.move(uid, "places", _place_label(p), ("places", place_id))
    flash(trash.notice(p["name"]), "success")
    return redirect(url_for(".city", city_id=p["city_id"]) if p["city_id"] else url_for(".index"))


# ---------- Park yeri ----------
def save_parking(user_id, lat, lon, note=""):
    """Kullanıcının tek park kaydı; varsa üzerine yazar."""
    execute("INSERT OR REPLACE INTO parking (user_id, lat, lon, note, saved_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (user_id, lat, lon, note))


@bp.route("/park", methods=["POST"])
@login_required
def park():
    pair = _pair(request.form.get("lat"), request.form.get("lon"))
    if pair is None:
        flash("Konum alınamadı. Tarayıcıda konum iznini verip tekrar dene.", "error")
        return redirect(url_for(".index"))
    save_parking(g.user["id"], *pair, form_str("note", 100))
    flash("🅿️ Park yeri kaydedildi. Dönüşte “🚗 Arabam nerede?”ye bak.", "success")
    return redirect(url_for(".index"))


@bp.route("/park/sil", methods=["POST"])
@login_required
def unpark():
    execute("DELETE FROM parking WHERE user_id = ?", (g.user["id"],))
    flash("✅ Park kaydı silindi. İyi yolculuklar!", "success")
    return redirect(url_for(".index"))


# ---------- Telegram konumu (bot_commands.py) ----------
def nearest_city(user_id, lat, lon, max_km=NEAR_CITY_KM):
    best = None
    for c in query("SELECT id, name, lat, lon FROM cities WHERE user_id = ? AND lat IS NOT NULL", (user_id,)):
        km = distance_km(lat, lon, c["lat"], c["lon"])
        if km <= max_km and (best is None or km < best[0]):
            best = (km, c)
    return best[1] if best else None


def add_location_place(user_id, lat, lon, name="", address=""):
    """Telegram'dan gelen konum: 'Telegram konumu GG.AA SS:DD' adlı, kategorisi 'Diğer' bir yer.
    30 km içinde kayıtlı bir şehir varsa ona bağlanır. (yer_id, ad, şehir ya da None) döner."""
    city = nearest_city(user_id, lat, lon)
    name = (name or f"Telegram konumu {todo.now_local().strftime('%d.%m %H:%M')}")[:100]
    place_id = execute(
        "INSERT INTO places (user_id, city_id, name, category, lat, lon, address) VALUES (?, ?, ?, 'other', ?, ?, ?)",
        (user_id, city["id"] if city else None, name, lat, lon, (address or "")[:200]),
    ).lastrowid
    return place_id, name, city
