#!/usr/bin/env python3
"""
拉取 tushare daily_basic 历史 → 重建 stock_market_cap
逐只拉取成分股 PE/PB/总市值, 汇总全市场
"""
import sys
import os
import logging
import time
import sqlite3
import pandas as pd

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
        logging.FileHandler("data/fetch_daily_basic.log", mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

DB_PATH = "data/heat_index.db"
START = "20150101"
END = "20260531"


def main():
    import tushare as ts
    from src.data.database import save_dataframe

    pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])

    # 从 stock_daily 获取已有股票代码
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT DISTINCT stock_code FROM stock_daily WHERE total_mv IS NOT NULL OR peTTM IS NOT NULL").fetchall()
    conn.close()

    if not rows:
        # 用 stock_basic 全市场
        df_sb = pro.stock_basic(exchange="", list_status="L", fields="ts_code")
        ts_codes = df_sb["ts_code"].tolist()
    else:
        ts_codes = []
        for r in rows:
            c = r[0]
            if c.startswith("sh"):
                ts_codes.append(c[2:] + ".SH")
            elif c.startswith("sz"):
                ts_codes.append(c[2:] + ".SZ")

    logger.info("Fetching daily_basic for %d stocks: %s ~ %s", len(ts_codes), START, END)

    all_cap = []   # stock_market_cap
    all_pe = []    # stock_daily 补充 PE/PB
    errors = 0

    for i, tc in enumerate(ts_codes):
        try:
            df = pro.daily_basic(
                ts_code=tc,
                start_date=START,
                end_date=END,
                fields="trade_date,total_mv,circ_mv,pe_ttm,pb",
            )
            if not df.empty:
                df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
                ak_code = ("sh" if tc.endswith(".SH") else "sz") + tc.replace(".SH", "").replace(".SZ", "")

                # 总市值
                cap = df[["trade_date", "total_mv"]].copy()
                cap["total_mv"] = pd.to_numeric(cap["total_mv"], errors="coerce")
                all_cap.append(cap)

                # PE/PB → 补写 stock_daily
                pb = df[["trade_date", "pe_ttm", "pb"]].copy()
                pb["stock_code"] = ak_code
                all_pe.append(pb)

            if (i + 1) % 100 == 0:
                logger.info("  Progress: %d/%d (errors: %d)", i + 1, len(ts_codes), errors)
            time.sleep(0.3)

        except Exception as e:
            errors += 1
            if errors <= 5:
                logger.error("  %s: %s", tc, str(e)[:100])
            time.sleep(0.3)

    # ── 汇总 stock_market_cap ──
    if all_cap:
        all_cap_df = pd.concat(all_cap, ignore_index=True)
        all_cap_df = all_cap_df.dropna(subset=["total_mv"])
        all_cap_df = all_cap_df[all_cap_df["total_mv"] > 0]
        market_cap = all_cap_df.groupby("trade_date").agg(
            total_mv=("total_mv", "sum"),
            stock_count=("total_mv", "count"),
        ).reset_index().sort_values("trade_date")

        save_dataframe(market_cap, "stock_market_cap")
        logger.info("stock_market_cap: %d rows, %s ~ %s",
                     len(market_cap), market_cap["trade_date"].iloc[0], market_cap["trade_date"].iloc[-1])

    # ── 补写 stock_daily PE/PB ──
    if all_pe:
        all_pe_df = pd.concat(all_pe, ignore_index=True)
        all_pe_df["pe_ttm"] = pd.to_numeric(all_pe_df["pe_ttm"], errors="coerce")
        all_pe_df["pb"] = pd.to_numeric(all_pe_df["pb"], errors="coerce")

        conn = sqlite3.connect(DB_PATH)
        # 用 UPDATE 逐行更新
        updated = 0
        for _, row in all_pe_df.iterrows():
            if pd.notna(row["pe_ttm"]) or pd.notna(row["pb"]):
                conn.execute(
                    "UPDATE stock_daily SET peTTM=?, pbMRQ=? WHERE trade_date=? AND stock_code=?",
                    (row.get("pe_ttm"), row.get("pb"), row["trade_date"], row["stock_code"])
                )
                updated += 1
        conn.commit()
        conn.close()
        logger.info("stock_daily PE/PB updated: %d rows", updated)

    # ── 验证 ──
    conn = sqlite3.connect(DB_PATH)
    for t in ["stock_market_cap", "stock_daily"]:
        r = conn.execute(f"SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM {t}").fetchall()[0]
        logger.info("  %s: %d rows, %s ~ %s", t, r[0], r[1], r[2])

    # 检查 stock_daily 有效 PE/PB 行数
    r = conn.execute("SELECT COUNT(*) FROM stock_daily WHERE peTTM IS NOT NULL AND peTTM > 0").fetchone()
    logger.info("  stock_daily peTTM valid: %d rows", r[0])
    r = conn.execute("SELECT COUNT(*) FROM stock_daily WHERE pbMRQ IS NOT NULL AND pbMRQ > 0").fetchone()
    logger.info("  stock_daily pbMRQ valid: %d rows", r[0])
    conn.close()

    logger.info("Done!")


if __name__ == "__main__":
    main()
