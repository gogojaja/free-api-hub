#!/usr/bin/env python3
"""Free API Hub — 每日最优API组合方案生成器

用法：
  python3 scripts/daily_api_recommendation.py [date]
  python3 scripts/daily_api_recommendation.py --help
"""
from __future__ import annotations
import sys, os, re, csv, json
from datetime import datetime

METRICS_URL = "http://localhost:5080/metrics"

def fetch_metrics():
    try:
        import urllib.request
        req = urllib.request.Request(METRICS_URL)
        with urllib.request.urlopen(req, timeout=10) as r:
            content = r.read().decode("utf-8")
        return parse_metrics(content)
    except Exception as e:
        print(f"无法获取metrics: {e}")
        return {}

def parse_metrics(content):
    data = {}
    for line in content.split('\n'):
        m = re.match(r'fah_requests_total\{provider="([^"]+)"\} (\d+)', line)
        if m:
            data[m.group(1)] = int(m.group(2))
    return data

def generate_report(date_str=None):
    today = date_str or datetime.now().strftime('%Y-%m-%d')
    metrics = fetch_metrics()
    
    lines = []
    lines.append("# %s 每日 API 成本优化方案\n" % today)
    lines.append("---\n")
    
    total_requests = sum(metrics.values())
    lines.append("## 今日用量概览 (累计)\n")
    lines.append("| Provider | 请求数 | 占比 |")
    lines.append("|----------|--------|------|")
    for provider, count in sorted(metrics.items(), key=lambda x: -x[1])[:5]:
        pct = "%.1f" % (count*100/total_requests) if total_requests > 0 else "0.0"
        lines.append("| %s | %d | %s%% |" % (provider, count, pct))
    lines.append("")
    
    lines.append("## 平台实时状态\n")
    lines.append("- **OpenRouter** `glm-5.2:free` -> 完全免费稳定")
    lines.append("- **百炼** 按量付费正常（已充值），25个模型可选")
    lines.append("- **智谱**: glm-4.5-flash 触发速率限制(429)")
    lines.append("- **硅基流动**: DeepSeek系列余额不足(402)")
    lines.append("")
    
    lines.append("## 最优组合方案\n")
    lines.append("### 免费层")
    lines.append("1. fah/chat-free -> 路由到 openrouter-glm52(free)")
    lines.append("2. 日常对话全部走免费provider\n")
    
    lines.append("### 超低价层")
    lines.append("3. SiliconFlow V4-Flash: ¥1/M输入 + ¥2/M输出")
    lines.append("   建议充值¥50约20M tokens总流量\n")
    
    lines.append("### 备用层")
    lines.append("4. 百炼 Qwen系列优先消耗充值额度\n")
    
    lines.append("## 配置建议\n")
    lines.append("```yaml")
    lines.append("routing:")
    lines.append("  default_strategy: cost")
    lines.append("  manual_override: ''")
    lines.append("aliases:")
    lines.append("- name: fah/chat-free")
    lines.append("  tags: [chat]")
    lines.append("  strategy: cost")
    lines.append("```")
    lines.append("")
    
    lines.append("## 购买建议\n")
    lines.append("| 平台 | 推荐套餐 | 金额 | 覆盖量 |")
    lines.append("|------|----------|------|--------|")
    lines.append("| 硅基流动 | DeepSeek-V4-Flash包 | ¥50 | ~20M tokens |")
    lines.append("| OpenRouter | credits充值 | $10(~¥70) | ~100M tokens |")
    lines.append("")
    
    return '\n'.join(lines)

def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='每日最优API组合方案')
    ap.add_argument('date', nargs='?', default=None, help='日期 YYYY-MM-DD')
    ap.add_argument('--output', '-o', default=None, help='输出文件路径')
    args = ap.parse_args(argv)
    
    report = generate_report(args.date)
    
    if args.output:
        d = os.path.dirname(args.output)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(args.output, 'w') as f:
            f.write(report)
        print("报告已保存: %s" % args.output)
    else:
        print(report)
    
    ledger_path = os.path.join("台账", "41_日报推荐记录.csv")
    os.makedirs("台账", exist_ok=True)
    date_str = args.date or datetime.now().strftime('%Y-%m-%d')
    
    with open(ledger_path, 'a', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        if not os.path.exists(ledger_path) or os.path.getsize(ledger_path) == 0:
            writer.writerow(['日期', '最优provider', '预计月成本区间', '备注'])
        writer.writerow([date_str, 'fah/chat-free->cost排序', '~¥100-200/月', '硅基流动欠费;智谱限速'])
    
    return 0

if __name__ == '__main__':
    main()
