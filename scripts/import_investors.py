#!/usr/bin/env python3
"""
新增投资者数据录入工具
数据来源: 中国结算(http://www.chinaclear.cn/) 月度统计
手动录入或 CSV 导入

用法:
  python scripts/import_investors.py                    # 查看当前数据
  python scripts/import_investors.py 2026-04 156.3      # 录入: 月份 新增万户
  python scripts/import_investors.py --from-csv investors.csv  # 从 CSV 导入
"""
import sys
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "heat_index.db")


def show_current():
    conn = conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT * FROM new_investors ORDER BY week_end_date DESC LIMIT 10").fetchall()
    print("当前新增投资者数据(最近10条):")
    for r in rows:
        print(f"  {r[0]}: {r[1]:.1f} 万户")
    conn.close()


def add_month(month_str: str, accounts: float):
    """录入单月数据 (month: YYYY-MM, 自动取月末日期)"""
    from datetime import date, timedelta
    parts = month_str.split("-")
    year, month = int(parts[0]), int(parts[1])
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    week_end = last_day.strftime("%Y-%m-%d")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO new_investors VALUES(?, ?)", (week_end, accounts))
    conn.commit()
    print(f"已写入: {week_end} ({month_str}) 新增 {accounts:.1f} 万户")
    conn.close()


def from_csv(csv_path: str):
    """CSV 格式: month(YYYY-MM),accounts(万户)"""
    import csv
    conn = sqlite3.connect(DB_PATH)
    count = 0
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            month = row.get("month", "").strip()
            accounts = float(row.get("accounts", 0))
            if month and accounts > 0:
                add_month(month, accounts)
                count += 1
    print(f"共导入 {count} 条记录")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        show_current()
    elif sys.argv[1] == "--from-csv" and len(sys.argv) >= 3:
        from_csv(sys.argv[2])
    elif len(sys.argv) >= 3:
        add_month(sys.argv[1], float(sys.argv[2]))
    else:
        print(__doc__)
