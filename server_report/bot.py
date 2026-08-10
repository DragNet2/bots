#!/usr/bin/env python3
"""
msk_lv_report_bot - Telegram бот для отправки отчётов и управления сервисами

Запускается на LV, слушает команды в канале и отправляет отчёты.
"""

import os
import subprocess
import json
import asyncio
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Конфигурация
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8369771647:AAHzjNzayWeQvU-K2UfhuTyb01_8opnckBA")
SCRIPT_PATH = os.getenv("REPORT_SCRIPT", "/usr/local/bin/lv_report.sh")
CHECK_SERVICES_SCRIPT = os.getenv("CHECK_SERVICES_SCRIPT", "/home/bots/server_report/scripts/check_services.sh")
PULSE_SCRIPT = os.getenv("PULSE_SCRIPT", "/usr/local/bin/ukusongs-pulse.sh")

# Разрешённые chat_id (каналы/группы), которым бот отвечает
ALLOWED_CHATS = [
    "233590599",  # Личный чат пользователя
]

# Пагинация
SERVICES_PER_PAGE = 8


def get_main_menu():
    """Возвращает главное меню (ReplyKeyboard)"""
    keyboard = [
        ["📊 Отчёт", "👥 Пользователи"],
        ["📋 Статус сервисов", "💾 Бэкап БД"],
        ["🗑 Управление бэкапами", "🔔 Ukusongs Pulse"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветственное сообщение"""
    await update.message.reply_text(
        "👋 <b>Бот управления сервером</b>\n\n"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=get_main_menu()
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки меню"""
    text = update.message.text
    chat_id = str(update.message.chat.id)

    if chat_id not in ALLOWED_CHATS:
        await update.message.reply_text("⛔ Бот не авторизован для этого чата")
        return

    if text == "📊 Отчёт":
        await run_report(update, context)
    elif text == "👥 Пользователи":
        await show_users(update, context)
    elif text == "📋 Статус сервисов":
        await show_services_status(update, context, "LV", page=0)
    elif text == "💾 Бэкап БД":
        await run_backup(update, context)
    elif text == "🗑 Управление бэкапами":
        await show_backup_management(update, context)
    elif text == "🔔 Ukusongs Pulse":
        await pulse(update, context)


async def run_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выполняет LV отчёт"""
    status_msg = await update.message.reply_text("⏳ Собираю данные...")

    try:
        result = subprocess.run(
            [SCRIPT_PATH],
            capture_output=True,
            text=True,
            timeout=120
        )
        await context.bot.deleteMessage(chat_id=update.message.chat.id, message_id=status_msg.message_id)

        if result.returncode == 0:
            await update.message.reply_text(
                "✅ Отчёт LV:\n\n" + (result.stdout or "Готово")[:4000],
                parse_mode="HTML",
                reply_markup=get_main_menu()
            )
        else:
            await update.message.reply_text(f"❌ Ошибка: {result.stderr[:500]}")
    except subprocess.TimeoutExpired:
        await context.bot.deleteMessage(chat_id=update.message.chat.id, message_id=status_msg.message_id)
        await update.message.reply_text("❌ Таймаут выполнения скрипта")
    except Exception as e:
        await context.bot.deleteMessage(chat_id=update.message.chat.id, message_id=status_msg.message_id)
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:500]}")


async def show_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список пользователей"""
    status_msg = await update.message.reply_text("⏳ Загружаю пользователей...")

    try:
        result = subprocess.run(
            ["sudo", "-u", "postgres", "psql", "-d", "ukusongs", "-t", "-c",
             "SELECT display_name, email, created_at::date, last_login_at::date FROM users ORDER BY created_at DESC LIMIT 50;"],
            capture_output=True,
            text=True,
            timeout=30
        )
        await context.bot.deleteMessage(chat_id=update.message.chat.id, message_id=status_msg.message_id)

        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            if lines and lines[0]:
                users_text = "👥 <b>Пользователи</b> (последние 50):\n\n"
                for line in lines:
                    parts = line.split('|')
                    if len(parts) >= 4:
                        name = parts[0].strip() or "—"
                        email = parts[1].strip() or "—"
                        created = parts[2].strip()[:10]
                        last_login = parts[3].strip()[:10] if parts[3].strip() and parts[3].strip() != '-' else "никогда"
                        users_text += f"• <b>{name}</b>\n  {email}\n  рег: {created} / вход: {last_login}\n\n"
            else:
                users_text = "👥 Нет пользователей"

            await update.message.reply_text(
                users_text,
                parse_mode="HTML",
                reply_markup=get_main_menu()
            )
        else:
            await update.message.reply_text(f"❌ Ошибка: {result.stderr[:300]}")
    except subprocess.TimeoutExpired:
        await context.bot.deleteMessage(chat_id=update.message.chat.id, message_id=status_msg.message_id)
        await update.message.reply_text("❌ Таймаут выполнения")
    except Exception as e:
        await context.bot.deleteMessage(chat_id=update.message.chat.id, message_id=status_msg.message_id)
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:300]}")


async def run_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выполняет бэкап локально на LV"""
    status_msg = await update.message.reply_text("🔄 Запускаю бэкап БД...")

    try:
        result = subprocess.run(
            ["/usr/local/bin/ukusongs-db-backup.sh"],
            capture_output=True,
            text=True,
            timeout=120
        )
        await context.bot.deleteMessage(chat_id=update.message.chat.id, message_id=status_msg.message_id)

        if result.returncode == 0:
            output = result.stdout

            # Автолок последнего бэкапа
            lock_result = subprocess.run(
                ["/usr/local/bin/ukusongs-db-backup.sh", "list"],
                capture_output=True,
                text=True,
                timeout=30
            )
            if lock_result.returncode == 0:
                first_line = lock_result.stdout.strip().split('\n')[0]
                if first_line and '|' in first_line:
                    parts = first_line.split('|')
                    if len(parts) >= 5:
                        filename = parts[4]
                        # Лочим если ещё не залочен
                        if parts[3] != "yes":
                            subprocess.run(
                                ["/usr/local/bin/ukusongs-db-backup.sh", "lock", filename],
                                capture_output=True,
                                timeout=30
                            )

            await update.message.reply_text(
                "✅ Бэкап выполнен!\n\n" + output[:4000],
                parse_mode="HTML",
                reply_markup=get_main_menu()
            )
        else:
            await update.message.reply_text(f"❌ Ошибка: {result.stderr[:300]}")
    except subprocess.TimeoutExpired:
        await context.bot.deleteMessage(chat_id=update.message.chat.id, message_id=status_msg.message_id)
        await update.message.reply_text("❌ Таймаут выполнения")
    except Exception as e:
        await context.bot.deleteMessage(chat_id=update.message.chat.id, message_id=status_msg.message_id)
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:300]}")


async def show_backup_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню управления бэкапами"""
    keyboard = [
        [InlineKeyboardButton("📋 Список бэкапов", callback_data="backup:list")],
        [InlineKeyboardButton("🔒 Залочить бэкап", callback_data="backup:lock")],
        [InlineKeyboardButton("🔓 Разлочить бэкап", callback_data="backup:unlock")],
        [InlineKeyboardButton("🗑 Удалить бэкап", callback_data="backup:delete_select")],
    ]

    await update.message.reply_text(
        "🗑 <b>Управление бэкапами</b>\n\n"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def get_backup_list():
    """Получает список бэкапов"""
    result = subprocess.run(
        ["/usr/local/bin/ukusongs-db-backup.sh", "list"],
        capture_output=True,
        text=True,
        timeout=30
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def format_backup_display(line):
    """Форматирует строку бэкапа для отображения (HTML версия)
    Формат входа: DATE|SIZE_BYTES|SIZE_HUMAN|LOCKED|FILENAME
    Пример: 20260809_143000|20971520|20 MiB|yes|ukusongs_db_20260809_143000.dump
    Выход: 14.08 в 13:56 - <b>54</b> Мб (жирный - день.месяц и число размера)
    """
    parts = line.split('|')
    if len(parts) < 5:
        return None

    datetime_str = parts[0]  # YYYYMMDD_HHMMSS
    size_human = parts[2]
    is_locked = parts[3] == "yes"
    filename = parts[4]

    if len(datetime_str) != 15:
        return None

    # Парсим дату/время
    year = datetime_str[0:4]
    month = datetime_str[4:6]
    day = datetime_str[6:8]
    time_str = datetime_str[9:]

    # DD.MM в HH:MM - SIZE_NUMBER UNIT
    # Format time as HH:MM
    hours = datetime_str[9:11]
    minutes = datetime_str[11:13]
    time_display = f"{hours}:{minutes}"

    # Bold: DD.MM and SIZE_NUMBER
    import re
    size_match = re.match(r'^(\d+)\s*(.*)$', size_human)
    if size_match:
        size_num = size_match.group(1)
        size_unit = size_match.group(2)
        # Русские "Мб" вместо "MiB"
        size_unit_display = size_unit.replace("MiB", "Мб").replace("GiB", "Гб").replace("KiB", "Кб")
        display = f"<b>{day}.{month}</b> в {time_display} — <b>{size_num}</b> {size_unit_display}"
    else:
        display = f"<b>{day}.{month}</b> в {time_display} — {size_human}"

    if is_locked:
        display = "🔒 " + display

    return display


def format_backup_display_plain(line):
    """Форматирует строку бэкапа для отображения (plain text версия)
    """
    parts = line.split('|')
    if len(parts) < 5:
        return None

    datetime_str = parts[0]
    size_human = parts[2]
    is_locked = parts[3] == "yes"

    if len(datetime_str) != 15:
        return None

    year = datetime_str[0:4]
    month = datetime_str[4:6]
    day = datetime_str[6:8]
    time_str = datetime_str[9:]

    # Format time as HH:MM
    hours = datetime_str[9:11]
    minutes = datetime_str[11:13]
    time_display = f"{hours}:{minutes}"

    import re
    size_match = re.match(r'^(\d+)\s*(.*)$', size_human)
    if size_match:
        size_num = size_match.group(1)
        size_unit = size_match.group(2)
        size_unit_display = size_unit.replace("MiB", "Мб").replace("GiB", "Гб").replace("KiB", "Кб")
        display = f"{day}.{month} в {time_display} — {size_num} {size_unit_display}"
    else:
        display = f"{day}.{month} в {time_display} — {size_human}"

    if is_locked:
        display = "🔒 " + display

    return display


async def show_backup_list_for_action(update, context, action, title):
    """Показывает нумерованный список бэкапов с кнопками-цифрами и пагинацией (для text reply)"""
    backup_list_raw = await get_backup_list()

    if not backup_list_raw or backup_list_raw == "empty":
        await update.message.reply_text("📋 Нет бэкапов", reply_markup=get_main_menu())
        return

    lines = backup_list_raw.strip().split('\n')
    all_backups = []
    for line in lines:
        if not line.strip():
            continue
        display = format_backup_display(line)
        if not display:
            continue
        parts = line.split('|')
        filename = parts[4]
        is_locked = parts[3] == "yes"

        # Фильтруем по action
        if action == "lock" and is_locked:
            continue
        elif action == "unlock" and not is_locked:
            continue

        all_backups.append((display, filename))

    if not all_backups:
        await update.message.reply_text(
            f"📋 Нет бэкапов для {title}",
            reply_markup=get_main_menu()
        )
        return

    # Пагинация: 8 на страницу
    PER_PAGE = 8
    total = len(all_backups)
    total_pages = (total + PER_PAGE - 1) // PER_PAGE

    page = 0
    start_idx = page * PER_PAGE
    end_idx = min(start_idx + PER_PAGE, total)

    # Формируем текст - нумерованный список
    msg = f"🗑 <b>{title}</b>\n\n"
    for i in range(start_idx, end_idx):
        display, _ = all_backups[i]
        num = i + 1
        msg += f"{num}. {display}\n"

    # Кнопки-цифры
    keyboard = []
    row = []
    for i in range(start_idx, end_idx):
        num = i + 1
        callback_suffix = f"{action}:{all_backups[i][1]}"
        row.append(InlineKeyboardButton(str(num), callback_data=f"backup:{callback_suffix}"))

        if len(row) == 4:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    # Навигация
    nav_row = []
    if total_pages > 1:
        nav_row.append(InlineKeyboardButton("▶", callback_data=f"backup:{action}:p0"))

    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("◀ Назад", callback_data="backup:back")])

    await update.message.reply_text(
        msg,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


def get_services_status(server=None):
    """Получает статус сервисов через check_services.sh"""
    try:
        cmd = [CHECK_SERVICES_SCRIPT]
        if server:
            cmd.append(server.lower())

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
        else:
            return None
    except Exception as e:
        print(f"Error getting services: {e}")
        return None


# Русские названия сервисов
SERVICE_NAMES_RU = {
    "ukusongs-site": "Ukusongs (сайт)",
    "ukusongs_bot": "Ukusongs Bot",
    "tasktracker_bot": "Task Tracker Bot",
    "nginx": "Nginx",
    "postgresql@16-main": "PostgreSQL",
    "4strings": "4strings",
    "video_downloader": "Video Downloader",
    "vpn_switch_bot": "VPN Switch Bot",
    "ym_downloader": "Yandex Music Bot",
    "ukusongs-pulse.timer": "Пульс (мониторинг)",
    "ukusongs-db-backup.timer": "Бэкап (таймер)",
}

def format_service_status(service, index):
    """Форматирует одну строку сервиса"""
    name = service.get("name", "unknown")
    svc_type = service.get("type", "service")
    status = service.get("status", "unknown")

    # Русское название
    display_name = SERVICE_NAMES_RU.get(name, name)

    # Иконка статуса
    if status == "active":
        icon = "🟢"
    elif status in ("inactive", "dead", "failed"):
        icon = "🔴"
    else:
        icon = "🟡"

    # Тип
    type_icon = "⏰" if svc_type == "timer" else "🔧"

    return f"{index}. {icon} {type_icon} {display_name}"


def build_services_message(services, page, server):
    """Формирует текст сообщения и клавиатуру для страницы"""
    total = len(services)
    total_pages = (total + SERVICES_PER_PAGE - 1) // SERVICES_PER_PAGE if total > 0 else 1

    start_idx = page * SERVICES_PER_PAGE
    end_idx = min(start_idx + SERVICES_PER_PAGE, total)

    # Формируем текст
    server_name = server
    message = f"📋 <b>Статус сервисов</b> ({server_name})\n"
    message += f"Страница {page + 1}/{total_pages}\n\n"

    for i in range(start_idx, end_idx):
        service = services[i]
        message += format_service_status(service, i + 1) + "\n"

    # Формируем inline-кнопки
    keyboard = []
    row = []

    # Кнопки с цифрами (1-8) - в один ряд
    nums_per_row = 8
    for i in range(start_idx, end_idx):
        service = services[i]
        btn_num = i + 1
        action = "stop" if service.get("status") == "active" else "start"
        row.append(
            InlineKeyboardButton(
                f"{btn_num}",
                callback_data=f"svc_toggle:{server}:{i}:{action}"
            )
        )
        if len(row) == nums_per_row:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    # Кнопки навигации
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀ Назад", callback_data=f"svc_page:{server}:{page - 1}"))
    else:
        nav_row.append(InlineKeyboardButton("◀", callback_data=f"svc_page:{server}:0"))

    nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data=f"svc_page:{server}:0"))

    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Далее ▶", callback_data=f"svc_page:{server}:{page + 1}"))
    else:
        nav_row.append(InlineKeyboardButton("▶", callback_data=f"svc_page:{server}:0"))

    keyboard.append(nav_row)

    return message, InlineKeyboardMarkup(keyboard)


async def show_services_status(update, context, server, page=0, edit=False, callback_query=None):
    """Показывает статус сервисов выбранного сервера с пагинацией"""
    services = get_services_status(server)

    if services is None:
        error_msg = "❌ Не удалось получить статус сервисов"
        if edit and callback_query:
            await callback_query.edit_message_text(error_msg)
        else:
            await update.message.reply_text(error_msg)
        return

    # Фильтруем по серверу (на всякий случай)
    services = [s for s in services if s.get("server") == server]

    message_text, keyboard = build_services_message(services, page, server)

    # Сохраняем в context для использования в callback
    context.user_data["services"] = {server: services}
    context.user_data["services_page"] = {server: page}

    if edit and callback_query:
        try:
            await callback_query.edit_message_text(
                message_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception as e:
            await callback_query.answer(f"Ошибка: {e}", show_alert=True)
    else:
        await update.message.reply_text(
            message_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик inline-кнопок"""
    query = update.callback_query
    await query.answer()

    data = query.data
    chat_id = str(query.message.chat.id)

    if chat_id not in ALLOWED_CHATS:
        await query.answer("⛔ Бот не авторизован", show_alert=True)
        return

    if data.startswith("svc_page:"):
        # Пагинация
        parts = data.split(":")
        server = parts[1]
        page = int(parts[2])

        services_data = context.user_data.get("services", {})
        services = services_data.get(server, [])

        if not services:
            services = get_services_status(server)
            if services:
                services = [s for s in services if s.get("server") == server]

        if services:
            context.user_data.setdefault("services", {})[server] = services
            message_text, keyboard = build_services_message(services, page, server)
            context.user_data.setdefault("services_page", {})[server] = page
            try:
                await query.edit_message_text(
                    message_text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            except Exception as e:
                await query.answer(f"Ошибка: {e}", show_alert=True)
        else:
            await query.answer("❌ Не удалось загрузить данные", show_alert=True)

    elif data.startswith("svc_toggle:"):
        # Toggle сервиса
        parts = data.split(":")
        server = parts[1]
        idx = int(parts[2])
        action = parts[3]  # "start" или "stop"

        services_data = context.user_data.get("services", {})
        services = services_data.get(server, [])

        if not services or idx >= len(services):
            await query.answer("❌ Сервис не найден", show_alert=True)
            return

        service = services[idx]
        service_name = service.get("name")

        # Выполняем toggle
        await query.answer(f"⏳ {action} {service_name}...")

        success = await toggle_service(service_name, action)

        if success:
            await query.answer(f"✅ {service_name} {action}ed", show_alert=True)
            # Обновляем статус
            services = get_services_status(server)
            if services:
                services = [s for s in services if s.get("server") == server]
            context.user_data.setdefault("services", {})[server] = services
            page = context.user_data.setdefault("services_page", {}).get(server, 0)
            message_text, keyboard = build_services_message(services, page, server)
            try:
                await query.edit_message_text(
                    message_text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            except Exception:
                pass
        else:
            await query.answer(f"❌ Ошибка {action} {service_name}", show_alert=True)

    elif data.startswith("backup:") and ":p" in data:
        # Пагинация - например backup:lock:p0
        parts = data.split(":")
        action = parts[1]
        # page parsing handled in show_backup_list_for_callback
        await show_backup_list_for_callback(query, context, action, action.capitalize() + " бэкап")

    elif data == "backup:back":
        # Возврат к меню управления бэкапами
        keyboard = [
            [InlineKeyboardButton("📋 Список бэкапов", callback_data="backup:list")],
            [InlineKeyboardButton("🔒 Залочить бэкап", callback_data="backup:lock")],
            [InlineKeyboardButton("🔓 Разлочить бэкап", callback_data="backup:unlock")],
            [InlineKeyboardButton("🗑 Удалить бэкап", callback_data="backup:delete_select")],
        ]
        await query.edit_message_text(
            "🗑 <b>Управление бэкапами</b>\n\n"
            "Выберите действие:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "backup:list":
        backup_list = await get_backup_list()
        msg = "📋 <b>Список бэкапов</b>\n\n"
        keyboard = []
        if backup_list and backup_list != "empty":
            lines = backup_list.split('\n')
            for line in lines:
                if not line.strip():
                    continue
                display = format_backup_display(line)
                if display:
                    msg += display + "\n"
        else:
            msg += "Нет бэкапов"

        keyboard.append([InlineKeyboardButton("◀ Назад", callback_data="backup:back")])
        await query.edit_message_text(
            msg,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "backup:lock":
        await show_backup_list_for_callback(query, context, "lock", "Залочить бэкап")

    elif data == "backup:unlock":
        await show_backup_list_for_callback(query, context, "unlock", "Разлочить бэкап")

    elif data == "backup:delete_select":
        await show_backup_list_for_callback(query, context, "delete", "Удалить бэкап")

    elif data.startswith("backup:lock:"):
        filename = data.replace("backup:lock:", "")
        result = subprocess.run(
            ["/usr/local/bin/ukusongs-db-backup.sh", "lock", filename],
            capture_output=True, text=True, timeout=30
        )
        await query.answer(result.stdout.strip() or "❌ Ошибка", show_alert=True)

    elif data.startswith("backup:unlock:"):
        filename = data.replace("backup:unlock:", "")
        result = subprocess.run(
            ["/usr/local/bin/ukusongs-db-backup.sh", "unlock", filename],
            capture_output=True, text=True, timeout=30
        )
        await query.answer(result.stdout.strip() or "❌ Ошибка", show_alert=True)

    elif data.startswith("backup:delete:"):
        filename = data.replace("backup:delete:", "")
        result = subprocess.run(
            ["/usr/local/bin/ukusongs-db-backup.sh", "delete", filename],
            capture_output=True, text=True, timeout=30
        )
        await query.answer(result.stdout.strip() or "❌ Ошибка", show_alert=True)


async def show_backup_list_for_callback(query, context, action, title):
    """Показывает нумерованный список бэкапов с кнопками-цифрами и пагинацией"""
    backup_list_raw = await get_backup_list()

    if not backup_list_raw or backup_list_raw == "empty":
        await query.edit_message_text(
            "📋 Нет бэкапов",
            parse_mode="HTML"
        )
        return

    # Получаем текущую страницу из callback_data
    page = 0
    callback_data_parts = query.data.split(":")
    if len(callback_data_parts) > 2:
        try:
            page = int(callback_data_parts[2])
        except:
            page = 0

    lines = backup_list_raw.strip().split('\n')
    all_backups = []
    for line in lines:
        if not line.strip():
            continue
        display = format_backup_display(line)
        if not display:
            continue
        parts = line.split('|')
        filename = parts[4]
        is_locked = parts[3] == "yes"

        # Фильтруем по action
        if action == "lock" and is_locked:
            continue
        elif action == "unlock" and not is_locked:
            continue

        all_backups.append((display, filename))

    if not all_backups:
        await query.edit_message_text(
            f"📋 Нет бэкапов для {title}",
            parse_mode="HTML"
        )
        return

    # Пагинация: 8 на страницу
    PER_PAGE = 8
    total = len(all_backups)
    total_pages = (total + PER_PAGE - 1) // PER_PAGE

    start_idx = page * PER_PAGE
    end_idx = min(start_idx + PER_PAGE, total)

    # Формируем текст - нумерованный список
    msg = f"🗑 <b>{title}</b>\n\n"
    for i in range(start_idx, end_idx):
        display, _ = all_backups[i]
        num = i + 1
        msg += f"{num}. {display}\n"

    # Кнопки-цифры
    keyboard = []
    row = []
    for i in range(start_idx, end_idx):
        num = i + 1
        callback_suffix = f"{action}:{all_backups[i][1]}"
        row.append(InlineKeyboardButton(str(num), callback_data=f"backup:{callback_suffix}"))

        if len(row) == 4:  # 4 кнопки в ряд
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    # Навигация
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀", callback_data=f"backup:{action}:p{page-1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("▶", callback_data=f"backup:{action}:p{page+1}"))

    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("◀ Назад", callback_data="backup:back")])

    await query.edit_message_text(
        msg,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def toggle_service(service_name, action):
    """Переключает состояние сервиса (start/stop) на LV"""
    try:
        cmd = ["systemctl", action, service_name]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode == 0
    except Exception as e:
        print(f"Error toggling service: {e}")
        return False


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет отчёт (команда /report)"""
    chat_id = str(update.effective_chat.id)

    if chat_id not in ALLOWED_CHATS:
        await update.message.reply_text("⛔ Бот не авторизован для этого чата")
        return

    await update.message.reply_text(
        "Выберите действие:",
        reply_markup=get_main_menu()
    )


async def backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запускает бэкап БД (команда /backup)"""
    chat_id = str(update.effective_chat.id)

    if chat_id not in ALLOWED_CHATS:
        await update.message.reply_text("⛔ Бот не авторизован для этого чата")
        return

    await update.message.reply_text(
        "Выберите действие:",
        reply_markup=get_main_menu()
    )


async def pulse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выполняет проверку Ukusongs Pulse и отправляет сводку (команда /pulse)"""
    chat_id = str(update.effective_chat.id)

    if chat_id not in ALLOWED_CHATS:
        await update.message.reply_text("⛔ Бот не авторизован для этого чата")
        return

    status_msg = await update.message.reply_text("⏳ Проверяю Ukusongs Pulse...")

    try:
        result = subprocess.run(
            [PULSE_SCRIPT],
            capture_output=True,
            text=True,
            timeout=60
        )
        await context.bot.deleteMessage(chat_id=chat_id, message_id=status_msg.message_id)

        # Выводим сырой output от скрипта
        output = result.stdout.strip()

        if output:
            # Убираем ANSI коды если есть
            output_clean = output.replace('\033[0m', '').replace('\033[0;32m', '').replace('\033[0;31m', '').replace('\033[1;33m', '')
            await update.message.reply_text(output_clean, parse_mode="HTML")
        else:
            await update.message.reply_text("📊 <b>Ukusongs Pulse</b>\n\nНе удалось получить данные", parse_mode="HTML")

    except subprocess.TimeoutExpired:
        await context.bot.deleteMessage(chat_id=chat_id, message_id=status_msg.message_id)
        await update.message.reply_text("❌ Таймаут выполнения проверки")
    except Exception as e:
        await context.bot.deleteMessage(chat_id=chat_id, message_id=status_msg.message_id)
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:500]}")


def main():
    """Запуск бота"""
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("backup", backup))
    app.add_handler(CommandHandler("pulse", pulse))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))

    print("Бот запущен. Ожидание команд...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()