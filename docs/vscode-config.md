# VSCode 模型路由配置手册

## 概述

将 VSCode 的 AI 扩展（Continue、Cline、GitHub Copilot 等）指向 Free API Hub 网关（localhost:5080）。

## 前提

Free API Hub 网关已在运行：
```bash
curl http://127.0.0.1:5080/health
# 返回: {"gateway_ready":true,"status":"ok"}
```

---

## 一、Continue 扩展配置

### 安装
在 VSCode 扩展商店搜索 "Continue" 并安装。

### 配置文件
编辑项目根目录 `.continuerc.json` 或全局配置 `~/.continue/config.json`:

```json
{
  "models": [
    {
      "title": "Free API Hub",
      "provider": "openai",
      "model": "free-api-hub",
      "apiBase": "http://127.0.0.1:5080/v1",
      "apiKey": "sk-placeholder",
      "completionOptions": {
        "temperature": 0.7,
        "maxTokens": 2048
      }
    }
  ],
  "tabAutocompleteModel": {
    "title": "Free API Hub (Tab)",
    "provider": "openai",
    "model": "free-api-hub",
    "apiBase": "http://127.0.0.1:5080/v1",
    "apiKey": "sk-placeholder"
  }
}
```

### 验证
在 Continue 输入框发送消息，观察输出。

---

## 二、Cline 扩展配置

### 安装
在 VSCode 扩展商店搜索 "Cline" 并安装。

### 配置文件
在 Cline 设置中添加 API 提供商:

1. 打开 Cline 设置（侧边栏 Cline 图标 → 齿轮）
2. API Provider: 选择 **OpenAI Compatible**
3. Base URL: `http://127.0.0.1:5080/v1`
4. API Key: `sk-placeholder`（任意非空字符串）
5. Model: `free-api-hub`（任意非空字符串）

### 验证
在 Cline 聊天窗口发送消息，观察回复。

---

## 三、GitHub Copilot 配置

GitHub Copilot 不支持自定义 API 端点，无法指向 Free API Hub。
如需在 VSCode 中使用自定义端点，推荐使用 Continue 或 Cline。

---

## 四、Roo Code 扩展配置

### 安装
在 VSCode 扩展商店搜索 "Roo Code" 并安装。

### 配置文件
在 Roo Code 设置中添加 API 提供商:

1. API Provider: **OpenAI Compatible**
2. Base URL: `http://127.0.0.1:5080/v1`
3. API Key: `sk-placeholder`
4. Model ID: `free-api-hub`

---

## 五、通用验证

### curl 测试
所有扩展配置完成后，通过 curl 验证网关可用：

```bash
# 1. 检查连接
curl http://127.0.0.1:5080/v1/models

# 2. 测试对话
curl -X POST http://127.0.0.1:5080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"test","messages":[{"role":"user","content":"hello"}]}'
```

### 查看网关日志确认请求到达
```bash
tail -f /Volumes/KINGSTON120G/free-api-hub/data/gateway.log
```

## 六、注意事项

1. **model 参数**：所有扩展传入的 model 值都会被 Free API Hub 忽略，每个提供商使用自身配置的模型
2. **API Key**：扩展要求 apiKey 不可为空。填入任意非空字符串即可
3. **流式支持**：Free API Hub 支持 SSE 流式，所有扩展的流式功能均可正常使用
4. **超时设置**：建议将扩展的超时设置为 60s 以上，以留出 failover 时间
5. **端口冲突**：确保 5080 端口未被其他程序占用
