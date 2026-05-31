#!/usr/bin/env python3
"""Step 5: 补写 stock_daily circ_mv (from tushare daily_basic)"""
import sys, os, time, sqlite3
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
dates = [r[0] for r in conn.execute(
    "SELECT DISTINCT trade_date FROM stock_daily WHERE circ_mv IS NULL OR circ_mv = 0 ORDER BY trade_date"
).fetchall()]
conn.close()
print(f'Dates needing circ_mv: {len(dates)}')

updated = 0
errors = 0
for i, d in enumerate(dates):
    ds = d.replace('-', '')
    try:
        df = pro.daily_basic(trade_date=ds, fields='ts_code,circ_mv,total_mv')
        if not df.empty:
            df['trade_date'] = d
            conn = sqlite3.connect(DB_PATH)
            for _, row in df.iterrows():
                tc = row.get('ts_code', '')
                if tc.endswith('.SH'):
                    ak_code = 'sh' + tc.replace('.SH', '')
                elif tc.endswith('.SZ'):
                    ak_code = 'sz' + tc.replace('.SZ', '')
                else:
                    continue
                circ = row.get('circ_mv')
                tmv = row.get('total_mv')
                if pd.notna(circ) and circ > 0:
                    conn.execute(
                        "UPDATE stock_daily SET circ_mv=?, total_mv=? WHERE trade_date=? AND stock_code=?",
                        (float(circ), float(tmv) if pd.notna(tmv) else None, d, ak_code)
                    )
                    updated += 1
            conn.commit()
            conn.close()
        if (i + 1) % 100 == 0:
            print(f'  Progress: {i+1}/{len(dates)} (updated: {updated})')
        time.sleep(0.3)
    except Exception as e:
        errors += 1
        if errors <= 5:
            print(f'  {d}: {str(e)[:80]}')
        time.sleep(0.5)

# 验证
conn = sqlite3.connect(DB_PATH)
r = conn.execute("SELECT COUNT(*) FROM stock_daily WHERE circ_mv IS NOT NULL AND circ_mv > 0").fetchone()
print(f'stock_daily circ_mv valid: {r[0]} rows')
conn.close()
print(f'Done! Updated: {updated}, Errors: {errors}')
