"""📄 Belge tarayıcı: telefonla çekilen sayfa fotoğraflarından tek PDF.

- Sayfalar tarayıcıda eklenir, sıralanır, döndürülür (static/scanner.js); işleme sunucuda (Pillow)
- Her sayfa sırayla işlenir: EXIF yönü düzeltilir, JPEG doğrudan küçük ölçekte açılır (12 MP fotoğraf belleğe
  tam boy açılmaz), uzun kenar en fazla 1754 px (A4, 150 dpi), istenen dönüş ve mod (renkli / gri / belge)
- Fotoğraflar kaydedilmez: PDF sadece seçilen hedefe gider (indir / not eki / Aktar / Telegram)
- Not eki sınırı storage.PDF_MAX_BYTES (3 MB): aşılırsa bir kez daha düşük kalite ve çözünürlükle denenir
- En fazla 20 sayfa, istek en fazla 60 MB; dev boyutlu resim (Image.MAX_IMAGE_PIXELS) reddedilir
"""
import io
import re

from flask import Blueprint, flash, g, redirect, render_template, request, send_file, url_for
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

from .. import storage, telegram
from .. import todo_reminders as todo
from ..auth import large_upload, login_required
from ..db import execute
from ..utils import fmt_size, form_choice, form_str
from . import transfer

bp = Blueprint("scanner", __name__, url_prefix="/tara")

MAX_PAGES = 20
MAX_REQUEST_MB = 60
PAGE_MAX_PX = 1754          # A4'ün uzun kenarı 150 dpi'da
DPI = 150
QUALITY = 65
RETRY_MAX_PX = 1240         # not ekine sığmazsa ikinci deneme (~106 dpi, sayfa yine A4 boyunda)
RETRY_QUALITY = 45
TELEGRAM_MAX_BYTES = 50 * 1024 * 1024
FORMATS = {"JPEG", "MPO", "PNG", "WEBP", "GIF", "BMP", "TIFF", "AVIF"}
MODES = {"color": "Renkli", "gray": "Gri", "doc": "Belge (yüksek kontrast)"}
TARGETS = {"download": "⬇️ PDF indir", "note": "📝 Nota ekle", "transfer": "📤 Aktar'a koy",
           "telegram": "📲 Telegram'a gönder"}
# Saat yönünde derece -> Pillow dönüşü (ROTATE_x saat yönünün tersine döndürür)
ROTATIONS = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}


class ScanError(Exception):
    pass


def default_title():
    return todo.now_local().strftime("Tarama %d.%m.%Y %H-%M")


def pdf_filename(title):
    """Dosya adında sorun çıkaran karakterleri atar; Türkçe harfler kalır."""
    name = " ".join(re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]+', " ", title).split()).strip(" .")
    return (name[:100] or "Tarama") + ".pdf"


def _telegram_linked():
    return bool(g.user["telegram_chat_id"]) and telegram.enabled()


# ---------- İşleme ----------
def _open(stream, label, mimetype, filename):
    """Resmi açar (henüz çözmeden); resim değilse ya da açılamıyorsa anlaşılır hata."""
    try:
        img = Image.open(stream)
    except Image.DecompressionBombError:
        raise ScanError(f"{label}: resim çok büyük.")
    except (UnidentifiedImageError, OSError):
        stream.seek(0)
        head = stream.read(16)
        if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"hevc", b"mif1", b"msf1"):
            raise ScanError(f"{label}: HEIC biçimi desteklenmiyor; kamerada “En uyumlu” (JPEG) biçimini seç.")
        if (mimetype or "").startswith("image/") or filename.lower().endswith(tuple(storage.IMAGE_EXTS)):
            raise ScanError(f"{label}: resim açılamadı (bozuk ya da desteklenmeyen biçim).")
        raise ScanError(f"{label}: resim değil. Sadece fotoğraf (JPG, PNG, WEBP) eklenebilir.")
    if img.format not in FORMATS:
        raise ScanError(f"{label}: desteklenmeyen biçim ({img.format}); JPG, PNG ya da WEBP olmalı.")
    if img.width * img.height > Image.MAX_IMAGE_PIXELS:
        raise ScanError(f"{label}: resim çok büyük ({img.width}×{img.height}).")
    return img


def _to_mode(img, mode):
    """Saydamlığı beyaz zemine oturtur; renkli için RGB, gri ve belge için L."""
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        img = Image.new("RGB", rgba.size, "white")
        img.paste(rgba, mask=rgba.getchannel("A"))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    target = "RGB" if mode == "color" else "L"
    return img if img.mode == target else img.convert(target)


def _enhance_document(img):
    """Belge modu: kâğıt beyaz, yazı koyu olsun (gölgesiz, iyi ışıkta çekilmiş sayfada en iyi sonuç)."""
    img = ImageOps.autocontrast(img, cutoff=2)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=70, threshold=3))
    img = ImageEnhance.Brightness(img).enhance(1.15)
    return ImageEnhance.Contrast(img).enhance(1.4)


def process_page(stream, label, mode="color", rotation=0, max_px=PAGE_MAX_PX, mimetype="", filename=""):
    """Tek sayfa: aç, küçült, yönünü düzelt, döndür, modu uygula. Pillow resmi döner."""
    img = _open(stream, label, mimetype, filename)
    try:
        scale = max_px / max(img.size)
        if scale < 1:
            # JPEG'i baştan 1/2, 1/4, 1/8 ölçekte çöz: bellek ve işlemci için
            img.draft("RGB" if mode == "color" else "L",
                      (max(1, int(img.width * scale)), max(1, int(img.height * scale))))
        img = ImageOps.exif_transpose(img)
        img = _to_mode(img, mode)
        img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    except Image.DecompressionBombError:
        raise ScanError(f"{label}: resim çok büyük.")
    except (OSError, SyntaxError, ValueError):
        raise ScanError(f"{label}: resim bozuk, okunamadı.")
    if rotation in ROTATIONS:
        img = img.transpose(ROTATIONS[rotation])
    if mode == "doc":
        img = _enhance_document(img)
    return img


def build_pdf(files, rotations, mode, title, max_px=PAGE_MAX_PX, quality=QUALITY):
    """files: FileStorage listesi (sırası sayfa sırası). PDF baytları döner; sorunlu sayfada ScanError."""
    pages = []
    for i, f in enumerate(files):
        f.stream.seek(0)
        pages.append(process_page(f.stream, f"{i + 1}. sayfa ({f.filename})", mode,
                                  rotations[i] if i < len(rotations) else 0, max_px, f.mimetype, f.filename or ""))
    buf = io.BytesIO()
    # Çözünürlük küçültmeyle orantılı: ikinci denemede de sayfa kâğıt boyunda kalır
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:], resolution=DPI * max_px / PAGE_MAX_PX,
                  quality=quality, title=title)
    return buf.getvalue()


def _rotations(count):
    """Gizli alandaki "0,90,180" -> sayfa başına saat yönünde derece."""
    raw = [s.strip() for s in (request.form.get("rotations") or "").split(",")]
    return [int(raw[i]) if i < len(raw) and raw[i] in ("90", "180", "270") else 0 for i in range(count)]


# ---------- Rotalar ----------
@bp.route("/")
@login_required
def index():
    targets = {k: v for k, v in TARGETS.items() if k != "telegram" or _telegram_linked()}
    return render_template("scanner/index.html", default_title=default_title(), modes=MODES, targets=targets,
                           max_pages=MAX_PAGES, max_mb=MAX_REQUEST_MB, note_max=fmt_size(storage.PDF_MAX_BYTES))


@bp.route("/pdf", methods=["POST"])
@login_required
@large_upload(MAX_REQUEST_MB)
def make_pdf():
    uid = g.user["id"]
    files = [f for f in request.files.getlist("files") if f and f.filename]
    title = form_str("title", 100) or default_title()
    mode = form_choice("mode", MODES, "doc")
    target = form_choice("target", TARGETS, "download")
    if not files:
        flash("Önce fotoğraf çek ya da galeriden seç.", "warning")
        return redirect(url_for(".index"))
    if len(files) > MAX_PAGES:
        flash(f"En fazla {MAX_PAGES} sayfa olabilir; {len(files)} sayfa seçildi.", "error")
        return redirect(url_for(".index"))
    if target == "telegram" and not _telegram_linked():
        flash("Önce Ayarlar'dan Telegram'ı bağla.", "warning")
        return redirect(url_for(".index"))
    rotations = _rotations(len(files))
    shrunk = False
    try:
        pdf = build_pdf(files, rotations, mode, title)
        if target == "note" and len(pdf) > storage.PDF_MAX_BYTES:
            pdf, shrunk = build_pdf(files, rotations, mode, title, RETRY_MAX_PX, RETRY_QUALITY), True
            if len(pdf) > storage.PDF_MAX_BYTES:
                raise ScanError(f"PDF küçültülünce de {fmt_size(len(pdf))} oldu; not eki en fazla "
                                f"{fmt_size(storage.PDF_MAX_BYTES)} olabilir. PDF indir ya da Aktar'a koy.")
    except ScanError as e:
        flash(str(e), "error")
        return redirect(url_for(".index"))

    name, pages = pdf_filename(title), len(files)
    summary = f"{pages} sayfa, {fmt_size(len(pdf))}"
    if target == "note":
        note_id = execute("INSERT INTO notes (user_id, title, content, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                          (uid, title, f"📄 {pages} sayfalık tarama")).lastrowid
        try:
            storage.save_attachment(FileStorage(io.BytesIO(pdf), filename=name, content_type="application/pdf"),
                                    uid, "note", note_id)
        except ValueError as e:
            execute("DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, uid))
            flash(f"Nota eklenemedi: {e} PDF indir ya da Aktar'a koy.", "error")
            return redirect(url_for(".index"))
        flash(f"📝 PDF yeni nota eklendi ({summary}"
              + ("; sığması için kalite düşürüldü" if shrunk else "") + ").", "success")
        return redirect(url_for("notes.edit", note_id=note_id))
    if target == "transfer":
        try:
            transfer.add_file(uid, pdf, name, "application/pdf", transfer.DEFAULT_MINUTES,
                              device=transfer.device_label(request.user_agent.string))
        except transfer.TransferError as e:
            flash(f"Aktar'a konamadı: {e}", "error")
            return redirect(url_for(".index"))
        flash(f"📤 PDF aktarma kutusuna kondu ({summary}); 1 saat sonra silinecek.", "success")
        return redirect(url_for("transfer.index"))
    if target == "telegram":
        try:
            if len(pdf) > TELEGRAM_MAX_BYTES:
                raise telegram.TelegramError("Telegram en fazla 50 MB dosya gönderebiliyor.")
            telegram.send_document(g.user["telegram_chat_id"], name, pdf, caption=f"📄 {title} · {pages} sayfa",
                                   mime="application/pdf")
            flash(f"📲 PDF Telegram'a gönderildi ({summary}).", "success")
        except telegram.TelegramError as e:
            flash(f"Gönderilemedi: {e}", "error")
        return redirect(url_for(".index"))

    resp = send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True, download_name=name)
    resp.headers["Cache-Control"] = "no-store"
    token = request.form.get("dl", "")
    if re.fullmatch(r"[a-z0-9]{6,20}", token):
        # İndirme bitince sayfa bunu görür ve "hazırlanıyor" durumunu kapatır (tarayıcı indirmeyi JS'e bildirmez)
        resp.set_cookie("tara_dl", token, max_age=120, path=url_for(".index"), samesite="Lax", secure=request.is_secure)
    return resp
