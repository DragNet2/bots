import httpx
import os
import re
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
        # https://vkvideo.ru/video-123456789_123456789

        patterns = [
            r"video(-?\d+)_(\d+)",
            r"video\.php\?oid=(-?\d+)&id=(\d+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1), match.group(2)

        return None

    async def get_vkvideo_url(self, url: str) -> str | None:
        """Get direct video URL from vkvideo.ru page."""
        # vkvideo.ru is a separate video hosting, need to scrape the page
        match = re.search(r"video(-?\d+)_(\d+)", url)
        if not match:
            return None

        video_id = match.group(2)

        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                response = await client.get(url)
                response.raise_for_status()
                html = response.text

                # Look for mp4 URL in the page
                # Pattern: src="https://vk.com/video...mp4..."
                mp4_pattern = r'(?:src|data-src)=["\']([^"\']*\.mp4[^"\']*)["\']'
                matches = re.findall(mp4_pattern, html)

                for mp4_url in matches:
                    if "vk.com/video" in mp4_url or mp4_url.startswith("http"):
                        return mp4_url

                # Alternative: look for player config
                player_pattern = r'"url720"\s*:\s*["\']([^"\']+)["\']'
                match = re.search(player_pattern, html)
                if match:
                    return match.group(1)

                player_pattern = r'"url480"\s*:\s*["\']([^"\']+)["\']'
                match = re.search(player_pattern, html)
                if match:
                    return match.group(1)

                player_pattern = r'"url360"\s*:\s*["\']([^"\']+)["\']'
                match = re.search(player_pattern, html)
                if match:
                    return match.group(1)

            except Exception as e:
                print(f"Error fetching vkvideo.ru page: {e}")

        return None

    async def get_video_url(self, url: str) -> str | None:
        """Get direct video URL from VK video page."""
        # For vkvideo.ru, scrape the page directly
        if "vkvideo.ru" in url.lower():
            return await self.get_vkvideo_url(url)

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
