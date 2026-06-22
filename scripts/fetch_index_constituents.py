"""
拉取沪深300+中证500成分股列表, 写入 index_constituents 表
用于 PE/PB 分位数计算时过滤 (方案B: 仅计算成分股PE分位)
"""
import sys
import logging
import sqlite3
import pandas as pd

sys.path.insert(0, '.')
from src.data.fetcher import fetch_index_constituents
from src.data.database import DB_PATH

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def create_table(conn):
    conn.execute('''
        CREATE TABLE IF NOT EXISTS index_constituents (
            index_code TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            update_date TEXT,
            PRIMARY KEY (index_code, stock_code)
        )
    ''')
    conn.commit()

def main():
    import baostock as bs
    conn = sqlite3.connect(DB_PATH)
    create_table(conn)

    # baostock 需要先登录
    lg = bs.login()
    if lg.error_code != '0':
        logger.error("baostock login failed: %s", lg.error_msg)
        conn.close()
        return
    logger.info("baostock login ok")

    total = 0
    for idx_name, label in [('hs300', '沪深300'), ('zz500', '中证500')]:
        logger.info("拉取 %s 成分股...", label)
        df = fetch_index_constituents(idx_name)
        if df.empty:
            logger.warning("%s 成分股为空!", label)
            continue

        # 取最新日期的数据
        if 'date' in df.columns:
            df = df.sort_values('date').drop_duplicates('code', keep='last')

        n = 0
        for _, row in df.iterrows():
            code = row.get('code', '')
            name = row.get('code_name', '')
            update = row.get('date', '')
            if not code:
                continue
            conn.execute(
                'INSERT OR REPLACE INTO index_constituents (index_code, stock_code, stock_name, update_date) VALUES (?,?,?,?)',
                (idx_name, code, name, str(update))
            )
            n += 1
        conn.commit()
        total += n
        logger.info("  %s: %d 只成分股写入", label, n)

    # 统计
    stats = pd.read_sql('''
        SELECT index_code, COUNT(*) as cnt
        FROM index_constituents
        GROUP BY index_code
    ''', conn)
    logger.info("成分股表统计:")
    print(stats.to_string(index=False))

    # 验证: 2021-02-18 有多少成分股在 stock_daily 中有 PE
    check = pd.read_sql('''
        SELECT ic.index_code, COUNT(DISTINCT ic.stock_code) as constituents,
               COUNT(DISTINCT CASE WHEN sd.peTTM > 0 THEN sd.stock_code END) as has_pe
        FROM index_constituents ic
        LEFT JOIN stock_daily sd ON sd.stock_code = ic.stock_code AND sd.trade_date = '2021-02-18'
        GROUP BY ic.index_code
    ''', conn)
    logger.info("2021-02-18 成分股PE覆盖:")
    print(check.to_string(index=False))

    conn.close()
    logger.info("完成: %d 行写入 index_constituents", total)

if __name__ == "__main__":
    main()
