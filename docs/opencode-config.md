# opencode 模型路由配置手册

## 概述

将 opencode 的 AI 请求指向 Free API Hub 网关（localhost:5080），由网关自动 failover 多个免费 API 提供商。

## 前提

Free API Hub 网关已在运行：
```bash
curl http://127.0.0.1:5080/health
# 返回: {"gateway_ready":true,"status":"ok"}
```

## 配置位置

opencode 读取配置的优先级（高→低）：
1. 命令行 `--config` 参数指定文件
2. 项目根目录 `opencode.json`
3. 项目根目录 `opencode.jsonc`
4. `~/.config/opencode/opencode.jsonc`

## 配置内容

### 方式一：项目级配置（推荐）

在项目根目录创建 `opencode.json`:

```json
{
  "provider": "openai",
  "apiBase": "http://127.0.0.1:5080/v1",
  "model": "free-api-hub",
  "apiKey": "sk-placeholder"
}
```

### 方式二：全局配置

编辑 `~/.config/opencode/opencode.jsonc`:

```jsonc
{
  "provider": "openai",
  "apiBase": "http://127.0.0.1:5080/v1",
  "model": "free-api-hub",
  "apiKey": "sk-placeholder"
}
```

## 参数说明

| 参数 | 值 | 说明 |
|------|-----|------|
| `provider` | `"openai"` | 使用 OpenAI 兼容协议 |
| `apiBase` | `"http://127.0.0.1:5080/v1"` | 指向 Free API Hub 网关 |
| `model` | `"free-api-hub"` | **会被网关忽略**，网关使用提供商自身配置的模型 |
| `apiKey` | `"sk-placeholder"` | **会被网关忽略**，网关使用 providers.yaml 中的 API Key |

## 验证配置

```bash
# 1. 确认网关在运行
curl http://127.0.0.1:5080/health

# 2. 检查可用提供商
curl http://127.0.0.1:5080/gateway/status | python3 -m json.tool

# 3. 在 opencode 中发送消息测试
opencode "Hello"
```

## 调试

如果 opencode 报错，按以下顺序排查：

```bash
# 1. 网关是否运行
curl http://127.0.0.1:5080/health

# 2. 提供商是否可用
curl http://127.0.0.1:5080/gateway/status

# 3. 直接测试对话接口
curl -X POST http://127.0.0.1:5080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"test","messages":[{"role":"user","content":"hi"}]}'

# 4. 查看网关日志
tail -30 /Volumes/KINGSTON120G/free-api-hub/data/gateway.log

# 5. 重置所有提供商的失败标记
curl -X POST http://127.0.0.1:5080/gateway/reset
```

## 常见问题

**Q: opencode 报 "401 Authentication Fails"**
A: `apiKey` 字段不可为空。填入任意字符串如 `"sk-placeholder"`，网关实际使用 providers.yaml 中的 Key。

**Q: opencode 报 "model not found"**
A: opencode 可能校验 model 值。填入任意非空字符串即可，网关会忽略它。

**Q: 所有提供商都返回 503**
A: 所有 API 配额耗尽。更新 providers.yaml 中的 api_key，或通过 `/gateway/reset` 重试。

**Q: 某提供商一直失败**
A: 网关自动标记失败 60s。可通过 `/gateway/reset` 手动重置，或调整优先级让其他提供商优先。
