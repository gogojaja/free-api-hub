# Free API Hub — 智能路由方案文档（design_smart_routing）

> **文档版本**：v1.0（方案评审稿）
> **日期**：2026-08-29
> **编制角色**：架构设计师（DevProjectTeamSkill 任务级加载：智能路由特性）
> **依据**：LiteLLM 官方路由文档（`docs.litellm.ai/docs/routing`、`/docs/proxy/load_balancing`，T2 vendor doc，2026-08-29 访问）；项目 `src/gateway.py` / `src/server.py` / `config/providers.yaml.example` 现状
> **状态**：方案评审（评审报告见 `reports/评审报告_智能路由方案_v1.0_逐原则.csv`）

---

## 1. 背景与现状

- 当前 `APIGateway.call_api`（`src/gateway.py:562`）按 `priority` 升序遍历 `providers`，对每个 provider 用其**自身配置的 `model`** 发起请求；客户端请求体里的 `model` 字段**不参与选路**（仅透传、无匹配逻辑）。
- 失败处理已成熟：`CircuitBreaker`（熔断/渐进恢复）、`RateLimiter`、重试退避、`_maybe_reload` 热加载均已实现（ADR-001~007）。
- `/v1/models`（`gateway.py:802`）直接返回各 provider 的裸下游模型名，客户端与具体 provider/模型强耦合。
- 痛点：①无法按请求语义（chat/code/轻量）选路；②无法按模型能力（context 窗口/工具调用）择优；③客户端绑定裸模型名，更换 provider 需改客户端；④无统一对外模型名片。

**结论**：智能路由是**叠加层**，不改动既有 failover/熔断/重试内核；默认策略 = 现状，保证零回归。

---

## 2. 方案目标

| 目标 | 说明 | 范围 |
|------|------|------|
| G1 统一模型别名层 | 对外暴露 `fah/chat-free` / `fah/code-x` / `fah/light` 等稳定别名，解耦客户端与下游模型 | MVP |
| G2 能力打分选路 | 按 tags + context 窗口 + 工具支持 + 延迟 在候选池内择优，替代纯 priority | MVP |
| G3 健康感知选路 | 复用 `CircuitBreaker` 可用性判定，熔断/半开 provider 自动出/入池 | MVP（复用既有） |
| G4 配置自动修正写回 | 网关生成路由建议、人工确认后写回 `config/*.yaml` | 设计留档，MVP 不实现（见 §7） |

---

## 3. 配置 Schema 扩展（`config/providers.yaml`）

在 `gateway` 下新增 `routing` 段；在 `providers[].capabilities` 新增能力元数据。**向后兼容**：不配置 `routing` 或 `routing.enabled=false` 时行为完全等同现状。

```yaml
gateway:
  retry_seconds: 60
  routing:
    enabled: true                 # 总开关；false = 完全走现状 priority-failover
    default_strategy: priority    # priority | capability | latency
    hide_raw: false               # true 时 /v1/models 仅展示别名
    aliases:
      - name: fah/chat-free       # 对外稳定模型名（客户端请求 model=此值）
        tags: [chat]              # 候选池 = 含 chat 标签的 provider
        strategy: capability      # 该别名选路策略（缺省用 default_strategy）
      - name: fah/code-x
        tags: [code]
        strategy: capability
      - name: fah/light
        tags: [lightweight]
        strategy: priority

providers:
  - name: zhipu
    display: 智谱AI
    endpoint: https://open.bigmodel.cn/api/paas/v4
    model: glm-4-flash
    priority: 1
    api_key: ${ZHIPU_API_KEY}
    capabilities:                 # 新增；缺省由路由层给保守默认值
      context_window: 128000
      output_limit: 4096
      supports_tools: false
      tags: [chat, lightweight]
```

`capabilities` 缺省值（未配置时）：`context_window=32768`、`output_limit=4096`、`supports_tools=false`、`tags=[]`（空 tags 的 provider 不进入任何按 tag 筛选的别名池，但可被 `priority` 默认策略兜底选中）。

---

## 4. 模块设计（新增 `src/routing.py`）

```
Router
  __init__(gateway_cfg, providers)      # 解析 routing 段 + provider capabilities
  resolve(model) -> (mode, candidates)  # mode: "alias" | "raw" | "default"
  select(candidates, strategy, ctx) -> provider
```

### 4.1 `resolve(model)`
- `model` 命中某 alias.name → `mode="alias"`，候选池 = providers 其 `tags` 与 alias.tags **有交集**（交集为空则退化为全池，避免空池）。
- `model` 为空 / 未知 / 命中某 provider 裸 `model` 字段（如 opencode 占位 `"free-api-hub"`）→ `mode="default"`，候选池 = **全部 providers（等同现状全池 failover，不钉死单 provider）**。
- 关键保护：opencode 传入的占位 `model` 被忽略、保持现状行为；`/v1/models` 在 `hide_raw=false`（默认）时继续返回裸下游模型名，opencode 现有模型列表不受影响。

### 4.2 `select(candidates, strategy, ctx)`
输入 `candidates` 已通过 breaker 可用性过滤（`get_available_providers` 交集）；按策略排序/打分：
- `priority`：按 `priority` 升序（=现状，确定性）。
- `capability`：
  1. **pre_call 检查**：预估 prompt token（`_estimate_tokens`）；过滤 `context_window < est*1.2` 的 provider；全被过滤则跳过此步（不破坏可用性）。
  2. 打分 `score = base + 能力加权`：
     - 请求带 `tools` 且 `supports_tools` → +100
     - `+ min(context_window, 200000) / 1000`（偏好大窗口）
     - `+ (1/priority) * 10`（轻微偏好高优先级）
  3. 取 `score` 最大者；并列按 `priority` 升序。
- `latency`：按每 provider 的 EWMA 延迟升序（需 §5 的小幅埋点；缺失样本时回退 `priority`）。

选路结果仅决定 `call_api` 外层遍历 `providers` 的**顺序与过滤**；既有 per-provider 重试/熔断/429/5xx 逻辑（`gateway.py:606` 起）**原样复用**，failover 自然生效。

### 4.3 配置校验（加载期）
- alias.tags 在所有 provider 中无交集 → 记 warning 并忽略该 alias（不中断启动）。
- `strategy` 非法值 → 回退 `priority` + warning。

---

## 5. 对既有代码的改动点（落地实施阶段，本方案文档不含实现）

| 文件 | 改动 | 风险 |
|------|------|------|
| `src/gateway.py` `_load_config` | 解析 `routing` 段与 `capabilities`；构建 `Router` | 低（纯解析+默认值） |
| `src/gateway.py` `call_api` | `providers = self._resolve_route(model)` 替换 `get_available_providers()`；`_resolve_route` = `router.resolve` ∩ breaker 可用集，再 `router.select` | 低（外层循环逻辑不变） |
| `src/gateway.py` `list_models` | alias 配置时前置别名条目；`hide_raw` 时仅别名 | 低 |
| `src/gateway.py` `_record_latency` | 增加每 provider EWMA（`self._provider_latency[name]`），支撑 `latency` 策略 | 低 |
| `src/server.py` | 无改动（`call_api`/`list_models` 接口不变） | 无 |

**回滚**：`routing.enabled=false` → `call_api` 退回 `get_available_providers()`（现状路径），爆炸半径限于解析阶段。

---

## 6. 测试计划（落地实施阶段）

| 用例 | 验证点 |
|------|--------|
| `tests/test_routing.py` 单测 | resolve 三种 mode；capability 过滤 context 超限；打分偏好 tools/大窗口；latency 排序；hide_raw；alias 校验降级 |
| `tests/test_regression.py` 扩展 | `/v1/models` 返回 alias；`call_api(model="fah/chat-free")` 命中 chat 标签 provider；未知 model 兜底现状；既有 41 用例全绿 |
| 实网冒烟 | 5080 实例分别用别名与裸模型名请求，确认选路符合预期 |

---

## 7. 不在 MVP 范围（设计留档）

**G4 配置自动修正写回**：`routing.auto_tune`（默认 `false`）。开启后网关仅**生成建议**并暴露 `GET /gateway/route-suggestion`（基于观察的健康/延迟重排 priority），写回 `config/*.yaml` 必须经运维人工确认 + 既有 `_snapshot_config` 备份（Tier3 人工门禁）。本方案 MVP 不实现，避免写入副作用；后续单独立项。

---

## 8. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 别名配置错误导致空池 | resolve 交集为空退化为全池；加载期校验 alias.tags 存在性 |
| 打分策略引入不确定选路、难排查 | capability/latency 均为确定性/可观测；响应头 `X-Provider-Name` 已透出实际 provider |
| 与现状行为偏差 | `default_strategy=priority` 且 `enabled=false` 全回退；pre_call 过滤失败不阻断 |
| 跨实例状态不一致 | 单实例内存态（与现状 breaker 一致）；Redis 共享不在范围 |

---

## 9. 来源与置信度

- LiteLLM `model_group_alias` / `model_name` 用户态别名 + `/v1/models` 展示：T2 vendor doc，置信度高（2026-08-29）。
- LiteLLM `routing_strategy`（simple-shuffle/least-busy/latency/cost）+ `model_info.context_window` + `enable_pre_call_checks`：T2 vendor doc，置信度高（2026-08-29）。
- 本方案 `capability` 打分公式为项目本地化适配（非引用固定实现），置信度中（需评审+实测）。
