from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import config


def get_main_keyboard() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text="📋 Список устройств", callback_data="refresh"),
    )
    return builder.as_markup()


def get_devices_keyboard(devices: list[dict], vpn_on: str, vpn_off: str) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for device in devices:
        mac = device.get("mac", "")
        name = device.get("name") or device.get("hostname") or mac[:17]
        builder.row(
            types.InlineKeyboardButton(
                text=f"📱 {name[:25]}",
                callback_data=f"device_{mac}"
            )
        )

    builder.row(types.InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh"))
    return builder.as_markup()


def get_confirm_keyboard(mac: str, policy: str, device_name: str) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(
            text=f"✅ Да, перенести в {policy}",
            callback_data=f"confirm_{mac}_{policy}"
        ),
        types.InlineKeyboardButton(
            text="❌ Отмена",
            callback_data=f"cancel_{mac}"
        )
    )
    return builder.as_markup()
