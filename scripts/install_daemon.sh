#!/bin/bash
# Free API Hub — 安装/卸载 launchd 守护进程（开机自启 + 自动拉活）
HUB_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_SRC="$HUB_DIR/scripts/com.user.freeapihub.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.user.freeapihub.plist"

case "${1:-install}" in
    install)
        echo "安装 launchd 守护进程..."
        sed "s|__HUB_DIR__|$HUB_DIR|g" "$PLIST_SRC" > "$PLIST_DST"
        launchctl unload "$PLIST_DST" 2>/dev/null || true
        launchctl load "$PLIST_DST"
        echo "✅ 守护进程已安装并启动"
        echo "   开机自启: 已启用"
        echo "   自动拉活: 已启用"
        echo ""
        echo "管理命令："
        echo "  launchctl start   com.user.freeapihub    # 手动启动"
        echo "  launchctl stop    com.user.freeapihub    # 手动停止"
        echo "  launchctl unload  $PLIST_DST  # 卸载"
        echo ""
        sleep 1
        curl -s http://127.0.0.1:5080/health
        ;;
    uninstall)
        echo "卸载 launchd 守护进程..."
        launchctl unload "$PLIST_DST" 2>/dev/null || true
        rm -f "$PLIST_DST"
        echo "✅ 守护进程已卸载"
        ;;
    *)
        echo "用法: $0 [install|uninstall]"
        exit 1
        ;;
esac
