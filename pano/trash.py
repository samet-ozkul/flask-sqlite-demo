"""Geri dönüşüm kutusu: silinen kayıt 30 gün saklanır, tek tıkla geri getirilir.

move() kaydı ve bağlı satırlarını (liste maddeleri, araç kayıtları, hedef hareketleri, ekler...)
JSON olarak kutuya koyar, sonra siler. Tablolar AUTOINCREMENT kullandığı için silinen id bir daha
verilmez; geri getirirken satırlar aynı id'lerle yerine konur, bağlantılar bozulmaz.
Ek dosyaları kutuda kaldığı sürece diskte durur; süre dolunca (purge) silinir.
"""
import json

from flask import url_for
from markupsafe import Markup, escape

from .db import get_db, query, query_one

KEEP_DAYS = 30


def notice(what):
    """Silme bildirimi: '<ad> çöp kutusuna taşındı. Geri getir →'"""
    return Markup('{} çöp kutusuna taşındı. <a href="{}">Geri getir →</a>').format(
        escape(what), url_for("trashbin.index"))


def _rows(table, where, args):
    return [dict(r) for r in query(f"SELECT * FROM {table} WHERE {where}", args)]


def move(user_id, module, label, parent, children=(), entity=None):
    """parent = (tablo, id); children = [(tablo, "fk = ?"), ...] parent id ile sorgulanır;
    entity = ekler için storage.ENTITIES anahtarı (ör. "note"). Kutudaki kaydın id'sini döner."""
    table, row_id = parent
    payload = {"order": [], "rows": {}}

    def capture(t, where, args):
        rows = _rows(t, where, args)
        if rows:
            payload["order"].append(t)
            payload["rows"].setdefault(t, []).extend(rows)

    capture(table, "id = ?", (row_id,))
    if not payload["rows"]:
        return None
    for child_table, where in children:
        capture(child_table, where, (row_id,))
    if entity:
        capture("attachments", "entity = ? AND entity_id = ?", (entity, row_id))
    db = get_db()
    cur = db.execute("INSERT INTO trash (user_id, module, label, payload) VALUES (?, ?, ?, ?)",
                     (user_id, module, label[:150], json.dumps(payload, ensure_ascii=False)))
    # Ek satırları da kaldırılır (dosyalar diskte kalır); çocuk satırlar ON DELETE CASCADE ile gider
    if entity:
        db.execute("DELETE FROM attachments WHERE entity = ? AND entity_id = ?", (entity, row_id))
    db.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
    db.commit()
    return cur.lastrowid


def restore(trash_id, user_id):
    """Kaydı yerine koyar. (başarılı_mı, mesaj)"""
    item = query_one("SELECT * FROM trash WHERE id = ? AND user_id = ?", (trash_id, user_id))
    if item is None:
        return False, "Kayıt bulunamadı."
    payload = json.loads(item["payload"])
    db = get_db()
    try:
        for table in payload["order"]:
            for row in payload["rows"][table]:
                cols = list(row)
                db.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                           [row[c] for c in cols])
        db.execute("DELETE FROM trash WHERE id = ?", (trash_id,))
        db.commit()
    except Exception as e:  # ör. bağlı olduğu liste de silinmişse
        db.rollback()
        return False, f"Geri getirilemedi: {e}"
    return True, f"{item['label']} geri getirildi."


def _attachment_files(payload):
    return [(r["filename"], r["thumb"]) for r in payload["rows"].get("attachments", [])]


def delete_forever(trash_id, user_id):
    from .storage import _remove_files
    item = query_one("SELECT * FROM trash WHERE id = ? AND user_id = ?", (trash_id, user_id))
    if item is None:
        return False
    for filename, thumb in _attachment_files(json.loads(item["payload"])):
        _remove_files({"filename": filename, "thumb": thumb})
    db = get_db()
    db.execute("DELETE FROM trash WHERE id = ?", (trash_id,))
    db.commit()
    return True


def purge(days=KEEP_DAYS):
    """Süresi dolanları kalıcı siler (cron /gunluk). Silinen kayıt sayısını döner."""
    old = query("SELECT id, user_id FROM trash WHERE deleted_at < datetime('now', ?)", (f"-{days} days",))
    for row in old:
        delete_forever(row["id"], row["user_id"])
    return len(old)


def kept_files():
    """Kutudaki kayıtların dosyaları: temizlik bunları sahipsiz saymamalı."""
    names = set()
    for row in query("SELECT payload FROM trash"):
        for filename, thumb in _attachment_files(json.loads(row["payload"])):
            names.add(filename)
            if thumb:
                names.add(thumb)
    return names
