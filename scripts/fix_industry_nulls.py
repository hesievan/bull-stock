#!/usr/bin/env python3
"""
修复 stock_industry 中 industry 为空的记录

用法:
  python scripts/fix_industry_nulls.py          # 交互模式
  python scripts/fix_industry_nulls.py --auto   # 自动修复
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.data.database import get_conn, DB_PATH
from src.data.fetcher import _get_pro, ak_to_ts, ts_to_ak, _ts_sleep
import pandas as pd
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def count_null_industry(db_path=None):
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM stock_industry WHERE industry IS NULL OR industry = ''"
        ).fetchone()
    return row[0]


def fix_industry_nulls(db_path=None, auto=False):
    """从 tushare stock_basic 重新拉取 industry 字段"""
    with get_conn(db_path) as conn:
        null_stocks = pd.read_sql(
            "SELECT code, code_name FROM stock_industry WHERE industry IS NULL OR industry = ''",
            conn
        )

    if null_stocks.empty:
        logger.info("No null industry records found")
        return 0

    logger.info("Found %d stocks with null industry", len(null_stocks))

    if not auto:
        confirm = input(f"Fix {len(null_stocks)} stocks? [y/N]: ")
        if confirm.lower() != "y":
            return 0

    pro = _get_pro()
    fixed = 0

    for _, row in null_stocks.iterrows():
        ak_code = row["code"]
        ts_code = ak_to_ts(ak_code)
        try:
            df = pro.stock_basic(ts_code=ts_code, fields="ts_code,industry")
            _ts_sleep()
            if df is not None and not df.empty:
                industry = df.iloc[0]["industry"]
                if industry:
                    with get_conn(db_path) as conn:
                        conn.execute(
                            "UPDATE stock_industry SET industry=? WHERE code=?",
                            (industry, ak_code)
                        )
                    fixed += 1
                    logger.info("Fixed %s (%s): %s", ak_code, row["code_name"], industry)
        except Exception as e:
            logger.warning("Failed to fix %s: %s", ak_code, str(e)[:60])

    logger.info("Fixed %d / %d stocks", fixed, len(null_stocks))
    return fixed


if __name__ == "__main__":
    auto = "--auto" in sys.argv
    n = count_null_industry()
    print(f"Null industry records: {n}")
    if n > 0:
        fix_industry_nulls(auto=auto)
