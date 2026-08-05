# Free API Hub — 设计文档

## 1. 架构

### 1.1 三层架构

```
                         ┌──────────────────────────────────────┐
                         │              客户端                    │
                         │     (opencode / VSCode / curl)        │
                         └──────────────────┬───────────────────┘
                                            │
                                   agent    │
                                   :5090    │
                                            ▼
                         ┌──────────────────────────────────────┐
                         │        AI Agent 层  (agent.py)        │
                         │  System Prompt 注入 / 工具执行 / 反馈闭环 │
                         └──────────────────┬───────────────────┘
                                            │
                              ┌─────────────┴─────────────┐
                              │              │              │
                       chat   │              │  code        │
                       :5080  │              │  :5081       │
                              ▼              ▼              ▼
                ┌────────────────────┐  ┌────────────────────┐
                │   server.py        │  │   server.py        │
                │   chat.yaml        │  │   code.yaml        │
                └───────┬───────────┘  └───────┬────────────┘
                        │                      │
                        ▼                      ▼
                ┌────────────────────┐  ┌────────────────────┐
                │   APIGateway 网关   │  │   APIGateway 网关   │
                └───────┬───────────┘  └───────┬────────────┘
                        │                      │
                ┌──────────┬──────────┬──────────┬──────────┬──────────┐  ┌──────────┬──────────┬──────────┬──────────┬──────────┐
                ▼          ▼          ▼          ▼          ▼          ▼  ▼          ▼          ▼          ▼          ▼
               智谱AI    火山引擎   硅基 Chat  硅基 Reason 火山引擎 Lite OpenRouter         Qwen3-Coder Nemotron   Nemotron   火山引擎 硅基 Coder
               P1        P2        P3        P4        P5         P6         Llama 3.3-70B   P1(硅基)   Super P2    Ultra P3   Code P4  P5
```

### 1.2 多平台多 Endpoint 设计

```
火山引擎 Ark (同一 Key)         硅基流动 (同一 Key)         OpenRouter (同一 Key)
         │                             │                          │
  ┌──────┼──────┐               ┌──────┼──────┐           ┌──────┼──────┐
  ▼      ▼      ▼               ▼      ▼      ▼           ▼      ▼      ▼
  Code   Pro   Lite          DeepSeek Qwen   Qwen-Coder Qwen-Coder Nemotron Nemotron
  (编程) (推理) (轻量)         V3     Chat   32B        32B      Super    Ultra
```

### 1.3 AI Agent 层工作流

```
用户请求 → Agent v2.1
  │
  ├→ 1. 注入 System Prompt（强制写 ```python 代码块）
  │
  ├→ 2. 调用 Free API Hub code 实例 → 获取模型回复
  │
  ├→ 3. 检测回复中的三种可执行代码来源：
  │   ├→ XML <tool_call> 标签（旧格式兼容）
  │   ├→ OpenAI function calling（部分模型兼容）
  │   └→ ```python 代码块（自动提取执行，核心路径）
  │
  ├→ 4. 自动执行代码 → 获取 stdout/stderr
  │
  ├→ 5. 第二次迭代：模型根据执行结果输出最终答案
  │
  └→ 6. SSE 流式返回（先发初始 chunk 再处理，防超时）
```

## 2. 目录结构

```
free-api-hub/
├── src/
│   ├── agent.py           # AI Agent 层 (port 5090)：System Prompt/自动代码执行/SSE流式
│   ├── server.py          # Flask HTTP 服务入口，--config 参数选择配置
│   └── gateway.py         # 多 Provider 网关核心，支持 Bearer + AK/SK V4 认证
├── config/
│   ├── chat.yaml          # 聊天实例配置 (port 5080, 7 提供商)
│   ├── code.yaml          # 编程实例配置 (port 5081, 10 提供商)
│   └── providers.yaml     # 已废弃，保留兼容
├── data/
│   ├── chat.log           # 聊天实例运行日志
│   ├── code.log           # 编程实例运行日志
│   ├── chat_usage.json    # 聊天实例用量追踪
│   └── code_usage.json    # 编程实例用量追踪
├── scripts/
│   ├── start.sh           # 启动双实例
│   ├── stop.sh            # 停止双实例
│   ├── start-chat.sh      # 启动聊天实例
│   ├── stop-chat.sh       # 停止聊天实例
│   ├── start-code.sh      # 启动编程实例
│   ├── stop-code.sh       # 停止编程实例
│   ├── setup.sh           # 一键安装配置
│   ├── install_daemon.sh  # 安装/卸载双实例 launchd 守护进程
│   ├── check_env.py       # 环境标记核验
│   ├── com.user.freeapihub.chat.plist  # 聊天实例 launchd 配置
│   └── com.user.freeapihub.code.plist  # 编程实例 launchd 配置
├── tests/
│   └── test_regression.py # 回归测试（双实例适配）
├── docs/
│   ├── requirements.md    # 需求文档
│   └── design.md          # 设计文档
├── backup/                # 配置文件自动备份
├── requirements.txt       # Python 依赖
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
  │   ├→ 成功 → 记录用量 → 返回响应
  │   ├→ 429/401 → 标记失败 → 切换下一家
  │   ├→ 5xx → 标记失败 → 切换下一家
  │   ├→ Timeout/ConnectionError → 标记失败 → 切换下一家
  │   └→ 全部失败 → 返回 error
  │
  └→ _track(name, tokens)
      ├→ 递增 total_requests
      ├→ 累加 total_tokens
      └→ 更新 last_used
```

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
    api_key: sk-xxx       # API Key
    auth_type: bearer     # 认证方式：bearer / ak_sk（可选，默认 bearer）
    access_key_id: AKID   # AK/SK 模式必填
    secret_access_key: SK # AK/SK 模式必填
    headers:              # 自定义请求头（可选）
      HTTP-Referer: http://localhost:5080
```

## 5. 认证机制

| 类型 | 说明 | 使用场景 |
|------|------|---------|
| Bearer Token | 标准 `Authorization: Bearer <key>` | 所有当前提供商 |
| AK/SK V4 | `_sign_v4()` 实现火山引擎 V4 签名算法 | 预留，当前未使用 |

认证方式由 provider 配置中的 `auth_type` 字段决定，默认为 `bearer`。

## 6. 进程管理

### 手动管理
```bash
bash scripts/start.sh           # 启动全部实例（chat + code + agent）
bash scripts/stop.sh            # 停止全部实例
bash scripts/start-chat.sh      # 启动聊天实例
bash scripts/stop-chat.sh       # 停止聊天实例
bash scripts/start-code.sh      # 启动编程实例
bash scripts/stop-code.sh       # 停止编程实例
bash scripts/start-agent.sh     # 启动 AI Agent 层
bash scripts/stop-agent.sh      # 停止 AI Agent 层
```

### launchd 守护进程
```bash
bash scripts/install_daemon.sh # 安装/卸载双实例 plist
```

双实例独立 PID 文件：`data/chat.pid`、`data/code.pid`

## 7. 关键变更记录

| 版本 | 变更 |
|------|------|
| v1.0 | 初始实现，单实例多提供商 failover |
| v1.1 | 修复模型路由 Bug：忽略客户端传入的 model，始终使用提供商自身配置的 model；加载配置时校验 model 字段 |
| v1.2 | 双实例架构（chat:5080 / code:5081）；server.py 增加 --config 参数；gateway.py 支持 config_path 参数化、按实例独立日志、AK/SK V4 签名；火山引擎多 endpoint 管理；新增 6 个启动/停止脚本；双实例 launchd plist；移除余额不足提供商 |
| v1.2a | 新增 OpenRouter 免费模型（Nemotron 3 Ultra / North Mini Code / MiMo V2.5）；硅基流动多模型接入（Qwen-Chat / Qwen-Reason / Qwen-Coder）；聊天实例按对话/推理模型分层；备份目录标准化 |
| v1.2b | 修复 OpenRouter 模型失效：code 实例 P1 替换为 Qwen3-Coder，P3 新增 Nemotron 3 Super，P5 替换为 Qwen Next；chat 实例 P6 替换为 Llama 3.3-70B；移除下架的 DeepSeek V4 Flash / North Mini Code / MiMo |
| v1.2c | 移除 code 实例中编程能力弱的 Zhipu/GLM-4-Plus（原 P2）；进一步清理非编程模型：Qwen3-Next、Doubao-Pro、Doubao-Lite；code 实例精简为 6 个纯编程模型 |
| v1.2d | 移除硅基流动 DeepSeek-V3（体验差），code 实例精简为 5 个纯编程模型 |
| v1.3 | 整合提供商：code 实例 P1 从 OpenRouter 切到硅基 Qwen3-Coder-30B；chat 实例移除博查；OpenRouter 仅保留 Nemotron/Llama 两个硅基+火山不支持的模型 |
| v2.0 | 新增 AI Agent 层 (agent.py, port 5090)：工程化 System Prompt 注入、4 个工具执行器（代码/文件/命令）、执行反馈闭环、多轮迭代；opencode 默认指向 Agent；新增 start-agent/stop-agent 脚本 |
| v2.1 | Agent 重构：从"模型调工具"改为"自动提取 ```python 代码块执行"，不依赖模型工具调用能力；SSE 先发空 chunk 再处理防 opencode 超时重试；移除闲聊模式，纯编程定位；temperature 0 + 无代码块时自动重试保证一致性 |

## 8. 配置操作入口

### opencode 配置
```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "agent-code": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "AI Agent 编程助手",
      "options": {
        "baseURL": "http://127.0.0.1:5090/v1",  // 通过 Agent 层调用
        "apiKey": "EMPTY"
      },
      "models": {
        "agent-code-v1": {
          "name": "agent-code-v1"
        }
      }
    },
    "free-api-hub-code": {
      // 直接调用编程网关（跳过 Agent）
      "options": { "baseURL": "http://127.0.0.1:5081/v1" }
    },
    "free-api-hub-chat": {
      // 直接调用聊天网关
      "options": { "baseURL": "http://127.0.0.1:5080/v1" }
    }
  }
}
```

### VSCode 配置
```json
{
  "github.copilot.advanced": {},
  "llm.request": {
    "provider": "openai",
    "server": "http://127.0.0.1:5080/v1" // 或 :5081/v1
  }
}
```

**注意：** 客户端传入的 `model` 参数会被忽略，每个提供商使用自身配置的模型。
