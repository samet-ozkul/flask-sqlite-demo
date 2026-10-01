"""🤖 Telegram webhook: mesajlardaki butonlar ve hesap bağlama.

Telegram, butona basıldığında ya da bota mesaj yazıldığında bu adrese POST eder.
İstek, webhook kurulurken verilen gizli anahtarla (X-Telegram-Bot-Api-Secret-Token)
doğrulanır; form olmadığı için CSRF kontrolünden muaftır.

Buton verisi (callback_data):
  done:<madde_id>  -> yapılacak işi tamamla, butonu "Geri al" yap
  undo:<madde_id>  -> yeniden aç, butonu "Tamamlandı" yap
"""
import json
import logging
import secrets
import time

from flask import Blueprint, abort, jsonify, request

from .. import bot_commands, scheduled, telegram
from .. import todo_reminders as todo
from ..auth import csrf_exempt
from ..db import execute, get_db, query, query_one
from ..todo_reminders import done_buttons, undo_buttons

bp = Blueprint("bot", __name__, url_prefix="/telegram")


@bp.route("/webhook", methods=["POST"])
@csrf_exempt
def webhook():
    if not telegram.enabled():
        abort(404)
    given = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secrets.compare_digest(given, telegram.webhook_secret()):
        abort(403)
    update = request.get_json(silent=True) or {}
    # Telegram cevap gecikirse (yapay zekâ çağrısı) aynı güncellemeyi tekrar gönderebilir: bir kez işle
    if update.get("update_id") is not None and not _first_time(update["update_id"]):
        return jsonify(ok=True)
    try:
        if "callback_query" in update:
            _handle_callback(update["callback_query"])
        elif "message" in update:
            _handle_message(update["message"])
    except Exception:
        # Telegram hata alırsa aynı güncellemeyi tekrar tekrar gönderir; kaydet ve her durumda 200 dön
        logging.getLogger(__name__).exception("Telegram güncellemesi işlenemedi")
    return jsonify(ok=True)


def _first_time(update_id):
    db = get_db()
    cur = db.execute("INSERT OR IGNORE INTO cache (key, value, expires_at) VALUES (?, '1', ?)",
                     (f"tg:update:{update_id}", time.time() + 2 * 86400))
    db.commit()
    return cur.rowcount == 1


def _handle_message(msg):
    text = (msg.get("text") or "").strip()
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    if not chat_id:
        return
    if not text.startswith("/start"):
        bot_commands.handle_message(msg)
        return
    code = text[len("/start"):].strip()
    user = query_one("SELECT id FROM users WHERE telegram_link_code = ?", (code,)) if code else None
    if user:
        execute("UPDATE users SET telegram_chat_id = ?, telegram_link_code = NULL WHERE id = ?", (chat_id, user["id"]))
        telegram.send_message(chat_id, "✅ Kişisel Pano bağlandı. Günlük özetler ve hatırlatmalar buraya gelecek.\n\n"
                                       + bot_commands.HELP)
    elif query_one("SELECT 1 FROM users WHERE telegram_chat_id = ?", (chat_id,)):
        telegram.send_message(chat_id, "Bu sohbet zaten panoya bağlı. 👍")
    else:
        telegram.send_message(chat_id, "Bağlamak için panoda <b>Ayarlar → Telegram'ı bağla</b> adımlarını izle.")


def _handle_callback(cq):
    callback_id = cq["id"]
    msg = cq.get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id") or (cq.get("from") or {}).get("id", ""))
    action, _, raw_id = (cq.get("data") or "").partition(":")
    handlers = {"done": _cb_todo, "undo": _cb_todo, "snz": _cb_snooze, "bill": _cb_bill, "billu": _cb_bill,
                "med": _cb_med, "medu": _cb_med}
    if action not in handlers:
        if not bot_commands.handle_callback(cq, chat_id, msg.get("message_id")):
            telegram.answer_callback(callback_id)
        return
    item_id = raw_id.split(":")[0]
    if not item_id.isdigit():
        telegram.answer_callback(callback_id)
        return
    # Butona basan kişi: bu sohbete bağlı pano kullanıcıları
    user_ids = [r["id"] for r in query("SELECT id FROM users WHERE telegram_chat_id = ?", (chat_id,))]
    if not user_ids:
        telegram.answer_callback(callback_id, "Bu sohbet panoya bağlı değil.")
        return
    buttons = handlers[action](action, int(item_id), raw_id, user_ids, callback_id)
    message_id = msg.get("message_id")
    if message_id and buttons is not None:
        try:
            telegram.edit_buttons(chat_id, message_id, buttons)
        except telegram.TelegramError:
            pass  # ör. "message is not modified" — iş zaten yapıldı


def _in(user_ids):
    return ",".join("?" * len(user_ids))


def _todo_item(item_id, user_ids):
    # Web'deki kuralın aynısı: kendi listesi ya da paylaşılan liste
    return query_one(
        "SELECT i.* FROM list_items i JOIN lists l ON l.id = i.list_id"
        f" WHERE i.id = ? AND (l.shared = 1 OR l.user_id IN ({_in(user_ids)}))",
        (item_id, *user_ids),
    )


def _cb_todo(action, item_id, raw, user_ids, callback_id):
    item = _todo_item(item_id, user_ids)
    if item is None:
        telegram.answer_callback(callback_id, "Madde bulunamadı, silinmiş olabilir.")
        return []
    todo.set_done(item_id, action == "done", actor_id=user_ids[0])
    if action == "done":
        note = " · sonraki eklendi 🔁" if item["repeat"] and item["due_date"] and not item["done"] else ""
        telegram.answer_callback(callback_id, "✅ Tamamlandı" + note)
        return undo_buttons(item_id)
    telegram.answer_callback(callback_id, "↩️ Yeniden açıldı")
    return done_buttons(item_id)


def _cb_snooze(action, item_id, raw, user_ids, callback_id):
    item = _todo_item(item_id, user_ids)
    if item is None or item["done"]:
        telegram.answer_callback(callback_id, "Madde bulunamadı ya da tamamlanmış.")
        return []
    mode = "1d" if raw.endswith(":1d") else "1h"
    new_date, new_time = todo.snooze(item_id, mode)
    label = ("yarın " if mode == "1d" else "") + new_time
    telegram.answer_callback(callback_id, f"⏰ {label} için ertelendi")
    return [[("✅ Tamamlandı", f"done:{item_id}")], [(f"⏰ Ertelendi: {label}", "noop")]]


def _cb_bill(action, bill_id, raw, user_ids, callback_id):
    from .bills import pay_bill
    bill = query_one(f"SELECT * FROM bills WHERE id = ? AND user_id IN ({_in(user_ids)})", (bill_id, *user_ids))
    if bill is None:
        telegram.answer_callback(callback_id, "Fatura bulunamadı.")
        return []
    key = f"billpay:{bill_id}"
    if action == "bill":
        if bill["paid"]:
            telegram.answer_callback(callback_id, "Bu fatura zaten ödenmiş.")
            return scheduled.bill_undo_buttons(bill_id)
        result = pay_bill(bill["user_id"], bill, add_expense=True)
        # Yanlış dokunuşta "Geri al" oluşan harcamayı ve sonraki faturayı da silebilsin
        execute("INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)",
                (key, json.dumps({"next_id": result["next_id"], "expense_id": result["expense_id"]})))
        telegram.answer_callback(callback_id, "✅ " + " ".join(result["messages"])[:190])
        return scheduled.bill_undo_buttons(bill_id)
    created = query_one("SELECT value FROM app_state WHERE key = ?", (key,))
    if created:
        ids = json.loads(created["value"])
        if ids.get("expense_id"):
            execute("DELETE FROM expenses WHERE id = ? AND user_id = ?", (ids["expense_id"], bill["user_id"]))
        if ids.get("next_id"):
            execute("DELETE FROM bills WHERE id = ? AND user_id = ? AND paid = 0", (ids["next_id"], bill["user_id"]))
        execute("DELETE FROM app_state WHERE key = ?", (key,))
    execute("UPDATE bills SET paid = 0, paid_at = NULL WHERE id = ?", (bill_id,))
    telegram.answer_callback(callback_id, "↩️ Ödeme geri alındı")
    return scheduled.bill_buttons(bill_id)


def _cb_med(action, log_id, raw, user_ids, callback_id):
    log = query_one(
        "SELECT l.*, m.name FROM med_logs l JOIN medications m ON m.id = l.med_id"
        f" WHERE l.id = ? AND m.user_id IN ({_in(user_ids)})", (log_id, *user_ids))
    if log is None:
        telegram.answer_callback(callback_id, "Kayıt bulunamadı.")
        return []
    scheduled.set_med_taken(log_id, action == "med")
    if action == "med":
        telegram.answer_callback(callback_id, f"✅ {log['name']} alındı")
        return scheduled.med_undo_buttons(log_id)
    telegram.answer_callback(callback_id, "↩️ Geri alındı")
    return scheduled.med_buttons(log_id)
