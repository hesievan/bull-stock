#!/usr/bin/env python3
"""Step 3: northbound_history — 全量北向资金 (2015~2026)"""
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

all_dfs = []
for year in range(2015, 2027):
    try:
        df = pro.moneyflow_hsgt(start_date=f'{year}0101', end_date=f'{year}1231')
        if not df.empty:
            all_dfs.append(df)
            print(f'  {year}: {len(df)} rows')
        time.sleep(0.3)
    except Exception as e:
        print(f'  {year}: ERROR {e}')

if all_dfs:
    all_data = pd.concat(all_dfs, ignore_index=True)
    all_data['trade_date'] = pd.to_datetime(all_data['trade_date']).dt.strftime('%Y-%m-%d')
    all_data['north_net'] = (
        pd.to_numeric(all_data.get('hgt', 0), errors='coerce').fillna(0)
        + pd.to_numeric(all_data.get('sgt', 0), errors='coerce').fillna(0)
    )
    keep = ['trade_date', 'hgt', 'sgt', 'north_net', 'south_money']
    all_data = all_data[[c for c in keep if c in all_data.columns]]

    conn = sqlite3.connect(DB_PATH)
    all_data.to_sql('_tmp_nb', conn, if_exists='replace', index=False)
    conn.execute("""
        INSERT OR REPLACE INTO northbound_history (trade_date, hgt, sgt, north_net, south_money)
        SELECT trade_date, hgt, sgt, north_net, south_money FROM _tmp_nb
    """)
    conn.execute("DROP TABLE _tmp_nb")
    conn.commit()
    r = conn.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM northbound_history").fetchone()
    print(f'northbound_history: {r[0]} rows, {r[1]} ~ {r[2]}')
    conn.close()
else:
    print('No data fetched')
