# 方案 v2：将 free-api-hub 升级为 opencode Skill + Agent + 本地 MCP

> 维护模式：技能维护模式（skill-authoring 六步）
> 维护范围：current_project（/Volumes/BR256G/free-api-hub）
> 单源事实：`.trae/skills/free-api-hub/`，部署到全局 `~/.config/opencode/skills/`
> 状态：v2 已通过多视角评审修正（CHANGES_REQUESTED 6 项已纳入）

## A. 安全修正（2 HIGH + 1 MED）
- **SEC-001 (HIGH)** `set_api_key`：写入后 `os.chmod(path, 0o600)`；server 任何 stdout/stderr/返回体**绝不回显明文 key**（只返回 `{"ok":true}`）。
- **SEC-002 (HIGH)** `add_provider`：入参强校验 —— `base_url` scheme ∈ {http,https}、`provider` 名匹配 `^[a-z0-9-]+$`；追加全局 `opencode.jsonc` 属外部高危操作，流程为 **确认 → 先备份全局 jsonc → 写后登记审计台账**。
- **SEC-003 (MED)** `health_check`：出站前 SSRF 防护，拒绝 `localhost/127.*/::1/169.254.*/10.*/192.168.*/172.16-31.*` 及非 http(s)，仅探测白名单内 provider。

## B. 架构解耦（ARCH-001）
- 移除对 `DevProjectTeamSkill` 的 `13_安全审计台账.csv` 与 `references/model_selection.md` 的硬编码依赖。
- 改为本项目自带轻量审计台账 `.trae/skills/free-api-hub/audit/security_audit.csv`（自适应路径，独立全局部署不断链）；模型档位建议改为 Skill/Agent 内联说明（S0~S3 简表），不跨技能引用。

## C. 代码健壮性（CODE-001）
- 零依赖 MCP 须完整实现握手：`initialize` 返回 `protocolVersion` + `capabilities.tools`，正确处理 `notifications/initialized`；`tools/call` 返回标准 `isError`/错误码；加最小自测 `python3 server.py --self-test`（打印 tools/list 结果）。

## D. 测试件（TEST-001）
- 新增 `mcp/tests/test_server.py`（stdlib `unittest`）：JSON-RPC 解析/序列化、`tool` 分发、key 文件 chmod、URL 校验、配置追加安全性；`health_check` 用本地 stub HTTP 桩（不联网）。

## 产物清单（≈12 文件）
```
.trae/skills/free-api-hub/
├── SKILL.md
├── domain/{api_catalog,key_vault,health_check,config_schema}.md
├── mcp/server.py            # 零依赖 stdio MCP + 握手 + 自测
├── mcp/tools_impl.py        # 工具实现（可被单测）
├── mcp/tests/test_server.py # 单测
└── audit/security_audit.csv # 本地审计台账
.opencode/agents/free-api-hub.md
opencode.jsonc               # 项目级：mcp.freeApiHub + skills.paths
```

## 落地步骤（B 分步，每步可回滚）
1. 写 Skill 源码（含内联模型档位表）
2. 写 `mcp/tools_impl.py` + `server.py`（含 SEC/CODE 修正）
3. 写 `mcp/tests/test_server.py` 并本地跑通
4. 写 Agent + 项目级 `opencode.jsonc`
5. 全局部署：copytree 到 `~/.config/opencode/skills/`+`agents/`；备份全局 `opencode.jsonc` 后追加 `mcp.freeApiHub`
6. 门禁：`check_skill_closure` + `check_version_consistency` + MCP `--self-test`

## 验收门禁
- 全局 `~/.config/opencode/skills/free-api-hub/SKILL.md` 存在且含「闭环执行系统」
- `check_skill_closure` 通过；`check_version_consistency` 通过
- `python3 server.py --self-test` 输出 tools 列表且无错
- `test_server.py` 全绿
- 全局配置追加后 opencode 加载 MCP 无协议错误
- Agent 在 `.opencode/agents/` 与全局均可见

## 风险与边界
- 本会话无网络：`health_check` 实际探测需在用户联网运行时验证（代码先就位 + 单测用 stub）
- 写全局 `opencode.jsonc` 属外部文件操作，须先备份 + 授权留痕
- 不引入 `mcp` SDK 依赖，保证离线可运行

---
**文档版本**：v2.0　**最后更新**：2026-08-27
