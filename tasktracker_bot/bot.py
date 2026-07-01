"""
Task Tracker - Main Bot
"""
import logging
import asyncio

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from config import BOT_TOKEN
from db import init_db
from handlers import router

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Main entry point."""
    logger.info("Initializing Task Tracker bot...")

    init_db()
    logger.info("Database initialized")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("Starting polling...")

    asyncio.run(dp.start_polling(bot))


if __name__ == "__main__":
    main()
