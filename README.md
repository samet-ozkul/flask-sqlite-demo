# Kişisel Pano

Günlük hayatta lazım olan her şey tek bir yerde, telefondan ve bilgisayardan erişilebilir.
Flask + SQLite; **PythonAnywhere ücretsiz planında** (512 MB disk) çalışacak şekilde tasarlandı.

| Grup | Modüller |
|---|---|
| 🏠 Pano | Günün özeti: hava durumu, döviz kuru, yaklaşan ödemeler/tarihler, bugünkü alışkanlıklar ve ilaçlar, hızlı harcama ve hızlı not |
| 📋 Listeler ve notlar | **Notlar** (Markdown, etiket, sabitleme, ek dosya) · **Listeler** (alışveriş/yapılacaklar, kullanıcılar arası paylaşım, yapılacaklara saat ve Telegram hatırlatması) · **Sonra Bak** (link kaydetme, telefondan “Paylaş” ile) |
| 💰 Para | **Harcamalar** (aylık özet, kategori grafiği) · **Faturalar** (son ödeme, aylık tekrar) · **Abonelikler** (yenileme tarihi, aylık/yıllık toplam) · **Borç / Alacak** |
| 🚗 Ev ve araç | **Araç** (muayene, sigorta, kasko, bakım, yakıt tüketimi) · **Garanti** (fatura fotoğrafı, bitiş tarihi) · **Ev Envanteri** (“matkap nerede?”) |
| 🧘 Kişisel | **Alışkanlıklar** (seri, takvim) · **Sağlık** (kilo, tansiyon, şeker, nabız, ilaçlar, randevular) · **Tarifler** (malzemeleri alışveriş listesine ekle) |
| ⚙️ Altyapı | Kullanıcı yönetimi, tek tıkla yedek al/geri yükle, disk kullanımı, Telegram günlük özeti, telefona uygulama olarak yükleme (PWA) |

## 512 MB disk nasıl korunuyor?

- Fotoğraflar en fazla 1600 px'e küçültülüp JPEG olarak kaydedilir (tipik fatura fotoğrafı 3-5 MB → ~250 KB), konum (EXIF/GPS) bilgisi silinir. Listelerde ~30 KB'lık küçük önizleme gösterilir.
- PDF en fazla 3 MB. Veritabanı + dosyalar toplamı `STORAGE_QUOTA_MB`'yi (varsayılan 350) aşınca yükleme reddedilir.
- Yedekler sunucuda **saklanmaz**: indirilir ya da Telegram'a gönderilir.
- Yönetim → **Temizlik**: sahipsiz dosyaları siler, veritabanını sıkıştırır (VACUUM).
- Yönetim sayfası tüm ev klasörünü ölçebilir; pip önbelleği şişerse `rm -rf ~/.cache/pip`.

## Güvenlik

- Herkese açık kayıt **kapalı**; hesapları yönetici oluşturur (`ALLOW_REGISTRATION=1` ile açılabilir).
- Şifreler hash'li; tüm formlarda CSRF koruması; giriş sonrası açık yönlendirme engeli.
- Aynı IP'den 10 hatalı denemede o kullanıcı için, 30 denemede IP için 15 dk kilit.
- Her kayıt kullanıcıya ait; dosyalar sadece sahibine sunulur.
- Şifre, kart bilgisi, kimlik fotoğrafı gibi hassas verileri burada saklamayın; bunun için şifre yöneticisi kullanın.

## Lokal çalıştırma

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set ADMIN_USERNAME=admin
set ADMIN_PASSWORD=admin12345
python app.py
```

http://localhost:5000

Testler: `python tests/test_core.py` (ve `tests/` altındaki diğer `test_*.py` dosyaları).

## PythonAnywhere'e kurulum

`KULLANICI` = PythonAnywhere kullanıcı adın.

### İlk kurulum

1. **Consoles → Bash**:
   ```bash
   git clone https://github.com/samet-ozkul/flask-sqlite-demo.git
   mkvirtualenv flask-demo --python=python3.12
   pip install --no-cache-dir -r ~/flask-sqlite-demo/requirements.txt
   python -c "import secrets; print(secrets.token_hex(32))"   # SECRET_KEY
   python -c "import secrets; print(secrets.token_urlsafe(16))"   # CRON_SECRET
   ```
2. **Web → Add a new web app → Manual configuration → Python 3.12**
3. Web sekmesinde:
   - **Source code** ve **Working directory:** `/home/KULLANICI/flask-sqlite-demo`
   - **Virtualenv:** `flask-demo`
   - **Security → Force HTTPS:** Enabled
   - **Static files:** URL `/static/` → Directory `/home/KULLANICI/flask-sqlite-demo/pano/static` (CSS/ikonlar Flask'a uğramadan sunulur)
4. **WSGI configuration file** içeriğini tamamen şununla değiştir:
   ```python
   import os, sys

   path = "/home/KULLANICI/flask-sqlite-demo"
   if path not in sys.path:
       sys.path.insert(0, path)

   os.environ["SECRET_KEY"] = "BURAYA_URETTIGIN_KEY"
   os.environ["ADMIN_USERNAME"] = "admin"
   os.environ["ADMIN_PASSWORD"] = "BURAYA_ADMIN_SIFRESI"
   os.environ["SESSION_COOKIE_SECURE"] = "1"
   # İsteğe bağlı (aşağıdaki bölümlere bak):
   # os.environ["TELEGRAM_BOT_TOKEN"] = "123456:ABC..."
   # os.environ["CRON_SECRET"] = "BURAYA_URETTIGIN_CRON_SECRET"
   # os.environ["GOLDAPI_KEY"] = "goldapi.io anahtarı (gram altın için)"

   from app import app as application
   ```
5. **Reload** → `https://KULLANICI.pythonanywhere.com`

### Demo sürümünden güncelleme

Önceki demo kurulumdaki kullanıcılar ve notlar korunur; veritabanı açılışta otomatik yeni şemaya geçer.

```bash
cd ~/flask-sqlite-demo && git fetch && git checkout kisisel-pano && git pull
workon flask-demo
pip install --no-cache-dir -r requirements.txt   # Pillow ve tzdata eklendi
```
Sonra 3. adımdaki **Static files** ayarını ekle, WSGI dosyasına yeni değişkenleri ekle ve **Reload**.

### Sonraki güncellemeler

`git pull` → gerekirse `pip install -r requirements.txt` → **Reload**. Veritabanı ve dosyalar git'te olmadığı için `git pull` verilere dokunmaz.

**Ayda bir:** Web sekmesindeki **Run until 1 month from today** butonuna bas (ücretsiz plan). Uygulama durdurulsa bile veriler silinmez.

## Ortam değişkenleri

| Değişken | Gerekli | Açıklama |
|---|---|---|
| `SECRET_KEY` | ✅ | Oturum imzalama anahtarı (uzun, rastgele) |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | ilk kurulumda | Yoksa açılışta bu yönetici oluşturulur |
| `SESSION_COOKIE_SECURE` | HTTPS'te `1` | Cookie sadece HTTPS'te gönderilir (Render'da otomatik) |
| `TELEGRAM_BOT_TOKEN` | – | Telegram bildirimleri için bot anahtarı |
| `CRON_SECRET` | – | `/cron/<CRON_SECRET>/...` adreslerini açar |
| `GOLDAPI_KEY` | – | Panoda gram altın fiyatı (goldapi.io ücretsiz plan, 8 saatte bir sorgulanır) |
| `STORAGE_QUOTA_MB` | – | Veritabanı + dosyalar için üst sınır (varsayılan 350) |
| `ALLOW_REGISTRATION` | – | `1` ise herkes kayıt olabilir (varsayılan kapalı) |
| `APP_TZ` | – | Saat dilimi (varsayılan `Europe/Istanbul`) |
| `REMINDER_DEFAULT_TIME` | – | Saati girilmemiş yapılacaklar için hatırlatma saati (varsayılan `09:00`) |
| `DATABASE_PATH`, `UPLOAD_DIR` | – | Varsayılan: proje klasöründe `app.db` ve `uploads/` |

## Telegram bildirimleri (isteğe bağlı)

1. Telegram'da **@BotFather** → `/newbot` → bir ad ver → verdiği **token**'ı WSGI dosyasına `TELEGRAM_BOT_TOKEN` olarak ekle, **Reload**.
2. Panoda **Ayarlar → Telegram'ı bağla** → açılan linkten botta **Başlat**'a bas → **Bağlantıyı doğrula**.
3. Günlük özet için bir sonraki bölümdeki cron görevini kur.

## Zamanlanmış görevler (cron-job.org, ücretsiz)

PythonAnywhere ücretsiz planında zamanlanmış görev yok. Bunun yerine [cron-job.org](https://cron-job.org) gibi ücretsiz bir servis, sitendeki gizli bir adresi belirli saatlerde çağırır:

| Görev | Adres | Önerilen zaman |
|---|---|---|
| Günlük özet (hava, yaklaşan ödemeler, randevular, ilaçlar) | `https://KULLANICI.pythonanywhere.com/cron/CRON_SECRET/gunluk` | Her gün 08:00 (`0 8 * * *`) |
| Yapılacaklar hatırlatmaları | `https://KULLANICI.pythonanywhere.com/cron/CRON_SECRET/hatirlatma` | 5 dakikada bir (`*/5 * * * *`) |
| Veritabanı yedeğini yöneticilere Telegram'dan gönder | `https://KULLANICI.pythonanywhere.com/cron/CRON_SECRET/yedek` | Haftada bir, Pazar 03:00 (`0 3 * * 0`) |

Günlük özet aynı gün ikinci kez çağrılsa da tekrar gönderilmez. `CRON_SECRET`'ı kimseyle paylaşma.
cron-job.org'da saat dilimini **Europe/Istanbul** yapmayı unutma.

**Yapılacaklar hatırlatmaları:** Maddeye tarih, isteğe bağlı saat ve hatırlatma zamanı (zamanı gelince / 15 dk / 1 saat / 3 saat / 1 gün önce) verilir. “… önce” seçilirse iki mesaj gelir: süre yaklaşınca ⏰ ve zamanı gelince 🔔. Mesaj maddeyi ekleyen kişiye (Telegram'ı bağlı değilse liste sahibine) gider. Hatırlatmalar cron çağrısı sıklığına göre en fazla 5 dakika gecikir; cron 6 saatten uzun süre çalışmazsa kaçan eski hatırlatmalar toplu gönderilmez. Tarih/saat/hatırlatma değiştirilince mesajlar yeniden gönderilebilir.

## Yedekleme

- **Yönetim → Yedek → Tam yedek**: veritabanı + tüm dosyalar tek bir zip. Haftada bir indirmen önerilir.
- **Sadece veritabanı**: küçük, hızlı. Telegram otomatik yedeği de bu türdendir.
- **Geri yükle**: zip'i seç, `EVET` yaz. Tüm veriler yedektekiyle değişir; eski sürüm yedekleri otomatik güncel şemaya taşınır.

## Telefona uygulama olarak ekleme

- **Android (Chrome):** ⋮ menüsü → *Uygulamayı yükle*. Sonra başka uygulamalardan **Paylaş → Pano** ile link kaydedebilirsin.
- **iPhone (Safari):** Paylaş → *Ana Ekrana Ekle*.

## Render (alternatif)

`render.yaml` Blueprint için hazır, ama Render ücretsiz planında disk kalıcı değil: servis her uyuduğunda veritabanı ve dosyalar silinir. Sadece deneme için uygundur.
