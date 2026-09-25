# Flask + SQLite Demo (Render free tier)

Kayıt / giriş / çıkış, kullanıcıya özel notlar ve basit bir admin sayfası içeren küçük test projesi.

- **Backend:** Flask + gunicorn
- **DB:** SQLite (Python'un yerleşik `sqlite3` modülü, ek bağımlılık yok)
- **Güvenlik:** werkzeug ile şifre hash'leme, oturum çerezi, CSRF token

## Lokal çalıştırma

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set ADMIN_USERNAME=admin
set ADMIN_PASSWORD=admin123
python app.py
```

http://localhost:5000

## Render'a deploy

1. Projeyi bir GitHub reposuna push'la.
2. Render → **New → Blueprint** → repoyu seç (`render.yaml` otomatik okunur).
3. `ADMIN_PASSWORD` değerini sorduğunda gir. `SECRET_KEY` otomatik üretilir.

Blueprint yerine elle **Web Service** oluşturursan:
- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app --workers 1 --bind 0.0.0.0:$PORT`
- Env: `PYTHON_VERSION=3.12.10`, `SECRET_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`

## ⚠️ Free tier notu

Free tier'da disk **kalıcı değil**: servis her deploy'da veya ~15 dk hareketsizlikten sonra uyuyup yeniden başladığında `app.db` sıfırlanır. Admin kullanıcısı env değişkenlerinden her açılışta yeniden oluşturulur, ama kayıtlı kullanıcılar ve notlar kaybolur. Test için yeterli; kalıcılık gerekirse Render Postgres veya ücretli persistent disk (`DATABASE_PATH=/var/data/app.db`) kullanılmalı.

## Rotalar

| Rota | Açıklama |
|---|---|
| `/register`, `/login`, `/logout` | Kimlik doğrulama |
| `/dashboard` | Not ekle / sil |
| `/admin` | Kullanıcı listesi (sadece admin) |
| `/healthz` | Render health check |
