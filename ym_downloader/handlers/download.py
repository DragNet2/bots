"""
Download handlers for Yandex Music Downloader Bot.
"""
import asyncio
import logging
import re
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest

from keyboards import get_main_menu_keyboard, get_cancel_keyboard, get_back_to_menu_keyboard, get_yes_no_keyboard, get_album_download_choice_keyboard
from services.yandex_downloader import YandexDownloader, sanitize_filename
from config import MAX_FILE_SIZE_MB

router = Router()
downloader = YandexDownloader()
user_states = {}

def get_downloader() -> YandexDownloader:
    if downloader.client is None:
        downloader.connect()
    return downloader

def detect_url_type(text: str) -> str | None:
    """Определяет тип ссылки Yandex Music. Возвращает: 'track', 'album', 'artist' или None"""
    base_url = text.split('?')[0]
    patterns = {
        'track': r'https?://music\.yandex\.(?:ru|com|kz|ua|by)/album/\d+/track/\d+',
        'album': r'https?://music\.yandex\.(?:ru|com|kz|ua|by)/album/\d+',
        'artist': r'https?://music\.yandex\.(?:ru|com|kz|ua|by)/artist/\d+',
    }
    for url_type, pattern in patterns.items():
        if re.match(pattern, base_url):
            return url_type
    return None


def is_valid_ym_url(url: str) -> bool:
    base_url = url.split('?')[0]
    patterns = [
        r'https?://music\.yandex\.(?:ru|com|kz|ua|by)/album/\d+/track/\d+',
        r'https?://music\.yandex\.(?:ru|com|kz|ua|by)/album/\d+',
        r'https?://music\.yandex\.(?:ru|com|kz|ua|by)/artist/\d+',
    ]
    return any(re.match(p, base_url) for p in patterns)


@router.message(Command("start"))
async def cmd_start(message: Message):
    if not get_downloader().client:
        await message.answer("🔄 Подключение к Яндекс.Музыке...")
        if not get_downloader().connect():
            await message.answer("❌ Ошибка подключения к Яндекс.Музыке. Проверьте токен.")
            return

    await message.answer(
        "🎵 <b>Yandex Music Downloader</b>\n\n"
        "Бот для скачивания музыки из Яндекс.Музыки.\n\n"
        "Поддерживает:\n"
        "• Скачивание отдельного трека\n"
        "• Скачивание альбома целиком\n"
        "• Скачивание всех треков исполнителя\n"
        "• Поиск и скачивание по названию\n\n"
        "Выберите действие:",
        reply_markup=get_main_menu_keyboard(),
        parse_mode='HTML',
    )


@router.callback_query(lambda c: c.data == "download_track")
async def callback_download_track(callback_query: CallbackQuery):
    await callback_query.message.edit_text(
        "🎵 <b>Скачивание трека</b>\n\n"
        "Отправьте ссылку на трек с Яндекс.Музыки.\n\n"
        "Формат: https://music.yandex.ru/album/{album_id}/track/{track_id}\n\n"
        "Нажмите /cancel для отмены.",
        reply_markup=get_cancel_keyboard(),
        parse_mode='HTML',
    )
    user_states[callback_query.from_user.id] = {"state": "waiting_track_url"}


@router.callback_query(lambda c: c.data == "download_album")
async def callback_download_album(callback_query: CallbackQuery):
    await callback_query.message.edit_text(
        "💿 <b>Скачивание альбома</b>\n\n"
        "Отправьте ссылку на альбом с Яндекс.Музыки.\n\n"
        "Формат: https://music.yandex.ru/album/{album_id}\n\n"
        "Нажмите /cancel для отмены.",
        reply_markup=get_cancel_keyboard(),
        parse_mode='HTML',
    )
    user_states[callback_query.from_user.id] = {"state": "waiting_album_url"}


@router.callback_query(lambda c: c.data.startswith("album_single_"))
async def callback_album_single(callback_query: CallbackQuery):
    album_id = callback_query.data.replace("album_single_", "")
    url = f"https://music.yandex.ru/album/{album_id}"
    user_states.pop(callback_query.from_user.id, None)

    album_info = await get_downloader().get_album_info(url)
    if not album_info:
        await callback_query.message.edit_text("❌ Не удалось получить информацию об альбоме.")
        return

    album_title, tracks_count = album_info
    status_msg = await callback_query.message.answer(
        f"💿 Альбом \"{album_title}\"\n\n"
        f"💾 Отправка треков: 0/{tracks_count}",
        parse_mode='HTML'
    )
    status_msg_id = status_msg.message_id

    sent_count = 0
    failed_tracks = []

    async def progress_callback(artist: str, title: str, data: bytes, downloaded: int, total: int):
        nonlocal sent_count, status_msg_id
        filename = f"{sanitize_filename(artist)} - {sanitize_filename(title)}.mp3"

        success, error = await send_file_safe(callback_query.message, data, filename)
        if success:
            sent_count += 1
            try:
                await callback_query.message.bot.delete_message(
                    chat_id=callback_query.message.chat.id,
                    message_id=status_msg_id
                )
            except TelegramBadRequest:
                pass
            new_msg = await callback_query.message.answer(
                f"💿 Альбом \"{album_title}\"\n\n"
                f"💾 Отправлено: {sent_count}/{total}",
                parse_mode='HTML'
            )
            status_msg_id = new_msg.message_id
        else:
            await callback_query.message.answer(f"❌ Ошибка отправки трека: {error}")
        await asyncio.sleep(0.5)

    async def error_callback(artist: str, title: str, track_url: str, error: str):
        failed_tracks.append((artist, title, track_url))

    try:
        async for _ in get_downloader().download_album_gen(url, progress_callback=progress_callback, error_callback=error_callback):
            pass

        try:
            await callback_query.message.bot.delete_message(
                chat_id=callback_query.message.chat.id,
                message_id=status_msg_id
            )
        except TelegramBadRequest:
            pass

        await callback_query.message.answer(
            f"✅ <b>Альбом скачан!</b>\n\nОтправлено треков: {sent_count}/{tracks_count}",
            parse_mode='HTML'
        )

        if failed_tracks:
            failed_text = "❌ <b>Не получилось скачать:</b>\n\n"
            for i, (artist, title, track_url) in enumerate(failed_tracks, 1):
                failed_text += f"{i}. <a href=\"{track_url}\">{artist} - {title}</a>\n"
            await callback_query.message.answer(failed_text, parse_mode='HTML')
    except Exception as e:
        logging.error(f"Album single download error: {e}")
        await callback_query.message.answer(f"❌ Ошибка: {e}")


@router.callback_query(lambda c: c.data.startswith("album_zip_"))
async def callback_album_zip(callback_query: CallbackQuery):
    album_id = callback_query.data.replace("album_zip_", "")
    url = f"https://music.yandex.ru/album/{album_id}"
    user_states.pop(callback_query.from_user.id, None)

    album_info = await get_downloader().get_album_info(url)
    if not album_info:
        await callback_query.message.edit_text("❌ Не удалось получить информацию об альбоме.")
        return

    album_title, tracks_count = album_info
    status_msg = await callback_query.message.edit_text(
        f"💿 <b>Скачиваю альбом целиком. Может занять продолжительное время</b>\n\n"
        f"Альбом \"{album_title}\"\n\n"
        f"Скачано: 0/{tracks_count}",
        parse_mode='HTML'
    )

    sent_parts = 0

    async def progress_callback(downloaded: int, total: int):
        nonlocal status_msg
        try:
            await status_msg.edit_text(
                f"💿 <b>Скачиваю альбом целиком. Может занять продолжительное время</b>\n\n"
                f"Альбом \"{album_title}\"\n\n"
                f"Скачано: {downloaded}/{total}",
                parse_mode='HTML'
            )
        except TelegramBadRequest:
            pass

    try:
        zip_results = None
        async for result in get_downloader().download_album_zip_gen(url, progress_callback=progress_callback):
            if isinstance(result, list):
                zip_results = result

        if zip_results:
            total_parts = len(zip_results)
            await status_msg.edit_text(
                f"📦 <b>Файлы скачаны, упаковываются в ZIP</b>\n\n"
                f"Отправка {total_parts} частей...",
                parse_mode='HTML'
            )

            for i, (folder_name, zip_filename, zip_data) in enumerate(zip_results):
                success, error = await send_file_safe(callback_query.message, zip_data, zip_filename)
                if success:
                    sent_parts += 1
                    try:
                        await status_msg.edit_text(
                            f"📦 <b>Отправка ZIP ({sent_parts}/{total_parts})...</b>",
                            parse_mode='HTML'
                        )
                    except TelegramBadRequest:
                        pass
                else:
                    await callback_query.message.answer(f"❌ Ошибка отправки части {i+1}: {error}")
                await asyncio.sleep(1)

            await status_msg.edit_text(
                f"✅ <b>ZIP архив ({total_parts} частей) отправлен!</b>\n\nСодержимое:\n{folder_name}/",
                parse_mode='HTML'
            )
        else:
            await callback_query.message.answer("❌ Не удалось создать ZIP. Проверьте ссылку.")
    except Exception as e:
        logging.error(f"Album ZIP download error: {e}")
        await callback_query.message.answer(f"❌ Ошибка: {e}")


@router.callback_query(lambda c: c.data == "download_artist")
async def callback_download_artist(callback_query: CallbackQuery):
    await callback_query.message.edit_text(
        "👤 <b>Скачивание исполнителя</b>\n\n"
        "Отправьте ссылку на страницу исполнителя на Яндекс.Музыке.\n\n"
        "Формат: https://music.yandex.ru/artist/{artist_id}\n\n"
        "⚠️ Будут скачаны все альбомы и треки исполнителя.\n\n"
        "Нажмите /cancel для отмены.",
        reply_markup=get_cancel_keyboard(),
        parse_mode='HTML',
    )
    user_states[callback_query.from_user.id] = {"state": "waiting_artist_url"}


@router.callback_query(lambda c: c.data == "search_download")
async def callback_search_download(callback_query: CallbackQuery):
    await callback_query.message.edit_text(
        "🔍 <b>Поиск и скачивание</b>\n\n"
        "Введите название песни или исполнителя.\n\n"
        "Пример: <code>Queen Bohemian Rhapsody</code>\n\n"
        "Бот найдёт лучший результат и скачает его.\n\n"
        "Нажмите /cancel для отмены.",
        reply_markup=get_cancel_keyboard(),
        parse_mode='HTML',
    )
    user_states[callback_query.from_user.id] = {"state": "waiting_search_query"}


@router.message(Command("cancel"))
async def cmd_cancel(message: Message):
    user_id = message.from_user.id
    if user_id in user_states:
        user_states.pop(user_id, None)
    await message.answer(
        "❌ <b>Отменено</b>\n\n"
        "Выберите действие:",
        reply_markup=get_main_menu_keyboard(),
        parse_mode='HTML',
    )


@router.message()
async def handle_message(message: Message):
    user_id = message.from_user.id
    text = message.text.strip() if message.text else ""

    if user_id not in user_states:
        url_type = detect_url_type(text)
        if url_type == 'track':
            await handle_track_download(message, text)
        elif url_type == 'album':
            await handle_album_choice(message, text)
        elif url_type == 'artist':
            await handle_artist_download(message, text)
        else:
            await message.answer(
                "Используйте меню или команды:\n"
                "/start - Главное меню\n"
                "/cancel - Отмена",
                reply_markup=get_main_menu_keyboard(),
            )
        return

    state = user_states[user_id]["state"]
    user_states.pop(user_id, None)

    if state == "waiting_track_url":
        await handle_track_download(message, text)
    elif state == "waiting_album_url":
        await handle_album_download(message, text)
    elif state == "waiting_artist_url":
        await handle_artist_download(message, text)
    elif state == "waiting_search_query":
        await handle_search_download(message, text)
    else:
        await message.answer("Что-то пошло не так. Нажмите /start")


async def send_file_safe(message: Message, data: bytes, filename: str, chat_id: int = None):
    try:
        import tempfile
        import os
        file_size_mb = len(data) / (1024 * 1024)
        if file_size_mb > MAX_FILE_SIZE_MB:
            return False, f"Файл слишком большой ({file_size_mb:.1f}MB > {MAX_FILE_SIZE_MB}MB)"

        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
            f.write(data)
            temp_path = f.name

        try:
            await message.answer_document(FSInputFile(temp_path, filename=filename))
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        return True, None
    except TelegramBadRequest as e:
        if "File too large" in str(e):
            return False, f"Файл слишком большой для Telegram ({file_size_mb:.1f}MB)"
        return False, f"Telegram error: {e}"
    except Exception as e:
        return False, f"Error sending file: {e}"


async def handle_track_download(message: Message, url: str):
    user_id = message.from_user.id

    if not is_valid_ym_url(url):
        await message.answer(
            "❌ <b>Неверный формат ссылки</b>\n\n"
            "Отправьте ссылку вида:\n"
            "<code>https://music.yandex.ru/album/123/track/456</code>\n\n"
            "Или нажмите /cancel для отмены.",
            parse_mode='HTML',
        )
        return

    status_msg = await message.answer("⏳ Скачивание трека...")
    user_states.pop(user_id, None)

    try:
        result = await get_downloader().download_track(url)
        if result:
            artist, title, data = result
            filename = f"{sanitize_filename(artist)} - {sanitize_filename(title)}.mp3"
            success, error = await send_file_safe(message, data, filename)

            if success:
                await status_msg.edit_text("✅ <b>Трек скачан!</b>", parse_mode='HTML')
            else:
                await status_msg.edit_text(f"❌ Ошибка отправки: {error}")
        else:
            await status_msg.edit_text("❌ Не удалось скачать трек. Проверьте ссылку.")
    except Exception as e:
        logging.error(f"Track download error: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {e}")


async def handle_album_choice(message: Message, url: str):
    base_url = url.split('?')[0]
    if not re.match(r'https?://music\.yandex\.(?:ru|com|kz|ua|by)/album/\d+$', base_url):
        await message.answer(
            "❌ <b>Неверный формат ссылки</b>\n\n"
            "Отправьте ссылку на альбом:\n"
            "<code>https://music.yandex.ru/album/12345</code>\n\n"
            "Или нажмите /cancel для отмены.",
            parse_mode='HTML',
        )
        return

    album_id = re.search(r'/album/(\d+)', base_url).group(1)
    await message.answer(
        "💿 <b>Выберите способ скачивания:</b>",
        reply_markup=get_album_download_choice_keyboard(album_id),
        parse_mode='HTML',
    )


async def handle_artist_download(message: Message, url: str):
    user_id = message.from_user.id
    base_url = url.split('?')[0]

    if not re.match(r'https?://music\.yandex\.(?:ru|com|kz|ua|by)/artist/\d+$', base_url):
        await message.answer(
            "❌ <b>Неверный формат ссылки</b>\n\n"
            "Отправьте ссылку на исполнителя:\n"
            "<code>https://music.yandex.ru/artist/12345</code>\n\n"
            "Или нажмите /cancel для отмены.",
            parse_mode='HTML',
        )
        return

    status_msg = await message.answer(
        "⏳ Скачивание всех треков исполнителя...\n\n"
        "⚠️ Это может занять очень долго время!\n"
        "В зависимости от количества треков."
    )
    user_states.pop(user_id, None)

    try:
        tracks = await get_downloader().download_artist(base_url)
        if tracks:
            sent_count = 0
            for artist, title, data in tracks:
                filename = f"{sanitize_filename(artist)} - {sanitize_filename(title)}.mp3"
                success, error = await send_file_safe(message, data, filename)
                if success:
                    sent_count += 1
                await asyncio.sleep(0.5)

            await status_msg.edit_text(
                f"✅ <b>Исполнитель скачан!</b>\n\nОтправлено треков: {sent_count}/{len(tracks)}",
                parse_mode='HTML'
            )
        else:
            await status_msg.edit_text("❌ Не удалось скачать. Проверьте ссылку.")
    except Exception as e:
        logging.error(f"Artist download error: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {e}")


async def handle_search_download(message: Message, query: str):
    user_id = message.from_user.id

    if len(query) < 2:
        await message.answer(
            "❌ <b>Слишком короткий запрос</b>\n\n"
            "Введите название песни или исполнителя.\n\n"
            "Или нажмите /cancel для отмены.",
            parse_mode='HTML',
        )
        return

    status_msg = await message.answer("🔍 Поиск...")
    user_states.pop(user_id, None)

    try:
        tracks = await get_downloader().search_and_download(query)
        if tracks:
            sent_count = 0
            for artist, title, data in tracks:
                filename = f"{sanitize_filename(artist)} - {sanitize_filename(title)}.mp3"
                success, error = await send_file_safe(message, data, filename)
                if success:
                    sent_count += 1
                await asyncio.sleep(0.5)

            await status_msg.edit_text(f"✅ <b>Готово!</b>\n\nНайдено и отправлено: {sent_count}", parse_mode='HTML')
        else:
            await status_msg.edit_text("❌ Ничего не найдено. Попробуйте другой запрос.")
    except Exception as e:
        logging.error(f"Search download error: {e}")
        await status_msg.edit_text(f"❌ Ошибка поиска: {e}")