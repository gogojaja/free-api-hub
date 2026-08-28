#!/usr/bin/env python3
"""free-api-hub 模型 limit 校验/修正 CLI（零依赖）。

用法：
  python3 model_limits_cli.py check [provider [model] [--dev-slug SLUG]]
  python3 model_limits_cli.py update <provider> <model> --context N [--output N]

check  只读：按官方来源（models.dev / OpenRouter API）核对本地 limit 差异。
update 高危：修正本地 limit，自动备份全局配置 + 登记审计，须人工确认。
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tools_impl as T  # noqa: E402


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv):
    if not argv or argv[0] not in ("check", "update"):
        print(__doc__)
        return 2

    op = argv[0]
    args = [a for a in argv[1:] if not a.startswith("--")]
    opts = {}
    i = argv.index("--dev-slug") if "--dev-slug" in argv else -1
    if i >= 0 and i + 1 < len(argv):
        opts["dev_slug"] = argv[i + 1]
    if "--context" in argv:
        opts["context"] = int(argv[argv.index("--context") + 1])
    if "--output" in argv:
        opts["output"] = int(argv[argv.index("--output") + 1])

    if op == "check":
        provider = args[1] if len(args) > 1 else ""
        model = args[2] if len(args) > 2 else ""
        _print(T.check_model_limits(provider, model, opts.get("dev_slug", "")))
        return 0

    if len(args) < 3 or "context" not in opts and "output" not in opts:
        print("update 用法: model_limits_cli.py update <provider> <model> --context N [--output N]")
        return 2
    provider, model = args[1], args[2]
    _print(T.update_model_limit(provider, model, opts.get("context"), opts.get("output")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))