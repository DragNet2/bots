import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from dotenv import load_dotenv

from vk_client import VKClient

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
dp = Dispatcher()
vk = VKClient()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Отправь мне ссылку на видео из ВКонтакте, "
        "и я перешлю его тебе."
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "Просто отправь ссылку на видео из ВК (публичное видео).\n"
        "Пример: https://vk.com/video-123456789_123456789"
    )


@dp.message()
async def handle_message(message: types.Message):
    text = message.text or ""

    # Check if it's a VK video link
    if "vk.com" not in text.lower() and "vkvideo.ru" not in text.lower():
        await message.answer("Отправьте ссылку на видео из ВКонтакте.")
        return

    await message.answer("Скачиваю видео...")

    temp_path = "/tmp/vk_video.mp4"
    success = await vk.download_video(text, temp_path)
    if not success:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        await message.answer("Не удалось скачать видео. Проверьте ссылку.")
        return

    # Send to user
    try:
        with open(temp_path, "rb") as video_file:
            await message.answer_video(
                video=types.BufferedInputFile(
                    video_file.read(), filename="video.mp4"
                )
            )
    except Exception as e:
        logger.error(f"Error sending video: {e}")
        await message.answer(f"Ошибка при отправке видео: {e}")

    finally:
        # Cleanup
        if os.path.exists(temp_path):
            os.remove(temp_path)


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
