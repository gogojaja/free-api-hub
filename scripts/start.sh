#!/bin/bash
# Free API Hub — 启动网关（后台守护进程）
HUB_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="$HUB_DIR/data/gateway.log"
PID_FILE="$HUB_DIR/data/gateway.pid"
PORT=5080

cd "$HUB_DIR"

# 检查是否已在运行
if [ -f "$PID_FILE" ]; then
    old_pid=$(cat "$PID_FILE")
    if kill -0 "$old_pid" 2>/dev/null; then
        echo "❌ 网关已在运行 (PID: $old_pid)"
        echo "   如需重启，请先运行: bash scripts/stop.sh"
        exit 1
    fi
    rm -f "$PID_FILE"
fi

# 检查配置
if [ ! -f "config/providers.yaml" ]; then
    echo "❌ 配置文件不存在，请先运行: bash scripts/setup.sh"
    exit 1
fi

echo "启动 Free API Hub (:$PORT)..."
nohup ./venv/bin/python src/server.py "$PORT" >> "$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"

# 等待启动完成
sleep 2
if kill -0 "$PID" 2>/dev/null; then
    echo "✅ 网关已启动 (PID: $PID)"
    echo "   日志: $LOG_FILE"
    echo "   接口: http://127.0.0.1:$PORT"
    echo ""
    echo "健康检查:"
    curl -s http://127.0.0.1:$PORT/health | python3 -m json.tool
else
    echo "❌ 启动失败，查看日志:"
    tail -10 "$LOG_FILE"
    exit 1
fi
