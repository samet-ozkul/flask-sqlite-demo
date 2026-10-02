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
