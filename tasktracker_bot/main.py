"""
Task Tracker - Main Launcher
Runs both Telegram bot and Web interface
"""
import logging
import asyncio
import threading
import sys
import os
import time

sys.path.insert(0, os.path.dirname(__file__))

from config import BOT_TOKEN
from db import init_db
from handlers import router as handler_router
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_web():
    """Run Flask web server in separate thread."""
    from web import app, WEB_PORT
    logger.info(f"Starting web interface on port {WEB_PORT}...")
    app.run(host="0.0.0.0", port=WEB_PORT, debug=False, use_reloader=False)


def main():
    """Main entry point - starts both bot and web."""
    logger.info("=" * 50)
    logger.info("Task Tracker Starting...")
    logger.info("=" * 50)

    init_db()
    logger.info("Database initialized")

    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()
    logger.info("Web interface thread started")

    logger.info("Bot: @bredeshok_bot")
    while True:
        try:
            logger.info("Initializing Telegram bot...")
            bot = Bot(token=BOT_TOKEN)
            dp = Dispatcher(storage=MemoryStorage())
            dp.include_router(handler_router)
            logger.info("Starting Telegram polling...")
            asyncio.run(dp.start_polling(bot))
        except Exception:
            logger.exception("Telegram bot crashed")
            time.sleep(5)


if __name__ == "__main__":
    main()
