"""
Free API Hub — 配置快照脚本测试 (NEW-004)

覆盖 3 组用例：
  TC-B1 备份递增与上限清理（backup 连续调用，序号递增，超 10 份清理最旧）
  TC-B2 回滚（restore 从最新/指定序号快照恢复配置内容）
  TC-B3 列表与错误处理（list 输出、恢复不存在序号报错、backup 缺失文件报错）

运行:
  cd /Volumes/KINGSTON120G/free-api-hub
  venv/bin/python tests/test_backup_config.py
"""
import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))

import backup_config


def test_backup_increment_and_cleanup():
    """TC-B1: 备份序号递增；超过上限清理最旧一份"""
    with tempfile.TemporaryDirectory() as td:
        backup_config.BACKUP_DIR = Path(td) / "backup"
        cfg = Path(td) / "test.yaml"
        cfg.write_text("gateway:\n  port: 5080\n", encoding="utf-8")

        paths = []
        for i in range(12):  # 超上限（10）触发清理
            p = backup_config.backup_config(cfg)
            paths.append(p)

        # 序号应递增至 v12
        assert paths[-1].name == "config_v12_test.yaml", f"最后快照应为 v12: {paths[-1].name}"
        # 上限清理：只保留最近 10 份（v3~v12）
        existing = backup_config._existing_snapshots(cfg)
        assert len(existing) == 10, f"应保留 10 份, 实际 {len(existing)}"
        assert existing[0][0] == 3, f"最旧应被清理为 v3, 实际 {existing[0][0]}"
        assert existing[-1][0] == 12, f"最新应为 v12, 实际 {existing[-1][0]}"
        # 快照内容与原文件一致
        import yaml
        content = yaml.safe_load(existing[-1][1].read_text(encoding="utf-8"))
        assert content["gateway"]["port"] == 5080, "快照内容与原配置不一致"
        print("[PASS] TC-B1 备份递增与上限清理")


def test_restore():
    """TC-B2: 回滚 — 从最新与指定序号快照恢复"""
    with tempfile.TemporaryDirectory() as td:
        backup_config.BACKUP_DIR = Path(td) / "backup"
        cfg = Path(td) / "test.yaml"
        cfg.write_text("gateway:\n  port: 5080\n", encoding="utf-8")

        backup_config.backup_config(cfg)  # v1: port=5080
        cfg.write_text("gateway:\n  port: 5081\n", encoding="utf-8")
        backup_config.backup_config(cfg)  # v2: port=5081

        # 默认回滚到最新（v2）
        src = backup_config.restore_config(cfg)
        assert src.name == "config_v2_test.yaml", f"默认应回滚 v2: {src.name}"
        assert "port: 5081" in cfg.read_text(encoding="utf-8")

        # 修改配置后回滚指定序号 v1
        cfg.write_text("gateway:\n  port: 9999\n", encoding="utf-8")
        src = backup_config.restore_config(cfg, index=1)
        assert src.name == "config_v1_test.yaml", f"指定回滚 v1: {src.name}"
        assert "port: 5080" in cfg.read_text(encoding="utf-8")

        # 回滚后配置仍可正常解析
        import yaml
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert data["gateway"]["port"] == 5080, "回滚后配置解析失败"
        print("[PASS] TC-B2 回滚 — 最新/指定序号均正确")


def test_list_and_errors():
    """TC-B3: 列表输出与错误处理"""
    import io
    import logging

    with tempfile.TemporaryDirectory() as td:
        backup_config.BACKUP_DIR = Path(td) / "backup"
        cfg = Path(td) / "test.yaml"
        cfg.write_text("gateway:\n  port: 5080\n", encoding="utf-8")

        # 无快照时 list 应提示
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        logging.getLogger().addHandler(handler)
        backup_config.list_snapshots(cfg)
        logging.getLogger().removeHandler(handler)
        assert "暂无快照" in buf.getvalue(), f"空列表提示缺失: {buf.getvalue()}"

        # 创建快照后 list 输出序号
        backup_config.backup_config(cfg)
        buf2 = io.StringIO()
        handler2 = logging.StreamHandler(buf2)
        logging.getLogger().addHandler(handler2)
        backup_config.list_snapshots(cfg)
        logging.getLogger().removeHandler(handler2)
        assert "config_v1" in buf2.getvalue(), f"列表应包含 v1: {buf2.getvalue()}"

        # 恢复不存在的序号 → 抛 FileNotFoundError
        try:
            backup_config.restore_config(cfg, index=99)
            raise AssertionError("应抛 FileNotFoundError（序号 99 不存在）")
        except FileNotFoundError:
            pass

        # backup 缺失文件 → 抛 FileNotFoundError
        try:
            backup_config.backup_config(Path(td) / "not_exist.yaml")
            raise AssertionError("应抛 FileNotFoundError（文件不存在）")
        except FileNotFoundError:
            pass

        # 无快照时 restore → 抛 FileNotFoundError
        try:
            backup_config.restore_config(Path(td) / "other.yaml")
            raise AssertionError("应抛 FileNotFoundError（无快照）")
        except FileNotFoundError:
            pass
        print("[PASS] TC-B3 列表与错误处理")


def run_all():
    tests = [
        ("TC-B1", test_backup_increment_and_cleanup),
        ("TC-B2", test_restore),
        ("TC-B3", test_list_and_errors),
    ]
    passed = 0
    failed = 0
    print("=" * 60)
    print("  Free API Hub — 配置快照测试")
    print("=" * 60)
    for tc_id, func in tests:
        try:
            func()
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {tc_id}: {e}")
            failed += 1
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
