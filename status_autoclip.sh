#!/bin/bash

# AutoClip 系统状态检查脚本
# 版本: 2.0
# 功能: 检查AutoClip系统各服务的运行状态

set -euo pipefail

# =============================================================================
# 配置区域
# =============================================================================

# 服务端口配置
BACKEND_PORT=8001
FRONTEND_PORT=3000
REDIS_PORT=6379

# PID文件
BACKEND_PID_FILE="backend.pid"
FRONTEND_PID_FILE="frontend.pid"
CELERY_PID_FILE="celery.pid"

# 日志目录
LOG_DIR="logs"

# =============================================================================
# 颜色和样式定义
# =============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

# 图标定义
ICON_SUCCESS="✅"
ICON_ERROR="❌"
ICON_WARNING="⚠️"
ICON_INFO="ℹ️"
ICON_HEALTH="💚"
ICON_SICK="🤒"
ICON_ROCKET="🚀"
ICON_DATABASE="🗄️"
ICON_WORKER="👷"
ICON_WEB="🌐"
ICON_REDIS="🔴"

# =============================================================================
# 工具函数
# =============================================================================

log_info() {
    echo -e "${BLUE}${ICON_INFO} $1${NC}"
}

log_success() {
    echo -e "${GREEN}${ICON_SUCCESS} $1${NC}"
}

log_warning() {
    echo -e "${YELLOW}${ICON_WARNING} $1${NC}"
}

log_error() {
    echo -e "${RED}${ICON_ERROR} $1${NC}"
}

log_header() {
    echo -e "\n${PURPLE}${ICON_ROCKET} $1${NC}"
    echo -e "${PURPLE}$(printf '=%.0s' {1..50})${NC}"
}

# 检查服务健康状态
check_service_health() {
    local url="$1"
    local service_name="$2"
    
    if curl -fsS "$url" >/dev/null 2>&1; then
        echo -e "${GREEN}${ICON_HEALTH} $service_name 健康${NC}"
        return 0
    else
        echo -e "${RED}${ICON_SICK} $service_name 不健康${NC}"
        return 1
    fi
}

# 检查进程状态
check_process_status() {
    local pid_file="$1"
    local service_name="$2"
    local process_pattern="$3"
    
    if [[ -f "$pid_file" ]]; then
        local pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            echo -e "${GREEN}${ICON_SUCCESS} $service_name 运行中 (PID: $pid)${NC}"
            return 0
        else
            echo -e "${RED}${ICON_ERROR} $service_name PID文件存在但进程不存在${NC}"
            return 1
        fi
    else
        # 检查是否有相关进程在运行
        if pgrep -f "$process_pattern" >/dev/null; then
            local pids=$(pgrep -f "$process_pattern" | tr '\n' ' ')
            echo -e "${YELLOW}${ICON_WARNING} $service_name 运行中但无PID文件 (PIDs: $pids)${NC}"
            return 0
        else
            echo -e "${RED}${ICON_ERROR} $service_name 未运行${NC}"
            return 1
        fi
    fi
}

# 获取服务信息
get_service_info() {
    local service_name="$1"
    local pid_file="$2"
    local process_pattern="$3"
    
    echo -e "\n${CYAN}📊 $service_name 详细信息:${NC}"
    
    if [[ -f "$pid_file" ]]; then
        local pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            echo "  PID: $pid"
            echo "  进程信息:"
            ps -p "$pid" -o pid,ppid,etime,pcpu,pmem,cmd --no-headers 2>/dev/null | while read line; do
                echo "    $line"
            done
        fi
    else
        local pids=$(pgrep -f "$process_pattern" 2>/dev/null || true)
        if [[ -n "$pids" ]]; then
            echo "  PIDs: $pids"
            echo "  进程信息:"
            echo "$pids" | while read pid; do
                ps -p "$pid" -o pid,ppid,etime,pcpu,pmem,cmd --no-headers 2>/dev/null | while read line; do
                    echo "    $line"
                done
            done
        fi
    fi
}

# =============================================================================
# 检查函数
# =============================================================================

check_redis() {
    log_header "Redis 服务状态"
    
    if redis-cli ping >/dev/null 2>&1; then
        log_success "Redis 服务运行正常"
        
        # 获取Redis信息
        echo -e "\n${CYAN}📊 Redis 详细信息:${NC}"
        redis-cli info server | grep -E "(redis_version|uptime_in_seconds|connected_clients)" | while read line; do
            echo "  $line"
        done
        return 0
    else
        log_error "Redis 服务未运行或无法连接"
        return 1
    fi
}

check_backend() {
    log_header "后端 API 服务状态"
    
    # 检查进程状态
    if check_process_status "$BACKEND_PID_FILE" "后端服务" "uvicorn.*backend.main:app"; then
        # 检查健康状态
        if check_service_health "http://localhost:$BACKEND_PORT/api/v1/health/" "后端API"; then
            get_service_info "后端服务" "$BACKEND_PID_FILE" "uvicorn.*backend.main:app"
            return 0
        else
            log_warning "后端进程运行但API不响应"
            return 1
        fi
    else
        return 1
    fi
}

check_frontend() {
    log_header "前端服务状态"
    
    # 检查进程状态
    if check_process_status "$FRONTEND_PID_FILE" "前端服务" "npm.*dev\|vite"; then
        # 检查健康状态
        if check_service_health "http://localhost:$FRONTEND_PORT/" "前端界面"; then
            get_service_info "前端服务" "$FRONTEND_PID_FILE" "npm.*dev\|vite"
            return 0
        else
            log_warning "前端进程运行但服务不响应"
            return 1
        fi
    else
        return 1
    fi
}

check_celery() {
    log_header "Celery Worker 状态"
    
    # 检查进程状态
    if check_process_status "$CELERY_PID_FILE" "Celery Worker" "celery.*worker"; then
        get_service_info "Celery Worker" "$CELERY_PID_FILE" "celery.*worker"
        
        # 检查Celery连接
        if command -v celery >/dev/null 2>&1; then
            echo -e "\n${CYAN}📊 Celery 详细信息:${NC}"
            if PYTHONPATH="${PWD}:${PYTHONPATH:-}" celery -A backend.core.celery_app inspect active >/dev/null 2>&1; then
                log_success "Celery 连接正常"
                
                # 获取活跃任务
                local active_tasks=$(PYTHONPATH="${PWD}:${PYTHONPATH:-}" celery -A backend.core.celery_app inspect active 2>/dev/null | jq -r '.[] | length' 2>/dev/null || echo "0")
                echo "  活跃任务数: $active_tasks"
            else
                log_warning "Celery 连接测试失败"
            fi
        fi
        return 0
    else
        return 1
    fi
}

check_database() {
    log_header "数据库状态"
    
    if [[ -f "data/autoclip.db" ]]; then
        log_success "数据库文件存在"
        
        # 获取数据库信息
        echo -e "\n${CYAN}📊 数据库详细信息:${NC}"
        local db_size=$(du -h "data/autoclip.db" 2>/dev/null | cut -f1)
        echo "  文件大小: $db_size"
        
        # 检查数据库连接
        if python -c "
import sys
sys.path.insert(0, '.')
from backend.core.database import test_connection
if test_connection():
    print('数据库连接正常')
else:
    print('数据库连接失败')
    sys.exit(1)
" 2>/dev/null; then
            log_success "数据库连接正常"
        else
            log_error "数据库连接失败"
            return 1
        fi
    else
        log_warning "数据库文件不存在"
        return 1
    fi
}

check_logs() {
    log_header "日志文件状态"
    
    if [[ -d "$LOG_DIR" ]]; then
        log_success "日志目录存在"
        
        echo -e "\n${CYAN}📊 日志文件信息:${NC}"
        ls -la "$LOG_DIR"/*.log 2>/dev/null | while read line; do
            echo "  $line"
        done
        
        # 显示最新日志
        echo -e "\n${CYAN}📝 最新日志 (最后10行):${NC}"
        for log_file in "$LOG_DIR"/*.log; do
            if [[ -f "$log_file" ]]; then
                echo -e "\n${YELLOW}$(basename "$log_file"):${NC}"
                tail -n 5 "$log_file" 2>/dev/null | while read line; do
                    echo "  $line"
                done
            fi
        done
    else
        log_warning "日志目录不存在"
    fi
}

# =============================================================================
# 主函数
# =============================================================================

main() {
    log_header "AutoClip 系统状态检查 v2.0"
    
    local overall_status=0
    
    # 检查各个服务
    check_redis || overall_status=1
    check_database || overall_status=1
    check_celery || overall_status=1
    check_backend || overall_status=1
    check_frontend || overall_status=1
    check_logs
    
    # 显示总体状态
    log_header "系统总体状态"
    
    if [[ $overall_status -eq 0 ]]; then
        log_success "所有服务运行正常"
        echo ""
        echo -e "${WHITE}🎉 AutoClip 系统完全健康！${NC}"
        echo ""
        echo -e "${CYAN}🌐 访问地址:${NC}"
        echo -e "  前端界面: http://localhost:$FRONTEND_PORT"
        echo -e "  后端API:  http://localhost:$BACKEND_PORT"
        echo -e "  API文档:  http://localhost:$BACKEND_PORT/docs"
    else
        log_error "部分服务存在问题"
        echo ""
        echo -e "${YELLOW}💡 建议操作:${NC}"
        echo -e "  1. 查看日志文件了解详细错误信息"
        echo -e "  2. 重启系统: ./stop_autoclip.sh && ./start_autoclip.sh"
        echo -e "  3. 检查环境配置和依赖"
    fi
    
    echo ""
    echo -e "${CYAN}📋 常用命令:${NC}"
    echo -e "  启动系统: ./start_autoclip.sh"
    echo -e "  停止系统: ./stop_autoclip.sh"
    echo -e "  查看日志: tail -f $LOG_DIR/*.log"
}

# 运行主函数
main "$@"
