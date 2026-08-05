#!/bin/bash
# Free API Hub — 启动所有网关实例
HUB_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "========================================"
echo "  Free API Hub — 启动全部实例"
echo "========================================"

bash "$HUB_DIR/scripts/start-chat.sh"
echo ""
bash "$HUB_DIR/scripts/start-code.sh"

echo ""
echo "健康检查："
sleep 2
curl -s http://127.0.0.1:5080/health && echo ""
curl -s http://127.0.0.1:5081/health && echo ""
