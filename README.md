# Kişisel Pano

Günlük hayatta lazım olan her şey tek bir yerde, telefondan ve bilgisayardan erişilebilir.
Flask + SQLite; **PythonAnywhere ücretsiz planında** (512 MB disk) çalışacak şekilde tasarlandı.

| Grup | Modüller |
|---|---|
| 📅 Genel | **Takvim** (tüm tarihler tek takvimde, telefon takvimine abonelik) · **Etkinlikler** (aileyle paylaşılan ortak takvim, Telegram hatırlatması) · **Otomasyon** (“eğer şu olursa bunu yap” kuralları) · **Arama** (tek kutudan tüm modüller, bottan `/ara`) · **Aktar** (telefon ile bilgisayar arasında süreli metin ve dosya) |
| 🏠 Pano | Günün özeti: hava durumu, döviz kuru, yaklaşan ödemeler/tarihler, bugünkü alışkanlıklar ve ilaçlar, hızlı harcama ve hızlı not |
| 📋 Listeler ve notlar | **Notlar** (Markdown, etiket, sabitleme, ek dosya) · **Listeler** (alışveriş/yapılacaklar, kullanıcılar arası paylaşım, paylaşılan listede işi birine atama, yapılacaklara saat ve Telegram hatırlatması) · **Sonra Bak** (link kaydetme, telefondan “Paylaş” ile) · **Şifreli Kasa** (hassas notlar tarayıcıda şifrelenir, sunucu okuyamaz) |
| 💰 Para | **Harcamalar** (aylık özet, kategori grafiği, bütçe limiti, Excel/CSV) · **Faturalar** (son ödeme, aylık tekrar) · **Abonelikler** (yenileme tarihi, aylık/yıllık toplam) · **Borç / Alacak** · **Kurlar** (30 günlük grafik, kur alarmı) · **Varlıklar** (nakit, döviz, altın; güncel TL değeri ve 90 günlük grafik) · **Hedefler** (birikim hedefi, ayda ne kadar, tahmini bitiş) · **Ortak Harcama** (Splitwise benzeri: kim ne ödedi, kim kime borçlu) · **Siparişler** (kargo takibi, beklenen teslim ve iade son günü hatırlatması) |
| 🚗 Ev ve araç | **Araç** (muayene, sigorta, kasko, bakım, yakıt tüketimi) · **Garanti** (fatura fotoğrafı, bitiş tarihi) · **Ev Envanteri** (“matkap nerede?”) · **Belgeler** (pasaport, ehliyet, kimlik, ruhsat, poliçe bitiş tarihleri; 1 hafta–6 ay önceden Telegram hatırlatması) · **Ev Bakımı** (kombi, klima, filtre, dedektör pili gibi periyodik işler; hazır şablonlar, yapılma geçmişi ve masraf, Telegram hatırlatması) |
| 🧘 Kişisel | **Alışkanlıklar** (seri, takvim) · **Günlük** (her gün bir satır ve ruh hali emojisi, ay takvimi, “1 yıl önce bugün”, akşam Telegram'dan “Bugün nasıldı?”) · **Sağlık** (kilo, tansiyon, şeker, nabız, ilaçlar, randevular) · **Tarifler** (malzemeleri alışveriş listesine ekle) · **Önemli Günler** (doğum günü, yıldönümü; yaş/yıl hesabı) · **İzleme / Okuma** (film, dizi, kitap listesi; kapaklı arama, puan) · **Harita** (gezdiğim / gitmek istediğim şehirler, şehir notları, yemek-gezilecek yerler, yol tarifi, “🚗 Arabam nerede?”) · **Kişiler** (iletişim hatırlatıcı: “dedeni 3 haftadır aramadın”, tek dokunuşla 📞 Aradım, arama/WhatsApp bağlantısı, doğum günleri) |
| ⚙️ Altyapı | Kullanıcı yönetimi, tek tıkla yedek al/geri yükle, disk kullanımı, Telegram günlük özeti, telefona uygulama olarak yükleme (PWA) · **Çöp kutusu** (silinen kayıt 30 gün saklanır, tek tıkla geri gelir) · **Tema** (otomatik / açık / koyu) · **Pano düzeni** (kartları seç ve sırala) |
| 🧰 Araçlar | **Zaman Takibi** (başlat/durdur sayacı, elle kayıt, proje bazında haftalık/aylık rapor, saatlik ücretle kazanç, Excel/CSV; bottan `/baslat`, `/durdur`, `/zaman`) · **Hesaplayıcılar** (kredi taksiti ve ödeme planı, mevduat getirisi, KDV, yüzde, tarih/iş günü/yaş, birim çevirici, döviz/altın, hesap bölüşme, yakıt maliyeti) · **Kanban** (sütunlu iş/proje panoları, sürükle-bırak, renk etiketi, son tarih, paylaşılan pano) · **Profil Sayfası** (Linktree / dijital kartvizit benzeri herkese açık sayfa: linkler, ara/WhatsApp/e-posta butonları, QR kod, rehbere ekle) · **Belge Tara** (telefonla çekilen sayfalardan tek PDF: sıralama, döndürme, belge modu; indir, nota ekle, Aktar'a koy, Telegram'a gönder) · **Acil Durum Kartı** (iPhone “Tıbbi Kimlik” benzeri: kan grubu, alerjiler, ilaçlar, acil kişiler; QR'la girişsiz açılan iki dilli sayfa, kilit ekranı duvar kâğıdı, yazdırılabilir cüzdan kartı, açılınca Telegram bildirimi) · **Yıl Özeti** (Spotify Wrapped benzeri: yılın harcamaları, alışkanlık serileri, bitirilen kitap/filmler, gezilen şehirler, çalışma saatleri ve etkinlik ısı haritası; Telegram'a gönder) · **Kısa Link & QR** (kendi adresinle `/k/kod` kısa link: tıklanma sayısı, son kullanma tarihi, aç/kapa, QR/PNG; okutunca bağlanan yazdırılabilir Wi-Fi misafir kartı; serbest QR; bottan `/kisalt`) |

**Menü:** Bilgisayarda üst çubukta en çok kullandığın modüller (☆ ile seçilir, Ayarlar → 📌 Menü kısayolları'ndan sıralanır) ve **☰ Modüller** açılır menüsü (gruplu, aramalı) durur; telefonda alt çubukta ilk 3 kısayol. **Ctrl+K** ya da **/** ile hızlı geçiş: modül adını yaz, Enter (eşleşme yoksa her yerde arar).

**Hesaplayıcılar** tamamen tarayıcıda çalışır, yazdıkça sonuç güncellenir; sunucuya veri gitmez ve veritabanına bir şey yazılmaz (son girilen değerler sadece o cihazda hatırlanır). Kredide “Vergi ekle” işaretlenirse KKDF %15 + BSMV %15 faize eklenir (ihtiyaç/taşıt kredisi; konut kredisinde yok). Döviz/altın hesabı Kurlar modülündeki güncel kuru kullanır; kur alınamazsa o kart “kur alınamadı” der. İş günü hesabında resmi tatiller düşülmez.

**Kanban:** Pano hazır sütun şablonuyla açılır (Yapılacak / Yapılıyor / Bitti, Fikir / Planlandı / Yapılıyor / Bitti ya da Bekleyenler / Bu hafta / Bugün / Bitti); sütunlar eklenip yeniden adlandırılır, sola/sağa taşınır. Kartlar bilgisayarda sürükle-bırak, telefonda basılı tutup sürükleyerek ya da kartın ⋯ menüsündeki ← → ↑ ↓ ile taşınır. En sağdaki sütun “bitti” sayılır: son tarihi olup orada olmayan kartlar yaklaşanlarda, takvimde ve günlük özette görünür. Silinen sütunun kartları soldaki sütuna geçer. Paylaşılan panoyu herkes düzenler; adını, paylaşımını değiştirmek ve silmek sahibine açıktır.

**Yıl Özeti:** Seçilen yılın tüm modüllerden derlenmiş özeti: en dikkat çekici 4-6 sayı, harcamalar (aylık ortalama, en çok harcanan kategoriler ve ay, geçen yıla göre değişim; devam eden yılda geçen yılın aynı dönemiyle), alışkanlıkların en uzun serisi, günlükte ruh hali dağılımı, bitirilen kitap/film/diziler, gezilen şehirler, zaman takibi, araç, kilo değişimi, hedefler ve GitHub benzeri etkinlik ısı haritası. Sadece kişinin kendi kayıtları sayılır (paylaşılan liste ve panolarda başkasının eklediği maddeler hariç). Ocak'ta varsayılan olarak geçen yıl açılır; 1 Ocak'ta günlük cron Telegram'ı bağlı herkese geçen yılın kısa özetini bir kez gönderir, panoda 15 Aralık – 31 Ocak arası bağlantı görünür.

## 512 MB disk nasıl korunuyor?

- Fotoğraflar en fazla 1600 px'e küçültülüp JPEG olarak kaydedilir (tipik fatura fotoğrafı 3-5 MB → ~250 KB), konum (EXIF/GPS) bilgisi silinir. Listelerde ~30 KB'lık küçük önizleme gösterilir.
- PDF en fazla 3 MB. Veritabanı + dosyalar toplamı `STORAGE_QUOTA_MB`'yi (varsayılan 350) aşınca yükleme reddedilir.
- Yedekler sunucuda **saklanmaz**: indirilir ya da Telegram'a gönderilir.
- **Aktar** dosyaları süreli durur (10 dk – 24 saat, varsayılan 1 saat), 5 dakikalık cron'da silinir, yedeğe girmez; dosya başına 25 MB, kişi başına toplam 100 MB.
- Silinen kayıtların ek dosyaları çöp kutusunda 30 gün durur, sonra günlük cron ile kalıcı silinir; acele varsa **Çöp kutusu → Boşalt**.
- **Kullanıcı başına sınır:** Yönetim → Kullanıcılar'da her kullanıcı için *depolama kotası* (dosyalarının toplam alanı: ekler, çöp kutusundaki ekler, Aktar, tarama kutusu, profil fotoğrafı) ve *dosya başına en fazla* boyut (0 = dosya yükleyemez) konur; boş = sınır yok. Kullanım çubuğu yönetim sayfasında, kişinin kendi kullanımı Ayarlar → 💾 Depolama'da görünür. Sınır dolunca yeni dosya reddedilir, mevcutlar silinmez.
- Yönetim → **Temizlik**: sahipsiz dosyaları siler (çöp kutusundakilere dokunmaz), veritabanını sıkıştırır (VACUUM).
- Yönetim sayfası tüm ev klasörünü ölçebilir; pip önbelleği şişerse `rm -rf ~/.cache/pip`.

## Güvenlik

- Herkese açık kayıt **kapalı**; hesapları yönetici oluşturur (`ALLOW_REGISTRATION=1` ile açılabilir).
- Şifreler hash'li; tüm formlarda CSRF koruması; giriş sonrası açık yönlendirme engeli.
- Aynı IP'den 10 hatalı denemede o kullanıcı için, 30 denemede IP için 15 dk kilit.
- **İki adımlı giriş (önerilir):** Ayarlar → 🔐 İki adımlı giriş → QR kodu Google Authenticator / Microsoft Authenticator ile okut. Girişte şifreden sonra 6 haneli kod istenir; aynı kod ikinci kez kullanılamaz, 5 hatalı kodda 15 dk kilit. Telefon kaybolursa diye 8 tek kullanımlık **yedek kod** verilir (Ayarlar'dan şifreyle yenilenir). Hem telefonunu hem yedek kodlarını kaybeden kullanıcının 2FA'sını yönetici Kullanıcılar sayfasından kapatabilir.
- **Diğer cihazlardan çıkış:** Ayarlar → 📱 Oturumlar → *Diğer cihazlardan çıkış yap* telefonda, iş bilgisayarında açık kalan (“Beni hatırla” ile 30 günlük olanlar dahil) bütün oturumları kapatır; bu cihaz açık kalır. Şifre değişince, yönetici şifreyi sıfırlayınca ve iki adımlı giriş açılınca bu kendiliğinden yapılır.
- **Şifremi unuttum:** Giriş sayfasındaki bağlantıdan kullanıcı adı girilir; hesap Telegram'a bağlıysa bota 15 dakika geçerli, tek kullanımlık bir sıfırlama bağlantısı gelir (saatte en fazla 3 istek). Şifre değişince diğer oturumlar kapanır ve Telegram'dan haber verilir; iki adımlı giriş açıksa girişte kod yine istenir. Telegram'ı bağlı olmayan kullanıcının şifresini yönetici **Yönetim → Kullanıcılar**'dan sıfırlar; yöneticinin kendisi için konsoldan:
  ```bash
  cd ~/flask-sqlite-demo && workon flask-demo && python -c "import sqlite3, getpass; from werkzeug.security import generate_password_hash as h; u = input('Kullanıcı adı: '); p = getpass.getpass('Yeni şifre: '); db = sqlite3.connect('app.db'); n = db.execute('UPDATE users SET password_hash = ? WHERE username = ?', (h(p), u)).rowcount; db.execute('DELETE FROM login_attempts'); db.commit(); print('Değişti' if n else 'Kullanıcı yok')"
  ```
- Her kayıt kullanıcıya ait; dosyalar sadece sahibine sunulur.
- **Şifreli Kasa:** Abone numarası, poliçe no, Wi-Fi şifresi gibi notlar kasa parolasıyla **tarayıcıda** şifrelenir (AES-256-GCM, anahtar PBKDF2-SHA256 600.000 turla türetilir). Kasa parolası sunucuya hiç gitmez; sunucuda ve yedeklerde sadece şifreli veri durur. Parola unutulursa kayıtlar kurtarılamaz (kasa hesap şifresiyle sıfırlanabilir). 5 dakika işlem yapılmazsa kilitlenir; aramada, yapay zekâda ve Telegram'da yer almaz.
- Kart bilgisi ve kimlik fotoğrafı gibi verileri yine de burada saklamayın; banka ve önemli hesap şifreleri için gerçek bir şifre yöneticisi kullanın.

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
   # os.environ["TMDB_API_KEY"] = "themoviedb.org anahtarı (film/dizi araması için)"

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
| `TMDB_API_KEY` | – | İzleme listesinde film/dizi araması (themoviedb.org ücretsiz; v3 anahtarı ya da v4 okuma belirteci). Kitap araması anahtarsız çalışır |
| `TRANSFER_MAX_MB`, `TRANSFER_TOTAL_MB` | – | Aktar: dosya başına üst sınır (varsayılan 25) ve kişi başına aynı anda toplam (varsayılan 100) |
| `DEFAULT_QUOTA_MB`, `DEFAULT_UPLOAD_MAX_MB` | – | Yeni (yönetici olmayan) kullanıcıların başlangıç depolama kotası ve dosya başına sınırı (MB); boşsa sınırsız. Yönetici sonradan kullanıcı başına değiştirir |
| `STORAGE_QUOTA_MB` | – | Veritabanı + dosyalar için üst sınır (varsayılan 350) |
| `ALLOW_REGISTRATION` | – | `1` ise herkes kayıt olabilir (varsayılan kapalı) |
| `APP_TZ` | – | Saat dilimi (varsayılan `Europe/Istanbul`) |
| `AI_PROVIDER` | – | Yapay zekâ sağlayıcısı: `anthropic`, `mistral`, `gemini`, `openai`, `openrouter`, `groq`, `deepseek`, `openai-compatible` |
| `AI_API_KEY` | – | Sağlayıcının API anahtarı |
| `AI_MODEL` | – | Model (verilmezse `anthropic` → `claude-opus-5`, `mistral` → `mistral-medium-latest`, `gemini` → `gemini-flash-latest`; diğerlerinde gerekli) |
| `AI_BASE_URL` | – | Sadece `openai-compatible` için (ör. yerel Ollama `http://localhost:11434/v1`) |
| `AI_EFFORT` | – | Claude'da düşünme derinliği `low` / `medium` / `high` (varsayılan `medium`) |
| `REMINDER_DEFAULT_TIME` | – | Saati girilmemiş yapılacaklar için hatırlatma saati (varsayılan `09:00`) |
| `DATABASE_PATH`, `UPLOAD_DIR` | – | Varsayılan: proje klasöründe `app.db` ve `uploads/` |

## Telegram bildirimleri (isteğe bağlı)

1. Telegram'da **@BotFather** → `/newbot` → bir ad ver → verdiği **token**'ı WSGI dosyasına `TELEGRAM_BOT_TOKEN` olarak ekle, **Reload**.
2. Panoda **Ayarlar → Telegram'ı bağla** → açılan linkten botta **Başlat**'a bas → **Bağlantıyı doğrula**.
3. Günlük özet ve hatırlatmalar için bir sonraki bölümdeki cron görevlerini kur.
4. **Mesaj butonları (isteğe bağlı):** Yönetim → Durum → **Webhook'u kur**. Bundan sonra hatırlatma mesajlarında **✅ Tamamlandı** butonu çıkar; basınca iş panoda tamamlanır, buton **↩️ Geri al**'a döner. Webhook açıkken hesap bağlama da botta **Başlat**'a basınca anında olur. HTTPS gerektirir (PythonAnywhere'de Force HTTPS açık olmalı); `SECRET_KEY` değişirse webhook'u yeniden kur.

### Bot komutları

Webhook kuruluyken bota yazarak panoyu açmadan giriş yapabilirsin (`/yardim` hepsini listeler):

| Yaz | Ne olur |
|---|---|
| `/harcama 250 market öğle` | Bugüne harcama ekler; kategori ilk kelimeden anlaşılır. Mesajdaki **↩️ Geri al** ile silinir |
| `/harcama` | Bu ayın toplamı ve kategoriler |
| `/not metin` | Not kaydeder |
| `/ekle süt, ekmek` · `/ekle market: süt` | Alışveriş listesine ekler (liste adı verilebilir) |
| `/yap fatura öde yarın 14:00` | Yapılacak ekler; sondaki *bugün, yarın, cuma, 25.12, 14:00, saat 9.30* anlaşılır ve zamanı gelince hatırlatılır |
| `/yap ... @ayse` | İşi birine atar (paylaşılan listeye); ona Telegram'dan haber gider |
| `/etkinlik piknik pazar 11:00` | Ortak takvime etkinlik ekler |
| `/gunluk bugün yürüyüşe çıktım` · `/gunluk 😄 ...` | Bugünün günlüğüne ekler; başa ruh hali emojisi (😞 😕 😐 🙂 😄) konabilir |
| `/liste` · `/liste market` | Açık maddeleri butonlarla gösterir; dokununca işaretlenir |
| `/bugun` | Günün özeti |
| `/rapor` · `/rapor bu ay` | Aylık rapor |
| `/ara matkap` | Her yerde arama |
| `/aktar metin` · dosya gönder → **📤 Aktar** | Aktarma kutusuna koyar; bilgisayarda Aktar sayfasından açılır (1 saat) |
| `/tara` → fotoğrafları gönder → **📄 PDF yap** | Belge tarama: sayfalar sormadan tarama kutusuna girer, PDF orijinal renkleriyle sohbete gelir (`/tara bitti`; siyah-beyaz için **⚫** ya da `/tara siyah`; `/tara iptal`); tek fotoğrafta **📄 Taramaya ekle** |
| `/kisalt https://ornek.com/uzun-adres [kod]` | Kısa link oluşturur (kod verilmezse rastgele); kısa adresi ve **Yönet →** bağlantısını döner |
| `/baslat web sitesi ana sayfa` | Zaman sayacını başlatır; baştaki kelimeler bir projenin adıysa o projeye, kalanı not olur (çalışan sayaç durur). Mesajdaki **⏹️ Durdur** ile durdurulur |
| `/durdur` | Sayacı durdurur, süreyi ve bugünün toplamını yazar |
| `/zaman` | Çalışan sayaç ve bugünün toplamı (proje bazında) |
| Link | Sonra Bak'a kaydedilir |
| Fotoğraf / PDF | Hangi garantiye ya da nota ekleneceği sorulur (açıklama yazarsan yeni garantinin adı olur) |
| Konum (📎 → Konum) | **🅿️ Park yeri** (Harita'daki park kaydının üzerine yazar, cevapta yol tarifi) ya da **📍 Yer olarak kaydet** (Harita'ya “Telegram konumu GG.AA SS:DD” adlı yer; 30 km içindeki şehrine bağlanır) |
| Düz yazı | Not / alışveriş / yapılacak / harcama / günlük olarak ne yapılacağı sorulur |

## Zamanlanmış görevler (cron-job.org, ücretsiz)

PythonAnywhere ücretsiz planında zamanlanmış görev yok. Bunun yerine [cron-job.org](https://cron-job.org) gibi ücretsiz bir servis, sitendeki gizli bir adresi belirli saatlerde çağırır:

| Görev | Adres | Önerilen zaman |
|---|---|---|
| Günlük özet (hava, yaklaşan ödemeler, randevular, ilaçlar) + 30 günü dolan çöpü temizleme | `https://KULLANICI.pythonanywhere.com/cron/CRON_SECRET/gunluk` | Her gün 08:00 (`0 8 * * *`) |
| Yapılacak, fatura, belge, ev bakımı, ilaç, günlük hatırlatmaları, akşam hava uyarısı ve zamanlı otomasyonlar | `https://KULLANICI.pythonanywhere.com/cron/CRON_SECRET/hatirlatma` | 5 dakikada bir (`*/5 * * * *`) |
| Veritabanı yedeğini yöneticilere Telegram'dan gönder | `https://KULLANICI.pythonanywhere.com/cron/CRON_SECRET/yedek` | Haftada bir, Pazar 03:00 (`0 3 * * 0`) |

Günlük özet aynı gün ikinci kez çağrılsa da tekrar gönderilmez. `CRON_SECRET`'ı kimseyle paylaşma.
cron-job.org'da saat dilimini **Europe/Istanbul** yapmayı unutma.

**Yapılacaklar hatırlatmaları:** Maddeye tarih, isteğe bağlı saat ve hatırlatma zamanı (zamanı gelince / 15 dk / 1 saat / 3 saat / 1 gün önce) verilir. “… önce” seçilirse iki mesaj gelir: süre yaklaşınca ⏰ ve zamanı gelince 🔔. Mesaj maddeyi ekleyen kişiye (Telegram'ı bağlı değilse liste sahibine) gider. Hatırlatmalar cron çağrısı sıklığına göre en fazla 5 dakika gecikir; cron 6 saatten uzun süre çalışmazsa kaçan eski hatırlatmalar toplu gönderilmez. Tarih/saat/hatırlatma değiştirilince mesajlar yeniden gönderilebilir. Mesajdaki **⏰ 1 saat ertele** / **📅 Yarına** butonları işi erteler.

**Tekrarlayan işler:** Yapılacağa *her gün / hafta içi / her hafta / her ay / her yıl* tekrarı verilebilir (bottan: `/yap çöpü at pazartesi 20:00 her hafta`). Tamamlanınca bir sonraki tarihle yenisi eklenir; geri alınırsa o yenisi silinir.

**Fatura hatırlatmaları:** “Telegram'dan hatırlat” işaretli ödenmemiş faturalar için son günden bir gün önce ve son gün 09:00'da mesaj gelir. **✅ Ödendi** butonu faturayı öder, tekrarlıysa sonraki ayı ekler ve harcamalara yazar; **↩️ Geri al** bunların hepsini geri alır.

**Otomasyon:** Otomasyon sayfasından “eğer şu olursa bunu yap” kuralları kurulur (hazır örnekler: her ay kira harcaması, market listesi kalabalıklaşınca haber, fatura ödenince eşe mesaj).

| Ne zaman | Ne yapılsın |
|---|---|
| Her gün / her hafta / her ay belirli saatte · harcama eklenince (kategori, en az tutar) · fatura ödenince · listedeki açık ürün sayısı eşiği geçince · yapılacak tamamlanınca | Telegram mesajı (kendine ya da Telegram'ı bağlı başka bir kullanıcıya) · harcama ekle · yapılacak ekle · alışveriş listesine ekle · not ekle |

Metinlerde `{tarih}`, `{ay}`, `{tutar}`, `{fatura}`, `{liste}`, `{adet}`, `{is}`, `{kim}` gibi yer tutucular kullanılır. Zamanlı kurallar 5 dakikalık `/hatirlatma` görevinde, her dönemde (gün/hafta/ay) bir kez çalışır; cron bir süre çalışmasa da dönem içinde geç de olsa yapılır. Otomasyonun yaptığı işlem başka bir otomasyonu tetiklemez, bir kural günde en fazla 20 kez çalışır, eylem hata verse de asıl işlem (ör. harcama kaydı) bozulmaz. **▶️ Şimdi dene** eylemi örnek değerlerle hemen bir kez yapar.

**Belgeler:** Seçilen süre kadar önce (1 hafta – 6 ay) ve bittiği gün 09:00'da mesaj gelir (“🛂 30 gün sonra bitiyor: Pasaport (Ayşe)”); panoda ve takvimde 30 gün önceden görünür. Belge yenilenip tarih değiştirilince hatırlatmalar yeni tarihe göre kurulur. Gizlilik için belge numarası ya da fotoğrafı saklanmaz.

**Ev Bakımı:** Her işin periyodu (ör. 6 ayda bir), sıradaki tarihi ve kaç gün önce hatırlatılacağı girilir; **📋 Hazır şablonlar**dan (kombi bakımı Ekim'de, klima bakımı Mayıs'ta, su arıtma filtresi 6 ayda bir, dedektör pili, DASK yenileme...) birkaçı işaretlenip tek seferde eklenir, listede olanlar tekrar eklenmez. **✅ Yaptım** sıradaki tarihi yapılma gününden periyot kadar ileri alır (31 Ocak + 1 ay = 28/29 Şubat); tutar ve not geçmişte görünür, istenirse Harcamalar'a da işlenir; **↩️ Son kaydı geri al** tarihi eski haline döndürür, eklenen harcamayı da siler. Seçilen süre kadar önce ve günü gelince 09:00'dan sonra birer kez mesaj gelir (“🔥 7 gün sonra: Kombi bakımı”), gecikmişse haftada bir yeniden; mesajdaki **✅ Yaptım** bugünle kaydeder, **⏰ 1 hafta ertele** sıradaki tarihi bir hafta sonraya alır (gecikmişse bugünden bir hafta sonrasına; pano ve takvim de yeni tarihi gösterir, mesaj o gün gelir). Gecikenler ve yaklaşanlar panoda, günlük özette ve takvimde görünür; durdurulan (pasif) işe hatırlatma gelmez.

**Siparişler:** Mağaza, ürün, tutar, beklenen teslim, kargo firması ve takip no girilir; **📍 Kargo takip** girilen takip linkini, yoksa takip numarasıyla Google aramasını açar. **📦 Teslim aldım** denince iade son günü hesaplanır (varsayılan 14 gün cayma hakkı; mağaza farklı veriyorsa değiştirilir, 0 = takip etme); teslim tarihi sonradan düzeltilirse yeniden hesaplanır. Beklenen teslim günü, iade için son 2 gün ve son gün 09:00'dan sonra birer kez mesaj gelir (“↩️ İade için son 2 gün: Kulaklık”); iadesi başlatılan, tamamlanan ya da iptal edilen siparişe gelmez. İstenirse sipariş harcamalara da eklenir; iade tamamlanınca ya da iptalde o harcama kaydı çöp kutusuna taşınabilir.

**Hava uyarısı:** Ayarlarda şehri olan kullanıcılara, yarın yağmur (≥%60), kar, gök gürültülü sağanak, don (≤0°), aşırı sıcak (≥35°) ya da kuvvetli rüzgâr (≥50 km/s) bekleniyorsa akşam 20:00'den sonra bir kez mesaj gelir; bugünün uyarıları günlük özette de yazar. Ayarlar'dan kapatılabilir.

**Günlük:** Günlük sayfasında akşam hatırlatması açılırsa, o gün yazılmadıysa seçilen saatte “📓 Bugün nasıldı?” diye sorulur; emojiye dokunmak ruh halini kaydeder (webhook kuruluysa). Panodaki “Bugün” kartından da tek dokunuşla girilebilir.

**Zaman takibi:** Zaman Takibi sayfasında proje ve not seçip **▶️ Başlat**; sayaç sayfa kapansa da sunucuda çalışmaya devam eder (aynı anda tek sayaç, yenisi başlayınca önceki durur). Elle kayıt için başlangıç–bitiş (gece yarısını geçebilir) ya da sadece süre (`1:30`, `90 dk`, `2 saat`) yazılır. Kayıt başladığı güne yazılır. **📊 Rapor** bu hafta / bu ay / geçen ay / özel aralık için proje bazında süre ve (saatlik ücreti olan projelerde) kazancı, haftalık görünümde gün gün toplamı gösterir; **⬇️ Excel** aynı aralığı indirir. 10 saatten uzun çalışan sayaç için bir kez “⏱️ Sayaç 10 saattir çalışıyor — unuttun mu?” mesajı ve **⏹️ Durdur** butonu gelir.

**Harita:** Şehir adı yazılınca Open-Meteo'dan adaylar gelir, doğrusu seçilir (gezdim / gitmek istiyorum, tarih, şehir notu). Şehre ya da bağımsız olarak yerler eklenir (🍽️ yemek, ☕ kafe, 🏛️ gezilecek, 🏨 konaklama, 🛍️ alışveriş, 🌳 doğa; durum, puan, adres, not). Konum haritaya dokunarak, “📍 Şu anki konumum” ile ya da Google Maps / Apple Haritalar / OpenStreetMap linki yapıştırarak verilir; `maps.app.goo.gl` gibi kısa linkler sunucudan açılamadığı için çözülmez (uzun linki ya da koordinatı yapıştır). Her yerde **🧭 Yol tarifi** ve **🗺️ Haritada aç** vardır. Harita tarayıcıda OpenStreetMap döşemeleriyle çizilir; harita kütüphanesi yüklenemezse sayfa liste olarak çalışır. **🅿️ Park ettim** telefonun konumunu (isteğe bağlı “B2 kat, 14 numara” notuyla) kaydeder, her seferinde öncekinin üzerine yazar; **🚗 Arabam nerede?** haritada gösterir, yol tarifi verir, **✅ Arabayı aldım** siler. Bota konum gönderilirse park yeri ya da yer olarak kaydetmek sorulur. Şehir silinince yerleri de çöp kutusuna gider ve birlikte geri gelir.

**Önemli günler:** Seçilen gün sayısı kadar önce ve o gün 09:00'da mesaj gelir (“🎂 7 gün sonra: Annemin doğum günü (60. yaş)”); panoda 30 gün önceden görünür.

**Kişiler:** Her kişiye görüşme sıklığı verilir (haftada bir … 6 ayda bir); vade son görüşmeden (hiç yoksa eklendiği günden) sayılır ve liste en çok gecikmiş olandan başlar. **📞 Aradım / 💬 Mesajlaştık / ☕ Görüştük** tek dokunuşla kaydeder, kişi sayfasından geçmişe dönük kayıt da girilir. Vadesi gelen kişi için 09:00'dan sonra bir kez mesaj gelir (“📞 Dede ile 23 gündür görüşmedin”); **✅ Aradım** kaydeder, **⏰ Yarın hatırlat** ertesi gün yeniden sorar. Gecikenler günlük özette “📇 Aranacaklar” satırında, doğum günleri (yaş biliniyorsa “60. yaş”) panoda ve takvimde görünür.

**Bütçe:** Harcamalar → 🎯 Bütçe'den toplam ve kategori bazında aylık limit konur; harcamalar sayfasında doluluk çubukları görünür, %80'e ve %100'e ulaşınca (her ay bir kez) Telegram uyarısı gelir. Bottan harcama eklerken ilgili bütçe durumu da yazılır.

**Aylık rapor:** Her ayın 1'inde günlük özetle birlikte geçen ayın raporu gelir: toplam ve önceki aya göre değişim, kategoriler, bütçe sonuçları, ödenen faturalar, abonelikler, alışkanlık başarı oranları. Bottan `/rapor` (geçen ay) ya da `/rapor bu ay`.

**Excel/CSV:** Harcamalar sayfasındaki **⬇️ Excel** o ayı indirir (`?ay=tumu` ile hepsi). Dosya Türkçe Excel'de doğrudan açılır (UTF-8, `;` ayırıcı, virgüllü ondalık).

**Belge Tara:** Telefonda **📷 Fotoğraf çek** ile sayfa sayfa (ya da **🖼️ Galeriden seç** ile topluca) eklenir; sayfalar sıralanır, 90° döndürülür. Görünüm varsayılan olarak *Orijinal renk* (fotoğraf olduğu gibi); istenirse *Gri* ya da *Siyah-beyaz belge* (kâğıt beyaz, yazı koyu); her sayfa en fazla A4 150 dpi'a küçültülür. PDF indirilir, yeni nota eklenir (not eki 3 MB'ı aşarsa bir kez daha küçültülür), Aktar'a konur (1 saat) ya da Telegram'a gönderilir. En fazla 20 sayfa / 60 MB; işleme sunucuda yapılır, fotoğraflar kaydedilmez. **Telegram'dan:** bota `/tara` yazıp sayfaların fotoğraflarını sırayla (ya da albüm olarak) gönder; tek bir durum mesajı sayıyı gösterir, **📄 PDF yap** ile PDF orijinal renkleriyle sohbete gelir (siyah-beyaz için **⚫ Siyah-beyaz**). Tarama modu dışında fotoğraf sorusundaki **📄 Taramaya ekle** de aynı kutuya koyar. Kutudaki sayfalar web'deki Belge Tara sayfasında listenin başında görünür (sıralanır, döndürülür, nota eklenir); PDF yapılınca silinir, yapılmazsa 24 saat sonra (yedeğe girmez). Albüm olarak gönderilen fotoğraflar artık tek soruda toplanır ve seçim hepsine uygulanır.

**Kur alarmı:** Kurlar sayfasında “Dolar 50 ₺ üstüne çıkınca” gibi alarm kurulur; tetiklenince mesaj gelir ve alarm kapanır. Kaynak Avrupa Merkez Bankası referans kuru olduğundan günde bir kez (iş günleri) güncellenir.

**İlaç hatırlatmaları:** “Doz saatlerinde Telegram'dan hatırlat” işaretli ilaçlar için her doz saatinde (en fazla 2 saat gecikmeyle) mesaj gelir; **✅ Aldım** ile işaretlenir. Sağlık → İlaçlar'da bugünkü dozlar işaretlenebilir ve son 7 günün uyumu görünür.

## Yedekleme

- **Yönetim → Yedek → Tam yedek**: veritabanı + tüm dosyalar tek bir zip. Haftada bir indirmen önerilir.
- **Sadece veritabanı**: küçük, hızlı. Telegram otomatik yedeği de bu türdendir.
- **Geri yükle**: zip'i seç, `EVET` yaz. Tüm veriler yedektekiyle değişir; eski sürüm yedekleri otomatik güncel şemaya taşınır.

## Yapay zekâ (isteğe bağlı)

Sağlayıcıdan bağımsızdır; ek paket kurmaz. Claude kendi API'siyle, diğerleri (Mistral, Google Gemini, OpenAI, OpenRouter, Groq, DeepSeek, Ollama…) OpenAI uyumlu API ile bağlanır. Hepsi PythonAnywhere ücretsiz izin listesinde (Gemini `.googleapis.com` kaydıyla).

WSGI dosyasına örnek:
```python
os.environ["AI_PROVIDER"] = "gemini"           # ya da "mistral", "anthropic"
os.environ["AI_API_KEY"] = "SAĞLAYICI_ANAHTARI"
# os.environ["AI_MODEL"] = "gemini-flash-lite-latest"   # isteğe bağlı
```
**Google Gemini:** anahtar [Google AI Studio → API Keys](https://aistudio.google.com/apikey)'ten alınır; ücretsiz katmanda dakikalık/günlük istek sınırı vardır. Ücretsiz katmanda gönderilen içerik Google tarafından ürün geliştirmede kullanılabilir; bunu istemiyorsan faturalandırmayı aç ya da başka sağlayıcı seç.
Yönetim → Durum → **Bağlantıyı test et** ile kontrol et. Her kullanıcı **Ayarlar → 🤖 Yapay zekâ**'dan kendisi açar; açmayan kullanıcının hiçbir verisi sağlayıcıya gönderilmez.

| Özellik | Nasıl |
|---|---|
| Fişten harcama / fatura | Bota fotoğraf → **🧾 Fişi oku** → tutar, mağaza, tarih, kategori (faturada son ödeme tarihi) okunur, tek dokunuşla kaydedilir. Web'de Harcamalar → **📷 Fişten ekle** |
| Doğal dil | Bota düz yazı: “yarın 3'te dişçiyi ara”, “markete 250 verdim”, “perşembe 14:30 diş randevum var”, “süt ve ekmek almam lazım” → onay kartı → **✅ Kaydet** |
| Soru sor | “bu ay markete ne kadar harcadım?” ya da `/sor ...` → verilerinin kısa özetinden cevap |

Soru cevaplarken harcamalar, yaklaşan ödemeler, listeler, alışkanlıklar, son ölçümler ve son notların kısa bir özeti sağlayıcıya gönderilir. Maliyet sağlayıcı ve modele göre değişir (Claude Opus 5'te fiş başına yaklaşık 1-2 sent).

## Telefon takvimine abonelik

Ayarlar → **📅 Telefon takvimine abonelik** → *Takvim adresi oluştur*. Faturalar, abonelik yenilemeleri, randevular, yapılacaklar, araç tarihleri, garanti bitişleri, ev bakımı ve önemli günler telefon takviminde görünür (son 60 gün ve gelecek 1 yıl; takvim uygulaması saatte bir günceller).

- **iPhone:** “iPhone / Mac'te aç” butonu ya da Ayarlar → Takvim → Hesaplar → Abone Olunan Takvim Ekle.
- **Google Takvim:** bilgisayardan calendar.google.com → Diğer takvimler **+** → *URL ile* → adresi yapıştır.

Adres gizli bir anahtar içerir, giriş gerektirmez; kimseyle paylaşma. *Yeni adres oluştur* eski adresi geçersiz kılar, *Kapat* aboneliği tamamen kapatır.

## Herkese açık profil sayfası

Menü → Araçlar → **🌐 Profil Sayfası**: ad, başlık, kısa yazı, emoji ya da fotoğraf, vurgu rengi, iletişim bilgileri ve en fazla 12 link ile `https://SİTE/p/adres` adresinde girişsiz açılan tek bir sayfa. Sayfada 📞 Ara, 💬 WhatsApp, ✉️ E-posta ve **📇 Rehbere ekle** (vCard) butonları olur; QR kodu kartvizit için PNG olarak indirilebilir. Varsayılan olarak kapalıdır ve arama motorlarına kapalıdır (`noindex`). Sayfada sadece bu ekranda yazılanlar görünür; notlar, harcamalar ve diğer kayıtlar asla gösterilmez. Linkler sadece `http(s)://`, `mailto:` ve `tel:` olabilir; fotoğraf 400×400'e küçültülür, konum (EXIF) bilgisi silinir ve veritabanında saklanır (yedeğe girer). Kapalı sayfa ile olmayan sayfa dışarıdan aynı “bulunamadı” sayfasını gösterir; sahibi kapalıyken de önizleyebilir.

## Acil durum kartı

Menü → Araçlar → **🆘 Acil Durum Kartı**: ad, doğum tarihi (sayfada yaş da yazar), kan grubu, alerjiler, kronik hastalıklar, kullanılan ilaçlar (**🩺 Sağlık'taki ilaçları getir** ile aktif ilaçlardan doldurulur), organ bağışı, notlar ve en fazla 5 acil kişi (Kişiler'den seçilebilir). Kart `https://SİTE/acil/<anahtar>` adresinde girişsiz, betiksiz tek bir sayfada açılır: kırmızı “ACİL DURUM / EMERGENCY” başlığı, büyük kan grubu kutusu, belirgin alerjiler ve her kişi için büyük **📞 Ara** düğmesi; etiketler Türkçe + İngilizce, boş alanlar görünmez. Anahtar tahmin edilemez (okunur bir adres değil); **🔄 Bağlantıyı yenile** eski bağlantıyı, QR'ı, duvar kâğıdını ve cüzdan kartını geçersiz kılar. **📱 Kilit ekranı duvar kâğıdı** (1080×2340 PNG, üstü saat için boş, ortada büyük QR) telefonun kilit ekranına, **🪪 Cüzdan kartı** (85,6 × 54 mm, ön yüz bilgiler, arka yüz QR) yazdırılıp cüzdana konur. Varsayılan olarak kapalıdır; kapalı kart dışarıdan “bulunamadı” görünür, sahibi önizleyebilir. Sayfa arama motorlarına ve önbelleğe kapalıdır (`noindex`, `no-store`); bağlantıyı bilen herkes bilgileri görebileceği için sadece acil durumda gerekecek bilgileri yaz. Başkası açınca görüntülenme sayısı artar; Telegram bağlıysa en fazla 30 dakikada bir “kartın görüntülendi” mesajı gelir. Silinen kart 30 gün çöp kutusunda kalır.

## Kısa link ve QR

Menü → Araçlar → **🔗 Kısa Link & QR**: uzun bir adres `https://SİTE/k/kod` olarak kısaltılır (kod boşsa karışmayan harflerden 6 karakter; elle yazılırsa küçük harf, rakam ve tire, Türkçe harfler dönüştürülür; kodlar tüm kullanıcılar arasında tekildir). Kısa adres girişsiz açılır, hedefe yönlendirir ve tıklanmayı sayar (WhatsApp/Telegram link önizlemeleri sayılmaz); sonuna `/onizle` eklenirse önce hedef gösterilir (sayılmaz). Pasif, süresi dolmuş ve olmayan link dışarıdan aynı “bulunamadı” sayfasını gösterir. Hedef sadece `http(s)://` olabilir; sitenin kendi `/k/` adresine giden link reddedilir. **📶 Wi-Fi** sekmesinde ağ adı ve şifreden, telefon kamerasıyla okutunca ağa bağlanan QR ve yazdırılabilir misafir kartı hazırlanır (şifre sunucuda düz metin durur, Şifreli Kasa değildir); **▦ Serbest QR** metin, web adresi ya da telefonu kaydetmeden QR'a çevirir.

## Telefona uygulama olarak ekleme

- **Android (Chrome):** ⋮ menüsü → *Uygulamayı yükle*. Sonra başka uygulamalardan **Paylaş → Pano** ile link kaydedebilirsin.
- **iPhone (Safari):** Paylaş → *Ana Ekrana Ekle*.

## Render (alternatif)

`render.yaml` Blueprint için hazır, ama Render ücretsiz planında disk kalıcı değil: servis her uyuduğunda veritabanı ve dosyalar silinir. Sadece deneme için uygundur.
