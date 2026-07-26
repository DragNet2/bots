#!/bin/bash
#==============================================================================
# check_services.sh
# Проверяет статус сервисов на LV и MSK серверах
# Возвращает JSON с результатами
#==============================================================================

set -euo pipefail

#------------------------------------------------------------------------------
# Конфигурация
#------------------------------------------------------------------------------

# SSH ключи
LV_SSH_KEY="${LV_SSH_KEY:-/root/.ssh/id_ed25519_traefk}"
LV_HOST="${LV_HOST:-31.57.158.84}"
LV_USER="${LV_USER:-root}"

MSK_SSH_KEY="${MSK_SSH_KEY:-/root/.ssh/id_ed25519_lv_to_msk}"
MSK_HOST="${MSK_HOST:-195.209.214.24}"
MSK_USER="${MSK_USER:-ubuntu}"

# Сервисы LV (проверяются локально на LV)
LV_SERVICES=(
    "ukusongs_bot"
    "video_downloader"
    "vpn_switch_bot"
    "ym_downloader"
    "msk_lv_report_bot"
    "nginx"
    "postgresql"
    "postgresql@16-main"
    "x-ui"
)

# Сервисы MSK (проверяются через SSH)
MSK_SERVICES=(
    "ukusongs"
    "4strings"
    "nginx"
    "postgresql"
    "ukusongs-tg-proxy"
    "ukusongs-db-backup"
)

#------------------------------------------------------------------------------
# Функции
#------------------------------------------------------------------------------

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >&2
}

error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2
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

        results+="{\"name\":\"$service_escaped\",\"server\":\"LV\",\"status\":\"$status\",\"enabled\":\"$enabled\",\"info\":\"$info_escaped\"}"
    done

    results+="]"
    echo "$results"
}

#------------------------------------------------------------------------------
# Сбор статуса MSK сервисов (через SSH)
#------------------------------------------------------------------------------
check_msk_services() {
    log "Checking MSK services via SSH..."

    local results="["
    local first=true

    for service in "${MSK_SERVICES[@]}"; do
        # SSH команда для получения статуса сервиса
        local status=$(ssh -i "$MSK_SSH_KEY" \
            -o StrictHostKeyChecking=no \
            -o ConnectTimeout=10 \
            "$MSK_USER@$MSK_HOST" \
            "systemctl is-active $service 2>/dev/null" 2>/dev/null | tr -d '\n' || echo "unknown")

        local enabled=$(ssh -i "$MSK_SSH_KEY" \
            -o StrictHostKeyChecking=no \
            -o ConnectTimeout=10 \
            "$MSK_USER@$MSK_HOST" \
            "systemctl is-enabled $service 2>/dev/null" 2>/dev/null | tr -d '\n' || echo "unknown")

        local main_pid=$(ssh -i "$MSK_SSH_KEY" \
            -o StrictHostKeyChecking=no \
            -o ConnectTimeout=10 \
            "$MSK_USER@$MSK_HOST" \
            "systemctl show $service -p MainPID --value 2>/dev/null" 2>/dev/null | tr -d '\n' || echo "0")

        local active_state=$(ssh -i "$MSK_SSH_KEY" \
            -o StrictHostKeyChecking=no \
            -o ConnectTimeout=10 \
            "$MSK_USER@$MSK_HOST" \
            "systemctl show $service -p ActiveState --value 2>/dev/null" 2>/dev/null | tr -d '\n' || echo "unknown")

        local sub_state=$(ssh -i "$MSK_SSH_KEY" \
            -o StrictHostKeyChecking=no \
            -o ConnectTimeout=10 \
            "$MSK_USER@$MSK_HOST" \
            "systemctl show $service -p SubState --value 2>/dev/null" 2>/dev/null | tr -d '\n' || echo "unknown")

        local start_time=$(ssh -i "$MSK_SSH_KEY" \
            -o StrictHostKeyChecking=no \
            -o ConnectTimeout=10 \
            "$MSK_USER@$MSK_HOST" \
            "systemctl show $service -p ActiveEnterTimestamp --value 2>/dev/null" 2>/dev/null | tr -d '\n' || echo "")

        local info="${main_pid}|${active_state}|${sub_state}|loaded|${start_time}"

        local service_escaped=$(json_escape "$service")
        local info_escaped=$(json_escape "$info")

        if [ "$first" = true ]; then
            first=false
        else
            results+=","
        fi

        results+="{\"name\":\"$service_escaped\",\"server\":\"MSK\",\"status\":\"$status\",\"enabled\":\"$enabled\",\"info\":\"$info_escaped\"}"
    done

    results+="]"
    echo "$results"
}

#------------------------------------------------------------------------------
# Main
#------------------------------------------------------------------------------
main() {
    local mode="${1:-all}"  # all, lv, msk

    log "Starting service check (mode: $mode)..."

    case "$mode" in
        lv)
            check_lv_services
            ;;
        msk)
            check_msk_services
            ;;
        all|*)
            # Собираем оба набора
            local lv_results=$(check_lv_services)
            local msk_results=$(check_msk_services)

            # Объединяем в один JSON
            echo "["
            echo "$lv_results" | sed 's/^\[//;s/\]$//'
            echo ","
            echo "$msk_results" | sed 's/^\[//;s/\]$//'
            echo "]"
            ;;
    esac

    log "Service check completed"
}

main "$@"