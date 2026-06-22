#!/usr/bin/env python3
"""
拉取 tushare 历史数据 (积分版)
补全: 北向资金, 国债收益率, 指数PE/PB, 全市场总市值(daily_basic)
"""
import sys
import os
import logging
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("data/fetch_tushare.log", mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def main():
    import tushare as ts
    import pandas as pd
    from src.data.database import get_latest_date, save_dataframe, DB_PATH, init_database
    import sqlite3

    init_database()

    pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])
    START = "2015-01-01"
    END = "2026-05-31"

    # ── 1. 北向资金 ──
    logger.info("=== 1/5: 北向资金 (moneyflow_hsgt) ===")
    latest = get_latest_date("northbound_history")
    if latest:
        start1 = latest
    else:
        start1 = START
    try:
        df1 = pro.moneyflow_hsgt(
            start_date=start1.replace("-", ""),
            end_date=END.replace("-", ""),
        )
        if not df1.empty:
            df1["trade_date"] = pd.to_datetime(df1["trade_date"]).dt.strftime("%Y-%m-%d")
            df1["north_net"] = (
                pd.to_numeric(df1.get("hgt", 0), errors="coerce").fillna(0)
                + pd.to_numeric(df1.get("sgt", 0), errors="coerce").fillna(0)
            )
            keep = ["trade_date", "hgt", "sgt", "north_net", "south_money"]
            df1 = df1[[c for c in keep if c in df1.columns]]
            save_dataframe(df1, "northbound_history")
            logger.info("  Saved %d rows, %s ~ %s", len(df1),
                         df1["trade_date"].min(), df1["trade_date"].max())
        else:
            logger.info("  No new data")
    except Exception as e:
        logger.error("  Failed: %s", e)

    time.sleep(1)

    # ── 2. 国债收益率 ──
    logger.info("=== 2/5: 国债收益率 (yc_cb) ===")
    latest = get_latest_date("bond_yield")
    if latest:
        start2 = latest
    else:
        start2 = START
    try:
        df2 = pro.yc_cb(
            start_date=start2.replace("-", ""),
            end_date=END.replace("-", ""),
        )
        if not df2.empty:
            df2 = df2[df2["curve_term"] <= 10].copy()
            save_dataframe(df2[["trade_date", "curve_term", "yield"]], "bond_yield")
            logger.info("  Saved %d rows", len(df2))
        else:
            logger.info("  No new data")
    except Exception as e:
        logger.error("  Failed: %s", e)

    time.sleep(1)

    # ── 3. 指数PE/PB ──
    logger.info("=== 3/5: 指数PE/PB (index_dailybasic) ===")
    latest = get_latest_date("index_pe_history")
    if latest:
        start3 = latest
    else:
        start3 = START
    all_pe = []
    index_map = {
        "sh000300": "000300.SH",
        "sh000001": "000001.SH",
        "sh000905": "000905.SH",
        "sh000852": "000852.SH",
        "sz399006": "399006.SZ",
        "sz399001": "399001.SZ",
    }
    for ak_code, ts_code in index_map.items():
        try:
            df3 = pro.index_dailybasic(
                ts_code=ts_code,
                start_date=start3.replace("-", ""),
                end_date=END.replace("-", ""),
                fields="trade_date,pe_ttm,pb,total_mv,turnover_rate,turnover_rate_f",
            )
            if not df3.empty:
                df3["index_code"] = ak_code
                all_pe.append(df3)
                logger.info("  %s: %d rows", ak_code, len(df3))
            time.sleep(0.5)
        except Exception as e:
            logger.error("  %s failed: %s", ak_code, str(e)[:80])
    if all_pe:
        df_pe = pd.concat(all_pe, ignore_index=True)
        save_dataframe(df_pe, "index_pe_history")
        logger.info("  Total index_pe_history: %d rows", len(df_pe))

    time.sleep(1)

    # ── 4. 全市场每日指标 (daily_basic) → stock_market_cap ──
    # 拉成分股列表（从已有 stock_daily）
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT DISTINCT stock_code FROM stock_daily").fetchall()
    codes = [r[0].replace(".", "") for r in rows]  # sh.600000 -> sh600000 -> 600000.SH
    # 转为 tushare 格式
    ts_codes = []
    for c in codes:
        if c.startswith("sh"):
            ts_codes.append(c[2:] + ".SH")
        elif c.startswith("sz"):
            ts_codes.append(c[2:] + ".SZ")
    conn.close()

    logger.info("=== 4/5: daily_basic (%d stocks) → stock_market_cap ===", len(ts_codes))

    # 按股票拉历史，再汇总
    cap_rows = []
    for i, tc in enumerate(ts_codes):
        try:
            df4 = pro.daily_basic(
                ts_code=tc,
                start_date=START.replace("-", ""),
                end_date=END.replace("-", ""),
                fields="trade_date,total_mv,circ_mv,pe_ttm,pb",
            )
            if not df4.empty:
                df4["trade_date"] = pd.to_datetime(df4["trade_date"]).dt.strftime("%Y-%m-%d")
                cap_rows.append(df4[["trade_date", "total_mv"]])
            if (i + 1) % 200 == 0:
                logger.info("  Progress: %d/%d", i + 1, len(ts_codes))
            time.sleep(0.3)
        except Exception as e:
            logger.error("  %s failed: %s", tc, str(e)[:80])

    if cap_rows:
        # 汇总每个交易日全市场总市值
        all_cap = pd.concat(cap_rows, ignore_index=True)
        all_cap["total_mv"] = pd.to_numeric(all_cap["total_mv"], errors="coerce")
        market_cap = all_cap.groupby("trade_date").agg(
            total_mv=("total_mv", "sum"),
            stock_count=("total_mv", "count"),
        ).reset_index()
        market_cap = market_cap.sort_values("trade_date")

        save_dataframe(market_cap, "stock_market_cap")
        logger.info("  stock_market_cap: %d rows, %s ~ %s",
                     len(market_cap), market_cap["trade_date"].iloc[0], market_cap["trade_date"].iloc[-1])

    # ── 5. 验证 ──
    logger.info("=== 5/5: Verify ===")
    conn = sqlite3.connect(DB_PATH)
    for t in ["northbound_history", "bond_yield", "index_pe_history", "stock_market_cap"]:
        r = conn.execute(f"SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM {t}").fetchone()
        logger.info("  %s: %d rows, %s ~ %s", t, r[0], r[1], r[2])
    conn.close()
    logger.info("Done!")


if __name__ == "__main__":
    main()
