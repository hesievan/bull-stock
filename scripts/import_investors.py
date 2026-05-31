#!/usr/bin/env python3
"""
新增投资者数据录入工具
数据来源: GitHub(hesievan/stock) data/raw/account_open_user.csv (月度, 中国结算)
也可手动录入

用法:
  python scripts/import_investors.py                          # 查看当前数据
  python scripts/import_investors.py --from-github             # 从 GitHub CSV 导入全量历史
  python scripts/import_investors.py --from-csv <path>        # 从本地 CSV 导入
  python scripts/import_investors.py 2026-04 269.19            # 录入单月(万户)

CSV 格式: date(YYYY/M/D),value(户数)
"""
import sys
import os
import csv
import sqlite3
from datetime import date, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "heat_index.db")
GITHUB_CSV_URL = "https://raw.githubusercontent.com/hesievan/stock/main/data/raw/account_open_user.csv"


def parse_date(s):
    """YYYY/M/D -> YYYY-MM-DD (取月末)"""
    parts = s.strip().split('/')
    year, month = int(parts[0]), int(parts[1])
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    return last_day.strftime("%Y-%m-%d")


def show_current():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT * FROM new_investors ORDER BY week_end_date DESC LIMIT 10").fetchall()
    total = conn.execute("SELECT COUNT(*) FROM new_investors").fetchone()[0]
    print("新增投资者数据 (最近10条 / 共{}条):".format(total))
    for r in rows:
        print("  {}  {:.1f} 万户".format(r[0], r[1]))
    conn.close()


def from_github():
    import urllib.request
    print("从 GitHub 下载数据...")
    with urllib.request.urlopen(GITHUB_CSV_URL) as resp:
        content = resp.read().decode('utf-8')
    lines = content.strip().split('\n')
    reader = csv.DictReader(lines)
    conn = sqlite3.connect(DB_PATH)
    count = 0
    for row in reader:
        d = row.get('date', '').strip()
        v = row.get('value', '').strip()
        if not d or not v:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO new_investors VALUES(?, ?)",
            (parse_date(d), float(v) / 10000)
        )
        count += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM new_investors").fetchone()[0]
    print("导入 {} 条, 数据库共 {} 条".format(count, total))
    conn.close()


def from_csv(csv_path):
    conn = sqlite3.connect(DB_PATH)
    count = 0
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = row.get('date', '').strip()
            v = row.get('value', '').strip()
            if not d or not v:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO new_investors VALUES(?, ?)",
                (parse_date(d), float(v) / 10000)
            )
            count += 1
    conn.commit()
    print("导入 {} 条".format(count))
    conn.close()


def add_month(month_str, accounts):
    """录入单月数据 (month: YYYY-MM, accounts: 万户)"""
    parts = month_str.split("-")
    year, month = int(parts[0]), int(parts[1])
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO new_investors VALUES(?, ?)",
                 (last_day.strftime("%Y-%m-%d"), accounts))
    conn.commit()
    print("已写入: {}  {:.1f} 万户".format(last_day.strftime("%Y-%m-%d"), accounts))
    conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        show_current()
    elif sys.argv[1] == "--from-github":
        from_github()
    elif sys.argv[1] == "--from-csv" and len(sys.argv) >= 3:
        from_csv(sys.argv[2])
    elif len(sys.argv) >= 3:
        add_month(sys.argv[1], float(sys.argv[2]))
    else:
        print(__doc__)
