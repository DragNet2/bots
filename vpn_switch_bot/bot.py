import asyncio
import logging
import re
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from config import config
from keenetic import keenetic_client
from keyboards import get_route_interface_keyboard, get_routes_action_row

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()

ALLOWED_USER_IDS = config.ALLOWED_USER_IDS


class RouteSetup(StatesGroup):
    waiting_for_interface = State()
    waiting_for_name = State()
    waiting_for_routes = State()


ROUTE_IFACE_CHOICES = {
    "route_iface_vpn_on": ("VPN ON", config.ROUTE_IFACE_VPN_ON_DESC),
    "route_iface_rostelecom": ("Ростелеком", config.ROUTE_IFACE_ROSTELECOM_DESC),
    "route_iface_arznet": ("ArzNet", config.ROUTE_IFACE_ARZNET_DESC),
}


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

    builder.row(*get_routes_action_row())

    return builder.as_markup()


async def get_hosts_with_policies():
    await keenetic_client.authenticate()
    hosts = await keenetic_client.get_all_hosts()
    await keenetic_client.get_policies()

    vpn_on_internal = keenetic_client.get_policy_internal_name(config.VPN_ON_POLICY)
    vpn_off_internal = keenetic_client.get_policy_internal_name(config.VPN_OFF_POLICY)

    filtered = [h for h in hosts if h.get("policy") in (vpn_on_internal, vpn_off_internal)]

    return filtered, vpn_on_internal, vpn_off_internal


def parse_windows_routes(text: str) -> list[tuple[str, str, str]]:
    results = []
    pattern = re.compile(
        r"route\s+add\s+"
        r"(?P<address>\d{1,3}(?:\.\d{1,3}){3})\s+"
        r"mask\s+"
        r"(?P<mask>\d{1,3}(?:\.\d{1,3}){3})\s+"
        r"(?P<gateway>\d{1,3}(?:\.\d{1,3}){3})",
        re.IGNORECASE,
    )
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = pattern.search(line)
        if m:
            results.append((m.group("address"), m.group("mask"), m.group("gateway")))
    return results


@router.message(Command("start"))
@router.message(Command("list"))
@router.message(Command("menu"))
async def cmd_list(message: Message):
    if not is_admin(message):
        await message.answer("⛔ Доступ запрещён")
        return

    hosts, vpn_on_internal, vpn_off_internal = await get_hosts_with_policies()

    text, buttons, total_pages = format_device_list(hosts, vpn_on_internal, vpn_off_internal, 0)
    keyboard = get_keyboard(buttons, 0, total_pages)

    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.message(Command("routes"))
async def cmd_routes(message: Message, state: FSMContext):
    if not is_admin(message):
        await message.answer("⛔ Доступ запрещён")
        return
    await start_routes_flow(message.answer, state)


async def start_routes_flow(sender, state: FSMContext):
    await state.clear()
    await state.set_state(RouteSetup.waiting_for_interface)
    await sender(
        "🛣 <b>Настройка маршрутизации</b>\n\n"
        "Добавить маршрут для какого интерфейса?",
        reply_markup=get_route_interface_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "routes_start")
async def routes_start_cb(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await callback.answer()
    await start_routes_flow(callback.message.answer, state)


@router.callback_query(F.data == "routes_cancel")
async def routes_cancel_cb(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await state.clear()
    await callback.answer("Отменено")
    await callback.message.delete()


@router.callback_query(F.data.in_(ROUTE_IFACE_CHOICES.keys()))
async def route_iface_chosen(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    label, desc = ROUTE_IFACE_CHOICES[callback.data]
    await callback.answer()

    await state.update_data(iface_label=label, iface_desc=desc)
    await state.set_state(RouteSetup.waiting_for_name)

    await callback.message.edit_text(
        f"✅ Интерфейс: <b>{label}</b>\n\n"
        "Как назвать эту группу маршрутов?\n"
        "<i>Введите текстовое название/описание</i>",
        parse_mode="HTML",
    )


@router.message(RouteSetup.waiting_for_name)
async def route_name_entered(message: Message, state: FSMContext):
    if not is_admin(message):
        await message.answer("⛔ Доступ запрещён")
        return

    name = message.text.strip()
    if not name:
        await message.answer("Название не может быть пустым. Введите снова:")
        return

    await state.update_data(route_name=name)
    await state.set_state(RouteSetup.waiting_for_routes)

    await message.answer(
        f"✅ Название: <b>{name}</b>\n\n"
        "Теперь введите маршруты в формате Windows (один или несколько):\n"
        "<code>route add 31.57.158.84 mask 255.255.255.255 0.0.0.0</code>",
        parse_mode="HTML",
    )


@router.message(RouteSetup.waiting_for_routes)
async def routes_text_entered(message: Message, state: FSMContext):
    if not is_admin(message):
        await message.answer("⛔ Доступ запрещён")
        return

    data = await state.get_data()
    iface_label = data.get("iface_label", "?")
    iface_desc = data.get("iface_desc", "")
    route_name = data.get("route_name", "")

    routes = parse_windows_routes(message.text)
    if not routes:
        await message.answer(
            "❌ Не распознано ни одного маршрута.\n"
            "Ожидаемый формат (можно несколько строк):\n"
            "<code>route add IP mask MASK GATEWAY</code>",
            parse_mode="HTML",
        )
        return

    progress = await message.answer(
        f"⏳ Распознано маршрутов: {len(routes)}\n"
        f"Интерфейс: {iface_label}\n"
        f"Описание: {route_name}\n\n"
        "Подключаемся к роутеру..."
    )

    await keenetic_client.authenticate()
    await keenetic_client.get_interfaces()
    iface_internal = keenetic_client.get_interface_internal_name(iface_desc)
    if not iface_internal:
        await progress.edit_text(
            f"❌ Интерфейс «{iface_label}» (description: {iface_desc}) не найден в Keenetic.\n"
            f"Проверьте ROUTE_IFACE_*_DESC в .env"
        )
        await state.clear()
        return

    ok = 0
    failed = []
    for idx, (address, mask, gateway) in enumerate(routes, 1):
        try:
            res = await keenetic_client.add_static_route(
                address=address,
                mask=mask,
                gateway=gateway,
                interface_internal=iface_internal,
                description=route_name,
            )
            if res:
                ok += 1
            else:
                failed.append(f"{idx}. route add {address} mask {mask} {gateway}")
        except Exception as e:
            logger.exception(f"Failed to add route {address}")
            failed.append(f"{idx}. route add {address} mask {mask} {gateway} → {e}")

    lines = [f"✅ Готово! Добавлено: {ok}/{len(routes)}"]
    lines.append(f"Интерфейс: {iface_label} ({iface_internal})")
    lines.append(f"Описание: {route_name}")
    if failed:
        lines.append("")
        lines.append("❌ Не удалось добавить:")
        lines.extend(failed[:20])
        if len(failed) > 20:
            lines.append(f"... и ещё {len(failed) - 20}")

    await progress.edit_text("\n".join(lines))
    await state.clear()


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


@router.callback_query(F.data == "refresh")
async def refresh_list(callback: CallbackQuery):
    if not is_admin(callback):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return

    hosts, vpn_on_internal, vpn_off_internal = await get_hosts_with_policies()

    text, buttons, total_pages = format_device_list(hosts, vpn_on_internal, vpn_off_internal, 0)
    keyboard = get_keyboard(buttons, 0, total_pages)

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


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
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(router)

    logger.info("Bot starting...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
