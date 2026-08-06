"""
Free API Hub — 渐进恢复测试 (FAILOVER-003)

覆盖 4 组用例：
  TC-F1 HALF_OPEN 20% 概率放行（CLOSED 全放行 / OPEN 全拒绝）
  TC-F2 连续 2 次成功 → CLOSED 全量恢复
  TC-F3 试探失败 → 立即回 OPEN（重置半开计数）
  TC-F4 恢复后流量全量放行（100%）

运行:
  cd /Volumes/KINGSTON120G/free-api-hub
  venv/bin/python tests/test_failover_recovery.py
"""
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))
from gateway import CircuitBreaker


def _to_half_open(cb, name, failures=3, timeout=1.1):
    """把 provider 推进到 HALF_OPEN 状态"""
    for _ in range(failures):
        cb.record_failure(name)
    time.sleep(timeout)
    assert cb.get_state(name) == "half_open", f"应 HALF_OPEN: {cb.get_state(name)}"


def test_probe_ratio():
    """TC-F1: HALF_OPEN 20% 放行 / CLOSED 全放行 / OPEN 全拒绝"""
    # HALF_OPEN ~20%
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1)
    _to_half_open(cb, "p")
    allowed = sum(1 for _ in range(2000) if cb.should_allow("p"))
    rate = allowed / 2000 * 100
    assert 15 <= rate <= 25, f"半开放行率应 ~20%: {rate:.1f}%"

    # CLOSED 全放行
    cb2 = CircuitBreaker()
    assert all(cb2.should_allow("x") for _ in range(100)), "CLOSED 应全放行"

    # OPEN 全拒绝
    cb3 = CircuitBreaker(failure_threshold=1)
    cb3.record_failure("y")
    assert not any(cb3.should_allow("y") for _ in range(100)), "OPEN 应全拒绝"
    print("[PASS] TC-F1 渐进放行 — 半开~20% / CLOSED 全放行 / OPEN 全拒绝")


def test_full_recovery():
    """TC-F2: 连续 2 次成功 → CLOSED 全量恢复"""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1)
    _to_half_open(cb, "q")

    cb.record_success("q")
    assert cb.get_state("q") == "half_open", "第 1 次成功仍应 HALF_OPEN(渐进)"
    cb.record_success("q")
    assert cb.get_state("q") == "closed", f"第 2 次成功应 CLOSED: {cb.get_state('q')}"
    assert all(cb.should_allow("q") for _ in range(100)), "恢复后应全量放行"
    print("[PASS] TC-F2 全量恢复 — 连续 2 次成功 → CLOSED → 100% 放行")


def test_probe_failure():
    """TC-F3: 试探失败 → 立即回 OPEN（不预调 get_state）"""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1)
    _to_half_open(cb, "r")

    cb.record_success("r")
    assert cb.get_state("r") == "half_open"
    cb.record_failure("r", error_type="5xx")  # 试探失败
    assert cb.get_state("r") == "open", f"试探失败应回 OPEN: {cb.get_state('r')}"
    assert not cb.should_allow("r"), "回 OPEN 后应拒绝"
    print("[PASS] TC-F3 试探失败 — 立即回 OPEN 并停止放行")


def test_recovery_then_full():
    """TC-F4: 恢复 → 全量放行；再次熔断冷却到期 → 重新进入 HALF_OPEN"""
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=1)
    _to_half_open(cb, "s", failures=2)
    cb.record_success("s")
    cb.record_success("s")
    assert cb.get_state("s") == "closed"

    # 再次熔断
    cb.record_failure("s")
    cb.record_failure("s")
    assert cb.get_state("s") == "open"
    time.sleep(1.1)
    assert cb.get_state("s") == "half_open", "冷却到期应再次进入 HALF_OPEN"
    print("[PASS] TC-F4 循环恢复 — 再次熔断后可重新渐进恢复")


def run_all():
    tests = [
        ("TC-F1", test_probe_ratio),
        ("TC-F2", test_full_recovery),
        ("TC-F3", test_probe_failure),
        ("TC-F4", test_recovery_then_full),
    ]
    passed = 0
    failed = 0
    print("=" * 60)
    print("  Free API Hub — 渐进恢复测试 (FAILOVER-003)")
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
