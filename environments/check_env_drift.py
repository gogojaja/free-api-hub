#!/usr/bin/env python3
"""环境三态比对（防漂移）：20_环境配置.csv(登记态) ↔ IaC(期望态) ↔ 实际运行(现实态)。

用法：
  python3 check_env_drift.py            # 比对 登记态 ↔ IaC 期望态
  python3 check_env_drift.py --actual   # 额外比对 实际监听端口（macOS/linux 尽力而为）
退出码非 0 表示漂移（门禁阻断）。
"""
import csv
import os
import re
import sys
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(ROOT, "台账", "20_环境配置.csv")
NONPROD_COMPOSE = os.path.join(ROOT, "environments", "nonprod", "docker-compose.yml")
PROD_COMPOSE = os.path.join(ROOT, "environments", "prod", "docker-compose.yml")

# 生产侧外部依赖端口（不在 compose 内声明，允许登记态存在但 IaC 不声明）
EXTERNAL_ALLOW = {"prod": {5432}}


def load_csv_ports():
    dev, test, prod = set(), set(), set()
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f)
        header = next(r)
        i_dev, i_test, i_prod = 3, 4, 5
        for row in r:
            for s, bucket in ((row[i_dev], dev), (row[i_test], test), (row[i_prod], prod)):
                m = re.match(r"^\s*(\d+)", s or "")
                if m:
                    bucket.add(int(m.group(1)))
    return dev | test, prod  # nonprod = dev∪test


def load_compose_ports(path):
    ports = set()
    if not os.path.exists(path):
        return ports
    text = open(path, encoding="utf-8").read()
    for m in re.finditer(r'-\s*"(\d+):\d+"', text):
        ports.add(int(m.group(1)))
    return ports


def load_actual_ports():
    """尽力而为：macOS 用 lsof，Linux 用 /proc/net/tcp。失败返回 None。"""
    try:
        out = subprocess.run(["lsof", "-iTCP", "-sTCP:LISTEN", "-nP"],
                             capture_output=True, text=True, timeout=10).stdout
        ports = set()
        for line in out.splitlines()[1:]:
            m = re.search(r":(\d+)\s", line)
            if m:
                ports.add(int(m.group(1)))
        return ports
    except Exception:
        return None


def main():
    nonprod_csv, prod_csv = load_csv_ports()
    nonprod_decl = load_compose_ports(NONPROD_COMPOSE)
    prod_decl = load_compose_ports(PROD_COMPOSE)

    drift = False
    print("== 环境三态比对 ==")
    print(f"[nonprod] 登记态端口: {sorted(nonprod_csv)}")
    print(f"[nonprod] IaC声明端口: {sorted(nonprod_decl)}")
    d1 = nonprod_decl - nonprod_csv
    d2 = nonprod_csv - nonprod_decl
    if d1 or d2:
        drift = True
        print(f"  ❌ 漂移: IaC多 {sorted(d1)} / 登记多 {sorted(d2)}")
    else:
        print("  ✅ nonprod 一致")

    print(f"[prod] 登记态端口: {sorted(prod_csv)}")
    print(f"[prod] IaC声明端口: {sorted(prod_decl)}")
    pd1 = prod_decl - prod_csv
    pd2 = (prod_csv - prod_decl) - EXTERNAL_ALLOW["prod"]
    if pd1 or pd2:
        drift = True
        print(f"  ❌ 漂移: IaC多 {sorted(pd1)} / 登记多(非外部) {sorted(pd2)}")
    else:
        print("  ✅ prod 一致（外部依赖 5432 已豁免）")

    if "--actual" in sys.argv:
        actual = load_actual_ports()
        if actual is None:
            print("[actual] 跳过（当前平台无 lsof/权限）")
        else:
            expected_all = nonprod_csv | prod_csv
            missing = expected_all - actual - EXTERNAL_ALLOW["prod"]
            print(f"[actual] 监听端口: {sorted(actual)}")
            if missing:
                print(f"  ⚠️ 登记端口未在运行态监听（环境未启动属正常）: {sorted(missing)}")
            else:
                print("  ✅ 运行态覆盖登记端口")

    print("结果:", "❌ 存在漂移（门禁阻断）" if drift else "✅ 无漂移")
    sys.exit(1 if drift else 0)


if __name__ == "__main__":
    main()
