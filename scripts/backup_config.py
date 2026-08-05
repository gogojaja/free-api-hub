#!/usr/bin/env python3
"""
Free API Hub — 配置文件快照管理脚本 (NEW-004)

提供三个子命令：
  backup  <config_path>  手动创建配置快照（备份至 backup/config_v<N>_<name>.yaml）
  restore <config_path>  从最新快照回滚配置
  list    <config_path>  列出该配置的全部快照

快照保留策略：每个配置文件保留最近 MAX_BACKUPS（默认 10）份。
"""
import argparse
import logging
import re
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
BACKUP_DIR = BASE_DIR / "backup"
MAX_BACKUPS = 10

logger = logging.getLogger(__name__)


def _init_logging(debug=False):
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="[%(levelname)s] %(message)s",
    )


def _snapshot_name(config_path: Path, index: int) -> str:
    """生成快照文件名：config_v<N>_<name>.yaml"""
    return f"config_v{index}_{config_path.stem}.yaml"


def _existing_snapshots(config_path: Path):
    """返回该配置现有快照的 (序号, 完整路径) 列表，按序号升序"""
    pattern = re.compile(rf"^config_v(\d+)_{re.escape(config_path.stem)}\.yaml$")
    found = []
    if BACKUP_DIR.exists():
        for f in BACKUP_DIR.glob(f"config_v*_{config_path.stem}.yaml"):
            m = pattern.match(f.name)
            if m:
                found.append((int(m.group(1)), f))
    return sorted(found, key=lambda item: item[0])


def backup_config(config_path: Path, debug=False) -> Path:
    """创建配置快照，返回快照路径；旧快照超出上限时清理最旧的一份"""
    _init_logging(debug)
    config_path = config_path.resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    existing = _existing_snapshots(config_path)
    next_index = (existing[-1][0] + 1) if existing else 1
    target = BACKUP_DIR / _snapshot_name(config_path, next_index)

    shutil.copy2(config_path, target)
    logger.info(f"✅ 已创建快照: {target}")

    if len(existing) >= MAX_BACKUPS:
        oldest_idx, oldest_path = existing[0]
        oldest_path.unlink()
        logger.info(f"🧹 超上限({MAX_BACKUPS})，清理最旧快照: {oldest_path}")
    return target


def restore_config(config_path: Path, index=None, debug=False):
    """从指定序号（缺省为最新）快照回滚配置，返回回滚来源快照路径"""
    _init_logging(debug)
    config_path = config_path.resolve()
    existing = _existing_snapshots(config_path)
    if not existing:
        raise FileNotFoundError(f"未找到 {config_path.stem} 的任何快照，无法回滚")

    if index is None:
        snap_idx, snap_path = existing[-1]
    else:
        match = [item for item in existing if item[0] == index]
        if not match:
            raise FileNotFoundError(
                f"未找到序号 {index} 的快照，现有序号: "
                f"{', '.join(str(i) for i, _ in existing)}"
            )
        snap_idx, snap_path = match[0]

    config_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(snap_path, config_path)
    logger.info(f"✅ 已从快照 config_v{snap_idx} 回滚: {config_path}")
    return snap_path


def list_snapshots(config_path: Path, debug=False):
    """列出该配置的全部快照"""
    _init_logging(debug)
    config_path = config_path.resolve()
    existing = _existing_snapshots(config_path)
    if not existing:
        logger.info(f"ℹ️  {config_path.stem} 暂无快照")
        return
    logger.info(f"📋 {config_path.name} 快照列表（共 {len(existing)} 份）:")
    for idx, path in existing:
        size = path.stat().st_size
        logger.info(f"  config_v{idx}: {path.name} ({size} bytes)")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Free API Hub 配置快照管理")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_backup = sub.add_parser("backup", help="创建配置快照")
    p_backup.add_argument("config", help="配置文件路径（如 config/chat.yaml）")

    p_restore = sub.add_parser("restore", help="从快照回滚配置")
    p_restore.add_argument("config", help="配置文件路径（如 config/chat.yaml）")
    p_restore.add_argument("--index", type=int, default=None,
                           help="快照序号（默认最新一份）")

    p_list = sub.add_parser("list", help="列出配置快照")
    p_list.add_argument("config", help="配置文件路径（如 config/chat.yaml）")

    parser.add_argument("--debug", action="store_true", help="输出 DEBUG 日志")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "backup":
            backup_config(Path(args.config), debug=args.debug)
        elif args.cmd == "restore":
            restore_config(Path(args.config), index=args.index, debug=args.debug)
        elif args.cmd == "list":
            list_snapshots(Path(args.config), debug=args.debug)
    except (FileNotFoundError, OSError) as e:
        logger.error(f"❌ {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
