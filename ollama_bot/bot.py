#!/usr/bin/env python3
"""
ollama_bot — Telegram-бот мониторинга баланса и расходов Ollama Cloud.

Возможности:
- 💰 текущий баланс (GET /api/me на https://ollama.com — план, включённый
  месячный пул, остаток по extra usage)
- 📅 расход за сегодня и 🗓 за месяц (дельта баланса, время московское)
- 🤖 список доступных cloud-моделей (GET /api/tags, фильтр :cloud)
- ⚠️ уведомления при остатке ниже $5 / $2 / $1

Запускается на LV (systemd-юнит ollama_bot). Конфиг — .env рядом с bot.py.

Ollama Cloud с 31.08.2026 перешла на прозрачную по-токенную тарификацию
(https://ollama.com/blog/transparent-pricing). Официального публичного API
для баланса пока нет; используется внутренний эндпоинт дашборда /api/me,
который принимает Bearer-токен с API-ключом ollama.com. Если эндпоинт
изменится — бот покажет ошибку с подсказкой проверить ключ.
"""

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN не задан — укажите его в .env")

# API-ключ Ollama Cloud (https://ollama.com/settings/keys).
# Передаётся как Bearer в Authorization на https://ollama.com/api/*
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")
if not OLLAMA_API_KEY:
    raise SystemExit("OLLAMA_API_KEY не задан — укажите его в .env")

ALLOWED_CHATS = [
    c.strip()
    for c in os.getenv("ALLOWED_CHATS", "233590599").split(",")
    if c.strip()
]

# Уведомление приходит, когда остаток опускается ниже порога
ALERT_THRESHOLDS = [5.0, 2.0, 1.0]
CHECK_INTERVAL_MIN = int(os.getenv("CHECK_INTERVAL_MIN", "30"))

API_BASE = "https://ollama.com/api"
TZ = ZoneInfo("Europe/Moscow")
STATE_FILE = os.path.join(BASE_DIR, "state.json")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO
)
log = logging.getLogger("ollama_bot")


# ----------------------------- состояние (state.json) -----------------------------

def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    fd, tmp_path = tempfile.mkstemp(dir=BASE_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, STATE_FILE)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def update_snapshots(state: dict, balance: float) -> dict:
    """Фиксирует баланс при первом замере текущего дня/месяца (МСК).

    Расход периода считается как (замер на начало периода - текущий баланс).
    Если бот был выключен, замер сместится на момент запуска — точность
    ограничена этим (в сообщениях показывается время замера).
    """
    now = datetime.now(TZ)
    day = state.get("day") or {}
    if day.get("date") != now.strftime("%Y-%m-%d"):
        state["day"] = {
            "date": now.strftime("%Y-%m-%d"),
            "balance": balance,
            "at": now.isoformat(timespec="seconds"),
        }
    month = state.get("month") or {}
    if month.get("month") != now.strftime("%Y-%m"):
        state["month"] = {
            "month": now.strftime("%Y-%m"),
            "balance": balance,
            "at": now.isoformat(timespec="seconds"),
        }
    return state


# ----------------------------- Ollama Cloud API -----------------------------

def _auth() -> dict:
    return {"Authorization": f"Bearer {OLLAMA_API_KEY}"}


class AccountUnavailable(Exception):
    """Эндпоинт /api/me недоступен или вернул неожиданный формат."""


def fetch_account() -> dict:
    """Информация об аккаунте и балансе Ollama Cloud.

    Эндпоинт /api/me используется дашбордом https://ollama.com/settings —
    отдаёт JSON с планом, лимитами и текущим использованием. Формат может
    меняться без предупреждения; если ключ неверный или эндпоинт изменился —
    бросаем AccountUnavailable с понятным сообщением.
    """
    r = requests.get(
        f"{API_BASE}/me",
        headers={**_auth(), "Accept": "application/json"},
        timeout=30,
    )
    if r.status_code in (401, 403):
        raise AccountUnavailable(
            "ключ отклонён /api/me — проверьте OLLAMA_API_KEY "
            "на https://ollama.com/settings/keys"
        )
    if r.status_code == 404:
        raise AccountUnavailable(
            "эндпоинт /api/me не найден — Ollama изменила API; "
            "обновите бота"
        )
    r.raise_for_status()
    return r.json()


def normalize_account(data: dict) -> dict:
    """Приводит ответ /api/me к стабильной внутренней структуре.

    Возвращает dict с полями:
      - balance: float — доступный остаток (для алертов и расхода)
      - plan: str — название плана (Free / Pro / Max / Team / …)
      - included: float | None — размер включённого месячного пула ($)
      - extra: float | None — размер extra usage баланса ($)
      - used: float | None — использовано в текущем месяце ($)
      - renews_at: str | None — дата следующего обновления
      - raw: dict — полный ответ API для отладки
    """
    raw = data if isinstance(data, dict) else {}

    # Возможные ключи в разных версиях ответа /api/me
    plan = (
        raw.get("plan")
        or raw.get("plan_name")
        or raw.get("tier")
        or raw.get("subscription_plan")
        or "?"
    )

    def _money(*keys, default=None):
        for k in keys:
            if k in raw and raw[k] is not None:
                try:
                    return float(raw[k])
                except (TypeError, ValueError):
                    continue
        return default

    included = _money(
        "included_credits",
        "monthly_credits",
        "plan_credits",
        "included",
        default=None,
    )
    extra = _money(
        "extra_credits",
        "extra_balance",
        "add_on_balance",
        "extra",
        default=None,
    )
    used = _money("used", "usage", "total_usage", default=None)

    # Доступный остаток: суммируем всё доступное, вычитаем потраченное
    parts = [v for v in (included, extra) if v is not None]
    if parts:
        available = sum(parts) - (used or 0.0)
    else:
        # Если API отдаёт только агрегированный остаток — используем его
        available = _money(
            "balance",
            "available",
            "remaining",
            "credit_balance",
            default=0.0,
        ) or 0.0
        if used is not None and not parts:
            # Нечем компенсировать used; оставляем available как есть
            pass

    renews_at = (
        raw.get("renews_at")
        or raw.get("resets_at")
        or raw.get("next_billing_at")
        or raw.get("period_end")
    )

    return {
        "balance": max(float(available), 0.0),
        "plan": str(plan),
        "included": included,
        "extra": extra,
        "used": used,
        "renews_at": str(renews_at) if renews_at else None,
        "raw": raw,
    }


def fetch_cloud_models() -> list:
    """Список моделей Ollama Cloud (фильтр :cloud)."""
    r = requests.get(
        f"{API_BASE}/tags",
        headers={**_auth(), "Accept": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    items = r.json().get("models", []) or []
    cloud = []
    for m in items:
        name = m.get("name", "")
        if ":cloud" in name:
            cloud.append(m)
    # Сортируем по имени
    cloud.sort(key=lambda m: m.get("name", ""))
    return cloud


# ----------------------------- тексты сообщений -----------------------------

def balance_text(acc: dict) -> str:
    plan = escape(acc["plan"])
    lines = [
        "💰 <b>Баланс Ollama Cloud</b>",
        "",
        f"Остаток: <b>${acc['balance']:.2f}</b>",
        f"План: {plan}",
    ]
    if acc.get("included") is not None:
        lines.append(f"Включено в план: ${acc['included']:.2f}/мес")
    if acc.get("extra") is not None:
        lines.append(f"Extra usage: ${acc['extra']:.2f}")
    if acc.get("used") is not None:
        lines.append(f"Использовано в месяце: ${acc['used']:.2f}")
    if acc.get("renews_at"):
        lines.append(f"Обновление: {escape(acc['renews_at'])}")
    lines.append("\nПополнить: https://ollama.com/settings/billing")
    return "\n".join(lines)


def spend_text(state: dict, balance: float, period: str) -> str:
    snap = state.get("day") if period == "day" else state.get("month")
    if not snap or "balance" not in snap:
        return "❌ Нет данных — подождите пару минут, бот снимет первый замер баланса"

    spent = max(snap["balance"] - balance, 0.0)
    since = datetime.fromisoformat(snap["at"]).strftime("%d.%m %H:%M")
    title = "📅 Расход за сегодня" if period == "day" else "🗓 Расход за текущий месяц"
    return (
        f"{title} <i>(время МСК)</i>\n\n"
        f"Потрачено: <b>${spent:.2f}</b>\n"
        f"Замер от {since} МСК: ${snap['balance']:.2f}\n"
        f"Остаток сейчас: <b>${balance:.2f}</b>"
    )


def models_text(items: list) -> str:
    if not items:
        return "🤖 <b>Cloud-модели Ollama</b>\n\nСписок пуст — проверьте API-ключ"

    lines = [f"🤖 <b>Cloud-модели Ollama</b> — {len(items)} шт.\n"]
    # Показываем первые 25, остальное — счётчик
    for m in items[:25]:
        name = m.get("name", "?")
        size = m.get("size")
        ctx = m.get("context_length") or m.get("max_context")
        ctx_part = f" · ctx {ctx}" if ctx else ""
        size_part = f" · {size / (1024**3):.1f} GB" if size else ""
        lines.append(f"• <code>{escape(name)}</code>{size_part}{ctx_part}")
    if len(items) > 25:
        lines.append(f"\n…и ещё {len(items) - 25} моделей")
    lines.append("\nПолный список: https://ollama.com/search?c=cloud")
    return "\n".join(lines)


def alert_text(balance: float, threshold: float) -> str:
    return (
        "⚠️ <b>Низкий баланс Ollama Cloud</b>\n\n"
        f"Остаток: <b>${balance:.2f}</b> — ниже порога ${threshold:.2f}\n\n"
        "Пополнить: https://ollama.com/settings/billing"
    )


# ----------------------------- хендлеры -----------------------------

def get_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["💰 Баланс", "📅 Сегодня"], ["🗓 Месяц", "🤖 Модели"]],
        resize_keyboard=True,
    )


def authorized(update: Update) -> bool:
    return str(update.effective_chat.id) in ALLOWED_CHATS


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 <b>Ollama Cloud монитор</b>\n\n"
        "💰 Баланс — текущий остаток\n"
        "📅 Сегодня / 🗓 Месяц — расход за период\n"
        "🤖 Модели — список cloud-моделей\n"
        "⚠️ Уведомления при остатке ниже $5 / $2 / $1",
        parse_mode="HTML",
        reply_markup=get_main_menu(),
    )


async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    msg = await update.message.reply_text("⏳ Запрашиваю баланс...")
    try:
        data = await asyncio.to_thread(fetch_account)
        acc = normalize_account(data)
        state = load_state()
        update_snapshots(state, acc["balance"])
        state["last_balance"] = acc["balance"]
        state["last_plan"] = acc["plan"]
        save_state(state)
        await msg.edit_text(balance_text(acc), parse_mode="HTML")
    except AccountUnavailable as e:
        await msg.edit_text(f"❌ {escape(str(e))[:400]}")
    except Exception as e:
        log.warning("balance error: %s", e)
        await msg.edit_text(f"❌ Ошибка Ollama API: {escape(str(e))[:300]}")


async def show_spending(update: Update, context: ContextTypes.DEFAULT_TYPE, period: str):
    if not authorized(update):
        return
    msg = await update.message.reply_text("⏳ Считаю расход...")
    try:
        data = await asyncio.to_thread(fetch_account)
        acc = normalize_account(data)
        state = load_state()
        update_snapshots(state, acc["balance"])
        save_state(state)
        await msg.edit_text(
            spend_text(state, acc["balance"], period), parse_mode="HTML"
        )
    except AccountUnavailable as e:
        await msg.edit_text(f"❌ {escape(str(e))[:400]}")
    except Exception as e:
        log.warning("spending error: %s", e)
        await msg.edit_text(f"❌ Ошибка Ollama API: {escape(str(e))[:300]}")


async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_spending(update, context, "day")


async def month_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_spending(update, context, "month")


async def models_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    msg = await update.message.reply_text("⏳ Загружаю модели...")
    try:
        items = await asyncio.to_thread(fetch_cloud_models)
        await msg.edit_text(models_text(items), parse_mode="HTML")
    except Exception as e:
        log.warning("models error: %s", e)
        await msg.edit_text(f"❌ Ошибка Ollama API: {escape(str(e))[:300]}")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "💰 Баланс":
        await show_balance(update, context)
    elif text == "📅 Сегодня":
        await show_spending(update, context, "day")
    elif text == "🗓 Месяц":
        await show_spending(update, context, "month")
    elif text == "🤖 Модели":
        await models_cmd(update, context)


# ----------------------------- фоновая проверка алертов -----------------------------

async def check_balance_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        data = await asyncio.to_thread(fetch_account)
        acc = normalize_account(data)
    except Exception as e:
        log.warning("Проверка баланса не удалась: %s", e)
        return

    balance = acc["balance"]
    state = load_state()
    update_snapshots(state, balance)

    alerted = set(state.get("alerted", []))
    if balance >= max(ALERT_THRESHOLDS):
        alerted = set()  # баланс пополнен — пороги сбрасываются

    crossed = [t for t in ALERT_THRESHOLDS if balance < t and t not in alerted]
    if crossed:
        threshold = max(crossed)
        for chat_id in ALLOWED_CHATS:
            try:
                await context.bot.send_message(
                    chat_id, alert_text(balance, threshold), parse_mode="HTML"
                )
            except Exception as e:
                log.warning("Не удалось отправить алерт в %s: %s", chat_id, e)
        alerted.update(crossed)

    state["alerted"] = sorted(alerted)
    state["last_balance"] = balance
    state["last_plan"] = acc["plan"]
    state["last_check"] = datetime.now(TZ).isoformat(timespec="seconds")
    save_state(state)


# ----------------------------- запуск -----------------------------

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", show_balance))
    app.add_handler(CommandHandler("today", today_cmd))
    app.add_handler(CommandHandler("month", month_cmd))
    app.add_handler(CommandHandler("models", models_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))

    if app.job_queue:
        app.job_queue.run_repeating(
            check_balance_job, interval=CHECK_INTERVAL_MIN * 60, first=15
        )
    else:
        log.warning(
            "JobQueue недоступен — установите python-telegram-bot[job-queue]; "
            "уведомления о порогах работать не будут"
        )

    log.info("Бот запущен (проверка баланса каждые %s мин)", CHECK_INTERVAL_MIN)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()