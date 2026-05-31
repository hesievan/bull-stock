#!/usr/bin/env python3
"""
Step 1: bond_yield — 2018~2026 国债收益率 (tushare yc_cb)
修复列名 yield -> yield_rate
"""
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

all_dfs = []
for year in range(2018, 2027):
    try:
        df = pro.yc_cb(start_date=f'{year}0101', end_date=f'{year}1231')
        if not df.empty:
            all_dfs.append(df)
            print(f'  {year}: {len(df)} rows')
        time.sleep(0.5)
    except Exception as e:
        print(f'  {year}: ERROR {e}')

if all_dfs:
    all_data = pd.concat(all_dfs, ignore_index=True)
    # 只保留国债 (curve_name 含 "国债")
    all_data = all_data[all_data['curve_name'].str.contains('国债', na=False)]
    # 只保留 <= 10年期限
    all_data = all_data[all_data['curve_term'] <= 10]
    # 重命名列
    all_data = all_data.rename(columns={'yield': 'yield_rate'})
    all_data['trade_date'] = pd.to_datetime(all_data['trade_date']).dt.strftime('%Y-%m-%d')
    save_cols = ['trade_date', 'curve_term', 'yield_rate']
    all_data = all_data[[c for c in save_cols if c in all_data.columns]]
    all_data = all_data.dropna(subset=['trade_date', 'yield_rate'])

    conn = sqlite3.connect(DB_PATH)
    all_data.to_sql('bond_yield_tmp', conn, if_exists='replace', index=False)
    conn.execute("""
        INSERT OR REPLACE INTO bond_yield (trade_date, curve_term, yield_rate)
        SELECT trade_date, curve_term, yield_rate FROM bond_yield_tmp
    """)
    conn.execute("DROP TABLE bond_yield_tmp")
    conn.commit()
    r = conn.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM bond_yield").fetchone()
    print(f'bond_yield: {r[0]} rows, {r[1]} ~ {r[2]}')
    conn.close()
else:
    print('No data fetched')
