#!/usr/bin/env python3
"""
msk_lv_report_bot - Telegram бот для отправки отчётов по запросу

Запускается на LV, слушает команды в канале и отправляет отчёты.
"""

import os
import subprocess
import asyncio
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Конфигурация
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8369771647:AAHyS9haEDBtpzjOlAyUWcpar4gbk-trfyg")
SCRIPT_PATH = os.getenv("REPORT_SCRIPT", "/opt/msk_lv_report/scripts/collect_and_notify.sh")

# Разрешённые chat_id (каналы/группы), которым бот отвечает
ALLOWED_CHATS = [
    "233590599",  # Личный чат пользователя
]


def get_main_menu():
    """Возвращает главное меню (ReplyKeyboard)"""
    keyboard = [
        ["📊 Отчёт", "💾 Бэкап БД"]
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
    elif text == "💾 Бэкап БД":
        await run_backup(update, context, chat_id)


async def run_report(update, context, chat_id):
    """Выполняет отчёт"""
    status_msg = await update.message.reply_text("⏳ Собираю данные...")

    try:
        result = subprocess.run(
            [SCRIPT_PATH],
            capture_output=True,
            text=True,
            timeout=120
        )
        await context.bot.deleteMessage(chat_id=chat_id, message_id=status_msg.message_id)

        if result.returncode != 0:
            await update.message.reply_text(f"❌ Ошибка: {result.stderr[:500]}")
    except subprocess.TimeoutExpired:
        await context.bot.deleteMessage(chat_id=chat_id, message_id=status_msg.message_id)
        await update.message.reply_text("❌ Таймаут выполнения скрипта")
    except Exception as e:
        await context.bot.deleteMessage(chat_id=chat_id, message_id=status_msg.message_id)
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:500]}")


async def run_backup(update, context, chat_id):
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
        await context.bot.deleteMessage(chat_id=chat_id, message_id=status_msg.message_id)

        if result.returncode == 0:
            await update.message.reply_text(
                "✅ Бэкап запущен!",
                reply_markup=get_main_menu()
            )
        else:
            await update.message.reply_text(f"❌ Ошибка: {result.stderr[:300]}")
    except subprocess.TimeoutExpired:
        await context.bot.deleteMessage(chat_id=chat_id, message_id=status_msg.message_id)
        await update.message.reply_text("❌ Таймаут выполнения")
    except Exception as e:
        await context.bot.deleteMessage(chat_id=chat_id, message_id=status_msg.message_id)
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:300]}")


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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))

    print("Бот запущен. Ожидание команд...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
