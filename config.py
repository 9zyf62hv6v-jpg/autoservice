import os
import sys

# Если приложение собрано в .exe — данные храним рядом с .exe,
# иначе — рядом с проектом.
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = "change-me-later-please"
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(BASE_DIR, "autoservice.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False