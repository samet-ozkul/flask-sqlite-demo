"""🔑 Şifremi unuttum: Telegram'ı bağlı kullanıcıya tek kullanımlık, 15 dakikalık sıfırlama bağlantısı.

- Veritabanında bağlantının kendisi değil SHA-256 özeti tutulur; yedek ele geçse bile bağlantı üretilemez
- Bağlantıyı açmak (GET) onu harcamaz, sadece yeni şifre kaydedilince (POST) harcanır; böylece
  bağlantı önizleyicileri ya da tarayıcı ön yüklemesi bağlantıyı bozamaz
- Yeni istek eskisini geçersiz kılar; kullanıcı başına saatte 3, IP başına saatte 10 istek
- Formda kullanıcının var olup olmadığı belli edilmez (her durumda aynı cevap)
- Şifre değişince diğer oturumlar kapanır; iki adımlı giriş açıksa kod yine istenir
"""
import hashlib
import secrets
import time

from .db import get_db, query_one

TTL = 15 * 60
WINDOW = 3600
USER_LIMIT = 3
IP_LIMIT = 10


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def over_limit(key, limit):
    """Saatlik sayaç (login_attempts tablosunda). Sınır aşıldıysa True; değilse sayar ve False."""
    now = time.time()
    db = get_db()
    row = db.execute("SELECT count, first_at FROM login_attempts WHERE key = ?", (key,)).fetchone()
    if row and now - row["first_at"] < WINDOW:
        if row["count"] >= limit:
            return True
        db.execute("UPDATE login_attempts SET count = count + 1 WHERE key = ?", (key,))
    else:
        db.execute("INSERT OR REPLACE INTO login_attempts (key, count, first_at) VALUES (?, 1, ?)", (key, now))
    db.commit()
    return False


def create(user_id):
    """Yeni bağlantı anahtarı; kullanıcının önceki bağlantıları ve süresi dolanlar silinir."""
    token = secrets.token_urlsafe(32)
    now = time.time()
    db = get_db()
    db.execute("DELETE FROM password_resets WHERE user_id = ? OR expires_at < ?", (user_id, now))
    db.execute("INSERT INTO password_resets (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
               (_hash(token), user_id, now + TTL))
    db.commit()
    return token


def lookup(token):
    """Geçerli bağlantının kullanıcısı ya da None."""
    if not token or len(token) > 100:
        return None
    return query_one("SELECT u.* FROM password_resets r JOIN users u ON u.id = r.user_id"
                     " WHERE r.token_hash = ? AND r.expires_at > ?", (_hash(token), time.time()))


def consume(token):
    """Bağlantıyı harcar (kullanıcının bütün bağlantılarıyla birlikte). Kullanıcı id'si ya da None."""
    user = lookup(token)
    if user is None:
        return None
    db = get_db()
    cur = db.execute("DELETE FROM password_resets WHERE user_id = ?", (user["id"],))
    db.commit()
    return user["id"] if cur.rowcount else None


def purge():
    db = get_db()
    db.execute("DELETE FROM password_resets WHERE expires_at < ?", (time.time(),))
    db.commit()


def request_message(user, url, escape):
    return (f"🔑 <b>Şifre sıfırlama</b>\n<b>{escape(user['username'])}</b> hesabı için şifre sıfırlama istendi.\n"
            f'<a href="{escape(url)}">Yeni şifre belirle →</a>\n\n'
            f"Bağlantı {TTL // 60} dakika geçerli ve bir kez kullanılabilir. Sen istemediysen bu mesajı yok say; "
            "şifren değişmez.")


def changed_message(user, escape):
    return (f"🔑 <b>{escape(user['username'])}</b> hesabının şifresi sıfırlama bağlantısıyla değiştirildi ve "
            "diğer cihazlardaki oturumlar kapatıldı.\nBunu sen yapmadıysan hemen yeni bir sıfırlama iste ve "
            "Ayarlar'dan iki adımlı girişi aç.")
