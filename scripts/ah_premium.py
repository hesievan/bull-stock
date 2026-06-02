#!/usr/bin/env python3
"""
AH股溢价指数计算器 (方案B: akshare H股 + baostock A股)

替代东方财富 push2his HSAHP 指数（已不可用）。
用 akshare stock_hk_daily 拿 H 股历史（HKD），
用 baostock 拿 A 股历史（CNY），
溢价 = A_close / H_close（CNY/HKD 比值，隐含汇率因子）。

绝对值与恒指 HSAHP 有系统偏差（汇率+等权vs市值加权），
但历史分位排名趋势完全一致，用于热度指数足够。

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

# 市值最大的15只AH股（覆盖金融+能源+材料主力）
AH_PAIRS = [
    ('01398', 'sh.601398', '工商银行'),
    ('01288', 'sh.601288', '农业银行'),
    ('00939', 'sh.601939', '建设银行'),
    ('03988', 'sh.601988', '中国银行'),
    ('03328', 'sh.601328', '交通银行'),
    ('02318', 'sh.601318', '中国平安'),
    ('02628', 'sh.601628', '中国人寿'),
    ('00386', 'sh.600028', '中国石化'),
    ('01088', 'sh.601088', '中国神华'),
    ('00857', 'sh.601857', '中国石油'),
    ('03968', 'sh.600036', '招商银行'),
    ('02899', 'sh.601899', '紫金矿业'),
    ('01618', 'sh.601618', '中国中冶'),
    ('00358', 'sh.600358', '江西铜业'),
    ('00941', 'sh.600941', '中国移动'),
]

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'heat_index.db')


def fetch_ah_premium_index(trade_date=None):
    """
    计算AH股溢价指数。
    返回: (trade_date_str, premium_float) 或 (None, None)
    premium = 所有 AH 股 (A_close/H_close) 的中位数
    """
    import akshare as ak
    import baostock as bs
    import pandas as pd

    if trade_date is None:
        # 最近交易日
        today = time.strftime('%Y-%m-%d')
        trade_date = today

    date_fmt = '%Y-%m-%d'
    td = trade_date.replace('-', '')  # YYYYMMDD for some calcs

    # 拉 H 股历史
    h_data = {}
    for h_code, a_code, name in AH_PAIRS:
        try:
            df = ak.stock_hk_daily(symbol=h_code, adjust='')
            df['date'] = pd.to_datetime(df['date']).dt.strftime(date_fmt)
            df['close'] = df['close'].astype(float)
            h_data[h_code] = df[['date', 'close']].rename(columns={'close': 'h_close'})
            time.sleep(0.15)
        except Exception as e:
            logger.warning("H股 %s(%s) 拉取失败: %s", name, h_code, e)

    # 拉 A 股历史
    bs.login()
    a_data = {}
    for h_code, a_code, name in AH_PAIRS:
        try:
            rs = bs.query_history_k_data_plus(
                a_code, 'date,close',
                start_date='2015-01-01', end_date=trade_date,
            )
            rows = []
            while rs.error_code == '0' and rs.next():
                rows.append(rs.get_row_data())
            if rows:
                df = pd.DataFrame(rows, columns=['date', 'close'])
                df['close'] = df['close'].astype(float)
                a_data[a_code] = df.rename(columns={'close': 'a_close'})
        except Exception as e:
            logger.warning("A股 %s(%s) 拉取失败: %s", name, a_code, e)
    bs.logout()

    # 合并算每日溢价
    all_ratios = {}
    for h_code, a_code, name in AH_PAIRS:
        if h_code not in h_data or a_code not in a_data:
            continue
        merged = h_data[h_code].merge(a_data[a_code], on='date', how='inner')
        if merged.empty:
            continue
        merged['ratio'] = merged['a_close'] / merged['h_close']
        for _, row in merged.iterrows():
            d = str(row['date'])
            all_ratios.setdefault(d, []).append(float(row['ratio']))

    if not all_ratios:
        logger.warning("AH溢价: 无有效数据")
        return None, None

    AH_PREMIUM_TABLE = 'ah_premium'
    conn = sqlite3.connect(DB_PATH)
    conn.execute(f'''CREATE TABLE IF NOT EXISTS {AH_PREMIUM_TABLE} (
        trade_date TEXT PRIMARY KEY,
        premium REAL,
        n_stocks INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')

    inserted = 0
    for d in sorted(all_ratios.keys()):
        vals = [v for v in all_ratios[d] if 0.3 < v < 3.0]
        if len(vals) < 5:
            continue
        med = float(np.median(vals))
        conn.execute(
            f'INSERT OR REPLACE INTO {AH_PREMIUM_TABLE} (trade_date, premium, n_stocks) VALUES (?,?,?)',
            (d, round(med, 4), len(vals))
        )
        inserted += 1

    # 返回 trade_date 对应值
    row = conn.execute(
        f'SELECT premium FROM {AH_PREMIUM_TABLE} WHERE trade_date=?', (trade_date,)
    ).fetchone()
    conn.commit()
    conn.close()

    premium_val = row[0] if row else None
    logger.info("AH溢价指数: %s -> %.4f (%d dates written)", trade_date, premium_val, inserted)
    return trade_date, premium_val


def backfill_history():
    """回填近1年所有交易日"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    # 获取已有数据
    existing = set()
    try:
        rows = conn.execute('SELECT trade_date FROM ah_premium').fetchall()
        existing = {r[0] for r in rows}
    except Exception:
        pass
    conn.close()

    # 用 baostock 的交易日历
    import baostock as bs
    import pandas as pd
    bs.login()
    rs = bs.query_trade_dates(start_date='2025-01-01', end_date='2026-06-01')
    dates = []
    while rs.error_code == '0' and rs.next():
        d = rs.get_row_data()[0]
        if d not in existing:
            dates.append(d)
    bs.logout()

    logger.info("需回填 %d 个交易日", len(dates))

    for d in dates:
        td, premium = fetch_ah_premium_index(d)
        if premium:
            logger.info("  %s: %.4f", td, premium)
        time.sleep(2)  # 限速


if __name__ == '__main__':
    if '--backfill' in sys.argv:
        backfill_history()
    else:
        date_arg = sys.argv[1] if len(sys.argv) > 1 else None
        td, premium = fetch_ah_premium_index(date_arg)
        if premium:
            pct = (premium - 1) * 100
            print(f"AH Premium Index: {premium:.4f} ({pct:+.1f}%)  [{td}]")
        else:
            print("FAILED")
