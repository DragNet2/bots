import asyncio
import logging
import os
import subprocess
import uuid
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

# Download queue
download_queue = asyncio.Queue()
is_downloading = False
queue_task = None


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

    while True:
        line = process.stdout.readline()
        if not line and process.poll() is not None:
            break

        line = line.strip()

        if "[download]" in line and "%" in line:
            try:
                pct_idx = line.index("%")
                pct_str = line[pct_idx-5:pct_idx].strip()
                downloaded = float(pct_str.replace(",", "."))

                if "of" in line and "at" in line:
                    size_str = line.split("of")[1].split("at")[0].strip()
                    if "MiB" in size_str:
                        total = float(size_str.replace("MiB", "")) * 1024 * 1024
                    elif "GiB" in size_str:
                        total = float(size_str.replace("GiB", "")) * 1024 * 1024 * 1024
                    elif "KB" in size_str:
                        total = float(size_str.replace("KB", "")) * 1024

                current_bytes = downloaded / 100 * total if total > 0 else 0
                await progress_callback(downloaded, 100, current_bytes, total)
            except:
                pass

    return_code = process.wait()
    return return_code == 0


async def process_download(message: types.Message, url: str):
    """Process a single video download."""
    global is_downloading

    video_url = None
    download_success = False
    task_id = str(uuid.uuid4())[:8]
    temp_path = f"/tmp/vk_video_{task_id}.mp4"

    try:
        video_url = await vk.get_video_url(url)

        if not video_url:
            await message.answer("❌ Не удалось получить ссылку на видео. Проверьте ссылку.")
            return

        video_link = f'<a href="{escape(video_url)}">🔗 Ссылка на видео</a>'

        async def on_progress(downloaded, total, current_bytes, total_bytes):
            bar = progress_bar(downloaded, 100)
            size_str = ""
            if total_bytes > 0:
                size_str = f" ({format_size(current_bytes)} / {format_size(total_bytes)})"
            await message.edit_text(
                f"{video_link}\n\n"
                f"⏬ Скачивание...\n"
                f"{bar}{size_str}",
                parse_mode="HTML"
            )

        success = await download_with_progress(url, temp_path, on_progress)

        if not success:
            await message.edit_text(
                f"{video_link}\n\n"
                f"❌ Скачивание прервано. Повторите команду с той же ссылкой для докачки.\n\n"
                f"(Файл сохранён для возобновления)",
                parse_mode="HTML"
            )
            return

        download_success = True

        if not os.path.exists(temp_path):
            await message.edit_text("❌ Файл не был создан.")
            return

        file_size = os.path.getsize(temp_path)

        await message.edit_text(
            f"{video_link}\n\n"
            f"✅ Скачивание завершено ({format_size(file_size)})\n\n"
            f"⏫ Загружаю в чат...",
            parse_mode="HTML"
        )

        try:
            with open(temp_path, "rb") as video_file:
                await message.answer_video(
                    video=types.BufferedInputFile(
                        video_file.read(), filename="video.mp4"
                    )
                )
            await message.edit_text(
                f"{video_link}\n\n"
                f"✅ Скачивание завершено ({format_size(file_size)})\n\n"
                f"✅ Загрузка в чат завершена!",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Error sending video: {e}")
            await message.edit_text(f"❌ Ошибка при отправке видео: {e}")

    except Exception as e:
        logger.error(f"Error: {e}")
        try:
            await message.edit_text(f"❌ Ошибка: {e}")
        except:
            await message.answer(f"❌ Ошибка: {e}")

    finally:
        if download_success and os.path.exists(temp_path):
            os.remove(temp_path)

    is_downloading = False


async def queue_worker():
    """Background worker that processes the download queue."""
    global is_downloading

    while True:
        try:
            # Wait for next item in queue
            message, url = await download_queue.get()

            is_downloading = True

            try:
                await message.edit_text("⏳ Начинаю скачивание...")
                await process_download(message, url)
            finally:
                download_queue.task_done()

                # If queue has more items, continue processing
                if not download_queue.empty():
                    is_downloading = False
                    continue
                else:
                    is_downloading = False
                    # Wait a bit before checking queue again
                    await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Queue worker error: {e}")
            is_downloading = False
            await asyncio.sleep(1)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Отправь мне ссылку на видео из ВКонтакте, "
        "и я перешлю его тебе.\n\n"
        "Можно отправлять несколько ссылок — они будут обработаны по очереди."
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "Просто отправь ссылку на видео из ВК (публичное видео).\n"
        "Можно отправлять несколько ссылок — они встанут в очередь.\n\n"
        "Пример: https://vk.com/video-123456789_123456789"
    )


@dp.message(Command("queue"))
async def cmd_queue(message: types.Message):
    """Check current queue status."""
    if download_queue.empty():
        if is_downloading:
            await message.answer("🎬 Сейчас скачивается видео. Очередь пуста.")
        else:
            await message.answer("✅ Очередь пуста, бот в режиме ожидания.")
    else:
        count = download_queue.qsize()
        await message.answer(f"📋 В очереди: {count} видео")


@dp.message()
async def handle_message(message: types.Message):
    global queue_task

    text = message.text or ""

    # Check if it's a VK video link
    if "vk.com" not in text.lower() and "vkvideo.ru" not in text.lower():
        await message.answer("Отправьте ссылку на видео из ВКонтакте.")
        return

    # Start queue worker if not running
    if queue_task is None or queue_task.done():
        queue_task = asyncio.create_task(queue_worker())

    # Add to queue
    await download_queue.put((message, text))

    queue_size = download_queue.qsize()

    if queue_size == 1:
        await message.answer("⏳ Видео в очереди на скачивание...")
    else:
        await message.answer(f"📋 Видео добавлено в очередь. Позиция: {queue_size}")


async def main():
    global queue_task
    queue_task = asyncio.create_task(queue_worker())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
