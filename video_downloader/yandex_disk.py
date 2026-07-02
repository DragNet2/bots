"""Yandex Disk API integration."""
import aiohttp
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

YANDEX_DISK_API = "https://cloud-api.yandex.net/v1/disk"


class YandexDisk:
    def __init__(self, token: str):
        self.token = token
        self.headers = {"Authorization": f"OAuth {token}"}

    async def upload_file(self, file_path: str, disk_path: str) -> bool:
        """Upload file to Yandex Disk.
        
        Args:
            file_path: Local file path
            disk_path: Path on Yandex Disk (e.g., /Downloads/video.mp4)
        
        Returns:
            True if successful
        """
        import os
        
        file_size = os.path.getsize(file_path)
        file_name = os.path.basename(file_path)
        
        # Get upload URL
        url = f"{YANDEX_DISK_API}/resources/upload"
        params = {"path": disk_path, "overwrite": "true"}
        
        try:
            async with aiohttp.ClientSession() as session:
                # Get presigned URL for upload
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
                
                # Upload file
                with open(file_path, "rb") as f:
                    file_data = f.read()
                
                async with session.put(upload_url, data=file_data) as resp:
                    if resp.status in (200, 201):
                        logger.info(f"Successfully uploaded {file_name} to Yandex Disk: {disk_path}")
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
