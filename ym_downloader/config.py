"""
Configuration for Yandex Music Downloader Bot.
"""

import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

YANDEX_TOKEN = os.getenv("YANDEX_TOKEN", "")

YANDEX_API_BASE = "https://api.music.yandex.net"

DOWNLOAD_DIR = "/tmp/ym_downloads"

MAX_FILE_SIZE_MB = 50

RATE_LIMIT_DELAY = 3
