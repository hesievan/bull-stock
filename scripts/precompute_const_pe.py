"""
预计算每日成分股 PE/PB 中位数
用分批 SQL 查询避免内存爆炸

策略:
  1. 从 index_constituents_hist 获取每月末成分股 (约 138 个截面)
  2. 对每个截面, 用 SQL 计算该成分股集合在每个交易日的 PE/PB 中位数
  3. 写入 index_daily_pe 表
"""
import sys
import logging
import sqlite3
import time
import pandas as pd

sys.path.insert(0, '.')
from src.data.database import DB_PATH

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS index_daily_pe (
            trade_date TEXT PRIMARY KEY,
            pe_med REAL,
            pb_med REAL,
            n_stocks INTEGER,
            const_date TEXT
        )
    ''')
    conn.commit()

    # 获取所有月末截面
    const_df = pd.read_sql('''
        SELECT trade_date as const_date, con_code
        FROM index_constituents_hist
        WHERE index_code IN ('hs300', 'zz500')
    ''', conn)

    const_dates = sorted(const_df['const_date'].unique())
    logger.info("Constituent dates: %d", len(const_dates))

    # 获取所有交易日
    trade_dates = pd.read_sql(
        "SELECT DISTINCT trade_date FROM stock_daily ORDER BY trade_date", conn
    )['trade_date'].tolist()
    logger.info("Trade dates: %d", len(trade_dates))

    # 为每个交易日找最近月末
    td_to_const = {}
    for td in trade_dates:
        td_cmp = td.replace('-', '')
        valid = [d for d in const_dates if d <= td_cmp]
        if valid:
            td_to_const[td] = max(valid)

    # 按截面分组
    from collections import defaultdict
    const_to_tds = defaultdict(list)
    for td, cd in td_to_const.items():
        const_to_tds[cd].append(td)

    logger.info("Groups: %d", len(const_to_tds))

    total = 0
    t0 = time.time()

    for cd, tds in sorted(const_to_tds.items()):
        codes = const_df[const_df['const_date'] == cd]['con_code'].tolist()
        if not codes:
            continue

        # 分批查询 (每批 200 个股票)
        min_td = min(tds)
        max_td = max(tds)

        all_pe = []
        batch_size = 200
        for i in range(0, len(codes), batch_size):
            batch = codes[i:i+batch_size]
            placeholders = ','.join(['?' for _ in batch])
            pe = pd.read_sql(f'''
                SELECT trade_date, peTTM, pbMRQ
                FROM stock_daily
                WHERE stock_code IN ({placeholders})
                  AND trade_date BETWEEN ? AND ?
            ''', conn, params=batch + [min_td, max_td])
            all_pe.append(pe)

        if not all_pe:
            continue

        pe_data = pd.concat(all_pe, ignore_index=True)
        pe_data['peTTM'] = pd.to_numeric(pe_data['peTTM'], errors='coerce')
        pe_data['pbMRQ'] = pd.to_numeric(pe_data['pbMRQ'], errors='coerce')

        for td in tds:
            day = pe_data[pe_data['trade_date'] == td]
            pe_vals = day['peTTM'][(day['peTTM'] > 0) & (day['peTTM'] <= 500)].dropna()
            pb_vals = day['pbMRQ'][(day['pbMRQ'] > 0) & (day['pbMRQ'] <= 10)].dropna()

            if len(pe_vals) > 0 or len(pb_vals) > 0:
                conn.execute('''
                    INSERT OR REPLACE INTO index_daily_pe
                    (trade_date, pe_med, pb_med, n_stocks, const_date)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    td,
                    float(pe_vals.median()) if len(pe_vals) > 0 else None,
                    float(pb_vals.median()) if len(pb_vals) > 0 else None,
                    len(pe_vals),
                    cd
                ))
                total += 1

        conn.commit()

        elapsed = time.time() - t0
        rate = total / elapsed if elapsed > 0 else 0
        logger.info("const_date=%s: %d dates, total=%d (%.1f/s)",
                     cd, len(tds), total, rate)

    # 验证
    stats = pd.read_sql('''
        SELECT COUNT(*) as total,
               COUNT(pe_med) as has_pe,
               COUNT(pb_med) as has_pb,
               MIN(trade_date) as first_date,
               MAX(trade_date) as last_date
        FROM index_daily_pe
    ''', conn)
    logger.info("Summary:\n%s", stats.to_string(index=False))

    # 2021-02-18
    check = pd.read_sql("SELECT * FROM index_daily_pe WHERE trade_date='2021-02-18'", conn)
    logger.info("2021-02-18: %s", check.to_string(index=False))

    conn.close()
    logger.info("Done: %d rows in %.1fs", total, time.time() - t0)

if __name__ == "__main__":
    main()
