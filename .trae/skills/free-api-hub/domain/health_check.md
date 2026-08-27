# health_check — 免费接口健康探测

> 配套 MCP：`health_check(provider, model?)`

## 探测流程
1. 解析 provider 的 `baseURL`（取自全局 `opencode.jsonc` 或 `api_catalog.md`）。
2. **SSRF 预检**（SEC-003）：仅允许 `http`/`https`；禁止目标主机为 `localhost`、`127.*`、`::1`、`169.254.*`、`10.*`、`192.168.*`、`172.16-31.*`。
3. 发起轻量请求（如 `/models` 或一次最小 chat completion），记录 HTTP 状态与耗时。
4. 返回 `{provider, reachable, latency_ms, status, error?}`；不返回任何 key。

## 超时与并发
- 单次探测默认超时 10s；多 provider 可并发（实现层控制最大并发 ≤ 5）。
- 失败区分：网络不可达 / 401（key 问题） / 429（限流） / 5xx。

## 边界
- 本环境若无网络，`health_check` 实际出站会失败；代码先就位，联网环境再验证。
- 探测绝不使用、绝不回显 key。
