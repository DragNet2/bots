import asyncio
import logging
import os
import subprocess
import threading
from html import escape
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

PROGRESS_EMOJI = ["▏", "▎", "▍", "▌", "▋", "▊", "▉", "▘", "▝", "▀"]


def progress_bar(current: float, total: float, width: int = 20) -> str:
    """Create a simple progress bar."""
    if total == 0:
        return "[▓▓▓▓▓▓▓▓▓▓] 100%"
    filled = int(width * current / total) if total > 0 else 0
    bar = "▓" * filled + "░" * (width - filled)
    percent = int(100 * current / total) if total > 0 else 0
    return f"[{bar}] {percent}%"


def format_size(size_bytes: float) -> str:
    """Format bytes to human readable size."""
    if size_bytes < 1024:
        return f"{size_bytes:.0f}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes/1024:.1f}KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes/1024/1024:.1f}MB"
    else:
        return f"{size_bytes/1024/1024/1024:.2f}GB"


async def download_with_progress(url: str, output_path: str, progress_callback):
    """Download video using yt-dlp with progress reporting."""
    venv_bin = os.path.dirname(os.path.abspath(__file__)) + "/venv/bin"
    yt_dlp_path = f"{venv_bin}/yt-dlp"

    process = subprocess.Popen(
        [yt_dlp_path, "-c", "-f", "best[ext=mp4]/best", "-o", output_path, url],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    downloaded = 0
    total = 0
    last_line = ""

    while True:
        line = process.stdout.readline()
        if not line and process.poll() is not None:
            break

        line = line.strip()
        if line:
            last_line = line

        # Parse yt-dlp output for progress
        # Look for patterns like: [download]  10.5% of 13.57MiB at  1.23MiB/s ETA 00:30
        if "[download]" in line and "%" in line:
            try:
                # Extract percentage
                pct_idx = line.index("%")
                pct_str = line[pct_idx-5:pct_idx].strip()
                downloaded = float(pct_str.replace(",", "."))

                # Extract total size
                if "of" in line and "at" in line:
                    size_str = line.split("of")[1].split("at")[0].strip()
                    if "MiB" in size_str:
                        total = float(size_str.replace("MiB", "")) * 1024 * 1024
                    elif "GiB" in size_str:
                        total = float(size_str.replace("GiB", "")) * 1024 * 1024 * 1024
                    elif "KB" in size_str:
                        total = float(size_str.replace("KB", "")) * 1024

                # Calculate downloaded bytes
                current_bytes = downloaded / 100 * total if total > 0 else 0

                await progress_callback(downloaded, 100, current_bytes, total)
            except:
                pass

    return_code = process.wait()
    return return_code == 0


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

    # Initial message
    status_msg = await message.answer("⏳ Приступаю к задаче...")

    temp_path = "/tmp/vk_video.mp4"
    video_url = None
    download_success = False

    try:
        # Get video URL first
        video_url = await vk.get_video_url(text)

        if not video_url:
            await status_msg.edit_text("❌ Не удалось получить ссылку на видео. Проверьте ссылку.")
            return

        # Format video URL as HTML link
        video_link = f'<a href="{escape(video_url)}">🔗 Ссылка на видео</a>'

        # Download with progress
        async def on_progress(downloaded: float, total: float, current_bytes: float, total_bytes: float):
            bar = progress_bar(downloaded, 100)
            size_str = ""
            if total_bytes > 0:
                size_str = f" ({format_size(current_bytes)} / {format_size(total_bytes)})"
            await status_msg.edit_text(
                f"{video_link}\n\n"
                f"⏬ Приступил к скачиванию...\n"
                f"{bar}{size_str}",
                parse_mode="HTML"
            )

        success = await download_with_progress(text, temp_path, on_progress)

        if not success:
            await status_msg.edit_text(
                f"{video_link}\n\n"
                f"❌ Скачивание прервано. Повторите команду с той же ссылкой для докачки.\n\n"
                f"(Файл сохранён для возобновления)",
                parse_mode="HTML"
            )
            return

        download_success = True

        # Check file exists
        if not os.path.exists(temp_path):
            await status_msg.edit_text("❌ Файл не был создан.")
            return

        file_size = os.path.getsize(temp_path)

        # Send video with progress
        await status_msg.edit_text(
            f"{video_link}\n\n"
            f"✅ Скачивание завершено ({format_size(file_size)})\n\n"
            f"⏫ Приступаю к загрузке видео в чат...",
            parse_mode="HTML"
        )

        try:
            with open(temp_path, "rb") as video_file:
                await message.answer_video(
                    video=types.BufferedInputFile(
                        video_file.read(), filename="video.mp4"
                    )
                )
            await status_msg.edit_text(
                f"{video_link}\n\n"
                f"✅ Скачивание завершено ({format_size(file_size)})\n\n"
                f"✅ Загрузка в чат завершена!",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error sending video: {e}")
            await status_msg.edit_text(f"❌ Ошибка при отправке видео: {e}")

    except Exception as e:
        logger.error(f"Error: {e}")
        try:
            await status_msg.edit_text(f"❌ Ошибка: {e}")
        except:
            await message.answer(f"❌ Ошибка: {e}")

    finally:
        # Only delete file if download was successful (sent to user)
        if download_success and os.path.exists(temp_path):
            os.remove(temp_path)


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
