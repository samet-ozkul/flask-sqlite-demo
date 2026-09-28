"""Telegram bot komutları: panoyu açmadan hızlı giriş ve sorgu.

Webhook kuruluyken (Yönetim sayfası) çalışır; bot.py gelen mesajı buraya yönlendirir.
Mesajı gönderen, bu sohbete bağlı pano kullanıcısıdır.

  /harcama 250 market öğle   -> harcama (kategori ilk kelimeden tahmin edilir)
  /harcama                   -> bu ayın özeti
  /not metin                 -> not
  /ekle süt, ekmek           -> alışveriş listesi ("/ekle market: süt" ile liste seçilir)
  /yap fatura öde yarın 14:00 -> yapılacak (sondaki tarih/saat kelimeleri anlaşılır)
  /liste [ad]                -> açık maddeler, dokununca işaretlenir
  /bugun                     -> günün özeti
  link                       -> Sonra Bak'a kaydedilir
  fotoğraf / PDF             -> garantiye ya da nota eklenir (sorulur)
  düz yazı                   -> ne yapılacağı butonlarla sorulur
"""
import io
import json
import re
import time
from datetime import timedelta

from flask import request, url_for
from werkzeug.datastructures import FileStorage

from . import telegram
from . import todo_reminders as todo
from .db import execute, get_db, query, query_one
from .utils import fmt_date, fmt_money, fold, now_local, parse_number, today, today_str

COMMANDS = [
    ("harcama", "Harcama ekle: /harcama 250 market öğle"),
    ("not", "Not kaydet: /not metin"),
    ("ekle", "Alışveriş listesine ekle: /ekle süt, ekmek"),
    ("yap", "Yapılacak ekle: /yap fatura öde yarın 14:00"),
    ("liste", "Açık maddeleri göster: /liste market"),
    ("bugun", "Günün özeti"),
    ("yardim", "Komutlar"),
]

HELP = """<b>Kişisel Pano komutları</b>

💸 <code>/harcama 250 market öğle</code> — harcama ekle
💸 <code>/harcama</code> — bu ayın özeti
📝 <code>/not metin</code> — not kaydet
🛒 <code>/ekle süt, ekmek</code> — alışveriş listesine ekle
    <code>/ekle market: süt</code> — belirli listeye
☑️ <code>/yap fatura öde yarın 14:00</code> — yapılacak ekle
    <code>/yap çöpü at pazartesi 20:00 her hafta</code> — tekrarlayan
📋 <code>/liste</code> ya da <code>/liste market</code> — açık maddeler
☀️ <code>/bugun</code> — günün özeti

🔖 Link gönder → Sonra Bak'a kaydedilir
📷 Fotoğraf gönder → garantiye ya da nota eklenir
✍️ Düz yazı gönder → ne yapacağımı sorarım"""

PENDING_TTL = 3600
LIST_SHOWN = 30
AMOUNT_RE = re.compile(r"^(\d[\d.,]*)(?:tl|₺)?$", re.IGNORECASE)
# "14:00" saattir; "12.10" tarihtir (12 Ekim), noktalı saat ancak "saat 14.30" şeklinde yazılırsa
TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
DOTTED_TIME_RE = re.compile(r"^\d{1,2}\.\d{2}$")
DATE_RE = re.compile(r"^(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?$")
WEEKDAYS = {"pazartesi": 0, "sali": 1, "carsamba": 2, "persembe": 3, "cuma": 4, "cumartesi": 5, "pazar": 6}
DEFAULT_LIST_NAMES = {"shopping": "Alışveriş", "todo": "Yapılacaklar"}
KIND_ICONS = {"shopping": "🛒", "todo": "☑️"}
# /yap sonunda tekrar ifadesi: "her gün", "hafta içi", "her hafta", "her ay", "her yıl"
REPEAT_PHRASES = [("hafta ici her gun", "weekdays"), ("her gun", "daily"), ("hafta ici", "weekdays"),
                  ("her hafta", "weekly"), ("her ay", "monthly"), ("her yil", "yearly")]


def parse_repeat_phrase(text):
    """'çöpü at her hafta pazartesi' değil, 'çöpü at pazartesi her hafta' -> ('çöpü at pazartesi', 'weekly')."""
    words = text.split()
    for phrase, value in REPEAT_PHRASES:
        n = len(phrase.split())
        if len(words) > n and fold(" ".join(words[-n:])) == phrase:
            return " ".join(words[:-n]), value
    return text, None


# ---------- Ortak ----------
def user_for_chat(chat_id):
    return query_one("SELECT * FROM users WHERE telegram_chat_id = ? ORDER BY id LIMIT 1", (str(chat_id),))


def esc(text):
    return telegram.escape(text)


def link(endpoint, **values):
    return url_for(endpoint, _external=True, **values)


def _set_pending(chat_id, data):
    data["ts"] = time.time()
    db = get_db()
    db.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)",
               (f"tg_pending:{chat_id}", json.dumps(data)))
    db.commit()


def _pop_pending(chat_id, kind):
    key = f"tg_pending:{chat_id}"
    row = query_one("SELECT value FROM app_state WHERE key = ?", (key,))
    if not row:
        return None
    execute("DELETE FROM app_state WHERE key = ?", (key,))
    data = json.loads(row["value"])
    if data.get("kind") != kind or time.time() - data.get("ts", 0) > PENDING_TTL:
        return None
    return data


# ---------- Mesaj yönlendirme ----------
def handle_message(msg):
    chat_id = str(msg["chat"]["id"])
    user = user_for_chat(chat_id)
    if user is None:
        telegram.send_message(chat_id, "Bu sohbet panoya bağlı değil. Panoda <b>Ayarlar → Telegram'ı bağla</b>.")
        return
    document = msg.get("document") or {}
    if msg.get("photo") or (document.get("mime_type") or "").startswith(("image/", "application/pdf")):
        ask_attachment(user, chat_id, msg)
        return
    text = (msg.get("text") or "").strip()
    if not text:
        return
    if text.startswith("/"):
        command, _, rest = text[1:].partition(" ")
        command = fold(command.split("@")[0])
        handler = {
            "harcama": cmd_expense, "h": cmd_expense,
            "not": cmd_note, "n": cmd_note,
            "ekle": cmd_shop, "e": cmd_shop,
            "yap": cmd_todo, "y": cmd_todo,
            "liste": cmd_list, "l": cmd_list,
            "bugun": cmd_today,
        }.get(command, cmd_help)
        handler(user, chat_id, rest.strip())
        return
    from .modules.links import URL_RE
    found = URL_RE.search(text)
    if found:
        save_link(user, chat_id, found.group(0), text)
        return
    ask_text(user, chat_id, text)


def handle_callback(cq, chat_id, message_id):
    """Bu modülün butonları. İşlendiyse True."""
    data = cq.get("data") or ""
    prefix = data.split(":", 1)[0]
    handlers = {"exu": cb_undo_expense, "li": cb_list_item, "lv": cb_list_view, "ph": cb_attachment, "tx": cb_text}
    if prefix not in handlers:
        return False
    user = user_for_chat(chat_id)
    if user is None:
        telegram.answer_callback(cq["id"], "Bu sohbet panoya bağlı değil.")
        return True
    handlers[prefix](user, chat_id, message_id, cq["id"], data)
    return True


# ---------- Harcama ----------
def _match_category(user_id, word):
    from .modules.expenses import CATEGORIES
    custom = [r["category"] for r in query("SELECT DISTINCT category FROM expenses WHERE user_id = ?", (user_id,))]
    key = fold(word)
    for c in CATEGORIES + custom:
        if fold(c) == key:
            return c
    return None


def parse_expense(user_id, text):
    """'250 market öğle' -> (250.0, 'Market', 'öğle'); tutar yoksa None."""
    tokens = text.split()
    if not tokens:
        return None
    m = AMOUNT_RE.match(tokens[0])
    amount = parse_number(m.group(1)) if m else None
    if amount is None or not (0 < amount < 1e10):
        return None
    rest = tokens[1:]
    if rest and fold(rest[0]) in ("tl", "₺", "lira"):
        rest = rest[1:]
    category = _match_category(user_id, rest[0]) if rest else None
    if category:
        rest = rest[1:]
    return round(amount, 2), category or "Diğer", " ".join(rest)[:200]


def _month_total(user_id):
    return query_one("SELECT COALESCE(SUM(amount), 0) AS s FROM expenses WHERE user_id = ? AND date >= ?",
                     (user_id, today().replace(day=1).isoformat()))["s"]


def cmd_expense(user, chat_id, rest):
    if not rest:
        _expense_summary(user, chat_id)
        return
    parsed = parse_expense(user["id"], rest)
    if parsed is None:
        telegram.send_message(chat_id, "Tutarı anlayamadım. Örnek: <code>/harcama 250 market öğle</code>")
        return
    amount, category, note = parsed
    expense_id = execute("INSERT INTO expenses (user_id, amount, category, note, date) VALUES (?, ?, ?, ?, ?)",
                         (user["id"], amount, category, note, today_str())).lastrowid
    text = f"💸 <b>{fmt_money(amount)}</b> · {esc(category)}" + (f" · {esc(note)}" if note else "")
    text += f"\nBu ay toplam: {fmt_money(_month_total(user['id']))}"
    telegram.send_message(chat_id, text, buttons=[[("↩️ Geri al", f"exu:{expense_id}")]])


def _expense_summary(user, chat_id):
    from .modules.expenses import category_icon, month_summary
    t = today()
    s = month_summary(user["id"], t.year, t.month)
    lines = [f"💸 <b>Bu ay: {fmt_money(s['total'])}</b> ({s['count']} harcama)"]
    if s["change"] is not None:
        lines.append(f"Geçen aya göre: {'+' if s['change'] >= 0 else ''}{round(s['change'])}%")
    for c, v in s["categories"][:6]:
        lines.append(f"{category_icon(c)} {esc(c)}: {fmt_money(v)}")
    lines.append(f'<a href="{esc(link("expenses.index"))}">Harcamaları aç →</a>')
    telegram.send_message(chat_id, "\n".join(lines))


def cb_undo_expense(user, chat_id, message_id, callback_id, data):
    expense_id = int(data.split(":")[1]) if data.split(":")[1].isdigit() else 0
    row = query_one("SELECT * FROM expenses WHERE id = ? AND user_id = ? AND created_at >= datetime('now', '-1 day')",
                    (expense_id, user["id"]))
    if row is None:
        telegram.answer_callback(callback_id, "Harcama bulunamadı ya da artık geri alınamaz.")
        return
    execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    telegram.answer_callback(callback_id, "↩️ Silindi")
    telegram.edit_message(chat_id, message_id, f"↩️ <s>{fmt_money(row['amount'])} · {esc(row['category'])}</s> silindi.")


# ---------- Not ----------
def cmd_note(user, chat_id, rest):
    if not rest:
        telegram.send_message(chat_id, "Örnek: <code>/not toplantıda bütçe konuşulacak</code>")
        return
    execute("INSERT INTO notes (user_id, title, content, updated_at) VALUES (?, '', ?, CURRENT_TIMESTAMP)",
            (user["id"], rest[:20000]))
    telegram.send_message(chat_id, f'📝 Not kaydedildi. <a href="{esc(link("notes.index"))}">Notlar →</a>')


# ---------- Listeler ----------
def _find_list(user_id, name, kind=None):
    from .modules.lists import accessible_lists
    key = fold(name).strip()
    if not key:
        return None
    lists = accessible_lists(user_id, kind)
    for exact in (True, False):
        for lst in lists:
            if (fold(lst["name"]) == key) if exact else fold(lst["name"]).startswith(key):
                return lst
    return None


def _default_list(user_id, kind):
    from .modules.lists import accessible_lists
    lists = accessible_lists(user_id, kind)  # önce kendi listeleri
    if lists:
        return lists[0]
    list_id = execute("INSERT INTO lists (user_id, name, kind) VALUES (?, ?, ?)",
                      (user_id, DEFAULT_LIST_NAMES[kind], kind)).lastrowid
    return query_one("SELECT * FROM lists WHERE id = ?", (list_id,))


def _split_target(user_id, rest, kind):
    """'market: süt, ekmek' -> (Market listesi, 'süt, ekmek'); liste adı yoksa varsayılan liste."""
    name, sep, remainder = rest.partition(":")
    if sep:
        lst = _find_list(user_id, name, kind)
        if lst:
            return lst, remainder.strip()
    return _default_list(user_id, kind), rest


def _open_count(list_id):
    return query_one("SELECT COUNT(*) AS n FROM list_items WHERE list_id = ? AND done = 0", (list_id,))["n"]


def cmd_shop(user, chat_id, rest):
    from .modules.lists import add_items, split_items
    if not rest:
        telegram.send_message(chat_id, "Örnek: <code>/ekle süt, ekmek, yumurta</code>")
        return
    lst, text = _split_target(user["id"], rest, "shopping")
    items = split_items(text, "shopping")
    if not items:
        telegram.send_message(chat_id, "Eklenecek ürün bulamadım.")
        return
    add_items(lst["id"], items, user["id"])
    telegram.send_message(
        chat_id,
        f"🛒 <b>{esc(lst['name'])}</b>: {esc(', '.join(items))} eklendi · {_open_count(lst['id'])} açık ürün",
        buttons=[[("📋 Listeyi göster", f"lv:{lst['id']}")]],
    )


def parse_when(text):
    """'fatura öde yarın 14:00' -> ('fatura öde', '2026-09-26', '14:00'). Sadece sondaki kelimelere bakar."""
    tokens = text.split()
    due_date = due_time = None
    t = today()
    while tokens:
        word = fold(tokens[-1]).strip(",.")
        dotted = DOTTED_TIME_RE.match(word) and len(tokens) >= 2 and fold(tokens[-2]) == "saat"
        if due_time is None and (TIME_RE.match(word) or dotted):
            due_time = todo.parse_time(word)
            if due_time:
                tokens.pop()
                if tokens and fold(tokens[-1]) == "saat":
                    tokens.pop()
                continue
        if due_date is None:
            m = DATE_RE.match(word)
            if word == "bugun":
                due_date = t
            elif word == "yarin":
                due_date = t + timedelta(days=1)
            elif word in WEEKDAYS:
                ahead = (WEEKDAYS[word] - t.weekday()) % 7 or 7
                due_date = t + timedelta(days=ahead)
            elif m:
                day, month = int(m.group(1)), int(m.group(2))
                year = int(m.group(3)) if m.group(3) else t.year
                year += 2000 if year < 100 else 0
                try:
                    due_date = t.replace(year=year, month=month, day=day)
                except ValueError:
                    break
                if not m.group(3) and due_date < t:
                    due_date = due_date.replace(year=t.year + 1)
            else:
                break
            tokens.pop()
            continue
        break
    if due_time and due_date is None:
        now = now_local()
        due_date = t if due_time > now.strftime("%H:%M") else t + timedelta(days=1)
    return " ".join(tokens), (due_date.isoformat() if due_date else None), due_time


def cmd_todo(user, chat_id, rest):
    if not rest:
        telegram.send_message(chat_id, "Örnek: <code>/yap faturayı öde yarın 14:00</code>")
        return
    lst, text = _split_target(user["id"], rest, "todo")
    text, repeat = parse_repeat_phrase(text)
    task, due_date, due_time = parse_when(text)
    if repeat and not due_date:
        due_date = today().isoformat()  # tekrar için başlangıç tarihi gerekir
    if not task:
        telegram.send_message(chat_id, "Yapılacak işi yazmayı unuttun. Örnek: <code>/yap ilaç al 21:00</code>")
        return
    remind = 0 if due_date else None
    item_id = execute(
        "INSERT INTO list_items (list_id, text, due_date, due_time, remind_before, repeat, created_by)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (lst["id"], task[:200], due_date, due_time, remind, repeat, user["id"]),
    ).lastrowid
    lines = [f"☑️ <b>{esc(task)}</b> · {esc(lst['name'])}"]
    if due_date:
        when = fmt_date(due_date, True) + (f" {due_time}" if due_time else "")
        hint = "" if due_time else f" ({todo.DEFAULT_DUE_TIME})"
        lines.append(f"📅 {when} · 🔔 zamanı gelince{hint}" + (f" · 🔁 {todo.repeat_label(repeat)}" if repeat else ""))
    telegram.send_message(chat_id, "\n".join(lines), buttons=todo.done_buttons(item_id))


def _list_keyboard(list_id):
    open_items = query(
        "SELECT id, text, qty FROM list_items WHERE list_id = ? AND done = 0"
        " ORDER BY due_date IS NULL, due_date, id LIMIT ?", (list_id, LIST_SHOWN))
    # Az önce işaretlenenler de görünsün ki yanlış dokunuş geri alınabilsin
    recent = query(
        "SELECT id, text, qty FROM list_items WHERE list_id = ? AND done = 1 AND done_at >= datetime('now', '-15 minutes')"
        " ORDER BY done_at DESC LIMIT 10", (list_id,))

    def label(r, mark):
        return f"{mark} {r['text'][:40]}" + (f" ({r['qty']})" if r["qty"] else "")

    return ([[(label(r, "☐"), f"li:{r['id']}")] for r in open_items]
            + [[(label(r, "✅"), f"li:{r['id']}")] for r in recent])


def _list_text(lst):
    n = _open_count(lst["id"])
    head = f"{KIND_ICONS.get(lst['kind'], '📋')} <b>{esc(lst['name'])}</b> — {n} açık madde"
    return head + ("\nİşaretlemek için dokun." if n else "\nHepsi tamam 🎉")


def _send_list(chat_id, lst):
    telegram.send_message(chat_id, _list_text(lst), buttons=_list_keyboard(lst["id"]))


def cmd_list(user, chat_id, rest):
    from .modules.lists import accessible_lists
    if rest:
        lst = _find_list(user["id"], rest)
        if lst is None:
            names = ", ".join(esc(l["name"]) for l in accessible_lists(user["id"])) or "henüz liste yok"
            telegram.send_message(chat_id, f"“{esc(rest)}” adında liste bulamadım. Listeler: {names}")
            return
    else:
        lists = sorted(accessible_lists(user["id"]), key=lambda l: (l["kind"] != "shopping", l["user_id"] != user["id"]))
        lst = next((l for l in lists if _open_count(l["id"])), lists[0] if lists else None)
        if lst is None:
            telegram.send_message(chat_id, "Henüz listen yok. <code>/ekle süt</code> ile başlayabilirsin.")
            return
    _send_list(chat_id, lst)


def _accessible_item(user_id, item_id):
    return query_one(
        "SELECT i.*, l.name AS list_name FROM list_items i JOIN lists l ON l.id = i.list_id"
        " WHERE i.id = ? AND (l.user_id = ? OR l.shared = 1)", (item_id, user_id))


def cb_list_item(user, chat_id, message_id, callback_id, data):
    raw = data.split(":")[1]
    item = _accessible_item(user["id"], int(raw)) if raw.isdigit() else None
    if item is None:
        telegram.answer_callback(callback_id, "Madde bulunamadı.")
        return
    todo.set_done(item["id"], not item["done"])
    telegram.answer_callback(callback_id, ("↩️ " if item["done"] else "✅ ") + item["text"][:40])
    lst = query_one("SELECT * FROM lists WHERE id = ?", (item["list_id"],))
    try:
        telegram.edit_message(chat_id, message_id, _list_text(lst), _list_keyboard(lst["id"]))
    except telegram.TelegramError:
        pass


def cb_list_view(user, chat_id, message_id, callback_id, data):
    from .modules.lists import accessible_lists
    raw = data.split(":")[1]
    lst = next((l for l in accessible_lists(user["id"]) if raw.isdigit() and l["id"] == int(raw)), None)
    telegram.answer_callback(callback_id)
    if lst:
        _send_list(chat_id, lst)


# ---------- Günün özeti ----------
def cmd_today(user, chat_id, rest):
    from .modules.cron import build_daily_message
    telegram.send_message(chat_id, build_daily_message(user, request.url_root))


def cmd_help(user, chat_id, rest):
    telegram.send_message(chat_id, HELP)


# ---------- Link ----------
def save_link(user, chat_id, url, text):
    from .modules.links import domain_of, valid_url
    url = url.rstrip(").,;!?'\"")[:2000]
    if not valid_url(url):
        ask_text(user, chat_id, text)
        return
    title = " ".join(text.replace(url, " ").split())[:300] or domain_of(url)
    existing = query_one("SELECT id FROM links WHERE user_id = ? AND url = ?", (user["id"], url))
    if existing:
        execute("UPDATE links SET is_read = 0 WHERE id = ?", (existing["id"],))
        message = "🔖 Bu link zaten kayıtlıydı; okunmamışlara alındı."
    else:
        execute("INSERT INTO links (user_id, url, title) VALUES (?, ?, ?)", (user["id"], url, title))
        message = f"🔖 Sonra Bak'a kaydedildi: <b>{esc(title)}</b>"
    telegram.send_message(chat_id, f'{message}\n<a href="{esc(link("links.index"))}">Linkler →</a>')


# ---------- Fotoğraf / PDF ----------
def ask_attachment(user, chat_id, msg):
    if msg.get("photo"):
        file_id, name = msg["photo"][-1]["file_id"], "telegram.jpg"  # en büyük boyut
    else:
        doc = msg["document"]
        file_id, name = doc["file_id"], doc.get("file_name") or "belge"
    caption = " ".join((msg.get("caption") or "").split())[:150]
    _set_pending(chat_id, {"kind": "file", "file_id": file_id, "name": name, "caption": caption})
    warranties = query("SELECT id, product FROM warranties WHERE user_id = ? ORDER BY id DESC LIMIT 5", (user["id"],))
    buttons = [[(f"🛡️ {w['product'][:35]}", f"ph:w:{w['id']}")] for w in warranties]
    buttons.append([("➕ Yeni garanti" + (f": {caption[:25]}" if caption else ""), "ph:new")])
    buttons.append([("📝 Nota ekle", "ph:note"), ("✖️ Vazgeç", "ph:x")])
    telegram.send_message(chat_id, "📷 Bunu nereye ekleyeyim?", buttons=buttons)


def cb_attachment(user, chat_id, message_id, callback_id, data):
    from .storage import save_attachment
    choice = data.split(":", 1)[1]
    pending = _pop_pending(chat_id, "file")
    if choice == "x" or pending is None:
        telegram.answer_callback(callback_id, "Vazgeçildi." if choice == "x" else "Süre doldu, dosyayı tekrar gönder.")
        telegram.edit_message(chat_id, message_id, "✖️ Vazgeçildi." if choice == "x" else "⌛ Süre doldu.")
        return
    telegram.answer_callback(callback_id, "Kaydediliyor...")
    uid, caption = user["id"], pending["caption"]
    if choice.startswith("w:"):
        raw = choice[2:]
        row = query_one("SELECT id, product FROM warranties WHERE id = ? AND user_id = ?",
                        (int(raw) if raw.isdigit() else 0, uid))
        if row is None:
            telegram.edit_message(chat_id, message_id, "Garanti bulunamadı.")
            return
        entity, entity_id, label = "warranty", row["id"], f"🛡️ {row['product']}"
        target = link("warranty.detail", warranty_id=row["id"])
    elif choice == "new":
        product = caption or f"Telegram fotoğrafı {now_local().strftime('%d.%m.%Y')}"
        entity_id = execute("INSERT INTO warranties (user_id, product, purchase_date) VALUES (?, ?, ?)",
                            (uid, product, today_str())).lastrowid
        entity, label = "warranty", f"🛡️ {product} (yeni garanti — bitiş tarihini panodan gir)"
        target = link("warranty.detail", warranty_id=entity_id)
    else:
        entity_id = execute(
            "INSERT INTO notes (user_id, title, content, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (uid, caption or "Fotoğraf", caption)).lastrowid
        entity, label = "note", f"📝 {caption or 'Fotoğraf'} (yeni not)"
        target = link("notes.edit", note_id=entity_id)
    try:
        data_bytes = telegram.download_file(pending["file_id"])
        save_attachment(FileStorage(io.BytesIO(data_bytes), filename=pending["name"]), uid, entity, entity_id)
    except (telegram.TelegramError, ValueError) as e:
        telegram.edit_message(chat_id, message_id, f"⚠️ Eklenemedi: {esc(e)}")
        return
    telegram.edit_message(chat_id, message_id, f'✅ Eklendi: {esc(label)}\n<a href="{esc(target)}">Aç →</a>')


# ---------- Düz yazı ----------
def ask_text(user, chat_id, text):
    _set_pending(chat_id, {"kind": "text", "text": text[:2000]})
    row = [("☑️ Yapılacak", "tx:todo")]
    if parse_expense(user["id"], text):
        row.append(("💸 Harcama", "tx:exp"))
    buttons = [[("📝 Not", "tx:note"), ("🛒 Alışveriş", "tx:shop")], row, [("✖️ Vazgeç", "tx:x")]]
    preview = text if len(text) <= 80 else text[:77] + "..."
    telegram.send_message(chat_id, f"Bunu ne yapayım?\n<i>{esc(preview)}</i>", buttons=buttons)


def cb_text(user, chat_id, message_id, callback_id, data):
    choice = data.split(":", 1)[1]
    pending = _pop_pending(chat_id, "text")
    telegram.answer_callback(callback_id)
    if choice == "x" or pending is None:
        telegram.edit_message(chat_id, message_id, "✖️ Vazgeçildi." if choice == "x" else "⌛ Süre doldu.")
        return
    handler = {"note": cmd_note, "shop": cmd_shop, "todo": cmd_todo, "exp": cmd_expense}.get(choice)
    if handler is None:
        return
    telegram.edit_message(chat_id, message_id, f"✔️ <i>{esc(pending['text'][:80])}</i>")
    handler(user, chat_id, pending["text"])
