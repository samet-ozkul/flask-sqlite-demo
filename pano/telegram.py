"""Telegram Bot API (api.telegram.org PythonAnywhere izin listesinde).

Bildirimler dışarıdan tetiklenen cron adresiyle gönderilir; arka planda çalışan işlem gerekmez.

Mesajlardaki butonlar (✅ Tamamlandı) için yönetim sayfasından webhook kurulur: Telegram
butona basıldığında /telegram/webhook adresine haber verir. Webhook açıkken Telegram
getUpdates'e izin vermediği için hesap bağlama ("/start KOD") da webhook üzerinden yapılır;
webhook kurulmamışsa eski yöntem (getUpdates) çalışmaya devam eder.
"""
import hashlib
import hmac
import html
import json
import os
import uuid
import urllib.error
import urllib.parse
import urllib.request

from flask import current_app

from .db import query_one
from .external import cached

TIMEOUT = 15


class TelegramError(Exception):
    pass


def token():
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def enabled():
    return bool(token())


def _call(method, params=None, files=None):
    if not enabled():
        raise TelegramError("TELEGRAM_BOT_TOKEN ayarlı değil.")
    url = f"https://api.telegram.org/bot{token()}/{method}"
    if files:
        boundary = uuid.uuid4().hex
        body = bytearray()
        for key, value in (params or {}).items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n").encode()
        for key, (filename, data, mime) in files.items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"; filename=\"{filename}\"\r\n"
                     f"Content-Type: {mime}\r\n\r\n").encode()
            body += data + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, data=bytes(body),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(params or {}).encode())
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:
            raise TelegramError(f"HTTP {e.code}")
    except Exception as e:
        raise TelegramError(str(e))
    if not payload.get("ok"):
        raise TelegramError(payload.get("description", "Bilinmeyen Telegram hatası"))
    return payload["result"]


def escape(text):
    return html.escape(str(text), quote=False)


def keyboard(rows):
    """[[("Metin", "callback_data"), ...], ...] -> Telegram inline_keyboard JSON."""
    return json.dumps({"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]})


def send_message(chat_id, text_html, buttons=None):
    params = {"chat_id": chat_id, "text": text_html, "parse_mode": "HTML", "disable_web_page_preview": "true"}
    if buttons:
        params["reply_markup"] = keyboard(buttons)
    return _call("sendMessage", params)


def answer_callback(callback_id, text=""):
    """Butona basınca Telegram'ın gösterdiği bekleme simgesini kapatır, kısa bir bildirim gösterir."""
    return _call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


def edit_buttons(chat_id, message_id, buttons):
    return _call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                            "reply_markup": keyboard(buttons or [])})


def send_document(chat_id, filename, data, caption=""):
    return _call("sendDocument", {"chat_id": chat_id, "caption": caption},
                 files={"document": (filename, data, "application/zip")})


def bot_username():
    if not enabled():
        return None
    result = cached(f"tg:me:{token()[:10]}", 7 * 86400, lambda: _call("getMe"))
    return result.get("username") if result else None


# ---------- Webhook ----------
def webhook_secret():
    """Telegram'ın her webhook isteğinde başlıkta gönderdiği gizli anahtar (SECRET_KEY'den türetilir).
    SECRET_KEY değişirse webhook yönetim sayfasından yeniden kurulmalı."""
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(key, b"telegram-webhook", hashlib.sha256).hexdigest()[:48]


def set_webhook(url):
    return _call("setWebhook", {"url": url, "secret_token": webhook_secret(),
                                "allowed_updates": '["message", "callback_query"]', "max_connections": 5})


def webhook_active():
    """Yönetim sayfasından webhook kurulduysa True (butonlar ancak o zaman çalışır)."""
    row = query_one("SELECT value FROM app_state WHERE key = 'telegram_webhook'")
    return bool(row and row["value"])


def delete_webhook():
    return _call("deleteWebhook")


def webhook_info():
    return _call("getWebhookInfo")


def find_chat_for_code(code):
    """Kullanıcı bota '/start KOD' gönderdiyse chat id'sini döner."""
    for update in _call("getUpdates", {"limit": 100, "allowed_updates": '["message"]'}):
        msg = update.get("message") or {}
        if (msg.get("text") or "").strip() == f"/start {code}":
            return str(msg["chat"]["id"])
    return None
