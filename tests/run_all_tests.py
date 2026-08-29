"""
Free API Hub — 统一测试调度入口（M4 集成回归）
顺序执行 7 个 M3 变更项单元测试文件（24 用例）+ 回归套件（17 用例，需 5080 实例在运行），
输出汇总 PASS/FAIL 计数 + CSV 汇总表（UTF-8 BOM）。

运行:
  cd /Volumes/KINGSTON120G/free-api-hub
  venv/bin/python tests/run_all_tests.py
"""
import csv
import os
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(BASE_DIR, "venv", "bin", "python")
REGRESSION_HOST = "http://127.0.0.1:5080"

SUITES = [
    ("单元测试 NEW-004 配置快照", "tests/test_backup_config.py", 3),
    ("单元测试 NEW-001 重试退避", "tests/test_retry.py", 3),
    ("单元测试 NEW-003 429 规范化", "tests/test_rate_limit.py", 3),
    ("单元测试 CONFIG-002 热加载", "tests/test_hot_reload.py", 3),
    ("单元测试 DEGRADE-001 失败率熔断", "tests/test_circuit_breaker_rate.py", 4),
    ("单元测试 FAILOVER-003 渐进恢复", "tests/test_failover_recovery.py", 4),
    ("单元测试 NEW-002 可观测性指标", "tests/test_metrics.py", 4),
    ("单元测试 智能路由 ADR-008/009/010", "tests/test_routing.py", 13),
    ("集成回归 回归套件(需 5080)", "tests/test_regression.py", 17),
]


def _run_suite(label, rel_path, expected):
    path = os.path.join(BASE_DIR, rel_path)
    proc = subprocess.run(
        [VENV_PY, path], capture_output=True, text=True, timeout=600
    )
    tail = proc.stdout.strip().splitlines()
    summary = next((ln for ln in reversed(tail) if "结果:" in ln), "")
    passed = 0
    failed = 0
    skipped = 0
    if "结果:" in summary:
        parts = summary.split("结果:")[1].split(",")
        passed = int(parts[0].strip().split()[0])
        failed = int(parts[1].strip().split()[0])
        if len(parts) > 2:
            skipped = int(parts[2].strip().split()[0])
    ok = proc.returncode == 0 and failed == 0
    return {
        "套件": label,
        "用例数": passed + failed + skipped,
        "通过": passed,
        "失败": failed,
        "跳过": skipped,
        "状态": "PASS" if ok else "FAIL",
    }


def main():
    print("=" * 64)
    print("  Free API Hub — 统一测试调度（M4 集成回归）")
    print("=" * 64)
    print(f"  回归目标: {REGRESSION_HOST}")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 64)

    # 前置检查：5080 实例
    import urllib.request
    try:
        with urllib.request.urlopen(f"{REGRESSION_HOST}/health/live", timeout=5) as resp:
            assert resp.status == 200
    except Exception:
        print("[WARN] 5080 实例未运行，回归套件用例将失败")
        print("       请先启动: venv/bin/python src/server.py --config config/chat.yaml &")
        print()

    rows = []
    all_ok = True
    for label, rel, expected in SUITES:
        try:
            r = _run_suite(label, rel, expected)
        except subprocess.TimeoutExpired:
            r = {"套件": label, "用例数": expected, "通过": 0, "失败": expected,
                 "跳过": 0, "状态": "TIMEOUT"}
        rows.append(r)
        ok = r["状态"] == "PASS"
        all_ok = all_ok and ok
        print(f"  [{r['状态']}] {label}: {r['通过']} 通过 / {r['失败']} 失败"
              f" / {r['跳过']} 跳过（共 {r['用例数']}）")

    total_pass = sum(r["通过"] for r in rows)
    total_fail = sum(r["失败"] for r in rows)
    total_skip = sum(r["跳过"] for r in rows)

    print("=" * 64)
    print(f"  汇总: {total_pass} 通过, {total_fail} 失败, {total_skip} 跳过"
          f"（{len(rows)} 套件）")
    print(f"  总体: {'ALL PASS' if all_ok else 'HAS FAILURE'}")
    print("=" * 64)

    csv_path = os.path.join(BASE_DIR, "测试汇总_M4_集成回归.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["套件", "用例数", "通过", "失败", "跳过", "状态"])
        writer.writeheader()
        writer.writerows(rows)
        writer.writerow({"套件": "TOTAL", "用例数": total_pass + total_fail + total_skip,
                         "通过": total_pass, "失败": total_fail,
                         "跳过": total_skip, "状态": "ALL PASS" if all_ok else "HAS FAILURE"})
    print(f"  汇总表已写入: {csv_path}")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
