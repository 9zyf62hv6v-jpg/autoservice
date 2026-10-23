import os
import sys
import threading
import webbrowser

from app import app, db, _auto_migrate


def open_browser():
    webbrowser.open("http://127.0.0.1:5000")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        _auto_migrate()

    if getattr(sys, "frozen", False):
        threading.Timer(1.5, open_browser).start()

    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)