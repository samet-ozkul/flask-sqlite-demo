"""💾 Kullanıcı kotaları: yöneticinin kullanıcı başına koyduğu depolama alanı ve dosya başına boyut sınırı.

- users.quota_mb: kullanıcının dosyalarının toplam kaplayabileceği alan (NULL = sınır yok, sadece genel disk kotası)
- users.upload_max_mb: tek dosyanın en fazla boyutu (NULL = modülün kendi sınırı; 0 = dosya yükleyemez)
Sayılanlar: ekler (çöp kutusundakiler dahil, dosyaları diskte durur), Aktar dosyaları, tarama kutusu, profil
fotoğrafı. Metin kayıtları (not, harcama...) çok küçük olduğu için sayılmaz. Genel disk kotası (STORAGE_QUOTA_MB)
her zaman ayrıca geçerlidir.
Yeni kullanıcıların varsayılanı: DEFAULT_QUOTA_MB / DEFAULT_UPLOAD_MAX_MB (boşsa sınırsız).
"""
import json
import os
import time

from .db import query, query_one
from .utils import fmt_size

MB = 1024 * 1024
MAX_UPLOAD_SETTING = 1000  # yöneticinin girebileceği en büyük dosya sınırı (MB)


class QuotaError(ValueError):
    """Kota aşıldı; ValueError'dan türer, böylece mevcut "geçersiz dosya" yakalamaları da bunu yakalar."""


def env_default(name):
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw.isdigit() else None


def limits(user_id):
    """(quota_mb, upload_max_mb) — None: sınır yok."""
    row = query_one("SELECT quota_mb, upload_max_mb FROM users WHERE id = ?", (user_id,))
    return (row["quota_mb"], row["upload_max_mb"]) if row else (None, None)


def _trash_attachment_bytes(user_id):
    total = 0
    for row in query("SELECT payload FROM trash WHERE user_id = ?", (user_id,)):
        try:
            total += sum(int(a.get("size") or 0) for a in json.loads(row["payload"])["rows"].get("attachments", []))
        except (ValueError, KeyError, TypeError):
            continue
    return total


def usage(user_id):
    """Kullanıcının dosyalarının kapladığı alan (bayt), kalem kalem ve toplam."""
    now = time.time()
    parts = {
        "attachments": query_one("SELECT COALESCE(SUM(size), 0) AS s FROM attachments WHERE user_id = ?", (user_id,))["s"],
        "trash": _trash_attachment_bytes(user_id),
        "transfer": query_one("SELECT COALESCE(SUM(size), 0) AS s FROM transfers WHERE user_id = ? AND expires_at > ?",
                              (user_id, now))["s"],
        "scan": query_one("SELECT COALESCE(SUM(size), 0) AS s FROM scan_inbox WHERE user_id = ? AND expires_at > ?",
                          (user_id, now))["s"],
        "profile": query_one("SELECT COALESCE(SUM(length(photo)), 0) AS s FROM public_profiles WHERE user_id = ?",
                             (user_id,))["s"],
    }
    parts["total"] = sum(parts.values())
    return parts


def summary(user_id):
    """Ayarlar ve yönetim sayfası için: kullanım, sınırlar ve yüzde."""
    quota_mb, upload_max = limits(user_id)
    u = usage(user_id)
    if quota_mb is None:
        percent = None
    else:
        percent = 100 if quota_mb == 0 else min(100, round(u["total"] * 100 / (quota_mb * MB)))
    return {"usage": u, "quota_mb": quota_mb, "upload_max_mb": upload_max, "percent": percent}


def max_file_bytes(user_id, default):
    """Modülün kendi sınırı ile yöneticinin dosya sınırından küçük olanı (sayfadaki ipuçları için)."""
    _quota, upload_max = limits(user_id)
    return default if upload_max is None else min(default, upload_max * MB)


def check_file(user_id, size, label="Dosya"):
    """Tek dosya yöneticinin koyduğu boyut sınırını aşıyor mu? Aşıyorsa QuotaError."""
    _quota, upload_max = limits(user_id)
    if upload_max is None:
        return
    if upload_max == 0:
        raise QuotaError("Hesabında dosya yükleme kapalı; yöneticiye başvur.")
    if size > upload_max * MB:
        raise QuotaError(f"{label} çok büyük ({fmt_size(size)}); en fazla {upload_max} MB yükleyebilirsin (yönetici sınırı).")


def check_space(user_id, extra_bytes):
    """Kullanıcının alanı extra_bytes daha almaya yetiyor mu? Yetmiyorsa QuotaError."""
    quota_mb, _upload = limits(user_id)
    if quota_mb is None or extra_bytes <= 0:
        return
    used = usage(user_id)["total"]
    if used + extra_bytes > quota_mb * MB:
        raise QuotaError(f"Depolama alanın doldu ({fmt_size(used)} / {quota_mb} MB). Eski dosyaları ya da çöp kutusunu "
                         "boşalt, ya da yöneticiden alan iste.")


def check(user_id, size, label="Dosya"):
    check_file(user_id, size, label)
    check_space(user_id, size)
