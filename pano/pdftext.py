"""PDF'ten düz yazı (ek paket yok): e-bilet gibi bilgisayarda üretilmiş PDF'leri yapay zekâya metin olarak vermek için.

Tam bir PDF okuyucu değildir; bilet PDF'lerinde yaygın olanı çözer:
- Sıkıştırılmamış ve FlateDecode akışlar, sıkıştırılmış nesne akışları (ObjStm, PDF 1.5+)
- Sayfa ve form içeriğindeki Tj / TJ / ' / " metinleri: yazı tipinin ToUnicode tablosu (bfchar / bfrange) varsa
  onunla, yoksa WinAnsi (cp1252) olarak. Aynı ad farklı sayfalarda farklı yazı tipiyse en çok kodu çözen seçilir.
- Satır ve kelime araları metnin konumundan çıkarılır: yazı tipinin harf genişlikleriyle (/Widths, /W) bir önceki
  yazının bittiği yer hesaplanır; aradaki boşluk yeterince büyükse kelime arası sayılır (harf aralığı ayarı değil).
Taranmış (sadece resim) ve şifreli PDF'lerden yazı çıkmaz; bozuk girdi hata vermez, boş metin döner.
"""
import re
import zlib

MAX_STREAM = 4 * 1024 * 1024   # açılan akış başına üst sınır (sıkıştırma bombası)
MAX_TOTAL = 24 * 1024 * 1024   # bütün açılan akışların toplamı (çok akışlı dev PDF'te bellek şişmesin)
MAX_TEXT = 20000               # dönen metnin üst sınırı (karakter)
SPACE_GAP = 0.15               # iki yazı arasında bu kadar em'den büyük boşluk kelime arası sayılır
PLAIN = (None, {}, 500.0, 1)   # bilinmeyen yazı tipi: ToUnicode yok, ortalama genişlik, tek baytlık kod

_OBJ_RE = re.compile(rb"(\d+)\s+\d+\s+obj\b")
_STREAM_RE = re.compile(rb">>\s*stream(?:\r\n|\n|\r)")
# Yazı içermeyen akışlar hiç açılmaz: resimler, gömülü yazı tipi dosyaları, çapraz başvuru tabloları
_SKIP_RE = re.compile(rb"/Subtype\s*/(?:Image|Type1C|CIDFontType0C|OpenType)\b|/Type\s*/(?:XRef|Metadata)\b|/Length[123]\b")
_REF_RE = re.compile(rb"(\d+)\s+\d+\s+R")
_NUM_RE = re.compile(rb"[+-]?(?:\d+\.?\d*|\.\d+)")
_EI_RE = re.compile(rb"\sEI(?=\s|$)")
_WS = frozenset(b" \t\r\n\f\x00")
_DELIM = frozenset(b"()<>[]{}/%")
_ESCAPES = {ord("n"): b"\n", ord("r"): b"\r", ord("t"): b"\t", ord("b"): b"\b", ord("f"): b"\f"}


# ---------- Nesneler ve akışlar ----------
def _inflate(raw):
    try:
        return zlib.decompressobj().decompress(raw, MAX_STREAM)
    except zlib.error:
        return None


def _decode(head, raw):
    """Akışın çözülmüş baytları; desteklenmeyen sıkıştırmada (resimler vb.) None."""
    m = re.search(rb"/Filter\s*(\[[^\]]*\]|/\w+)", head)
    names = re.findall(rb"/(\w+)", m.group(1)) if m else []
    if not names:
        return raw
    if names in ([b"FlateDecode"], [b"Fl"]):
        return _inflate(raw)
    return None


def _objects(data):
    """{nesne_no: (sözlük_baytları, çözülmüş_akış | None)}; sıkıştırılmış nesne akışlarının içi de eklenir."""
    objs, total = {}, 0
    for m in _OBJ_RE.finditer(data):
        end = data.find(b"endobj", m.end())
        if end < 0:
            continue
        body = data[m.end():end]
        s = _STREAM_RE.search(body)
        if s:
            head, raw = body[:s.start() + 2], body[s.end():]
            e = raw.rfind(b"endstream")
            stream = None if _SKIP_RE.search(head) or total > MAX_TOTAL else _decode(head, raw[:e] if e >= 0 else raw)
            total += len(stream or b"")
            objs[int(m.group(1))] = (head, stream)
        else:
            objs[int(m.group(1))] = (body, None)
    for head, stream in list(objs.values()):
        first = re.search(rb"/First\s+(\d+)", head)
        if not stream or not first or not re.search(rb"/Type\s*/ObjStm\b", head):
            continue
        first = int(first.group(1))
        nums = [int(x) for x in stream[:first].split() if x.isdigit()]
        pairs = list(zip(nums[0::2], nums[1::2]))
        for k, (num, offset) in enumerate(pairs):
            stop = pairs[k + 1][1] if k + 1 < len(pairs) else len(stream) - first
            objs.setdefault(num, (stream[first + offset:first + stop], None))
    return objs


def _deref(objs, value):
    """Değer '12 0 R' ise o nesnenin baytları, değilse kendisi."""
    m = re.fullmatch(rb"\s*(\d+)\s+\d+\s+R\s*", value)
    return objs.get(int(m.group(1)), (b"", None))[0] if m else value


# ---------- Yazı tipleri ----------
def _hex(text):
    text = re.sub(rb"\s", b"", text)
    return bytes.fromhex((text + b"0" * (len(text) % 2)).decode("ascii"))


def _utf16(raw):
    return raw.decode("utf-16-be", "ignore") if len(raw) % 2 == 0 else raw.decode("latin-1")


def _cmap(data):
    """ToUnicode tablosu -> ({kod_baytları: metin}, kod uzunlukları büyükten küçüğe)."""
    table = {}
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f\s]+)>\s*<([0-9A-Fa-f\s]*)>", block):
            table[_hex(src)] = _utf16(_hex(dst))
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for lo, hi, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f\s]*>|\[[^\]]*\])", block):
            lo_b = _hex(lo)
            first, last, width = int.from_bytes(lo_b, "big"), int.from_bytes(_hex(hi), "big"), len(lo_b)
            if not 0 <= last - first < 65536:
                continue
            if dst.startswith(b"["):
                for k, item in enumerate(re.findall(rb"<([0-9A-Fa-f\s]*)>", dst)[:last - first + 1]):
                    table[(first + k).to_bytes(width, "big")] = _utf16(_hex(item))
                continue
            base = _hex(dst[1:-1])
            start = int.from_bytes(base, "big")
            for k in range(min(last - first + 1, 256 ** len(base) - start)):  # hedefin son baytı artar
                table[(first + k).to_bytes(width, "big")] = _utf16((start + k).to_bytes(len(base), "big"))
    return table, sorted({len(k) for k in table}, reverse=True) or [1]


def _cid_widths(objs, desc):
    """CIDFont /W dizisi ('c [w1 w2 ...]' ve 'c_ilk c_son w' biçimleri) -> {cid: genişlik}."""
    m = re.search(rb"/W\s*(\[|(\d+)\s+\d+\s+R)", desc)
    if not m:
        return {}
    data = objs.get(int(m.group(2)), (b"", None))[0] if m.group(2) else desc[m.start(1):]
    out, pending, inner, depth = {}, [], [], 0
    for kind, value in _tokens(data):
        if kind == "[":
            depth += 1
            inner = []
        elif kind == "]":
            depth -= 1
            if depth <= 0:
                break
            if pending:
                for k, w in enumerate(inner[:65536]):
                    out[int(pending[-1]) + k] = w
            pending = []
        elif kind == "num" and depth == 2:
            inner.append(value)
        elif kind == "num" and depth == 1:
            pending.append(value)
            if len(pending) == 3:
                lo, hi = int(pending[0]), int(pending[1])
                for c in range(lo, min(hi, lo + 65535) + 1):
                    out[c] = pending[2]
                pending = []
    return out


def _font_info(objs, head):
    """(ToUnicode tablosu | None, {kod: genişlik (binde em)}, varsayılan genişlik, kod bayt sayısı)"""
    m = re.search(rb"/ToUnicode\s+(\d+)\s+\d+\s+R", head)
    target = objs.get(int(m.group(1))) if m else None
    cmap = _cmap(target[1]) if target and target[1] else None
    if re.search(rb"/Subtype\s*/Type0\b", head):
        m = re.search(rb"/DescendantFonts\s*(\[[^\]]*\]|\d+\s+\d+\s+R)", head)
        desc = _deref(objs, m.group(1)) if m else b""
        ref = _REF_RE.search(desc)
        desc = objs.get(int(ref.group(1)), (b"", None))[0] if ref else desc
        dw = re.search(rb"/DW\s+(\d+(?:\.\d+)?)", desc)
        return cmap, _cid_widths(objs, desc), float(dw.group(1)) if dw else 1000.0, 2
    first = re.search(rb"/FirstChar\s+(\d+)", head)
    m = re.search(rb"/Widths\s*(\[[^\]]*\]|\d+\s+\d+\s+R)", head)
    values = [float(x) for x in _NUM_RE.findall(_deref(objs, m.group(1)))] if m else []
    start = int(first.group(1)) if first else 0
    return cmap, {start + k: w for k, w in enumerate(values)}, 500.0, 1


def _fonts(objs):
    """{kaynak_adı: [yazı tipi bilgisi, ...]} — sayfa kaynaklarındaki adlarıyla (aynı ad birden çok yazı tipi olabilir)."""
    infos, names = {}, {}
    for head, _stream in objs.values():
        for m in re.finditer(rb"/Font\s*(?:<<(.*?)>>|(\d+)\s+\d+\s+R)", head, re.S):
            body = m.group(1) if m.group(1) is not None else objs.get(int(m.group(2)), (b"", None))[0]
            for name, ref in re.findall(rb"/([^\s/\[\]()<>{}%]+)\s+(\d+)\s+\d+\s+R", body):
                ref = int(ref)
                if ref in objs and ref not in infos:
                    infos[ref] = _font_info(objs, objs[ref][0])
                if ref in infos and ref not in names.setdefault(name, []):
                    names[name].append(ref)
    return {name: [infos[r] for r in refs] for name, refs in names.items()}


def _apply(cmap, raw):
    """(metin, çözülemeyen kod sayısı, toplam kod)"""
    table, widths = cmap
    out, bad, total, i = [], 0, 0, 0
    while i < len(raw):
        total += 1
        for w in widths:
            text = table.get(raw[i:i + w])
            if text is not None:
                out.append(text)
                i += w
                break
        else:
            bad += 1
            i += widths[-1]
    return "".join(out), bad, total


def _show(raw, cands):
    """(metin, seçilen yazı tipi): ToUnicode'u olanlardan en çok kodu çözen; hiçbiri çözemezse WinAnsi."""
    best, best_info = None, None
    for info in cands:
        if info[0]:
            result = _apply(info[0], raw)
            if best is None or result[1] < best[1]:
                best, best_info = result, info
    if best and best[1] * 2 <= best[2]:
        return best[0], best_info
    text = "".join(ch for ch in raw.decode("cp1252", "replace") if ch.isprintable() and ch != "�")
    return text, next((i for i in cands if not i[0]), cands[0] if cands else PLAIN)


def _advance(raw, info, size, char_space, word_space):
    """Yazının yatay ilerlemesi (metin uzayında): harf genişliği × boyut + harf ve kelime aralığı."""
    _cmap, widths, default, n = info
    total = 0.0
    for i in range(0, len(raw) - n + 1, n):
        code = int.from_bytes(raw[i:i + n], "big")
        total += widths.get(code, default) * size / 1000 + char_space + (word_space if n == 1 and code == 32 else 0)
    return total


# ---------- İçerik akışı ----------
def _literal(data, i):
    """'(' sonrasından ')' 'e kadar: (bayt, sonraki konum). İç içe parantez ve kaçışlar çözülür."""
    out, depth, n = bytearray(), 1, len(data)
    while i < n:
        c = data[i]
        if c == 0x5C:  # ters bölü
            i += 1
            if i >= n:
                break
            e = data[i]
            if e in _ESCAPES:
                out += _ESCAPES[e]
            elif 0x30 <= e <= 0x37:  # sekizlik \ddd
                octal = re.match(rb"[0-7]{1,3}", data[i:i + 3]).group()
                out.append(int(octal, 8) & 0xFF)
                i += len(octal) - 1
            elif e == 0x0D:  # satır devamı
                if data[i + 1:i + 2] == b"\n":
                    i += 1
            elif e != 0x0A:
                out.append(e)
        elif c == 0x28:
            depth += 1
            out.append(c)
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return bytes(out), i + 1
            out.append(c)
        else:
            out.append(c)
        i += 1
    return bytes(out), n


def _tokens(data):
    """(tür, değer): 'str' (bayt), 'name', 'num', 'op', '[' ']' '<<' '>>'."""
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        if c in _WS:
            i += 1
        elif c == 0x25:  # % yorum
            while i < n and data[i] not in (0x0A, 0x0D):
                i += 1
        elif c == 0x28:
            text, i = _literal(data, i + 1)
            yield "str", text
        elif c == 0x3C:
            if data[i + 1:i + 2] == b"<":
                yield "<<", None
                i += 2
                continue
            j = data.find(b">", i)
            if j < 0:
                return
            try:
                yield "str", _hex(data[i + 1:j])
            except ValueError:
                pass
            i = j + 1
        elif c == 0x3E:
            if data[i + 1:i + 2] == b">":
                yield ">>", None
            i += 2 if data[i + 1:i + 2] == b">" else 1
        elif c in (0x5B, 0x5D):
            yield chr(c), None
            i += 1
        elif c in (0x7B, 0x7D, 0x29):
            i += 1
        else:
            j = i + 1
            while j < n and data[j] not in _WS and data[j] not in _DELIM:
                j += 1
            word, i = data[i:j], j
            if c == 0x2F:
                yield "name", word[1:]
            elif _NUM_RE.fullmatch(word):
                yield "num", float(word)
            else:
                yield "op", word
                if word == b"ID":  # satır içi resim: ikili veri EI'ye kadar atlanır
                    m = _EI_RE.search(data, i)
                    i = m.end() if m else n


def _content_text(data, fonts):
    """İçerik akışındaki yazılar; satır ve kelime araları konumlardan çıkarılır."""
    out, operands, arr, depth = [], [], None, 0
    cands, size, tc, tw = [], 12.0, 0.0, 0.0
    a = d = 1.0            # metin matrisinin yatay / dikey ölçeği
    lx = x = y = 0.0       # satır başı ve şu anki konum
    end = None             # son yazının bittiği (x, y)

    def show(raw):
        nonlocal x, end
        text, info = _show(raw, cands)
        if text and end and out:
            if abs(y - end[1]) > max(1.0, abs(size * d) * 0.3):
                out.append("\n")
            elif x - end[0] > abs(size * a) * SPACE_GAP:
                out.append(" ")
        out.append(text)
        x += _advance(raw, info, size, tc, tw) * a
        if text:
            end = (x, y)

    for kind, value in _tokens(data):
        if depth:  # işaretli içerik özellikleri (<< /MCID 0 >>) atlanır
            depth += {"<<": 1, ">>": -1}.get(kind, 0)
            continue
        if kind == "<<":
            depth = 1
        elif kind == "[":
            arr = []
        elif kind == "]":
            operands.append(("arr", arr or []))
            arr = None
        elif arr is not None:
            arr.append((kind, value))
        elif kind != "op":
            operands.append((kind, value))
        else:
            nums = [v for k, v in operands if k == "num"]
            if value == b"Tf" and len(operands) >= 2 and operands[-2][0] == "name":
                cands, size = fonts.get(operands[-2][1], []), nums[-1] if nums else size
            elif value == b"Tc" and nums:
                tc = nums[-1]
            elif value == b"Tw" and nums:
                tw = nums[-1]
            elif value == b"BT":
                a = d = 1.0
                lx = x = y = 0.0
            elif value in (b"Td", b"TD") and len(nums) >= 2:
                lx += nums[-2] * a
                y += nums[-1] * d
                x = lx
            elif value == b"Tm" and len(nums) >= 6:
                a, d = nums[-6] or 1.0, nums[-3] or 1.0
                lx = x = nums[-2]
                y = nums[-1]
            elif value in (b"T*", b"'", b'"'):
                y -= 1000 * d  # satır aralığı bilinmez; yeni satır sayılır
                x = lx
                if value == b'"' and len(nums) >= 2:
                    tw, tc = nums[0], nums[1]
            if value in (b"Tj", b"'", b'"'):
                show(next((v for k, v in reversed(operands) if k == "str"), b""))
            elif value == b"TJ":
                for k, v in next((v for k, v in reversed(operands) if k == "arr"), []):
                    if k == "str":
                        show(v)
                    elif k == "num":
                        x -= v / 1000 * size * a
            operands = []
    return "".join(out)


def pdf_text(data):
    """PDF baytlarından okunabilen yazı; yazı yoksa ya da okunamadıysa ''."""
    if not data.startswith(b"%PDF-") or b"/Encrypt" in data:
        return ""
    try:
        objs = _objects(data)
        fonts = _fonts(objs)
        contents = []
        for num in sorted(objs):
            head, stream = objs[num]
            if re.search(rb"/Type\s*/Page\b", head):
                m = re.search(rb"/Contents\s*(\[[^\]]*\]|\d+\s+\d+\s+R)", head)
                refs = [int(r) for r in _REF_RE.findall(m.group(1))] if m else []
                if len(refs) == 1 and objs.get(refs[0], (b"", None))[1] is None:  # içerik dizisi ayrı nesnede
                    refs = [int(r) for r in _REF_RE.findall(objs.get(refs[0], (b"", None))[0])]
                contents += [objs[r][1] for r in refs if r in objs and objs[r][1]]
            elif stream and re.search(rb"/Subtype\s*/Form\b", head):
                contents.append(stream)
        lines = []
        for stream in contents:
            for line in _content_text(stream, fonts).splitlines():
                line = " ".join(line.split())
                if line:
                    lines.append(line)
        return "\n".join(lines)[:MAX_TEXT]
    except Exception:  # alışılmadık / bozuk PDF: okunamadı say (yapay zekâ çağrılmaz, kullanıcıya söylenir)
        return ""
