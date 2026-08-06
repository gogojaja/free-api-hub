"""
Free API Hub — 可观测性指标测试 (NEW-002)

覆盖 4 组用例：
  TC-M1 usage 计数指标（requests/tokens/errors，label=provider）
  TC-M2 熔断器状态指标（closed=0 / open=1）
  TC-M3 延迟直方图指标（quantile/sum/count）
  TC-M4 公开只读 /metrics 路由（server.py 冒烟，含 Content-Type）

运行:
  cd /Volumes/KINGSTON120G/free-api-hub
  venv/bin/python tests/test_metrics.py
"""
import sys
import os
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))
from gateway import APIGateway, CircuitBreaker, _percentile


def _make_gateway():
    api = APIGateway.__new__(APIGateway)
    api.providers = [
        {"name": "p1", "model": "m1", "endpoint": "https://x.com", "api_key": "k"},
        {"name": "p2", "model": "m2", "endpoint": "https://y.com", "api_key": "k"},
    ]
    api.gateway_cfg = {"retry_attempts": 1}
    api.retry_seconds = 15
    api.request_timeout = 30
    api._config_mtime = 0
    api._config_reload_ts = 9999999999
    api.usage = {
        "p1": {"total_requests": 5, "total_tokens": 100, "errors": 1, "last_used": "x"},
        "p2": {"total_requests": 3, "total_tokens": 50, "errors": 0, "last_used": "x"},
    }
    api._lock = threading.Lock()
    api._latency_samples = [0.5, 1.0, 2.0, 4.0]
    api._breaker = CircuitBreaker(3, 60)
    api.current_provider = None
    api.current_provider_display = None
    api._has_creds = lambda p: True
    api.get_available_providers = lambda: [{"name": "p1"}]
    return api


def test_usage_metrics():
    """TC-M1: usage 计数指标"""
    api = _make_gateway()
    text = api.get_metrics_text()
    assert 'fah_requests_total{provider="p1"} 5' in text
    assert 'fah_requests_total{provider="p2"} 3' in text
    assert 'fah_tokens_total{provider="p1"} 100' in text
    assert 'fah_errors_total{provider="p2"} 0' in text
    assert 'fah_available_providers 1' in text
    print("[PASS] TC-M1 usage 计数指标")


def test_breaker_metric():
    """TC-M2: 熔断器状态 closed=0 / open=1"""
    api = _make_gateway()
    # 先记录 1 次失败（未达阈值）→ p2 进入 states 且 closed=0
    api._breaker.record_failure("p2", error_type="5xx")
    assert 'fah_circuit_breaker_state{provider="p2"} 0' in api.get_metrics_text()
    # 累计 3 次 → open=1
    api._breaker.record_failure("p2", error_type="5xx")
    api._breaker.record_failure("p2", error_type="5xx")
    assert 'fah_circuit_breaker_state{provider="p2"} 1' in api.get_metrics_text()
    print("[PASS] TC-M2 熔断器状态指标")


def test_latency_metrics():
    """TC-M3: 延迟直方图 quantile/sum/count"""
    api = _make_gateway()
    text = api.get_metrics_text()
    assert 'fah_http_latency_seconds{quantile="0.5"} 1.500000' in text
    assert 'fah_http_latency_seconds_sum 7.500000' in text
    assert 'fah_http_latency_seconds_count 4' in text
    # _percentile 单元
    assert _percentile([1, 2, 3, 4], 0.5) == 2.5
    assert _percentile([], 0.5) == 0.0
    assert _percentile([5], 0.9) == 5.0
    print("[PASS] TC-M3 延迟直方图指标 + percentile")


def test_metrics_route_smoke():
    """TC-M4: /metrics 路由冒烟（公开只读 + Prometheus Content-Type）"""
    import server as srv

    with srv.app.test_client() as client:
        resp = client.get("/metrics")
        assert resp.status_code == 200, f"公开只读应 200: {resp.status_code}"
        assert "text/plain" in resp.content_type, f"应 Prometheus 文本: {resp.content_type}"
        body = resp.get_data(as_text=True)
        # 未初始化时返回 fah_gateway_ready 0；初始化后返回完整指标
        assert body.startswith("# HELP") or body.startswith("fah_"), \
            f"应含指标内容: {body[:80]}"
    print("[PASS] TC-M4 /metrics 路由冒烟（公开只读）")


def run_all():
    tests = [
        ("TC-M1", test_usage_metrics),
        ("TC-M2", test_breaker_metric),
        ("TC-M3", test_latency_metrics),
        ("TC-M4", test_metrics_route_smoke),
    ]
    passed = 0
    failed = 0
    print("=" * 60)
    print("  Free API Hub — 可观测性指标测试 (NEW-002)")
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
