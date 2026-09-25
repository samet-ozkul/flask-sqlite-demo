"""Tarih/saat, form okuma, biçimlendirme ve Jinja filtreleri."""
import calendar
import html
import os
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import redirect, request, url_for
from markupsafe import Markup

TZ = ZoneInfo(os.environ.get("APP_TZ", "Europe/Istanbul"))

CURRENCIES = {"TRY": "₺", "USD": "$", "EUR": "€", "GBP": "£"}
MONTHS_TR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
             "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
MONTHS_TR_SHORT = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]
WEEKDAYS_TR = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
WEEKDAYS_TR_SHORT = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"]


# ---------- Tarih / saat ----------
def now_local():
    return datetime.now(TZ)


def today():
    return now_local().date()


def today_str():
    return today().isoformat()


def parse_date(value):
    """'YYYY-MM-DD' (ya da date) -> date; geçersizse None."""
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def add_months(d, months):
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def add_cycle(d, cycle):
    if cycle == "weekly":
        return d + timedelta(days=7)
    if cycle == "yearly":
        return add_months(d, 12)
    return add_months(d, 1)


def days_until(value):
    d = parse_date(value)
    return None if d is None else (d - today()).days


def month_bounds(year, month):
    """Ayın ilk günü ve bir sonraki ayın ilk günü (ISO string)."""
    first = date(year, month, 1)
    return first.isoformat(), add_months(first, 1).isoformat()


# ---------- Yönlendirme ----------
def safe_path(url):
    """Sadece site içi göreli yollar ('/...'); açık yönlendirmeyi engeller."""
    if url and url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return url
    return None


def redirect_back(endpoint, **values):
    """Formdaki/URL'deki `next` (ya da next=dashboard) varsa oraya, yoksa endpoint'e yönlendirir."""
    target = request.form.get("next") or request.args.get("next")
    if target == "dashboard":
        return redirect(url_for("dashboard.index"))
    return redirect(safe_path(target) or url_for(endpoint, **values))


# ---------- Form okuma ----------
def form_str(name, max_len=500, default=""):
    return (request.form.get(name) or default).strip()[:max_len]


def form_bool(name):
    return 1 if request.form.get(name) in ("1", "on", "true", "yes") else 0


def parse_number(value):
    """'1.234,56' / '1234.56' / '12,5' -> float; boş/geçersizse None."""
    if value is None:
        return None
    s = str(value).strip().replace(" ", "").replace("₺", "")
    if not s:
        return None
    if "," in s and "." in s:
        # hangisi sondaysa o ondalık ayırıcıdır
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
        # Sadece nokta ve 3'lü gruplar: Türkçe binlik ayırıcı ("1.000", "12.500", "1.234.567").
        # fmt_number 1000'i "1.000" gösterdiği için formdan böyle geri gelir.
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def form_float(name):
    return parse_number(request.form.get(name))


def form_int(name):
    n = parse_number(request.form.get(name))
    return None if n is None else int(n)


def form_date(name):
    """<input type=date> değeri -> ISO string ya da None."""
    d = parse_date(request.form.get(name))
    return d.isoformat() if d else None


def form_choice(name, choices, default):
    value = request.form.get(name)
    return value if value in choices else default


def split_tags(value):
    return [t.strip() for t in (value or "").split(",") if t.strip()]


def normalize_tags(value):
    seen = []
    for t in split_tags(value):
        t = t.lower()[:30]
        if t not in seen:
            seen.append(t)
    return ", ".join(seen[:10])


# ---------- Biçimlendirme ----------
def fmt_number(value, decimals=2):
    if value is None:
        return ""
    s = f"{value:,.{decimals}f}"  # 1,234.56
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")  # 1.234,56
    if decimals and s.endswith("," + "0" * decimals):
        s = s[: -(decimals + 1)]
    return s


def fmt_money(value, currency="TRY"):
    if value is None:
        return "—"
    symbol = CURRENCIES.get(currency, currency)
    return f"{fmt_number(value)} {symbol}"


def fmt_date(value, with_weekday=False):
    d = parse_date(value)
    if d is None:
        return ""
    s = f"{d.day} {MONTHS_TR_SHORT[d.month - 1]}"
    if d.year != today().year:
        s += f" {d.year}"
    if with_weekday:
        s += f", {WEEKDAYS_TR_SHORT[d.weekday()]}"
    return s


def rel_days(value):
    n = days_until(value)
    if n is None:
        return ""
    if n == 0:
        return "bugün"
    if n == 1:
        return "yarın"
    if n == -1:
        return "dün"
    if n > 0:
        return f"{n} gün sonra"
    return f"{-n} gün geçti"


def urgency(value, warn_days=3):
    """CSS sınıfı: overdue / soon / later / ''"""
    n = days_until(value)
    if n is None:
        return ""
    if n < 0:
        return "overdue"
    if n <= warn_days:
        return "soon"
    return "later"


def local_dt(value, fmt="%d.%m.%Y %H:%M"):
    """SQLite CURRENT_TIMESTAMP (UTC) -> yerel saat metni."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace(" ", "T"))
    except ValueError:
        return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ).strftime(fmt)


def fmt_size(num_bytes):
    n = float(num_bytes or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{fmt_number(n, 0 if unit == 'B' else 1)} {unit}"
        n /= 1024


# ---------- Güvenli mini Markdown ----------
_INLINE = [
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*(.+?)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"), r"<em>\1</em>"),
    (re.compile(r"~~(.+?)~~"), r"<del>\1</del>"),
    (re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)"),
     r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>'),
    (re.compile(r'(?<!["=>])(https?://[^\s<]+)'),
     r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>'),
]


def _inline(text):
    for pattern, repl in _INLINE:
        text = pattern.sub(repl, text)
    return text


def render_markdown(text):
    """Önce tüm HTML kaçışlanır, sonra sınırlı Markdown uygulanır (XSS güvenli)."""
    out, in_list = [], None
    for raw in html.escape(text or "").splitlines():
        line = raw.rstrip()
        m_task = re.match(r"^\s*[-*] \[( |x|X)\] (.*)$", line)
        m_item = re.match(r"^\s*[-*] (.*)$", line)
        m_num = re.match(r"^\s*\d+[.)] (.*)$", line)
        kind = "ul" if (m_task or m_item) else "ol" if m_num else None
        if in_list and kind != in_list:
            out.append(f"</{in_list}>")
            in_list = None
        if kind and not in_list:
            out.append(f"<{kind}>")
            in_list = kind
        if m_task:
            checked = "checked" if m_task.group(1).lower() == "x" else ""
            out.append(f'<li class="task"><input type="checkbox" disabled {checked}> {_inline(m_task.group(2))}</li>')
        elif m_item:
            out.append(f"<li>{_inline(m_item.group(1))}</li>")
        elif m_num:
            out.append(f"<li>{_inline(m_num.group(1))}</li>")
        elif re.match(r"^#{1,3} ", line):
            level = min(len(line) - len(line.lstrip("#")), 3) + 2  # h3-h5
            out.append(f"<h{level}>{_inline(line.lstrip('#').strip())}</h{level}>")
        elif line.strip() == "":
            out.append("")
        else:
            out.append(f"<p>{_inline(line)}</p>")
    if in_list:
        out.append(f"</{in_list}>")
    return Markup("\n".join(out))


def init_app(app):
    app.jinja_env.filters.update(
        money=fmt_money,
        num=fmt_number,
        date_tr=fmt_date,
        rel_days=rel_days,
        days_until=days_until,
        urgency=urgency,
        localdt=local_dt,
        md=render_markdown,
        tags=split_tags,
        size=fmt_size,
    )
    app.jinja_env.globals.update(
        today_str=today_str,
        CURRENCIES=CURRENCIES,
        MONTHS_TR=MONTHS_TR,
        WEEKDAYS_TR=WEEKDAYS_TR,
    )
