"""🧮 Hesaplayıcılar: kredi, mevduat, KDV, yüzde, tarih, birim, döviz/altın, hesap bölüşme, yakıt.

Bütün hesaplar tarayıcıda (static/calculators.js), yazdıkça güncellenir; sunucu sadece sayfayı ve güncel
kurları gömer (kur alınamazsa döviz hesaplayıcısı "kur alınamadı" der). Veritabanı tablosu yok: son girilen
değerler tarayıcının localStorage'ında durur.
"""
from flask import Blueprint, render_template

from ..auth import login_required
from .rates import CURRENCY_NAMES, current_values

bp = Blueprint("calculators", __name__, url_prefix="/hesapla")

# (çapa, ikon, kısa ad) — sayfa üstündeki atlama çipleri; kartlar şablonda aynı sırada
SECTIONS = [
    ("kredi", "🏦", "Kredi"), ("mevduat", "🐷", "Mevduat"), ("kdv", "🧾", "KDV"), ("yuzde", "💯", "Yüzde"),
    ("tarih", "📅", "Tarih"), ("birim", "📏", "Birim"), ("doviz", "💱", "Döviz / altın"),
    ("bolus", "🍽️", "Hesap bölüşme"), ("yakit", "⛽", "Yakıt"),
]
SYMBOLS = {"TRY": "₺", "USD": "$", "EUR": "€", "GBP": "£", "XAU": "gr altın"}


@bp.route("/")
@login_required
def index():
    values, rate_date = current_values()
    # Sayfaya gömülen kurlar: 1 birimin TL karşılığı (XAU = gram altın); hiç kur yoksa null
    rates = {**values, "date": rate_date} if values else None
    currencies = [("TRY", "₺ Türk lirası")] + [(c, CURRENCY_NAMES[c]) for c in CURRENCY_NAMES if c in values]
    return render_template("calculators/index.html", sections=SECTIONS, rates=rates, values=values,
                           rate_date=rate_date, currencies=currencies, symbols=SYMBOLS)
