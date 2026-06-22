#!/usr/bin/env python3
"""
全量回填 tushare 全市场数据到 stock_daily
用 tushare daily + daily_basic 替代 baostock 的 800 只成分股数据
"""
import sys
import os
import time
import logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

from src.data.fetcher import fetch_daily_basic_to_stock_daily
import sqlite3
from src.data.database import DB_PATH

# 获取所有需要回填的日期
conn = sqlite3.connect(DB_PATH)
# 找出 amount < 100 行的日期 (即 baostock 数据，需要替换)
dates_to_fill = conn.execute("""
    SELECT trade_date, COUNT(*) as n_amt
    FROM stock_daily
    WHERE trade_date >= '2015-01-05'
    GROUP BY trade_date
    HAVING SUM(CASE WHEN amount > 0 THEN 1 ELSE 0 END) < 4000
    ORDER BY trade_date
""").fetchall()
conn.close()

logger.info(f"需要回填: {len(dates_to_fill)} 个交易日")

# 回填
t_start = time.time()
success = 0
failed = 0

for i, (td, _) in enumerate(dates_to_fill):
    try:
        written = fetch_daily_basic_to_stock_daily(td)
        if written > 0:
            success += 1
            if (i + 1) % 100 == 0:
                elapsed = time.time() - t_start
                rate = (i + 1) / elapsed
                remaining = (len(dates_to_fill) - i - 1) / rate
                logger.info(f"进度: {i+1}/{len(dates_to_fill)} ({success}成功/{failed}失败) "
                           f"[{elapsed:.0f}s, 剩余{remaining:.0f}s]")
        else:
            failed += 1
    except Exception as e:
        failed += 1
        logger.error(f"{td}: {str(e)[:60]}")

    time.sleep(0.1)  # 限速

elapsed = time.time() - t_start
logger.info(f"回填完成: {success}成功, {failed}失败, 耗时{elapsed:.0f}s ({elapsed/60:.1f}分钟)")
