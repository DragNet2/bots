"""Yandex Disk API integration."""
import aiohttp
import asyncio
import logging
import os
from typing import Optional, Callable

logger = logging.getLogger(__name__)

YANDEX_DISK_API = "https://cloud-api.yandex.net/v1/disk"


class YandexDisk:
    def __init__(self, token: str):
        self.token = token
        self.headers = {"Authorization": f"OAuth {token}"}

    async def upload_file(
        self,
        file_path: str,
        disk_path: str,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> tuple[bool, str]:
        """Upload file to Yandex Disk with progress tracking.

        Args:
            file_path: Local file path
            disk_path: Path on Yandex Disk (e.g., /Downloads/video.mp4)
            progress_callback: Optional callback(loaded_bytes, total_bytes)

        Returns:
            (True, "") if successful, (False, error_message) otherwise
        """
        file_size = os.path.getsize(file_path)

        try:
            async with aiohttp.ClientSession() as session:
                # Get upload URL
                url = f"{YANDEX_DISK_API}/resources/upload"
                params = {"path": disk_path, "overwrite": "true"}

                async with session.get(url, headers=self.headers, params=params) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        logger.error(f"Failed to get upload URL: {error}")
                        return False, f"Не удалось получить ссылку загрузки ({resp.status}): {error[:200]}"

                    data = await resp.json()
                    upload_url = data.get("href")

                    if not upload_url:
                        logger.error("No upload URL in response")
                        return False, "Яндекс.Диск не вернул ссылку для загрузки"

                # Stream file in chunks to avoid loading it fully into memory
                chunk_size = 1024 * 1024  # 1MB chunks

                async def file_sender():
                    uploaded = 0
                    with open(file_path, "rb") as f:
                        while True:
                            chunk = f.read(chunk_size)
                            if not chunk:
                                break
                            uploaded += len(chunk)
                            if progress_callback:
                                result = progress_callback(uploaded, file_size)
                                if asyncio.iscoroutine(result):
                                    await result
                            yield chunk

                async with session.put(
                    upload_url,
                    data=file_sender(),
                    headers={"Content-Length": str(file_size)},
                ) as resp:
                    if resp.status in (200, 201):
                        logger.info(f"Uploaded {os.path.basename(file_path)} to Yandex Disk")
                        if progress_callback:
                            result = progress_callback(file_size, file_size)
                            if asyncio.iscoroutine(result):
                                await result
                        return True, ""
                    else:
                        error = await resp.text()
                        logger.error(f"Upload failed: {error}")
                        return False, f"Ошибка загрузки файла ({resp.status}): {error[:200]}"

        except Exception as e:
            logger.error(f"Error uploading to Yandex Disk: {e}")
            return False, f"Ошибка загрузки: {e}"

    async def publish_file(self, disk_path: str) -> str | None:
        """Publish a file/folder and return its public URL.

        Args:
            disk_path: Path on Yandex Disk (e.g., /Downloads/video.mp4)
        Returns:
            Public URL of the resource, or None on failure.
        """
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{YANDEX_DISK_API}/resources/publish"
                params = {"path": disk_path}

                async with session.put(url, headers=self.headers, params=params) as resp:
                    if resp.status not in (200, 201, 202):
                        error = await resp.text()
                        logger.error(f"Failed to publish {disk_path}: {error}")
                        return None

                    data = await resp.json() if resp.content_type == "application/json" else {}
                    operation_href = data.get("href")

                # Async operation: poll until completed (usually instant)
                if operation_href:
                    for _ in range(10):
                        async with session.get(operation_href, headers=self.headers) as resp:
                            if resp.status == 200:
                                op = await resp.json()
                                if op.get("status") == "success":
                                    break
                        await asyncio.sleep(0.5)

                # Fetch public URL from resource metadata
                meta_url = f"{YANDEX_DISK_API}/resources"
                params = {"path": disk_path, "fields": "public_url"}

                async with session.get(meta_url, headers=self.headers, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        public_url = data.get("public_url")
                        if public_url:
                            logger.info(f"Published {disk_path}: {public_url}")
                        return public_url
                    logger.error(f"Failed to get public URL for {disk_path}: {await resp.text()}")
                    return None

        except Exception as e:
            logger.error(f"Error publishing {disk_path}: {e}")
            return False

    async def create_folder(self, path: str) -> bool:
        """Create folder on Yandex Disk."""
        url = f"{YANDEX_DISK_API}/resources"
        params = {"path": path}
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(url, headers=self.headers, params=params) as resp:
                    return resp.status in (200, 201)
        except Exception as e:
            logger.error(f"Error creating folder: {e}")
            return False

    async def get_info(self) -> dict:
        """Get disk info."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(YANDEX_DISK_API, headers=self.headers) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    return {}
        except Exception as e:
            logger.error(f"Error getting disk info: {e}")
            return {}
