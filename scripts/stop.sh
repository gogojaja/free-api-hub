#!/bin/bash
# Free API Hub — 停止网关
HUB_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$HUB_DIR/data/gateway.pid"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "✅ 网关已停止 (PID: $PID)"
    else
        echo "⚠️  进程已不存在"
    fi
    rm -f "$PID_FILE"
else
    # 尝试通过端口查找
    PID=$(lsof -ti:5080 2>/dev/null)
    if [ -n "$PID" ]; then
        kill "$PID"
        echo "✅ 网关已停止 (PID: $PID)"
    else
        echo "⚠️  网关未在运行"
    fi
fi
