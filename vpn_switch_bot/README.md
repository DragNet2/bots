# VPN Switch Bot

Telegram-бот для управления Keenetic-роутером: включение/выключение VPN-профилей.

## Быстрый старт

```bash
# 1. Установить зависимости
pip install -r requirements.txt

# 2. Настроить переменные окружения
cp .env.example .env
# Заполнить .env реальными значениями (BOT_TOKEN, KEENETIC_*)

# 3. Запуск
python bot.py
```

## Переменные окружения

| Переменная | Описание | Обязательно |
|-----------|----------|-------------|
| `BOT_TOKEN` | Telegram Bot Token | Да |
| `KEENETIC_HOST` | IP адрес роутера | Да |
| `KEENETIC_PORT` | Порт API (default: 8443) | Нет |
| `KEENETIC_USERNAME` | Логин Keenetic | Да |
| `KEENETIC_PASSWORD` | Пароль Keenetic | Да |

## Деплой

Деплой через GitHub Actions. Сервер: **LV** (31.57.158.84).

## Структура

```
vpn_switch_bot/
├── bot.py         # Telegram-бот
├── config.py      # Конфигурация
├── keenetic.py    # Keenetic API клиент
├── keyboards.py   # Клавиатуры бота
└── requirements.txt
```
test push Tue May 19 11:56:19 MSK 2026
test chown fix Tue May 19 11:59:12 MSK 2026
test sudo fix Tue May 19 12:05:56 MSK 2026
