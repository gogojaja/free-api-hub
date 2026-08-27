# config_schema — 聚合网关配置 schema

> 用于产出 free-api-hub 网关的路由/限流/降级配置片段。

## 聚合网关配置（示例 JSON）
```json
{
  "gateway": {
    "route_strategy": "round_robin | weighted | fallback",
    "providers": [
      {
        "name": "siliconflow",
        "base_url": "https://api.siliconflow.cn/v1",
        "models": ["Qwen/Qwen2.5-7B-Instruct"],
        "weight": 3,
        "key_ref": "siliconflow-api-key"
      }
    ],
    "rate_limit": { "rpm": 60, "concurrency": 4 },
    "fallback": {
      "on_error": ["429", "5xx"],
      "next_provider": "openrouter",
      "max_retries": 2
    }
  }
}
```

## 字段说明
- `route_strategy`：轮询 / 加权 / 降级优先。
- `key_ref`：引用 `~/.config/opencode/<provider>-api-key` 别名，不在配置中放明文。
- `fallback`：当命中错误码时切换到 `next_provider`，最多 `max_retries`。

## 约束
- 配置中**禁止出现明文 key**（只允许 `key_ref` 别名）。
- 新增 provider 须先经 `add_provider` 注册，再在此引用。
