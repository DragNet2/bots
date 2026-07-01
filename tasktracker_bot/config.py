"""
Task Tracker - Configuration
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATABASE_PATH = os.path.join(BASE_DIR, "tasks.db")

ADMIN_USER_IDS = [233590599]

GROQ_MODEL = "llama-3.3-70b-versatile"

WEB_PORT = 5001


def _load_env_file(path: str):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = value


_load_env_file(os.path.join(BASE_DIR, ".env"))

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")


TASKTRACKER_SECRET_KEY = os.getenv("TASKTRACKER_SECRET_KEY", "")
TASKTRACKER_ALLOWED_EMAILS = [
    e.strip().lower()
    for e in os.getenv("TASKTRACKER_ALLOWED_EMAILS", "").split(",")
    if e.strip()
]

TASKTRACKER_GOOGLE_CLIENT_ID = os.getenv("TASKTRACKER_GOOGLE_CLIENT_ID", "")
TASKTRACKER_GOOGLE_CLIENT_SECRET = os.getenv("TASKTRACKER_GOOGLE_CLIENT_SECRET", "")
TASKTRACKER_GOOGLE_REDIRECT_URI = os.getenv(
    "TASKTRACKER_GOOGLE_REDIRECT_URI", "https://vnbm.ru/auth/google/callback"
)
