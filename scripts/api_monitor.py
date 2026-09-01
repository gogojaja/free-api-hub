#!/usr/bin/env python3
"""Free API Hub 综合监控工具

用法:
  api_monitor.py --check      # 快速检查各平台状态
  api_monitor.py --daily      # 生成今日最优方案
  api_monitor.py --rotate     # 轮换不可用模型
  api_monitor.py --status     # 查看缓存状态(无API调用)
  api_monitor.py --gateway    # 查看网关请求统计
"""
from __future__ import annotations
import sys, os, json, csv, re
from datetime import datetime
from typing import Dict, List
import urllib.request
import urllib.error

CACHE_FILE = "/tmp/fah_status.json"
CONFIG_PATH = "config/chat.yaml"
LOG_PATH = "data/chat.log"

STATE_ICON = {
    "OK": "✅", "NO_LOG": "○", "BALANCE_LOW": "❌",
    "RATE_LIMITED": "⚠️", "QUOTA_EXHAUSTED": "❌", "UNKNOWN": "?"
}


def load_config():
    import yaml
    with open(CONFIG_PATH, 'r') as f:
        return yaml.safe_load(f)


def classify_providers() -> Dict[str, List[str]]:
    data = load_config()
    result = {"free": [], "cheap": [], "paid": []}
    for p in data['providers']:
        name = p.get('name', '')
        tags = p.get('capabilities', {}).get('tags', [])
        cost = p.get('cost_per_mtok', 999) or 0
        if 'free' in tags:
            result["free"].append(name)
        elif 'cheap' in tags:
            result["cheap"].append(name)
        elif 'paid-as-you-go' in tags:
            result["paid"].append(name)
        elif cost == 0:
            result["free"].append(name)
        elif cost < 0.01:
            result["cheap"].append(name)
        else:
            result["paid"].append(name)
    return result


def quick_check() -> Dict:
    if os.path.exists(CACHE_FILE):
        mtime = os.path.getmtime(CACHE_FILE)
        if datetime.now().timestamp() - mtime < 3600:
            with open(CACHE_FILE) as f:
                return json.load(f)

    providers = classify_providers()["free"]
    status = {}

    if not os.path.exists(LOG_PATH):
        return {p: "NO_LOG" for p in providers}

    with open(LOG_PATH) as f:
        lines = f.readlines()[-200:]

    for p in providers:
        matches = [l for l in lines if p in l]
        if not matches:
            status[p] = "NO_LOG"
        elif any("402" in l for l in matches[-5:]):
            status[p] = "BALANCE_LOW"
        elif any("429" in l for l in matches[-5:]):
            status[p] = "RATE_LIMITED"
        elif any("403" in l for l in matches[-5:]):
            status[p] = "QUOTA_EXHAUSTED"
        elif any("200" in l or "success" in l.lower() for l in matches[-5:]):
            status[p] = "OK"
        else:
            status[p] = "UNKNOWN"

    with open(CACHE_FILE, 'w') as f:
        json.dump(status, f, indent=2)
    return status


def cmd_check():
    status = quick_check()
    print("免费Provider状态:")
    for p, s in sorted(status.items()):
        print(f"  {STATE_ICON.get(s, '?')} {p:<25} {s}")


def cmd_daily():
    status = quick_check()
    ok = [p for p, s in status.items() if s == "OK"]
    problems = [p for p, s in status.items() if s != "OK" and s != "NO_LOG"]

    print("=" * 50)
    print(f"今日最优方案 ({datetime.now().strftime('%Y-%m-%d')})")
    print("=" * 50)

    if ok:
        print("\n免费可用:")
        for p in ok:
            print(f"  - {p}")
    else:
        print("\n免费池: 全部不可用，需切换其他alias")

    print("\n低价备选 (fah/chat-cheap):")
    print("  - siliconflow-v4 (¥0.0015/M)")
    print("  - openrouter (¥0.03/M)")

    if problems:
        print("\n有问题需处理:")
        for p in problems:
            print(f"  - {p} → {status[p]}")

    print("\n路由建议:")
    print("  日常对话: fah/chat-free (自动选OK的provider)")
    print("  复杂任务: fah/chat-cheap")
    print("  百炼专用: fah/bailian")


def cmd_rotate():
    data = load_config()
    bailian = [p for p in data['providers'] if p['name'].startswith('bailian')]
    print("百炼可轮换模型 (已充值按量付费):")
    for i, p in enumerate(bailian[:10], 1):
        model = p.get('model', '?')
        name = p['name']
        print(f"  {i:2}. {name:<25} {model}")
    print(f"  ... 共{len(bailian)}个模型")


def cmd_status():
    if os.path.exists(CACHE_FILE):
        mtime = os.path.getmtime(CACHE_FILE)
        age = datetime.now().timestamp() - mtime
        with open(CACHE_FILE) as f:
            status = json.load(f)
        print(f"缓存时间: {datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')}")
        print(f"缓存时长: {age/60:.0f}分钟前")
        for p, s in status.items():
            print(f"  {STATE_ICON.get(s, '?')} {p}: {s}")
    else:
        print("无缓存，运行 --check 生成")


def cmd_gateway():
    try:
        req = urllib.request.Request("http://localhost:5080/metrics")
        with urllib.request.urlopen(req, timeout=5) as r:
            content = r.read().decode()
        total = {}
        for line in content.split('\n'):
            m = re.match(r'fah_requests_total\{provider="([^"]+)"\} (\d+)', line)
            if m and int(m.group(2)) > 0:
                total[m.group(1)] = int(m.group(2))
        if not total:
            print("无请求数据")
            return
        grand = sum(total.values())
        print(f"网关请求统计 (共{grand}次):")
        for p, c in sorted(total.items(), key=lambda x: -x[1])[:10]:
            print(f"  {p:<30} {c:>5} ({c*100/grand:.1f}%)")
    except Exception as e:
        print(f"网关未运行或无法连接: {e}")


def main():
    import argparse
    ap = argparse.ArgumentParser(description='API监控工具')
    ap.add_argument('--check', action='store_true', help='快速检查状态')
    ap.add_argument('--daily', action='store_true', help='生成今日方案')
    ap.add_argument('--rotate', action='store_true', help='列出可轮换模型')
    ap.add_argument('--status', action='store_true', help='查看缓存状态')
    ap.add_argument('--gateway', action='store_true', help='查看网关统计')
    args = ap.parse_args()
    if args.check:
        cmd_check()
    elif args.daily:
        cmd_daily()
    elif args.rotate:
        cmd_rotate()
    elif args.status:
        cmd_status()
    elif args.gateway:
        cmd_gateway()
    else:
        cmd_check()


if __name__ == '__main__':
    main()
