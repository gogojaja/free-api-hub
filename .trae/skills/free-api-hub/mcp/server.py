#!/usr/bin/env python3
"""free-api-hub 本地 MCP 服务（零依赖 stdio JSON-RPC）。

协议最小集：initialize / notifications/initialized / tools/list / tools/call / ping。
启动：python3 server.py
自测：python3 server.py --self-test
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tools_impl as T  # noqa: E402


PROTOCOL_VERSION = "2024-11-05"


def _tool_list():
    return [{"name": n, "description": m["description"], "inputSchema": m["inputSchema"]}
            for n, m in T.TOOLS.items()]


def _dispatch(name, arguments):
    tool = T.TOOLS.get(name)
    if not tool:
        return {"isError": True, "content": [{"type": "text", "text": f"未知工具: {name}"}]}
    try:
        result = tool["fn"](arguments or {})
        text = json.dumps(result, ensure_ascii=False)
        return {"isError": not result.get("ok", True), "content": [{"type": "text", "text": text}]}
    except Exception as e:
        return {"isError": True, "content": [{"type": "text", "text": f"工具执行异常: {e}"}]}


def _handle(msg):
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params", {}) or {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "free-api-hub", "version": "1.0.0"},
        }}
    if method == "notifications/initialized":
        return None  # 通知无响应
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": _tool_list()}}
    if method == "tools/call":
        name = params.get("name", "")
        res = _dispatch(name, params.get("arguments", {}))
        return {"jsonrpc": "2.0", "id": mid, "result": res}
    # 未知方法
    if mid is not None:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None


def _self_test():
    print("== free-api-hub MCP self-test ==")
    print(f"protocolVersion = {PROTOCOL_VERSION}")
    tools = _tool_list()
    print(f"tools/list ({len(tools)}):")
    for t in tools:
        print(f"  - {t['name']}: {t['description'][:40]}")
    # 非联网安全校验
    assert T.is_safe_url("https://api.siliconflow.cn/v1") is True
    assert T.is_safe_url("http://127.0.0.1/api") is False
    assert T.is_safe_url("http://169.254.169.254/") is False
    assert T.is_safe_url("ftp://x") is False
    assert T.validate_provider_name("openrouter") is True
    assert T.validate_provider_name("Bad_Name") is False
    print("is_safe_url / validate_provider_name: OK")
    cat = T.catalog_search("glm")
    print(f"catalog_search('glm'): {cat.get('count')} hit(s)")
    print("self-test PASSED")


def main():
    if "--self-test" in sys.argv:
        _self_test()
        return
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        try:
            resp = _handle(msg)
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32603, "message": str(e)}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
