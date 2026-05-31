#!/usr/bin/env python3
"""
历史数据初始化脚本
使用混合方案: tushare(PE/PB/融资融券/北向/国债) + akshare(指数行情/AH溢价)
python scripts/init_history.py              # 默认 2015-01-01
python scripts/init_history.py 2010-01-01   # 从指定日期开始
"""
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 确保 tushare token 已设置
TOKEN_PATH = os.path.expanduser("~/daily_stock_analysis/.env")
if os.path.exists(TOKEN_PATH):
    for line in open(TOKEN_PATH):
        line = line.strip()
        if line.startswith("TUSHARE_TOKEN=") and not os.environ.get("TUSHARE_TOKEN"):
            os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1]
            break

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


def main():
    from src.data.database import init_database
    from datetime import date

    start = sys.argv[1] if len(sys.argv) > 1 else "2015-01-01"
    end = date.today().strftime("%Y-%m-%d")

    logger.info("=" * 60)
    logger.info("历史数据初始化: %s → %s", start, end)
    logger.info("=" * 60)

    init_database()

    # 使用 fetcher 的统一入口
    from src.data.fetcher import fetch_all_history
    fetch_all_history(start, end)

    logger.info("初始化完成!")


if __name__ == "__main__":
    main()
