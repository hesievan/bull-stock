#!/usr/bin/env python3
"""Step 5b: 按股票拉取 circ_mv (比逐日拉取快10倍)"""
import sys
import os
import time
import sqlite3
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.data.database import DB_PATH

_env_path = os.path.expanduser("~/daily_stock_analysis/.env")
if os.path.exists(_env_path):
    for line in open(_env_path):
        line = line.strip()
        if line.startswith("TUSHARE_TOKEN=") and not os.environ.get("TUSHARE_TOKEN"):
            os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1]
            break

import tushare as ts
pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])

conn = sqlite3.connect(DB_PATH)

# 获取 stock_daily 中所有股票及其日期范围
stocks = conn.execute("""
    SELECT stock_code, MIN(trade_date) as min_date, MAX(trade_date) as max_date
    FROM stock_daily
    GROUP BY stock_code
    ORDER BY stock_code
""").fetchall()
print(f"Stocks to update: {len(stocks)}", flush=True)

# 获取哪些股票/日期缺少 circ_mv
missing = conn.execute("""
    SELECT stock_code, trade_date FROM stock_daily
    WHERE circ_mv IS NULL OR circ_mv = 0
    ORDER BY stock_code, trade_date
""").fetchall()
print(f"Missing circ_mv rows: {len(missing)}", flush=True)

# 按股票分组缺失日期
from collections import defaultdict
stock_dates = defaultdict(list)
for code, dt in missing:
    stock_dates[code].append(dt)

print(f"Stocks needing update: {len(stock_dates)}", flush=True)
conn.close()

updated = 0
errors = 0
api_calls = 0

for i, (code, dates) in enumerate(sorted(stock_dates.items())):
    # 转换为 tushare ts_code
    if code.startswith("sh"):
        ts_code = code[2:] + ".SH"
    elif code.startswith("sz"):
        ts_code = code[2:] + ".SZ"
    else:
        continue

    min_date = dates[0].replace("-", "")
    max_date = dates[-1].replace("-", "")

    try:
        df = pro.daily_basic(ts_code=ts_code, start_date=min_date, end_date=max_date,
                             fields='trade_date,circ_mv,total_mv')
        api_calls += 1

        if not df.empty:
            conn = sqlite3.connect(DB_PATH)
            for _, row in df.iterrows():
                td = row['trade_date']
                if len(td) == 8:
                    td = f"{td[:4]}-{td[4:6]}-{td[6:]}"
                circ = row.get('circ_mv')
                tmv = row.get('total_mv')
                if pd.notna(circ) and circ > 0:
                    conn.execute(
                        "UPDATE stock_daily SET circ_mv=?, total_mv=? WHERE trade_date=? AND stock_code=?",
                        (float(circ), float(tmv) if pd.notna(tmv) else None, td, code)
                    )
                    updated += 1
            conn.commit()
            conn.close()

        if (i + 1) % 20 == 0:
            print(f"Progress: {i+1}/{len(stock_dates)} (api_calls: {api_calls}, updated: {updated})", flush=True)
        time.sleep(0.35)  # 200 calls/min limit

    except Exception as e:
        errors += 1
        if errors <= 10:
            print(f"  {code}: {str(e)[:80]}", flush=True)
        time.sleep(1)

# 验证
conn = sqlite3.connect(DB_PATH)
r = conn.execute("SELECT COUNT(*) FROM stock_daily WHERE circ_mv IS NOT NULL AND circ_mv > 0").fetchone()
r2 = conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()
print(f"circ_mv valid: {r[0]}/{r2[0]} ({r[0]/r2[0]*100:.1f}%)")
conn.close()
print(f"Done! API calls: {api_calls}, Updated: {updated}, Errors: {errors}", flush=True)
