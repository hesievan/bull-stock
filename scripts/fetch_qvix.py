#!/usr/bin/env python3
"""
获取 50ETF 期权隐含波动率 (iVIX/QVIX) 数据

数据源: akshare index_option_50etf_qvix
用途: 作为恐慌指标，替代波动率替代指标
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.database import get_conn, DB_PATH
import pandas as pd
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def fetch_qvix():
    """获取 50ETF QVIX 数据"""
    try:
        import akshare as ak
    except ImportError:
        logger.error("akshare not installed")
        return 0

    logger.info("Fetching 50ETF QVIX data...")
    df = ak.index_option_50etf_qvix()
    if df is None or df.empty:
        logger.error("Failed to fetch QVIX data")
        return 0

    # 整理数据
    df = df.rename(columns={"date": "trade_date", "close": "qvix"})
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
    df = df[["trade_date", "qvix"]]
    df["qvix"] = pd.to_numeric(df["qvix"], errors="coerce")
    df = df.dropna()

    logger.info("QVIX data: %d rows from %s to %s",
                len(df), df["trade_date"].min(), df["trade_date"].max())

    # 写入数据库
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS qvix_daily (
                trade_date TEXT PRIMARY KEY,
                qvix REAL
            )
        """)
        inserted = 0
        for _, row in df.iterrows():
            conn.execute(
                "INSERT OR REPLACE INTO qvix_daily (trade_date, qvix) VALUES (?, ?)",
                (row["trade_date"], row["qvix"])
            )
            inserted += 1

    logger.info("Saved %d QVIX records", inserted)

    # 验证
    with get_conn() as conn:
        stats = conn.execute(
            "SELECT COUNT(*), MIN(trade_date), MAX(trade_date), AVG(qvix) FROM qvix_daily"
        ).fetchone()
        logger.info("qvix_daily: %d rows from %s to %s, avg=%.2f",
                    stats[0], stats[1], stats[2], stats[3])

    return inserted

if __name__ == "__main__":
    fetch_qvix()
