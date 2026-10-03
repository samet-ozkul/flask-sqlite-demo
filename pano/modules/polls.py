"""🗳️ Anket: StrawPoll / Doodle benzeri; anketi sen oluşturursun, linke sahip olan herkes girişsiz bir kez oy verir.

- Yönetim /anket/ (giriş gerekli); herkese açık sayfa /a/<kod> (girişsiz, betiksiz): soru, seçenekler, oy ver, sonuçlar
- Türler: tek seçim, çoklu seçim (en fazla N) ve tarih anketi (Doodle gibi: tarih + isteğe bağlı saat; katılımcı uygun
  olduğu günlerin hepsini işaretler, hiçbiri uymuyorsa boş gönderebilir; isim her zaman istenir)
- Bir kişi bir oy (kesin değil; yönetim sayfasında da söylenir):
  ortak link: oy verince bu anket için rastgele bir belirteç 1 yıllık HttpOnly çereze yazılır (sunucuda SHA-256'sı);
  aynı belirteçle ya da aynı tarayıcı oturumuyla (çift tıklama) ikinci oy reddedilir, verilen oy değiştirilemez.
  İsteğe bağlı "aynı IP'den tek oy" (IP düz saklanmaz, SECRET_KEY ile HMAC); isim isteniyorsa aynı isimle ikinci oy da
  reddedilir (büyük/küçük ve Türkçe harf duyarsız). Her durumda anket başına IP başına saatte 30 oy ve bal tuzağı.
  Kişiye özel linkler (/a/<kod>?d=<anahtar>): her link bir kez oy verir; "sadece davetliler" açıksa ortak link oy
  almaz. Davet linkiyle verilen oyda çerez/IP kontrolü yapılmaz (aynı telefondan iki davetli oy verebilsin)
- Oy geldikten sonra mevcut seçenekler ve sıraları, tür, en fazla seçim ve isim kuralı değişmez (verilmiş oyların
  anlamı bozulmasın); sadece yeni seçenek eklenebilir (önceki oy verenlere sorulmamış sayılır). Soru, açıklama,
  görünürlük, bitiş ve bildirim değiştirilebilir
- Sonuçlar: oy verene (anket kapanınca herkese) / herkese her zaman / sadece sahibine. İsimler sadece sahibine görünür,
  herkese açık sayfada sadece sayılar
- Sahibi kendi linkinden oy verebilir ve oyu sayılır (Doodle'da düzenleyenin de uygun günlerini işaretlemesi gibi);
  sayfada "bu senin anketin" şeridi görür ve sonuçları her zaman görür
- Telegram: "her yeni oyda haber ver" açıksa en fazla 10 dakikada bir toplu mesaj (bekleyenleri cron da gönderir);
  bitiş zamanı geçince cron anketi kapatır ve bir kez "Anket kapandı: kazanan …" yazar
- Olmayan ve silinmiş (çöpteki) kod dışarıdan aynı 404; link önizleme robotları görüntülenme sayılmaz
"""
import csv
import hashlib
import hmac
import io
import json
import secrets
from datetime import datetime, timedelta
from itertools import zip_longest

import segno
from flask import (Blueprint, Response, abort, current_app, flash, g, make_response, redirect, render_template,
                   request, send_file, session, url_for)

from .. import telegram, trash
from .. import todo_reminders as todo
from ..auth import login_required
from ..db import execute, get_db, owned_or_404, query, query_one
from ..totp import qr_svg
from ..utils import (MONTHS_TR, MONTHS_TR_SHORT, TZ, WEEKDAYS_TR, WEEKDAYS_TR_SHORT, fold, form_bool, form_choice,
                     form_int, is_link_preview, local_dt, parse_date, parse_number)
from .expenses import _csv_safe
from .shortlinks import AUTO_ALPHABET

# Yönetim /anket altında, herkese açık sayfa /a/ altında: tek blueprint iki ayrı kökte (booking.py gibi)
bp = Blueprint("polls", __name__)

KINDS = {"single": ("🔘", "Tek seçim"), "multi": ("☑️", "Çoklu seçim"), "date": ("📅", "Tarih anketi")}
VISIBILITY = {"after_vote": "Oy verdikten sonra (anket kapanınca herkes)", "always": "Herkes, her zaman",
              "owner": "Sadece ben"}
MIN_OPTIONS, MAX_OPTIONS = 2, 20
MAX_INVITES = 100
CODE_LEN = 7
QUESTION_MAX, DESC_MAX, OPTION_MAX, NAME_MAX, LABEL_MAX = 200, 1000, 120, 60, 40
IP_LIMIT = 30                       # anket başına, IP başına saatte oy
NOTIFY_EVERY = timedelta(minutes=10)
COOKIE_DAYS = 365
TOKEN_BYTES = 16                    # tarayıcı belirteci: 22 karakter, 128 bit
INVITE_BYTES = 12                   # davet anahtarı: 16 karakter, 96 bit
DB_FORMAT = "%Y-%m-%d %H:%M"        # bitiş: yerel saat, saat dilimsiz
STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"  # son bildirim: yerel
INPUT_FORMAT = "%Y-%m-%dT%H:%M"     # <input type="datetime-local">
QR_PNG_SCALE = 20
PUBLIC_ENDPOINTS = ("polls.public",)
INVALID_INVITE = "Bu kişiye özel link geçersiz ya da iptal edilmiş; anketi oluşturan kişiden yeni link iste."
# Sayfada betik yok; kullanıcı metni kaçışlansa da ek önlem: dış kaynak ve gömme kapalı, form sadece bu siteye
CSP = ("default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'")


# ---------- Saat ----------
def now_local():
    """Şu an, yerel saat ve saat dilimsiz (bitiş böyle saklanır). todo.now_local testte taklit edilir."""
    return todo.now_local().astimezone(TZ).replace(tzinfo=None)


def parse_dt(value, fmt=DB_FORMAT):
    try:
        return datetime.strptime(value or "", fmt)
    except ValueError:
        return None


def long_when(dt):
    """'12 Ekim 2026 Pazartesi 18:00'"""
    return f"{dt.day} {MONTHS_TR[dt.month - 1]} {dt.year} {WEEKDAYS_TR[dt.weekday()]} {dt:%H:%M}"


def is_closed(poll, now=None):
    """Elle kapatıldı ya da bitiş zamanı geçti (cron henüz işaretlemediyse de)."""
    if poll["closed"]:
        return True
    closes = parse_dt(poll["closes_at"])
    return bool(closes) and closes <= (now or now_local())


# ---------- Kod ve anahtarlar ----------
def _trashed_codes():
    codes = set()
    for row in query("SELECT payload FROM trash WHERE module = 'polls'"):
        codes |= {r.get("code") for r in json.loads(row["payload"])["rows"].get("polls", [])}
    return codes


def new_code():
    """Karışmayan harflerden 7 karakter (0/o, 1/l/i yok); çöpteki anketin kodu da verilmez (geri getirilince
    çakışmasın)."""
    trashed = _trashed_codes()
    for _ in range(50):
        code = "".join(secrets.choice(AUTO_ALPHABET) for _ in range(CODE_LEN))
        if code not in trashed and not query_one("SELECT 1 FROM polls WHERE code = ?", (code,)):
            return code
    raise RuntimeError("Anket kodu üretilemedi")


def _hmac(kind, value):
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(key, f"poll-{kind}:{value}".encode(), hashlib.sha256).hexdigest()[:32]


def ip_hash(ip):
    """IP düz saklanmaz: SECRET_KEY ile HMAC (aynı IP'yi tanımaya yeter)."""
    return _hmac("ip", ip or "")


def session_hash(code):
    """Bu tarayıcı oturumunun bu ankete özel izi (oturumda CSRF anahtarı yoksa boş)."""
    key = session.get("_csrf")
    return _hmac("session", f"{code}:{key}") if key else ""


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def cookie_name(code):
    return f"anket_{code}"


def public_url(code):
    return url_for("polls.public", code=code, _external=True)


def invite_url(code, token):
    return url_for("polls.public", code=code, d=token, _external=True)


# ---------- Kayıtlar ----------
def get_options(poll):
    """Tarih anketinde kronolojik, diğerlerinde yazıldığı sırayla."""
    order = "option_date, COALESCE(option_time, ''), id" if poll["kind"] == "date" else "sort, id"
    return query(f"SELECT * FROM poll_options WHERE poll_id = ? ORDER BY {order}", (poll["id"],))


def option_label(o, short=False):
    """Seçenek metni; tarih anketinde '12 Ekim 2026 Pazartesi · 19:00' (kısa: '12 Eki Pzt 19:00')."""
    if not o["option_date"]:
        return o["text"]
    d = parse_date(o["option_date"])
    if short:
        text = f"{d.day} {MONTHS_TR_SHORT[d.month - 1]} {WEEKDAYS_TR_SHORT[d.weekday()]}"
        return text + (f" {o['option_time']}" if o["option_time"] else "")
    text = f"{d.day} {MONTHS_TR[d.month - 1]} {d.year} {WEEKDAYS_TR[d.weekday()]}"
    return text + (f" · {o['option_time']}" if o["option_time"] else "")


def needs_name(poll):
    return poll["kind"] == "date" or bool(poll["require_name"])


def vote_count(poll_id):
    return query_one("SELECT COUNT(*) AS n FROM poll_votes WHERE poll_id = ?", (poll_id,))["n"]


def tally(poll):
    """Seçenek başına oy: {'rows': [{id, label, short, count, pct, lead, after}], 'total', 'leaders'}.
    Yüzde, oy verenlerin kaçının o seçeneği işaretlediği (tek seçimde toplam %100)."""
    total = vote_count(poll["id"])
    counts = {r["option_id"]: r["n"] for r in query(
        "SELECT c.option_id, COUNT(*) AS n FROM poll_vote_choices c JOIN poll_votes v ON v.id = c.vote_id"
        " WHERE v.poll_id = ? GROUP BY c.option_id", (poll["id"],))}
    top = max(counts.values(), default=0)
    rows = [{"id": o["id"], "label": option_label(o), "short": option_label(o, short=True), "after": o["after_vote_id"],
             "count": counts.get(o["id"], 0), "pct": round(counts.get(o["id"], 0) * 100 / total) if total else 0,
             "lead": top > 0 and counts.get(o["id"], 0) == top} for o in get_options(poll)]
    return {"rows": rows, "total": total, "leaders": [r for r in rows if r["lead"]]}


def leader_text(poll, t, escape=str):
    """'Pizza (%45)', beraberlikte 'Pizza, Döner (%40)', tarih anketinde '12 Ekim … (5/7 kişi)'; oy yoksa ''."""
    leaders = t["leaders"]
    if not leaders:
        return ""
    names = ", ".join(escape(r["label"]) for r in leaders[:3]) + (" …" if len(leaders) > 3 else "")
    share = f"{leaders[0]['count']}/{t['total']} kişi" if poll["kind"] == "date" else f"%{leaders[0]['pct']}"
    return f"{names} ({share})"


def invite_names(poll_id):
    """{davet id: görünen ad}; etiketsizler sırayla 'Davetli 1, 2...'"""
    out, n = {}, 0
    for r in query("SELECT id, label FROM poll_invites WHERE poll_id = ? ORDER BY id", (poll_id,)):
        n += 1
        out[r["id"]] = r["label"] or f"Davetli {n}"
    return out


def vote_rows(poll, t):
    """Sahibinin tablosu: oy başına isim, davet, seçilenler (en eski önce)."""
    labels = {r["id"]: r["label"] for r in t["rows"]}
    chosen = {}
    for c in query("SELECT c.vote_id, c.option_id FROM poll_vote_choices c JOIN poll_votes v ON v.id = c.vote_id"
                   " WHERE v.poll_id = ?", (poll["id"],)):
        chosen.setdefault(c["vote_id"], set()).add(c["option_id"])
    names = invite_names(poll["id"])
    out = []
    for v in query("SELECT * FROM poll_votes WHERE poll_id = ? ORDER BY id", (poll["id"],)):
        ids = chosen.get(v["id"], set())
        out.append({"id": v["id"], "name": v["voter_name"], "created_at": v["created_at"], "chosen": ids,
                    "invite": names.get(v["invite_id"], "") if v["invite_id"] else "",
                    "choices": ", ".join(labels[r["id"]] for r in t["rows"] if r["id"] in ids)})
    return out


def invite_rows(poll):
    names = invite_names(poll["id"])
    return [{**dict(r), "name": names[r["id"]], "url": invite_url(poll["code"], r["token"])}
            for r in query("SELECT * FROM poll_invites WHERE poll_id = ? ORDER BY id", (poll["id"],))]


# ---------- Telegram ----------
def _chat_of(user_id):
    owner = query_one("SELECT telegram_chat_id FROM users WHERE id = ?", (user_id,))
    return owner["telegram_chat_id"] if owner and owner["telegram_chat_id"] and telegram.enabled() else None


def votes_message(poll, new, url, escape):
    """'🗳️ “Akşam ne yiyelim?” anketine 3 yeni oy · önde: Pizza (%45)'"""
    t = tally(poll)
    head = f"🗳️ <b>“{escape(poll['question'])}”</b> anketine {new} yeni oy"
    lead = leader_text(poll, t, escape)
    if lead:
        word = "en uygun" if poll["kind"] == "date" else ("berabere" if len(t["leaders"]) > 1 else "önde")
        head += f" · {word}: {lead}"
    return "\n".join([head, f"Toplam {t['total']} oy", f'<a href="{escape(url)}">Sonuçlar →</a>'])


def closed_message(poll, url, escape):
    t = tally(poll)
    lines = [f"🔒 <b>Anket kapandı:</b> “{escape(poll['question'])}”"]
    if t["total"]:
        label = "En uygun" if poll["kind"] == "date" else ("Berabere" if len(t["leaders"]) > 1 else "Kazanan")
        lines.append(f"🏆 {label}: {leader_text(poll, t, escape)} · toplam {t['total']} oy")
    else:
        lines.append("Hiç oy verilmedi.")
    lines.append(f'<a href="{escape(url)}">Sonuçlar →</a>')
    return "\n".join(lines)


def claim_votes(poll, now):
    """Bildirilmemiş oy varsa ve son bildirimden 10 dakika geçtiyse bildirim hakkını alır; yeni oy sayısını döner (yoksa
    0). Hak tek sorguda alınır: aynı anda gelen iki oy (ya da oy ile cron) iki mesaj üretmesin."""
    db = get_db()
    row = db.execute("SELECT COUNT(*) AS n, MAX(id) AS last FROM poll_votes WHERE poll_id = ? AND id > ?",
                     (poll["id"], poll["notified_vote_id"])).fetchone()
    if not row["n"]:
        return 0
    claimed = db.execute(
        "UPDATE polls SET notified_at = ?, notified_vote_id = ? WHERE id = ? AND notified_vote_id = ?"
        " AND (notified_at IS NULL OR notified_at <= ?)",
        (now.strftime(STAMP_FORMAT), row["last"], poll["id"], poll["notified_vote_id"],
         (now - NOTIFY_EVERY).strftime(STAMP_FORMAT))).rowcount == 1
    db.commit()
    return row["n"] if claimed else 0


def notify_votes(poll_id):
    """Oydan sonra: "haber ver" açıksa ve sıra geldiyse sahibine toplu mesaj; hata sayfayı bozmaz."""
    poll = query_one("SELECT * FROM polls WHERE id = ?", (poll_id,))
    chat_id = _chat_of(poll["user_id"]) if poll and poll["notify"] else None
    if not chat_id:
        return
    new = claim_votes(poll, now_local())
    if not new:
        return   # 10 dakika dolmadı: bu oy bir sonraki mesaja (ya da cron'a) kalır
    try:
        telegram.send_message(chat_id, votes_message(poll, new, url_for(".detail", poll_id=poll_id, _external=True),
                                                     telegram.escape))
    except telegram.TelegramError as e:
        current_app.logger.warning("Anket bildirimi gönderilemedi (anket %s): %s", poll_id, e)
    except Exception:  # bildirim yan etkidir; oy yine kaydedilmiş olmalı
        current_app.logger.exception("Anket bildirimi hatası")


def pending_notices(now):
    """Cron: "haber ver" açık, bildirilmemiş oyu olan, son mesajdan 10 dakika geçmiş anketler (sahibinin sohbetiyle)."""
    return query(
        "SELECT p.*, u.telegram_chat_id AS chat_id FROM polls p JOIN users u ON u.id = p.user_id"
        " WHERE p.notify = 1 AND u.telegram_chat_id IS NOT NULL"
        " AND EXISTS (SELECT 1 FROM poll_votes v WHERE v.poll_id = p.id AND v.id > p.notified_vote_id)"
        " AND (p.notified_at IS NULL OR p.notified_at <= ?)", ((now - NOTIFY_EVERY).strftime(STAMP_FORMAT),))


def expired(now):
    """Cron: bitiş zamanı geçmiş ama kapalı işaretlenmemiş anketler (sahibinin sohbetiyle; bağlı değilse NULL)."""
    return query("SELECT p.*, u.telegram_chat_id AS chat_id FROM polls p JOIN users u ON u.id = p.user_id"
                 " WHERE p.closed = 0 AND p.closes_at IS NOT NULL AND p.closes_at <= ?", (now.strftime(DB_FORMAT),))


def mark_closed(poll_id):
    """Kapalı işaretle; bekleyen oy bildirimi de düşer (kapanış mesajı son durumu zaten söyler)."""
    execute("UPDATE polls SET closed = 1, notified_vote_id = MAX(notified_vote_id,"
            " (SELECT COALESCE(MAX(id), 0) FROM poll_votes WHERE poll_id = ?)) WHERE id = ?", (poll_id, poll_id))


# ---------- Oluştur / düzenle ----------
def _option_key(o):
    return (o["option_date"], o["option_time"] or "") if o["option_date"] else fold(o["text"])


def _raw_form():
    """Formdaki ham değerler (hata sonrası yazılanlar kaybolmasın)."""
    f = request.form
    return {"question": f.get("question", "").strip()[:QUESTION_MAX],
            "description": f.get("description", "").strip()[:DESC_MAX].replace("\r\n", "\n"),
            "kind": form_choice("kind", KINDS, "single"), "max_choices": f.get("max_choices", "").strip()[:4],
            "results_visibility": form_choice("results_visibility", VISIBILITY, "after_vote"),
            "closes_at": f.get("closes_at", "").strip()[:16],
            **{k: form_bool(k) for k in ("require_name", "one_per_ip", "invite_only", "notify")},
            "opts": [x[:OPTION_MAX * 2] for x in f.getlist("opt")[:50]],
            "dates": list(zip_longest([x[:10] for x in f.getlist("opt_date")[:50]],
                                      [x[:5] for x in f.getlist("opt_time")[:50]], fillvalue=""))}


def _values_from(poll):
    """Kayıttaki anket -> form değerleri (seçenekler ayrıca)."""
    closes = parse_dt(poll["closes_at"])
    return {**{k: poll[k] for k in ("question", "description", "kind", "results_visibility", "require_name",
                                    "one_per_ip", "invite_only", "notify")},
            "max_choices": str(poll["max_choices"] or ""), "closes_at": closes.strftime(INPUT_FORMAT) if closes else ""}


def parse_options(kind, raw, existing=()):
    """Formdaki yeni seçenekler: [{'text', 'option_date', 'option_time'}]; hatalıysa ValueError. existing: oy gelmiş
    anketin mevcut seçenekleri (aynısı tekrar eklenemez; toplam yine en fazla 20)."""
    out, seen = [], {_option_key(o) for o in existing}
    if kind == "date":
        today = now_local().date()
        for i, (d_raw, t_raw) in enumerate(raw["dates"], 1):
            d_raw, t_raw = d_raw.strip(), t_raw.strip()
            if not d_raw and not t_raw:
                continue
            d = parse_date(d_raw)
            if d is None:
                raise ValueError(f"{i}. satır: tarih seç" + (" (saat tek başına yazılamaz)." if t_raw else "."))
            t = todo.parse_time(t_raw) if t_raw else None
            if t_raw and t is None:
                raise ValueError(f"{i}. satır: saat geçersiz (ör. 19:00).")
            if d < today:
                raise ValueError(f"{i}. satır: geçmiş bir gün seçilemez ({d.day} {MONTHS_TR[d.month - 1]} {d.year}).")
            o = {"text": "", "option_date": d.isoformat(), "option_time": t}
            if _option_key(o) in seen:
                raise ValueError(f"Aynı tarih{' ve saat' if t else ''} iki kez yazılmış: {option_label(o)}.")
            seen.add(_option_key(o))
            out.append(o)
        out.sort(key=_option_key)
        what = "tarih"
    else:
        for text in raw["opts"]:
            text = " ".join(text.split())[:OPTION_MAX]
            if not text:
                continue
            o = {"text": text, "option_date": None, "option_time": None}
            if _option_key(o) in seen:
                raise ValueError(f"Aynı seçenek iki kez yazılmış: “{text}”.")
            seen.add(_option_key(o))
            out.append(o)
        what = "seçenek"
    if len(existing) + len(out) < MIN_OPTIONS:
        raise ValueError(f"En az {MIN_OPTIONS} {what} yaz.")
    if len(existing) + len(out) > MAX_OPTIONS:
        raise ValueError(f"En fazla {MAX_OPTIONS} {what} olabilir.")
    return out


def parse_poll(raw, poll=None, existing=None):
    """Ham form -> (değerler, yeni seçenekler); hatalıysa ValueError. existing (oy gelmiş anketin seçenekleri) verilirse
    tür, en fazla seçim ve isim kuralı kayıttaki gibi kalır, seçenekler eklenecekler olarak döner."""
    locked = existing is not None
    v = {"question": " ".join(raw["question"].split()), "description": raw["description"],
         **{k: raw[k] for k in ("kind", "results_visibility", "require_name", "one_per_ip", "invite_only", "notify")},
         "max_choices": None, "closes_at": None}
    if locked:
        v.update(kind=poll["kind"], max_choices=poll["max_choices"], require_name=poll["require_name"])
    if not v["question"]:
        raise ValueError("Soruyu yaz (ör. Akşam ne yiyelim?).")
    options = parse_options(v["kind"], raw, existing or ())
    if v["kind"] == "date":
        v["require_name"] = 1   # Doodle: kimin hangi gün uygun olduğu bilinmeli
    if v["kind"] == "multi" and not locked and raw["max_choices"]:
        n, count = parse_number(raw["max_choices"]), len(options)
        if n is None or n != int(n) or not 2 <= n <= count:
            raise ValueError(f"“En fazla kaç seçim” 2 ile {count} arasında olmalı (boş bırakırsan sınırsız).")
        v["max_choices"] = None if n == count else int(n)
    if raw["closes_at"]:
        closes = parse_dt(raw["closes_at"], INPUT_FORMAT) or parse_dt(raw["closes_at"])
        if closes is None:
            raise ValueError("Bitiş zamanı geçersiz.")
        v["closes_at"] = closes.strftime(DB_FORMAT)
        if (poll is None or v["closes_at"] != poll["closes_at"]) and closes <= now_local():
            raise ValueError("Bitiş zamanı geçmişte olamaz.")
    return v, options


def _insert_options(db, poll_id, options, after_vote_id=0):
    start = db.execute("SELECT COALESCE(MAX(sort), 0) AS n FROM poll_options WHERE poll_id = ?",
                       (poll_id,)).fetchone()["n"]
    db.executemany("INSERT INTO poll_options (poll_id, text, option_date, option_time, sort, after_vote_id)"
                   " VALUES (?, ?, ?, ?, ?, ?)",
                   [(poll_id, o["text"], o["option_date"], o["option_time"], start + i, after_vote_id)
                    for i, o in enumerate(options, 1)])


def _rows(values, total, blank, min_visible):
    """Formdaki seçenek satırları (dolu olanlar + boşlar, toplam total) ve ilk kaçının açık görüneceği. Betik
    gerekmez: kalanlar '＋ Daha fazla satır' altında."""
    rows = list(values) + [blank] * max(0, total - len(values))
    last = max((i for i, r in enumerate(rows) if r != blank), default=-1)
    return rows, min(len(rows), max(min_visible, last + 2))


def _render_form(poll, values, status=200, raw_rows=None):
    """Oluştur/düzenle formu. raw_rows: hata sonrası yazılan satırlar (opts, dates)."""
    existing, votes = [], 0
    if poll:
        votes = vote_count(poll["id"])
        if votes:
            existing = get_options(poll)
    if raw_rows is None:
        own = [] if existing or not poll else get_options(poll)
        raw_rows = {"opts": [o["text"] for o in own if not o["option_date"]],
                    "dates": [(o["option_date"], o["option_time"] or "") for o in own if o["option_date"]]}
    free = MAX_OPTIONS - len(existing)
    text_rows, text_visible = _rows(raw_rows["opts"], free, "", min(free, 2 if existing else 4))
    date_rows, date_visible = _rows(raw_rows["dates"], free, ("", ""), min(free, 2 if existing else 5))
    return render_template(
        "polls/form.html", poll=poll, f=values, existing=[option_label(o) for o in existing], votes=votes,
        locked=bool(existing), text_rows=text_rows, text_visible=text_visible, date_rows=date_rows,
        date_visible=date_visible, kinds=KINDS, visibility=VISIBILITY, max_options=MAX_OPTIONS,
        today=now_local().date().isoformat(),
        telegram_ready=telegram.enabled() and bool(g.user["telegram_chat_id"])), status


# ---------- Yönetim ----------
@bp.route("/anket/")
@login_required
def index():
    now = now_local()
    polls = [{**dict(p), "is_closed": is_closed(p, now), "closes": parse_dt(p["closes_at"])} for p in query(
        "SELECT p.*, (SELECT COUNT(*) FROM poll_votes v WHERE v.poll_id = p.id) AS votes FROM polls p"
        " WHERE p.user_id = ? ORDER BY p.id DESC", (g.user["id"],))]
    polls.sort(key=lambda p: p["is_closed"])   # açıklar önce (sıralama kararlı: içlerinde en yeni önce)
    return render_template("polls/index.html", polls=polls, kinds=KINDS, public_url=public_url, long_when=long_when)


@bp.route("/anket/yeni", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "GET":
        return _render_form(None, {"kind": "single", "require_name": 1, "results_visibility": "after_vote",
                                   "notify": 1 if g.user["telegram_chat_id"] else 0})
    raw = _raw_form()
    try:
        v, options = parse_poll(raw)
    except ValueError as e:
        flash(str(e), "error")
        return _render_form(None, raw, 400, raw)
    db = get_db()
    poll_id = db.execute(
        "INSERT INTO polls (user_id, code, question, description, kind, max_choices, require_name, results_visibility,"
        " one_per_ip, invite_only, closes_at, notify) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (g.user["id"], new_code(), v["question"], v["description"], v["kind"], v["max_choices"], v["require_name"],
         v["results_visibility"], v["one_per_ip"], v["invite_only"], v["closes_at"], v["notify"])).lastrowid
    _insert_options(db, poll_id, options)
    db.commit()
    flash("🗳️ Anket hazır: linki paylaş." + (" Kişiye özel linkleri aşağıdan oluştur." if v["invite_only"] else ""),
          "success")
    return redirect(url_for(".detail", poll_id=poll_id))


@bp.route("/anket/<int:poll_id>/duzenle", methods=["GET", "POST"])
@login_required
def edit(poll_id):
    uid = g.user["id"]
    poll = owned_or_404("polls", poll_id, uid)
    if request.method == "GET":
        return _render_form(poll, _values_from(poll))
    raw = _raw_form()
    db = get_db()
    if db.in_transaction:
        db.commit()
    db.execute("BEGIN IMMEDIATE")   # bu arada gelen oy, silinip yeniden yazılan seçeneklere düşmesin
    try:
        last_vote = db.execute("SELECT MAX(id) AS m FROM poll_votes WHERE poll_id = ?", (poll_id,)).fetchone()["m"]
        existing = get_options(poll) if last_vote else None
        v, options = parse_poll(raw, poll, existing)
    except ValueError as e:
        db.rollback()
        flash(str(e), "error")
        return _render_form(poll, raw, 400, raw)
    # "Haber ver" yeni açıldıysa eski oylar "yeni" sayılmaz
    db.execute(
        "UPDATE polls SET question = ?, description = ?, kind = ?, max_choices = ?, require_name = ?,"
        " results_visibility = ?, one_per_ip = ?, invite_only = ?, closes_at = ?, notify = ?,"
        " notified_vote_id = CASE WHEN ? = 1 AND notify = 0 THEN ? ELSE notified_vote_id END,"
        " updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
        (v["question"], v["description"], v["kind"], v["max_choices"], v["require_name"], v["results_visibility"],
         v["one_per_ip"], v["invite_only"], v["closes_at"], v["notify"], v["notify"], last_vote or 0, poll_id, uid))
    if last_vote:
        _insert_options(db, poll_id, options, after_vote_id=last_vote)
    else:
        db.execute("DELETE FROM poll_options WHERE poll_id = ?", (poll_id,))
        _insert_options(db, poll_id, options)
    db.commit()
    message = "🗳️ Anket kaydedildi."
    if last_vote and options:
        message += f" {len(options)} yeni seçenek eklendi; daha önce oy verenlere sorulmamış sayılır."
    if is_closed(query_one("SELECT * FROM polls WHERE id = ?", (poll_id,))):
        message += " Anket kapalı; oy almak için “▶️ Yeniden aç”."
    flash(message, "success")
    return redirect(url_for(".detail", poll_id=poll_id))


@bp.route("/anket/<int:poll_id>")
@login_required
def detail(poll_id):
    poll = owned_or_404("polls", poll_id, g.user["id"])
    t = tally(poll)
    url = public_url(poll["code"])
    invites = invite_rows(poll)
    now = now_local()
    closes = parse_dt(poll["closes_at"])
    return render_template(
        "polls/detail.html", poll=poll, t=t, votes=vote_rows(poll, t), invites=invites, url=url, qr=qr_svg(url),
        closed=is_closed(poll, now), closes=closes, closes_past=bool(closes) and closes <= now, long_when=long_when,
        kinds=KINDS, visibility=VISIBILITY, needs_name=needs_name(poll), max_invites=MAX_INVITES,
        bulk="\n".join(f"{i['name']}: {i['url']}" for i in invites if not i["used_at"]),
        telegram_ready=telegram.enabled() and bool(g.user["telegram_chat_id"]))


@bp.route("/anket/<int:poll_id>/durum", methods=["POST"])
@login_required
def toggle(poll_id):
    uid = g.user["id"]
    poll = owned_or_404("polls", poll_id, uid)
    now = now_local()
    if not is_closed(poll, now):
        mark_closed(poll_id)
        flash("⏸️ Anket kapatıldı: artık oy verilemez. Sonuçlar görünürlük ayarına göre görünmeye devam eder.",
              "success")
    else:
        closes = parse_dt(poll["closes_at"])
        execute("UPDATE polls SET closed = 0, closes_at = ? WHERE id = ? AND user_id = ?",
                (None if closes and closes <= now else poll["closes_at"], poll_id, uid))
        flash("▶️ Anket yeniden açıldı: oy verilebilir." + (" Bitiş zamanı geçtiği için kaldırıldı; istersen düzenleyip"
                                                            " yenisini koy." if closes and closes <= now else ""),
              "success")
    return redirect(url_for(".detail", poll_id=poll_id))


@bp.route("/anket/<int:poll_id>/sil", methods=["POST"])
@login_required
def delete(poll_id):
    poll = owned_or_404("polls", poll_id, g.user["id"])
    # Sıra geri getirmede de korunur: davetler oylardan, oylar seçimlerden önce
    trash.move(g.user["id"], "polls", f"🗳️ {poll['question'][:100]}", ("polls", poll_id),
               children=[("poll_options", "poll_id = ?"), ("poll_invites", "poll_id = ?"),
                         ("poll_votes", "poll_id = ?"),
                         ("poll_vote_choices", "vote_id IN (SELECT id FROM poll_votes WHERE poll_id = ?)")])
    flash(trash.notice("Anket"), "success")
    return redirect(url_for(".index"))


@bp.route("/anket/<int:poll_id>/oy/<int:vote_id>/sil", methods=["POST"])
@login_required
def vote_delete(poll_id, vote_id):
    """Tek oy (spam) kalıcı silinir; kişiye özel linkle verildiyse o link yeniden kullanılabilir."""
    owned_or_404("polls", poll_id, g.user["id"])
    vote = query_one("SELECT * FROM poll_votes WHERE id = ? AND poll_id = ?", (vote_id, poll_id))
    if vote is None:
        abort(404)
    db = get_db()
    db.execute("DELETE FROM poll_votes WHERE id = ?", (vote_id,))
    if vote["invite_id"]:
        db.execute("UPDATE poll_invites SET used_at = NULL WHERE id = ?", (vote["invite_id"],))
    db.commit()
    flash("🗑️ Oy silindi." + (" Kişiye özel linki yeniden oy verebilir." if vote["invite_id"] else ""), "success")
    return redirect(url_for(".detail", poll_id=poll_id, _anchor="oylar"))


@bp.route("/anket/<int:poll_id>/davet", methods=["POST"])
@login_required
def invite_create(poll_id):
    poll = owned_or_404("polls", poll_id, g.user["id"])
    labels = [" ".join(line.split())[:LABEL_MAX] for line in request.form.get("labels", "").splitlines()]
    labels = [x for x in labels if x] + [""] * max(0, min(form_int("count") or 0, MAX_INVITES))
    have = query_one("SELECT COUNT(*) AS n FROM poll_invites WHERE poll_id = ?", (poll_id,))["n"]
    if not labels:
        flash("Her satıra bir kişi yaz (ör. Ahmet) ya da kaç numaralı link istediğini seç.", "error")
    elif have + len(labels) > MAX_INVITES:
        flash(f"Bir ankette en fazla {MAX_INVITES} kişiye özel link olabilir (şu an {have}).", "error")
    else:
        db = get_db()
        db.executemany("INSERT INTO poll_invites (poll_id, token, label) VALUES (?, ?, ?)",
                       [(poll_id, secrets.token_urlsafe(INVITE_BYTES), label) for label in labels])
        db.commit()
        flash(f"🔑 {len(labels)} kişiye özel link hazır; her biri bir kez oy verir." +
              ("" if poll["invite_only"] else " “Sadece davetliler” kapalı: ortak link de oy almaya devam ediyor."),
              "success")
    return redirect(url_for(".detail", poll_id=poll_id, _anchor="davet"))


@bp.route("/anket/<int:poll_id>/davet/<int:invite_id>/sil", methods=["POST"])
@login_required
def invite_delete(poll_id, invite_id):
    owned_or_404("polls", poll_id, g.user["id"])
    invite = query_one("SELECT * FROM poll_invites WHERE id = ? AND poll_id = ?", (invite_id, poll_id))
    if invite is None:
        abort(404)
    if invite["used_at"]:
        flash("Bu linkle oy verilmiş; iptal edilemez. Oyu silmek için oylar tablosunu kullan.", "warning")
    else:
        execute("DELETE FROM poll_invites WHERE id = ? AND used_at IS NULL", (invite_id,))
        flash("Kişiye özel link iptal edildi: artık oy veremez.", "success")
    return redirect(url_for(".detail", poll_id=poll_id, _anchor="davet"))


@bp.route("/anket/<int:poll_id>/sonuclar.csv")
@login_required
def export(poll_id):
    """Excel'de doğrudan açılan CSV (UTF-8 BOM, ';'): oy başına satır, seçenek başına sütun (✓), sonda toplamlar."""
    poll = owned_or_404("polls", poll_id, g.user["id"])
    t = tally(poll)
    votes = vote_rows(poll, t)
    invite_col = any(v["invite"] for v in votes)   # davet sütunu sadece davetle oy varsa
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(["Zaman", "İsim", *(["Davet"] if invite_col else []), *(_csv_safe(r["label"]) for r in t["rows"])])
    for v in votes:
        writer.writerow([local_dt(v["created_at"]), _csv_safe(v["name"]),
                         *([_csv_safe(v["invite"])] if invite_col else []),
                         *("✓" if r["id"] in v["chosen"] else "" for r in t["rows"])])
    writer.writerow(["Toplam", f"{t['total']} oy", *([""] if invite_col else []), *(r["count"] for r in t["rows"])])
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="anket-{poll["code"]}.csv"'})


@bp.route("/anket/<int:poll_id>/qr.png")
@login_required
def qr_png(poll_id):
    """Afiş, sunum, grup mesajı için büyük QR."""
    poll = owned_or_404("polls", poll_id, g.user["id"])
    buf = io.BytesIO()
    segno.make(public_url(poll["code"]), error="m").save(buf, kind="png", scale=QR_PNG_SCALE, border=4)
    buf.seek(0)
    return send_file(buf, mimetype="image/png", as_attachment=True, download_name=f"anket-{poll['code']}-qr.png")


# ---------- Herkese açık sayfa ----------
def _poll_or_404(code):
    """Olmayan ve silinmiş (çöpteki) anket dışarıdan aynı 404; adreste büyük/küçük harf fark etmez."""
    poll = query_one("SELECT * FROM polls WHERE code = ?", (fold(code),)) if len(code) <= 32 else None
    if poll is None:
        abort(404)
    return poll


def _is_owner(poll):
    return g.user is not None and g.user["id"] == poll["user_id"]


def _invite(poll, token):
    if not token or len(token) > 64:
        return None
    return query_one("SELECT * FROM poll_invites WHERE poll_id = ? AND token = ?", (poll["id"], token))


def _my_vote(poll):
    """Bu tarayıcının oyu: çerezdeki belirteç, yoksa (çerez engelliyse) oturum izi."""
    token = request.cookies.get(cookie_name(poll["code"]), "")
    if token and len(token) <= 64:
        vote = query_one("SELECT * FROM poll_votes WHERE poll_id = ? AND voter_hash = ?",
                         (poll["id"], token_hash(token)))
        if vote:
            return vote
    key = session_hash(poll["code"])
    if not key:
        return None
    return query_one("SELECT * FROM poll_votes WHERE poll_id = ? AND session_hash = ?", (poll["id"], key))


def _choices_of(vote, t):
    ids = {r["option_id"] for r in query("SELECT option_id FROM poll_vote_choices WHERE vote_id = ?", (vote["id"],))}
    return [r["label"] for r in t["rows"] if r["id"] in ids]


def _render_public(poll, token, status=200, error=None, form=None, notice=False):
    """Sayfanın durumu: davet linkinde o linkin oyu, ortak linkte bu tarayıcının oyu; sonuçlar görünürlüğe göre
    (sahibi her zaman görür)."""
    now = now_local()
    owner = _is_owner(poll)
    closed = is_closed(poll, now)
    invite = _invite(poll, token) if token else None
    if token and invite is None:
        error, status = error or INVALID_INVITE, 403
    if invite:
        vote = query_one("SELECT * FROM poll_votes WHERE invite_id = ?", (invite["id"],)) if invite["used_at"] else None
        voted = bool(invite["used_at"])
    else:
        vote = _my_vote(poll)
        voted = vote is not None
    can_vote = not closed and not voted and (invite is not None or (not token and not poll["invite_only"]))
    visibility = poll["results_visibility"]
    show_results = owner or visibility == "always" or (visibility == "after_vote" and (voted or closed))
    t = tally(poll)
    closes = parse_dt(poll["closes_at"])
    resp = make_response(render_template(
        "polls/public.html", poll=poll, owner=owner, closed=closed, voted=voted, can_vote=can_vote, token=token,
        invite=invite, invite_only=bool(poll["invite_only"]) and invite is None, error=error,
        form=form or {"choices": [], "name": invite["label"] if invite else ""}, notice=notice and voted,
        options=t["rows"], t=t, show_results=show_results, my_choices=_choices_of(vote, t) if vote else [],
        needs_name=needs_name(poll), closes=long_when(closes) if closes else None,
        closes_past=bool(closes) and closes <= now), status)
    resp.headers["Content-Security-Policy"] = CSP
    return resp


@bp.after_request
def _public_headers(resp):
    """Herkese açık sayfa (404 dahil): önbelleğe alınmaz, dizine eklenmez, gömülemez; adres ve davet anahtarı başka
    siteye (Referer) sızmaz."""
    if request.endpoint in PUBLIC_ENDPOINTS:
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@bp.route("/a/<code>", methods=["GET", "POST"])
def public(code):
    poll = _poll_or_404(code)
    token = request.values.get("d", "").strip()
    if request.method == "POST":
        return _vote(poll, token)
    if code != poll["code"]:   # büyük harfli adres: oy çerezinin yolu küçük harfli adrese bağlı
        return redirect(url_for(".public", code=poll["code"], **({"d": token} if token else {})))
    notice = request.args.get("ok") == "1"
    # Sahibi, oy sonrası yönlendirme, HEAD ve WhatsApp/Telegram gibi uygulamaların link önizlemesi görüntülenme sayılmaz
    if request.method == "GET" and not (_is_owner(poll) or notice or is_link_preview(request.user_agent.string)):
        execute("UPDATE polls SET views = views + 1 WHERE id = ?", (poll["id"],))
    return _render_public(poll, token, notice=notice)


def _vote(poll, token):
    form = {"choices": request.form.getlist("o")[:MAX_OPTIONS], "name": request.form.get("name", "")[:NAME_MAX]}

    def fail(message, status=400):
        return _render_public(poll, token, status, error=message, form=form)

    if request.form.get("website"):   # bal tuzağı: insanlar görmez, robotlar doldurur
        return fail("Oy kaydedilemedi.")
    if is_closed(poll):
        return fail("Anket kapandı; artık oy verilemez.", 409)
    invite = None
    if token:
        invite = _invite(poll, token)
        if invite is None:
            return fail(INVALID_INVITE, 403)
        if invite["used_at"]:
            return fail("Bu kişiye özel linkle zaten oy verildi; verilen oy değiştirilemez.", 409)
    elif poll["invite_only"]:
        return fail("Bu ankete sadece kişiye özel linklerle oy verilebilir.", 403)
    elif _my_vote(poll):
        return fail("Bu tarayıcıdan zaten oy verildi; verilen oy değiştirilemez.", 409)
    options = {str(o["id"]) for o in get_options(poll)}
    chosen = [int(c) for c in dict.fromkeys(form["choices"]) if c in options]
    if poll["kind"] == "single" and len(chosen) != 1:
        return fail("Bir seçenek seç.")
    if poll["kind"] == "multi":
        if not chosen:
            return fail("En az bir seçenek seç.")
        if poll["max_choices"] and len(chosen) > poll["max_choices"]:
            return fail(f"En fazla {poll['max_choices']} seçenek işaretleyebilirsin.")
    name = " ".join(form["name"].split()) if needs_name(poll) else ""
    if needs_name(poll) and not name:
        return fail("Adını yaz.")
    digest = ip_hash(request.remote_addr)
    if query_one("SELECT COUNT(*) AS n FROM poll_votes WHERE poll_id = ? AND ip_hash = ?"
                 " AND created_at >= datetime('now', '-1 hour')", (poll["id"], digest))["n"] >= IP_LIMIT:
        return fail("Bu bağlantıdan kısa sürede çok fazla oy verildi; bir saat sonra tekrar dene.", 429)
    browser_token = secrets.token_urlsafe(TOKEN_BYTES)
    db = get_db()
    if db.in_transaction:
        db.commit()
    db.execute("BEGIN IMMEDIATE")   # aynı anda gelen iki oy (çift tıklama) kontrolleri birlikte geçemesin
    try:
        error = _duplicate(db, poll, invite, name, digest)
        if error:
            db.rollback()
            return fail(error, 409)
        vote_id = db.execute(
            "INSERT INTO poll_votes (poll_id, voter_name, name_key, voter_hash, session_hash, ip_hash, invite_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (poll["id"], name, fold(name), token_hash(browser_token), session_hash(poll["code"]), digest,
             invite["id"] if invite else None)).lastrowid
        db.executemany("INSERT INTO poll_vote_choices (vote_id, option_id) VALUES (?, ?)",
                       [(vote_id, option_id) for option_id in chosen])
        if invite:
            db.execute("UPDATE poll_invites SET used_at = CURRENT_TIMESTAMP WHERE id = ?", (invite["id"],))
        db.commit()
    except Exception:
        db.rollback()
        raise
    notify_votes(poll["id"])
    resp = redirect(url_for(".public", code=poll["code"], ok=1, **({"d": token} if token else {})), code=303)
    resp.set_cookie(cookie_name(poll["code"]), browser_token, max_age=COOKIE_DAYS * 86400, path=f"/a/{poll['code']}",
                    httponly=True, samesite="Lax", secure=bool(current_app.config.get("SESSION_COOKIE_SECURE")))
    return resp


def _duplicate(db, poll, invite, name, digest):
    """Kilit altında tekrar kontrolü; sorun varsa kullanıcıya gösterilecek metin."""
    pid = poll["id"]
    if invite:
        row = db.execute("SELECT used_at FROM poll_invites WHERE id = ?", (invite["id"],)).fetchone()
        if row is None:
            return "Bu kişiye özel link iptal edilmiş; anketi oluşturan kişiden yeni link iste."
        if row["used_at"]:
            return "Bu kişiye özel linkle zaten oy verildi; verilen oy değiştirilemez."
    else:   # davet linkinde tarayıcı/IP kontrolü yok: aynı telefondan iki davetli oy verebilsin
        key = session_hash(poll["code"])
        if key and db.execute("SELECT 1 FROM poll_votes WHERE poll_id = ? AND session_hash = ?", (pid, key)).fetchone():
            return "Bu tarayıcıdan zaten oy verildi; verilen oy değiştirilemez."
        if poll["one_per_ip"] and db.execute("SELECT 1 FROM poll_votes WHERE poll_id = ? AND ip_hash = ?",
                                             (pid, digest)).fetchone():
            return ("Bu internet bağlantısından (IP) zaten oy verilmiş: bu ankette aynı Wi-Fi'deki herkes tek oy "
                    "sayılıyor.")
    if name and db.execute("SELECT 1 FROM poll_votes WHERE poll_id = ? AND name_key = ?", (pid, fold(name))).fetchone():
        return (f"“{name}” adıyla zaten oy verilmiş. Sen değilsen adının yanına soyadının baş harfini ekle "
                f"(ör. {name} K.).")
    return None
