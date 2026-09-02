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
import re
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


def human_size(num_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{num_bytes} B"
        num_bytes /= 1024
    return f"{num_bytes:.1f} PB"


def fetch_account() -> dict:
    """Информация об аккаунте Ollama Cloud.

    Эндпоинт /api/me используется дашбордом https://ollama.com/settings и
    ожидает POST (не GET). Отдаёт JSON с профилем (Email/Name/Plan). Для
    бесплатного плана баланс/кредиты не возвращаются; normalize_account
    обрабатывает оба варианта. Если ключ неверный или эндпоинт изменился —
    бросаем AccountUnavailable с понятным сообщением.
    """
    r = requests.post(
        f"{API_BASE}/me",
        headers={**_auth(), "Accept": "application/json"},
        timeout=30,
    )
    if r.status_code in (401, 403):
        raise AccountUnavailable(
            "ключ отклонён /api/me — проверьте OLLAMA_API_KEY "
            "на https://ollama.com/settings/keys"
        )
    if r.status_code in (404, 405):
        raise AccountUnavailable(
            f"эндпоинт /api/me недоступен ({r.status_code}) — "
            "Ollama изменила API; обновите бота"
        )
    r.raise_for_status()
    return r.json()


def normalize_account(data: dict) -> dict:
    """Приводит ответ /api/me к стабильной внутренней структуре.

    Реальный ответ сейчас содержит: ID, Email, Name, Plan, Bio, AvatarURL,
    FirstName, LastName, Links, CreatedAt. Платные планы (Pro/Max/Team) могут
    отдавать дополнительные поля — нормализатор понимает обе формы.

    Возвращает dict с полями:
      - balance: float — доступный остаток (0.0, если API не отдаёт)
      - plan: str — название плана (free / pro / max / team / …)
      - email: str | None
      - name: str | None
      - included: float | None — размер включённого месячного пула ($)
      - extra: float | None — размер extra usage баланса ($)
      - used: float | None — использовано в текущем месяце ($)
      - renews_at: str | None
      - raw: dict — полный ответ API
    """
    raw = data if isinstance(data, dict) else {}

    plan = (
        raw.get("Plan")
        or raw.get("plan")
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
        "IncludedCredits",
        default=None,
    )
    extra = _money(
        "extra_credits",
        "extra_balance",
        "add_on_balance",
        "extra",
        "ExtraCredits",
        default=None,
    )
    used = _money("used", "usage", "total_usage", "Used", default=None)

    parts = [v for v in (included, extra) if v is not None]
    if parts:
        available = sum(parts) - (used or 0.0)
    else:
        available = _money(
            "balance",
            "available",
            "remaining",
            "credit_balance",
            "Balance",
            default=0.0,
        ) or 0.0

    renews_at = (
        raw.get("renews_at")
        or raw.get("resets_at")
        or raw.get("next_billing_at")
        or raw.get("period_end")
        or raw.get("RenewsAt")
    )

    return {
        "balance": max(float(available), 0.0),
        "plan": str(plan),
        "email": raw.get("Email") or raw.get("email"),
        "name": raw.get("Name") or raw.get("name"),
        "included": included,
        "extra": extra,
        "used": used,
        "renews_at": str(renews_at) if renews_at else None,
        "raw": raw,
    }


def fetch_cloud_models() -> list:
    """Список моделей Ollama Cloud (GET /api/tags).

    Публичный эндпоинт ollama.com/api/tags не разделяет локальные и cloud
    модели отдельным флагом, поэтому возвращаем всё, что доступно по ключу
    (для бесплатного плана — каталог cloud-моделей). При наличии тега
    :cloud оставляем только их, иначе — все модели.
    """
    r = requests.get(
        f"{API_BASE}/tags",
        headers={**_auth(), "Accept": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    items = r.json().get("models", []) or []
    cloud = [m for m in items if ":cloud" in m.get("name", "")]
    if not cloud:
        cloud = items  # бесплатный план: cloud-модели без суффикса
    cloud.sort(key=lambda m: m.get("name", ""))
    return cloud


PRICING_URL = "https://ollama.com/pricing"
PRICING_TTL_H = 24  # обновлять прайс не чаще раза в сутки


def fetch_pricing(state: dict) -> dict:
    """Цены cloud-моделей со страницы https://ollama.com/pricing.

    Возвращает dict {имя_модели: {"in": float, "cached": float, "out": float}}
    — $ за миллион токенов. Кэшируется в state.json на PRICING_TTL_H часов:
    страница HTML (не API), дёргать её на каждый запрос /models нельзя.
    При ошибке парсинга/сети возвращает последний удачный кэш, если есть.
    """
    cached = state.get("pricing") or {}
    fetched_at = cached.get("fetched_at")
    if fetched_at:
        try:
            age_h = (
                datetime.now(TZ) - datetime.fromisoformat(fetched_at)
            ).total_seconds() / 3600
            if age_h < PRICING_TTL_H and cached.get("models"):
                return cached["models"]
        except ValueError:
            pass  # битая дата в кэше — перекачиваем

    r = requests.get(
        PRICING_URL,
        headers={
            "Accept": "text/html",
            "User-Agent": "Mozilla/5.0 (compatible; ollama_bot)",
        },
        timeout=30,
    )
    r.raise_for_status()

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table")
    if table is None:
        log.warning("pricing: таблица на %s не найдена", PRICING_URL)
        return cached.get("models") or {}

    def _money(cell: str):
        m = re.search(r"\$([0-9][0-9,]*(?:\.[0-9]+)?)", cell)
        return float(m.group(1).replace(",", "")) if m else None

    models = {}
    for tr in table.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) < 4 or not cells[0]:
            continue
        name, price_in, price_cached, price_out = cells[0], *cells[1:4]
        prices = {
            "in": _money(price_in),
            "cached": _money(price_cached),
            "out": _money(price_out),
        }
        if prices["in"] is None and prices["out"] is None:
            continue
        models[name] = prices

    if not models:
        log.warning("pricing: пустая таблица на %s", PRICING_URL)
        return cached.get("models") or {}

    state["pricing"] = {
        "fetched_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "models": models,
    }
    save_state(state)
    return models


def _price_for(pricing: dict, model_name: str) -> dict | None:
    """Ищет цену модели: точное имя, затем база до ':' (api: nemotron-3-nano:30b
    -> pricing: nemotron-3-nano), затем префикс."""
    if model_name in pricing:
        return pricing[model_name]
    base = model_name.split(":")[0]
    if base in pricing:
        return pricing[base]
    for pname, prices in pricing.items():
        if pname.split(":")[0] == base:
            return prices
    return None


# ----------------------------- тексты сообщений -----------------------------

def balance_text(acc: dict) -> str:
    plan = escape(acc["plan"])
    lines = [
        "💰 <b>Аккаунт Ollama Cloud</b>",
        "",
        f"План: <b>{plan}</b>",
    ]
    if acc.get("name"):
        lines.append(f"Имя: {escape(acc['name'])}")
    if acc.get("email"):
        lines.append(f"Email: {escape(acc['email'])}")

    # Баланс показываем, только если API его реально отдал (платные планы)
    has_money = (
        acc.get("included") is not None
        or acc.get("extra") is not None
        or acc.get("used") is not None
        or acc["balance"] > 0
    )
    if has_money:
        lines.append("")
        lines.append(f"Остаток: <b>${acc['balance']:.2f}</b>")
        if acc.get("included") is not None:
            lines.append(f"Включено в план: ${acc['included']:.2f}/мес")
        if acc.get("extra") is not None:
            lines.append(f"Extra usage: ${acc['extra']:.2f}")
        if acc.get("used") is not None:
            lines.append(f"Использовано в месяце: ${acc['used']:.2f}")
    else:
        lines.append("")
        lines.append(
            "ℹ️ API не отдаёт баланс для текущего плана — "
            f"расход «сегодня/месяц» недоступен. "
            f"Проверьте вручную: https://ollama.com/settings/billing"
        )

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


def models_text(items: list, pricing: dict | None = None) -> str:
    if not items:
        return (
            "🤖 <b>Модели Ollama Cloud</b>\n\n"
            "Модели не найдены. Проверьте OLLAMA_API_KEY или план на "
            "https://ollama.com/settings"
        )
    pricing = pricing or {}
    have_prices = any(_price_for(pricing, m.get("name", "")) for m in items)
    lines = [f"🤖 <b>Модели Ollama Cloud</b> — {len(items)} шт."]
    if have_prices:
        lines.append(
            "<i>Цены $ за 1M токенов: ↗️ вход / ↘️ выход</i>\n"
        )
    else:
        lines.append("")
    # Показываем первые 25, остальное — счётчик
    for m in items[:25]:
        name = escape(m.get("name", "?"))
        size = m.get("size")
        size_part = f" · {human_size(size)}" if size else ""
        lines.append(f"• <code>{name}</code>{size_part}")
        p = _price_for(pricing, m.get("name", ""))
        if p and (p.get("in") is not None or p.get("out") is not None):
            pin = f"{p['in']:.2f}".rstrip("0").rstrip(".") if p.get("in") else "—"
            pout = (
                f"{p['out']:.2f}".rstrip("0").rstrip(".")
                if p.get("out")
                else "—"
            )
            lines.append(f"  <i>↗️ ${pin} / ↘️ ${pout}</i>")
    if len(items) > 25:
        lines.append(f"\n…и ещё {len(items) - 25} моделей")
    lines.append("\nПолный список: https://ollama.com/search?c=cloud")
    if not have_prices:
        lines.append(
            "Цены: https://ollama.com/pricing (не удалось загрузить)"
        )
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
        "💰 Баланс — план и остаток (если API отдаёт)\n"
        "📅 Сегодня / 🗓 Месяц — расход за период\n"
        "🤖 Модели — список доступных моделей\n"
        "⚠️ Уведомления при остатке ниже $5 / $2 / $1 (для платных планов)",
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
        if str(acc.get("plan", "")).lower() == "free":
            await msg.edit_text(
                "ℹ️ План <b>free</b> — API Ollama не отдаёт баланс/расход, "
                "поэтому посчитать нечего.\n"
                "Проверьте вручную: https://ollama.com/settings/billing",
                parse_mode="HTML",
            )
            return
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
        items, pricing = await asyncio.to_thread(_models_with_pricing)
        await msg.edit_text(models_text(items, pricing), parse_mode="HTML")
    except Exception as e:
        log.warning("models error: %s", e)
        await msg.edit_text(f"❌ Ошибка Ollama API: {escape(str(e))[:300]}")


def _models_with_pricing() -> tuple:
    """Модели + прайс (прайс берётся из кэша или парсится с сайта)."""
    items = fetch_cloud_models()
    state = load_state()
    try:
        pricing = fetch_pricing(state)
    except Exception as e:
        # Прайс не критичен: показываем модели без цен
        log.warning("pricing error: %s", e)
        pricing = (state.get("pricing") or {}).get("models") or {}
    return items, pricing


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

    # На бесплатном плане API не отдаёт баланс — алерты не отправляем,
    # только фиксируем замер (на случай перехода на платный план).
    if str(acc.get("plan", "")).lower() == "free":
        state["last_balance"] = balance
        state["last_plan"] = acc["plan"]
        state["last_check"] = datetime.now(TZ).isoformat(timespec="seconds")
        save_state(state)
        return

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