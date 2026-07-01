import asyncio
import os
import re
import subprocess
from dotenv import load_dotenv

load_dotenv()

VK_API_URL = "https://api.vk.com/method"


class VKClient:
    def __init__(self):
        self.token = os.getenv("VK_SERVICE_ACCESS_TOKEN")
        self.version = "5.131"

    async def get_video(self, owner_id: str, video_id: str) -> dict | None:
        """Get video info from VK."""
        import httpx
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

    async def get_video_url(self, url: str) -> str | None:
        """Get direct video URL from VK video page using yt-dlp."""
        # Use yt-dlp to get direct URL
        try:
            result = subprocess.run(
                ["yt-dlp", "--get-url", "-f", "best[ext=mp4]/best", url],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception as e:
            print(f"yt-dlp error: {e}")

        return None

    async def download_video(self, url: str, output_path: str) -> bool:
        """Download video using yt-dlp."""
        try:
            result = subprocess.run(
                ["yt-dlp", "-f", "best[ext=mp4]/best", "-o", output_path, url],
                capture_output=True,
                text=True,
                timeout=300,
            )
            return result.returncode == 0
        except Exception as e:
            print(f"yt-dlp download error: {e}")
            return False
