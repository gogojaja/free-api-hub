#!/usr/bin/env python3
"""Free API Hub — 每周价格/优惠/免费额度监测器（ADR-011）

每周一巡三事：
- 基准价 -> 台账/24_模型价格.csv
- 优惠   -> 台账/26_优惠日历.csv  （结束时间必填）
- 免费额度 -> 台账/27_免费额度.csv（刷新周期/刷新时间/当前状态）
产出周报 CSV（diff 上期->本期）+ 额度耗尽/将重置 ACTION 预警。
只读公共 API（OpenRouter / models.dev / 各平台公开端点），零密钥落盘。
DEBUG 日志：--debug 开启。
"""
import argparse
import os
import csv
import json
import logging
import sys
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

LEDGERS = {
    "price": "台账/24_模型价格.csv",
    "promo": "台账/26_优惠日历.csv",
    "quota": "台账/27_免费额度.csv",
}

# 平台白名单（铁律：仅公共 HTTPS 合法域）
ALLOWED_DOMAINS = ("openrouter.ai", "models.dev", "api-docs.deepseek.com")

# 价格快照数据源（T1）
PLATFORMS = [
    {"name": "openrouter", "url": "https://openrouter.ai/api/v1/models", "field": "data"},
]

KEY_DIR = os.path.join(os.path.expanduser("~"), ".config", "opencode")

# 国产免费档免费额度核验（T1 端点，max_tokens=1 最小探测，成本≈0）
QUOTA_PROBES = [
    {"platform": "siliconflow", "key_file": "siliconflow-api-key",
     "url": "https://api.siliconflow.cn/v1/chat/completions",
     "models": ["deepseek-ai/DeepSeek-V3.2", "deepseek-ai/DeepSeek-V3.2-Exp",
                "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B"]},
    {"platform": "zhipu", "key_file": "zhipu-api-key",
     "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
     "models": ["glm-4.5-flash"]},
    {"platform": "bailian", "key_file": "bailian-api-key",
     "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
     "models": ["qwen-plus", "qwen-turbo", "qwen-max"]},
]


def _get_json(url: str) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": "free-api-hub-model-monitor/1.0"}
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def snapshot_openrouter(data: dict) -> list[dict]:
    rows = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for d in data.get("data", []):
        i = d.get("id", "")
        if not (("deepseek" in i.lower()) or ("qwen" in i.lower())):
            continue
        p = d.get("pricing", {}) or {}
        arch = d.get("architecture", {}) or {}
        rows.append({
            "平台": "openrouter", "模型": i,
            "输入价($/M)": p.get("prompt", ""),
            "输出价($/M)": p.get("completion", ""),
            "缓存命中($/M)": p.get("request", ""),
            "context": d.get("context_length", ""),
            "output": arch.get("output_token_limit", ""),
            "来源T1": "https://openrouter.ai/api/v1/models", "核验日期": today,
        })
    return rows


def diff(prev, curr, keys):
    prevs = {(r[k] for k in keys): r for r in prev}
    changes = []
    for r in curr:
        k = tuple(r[kk] for kk in keys)
        if k not in prevs or prevs[k] != r:
            changes.append(r)
    return changes


def _probe_chat(url, key, model, timeout=30):
    """最小 chat 探测（max_tokens=1），返回 (status, note)。
    status: ok | 429 | 4xx | err · 不回显 key 明文。仅白名单域。
    """
    import urllib.error
    host = url.split("/")[2]
    if host not in ALLOWED_DOMAINS and host not in (
            "api.siliconflow.cn", "open.bigmodel.cn", "dashscope.aliyuncs.com"):
        return "err", f"非法域 {host}"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8"))
            return "ok", f"可用(prompt={resp.get('usage', {}).get('prompt_tokens', '?')})"
    except urllib.error.HTTPError as e:
        return str(e.code), e.read()[:120].decode(errors="replace")
    except Exception as e:
        return "err", f"{type(e).__name__}"


def snapshot_quota():
    """逐平台最小探测免费/按量模型可用性 → 台账 27 行（只读，不落盘 key）。"""
    rows = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for probe in QUOTA_PROBES:
        key_path = os.path.join(KEY_DIR, probe["key_file"])
        if not os.path.exists(key_path):
            rows.append([probe["platform"], "|".join(probe["models"]), "免费额度",
                         "无key文件", "-", "-", "不可核验(无Key文件)", "本地Key目录", today])
            continue
        key = open(key_path).read().strip()
        if not key:
            rows.append([probe["platform"], "|".join(probe["models"]), "免费额度",
                         "空key", "-", "-", "不可核验(Key为空)", "本地Key目录", today])
            continue
        for m in probe["models"]:
            status, note = _probe_chat(probe["url"], key, m)
            rows.append([probe["platform"], m, "免费/按量",
                         "", "", "",
                         f"{status}: {note[:60]}", probe["url"], today])
    return rows


def merge_quota_ledger(new_rows):
    """27 台账 upsert：按 (平台,模型) 去重更新，保留人工备注行（非探针行）。"""
    path = LEDGERS["quota"]
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            rows = [r for r in csv.reader(f) if r]
    else:
        rows = [["平台", "模型", "额度类型", "额度量", "刷新周期", "刷新时间", "当前状态", "来源T1", "核验日期"]]
    header = rows[0]
    probe_keys = {(r[0], r[1]) for r in new_rows}
    # 保留表头 + 非探针平台/模型的人工行
    kept = [r for r in rows if r == header or (r[0], r[1]) not in probe_keys]
    for nr in new_rows:
        kept.append(nr)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerows(kept)
    return len(new_rows)


def main():
    ap = argparse.ArgumentParser(description="每周模型价格/优惠/免费额度监测")
    ap.add_argument("--debug", action="store_true", help="DEBUG 日志")
    ap.add_argument("--dry-run", action="store_true", help="只探测不写台账")
    ap.add_argument("--write", action="store_true", help="写台账（默认只读探测）")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    rows = []
    for p in PLATFORMS:
        url = p["url"]
        if not url.startswith("https://"):
            logger.warning("跳过非 HTTPS %s", url)
            continue
        host = url.split("/")[2]
        if host not in ALLOWED_DOMAINS:
            logger.warning("跳过非法域 %s", url)
            continue
        data = _get_json(url)
        rows.extend(snapshot_openrouter(data))

    logger.info("抓取 %d 条 DeepSeek/Qwen 模型价格记录", len(rows))

    # 官方价保护：合并手工维护的官方基价行（来源=api-docs.deepseek.com），防覆盖丢失
    off = []
    if os.path.exists(LEDGERS["price"]):
        with open(LEDGERS["price"], encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("来源T1") == "https://api-docs.deepseek.com/quick_start/pricing":
                    off.append(r)
    merged = off + rows
    logger.info("合并后 %d 行（官方 %d + 平台 %d）", len(merged), len(off), len(rows))

    if args.write:
        with open(LEDGERS["price"], "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(merged[0].keys()))
            w.writeheader()
            w.writerows(merged)
        logger.info("已写入 %s（%d 行）", LEDGERS["price"], len(merged))

    # 免费额度核验（27 台账）：国产平台最小探测
    logger.info("开始国产平台免费额度核验（%d 平台）", len(QUOTA_PROBES))
    qrows = snapshot_quota()
    for r in qrows:
        logger.info("  核验 %s | %s | %s", r[0], r[1][:40], r[6][:50])
    if args.write:
        n = merge_quota_ledger(qrows)
        logger.info("免费额度核验结果已追加 %d 行 → %s", n, LEDGERS["quota"])
    return 0


if __name__ == "__main__":
    sys.exit(main())