"""
Free API Hub — 失败率熔断测试 (DEGRADE-001)

覆盖 4 组用例：
  TC-D1 失败率熔断：窗口失败率 >25%（样本>=10）→ OPEN
  TC-D2 低失败率不熔断：<25% 保持 CLOSED
  TC-D3 差异化冷却：429 短冷却 vs 5xx 长冷却
  TC-D4 半开恢复：冷却到期转 HALF_OPEN，成功 → CLOSED

运行:
  cd /Volumes/KINGSTON120G/free-api-hub
  venv/bin/python tests/test_circuit_breaker_rate.py
"""
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))
from gateway import CircuitBreaker


def test_rate_based_open():
    """TC-D1: 窗口失败率 >25%（样本10，6失败=60%）→ OPEN"""
    cb = CircuitBreaker(failure_threshold=99, recovery_timeout=60,
                        min_samples=10, failure_rate_threshold=0.25)
    for i in range(10):
        if i < 6:
            cb.record_failure("p2")
        else:
            cb.record_success("p2")
    assert cb.get_state("p2") == "open", f"60% 失败率应 OPEN: {cb.get_state('p2')}"
    assert not cb.is_available("p2"), "OPEN 状态应不可用"
    print("[PASS] TC-D1 失败率熔断 — 60% > 25% 触发 OPEN")


def test_low_rate_no_open():
    """TC-D2: 窗口失败率 <25%（样本10，2失败=20%）→ 保持 CLOSED"""
    cb = CircuitBreaker(failure_threshold=99, recovery_timeout=60,
                        min_samples=10, failure_rate_threshold=0.25)
    for i in range(10):
        if i < 2:
            cb.record_failure("p3")
        else:
            cb.record_success("p3")
    assert cb.get_state("p3") == "closed", f"20% 失败率应 CLOSED: {cb.get_state('p3')}"
    assert cb.is_available("p3"), "CLOSED 状态应可用"
    print("[PASS] TC-D2 低失败率不熔断 — 20% < 25% 保持 CLOSED")


def test_diff_cooldown():
    """TC-D3: 429 短冷却(15s) vs 5xx 长冷却(60s)"""
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60,
                        cooldown_429=15, cooldown_5xx=60)
    cb.record_failure("p429", error_type="429")
    cb.record_failure("p5xx", error_type="5xx")
    detail = cb.get_detail()
    assert detail["p429"]["cooldown"] == 15, f"429 应短冷却 15s: {detail['p429']}"
    assert detail["p5xx"]["cooldown"] == 60, f"5xx 应长冷却 60s: {detail['p5xx']}"

    # 429 冷却到点转半开
    cb._states["p429"]["last_failure_ts"] = time.time() - 16
    assert cb.get_state("p429") == "half_open", "429 冷却 16s 后应转 HALF_OPEN"
    # 5xx 未到点仍 OPEN
    cb._states["p5xx"]["last_failure_ts"] = time.time() - 16
    assert cb.get_state("p5xx") == "open", "5xx 冷却 16s 未到仍应 OPEN"
    print("[PASS] TC-D3 差异化冷却 — 429 短/5xx 长正确")


def test_half_open_recovery():
    """TC-D4: 冷却到期转 HALF_OPEN，渐进恢复（FAILOVER-003）"""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1)
    for _ in range(3):
        cb.record_failure("p5")
    assert cb.get_state("p5") == "open"
    time.sleep(1.1)
    assert cb.get_state("p5") == "half_open", f"应 HALF_OPEN: {cb.get_state('p5')}"
    cb.record_success("p5")
    assert cb.get_state("p5") == "half_open", "渐进恢复第 1 次成功仍应 HALF_OPEN"
    cb.record_success("p5")
    assert cb.get_state("p5") == "closed", f"连续 2 次成功应 CLOSED: {cb.get_state('p5')}"

    # HALF_OPEN 失败 → 回 OPEN
    for _ in range(3):
        cb.record_failure("p5")
    time.sleep(1.1)
    cb.record_failure("p5")  # HALF_OPEN 探测失败
    assert cb.get_state("p5") == "open", "半开失败应回 OPEN"
    print("[PASS] TC-D4 半开恢复 — 连续2次成功 CLOSED / 探测失败回 OPEN")


def run_all():
    tests = [
        ("TC-D1", test_rate_based_open),
        ("TC-D2", test_low_rate_no_open),
        ("TC-D3", test_diff_cooldown),
        ("TC-D4", test_half_open_recovery),
    ]
    passed = 0
    failed = 0
    print("=" * 60)
    print("  Free API Hub — 失败率熔断测试 (DEGRADE-001)")
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
