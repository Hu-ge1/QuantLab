#!/usr/bin/env bash
# QuantLab 启动脚本（Git Bash / macOS / Linux）
#
# 与 scripts/start.ps1 行为对齐：
#   Python 预检 → 已在运行？ → 端口占用？ → 依赖自装 → 后台拉起 → 轮询就绪 → 打开浏览器
#
# 健康探测与端口探测都用 Python 自己实现，不依赖 curl / lsof / netstat，
# 因为 Python 本来就是本项目的硬性前置条件。
#
# 环境变量：
#   QUANTLAB_PYTHON   指定 Python 解释器（PATH 上有多个 python 时用）
#
# Windows 用户建议直接用「启动 QuantLab.cmd」——本脚本在 Git Bash 下可用，
# 但后台进程的停止不如 .ps1 版本可靠。

set -u

APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$APP_ROOT/backend"
LOG_DIR="$APP_ROOT/logs"
HEALTH_URL="http://127.0.0.1:8000/api/health"
APP_URL="http://127.0.0.1:8000"
PORT=8000
BOOT_TIMEOUT_SECONDS=30

# ── 工具函数 ────────────────────────────────────────────────────────────────

find_python() {
    # PATH 上有多个 python 时（例如 conda / 便携版 / 系统版），
    # 用 QUANTLAB_PYTHON 显式指定解释器，避免装到错的环境里。
    if [ -n "${QUANTLAB_PYTHON:-}" ] && [ -x "${QUANTLAB_PYTHON}" ]; then
        printf '%s\n' "$QUANTLAB_PYTHON"
        return 0
    fi
    local cand
    for cand in python3 python; do
        if command -v "$cand" >/dev/null 2>&1; then
            command -v "$cand"
            return 0
        fi
    done
    return 1
}

# 只有自报 app == "quant-lab" 才算「本项目在跑」，光看端口通不算。
is_running() {
    "$PYTHON_BIN" - "$HEALTH_URL" <<'PY' >/dev/null 2>&1
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=2) as resp:
        sys.exit(0 if json.load(resp).get("app") == "quant-lab" else 1)
except Exception:
    sys.exit(1)
PY
}

port_in_use() {
    "$PYTHON_BIN" - "127.0.0.1" "$PORT" <<'PY' >/dev/null 2>&1
import socket, sys
sock = socket.socket()
sock.settimeout(1)
try:
    sys.exit(0 if sock.connect_ex((sys.argv[1], int(sys.argv[2]))) == 0 else 1)
finally:
    sock.close()
PY
}

open_browser() {
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$1" >/dev/null 2>&1 &
    elif command -v open >/dev/null 2>&1; then
        open "$1" >/dev/null 2>&1 &
    elif command -v explorer.exe >/dev/null 2>&1; then
        explorer.exe "$1" >/dev/null 2>&1 &
    else
        echo "请手动打开: $1"
    fi
}

die() {
    echo "$1" >&2
    exit 1
}

# ── ① 找到 Python ───────────────────────────────────────────────────────────

PYTHON_BIN="$(find_python)" || die "未找到 Python。请安装 Python 3.10 或更高版本。"
export PYTHON_BIN

# ── ② 已经在运行？ ──────────────────────────────────────────────────────────

if is_running; then
    echo "QuantLab 已在运行，直接打开浏览器..."
    open_browser "$APP_URL"
    exit 0
fi

# ── ③ 端口被别的程序占了？ ──────────────────────────────────────────────────

if port_in_use; then
    die "端口 $PORT 已被其他程序占用，QuantLab 无法启动。"
fi

# ── ④ 依赖自检与安装 ────────────────────────────────────────────────────────

if ! "$PYTHON_BIN" -c "import fastapi, uvicorn, httpx, akshare, pandas, numpy, pydantic" >/dev/null 2>&1; then
    echo "首次运行：正在安装后端依赖..."
    if ! "$PYTHON_BIN" -m pip install -r "$BACKEND_DIR/requirements.txt"; then
        die "依赖安装失败。请检查网络后重试。"
    fi
fi

# ── ⑤ 后台拉起服务 ──────────────────────────────────────────────────────────

mkdir -p "$LOG_DIR" || die "无法创建日志目录: $LOG_DIR"
STDOUT_LOG="$LOG_DIR/server.out.log"
STDERR_LOG="$LOG_DIR/server.err.log"

cd "$BACKEND_DIR" || die "无法进入后端目录: $BACKEND_DIR"

nohup "$PYTHON_BIN" -m uvicorn main:app --host 127.0.0.1 --port "$PORT" \
    >"$STDOUT_LOG" 2>"$STDERR_LOG" &
SERVER_PID=$!
echo "$SERVER_PID" >"$LOG_DIR/server.pid"

echo "正在启动 QuantLab..."
MAX_TRIES=$((BOOT_TIMEOUT_SECONDS * 2))
for _ in $(seq 1 "$MAX_TRIES"); do
    sleep 0.5
    if is_running; then
        echo "QuantLab 已启动: $APP_URL"
        open_browser "$APP_URL"
        exit 0
    fi
done

# ── 启动失败 ────────────────────────────────────────────────────────────────

echo "QuantLab 启动失败。错误日志: $STDERR_LOG" >&2
if kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null
fi
rm -f "$LOG_DIR/server.pid"
if [ -f "$STDERR_LOG" ]; then
    tail -n 30 "$STDERR_LOG"
fi
exit 1
