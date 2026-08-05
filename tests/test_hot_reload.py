"""
Free API Hub — 配置热加载测试 (CONFIG-002)

覆盖 3 组用例：
  TC-H1 mtime 变化 + TTL 越过后重载（新增 provider 生效）
  TC-H2 TTL 内不重载（避免频繁磁盘 IO）
  TC-H3 配置损坏时保留旧配置继续服务

运行:
  cd /Volumes/KINGSTON120G/free-api-hub
  venv/bin/python tests/test_hot_reload.py
"""
import sys
import os
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))
import gateway as gw


def _write_config(path, n_providers):
    lines = ["gateway:\n", "  port: 5080\n", "providers:\n"]
    for i in range(1, n_providers + 1):
        lines.append(
            f"  - name: p{i}\n    model: m{i}\n    priority: {i}\n    api_key: k{i}\n"
        )
    path.write_text("".join(lines), encoding="utf-8")


def _make_gateway(tmp):
    old_base = gw.BASE_DIR
    old_data = gw.DATA_DIR
    gw.BASE_DIR = Path(tmp)
    gw.DATA_DIR = Path(tmp) / "data"
    cfg = Path(tmp) / "config" / "chat.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    _write_config(cfg, 1)
    try:
        api = gw.APIGateway(config_path=str(cfg))
    finally:
        gw.BASE_DIR = old_base
        gw.DATA_DIR = old_data
    return api, cfg


def test_reload_on_mtime_change():
    """TC-H1: mtime 变化 + TTL 越过后重载，新增 provider 生效"""
    with tempfile.TemporaryDirectory() as td:
        api, cfg = _make_gateway(td)
        assert [p["name"] for p in api.providers] == ["p1"]

        _write_config(cfg, 2)
        os.utime(cfg, None)
        api._config_reload_ts = 0
        api._config_mtime = 0

        api.get_available_providers()
        names = [p["name"] for p in api.providers]
        assert names == ["p1", "p2"], f"热加载后应为 2 个 provider: {names}"
        assert api._config_mtime == cfg.stat().st_mtime, "mtime 指纹应更新"
        print("[PASS] TC-H1 mtime 变化重载 — 新增 provider 生效")


def test_no_reload_within_ttl():
    """TC-H2: TTL(5s) 内不重载，避免频繁磁盘 IO"""
    with tempfile.TemporaryDirectory() as td:
        api, cfg = _make_gateway(td)
        _write_config(cfg, 2)
        os.utime(cfg, None)
        api._config_mtime = 0  # mtime 变化但 TTL 未到
        # 不重置 _config_reload_ts，保持刚加载

        api.get_available_providers()
        names = [p["name"] for p in api.providers]
        assert names == ["p1"], f"TTL 内不应重载: {names}"
        print("[PASS] TC-H2 TTL 内不重载 — 避免频繁磁盘 IO")


def test_keep_old_on_broken_config():
    """TC-H3: 配置损坏时保留旧配置继续服务"""
    with tempfile.TemporaryDirectory() as td:
        api, cfg = _make_gateway(td)
        cfg.write_text("gateway: [broken\n", encoding="utf-8")
        os.utime(cfg, None)
        api._config_reload_ts = 0
        api._config_mtime = 0

        api.get_available_providers()
        names = [p["name"] for p in api.providers]
        assert names == ["p1"], f"损坏配置应保留旧配置: {names}"
        print("[PASS] TC-H3 配置损坏保留旧配置 — 服务不中断")


def run_all():
    tests = [
        ("TC-H1", test_reload_on_mtime_change),
        ("TC-H2", test_no_reload_within_ttl),
        ("TC-H3", test_keep_old_on_broken_config),
    ]
    passed = 0
    failed = 0
    print("=" * 60)
    print("  Free API Hub — 配置热加载测试 (CONFIG-002)")
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
