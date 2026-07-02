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
    ) -> bool:
        """Upload file to Yandex Disk with progress tracking.
        
        Args:
            file_path: Local file path
            disk_path: Path on Yandex Disk (e.g., /Downloads/video.mp4)
            progress_callback: Optional callback(loaded_bytes, total_bytes)
        
        Returns:
            True if successful
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
                        return False
                    
                    data = await resp.json()
                    upload_url = data.get("href")
                    
                    if not upload_url:
                        logger.error("No upload URL in response")
                        return False
                
                # Read file and upload
                with open(file_path, "rb") as f:
                    file_data = f.read()
                
                # Upload with progress tracking
                uploaded = 0
                chunk_size = 1024 * 1024  # 1MB chunks
                
                async with session.put(upload_url, data=file_data) as resp:
                    if resp.status in (200, 201):
                        logger.info(f"Uploaded {os.path.basename(file_path)} to Yandex Disk")
                        if progress_callback:
                            progress_callback(file_size, file_size)  # Complete
                        return True
                    else:
                        error = await resp.text()
                        logger.error(f"Upload failed: {error}")
                        return False
                        
        except Exception as e:
            logger.error(f"Error uploading to Yandex Disk: {e}")
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
