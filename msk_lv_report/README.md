# msk_lv_bot - Мониторинг PostgreSQL с уведомлениями в Telegram

Shell-скрипты для мониторинга баз данных PostgreSQL на серверах MSK и LV с отправкой уведомлений в Telegram.

## Возможности

- Сбор метрик с обоих серверов:
  - Текущий размер базы данных
  - Количество пользователей в базе
  - Размер папки с бэкапами (только MSK)
  - Размер логов PostgreSQL
  - Свободное место на диске

- Отправка уведомлений в Telegram каждые 15 минут
- Автоматические предупреждения при превышении пороговых значений (>15 GB)

## Архитектура

```
MSK (195.209.214.24)          LV (31.57.158.84)              Telegram
┌─────────────────┐          ┌─────────────────┐          ┌─────────────┐
│ collect_msk_    │          │ collect_and_    │──────────▶│   Channel   │
│ metrics.sh       │──scp──▶  │ notify.sh       │   curl    │ @+Wy6LXS... │
│ (каждые 15 мин) │          │ (каждые 15 мин) │◀───────── │             │
└─────────────────┘          └─────────────────┘          └─────────────┘
```

Поскольку Telegram заблокирован в России, уведомления отправляются только с LV.

## Структура проекта

```
msk_lv_bot/
├── scripts/
│   ├── collect_msk_metrics.sh    # Скрипт сбора метрик MSK
│   └── collect_and_notify.sh     # Скрипт для LV (сбор + отправка)
├── deploy/
│   ├── msk_collect_metrics.service  # systemd service для MSK
│   ├── msk_collect_metrics.timer    # systemd timer для MSK
│   ├── lv_monitor.service            # systemd service для LV
│   └── lv_monitor.timer              # systemd timer для LV
├── .env.example                  # Пример конфигурации
├── README.md
└── .gitignore
```

## Установка

### На MSK

```bash
# Создать директорию для бота
sudo mkdir -p /opt/msk_lv_bot/scripts

# Скопировать скрипт
sudo cp scripts/collect_msk_metrics.sh /opt/msk_lv_bot/scripts/
sudo chmod +x /opt/msk_lv_bot/scripts/collect_msk_metrics.sh

# Установить systemd unit'ы
sudo cp deploy/msk_collect_metrics.service /etc/systemd/system/
sudo cp deploy/msk_collect_metrics.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable msk_collect_metrics.timer
sudo systemctl start msk_collect_metrics.timer
```

### На LV

```bash
# Создать директорию для бота
sudo mkdir -p /opt/msk_lv_bot/scripts

# Скопировать скрипты
sudo cp scripts/collect_and_notify.sh /opt/msk_lv_bot/scripts/
sudo cp scripts/collect_msk_metrics.sh /opt/msk_lv_bot/scripts/
sudo chmod +x /opt/msk_lv_bot/scripts/*.sh

# Скопировать конфигурацию
sudo cp .env.example /opt/msk_lv_bot/.env
sudo nano /opt/msk_lv_bot/.env  # Указать правильный CHAT_ID

# Установить systemd unit'ы
sudo cp deploy/lv_monitor.service /etc/systemd/system/
sudo cp deploy/lv_monitor.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable lv_monitor.timer
sudo systemctl start lv_monitor.timer
```

## Настройка CHAT_ID

Для приватных каналов chat_id отличается от публичной ссылки. Чтобы узнать правильный ID:

1. Откройте канал в Telegram
2. Перешлите любое сообщение из канала боту `@userinfobot`
3. Бот покажет ваш ID - это и будет chat_id для приватных каналов

Или:
1. Добавьте бота в канал как админа
2. Перешлите сообщение из канала боту `@ukusongs_import_bot`
3. Выполните на LV: `curl "https://api.telegram.org/bot<TOKEN>/getUpdates"`
4. В ответе найдите `"id": -100xxxxxxxxx`

## Проверка работы

```bash
# На MSK - проверить собранные метрики
cat /tmp/msk_lv_metrics/msk_metrics.json

# На LV - запустить вручную
sudo /opt/msk_lv_bot/scripts/collect_and_notify.sh

# Посмотреть логи
sudo journalctl -u lv_monitor.service -f
```

## Переменные окружения

| Переменная | Описание | Значение по умолчанию |
|------------|----------|----------------------|
| `TELEGRAM_BOT_TOKEN` | Токен Telegram бота | (установлен) |
| `TELEGRAM_CHAT_ID` | ID чата/канала | -1002474493698 |
| `TELEGRAM_PROXY` | SOCKS5 прокси для Telegram | (пусто) |
| `MSK_HOST` | IP сервера MSK | 195.209.214.24 |
| `MSK_USER` | Пользователь SSH для MSK | ubuntu |
| `MSK_KEY` | Путь к SSH ключу | (установлен) |
| `LV_PG_USER` | Пользователь PostgreSQL LV | ukusongs |
| `LV_PG_DATABASE` | Имя базы данных | ukusongs |
