"""SQLite bağlantısı, şema migration'ları ve küçük sorgu yardımcıları."""
import os
import sqlite3
from contextlib import closing

from flask import abort, current_app, g
from werkzeug.security import generate_password_hash

# Her eleman bir şema sürümü. Sıra değişmez, sadece sona eklenir.
# PRAGMA user_version hangi sürümde olduğumuzu tutar.
MIGRATIONS = [
    # 1: ilk demo şeması (mevcut kurulumlar bu sürümde)
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        is_admin INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    # 2: kişisel pano modülleri
    """
    ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT '';
    ALTER TABLE users ADD COLUMN city TEXT NOT NULL DEFAULT '';
    ALTER TABLE users ADD COLUMN lat REAL;
    ALTER TABLE users ADD COLUMN lon REAL;
    ALTER TABLE users ADD COLUMN telegram_chat_id TEXT;
    ALTER TABLE users ADD COLUMN telegram_link_code TEXT;
    ALTER TABLE users ADD COLUMN notify_daily INTEGER NOT NULL DEFAULT 1;

    ALTER TABLE notes ADD COLUMN title TEXT NOT NULL DEFAULT '';
    ALTER TABLE notes ADD COLUMN tags TEXT NOT NULL DEFAULT '';
    ALTER TABLE notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE notes ADD COLUMN updated_at TEXT;

    CREATE TABLE lists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'todo' CHECK (kind IN ('todo', 'shopping')),
        shared INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE list_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        list_id INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
        text TEXT NOT NULL,
        qty TEXT NOT NULL DEFAULT '',
        due_date TEXT,
        done INTEGER NOT NULL DEFAULT 0,
        done_at TEXT,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_list_items_list ON list_items(list_id, done);

    CREATE TABLE links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        url TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        tags TEXT NOT NULL DEFAULT '',
        is_read INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        amount REAL NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'TRY',
        cycle TEXT NOT NULL DEFAULT 'monthly' CHECK (cycle IN ('weekly', 'monthly', 'yearly')),
        next_date TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE bills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        amount REAL,
        due_date TEXT NOT NULL,
        paid INTEGER NOT NULL DEFAULT 0,
        paid_at TEXT,
        recurring INTEGER NOT NULL DEFAULT 0,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_bills_user_due ON bills(user_id, paid, due_date);

    CREATE TABLE debts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        person TEXT NOT NULL,
        direction TEXT NOT NULL CHECK (direction IN ('lent', 'borrowed')),
        amount REAL NOT NULL,
        currency TEXT NOT NULL DEFAULT 'TRY',
        date TEXT NOT NULL,
        due_date TEXT,
        settled INTEGER NOT NULL DEFAULT 0,
        settled_at TEXT,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        amount REAL NOT NULL,
        category TEXT NOT NULL DEFAULT 'Diğer',
        note TEXT NOT NULL DEFAULT '',
        date TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_expenses_user_date ON expenses(user_id, date);

    CREATE TABLE vehicles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        plate TEXT NOT NULL DEFAULT '',
        inspection_date TEXT,
        insurance_date TEXT,
        casco_date TEXT,
        service_date TEXT,
        service_km INTEGER,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE vehicle_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK (kind IN ('fuel', 'service', 'repair', 'other')),
        date TEXT NOT NULL,
        km INTEGER,
        amount REAL,
        liters REAL,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE warranties (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        product TEXT NOT NULL,
        brand TEXT NOT NULL DEFAULT '',
        store TEXT NOT NULL DEFAULT '',
        serial_no TEXT NOT NULL DEFAULT '',
        purchase_date TEXT,
        warranty_until TEXT,
        price REAL,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        location TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT '',
        quantity INTEGER NOT NULL DEFAULT 1,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE habits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        icon TEXT NOT NULL DEFAULT '✅',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE habit_logs (
        habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
        date TEXT NOT NULL,
        PRIMARY KEY (habit_id, date)
    );

    CREATE TABLE health_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK (kind IN ('weight', 'bp', 'sugar', 'pulse')),
        value1 REAL NOT NULL,
        value2 REAL,
        measured_at TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_health_user_kind ON health_metrics(user_id, kind, measured_at);
    CREATE TABLE medications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        dose TEXT NOT NULL DEFAULT '',
        times TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE appointments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        place TEXT NOT NULL DEFAULT '',
        starts_at TEXT NOT NULL,
        done INTEGER NOT NULL DEFAULT 0,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE recipes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        ingredients TEXT NOT NULL DEFAULT '',
        steps TEXT NOT NULL DEFAULT '',
        tags TEXT NOT NULL DEFAULT '',
        servings TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT
    );

    CREATE TABLE attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        entity TEXT NOT NULL,
        entity_id INTEGER NOT NULL,
        filename TEXT NOT NULL,
        thumb TEXT,
        original_name TEXT NOT NULL DEFAULT '',
        mime TEXT NOT NULL,
        size INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_attachments_entity ON attachments(entity, entity_id);

    CREATE TABLE cache (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        expires_at REAL NOT NULL
    );
    CREATE TABLE login_attempts (
        key TEXT PRIMARY KEY,
        count INTEGER NOT NULL,
        first_at REAL NOT NULL
    );
    CREATE TABLE app_state (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    """,
    # 3: yapılacaklar için saat + Telegram hatırlatması
    """
    ALTER TABLE list_items ADD COLUMN due_time TEXT;           -- 'HH:MM', boşsa varsayılan saat
    ALTER TABLE list_items ADD COLUMN remind_before INTEGER;   -- dakika; NULL = hatırlatma yok, 0 = zamanında
    ALTER TABLE list_items ADD COLUMN pre_sent_at TEXT;        -- "yaklaşıyor" mesajı gönderildi
    ALTER TABLE list_items ADD COLUMN due_sent_at TEXT;        -- "zamanı geldi" mesajı gönderildi
    CREATE INDEX idx_list_items_due ON list_items(done, due_date);
    """,
    # 4: tekrarlayan işler, fatura ve ilaç hatırlatmaları
    """
    ALTER TABLE list_items ADD COLUMN repeat TEXT;             -- NULL | daily | weekdays | weekly | monthly | yearly
    ALTER TABLE list_items ADD COLUMN spawned_id INTEGER;      -- tamamlanınca oluşturulan sonraki tekrar
    ALTER TABLE bills ADD COLUMN remind INTEGER NOT NULL DEFAULT 1;  -- Telegram'dan hatırlat
    ALTER TABLE bills ADD COLUMN pre_sent_at TEXT;             -- "yarın son gün" gönderildi
    ALTER TABLE bills ADD COLUMN due_sent_at TEXT;             -- "bugün son gün" gönderildi
    ALTER TABLE medications ADD COLUMN notify INTEGER NOT NULL DEFAULT 0;  -- doz saatinde Telegram
    CREATE TABLE med_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        med_id INTEGER NOT NULL REFERENCES medications(id) ON DELETE CASCADE,
        date TEXT NOT NULL,
        slot TEXT NOT NULL,          -- 'HH:MM'
        sent_at TEXT,
        taken_at TEXT,
        UNIQUE (med_id, date, slot)
    );
    """,
    # 5: önemli günler, kur alarmları
    """
    CREATE TABLE special_days (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'birthday' CHECK (kind IN ('birthday', 'anniversary', 'other')),
        month INTEGER NOT NULL,
        day INTEGER NOT NULL,
        year INTEGER,                              -- biliniyorsa yaş / kaçıncı yıl hesaplanır
        notify_days INTEGER NOT NULL DEFAULT 7,    -- kaç gün önce hatırlat (0 = sadece o gün)
        pre_sent_year INTEGER,                     -- "yaklaşıyor" mesajının gönderildiği yıl
        day_sent_year INTEGER,                     -- "bugün" mesajının gönderildiği yıl
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE rate_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        currency TEXT NOT NULL CHECK (currency IN ('USD', 'EUR', 'GBP', 'XAU')),  -- XAU: gram altın
        direction TEXT NOT NULL CHECK (direction IN ('above', 'below')),
        threshold REAL NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        triggered_at TEXT,
        triggered_value REAL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    # 6: aylık bütçe limitleri
    """
    CREATE TABLE budgets (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        category TEXT NOT NULL,        -- '*' = toplam aylık bütçe
        amount REAL NOT NULL,
        PRIMARY KEY (user_id, category)
    );
    """,
    # 7: telefon takvimine abonelik (ICS) için gizli adres
    """
    ALTER TABLE users ADD COLUMN calendar_token TEXT;
    CREATE UNIQUE INDEX idx_users_calendar_token ON users(calendar_token);
    """,
    # 8: iki adımlı giriş (TOTP) ve yedek kodlar
    """
    ALTER TABLE users ADD COLUMN totp_secret TEXT;
    ALTER TABLE users ADD COLUMN totp_enabled INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN totp_last_step INTEGER;   -- aynı kod ikinci kez kullanılamasın
    CREATE TABLE recovery_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        code_hash TEXT NOT NULL,
        used_at TEXT
    );
    """,
    # 9: yapay zekâ (kullanıcı açmadıkça hiçbir veri sağlayıcıya gönderilmez)
    """
    ALTER TABLE users ADD COLUMN ai_enabled INTEGER NOT NULL DEFAULT 0;
    """,
    # 10: varlıklar, birikim hedefleri, ortak harcama
    """
    CREATE TABLE assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('cash', 'fx', 'gold', 'other')),
        currency TEXT NOT NULL DEFAULT 'TRY',     -- fx: USD/EUR/GBP; diğerleri TRY
        quantity REAL NOT NULL,                   -- cash: TL; fx: döviz miktarı; gold: gram; other: adet
        unit_price REAL,                          -- gold/other: elle girilen birim TL fiyatı (canlı fiyat yoksa)
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT
    );
    CREATE TABLE asset_snapshots (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        date TEXT NOT NULL,
        total_try REAL NOT NULL,
        PRIMARY KEY (user_id, date)
    );
    CREATE TABLE goals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        icon TEXT NOT NULL DEFAULT '🏁',
        target REAL NOT NULL,
        deadline TEXT,
        note TEXT NOT NULL DEFAULT '',
        done_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE goal_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
        amount REAL NOT NULL,                     -- eksi değer: hedeften para çekildi
        date TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE split_groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE split_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER NOT NULL REFERENCES split_groups(id) ON DELETE CASCADE,
        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,   -- uygulama kullanıcısıysa
        name TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE split_expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER NOT NULL REFERENCES split_groups(id) ON DELETE CASCADE,
        payer_id INTEGER NOT NULL REFERENCES split_members(id) ON DELETE CASCADE,
        amount REAL NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        date TEXT NOT NULL,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE split_shares (
        expense_id INTEGER NOT NULL REFERENCES split_expenses(id) ON DELETE CASCADE,
        member_id INTEGER NOT NULL REFERENCES split_members(id) ON DELETE CASCADE,
        share REAL NOT NULL,
        PRIMARY KEY (expense_id, member_id)
    );
    CREATE TABLE split_settlements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER NOT NULL REFERENCES split_groups(id) ON DELETE CASCADE,
        from_id INTEGER NOT NULL REFERENCES split_members(id) ON DELETE CASCADE,
        to_id INTEGER NOT NULL REFERENCES split_members(id) ON DELETE CASCADE,
        amount REAL NOT NULL,
        date TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    # 11: iş atama ve aile etkinlikleri (ortak takvim)
    """
    ALTER TABLE list_items ADD COLUMN assignee_id INTEGER REFERENCES users(id) ON DELETE SET NULL;
    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        date TEXT NOT NULL,
        time TEXT,
        place TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        shared INTEGER NOT NULL DEFAULT 1,
        remind_before INTEGER,
        pre_sent_at TEXT,
        due_sent_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_events_date ON events(date);
    """,
    # 12: belge geçerlilik tarihleri, izleme/okuma listesi, hava uyarısı tercihi
    """
    CREATE TABLE documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'other'
            CHECK (kind IN ('passport', 'license', 'id', 'vehicle', 'insurance', 'residence', 'other')),
        holder TEXT NOT NULL DEFAULT '',
        expires_on TEXT NOT NULL,
        notify_days INTEGER NOT NULL DEFAULT 30,
        sent_for TEXT,
        day_sent_for TEXT,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_documents_user ON documents(user_id, expires_on);
    CREATE TABLE watchlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK (kind IN ('movie', 'series', 'book')),
        title TEXT NOT NULL,
        year TEXT NOT NULL DEFAULT '',
        creator TEXT NOT NULL DEFAULT '',
        image_url TEXT NOT NULL DEFAULT '',
        external_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'want' CHECK (status IN ('want', 'doing', 'done')),
        rating INTEGER,
        note TEXT NOT NULL DEFAULT '',
        started_at TEXT,
        finished_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_watchlist_user ON watchlist(user_id, status);
    ALTER TABLE users ADD COLUMN weather_alerts INTEGER NOT NULL DEFAULT 1;
    """,
    # 13: günlük ve ruh hali
    """
    CREATE TABLE journal (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        date TEXT NOT NULL,
        mood INTEGER CHECK (mood IS NULL OR mood BETWEEN 1 AND 5),
        text TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_id, date)
    );
    ALTER TABLE users ADD COLUMN journal_reminder TEXT;
    """,
    # 14: geri dönüşüm kutusu, tema ve pano düzeni
    """
    CREATE TABLE trash (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        module TEXT NOT NULL,
        label TEXT NOT NULL,
        payload TEXT NOT NULL,
        deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_trash_user ON trash(user_id, deleted_at);
    ALTER TABLE users ADD COLUMN theme TEXT NOT NULL DEFAULT 'auto';
    ALTER TABLE users ADD COLUMN dashboard_cards TEXT;
    """,
    # 15: oturum sürümü — artınca eski oturum çerezleri geçersiz olur (diğer cihazlardan çıkış)
    """
    ALTER TABLE users ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 0;
    """,
    # 16: şifremi unuttum (Telegram'a tek kullanımlık bağlantı; bağlantının özeti saklanır)
    """
    CREATE TABLE password_resets (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        expires_at REAL NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    # 17: şifreli kasa (sunucuda sadece şifreli veri ve sarılmış anahtar durur)
    """
    CREATE TABLE vault_meta (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        salt TEXT NOT NULL,
        iterations INTEGER NOT NULL,
        wrapped_key TEXT NOT NULL,
        hint TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE vault_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        data TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_vault_items_user ON vault_items(user_id);
    """,
    # 18: otomasyon kuralları ve çalışma kayıtları
    """
    CREATE TABLE automations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        trigger_type TEXT NOT NULL,
        trigger_config TEXT NOT NULL DEFAULT '{}',
        action_type TEXT NOT NULL,
        action_config TEXT NOT NULL DEFAULT '{}',
        last_period TEXT,
        last_run_at TEXT,
        run_count INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_automations_trigger ON automations(trigger_type, enabled);
    CREATE TABLE automation_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        automation_id INTEGER NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        ran_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        ok INTEGER NOT NULL,
        detail TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_automation_log ON automation_log(automation_id, ran_at);
    """,
    # 19: cihazlar arası aktarma (süreli metin ve dosya; dosyalar uploads/_aktarma/ altında)
    """
    CREATE TABLE transfers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK (kind IN ('text', 'file')),
        text TEXT NOT NULL DEFAULT '',
        filename TEXT NOT NULL DEFAULT '',
        stored_name TEXT NOT NULL DEFAULT '',
        size INTEGER NOT NULL DEFAULT 0,
        mime TEXT NOT NULL DEFAULT '',
        once INTEGER NOT NULL DEFAULT 0,
        device TEXT NOT NULL DEFAULT '',
        expires_at REAL NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_transfers_user ON transfers(user_id, expires_at);
    """,
    # 20: menü kısayolları (virgülle ayrılmış modül anahtarları; NULL = varsayılanlar)
    """
    ALTER TABLE users ADD COLUMN nav_pins TEXT;
    """,
    # 21: kişiler ve görüşme kayıtları (iletişim hatırlatıcı; doğum günü '--MM-DD' ise yıl bilinmiyor)
    """
    CREATE TABLE contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        relation TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        birthday TEXT,
        address TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        contact_every INTEGER,
        last_contact_at TEXT,
        nudged_for TEXT,
        snooze_until TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_contacts_user ON contacts(user_id);
    CREATE TABLE contact_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        date TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'call' CHECK (kind IN ('call', 'message', 'visit', 'other')),
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_contact_logs ON contact_logs(contact_id, date);
    """,
    # 22: siparişler, kargo takibi ve iade süresi (sent_flags: "tür:tarih" virgüllü, her hatırlatma bir kez)
    """
    CREATE TABLE orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        store TEXT NOT NULL DEFAULT '',
        item TEXT NOT NULL,
        amount REAL,
        ordered_on TEXT NOT NULL,
        expected_on TEXT,
        delivered_on TEXT,
        carrier TEXT NOT NULL DEFAULT '',
        tracking_no TEXT NOT NULL DEFAULT '',
        tracking_url TEXT NOT NULL DEFAULT '',
        order_url TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'ordered'
            CHECK (status IN ('ordered', 'shipped', 'delivered', 'returning', 'returned', 'cancelled')),
        return_days INTEGER NOT NULL DEFAULT 14,  -- 0 = iade takibi yok
        return_by TEXT,                           -- teslimde delivered_on + return_days
        expense_id INTEGER,                       -- harcamaya eklendiyse (FK yok: çöpten geri getirme bozulmasın)
        sent_flags TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_orders_user ON orders(user_id, status);
    """,
    # 23: zaman takibi (projeler ve kayıtlar; zamanlar UTC 'YYYY-MM-DD HH:MM:SS')
    """
    CREATE TABLE time_projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        color TEXT NOT NULL DEFAULT '',            -- hazır renk anahtarı (timetrack.COLORS)
        hourly_rate REAL,                          -- saatlik ücret (₺); boşsa kazanç hesaplanmaz
        archived INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE time_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        project_id INTEGER REFERENCES time_projects(id) ON DELETE CASCADE,   -- NULL = projesiz
        note TEXT NOT NULL DEFAULT '',
        started_at TEXT NOT NULL,
        ended_at TEXT,                             -- NULL = sayaç çalışıyor
        long_warned INTEGER NOT NULL DEFAULT 0,    -- "10 saattir çalışıyor" uyarısı gönderildi
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_time_entries_user ON time_entries(user_id, started_at);
    """,
    # 24: kanban panoları (sütunlar ve kartlar; en sağdaki sütun "bitti" sayılır)
    """
    CREATE TABLE boards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        shared INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE board_columns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        board_id INTEGER NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_board_columns_board ON board_columns(board_id, position);
    CREATE TABLE cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        board_id INTEGER NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
        column_id INTEGER NOT NULL REFERENCES board_columns(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        due_date TEXT,
        color TEXT NOT NULL DEFAULT '',
        position INTEGER NOT NULL DEFAULT 0,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_cards_column ON cards(column_id, position);
    CREATE INDEX idx_cards_board ON cards(board_id, due_date);
    """,
    # 25: herkese açık profil sayfası (/p/<adres>); fotoğraf veritabanında (sahipsiz dosya temizliği silmesin)
    """
    CREATE TABLE public_profiles (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        slug TEXT UNIQUE NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 0,
        display_name TEXT NOT NULL DEFAULT '',
        headline TEXT NOT NULL DEFAULT '',
        bio TEXT NOT NULL DEFAULT '',
        avatar_emoji TEXT NOT NULL DEFAULT '',
        photo BLOB,                                -- en fazla 400×400 JPEG, EXIF'siz
        accent TEXT NOT NULL DEFAULT '',           -- profile.ACCENTS anahtarı
        email TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL DEFAULT '',
        location TEXT NOT NULL DEFAULT '',
        links TEXT NOT NULL DEFAULT '[]',          -- JSON: [{"label": ..., "url": ...}], en fazla 12
        noindex INTEGER NOT NULL DEFAULT 1,        -- arama motorları dizine eklemesin
        views INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    # 26: harita — şehirler, yerler (şehre bağlı ya da bağımsız) ve park yeri (kullanıcı başına tek kayıt)
    """
    CREATE TABLE cities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        country TEXT NOT NULL DEFAULT '',
        lat REAL,
        lon REAL,
        status TEXT NOT NULL DEFAULT 'visited' CHECK (status IN ('visited', 'wish')),
        visited_on TEXT,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_cities_user ON cities(user_id);
    CREATE TABLE places (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        city_id INTEGER REFERENCES cities(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'other'
            CHECK (category IN ('food', 'cafe', 'sight', 'stay', 'shop', 'nature', 'other')),
        status TEXT NOT NULL DEFAULT 'wish' CHECK (status IN ('visited', 'wish')),
        rating INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
        lat REAL,
        lon REAL,
        address TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_places_user ON places(user_id, city_id);
    CREATE INDEX idx_places_city ON places(city_id);
    CREATE TABLE parking (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        lat REAL NOT NULL,
        lon REAL NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    # 27: tarama kutusu (Telegram'dan gelen sayfalar PDF yapılana kadar; dosyalar uploads/_tarama/ altında, 24 saat)
    """
    CREATE TABLE scan_inbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        filename TEXT NOT NULL DEFAULT '',
        stored_name TEXT NOT NULL,
        mime TEXT NOT NULL,
        size INTEGER NOT NULL,
        expires_at REAL NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_scan_inbox_user ON scan_inbox(user_id, id);
    """,
    # 28: kullanıcı başına depolama kotası ve dosya boyutu sınırı (NULL = sınır yok; upload_max_mb 0 = yükleme kapalı)
    """
    ALTER TABLE users ADD COLUMN quota_mb INTEGER;
    ALTER TABLE users ADD COLUMN upload_max_mb INTEGER;
    """,
    # 29: acil durum kartı (/acil/<anahtar>, girişsiz; kullanıcı başına tek kart; id çöp kutusu için)
    """
    CREATE TABLE emergency_cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
        token TEXT NOT NULL UNIQUE,                -- secrets.token_urlsafe; yenilenince eski bağlantı/QR açılmaz
        enabled INTEGER NOT NULL DEFAULT 0,
        full_name TEXT NOT NULL DEFAULT '',
        birth_date TEXT,                           -- 'YYYY-MM-DD'
        blood_type TEXT NOT NULL DEFAULT '',       -- emergency.BLOOD_TYPES anahtarı ('' = bilinmiyor)
        allergies TEXT NOT NULL DEFAULT '',
        conditions TEXT NOT NULL DEFAULT '',       -- kronik hastalıklar
        medications TEXT NOT NULL DEFAULT '',
        organ_donor TEXT NOT NULL DEFAULT '',      -- '' belirtilmedi | yes | no
        notes TEXT NOT NULL DEFAULT '',
        contacts TEXT NOT NULL DEFAULT '[]',       -- JSON: [{"name", "relation", "phone"}], en fazla 5
        views INTEGER NOT NULL DEFAULT 0,
        last_viewed_at TEXT,                       -- UTC 'YYYY-MM-DD HH:MM:SS'
        notified_at TEXT,                          -- son Telegram bildirimi (en fazla 30 dakikada bir)
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    # 30: kısa linkler (/k/<kod>; kod tüm kullanıcılar arasında tekil, küçük harf) ve Wi-Fi QR kartları
    """
    CREATE TABLE short_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        code TEXT NOT NULL UNIQUE,
        target TEXT NOT NULL,                      -- sadece http(s)://
        title TEXT NOT NULL DEFAULT '',
        clicks INTEGER NOT NULL DEFAULT 0,
        last_click_at TEXT,                        -- UTC
        expires_at TEXT,                           -- 'YYYY-MM-DD' (o gün dahil açılır); NULL = süresiz
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_short_links_user ON short_links(user_id, id);
    CREATE TABLE wifi_cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL DEFAULT '',
        ssid TEXT NOT NULL,
        password TEXT NOT NULL DEFAULT '',         -- düz metin (Şifreli Kasa değildir)
        security TEXT NOT NULL DEFAULT 'WPA' CHECK (security IN ('WPA', 'WEP', 'nopass')),
        hidden INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_wifi_cards_user ON wifi_cards(user_id);
    """,
    # 31: ev bakımı — periyodik işler ve yapılma geçmişi (reminded_for/on: son hatırlatmanın hangi tarih için
    #     ve hangi gün gönderildiği; sıradaki tarih değişince hatırlatmalar yeniden kurulur)
    """
    CREATE TABLE home_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        icon TEXT NOT NULL DEFAULT '🔧',
        category TEXT NOT NULL DEFAULT 'other',
        interval_n INTEGER NOT NULL DEFAULT 1,
        interval_unit TEXT NOT NULL DEFAULT 'month' CHECK (interval_unit IN ('day', 'week', 'month', 'year')),
        next_due TEXT NOT NULL,                    -- 'YYYY-MM-DD'
        remind_days INTEGER NOT NULL DEFAULT 3,    -- 0, 1, 3, 7, 14, 30 gün önce
        notes TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        reminded_for TEXT,
        reminded_on TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_home_tasks_user ON home_tasks(user_id, active, next_due);
    CREATE TABLE home_task_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL REFERENCES home_tasks(id) ON DELETE CASCADE,
        done_on TEXT NOT NULL,
        cost REAL,
        note TEXT NOT NULL DEFAULT '',
        expense_id INTEGER,                        -- harcamaya eklendiyse (FK yok: çöpten geri getirme bozulmasın)
        prev_due TEXT,                             -- kayıttan önceki sıradaki tarih ("geri al" buna döner)
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_home_task_logs ON home_task_logs(task_id, done_on);
    """,
    # 32: haftalık değerlendirme (hafta pazartesisiyle anılır) ve pazar hatırlatma saati (NULL = kapalı)
    """
    CREATE TABLE weekly_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        week_start TEXT NOT NULL,                  -- 'YYYY-MM-DD', haftanın pazartesisi (yerel)
        score INTEGER CHECK (score IS NULL OR score BETWEEN 1 AND 5),
        went_well TEXT NOT NULL DEFAULT '',
        hard TEXT NOT NULL DEFAULT '',
        learned TEXT NOT NULL DEFAULT '',
        priorities TEXT NOT NULL DEFAULT '[]',     -- gelecek haftanın öncelikleri, JSON: [{"text", "done"}], en fazla 3
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (user_id, week_start)
    );
    ALTER TABLE users ADD COLUMN weekly_prompt TEXT;
    """,
    # 33: randevu sayfası (/r/<adres>, girişsiz; kullanıcı başına tek sayfa). Saatler uygulamanın yerel saati
    # (APP_TZ) 'YYYY-MM-DD HH:MM'; aynı başlangıca ikinci aktif randevu veritabanında da engellenir
    """
    CREATE TABLE booking_pages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
        slug TEXT NOT NULL UNIQUE,
        enabled INTEGER NOT NULL DEFAULT 0,
        title TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        min_notice_hours INTEGER NOT NULL DEFAULT 2,  -- en az kaç saat önceden
        horizon_days INTEGER NOT NULL DEFAULT 30,     -- bugünden itibaren kaç gün
        slot_step INTEGER NOT NULL DEFAULT 30,        -- boş saatler kaç dakikada bir başlar
        needs_approval INTEGER NOT NULL DEFAULT 1,
        busy_from_calendar INTEGER NOT NULL DEFAULT 1, -- takvimdeki saatli kayıtlar dolu sayılır
        weekly_hours TEXT NOT NULL DEFAULT '{}',      -- JSON: {"0": [["09:00", "12:00"], ["13:00", "17:00"]]} 0 = Pazartesi
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE booking_blocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        start_date TEXT NOT NULL,                     -- 'YYYY-MM-DD', iki gün de dahil (tatil / izin)
        end_date TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_booking_blocks_user ON booking_blocks(user_id, end_date);
    CREATE TABLE booking_types (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        duration_min INTEGER NOT NULL DEFAULT 30,
        location_kind TEXT NOT NULL DEFAULT 'online'
            CHECK (location_kind IN ('in_person', 'phone', 'online', 'callback')),
        location_detail TEXT NOT NULL DEFAULT '',     -- adres / aranacak numara / görüşme linki
        buffer_min INTEGER NOT NULL DEFAULT 0,        -- öncesi ve sonrası boş kalacak dakika
        active INTEGER NOT NULL DEFAULT 1,
        sort INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_booking_types_user ON booking_types(user_id, sort);
    CREATE TABLE bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        type_id INTEGER REFERENCES booking_types(id) ON DELETE SET NULL,
        type_name TEXT NOT NULL DEFAULT '',           -- tür sonradan değişse/silinse de randevuda kalır
        location_kind TEXT NOT NULL DEFAULT '',
        location_detail TEXT NOT NULL DEFAULT '',
        buffer_min INTEGER NOT NULL DEFAULT 0,
        start_at TEXT NOT NULL,                       -- yerel 'YYYY-MM-DD HH:MM'
        end_at TEXT NOT NULL,
        name TEXT NOT NULL,
        contact TEXT NOT NULL,                        -- telefon ya da e-posta
        note TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'confirmed', 'rejected', 'cancelled')),
        cancelled_by TEXT,                            -- 'owner' | 'visitor'
        manage_token TEXT NOT NULL UNIQUE,            -- ziyaretçinin /r/i/<anahtar> bağlantısı
        event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,   -- onaylanınca takvime eklenen etkinlik
        ip_hash TEXT NOT NULL DEFAULT '',             -- SECRET_KEY ile HMAC; IP düz saklanmaz
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE UNIQUE INDEX idx_bookings_slot ON bookings(user_id, start_at) WHERE status IN ('pending', 'confirmed');
    CREATE INDEX idx_bookings_user ON bookings(user_id, start_at);
    CREATE INDEX idx_bookings_ip ON bookings(ip_hash, created_at);
    """,
    # 34: ödünç — verilen / alınan eşyalar. inventory_id ve contact_id için FK yok (home_task_logs.expense_id gibi):
    #     envanterden ya da Kişiler'den silinen kayıt ödünçte adıyla kalır, çöpten geri getirme hiçbir sırada bozulmaz
    """
    CREATE TABLE loans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        direction TEXT NOT NULL DEFAULT 'lent' CHECK (direction IN ('lent', 'borrowed')),  -- ben verdim / ben aldım
        item_name TEXT NOT NULL,
        inventory_id INTEGER,                      -- Ev Envanteri eşyası (NULL: serbest metin)
        person_name TEXT NOT NULL,
        contact_id INTEGER,                        -- Kişiler kaydı (NULL: serbest ad)
        phone TEXT NOT NULL DEFAULT '',
        given_on TEXT NOT NULL,                    -- 'YYYY-MM-DD', verildiği / alındığı gün
        due_on TEXT,                               -- beklenen dönüş (isteğe bağlı)
        remind_every_days INTEGER NOT NULL DEFAULT 14,  -- dönüş tarihi yoksa kaç günde bir hatırlatılır (0 = hiç)
        note TEXT NOT NULL DEFAULT '',
        returned_on TEXT,                          -- NULL = hâlâ dışarıda
        reminded_on TEXT,                          -- son Telegram hatırlatmasının günü
        snooze_until TEXT,                         -- "⏰ 1 hafta sonra": sıradaki hatırlatma bu gün
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_loans_user ON loans(user_id, returned_on);
    CREATE INDEX idx_loans_inventory ON loans(inventory_id);
    CREATE INDEX idx_loans_contact ON loans(contact_id);
    """,
    # 35: anket (/a/<kod>, girişsiz oy). Bir kişi bir oy: tarayıcı belirteci (çerez) ve oturum anahtarının hash'i,
    #     isteğe bağlı IP (HMAC; düz saklanmaz), isim ya da kişiye özel tek kullanımlık link. Saatler yerel, saat dilimsiz
    """
    CREATE TABLE polls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        code TEXT NOT NULL UNIQUE,                    -- karışmayan harflerden 7 karakter, küçük harf
        question TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL DEFAULT 'single' CHECK (kind IN ('single', 'multi', 'date')),
        max_choices INTEGER,                          -- çoklu seçimde en fazla kaç seçenek (NULL = sınırsız)
        require_name INTEGER NOT NULL DEFAULT 1,      -- tarih anketinde her zaman 1
        results_visibility TEXT NOT NULL DEFAULT 'after_vote'
            CHECK (results_visibility IN ('after_vote', 'always', 'owner')),
        one_per_ip INTEGER NOT NULL DEFAULT 0,        -- aynı internet bağlantısından tek oy
        invite_only INTEGER NOT NULL DEFAULT 0,       -- sadece kişiye özel linkler oy verir
        closes_at TEXT,                               -- yerel 'YYYY-MM-DD HH:MM'; NULL = süresiz
        closed INTEGER NOT NULL DEFAULT 0,            -- elle ya da bitişte cron kapattı
        notify INTEGER NOT NULL DEFAULT 0,            -- yeni oyları Telegram'dan bildir (en fazla 10 dakikada bir)
        notified_at TEXT,                             -- son toplu bildirim, yerel 'YYYY-MM-DD HH:MM:SS'
        notified_vote_id INTEGER NOT NULL DEFAULT 0,  -- bildirilen son oy (sonrakiler "yeni")
        views INTEGER NOT NULL DEFAULT 0,             -- herkese açık sayfa (sahibi ve link önizlemeleri sayılmaz)
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_polls_user ON polls(user_id, id);
    CREATE INDEX idx_polls_closing ON polls(closed, closes_at);
    CREATE TABLE poll_options (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
        text TEXT NOT NULL DEFAULT '',                -- tarih anketinde boş
        option_date TEXT,                             -- tarih anketinde 'YYYY-MM-DD'
        option_time TEXT,                             -- isteğe bağlı 'HH:MM'
        sort INTEGER NOT NULL DEFAULT 0,
        after_vote_id INTEGER NOT NULL DEFAULT 0      -- oy geldikten sonra eklendiyse o anki son oy (öncekilere sorulmadı)
    );
    CREATE INDEX idx_poll_options ON poll_options(poll_id, sort);
    CREATE TABLE poll_invites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
        token TEXT NOT NULL UNIQUE,                   -- /a/<kod>?d=<anahtar>, tek kullanımlık
        label TEXT NOT NULL DEFAULT '',               -- boşsa numarayla gösterilir
        used_at TEXT,                                 -- UTC; oy verilince
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_poll_invites ON poll_invites(poll_id, id);
    CREATE TABLE poll_votes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
        voter_name TEXT NOT NULL DEFAULT '',
        name_key TEXT NOT NULL DEFAULT '',            -- fold(isim): aynı isimle ikinci oy engellenir
        voter_hash TEXT NOT NULL DEFAULT '',          -- tarayıcı belirtecinin (çerez) SHA-256'sı
        session_hash TEXT NOT NULL DEFAULT '',        -- oturum anahtarının HMAC'i (çift tıklamada iki oy olmasın)
        ip_hash TEXT NOT NULL DEFAULT '',             -- SECRET_KEY ile HMAC; IP düz saklanmaz
        invite_id INTEGER REFERENCES poll_invites(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_poll_votes ON poll_votes(poll_id, id);
    CREATE INDEX idx_poll_votes_voter ON poll_votes(poll_id, voter_hash);
    CREATE INDEX idx_poll_votes_ip ON poll_votes(poll_id, ip_hash, created_at);
    CREATE TABLE poll_vote_choices (
        vote_id INTEGER NOT NULL REFERENCES poll_votes(id) ON DELETE CASCADE,
        option_id INTEGER NOT NULL REFERENCES poll_options(id) ON DELETE CASCADE,
        PRIMARY KEY (vote_id, option_id)
    );
    CREATE INDEX idx_poll_vote_choices_option ON poll_vote_choices(option_id);
    """,
    # 36: bilet cüzdanı (bilet dosyaları attachments'ta, entity 'ticket'). starts_on NULL: Telegram'dan gelen, tarihi
    #     henüz girilmemiş taslak. day_sent_for / eve_sent_for: etkinlik günü ve bir gün önceki akşam mesajının hangi
    #     'tarih saat' için gönderildiği; tarih ya da saat değişince mesajlar yeniden kurulur
    """
    CREATE TABLE tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind TEXT NOT NULL DEFAULT 'other'
            CHECK (kind IN ('concert', 'theatre', 'cinema', 'sport', 'flight', 'bus', 'train', 'museum', 'other')),
        title TEXT NOT NULL,
        starts_on TEXT,                            -- 'YYYY-MM-DD'
        starts_at TEXT,                            -- 'HH:MM' yerel; NULL = saat yok
        venue TEXT NOT NULL DEFAULT '',
        address TEXT NOT NULL DEFAULT '',
        seat TEXT NOT NULL DEFAULT '',             -- koltuk / blok / sıra / kapı (serbest metin)
        booking_code TEXT NOT NULL DEFAULT '',     -- PNR / rezervasyon no
        holder TEXT NOT NULL DEFAULT '',           -- kimin adına / kaç kişi
        price REAL,
        expense_id INTEGER,                        -- harcamaya eklendiyse (FK yok: çöpten geri getirme bozulmasın)
        barcode_text TEXT NOT NULL DEFAULT '',     -- biletteki QR/barkod içeriği (elle girilirse büyük QR gösterilir)
        note TEXT NOT NULL DEFAULT '',
        day_sent_for TEXT,
        eve_sent_for TEXT,
        archived INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_tickets_user ON tickets(user_id, starts_on);
    """,
    # 37: davetiye (/d/<kod>, girişsiz LCV). Tarih ve saatler yerel, saat dilimsiz. Kapak fotoğrafı veritabanında
    #     (profil gibi; çöp kutusunda base64). FK'lar çöpten geri getirme sırasına uyar: davetiye -> davetliler ->
    #     yanıtlar (guest_id) -> takvim etkinliği (event_id'de FK yok). rev: her yeni/değişen yanıtta artan sayaç
    #     (toplu Telegram bildirimi notified_rev'den sonrakileri yazar)
    """
    CREATE TABLE invites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        code TEXT NOT NULL UNIQUE,                    -- karışmayan harflerden 7 karakter, küçük harf
        title TEXT NOT NULL,
        host TEXT NOT NULL DEFAULT '',                -- ev sahibi adı
        starts_on TEXT NOT NULL,                      -- 'YYYY-MM-DD'
        starts_at TEXT NOT NULL,                      -- 'HH:MM'
        ends_at TEXT,                                 -- 'HH:MM'; başlangıçtan önceyse ertesi gün (NULL: 3 saat sayılır)
        place TEXT NOT NULL DEFAULT '',
        address TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        cover_emoji TEXT NOT NULL DEFAULT '🎉',
        accent TEXT NOT NULL DEFAULT 'pink',          -- profile.ACCENTS anahtarı
        photo BLOB,                                   -- kapak: en fazla 1200×630 JPEG, EXIF'siz
        rsvp_deadline TEXT,                           -- 'YYYY-MM-DD' (o gün dahil); NULL = etkinlik başlayana kadar
        allow_maybe INTEGER NOT NULL DEFAULT 1,
        ask_count INTEGER NOT NULL DEFAULT 1,         -- "Geliyorum"da kişi sayısı sorulur
        max_per_response INTEGER NOT NULL DEFAULT 6,
        capacity INTEGER,                             -- toplam "geliyor" kişi sınırı (NULL = sınırsız)
        question TEXT NOT NULL DEFAULT '',            -- isteğe bağlı özel soru
        show_guests TEXT NOT NULL DEFAULT 'counts' CHECK (show_guests IN ('none', 'counts', 'names')),
        invite_only INTEGER NOT NULL DEFAULT 0,       -- ortak link yanıt almaz, sadece kişiye özel linkler
        closed INTEGER NOT NULL DEFAULT 0,            -- LCV elle kapatıldı
        event_id INTEGER,                             -- takvimdeki özel etkinlik (FK yok: geri getirme sırası)
        notify INTEGER NOT NULL DEFAULT 0,            -- yeni/değişen yanıtları Telegram'dan bildir (10 dakikada bir)
        rev INTEGER NOT NULL DEFAULT 0,
        notified_rev INTEGER NOT NULL DEFAULT 0,
        notified_at TEXT,                             -- son toplu bildirim, yerel 'YYYY-MM-DD HH:MM:SS'
        deadline_sent_for TEXT,                       -- LCV son tarihi özeti hangi son tarih için gönderildi
        eve_sent_for TEXT,                            -- bir gün önceki akşam mesajı hangi etkinlik günü için gönderildi
        views INTEGER NOT NULL DEFAULT 0,             -- sahibi ve link önizlemeleri sayılmaz
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_invites_user ON invites(user_id, starts_on);
    CREATE INDEX idx_invites_date ON invites(starts_on);
    CREATE TABLE invite_guests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invite_id INTEGER NOT NULL REFERENCES invites(id) ON DELETE CASCADE,
        label TEXT NOT NULL,                          -- "Ahmet ve ailesi" (sahibine görünür; formda ad olarak gelir)
        phone TEXT NOT NULL DEFAULT '',               -- WhatsApp davet mesajı için
        token TEXT NOT NULL UNIQUE,                   -- /d/<kod>?k=<anahtar>; bu linkin yanıtı düzenlenebilir
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_invite_guests ON invite_guests(invite_id, id);
    CREATE TABLE invite_responses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invite_id INTEGER NOT NULL REFERENCES invites(id) ON DELETE CASCADE,
        guest_id INTEGER REFERENCES invite_guests(id) ON DELETE SET NULL,   -- kişiye özel linkle verildiyse
        name TEXT NOT NULL,
        name_key TEXT NOT NULL,                       -- fold(ad): aynı isimle ikinci yanıt engellenir
        status TEXT NOT NULL CHECK (status IN ('yes', 'no', 'maybe')),
        count INTEGER NOT NULL DEFAULT 0,             -- "geliyor" kişi sayısı (yanıtlayan dahil); diğerlerinde 0
        note TEXT NOT NULL DEFAULT '',
        answer TEXT NOT NULL DEFAULT '',              -- özel sorunun cevabı
        edit_token_hash TEXT NOT NULL DEFAULT '',     -- düzenleme belirtecinin (çerez / kişisel link) SHA-256'sı
        ip_hash TEXT NOT NULL DEFAULT '',             -- SECRET_KEY ile HMAC; IP düz saklanmaz
        rev INTEGER NOT NULL DEFAULT 0,               -- son değişikliğin sayaç değeri
        created_rev INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_invite_responses ON invite_responses(invite_id, id);
    CREATE UNIQUE INDEX idx_invite_responses_name ON invite_responses(invite_id, name_key);
    CREATE UNIQUE INDEX idx_invite_responses_guest ON invite_responses(guest_id) WHERE guest_id IS NOT NULL;
    CREATE INDEX idx_invite_responses_token ON invite_responses(invite_id, edit_token_hash);
    CREATE INDEX idx_invite_responses_ip ON invite_responses(invite_id, ip_hash, created_at);
    """,
]

SCHEMA_VERSION = len(MIGRATIONS)


def connect(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn):
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        # Her migration tek transaction: ya tamamen uygulanır ya hiç
        conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    return conn.execute("PRAGMA user_version").fetchone()[0]


def seed_admin(conn):
    """ADMIN_USERNAME / ADMIN_PASSWORD verilmişse ve kullanıcı yoksa admin oluşturur."""
    username = os.environ.get("ADMIN_USERNAME")
    password = os.environ.get("ADMIN_PASSWORD")
    if not (username and password):
        return
    if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        return
    conn.execute(
        "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 1)",
        (username, generate_password_hash(password)),
    )
    conn.commit()


def init_app(app):
    path = app.config["DATABASE"]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with closing(connect(path)) as conn:
        migrate(conn)
        seed_admin(conn)
    app.teardown_appcontext(close_db)


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def query(sql, args=()):
    return get_db().execute(sql, args).fetchall()


def query_one(sql, args=()):
    return get_db().execute(sql, args).fetchone()


def execute(sql, args=()):
    """Tek bir yazma sorgusu çalıştırır ve commit eder. cursor döner (lastrowid için)."""
    conn = get_db()
    cur = conn.execute(sql, args)
    conn.commit()
    return cur


def owned_or_404(table, row_id, user_id):
    """Kullanıcıya ait satırı döner; yoksa ya da başkasınınsa 404."""
    row = query_one(f"SELECT * FROM {table} WHERE id = ? AND user_id = ?", (row_id, user_id))
    if row is None:
        abort(404)
    return row
