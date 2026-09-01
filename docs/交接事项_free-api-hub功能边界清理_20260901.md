# 交接事项：free-api-hub 功能边界清理

> **来源**：DevProjectTeamSkill 评审报告（2026-09-01）
> **优先级**：高
> **要求**：free-api-hub 自行处理

---

## 一、背景

free-api-hub 已重新定位为**模型清单管理基础设施**，网关代码（`src/gateway.py`、`src/routing.py`、`src/server.py`）已删除。但配置文件中仍保留路由相关配置，与铁律冲突。

---

## 二、铁律（不可违反）

**项目定位（铁律之铁律）**：本项目是整个项目群的基础设施，以稳定为第一要求。职责：维护可用模型清单、提供商余额/费用/优惠信息、保证项目群使用最低价最适合的模型。**禁止**：模型路由、请求聚合、流量转发。只做最基础的模型列表可用性服务。

---

## 三、需清理项

| 序号 | 配置项 | 当前状态 | 操作 | 理由 |
|------|--------|----------|------|------|
| 1 | `gateway.routing.enabled` | `true` | 删除或设为 `false` | 禁止模型路由 |
| 2 | `gateway.routing.default_strategy` | `cost` | 删除 | 禁止模型路由 |
| 3 | `gateway.routing.hide_raw` | `false` | 删除 | 禁止模型路由 |
| 4 | `gateway.routing.manual_override` | `''` | 删除 | 禁止模型路由 |
| 5 | `gateway.routing.aliases` | 4 个别名 | 删除整个段 | 禁止模型路由 |
| 6 | `gateway.routing.schedule_enabled` | `false` | 删除 | 禁止流量转发 |
| 7 | `gateway.routing.schedule` | 调度配置 | 删除整个段 | 禁止流量转发 |

---

## 四、保留项

| 序号 | 配置项 | 保留理由 |
|------|--------|----------|
| 1 | `providers[].cost_per_mtok` | 价格监控需要 |
| 2 | `providers[].capabilities` | 模型信息需要 |
| 3 | `providers[].tags` | 模型分类需要（free/cheap/paid-as-you-go） |
| 4 | `providers[].priority` | 清单排序需要 |
| 5 | `providers[].context_window` | 模型能力信息 |
| 6 | `providers[].output_limit` | 模型能力信息 |

---

## 五、清理后配置示例

```yaml
gateway:
  port: 5080
  retry_seconds: 15
  failure_threshold: 3
  timeout: 30
  log: data/chat.log

providers:
  - name: zhipu
    display: 智谱 GLM-4.5-Flash (免费)
    endpoint: https://open.bigmodel.cn/api/paas/v4
    model: glm-4.5-flash
    priority: 1
    api_key: ${ZHIPU_API_KEY}
    cost_per_mtok: 0
    capabilities:
      context_window: 128000
      output_limit: 4096
      supports_tools: true
      tags:
        - free
        - chat
```

---

## 六、验证

清理完成后，运行以下验证：

1. **配置语法检查**：`python -c "import yaml; yaml.safe_load(open('config/providers.yaml'))"`
2. **铁律合规检查**：确认无 `routing`、`aliases`、`schedule` 配置项
3. **清单功能测试**：确认 `providers` 列表正常加载

---

## 七、交付物

| 交付物 | 说明 |
|--------|------|
| 更新后的 `config/providers.yaml` | 清理路由配置 |
| 更新后的 `交接文档.md` | 记录清理完成 |
| 更新后的 `AGENTS.md` | 确认铁律执行 |

---

**交接时间**：2026-09-01
**来源**：DevProjectTeamSkill 评审报告
