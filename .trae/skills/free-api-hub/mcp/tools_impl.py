#!/usr/bin/env python3
"""free-api-hub MCP 工具实现（零依赖，纯标准库）。

所有工具返回 dict；敏感信息（key 明文）绝不出现在返回值中。
"""

import os
import re
import csv
import json
import shutil
import ipaddress
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

# ─── 路径解析 ───────────────────────────────────────────────
def _opencode_dir() -> Path:
    env = os.environ.get("OPENCE_HOME")
    if env:
        return Path(env)
    return Path(os.path.expanduser("~")) / ".config" / "opencode"


def _config_path() -> Path:
    env = os.environ.get("OPENCE_CONFIG")
    if env:
        return Path(env)
    return _opencode_dir() / "opencode.jsonc"


def _skill_dir() -> Path:
    # mcp/tools_impl.py -> .trae/skills/free-api-hub
    return Path(__file__).resolve().parent.parent


def _audit_path() -> Path:
    return _skill_dir() / "audit" / "security_audit.csv"


def _catalog_path() -> Path:
    return _skill_dir() / "domain" / "api_catalog.md"


# ─── jsonc 安全解析（引号内不剥注释） ──────────────────────
def _strip_jsonc(text: str) -> str:
    out = []
    i, n = 0, len(text)
    in_str = False
    esc = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
                out.append(c)
            elif c == "/" and i + 1 < n and text[i + 1] == "/":
                while i < n and text[i] != "\n":
                    i += 1
                continue
            elif c == "/" and i + 1 < n and text[i + 1] == "*":
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i += 2
                continue
            else:
                out.append(c)
        i += 1
    return "".join(out)


def load_config() -> dict:
    p = _config_path()
    if not p.exists():
        return {}
    try:
        return json.loads(_strip_jsonc(p.read_text(encoding="utf-8")))
    except Exception as e:
        raise RuntimeError(f"解析配置失败 {p}: {e}")


def save_config(cfg: dict) -> None:
    p = _config_path()
    p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ─── 审计台账（本地、自包含，ARCH-001） ───────────────────
def audit(action: str, provider: str, result: str, detail: str = "") -> None:
    path = _audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["timestamp", "action", "provider", "result", "detail"])
        w.writerow([ts, action, provider, result, detail])


# ─── SSRF / 输入校验（SEC-002 / SEC-003） ──────────────────
_BLOCK_HOST_PATTERNS = (
    r"^localhost$",
    r"^127\.",
    r"^::1$",
    r"^169\.254\.",
    r"^10\.",
    r"^192\.168\.",
    r"^172\.(1[6-9]|2[0-9]|3[0-1])\.",
)
_PROVIDER_RE = re.compile(r"^[a-z0-9-]+$")


def is_safe_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    if not (url.startswith("http://") or url.startswith("https://")):
        return False
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    if not host:
        return False
    for pat in _BLOCK_HOST_PATTERNS:
        if re.match(pat, host):
            return False
    # IP 字面量私网段拦截
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    except ValueError:
        pass  # 域名：运行时 DNS 解析由调用方环境承担，此处不联网
    return True


def validate_provider_name(name: str) -> bool:
    return bool(_PROVIDER_RE.match(name or ""))


# ─── 工具实现 ──────────────────────────────────────────────
def list_providers() -> dict:
    cfg = load_config()
    providers = cfg.get("provider", {}) or {}
    rows = []
    for name, spec in providers.items():
        opts = spec.get("options", {}) or {}
        rows.append({
            "name": name,
            "base_url": opts.get("baseURL", ""),
            "models": list((spec.get("models", {}) or {}).keys()),
        })
    return {"ok": True, "count": len(rows), "providers": rows}


def get_provider_config(provider: str) -> dict:
    cfg = load_config()
    spec = (cfg.get("provider", {}) or {}).get(provider)
    if not spec:
        return {"ok": False, "error": f"未配置 provider: {provider}"}
    opts = spec.get("options", {}) or {}
    key_ref = opts.get("apiKey", "")
    key_file = None
    m = re.search(r"\{file:(.*?)\}", key_ref or "")
    if m:
        key_file = m.group(1)
    key_exists = bool(key_file) and Path(key_file).expanduser().exists()
    return {
        "ok": True,
        "name": provider,
        "base_url": opts.get("baseURL", ""),
        "models": list((spec.get("models", {}) or {}).keys()),
        "key_file": key_file,
        "key_exists": key_exists,
    }


def catalog_search(keyword: str = "") -> dict:
    p = _catalog_path()
    if not p.exists():
        return {"ok": False, "error": "api_catalog.md 不存在"}
    text = p.read_text(encoding="utf-8")
    rows = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        if cells[0] in ("提供方", ""):  # 表头/分隔行
            continue
        provider, base_url, models, key_file = cells[0], cells[1], cells[2], cells[3]
        if not keyword or keyword.lower() in (provider + " " + models).lower():
            rows.append({"provider": provider, "base_url": base_url, "models": models, "key_file": key_file})
    return {"ok": True, "count": len(rows), "results": rows}


def health_check(provider: str, model: str = "") -> dict:
    cfg = get_provider_config(provider)
    if not cfg.get("ok"):
        return cfg
    base_url = cfg["base_url"]
    if not is_safe_url(base_url):
        return {"ok": False, "error": f"SSRF 防护：拒绝探测目标 {base_url}", "blocked": True}
    # 无网络环境直接返回不可达，避免长阻塞
    try:
        req = urllib.request.Request(base_url, method="GET", headers={"User-Agent": "free-api-hub-mcp/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
        return {"ok": True, "provider": provider, "reachable": True, "status": status, "latency_ms": None}
    except urllib.error.HTTPError as e:
        return {"ok": True, "provider": provider, "reachable": True, "status": e.code, "latency_ms": None}
    except Exception as e:
        return {"ok": True, "provider": provider, "reachable": False, "error": str(e)[:200]}


def list_api_keys() -> dict:
    cfg = load_config()
    providers = list((cfg.get("provider", {}) or {}).keys())
    rows = []
    for name in providers:
        spec = cfg["provider"][name]
        opts = spec.get("options", {}) or {}
        m = re.search(r"\{file:(.*?)\}", opts.get("apiKey", "") or "")
        key_file = Path(m.group(1)).expanduser() if m else None
        rows.append({"provider": name, "key_exists": bool(key_file and key_file.exists())})
    return {"ok": True, "keys": rows}


def set_api_key(provider: str, key: str) -> dict:
    if not validate_provider_name(provider):
        return {"ok": False, "error": "provider 名非法（仅允许 [a-z0-9-]）"}
    if not key or not isinstance(key, str):
        return {"ok": False, "error": "key 为空"}
    key_path = _opencode_dir() / f"{provider}-api-key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(key, encoding="utf-8")
    os.chmod(key_path, 0o600)  # SEC-001
    actual = oct(key_path.stat().st_mode & 0o777)
    audit("set_api_key", provider, "ok", str(key_path))
    # 不回显明文
    return {"ok": True, "provider": provider, "key_file": str(key_path), "mode": actual}


def add_provider(name: str, base_url: str, models: str = "", key_alias: str = "") -> dict:
    if not validate_provider_name(name):
        return {"ok": False, "error": "provider 名非法（仅允许 [a-z0-9-]）"}
    if not is_safe_url(base_url):
        return {"ok": False, "error": "base_url 非法或命中 SSRF 防护（仅 http/https 且非私网）"}
    model_list = [m.strip() for m in (models or "").split(",") if m.strip()]
    if not model_list:
        return {"ok": False, "error": "至少提供一个 models（逗号分隔）"}
    key_alias = key_alias or name
    if not validate_provider_name(key_alias):
        return {"ok": False, "error": "key_alias 非法"}

    cfg = load_config()
    providers = cfg.setdefault("provider", {})
    # SEC-002：先备份全局配置
    cfg_path = _config_path()
    if cfg_path.exists():
        bak = cfg_path.with_suffix(".jsonc.bak." + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))
        shutil.copy(cfg_path, bak)
    providers[name] = {
        "npm": "@ai-sdk/openai-compatible",
        "name": name,
        "whitelist": model_list,
        "options": {
            "baseURL": base_url,
            "apiKey": f"{{file:~/.config/opencode/{key_alias}-api-key}}",
        },
        "models": {m: {"name": m, "tool_call": True, "limit": {"context": 32768, "output": 8192}} for m in model_list},
    }
    save_config(cfg)
    audit("add_provider", name, "ok", f"base_url={base_url} models={models}")
    return {"ok": True, "provider": name, "backup": str(bak) if cfg_path.exists() else None}


# ─── 工具元数据（供 server 暴露） ──────────────────────────
TOOLS = {
    "list_providers": {
        "description": "列出已配置的免费 API 提供方及其 baseURL/模型。",
        "inputSchema": {"type": "object", "properties": {}},
        "fn": lambda a: list_providers(),
    },
    "get_provider_config": {
        "description": "返回某提供方的 baseURL 与免费模型白名单；key 仅返回是否存在，不返明文。",
        "inputSchema": {"type": "object", "properties": {"provider": {"type": "string"}}},
        "fn": lambda a: get_provider_config(a.get("provider", "")),
    },
    "catalog_search": {
        "description": "从免费 API 目录按关键字模糊检索提供方/模型。",
        "inputSchema": {"type": "object", "properties": {"keyword": {"type": "string"}}},
        "fn": lambda a: catalog_search(a.get("keyword", "")),
    },
    "health_check": {
        "description": "探测提供方接口可达性与状态（SSRF 防护，不返 key）。",
        "inputSchema": {"type": "object", "properties": {"provider": {"type": "string"}, "model": {"type": "string"}}},
        "fn": lambda a: health_check(a.get("provider", ""), a.get("model", "")),
    },
    "list_api_keys": {
        "description": "列出各提供方 key 文件是否存在（不读明文）。",
        "inputSchema": {"type": "object", "properties": {}},
        "fn": lambda a: list_api_keys(),
    },
    "set_api_key": {
        "description": "写入提供方 key（高危：0600 权限，明文不回显，须确认）。",
        "inputSchema": {"type": "object", "properties": {"provider": {"type": "string"}, "key": {"type": "string"}}},
        "fn": lambda a: set_api_key(a.get("provider", ""), a.get("key", "")),
    },
    "add_provider": {
        "description": "按模板登记新免费提供方（高危：强校验 + 备份全局配置，须确认）。",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string"}, "base_url": {"type": "string"},
            "models": {"type": "string"}, "key_alias": {"type": "string"}}},
        "fn": lambda a: add_provider(a.get("name", ""), a.get("base_url", ""),
                                     a.get("models", ""), a.get("key_alias", "")),
    },
}
