"""
Menu handlers for Yandex Music Downloader Bot.
"""
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command

from keyboards import get_main_menu_keyboard, get_back_to_menu_keyboard

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message):
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


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    await message.answer(
        "📋 <b>Главное меню</b>\n\n"
        "Выберите действие:",
        reply_markup=get_main_menu_keyboard(),
        parse_mode='HTML',
    )


@router.message(F.text == "📋 Меню")
async def handle_menu_button(message: Message):
    await message.answer(
        "📋 <b>Главное меню</b>\n\n"
        "Выберите действие:",
        reply_markup=get_main_menu_keyboard(),
        parse_mode='HTML',
    )


@router.callback_query(lambda c: c.data == "menu_main")
async def callback_menu_main(callback_query: CallbackQuery):
    await callback_query.message.edit_text(
        "📋 <b>Главное меню</b>\n\n"
        "Выберите действие:",
        reply_markup=get_main_menu_keyboard(),
        parse_mode='HTML',
    )


@router.callback_query(lambda c: c.data == "cancel")
async def callback_cancel(callback_query: CallbackQuery):
    await callback_query.message.edit_text(
        "❌ <b>Отменено</b>\n\n"
        "Выберите действие:",
        reply_markup=get_main_menu_keyboard(),
        parse_mode='HTML',
    )