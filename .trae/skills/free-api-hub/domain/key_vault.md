# key_vault — API key 别名管理

> 配套 MCP：`list_api_keys()` / `set_api_key(provider, key)`

## 存储约定（铁律 §3 A 级）
- key 明文只落文件：`~/.config/opencode/<provider>-api-key`（与全局 `opencode.jsonc` 的 `apiKey: "{file:...}"` 引用一致）。
- 写入后权限必须为 `0600`（`os.chmod(path, 0o600)`）。
- **任何 stdout / stderr / 工具返回体不得回显明文 key**，只返回 `{"ok": true}` 或存在性。

## 别名管理
- `list_api_keys()`：列出各 provider 的 key 文件「是否存在」与「别名」，不读明文。
- `set_api_key(provider, key)`：高危写操作，须用户确认；写后校验权限位。

## 审计
- 所有 key 写操作登记到 `.trae/skills/free-api-hub/audit/security_audit.csv`（时间/provider/操作/结果）。

## 边界
- 禁止把 key 提交到 Git、写入日志、回显到对话。
- 跨机迁移使用别名引用，不在仓库内保存明文。
