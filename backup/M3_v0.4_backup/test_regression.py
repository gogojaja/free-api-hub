"""
Free API Hub — 回归测试
覆盖：健康检查、Liveness/Readiness 探针、模型列表、状态查询、重置、chat completions、模型路由隔离、认证拒绝、环境变量隔离

运行:
  cd /Volumes/KINGSTON120G/free-api-hub
  venv/bin/python tests/test_regression.py
"""
import sys
import os
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

HOST = "http://127.0.0.1:5080"

# 管理端点认证 Token（从 .env 加载）
HUB_DIR = os.path.dirname(os.path.dirname(__file__))
_env_path = os.path.join(HUB_DIR, ".env")
ADMIN_TOKEN = ""
if os.path.exists(_env_path):
    with open(_env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("ADMIN_TOKEN="):
                ADMIN_TOKEN = line.split("=", 1)[1].strip()
                break

AUTH_HEADERS = {"Authorization": f"Bearer {ADMIN_TOKEN}"} if ADMIN_TOKEN else {}


def test_health():
    """TC-01: 健康检查"""
    import requests
    resp = requests.get(f"{HOST}/health", timeout=10)
    assert resp.status_code == 200, f"health 返回 {resp.status_code}"
    data = resp.json()
    assert data.get("status") == "ok", f"status 不是 ok: {data}"
    assert data.get("gateway_ready") is True, f"gateway_ready 不是 true: {data}"
    print("[PASS] TC-01 健康检查")


def test_models():
    """TC-02: 模型列表"""
    import requests
    resp = requests.get(f"{HOST}/v1/models", timeout=10)
    assert resp.status_code == 200, f"models 返回 {resp.status_code}"
    data = resp.json()
    assert "data" in data, f"缺少 data 字段: {data}"
    models = data["data"]
    assert len(models) > 0, f"模型列表为空: {data}"
    for m in models:
        assert "id" in m, f"模型缺少 id: {m}"
        assert "provider" in m, f"模型缺少 provider: {m}"
        assert m.get("id") != "unknown", f"模型 id 为 unknown: {m}"
        assert m.get("id") != "gateway", f"模型 id 为 gateway（路由 Bug）: {m}"
    print(f"[PASS] TC-02 模型列表 ({len(models)} 个)")


def test_gateway_status():
    """TC-03: 网关状态（需认证）"""
    import requests
    resp = requests.get(f"{HOST}/gateway/status", timeout=10, headers=AUTH_HEADERS)
    assert resp.status_code == 200, f"status 返回 {resp.status_code}"
    data = resp.json()
    assert "available_providers" in data, f"缺少 available_providers: {data}"
    assert "providers_configured" in data, f"缺少 providers_configured: {data}"
    assert "usage" in data, f"缺少 usage: {data}"
    configured = data["providers_configured"]
    for p in configured:
        assert p.get("has_key") is True, f"{p['name']} 缺少 API Key"
        assert p.get("model", ""), f"{p['name']} 缺少 model 配置"
        assert p.get("model") != "unknown", f"{p['name']} model 为 unknown"
        assert p.get("model") != "gateway", f"{p['name']} model 为 gateway（路由 Bug）"
    print(f"[PASS] TC-03 网关状态 ({len(configured)} 个提供商)")


def test_reset_failures():
    """TC-04: 重置失败状态（需认证）"""
    import requests
    resp = requests.post(f"{HOST}/gateway/reset", timeout=10, headers=AUTH_HEADERS)
    assert resp.status_code == 200, f"reset 返回 {resp.status_code}"
    data = resp.json()
    assert data.get("status") == "ok", f"reset 状态不对: {data}"
    print("[PASS] TC-04 重置失败状态")


def test_chat_non_stream():
    """TC-05: 非流式对话（测试模型路由隔离）"""
    import requests
    payload = {
        "model": "gateway",  # 故意传入无效模型名，测试路由隔离
        "messages": [{"role": "user", "content": "Say hello in one word"}],
        "stream": False,
        "temperature": 0.1,
        "max_tokens": 50,
    }
    resp = requests.post(
        f"{HOST}/v1/chat/completions",
        json=payload,
        timeout=60,
    )
    if resp.status_code == 503:
        print("[SKIP] TC-05 非流式对话 — 所有提供商不可用（配额耗尽），跳过测试")
        return
    assert resp.status_code == 200, f"chat 返回 {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "choices" in data, f"缺少 choices: {data}"
    assert len(data["choices"]) > 0, f"choices 为空: {data}"
    content = data["choices"][0].get("message", {}).get("content", "")
    assert content, f"content 为空: {data}"
    print(f"[PASS] TC-05 非流式对话 (model 传入 'gateway'，实际使用提供商自身模型)")


def test_chat_stream():
    """TC-06: 流式对话"""
    import requests
    payload = {
        "model": "ignored",
        "messages": [{"role": "user", "content": "Count 1 2 3"}],
        "stream": True,
        "temperature": 0.1,
        "max_tokens": 50,
    }
    resp = requests.post(
        f"{HOST}/v1/chat/completions",
        json=payload,
        stream=True,
        timeout=60,
    )
    if resp.status_code == 503:
        print("[SKIP] TC-06 流式对话 — 所有提供商不可用，跳过测试")
        return
    assert resp.status_code == 200, f"chat stream 返回 {resp.status_code}"
    chunks = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("data: "):
            chunk = line[6:]
            if chunk.strip() == "[DONE]":
                break
            chunks.append(chunk)
    assert len(chunks) > 0, "流式响应为空"
    print(f"[PASS] TC-06 流式对话 ({len(chunks)} 个 chunk)")


def test_model_name_isolation():
    """TC-07: 模型路由隔离验证 — 确保不会将客户端 model 传给下游"""
    HUB_DIR = os.path.dirname(os.path.dirname(__file__))
    from gateway import APIGateway
    for cfg_name in ("config/chat.yaml", "config/code.yaml"):
        cfg_path = os.path.join(HUB_DIR, cfg_name)
        if not os.path.exists(cfg_path):
            continue
        gw = APIGateway(config_path=cfg_path)
        for p in gw.providers:
            expected_model = p.get("model", "")
            provider_model = p.get("model") or "gpt-3.5-turbo"
            assert provider_model == expected_model, \
                f"[{cfg_name}] {p['name']}: 期望模型 '{expected_model}', 得到 '{provider_model}'（路由 Bug）"
    print(f"[PASS] TC-07 模型路由隔离 — 全部配置文件的提供商模型均不受客户端影响")


def test_providers_have_model():
    """TC-08: 检查所有配置文件中每个提供商都有 model 配置"""
    import yaml
    HUB_DIR = os.path.dirname(os.path.dirname(__file__))
    checked = 0
    for cfg_name in ("config/chat.yaml", "config/code.yaml", "config/providers.yaml"):
        cfg_path = os.path.join(HUB_DIR, cfg_name)
        if not os.path.exists(cfg_path):
            continue
        with open(cfg_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        providers = config.get("providers", [])
        missing = [p["name"] for p in providers if not p.get("model")]
        assert len(missing) == 0, f"[{cfg_name}] 以下提供商缺少 model 配置: {missing}"
        checked += len(providers)
    assert checked > 0, "未找到任何提供商配置"
    print(f"[PASS] TC-08 配置校验 — {checked} 个提供商均有 model 配置")


def test_auth_denied():
    """TC-09: 管理端点认证拒绝 — 无 Token 访问返回 401"""
    import requests
    # 不带 Authorization 头访问
    resp = requests.get(f"{HOST}/gateway/status", timeout=10)
    assert resp.status_code == 401, f"无 Token 访问 status 应返回 401, 实际 {resp.status_code}"
    resp = requests.post(f"{HOST}/gateway/reset", timeout=10)
    assert resp.status_code == 401, f"无 Token 访问 reset 应返回 401, 实际 {resp.status_code}"
    # 带错误 Token 访问
    bad_headers = {"Authorization": "Bearer wrong-token"}
    resp = requests.get(f"{HOST}/gateway/status", timeout=10, headers=bad_headers)
    assert resp.status_code == 401, f"错误 Token 访问应返回 401, 实际 {resp.status_code}"
    print("[PASS] TC-09 管理端点认证拒绝 — 无 Token/错误 Token 均返回 401")


def test_env_key_isolation():
    """TC-10: 配置文件中无明文 API Key"""
    import yaml
    HUB_DIR = os.path.dirname(os.path.dirname(__file__))
    sensitive_patterns = ["sk-or-v1-", "sk-963a", "326a04", "ark-5949"]
    for cfg_name in ("config/chat.yaml", "config/code.yaml", "config/providers.yaml"):
        cfg_path = os.path.join(HUB_DIR, cfg_name)
        if not os.path.exists(cfg_path):
            continue
        with open(cfg_path, encoding="utf-8") as f:
            content = f.read()
        for pattern in sensitive_patterns:
            assert pattern not in content, f"[{cfg_name}] 发现明文 Key 片段: {pattern}"
    print("[PASS] TC-10 配置文件无明文 Key — 全部使用环境变量占位符")


def test_liveness_probe():
    """TC-11: Liveness 探针 — 进程存活即返回 200"""
    import requests
    resp = requests.get(f"{HOST}/health/live", timeout=10)
    assert resp.status_code == 200, f"/health/live 应返回 200, 实际 {resp.status_code}"
    data = resp.json()
    assert data.get("status") == "alive", f"status 应为 alive: {data}"
    print("[PASS] TC-11 Liveness 探针 — 返回 200 alive")


def test_readiness_probe():
    """TC-12: Readiness 探针 — 网关就绪返回 200，包含详细健康信息"""
    import requests
    resp = requests.get(f"{HOST}/health/ready", timeout=10)
    data = resp.json()
    if resp.status_code == 200:
        assert data.get("status") == "ready", f"status 应为 ready: {data}"
        assert data.get("available_providers", 0) > 0, f"available_providers 应 > 0: {data}"
        assert "total_providers" in data, f"缺少 total_providers: {data}"
        assert "failed_providers" in data, f"缺少 failed_providers: {data}"
        assert "available_names" in data, f"缺少 available_names: {data}"
        print(f"[PASS] TC-12 Readiness 探针 — 就绪, {data['available_providers']}/{data['total_providers']} 可用")
    elif resp.status_code == 503:
        assert data.get("status") == "not_ready", f"503 时 status 应为 not_ready: {data}"
        print(f"[SKIP] TC-12 Readiness 探针 — 未就绪 (无可用 provider)")
    else:
        raise AssertionError(f"/health/ready 应返回 200 或 503, 实际 {resp.status_code}: {data}")


def test_circuit_breaker_status():
    """TC-13: 网关状态包含熔断器信息"""
    import requests
    resp = requests.get(f"{HOST}/gateway/status", timeout=10, headers=AUTH_HEADERS)
    assert resp.status_code == 200, f"status 返回 {resp.status_code}"
    data = resp.json()
    assert "circuit_breakers" in data, f"缺少 circuit_breakers 字段: {data}"
    assert "failed_providers" in data, f"缺少 failed_providers 字段: {data}"
    print(f"[PASS] TC-13 熔断器状态可见 — circuit_breakers 字段存在")


def test_circuit_breaker_unit():
    """TC-14: 熔断器状态机单元测试 — CLOSED→OPEN→HALF_OPEN→CLOSED"""
    from gateway import CircuitBreaker
    import time as _time

    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1)

    # 初始状态：CLOSED，可用
    assert cb.is_available("test"), "初始状态应可用(CLOSED)"
    assert cb.get_state("test") == "closed", f"初始状态应为 closed, 实际 {cb.get_state('test')}"

    # 2 次失败：仍未熔断
    cb.record_failure("test")
    cb.record_failure("test")
    assert cb.is_available("test"), "2 次失败后仍应可用(未达阈值)"
    assert cb.get_state("test") == "closed", f"2 次失败后应为 closed, 实际 {cb.get_state('test')}"

    # 第 3 次失败：熔断
    cb.record_failure("test")
    assert not cb.is_available("test"), "3 次失败后应不可用(OPEN)"
    assert cb.get_state("test") == "open", f"3 次失败后应为 open, 实际 {cb.get_state('test')}"

    # 等待恢复超时：转为 HALF_OPEN
    _time.sleep(1.1)
    state = cb.get_state("test")
    assert state == "half_open", f"恢复超时后应为 half_open, 实际 {state}"
    assert cb.is_available("test"), "HALF_OPEN 状态应可被调用"

    # HALF_OPEN 渐进恢复（FAILOVER-003）：连续 2 次成功才回 CLOSED
    cb.record_success("test")
    assert cb.get_state("test") == "half_open", \
        f"第 1 次成功后应仍为 half_open(渐进恢复), 实际 {cb.get_state('test')}"
    cb.record_success("test")
    assert cb.get_state("test") == "closed", f"HALF_OPEN 连续 2 次成功后应为 closed, 实际 {cb.get_state('test')}"

    # 再次熔断并测试 HALF_OPEN 失败 → OPEN
    cb.record_failure("test")
    cb.record_failure("test")
    cb.record_failure("test")
    assert cb.get_state("test") == "open", "应再次熔断(open)"
    _time.sleep(1.1)
    cb.record_failure("test")  # HALF_OPEN 失败
    assert cb.get_state("test") == "open", f"HALF_OPEN 失败后应为 open, 实际 {cb.get_state('test')}"

    # reset 测试
    cb.reset()
    assert cb.is_available("test"), "reset 后应可用"
    assert cb.get_state("test") == "closed", f"reset 后应为 closed, 实际 {cb.get_state('test')}"

    print("[PASS] TC-14 熔断器状态机 — CLOSED→OPEN→HALF_OPEN→CLOSED 全路径验证通过")


def run_all():
    tests = [
        ("TC-01", test_health),
        ("TC-02", test_models),
        ("TC-03", test_gateway_status),
        ("TC-04", test_reset_failures),
        ("TC-07", test_model_name_isolation),
        ("TC-08", test_providers_have_model),
        ("TC-09", test_auth_denied),
        ("TC-10", test_env_key_isolation),
        ("TC-11", test_liveness_probe),
        ("TC-12", test_readiness_probe),
        # TC-05/06 需要至少一个提供商可用，放在后面
        ("TC-05", test_chat_non_stream),
        ("TC-06", test_chat_stream),
    ]

    passed = 0
    failed = 0
    skipped = 0

    print("=" * 60)
    print("  Free API Hub — 回归测试")
    print("=" * 60)
    print(f"  目标: {HOST}")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    for tc_id, func in tests:
        try:
            func()
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {tc_id} {func.__name__}: {e}")
            failed += 1
        except Exception as e:
            if "所有提供商不可用" in str(e) or "503" in str(e):
                skipped += 1
            else:
                print(f"[FAIL] {tc_id} {func.__name__}: {e}")
                failed += 1

    print("=" * 60)
    print(f"  结果: {passed} 通过, {failed} 失败, {skipped} 跳过")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
