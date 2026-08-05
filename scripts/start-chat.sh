#!/bin/bash
# Free API Hub — 启动聊天实例
HUB_DIR="$(cd "$(dirname "$0")/.." && pwd)"
NAME="chat"
CONFIG="config/chat.yaml"
LOG_FILE="$HUB_DIR/data/chat.log"
PID_FILE="$HUB_DIR/data/chat.pid"

cd "$HUB_DIR"

if [ -f "$PID_FILE" ]; then
    old_pid=$(cat "$PID_FILE")
    if kill -0 "$old_pid" 2>/dev/null; then
        echo "❌ 聊天实例已在运行 (PID: $old_pid)"
        echo "   如需重启，请先运行: bash scripts/stop-chat.sh"
        exit 1
    fi
    rm -f "$PID_FILE"
fi

if [ ! -f "$CONFIG" ]; then
    echo "❌ 配置文件不存在: $CONFIG"
    exit 1
fi

echo "启动聊天实例 ($CONFIG)..."
nohup ./venv/bin/python src/server.py --config "$CONFIG" >> "$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"

sleep 2
PORT=$(grep -A5 '^gateway:' "$CONFIG" | grep 'port:' | awk '{print $2}')
if kill -0 "$PID" 2>/dev/null; then
    echo "✅ 聊天实例已启动 (PID: $PID, :$PORT)"
    echo "   日志: $LOG_FILE"
else
    echo "❌ 启动失败，查看日志:"
    tail -5 "$LOG_FILE"
    exit 1
fi
