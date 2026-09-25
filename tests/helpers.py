"""Test yardımcıları: geçici DB ile uygulama + giriş yapmış istemci."""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_app():
    tmp = tempfile.mkdtemp(prefix="pano-test-")
    os.environ["DATABASE_PATH"] = os.path.join(tmp, "test.db")
    os.environ["UPLOAD_DIR"] = os.path.join(tmp, "uploads")
    os.environ["ADMIN_USERNAME"] = "admin"
    os.environ["ADMIN_PASSWORD"] = "admin12345"
    from pano import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app


def csrf(resp):
    m = re.search(r'name="_csrf" value="(\w+)"', resp.get_data(as_text=True))
    return m.group(1) if m else None


class Client:
    """Giriş yapmış test istemcisi; post() CSRF'i otomatik ekler."""

    def __init__(self, app, username="admin", password="admin12345"):
        self.c = app.test_client()
        r = self.c.get("/giris")
        self.token = csrf(r)
        r = self.c.post("/giris", data={"_csrf": self.token, "username": username, "password": password})
        assert r.status_code == 302, f"giriş başarısız: {r.status_code}"
        self.token = csrf(self.c.get("/notlar/"))

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, data=None, **kw):
        data = dict(data or {})
        data["_csrf"] = self.token
        return self.c.post(url, data=data, **kw)

    def text(self, url):
        r = self.get(url)
        assert r.status_code == 200, f"{url} -> {r.status_code}"
        return r.get_data(as_text=True)
