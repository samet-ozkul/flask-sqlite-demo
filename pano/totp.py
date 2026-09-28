"""İki adımlı giriş: RFC 6238 TOTP (Google Authenticator, Microsoft Authenticator, 1Password, Aegis...).

- 30 saniyelik adım, 6 hane, SHA-1 (uygulamaların varsayılanı); saat kaymasına karşı ±1 adım
- Kullanılan adım kaydedilir: aynı kod ikinci kez kabul edilmez
- Telefon kaybolursa diye 8 tek kullanımlık yedek kod (hash'li saklanır)
"""
import base64
import hashlib
import hmac
import io
import secrets
import struct
import time
import urllib.parse

import segno
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db, query

STEP = 30
DIGITS = 6
WINDOW = 1
ISSUER = "Kişisel Pano"
RECOVERY_COUNT = 8


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def _code_at(secret, counter, digits=DIGITS):
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** digits).zfill(digits)


def code_now(secret, at=None):
    return _code_at(secret, int((at or time.time()) // STEP))


def verify(secret, code, last_step=None, at=None):
    """Kod geçerliyse eşleşen adımı döner, değilse None. last_step ve öncesi reddedilir (tekrar kullanım)."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != DIGITS or not secret:
        return None
    current = int((at or time.time()) // STEP)
    for step in range(current - WINDOW, current + WINDOW + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(_code_at(secret, step), code):
            return step
    return None


def provisioning_uri(secret, account):
    label = urllib.parse.quote(f"{ISSUER}:{account}")
    params = urllib.parse.urlencode({"secret": secret, "issuer": ISSUER, "digits": DIGITS, "period": STEP})
    return f"otpauth://totp/{label}?{params}"


def qr_svg(data):
    """Beyaz zeminli SVG QR (koyu temada da okunur)."""
    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="svg", scale=5, border=3, dark="#000", light="#fff",
                                     xmldecl=False, svgclass="qr")
    return buf.getvalue().decode("utf-8")


def format_secret(secret):
    """Elle girmek için 4'lü gruplar: 'ABCD EFGH ...'"""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


# ---------- Yedek kodlar ----------
def _normalize_recovery(code):
    return "".join(ch for ch in (code or "").lower() if ch.isalnum())


def new_recovery_codes(user_id):
    """Eski kodları siler, 8 yeni kod üretir ve düz metin olarak döner (bir kez gösterilir)."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"  # karışan harfler (i, l, o, 0, 1) yok
    codes = ["".join(secrets.choice(alphabet) for _ in range(8)) for _ in range(RECOVERY_COUNT)]
    db = get_db()
    db.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))
    db.executemany("INSERT INTO recovery_codes (user_id, code_hash) VALUES (?, ?)",
                   [(user_id, generate_password_hash(c)) for c in codes])
    db.commit()
    return [f"{c[:4]}-{c[4:]}" for c in codes]


def use_recovery_code(user_id, code):
    """Kullanılmamış bir yedek kodla eşleşirse onu harcar ve True döner."""
    code = _normalize_recovery(code)
    if len(code) != 8:
        return False
    for row in query("SELECT id, code_hash FROM recovery_codes WHERE user_id = ? AND used_at IS NULL", (user_id,)):
        if check_password_hash(row["code_hash"], code):
            db = get_db()
            db.execute("UPDATE recovery_codes SET used_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],))
            db.commit()
            return True
    return False


def recovery_left(user_id):
    return query("SELECT COUNT(*) AS n FROM recovery_codes WHERE user_id = ? AND used_at IS NULL", (user_id,))[0]["n"]


def disable(user_id):
    db = get_db()
    db.execute("UPDATE users SET totp_enabled = 0, totp_secret = NULL, totp_last_step = NULL WHERE id = ?", (user_id,))
    db.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))
    db.commit()
