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
  konum                      -> park yeri ya da Harita'ya yer olarak kaydedilir (sorulur)
  düz yazı                   -> ne yapılacağı butonlarla sorulur
"""
import io
import json
import re
import time
from datetime import timedelta

from flask import request, url_for
from werkzeug.datastructures import FileStorage

from . import ai, assistant, automation, telegram
from . import todo_reminders as todo
from .db import execute, get_db, query, query_one
from .utils import fmt_date, fmt_money, fold, now_local, parse_number, today, today_str

COMMANDS = [
    ("harcama", "Harcama ekle: /harcama 250 market öğle"),
    ("not", "Not kaydet: /not metin"),
    ("ekle", "Alışveriş listesine ekle: /ekle süt, ekmek"),
    ("yap", "Yapılacak ekle: /yap fatura öde yarın 14:00"),
    ("liste", "Açık maddeleri göster: /liste market"),
    ("etkinlik", "Ortak etkinlik ekle: /etkinlik piknik pazar 11:00"),
    ("gunluk", "Günlüğe yaz: /gunluk bugün çok yoğundu"),
    ("aktar", "Aktarma kutusuna metin koy: /aktar metin"),
    ("tara", "Belge tara: /tara yaz, sayfaların fotoğraflarını gönder, PDF yap"),
    ("baslat", "Zaman sayacını başlat: /baslat proje not"),
    ("durdur", "Zaman sayacını durdur"),
    ("zaman", "Çalışan sayaç ve bugünün toplamı"),
    ("bugun", "Günün özeti"),
    ("rapor", "Geçen ayın raporu (/rapor bu ay)"),
    ("ara", "Her yerde ara: /ara matkap"),
    ("sor", "Verilerine soru sor: /sor bu ay ne kadar harcadım"),
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
    <code>/yap bulaşıkları yıka yarın 21:00 @ayse</code> — birine ata
👨‍👩‍👧 <code>/etkinlik annemlerde yemek cumartesi 19:00</code> — ortak takvime ekle
📓 <code>/gunluk 🙂 bugün yürüyüşe çıktım</code> — günlüğe yaz (başa emoji koyarsan ruh hali olur)
📋 <code>/liste</code> ya da <code>/liste market</code> — açık maddeler
📤 <code>/aktar metin</code> — bilgisayarda açmak için aktarma kutusuna koy (dosya gönderirsen “📤 Aktar”)
📄 <code>/tara</code> — sayfaların fotoğraflarını gönder, bitince “📄 PDF yap” (tek fotoğrafta “📄 Taramaya ekle”)
⏱️ <code>/baslat web sitesi tasarım</code> — zaman sayacını başlat (baştaki kelimeler proje adıysa o projeye)
    <code>/durdur</code> — sayacı durdur · <code>/zaman</code> — çalışan sayaç ve bugünün toplamı
☀️ <code>/bugun</code> — günün özeti
📊 <code>/rapor</code> — geçen ayın raporu · <code>/rapor bu ay</code>
🔍 <code>/ara matkap</code> — notlar, envanter, garantiler... her yerde ara
🤖 <code>/sor bu ay markete ne kadar harcadım?</code> — verilerine soru sor (yapay zekâ açıksa)

🔖 Link gönder → Sonra Bak'a kaydedilir
📷 Fotoğraf gönder → garantiye ya da nota eklenir
📍 Konum gönder → park yeri ya da Harita'ya yer olarak kaydedilir
✍️ Düz yazı gönder → ne yapacağımı sorarım (yapay zekâ açıksa kendisi anlar: "yarın 3'te dişçiyi ara")
🧾 Yapay zekâ açıksa fiş/fatura fotoğrafından tutar ve kategori okunur"""

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
    if _image_part(msg) and _scan_state(chat_id):
        scan_add_message(user, chat_id, msg)
        return
    if msg.get("photo") or document or msg.get("video"):
        ask_attachment(user, chat_id, msg)
        return
    if msg.get("location"):
        ask_location(user, chat_id, msg)
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
            "etkinlik": cmd_event,
            "gunluk": cmd_journal, "g": cmd_journal,
            "tara": cmd_scan,
            "aktar": cmd_transfer,
            "baslat": cmd_timer_start, "durdur": cmd_timer_stop, "zaman": cmd_timer_status,
            "liste": cmd_list, "l": cmd_list,
            "bugun": cmd_today,
            "rapor": cmd_report,
            "ara": cmd_search,
            "sor": cmd_ask,
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
    handlers = {"exu": cb_undo_expense, "li": cb_list_item, "lv": cb_list_view, "ph": cb_attachment, "tx": cb_text,
                "rc": cb_receipt, "ai": cb_ai, "jm": cb_journal_mood, "ct": cb_contact, "ctz": cb_contact,
                "tt": cb_timer, "loc": cb_location, "scn": cb_scan}
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
    automation.fire("expense_added", user["id"], **{"tutar": amount, "kategori": category, "not": note})
    text = f"💸 <b>{fmt_money(amount)}</b> · {esc(category)}" + (f" · {esc(note)}" if note else "")
    text += f"\nBu ay toplam: {fmt_money(_month_total(user['id']))}"
    from . import budgets
    t = today()
    for line in (budgets.status_line(user["id"], category, t.year, t.month),
                 budgets.status_line(user["id"], budgets.TOTAL, t.year, t.month)):
        if line:
            text += f"\n🎯 {esc(line)}"
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


# ---------- Günlük ----------
def _mood_prefix(text):
    """'😄 harika gün' -> (5, 'harika gün'); emoji yoksa (None, metin)."""
    from .modules.journal import MOODS
    for n, (emoji, _label) in MOODS.items():
        for variant in (emoji, emoji.replace("\ufe0f", "")):
            if variant and text.startswith(variant):
                return n, text[len(variant):].strip()
    return None, text


def cmd_journal(user, chat_id, rest):
    from .modules.journal import TEXT_MAX, entry_text, mood_buttons, save_entry
    day = today_str()
    mood, text = _mood_prefix(rest)
    if mood or text:
        save_entry(user["id"], day, mood=mood, text=text[:TEXT_MAX] if text else None, append=True)
    row = query_one("SELECT * FROM journal WHERE user_id = ? AND date = ?", (user["id"], day))
    if not (mood or text):
        body = entry_text(day, row, esc) if row else "📓 Bugün henüz yazmadın. <code>/gunluk bugün şöyle geçti...</code>"
    else:
        body = "✔️ Günlüğe eklendi.\n" + entry_text(day, row, esc)
    body += f'\n<a href="{esc(link("journal.index"))}">Günlük →</a>'
    ask_mood = not (row and row["mood"])
    telegram.send_message(chat_id, body + ("\n\nBugün nasıldı?" if ask_mood else ""),
                          buttons=mood_buttons(day) if ask_mood else None)


def cb_journal_mood(user, chat_id, message_id, callback_id, data):
    from datetime import date, timedelta
    from .modules.journal import MOODS, entry_text, save_entry
    parts = data.split(":")
    try:
        day, mood = date.fromisoformat(parts[1]), int(parts[2])
    except (IndexError, ValueError):
        telegram.answer_callback(callback_id)
        return
    t = today()
    if mood not in MOODS or day > t or day < t - timedelta(days=7):
        telegram.answer_callback(callback_id, "Bu gün için artık kaydedilemiyor.")
        return
    save_entry(user["id"], day.isoformat(), mood=mood)
    telegram.answer_callback(callback_id, f"{MOODS[mood][0]} kaydedildi")
    row = query_one("SELECT * FROM journal WHERE user_id = ? AND date = ?", (user["id"], day.isoformat()))
    try:
        telegram.edit_message(chat_id, message_id, entry_text(day.isoformat(), row, esc) +
                              "\nİstersen <code>/gunluk ...</code> ile birkaç satır ekle.")
    except telegram.TelegramError:
        pass


# ---------- Kişiler ----------
def cb_contact(user, chat_id, message_id, callback_id, data):
    """Dürtme mesajı: ct:<id> -> bugün arandı olarak kaydet, ctz:<id> -> yarın yeniden hatırlat."""
    from .modules.contacts import add_log, snooze, status
    action, _, raw_id = data.partition(":")
    contact = query_one("SELECT * FROM contacts WHERE id = ? AND user_id = ?",
                        (int(raw_id) if raw_id.isdigit() else 0, user["id"]))
    if contact is None:
        telegram.answer_callback(callback_id, "Kişi bulunamadı, silinmiş olabilir.")
        return
    name = f"<b>{esc(contact['name'])}</b>"
    if action == "ct":
        add_log(contact, "call", today_str())
        st = status(query_one("SELECT * FROM contacts WHERE id = ?", (contact["id"],)))
        telegram.answer_callback(callback_id, "✅ Kaydedildi")
        text = f"✅ {name} ile görüşme kaydedildi."
        if st["due"]:
            text += f" Sıradaki: {fmt_date(st['due'], True)}."
    else:
        snooze(contact["id"], (today() + timedelta(days=1)).isoformat())
        telegram.answer_callback(callback_id, "⏰ Yarın hatırlatırım")
        text = f"⏰ {name}: yarın yeniden hatırlatırım."
    url = link("contacts.detail", contact_id=contact["id"])
    try:
        telegram.edit_message(chat_id, message_id, f'{text}\n<a href="{esc(url)}">Kişi sayfası →</a>')
    except telegram.TelegramError:
        pass


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
    assignee, rest = _extract_mention(rest)
    lst, text = _split_target(user["id"], rest, "todo")
    if assignee and assignee["id"] != user["id"] and not lst["shared"]:
        lst = _shared_todo_list(user["id"])  # başkasına atanan iş onun görebileceği listeye
    text, repeat = parse_repeat_phrase(text)
    task, due_date, due_time = parse_when(text)
    if repeat and not due_date:
        due_date = today().isoformat()  # tekrar için başlangıç tarihi gerekir
    if not task:
        telegram.send_message(chat_id, "Yapılacak işi yazmayı unuttun. Örnek: <code>/yap ilaç al 21:00</code>")
        return
    remind = 0 if due_date else None
    item_id = execute(
        "INSERT INTO list_items (list_id, text, due_date, due_time, remind_before, repeat, created_by, assignee_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (lst["id"], task[:200], due_date, due_time, remind, repeat, user["id"], assignee["id"] if assignee else None),
    ).lastrowid
    lines = [f"☑️ <b>{esc(task)}</b> · {esc(lst['name'])}"
             + (f" · ➡️ {esc(assignee['display_name'] or assignee['username'])}" if assignee else "")]
    if assignee and assignee["id"] != user["id"]:
        from .modules.lists import notify_assignee
        notify_assignee(item_id, user)
    if due_date:
        when = fmt_date(due_date, True) + (f" {due_time}" if due_time else "")
        hint = "" if due_time else f" ({todo.DEFAULT_DUE_TIME})"
        lines.append(f"📅 {when} · 🔔 zamanı gelince{hint}" + (f" · 🔁 {todo.repeat_label(repeat)}" if repeat else ""))
    telegram.send_message(chat_id, "\n".join(lines), buttons=todo.done_buttons(item_id))


def _extract_mention(text):
    """'çöpü at @ayse yarın' -> (Ayşe'nin kullanıcı satırı, 'çöpü at yarın'). Bulunamazsa (None, metin)."""
    words = text.split()
    for i, word in enumerate(words):
        if word.startswith("@") and len(word) > 1:
            key = fold(word[1:])
            for u in query("SELECT * FROM users ORDER BY id"):
                if key in (fold(u["username"]), fold(u["display_name"])) or \
                        (u["display_name"] and fold(u["display_name"]).startswith(key)):
                    return u, " ".join(words[:i] + words[i + 1:])
    return None, text


def _shared_todo_list(user_id):
    from .modules.lists import accessible_lists
    for lst in accessible_lists(user_id, "todo"):
        if lst["shared"]:
            return lst
    list_id = execute("INSERT INTO lists (user_id, name, kind, shared) VALUES (?, 'Ev işleri', 'todo', 1)",
                      (user_id,)).lastrowid
    return query_one("SELECT * FROM lists WHERE id = ?", (list_id,))


def cmd_event(user, chat_id, rest):
    if not rest:
        telegram.send_message(chat_id, "Örnek: <code>/etkinlik annemlerde yemek cumartesi 19:00</code>")
        return
    title, due_date, due_time = parse_when(rest)
    if not title or not due_date:
        telegram.send_message(chat_id, "Başlık ve tarih gerekli. Örnek: <code>/etkinlik piknik pazar 11:00</code>")
        return
    execute("INSERT INTO events (user_id, title, date, time, shared, remind_before) VALUES (?, ?, ?, ?, 1, 0)",
            (user["id"], title[:150], due_date, due_time))
    when = fmt_date(due_date, True) + (f" {due_time}" if due_time else "")
    telegram.send_message(chat_id, f"👨‍👩‍👧 <b>{esc(title)}</b> ortak takvime eklendi · {when}\n"
                                   f'<a href="{esc(link("events.index"))}">Etkinlikler →</a>')


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
    todo.set_done(item["id"], not item["done"], actor_id=user["id"])
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


def cmd_report(user, chat_id, rest):
    from .reports import monthly_report
    t = today()
    if fold(rest).replace(" ", "") == "buay":
        year, month = t.year, t.month
    else:
        prev = t.replace(day=1) - timedelta(days=1)
        year, month = prev.year, prev.month
    url = link("expenses.index", ay=f"{year:04d}-{month:02d}")
    telegram.send_message(chat_id, monthly_report(user, year, month, url, esc))


def cmd_search(user, chat_id, rest):
    from .search import search
    if not rest:
        telegram.send_message(chat_id, "Örnek: <code>/ara matkap</code>")
        return
    groups = search(user["id"], rest, per_group=3)
    if not groups:
        telegram.send_message(chat_id, f"🔍 “{esc(rest)}” ile eşleşen bir şey bulunamadı.")
        return
    lines = [f"🔍 <b>{esc(rest)}</b> — {sum(gr['count'] for gr in groups)} sonuç"]
    for gr in groups[:6]:
        lines.append(f"\n{gr['icon']} <b>{esc(gr['label'])}</b>" + (f" ({gr['count']})" if gr["count"] > 3 else ""))
        for it in gr["results"]:
            url = request.url_root.rstrip("/") + it["url"]
            detail = f" — {esc(it['detail'])}" if it["detail"] else ""
            lines.append(f'• <a href="{esc(url)}">{esc(it["title"])}</a>{detail}')
    lines.append(f'\n<a href="{esc(link("search.index", q=rest))}">Tüm sonuçlar →</a>')
    # Telegram sınırı 4096 karakter: HTML etiketini bölmemek için kesmek yerine satır at ("Tüm sonuçlar" kalsın)
    while len("\n".join(lines)) > 4000 and len(lines) > 2:
        lines.pop(-2)
    telegram.send_message(chat_id, "\n".join(lines))


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
def _image_part(msg):
    """Fotoğraf ya da resim belgesi ise (file_id, ad, tür); değilse None."""
    if msg.get("photo"):
        return msg["photo"][-1]["file_id"], "telegram.jpg", "image/jpeg"  # en büyük boyut
    doc = msg.get("document") or {}
    if (doc.get("mime_type") or "").startswith("image/"):
        return doc["file_id"], doc.get("file_name") or "resim", doc["mime_type"]
    return None


def _peek_pending(chat_id, kind):
    row = query_one("SELECT value FROM app_state WHERE key = ?", (f"tg_pending:{chat_id}",))
    data = json.loads(row["value"]) if row else None
    if not data or data.get("kind") != kind or time.time() - data.get("ts", 0) > PENDING_TTL:
        return None
    return data


def ask_attachment(user, chat_id, msg):
    if msg.get("photo"):
        file_id, name, mime = msg["photo"][-1]["file_id"], "telegram.jpg", "image/jpeg"  # en büyük boyut
    else:
        doc = msg.get("document") or msg.get("video")
        file_id, name = doc["file_id"], doc.get("file_name") or ("video.mp4" if msg.get("video") else "belge")
        mime = doc.get("mime_type") or "application/octet-stream"
    caption = " ".join((msg.get("caption") or "").split())[:150]
    group = msg.get("media_group_id")
    prev = _peek_pending(chat_id, "file") if group else None
    if prev and prev.get("group") == group and len(prev.get("more", [])) < 30:
        # Albümün devamı: yeni soru yerine ilk sorunun sayısı güncellenir, seçim hepsine uygulanır
        prev.setdefault("more", []).append({"file_id": file_id, "name": name, "mime": mime})
        prev["caption"] = prev["caption"] or caption
        _set_pending(chat_id, prev)
        if prev.get("msg") and prev.get("buttons"):
            try:
                telegram.edit_message(chat_id, prev["msg"], f"📷 {1 + len(prev['more'])} dosya geldi. Hepsini nereye ekleyeyim?",
                                      prev["buttons"])
            except telegram.TelegramError:
                pass
        return
    pending = {"kind": "file", "file_id": file_id, "name": name, "caption": caption, "mime": mime, "group": group}
    _set_pending(chat_id, pending)
    if not (mime.startswith("image/") or mime == "application/pdf"):
        # Garantiye/nota sadece fotoğraf ve PDF eklenir; diğer dosyalar aktarma kutusuna gidebilir
        buttons = [[("📤 Aktar (1 saat)", "ph:tr")], [("✖️ Vazgeç", "ph:x")]]
        sent = telegram.send_message(chat_id, f"📎 <b>{esc(name)}</b> dosyasını bilgisayarda açmak için aktarma kutusuna koyayım mı?",
                                     buttons=buttons)
        _remember_prompt(chat_id, pending, sent, buttons)
        return
    warranties = query("SELECT id, product FROM warranties WHERE user_id = ? ORDER BY id DESC LIMIT 5", (user["id"],))
    buttons = [[(f"🛡️ {w['product'][:35]}", f"ph:w:{w['id']}")] for w in warranties]
    is_image = bool(msg.get("photo")) or (msg.get("document") or {}).get("mime_type", "").startswith("image/")
    if is_image:
        buttons.insert(0, [("📄 Taramaya ekle (PDF için)", "ph:sc")])
    if is_image and assistant.available(user):
        buttons.insert(0, [("🧾 Fişi oku (yapay zekâ)", "ph:ai")])
    buttons.append([("➕ Yeni garanti" + (f": {caption[:25]}" if caption else ""), "ph:new")])
    buttons.append([("📝 Nota ekle", "ph:note"), ("📤 Aktar", "ph:tr")])
    buttons.append([("✖️ Vazgeç", "ph:x")])
    sent = telegram.send_message(chat_id, "📷 Bunu nereye ekleyeyim?", buttons=buttons)
    _remember_prompt(chat_id, pending, sent, buttons)


def _remember_prompt(chat_id, pending, sent, buttons):
    """Albümün sonraki fotoğrafları bu soruyu güncelleyebilsin diye mesaj kimliği saklanır."""
    if pending.get("group") and isinstance(sent, dict) and sent.get("message_id"):
        pending.update(msg=sent["message_id"], buttons=buttons)
        _set_pending(chat_id, pending)


def _pending_files(pending):
    """Bekleyen dosyalar: ilki ve (albümse) devamı."""
    first = {"file_id": pending["file_id"], "name": pending["name"], "mime": pending.get("mime", "")}
    return [first] + pending.get("more", [])


def cb_attachment(user, chat_id, message_id, callback_id, data):
    from .storage import save_attachment
    choice = data.split(":", 1)[1]
    pending = _pop_pending(chat_id, "file")
    if choice == "x" or pending is None:
        telegram.answer_callback(callback_id, "Vazgeçildi." if choice == "x" else "Süre doldu, dosyayı tekrar gönder.")
        telegram.edit_message(chat_id, message_id, "✖️ Vazgeçildi." if choice == "x" else "⌛ Süre doldu.")
        return
    if choice == "ai":
        _read_receipt(user, chat_id, message_id, callback_id, pending)
        return
    if choice == "tr":
        _transfer_file(user, chat_id, message_id, callback_id, pending)
        return
    if choice == "sc":
        _scan_files(user, chat_id, message_id, callback_id, pending)
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
    saved, errors = 0, []
    for f in _pending_files(pending):
        try:
            data_bytes = telegram.download_file(f["file_id"])
            save_attachment(FileStorage(io.BytesIO(data_bytes), filename=f["name"]), uid, entity, entity_id)
            saved += 1
        except (telegram.TelegramError, ValueError) as e:
            errors.append(str(e))
    if not saved:
        telegram.edit_message(chat_id, message_id, f"⚠️ Eklenemedi: {esc(errors[0] if errors else '')}")
        return
    count = f" ({saved} dosya)" if saved > 1 else ""
    warn = f"\n⚠️ {len(errors)} dosya eklenemedi: {esc(errors[0])}" if errors else ""
    telegram.edit_message(chat_id, message_id, f'✅ Eklendi{count}: {esc(label)}{warn}\n<a href="{esc(target)}">Aç →</a>')


# ---------- Konum: park yeri ya da Harita'ya yer ----------
def ask_location(user, chat_id, msg):
    loc = msg["location"]
    try:
        lat, lon = round(float(loc["latitude"]), 6), round(float(loc["longitude"]), 6)
    except (KeyError, TypeError, ValueError):
        return
    venue = msg.get("venue") or {}  # Telegram'da listeden seçilen mekân: adı ve adresi de gelir
    _set_pending(chat_id, {"kind": "location", "lat": lat, "lon": lon,
                           "title": (venue.get("title") or "")[:100], "address": (venue.get("address") or "")[:200]})
    what = f"<b>{esc(venue['title'])}</b> konumunu" if venue.get("title") else "Bu konumu"
    telegram.send_message(chat_id, f"📍 {what} ne yapayım?",
                          buttons=[[("🅿️ Park yeri", "loc:park"), ("📍 Yer olarak kaydet", "loc:place")],
                                   [("✖️ Vazgeç", "loc:x")]])


def cb_location(user, chat_id, message_id, callback_id, data):
    from .modules.places import add_location_place, directions_url, save_parking
    choice = data.split(":", 1)[1]
    pending = _pop_pending(chat_id, "location")
    telegram.answer_callback(callback_id)
    if choice == "x" or pending is None:
        telegram.edit_message(chat_id, message_id, "✖️ Vazgeçildi." if choice == "x" else "⌛ Süre doldu, konumu tekrar gönder.")
        return
    lat, lon = pending["lat"], pending["lon"]
    if choice == "park":
        save_parking(user["id"], lat, lon)
        telegram.edit_message(chat_id, message_id,
                              "🅿️ Park yeri kaydedildi. Dönüşte bu mesajdan yol tarifi alabilirsin.\n"
                              f'<a href="{esc(directions_url(lat, lon))}">🧭 Yol tarifi</a> · '
                              f'<a href="{esc(link("places.index"))}">🗺️ Harita →</a>')
    elif choice == "place":
        place_id, name, city = add_location_place(user["id"], lat, lon, pending.get("title"), pending.get("address"))
        where = f" ({esc(city['name'])})" if city else ""
        telegram.edit_message(chat_id, message_id,
                              f"📍 Yer olarak kaydedildi: <b>{esc(name)}</b>{where}\n"
                              f'Adını, kategorisini ve notunu eklemek için '
                              f'<a href="{esc(link("places.edit", place_id=place_id))}">düzenle →</a>')


# ---------- Aktarma kutusu ----------
def cmd_transfer(user, chat_id, rest):
    from .modules.transfer import DEFAULT_MINUTES, TransferError, add_text
    if not rest:
        telegram.send_message(chat_id, "Örnek: <code>/aktar bilgisayarda açılacak link ya da metin</code>\n"
                                       "Dosya ya da fotoğraf gönderirsen “📤 Aktar”ı seç.")
        return
    try:
        add_text(user["id"], rest, DEFAULT_MINUTES, "🤖 Telegram")
    except TransferError as e:
        telegram.send_message(chat_id, f"⚠️ {esc(e)}")
        return
    telegram.send_message(chat_id, f'📤 Aktarma kutusuna kondu, 1 saat duracak. <a href="{esc(link("transfer.index"))}">Aktar →</a>')


def _transfer_file(user, chat_id, message_id, callback_id, pending):
    from .modules.transfer import DEFAULT_MINUTES, TransferError, add_file
    telegram.answer_callback(callback_id, "Aktarılıyor...")
    files, done, errors = _pending_files(pending), 0, []
    for f in files:
        try:
            data = telegram.download_file(f["file_id"])
            add_file(user["id"], data, f["name"], f.get("mime", ""), DEFAULT_MINUTES, device="🤖 Telegram",
                     size_hint=len(data))
            done += 1
        except (telegram.TelegramError, TransferError) as e:
            errors.append(str(e))
    if not done:
        telegram.edit_message(chat_id, message_id, f"⚠️ Aktarılamadı: {esc(errors[0] if errors else '')}")
        return
    what = f"<b>{esc(pending['name'])}</b>" if len(files) == 1 else f"<b>{done} dosya</b>"
    warn = f"\n⚠️ {len(errors)} dosya aktarılamadı: {esc(errors[0])}" if errors else ""
    telegram.edit_message(chat_id, message_id, f"📤 {what} aktarma kutusuna kondu, 1 saat duracak.{warn}\n"
                                               f'<a href="{esc(link("transfer.index"))}">Aktar →</a>')


# ---------- Belge tarama: Telegram'dan sayfa ----------
SCAN_MODE_TTL = 15 * 60  # /tara modu son sayfadan bu kadar sonra kendiliğinden kapanır
SCAN_BUTTONS = [[("📄 PDF yap", "scn:pdf:doc"), ("🎨 Renkli PDF", "scn:pdf:color")], [("🗑️ Kutuyu boşalt", "scn:clr")]]


def _scan_state(chat_id):
    row = query_one("SELECT value FROM app_state WHERE key = ?", (f"scan_mode:{chat_id}",))
    data = json.loads(row["value"]) if row else None
    return data if data and data.get("until", 0) > time.time() else None


def _scan_set(chat_id, msg_id=None):
    db = get_db()
    db.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)",
               (f"scan_mode:{chat_id}", json.dumps({"until": time.time() + SCAN_MODE_TTL, "msg": msg_id})))
    db.commit()


def _scan_end(chat_id):
    execute("DELETE FROM app_state WHERE key = ?", (f"scan_mode:{chat_id}",))


def _scan_text(count, mode_on, note=""):
    lines = [f"📄 <b>Tarama kutusu: {count} sayfa</b>" + (" · tarama modu açık" if mode_on else "")]
    if note:
        lines.append(note)
    if mode_on:
        lines.append("Sayfaların fotoğraflarını sırayla gönder (albüm de olur); bitince <b>📄 PDF yap</b>.")
    lines.append(f'Sırala/döndür ya da nota ekle: <a href="{esc(link("scanner.index"))}">Belge Tara →</a>')
    return "\n".join(lines)


def _scan_show(chat_id, count, note="", edit_id=None):
    """Durum mesajını günceller (yoksa yenisini gönderir); mesaj kimliğini döner."""
    state = _scan_state(chat_id)
    text = _scan_text(count, state is not None, note)
    target = edit_id or (state or {}).get("msg")
    if target:
        try:
            telegram.edit_message(chat_id, target, text, SCAN_BUTTONS)
            return target
        except telegram.TelegramError:
            pass  # silinmiş ya da çok eski mesaj: yenisini gönder
    sent = telegram.send_message(chat_id, text, buttons=SCAN_BUTTONS)
    return sent.get("message_id") if isinstance(sent, dict) else None


def _add_scan_pages(user, files):
    """Telegram dosyalarını kutuya koyar: (kutudaki sayı, hatalar)."""
    from .modules.scanner import ScanError, inbox_add, inbox_count
    errors = []
    for f in files:
        try:
            inbox_add(user["id"], telegram.download_file(f["file_id"]), f["name"])
        except (telegram.TelegramError, ScanError) as e:
            errors.append(str(e))
    return inbox_count(user["id"]), errors


def scan_add_message(user, chat_id, msg):
    """Tarama modunda gelen fotoğraf: sormadan kutuya, durum mesajı güncellenir (albümde tek mesaj)."""
    file_id, name, mime = _image_part(msg)
    count, errors = _add_scan_pages(user, [{"file_id": file_id, "name": name, "mime": mime}])
    note = f"⚠️ {esc(errors[0])}" if errors else ""
    _scan_set(chat_id, _scan_show(chat_id, count, note))  # süre son sayfadan itibaren yeniden başlar


def _scan_files(user, chat_id, message_id, callback_id, pending):
    """Fotoğraf sorusundaki "📄 Taramaya ekle": soru mesajı durum mesajına dönüşür."""
    telegram.answer_callback(callback_id, "Taramaya ekleniyor...")
    count, errors = _add_scan_pages(user, _pending_files(pending))
    note = f"⚠️ {esc(errors[0])}" if errors else ""
    if not count:
        telegram.edit_message(chat_id, message_id, f"⚠️ Taramaya eklenemedi: {esc(errors[0] if errors else '')}")
        return
    _scan_show(chat_id, count, note, edit_id=message_id)


def cmd_scan(user, chat_id, rest):
    from .modules.scanner import inbox_count, inbox_remove
    word = fold(rest)
    if word in ("bitti", "bitir", "pdf", "tamam", "renkli", "gri"):
        mode = {"renkli": "color", "gri": "gray"}.get(word, "doc")
        scan_pdf(user, chat_id, mode)
        return
    if word in ("iptal", "bosalt", "temizle", "sil", "kapat"):
        n = inbox_remove(user["id"])
        _scan_end(chat_id)
        telegram.send_message(chat_id, f"🗑️ Tarama kapatıldı, kutudaki {n} sayfa silindi." if n else "Tarama kapatıldı.")
        return
    _scan_set(chat_id)
    _scan_set(chat_id, _scan_show(chat_id, inbox_count(user["id"]), "📷 Tarama modu açıldı."))


def scan_pdf(user, chat_id, mode="doc", message_id=None):
    """Kutudaki sayfalardan PDF yapar ve sohbete gönderir; başarılıysa kutu boşalır, mod kapanır."""
    from .modules.scanner import (MODES, TELEGRAM_MAX_BYTES, ScanError, build_pdf, close_files, default_title,
                                  inbox_files, inbox_items, inbox_remove, pdf_filename)
    from .utils import fmt_size
    rows = inbox_items(user["id"])
    if not rows:
        text = "📄 Tarama kutusu boş. <code>/tara</code> yazıp sayfaların fotoğraflarını gönder."
        if message_id:
            telegram.edit_message(chat_id, message_id, text)
        else:
            telegram.send_message(chat_id, text)
        return
    title = default_title()
    files = inbox_files(rows)
    try:
        pdf = build_pdf(files, [0] * len(files), mode if mode in MODES else "doc", title)
        if len(pdf) > TELEGRAM_MAX_BYTES:
            raise ScanError("PDF Telegram için çok büyük (en fazla 50 MB); web'deki Belge Tara'dan indir.")
        telegram.send_document(chat_id, pdf_filename(title), pdf, caption=f"📄 {title} · {len(rows)} sayfa",
                               mime="application/pdf")
    except (ScanError, telegram.TelegramError) as e:
        _scan_show(chat_id, len(rows), f"⚠️ PDF yapılamadı: {esc(e)}", edit_id=message_id)
        return
    finally:
        close_files(files)
    inbox_remove(user["id"], {r["id"] for r in rows})
    _scan_end(chat_id)
    done = f"✅ PDF gönderildi: {len(rows)} sayfa, {fmt_size(len(pdf))}. Tarama kutusu boşaldı."
    if message_id:
        try:
            telegram.edit_message(chat_id, message_id, done)
            return
        except telegram.TelegramError:
            pass
    telegram.send_message(chat_id, done)


def cb_scan(user, chat_id, message_id, callback_id, data):
    from .modules.scanner import inbox_remove
    parts = data.split(":")
    if parts[1:2] == ["pdf"]:
        telegram.answer_callback(callback_id, "PDF hazırlanıyor...")
        scan_pdf(user, chat_id, parts[2] if len(parts) > 2 else "doc", message_id)
    elif parts[1:2] == ["clr"]:
        n = inbox_remove(user["id"])
        _scan_end(chat_id)
        telegram.answer_callback(callback_id, "Boşaltıldı")
        telegram.edit_message(chat_id, message_id, f"🗑️ Tarama kutusu boşaltıldı ({n} sayfa).")
    else:
        telegram.answer_callback(callback_id)


# ---------- Zaman takibi ----------
def cmd_timer_start(user, chat_id, rest):
    """'/baslat web sitesi ana sayfa': baştaki kelimeler bir projenin adıysa o proje, kalanı not."""
    from .modules import timetrack as tt
    project, note = tt.match_project(user["id"], rest)
    entry_id, stopped = tt.start_timer(user["id"], project["id"] if project else None, note)
    lines = ["▶️ <b>Sayaç başladı</b>", tt.entry_line(tt.get_entry(entry_id, user["id"]), esc)]
    if stopped:
        lines.append(f"⏹️ Önceki sayaç durdu: {tt.fmt_duration(tt.seconds_of(stopped))} · {tt.entry_line(stopped, esc)}")
    lines.append(f'<a href="{esc(link("timetrack.index"))}">Zaman takibi →</a>')
    telegram.send_message(chat_id, "\n".join(lines), buttons=tt.stop_buttons(entry_id))


def cmd_timer_stop(user, chat_id, rest):
    from .modules import timetrack as tt
    stopped = tt.stop_timer(user["id"])
    if stopped is None:
        telegram.send_message(chat_id, "⏱️ Çalışan sayaç yok. Başlatmak için: <code>/baslat proje not</code>")
        return
    telegram.send_message(chat_id, tt.stopped_message(stopped, link("timetrack.edit", entry_id=stopped["id"]), esc))


def cmd_timer_status(user, chat_id, rest):
    from .modules import timetrack as tt
    text, current = tt.status_message(user["id"], link("timetrack.index"), esc)
    telegram.send_message(chat_id, text, buttons=tt.stop_buttons(current["id"]) if current else None)


def cb_timer(user, chat_id, message_id, callback_id, data):
    """⏹️ Durdur butonu (tt:<kayıt_id>): sadece o sayacı durdurur."""
    from .modules import timetrack as tt
    raw = data.split(":")[1] if ":" in data else ""
    entry = tt.get_entry(int(raw), user["id"]) if raw.isdigit() else None
    if entry is None:
        telegram.answer_callback(callback_id, "Kayıt bulunamadı, silinmiş olabilir.")
        return
    stopped = tt.stop_entry(entry["id"], user["id"])
    if stopped is None:
        telegram.answer_callback(callback_id, "Bu sayaç zaten durmuş.")
        try:
            telegram.edit_buttons(chat_id, message_id, [])
        except telegram.TelegramError:
            pass
        return
    telegram.answer_callback(callback_id, f"⏹️ Durdu: {tt.fmt_duration(tt.seconds_of(stopped))}")
    try:
        telegram.edit_message(chat_id, message_id, tt.stopped_message(stopped, link("timetrack.edit", entry_id=stopped["id"]),
                                                                      esc))
    except telegram.TelegramError:
        pass


# ---------- Yapay zekâ: fiş okuma ----------
def _read_receipt(user, chat_id, message_id, callback_id, pending):
    telegram.answer_callback(callback_id, "Okunuyor…")
    telegram.edit_message(chat_id, message_id, "🔍 Fiş okunuyor…")
    try:
        receipt = assistant.read_receipt(telegram.download_file(pending["file_id"]))
    except (ai.AIError, telegram.TelegramError) as e:
        telegram.edit_message(chat_id, message_id, f"⚠️ Okunamadı: {esc(str(e)[:200])}")
        return
    if not receipt["is_receipt"]:
        telegram.edit_message(chat_id, message_id, "🤷 Bu bir fiş ya da fatura gibi görünmüyor; tutarı okuyamadım.")
        return
    _set_pending(chat_id, {"kind": "receipt", "receipt": receipt})
    buttons = [[("✅ Harcama olarak kaydet", "rc:exp")]]
    if receipt["kind"] == "bill" or receipt["due_date"]:
        buttons.insert(0 if receipt["kind"] == "bill" else 1, [("🧾 Fatura olarak kaydet", "rc:bill")])
    buttons.append([("✖️ Vazgeç", "rc:x")])
    telegram.edit_message(chat_id, message_id, _receipt_card(receipt), buttons)


def _receipt_card(r):
    head = "🧾 <b>Fatura okundu</b>" if r["kind"] == "bill" else "🧾 <b>Fiş okundu</b>"
    lines = [head, f"<b>{fmt_money(r['total'], r['currency'])}</b> · {esc(r['category'])}"
             + (f" · {esc(r['merchant'])}" if r["merchant"] else "")]
    lines.append(f"📅 {fmt_date(r['date'], True)}" + (f" · son ödeme {fmt_date(r['due_date'], True)}" if r["due_date"] else ""))
    if r["summary"]:
        lines.append(f"<i>{esc(r['summary'])}</i>")
    if r["currency"] != "TRY":
        lines.append("Harcamalar TL tutulduğu için güncel kurla çevrilerek kaydedilir.")
    return "\n".join(lines)


def _in_try(amount, currency):
    from . import external
    if currency == "TRY":
        return amount, ""
    converted = external.to_try(amount, currency)
    if converted is None:
        return amount, f" ({fmt_money(amount, currency)}, kur alınamadı)"
    return round(converted, 2), f" ({fmt_money(amount, currency)})"


def cb_receipt(user, chat_id, message_id, callback_id, data):
    choice = data.split(":", 1)[1]
    pending = _pop_pending(chat_id, "receipt")
    telegram.answer_callback(callback_id)
    if choice == "x" or pending is None:
        telegram.edit_message(chat_id, message_id, "✖️ Vazgeçildi." if choice == "x" else "⌛ Süre doldu.")
        return
    r, uid = pending["receipt"], user["id"]
    amount, note_extra = _in_try(r["total"], r["currency"])
    if choice == "bill":
        bill_id = execute(
            "INSERT INTO bills (user_id, name, amount, due_date, remind, note) VALUES (?, ?, ?, ?, 1, ?)",
            (uid, r["merchant"] or "Fatura", amount, r["due_date"] or r["date"], (r["summary"] + note_extra).strip()),
        ).lastrowid
        telegram.edit_message(chat_id, message_id,
                              f"✅ Fatura kaydedildi: <b>{esc(r['merchant'] or 'Fatura')}</b> · {fmt_money(amount)}"
                              f" · son gün {fmt_date(r['due_date'] or r['date'], True)}\n"
                              f'<a href="{esc(link("bills.edit", bill_id=bill_id))}">Faturayı aç →</a>')
        return
    expense_id = execute("INSERT INTO expenses (user_id, amount, category, note, date) VALUES (?, ?, ?, ?, ?)",
                         (uid, amount, r["category"], (r["merchant"] + note_extra).strip()[:200], r["date"])).lastrowid
    automation.fire("expense_added", uid, **{"tutar": amount, "kategori": r["category"], "not": r["merchant"]})
    telegram.edit_message(chat_id, message_id,
                          f"✅ Harcama kaydedildi: <b>{fmt_money(amount)}</b> · {esc(r['category'])}"
                          + (f" · {esc(r['merchant'])}" if r["merchant"] else "")
                          + f"\nBu ay toplam: {fmt_money(_month_total(uid))}",
                          [[("↩️ Geri al", f"exu:{expense_id}")]])


# ---------- Yapay zekâ: doğal dil ve soru ----------
def cmd_ask(user, chat_id, rest):
    if not assistant.available(user):
        telegram.send_message(chat_id, "🤖 Yapay zekâ kapalı. Panoda <b>Ayarlar → Yapay zekâ</b> bölümünden açabilirsin"
                                       " (yöneticinin sağlayıcıyı ayarlamış olması gerekir).")
        return
    if not rest:
        telegram.send_message(chat_id, "Örnek: <code>/sor bu ay markete ne kadar harcadım?</code>")
        return
    _answer(user, chat_id, rest)


def _answer(user, chat_id, question):
    telegram.send_typing(chat_id)
    try:
        answer = assistant.answer_question(user, question)
    except ai.AIError as e:
        telegram.send_message(chat_id, f"⚠️ Yapay zekâ yanıt vermedi: {esc(str(e)[:200])}")
        return
    telegram.send_message(chat_id, "🤖 " + esc(answer)[:3900])


def _intent_card(intent, user):
    a = intent
    if a["action"] == "expense":
        text = f"💸 <b>Harcama</b>: {fmt_money(a['amount'])} · {esc(a['category'])}" + (f" · {esc(a['text'])}" if a["text"] else "")
        if a["date"] and a["date"] != today_str():
            text += f"\n📅 {fmt_date(a['date'], True)}"
        return text
    when = (fmt_date(a["date"], True) + (f" {a['time']}" if a["time"] else "")) if a["date"] else ""
    if a["action"] == "todo":
        return f"☑️ <b>Yapılacak</b>: {esc(a['text'])}" + (f"\n📅 {when} · 🔔 zamanı gelince" if when else "")
    if a["action"] == "event":
        return (f"👨‍👩‍👧 <b>Ortak etkinlik</b>: {esc(a['text'])}\n📅 {when or fmt_date(a['date'], True)}"
                + (f" · {esc(a['place'])}" if a["place"] else ""))
    if a["action"] == "appointment":
        return (f"🩺 <b>Randevu</b>: {esc(a['text'])}\n📅 {when or fmt_date(a['date'], True)}"
                + (f" · {esc(a['place'])}" if a["place"] else ""))
    if a["action"] == "shopping":
        lst = _find_list(user["id"], a["list_name"], "shopping") if a["list_name"] else None
        return f"🛒 <b>{esc(lst['name'] if lst else 'Alışveriş listesi')}</b>: {esc(', '.join(a['items']))}"
    return f"📝 <b>Not</b>: {esc(a['text'])}"


def _natural(user, chat_id, text):
    """True: yapay zekâ işledi; False: anlaşılamadı, klasik seçeneklere dön."""
    telegram.send_typing(chat_id)
    intent = assistant.parse_intent(user, text)
    if intent["action"] == "question":
        _answer(user, chat_id, intent["text"] or text)
        return True
    if intent["action"] == "unknown":
        return False
    _set_pending(chat_id, {"kind": "intent", "intent": intent, "text": text[:2000]})
    telegram.send_message(chat_id, _intent_card(intent, user),
                          buttons=[[("✅ Kaydet", "ai:ok"), ("🔀 Başka türlü", "ai:alt")], [("✖️ Vazgeç", "ai:x")]])
    return True


def _save_intent(user, a):
    """Onaylanan niyeti kaydeder; (mesaj, butonlar) döner."""
    from .modules.lists import add_items
    uid = user["id"]
    if a["action"] == "expense":
        expense_id = execute("INSERT INTO expenses (user_id, amount, category, note, date) VALUES (?, ?, ?, ?, ?)",
                             (uid, a["amount"], a["category"], a["text"][:200], a["date"] or today_str())).lastrowid
        automation.fire("expense_added", uid, **{"tutar": a["amount"], "kategori": a["category"], "not": a["text"][:200]})
        return (f"✅ Harcama kaydedildi: <b>{fmt_money(a['amount'])}</b> · {esc(a['category'])}"
                f"\nBu ay toplam: {fmt_money(_month_total(uid))}", [[("↩️ Geri al", f"exu:{expense_id}")]])
    if a["action"] == "todo":
        lst = (_find_list(uid, a["list_name"], "todo") if a["list_name"] else None) or _default_list(uid, "todo")
        item_id = execute(
            "INSERT INTO list_items (list_id, text, due_date, due_time, remind_before, created_by) VALUES (?, ?, ?, ?, ?, ?)",
            (lst["id"], a["text"], a["date"], a["time"] if a["date"] else None, 0 if a["date"] else None, uid)).lastrowid
        return f"✅ Yapılacak eklendi: <b>{esc(a['text'])}</b> · {esc(lst['name'])}", todo.done_buttons(item_id)
    if a["action"] == "event":
        execute("INSERT INTO events (user_id, title, date, time, place, shared, remind_before) VALUES (?, ?, ?, ?, ?, 1, 0)",
                (uid, a["text"], a["date"], a["time"], a["place"]))
        return (f"✅ Ortak takvime eklendi: <b>{esc(a['text'])}</b> · {fmt_date(a['date'], True)} {a['time'] or ''}".strip()
                + f'\n<a href="{esc(link("events.index"))}">Etkinlikler →</a>', None)
    if a["action"] == "appointment":
        execute("INSERT INTO appointments (user_id, title, place, starts_at) VALUES (?, ?, ?, ?)",
                (uid, a["text"], a["place"], f"{a['date']}T{a['time'] or todo.DEFAULT_DUE_TIME}"))
        return (f"✅ Randevu kaydedildi: <b>{esc(a['text'])}</b> · {fmt_date(a['date'], True)} {a['time'] or ''}".strip()
                + f'\n<a href="{esc(link("health.index", tab="randevu"))}">Randevular →</a>', None)
    if a["action"] == "shopping":
        lst = (_find_list(uid, a["list_name"], "shopping") if a["list_name"] else None) or _default_list(uid, "shopping")
        add_items(lst["id"], a["items"], uid)
        return (f"✅ <b>{esc(lst['name'])}</b>: {esc(', '.join(a['items']))} eklendi · {_open_count(lst['id'])} açık ürün",
                [[("📋 Listeyi göster", f"lv:{lst['id']}")]])
    execute("INSERT INTO notes (user_id, title, content, updated_at) VALUES (?, '', ?, CURRENT_TIMESTAMP)", (uid, a["text"]))
    return "✅ Not kaydedildi.", None


def cb_ai(user, chat_id, message_id, callback_id, data):
    choice = data.split(":", 1)[1]
    pending = _pop_pending(chat_id, "intent")
    telegram.answer_callback(callback_id)
    if choice == "x" or pending is None:
        telegram.edit_message(chat_id, message_id, "✖️ Vazgeçildi." if choice == "x" else "⌛ Süre doldu.")
        return
    if choice == "alt":
        telegram.edit_message(chat_id, message_id, f"✔️ <i>{esc(pending['text'][:80])}</i>")
        _ask_buttons(user, chat_id, pending["text"])
        return
    text, buttons = _save_intent(user, pending["intent"])
    telegram.edit_message(chat_id, message_id, text, buttons)


# ---------- Düz yazı ----------
def ask_text(user, chat_id, text):
    if assistant.available(user):
        try:
            if _natural(user, chat_id, text):
                return
        except ai.AIError as e:
            import logging
            logging.getLogger(__name__).warning("Yapay zekâ niyet çıkaramadı: %s", e)
    _ask_buttons(user, chat_id, text)


def _ask_buttons(user, chat_id, text):
    _set_pending(chat_id, {"kind": "text", "text": text[:2000]})
    row = [("☑️ Yapılacak", "tx:todo")]
    if parse_expense(user["id"], text):
        row.append(("💸 Harcama", "tx:exp"))
    buttons = [[("📝 Not", "tx:note"), ("🛒 Alışveriş", "tx:shop")], row, [("📓 Günlük", "tx:jr"), ("📤 Aktar", "tx:tr")],
               [("✖️ Vazgeç", "tx:x")]]
    preview = text if len(text) <= 80 else text[:77] + "..."
    telegram.send_message(chat_id, f"Bunu ne yapayım?\n<i>{esc(preview)}</i>", buttons=buttons)


def cb_text(user, chat_id, message_id, callback_id, data):
    choice = data.split(":", 1)[1]
    pending = _pop_pending(chat_id, "text")
    telegram.answer_callback(callback_id)
    if choice == "x" or pending is None:
        telegram.edit_message(chat_id, message_id, "✖️ Vazgeçildi." if choice == "x" else "⌛ Süre doldu.")
        return
    handler = {"note": cmd_note, "shop": cmd_shop, "todo": cmd_todo, "exp": cmd_expense, "jr": cmd_journal,
               "tr": cmd_transfer}.get(choice)
    if handler is None:
        return
    telegram.edit_message(chat_id, message_id, f"✔️ <i>{esc(pending['text'][:80])}</i>")
    handler(user, chat_id, pending["text"])
