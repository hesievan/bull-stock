#!/usr/bin/env python3
"""
批量回填 daily_seal_rate 历史数据 (本地计算, 无需 API)。

从 stock_daily 的 OHLC 数据计算涨停封板率:
  - 涨停价 = round(pre_close * (1 + limit_factor), 2)
  - 触板: high >= 涨停价
  - 涨停: close >= 涨停价
  - 封板率 = 涨停数 / 触板数

支持断点续传: 跳过已有数据的日期。
"""
import sys
import os
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def main():
    from src.data.database import DB_PATH, get_conn
    from src.data.fetcher import fetch_limit_list
    import sqlite3

    db = DB_PATH
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM stock_daily ORDER BY trade_date"
    ).fetchall()
    all_dates = [r[0] for r in rows]

    existing = set()
    try:
        ex_rows = conn.execute("SELECT trade_date FROM daily_seal_rate").fetchall()
        existing = {r[0] for r in ex_rows}
    except Exception:
        pass
    conn.close()

    need = [d for d in all_dates if d not in existing]
    logger.info("Total trade dates: %d, already have: %d, need to fetch: %d",
                len(all_dates), len(existing), len(need))

    if not need:
        logger.info("Nothing to backfill, daily_seal_rate is up-to-date")
        return

    ok = 0
    skip = 0
    fail = 0
    t0 = time.time()

    for i, td in enumerate(need):
        try:
            result = fetch_limit_list(td)
            if result:
                ok += 1
            else:
                skip += 1
        except Exception as e:
            logger.error("  %s FAILED: %s", td, str(e)[:80])
            fail += 1

        if (i + 1) % 200 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            eta = (len(need) - i - 1) / rate if rate > 0 else 0
            logger.info("  Progress: %d/%d (%.1f%%) — ok=%d skip=%d fail=%d — %.1fs elapsed, ETA %.0fs",
                        i + 1, len(need), (i + 1) / len(need) * 100,
                        ok, skip, fail, elapsed, eta)

    elapsed = time.time() - t0
    logger.info("=" * 60)
    logger.info("Backfill complete: %d ok / %d skip / %d fail / %d total (%.1fs)",
                ok, skip, fail, len(need), elapsed)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
