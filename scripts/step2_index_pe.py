#!/usr/bin/env python3
"""
Step 2: index_pe_history — 指数PE/PB/总市值/换手率 (tushare index_dailybasic)
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

index_map = {
    "sh000300": "000300.SH",
    "sh000001": "000001.SH",
    "sh000905": "000905.SH",
    "sh000852": "000852.SH",
    "sz399006": "399006.SZ",
    "sz399001": "399001.SZ",
}

all_dfs = []
for ak_code, ts_code in index_map.items():
    try:
        df = pro.index_dailybasic(
            ts_code=ts_code,
            start_date='20150101',
            end_date='20260531',
            fields='trade_date,pe_ttm,pb,total_mv,turnover_rate,turnover_rate_f',
        )
        if not df.empty:
            df['index_code'] = ak_code
            all_dfs.append(df)
            print(f'  {ak_code}: {len(df)} rows')
        time.sleep(0.5)
    except Exception as e:
        print(f'  {ak_code}: ERROR {e}')

if all_dfs:
    all_data = pd.concat(all_dfs, ignore_index=True)
    all_data['trade_date'] = pd.to_datetime(all_data['trade_date']).dt.strftime('%Y-%m-%d')
    conn = sqlite3.connect(DB_PATH)
    all_data.to_sql('index_pe_tmp', conn, if_exists='replace', index=False)
    conn.execute("""
        INSERT OR REPLACE INTO index_pe_history (trade_date, index_code, pe_ttm, pb, total_mv, turnover_rate)
        SELECT trade_date, index_code, pe_ttm, pb, total_mv, turnover_rate FROM index_pe_tmp
    """)
    conn.execute("DROP TABLE index_pe_tmp")
    conn.commit()
    r = conn.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM index_pe_history").fetchone()
    print(f'index_pe_history: {r[0]} rows, {r[1]} ~ {r[2]}')
    # 验证沪深300 PE
    r2 = conn.execute("SELECT trade_date, pe_ttm, total_mv FROM index_pe_history WHERE index_code='sh000300' ORDER BY trade_date DESC LIMIT 3").fetchall()
    for row in r2:
        print(f'  HS300: {row[0]} PE={row[1]:.2f} MV={row[2]/1e4:.0f}万亿')
    conn.close()
else:
    print('No data fetched')
