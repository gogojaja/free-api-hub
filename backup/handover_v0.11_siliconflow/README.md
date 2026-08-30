# Free API Hub（PRJ-001）

统一多免费 API 提供商网关，对外暴露 OpenAI 兼容接口，自动 failover。全生命周期由 DevProjectTeamSkill v21 治理（M0~M6 已完成，项目结项）。

## 快速开始

```bash
# 1. 启动网关（chat 实例）
nohup venv/bin/python src/server.py --config config/chat.yaml > data/chat_server.log 2>&1 &

# 2. 健康检查
curl http://127.0.0.1:5080/health
# {"gateway_ready":true,"status":"ok"}
```

## 端口

| 实例 | 端口 | 用途 |
|------|------|------|
| chat | 5080 | 通用对话 / 轻量任务 |
| code | 5081 | 代码生成 / 编程辅助 |

> 禁止操作主环境端口 5001/5002/5003。

## 核心能力

- OpenAI 兼容 `/v1/chat/completions` + `/v1/models`（含 SSE 流式）
- 多提供商自动 failover + 失败率熔断 + 渐进恢复
- 重试 + 指数退避、429 规范化、配置热加载、配置快照
- 可观测性指标 `GET /metrics`（Prometheus 文本）
- 用量追踪 + 网关状态/重置端点

## 文档索引

| 文档 | 说明 |
|------|------|
| `交接文档.md` | **工作断点 / 会话交接（唯一事实来源，先读此文）** |
| `AGENTS.md` | AI 助理工作规则 |
| `docs/requirements.md` | 需求文档（17 项，全 ✅） |
| `docs/design.md` | 设计文档（v3.8，架构 + CHG-REQ-001 七变更项 + ADR） |
| `docs/ADR.md` | 架构决策记录（ADR-001~007） |
| `docs/运维手册.md` | 运维手册（启动/停止/健康/指标/故障恢复） |
| `docs/客户端配置.md` | AI 客户端（opencode/VSCode）模型路由配置 |
| `docs/整改方案_严重缺陷_v1.0.md` | 严重缺陷整改方案（D-S01~08，全整改） |
| `docs/README.md` | docs 目录索引 |
| `台账/` | 主台账 17 CSV（基线 v0.8，项目结项） |
| `requirements/` | 需求工作目录（17 CSV：SRS 10 章 + 评审 + 追溯） |
| `reports/` | 评审报告（需求/审视/架构/投产）+ 测试汇总 |
| `backup/` | 里程碑快照与配置备份（禁删） |

## 目录结构

```
├── src/                  # 网关实现（server.py + gateway.py）
├── tests/                # 8 套件 41 用例 + run_all_tests.py 统一调度
├── config/               # 实例配置（providers.yaml 不入库）
├── scripts/              # 辅助脚本（备份/守护等）
├── docs/                 # 项目文档
├── 台账/                 # 全生命周期台账
├── requirements/         # 需求工作目录
├── reports/              # 评审报告与测试汇总
├── data/                 # 运行时数据（usage/log，不入库）
└── backup/               # 备份快照
```

## 许可证

知识产权所有：段波。
