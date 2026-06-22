#!/usr/bin/env python3
"""
Step 4: daily_basic → stock_market_cap + stock_daily PE/PB 更新
按交易日拉全市场快照，汇总总市值 + 补写PE/PB
"""
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

# 获取交易日列表 (从已有的 index_daily)
conn = sqlite3.connect(DB_PATH)
dates = [r[0] for r in conn.execute(
    "SELECT DISTINCT trade_date FROM index_daily WHERE trade_date >= '2015-01-01' ORDER BY trade_date"
).fetchall()]
conn.close()
print(f'Total trading days: {len(dates)} ({dates[0]} ~ {dates[-1]})')

# 按交易日拉 daily_basic
cap_rows = []
pe_rows = []
errors = 0
total_stocks = 0

for i, d in enumerate(dates):
    ds = d.replace('-', '')
    try:
        df = pro.daily_basic(
            trade_date=ds,
            fields='ts_code,total_mv,pe_ttm,pb',
        )
        if not df.empty:
            df['trade_date'] = d
            # 总市值汇总
            valid = df.dropna(subset=['total_mv'])
            valid = valid[valid['total_mv'] > 0]
            cap_rows.append({
                'trade_date': d,
                'total_mv': valid['total_mv'].sum(),
                'stock_count': len(valid),
            })
            # PE/PB 明细 → stock_daily
            for _, row in df.iterrows():
                tc = row.get('ts_code', '')
                if tc.endswith('.SH'):
                    ak_code = 'sh' + tc.replace('.SH', '')
                elif tc.endswith('.SZ'):
                    ak_code = 'sz' + tc.replace('.SZ', '')
                else:
                    continue
                pe = row.get('pe_ttm')
                pb = row.get('pb')
                if pd.notna(pe) and pe > 0:
                    pe_rows.append((float(pe), float(pb) if pd.notna(pb) and pb > 0 else None, d, ak_code))
            total_stocks += len(valid)

        if (i + 1) % 100 == 0:
            print(f'  Progress: {i+1}/{len(dates)} ({total_stocks} stocks so far, errors: {errors})')
        time.sleep(0.3)

    except Exception as e:
        errors += 1
        if errors <= 10:
            print(f'  {d}: ERROR {str(e)[:80]}')
        time.sleep(0.5)

# 保存 stock_market_cap
if cap_rows:
    cap_df = pd.DataFrame(cap_rows)
    conn = sqlite3.connect(DB_PATH)
    cap_df.to_sql('_tmp_mc', conn, if_exists='replace', index=False)
    conn.execute("""
        INSERT OR REPLACE INTO stock_market_cap (trade_date, total_mv, stock_count)
        SELECT trade_date, total_mv, stock_count FROM _tmp_mc
    """)
    conn.execute("DROP TABLE _tmp_mc")
    conn.commit()
    r = conn.execute("SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM stock_market_cap").fetchone()
    print(f'stock_market_cap: {r[0]} rows, {r[1]} ~ {r[2]}')
    r2 = conn.execute("SELECT trade_date, total_mv, stock_count FROM stock_market_cap ORDER BY trade_date DESC LIMIT 3").fetchall()
    for row in r2:
        print(f'  {row[0]}: {row[1]/1e4:.1f}万亿 ({row[2]} stocks)')
    conn.close()

# 保存 stock_daily PE/PB
if pe_rows:
    conn = sqlite3.connect(DB_PATH)
    # 批量更新 (只更新已有行)
    updated = 0
    for pe, pb, td, code in pe_rows:
        r = conn.execute(
            "UPDATE stock_daily SET peTTM=?, pbMRQ=? WHERE trade_date=? AND stock_code=?",
            (pe, pb, td, code)
        )
        updated += r.rowcount
    conn.commit()
    conn.close()
    print(f'stock_daily PE/PB updated: {updated} rows')

print(f'Done! Errors: {errors}')
