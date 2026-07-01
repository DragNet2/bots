# Task Tracker Bot

Telegram-бот с веб-интерфейсом для отслеживания задач с AI-помощником (Groq).

## Быстрый старт

```bash
# 1. Установить зависимости
pip install -r requirements.txt

# 2. Настроить переменные окружения
cp .env.example .env
# Заполнить .env реальными значениями (BOT_TOKEN, GROQ_API_KEY)

# 3. Запуск бота
python bot.py

# 4. Запуск веб-интерфейса (опционально)
python web.py
```

## Переменные окружения

| Переменная | Описание | Обязательно |
|-----------|----------|-------------|
| `BOT_TOKEN` | Telegram Bot Token | Да |
| `GROQ_API_KEY` | Groq API Key | Да |
| `TASKTRACKER_SECRET_KEY` | Секретный ключ для веб-сессий | Да |
| `ADMIN_USER_IDS` | Telegram ID администраторов (через запятую) | Нет |
| `WEB_PORT` | Порт веб-интерфейса | Нет |

## Google OAuth (опционально)

Для авторизации через Google нужно настроить:
- `TASKTRACKER_GOOGLE_CLIENT_ID`
- `TASKTRACKER_GOOGLE_CLIENT_SECRET`
- `TASKTRACKER_ALLOWED_EMAILS`

## Деплой

Деплой через GitHub Actions. Сервер: **LV** (31.57.158.84).

## Структура

```
tasktracker_bot/
├── bot.py         # Telegram-бот
├── web.py         # Flask веб-интерфейс
├── handlers.py    # Обработчики команд
├── db.py          # База данных задач
├── ai_service.py  # Groq AI интеграция
├── config.py      # Конфигурация
└── requirements.txt
```
