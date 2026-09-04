# Ollama Cloud Balance Bot

Telegram-бот мониторинга баланса и расходов Ollama Cloud.

## Возможности

- `/balance` — текущий баланс Ollama Cloud (план, «$X из $Y использовано»,
  запросы за месяц, модели);
- `/today` и `/month` — расход за сегодня / текущий месяц по МСК;
- `/models` — список cloud-моделей Ollama (фильтр `:cloud`);
- алерты при остатке ≤ $5 / $2 / $1 (каждый порог — один раз, повторно после пополнения).

Расход «сегодня/месяц» считается по дельтам снапшотов баланса: при первом
запросе за день/месяц фиксируется баланс в `state.json`, дальше показывается
разница.

С 31.08.2026 Ollama перешла на прозрачную по-токенную тарификацию
(https://ollama.com/blog/transparent-pricing): планы Free / Pro / Max / Team
включают месячный пул кредитов. Баланс определяется как остаток от
включённого пула и дополнительного extra usage.

> У Ollama нет официального публичного API для баланса. Бот использует
> внутренний эндпоинт дашборда `https://ollama.com/api/me` с Bearer-токеном
> API-ключа ollama.com. Если эндпоинт изменится, бот сообщит об этом
> понятной ошибкой.

## Команды и кнопки

| Кнопка | Команда | Что делает |
|---|---|---|
| 💰 Баланс | `/balance` | «$X из $Y использовано», запросы за месяц |
| 📅 Сегодня | `/today` | Расход с начала дня (МСК) |
| 🗓 Месяц | `/month` | Расход с начала месяца (МСК) |
| 🤖 Модели | `/models` | Список cloud-моделей Ollama |

Доступ только из чатов из `ALLOWED_CHATS`.

## Конфигурация

Скопируйте `.env.example` в `.env` и заполните:

- `BOT_TOKEN` — токен бота от @BotFather;
- `OLLAMA_API_KEY` — API-ключ ([ollama.com/settings/keys](https://ollama.com/settings/keys));
- `ALLOWED_CHATS` — id чатов через запятую;
- `CHECK_INTERVAL_MIN` — интервал фоновой проверки баланса (мин, по умолчанию 30).

`state.json` создаётся автоматически рядом с ботом (снапшоты и сработавшие
алерты). Добавлен в `.gitignore`, чтобы переживать деплои (`git clean -fd`).

## Установка вручную

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env  # заполнить
venv/bin/python bot.py
```

## systemd (LV-сервер)

`/etc/systemd/system/ollama_bot.service`:

```ini
[Unit]
Description=Ollama Cloud balance Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=bots
WorkingDirectory=/home/bots/ollama_bot
ExecStart=/home/bots/ollama_bot/venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ollama_bot
```

> Если на сервере используется системный python без venv, поменяйте
> `ExecStart` на `/usr/bin/python3 bot.py` (зависимости — через
> `pip install -r requirements.txt`).

## Деплой

Автоматический: push в `main` репозитория `bots` с изменениями в
`ollama_bot/**` (см. `.github/workflows/deploy.yml`). Ручной запуск —
workflow dispatch с выбором `ollama_bot`.