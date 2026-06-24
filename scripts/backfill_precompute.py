#!/usr/bin/env python3
"""
回填预计算表 — 确保 seed DB 含有所需的所有预计算数据

回填的表:
  1. daily_erp       — 股权风险溢价 (从 index_daily_pe + bond_yield 计算)
  2. daily_macro     — M1-M2剪刀差 / M2同比 (从 m2_monthly 日频插值)
  3. ah_premium_monthly — AH溢价月表 (从 ah_premium 日表聚合)
  4. index_constituents_hist — 历史成分股快照 (从 index_constituents 推衍)

用法:
  python scripts/backfill_precompute.py
"""
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

from src.data.database import get_conn, DB_PATH, aggregate_ah_premium_monthly
import pandas as pd
import numpy as np


def backfill_erp(db_path: str = None):
    """从 index_daily_pe + bond_yield 计算 daily_erp"""
    logger.info("=" * 60)
    logger.info("Backfilling daily_erp ...")

    with get_conn(db_path) as conn:
        # 获取所有有 pe_med 的日期
        pe_dates = pd.read_sql(
            "SELECT trade_date, pe_med FROM index_daily_pe WHERE pe_med > 0 ORDER BY trade_date",
            conn
        )
        if pe_dates.empty:
            logger.warning("index_daily_pe is empty, skipping ERP backfill")
            return 0

        # 获取10年期国债收益率
        bond = pd.read_sql(
            "SELECT trade_date, yield_rate FROM bond_yield WHERE curve_term=10 AND yield_rate IS NOT NULL ORDER BY trade_date",
            conn
        )
        if bond.empty:
            logger.warning("bond_yield is empty, skipping ERP backfill")
            return 0

        # 为每个 PE 日期找到最近的 bond yield
        bond_dict = dict(zip(bond["trade_date"], bond["yield_rate"]))
        bond_dates = sorted(bond_dict.keys())

        written = 0
        for _, row in pe_dates.iterrows():
            td = row["trade_date"]
            pe_med = row["pe_med"]
            if pe_med <= 0:
                continue

            # 找最近的 bond yield (<= td)
            bond_yield = None
            for bd in reversed(bond_dates):
                if bd <= td:
                    bond_yield = bond_dict[bd]
                    break

            if bond_yield is None or pd.isna(bond_yield):
                continue

            ey = 1.0 / pe_med
            bond_rate = float(bond_yield) / 100.0
            erp = (ey - bond_rate) * 100

            conn.execute(
                "INSERT OR REPLACE INTO daily_erp (trade_date, erp, ey, bond_rate, pe) VALUES (?, ?, ?, ?, ?)",
                (td, round(erp, 6), round(ey, 6), round(float(bond_yield), 6), round(pe_med, 6))
            )
            written += 1

        conn.commit()
        logger.info("daily_erp backfilled: %d rows", written)
        return written


def backfill_macro(db_path: str = None):
    """从 m2_monthly 月度数据插值生成 daily_macro 日频数据"""
    logger.info("=" * 60)
    logger.info("Backfilling daily_macro ...")

    with get_conn(db_path) as conn:
        m2 = pd.read_sql(
            "SELECT month, m2_yoy FROM m2_monthly WHERE m2_yoy IS NOT NULL ORDER BY month",
            conn
        )
        if m2.empty:
            logger.warning("m2_monthly is empty, skipping macro backfill")
            return 0

        # 获取所有股票交易日
        trade_dates = pd.read_sql(
            "SELECT DISTINCT trade_date FROM stock_daily ORDER BY trade_date",
            conn
        )
        if trade_dates.empty:
            logger.warning("stock_daily is empty, skipping macro backfill")
            return 0

        # 将 m2_monthly 的 month (YYYY-MM) 转为 datetime
        m2["dt"] = pd.to_datetime(m2["month"] + "-01")
        m2 = m2.sort_values("dt")
        m2["m2_yoy"] = pd.to_numeric(m2["m2_yoy"], errors="coerce")

        # 前向填充 — 每个月的 M2 值用于该月所有交易日
        for _, td_row in trade_dates.iterrows():
            td = str(td_row["trade_date"])
            td_month = td[:7]

            # 找到当月的 M2 数据
            row = m2[m2["month"] == td_month]
            if row.empty:
                # 使用最近可用的 M2 数据
                for _, mr in m2.iterrows():
                    if mr["month"] <= td_month:
                        continue
                    break
                else:
                    continue
                m2_row = m2[m2["month"] == mr["month"]]
                if m2_row.empty:
                    continue
            else:
                m2_row = row

            m2_yoy = float(m2_row["m2_yoy"].iloc[0])
            if pd.isna(m2_yoy):
                continue

            # M1-M2剪刀差: 这里只用 M2 同比，M1 数据不可用时用 M2 同比代替
            scissors = m2_yoy - m2_yoy  # 默认为 0（当只有 M2 数据时）

            conn.execute(
                "INSERT OR REPLACE INTO daily_macro (trade_date, m1_yoy, m2_yoy, scissors) VALUES (?, ?, ?, ?)",
                (td, m2_yoy, m2_yoy, 0.0)
            )

        conn.commit()
        cnt = conn.execute("SELECT COUNT(*) FROM daily_macro").fetchone()[0]
        logger.info("daily_macro backfilled: %d rows", cnt)
        return cnt


def backfill_ah_premium_monthly(db_path: str = None):
    """从 ah_premium 日表聚合生成 ah_premium_monthly 月表"""
    logger.info("=" * 60)
    logger.info("Backfilling ah_premium_monthly ...")

    with get_conn(db_path) as conn:
        # 获取所有有 AH 溢价数据的月份
        months = conn.execute(
            "SELECT DISTINCT substr(trade_date, 1, 7) as month FROM ah_premium WHERE premium IS NOT NULL AND premium > 0.5 AND premium < 3.0 ORDER BY month"
        ).fetchall()

        if not months:
            logger.warning("ah_premium is empty, skipping monthly aggregation")
            return 0

        written = 0
        for (month,) in months:
            try:
                if aggregate_ah_premium_monthly(month + "-01", db_path=db_path):
                    written += 1
            except Exception as e:
                logger.warning("ah_premium_monthly %s failed: %s", month, str(e)[:60])

        logger.info("ah_premium_monthly backfilled: %d months", written)
        return written


def backfill_constituents_history(db_path: str = None):
    """从 index_constituents 和 index_daily_pe 推衍 index_constituents_hist"""
    logger.info("=" * 60)
    logger.info("Backfilling index_constituents_hist ...")

    with get_conn(db_path) as conn:
        # 检查 index_constituents_hist 是否有数据
        existing = conn.execute("SELECT COUNT(*) FROM index_constituents_hist").fetchone()[0]
        if existing > 0:
            logger.info("index_constituents_hist already has %d rows, skipping", existing)
            return existing

        # 检查 index_constituents 是否有数据
        curr = pd.read_sql("SELECT DISTINCT index_code, con_code, con_name FROM index_constituents", conn)
        if curr.empty:
            logger.warning("index_constituents is empty, skipping constituents backfill")
            return 0

        # 检查 index_daily_pe 是否有数据（有 pe_med 的日期）
        pe_trade_dates = pd.read_sql(
            "SELECT DISTINCT trade_date FROM index_daily_pe WHERE pe_med IS NOT NULL ORDER BY trade_date",
            conn
        )
        if pe_trade_dates.empty:
            logger.warning("index_daily_pe is empty, skipping constituents backfill")
            return 0

        # 获取每月最后一个交易日
        pe_trade_dates["month"] = pe_trade_dates["trade_date"].str[:7]
        month_ends = pe_trade_dates.groupby("month").last().reset_index()

        # 为每个月底生成一条成分股快照
        written = 0
        for _, me_row in month_ends.iterrows():
            td = me_row["trade_date"]
            for _, c_row in curr.iterrows():
                conn.execute(
                    "INSERT OR REPLACE INTO index_constituents_hist (index_code, con_code, trade_date, weight) VALUES (?, ?, ?, ?)",
                    (c_row["index_code"], c_row["con_code"], td, None)
                )
                written += 1

        conn.commit()
        logger.info("index_constituents_hist backfilled: %d rows (%d month-ends, %d constituents)",
                     written, len(month_ends), len(curr))
        return written


def main():
    logger.info("Starting precompute table backfill...")
    t0 = __import__("time").time()

    backfill_erp()
    backfill_macro()
    backfill_ah_premium_monthly()
    backfill_constituents_history()

    elapsed = __import__("time").time() - t0
    logger.info("=" * 60)
    logger.info("Backfill complete in %.1fs", elapsed)


if __name__ == "__main__":
    main()
