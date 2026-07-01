#!/bin/bash
#==============================================================================
# collect_and_notify.sh
# Собирает метрики LV, забирает метрики с MSK, отправляет отчёт в Telegram
# Запускается по cron/systemd timer каждые 15 минут
#==============================================================================

set -euo pipefail

#------------------------------------------------------------------------------
# Загрузка конфигурации
#------------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/../.env" ]; then
    set -a
    source "$SCRIPT_DIR/../.env"
    set +a
fi

#------------------------------------------------------------------------------
# Конфигурация (defaults)
#------------------------------------------------------------------------------

# MSK Server
MSK_HOST="${MSK_HOST:-195.209.214.24}"
MSK_USER="${MSK_USER:-ubuntu}"
MSK_KEY="${MSK_KEY:-/root/.ssh/id_ed25519_traefk}"
MSK_METRICS_PATH="/tmp/msk_lv_metrics/msk_metrics.json"
MSK_SCRIPT_PATH="/opt/msk_lv_report/scripts/collect_msk_metrics.sh"

# Telegram
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"
TELEGRAM_PROXY="${TELEGRAM_PROXY:-}"

# LV PostgreSQL
LV_PG_USER="${LV_PG_USER:-ukusongs}"
LV_PG_DATABASE="${LV_PG_DATABASE:-ukusongs}"

# Временные файлы
TMP_DIR="/tmp/msk_lv_notify"
MSK_METRICS_LOCAL="$TMP_DIR/msk_metrics.json"

#------------------------------------------------------------------------------
# Функции
#------------------------------------------------------------------------------

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2
}

# Получает метрики LV
get_lv_metrics() {
    # Размер БД
    LV_DB_SIZE=$(sudo -u postgres psql -d ukusongs -t -c "SELECT pg_size_pretty(pg_database_size('ukusongs'));" 2>/dev/null | tr -d ' ' || echo "Error")
    LV_DB_SIZE_BYTES=$(sudo -u postgres psql -d ukusongs -t -c "SELECT pg_database_size('ukusongs');" 2>/dev/null | tr -d ' ' || echo "0")

    # Количество пользователей сайта
    LV_USERS_COUNT=$(sudo -u postgres psql -d ukusongs -t -c "SELECT COUNT(*) FROM users;" 2>/dev/null | tr -d ' ' || echo "Error")

    # Бэкапы на LV нет (репликация)
    LV_BACKUPS_SIZE="N/A (репликация)"

    # Размер логов PostgreSQL
    if [ -d "/var/log/postgresql" ]; then
        LV_PG_LOGS_SIZE=$(du -sh /var/log/postgresql 2>/dev/null | cut -f1 || echo "Error")
    else
        LV_PG_LOGS_SIZE="No logs dir"
    fi

    # Свободное место
    LV_DISK_FREE=$(df -h / | tail -1 | awk '{print $4}' || echo "Error")

    # Репликация MSK → LV: проверяем статус подписки на LV
    local sub_status=$(sudo -u postgres psql -d ukusongs -t -c "SELECT subenabled FROM pg_subscription WHERE subname = 'msk_subscription';" 2>/dev/null | tr -d ' ' || echo "f")
    
    if [ "$sub_status" = "t" ]; then
        LV_REPLICATION_FROM_MSK="ok"
    else
        LV_REPLICATION_FROM_MSK="inactive"
    fi
}

# Получает статистику по торрентам video_downloader
get_torrent_metrics() {
    TORRENT_DIR="/tmp/torrents"
    
    # Проверяем активные процессы aria2 и ffmpeg
    TORRENT_PROCESSES=$(ps aux 2>/dev/null | grep -E 'aria2c|ffmpeg' | grep -v grep | wc -l || echo "0")
    
    # Основная папка с загрузками
    if [ -d "$TORRENT_DIR" ]; then
        TORRENT_DOWNLOADS_COUNT=$(find "$TORRENT_DIR" -maxdepth 1 -type d -name 'downloads_*' 2>/dev/null | wc -l || echo "0")
        TORRENT_DOWNLOADS_SIZE=$(du -sh "$TORRENT_DIR" 2>/dev/null | cut -f1 || echo "Error")
        TORRENT_DOWNLOADS_SIZE_BYTES=$(du -sb "$TORRENT_DIR" 2>/dev/null | cut -f1 || echo "0")
        
        # Самая старая папка (для определения времени работы)
        OLDEST_DIR=$(find "$TORRENT_DIR" -maxdepth 1 -type d -name 'downloads_*' -type d -exec stat -c '%Y %n' {} \; 2>/dev/null | sort -n | head -1 | awk '{print $2}')
        if [ -n "$OLDEST_DIR" ] && [ -d "$OLDEST_DIR" ]; then
            TORRENT_OLDEST_SECONDS=$(($(date +%s) - $(stat -c '%Y' "$OLDEST_DIR" 2>/dev/null || echo "0")))
            TORRENT_OLDEST_TIME=$(printf '%02d:%02d:%02d' $((TORRENT_OLDEST_SECONDS/3600)) $(((TORRENT_OLDEST_SECONDS%3600)/60)) $((TORRENT_OLDEST_SECONDS%60)))
        else
            TORRENT_OLDEST_TIME="N/A"
        fi
        
        #orphaned .transcoded.mp4 файлы (не удалены после отправки)
        TRANSCODED_COUNT=$(find "$TORRENT_DIR" -name '*.transcoded.mp4' 2>/dev/null | wc -l || echo "0")
        TRANSCODED_SIZE=$(du -sh "$TORRENT_DIR"/*.transcoded.mp4 2>/dev/null | cut -f1 || echo "0")
        if [ "$TRANSCODED_COUNT" = "0" ]; then
            TRANSCODED_SIZE="-"
        fi
        
        #orphaned .torrent файлы
        TORRENT_FILES_COUNT=$(find "$TORRENT_DIR" -maxdepth 1 -name 'torrent_*.torrent' 2>/dev/null | wc -l || echo "0")
    else
        TORRENT_DOWNLOADS_COUNT="0"
        TORRENT_DOWNLOADS_SIZE="Empty"
        TORRENT_DOWNLOADS_SIZE_BYTES="0"
        TORRENT_OLDEST_TIME="N/A"
        TRANSCODED_COUNT="0"
        TRANSCODED_SIZE="-"
        TORRENT_FILES_COUNT="0"
    fi
    
    # Временные файлы VK видео
    VK_TEMP_COUNT=$(find /tmp -maxdepth 1 -name 'vk_video_*.mp4' 2>/dev/null | wc -l || echo "0")
    VK_TEMP_SIZE=$(du -sh /tmp/vk_video_*.mp4 2>/dev/null | cut -f1 || echo "-")
    if [ "$VK_TEMP_COUNT" = "0" ]; then
        VK_TEMP_SIZE="-"
    fi
}

# Забирает метрики с MSK
fetch_msk_metrics() {
    log "Fetching MSK metrics from $MSK_HOST..."

    # Вариант 1: Запустить скрипт сбора на MSK через SSH
    if ssh -i "$MSK_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$MSK_USER@$MSK_HOST" "bash $MSK_SCRIPT_PATH" > "$MSK_METRICS_LOCAL" 2>&1; then
        log "MSK metrics collected via SSH exec"
        return 0
    fi

    # Вариант 2: Скопировать уже собранные метрики
    log "Trying to copy existing MSK metrics..."
    mkdir -p "$TMP_DIR"
    if scp -i "$MSK_KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$MSK_USER@$MSK_HOST:$MSK_METRICS_PATH" "$MSK_METRICS_LOCAL" 2>/dev/null; then
        log "MSK metrics copied successfully"
        return 0
    fi

    error "Failed to fetch MSK metrics"
    return 1
}

# Формирует текстовое сообщение
format_message() {
    local msk_db_size="$1"
    local msk_users="$2"
    local msk_backups="$3"
    local msk_logs="$4"
    local msk_disk="$5"
    local msk_db_bytes="$6"

    local lv_db_size="$7"
    local lv_users="$8"
    local lv_backups="$9"
    local lv_logs="${10}"
    local lv_disk="${11}"
    local lv_db_bytes="${12}"
    local msk_repl_status="${13:-N/A}"  # LV → MSK replication status
    local lv_repl_status="${14:-N/A}"    # MSK → LV replication status
    # Torrent stats
    local torrent_procs="${15:-0}"
    local torrent_count="${16:-0}"
    local torrent_size="${17:-Empty}"
    local torrent_time="${18:-N/A}"
    local transcoded_count="${19:-0}"
    local transcoded_size="${20:--}"
    local torrent_files="${21:-0}"
    local vk_temp_count="${22:-0}"
    local vk_temp_size="${23:--}"

    local timestamp=$(date '+%Y-%m-%d %H:%M')

    # Форматируем статус репликации
    local msk_repl_icon="❌"
    local msk_repl_text="неактивна"
    if [ "$msk_repl_status" = "ok" ]; then
        msk_repl_icon="✅"
        msk_repl_text="активна"
    fi

    local lv_repl_icon="❌"
    local lv_repl_text="неактивна"
    if [ "$lv_repl_status" = "ok" ]; then
        lv_repl_icon="✅"
        lv_repl_text="активна"
    fi

    # Иконки для торрентов
    local torrent_icon="⏸️"
    local torrent_text="нет активных"
    if [ "$torrent_procs" -gt 0 ] 2>/dev/null; then
        torrent_icon="⏬️"
        torrent_text="идёт загрузка"
    fi

    local message="📊 <b>Мониторинг PostgreSQL</b>
⏰ $timestamp

🇷🇺 <b>MSK</b> (Основной сервер)
├─ 💾 Размер БД: <code>$msk_db_size</code>
├─ 👥 Пользователей: <code>$msk_users</code>
├─ 📁 Бэкапы: <code>$msk_backups</code>
├─ 📋 Логи БД: <code>$msk_logs</code>
├─ 💿 Свободно: <code>$msk_disk</code>
└─ 🔄 LV→MSK: <code>$msk_repl_icon $msk_repl_text</code>

🇪🇪 <b>LV</b> (Реплика)
├─ 💾 Размер БД: <code>$lv_db_size</code>
├─ 👥 Пользователей: <code>$lv_users</code>
├─ 📁 Бэкапы: <code>$lv_backups</code>
├─ 📋 Логи БД: <code>$lv_logs</code>
├─ 💿 Свободно: <code>$lv_disk</code>
└─ 🔄 MSK→LV: <code>$lv_repl_icon $lv_repl_text</code>

📥 <b>Торренты</b> (video_downloader)
├─ 🔄 Процессы: <code>$torrent_icon $torrent_procs шт</code>
├─ 📁 Загрузок: <code>$torrent_count шт</code>
├─ 💾 Размер: <code>$torrent_size</code>
├─ ⏱ Работает: <code>$torrent_time</code>
├─ 📋 Статус: <code>$torrent_text</code>
│
├─ 🎬 Transcoded: <code>$transcoded_count шт ($transcoded_size)</code>
├─ 📦 .torrent файлы: <code>$torrent_files шт</code>
└─ 🎥 VK temp: <code>$vk_temp_count шт ($vk_temp_size)</code>"

    # Добавляем предупреждения
    if [ "$msk_db_bytes" -gt 15000000000 ] 2>/dev/null; then
        message="$message
⚠️ БД MSK > 15 GB"
    fi

    if [ "$lv_db_bytes" -gt 15000000000 ] 2>/dev/null; then
        message="$message
⚠️ БД LV > 15 GB"
    fi

    echo "$message"
}

# Отправляет сообщение в Telegram
send_telegram() {
    local message="$1"

    if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
        error "Telegram credentials not configured"
        return 1
    fi

    log "Sending Telegram notification..."

    local curl_cmd="curl -sS --connect-timeout 10 -m 30"
    
    # Добавляем прокси если указан
    if [ -n "$TELEGRAM_PROXY" ]; then
        curl_cmd="$curl_cmd --proxy '$TELEGRAM_PROXY'"
    fi

    curl_cmd="$curl_cmd -X POST 'https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage'"
    curl_cmd="$curl_cmd -d 'chat_id=${TELEGRAM_CHAT_ID}'"
    curl_cmd="$curl_cmd -d \"text=${message}\""
    curl_cmd="$curl_cmd -d 'parse_mode=HTML'"

    eval "$curl_cmd" | grep -q '"ok":true' && {
        log "Telegram notification sent successfully!"
        return 0
    }

    error "Failed to send Telegram notification"
    return 1
}

#------------------------------------------------------------------------------
# Main
#------------------------------------------------------------------------------

main() {
    log "Starting metrics collection..."

    # Создаем временную директорию
    mkdir -p "$TMP_DIR"

    # Собираем метрики LV
    log "Collecting LV metrics..."
    get_lv_metrics

    # Собираем метрики торрентов
    log "Collecting torrent metrics..."
    get_torrent_metrics

    # Забираем метрики с MSK
    if ! fetch_msk_metrics; then
        # Если не удалось получить метрики MSK - отправляем только LV
        error "Using LV metrics only"
    fi

    # Читаем метрики MSK
    if [ -f "$MSK_METRICS_LOCAL" ]; then
        MSK_DB_SIZE=$(grep '"db_size"' "$MSK_METRICS_LOCAL" | cut -d'"' -f4)
        MSK_DB_SIZE_BYTES=$(grep '"db_size_bytes"' "$MSK_METRICS_LOCAL" | grep -o '[0-9]*')
        MSK_USERS_COUNT=$(grep '"users_count"' "$MSK_METRICS_LOCAL" | cut -d'"' -f4)
        MSK_BACKUPS_SIZE=$(grep '"backups_size"' "$MSK_METRICS_LOCAL" | cut -d'"' -f4)
        MSK_PG_LOGS_SIZE=$(grep '"pg_logs_size"' "$MSK_METRICS_LOCAL" | cut -d'"' -f4)
        MSK_DISK_FREE=$(grep '"disk_free"' "$MSK_METRICS_LOCAL" | cut -d'"' -f4)
        # Репликация LV → MSK (из MSK метрик)
        MSK_REPL_STATUS=$(grep '"replication_from_lv"' "$MSK_METRICS_LOCAL" | cut -d'"' -f4 || echo "N/A")
    else
        MSK_DB_SIZE="N/A"
        MSK_DB_SIZE_BYTES="0"
        MSK_USERS_COUNT="N/A"
        MSK_BACKUPS_SIZE="N/A"
        MSK_PG_LOGS_SIZE="N/A"
        MSK_DISK_FREE="N/A"
        MSK_REPL_STATUS="N/A"
    fi

    # Формируем и отправляем сообщение
    local msg=$(format_message \
        "$MSK_DB_SIZE" "$MSK_USERS_COUNT" "$MSK_BACKUPS_SIZE" "$MSK_PG_LOGS_SIZE" "$MSK_DISK_FREE" "$MSK_DB_SIZE_BYTES" \
        "$LV_DB_SIZE" "$LV_USERS_COUNT" "$LV_BACKUPS_SIZE" "$LV_PG_LOGS_SIZE" "$LV_DISK_FREE" "$LV_DB_SIZE_BYTES" \
        "$MSK_REPL_STATUS" "$LV_REPLICATION_FROM_MSK" \
        "$TORRENT_PROCESSES" "$TORRENT_DOWNLOADS_COUNT" "$TORRENT_DOWNLOADS_SIZE" "$TORRENT_OLDEST_TIME" \
        "$TRANSCODED_COUNT" "$TRANSCODED_SIZE" "$TORRENT_FILES_COUNT" "$VK_TEMP_COUNT" "$VK_TEMP_SIZE" \
    )

    send_telegram "$msg"

    log "Completed"
}

main "$@"
