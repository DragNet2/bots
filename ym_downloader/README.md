# Yandex Music Downloader Bot

Telegram-бот для скачивания музыки из Яндекс.Музыки.

## Быстрый старт

```bash
# 1. Установить зависимости
pip install -r requirements.txt

# 2. Настроить переменные окружения
cp .env.example .env
# Заполнить .env реальными значениями (BOT_TOKEN, YANDEX_TOKEN)

# 3. Запуск
python bot.py
```

## Переменные окружения

| Переменная | Описание | Обязательно |
|-----------|----------|-------------|
| `BOT_TOKEN` | Telegram Bot Token | Да |
| `YANDEX_TOKEN` | Yandex Music API Token | Да |
| `YANDEX_API_BASE` | Базовый URL API | Нет |
| `DOWNLOAD_DIR` | Папка для загрузок | Нет |
| `MAX_FILE_SIZE_MB` | Макс. размер файла (MB) | Нет |
| `RATE_LIMIT_DELAY` | Задержка между запросами (сек) | Нет |

## Деплой

Деплой через GitHub Actions. Сервер: **LV** (31.57.158.84).

## Структура

```
ym_downloader/
├── bot.py                    # Точка входа
├── config.py                 # Конфигурация
├── handlers/                 # Обработчики команд
│   ├── menu.py
│   └── download.py
├── services/                 # Бизнес-логика
│   └── yandex_downloader.py
├── keyboards/                # Клавиатуры
│   └── inline.py
└── requirements.txt
```
test Tue May 19 11:55:47 MSK 2026
test push Tue May 19 11:56:09 MSK 2026
test chown fix Tue May 19 11:59:04 MSK 2026
test sudo fix Tue May 19 12:05:48 MSK 2026
