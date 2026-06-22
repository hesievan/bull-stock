#!/usr/bin/env python3
"""
用 akshare 补充 bond_yield 表中的10年期国债收益率数据

tushare yc_cb 接口需要5000+积分，本脚本用 akshare bond_zh_us_rate 替代
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.database import get_conn
import pandas as pd
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def backfill_bond_yield():
    """用 akshare 补充10年期国债收益率"""
    try:
        import akshare as ak
    except ImportError:
        logger.error("akshare not installed")
        return 0

    # 获取 akshare 数据
    logger.info("Fetching bond yield from akshare...")
    df = ak.bond_zh_us_rate(start_date='20180101')
    if df is None or df.empty:
        logger.error("Failed to fetch bond yield data")
        return 0

    # 整理数据
    df = df.rename(columns={
        '日期': 'trade_date',
        '中国国债收益率10年': 'yield_rate'
    })
    df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d')
    df['curve_term'] = 10.0
    df['yield_rate'] = pd.to_numeric(df['yield_rate'], errors='coerce')
    df = df.dropna(subset=['yield_rate'])
    df = df[['trade_date', 'curve_term', 'yield_rate']]

    logger.info("akshare data: %d rows from %s to %s",
                len(df), df['trade_date'].min(), df['trade_date'].max())

    # 写入数据库
    inserted = 0
    with get_conn() as conn:
        for _, row in df.iterrows():
            # 只插入不存在的记录
            exists = conn.execute(
                "SELECT 1 FROM bond_yield WHERE trade_date=? AND curve_term=10",
                (row['trade_date'],)
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO bond_yield (trade_date, curve_term, yield_rate) VALUES (?, ?, ?)",
                    (row['trade_date'], row['curve_term'], row['yield_rate'])
                )
                inserted += 1

    logger.info("Inserted %d new records", inserted)

    # 验证
    with get_conn() as conn:
        stats = conn.execute(
            "SELECT COUNT(*) as total, MIN(trade_date) as first, MAX(trade_date) as last FROM bond_yield WHERE curve_term=10"
        ).fetchone()
        logger.info("bond_yield (10Y): %d rows from %s to %s", stats[0], stats[1], stats[2])

    return inserted

if __name__ == "__main__":
    backfill_bond_yield()
