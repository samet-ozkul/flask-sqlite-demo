"""Yapay zekâ destekli işler (sağlayıcı pano/ai.py'de seçilir).

- read_receipt: fiş / fatura fotoğrafından tutar, mağaza, tarih, kategori, son ödeme tarihi
- read_ticket: bilet fotoğrafı / ekran görüntüsü ya da PDF'ten başlık, tür, tarih, saat, yer, koltuk, rezervasyon kodu
- parse_intent: "yarın 3'te dişçiyi ara", "markete 250 verdim" gibi düz yazıyı kayda çevirir
- answer_question: "bu ay ne kadar harcadım?" — kullanıcının verisinden kısa bir özet çıkarılıp sorulur

Kullanıcı Ayarlar'dan açmadıkça (users.ai_enabled) hiçbir veri sağlayıcıya gönderilmez.
"""
import io
from datetime import date, timedelta

from PIL import Image, ImageOps, UnidentifiedImageError

from . import ai
from .db import query
from .pdftext import pdf_text
from .utils import MONTHS_TR, WEEKDAYS_TR, fmt_money, now_local, parse_date, today

RECEIPT_MAX_PX = 1600
TICKET_TEXT_MAX = 8000   # PDF'ten çıkan yazının modele gönderilen kısmı (bilet bilgisi ilk sayfalarda)
NULL = {"type": "null"}


def available(user):
    """Bu kullanıcı için yapay zekâ kullanılabilir mi (yönetici ayarladı + kullanıcı açtı)."""
    return ai.enabled() and bool(user["ai_enabled"])


def _categories():
    from .modules.expenses import CATEGORIES
    return CATEGORIES


def _today_line():
    t = today()
    return f"Bugün {t.isoformat()} ({t.day} {MONTHS_TR[t.month - 1]} {t.year}, {WEEKDAYS_TR[t.weekday()]}), saat {now_local().strftime('%H:%M')}."


def to_jpeg(data):
    """Her türlü fotoğrafı (PNG, WEBP, telefondan dönük çekim) en fazla 1600px JPEG'e çevirir."""
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except (UnidentifiedImageError, OSError):
        raise ai.AIError("Dosya okunabilir bir fotoğraf değil.")
    img.thumbnail((RECEIPT_MAX_PX, RECEIPT_MAX_PX))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


# ---------- Fiş / fatura ----------
def _receipt_schema():
    return {
        "type": "object",
        "properties": {
            "is_receipt": {"type": "boolean"},
            "kind": {"type": "string", "enum": ["expense", "bill"]},
            "merchant": {"type": "string"},
            "total": {"anyOf": [{"type": "number"}, NULL]},
            "currency": {"type": "string", "enum": ["TRY", "USD", "EUR", "GBP"]},
            "date": {"anyOf": [{"type": "string"}, NULL]},
            "due_date": {"anyOf": [{"type": "string"}, NULL]},
            "category": {"type": "string", "enum": _categories()},
            "summary": {"type": "string"},
        },
        "required": ["is_receipt", "kind", "merchant", "total", "currency", "date", "due_date", "category", "summary"],
        "additionalProperties": False,
    }


RECEIPT_SYSTEM = """Türkiye'deki fiş ve faturaları okuyan bir asistansın: market fişleri, restoran adisyonları,
akaryakıt fişleri, e-arşiv faturalar, elektrik/su/doğalgaz/internet/telefon faturaları.

- total: ödenen ya da ödenecek toplam tutar (TOPLAM, GENEL TOPLAM, ÖDENECEK TUTAR), KDV dahil. Ara toplamı, KDV satırını ya da para üstünü alma. "1.234,56" yazılışı 1234.56 demektir.
- date: fişin/faturanın düzenlenme tarihi, YYYY-MM-DD. Okunamıyorsa null.
- kind: son ödeme tarihi olan bir abonelik faturasıysa (elektrik, su, doğalgaz, telefon, internet, aidat) "bill"; aksi halde "expense".
- due_date: sadece "bill" için son ödeme tarihi, YYYY-MM-DD; yoksa null.
- merchant: işletmenin kısa adı (ör. "Migros", "Shell", "Enerjisa").
- category: verilen listeden en uygun olanı.
- summary: 1 kısa Türkçe cümle (ör. "12 ürün, çoğu gıda").
- Fotoğraf fiş ya da fatura değilse is_receipt=false, total=null yap; tahmin uydurma."""


def read_receipt(image_bytes):
    """{'is_receipt', 'kind', 'merchant', 'total', 'currency', 'date', 'due_date', 'category', 'summary'}"""
    data = ai.complete_json(RECEIPT_SYSTEM, _today_line() + "\nBu fişi/faturayı oku.", _receipt_schema(),
                            image=to_jpeg(image_bytes))
    total = data.get("total")
    try:
        total = round(float(total), 2) if total is not None else None
    except (TypeError, ValueError):
        total = None
    if total is not None and not (0 < total < 1e9):
        total = None
    d, due = parse_date(data.get("date")), parse_date(data.get("due_date"))
    category = data.get("category") if data.get("category") in _categories() else "Diğer"
    return {
        "is_receipt": bool(data.get("is_receipt")) and total is not None,
        "kind": "bill" if data.get("kind") == "bill" else "expense",
        "merchant": str(data.get("merchant") or "")[:80],
        "total": total,
        "currency": data.get("currency") if data.get("currency") in ("TRY", "USD", "EUR", "GBP") else "TRY",
        "date": (d.isoformat() if d and d <= today() + timedelta(days=1) else today().isoformat()),
        "due_date": due.isoformat() if due else None,
        "category": category,
        "summary": str(data.get("summary") or "")[:160],
    }


# ---------- Bilet ----------
def _ticket_kinds():
    from .modules.tickets import KINDS
    return list(KINDS)


def _ticket_schema():
    text = {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "is_ticket": {"type": "boolean"},
            "kind": {"type": "string", "enum": _ticket_kinds()},
            "title": text,
            "date": {"anyOf": [text, NULL]},
            "time": {"anyOf": [text, NULL]},
            "venue": text,
            "address": text,
            "seat": text,
            "booking_code": text,
            "holder": text,
            "price": {"anyOf": [{"type": "number"}, NULL]},
        },
        "required": ["is_ticket", "kind", "title", "date", "time", "venue", "address", "seat", "booking_code", "holder",
                     "price"],
        "additionalProperties": False,
    }


TICKET_SYSTEM = """Etkinlik ve yolculuk biletlerini okuyan bir asistansın: konser, tiyatro, sinema, maç, müze biletleri;
uçak e-bileti / biniş kartı, otobüs ve tren biletleri (Türkçe ya da yabancı dilde).

- kind: concert (konser, festival), theatre (tiyatro, opera, bale, stand-up), cinema, sport (maç, yarış), flight (uçak),
  bus (otobüs), train (tren), museum (müze, sergi, ören yeri), other.
- title: kısa başlık. Etkinlikte etkinliğin ya da sanatçının adı ("Tarkan Konseri", "Fenerbahçe - Galatasaray");
  yolculukta "Kalkış → Varış" ve sefer/uçuş no ("İstanbul → Ankara TK2124").
- date: etkinliğin ya da kalkışın tarihi, YYYY-MM-DD. time: başlangıç / kalkış saati HH:MM (24 saat; biniş ya da kapı
  kapanış saati değil). Yazmıyorsa null.
- venue: yer (salon, stadyum, sinema, havalimanı, terminal, gar). address: biletteki adres; yoksa boş.
- seat: koltuk, blok, sıra, kapı, vagon, peron; kısa ("Blok 104 · Sıra 12 · Koltuk 7", "Koltuk 14C · Kapı B12").
- booking_code: PNR / rezervasyon / bilet numarası (en belirgin olanı).
- holder: bilet kimin adına (yolcu / seyirci adı); birden çok kişiyse adlar ya da "2 kişi".
- price: ödenen toplam tutar, sadece Türk lirasıysa (₺, TL, TRY); "1.234,56" yazılışı 1234.56 demektir. Yoksa null.
- Biletin üzerinde yazmayan bilgiyi uydurma: boş metin ya da null bırak.
- Belge bir bilet değilse is_ticket=false yap, diğer alanları boş bırak."""


def read_ticket(data, mime=""):
    """{'is_ticket', 'kind', 'title', 'date', 'time', 'venue', 'address', 'seat', 'booking_code', 'holder', 'price'}.
    Fotoğraf / ekran görüntüsü resim olarak gönderilir; PDF'in yazısı çıkarılıp metin olarak (her sağlayıcıda çalışsın
    diye). Taranmış (yazısız) PDF okunamaz: AIError."""
    from .todo_reminders import parse_time
    prompt = _today_line() + "\nBu bileti oku."
    if mime == "application/pdf" or data[:5] == b"%PDF-":
        text = pdf_text(data)
        if len(text.strip()) < 20:
            raise ai.AIError("PDF'teki yazı okunamadı (taranmış olabilir); biletin ekran görüntüsünü gönder ya da "
                             "bilgileri kendin gir.")
        result = ai.complete_json(TICKET_SYSTEM, f"{prompt}\n\nBilet PDF'inin yazısı:\n{text[:TICKET_TEXT_MAX]}",
                                  _ticket_schema())
    else:
        result = ai.complete_json(TICKET_SYSTEM, prompt, _ticket_schema(), image=to_jpeg(data))

    def clean(key, limit):
        return " ".join(str(result.get(key) or "").split())[:limit]

    price = result.get("price")
    try:
        price = round(float(price), 2) if price is not None else None
    except (TypeError, ValueError):
        price = None
    d = parse_date(result.get("date"))
    out = {
        "kind": result.get("kind") if result.get("kind") in _ticket_kinds() else "other",
        "title": clean("title", 150),
        "date": d.isoformat() if d else None,
        "time": parse_time(str(result.get("time") or "")),
        "venue": clean("venue", 150),
        "address": clean("address", 300),
        "seat": clean("seat", 150),
        "booking_code": clean("booking_code", 60),
        "holder": clean("holder", 150),
        "price": price if price and 0 < price < 1e7 else None,
    }
    out["is_ticket"] = bool(result.get("is_ticket")) and bool(out["title"])
    return out


# ---------- Doğal dil ----------
INTENT_ACTIONS = ["expense", "todo", "appointment", "event", "shopping", "note", "question", "unknown"]


def _intent_schema():
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": INTENT_ACTIONS},
            "text": {"type": "string"},
            "amount": {"anyOf": [{"type": "number"}, NULL]},
            "category": {"anyOf": [{"type": "string", "enum": _categories()}, NULL]},
            "items": {"type": "array", "items": {"type": "string"}},
            "date": {"anyOf": [{"type": "string"}, NULL]},
            "time": {"anyOf": [{"type": "string"}, NULL]},
            "place": {"anyOf": [{"type": "string"}, NULL]},
            "list_name": {"anyOf": [{"type": "string"}, NULL]},
        },
        "required": ["action", "text", "amount", "category", "items", "date", "time", "place", "list_name"],
        "additionalProperties": False,
    }


INTENT_SYSTEM = """Kişisel bir pano uygulamasına Türkçe yazılan kısa mesajı tek bir kayda çeviriyorsun.

action:
- expense: yapılmış bir harcama ("markete 250 verdim", "benzin 1800"). amount ve category doldur, text = kısa açıklama.
- todo: yapılacak bir iş ("yarın 3'te dişçiyi ara"). text = iş; tarih/saat varsa date/time.
- appointment: doktor/hastane/diş randevusu ("perşembe 14:30 dişçi randevum var"). text = randevu başlığı, place = yer (varsa), date ve time.
- event: aile/ev etkinliği, buluşma, davet, doğum günü partisi (doktor değil) ("cumartesi 19:00 annemlerde yemek"). text = başlık, place = yer (varsa), date ve time.
- shopping: alınacaklar ("süt ve ekmek almam lazım"). items = ürünler (her biri kısa), list_name = kullanıcı liste adı söylediyse.
- note: hatırlanacak bilgi ("wifi şifresi kutunun arkasında"). text = notun kendisi.
- question: kullanıcının kendi verisi hakkında soru ("bu ay ne kadar harcadım?", "sıradaki faturam ne?"). text = soru.
- unknown: hiçbirine uymuyorsa.

Kurallar: tarihleri bugüne göre hesapla ve YYYY-MM-DD yaz; saat HH:MM (24 saat, "3'te" öğleden sonraysa 15:00).
"yarın", "haftaya", "cuma", "ayın 5'i" gibi ifadeleri çevir. Kullanılmayan alanlar null (items için boş liste)."""


def parse_intent(user, text):
    lists = [r["name"] for r in query(
        "SELECT name FROM lists WHERE user_id = ? OR shared = 1 ORDER BY id", (user["id"],))][:20]
    prompt = (f"{_today_line()}\nKullanıcının listeleri: {', '.join(lists) or 'yok'}\n"
              f"Kategoriler: {', '.join(_categories())}\n\nMesaj: {text}")
    data = ai.complete_json(INTENT_SYSTEM, prompt, _intent_schema())
    action = data.get("action") if data.get("action") in INTENT_ACTIONS else "unknown"
    amount = data.get("amount")
    try:
        amount = round(float(amount), 2) if amount is not None else None
    except (TypeError, ValueError):
        amount = None
    from .todo_reminders import parse_time
    d = parse_date(data.get("date"))
    out = {
        "action": action,
        "text": " ".join(str(data.get("text") or "").split())[:200],
        "amount": amount if amount and 0 < amount < 1e9 else None,
        "category": data.get("category") if data.get("category") in _categories() else "Diğer",
        "items": [" ".join(str(i).split())[:100] for i in (data.get("items") or []) if str(i).strip()][:30],
        "date": d.isoformat() if d else None,
        "time": parse_time(data.get("time") or ""),
        "place": str(data.get("place") or "")[:100],
        "list_name": str(data.get("list_name") or "")[:80],
    }
    # Eksik bilgiyle kayıt önerme
    if (action == "expense" and not out["amount"]) or (action == "shopping" and not out["items"]) \
            or (action in ("todo", "note", "appointment") and not out["text"]) \
            or (action in ("appointment", "event") and not out["date"]) or (action == "event" and not out["text"]):
        out["action"] = "unknown"
    return out


# ---------- Soru cevaplama ----------
ANSWER_SYSTEM = """Kişisel pano uygulamasının asistanısın. Kullanıcının sorusunu SADECE verilen verilere dayanarak,
kısa ve net Türkçe cevapla (en fazla 6-8 satır). Para "1.234,56 ₺" biçiminde. Veride yoksa bunu açıkça söyle,
tahmin uydurma. Yatırım ya da finansal tavsiye verme. Markdown başlık kullanma; gerekirse kısa madde işaretleri kullan."""


def _month_lines(user_id, year, month, label):
    from .modules.expenses import month_summary
    s = month_summary(user_id, year, month)
    cats = ", ".join(f"{c} {fmt_money(v)}" for c, v in s["categories"][:8]) or "yok"
    return f"{label} ({year}-{month:02d}) harcama: {fmt_money(s['total'])} ({s['count']} kayıt). Kategoriler: {cats}"


def data_snapshot(user):
    """Soruya bağlam: kullanıcının verisinin kısa, metin özeti."""
    from . import budgets
    from .modules.habits import today_status
    from .reminders import upcoming
    uid = user["id"]
    t = today()
    lines = [_today_line()]
    lines.append(_month_lines(uid, t.year, t.month, "Bu ay"))
    for back in (1, 2):
        m = date(t.year, t.month, 1)
        for _ in range(back):
            m = (m - timedelta(days=1)).replace(day=1)
        lines.append(_month_lines(uid, m.year, m.month, f"{back} ay önce"))
    recent = query("SELECT * FROM expenses WHERE user_id = ? ORDER BY date DESC, id DESC LIMIT 20", (uid,))
    if recent:
        lines.append("Son harcamalar: " + "; ".join(
            f"{r['date']} {fmt_money(r['amount'])} {r['category']}" + (f" ({r['note']})" if r["note"] else "") for r in recent))
    status = budgets.month_status(uid, t.year, t.month)
    if status:
        lines.append("Bütçe: " + "; ".join(f"{r['label']} {fmt_money(r['spent'])}/{fmt_money(r['limit'])}" for r in status))
    items = upcoming(uid, days=30, long_days=60)
    if items:
        lines.append("Yaklaşanlar: " + "; ".join(
            f"{i['date']} {i['title']}" + (f" ({i['detail']})" if i["detail"] else "") for i in items[:30]))
    subs = query("SELECT * FROM subscriptions WHERE user_id = ? AND active = 1", (uid,))
    if subs:
        lines.append("Abonelikler: " + "; ".join(
            f"{r['name']} {fmt_money(r['amount'], r['currency'])}/{r['cycle']} sonraki {r['next_date']}" for r in subs))
    debts = query("SELECT * FROM debts WHERE user_id = ? AND settled = 0", (uid,))
    if debts:
        lines.append("Açık borç/alacak: " + "; ".join(
            f"{r['person']} {'bana borçlu' if r['direction'] == 'lent' else 'ona borçluyum'} {fmt_money(r['amount'], r['currency'])}"
            for r in debts))
    open_items = query(
        "SELECT i.text, l.name FROM list_items i JOIN lists l ON l.id = i.list_id"
        " WHERE i.done = 0 AND (l.user_id = ? OR l.shared = 1) ORDER BY i.id DESC LIMIT 40", (uid,))
    if open_items:
        lines.append("Açık liste maddeleri: " + "; ".join(f"{r['name']}: {r['text']}" for r in open_items))
    habits = today_status(uid)
    if habits:
        lines.append("Alışkanlıklar: " + "; ".join(
            f"{h['habit']['name']} seri {h['streak']} gün{' (bugün yapıldı)' if h['done'] else ''}" for h in habits))
    for kind, label in (("weight", "kilo"), ("bp", "tansiyon"), ("sugar", "şeker"), ("pulse", "nabız")):
        rows = query("SELECT * FROM health_metrics WHERE user_id = ? AND kind = ? ORDER BY measured_at DESC LIMIT 3",
                     (uid, kind))
        if rows:
            lines.append(f"Son {label} ölçümleri: " + "; ".join(
                f"{r['measured_at'][:10]} {r['value1']:g}" + (f"/{r['value2']:g}" if r["value2"] else "") for r in rows))
    vehicles = query("SELECT * FROM vehicles WHERE user_id = ?", (uid,))
    for v in vehicles:
        lines.append(f"Araç {v['name']} ({v['plate']}): muayene {v['inspection_date']}, sigorta {v['insurance_date']}, "
                     f"kasko {v['casco_date']}, bakım {v['service_date']}")
    notes = query("SELECT title, content FROM notes WHERE user_id = ? ORDER BY COALESCE(updated_at, created_at) DESC LIMIT 10",
                  (uid,))
    if notes:
        lines.append("Son notlar: " + "; ".join(
            ((n["title"] + ": ") if n["title"] else "") + " ".join(n["content"].split())[:100] for n in notes))
    return "\n".join(lines)


def answer_question(user, question):
    return ai.complete_text(ANSWER_SYSTEM, f"VERİLER:\n{data_snapshot(user)}\n\nSORU: {question}")
