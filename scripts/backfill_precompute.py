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
import logging
import os
import sys
import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd

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





def _backfill_qvix_batch(db_path: str, force: bool):
    """批量回填 QVIX 恐慌指数 — 一次性下载，逐日匹配写入"""
    from src.data.qvix_fetcher import fetch_panic_index
    try:
        df = fetch_panic_index(timeout=60)
    except Exception as e:
        logger.warning("QVIX 数据获取失败，跳过回填: %s", e)
        return
    if df.empty:
        logger.warning("QVIX 数据为空，跳过回填")
        return

    with get_conn(db_path) as conn:
        all_dates = _fetch_trade_dates(conn)
        existing = set()
        if not force:
            rows = conn.execute("SELECT trade_date FROM qvix_daily").fetchall()
            existing = {r[0] for r in rows}

        qvix_dates = df.index.sort_values()
        ok = 0
        for td in all_dates:
            if td in existing:
                continue
            target = pd.Timestamp(td)
            if target in df.index:
                row = df.loc[target]
            else:
                prev = qvix_dates[qvix_dates <= target]
                if len(prev) == 0:
                    continue
                row = df.loc[prev[-1]]
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO qvix_daily
                        (trade_date, qvix, qvix_50, qvix_300, qvix_1000, panic_index, concentration)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    td,
                    round(float(row["panic_index"]), 4),
                    round(float(row["qvix_50"]), 4) if pd.notna(row["qvix_50"]) else None,
                    round(float(row["qvix_300"]), 4) if pd.notna(row["qvix_300"]) else None,
                    round(float(row["qvix_1000"]), 4) if pd.notna(row["qvix_1000"]) else None,
                    round(float(row["panic_index"]), 4) if pd.notna(row["panic_index"]) else None,
                    round(float(row["concentration"]), 4) if pd.notna(row["concentration"]) else None,
                ))
                ok += 1
            except Exception as e:
                logger.warning("QVIX batch %s error: %s", td, e)
        logger.info("QVIX 恐慌指数: %d/%d done", ok, len(all_dates) - len(existing))
        # 移除旧的旧版 sz000016 QVIX 记录（如果存在且已被覆盖）
        conn.execute("DELETE FROM qvix_daily WHERE qvix IS NOT NULL AND panic_index IS NULL AND qvix_50 IS NULL")


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

    # 回填 daily_erp (V2 引擎依赖)
    _backfill_derived("daily_erp", _compute_daily_erp, all_dates, db, critical=True)
    # daily_turnover 仅加速用，非 CI 关键路径
    _backfill_derived("daily_turnover", _compute_daily_turnover, all_dates, db, critical=False)
    # QVIX 恐慌指数 — 批量下载一次，避免每个日期都请求 HTTP
    _backfill_qvix_batch(db, force)

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
