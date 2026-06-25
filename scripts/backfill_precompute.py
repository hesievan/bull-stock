#!/usr/bin/env python3
"""
预计算表批量回填 — 为 V2 引擎提供 10 年历史数据，确保百分位计算有足够样本。

回填的表:
  index_daily_pe    — 成分股 PE/PB 中位数 (用于 PE/ERP/巴菲特)
  daily_circ_mv     — 全市场流通市值 (用于 margin_ratio)
  daily_total_mv    — 全市场总市值 (用于 deposit_ratio/巴菲特)
  daily_erp         — 股权风险溢价 (用于 ERP 计算加速)
  daily_updown      — 涨跌家数比 (展示)
  daily_limit       — 涨停/跌停统计 (展示)
  daily_below_net   — 破净率 (展示)
  daily_ma_alignment — 均线排列比 (用于 ma_alignment)
  daily_turnover    — 换手率 (加速 turnover 计算)
  qvix_daily        — QVIX 恐慌指数 (展示)
"""
import sys, os, time, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
from datetime import date, timedelta

from src.data.database import (
    get_conn, read_dataframe, DB_PATH,
    update_index_daily_pe,
    compute_daily_circ_mv, compute_daily_total_mv,
    compute_daily_updown, compute_daily_limit,
    compute_daily_below_net, compute_daily_ma_alignment,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TASKS = [
    ("index_daily_pe",    update_index_daily_pe,     "成分股PE/PB中位数", True),
    ("daily_circ_mv",     compute_daily_circ_mv,     "流通市值",          True),
    ("stock_market_cap",  compute_daily_total_mv,    "总市值",            True),
    ("daily_updown",      compute_daily_updown,      "涨跌家数比",        False),
    ("daily_limit",       compute_daily_limit,       "涨停统计",          False),
    ("daily_below_net",   compute_daily_below_net,   "破净率",            False),
    ("daily_ma_alignment", compute_daily_ma_alignment, "均线排列比",      True),
]


def _existing_dates(table: str, db_path: str) -> set:
    try:
        df = read_dataframe(f"SELECT trade_date FROM {table}", db_path=db_path)
        return set(df["trade_date"].tolist()) if not df.empty else set()
    except Exception:
        return set()


def _fetch_trade_dates(conn) -> list:
    """从 stock_daily 获取所有有数据的历史交易日 (排序)"""
    df = pd.read_sql(
        "SELECT DISTINCT trade_date FROM stock_daily ORDER BY trade_date", conn
    )
    if df.empty:
        # 兜底: 从 index_daily 获取
        df = pd.read_sql(
            "SELECT DISTINCT trade_date FROM index_daily ORDER BY trade_date", conn
        )
    return df["trade_date"].tolist() if not df.empty else []


def _compute_daily_erp(trade_date: str, db_path: str) -> bool:
    """计算单日 ERP 并写入 daily_erp 表"""
    with get_conn(db_path) as conn:
        pe_row = conn.execute(
            "SELECT pe_med FROM index_daily_pe WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1",
            (trade_date,)
        ).fetchone()
        bond_row = conn.execute(
            "SELECT yield_rate FROM bond_yield WHERE curve_term=10 AND trade_date<=? ORDER BY trade_date DESC LIMIT 1",
            (trade_date,)
        ).fetchone()
        if not pe_row or not bond_row or pe_row[0] is None or bond_row[0] is None:
            return False
        erp = (1.0 / pe_row[0] - bond_row[0] / 100.0) * 100
        conn.execute(
            "INSERT OR REPLACE INTO daily_erp (trade_date, erp) VALUES (?, ?)",
            (trade_date, round(erp, 6))
        )
        return True


def _compute_daily_turnover(trade_date: str, db_path: str) -> bool:
    """计算单日换手率并写入 daily_turnover 表"""
    with get_conn(db_path) as conn:
        df = pd.read_sql(
            "SELECT SUM(turnover_rate * circ_mv / 100.0) / 1e8 as turnover_rate "
            "FROM stock_daily WHERE trade_date=? AND turnover_rate>0 AND circ_mv>0",
            conn, params=[trade_date]
        )
        if df.empty or df.iloc[0, 0] is None:
            return False
        conn.execute(
            "INSERT OR REPLACE INTO daily_turnover (trade_date, turnover_rate) VALUES (?, ?)",
            (trade_date, round(float(df.iloc[0, 0]), 4))
        )
        return True


def _compute_qvix_daily(trade_date: str, db_path: str) -> bool:
    """计算单日 QVIX 并写入 qvix_daily 表"""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT close FROM index_daily WHERE index_code='sz000016' AND trade_date=?",
            (trade_date,)
        ).fetchone()
        if not row or row[0] is None:
            return False
        conn.execute(
            "INSERT OR REPLACE INTO qvix_daily (trade_date, qvix) VALUES (?, ?)",
            (trade_date, round(float(row[0]), 4))
        )
        return True


def backfill_precompute(db_path: str = None, force: bool = False):
    """回填所有预计算表"""
    db = db_path or DB_PATH
    with get_conn(db) as conn:
        all_dates = _fetch_trade_dates(conn)
    if not all_dates:
        logger.warning("No trade dates found in stock_daily/index_daily, cannot backfill")
        return False

    logger.info("Found %d trade dates to backfill", len(all_dates))

    # 回填每张表
    for table, func, label, critical in TASKS:
        existing = _existing_dates(table, db) if not force else set()
        need = [d for d in all_dates if d not in existing]
        if not need:
            logger.info("  %s (%s): up-to-date (%d rows)", table, label, len(existing))
            continue
        ok = 0
        t0 = time.time()
        for i, td in enumerate(need):
            try:
                if func(td, db):
                    ok += 1
            except Exception as e:
                if critical:
                    logger.error("  %s %s CRITICAL FAIL: %s", table, td, e)
                    return False
            if (i + 1) % 100 == 0:
                logger.info("  %s: %d/%d (%.1f%%)", table, i + 1, len(need), (i + 1) / len(need) * 100)
        elapsed = time.time() - t0
        logger.info("  %s (%s): %d/%d done (%.1fs)", table, label, ok, len(need), elapsed)

    # 回填 daily_erp
    _backfill_derived("daily_erp", _compute_daily_erp, all_dates, db, critical=True)
    # 回填 daily_turnover
    _backfill_derived("daily_turnover", _compute_daily_turnover, all_dates, db, critical=True)
    # 回填 qvix_daily
    _backfill_derived("qvix_daily", _compute_qvix_daily, all_dates, db, critical=False)

    logger.info("Precompute backfill complete: %d dates processed", len(all_dates))
    return True


def _backfill_derived(table: str, func, all_dates: list, db_path: str, critical: bool = False):
    existing = _existing_dates(table, db_path)
    need = [d for d in all_dates if d not in existing]
    if not need:
        logger.info("  %s: up-to-date (%d rows)", table, len(existing))
        return
    ok = 0
    t0 = time.time()
    for i, td in enumerate(need):
        try:
            if func(td, db_path):
                ok += 1
        except Exception as e:
            if critical:
                logger.error("  %s %s CRITICAL FAIL: %s", table, td, e)
                return
        if (i + 1) % 200 == 0:
            logger.info("  %s: %d/%d (%.1f%%)", table, i + 1, len(need), (i + 1) / len(need) * 100)
    elapsed = time.time() - t0
    logger.info("  %s: %d/%d done (%.1fs)", table, ok, len(need), elapsed)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="回填预计算表")
    parser.add_argument("--force", action="store_true", help="强制全部重算")
    parser.add_argument("--db", help="数据库路径 (默认 DB_PATH)")
    args = parser.parse_args()

    ok = backfill_precompute(db_path=args.db, force=args.force)
    sys.exit(0 if ok else 1)
