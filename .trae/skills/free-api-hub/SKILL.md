---
name: free-api-hub
description: 免费API聚合网关的开发与运维技能：当用户要接入新的免费API（DeepSeek/SiliconFlow/Zhipu/Bailian/OpenRouter等）、配置聚合网关的路由/限流/降级fallback、检查接口健康、管理API key别名时加载。用户说「接入免费API/配置聚合网关/检查接口健康/管理API key」时加载。
---

# free-api-hub 技能

> 版权声明：本项目自有技能。本地 MCP 工具集与 Agent 配套使用。

## 目标与触发
- **目标**：把 `free-api-hub`（免费API聚合网关）的日常开发/接入/运维动作闭环化——接入新免费接口、配置聚合路由、探测接口健康、管理 key 别名，并尽量复用本地 MCP 工具。
- **触发**：用户说「接入免费API」「配置聚合网关」「检查接口健康」「管理API key」「列出免费提供方」时加载。

## 1. 核心能力
| 能力 | 说明 | 主用工具 |
|------|------|----------|
| 接入新免费API | 按模板登记 provider（baseURL/免费模型/key别名） | MCP `add_provider` |
| 聚合网关配置 | 路由/负载均衡/限流/降级 fallback schema | `domain/config_schema.md` |
| 接口健康管理 | 可达性/延迟探测、目录检索 | MCP `health_check` / `catalog_search` |
| key 别名管理 | 列出/写入提供方 key（脱敏存储） | MCP `list_api_keys` / `set_api_key` |

## 2. 本地 MCP 工具集（零依赖 stdio）
服务入口：`.trae/skills/free-api-hub/mcp/server.py`，注册于 `opencode.jsonc` 的 `mcp.freeApiHub`。
- `list_providers()` — 列出已配置免费提供方
- `get_provider_config(provider)` — 返回 baseURL/免费模型白名单（key 仅显存在性）
- `catalog_search(keyword)` — 从 `domain/api_catalog.md` 检索免费接口
- `health_check(provider, model?)` — 探测可达性/延迟（SSR 防护）
- `list_api_keys()` / `set_api_key(provider, key)` — key 别名管理（脱敏、0600）
- `add_provider(name, base_url, models, key_alias)` — 按模板登记（高危、强校验）

## 3. Agent 协作
配套 Agent `free-api-hub`（`.opencode/agents/free-api-hub.md`）：承接网关开发任务，调用上述 MCP 工具完成接入/路由/健康动作，按下方模型档位建议选档。

## 4. 模型档位建议（内联简表，S0~S3）
| 档位 | 适用动作 | 推荐模型档 |
|------|----------|-----------|
| S0 | 导航/查目录/列配置 | 本地轻量（qwen2.5:7b） |
| S1 | 写适配代码/单测/配置生成 | 低价档（glm-4.7-flash / deepseek-v4-flash-free） |
| S2 | 多 provider 路由策略/降级设计 | 稳定档（glm-5.2:free / qwen3.7-flash） |
| S3 | 安全审计/密钥策略重构 | 强模型，禁止降档 |

## 5. 铁律（安全与边界）
- **key 安全**：明文只落 `~/.config/opencode/<provider>-api-key`，权限 0600；任何日志/返回不回显明文（铁律 §3 A 级）。
- **SSRF 防护**：`health_check` 仅允许 http(s) 且禁止私网/回环/链路本地地址。
- **外部文件授权**：写全局 `opencode.jsonc` 必须先备份 + 用户确认 + 登记 `audit/security_audit.csv`。
- **双平台兼容**：路径用 `os.path`/`$HOME`，行尾 LF。

## 6. 目录结构
```
free-api-hub/
├── 台账/20_环境配置.csv        # 3 环境(dev/test/prod)单一真实源
├── environments/               # 双套环境组 nonprod(dev+test)/prod
├── .env.nonprod.example / .env.prod.example  # 密钥别名示例
├── .trae/skills/free-api-hub/
│   ├── SKILL.md
│   ├── domain/{api_catalog,key_vault,health_check,config_schema,environment}.md
│   ├── mcp/{server.py,tools_impl.py,tests/test_server.py}
│   └── audit/security_audit.csv
└── .opencode/agents/free-api-hub.md
```

## 7. 三套环境（dev / test / prod）
按 DevProjectTeamSkill 环境标准：双环境组拓扑（nonprod=dev+test 共用平台，prod 独立）。
- 配置单一真实源：`台账/20_环境配置.csv`（端口区间/共用边界/权限角色/密钥别名）。
- 密钥仅存别名，经 `.secrets/` 注入，禁入库（铁律 §3 A 级）。
- 详细见 `domain/environment.md`。

## 闭环执行系统

### 1. 任务入口
- 输入：用户要求接入免费API/配置聚合网关/检查接口健康/管理API key。
- 前置：已加载本技能；MCP `freeApiHub` 已注册并可用；目标 provider 已知或待检索。
- 不适用：与免费API网关无关的任务、或需联网但当前环境不可达的实时探测（先就位代码，联网后验证）。

### 2. 执行状态
| 状态 | 进入条件 | 退出条件 | 处理方式 |
|------|---------|---------|---------|
| 待启动 | 用户指令明确 | 用户确认/系统启动 | 读取 domain 明细与 MCP 工具列表 |
| 执行中 | 已调用 MCP 或编写适配 | 动作完成/失败 | 按能力表执行 |
| 校验中 | 关键动作完成 | 通过/失败 | 校验 key 权限/SSRF/配置合法性 |
| 阻塞 | 依赖缺失/需授权 | 补充信息/人工确认 | 暂停并登记阻塞原因 |
| 完成 | 验收通过 | 进入交接 | 记录证据与台账 |
| 回退 | 执行失败/门禁未过 | 回到稳定状态 | 触发回滚、保留审计 |

### 3. 执行动作层
- 接入新API：MCP `add_provider`（强校验）→ 写 key（`set_api_key`）→ 验证 `get_provider_config`。
- 健康管理：MCP `catalog_search` 检索 → `health_check` 探测 → 记录结果。
- 网关配置：参考 `domain/config_schema.md` 产出路由/限流/降级配置。
- 所需工具/脚本：`mcp/server.py`、`mcp/tools_impl.py`。
- 输入输出约束：key 明文不出现在任何 stdout/文件（除 0600 key 文件本身）。

### 4. 验收门禁
- 必须产出物：provider 登记记录 / 健康探测结果 / 配置片段（按需）。
- 通过条件：MCP 工具返回符合契约、key 文件权限 0600、配置通过 `add_provider` 校验。
- 失败条件：SSRF 拦截未生效、key 明文泄露、外部文件未授权写入。
- 审核对象：用户（高危操作）、本技能 Agent。

### 5. 失败处理
- 失败类型：provider 校验失败、key 写权限异常、网络不可达、配置注入拦截。
- 恢复策略：回滚 `add_provider` 追加、重建 key 文件权限。
- 回滚方案：从备份的全局 `opencode.jsonc` 恢复；删除多余 key 文件。
- 重试策略：仅在前置条件满足时重试，不绕过校验。
- 是否需要人工确认：写全局配置、写 key 均须人工确认。

### 6. 产出与交接
- 产出物列表：provider 配置、健康报告、网关配置片段、审计台账记录。
- 保存路径：`.trae/skills/free-api-hub/audit/security_audit.csv`、项目配置目录。
- 交接对象：后续维护者 / Agent。
- 下一步动作：配置并入网关代码、定时健康巡检。
- 归档条件：登记完成、审计齐全。

### 7. 审计记录
- 执行时间：每次高危操作时间。
- 关键参数：provider 名、操作类型、目标路径。
- 关键决策：授权确认、校验结果。
- 结果证据：`audit/security_audit.csv` 行。
- 失败原因：在审计台账留痕。

---

**文档版本**：v1.0.0　**最后更新**：2026-08-27
**知识产权所有**：free-api-hub 项目
