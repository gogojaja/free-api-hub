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
    env = os.environ.get("OPENCE_AUDIT")
    if env:
        return Path(env)
    return _skill_dir() / "audit" / "security_audit.csv"


def _catalog_path() -> Path:
    return _skill_dir() / "domain" / "api_catalog.md"


# ─── jsonc 安全解析（引号内不剥注释） ──────────────────────
def _strip_jsonc_indexed(text: str):
    """剥离注释，返回 (cleaned, idx)。idx[i] = cleaned 第 i 个字符在原文中的索引。"""
    out = []
    idx = []
    i, n = 0, len(text)
    in_str = False
    esc = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            idx.append(i)
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
                idx.append(i)
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
                idx.append(i)
        i += 1
    return "".join(out), idx


def _strip_jsonc(text: str) -> str:
    return _strip_jsonc_indexed(text)[0]


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


def _last_content_pos(raw: str, before: int) -> int:
    """从 before-1 向左找最后一个非空白、非注释的字符索引。"""
    p = before - 1
    while p >= 0:
        c = raw[p]
        if c in " \t\r\n":
            p -= 1
            continue
        if p >= 1 and c == "/" and raw[p - 1] == "*":  # /* ... */ 结尾
            q = p - 2
            while q >= 1 and not (raw[q] == "/" and raw[q + 1] == "*"):
                q -= 1
            p = q - 1
            continue
        line_start = raw.rfind("\n", 0, p) + 1
        if raw[line_start:p + 1].lstrip().startswith("//"):  # // 行注释
            p = line_start - 1
            continue
        return p
    return -1


def _insert_provider_preserving(raw: str, name: str, spec: dict) -> str:
    """在 raw jsonc 文本的 provider 对象内插入新条目，保留注释与格式。

    成功返回新文本；结构不满足时抛 ValueError，由调用方回退整体重写。
    """
    cleaned, idx = _strip_jsonc_indexed(raw)
    key_pos = cleaned.find('"provider"')
    if key_pos < 0:
        raise ValueError("未找到 provider 键")
    colon = cleaned.find(":", key_pos)
    if colon < 0:
        raise ValueError("provider 键后无冒号")
    j = colon + 1
    while j < len(cleaned) and cleaned[j] in " \t\r\n":
        j += 1
    if j >= len(cleaned) or cleaned[j] != "{":
        raise ValueError("provider 值不是对象")

    obj, end = json.JSONDecoder().raw_decode(cleaned, j)  # end-1 = 闭合 } 的 cleaned 索引
    if name in obj:
        raise ValueError(f"provider '{name}' 已存在")
    if not isinstance(obj, dict):
        raise ValueError("provider 不是 dict")

    entry_lines = f'"{name}": ' + json.dumps(spec, indent=2, ensure_ascii=False)
    lines = entry_lines.splitlines()
    entry_block = lines[0] + "".join("\n    " + ln if ln else "" for ln in lines[1:])

    if obj == {}:  # 空对象：直接放进大括号内
        open_raw = idx[j]
        return raw[:open_raw + 1] + "\n    " + entry_block + "\n  " + raw[open_raw + 1:]

    closing_raw = idx[end - 1]
    last_cleaned = cleaned[:end - 1].rstrip()
    need_comma = not last_cleaned.endswith(",")
    p = _last_content_pos(raw, closing_raw)
    if p < 0:
        raise ValueError("provider 对象内未找到已有条目")
    insert_at = p + 1
    comma = "" if last_cleaned.endswith(",") else ","
    return raw[:insert_at] + comma + "\n    " + entry_block + "\n  " + raw[insert_at:]


def add_provider_entry(name: str, spec: dict) -> dict:
    """向全局配置登记 provider，优先注释保留式插入，失败回退整体重写。"""
    p = _config_path()
    if p.exists():
        raw = p.read_text(encoding="utf-8")
        try:
            new_raw = _insert_provider_preserving(raw, name, spec)
            json.loads(_strip_jsonc(new_raw))  # 结果必须可解析
            p.write_text(new_raw, encoding="utf-8")
            return {"mode": "preserved"}
        except (ValueError, json.JSONDecodeError):
            pass  # 回退整体重写
    cfg = load_config()
    cfg.setdefault("provider", {})[name] = spec
    save_config(cfg)
    return {"mode": "rewritten"}


# ─── 模型 limit 校验/修正（官方上下文最大值） ─────────────
_MODELS_DEV_RAW = "https://raw.githubusercontent.com/sst/models.dev/dev/providers/{slug}/models/{model}.toml"
_OPENROUTER_API = "https://openrouter.ai/api/v1/models"
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\-]{0,199}$")

# models.dev 提供方 slug 别名映射（本地 provider 名 → 候选 models.dev slug 顺序）
_MODELS_DEV_ALIASES = {
    "opencode": ["opencode"],
    "zhipu": ["zhipuai", "zai", "zhipu"],
    "siliconflow": ["siliconflow"],
    "bailian": ["alibaba", "qwen", "bailian"],
    "moonshot": ["moonshotai", "moonshot"],
    "modelscope": ["modelscope"],
    "ollama": ["ollama"],
}


def _http_get(url: str, timeout: int = 10):
    req = urllib.request.Request(url, headers={"User-Agent": "free-api-hub-mcp/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def _toml_limit(text: str):
    """从 models.dev 模型 toml 中提取 [limit] 段（无 tomllib 兼容 3.9+）。"""
    m = re.search(r"\[limit\]", text)
    if not m:
        return None
    sec = text[m.end():]
    nxt = re.search(r"^\s*\[", sec, re.M)
    if nxt:
        sec = sec[:nxt.start()]

    def grab(key):
        mm = re.search(rf"^\s*{key}\s*=\s*([0-9_]+)", sec, re.M)
        return int(mm.group(1).replace("_", "")) if mm else None

    ctx, out = grab("context"), grab("output")
    if ctx is None and out is None:
        return None
    return {"context": ctx, "output": out}


def _openrouter_limits(model: str):
    data = json.loads(_http_get(_OPENROUTER_API))
    for m in data.get("data", []):
        if m.get("id") == model:
            lim = {"context": m.get("context_length"), "output": m.get("max_completion_tokens")}
            return lim, "openrouter.api"
    return None, None


def _modelsdev_limits(slug: str, model: str):
    url = _MODELS_DEV_RAW.format(slug=slug, model=model)
    try:
        body = _http_get(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    return _toml_limit(body)


def _official_limits(provider: str, model: str, dev_slug: str = ""):
    """返回 (limit_dict, source)；无官方数据返回 (None, None)。"""
    if provider == "openrouter":
        lim, src = _openrouter_limits(model)
        if lim and (lim.get("context") or lim.get("output")):
            return lim, src
        return None, None
    slugs = [s for s in ([dev_slug] if dev_slug else []) + _MODELS_DEV_ALIASES.get(provider, [provider]) if s]
    for slug in slugs:
        try:
            lim = _modelsdev_limits(slug, model)
        except Exception:
            continue
        if lim:
            return lim, f"models.dev/{slug}"
    return None, None


def validate_model_id(model: str) -> bool:
    return bool(model and _MODEL_ID_RE.match(model))


def _diff_status(local: dict, official: dict) -> str:
    """local/official 均为 {context, output}（值可为 None）。"""
    if not official or (official["context"] is None and official["output"] is None):
        return "no_official"
    lc, lo = local.get("context"), local.get("output")
    oc, oo = official["context"], official["output"]
    same = (oc is None or oc == lc) and (oo is None or oo == lo)
    return "match" if same else "diff"


def check_model_limits(provider: str = "", model: str = "", dev_slug: str = ""):
    """只读：按官方来源（models.dev / OpenRouter API）核对本地模型 limit。"""
    if dev_slug and not re.match(r"^[a-z0-9-]+$", dev_slug):
        return {"ok": False, "error": "dev_slug 非法（仅允许 [a-z0-9-]）"}
    if model and not validate_model_id(model):
        return {"ok": False, "error": "model 非法"}
    if provider and not validate_provider_name(provider):
        return {"ok": False, "error": "provider 名非法（仅允许 [a-z0-9-]）"}

    cfg = load_config()
    providers = cfg.get("provider", {}) or {}
    if provider:
        if provider not in providers:
            return {"ok": False, "error": f"未配置 provider: {provider}"}
        providers = {provider: providers[provider]}

    rows, summary = [], {"match": 0, "diff": 0, "no_official": 0}
    for pname, spec in providers.items():
        for mid, mcfg in (spec.get("models", {}) or {}).items():
            local = mcfg.get("limit") or {"context": None, "output": None}
            official, src = None, None
            try:
                official, src = _official_limits(pname, mid, dev_slug)
            except Exception:
                official, src = None, None
            status = _diff_status(local, official or {})
            summary[status] = summary.get(status, 0) + 1
            rows.append({
                "provider": pname, "model": mid,
                "local": {"context": local.get("context"), "output": local.get("output")},
                "official": official or {},
                "source": src or "", "status": status,
            })
    return {"ok": True, "checked": len(rows), "summary": summary, "rows": rows}


def _replace_limit_preserving(raw: str, provider: str, model: str, limit: dict) -> str:
    """在 raw jsonc 中只替换目标模型 limit 对象，保留其余注释/格式。"""
    cleaned, idx = _strip_jsonc_indexed(raw)
    pk = re.compile(r'"provider"\s*:').search(cleaned)
    if not pk:
        raise ValueError("未找到 provider 键")
    pv = pk.end()
    while pv < len(cleaned) and cleaned[pv] in " \t\r\n":
        pv += 1
    if pv >= len(cleaned) or cleaned[pv] != "{":
        raise ValueError("provider 值不是对象")
    pval_end = json.JSONDecoder().raw_decode(cleaned, pv)[1]

    ep = re.compile(r'"' + re.escape(provider) + r'"\s*:').search(cleaned, pv, pval_end)
    if not ep:
        raise ValueError(f"provider '{provider}' 未找到")
    ev = ep.end()
    while ev < len(cleaned) and cleaned[ev] in " \t\r\n":
        ev += 1
    if ev >= len(cleaned) or cleaned[ev] != "{":
        raise ValueError(f"provider '{provider}' 值不是对象")
    eval_end = json.JSONDecoder().raw_decode(cleaned, ev)[1]

    em = re.compile(r'"' + re.escape(model) + r'"\s*:').search(cleaned, ev, eval_end)
    if not em:
        raise ValueError(f"model '{model}' 未找到")
    mv = em.end()
    while mv < len(cleaned) and cleaned[mv] in " \t\r\n":
        mv += 1
    if mv >= len(cleaned) or cleaned[mv] != "{":
        raise ValueError(f"model '{model}' 值不是对象")
    mval_end = json.JSONDecoder().raw_decode(cleaned, mv)[1]

    el = re.compile(r'"limit"\s*:').search(cleaned, mv, mval_end)
    if not el:
        raise ValueError(f"model '{model}' 未找到 limit 字段")
    lv = el.end()
    while lv < len(cleaned) and cleaned[lv] in " \t\r\n":
        lv += 1
    _, lend = json.JSONDecoder().raw_decode(cleaned, lv)

    raw_l0 = idx[lv]
    raw_l1 = idx[lend - 1] + 1
    return raw[:raw_l0] + json.dumps(limit, ensure_ascii=False) + raw[raw_l1:]


def update_model_limit(provider: str, model: str, context=None, output=None) -> dict:
    """高危：按给定官方值修正本地模型 limit（先备份 + 登记审计，须确认）。"""
    if not validate_provider_name(provider):
        return {"ok": False, "error": "provider 名非法（仅允许 [a-z0-9-]）"}
    if not validate_model_id(model):
        return {"ok": False, "error": "model 非法"}
    if context is None and output is None:
        return {"ok": False, "error": "至少提供 context 或 output"}
    if context is not None and (not isinstance(context, int) or context <= 0):
        return {"ok": False, "error": "context 须为正整数"}
    if output is not None and (not isinstance(output, int) or output <= 0):
        return {"ok": False, "error": "output 须为正整数"}

    cfg = load_config()
    spec = (cfg.get("provider", {}) or {}).get(provider)
    if not spec:
        return {"ok": False, "error": f"未配置 provider: {provider}"}
    mcfg = (spec.get("models", {}) or {}).get(model)
    if not mcfg:
        return {"ok": False, "error": f"provider '{provider}' 未配置 model: {model}"}

    old = mcfg.get("limit") or {}
    new = {
        "context": context if context is not None else old.get("context"),
        "output": output if output is not None else old.get("output"),
    }
    if old.get("context") == new["context"] and old.get("output") == new["output"]:
        return {"ok": True, "provider": provider, "model": model, "unchanged": True, "limit": old}

    cfg_path = _config_path()
    bak = None
    if cfg_path.exists():
        bak = cfg_path.with_suffix(".jsonc.bak." + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))
        shutil.copy(cfg_path, bak)

    mode = "rewritten"
    if cfg_path.exists():
        raw = cfg_path.read_text(encoding="utf-8")
        try:
            new_raw = _replace_limit_preserving(raw, provider, model, new)
            json.loads(_strip_jsonc(new_raw))  # 必须可解析
            # 复核：目标模型 limit 已变为 new
            chk = json.loads(_strip_jsonc(new_raw))["provider"][provider]["models"][model]
            if (chk.get("limit") or {}).get("context") != new["context"] or \
               (chk.get("limit") or {}).get("output") != new["output"]:
                raise ValueError("复核不一致")
            cfg_path.write_text(new_raw, encoding="utf-8")
            mode = "preserved"
        except (ValueError, json.JSONDecodeError, KeyError):
            mcfg["limit"] = new
            save_config(cfg)

    audit("update_model_limit", provider, "ok",
          f"{model} ctx={new['context']} out={new['output']} mode={mode}")
    return {"ok": True, "provider": provider, "model": model, "limit": new,
            "mode": mode, "backup": str(bak) if bak else None}


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

# 保护区：由 OpenCode 内置适配器/官方 SDK 管理，禁止 add_provider 登记或覆盖
_PROTECTED_PROVIDERS = {"opencode"}


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
    if name in _PROTECTED_PROVIDERS:
        return {"ok": False, "error": f"provider '{name}' 为保护区（OpenCode 内置适配器管理），禁止登记/覆盖"}
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
    # 防破坏：已存在的 provider 拒绝整块覆盖，避免摧毁手工调优的配置
    if name in providers:
        existing_url = (providers[name].get("options", {}) or {}).get("baseURL", "")
        return {"ok": False, "error": f"provider '{name}' 已存在（baseURL={existing_url}），拒绝覆盖；如需变更请先手动删除旧配置"}
    key_path = _opencode_dir() / f"{key_alias}-api-key"
    spec = {
        "npm": "@ai-sdk/openai-compatible",
        "name": name,
        "whitelist": model_list,
        "options": {
            "baseURL": base_url,
            "apiKey": "{file:" + str(key_path) + "}",
        },
        "models": {m: {"name": m, "tool_call": True, "limit": {"context": 32768, "output": 8192}} for m in model_list},
    }
    # SEC-002：先备份全局配置
    cfg_path = _config_path()
    bak = None
    if cfg_path.exists():
        bak = cfg_path.with_suffix(".jsonc.bak." + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))
        shutil.copy(cfg_path, bak)
    # 注释保留式写入，失败自动回退整体重写
    mode = add_provider_entry(name, spec)["mode"]
    audit("add_provider", name, "ok", f"base_url={base_url} models={models} mode={mode}")
    return {"ok": True, "provider": name, "write_mode": mode, "backup": str(bak) if bak else None}


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
    "check_model_limits": {
        "description": "只读：按官方来源（models.dev / OpenRouter API）核对本地各模型 limit（context/output）差异。",
        "inputSchema": {"type": "object", "properties": {
            "provider": {"type": "string"}, "model": {"type": "string"},
            "dev_slug": {"type": "string"}}},
        "fn": lambda a: check_model_limits(a.get("provider", ""), a.get("model", ""),
                                           a.get("dev_slug", "")),
    },
    "update_model_limit": {
        "description": "高危：按给定官方值修正本地模型 limit（先备份全局配置 + 登记审计，须确认）。",
        "inputSchema": {"type": "object", "properties": {
            "provider": {"type": "string"}, "model": {"type": "string"},
            "context": {"type": "integer"}, "output": {"type": "integer"}}},
        "fn": lambda a: update_model_limit(a.get("provider", ""), a.get("model", ""),
                                           a.get("context"), a.get("output")),
    },
}
