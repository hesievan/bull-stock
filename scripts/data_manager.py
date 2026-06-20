#!/usr/bin/env python3
"""
统一数据补充工具 — 替代多个重复的 backfill/fetch 脚本

用法:
  python scripts/data_manager.py status              # 查看数据状态
  python scripts/data_manager.py backfill            # 全量补充
  python scripts/data_manager.py backfill --only margin  # 只补充融资融券
  python scripts/data_manager.py backfill --only northbound  # 只补充北向
  python scripts/data_manager.py backfill --only bond  # 只补充国债
  python scripts/data_manager.py backfill --only qvix  # 只补充QVIX
  python scripts/data_manager.py backfill --only ah   # 只补充AH溢价
  python scripts/data_manager.py backfill --only turnover  # 只补充换手率
  python scripts/data_manager.py backfill --since 2024-01-01  # 从指定日期开始
"""
import sys
import os
import time
import logging
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def show_status():
    from src.data.database import get_conn, DB_PATH
    tables = [
        "stock_daily", "index_daily", "margin_history", "northbound_history",
        "bond_yield", "qvix_daily", "ah_premium", "m2_monthly",
        "index_pe_history", "daily_turnover", "daily_updown", "daily_limit",
        "daily_ma_alignment", "stock_industry",
    ]
    print(f"\n数据库: {DB_PATH}")
    print(f"{'表名':<25} {'行数':>10} {'最新日期':>12} {'最早日期':>12}")
    print("-" * 65)
    with get_conn() as conn:
        for table in tables:
            try:
                row = conn.execute(f"SELECT COUNT(*), MAX(trade_date), MIN(trade_date) FROM {table}").fetchone()
                count, max_d, min_d = row
                print(f"{table:<25} {count:>10,} {max_d or 'N/A':>12} {min_d or 'N/A':>12}")
            except Exception:
                print(f"{table:<25} {'N/A':>10} {'N/A':>12} {'N/A':>12}")


def backfill(only=None, since=None):
    from src.data.database import init_database
    init_database()

    if not only or only == "margin":
        logger.info("补充融资融券数据...")
        try:
            from scripts.backfill_margin_2015 import main as backfill_margin
            backfill_margin()
        except Exception as e:
            logger.error("融资融券补充失败: %s", e)

    if not only or only == "northbound":
        logger.info("补充北向资金数据...")
        try:
            from src.data.fetcher import fetch_northbound_history
            start = since or "2015-01-01"
            end = date.today().strftime("%Y-%m-%d")
            df = fetch_northbound_history(start, end)
            if df is not None and not df.empty:
                from src.data.database import save_dataframe
                save_dataframe(df, "northbound_history")
                logger.info("北向资金补充完成: %d行", len(df))
        except Exception as e:
            logger.error("北向资金补充失败: %s", e)

    if not only or only == "bond":
        logger.info("补充国债收益率...")
        try:
            from scripts.backfill_bond_yield import main as backfill_bond
            backfill_bond()
        except Exception as e:
            logger.error("国债收益率补充失败: %s", e)

    if not only or only == "qvix":
        logger.info("补充QVIX数据...")
        try:
            from scripts.fetch_qvix import fetch_qvix
            fetch_qvix()
        except Exception as e:
            logger.error("QVIX补充失败: %s", e)

    if not only or only == "ah":
        logger.info("补充AH溢价...")
        try:
            from scripts.ah_premium import fetch_ah_premium_index
            fetch_ah_premium_index()
        except Exception as e:
            logger.error("AH溢价补充失败: %s", e)

    if not only or only == "turnover":
        logger.info("补充换手率...")
        try:
            from scripts.fix_turnover import main as fix_turnover
            fix_turnover()
        except Exception as e:
            logger.error("换手率补充失败: %s", e)

    if not only or only == "industry":
        logger.info("补充行业分类...")
        try:
            from src.data.fetcher import fetch_stock_industry
            df = fetch_stock_industry()
            if df is not None and not df.empty:
                from src.data.database import save_dataframe
                save_dataframe(df, "stock_industry")
                logger.info("行业分类补充完成: %d行", len(df))
        except Exception as e:
            logger.error("行业分类补充失败: %s", e)

    logger.info("数据补充完成")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Unified data manager")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Show data status")

    bp = sub.add_parser("backfill", help="Backfill data")
    bp.add_argument("--only", choices=["margin", "northbound", "bond", "qvix", "ah", "turnover", "industry"],
                    help="Only backfill specific data")
    bp.add_argument("--since", help="Start date (YYYY-MM-DD)")

    args = parser.parse_args()

    if args.command == "status":
        show_status()
    elif args.command == "backfill":
        backfill(only=args.only, since=args.since)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
