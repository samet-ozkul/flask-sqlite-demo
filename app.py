# WSGI giriş noktası: PythonAnywhere `from app import app as application`, Render `gunicorn app:app`
import os

from pano import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
