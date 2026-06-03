#!/usr/bin/env python3
"""
AH股溢价指数计算器 (方案B: akshare H股 + tushare A股)

用 akshare stock_hk_daily 拿 H 股历史（HKD），
用 tushare daily 拿 A 股历史（CNY），
溢价 = A股价格 / H股价格 的中位数。

用法:
  python scripts/ah_premium.py                    # 计算最新
  python scripts/ah_premium.py 2026-05-29         # 指定日期
  python scripts/ah_premium.py --backfill         # 回填历史
"""
import sys, os, logging, time
import sqlite3
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'heat_index.db')

# 15只核心AH股: H股代码 → tushare A股代码
AH_PAIRS = [
    ('01398', '601398.SH'), ('01288', '601288.SH'), ('00939', '601939.SH'),
    ('03988', '601988.SH'), ('03328', '601328.SH'), ('02318', '601318.SH'),
    ('02628', '601628.SH'), ('00386', '600028.SH'), ('01088', '601088.SH'),
    ('00857', '601857.SH'), ('03968', '600036.SH'), ('02899', '601899.SH'),
    ('01618', '601618.SH'), ('00358', '600358.SH'), ('00941', '600941.SH'),
]


def fetch_ah_premium_index(trade_date=None):
    """计算AH股溢价指数"""
    import akshare as ak
    import tushare as ts

    if trade_date is None:
        trade_date = time.strftime('%Y-%m-%d')

    token = os.environ.get('TUSHARE_TOKEN', '')
    if not token:
        _env = os.path.expanduser('~/daily_stock_analysis/.env')
        if os.path.exists(_env):
            for line in open(_env):
                if line.strip().startswith('TUSHARE_TOKEN='):
                    token = line.strip().split('=', 1)[1]
                    break
    pro = ts.pro_api(token)

    t0 = time.time()
    premiums = []
    failed = 0

    for h_code, a_ts_code in AH_PAIRS:
        try:
            # H股历史 (akshare)
            df_h = ak.stock_hk_daily(symbol=h_code, adjust="")
            df_h['date'] = pd.to_datetime(df_h['date']).dt.strftime('%Y-%m-%d')
            df_h['h_close'] = df_h['close'].astype(float)
            df_h = df_h[['date', 'h_close']]

            # A股历史 (tushare)
            ds = trade_date.replace('-', '')
            df_a = pro.daily(ts_code=a_ts_code, start_date='20150101', end_date=ds)
            time.sleep(0.15)
            if df_a is None or df_a.empty:
                failed += 1
                continue
            df_a['date'] = pd.to_datetime(df_a['trade_date'], format='%Y%m%d').dt.strftime('%Y-%m-%d')
            df_a['a_close'] = df_a['close'].astype(float)
            df_a = df_a[['date', 'a_close']]

            # 合并
            merged = df_h.merge(df_a, on='date', how='inner')
            if merged.empty:
                failed += 1
                continue

            merged['premium'] = merged['a_close'] / merged['h_close']
            latest = merged.iloc[-1]
            if -0.5 < latest['premium'] < 3.0:
                premiums.append(float(latest['premium']))
        except Exception:
            failed += 1
            continue

    if len(premiums) < 5:
        logger.warning("AH premium: 有效数据不足 (%d/15), failed=%d", len(premiums), failed)
        return None, None

    premium_val = float(np.median(premiums))
    logger.info("AH premium index: %.4f (n=%d, failed=%d, %.1fs)", premium_val, len(premiums), failed, time.time() - t0)

    # 写入数据库
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS ah_premium (
        trade_date TEXT PRIMARY KEY, premium REAL, n_stocks INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.execute(
        'INSERT OR REPLACE INTO ah_premium (trade_date, premium, n_stocks) VALUES (?,?,?)',
        (trade_date, round(premium_val, 4), len(premiums))
    )
    conn.commit()
    conn.close()

    return trade_date, premium_val


if __name__ == '__main__':
    import pandas as pd
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    td, premium = fetch_ah_premium_index(date_arg)
    if premium:
        print(f"AH Premium Index: {premium:.4f} [{td}]")
    else:
        print("FAILED")
