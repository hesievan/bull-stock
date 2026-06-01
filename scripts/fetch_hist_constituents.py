"""
拉取沪深300+中证500历史成分股, 写入 index_constituents 表
使用 tushare index_weight 接口 (每月末截面数据)

策略:
  - 每月拉取月末交易日成分股
  - 沪深300: 000300.SH
  - 中证500: 000905.SH
  - 时间范围: 2015-01 ~ 2026-06 (约138个月 × 2指数 = 276次API调用)
  - tushare 频率: 200次/分钟, 预计 ~2分钟

写入 index_constituents_rolling 表 (区别于当前成分股表)
  - 字段: index_code, con_code, trade_date (每月末日期)
"""
import sys, os, time, logging
import sqlite3
import pandas as pd
import tushare as ts
from datetime import date, timedelta
import calendar

sys.path.insert(0, '.')
from src.data.database import DB_PATH

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

TOKEN = os.environ.get('TUSHARE_TOKEN', '473bc93a521c11cac2f5136b08bccbcb819d220fcee5d8f04b389577')
INDEX_MAP = {
    '000300.SH': 'hs300',
    '000905.SH': 'zz500',
}

def create_table(conn):
    conn.execute('''
        CREATE TABLE IF NOT EXISTS index_constituents_hist (
            index_code TEXT NOT NULL,
            con_code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            weight REAL,
            PRIMARY KEY (index_code, con_code, trade_date)
        )
    ''')
    conn.commit()

def get_month_end_dates(pro, start_year=2015, end_year=2026):
    """获取每月最后一个交易日的日期"""
    dates = []
    # 用 tushare 交易日历
    try:
        cal = pro.trade_cal(exchange='SSE', start_date=f'{start_year}0101', end_date=f'{end_year}0630')
        cal = cal[cal['is_open'] == 1]
        for y in range(start_year, end_year + 1):
            for m in range(1, 13):
                if y == end_year and m > 6:
                    break
                month_cal = cal[(cal['cal_date'].str.startswith(f'{y}{m:02d}'))]
                if not month_cal.empty:
                    last_day = month_cal['cal_date'].max()
                    dates.append(last_day)
    except Exception as e:
        logger.warning("trade_cal failed, using calendar: %s", str(e)[:80])
        for y in range(start_year, end_year + 1):
            for m in range(1, 13):
                if y == end_year and m > 6:
                    break
                last_day = calendar.monthrange(y, m)[1]
                dates.append(f"{y}{m:02d}{last_day:02d}")
    return dates

def fetch_one_month(pro, index_code, trade_date, conn):
    """拉取单月成分股并写入"""
    try:
        df = pro.index_weight(index_code=index_code, start_date=trade_date, end_date=trade_date)
        if df is None or df.empty:
            return 0

        idx_name = INDEX_MAP.get(index_code, index_code)
        # 转换 con_code 到 akshare 格式
        def to_ak_code(ts_code):
            # 600519.SH -> sh600519
            if '.' in ts_code:
                code, exchange = ts_code.split('.')
                prefix = 'sh' if exchange == 'SH' else 'sz'
                return f"{prefix}{code}"
            return ts_code

        n = 0
        for _, row in df.iterrows():
            con_code = to_ak_code(row['con_code'])
            weight = row.get('weight', 0)
            conn.execute(
                'INSERT OR REPLACE INTO index_constituents_hist (index_code, con_code, trade_date, weight) VALUES (?,?,?,?)',
                (idx_name, con_code, trade_date, weight)
            )
            n += 1
        conn.commit()
        return n
    except Exception as e:
        logging.warning("fetch %s %s: %s", index_code, trade_date, str(e)[:80])
        return 0

def main():
    conn = sqlite3.connect(DB_PATH)
    create_table(conn)
    pro = ts.pro_api(TOKEN)

    dates = get_month_end_dates(pro)
    logger.info("拉取 %d 个月末 × %d 指数 = %d 次API调用", len(dates), len(INDEX_MAP), len(dates) * len(INDEX_MAP))

    total = 0
    calls = 0
    for i, dt in enumerate(dates):
        for idx_code in INDEX_MAP:
            n = fetch_one_month(pro, idx_code, dt, conn)
            total += n
            calls += 1
            time.sleep(0.3)  # ~3.3次/秒, 不超过200次/分钟

        if (i + 1) % 12 == 0:
            logger.info("进度: %d/%d 月, 已写入 %d 行", i + 1, len(dates), total)

    # 验证
    stats = pd.read_sql('''
        SELECT index_code, COUNT(*) as rows, COUNT(DISTINCT trade_date) as dates, COUNT(DISTINCT con_code) as stocks
        FROM index_constituents_hist GROUP BY index_code
    ''', conn)
    logger.info("写入统计:")
    print(stats.to_string(index=False))

    # 2021-02 沪深300成分股数量
    check = pd.read_sql('''
        SELECT COUNT(*) FROM index_constituents_hist 
        WHERE index_code='hs300' AND trade_date='20210226'
    ''', conn)
    logger.info("2021-02 HS300成分股: %d", check.iloc[0, 0])

    conn.close()
    logger.info("完成: %d 行写入, %d 次API调用", total, calls)

if __name__ == "__main__":
    main()
