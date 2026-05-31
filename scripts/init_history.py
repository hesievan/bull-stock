#!/usr/bin/env python3
"""
历史数据初始化脚本 (三源合一版)
一次性拉取所有历史数据存入 SQLite

数据源:
  baostock: 指数日行情、成分股列表、个股历史K线、行业分类
  tushare:  融资融券、北向资金、国债收益率
  akshare:  AH溢价

使用方式:
  python scripts/init_history.py              # 默认 2015-01-01
  python scripts/init_history.py 2010-01-01   # 从指定日期开始

注意: tushare 有频率限制(1次/小时)，初始化可能需要较长时间
"""
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 加载 tushare token
_env_path = os.path.expanduser("~/daily_stock_analysis/.env")
if os.path.exists(_env_path):
    for line in open(_env_path):
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
    from src.data.fetcher import fetch_all_history
    from datetime import date

    start = sys.argv[1] if len(sys.argv) > 1 else "2015-01-01"
    end = date.today().strftime("%Y-%m-%d")

    logger.info("=" * 60)
    logger.info("历史数据初始化 (三源合一): %s → %s", start, end)
    logger.info("=" * 60)

    init_database()
    fetch_all_history(start, end)

    logger.info("初始化完成!")


if __name__ == "__main__":
    main()
