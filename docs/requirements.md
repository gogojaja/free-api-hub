# Free API Hub — 需求文档

## 1. 项目概述

统一多免费 API 提供商网关，对外暴露 OpenAI 兼容接口，自动 failover。提供双实例架构：
- **聊天实例** (`:5080`)：通用对话、轻量任务，对话/推理模型分层
- **编程实例** (`:5081`)：代码生成、编程辅助，按编程能力排序

## 2. 功能需求

| ID | 需求 | 优先级 | 状态 |
|----|------|--------|------|
| REQ-01 | 暴露 OpenAI 兼容的 `/v1/chat/completions` 接口 | P0 | ✅ |
| REQ-02 | 暴露 `/v1/models` 接口，列出可用模型 | P0 | ✅ |
| REQ-03 | 支持流式（SSE）和非流式响应 | P0 | ✅ |
| REQ-04 | 多 API 提供商自动 failover，请求按优先级依次尝试 | P0 | ✅ |
| REQ-05 | 提供商不可用时（429/401/5xx/超时）自动标记失败并切换 | P0 | ✅ |
| REQ-06 | 失败提供商 configurable 秒后自动恢复重试（默认 15s） | P0 | ✅ |
| REQ-07 | 提供商配置通过 YAML 文件管理，支持动态添加/修改 | P0 | ✅ |
| REQ-08 | 用量追踪（请求数、token 数、错误数、最后使用时间） | P1 | ✅ |
| REQ-09 | 网关状态查询 `/gateway/status` | P1 | ✅ |
| REQ-10 | 失败状态重置 `/gateway/reset` | P1 | ✅ |
| REQ-11 | 健康检查 `/health` | P1 | ✅ |
| REQ-12 | 模型路由隔离：每个提供商使用自身配置的模型，不受客户端传入 model 参数影响 | P0 | ✅ v1.1 |
| REQ-13 | 双实例部署：聊天和编程分离，独立端口/配置/日志/进程 | P0 | ✅ v1.2 |
| REQ-14 | 火山引擎单账号多 endpoint 管理：同一 API Key 绑定不同模型（Code/Pro/Lite） | P1 | ✅ v1.2 |
| REQ-15 | AK/SK V4 签名认证支持（为火山引擎保留，当前用 Bearer Token） | P2 | ✅ v1.2 |
| REQ-16 | 多 OpenRouter 免费模型接入（Nemotron/North Mini Code/MiMo） | P1 | ✅ v1.2 |
| REQ-17 | 同平台多模型多 endpoint：硅基流动单 Key 多模型、火山引擎多 endpoint | P1 | ✅ v1.2 |

## 3. 非功能需求

| 类别 | 需求 |
|------|------|
| 兼容 | 完全兼容 OpenAI Chat Completions API 格式 |
| 兼容 | 兼容 OpenAI 流式 SSE 格式 |
| 可靠 | 请求超时默认 30s |
| 可靠 | 多级错误处理：超时/连接/HTTP/异常，全部捕获 |
| 维护 | 配置文件热加载（每次请求重新读取） |
| 维护 | 日志记录每次请求的提供商、模型、耗时，按实例独立日志文件 |
| 维护 | 进程守护：支持 launchd 开机自启 + KeepAlive 自动拉活，双实例独立 plist |
| 安全 | 环境隔离：测试端口 5080/5081，禁止操作 5001/5002/5003 主环境端口 |
| 安全 | 文件修改前强制备份，双实例配置独立修改互不影响 |

## 4. 实例规格

### 聊天实例 (:5080)

| 优先级 | 提供商 | 模型 | 类型 | Key |
|--------|--------|------|------|-----|
| P1 | 智谱AI | `glm-4-flash` | 对话 | ✅ |
| P2 | 火山引擎 Doubao-Pro | `ep-20260613101926-l67rn` | 推理 | ✅ |
| P3 | 硅基流动 Qwen-Chat | `Qwen/Qwen2.5-7B-Instruct` | 对话 | ✅ |
| P4 | 硅基流动 Qwen-Reason | `Qwen/Qwen3-32B` | 推理 | ✅ |
| P5 | 火山引擎 Doubao-Lite | `ep-20260613102219-rxrhv` | 轻量 | ✅ |
| P6 | OpenRouter | `meta-llama/llama-3.3-70b-instruct:free` | 海外兜底 | ✅ |

### 编程实例 (:5081)

| 优先级 | 提供商 | 模型 | 特点 | Key |
|--------|--------|------|------|-----|
| P1 | 硅基流动 Qwen3-Coder | `Qwen/Qwen3-Coder-30B-A3B-Instruct` | 代码专用主力 | ✅ |
| P2 | NVIDIA Nemotron 3 Super Free | `nvidia/nemotron-3-super-120b-a12b:free` | 强推理+编程 | ✅ |
| P3 | NVIDIA Nemotron 3 Ultra Free | `nvidia/nemotron-3-ultra-550b-a55b:free` | 最强推理+编程 | ✅ |
| P4 | 火山引擎 Doubao-Code | `ep-20260613101407-6vz7g` | 代码专用 | ✅ |
| P5 | 硅基流动 Qwen-Coder | `Qwen/Qwen2.5-Coder-32B-Instruct` | 代码专用备选 | ✅ |

## 5. 接口规格

### POST /v1/chat/completions

请求体:
```json
{
  "model": "任意值（会被忽略，每个提供商使用自身配置的 model）",
  "messages": [{"role": "user", "content": "你好"}],
  "stream": false,
  "temperature": 0.7,
  "max_tokens": 2048
}
```

响应（非流式）:
```json
{
  "id": "chatcmpl-xxx",
  "object": "chat.completion",
  "choices": [{"message": {"role": "assistant", "content": "..."}}],
  "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
}
```

响应（流式）: SSE 格式，每行 `data: {...}\n\n`，结束 `data: [DONE]\n\n`

### GET /v1/models
各实例返回自身配置的提供商列表。

### GET /gateway/status
返回当前状态、可用提供商、失败列表、用量数据

### POST /gateway/reset
清空所有提供商的失败标记

### GET /health
```json
{"status": "ok", "gateway_ready": true}
```
