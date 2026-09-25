"""🤖 Telegram webhook: mesajlardaki butonlar ve hesap bağlama.

Telegram, butona basıldığında ya da bota mesaj yazıldığında bu adrese POST eder.
İstek, webhook kurulurken verilen gizli anahtarla (X-Telegram-Bot-Api-Secret-Token)
doğrulanır; form olmadığı için CSRF kontrolünden muaftır.

Buton verisi (callback_data):
  done:<madde_id>  -> yapılacak işi tamamla, butonu "Geri al" yap
  undo:<madde_id>  -> yeniden aç, butonu "Tamamlandı" yap
"""
import secrets

from flask import Blueprint, abort, jsonify, request

from .. import telegram
from ..auth import csrf_exempt
from ..db import execute, query, query_one

bp = Blueprint("bot", __name__, url_prefix="/telegram")


def done_buttons(item_id):
    return [[("✅ Tamamlandı", f"done:{item_id}")]]


def undo_buttons(item_id):
    return [[("↩️ Geri al", f"undo:{item_id}")]]


@bp.route("/webhook", methods=["POST"])
@csrf_exempt
def webhook():
    if not telegram.enabled():
        abort(404)
    given = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secrets.compare_digest(given, telegram.webhook_secret()):
        abort(403)
    update = request.get_json(silent=True) or {}
    try:
        if "callback_query" in update:
            _handle_callback(update["callback_query"])
        elif "message" in update:
            _handle_message(update["message"])
    except telegram.TelegramError:
        pass  # Telegram hata alırsa aynı güncellemeyi tekrar tekrar gönderir; her durumda 200 dön
    return jsonify(ok=True)


def _handle_message(msg):
    text = (msg.get("text") or "").strip()
    chat_id = str((msg.get("chat") or {}).get("id", ""))
    if not chat_id or not text.startswith("/start"):
        return
    code = text[len("/start"):].strip()
    user = query_one("SELECT id FROM users WHERE telegram_link_code = ?", (code,)) if code else None
    if user:
        execute("UPDATE users SET telegram_chat_id = ?, telegram_link_code = NULL WHERE id = ?", (chat_id, user["id"]))
        telegram.send_message(chat_id, "✅ Kişisel Pano bağlandı. Günlük özetler ve hatırlatmalar buraya gelecek.")
    elif query_one("SELECT 1 FROM users WHERE telegram_chat_id = ?", (chat_id,)):
        telegram.send_message(chat_id, "Bu sohbet zaten panoya bağlı. 👍")
    else:
        telegram.send_message(chat_id, "Bağlamak için panoda <b>Ayarlar → Telegram'ı bağla</b> adımlarını izle.")


def _handle_callback(cq):
    callback_id = cq["id"]
    msg = cq.get("message") or {}
    chat_id = str((msg.get("chat") or {}).get("id") or (cq.get("from") or {}).get("id", ""))
    action, _, raw_id = (cq.get("data") or "").partition(":")
    if action not in ("done", "undo") or not raw_id.isdigit():
        telegram.answer_callback(callback_id)
        return

    # Butona basan kişi: bu sohbete bağlı pano kullanıcıları
    user_ids = [r["id"] for r in query("SELECT id FROM users WHERE telegram_chat_id = ?", (chat_id,))]
    if not user_ids:
        telegram.answer_callback(callback_id, "Bu sohbet panoya bağlı değil.")
        return
    marks = ",".join("?" * len(user_ids))
    # Web'deki kuralın aynısı: kendi listesi ya da paylaşılan liste
    item = query_one(
        "SELECT i.* FROM list_items i JOIN lists l ON l.id = i.list_id"
        f" WHERE i.id = ? AND (l.shared = 1 OR l.user_id IN ({marks}))",
        (int(raw_id), *user_ids),
    )
    message_id = msg.get("message_id")
    if item is None:
        telegram.answer_callback(callback_id, "Madde bulunamadı, silinmiş olabilir.")
        if message_id:
            telegram.edit_buttons(chat_id, message_id, [])
        return

    if action == "done":
        if not item["done"]:
            execute("UPDATE list_items SET done = 1, done_at = CURRENT_TIMESTAMP WHERE id = ?", (item["id"],))
        telegram.answer_callback(callback_id, "✅ Tamamlandı")
        buttons = undo_buttons(item["id"])
    else:
        if item["done"]:
            execute("UPDATE list_items SET done = 0, done_at = NULL WHERE id = ?", (item["id"],))
        telegram.answer_callback(callback_id, "↩️ Yeniden açıldı")
        buttons = done_buttons(item["id"])
    if message_id:
        try:
            telegram.edit_buttons(chat_id, message_id, buttons)
        except telegram.TelegramError:
            pass  # ör. "message is not modified" — iş zaten yapıldı
