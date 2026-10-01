"""Hesaplayıcılar: sayfa, giriş zorunluluğu, bölümler, gömülü kurlar; hesapların kendisi Node betiğinde.

Hesaplar tarayıcıda (static/calculators.js) yapıldığı için burada sayfa ve kur gömme denenir;
saf JS fonksiyonları tests/calculators_check.js ile (Node varsa) çalıştırılır.
Çalıştır: .venv/Scripts/python tests/test_calculators.py
"""
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

app = make_app()

import pano.external as ext  # noqa: E402

RATES = [{"USD": 48.85, "EUR": 55.52, "GBP": 64.6, "date": "2026-09-24"}]
GOLD = [4210.5]
ext.rates = lambda: RATES[0]
ext.gold_gram_try = lambda: GOLD[0]

C = Client(app)
RAW = app.test_client()
SECTIONS = ["kredi", "mevduat", "kdv", "yuzde", "tarih", "birim", "doviz", "bolus", "yakit"]


def embedded(page):
    return json.loads(page.split('id="calc-rates">')[1].split("</script>")[0])


def test_login_required():
    r = RAW.get("/hesapla/")
    assert r.status_code == 302 and "/giris" in r.headers["Location"]
    print("  login OK")


def test_page():
    h = C.text("/hesapla/")
    assert "calculators.js" in h and "Hesaplayıcılar" in h
    for s in SECTIONS:
        assert f'id="{s}"' in h and f'href="#{s}"' in h, s
    for form in SECTIONS:
        assert f'data-calc="{form}"' in h, form
    assert "KKDF %15 + BSMV %15" in h and "Ödeme planı" in h and "bankandan kontrol et" in h
    assert "KDV dahil fiyattan" in h and "KDV hariç fiyattan" in h and "resmi tatiller" in h
    # Menüde yeni "Araçlar" grubu
    menu = C.text("/menu")
    assert "Araçlar" in menu and "/hesapla/" in menu
    print("  page OK")


def test_rates_embedded():
    h = C.text("/hesapla/")
    assert embedded(h) == {"USD": 48.85, "EUR": 55.52, "GBP": 64.6, "XAU": 4210.5, "date": "2026-09-24"}
    assert '<option value="XAU"' in h and "1 $ = 48,85 ₺" in h and "1 gr altın = 4.210,50 ₺" in h
    assert "Kur alınamadı" not in h
    # Altın yoksa sadece döviz
    GOLD[0] = None
    h = C.text("/hesapla/")
    assert "XAU" not in embedded(h) and '<option value="XAU"' not in h and '<option value="USD"' in h
    # Hiç kur yoksa sayfa yine açılır, döviz kartı "kur alınamadı" der
    RATES[0] = None
    h = C.text("/hesapla/")
    assert embedded(h) is None and "Kur alınamadı" in h and 'data-calc="doviz"' not in h
    assert 'data-calc="kredi"' in h and 'id="doviz"' in h
    RATES[0] = {"USD": 48.85, "EUR": 55.52, "GBP": 64.6, "date": "2026-09-24"}
    GOLD[0] = 4210.5
    print("  rates OK")


def test_static_and_node():
    r = C.get("/static/calculators.js")
    assert r.status_code == 200 and b"module.exports" in r.get_data()
    r.close()
    node = shutil.which("node")
    if not node:
        print("  node yok, JS denetimi atlandı")
        return
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run([node, os.path.join(root, "tests", "calculators_check.js")],
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "OK", out.stdout + out.stderr
    print("  node OK")


if __name__ == "__main__":
    test_login_required()
    test_page()
    test_rates_embedded()
    test_static_and_node()
    print("OK")
