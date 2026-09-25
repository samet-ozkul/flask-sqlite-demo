"""Telegram Bot API (api.telegram.org PythonAnywhere izin listesinde).

Webhook kullanılmıyor: hesap bağlama getUpdates ile, bildirimler dışarıdan tetiklenen
cron adresi ile gönderiliyor. Böylece arka planda çalışan işlem gerekmiyor.
"""
import html
import json
import os
import uuid
import urllib.error
import urllib.parse
import urllib.request

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


def send_message(chat_id, text_html):
    return _call("sendMessage", {"chat_id": chat_id, "text": text_html, "parse_mode": "HTML",
                                 "disable_web_page_preview": "true"})


def send_document(chat_id, filename, data, caption=""):
    return _call("sendDocument", {"chat_id": chat_id, "caption": caption},
                 files={"document": (filename, data, "application/zip")})


def bot_username():
    if not enabled():
        return None
    result = cached(f"tg:me:{token()[:10]}", 7 * 86400, lambda: _call("getMe"))
    return result.get("username") if result else None


def find_chat_for_code(code):
    """Kullanıcı bota '/start KOD' gönderdiyse chat id'sini döner."""
    for update in _call("getUpdates", {"limit": 100, "allowed_updates": '["message"]'}):
        msg = update.get("message") or {}
        if (msg.get("text") or "").strip() == f"/start {code}":
            return str(msg["chat"]["id"])
    return None
