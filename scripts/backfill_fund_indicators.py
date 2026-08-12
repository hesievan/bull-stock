#!/usr/bin/env python3
"""
Phase 0 数据回填 — 资金维度扩容所需的三个新序列

1. bond_yield: 回填 2Y + 10Y 国债收益率 (yield_spread = 10Y - 2Y)
2. m1_monthly: 新建表, 写入 M1 同比 (m1_m2_spread = M1同比 - M2同比)
3. northbound_history: 回填北向资金到今天 (north_ratio = north_net / 当日成交额)

运行: python scripts/backfill_fund_indicators.py
"""
import logging
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _load_env_token():
    """从 .env 读取 TUSHARE_TOKEN (与 run_daily.py 一致), 必须在 import fetcher 前设置"""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("TUSHARE_TOKEN="):
                    os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1].strip('"\'')
    except FileNotFoundError:
        pass


_load_env_token()

from src.data.database import (
    DB_PATH, init_database, get_latest_date, read_dataframe,
)
import src.data.fetcher as fetcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _summarize(label, table, date_col="trade_date", extra=None):
    try:
        cnt = read_dataframe(f"SELECT COUNT(*) AS n FROM {table}").iloc[0]["n"]
        mx = get_latest_date(table, date_col=date_col)
        msg = f"  {label:14s} rows={cnt:6d}  latest={mx}"
        if extra:
            msg += f"  ({extra})"
        logger.info(msg)
    except Exception as e:
        logger.warning("  %s summary failed: %s", label, e)


def main():
    logger.info("=== Phase 0 资金维度数据回填 ===")
    init_database(DB_PATH)  # 确保 m1_monthly 表存在

    # 1. 国债收益率 2Y + 10Y
    logger.info("[1/3] 债券收益率 (2Y+10Y) -> bond_yield ...")
    df = fetcher.fetch_bond_yield_history("2010-01-01", date.today().strftime("%Y-%m-%d"))
    if df is not None and not df.empty:
        fetcher._save(df, "bond_yield")
        logger.info("        bond_yield 写入 %d 行", len(df))
    else:
        logger.warning("        bond_yield 获取为空")
    _summarize("bond_yield", "bond_yield",
               extra="2Y/10Y from bond_zh_us_rate")

    # 2. M1 同比
    logger.info("[2/3] M1 货币供应 -> m1_monthly ...")
    fetcher.fetch_m1_history()
    _summarize("m1_monthly", "m1_monthly", date_col="month")

    # 3. 北向资金回填到今天
    logger.info("[3/3] 北向资金回填 -> northbound_history ...")
    nb_latest = get_latest_date("northbound_history")
    logger.info("        当前最新: %s, 目标: %s", nb_latest, date.today())
    # 从已有最新往前多取 1 个月, 避免端点遗漏; 若为空则从沪港通开通起
    start = "2014-11-01" if not nb_latest else "2026-06-01"
    nb = fetcher.fetch_northbound_history(start, date.today().strftime("%Y-%m-%d"))
    if nb is not None and not nb.empty:
        fetcher._save(nb, "northbound_history")
        logger.info("        northbound_history 写入 %d 行", len(nb))
    else:
        logger.warning("        northbound_history 获取为空")
    _summarize("northbound_history", "northbound_history")

    # 校验三条新序列覆盖
    logger.info("=== 校验新序列覆盖 ===")
    # bond_yield 2Y/10Y
    for t in (2.0, 10.0):
        r = read_dataframe(
            f"SELECT COUNT(*) n, MIN(trade_date) mn, MAX(trade_date) mx "
            f"FROM bond_yield WHERE curve_term={t} AND yield_rate IS NOT NULL"
        ).iloc[0]
        logger.info("  bond_yield term=%.0f: n=%d range=%s~%s", t, r["n"], r["mn"], r["mx"])
    # m1 + m2 月份对齐
    m1 = read_dataframe("SELECT COUNT(*) n, MIN(month) mn, MAX(month) mx FROM m1_monthly").iloc[0]
    m2 = read_dataframe("SELECT COUNT(*) n, MIN(month) mn, MAX(month) mx FROM m2_monthly").iloc[0]
    logger.info("  m1_monthly: n=%d range=%s~%s", m1["n"], m1["mn"], m1["mx"])
    logger.info("  m2_monthly: n=%d range=%s~%s", m2["n"], m2["mn"], m2["mx"])

    logger.info("=== Phase 0 回填完成 ===")


if __name__ == "__main__":
    main()
