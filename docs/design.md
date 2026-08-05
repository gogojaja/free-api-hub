# Free API Hub — 设计文档

## 1. 架构

### 1.1 两层架构

```
                         ┌──────────────────────────────────────┐
                         │              客户端                    │
                         │     (opencode / VSCode / curl)        │
                         └──────────────┬───────────────────────┘
                                        │
                              ┌─────────┴─────────┐
                              │                   │
                       chat   │                   │  code
                       :5080  │                   │  :5081
                              ▼                   ▼
                ┌────────────────────┐  ┌────────────────────┐
                │   server.py        │  │   server.py        │
                │   config/chat.yaml │  │   config/code.yaml │
                │   .env (密钥)      │  │   .env (密钥)      │
                └───────┬───────────┘  └───────┬────────────┘
                        │                      │
                        ▼                      ▼
                ┌────────────────────┐  ┌────────────────────┐
                │   APIGateway 网关   │  │   APIGateway 网关   │
                │   +线程锁+熔断器    │  │   +线程锁+熔断器    │
                └───────┬───────────┘  └───────┬────────────┘
                        │                      │
              ┌─────────┼──────────┐   ┌──────┼──┬──────┬──────┐
              ▼         ▼          ▼   ▼       ▼  ▼      ▼      ▼
            智谱     智谱     OpenRouter DeepSeek 智谱 OpenRouter 火山
           GLM-4.7 GLM-4.5-Air  Free   V4Flash GLM-4.7 Free   Doubao
            P1        P2        P3      P1      P2    P3       P4
```



## 2. 目录结构

```
free-api-hub/
├── src/
│   ├── server.py          # Flask HTTP 服务入口，--config 参数选择配置，管理端点认证
│   └── gateway.py         # 多 Provider 网关核心，线程锁保护，环境变量加载，熔断器
├── config/
│   ├── chat.yaml          # 聊天实例配置 (port 5080: 智谱GLM-4.7 → GLM-4.5-Air → OpenRouter)
│   └── code.yaml          # 编程实例配置 (port 5081: DeepSeek V4 Flash → 智谱GLM-4.7 → OpenRouter → 火山Doubao)
├── .env                   # 环境变量（API Key + ADMIN_TOKEN，不提交 Git）
├── data/
│   ├── chat.log           # 聊天实例运行日志
│   ├── code.log           # 编程实例运行日志
│   ├── chat_usage.json    # 聊天实例用量追踪
│   └── code_usage.json    # 编程实例用量追踪
├── scripts/
│   ├── start-chat.sh      # 启动聊天实例
│   ├── start-code.sh      # 启动编程实例
│   └── check_env.py       # 环境标记核验（待创建）
├── tests/
│   └── test_regression.py # 回归测试（12 个用例）
├── docs/
│   └── design.md          # 设计文档
├── backup/                # 配置文件自动备份
├── requirements.txt       # Python 依赖
├── .gitignore             # 包含 .env 排除规则
├── AGENTS.md              # AI 助理指令
└── venv/                  # 虚拟环境（不提交）
```

## 3. 核心模块

### 3.1 APIGateway (gateway.py)

```
┌──────────────────────────────────────┐
│  APIGateway                           │
│──────────────────────────────────────│
│  - config_path                        │
│  - log_path                           │
│  - usage_path                         │
│  - providers: list (按 priority 排序)   │
│  - failed_providers: dict              │
│  - retry_seconds: 15                   │
│  - request_timeout: 30                 │
├──────────────────────────────────────┤
│  + _load_config()                     │
│  + _setup_logging()                   │
│  + _has_creds(p)                      │
│  + _sign_v4(request)  (AK/SK V4 签名)  │
│  + _load_usage() / _save_usage()      │
│  + get_available_providers()          │
│  + call_api(messages, stream, model)  │
│  + get_status()                       │
│  + list_models()                      │
│  + reset_failures()                   │
└──────────────────────────────────────┘
```

### 3.2 数据流

```
call_api()
  │
  ├→ get_available_providers()
  │   ├→ _has_creds(): 检查 api_key/ak_sk 是否有效
  │   └→ 检查是否在 failed_providers 中且未到 retry_seconds
  │
  ├→ for provider in providers:
  │   ├→ 构建请求 URL、Headers、Payload
  │   ├→ api_model = provider["model"]  (始终使用提供商自身模型)
  │   ├→ 根据 auth_type 选择认证方式: bearer / ak_sk
  │   ├→ POST /chat/completions
  │   ├→ 成功 → 记录 current_provider/current_provider_display → 记录用量 → 返回响应
  │   ├→ 429/401 → 标记失败 → 切换下一家
  │   ├→ 5xx → 标记失败 → 切换下一家
  │   ├→ Timeout/ConnectionError → 标记失败 → 切换下一家
  │   └→ 全部失败 → 返回 error
  │
  └→ _track(name, tokens)
      ├→ 递增 total_requests
      ├→ 累加 total_tokens
      └→ 更新 last_used

server.py 收到响应后：
  ├→ 非流式：向 JSON body 注入 provider_name / provider_display 字段
  └→ 流式：  向 HTTP 响应头注入 X-Provider-Name / X-Provider-Display
```

### 3.3 Provider 信息透传

| 模式 | 方式 | 示例 |
|------|------|------|
| 非流式响应 | JSON body 字段 | `{"provider_name":"zhipu","provider_display":"智谱 GLM-4.7", ...}` |
| 流式响应 | HTTP 响应头 | `X-Provider-Name: zhipu`<br>`X-Provider-Display: %E6%99%BA%E8%B0%B1%20GLM-4.7`(URL-encoded) |

## 4. 配置格式

### chat.yaml / code.yaml

```yaml
gateway:
  port: 5080              # 服务端口
  retry_seconds: 15       # 失败后重试间隔(秒)
  timeout: 30             # 请求超时(秒)
  log: data/chat.log      # 日志文件路径

providers:
  - name: openrouter      # 唯一标识
    display: DeepSeek V4  # 展示名称
    endpoint: https://... # API 端点
    model: deepseek/...   # 模型名（v1.1 校验，忽略客户端传入）
    priority: 1           # 优先级（越小越优先）
    api_key: ${ENV_VAR}   # API Key（环境变量占位符，从 .env 加载）
    auth_type: bearer     # 认证方式：bearer / ak_sk（可选，默认 bearer）
    access_key_id: AKID   # AK/SK 模式必填
    secret_access_key: SK # AK/SK 模式必填
    headers:              # 自定义请求头（可选）
      HTTP-Referer: http://localhost:5080
```

**密钥管理**：API Key 通过 `.env` 文件管理环境变量，YAML 配置中使用 `${ENV_VAR}` 占位符，`gateway.py` 启动时自动加载替换。`.env` 已加入 `.gitignore`。

## 5. 认证机制

| 类型 | 说明 | 使用场景 |
|------|------|---------|
| Bearer Token | 标准 `Authorization: Bearer <key>` | 所有当前提供商 |
| AK/SK V4 | `_sign_v4()` 实现火山引擎 V4 签名算法 | 预留，当前未使用 |

认证方式由 provider 配置中的 `auth_type` 字段决定，默认为 `bearer`。

## 6. 进程管理

### 手动管理
```bash
# 启动
python3 src/server.py --config config/chat.yaml &   # 聊天实例 :5080
python3 src/server.py --config config/code.yaml &   # 编程实例 :5081
```

双实例独立 PID 可通过 `lsof -ti:5080` / `lsof -ti:5081` 获取

### 健康检查（双探针）

| 端点 | 用途 | 返回码 | 说明 |
|------|------|--------|------|
| `/health/live` | Liveness 探针 | 200 | 进程存活即返回，用于自动重启判定 |
| `/health/ready` | Readiness 探针 | 200/503 | 网关初始化且至少 1 个 provider 可用返回 200，否则 503 |
| `/health` | 向后兼容 | 200 | 等同 `/health/live`，保持旧客户端兼容 |

Readiness 响应体示例：
```json
{
  "status": "ready",
  "ready": true,
  "total_providers": 3,
  "available_providers": 2,
  "failed_providers": 1,
  "available_names": ["zhipu", "zhipu-fast"],
  "failed_names": ["openrouter"]
}
```

## 7. 关键变更记录

| 版本 | 变更 |
|------|------|
| v1.0 | 初始实现，单实例多提供商 failover |
| v1.1 | 修复模型路由 Bug：忽略客户端传入的 model，始终使用提供商自身配置的模型；加载配置时校验 model 字段 |
| v1.2 | 双实例架构（chat:5080 / code:5081）；server.py 增加 --config 参数；gateway.py 支持 config_path 参数化、按实例独立日志、AK/SK V4 签名；火山引擎多 endpoint 管理；新增 6 个启动/停止脚本；双实例 launchd plist；移除余额不足提供商 |
| v1.2a-v1.3 | 多轮提供商增删调整、模型替换 |
| v2.0-v2.1 | 新增 AI Agent 层后多次重构，最终移除 |
| v3.0 | **移除 Agent 层**，架构简化为 `client → gateway(:5080/5081)`；修复 `**kwargs` 参数透传（tools/tool_choice/stop 等不再被丢弃）；DeepSeek V4 Flash 验证可正确返回 tool calls |
| v3.1 | **Provider 精简**：chat 实例（智谱 GLM-4.7 P1 → GLM-4.5-Air P2 → OpenRouter P3），code 实例（DeepSeek V4 Flash P1 → 智谱 GLM-4.7 P2 → OpenRouter P3 → 火山 Doubao P4） |
| v3.2 | **Provider 信息透传**：响应中添加 `X-Provider-Name` HTTP 头和 `provider_name`/`provider_display` JSON 字段，客户端可识别当前路由到的实际 provider |
| v3.3 | **安全整改 + 质量修复**：API Key 移至环境变量(.env)；管理端点增加 Bearer Token 认证；移除 Agent 层(agent.py RCE 漏洞)；_StreamWrapper 递归改 while 循环；failed_providers/usage 增加 threading.Lock 线程保护；配置文件支持 `${ENV_VAR}` 占位符；gateway.py 增加 _load_dotenv() 自动加载 .env；回归测试新增 TC-09(认证拒绝)/TC-10(无明文Key) |
| v3.4 | **深度健康检查**：拆分 `/health` 为 `/health/live`(Liveness) + `/health/ready`(Readiness) 双探针；Readiness 反映 provider 可用性详情(总数/可用/失败/名称)；gateway.py 新增 `is_ready()` / `health_detail()` 方法；回归测试新增 TC-11(Liveness)/TC-12(Readiness) |

## 8. 配置操作入口

### opencode 配置
```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "free-api-hub-chat": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Free API Hub chat (聊天实例)",
      "options": {
        "baseURL": "http://127.0.0.1:5080/v1",
        "apiKey": "sk-placeholder"
      },
      "models": {
        "free-api-hub-chat": { "name": "free-api-hub-chat" }
      }
    },
    "free-api-hub-code": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Free API Hub Code (编程实例)",
      "options": {
        "baseURL": "http://127.0.0.1:5081/v1",
        "apiKey": "EMPTY"
      },
      "models": {
        "free-api-hub-code": { "name": "free-api-hub-code" }
      }
    }
  }
}
```

**注意：** 客户端传入的 `model` 参数会被忽略，每个提供商使用自身配置的模型。
**注意：** 流式响应可通过 `X-Provider-Name` / `X-Provider-Display` HTTP 头获取当前路由的 provider。
