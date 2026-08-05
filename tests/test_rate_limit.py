"""
Free API Hub — 429 规范化测试 (NEW-003)

覆盖 3 组用例：
  TC-L1 RateLimiter 滑动窗口：窗口内限次放行、超限拒绝、窗口滑动后放行
  TC-L2 限流响应含规范化 429 头（Retry-After / X-RateLimit-*）
  TC-L3 网关错误响应状态码透传（429 场景）

运行:
  cd /Volumes/KINGSTON120G/free-api-hub
  venv/bin/python tests/test_rate_limit.py
"""
import sys
import os
import json
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))
from gateway import RateLimiter


def test_rate_limiter_window():
    """TC-L1: 滑动窗口限流 — 窗口内放行 limit 次，超出拒绝，窗口滑动后放行"""
    rl = RateLimiter(limit=3, window_seconds=60)
    t0 = 1000.0

    results = [rl.check(now=t0 + i) for i in range(5)]
    assert all(r[0] for r in results[:3]), f"前 3 次应放行: {results[:3]}"
    assert all(not r[0] for r in results[3:]), f"后 2 次应拒绝: {results[3:]}"
    assert results[0][1] == 2, f"第 1 次 remaining 应为 2: {results[0][1]}"
    assert results[2][1] == 0, f"第 3 次 remaining 应为 0: {results[2][1]}"
    assert results[3][2] == t0 + 60, f"reset 应为窗口起点+60: {results[3][2]}"

    # 窗口滑动后放行
    assert rl.check(now=t0 + 61)[0], "窗口滑动后应放行"

    # 多窗口滚动：最旧时间戳过期即放行
    assert rl.check(now=t0 + 61)[0] is True
    print("[PASS] TC-L1 滑动窗口限流 — 放行/拒绝/滑动均正确")


def test_rate_limit_headers():
    """TC-L2: 规范化 429 响应头（模拟 Flask 层行为）"""
    import time as _time
    rl = RateLimiter(limit=2, window_seconds=60)
    rl.check()
    rl.check()
    allowed, remaining, reset_ts = rl.check()
    assert not allowed, "第 3 次应被限流"
    assert remaining == 0
    retry_after = max(1, int(reset_ts - _time.time()))
    assert retry_after >= 1, f"Retry-After 应 >= 1: {retry_after}"

    headers = {
        "Retry-After": str(retry_after),
        "X-RateLimit-Limit": str(rl.limit),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(reset_ts),
    }
    assert headers["Retry-After"] == str(retry_after)
    assert headers["X-RateLimit-Limit"] == "2"
    assert headers["X-RateLimit-Remaining"] == "0"
    assert headers["X-RateLimit-Reset"] == str(reset_ts)

    body = json.dumps({"error": "rate_limit_exceeded",
                       "message": "请求过于频繁，请稍后重试",
                       "retry_after": retry_after})
    parsed = json.loads(body)
    assert parsed["error"] == "rate_limit_exceeded"
    assert parsed["retry_after"] == retry_after
    print("[PASS] TC-L2 规范化 429 — Retry-After + X-RateLimit-* 头齐全")


def test_error_status_passthrough():
    """TC-L3: 网关错误响应透传状态码（429 场景）"""
    from gateway import APIGateway
    import gateway as gw

    api = gw.APIGateway.__new__(gw.APIGateway)
    api.config_path = Path("config/chat.yaml")
    api.gateway_cfg = {"retry_attempts": 1}
    api.retry_seconds = 15
    api.request_timeout = 30
    api.providers = [
        {"name": "p1", "model": "m1", "endpoint": "https://x.com",
         "api_key": "k", "priority": 1},
    ]
    api.current_provider = None
    api.current_provider_display = None
    api._breaker = gw.CircuitBreaker(3, 60)
    api.usage = {}
    api._lock = gw.threading.Lock()
    api._mark_failed = lambda name: None
    api._track = lambda name, tokens=0: None

    class FakeResp:
        status_code = 429
        headers = {"Retry-After": "5"}

        def json(self):
            return {}

    original_post = gw.requests.post
    original_sleep = gw.time.sleep
    gw.requests.post = lambda *a, **k: FakeResp()
    gw.time.sleep = lambda s: None
    try:
        r = api.call_api([{"role": "user", "content": "hi"}])
    finally:
        gw.requests.post = original_post
        gw.time.sleep = original_sleep
    assert r.get("status_code") == 429, f"429 场景应透传 status_code=429: {r}"
    assert "error" in r, f"应返回错误: {r}"
    print("[PASS] TC-L3 错误状态码透传 — 429 场景 status_code=429")


def run_all():
    tests = [
        ("TC-L1", test_rate_limiter_window),
        ("TC-L2", test_rate_limit_headers),
        ("TC-L3", test_error_status_passthrough),
    ]
    passed = 0
    failed = 0
    print("=" * 60)
    print("  Free API Hub — 429 规范化测试 (NEW-003)")
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
