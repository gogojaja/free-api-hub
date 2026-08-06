#!/bin/bash
# Free API Hub — 安装/卸载 launchd 守护进程（双实例）
HUB_DIR="$(cd "$(dirname "$0")/.." && pwd)"

install_one() {
    local name="$1"
    local port="$2"
    local PLIST_SRC="$HUB_DIR/scripts/com.user.freeapihub.$name.plist"
    local PLIST_DST="$HOME/Library/LaunchAgents/com.user.freeapihub.$name.plist"

    sed "s|__HUB_DIR__|$HUB_DIR|g" "$PLIST_SRC" > "$PLIST_DST"
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    launchctl load "$PLIST_DST"
    echo "  ✅ $name 守护进程已安装 (:$port)"
}

uninstall_one() {
    local name="$1"
    local PLIST_DST="$HOME/Library/LaunchAgents/com.user.freeapihub.$name.plist"
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    rm -f "$PLIST_DST"
    echo "  ✅ $name 守护进程已卸载"
}

case "${1:-install}" in
    install)
        echo "安装 launchd 守护进程..."
        install_one "chat" "5080"
        install_one "code" "5081"
        echo ""
        echo "管理命令："
        echo "  launchctl start|stop com.user.freeapihub.chat"
        echo "  launchctl start|stop com.user.freeapihub.code"
        echo ""
        sleep 1
        curl -s http://127.0.0.1:5080/health
        echo ""
        curl -s http://127.0.0.1:5081/health
        ;;
    uninstall)
        echo "卸载 launchd 守护进程..."
        uninstall_one "chat"
        uninstall_one "code"
        echo "✅ 全部守护进程已卸载"
        ;;
    *)
        echo "用法: $0 [install|uninstall]"
        exit 1
        ;;
esac
