"""
Free API Hub — 重试+指数退避测试 (NEW-001)

覆盖 3 组用例：
  TC-R1 瞬时错误（ConnectionError/Timeout）重试后成功（共 3 次请求）
  TC-R2 401 不重试，直接 failover；429 按 Retry-After 等待后重试
  TC-R3 重试耗尽后标记失败并返回错误

运行:
  cd /Volumes/KINGSTON120G/free-api-hub
  venv/bin/python tests/test_retry.py
"""
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))
import gateway as gw


class FakeResp:
    def __init__(self, code, body=None):
        self.status_code = code
        self._body = body or {}
        self.headers = {}

    def json(self):
        return self._body


def _make_gateway():
    api = gw.APIGateway.__new__(gw.APIGateway)
    api.config_path = Path("config/chat.yaml")
    api.gateway_cfg = {"retry_attempts": 3}
    api.retry_seconds = 15
    api.request_timeout = 30
    api._config_mtime = api.config_path.stat().st_mtime
    api._config_reload_ts = 9999999999
    api.providers = [
        {"name": "p1", "model": "m1", "endpoint": "https://x.com",
         "api_key": "k", "priority": 1},
    ]
    api.current_provider = None
    api.current_provider_display = None
    api._breaker = gw.CircuitBreaker(3, 60)
    api.usage = {}
    api._lock = gw.threading.Lock()
    api._mark_failed = lambda name, error_type=None: None
    api._track = lambda name, tokens=0: None
    return api


def test_retry_then_success():
    """TC-R1: 瞬时错误重试后成功（共 3 次请求，2 次退避）"""
    api = _make_gateway()
    calls = {"n": 0}
    original_post = gw.requests.post
    original_sleep = gw.time.sleep
    gw.time.sleep = lambda s: None

    def fake_post(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise gw.requests.exceptions.ConnectionError("boom")
        return FakeResp(200, {"usage": {"total_tokens": 5},
                              "choices": [{"message": {"content": "hi"}}]})

    gw.requests.post = fake_post
    try:
        r = api.call_api([{"role": "user", "content": "hi"}])
    finally:
        gw.requests.post = original_post
        gw.time.sleep = original_sleep
    assert calls["n"] == 3, f"瞬时错误应重试 2 次共 3 次请求, 实际 {calls['n']}"
    assert r.get("choices"), f"重试后应成功返回, 实际 {r}"
    print("[PASS] TC-R1 瞬时错误重试后成功 (3 次请求)")


def test_no_retry_on_401_retry_on_429():
    """TC-R2: 401 不重试直接 failover；429 按 Retry-After 等待后重试"""
    api = _make_gateway()
    original_post = gw.requests.post
    original_sleep = gw.time.sleep
    gw.time.sleep = lambda s: None

    # 401 → 只请求 1 次
    calls = {"n": 0}

    def fake_401(*a, **k):
        calls["n"] += 1
        return FakeResp(401)

    gw.requests.post = fake_401
    try:
        r = api.call_api([{"role": "user", "content": "hi"}])
    finally:
        pass
    assert calls["n"] == 1, f"401 应只请求 1 次, 实际 {calls['n']}"
    assert "error" in r, f"401 failover 后应返回错误: {r}"

    # 429 带 Retry-After:3 → 等待 3s 重试，第二次成功
    calls2 = {"n": 0}
    waits = []

    def fake_429(*a, **k):
        calls2["n"] += 1
        resp = FakeResp(429)
        resp.headers["Retry-After"] = "3"
        if calls2["n"] == 1:
            return resp
        return FakeResp(200, {"usage": {"total_tokens": 5},
                              "choices": [{"message": {"content": "hi"}}]})

    def record_sleep(secs):
        waits.append(secs)

    gw.requests.post = fake_429
    gw.time.sleep = record_sleep
    try:
        r2 = api.call_api([{"role": "user", "content": "hi"}])
    finally:
        gw.requests.post = original_post
        gw.time.sleep = original_sleep
    assert calls2["n"] == 2, f"429 应重试 1 次共 2 次请求, 实际 {calls2['n']}"
    assert waits == [3.0], f"429 应按 Retry-After 等待 3s, 实际 {waits}"
    assert r2.get("choices"), f"429 重试后应成功: {r2}"
    print("[PASS] TC-R2 401 不重试 / 429 按 Retry-After 重试")


def test_retry_exhausted():
    """TC-R3: 重试耗尽后标记失败并返回错误"""
    api = _make_gateway()
    calls = {"n": 0}
    marked = {"count": 0}
    api._mark_failed = lambda name, error_type=None: marked.__setitem__("count", marked["count"] + 1)

    original_post = gw.requests.post
    original_sleep = gw.time.sleep
    gw.time.sleep = lambda s: None

    def fake_always_429(*a, **k):
        calls["n"] += 1
        resp = FakeResp(429)
        resp.headers["Retry-After"] = "1"
        return resp

    gw.requests.post = fake_always_429
    try:
        r = api.call_api([{"role": "user", "content": "hi"}])
    finally:
        gw.requests.post = original_post
        gw.time.sleep = original_sleep
    assert calls["n"] == 3, f"429 重试耗尽应为 3 次请求, 实际 {calls['n']}"
    assert marked["count"] == 1, f"重试耗尽后应标记失败 1 次, 实际 {marked['count']}"
    assert "error" in r, f"重试耗尽应返回错误: {r}"
    print("[PASS] TC-R3 重试耗尽标记失败并返回错误")


def run_all():
    tests = [
        ("TC-R1", test_retry_then_success),
        ("TC-R2", test_no_retry_on_401_retry_on_429),
        ("TC-R3", test_retry_exhausted),
    ]
    passed = 0
    failed = 0
    print("=" * 60)
    print("  Free API Hub — 重试+指数退避测试 (NEW-001)")
    print("=" * 60)
    for tc_id, func in tests:
        try:
            func()
            passed += 1
        except Exception as e:
            print(f"[FAIL] {tc_id}: {e}")
            failed += 1
    print("=" * 60)
    print(f"  结果: {passed} 通过, {failed} 失败")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
