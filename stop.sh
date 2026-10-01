#!/usr/bin/env bash
# QuantLab 停止脚本（Git Bash / macOS / Linux）
#
# 与 scripts/stop.ps1 行为对齐：
#   ① 先确认 8000 端口上跑的确实是 QuantLab（健康检查自报 app == "quant-lab"）
#   ② 再按「命令行特征」定位进程，只杀用本项目参数拉起来的 uvicorn
#
# 「按端口杀进程」会把 8000 上任何别的服务一起干掉，所以这里坚持特征匹配。

set -u

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$APP_ROOT/logs"
PID_FILE="$LOG_DIR/server.pid"
HEALTH_URL="http://127.0.0.1:8000/api/health"
PATTERN="uvicorn main:app --host 127\.0\.0\.1 --port 8000"

find_python() {
    local cand
    for cand in python3 python; do
        if command -v "$cand" >/dev/null 2>&1; then
            command -v "$cand"
            return 0
        fi
    done
    return 1
}

PYTHON_BIN="$(find_python)" || {
    echo "未找到 Python，无法确认服务身份。"
    exit 0
}

# ── ① 确认身份 ──────────────────────────────────────────────────────────────

if ! "$PYTHON_BIN" - "$HEALTH_URL" <<'PY' >/dev/null 2>&1
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=2) as resp:
        sys.exit(0 if json.load(resp).get("app") == "quant-lab" else 1)
except Exception:
    sys.exit(1)
PY
then
    echo "QuantLab 当前未在运行，或 8000 端口被其他程序占用。停止操作已取消。"
    exit 0
fi

# ── ② 定位并停止 ────────────────────────────────────────────────────────────

killed_any=0

kill_if_quantlab() {
    local pid="$1"
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    local cmd
    cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    if [ -z "$cmd" ] && [ -r "/proc/$pid/cmdline" ]; then
        cmd="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)"
    fi
    case "$cmd" in
        *"uvicorn main:app"*"--port 8000"*)
            kill "$pid" 2>/dev/null && killed_any=1
            return 0
            ;;
    esac
    return 1
}

# 先试 pid 文件（uvicorn --reload 会 fork 子进程，连子进程一起收）
if [ -f "$PID_FILE" ]; then
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$pid" ]; then
        for child in $(pgrep -P "$pid" 2>/dev/null || true); do
            kill_if_quantlab "$child"
        done
        kill_if_quantlab "$pid"
    fi
fi

# pid 文件不管用时，按命令行特征兜底（等价于 stop.ps1 的 CommandLine 匹配）
if command -v pgrep >/dev/null 2>&1; then
    for pid in $(pgrep -f "$PATTERN" 2>/dev/null || true); do
        kill_if_quantlab "$pid"
    done
fi

rm -f "$PID_FILE"

if [ "$killed_any" -eq 1 ]; then
    echo "QuantLab 已停止。"
else
    echo "已确认 QuantLab 在运行，但未能定位到可停止的进程。"
    echo "如仍可访问 $HEALTH_URL，请手动结束该 uvicorn 进程。"
    if ! command -v pgrep >/dev/null 2>&1; then
        echo "提示：当前环境没有 pgrep（Git Bash for Windows 常见），"
        echo "      改双击「停止 QuantLab.cmd」即可，它用 PowerShell 按命令行特征停止。"
    fi
fi
