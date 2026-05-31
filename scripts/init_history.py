#!/usr/bin/env python3
"""
历史数据初始化脚本
一次性拉取所有历史数据存入 SQLite
使用方式：
  python scripts/init_history.py              # 默认 2015-01-01 到今天
  python scripts/init_history.py 2010-01-01   # 从指定日期开始
"""
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


def main():
    from src.data.database import init_database
    from src.data.fetcher import (
        fetch_all_index_history,
        fetch_margin_history,
        fetch_northbound_history,
        fetch_ah_premium,
    )

    start = sys.argv[1] if len(sys.argv) > 1 else "2015-01-01"

    logger.info("=" * 60)
    logger.info("HISTORY DATA INITIALIZATION")
    logger.info("Start date: %s", start)
    logger.info("=" * 60)

    # 初始化数据库
    init_database()

    # 1. 指数日行情（全量）
    logger.info("Step 1/4: Fetching index daily data (this may take a while)...")
    fetch_all_index_history(start)

    # 2. 融资融券
    logger.info("Step 2/4: Fetching margin trading data...")
    from src.data.database import save_dataframe
    end = None
    df = fetch_margin_history(start, end)
    if not df.empty:
        save_dataframe(df, "margin_history")
        print(f"  Margin: {len(df)} rows")

    # 3. 北向资金
    logger.info("Step 3/4: Fetching northbound data...")
    df = fetch_northbound_history(start, end)
    if not df.empty:
        save_dataframe(df, "northbound_history")
        print(f"  Northbound: {len(df)} rows")

    # 4. AH溢价
    logger.info("Step 4/4: Fetching AH premium...")
    df = fetch_ah_premium()
    if not df.empty:
        save_dataframe(df, "ah_premium")
        print(f"  AH premium: {len(df)} rows")

    logger.info("Initialization complete!")


if __name__ == "__main__":
    main()
