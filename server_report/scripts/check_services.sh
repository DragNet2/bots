#!/bin/bash
#==============================================================================
# check_services.sh
# Проверяет статус сервисов на LV сервере
# Возвращает JSON с результатами
#==============================================================================

set -euo pipefail

#------------------------------------------------------------------------------
# Конфигурация
#------------------------------------------------------------------------------

# Сервисы LV (проверяются локально)
LV_SERVICES=(
    "ukusongs-site"
    "4strings"
    "ukusongs_bot"
    "tasktracker_bot"
    "video_downloader"
    "vpn_switch_bot"
    "ym_downloader"
    "nginx"
    "postgresql@16-main"
)

# Таймеры LV
LV_TIMERS=(
    "ukusongs-pulse.timer"
    "ukusongs-db-backup.timer"
)

#------------------------------------------------------------------------------
# Функции
#------------------------------------------------------------------------------

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >&2
}

# Экранирует строку для JSON
json_escape() {
    local str="$1"
    str="${str//\\/\\\\}"
    str="${str//\"/\\\"}"
    str="${str//$'\n'/\\n}"
    str="${str//$'\r'/\\r}"
    str="${str//$'\t'/\\t}"
    echo "$str"
}

# Проверяет статус одного сервиса
check_service_status() {
    local service="$1"
    local status=$(systemctl is-active "$service" 2>/dev/null | tr -d '\n' || echo "unknown")
    echo "$status"
}

# Проверяет, включен ли сервис при загрузке
check_service_enabled() {
    local service="$1"
    local enabled=$(systemctl is-enabled "$service" 2>/dev/null | tr -d '\n' || echo "unknown")
    echo "$enabled"
}

# Получает информацию о сервисе через systemctl show
get_service_info() {
    local service="$1"
    local main_pid=$(systemctl show "$service" -p MainPID --value 2>/dev/null | tr -d '\n' || echo "0")
    local active_state=$(systemctl show "$service" -p ActiveState --value 2>/dev/null | tr -d '\n' || echo "unknown")
    local sub_state=$(systemctl show "$service" -p SubState --value 2>/dev/null | tr -d '\n' || echo "unknown")
    local load_state=$(systemctl show "$service" -p LoadState --value 2>/dev/null | tr -d '\n' || echo "unknown")
    local start_time=$(systemctl show "$service" -p ActiveEnterTimestamp --value 2>/dev/null | tr -d '\n' || echo "")

    echo "${main_pid}|${active_state}|${sub_state}|${load_state}|${start_time}"
}

#------------------------------------------------------------------------------
# Сбор статуса LV сервисов (локально)
#------------------------------------------------------------------------------
check_lv_services() {
    log "Checking LV services..."

    local results="["
    local first=true

    # Сервисы
    for service in "${LV_SERVICES[@]}"; do
        local status=$(check_service_status "$service")
        local enabled=$(check_service_enabled "$service")
        local info=$(get_service_info "$service")

        local service_escaped=$(json_escape "$service")
        local info_escaped=$(json_escape "$info")

        if [ "$first" = true ]; then
            first=false
        else
            results+=","
        fi

        results+="{\"name\":\"$service_escaped\",\"server\":\"LV\",\"type\":\"service\",\"status\":\"$status\",\"enabled\":\"$enabled\",\"info\":\"$info_escaped\"}"
    done

    # Таймеры
    for timer in "${LV_TIMERS[@]}"; do
        local status=$(check_service_status "$timer")
        local enabled=$(check_service_enabled "$timer")
        local info=$(get_service_info "$timer")

        local timer_escaped=$(json_escape "$timer")
        local info_escaped=$(json_escape "$info")

        results+=","
        results+="{\"name\":\"$timer_escaped\",\"server\":\"LV\",\"type\":\"timer\",\"status\":\"$status\",\"enabled\":\"$enabled\",\"info\":\"$info_escaped\"}"
    done

    results+="]"
    echo "$results"
}

#------------------------------------------------------------------------------
# Main
#------------------------------------------------------------------------------
main() {
    local mode="${1:-lv}"

    log "Starting service check (mode: $mode)..."

    case "$mode" in
        lv)
            check_lv_services
            ;;
        *)
            check_lv_services
            ;;
    esac

    log "Service check completed"
}

main "$@"
