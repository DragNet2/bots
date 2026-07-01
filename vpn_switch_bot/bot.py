import asyncio
import logging
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from config import config
from keenetic import keenetic_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()

ALLOWED_USER_IDS = config.ALLOWED_USER_IDS


def is_admin(update) -> bool:
    if isinstance(update, Message):
        user_id = update.from_user.id
    elif isinstance(update, CallbackQuery):
        user_id = update.from_user.id
    else:
        return False
    return user_id in ALLOWED_USER_IDS

ITEMS_PER_PAGE = 8


def get_device_status(host: dict, vpn_on_internal: str, vpn_off_internal: str) -> tuple[str, str]:
    policy = host.get("policy", "")
    if policy == vpn_on_internal:
        return "ON", "🟢"
    elif policy == vpn_off_internal:
        return "OFF", "🔴"
    return "?", "⚪️"


def format_device_list(hosts: list[dict], vpn_on_internal: str, vpn_off_internal: str, page: int = 0) -> tuple[str, list[dict], int]:
    start = page * ITEMS_PER_PAGE
    end = min(start + ITEMS_PER_PAGE, len(hosts))
    page_hosts = hosts[start:end]
    total_pages = (len(hosts) - 1) // ITEMS_PER_PAGE + 1 if hosts else 1

    lines = []
    buttons = []

    for i, h in enumerate(page_hosts, start=start + 1):
        name = h.get("name") or "Без имени"
        status, icon = get_device_status(h, vpn_on_internal, vpn_off_internal)
        lines.append(f"{i}. {icon} {name}")
        buttons.append(h)

    text = "🔌 <b>VPN Policy Manager</b>\n\n"
    if hosts:
        text += "\n".join(lines)
    else:
        text += "Нет устройств в политиках"

    if total_pages > 1:
        text += f"\n\n📄 Страница {page + 1}/{total_pages}"

    return text, buttons, total_pages


def get_keyboard(buttons: list, page: int, total_pages: int):
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram import types

    builder = InlineKeyboardBuilder()

    for i, btn in enumerate(buttons, start=page * ITEMS_PER_PAGE + 1):
        mac = btn.get("mac", "").lower().replace(":", "")
        builder.add(types.InlineKeyboardButton(text=str(i), callback_data=f"toggle_{mac}"))

    builder.adjust(8)

    nav_row = []
    if total_pages > 1:
        if page > 0:
            nav_row.append(types.InlineKeyboardButton(text="◀️", callback_data=f"page_{page - 1}"))
        if page < total_pages - 1:
            nav_row.append(types.InlineKeyboardButton(text="▶️", callback_data=f"page_{page + 1}"))

    if nav_row:
        builder.row(*nav_row)

    return builder.as_markup()


async def get_hosts_with_policies():
    await keenetic_client.authenticate()
    hosts = await keenetic_client.get_all_hosts()
    await keenetic_client.get_policies()

    vpn_on_internal = keenetic_client.get_policy_internal_name(config.VPN_ON_POLICY)
    vpn_off_internal = keenetic_client.get_policy_internal_name(config.VPN_OFF_POLICY)

    filtered = [h for h in hosts if h.get("policy") in (vpn_on_internal, vpn_off_internal)]

    return filtered, vpn_on_internal, vpn_off_internal


@router.message(Command("start"))
@router.message(Command("list"))
async def cmd_list(message: Message):
    if not is_admin(message):
        await message.answer("⛔ Доступ запрещён")
        return

    hosts, vpn_on_internal, vpn_off_internal = await get_hosts_with_policies()

    text, buttons, total_pages = format_device_list(hosts, vpn_on_internal, vpn_off_internal, 0)
    keyboard = get_keyboard(buttons, 0, total_pages)

    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("toggle_"))
async def toggle_device(callback: CallbackQuery):
    if not is_admin(callback):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    mac_raw = callback.data.replace("toggle_", "")
    mac = ":".join([mac_raw[i:i+2] for i in range(0, len(mac_raw), 2)])

    hosts, vpn_on_internal, vpn_off_internal = await get_hosts_with_policies()

    host = None
    for h in hosts:
        if h.get("mac", "").lower() == mac.lower():
            host = h
            break

    if not host:
        await callback.answer("❌ Устройство не найдено", show_alert=True)
        return

    current_policy = host.get("policy", "")
    name = host.get("name") or "Устройство"

    if current_policy == vpn_on_internal:
        target_policy = config.VPN_OFF_POLICY
        target_icon = "🔴"
        target_status = "Выключаем VPN"
    else:
        target_policy = config.VPN_ON_POLICY
        target_icon = "🟢"
        target_status = "Включаем VPN"

    await callback.answer("⏳", show_alert=False)
    intermediate_msg = await callback.message.answer(f"⏳ {name} → {target_icon} {target_status}...", parse_mode="HTML")

    success = await keenetic_client.set_host_policy(mac, target_policy)

    if success:
        hosts, vpn_on_internal, vpn_off_internal = await get_hosts_with_policies()

        text, buttons, total_pages = format_device_list(hosts, vpn_on_internal, vpn_off_internal, 0)
        keyboard = get_keyboard(buttons, 0, total_pages)
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await intermediate_msg.delete()
    else:
        await intermediate_msg.edit_text("❌ Ошибка при переключении политики")
        await asyncio.sleep(2)
        hosts, vpn_on_internal, vpn_off_internal = await get_hosts_with_policies()
        text, buttons, total_pages = format_device_list(hosts, vpn_on_internal, vpn_off_internal, 0)
        keyboard = get_keyboard(buttons, 0, total_pages)
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data.startswith("page_"))
async def change_page(callback: CallbackQuery):
    if not is_admin(callback):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    page = int(callback.data.split("_")[1])

    hosts, vpn_on_internal, vpn_off_internal = await get_hosts_with_policies()

    text, buttons, total_pages = format_device_list(hosts, vpn_on_internal, vpn_off_internal, page)
    keyboard = get_keyboard(buttons, page, total_pages)

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


async def main():
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Bot starting...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
