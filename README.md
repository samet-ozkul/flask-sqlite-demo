# Flask + SQLite Demo (Render / PythonAnywhere free tier)

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

## PythonAnywhere'e deploy (veriler kalıcı, ücretsiz)

`KULLANICI` = PythonAnywhere kullanıcı adın.

1. **Consoles → Bash** aç:
   ```bash
   git clone https://github.com/samet-ozkul/flask-sqlite-demo.git
   mkvirtualenv flask-demo --python=python3.12
   pip install -r flask-sqlite-demo/requirements.txt
   python -c "import secrets; print(secrets.token_hex(32))"   # SECRET_KEY için kopyala
   ```
2. **Web → Add a new web app → Manual configuration → Python 3.12**
3. Web sekmesinde:
   - **Source code:** `/home/KULLANICI/flask-sqlite-demo`
   - **Virtualenv:** `flask-demo`
   - **Security → Force HTTPS:** Enabled
4. **WSGI configuration file** linkine tıkla, içeriği tamamen şununla değiştir:
   ```python
   import os, sys

   path = "/home/KULLANICI/flask-sqlite-demo"
   if path not in sys.path:
       sys.path.insert(0, path)

   os.environ["SECRET_KEY"] = "BURAYA_URETTIGIN_KEY"
   os.environ["ADMIN_USERNAME"] = "admin"
   os.environ["ADMIN_PASSWORD"] = "BURAYA_ADMIN_SIFRESI"
   os.environ["SESSION_COOKIE_SECURE"] = "1"

   from app import app as application
   ```
5. **Reload** → `https://KULLANICI.pythonanywhere.com`

**Güncelleme:** lokalde `git push` → PythonAnywhere Bash'te `cd ~/flask-sqlite-demo && git pull` → Web sekmesinde **Reload**. `app.db` git'te olmadığı için `git pull` verilere dokunmaz.

**Ayda bir:** Free planda web app 1 ay sonra durur. Web sekmesindeki süre uzatma butonuna (**Run until 1 month from today**) basman yeterli; dosyalar ve veriler silinmez.

## Rotalar

| Rota | Açıklama |
|---|---|
| `/register`, `/login`, `/logout` | Kimlik doğrulama |
| `/dashboard` | Not ekle / sil |
| `/admin` | Kullanıcı listesi (sadece admin) |
| `/healthz` | Render health check |
