#!/usr/bin/env python3
"""
openrouter_bot — Telegram-бот мониторинга баланса и расходов OpenRouter.

Возможности:
- 💰 текущий баланс (GET /api/v1/credits)
- 📅 расход за сегодня и 🗓 за месяц (дельта баланса, время московское)
- 🤖 расход по моделям (GET /api/v1/activity, нужен management-ключ)
- ⚠️ уведомления при остатке ниже $5 / $2 / $1

Запускается на LV (systemd-юнит openrouter_bot). Конфиг — .env рядом с bot.py.
"""

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
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

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
if not OPENROUTER_API_KEY:
    raise SystemExit("OPENROUTER_API_KEY не задан — укажите его в .env")

# Management-ключ нужен только для /activity (расход по моделям).
# Если не задан — используется основной ключ (сработает, если у него есть права management).
OPENROUTER_MGMT_KEY = os.getenv("OPENROUTER_MGMT_KEY") or OPENROUTER_API_KEY

ALLOWED_CHATS = [
    c.strip()
    for c in os.getenv("ALLOWED_CHATS", "233590599").split(",")
    if c.strip()
]

# Уведомление приходит, когда остаток опускается ниже порога
ALERT_THRESHOLDS = [5.0, 2.0, 1.0]
CHECK_INTERVAL_MIN = int(os.getenv("CHECK_INTERVAL_MIN", "30"))

API_BASE = "https://openrouter.ai/api/v1"
TZ = ZoneInfo("Europe/Moscow")
STATE_FILE = os.path.join(BASE_DIR, "state.json")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO
)
log = logging.getLogger("openrouter_bot")


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


# ----------------------------- OpenRouter API -----------------------------

def _auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def fetch_credits() -> dict:
    """Баланс аккаунта OpenRouter (обычный API-ключ)."""
    r = requests.get(
        f"{API_BASE}/credits", headers=_auth(OPENROUTER_API_KEY), timeout=30
    )
    r.raise_for_status()
    data = r.json().get("data", {})
    total_credits = float(data.get("total_credits") or 0)
    total_usage = float(data.get("total_usage") or 0)
    return {
        "total_credits": total_credits,
        "total_usage": total_usage,
        "balance": total_credits - total_usage,
    }


class ActivityUnavailable(Exception):
    """API /activity недоступен (нет management-прав и т.п.)."""


def fetch_activity() -> list:
    """Активность по endpoint'ам за последние 30 завершённых UTC-дней."""
    r = requests.get(
        f"{API_BASE}/activity", headers=_auth(OPENROUTER_MGMT_KEY), timeout=60
    )
    if r.status_code == 403:
        raise ActivityUnavailable(
            "ключу запрещён доступ к /activity — нужен management-ключ "
            "(openrouter.ai/docs/guides/overview/auth/management-api-keys)"
        )
    if r.status_code == 401:
        raise ActivityUnavailable("неверный ключ для /activity")
    r.raise_for_status()
    return r.json().get("data", [])


def fetch_models() -> dict:
    """Список моделей OpenRouter с ценами и рейтингом."""
    r = requests.get(
        "https://openrouter.ai/api/v1/models",
        headers=_auth(OPENROUTER_API_KEY),
        timeout=30
    )
    r.raise_for_status()
    return r.json().get("data", [])


# ----------------------------- тексты сообщений -----------------------------

def balance_text(credits: dict) -> str:
    return (
        "💰 <b>Баланс OpenRouter</b>\n\n"
        f"Остаток: <b>${credits['balance']:.2f}</b>\n"
        f"Пополнено всего: ${credits['total_credits']:.2f}\n"
        f"Израсходовано всего: ${credits['total_usage']:.2f}"
    )


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
    now_utc = datetime.now(timezone.utc).date()
    month_prefix = f"{now_utc:%Y-%m}"

    totals_30 = {}  # model -> [cost, requests]
    totals_month = {}
    for it in items:
        model = it.get("model") or "?"
        cost = float(it.get("usage") or 0)
        reqs = int(it.get("requests") or 0)
        acc = totals_30.setdefault(model, [0.0, 0])
        acc[0] += cost
        acc[1] += reqs
        if (it.get("date") or "") >= f"{month_prefix}-01":
            acc = totals_month.setdefault(model, [0.0, 0])
            acc[0] += cost
            acc[1] += reqs

    def block(title: str, totals: dict) -> str:
        if not totals:
            return f"<b>{title}</b>\nНет данных"
        total_cost = sum(v[0] for v in totals.values())
        lines = [f"<b>{title}</b> — итого <b>${total_cost:.2f}</b>"]
        top = sorted(totals.items(), key=lambda x: -x[1][0])[:15]
        for model, (cost, reqs) in top:
            lines.append(f"• {escape(model)} — ${cost:.4f} · {reqs} запр.")
        extra = len(totals) - len(top)
        if extra > 0:
            lines.append(f"…и ещё {extra} моделей")
        return "\n".join(lines)

    dates = sorted({it.get("date") for it in items if it.get("date")})
    period_note = f"\nДанные: {dates[0]} … {dates[-1]} (UTC)" if dates else ""

    return (
        f"🤖 <b>Расход по моделям</b>{period_note}\n\n"
        + block("Последние 30 дней", totals_30)
        + "\n\n"
        + block(f"Текущий месяц (с {month_prefix}-01 UTC)", totals_month)
        + "\n\n<i>Расходы за сегодня в /activity не попадают — см. 📅 Сегодня</i>"
    )


def alert_text(balance: float, threshold: float) -> str:
    return (
        "⚠️ <b>Низкий баланс OpenRouter</b>\n\n"
        f"Остаток: <b>${balance:.2f}</b> — ниже порога ${threshold:.2f}\n\n"
        "Пополнить: https://openrouter.ai/settings/credits"
    )


def prices_text(models: list, used_models: set) -> str:
    """Формирует текст с ценами на модели по трём разделам."""
    
    # Раздел 1: использованные модели за последние 30 дней
    used_section = "📊 <b>Ваши модели (30 дней)</b>\n"
    if not used_models:
        used_section += "Нет данных — используйте модели, чтобы увидеть их здесь\n"
    else:
        used_models_sorted = sorted(
            [(m, _get_model_price(m, models)) for m in used_models],
            key=lambda x: x[1]["input"] if x[1] else float("inf")
        )
        for model_id, price_info in used_models_sorted[:10]:
            name = model_id.split("/")[-1][:30]
            if price_info:
                used_section += f"• {escape(name)} — 💰 ${price_info['input']:.4f}/${price_info['output']:.4f}\n"
            else:
                used_section += f"• {escape(name)} — цена не найдена\n"

    # Раздел 2: лучшие модели по цене/качеству
    best_value = "🏆 <b>Лучшие по цене/качеству</b>\n"
    rated_models = []
    for m in models:
        pricing = m.get("pricing", {}) or {}
        # OpenRouter API использует разные форматы цен
        input_price = float(
            pricing.get("input", 0) or
            pricing.get("prompt_tokens", 0) or
            list(pricing.values())[0] if pricing else 0
        )
        output_price = float(
            pricing.get("output", 0) or
            pricing.get("completion_tokens", 0) or
            list(pricing.values())[1] if len(pricing) > 1 else 0
        )
        context_length = int(m.get("context_length", 0) or 0)
        
        # Исключаем слишком дорогие модели и без рейтинга
        if output_price > 0.01 or context_length < 8000:
            continue
        
        # Рейтинг из openrouter_ranking (поле рейтинга модели на сайте)
        rating = float(m.get("openrouter_ranking", 0) or m.get("rating", 0) or 0)
        if rating < 7.0:
            continue
            
        # Считаем "стоимость за качество" — чем ниже, тем лучше
        score = (input_price + output_price) / (rating ** 2) if rating > 0 else float("inf")
        rated_models.append((m["id"], score, rating, input_price, output_price))
    
    rated_models.sort(key=lambda x: x[1])
    for model_id, score, rating, inp, outp in rated_models[:8]:
        name = model_id.split("/")[-1][:28]
        best_value += f"• {escape(name)} ⭐{rating:.1f} — 💰 ${inp:.6f}/${outp:.6f}\n"
    if not rated_models:
        best_value += "Модели не найдены\n"

    # Раздел 3: бесплатные модели по рейтингу
    free_section = "🆓 <b>Бесплатные модели</b>\n"
    free_models = []
    for m in models:
        pricing = m.get("pricing", {}) or {}
        input_price = float(
            pricing.get("input", 0) or
            pricing.get("prompt_tokens", 0) or
            list(pricing.values())[0] if pricing else 0
        )
        output_price = float(
            pricing.get("output", 0) or
            pricing.get("completion_tokens", 0) or
            list(pricing.values())[1] if len(pricing) > 1 else 0
        )
        
        # Бесплатные — оба нуля
        if input_price > 0 or output_price > 0:
            continue
            
        # Рейтинг из openrouter_ranking
        rating = float(m.get("openrouter_ranking", 0) or m.get("rating", 0) or 0)
        context_length = int(m.get("context_length", 0) or 0)
        free_models.append((m["id"], rating, context_length))
    
    free_models.sort(key=lambda x: -x[1])
    for model_id, rating, ctx_len in free_models[:8]:
        name = model_id.split("/")[-1][:28]
        ctx = f"{ctx_len // 1000}K" if ctx_len else "?"
        rating_str = f"⭐{rating:.1f}" if rating > 0 else "⭐?"
        free_section += f"• {escape(name)} {rating_str} 📝{ctx}\n"
    if not free_models:
        free_section += "Модели не найдены\n"

    return f"💰 <b>Цены на модели OpenRouter</b>\n\n{used_section}\n{best_value}\n{free_section}"


def _get_model_price(model_id: str, models: list) -> dict | None:
    """Получает цену модели по ID (с поддержкой коротких имён)."""
    short_name = model_id.split("/")[-1]
    for m in models:
        mid = m.get("id", "")
        # Ищем сначала по полному совпадению, потом по короткому имени
        if mid == model_id or mid == short_name or mid.endswith(short_name):
            pricing = m.get("pricing", {}) or {}
            # OpenRouter API использует разные форматы цен
            input_price = float(
                pricing.get("input", 0) or
                pricing.get("prompt_tokens", 0) or
                list(pricing.values())[0] if pricing else 0
            )
            output_price = float(
                pricing.get("output", 0) or
                pricing.get("completion_tokens", 0) or
                list(pricing.values())[1] if len(pricing) > 1 else 0
            )
            return {"input": input_price, "output": output_price}
    return None


# ----------------------------- хендлеры -----------------------------

def get_main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["💰 Баланс", "📅 Сегодня"], ["🗓 Месяц", "🤖 По моделям"], ["💰 Цены"]],
        resize_keyboard=True,
    )


def authorized(update: Update) -> bool:
    return str(update.effective_chat.id) in ALLOWED_CHATS


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 <b>OpenRouter монитор</b>\n\n"
        "💰 Баланс — текущий остаток\n"
        "📅 Сегодня / 🗓 Месяц — расход за период\n"
        "🤖 По моделям — разбивка расходов\n"
        "💰 Цены — цены и рейтинг моделей\n"
        "⚠️ Уведомления при остатке ниже $5 / $2 / $1",
        parse_mode="HTML",
        reply_markup=get_main_menu(),
    )


async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    msg = await update.message.reply_text("⏳ Запрашиваю баланс...")
    try:
        credits = await asyncio.to_thread(fetch_credits)
        state = load_state()
        update_snapshots(state, credits["balance"])
        state["last_balance"] = credits["balance"]
        save_state(state)
        await msg.edit_text(balance_text(credits), parse_mode="HTML")
    except Exception as e:
        log.warning("balance error: %s", e)
        await msg.edit_text(f"❌ Ошибка OpenRouter API: {escape(str(e))[:300]}")


async def show_spending(update: Update, context: ContextTypes.DEFAULT_TYPE, period: str):
    if not authorized(update):
        return
    msg = await update.message.reply_text("⏳ Считаю расход...")
    try:
        credits = await asyncio.to_thread(fetch_credits)
        state = load_state()
        update_snapshots(state, credits["balance"])
        save_state(state)
        await msg.edit_text(
            spend_text(state, credits["balance"], period), parse_mode="HTML"
        )
    except Exception as e:
        log.warning("spending error: %s", e)
        await msg.edit_text(f"❌ Ошибка OpenRouter API: {escape(str(e))[:300]}")


async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_spending(update, context, "day")


async def month_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_spending(update, context, "month")


async def models_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    msg = await update.message.reply_text("⏳ Загружаю активность...")
    try:
        items = await asyncio.to_thread(fetch_activity)
        await msg.edit_text(models_text(items), parse_mode="HTML")
    except ActivityUnavailable as e:
        await msg.edit_text(f"❌ Расход по моделям недоступен: {escape(str(e))}")
    except Exception as e:
        log.warning("models error: %s", e)
        await msg.edit_text(f"❌ Ошибка OpenRouter API: {escape(str(e))[:300]}")


async def prices_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    msg = await update.message.reply_text("⏳ Загружаю цены...")
    try:
        models, items = await asyncio.gather(
            asyncio.to_thread(fetch_models),
            asyncio.to_thread(fetch_activity),
        )
        
        # Получаем модели, использованные за последние 30 дней
        used_models: set[str] = set()
        for it in items:
            model = it.get("model")
            if model:
                used_models.add(model)
        
        await msg.edit_text(prices_text(models, used_models), parse_mode="HTML")
    except ActivityUnavailable as e:
        # Если /activity недоступен, покажем цены без списка использованных
        try:
            models = await asyncio.to_thread(fetch_models)
            await msg.edit_text(prices_text(models, set()), parse_mode="HTML")
        except Exception:
            await msg.edit_text(f"❌ Расход по моделям недоступен: {escape(str(e))}")
    except Exception as e:
        log.warning("prices error: %s", e)
        await msg.edit_text(f"❌ Ошибка: {escape(str(e))[:300]}")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "💰 Баланс":
        await show_balance(update, context)
    elif text == "📅 Сегодня":
        await show_spending(update, context, "day")
    elif text == "🗓 Месяц":
        await show_spending(update, context, "month")
    elif text == "🤖 По моделям":
        await models_cmd(update, context)
    elif text == "💰 Цены":
        await prices_cmd(update, context)


# ----------------------------- фоновая проверка алертов -----------------------------

async def check_balance_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        credits = await asyncio.to_thread(fetch_credits)
    except Exception as e:
        log.warning("Проверка баланса не удалась: %s", e)
        return

    balance = credits["balance"]
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
    app.add_handler(CommandHandler("prices", prices_cmd))
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
