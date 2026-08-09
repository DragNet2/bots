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
SCRIPT_PATH = os.getenv("REPORT_SCRIPT", "/home/bots/msk_lv_report/scripts/collect_and_notify.sh")
CHECK_SERVICES_SCRIPT = os.getenv("CHECK_SERVICES_SCRIPT", "/home/bots/msk_lv_report/scripts/check_services.sh")

# Разрешённые chat_id (каналы/группы), которым бот отвечает
ALLOWED_CHATS = [
    "233590599",  # Личный чат пользователя
]

# Пагинация
SERVICES_PER_PAGE = 8


def get_main_menu():
    """Возвращает главное меню (ReplyKeyboard)"""
    keyboard = [
        ["📊 Отчёт", "📋 Статус сервисов"],
        ["💾 Бэкап БД"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветственное сообщение"""
    await update.message.reply_text(
        "👋 <b>Бот управления MSK/LV</b>\n\n"
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
        await run_report(update, context, chat_id)
    elif text == "📋 Статус сервисов":
        await show_server_choice(update, context)
    elif text == "💾 Бэкап БД":
        await run_backup(update, context, chat_id)


async def show_server_choice(update, context):
    """Показывает выбор сервера (LV или MSK)"""
    keyboard = [
        [InlineKeyboardButton("🇪🇪 LV", callback_data="services:LV"),
         InlineKeyboardButton("MSK 🇷🇺", callback_data="services:MSK")],
    ]

    await update.message.reply_text(
        "📋 <b>Статус сервисов</b>\n\n"
        "Выберите сервер:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def run_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выполняет отчёт"""
    status_msg = await update.message.reply_text("⏳ Собираю данные...")

    try:
        result = subprocess.run(
            [SCRIPT_PATH],
            capture_output=True,
            text=True,
            timeout=120
        )
        await context.bot.deleteMessage(chat_id=update.message.chat.id, message_id=status_msg.message_id)

        if result.returncode != 0:
            await update.message.reply_text(f"❌ Ошибка: {result.stderr[:500]}")
    except subprocess.TimeoutExpired:
        await context.bot.deleteMessage(chat_id=update.message.chat.id, message_id=status_msg.message_id)
        await update.message.reply_text("❌ Таймаут выполнения скрипта")
    except Exception as e:
        await context.bot.deleteMessage(chat_id=update.message.chat.id, message_id=status_msg.message_id)
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:500]}")


async def run_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выполняет бэкап"""
    status_msg = await update.message.reply_text("🔄 Запускаю бэкап БД...")

    try:
        result = subprocess.run(
            ["ssh", "-i", "/root/.ssh/id_ed25519_lv_to_msk",
             "-o", "StrictHostKeyChecking=no",
             "-o", "ConnectTimeout=30",
             "ubuntu@195.209.214.24",
             "sudo systemctl start ukusongs-db-backup"],
            capture_output=True,
            text=True,
            timeout=60
        )
        await context.bot.deleteMessage(chat_id=update.message.chat.id, message_id=status_msg.message_id)

        if result.returncode == 0:
            await update.message.reply_text(
                "✅ Бэкап запущен!",
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


def format_service_status(service, index):
    """Форматирует одну строку сервиса"""
    name = service.get("name", "unknown")
    status = service.get("status", "unknown")

    # Иконка статуса
    if status == "active":
        icon = "🟢"
    elif status in ("inactive", "dead", "failed"):
        icon = "🔴"
    else:
        icon = "🟡"

    return f"{index}. {icon} {name}"


def build_services_message(services, page, server):
    """Формирует текст сообщения и клавиатуру для страницы"""
    total = len(services)
    total_pages = (total + SERVICES_PER_PAGE - 1) // SERVICES_PER_PAGE if total > 0 else 1

    start_idx = page * SERVICES_PER_PAGE
    end_idx = min(start_idx + SERVICES_PER_PAGE, total)

    # Формируем текст
    server_name = "LV" if server == "LV" else "MSK"
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

    # Кнопка возврата к выбору сервера
    keyboard.append([InlineKeyboardButton("🔙 К выбору сервера", callback_data="services:back")])

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

    if data == "services:back":
        # Возврат к выбору сервера
        keyboard = [
            [InlineKeyboardButton("🇪🇪 LV", callback_data="services:LV"),
             InlineKeyboardButton("MSK 🇷🇺", callback_data="services:MSK")],
        ]
        await query.edit_message_text(
            "📋 <b>Статус сервисов</b>\n\n"
            "Выберите сервер:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("services:"):
        # Выбор сервера
        server = data.split(":")[1]
        await show_services_status(None, context, server, page=0, edit=True, callback_query=query)

    elif data.startswith("svc_page:"):
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

        success = await toggle_service(service_name, server, action)

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


async def toggle_service(service_name, server, action):
    """Переключает состояние сервиса (start/stop)"""
    try:
        if server == "LV":
            # Локально на LV
            cmd = ["systemctl", action, service_name]
        else:
            # На MSK через SSH
            cmd = [
                "ssh", "-i", "/root/.ssh/id_ed25519_lv_to_msk",
                "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=30",
                "ubuntu@195.209.214.24",
                f"sudo systemctl {action} {service_name}"
            ]

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


def main():
    """Запуск бота"""
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("backup", backup))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))

    print("Бот запущен. Ожидание команд...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()