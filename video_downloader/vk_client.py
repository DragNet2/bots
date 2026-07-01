import httpx
import os
from dotenv import load_dotenv

load_dotenv()

VK_API_URL = "https://api.vk.com/method"


class VKClient:
    def __init__(self):
        self.token = os.getenv("VK_SERVICE_ACCESS_TOKEN")
        self.version = "5.131"

    async def get_video(self, owner_id: str, video_id: str) -> dict | None:
        """Get video info from VK."""
        async with httpx.AsyncClient() as client:
            params = {
                "access_token": self.token,
                "v": self.version,
                "owner_id": owner_id,
                "videos": f"{owner_id}_{video_id}",
            }
            response = await client.get(
                f"{VK_API_URL}/video.get", params=params
            )
            data = response.json()
            return data.get("response", {}).get("items", [None])[0]

    def extract_video_id(self, url: str) -> tuple[str, str] | None:
        """Extract owner_id and video_id from VK video URL."""
        # Formats:
        # https://vk.com/video-123456789_123456789
        # https://vk.com/video123456789_123456789
        import re

        patterns = [
            r"video(-?\d+)_(\d+)",
            r"video\.php\?oid=(-?\d+)&id=(\d+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1), match.group(2)

        return None

    async def get_video_url(self, url: str) -> str | None:
        """Get direct video URL from VK video page."""
        ids = self.extract_video_id(url)
        if not ids:
            return None

        owner_id, video_id = ids
        video = await self.get_video(owner_id, video_id)

        if not video:
            return None

        # Prefer mp4 files with highest quality
        files = video.get("files", {})
        for key in ["mp4_1080", "mp4_720", "mp4_480", "mp4_360", "mp4_240"]:
            if key in files:
                return files[key]

        return None
