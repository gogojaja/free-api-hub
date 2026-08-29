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
PLATFORMS = [
    {"name": "openrouter", "url": "https://openrouter.ai/api/v1/models", "field": "data"},
    {"name": "models.dev", "url": "https://models.dev/api.json", "field": ""},
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
    return 0


if __name__ == "__main__":
    sys.exit(main())