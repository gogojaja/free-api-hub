#!/bin/bash
# Free API Hub — 停止所有网关实例
HUB_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "========================================"
echo "  Free API Hub — 停止全部实例"
echo "========================================"

bash "$HUB_DIR/scripts/stop-chat.sh"
bash "$HUB_DIR/scripts/stop-code.sh"

echo "全部实例已停止"
