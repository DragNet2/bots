#!/bin/bash
#==============================================================================
# collect_msk_metrics.sh
# Собирает метрики PostgreSQL на сервере MSK и сохраняет в JSON файл
# Запускается по cron/systemd timer каждые 15 минут
#==============================================================================

set -euo pipefail

METRICS_FILE="/tmp/msk_lv_metrics/msk_metrics.json"
METRICS_DIR="/tmp/msk_lv_metrics"

# Создаем директорию для метрик
mkdir -p "$METRICS_DIR"

#------------------------------------------------------------------------------
# Функции
#------------------------------------------------------------------------------

get_db_size() {
    sudo -u postgres psql -d ukusongs -t -c "SELECT pg_size_pretty(pg_database_size('ukusongs'));" 2>/dev/null | tr -d ' ' || echo "Error"
}

get_db_size_bytes() {
    sudo -u postgres psql -d ukusongs -t -c "SELECT pg_database_size('ukusongs');" 2>/dev/null | tr -d ' ' || echo "0"
}

get_users_count() {
    # Количество пользователей сайта (из таблицы users)
    sudo -u postgres psql -d ukusongs -t -c "SELECT COUNT(*) FROM users;" 2>/dev/null | tr -d ' ' || echo "Error"
}

get_backups_size() {
    # Проверяем несколько возможных путей для бэкапов
    for path in "/backups/postgresql" "/var/backups/postgresql" "/backups" "/var/backups"; do
        if [ -d "$path" ]; then
            du -sh "$path" 2>/dev/null | cut -f1 || echo "Error"
            return 0
        fi
    done
    echo "No backup dir"
}

get_pg_logs_size() {
    local log_dir="/var/log/postgresql"
    if [ -d "$log_dir" ]; then
        du -sh "$log_dir" 2>/dev/null | cut -f1 || echo "Error"
    else
        echo "No logs dir"
    fi
}

get_disk_free() {
    df -h / | tail -1 | awk '{print $4}' || echo "Error"
}

get_replication_status() {
    # LV → MSK: проверяем статус подписки lv_subscription
    local sub_status=$(sudo -u postgres psql -d ukusongs -t -c "SELECT subenabled FROM pg_subscription WHERE subname = 'lv_subscription';" 2>/dev/null | tr -d ' ' || echo "f")
    
    if [ "$sub_status" = "t" ]; then
        echo "ok"
    else
        echo "inactive"
    fi
}

#------------------------------------------------------------------------------
# Сбор метрик
#------------------------------------------------------------------------------

DB_SIZE=$(get_db_size)
DB_SIZE_BYTES=$(get_db_size_bytes)
USERS_COUNT=$(get_users_count)
BACKUPS_SIZE=$(get_backups_size)
PG_LOGS_SIZE=$(get_pg_logs_size)
DISK_FREE=$(get_disk_free)
REPLICATION_STATUS=$(get_replication_status)
COLLECTED_AT=$(date -Iseconds)

#------------------------------------------------------------------------------
# Сохранение в JSON
#------------------------------------------------------------------------------

cat > "$METRICS_FILE" << EOF
{
  "db_size": "$DB_SIZE",
  "db_size_bytes": $DB_SIZE_BYTES,
  "users_count": "$USERS_COUNT",
  "backups_size": "$BACKUPS_SIZE",
  "pg_logs_size": "$PG_LOGS_SIZE",
  "disk_free": "$DISK_FREE",
  "replication_from_lv": "$REPLICATION_STATUS",
  "collected_at": "$COLLECTED_AT",
  "server": "MSK"
}
EOF

echo "Metrics collected at $COLLECTED_AT"
cat "$METRICS_FILE"
