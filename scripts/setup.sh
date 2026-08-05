#!/bin/bash
# Free API Hub — 一键安装配置脚本
# 创建 venv → 安装依赖 → 交互式录入 API Key → 生成配置文件

set -e

HUB_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HUB_DIR"

echo "========================================"
echo "  Free API Hub — 一键安装配置"
echo "========================================"
echo ""

# ---- Step 1: 创建虚拟环境 ----
echo "[1/4] 创建虚拟环境..."
if [ -d "venv" ]; then
    echo "  → venv 已存在，跳过"
else
    python3 -m venv venv
    echo "  ✅ venv 已创建"
fi

# ---- Step 2: 安装依赖 ----
echo "[2/4] 安装 Python 依赖..."
./venv/bin/pip install -q -r requirements.txt
echo "  ✅ 依赖安装完成"

# ---- Step 3: 交互式录入 API Key ----
echo "[3/4] 配置 API 提供商（直接回车可跳过该平台）"
echo ""

CONFIG_FILE="$HUB_DIR/config/chat.yaml"

cat > "$CONFIG_FILE" << 'YAML_HEAD'
# Free API Hub 提供商配置
# 由 setup.sh 自动生成

gateway:
  port: 5080
  retry_seconds: 60

providers:
YAML_HEAD

input_key() {
    local prompt="$1"
    local key=""
    if [ -t 0 ]; then
        # 终端模式：直接读取
        read -p "$prompt" key
    else
        # 管道模式：从 /dev/tty 读取
        read -p "$prompt" key < /dev/tty
    fi
    echo "$key"
}

append_provider() {
    local name="$1"
    local display="$2"
    local endpoint="$3"
    local model="$4"
    local priority="$5"
    local extra_headers="$6"
    local note="$7"

    echo ""
    echo "--- $display ($name) ---"
    echo "  接口: $endpoint"
    echo "  模型: $model"
    [ -n "$note" ] && echo "  提示: $note"

    api_key=$(input_key "  请输入 API Key（直接回车跳过）: ")
    echo ""

    if [ -z "$api_key" ]; then
        echo "  ⏭️  跳过 $display"
        return
    fi

    cat >> "$CONFIG_FILE" << PROVIDER
  - name: $name
    display: $display
    endpoint: $endpoint
    model: $model
    priority: $priority
    api_key: $api_key
PROVIDER

    if [ "$extra_headers" = "yes" ]; then
        cat >> "$CONFIG_FILE" << PROVIDER
    headers:
      HTTP-Referer: http://localhost:5080
      X-Title: Free-API-Hub
PROVIDER
    fi

    echo "  ✅ $display 已配置"
}

append_provider "openrouter"   "OpenRouter"       "https://openrouter.ai/api/v1"                "deepseek/deepseek-v4-flash:free" 1 "yes" ""
append_provider "zhipu"        "智谱AI"           "https://open.bigmodel.cn/api/paas/v4"        "glm-4-flash"                      2 ""    ""
append_provider "siliconflow"  "硅基流动"         "https://api.siliconflow.cn/v1"               "deepseek-ai/DeepSeek-R1"          3 ""    ""
append_provider "volcengine"   "火山引擎"         "https://ark.cn-beijing.volces.com/api/v3"    "<推理接入点ID>"                    4 ""    "需先在 console.volcengine.com 创建推理接入点"

echo ""
echo "  ✅ 配置文件已生成: $CONFIG_FILE"

# ---- Step 4: 验证 ----
echo "[4/4] 验证配置..."
for cfg in config/chat.yaml config/code.yaml; do
    if [ ! -f "$cfg" ]; then
        echo "  ⏭️  $cfg 不存在，跳过验证"
        continue
    fi
    ./venv/bin/python -c "
import sys
sys.path.insert(0, '$HUB_DIR/src')
from gateway import APIGateway
try:
    gw = APIGateway(config_path='$HUB_DIR/$cfg')
    status = gw.get_status()
    configured = [p['name'] for p in status['providers_configured'] if p['has_key']]
    if configured:
        print(f'  ✅ [$cfg] 已配置 {len(configured)} 个提供商: {\", \".join(configured)}')
    else:
        print(f'  ❌ [$cfg] 未配置任何提供商')
        sys.exit(1)
except Exception as e:
    print(f'  ❌ [$cfg] 验证失败: {e}')
    sys.exit(1)
" 2>&1
done

echo ""
echo "========================================"
echo "  🎉 安装完成！"
echo "========================================"
echo ""
echo "已生成: config/chat.yaml"
echo ""
echo "如需配置编程实例，请编辑 config/code.yaml 填入 API Key"
echo ""
echo "启动网关："
echo "  bash scripts/start.sh"
echo ""
echo "停止网关："
echo "  bash scripts/stop.sh"
echo ""
echo "单实例管理："
echo "  bash scripts/start-chat.sh    # 仅聊天 (:5080)"
echo "  bash scripts/start-code.sh    # 仅编程 (:5081)"
echo "  bash scripts/stop-chat.sh"
echo "  bash scripts/stop-code.sh"
echo ""
echo "安装守护进程（开机自启）："
echo "  bash scripts/install_daemon.sh"
echo ""
echo "查看状态："
echo "  curl http://127.0.0.1:5080/gateway/status"
echo "  curl http://127.0.0.1:5081/gateway/status"
echo ""
