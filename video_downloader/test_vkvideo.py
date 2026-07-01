import asyncio
import httpx
import re
import os
from dotenv import load_dotenv

load_dotenv()

async def test():
    # Video IDs from vkvideo.ru URL
    owner_id = "-206967489"
    video_id = "456239073"

    token = os.getenv("VK_SERVICE_ACCESS_TOKEN")
    api_url = "https://api.vk.com/method/video.get"

    async with httpx.AsyncClient() as client:
        params = {
            "access_token": token,
            "v": "5.131",
            "owner_id": owner_id,
            "videos": f"{owner_id}_{video_id}",
        }
        response = await client.get(api_url, params=params)
        data = response.json()
        print(f"API response: {data}")

        if "response" in data:
            items = data["response"].get("items", [])
            if items:
                video = items[0]
                print(f"Video title: {video.get('title')}")
                files = video.get("files", {})
                print(f"Available files: {list(files.keys())}")
                for key in ["mp4_1080", "mp4_720", "mp4_480", "mp4_360", "mp4_240"]:
                    if key in files:
                        print(f"Found {key}: {files[key][:100]}...")
                        break

asyncio.run(test())
