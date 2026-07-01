"""
Yandex Music Downloader Bot - Telegram Bot for downloading music from Yandex.Music.

Structure:
    bot.py              - Entry point, bot initialization
    config.py          - Configuration (tokens, settings)
    handlers/          - Message and callback handlers
        menu.py        - Main menu
        download.py    - Download operations
    services/          - Business logic
        yandex_downloader.py - Yandex Music API wrapper
    keyboards/         - Inline keyboards
        inline.py      - Keyboard layouts
"""

import logging
import asyncio
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

from aiogram import Bot, Dispatcher
from aiogram.filters import Command

from config import BOT_TOKEN
from services.yandex_downloader import YandexDownloader

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def on_startup(bot: Bot):
    downloader = YandexDownloader()
    if downloader.connect():
        logger.info("Yandex Music client connected successfully")
    else:
        logger.error("Failed to connect to Yandex Music")
    admin_id = os.environ.get("ADMIN_ID", "").strip()
    if admin_id.isdigit():
        await bot.send_message(chat_id=int(admin_id), text="Bot started")


def main():
    logger.info("Starting Yandex Music Downloader Bot...")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    from handlers.menu import router as menu_router
    from handlers.download import router as download_router

    dp.include_router(menu_router)
    dp.include_router(download_router)

    logger.info("Bot initialized. Starting polling...")

    asyncio.run(dp.start_polling(bot, on_startup=on_startup))


if __name__ == "__main__":
    main()
