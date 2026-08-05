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
| v3.5 | **架构变更设计（CHG-REQ-001）**：7 变更项纳入架构资产（§9），含失败率熔断 DEGRADE-001、配置热加载 CONFIG-002、渐进恢复 FAILOVER-003、重试退避 NEW-001、可观测性指标 NEW-002、429 规范化 NEW-003、配置快照 NEW-004；仅设计落地，代码实现属 M3 |

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

## 9. 架构变更设计（CHG-REQ-001，v3.5 引入）

> 本节为需求审视变更项 CHG-REQ-001 的架构设计，7 项全部经社区实践验证（方案验证铁律），代码实现归 M3 开发阶段。

### 9.1 变更项总览

| 变更项 | 类型 | 设计模块 | 难度 | 设计状态 |
|--------|------|----------|------|----------|
| DEGRADE-001 | 修改 | 失败率熔断 | 中 | 已设计 |
| CONFIG-002 | 修改 | 配置热加载 | 低 | 已设计 |
| FAILOVER-003 | 修改 | 渐进恢复 | 中 | 已设计 |
| NEW-001 | 增加 | 重试+指数退避 | 低 | 已设计 |
| NEW-002 | 增加 | 可观测性指标 | 中 | 已设计 |
| NEW-003 | 增加 | 429 规范化 | 低 | 已设计 |
| NEW-004 | 增加 | 配置快照 | 低 | 已设计 |

### 9.2 DEGRADE-001 失败率熔断（修改）

**现状**：`CircuitBreaker`（gateway.py §4）为计数熔断——连续失败 `failure_threshold` 次（默认 3）即 OPEN，429/5xx/超时同权累计。

**目标设计**：升级为失败率滑动窗口熔断。

```
维护 60s 滑动窗口（deque，仅记录时间戳）
窗口内失败率 = 窗口内失败数 / 窗口内请求数
失败率 > 25% → OPEN（熔断，拒绝请求）
429 与 5xx 差异化 cooldown：429 短冷却（如 15s）/ 5xx 长冷却（如 60s）
窗口不足最小样本（如 10 次请求）时不熔断，避免冷启动误判
```

**设计要点**：
- `CircuitBreaker` 扩展：每 provider 维护 `deque[(ts, is_failure)]`，过期条目出队；OPEN 判定改失败率而非纯计数
- 保留既有状态机 CLOSED → OPEN → HALF_OPEN → CLOSED，仅触发条件升级
- 半开探测（HALF_OPEN）成功后 → CLOSED，失败 → OPEN（沿用现逻辑）

**依据**：resilience4j 失败率阈值（≥50%/100 调用窗口）+ 半开探测；Martin Fowler 三态熔断；LiteLLM `allowed_fails`/`cooldown_time`。

### 9.3 CONFIG-002 配置热加载（修改）

**现状**：`APIGateway.__init__` 一次性 `_load_config()`，每次启动读 YAML；无运行期重载。

**目标设计**：弃每次请求重读 YAML，改为 mtime 检测 + TTL 缓存。

```
保留启动加载路径（现有行为不变）
新增 _config_cache: {"mtime": float, "loaded_at": float, "config": dict}
每次调用前检测：config 文件 mtime 变化 且 距上次加载 > 5s → 重载
重载失败（YAML 损坏）→ 保留旧配置 + 日志告警 + 沿用旧配置继续服务
```

**设计要点**：
- 热加载仅替换 `self.config` / `self.providers` / `self.gateway_cfg` 及依赖参数（retry_seconds/timeout）
- 熔断器状态在热加载后保留（provider 仍按 name 对应），已移除的 provider 状态清理
- 每请求只做一次 `os.stat`（内存级），避免磁盘 IO

**依据**：LiteLLM 生产配置实践——避免每请求磁盘 IO，TTL 缓存 + 变更检测。

### 9.4 FAILOVER-003 渐进恢复（修改）

**现状**：`get_available_providers()` 中冷却到期即全量恢复可用（`_get_state` OPEN→HALF_OPEN 后立即放行）。

**目标设计**：冷却到期后渐进引入流量。

```
冷却到期（HALF_OPEN 后）：
  阶段 1：20% 流量试探（低权重引入，仅放行约 1/5 请求）
  阶段 2：连续 2 次成功 → 全量恢复（100% 权重）
  阶段 3：试探期间失败 → 立即回到 OPEN（重新冷却）
```

**设计要点**：
- `get_available_providers()` 增加权重判定：HALF_OPEN 状态 provider 仅以 20% 概率放行
- `record_success()` 记录 half_open 成功计数，达 2 次即转 CLOSED 全量恢复
- 保持与 DEGRADE-001 的衔接：429/5xx 差异化冷却时长在熔断模块内处理

**依据**：LiteLLM cooldown 渐进 re-introduce；resilience4j half-open 探针（10 probes 收敛）。

### 9.5 NEW-001 重试+指数退避（增加）

**现状**：`call_api()` 无重试逻辑，瞬时失败直接 `_mark_failed` 切下一家。

**目标设计**：瞬时失败（429/超时/连接错误）重试 2-3 次。

```
瞬时失败（429/Timeout/ConnectionError）→ 重试当前 provider，最多 3 次
backoff 序列：1s / 2s / 4s + jitter（±20% 随机抖动，防惊群）
5xx / 401 不重试，直接 failover（避免无效重试 + 认证错误重试无意义）
重试仍失败 → _mark_failed + 切换下一家
```

**设计要点**：
- 重试仅作用于同一 provider 的瞬时失败，不跨 provider 重复计数
- `retry_after` 头存在时优先使用（贴合 NEW-003 规范化输出）
- jitter 采用 full jitter（0 到 base 区间随机）避免惊群

**依据**：LiteLLM `num_retries` + RateLimitError 指数退避；OpenAI 社区实践——429/500/529 重试、401/400 不重试。

### 9.6 NEW-002 可观测性指标（增加）

**现状**：`get_status()` 提供内部状态，无 Prometheus 指标端点。

**目标设计**：新增 `GET /metrics` 输出 Prometheus 文本格式。

```
# 指标项（复用现有 usage 记录 + 熔断器状态）
free_api_hub_request_duration_seconds_histogram  请求延迟直方图
free_api_hub_errors_total{provider}              错误计数
free_api_hub_fallback_total                      失败切换次数
free_api_hub_requests_total{provider}            请求计数
free_api_hub_tokens_total{provider}              用量累计
free_api_hub_circuit_state{provider,state}       熔断器状态

# 端点特性
- 文本格式（Content-Type: text/plain; version=0.0.4）
- 只读、无认证（与 /v1/models 同级公开）或受管理端点认证保护（二选一，M3 定）
- 数据源：内存计数 + usage.json 持久化
```

**设计要点**：
- 在 `server.py` 增加 `/metrics` 路由，`gateway.py` 增加 `get_metrics_text()` 方法
- 延迟直方图在 `call_api()` 调用周期内计时（time.monotonic）
- 指标计数与 `_track`/`_mark_failed` 联动，避免双写不一致

**依据**：Prometheus 客户端惯例；LiteLLM 观测性路线图；生产 LLM 网关基准。

### 9.7 NEW-003 429 规范化（增加）

**现状**：`server.py` 无网关自身限流，上游 429 透传给客户端。

**目标设计**：网关自身限流时返回规范化 429。

```
HTTP/1.1 429 Too Many Requests
Retry-After: 60
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 0
X-RateLimit-Reset: <window 重置时间戳>

响应体：{"error": "rate_limit_exceeded", "message": "...", "retry_after": 60}
```

**设计要点**：
- 网关自身限流策略（M3 定：按 IP / 按总量滑动窗口）
- 响应头三件套 + Retry-After，客户端可据此实现退避
- 上游 429 转发时同步透传 Retry-After（NEW-001 重试衔接）

**依据**：APISIX `limit-count` 插件规范（429 + Retry-After + X-RateLimit-* 头）。

### 9.8 NEW-004 配置快照（增加）

**现状**：`config/*.yaml` 无版本化，直接修改无回滚能力。

**目标设计**：config 重载/修改前自动备份。

```
备份触发点：
  1) 热加载（CONFIG-002）检测到变更并成功解析后
  2) 手动编辑配置后由运维脚本触发
备份格式：backup/config_v<N>_<name>.yaml（N 递增，保留最近 10 份）
损坏回滚：检测到 YAML 解析失败 → 保留旧配置并提示从 backup 恢复
```

**设计要点**：
- 备份在 `gateway.py` 热加载路径内自动执行（CONFIG-002 联动）
- 附 `scripts/backup_config.py` 手动快照入口 + `scripts/restore_config.py` 回滚入口
- 与现有 `backup/` 目录规范统一（台账备份同址）

**依据**：项目 `backup/` 目录规范延伸；配置回滚运维实践。

### 9.9 变更项依赖关系

```
DEGRADE-001 (失败率熔断) ──► 依赖：现有 CircuitBreaker 状态机
FAILOVER-003 (渐进恢复) ──► 依赖：DEGRADE-001 冷却时长差异化
NEW-001 (重试退避)      ──► 依赖：429 语义（NEW-003 retry_after 衔接）
NEW-004 (配置快照)      ──► 依赖：CONFIG-002 热加载触发备份
CONFIG-002 / NEW-002 / NEW-003：相对独立，可并行实现
```

实现顺序建议（M3）：NEW-001 → NEW-003 → NEW-004 → CONFIG-002 → DEGRADE-001 → FAILOVER-003 → NEW-002。
