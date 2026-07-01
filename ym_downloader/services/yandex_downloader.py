"""
Yandex Music Downloader Service.
"""
import asyncio
import logging
import os
import re
import time
import zipfile
import tempfile
from dataclasses import dataclass
from typing import Optional

from yandex_music import Client

from config import YANDEX_TOKEN, YANDEX_API_BASE, DOWNLOAD_DIR, RATE_LIMIT_DELAY

logger = logging.getLogger(__name__)


def sanitize_filename(filename: str) -> str:
    """Удаляет/заменяет недопустимые символы в имени файла."""
    filename = filename.replace("'", "'")
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    filename = re.sub(r'\s+', ' ', filename)
    filename = filename.strip()
    return filename if filename else "unknown"


@dataclass
class TrackInfo:
    track_id: int
    album_id: int
    title: str
    artist: str
    album: str
    duration_seconds: int
    url: str


class YandexDownloader:
    def __init__(self, token: str = YANDEX_TOKEN):
        self.token = token
        self.client: Optional[Client] = None
        self._download_dir = DOWNLOAD_DIR
        os.makedirs(self._download_dir, exist_ok=True)

    def connect(self) -> bool:
        try:
            self.client = Client(self.token).init()
            logger.info("Yandex Music client initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Yandex Music client: {e}")
            return False

    def _parse_track_url(self, url: str) -> tuple[int, int]:
        match = re.match(r'https?://music\.yandex\.(?:ru|com|kz|ua|by)/album/(\d+)/track/(\d+)', url)
        if match:
            return int(match.group(1)), int(match.group(2))
        match = re.search(r'/album/(\d+).*?/track/(\d+)', url)
        if match:
            return int(match.group(1)), int(match.group(2))
        raise ValueError(f"Invalid Yandex Music URL: {url}")

    def _parse_album_url(self, url: str) -> int:
        match = re.search(r'https?://music\.yandex\.(?:ru|com|kz|ua|by)/album/(\d+)', url)
        if match:
            return int(match.group(1))
        raise ValueError(f"Invalid Yandex Music album URL: {url}")

    def _parse_artist_url(self, url: str) -> int:
        match = re.search(r'https?://music\.yandex\.(?:ru|com|kz|ua|by)/artist/(\d+)', url)
        if match:
            return int(match.group(1))
        raise ValueError(f"Invalid Yandex Music artist URL: {url}")

    async def get_track_info(self, album_id: int, track_id: int) -> Optional[TrackInfo]:
        try:
            await asyncio.to_thread(self.client.tracks, [track_id])
            tracks = await asyncio.to_thread(self.client.tracks, [track_id])
            if not tracks:
                return None

            track = tracks[0]
            full_track = await asyncio.to_thread(track.fetch_track)

            artists = ", ".join([a.name for a in (full_track.artists if hasattr(full_track, 'artists') else [track.artists[0] if track.artists else None]) if a])

            return TrackInfo(
                track_id=track_id,
                album_id=album_id,
                title=full_track.title if hasattr(full_track, 'title') else track.title,
                artist=artists or "Unknown",
                album=track.albums[0].title if track.albums else "Unknown",
                duration_seconds=int(full_track.duration_ms / 1000) if hasattr(full_track, 'duration_ms') else 0,
                url=f"https://music.yandex.ru/album/{album_id}/track/{track_id}"
            )
        except Exception as e:
            logger.error(f"Error getting track info: {e}")
            return None

    async def download_track(self, url: str) -> Optional[tuple[str, str, bytes]]:
        try:
            album_id, track_id = self._parse_track_url(url)
            logger.info(f"Downloading track {track_id} from album {album_id}")

            await asyncio.sleep(RATE_LIMIT_DELAY)

            tracks = await asyncio.to_thread(self.client.tracks, [track_id])
            if not tracks:
                logger.error(f"Track {track_id} not found")
                return None

            track = tracks[0]
            artist = ", ".join(track.artists_name()) if hasattr(track, 'artists_name') else (track.artists[0].name if track.artists else "Unknown")
            title = track.title
            temp_path = os.path.join(self._download_dir, f"track_{track_id}.mp3")

            await asyncio.to_thread(track.download, temp_path)

            with open(temp_path, 'rb') as f:
                data = f.read()

            os.remove(temp_path)
            logger.info(f"Track {track_id} downloaded successfully ({len(data)} bytes)")
            return (artist, title, data)

        except Exception as e:
            logger.error(f"Error downloading track from {url}: {e}")
            return None

    async def download_album(self, url: str) -> list[tuple[str, str, bytes]]:
        try:
            album_id = self._parse_album_url(url)
            logger.info(f"Downloading album {album_id}")

            await asyncio.sleep(RATE_LIMIT_DELAY)

            album = await asyncio.to_thread(self.client.albums_with_tracks, album_id)
            if not album:
                logger.error(f"Album {album_id} not found")
                return []

            all_tracks = album.volumes[0] if album.volumes else []
            results = []
            tracks_count = len(all_tracks)

            for i, track in enumerate(all_tracks):
                logger.info(f"Downloading track {i+1}/{tracks_count}: {track.title}")

                await asyncio.sleep(RATE_LIMIT_DELAY)

                try:
                    artist = ", ".join(track.artists_name()) if hasattr(track, 'artists_name') else (track.artists[0].name if track.artists else "Unknown")
                    title = track.title
                    temp_path = os.path.join(self._download_dir, f"album_{album_id}_track_{track.id}.mp3")

                    await asyncio.to_thread(track.download, temp_path)

                    with open(temp_path, 'rb') as f:
                        data = f.read()

                    os.remove(temp_path)
                    results.append((artist, title, data))
                    logger.info(f"Track {sanitize_filename(artist)} - {sanitize_filename(title)} downloaded ({len(data)} bytes)")

                except Exception as e:
                    logger.error(f"Error downloading track {track.id}: {e}")
                    continue

            return results

        except Exception as e:
            logger.error(f"Error downloading album from {url}: {e}")
            return []

    async def download_album_zip(self, url: str, max_zip_size_mb: int = 45) -> list[tuple[str, str, bytes]]:
        """Скачивает альбом в ZIP, разбитый на части. Возвращает список (folder_name, zip_filename, zip_data)."""
        try:
            album_id = self._parse_album_url(url)
            logger.info(f"Downloading album {album_id} as ZIP (max {max_zip_size_mb}MB per file)")

            await asyncio.sleep(RATE_LIMIT_DELAY)

            album = await asyncio.to_thread(self.client.albums_with_tracks, album_id)
            if not album:
                logger.error(f"Album {album_id} not found")
                return []

            all_tracks = album.volumes[0] if album.volumes else []
            tracks_count = len(all_tracks)

            year = getattr(album, 'year', None) or "Unknown"
            album_title = sanitize_filename(album.title)
            folder_name = f"{year} - {album_title}"

            max_size_bytes = max_zip_size_mb * 1024 * 1024

            results = []
            current_zip_path = None
            current_zipf = None
            current_part = 1
            current_size = 0

            for i, track in enumerate(all_tracks):
                logger.info(f"Downloading track {i+1}/{tracks_count}: {track.title}")

                await asyncio.sleep(RATE_LIMIT_DELAY)

                try:
                    artist = ", ".join(track.artists_name()) if hasattr(track, 'artists_name') else (track.artists[0].name if track.artists else "Unknown")
                    title = track.title
                    track_num = f"{i+1:02d}"
                    filename = f"{track_num} - {sanitize_filename(title)}.mp3"

                    temp_path = os.path.join(self._download_dir, f"temp_track_{track.id}.mp3")
                    await asyncio.to_thread(track.download, temp_path)

                    track_size = os.path.getsize(temp_path)

                    if current_zipf is None or current_size + track_size > max_size_bytes:
                        if current_zipf is not None:
                            current_zipf.close()
                            with open(current_zip_path, 'rb') as f:
                                zip_data = f.read()
                            part_num = current_part
                            current_part += 1
                            results.append((folder_name, f"{folder_name} (part {part_num}).zip", zip_data))
                            os.remove(current_zip_path)
                            logger.info(f"ZIP part {part_num} created: {len(zip_data)} bytes")

                        current_zip_path = os.path.join(self._download_dir, f"album_{album_id}_part{current_part}.zip")
                        current_zipf = zipfile.ZipFile(current_zip_path, 'w', zipfile.ZIP_DEFLATED)
                        current_size = 0

                    current_zipf.write(temp_path, f"{folder_name}/{filename}")
                    current_size += track_size
                    os.remove(temp_path)

                    logger.info(f"Added to ZIP part {current_part}: {folder_name}/{filename}")

                except Exception as e:
                    logger.error(f"Error downloading track {track.id}: {e}")
                    continue

            if current_zipf is not None:
                current_zipf.close()
                with open(current_zip_path, 'rb') as f:
                    zip_data = f.read()
                zip_filename = f"{folder_name} (part {current_part}).zip"
                results.append((folder_name, zip_filename, zip_data))
                os.remove(current_zip_path)
                logger.info(f"ZIP part {current_part} created: {len(zip_data)} bytes")

            logger.info(f"Album ZIP completed: {len(results)} parts")
            return results

        except Exception as e:
            logger.error(f"Error creating album ZIP from {url}: {e}")
            return []

    async def download_artist(self, url: str) -> list[tuple[str, str, bytes]]:
        try:
            artist_id = self._parse_artist_url(url)
            logger.info(f"Downloading all tracks for artist {artist_id}")

            await asyncio.sleep(RATE_LIMIT_DELAY)

            artist = await asyncio.to_thread(self.client.artists, artist_id)
            if not artist:
                logger.error(f"Artist {artist_id} not found")
                return []

            results = []
            albums = await asyncio.to_thread(self.client.artist_direct_albums, artist_id)

            logger.info(f"Found {len(albums)} albums for artist")

            for album in albums:
                logger.info(f"Processing album: {album.title}")

                await asyncio.sleep(RATE_LIMIT_DELAY)

                album_tracks = await asyncio.to_thread(self.client.albums_with_tracks, album.id)
                if not album_tracks:
                    continue

                for track in album_tracks.tracks:
                    await asyncio.sleep(RATE_LIMIT_DELAY)

                    try:
                        track_artist = ", ".join(track.artists_name()) if hasattr(track, 'artists_name') else (track.artists[0].name if track.artists else "Unknown")
                        title = track.title
                        temp_path = os.path.join(self._download_dir, f"artist_{artist_id}_track_{track.id}.mp3")

                        await asyncio.to_thread(track.download, temp_path)

                        with open(temp_path, 'rb') as f:
                            data = f.read()

                        os.remove(temp_path)
                        results.append((track_artist, title, data))
                        logger.info(f"Downloaded: {track_artist} - {title} ({len(data)} bytes)")

                    except Exception as e:
                        logger.error(f"Error downloading track {track.id}: {e}")
                        continue

            return results

        except Exception as e:
            logger.error(f"Error downloading artist from {url}: {e}")
            return []

    async def search_and_download(self, query: str) -> list[tuple[str, str, bytes]]:
        try:
            logger.info(f"Searching for: {query}")

            await asyncio.sleep(RATE_LIMIT_DELAY)

            result = await asyncio.to_thread(self.client.search, query)
            if not result or not result.best or not result.best.result:
                logger.warning(f"No results found for: {query}")
                return []

            track = result.best.result
            if hasattr(track, 'albums') and track.albums:
                album_id = track.albums[0].id
                track_id = track.id
                url = f"https://music.yandex.ru/album/{album_id}/track/{track_id}"

                result = await self.download_track(url)
                if result:
                    artist, title, data = result
                    return [(artist, title, data)]

            return []

        except Exception as e:
            logger.error(f"Error searching and downloading: {e}")
            return []

    def get_download_dir(self) -> str:
        return self._download_dir

    async def cleanup(self):
        for filename in os.listdir(self._download_dir):
            filepath = os.path.join(self._download_dir, filename)
            try:
                if os.path.isfile(filepath):
                    os.remove(filepath)
            except Exception as e:
                logger.error(f"Error cleaning up {filepath}: {e}")