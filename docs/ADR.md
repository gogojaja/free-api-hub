# Free API Hub — 架构决策记录（ADR）

> **文档版本**：v1.0
> **创建日期**：2026-08-05
> **编制角色**：架构设计师（DevProjectTeamSkill M2 架构阶段）
> **范围**：CHG-REQ-001 需求审视变更项（去3改3增4 中涉及的 7 个架构相关变更）
> **约束**：端口 5080/5081（chat/code）；禁 .xlsx；成本 <20 人*日；方案均经社区实践验证

---

## ADR-001：DEGRADE-001 失败率熔断（修改）

**状态**：已接受（设计）| 实现归 M3

**背景**：现有 `CircuitBreaker` 为连续失败计数熔断（failure_threshold=3），429/5xx/超时同权累计，无法区分短期限流与长期故障，低流量下误熔断风险高。

**决策**：升级为 1min 滑动窗口失败率熔断。
- 失败率 > 25% 开路（OPEN），窗口内样本不足 10 次时不熔断（防冷启动误判）
- 429 短冷却（15s）/ 5xx 长冷却（60s）差异化处理
- 保留 CLOSED → OPEN → HALF_OPEN → CLOSED 三态状态机，仅触发条件升级

**备选方案**：
- A. 维持计数熔断 → 无法区分限流/故障，拒绝（低流量误判）
- B. resilience4j 全量方案（50%/100 调用窗口）→ 阈值偏高，本项目免费 provider 流量低，25%/10 样本更贴合，采纳为设计参考但参数本地化

**后果**：实现复杂度中等；熔断判定更准确，减少可用 provider 被误熔断；需新增滑动窗口数据结构与时间戳队列。

**依据**：resilience4j 官方文档（50% 阈值/100 调用/10 probes）、Martin Fowler 三态熔断、LiteLLM `allowed_fails`/`cooldown_time`、项目整改方案 D-S03 雏形。

---

## ADR-002：CONFIG-002 配置热加载（修改）

**状态**：已接受（设计）| 实现归 M3

**背景**：当前仅启动时一次性加载配置，修改 config 需重启进程；同时避免了"每请求重读 YAML"的磁盘 IO 反模式。

**决策**：config 文件 mtime 检测 + 5s TTL 缓存热加载。
- 每次调用前 `os.stat` 检测 mtime，变更且距上次加载 >5s 才重载
- 重载失败（YAML 损坏）→ 保留旧配置 + 日志告警，继续服务不中断
- 热加载替换 `self.config`/`self.providers`/`self.gateway_cfg`，保留熔断器状态（按 provider name 对应）

**备选方案**：
- A. 每请求重读 YAML → 磁盘 IO 反模式，拒绝（LiteLLM 生产实践明确避免）
- B. 显式 reload 管理端点 → 需人工触发，仅作辅助，不作为主路径

**后果**：实现复杂度低；运行期配置变更无需重启；需处理 provider 增删时的熔断器状态清理。

**依据**：LiteLLM 生产配置实践、评审报告架构评估 CONFIG-002。

---

## ADR-003：FAILOVER-003 渐进恢复（修改）

**状态**：已接受（设计）| 实现归 M3

**背景**：现有冷却到期后 provider 立即全量恢复，若服务仍未恢复会再次触发集中失败，造成抖动。

**决策**：冷却到期后渐进引入流量。
- 阶段 1：HALF_OPEN 仅以 20% 概率放行（低权重试探）
- 阶段 2：连续 2 次成功 → 转 CLOSED 全量恢复
- 阶段 3：试探期间失败 → 立即回 OPEN 重新冷却

**备选方案**：
- A. 冷却到期全量恢复 → 抖动风险，拒绝（现状）
- B. resilience4j 半开 10 probes → 本项目流量低，20%/2 次成功更匹配

**后果**：实现复杂度中等；恢复过程平滑；与 DEGRADE-001 差异化冷却衔接。

**依据**：LiteLLM cooldown 渐进 re-introduce、resilience4j half-open 探测。

---

## ADR-004：NEW-001 重试+指数退避（增加）

**状态**：已接受（设计）| 实现归 M3

**背景**：当前瞬时失败（429/超时/连接错误）直接 failover 到下一家，未利用重试解决瞬时抖动的机会，可能白白消耗备用 provider。

**决策**：瞬时失败重试当前 provider，最多 3 次。
- backoff 1s/2s/4s + jitter（full jitter，防惊群）
- 5xx/401 不重试直接 failover（无效重试 / 认证错误重试无意义）
- 上游 `Retry-After` 头存在时优先使用（与 NEW-003 衔接）

**备选方案**：
- A. 不重试（现状）→ 瞬时抖动直接消耗 failover，拒绝
- B. 全错误类型重试 → 5xx/401 重试浪费成本，拒绝（社区共识）

**后果**：实现复杂度低；单请求最长额外耗时 ~7s（1+2+4）+ jitter；需与 failover 语义协调（重试耗尽才切下一家）。

**依据**：LiteLLM `num_retries` + RateLimitError 指数退避；社区共识：429/500/529 重试、401/400 不重试。

---

## ADR-005：NEW-002 可观测性指标（增加）

**状态**：已接受（设计）| 实现归 M3

**背景**：仅 `get_status()` 提供内部快照，无标准化可观测端点，无法接入监控告警。

**决策**：新增 `GET /metrics`，Prometheus 文本格式（Content-Type: text/plain; version=0.0.4）。
- 指标：请求延迟直方图 / 错误数 / fallback 次数 / 请求数 / token 用量 / 熔断器状态
- 数据源复用现有 `usage` 记录 + 内存计数，避免双写不一致
- 认证策略：M3 定（公开只读 或 受管理端点认证保护）

**备选方案**：
- A. 扩展 `get_status()` JSON → 非 Prometheus 标准，无法直接接入 Grafana，拒绝
- B. 引入第三方 metrics 库（如 prometheus_client）→ 增加依赖，本项目数据量小，手写文本格式足够，暂不引入

**后果**：实现复杂度中等；无新增依赖；指标精度依赖 `_track`/`_mark_failed` 联动。

**依据**：Prometheus 客户端惯例、LiteLLM 观测性路线图、生产 LLM 网关基准。

---

## ADR-006：NEW-003 429 规范化（增加）

**状态**：已接受（设计）| 实现归 M3

**背景**：网关自身无限流；上游 429 直接透传，客户端无法实现标准退避。

**决策**：网关自身限流时返回规范化 429。
- 响应头：`Retry-After` + `X-RateLimit-Limit/Remaining/Reset`
- 响应体：`{"error": "rate_limit_exceeded", "retry_after": N}`
- 上游 429 转发时透传 `Retry-After`（与 NEW-001 重试衔接）

**备选方案**：
- A. 仅返回裸 429 → 客户端无法退避，拒绝
- B. 采用 IETF `RateLimit` 新标准头 → 尚未广泛采用，本项目兼容性优先用 X- 前缀惯例

**后果**：实现复杂度低；需定义网关自身限流策略（M3：按 IP / 总量滑动窗口）。

**依据**：APISIX `limit-count` 插件官方文档（429 + Retry-After + X-RateLimit-* 头）。

---

## ADR-007：NEW-004 配置快照（增加）

**状态**：已接受（设计）| 实现归 M3

**背景**：`config/*.yaml` 无版本化，配置修改损坏后无回滚能力。

**决策**：config 重载/修改前自动备份至 `backup/config_v<N>_<name>.yaml`。
- 备份触发点：热加载检测到变更并成功解析后；手动运维脚本触发
- 保留最近 10 份，损坏时从 backup 回滚
- 附 `scripts/backup_config.py` + `scripts/restore_config.py` 入口

**备选方案**：
- A. 不做版本化 → 无回滚能力，拒绝（现状）
- B. 引入 git 化配置管理 → 超出本项目范围，backup 目录方案更轻量

**后果**：实现复杂度低；与 CONFIG-002 联动（热加载成功即备份）；与现有 `backup/` 目录规范统一。

**依据**：项目 `backup/` 目录规范延伸、配置回滚运维实践。

---

## ADR 汇总表

| ADR | 变更项 | 决策要点 | 难度 |
|-----|--------|----------|------|
| ADR-001 | DEGRADE-001 | 1min 窗口失败率 >25% 开路，429/5xx 差异化冷却 | 中 |
| ADR-002 | CONFIG-002 | mtime 检测 + 5s TTL 缓存，损坏保留旧配置 | 低 |
| ADR-003 | FAILOVER-003 | 冷却到期 20% 试探，2 次成功全量恢复 | 中 |
| ADR-004 | NEW-001 | 瞬时失败重试 3 次，1s/2s/4s+jitter，5xx/401 不重试 | 低 |
| ADR-005 | NEW-002 | GET /metrics Prometheus 文本，复用 usage 记录 | 中 |
| ADR-006 | NEW-003 | 429 + Retry-After + X-RateLimit-* 规范化 | 低 |
| ADR-007 | NEW-004 | 重载前备份 backup/config_v<N>.yaml，保留 10 份 | 低 |

---

## ADR-008：智能路由 · 统一模型别名层（增加）

**状态**：已接受（设计）| 实现归智能路由特性

**背景**：`call_api` 当前忽略客户端 `model` 字段、按 priority 顺序尝试；`/v1/models` 暴露裸下游模型名，客户端与具体 provider/模型强耦合，更换 provider 需改客户端。

**决策**：新增叠加式别名层。
- `gateway.routing.aliases` 定义对外稳定别名（`fah/chat-free` 等）→ 标签筛选候选池。
- `resolve(model)`：命中 alias.name→按 tags 交集取候选池（交集为空退化为全池）；**空 model / 未知 model / 裸下游模型名（如 opencode 占位 `free-api-hub`）→ 一律退化为全池（=现状全池 failover，不钉死单 provider，避免削弱 opencode 的 failover）**。仅 alias 驱动新选路。
- `/v1/models` 前置别名条目；`hide_raw` 可仅展示别名。
- `routing.enabled=false` 完全回退现状。

**备选方案**：
- A. 直接改造 `call_api` 用客户端 model 选路 → 破坏现状「指定模型」语义与零回归保障，拒绝（采用叠加层）。
- B. 引入 LiteLLM 作为路由依赖 → 超出本项目轻量自托管定位、新增重依赖，拒绝（手写等效实现）。

**后果**：实现复杂度低；客户端与下游解耦；选路仅作用于候选池顺序/过滤，既有 failover/熔断/重试内核不变。

**依据**：LiteLLM `model_group_alias` + `model_name` 用户态别名、`/v1/models` 展示（docs.litellm.ai/docs/proxy/load_balancing，T2 vendor doc，2026-08-29）。

---

## ADR-009：智能路由 · 能力打分选路（增加）

**状态**：已接受（设计）| 实现归智能路由特性

**背景**：纯 priority 选路无法按请求语义/模型能力（context 窗口/工具调用/延迟）择优，低质 provider 可能与高质 provider 等权。

**决策**：每 provider 增加 `capabilities`（context_window/output_limit/supports_tools/tags）。
- 策略 `priority`（默认=现状）/ `capability` / `latency`。
- `capability`：pre_call 估算 prompt 过滤 context 超限者；打分（tools+100、大窗口加分、轻微优先级加权）取最优。
- `latency`：按每 provider EWMA 延迟升序（需 `_record_latency` 小幅埋点）。
- 候选池先经 `CircuitBreaker` 可用性过滤，复用既有 per-provider 重试/熔断逻辑。

**备选方案**：
- A. 实现 cost-based 策略 → 本项目全免费（cost=0）无意义，拒绝（不实现）。
- B. Redis 共享健康/延迟态 → 超出单实例轻量定位，拒绝（内存态，与现状一致）。

**后果**：实现复杂度中；选路可解释（响应头 `X-Provider-Name` 透出实际 provider）；`default_strategy=priority` 保证零回归。

**依据**：LiteLLM `routing_strategy`（latency/cost 等）、`model_info.context_window`、`enable_pre_call_checks`（docs.litellm.ai/docs/routing，T2 vendor doc，2026-08-29）。

---

**文档版本**：v1.0
**最后更新**：2026-08-29（追加 ADR-008/009 智能路由），ADR-010/011/012 模型中枢

---

## ADR-010：模型管理中枢 · manual_override 手动指定契约（增加）

**状态**：已接受（设计）| 实现归模型中枢一期

**背景**：双主模型（DeepSeek/Qwen）多平台候选，需先支持用户手动指定当前主用模型与平台，后治理再自动。

**决策**：新增 `routing.manual_override`（可填裸模型名或 alias 名）。仲裁优先级：**manual_override > 显式命中 alias > priority/capability 排序 > 兜底全池 failover**。
- 未命中/未知/裸下游模型名（如 opencode `free-api-hub`）→ 保持现状全池 failover。
- 一期仅实现 manual_override；`schedule` 时段调度归二期（叠加式，不破坏 ADR-008/009）。
- 客户端经 `manual_override` 指定后，路由层将候选池钉死于该 provider；`X-Provider-Name` 透出实际 provider。

**备选方案**：
- A. 直接改 call_api 指定模型 → 破坏现状 failover，拒绝（叠加层，与 ADR-008 一致）。
- B. 一期即上 schedule 时段调度 → 数据依赖夜间折扣核验（INSUFFICIENT），拒绝（放二期）。

**后果**：实现复杂度低；手动优先、自动兜底；与既有 failover/熔断/重试内核零冲突。

**依据**：ADR-008 选路内核对齐（docs/ADR.md，T1 本库，2026-08-29）。

---

## ADR-011：模型管理中枢 · 每周价格/优惠/免费额度监测（增加）

**状态**：已接受（设计）| 实现归模型中枢第一期

**背景**：夜间折扣（DeepSeek 官方）、短时优惠、免费额度刷新周期均随平台政策波动，需定期更新并留痕，支撑「及时调换模型」。

**决策**：`scripts/model_monitor.py` 每周一巡，三台账联动：
- `台账/24_模型价格.csv`（基准价）
- `台账/26_优惠日历.csv`（夜间/短时/免费额度优惠，**结束时间必填**）
- `台账/27_免费额度.csv`（额度量/刷新周期/刷新时间/当前状态）
产出周报 CSV（diff 上期→本期），含额度耗尽/将重置预警（ACTION=切换建议）；留痕入 `台账/13_安全审计台账.csv`（AUD 逐周）。
**铁律 #9**：监测任务预估超阈值则先报价三选一 + 登记 `台账/40_大模型成本台账.csv`。

**备选方案**：A. 人工每周巡检 → 不可审计、易漏，拒绝。B. 用第三方比价聚合（如 models.dev 订阅）→ 引入外部订阅成本，拒绝（自研直查官方源）。

**后果**：每周 ≤1K token；免费额度实测依赖平台 API（SiliconFlow 页面 JS 渲染不可静态抓）。

**依据**：方案文档 §4（docs/模型中枢方案.md，T1 本库，2026-08-29）。

---

## ADR-012：模型管理中枢 · 台账契约（24 价格 / 26 优惠 / 27 免费额度）（增加）

**状态**：已接受（设计）

**背景**：评审 CR 要求价格、优惠、免费额度三份数据分离且字段契约完整，支持手动选择与后续智能路由的输入。

**决策**：字段契约见 `docs/模型中枢方案.md` §5。登记路径 `台账/24_模型价格.csv`、`台账/26_优惠日历.csv`、`台账/27_免费额度.csv`，均 UTF-8 BOM；来源分级 T1 强制 + 核验日期必填。

**备选方案**：A. 合并单 CSV → 字段复用冲突、跨类数据混排，拒绝（分三表）。B. 用 xlsx → 全库禁 xlsx，拒绝（CSV）。

**后果**：三表单源、可 diff、可追溯。

**依据**：方案文档 §5（docs/模型中枢方案.md，T1 本库，2026-08-29）。
