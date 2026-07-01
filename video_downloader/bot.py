import asyncio
import logging
import os
import httpx
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
    if "vk.com" not in text.lower():
        await message.answer("Отправьте ссылку на видео из ВКонтакте.")
        return

    await message.answer("Скачиваю видео...")

    video_url = await vk.get_video_url(text)
    if not video_url:
        await message.answer("Не удалось получить видео. Проверьте ссылку.")
        return

    # Download video
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(video_url)
            response.raise_for_status()

            # Save to temp file
            temp_path = "/tmp/vk_video.mp4"
            with open(temp_path, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    f.write(chunk)

        # Send to user
        with open(temp_path, "rb") as video_file:
            await message.answer_video(
                video=types.BufferedInputFile(
                    video_file.read(), filename="video.mp4"
                )
            )

    except Exception as e:
        logger.error(f"Error downloading video: {e}")
        await message.answer(f"Ошибка при скачивании: {e}")

    finally:
        # Cleanup
        if os.path.exists(temp_path):
            os.remove(temp_path)


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
