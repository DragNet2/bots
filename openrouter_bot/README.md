# OpenRouter Balance Bot

Telegram-бот мониторинга баланса и расходов OpenRouter.

## Возможности

- `/balance` — текущий баланс OpenRouter (API `/api/v1/credits`);
- `/today` и `/month` — расход за сегодня / текущий месяц по МСК;
- `/models` — расход по моделям (API `/api/v1/activity`, management-ключ);
- алерты при остатке ≤ $5 / $2 / $1 (каждый порог — один раз, повторно после пополнения).

Расход «сегодня/месяц» считается по дельтам снапшотов баланса: при первом
запросе за день/месяц фиксируется баланс в `state.json`, дальше показывается
разница. Нюанс: `/api/v1/activity` отдаёт только завершённые UTC-дни (сегодня
не входит) и только management-ключу — поэтому расход за сегодня оценивается
через снапшоты.

## Команды и кнопки

| Кнопка | Команда | Что делает |
|---|---|---|
| 💰 Баланс | `/balance` | Баланс, пополнено, потрачено всего |
| 📅 Сегодня | `/today` | Расход с начала дня (МСК) |
| 🗓 Месяц | `/month` | Расход с начала месяца (МСК) |
| 🤖 По моделям | `/models` | Top моделей за 30 дней и за месяц (UTC) |

Доступ только из чатов из `ALLOWED_CHATS`.

## Конфигурация

Скопируйте `.env.example` в `.env` и заполните:

- `BOT_TOKEN` — токен бота от @BotFather;
- `OPENROUTER_API_KEY` — обычный ключ ([openrouter.ai/keys](https://openrouter.ai/keys));
- `OPENROUTER_MGMT_KEY` — management-ключ (для `/models`; иначе 403);
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

`/etc/systemd/system/openrouter_bot.service`:

```ini
[Unit]
Description=OpenRouter balance Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=bots
WorkingDirectory=/home/bots/openrouter_bot
ExecStart=/home/bots/openrouter_bot/venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now openrouter_bot
```

> Если на сервере используется системный python без venv, поменяйте
> `ExecStart` на `/usr/bin/python3 bot.py` (зависимости — через
> `pip install -r requirements.txt`).

## Деплой

Автоматический: push в `main` репозитория `bots` с изменениями в
`openrouter_bot/**` (см. `.github/workflows/deploy.yml`). Ручной запуск —
workflow dispatch с выбором `openrouter_bot`.
