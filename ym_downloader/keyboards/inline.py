"""
Keyboards for Yandex Music Downloader Bot.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def get_main_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎵 Скачать трек", callback_data="download_track")],
        [InlineKeyboardButton(text="💿 Скачать альбом", callback_data="download_album")],
        [InlineKeyboardButton(text="👤 Скачать исполнителя", callback_data="download_artist")],
        [InlineKeyboardButton(text="🔍 Поиск и скачивание", callback_data="search_download")],
    ])


def get_cancel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


def get_album_download_choice_keyboard(album_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📁 Отдельными файлами", callback_data=f"album_single_{album_id}")],
        [InlineKeyboardButton(text="📦 Скачать ZIP", callback_data=f"album_zip_{album_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


def get_back_to_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Меню", callback_data="menu_main")],
    ])


def get_yes_no_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data="confirm_yes")],
        [InlineKeyboardButton(text="❌ Нет", callback_data="confirm_no")],
    ])