# api_catalog — 免费 API 目录

> 结构化检索源：`catalog_search(keyword)` 从本文件解析表格。

## 已收录免费提供方
| 提供方 | baseURL | 免费模型（示例） | key 文件 |
|--------|---------|----------------|----------|
| deepseek | https://api.deepseek.com/v1 | deepseek-chat（free 档见白名单） | ~/.config/opencode/deepseek-api-key |
| siliconflow | https://api.siliconflow.cn/v1 | Qwen/Qwen2.5-7B-Instruct、deepseek-ai/DeepSeek-V4-Flash | siliconflow-api-key |
| zhipu | https://open.bigmodel.cn/api/paas/v4 | glm-4.7-flash | zhipu-api-key |
| bailian | https://dashscope.aliyuncs.com/compatible-mode/v1 | qwen3.7-flash | bailian-api-key |
| openrouter | https://openrouter.ai/api/v1 | z-ai/glm-5.2:free | openrouter-api-key |

## 检索约定
- `catalog_search(keyword)` 按 提供方/模型名 模糊匹配上表。
- 新增提供方须经 `add_provider`（强校验）写入全局 `opencode.jsonc` 的 `provider` 段，并同步更新本目录。

## 边界
- 模型白名单以各 provider 官方实时为准；本目录为静态快照，接入前建议 `health_check` 复核。
