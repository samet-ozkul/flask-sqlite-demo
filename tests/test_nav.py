"""Üst menü: kısayollar (☆), ☰ Modüller menüsü, alt çubuk, hızlı geçiş verisi.

Çalıştır: .venv/Scripts/python tests/test_nav.py
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import Client, make_app  # noqa: E402

from markupsafe import escape  # noqa: E402

app = make_app()

from pano.auth import create_user  # noqa: E402
from pano.db import query_one  # noqa: E402
from pano.modules import MAX_PINS, MODULES  # noqa: E402

with app.app_context():
    create_user("ayse", "ayse12345")
ADMIN = Client(app)
AYSE = Client(app, "ayse", "ayse12345")


def pins(client):
    page = client.text("/")
    nav = page.split('<nav class="topnav"', 1)[1].split('<details class="navmenu', 1)[0]
    return re.findall(r'title="([^"]+)">', nav), page


def test_defaults_and_menu():
    titles, page = pins(ADMIN)
    assert titles == ["Listeler", "Notlar", "Harcamalar", "Takvim"]
    # Alt çubuk: Pano + ilk 3 kısayol + Menü
    bottom = page.split('class="bottomnav"', 1)[1].split("</nav>", 1)[0]
    assert re.findall(r"<em>([^<]+)</em>", bottom) == ["Listeler", "Notlar", "Harcamalar"]
    # ☰ Modüller: her modül bir kez, gruplu
    menu = page.split('class="navmenu-panel"', 1)[1].split("</details>", 1)[0]
    for _key, _ep, title, _icon, _group in MODULES:
        assert f'data-name="{escape(title)}"' in menu, title   # "Kısa Link & QR" -> &amp;
    assert "Araçlar" in menu or not any(m[4] == "Araçlar" for m in MODULES)
    # Hızlı geçiş verisi tüm modüller + hesap sayfaları; güvenli JSON
    data = json.loads(page.split('id="quickjump-data">', 1)[1].split("</script>", 1)[0])
    assert {d["t"] for d in data} >= {m[2] for m in MODULES} | {"Ayarlar", "Çöp kutusu"}
    # Kısayol olmayan bir modülde ☰ etiketi o modülü gösterir
    page = ADMIN.text("/kurlar/")
    summary = page.split('<details class="navmenu dropdown">', 1)[1].split("</summary>", 1)[0]
    assert "Kurlar" in summary and 'class="active"' in summary
    page = ADMIN.text("/notlar/")
    summary = page.split('<details class="navmenu dropdown">', 1)[1].split("</summary>", 1)[0]
    assert "Modüller" in summary  # kısayoldaysa ☰ etiketi değişmez, kısayol vurgulanır
    assert 'class="active" title="Notlar"' in page
    print("  defaults/menu OK")


def test_pin_unpin():
    r = ADMIN.post("/menu/sabitle", data={"module": "rates", "next": "/kurlar/"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/kurlar/")
    titles, _ = pins(ADMIN)
    assert titles[-1] == "Kurlar"
    ADMIN.post("/menu/sabitle", data={"module": "notes"})  # kaldır
    titles, _ = pins(ADMIN)
    assert "Notlar" not in titles and titles == ["Listeler", "Harcamalar", "Takvim", "Kurlar"]
    ADMIN.post("/menu/sabitle", data={"module": "yok-boyle"})  # bilinmeyen yok sayılır
    for key in ("habits", "health", "journal"):
        ADMIN.post("/menu/sabitle", data={"module": key})
    r = ADMIN.post("/menu/sabitle", data={"module": "recipes"}, follow_redirects=True)
    assert f"En fazla {MAX_PINS} kısayol" in r.get_data(as_text=True)
    assert len(pins(ADMIN)[0]) == MAX_PINS
    assert pins(AYSE)[0] == ["Listeler", "Notlar", "Harcamalar", "Takvim"]  # başkasını etkilemez
    assert "★" in ADMIN.text("/menu") and "☆" in ADMIN.text("/menu")
    print("  pin/unpin OK")


def test_settings_order():
    page = ADMIN.text("/ayarlar/")
    assert 'id="kisayollar"' in page
    ADMIN.post("/ayarlar/kisayollar", data={"order": ["rates", "lists", "habits", "agenda"],
                                           "show": ["rates", "lists", "agenda"]})
    assert query_one_app("SELECT nav_pins FROM users WHERE id = 1")["nav_pins"] == "rates,lists,agenda"
    assert pins(ADMIN)[0] == ["Kurlar", "Listeler", "Takvim"]
    ADMIN.post("/ayarlar/kisayollar", data={"order": [], "show": []})
    assert pins(ADMIN)[0] == []  # hepsi kaldırılabilir; ☰ Modüller yine var
    assert "☰" in ADMIN.text("/")
    ADMIN.post("/ayarlar/kisayollar", data={"reset": "1"})
    assert query_one_app("SELECT nav_pins FROM users WHERE id = 1")["nav_pins"] is None
    print("  settings OK")


def test_logged_out():
    page = app.test_client().get("/giris").get_data(as_text=True)
    assert "navmenu" not in page and "quickjump" not in page
    print("  logged out OK")


def query_one_app(sql, args=()):
    with app.app_context():
        return query_one(sql, args)


if __name__ == "__main__":
    test_defaults_and_menu()
    test_pin_unpin()
    test_settings_order()
    test_logged_out()
    print("OK")
