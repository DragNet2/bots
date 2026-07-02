import asyncio
import logging
import os
import re
import subprocess
import uuid
from html import escape
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

from vk_client import VKClient

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
dp = Dispatcher()
vk = VKClient()

# Video download queue
video_queue = asyncio.Queue()
is_downloading = False
queue_task = None

# Active torrents tracking
active_torrents = {}  # task_id -> {name, size, status, download_dir, chat_id, message_id}

# Torrent download directory
TORRENT_DIR = "/tmp/torrents"
os.makedirs(TORRENT_DIR, exist_ok=True)


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


async def transcode_video(input_path: str, output_path: str, max_size_mb: int = 1900, progress_callback=None):
    """Transcode video to fit within max_size_mb using FFmpeg."""
    import re

    # Get input duration
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", input_path],
        capture_output=True,
        text=True,
        timeout=30,
    )
    duration = float(probe.stdout.strip()) if probe.returncode == 0 else 0

    # Calculate target bitrate: target_size / duration * 8 (bits per byte)
    target_bitrate = f"{(max_size_mb * 8 * 1000) // duration}k" if duration > 0 else "2000k"

    process = subprocess.Popen(
        [
            "ffmpeg", "-y", "-i", input_path,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-b:v", target_bitrate,
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            "-progress", "pipe:1",
            output_path
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    last_update = 0
    while True:
        line = process.stdout.readline()
        if not line and process.poll() is not None:
            break

        line = line.decode().strip()
        if line.startswith("out_time_ms=") or (duration > 0 and "time=" in line):
            try:
                if duration > 0:
                    # Parse time=00:01:23.45 format
                    time_match = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
                    if time_match:
                        hours = int(time_match.group(1))
                        minutes = int(time_match.group(2))
                        seconds = float(time_match.group(3))
                        current_time = hours * 3600 + minutes * 60 + seconds
                        percent = min(100, (current_time / duration) * 100)
                        if progress_callback and (asyncio.get_event_loop().time() - last_update > 5):
                            await progress_callback(percent, 100)
                            last_update = asyncio.get_event_loop().time()
            except:
                pass

    return_code = process.wait()
    return return_code == 0


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


async def get_video_info_from_url(url: str):
    """Extract video info from various video hosting sites."""
    import re
    import asyncio

    url_lower = url.lower()

    # VK video (various domains with VK video IDs)
    vk_pattern = r'vk\.com|vk\.io|vk\.ru|userapi\.com|mp4upload\.com'
    if re.search(vk_pattern, url_lower):
        return await vk.get_video_info(url)

    # sex.spreee.name - uses embed-player.space CDN
    if 'sex.spreee.name' in url_lower or 'embed-player.space' in url_lower:
        try:
            import subprocess
            result = subprocess.run(
                ["curl", "-s", "-L", "-A", "Mozilla/5.0", url],
                capture_output=True,
                text=True,
                timeout=30,
            )
            html = result.stdout

            # Try to find video URL in various formats
            patterns = [
                r'["\'](https?://[^"\']+\.mp4[^"\']*)["\']',
                r'src=["\']([^"\']+)["\']\s*type=["\']video',
                r'"file"\s*:\s*["\']([^"\']+)["\']',
                r'"src"\s*:\s*["\']([^"\']+\.mp4[^"\']*)["\']',
            ]
            for pattern in patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    video_url = match.group(1).replace("\\", "")
                    # Extract title
                    title_match = re.search(r'<title>([^<]+)</title>', html)
                    title = title_match.group(1).strip() if title_match else "Video"
                    return {"url": video_url, "title": title}

            # Try iframe src
            iframe_match = re.search(r'<iframe[^>]+src=["\']([^"\']+)["\']', html)
            if iframe_match:
                iframe_url = iframe_match.group(1)
                return await get_video_info_from_url(iframe_url)

        except Exception as e:
            logger.error(f"Failed to extract from sex.spreee.name: {e}")

    # ukdevilz.com, noodlemagazine.com - use direct video extraction
    if 'ukdevilz.com' in url_lower or 'noodlemagazine.com' in url_lower:
        try:
            import subprocess
            result = subprocess.run(
                ["curl", "-s", "-L", "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", url],
                capture_output=True,
                text=True,
                timeout=30,
            )
            html = result.stdout

            # Look for direct video file URLs
            patterns = [
                (r'(https://[^"\']+videofile[^"\']+\.mp4[^"\']*)', 1),
                (r'(https://[^"\']+pvvstream[^"\']+\.mp4[^"\']*)', 1),
                (r'"file"\s*:\s*["\']([^"\']+)["\']', 1),
            ]
            for pattern, group_idx in patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    video_url = match.group(group_idx).replace("\\", "").split("?")[0]  # Remove query params
                    if not video_url.startswith("http"):
                        continue
                    # Extract title
                    title_match = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html)
                    title = title_match.group(1).strip() if title_match else "Video"
                    return {"url": video_url, "title": title}

        except Exception as e:
            logger.error(f"Failed to extract from ukdevilz/noodlemagazine: {e}")

    # 36ebalka.ru and similar sites
    if '36ebalka.ru' in url_lower:
        try:
            import subprocess
            result = subprocess.run(
                ["curl", "-s", "-L", "-A", "Mozilla/5.0", url],
                capture_output=True,
                text=True,
                timeout=30,
            )
            html = result.stdout

            # Look for video player sources - prioritize direct .mp4 files
            patterns = [
                (r'(https://[^"\']+36ebalka\.ru[^"\']+\.mp4[^"\']*)', 1),  # Direct mp4 from 36ebalka
                (r'(https://[^"\']+get_file[^"\']+\.mp4[^"\']*)', 1),  # get_file mp4
                (r'<meta\s+property="og:video"\s+content="([^"]+)"', 1),  # og:video (fallback)
            ]
            for pattern, group_idx in patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    video_url = match.group(group_idx).replace("\\", "")
                    if not video_url.startswith("http"):
                        continue
                    title_match = re.search(r'<title>([^<]+)</title>', html)
                    title = title_match.group(1).strip() if title_match else "Video"
                    return {"url": video_url, "title": title}

            # Check for player config JSON
            config_match = re.search(r'player\.conf\s*=\s*(\{[^;]+\})', html)
            if config_match:
                import json
                try:
                    config = json.loads(config_match.group(1))
                    if "file" in config:
                        return {"url": config["file"], "title": config.get("title", "Video")}
                except:
                    pass

        except Exception as e:
            logger.error(f"Failed to extract from video site: {e}")

    return None


async def process_video_download(chat_id: int, message_id: int, url: str):
    """Process a single video download."""
    global is_downloading, bot

    download_success = False
    task_id = str(uuid.uuid4())[:8]
    temp_path = f"/tmp/vk_video_{task_id}.mp4"

    try:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="⏳ Начинаю скачивание..."
            )
        except Exception as e:
            logger.error(f"Could not edit message: {e}")

        video_info = await get_video_info_from_url(url)

        if not video_info or not video_info.get("url"):
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="❌ Не удалось получить информацию о видео. Проверьте ссылку."
            )
            return

        video_title = video_info.get("title", "Без названия")
        video_url = video_info["url"]
        video_link = f'<a href="{escape(video_url)}">🔗 Ссылка на видео</a>'

        async def on_progress(downloaded, total, current_bytes, total_bytes):
            bar = progress_bar(downloaded, 100)
            size_str = ""
            if total_bytes > 0:
                size_str = f" ({format_size(current_bytes)} / {format_size(total_bytes)})"
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"{escape(video_title)}\n\n{video_link}\n\n⏬ Скачивание...\n{bar}{size_str}",
                    parse_mode="HTML"
                )
            except Exception:
                pass

        success = await download_with_progress(url, temp_path, on_progress)

        if not success:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"{escape(video_title)}\n\n{video_link}\n\n❌ Скачивание прервано. Повторите команду с той же ссылкой для докачки.\n\n(Файл сохранён для возобновления)",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            return

        download_success = True

        if not os.path.exists(temp_path):
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="❌ Файл не был создан."
                )
            except Exception:
                pass
            return

        file_size = os.path.getsize(temp_path)

        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"{escape(video_title)}\n\n{video_link}\n\n✅ Скачивание завершено ({format_size(file_size)})\n\n⏫ Загружаю в чат...",
                parse_mode="HTML"
            )
        except Exception:
            pass

        try:
            with open(temp_path, "rb") as video_file:
                await bot.send_video(
                    chat_id=chat_id,
                    video=types.BufferedInputFile(video_file.read(), filename="video.mp4")
                )
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"{escape(video_title)}\n\n{video_link}\n\n✅ Скачивание завершено ({format_size(file_size)})\n\n✅ Загрузка в чат завершена!",
                    parse_mode="HTML"
                )
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Error sending video: {e}")
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"❌ Ошибка при отправке видео: {e}"
                )
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Error: {e}")

    finally:
        if download_success and os.path.exists(temp_path):
            os.remove(temp_path)

    is_downloading = False


async def video_queue_worker():
    """Background worker that processes the video download queue."""
    global is_downloading

    while True:
        try:
            chat_id, message_id, url = await video_queue.get()

            is_downloading = True

            try:
                await process_video_download(chat_id, message_id, url)
            finally:
                video_queue.task_done()

                if not video_queue.empty():
                    is_downloading = False
                    continue
                else:
                    is_downloading = False
                    await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Queue worker error: {e}")
            is_downloading = False
            await asyncio.sleep(1)


# ====== TORRENT FUNCTIONS ======

def get_aria2_bin() -> str:
    """Get path to aria2c."""
    return os.path.dirname(os.path.abspath(__file__)) + "/venv/bin/aria2c"


# Cookie file for rutracker (export from browser)
COOKIES_FILE = os.path.dirname(os.path.abspath(__file__)) + "/rutracker_cookies.txt"


def is_torrent_url(url: str) -> bool:
    """Check if URL is a torrent/magnet link."""
    url_lower = url.lower()
    return any([
        "rutracker.org" in url_lower and ("dl.php" in url_lower or "viewtopic.php" in url_lower),
        url_lower.endswith(".torrent"),
        url_lower.startswith("magnet:"),
    ])


def extract_rutracker_id(url: str) -> str | None:
    """Extract topic ID from rutracker URL."""
    match = re.search(r'dl\.php\?t=(\d+)', url)
    if match:
        return match.group(1)
    match = re.search(r'topic_id=(\d+)', url)
    if match:
        return match.group(1)
    return None


async def download_torrent(chat_id: int, message_id: int, url: str):
    """Download torrent file and start download with aria2."""
    global bot

    task_id = str(uuid.uuid4())[:8]
    torrent_path = f"{TORRENT_DIR}/torrent_{task_id}.torrent"
    download_dir = f"{TORRENT_DIR}/downloads_{task_id}"
    total_size = 0
    os.makedirs(download_dir, exist_ok=True)

    try:
        # Determine torrent type and get file
        if url.lower().startswith("magnet:"):
            # Magnet link - save to file
            with open(torrent_path.replace(".torrent", ".magnet"), "w") as f:
                f.write(url)
            torrent_arg = torrent_path.replace(".torrent", ".magnet")
        elif "rutracker.org" in url.lower():
            # Rutracker - need to download .torrent file
            topic_id = extract_rutracker_id(url)
            if not topic_id:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="❌ Не удалось извлечь ID торрента из ссылки."
                )
                return

            # Build the download URL
            download_url = f"https://rutracker.org/forum/dl.php?t={topic_id}"

            # Use curl to download the .torrent file
            curl_cmd = ["curl", "-s", "-L", "-o", torrent_path]
            if os.path.exists(COOKIES_FILE):
                curl_cmd.extend(["--cookie", COOKIES_FILE])
            curl_cmd.append(download_url)

            result = subprocess.run(curl_cmd, capture_output=True, text=True, timeout=60)

            if result.returncode != 0 or not os.path.exists(torrent_path) or os.path.getsize(torrent_path) == 0:
                # Check if we got an HTML page (login required)
                with open(torrent_path, "r", errors="ignore") as f:
                    content = f.read()[:500]
                if "login" in content.lower() or "<html" in content.lower():
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text="⚠️ Rutracker требует авторизацию.\n\n"
                             "Для скачивания торрентов с rutracker:\n"
                             "1. Экспортируйте cookies из браузера в файл `rutracker_cookies.txt` (формат Netscape)\n"
                             "2. Положите файл в папку бота: `/home/bots/video_downloader/rutracker_cookies.txt`\n\n"
                             "Или укажите логин/пароль от rutracker в .env",
                        parse_mode="HTML"
                    )
                else:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=f"❌ Не удалось скачать .torrent файл.\n\n{result.stderr[:300] if result.stderr else 'Unknown error'}",
                        parse_mode="HTML"
                    )
                return

            torrent_arg = torrent_path
        else:
            # Direct .torrent URL
            venv_bin = os.path.dirname(os.path.abspath(__file__)) + "/venv/bin"
            aria2_path = f"{venv_bin}/aria2c"

            result = subprocess.run(
                [aria2_path, "-d", TORRENT_DIR, "-o", f"torrent_{task_id}.torrent", url],
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode != 0:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"❌ Не удалось скачать .torrent файл.\n\n{result.stderr[:500] if result.stderr else 'Unknown error'}",
                    parse_mode="HTML"
                )
                return

            torrent_arg = torrent_path

        # Get magnet URI from torrent file
        aria2_path = get_aria2_bin()
        magnet_uri = None
        torrent_name = f"Торрент #{task_id}"

        try:
            info_result = subprocess.run(
                [aria2_path, "--show-files", torrent_arg],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if info_result.returncode == 0:
                lines = info_result.stdout.strip().split("\n")
                for line in lines:
                    if "Magnet URI:" in line:
                        magnet_uri = line.split("Magnet URI:")[1].strip()
                    if "Name:" in line and "Total" not in line:
                        torrent_name = line.split("Name:")[1].strip()[:100]
        except Exception as e:
            logger.error(f"Failed to get magnet URI: {e}")

        # Register torrent in active_torrents
        global active_torrents
        active_torrents[task_id] = {
            "name": torrent_name,
            "size": total_size,
            "status": "downloading",
            "download_dir": download_dir,
            "chat_id": chat_id,
            "message_id": message_id,
        }
        logger.info(f"Registered torrent {task_id}: {torrent_name}")

        if not magnet_uri:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="❌ Не удалось извлечь magnet URI из торрента.",
                parse_mode="HTML"
            )
            return

        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"📥 <b>{escape(torrent_name)}</b>\n\n⏬ Запускаю загрузку через aria2...",
            parse_mode="HTML"
        )

        # Get total size from torrent info
        try:
            info_result = subprocess.run(
                [aria2_path, "--show-files", torrent_arg],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if info_result.returncode == 0:
                output = info_result.stdout
                for line in output.split("\n"):
                    if "Total Length:" in line:
                        size_str = line.split("Total Length:")[1].strip()
                        # Format: "1.4GiB (1,567,434,752)" or "1,567,434,752"
                        import re
                        # Try to find bytes in parentheses first: (1,567,434,752)
                        match = re.search(r'\(([\d,]+)\)', size_str)
                        if match:
                            total_size = int(match.group(1).replace(",", ""))
                        else:
                            # Try GiB format: 1.4GiB
                            match = re.search(r'([\d.]+)GiB', size_str)
                            if match:
                                total_size = int(float(match.group(1)) * 1024 * 1024 * 1024)
        except Exception as e:
            logger.error(f"Failed to get torrent size: {e}")

        # Start aria2c in background with .torrent file (not magnet - magnet needs DHT for metadata)
        process = subprocess.Popen(
            [aria2_path, "-d", download_dir, "--bt-stop-timeout=300", torrent_arg],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait and check first progress
        await asyncio.sleep(5)

        if process.poll() is not None:
            _, stderr = process.communicate()
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"❌ Ошибка запуска торрента.\n\n{stderr.decode()[:500] if stderr else 'Unknown error'}",
                parse_mode="HTML"
            )
            return

        # Track progress
        last_update = 0
        last_size = 0
        stable_count = 0  # Count consecutive checks with same size (download complete)
        max_wait = 600  # 10 minutes max
        start_time = asyncio.get_event_loop().time()
        aria2_exit_code = None
        aria2_stderr = ""

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > max_wait:
                logger.info(f"Torrent download timed out after {max_wait}s")
                break

            # Find downloaded file and current size
            current_size = 0
            try:
                for f in os.listdir(download_dir):
                    fpath = os.path.join(download_dir, f)
                    if os.path.isfile(fpath):
                        current_size += os.path.getsize(fpath)
            except:
                pass

            # Check if process finished
            if process.poll() is not None:
                # Check exit status
                aria2_exit_code = process.wait()
                if aria2_exit_code != 0:
                    stdout, stderr = process.communicate()
                    aria2_stderr = stderr.decode()[:200] if stderr else ''
                    logger.error(f"aria2 exited with code {aria2_exit_code}: {aria2_stderr}")
                break

            # Check if download is complete (size matches total and stable)
            if total_size > 0 and current_size >= total_size:
                if current_size == last_size:
                    stable_count += 1
                    if stable_count >= 3:  # 3 consecutive same sizes = complete
                        logger.info(f"Download complete: {current_size} bytes (stable for 3 checks)")
                        # Kill aria2 - download is done
                        try:
                            process.terminate()
                            process.wait(timeout=5)
                        except:
                            try:
                                process.kill()
                            except:
                                pass
                        aria2_exit_code = 0
                        # Update status to complete
                        if task_id in active_torrents:
                            active_torrents[task_id]["status"] = "complete"
                        break
                else:
                    stable_count = 0

            last_size = current_size

            # Update message every 10 seconds
            if asyncio.get_event_loop().time() - last_update > 10 or last_update == 0:
                bar = progress_bar(current_size, total_size) if total_size > 0 else "⏳"
                size_str = f" ({format_size(current_size)} / {format_size(total_size)})" if total_size > 0 else f" ({format_size(current_size)})"
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=f"📥 <b>{escape(torrent_name)}</b>\n\n⏬ Загрузка через aria2...\n{bar}{size_str}",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Failed to edit message: {e}")
                last_update = asyncio.get_event_loop().time()

            await asyncio.sleep(5)

        # Find final file
        final_file = None
        final_size = 0
        try:
            for f in os.listdir(download_dir):
                fpath = os.path.join(download_dir, f)
                if os.path.isfile(fpath):
                    if final_file is None or os.path.getsize(fpath) > final_size:
                        final_file = f
                        final_size = os.path.getsize(fpath)
        except:
            pass

        # Check result - process already finished, aria2_exit_code is set
        logger.info(f"Torrent download loop finished. exit_code={aria2_exit_code}, final_file={final_file}, final_size={final_size}")

        if final_file and final_size > 0 and aria2_exit_code == 0 and (total_size == 0 or abs(final_size - total_size) < 1024*1024):
            logger.info(f"Starting file processing: {final_file}, size={final_size}")
            is_avi = final_file.lower().endswith(".avi")
            is_mp4 = final_file.lower().endswith(".mp4")
            is_streamable = any(final_file.lower().endswith(ext) for ext in [".mp4", ".mkv", ".mov", ".webm", ".flv"])
            need_transcode = is_avi or final_size > 1.5 * 1024 * 1024 * 1024
            logger.info(f"is_avi={is_avi}, is_streamable={is_streamable}, need_transcode={need_transcode}")
            download_success = True
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"📥 <b>{escape(torrent_name)}</b>\n\n✅ Загрузка завершена ({format_size(final_size)})\n\n⏫ Загружаю в чат...",
                    parse_mode="HTML"
                )
            except:
                pass

            # Send file to user
            final_path = os.path.join(download_dir, final_file)
            is_avi = final_file.lower().endswith(".avi")
            is_wmv = final_file.lower().endswith(".wmv")
            is_mp4 = final_file.lower().endswith(".mp4")
            is_streamable = any(final_file.lower().endswith(ext) for ext in [".mp4", ".mkv", ".mov", ".webm", ".flv"])
            need_transcode = is_avi or is_wmv or final_size > 1.5 * 1024 * 1024 * 1024  # > 1.5GB or AVI/WMV
            logger.info(f"Final check: is_avi={is_avi}, is_streamable={is_streamable}, need_transcode={need_transcode}, final_size={final_size}")

            try:
                if is_streamable and not need_transcode:
                    # Send as video - use file path for streaming
                    logger.info("Sending as MP4 video...")
                    await bot.send_video(
                        chat_id=chat_id,
                        video=types.FSInputFile(final_path)
                    )
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=f"📥 <b>{escape(torrent_name)}</b>\n\n✅ Загрузка в чат завершена!",
                        parse_mode="HTML"
                    )
                elif need_transcode:
                    logger.info("Entering transcoding branch...")
                    # Transcode to smaller size / convert AVI to MP4
                    transcoded_path = final_path + ".transcoded.mp4"
                    try:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=f"📥 <b>{escape(torrent_name)}</b>\n\n🔄 Перекодирование видео для отправки...",
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"Failed to edit message before transcoding: {e}")

                    async def transcode_progress(percent, total):
                        bar = progress_bar(percent, 100)
                        try:
                            await bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=f"📥 <b>{escape(torrent_name)}</b>\n\n🔄 Перекодирование...\n{bar}",
                                parse_mode="HTML"
                            )
                        except:
                            pass

                    logger.info(f"Starting transcode: {final_path} -> {transcoded_path}")
                    try:
                        success = await transcode_video(final_path, transcoded_path, max_size_mb=1800, progress_callback=transcode_progress)
                        logger.info(f"Transcode result: success={success}, exists={os.path.exists(transcoded_path)}")
                    except Exception as e:
                        logger.error(f"Transcode failed with exception: {e}")
                        success = False

                    if success and os.path.exists(transcoded_path):
                        transcoded_size = os.path.getsize(transcoded_path)
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=f"📥 <b>{escape(torrent_name)}</b>\n\n✅ Перекодирование завершено ({format_size(transcoded_size)})\n\n⏫ Загружаю в чат...",
                            parse_mode="HTML"
                        )
                        try:
                            await bot.send_video(
                                chat_id=chat_id,
                                video=types.FSInputFile(transcoded_path)
                            )
                            os.remove(transcoded_path)
                            await bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=f"📥 <b>{escape(torrent_name)}</b>\n\n✅ Загрузка в чат завершена!",
                                parse_mode="HTML"
                            )
                        except Exception as e:
                            logger.error(f"Failed to send transcoded video: {e}")
                            await bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=f"❌ Ошибка отправки: {e}",
                                parse_mode="HTML"
                            )
                    else:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=f"❌ Ошибка перекодирования.",
                            parse_mode="HTML"
                        )
                else:
                    # Send as document - use file path for streaming (max 50MB)
                    if final_size > 50 * 1024 * 1024:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=f"❌ Файл слишком большой для отправки как документ ({format_size(final_size)}).",
                            parse_mode="HTML"
                        )
                    else:
                        await bot.send_document(
                            chat_id=chat_id,
                            document=types.FSInputFile(final_path)
                        )
                
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=f"📥 <b>{escape(torrent_name)}</b>\n\n✅ Загрузка завершена ({format_size(final_size)})\n\n✅ Отправлено!",
                        parse_mode="HTML"
                    )
                except:
                    pass
            except Exception as e:
                logger.error(f"Failed to send file: {e}")
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=f"❌ Ошибка отправки файла: {e}"
                    )
                except:
                    pass
        else:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"❌ Загрузка не удалась.\n\n{stderr.decode()[:300] if stderr else ''}",
                    parse_mode="HTML"
                )
            except:
                pass

    except Exception as e:
        logger.error(f"Torrent error: {e}")
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"❌ Ошибка: {e}"
            )
        except:
            pass
    finally:
        # Remove from active_torrents
        if task_id in active_torrents:
            del active_torrents[task_id]
            logger.info(f"Removed torrent {task_id} from active_torrents")


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Отправь мне ссылку на видео, и я перешлю его тебе.\n\n"
        "Поддерживаются:\n"
        "• VK видео\n"
        "• sex.spreee.name\n"
        "• 36ebalka.ru\n"
        "• noodlemagazine.com\n"
        "• Торренты (Rutracker, magnet)\n\n"
        "Можно отправлять несколько ссылок — они будут обработаны по очереди."
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📹 <b>Видео:</b> отправь ссылку на видео (VK, sex.spreee, 36ebalka и др.)\n"
        "📥 <b>Торренты:</b> отправь ссылку Rutracker или magnet\n\n"
        "Команды:\n"
        "/queue — статус очереди\n"
        "/torrents — активные загрузки",
        parse_mode="HTML"
    )


@dp.message(Command("queue"))
async def cmd_queue(message: types.Message):
    """Check current video queue status."""
    if video_queue.empty():
        if is_downloading:
            await message.answer("🎬 Сейчас скачивается видео. Очередь пуста.")
        else:
            await message.answer("✅ Очередь пуста, бот в режиме ожидания.")
    else:
        count = video_queue.qsize()
        await message.answer(f"📋 В очереди видео: {count}")


@dp.message(Command("torrents"))
async def cmd_torrents(message: types.Message):
    """Show active torrent downloads with inline keyboard."""
    global active_torrents

    if not active_torrents:
        await message.answer("✅ Нет активных загрузок.")
        return

    # Build list of torrents
    torrent_list = list(active_torrents.items())
    total = len(torrent_list)

    # Create message text
    lines = ["📥 <b>Активные загрузки:</b>", ""]
    keyboard = []

    for i, (task_id, info) in enumerate(torrent_list, 1):
        name = escape(info.get("name", "Без названия"))[:50]
        size_str = format_size(info.get("size", 0))
        status = info.get("status", "unknown")
        status_emoji = {"downloading": "⏬", "seeding": "🌱", "complete": "✅"}.get(status, "⏳")

        lines.append(f"{i}. {status_emoji} <code>{name}</code>")
        lines.append(f"   💾 {size_str} | ID: {task_id}")
        lines.append("")

        # Add button with number
        keyboard.append([InlineKeyboardButton(text=f"🗑 {i}", callback_data=f"del_torrent_{task_id}")])

    lines.append(f"Всего: {total}")
    text = "\n".join(lines)

    # Create inline keyboard
    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await message.answer(text, parse_mode="HTML", reply_markup=markup)


@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    """Handle inline keyboard callbacks."""
    global active_torrents

    data = callback.data or ""

    if data.startswith("del_torrent_"):
        task_id = data.replace("del_torrent_", "")

        if task_id in active_torrents:
            info = active_torrents[task_id]
            download_dir = info.get("download_dir", "")

            # Kill aria2 process for this task
            try:
                subprocess.run(["pkill", "-f", f"downloads_{task_id}"], capture_output=True)
            except:
                pass

            # Remove files
            try:
                if download_dir:
                    subprocess.run(["rm", "-rf", download_dir], capture_output=True)
                torrent_file = f"{TORRENT_DIR}/torrent_{task_id}.torrent"
                subprocess.run(["rm", "-f", torrent_file], capture_output=True)
            except:
                pass

            # Remove from active_torrents
            del active_torrents[task_id]

            await callback.answer(f"✅ Торрент {task_id} удалён")
            await callback.message.edit_text(f"✅ Торрент удалён: {info.get('name', task_id)}")
        else:
            await callback.answer("❌ Торрент не найден", show_alert=True)
    else:
        await callback.answer("Неизвестная команда", show_alert=True)


@dp.message()
async def handle_message(message: types.Message):
    global queue_task

    text = message.text or ""

    # Check if it's a torrent link
    if is_torrent_url(text):
        # Handle torrent download
        status_msg = await message.answer("📥 Получена ссылка на торрент...")
        asyncio.create_task(download_torrent(message.chat.id, status_msg.message_id, text))
        return

    # Check if it's a VK video link or other supported video sites
    text_lower = text.lower()
    is_video_url = any(domain in text_lower for domain in [
        "vk.com", "vkvideo.ru", "vk.ru",
        "ukdevilz.com", "noodlemagazine.com",
        "sex.spreee.name", "36ebalka.ru",
        "embed-player.space"
    ])
    if not is_video_url and not is_torrent_url(text):
        await message.answer("Отправьте ссылку на видео из ВКонтакте или торрент.")
        return

    # Start queue worker if not running
    if queue_task is None or queue_task.done():
        queue_task = asyncio.create_task(video_queue_worker())

    # Send initial message and get its ID
    status_msg = await message.answer("⏳ Видео в очереди на скачивание...")

    # Add to queue: (chat_id, message_id, url)
    await video_queue.put((message.chat.id, status_msg.message_id, text))

    queue_size = video_queue.qsize()

    if queue_size > 1:
        await status_msg.edit_text(f"📋 Видео добавлено в очередь. Позиция: {queue_size}")


async def main():
    global queue_task
    queue_task = asyncio.create_task(video_queue_worker())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
