#!/bin/bash
# Free API Hub — 停止编程实例
HUB_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$HUB_DIR/data/code.pid"
PORT=5081

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "✅ 编程实例已停止 (PID: $PID)"
    else
        echo "⚠️  编程实例进程已不存在"
    fi
    rm -f "$PID_FILE"
else
    PID=$(lsof -ti:$PORT 2>/dev/null)
    if [ -n "$PID" ]; then
        kill "$PID"
        echo "✅ 编程实例已停止 (PID: $PID)"
    else
        echo "⚠️  编程实例未在运行"
    fi
fi
