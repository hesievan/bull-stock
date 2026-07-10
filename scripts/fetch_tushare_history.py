#!/usr/bin/env python3
"""
拉取 tushare 全量历史数据，构建/补充种子数据库 (V2 引擎)。

修复说明:
  原脚本在 V2 架构清理 (commit 48b06e6) 中被误删，导致
  .github/workflows/rebuild_seed.yml 与 daily.yml 的 "full backfill"
  步骤因找不到文件而失败 (exit code 2)。
  本脚本重写为 V2 兼容版本，复用 src/data/fetcher 的增量抓取函数，
  按交易日逐日回填原始数据；下游 backfill_precompute.py 负责派生表。

抓取范围:
  - index_daily        (6+ 指数日行情, 2015 至今)
  - stock_daily        (全市场 K 线 + PE/PB/市值, 逐交易日)
  - margin_history     (融资融券)
  - northbound_history (北向资金)
  - bond_yield         (国债收益率, akshare)
  - m2_monthly         (M2 货币供应)

用法:
  python scripts/fetch_tushare_history.py
  python scripts/fetch_tushare_history.py --start 2015-01-01
"""
import sys
import os
import logging
import time
import argparse
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_env():
    if os.environ.get("TUSHARE_TOKEN"):
        return
    from pathlib import Path
    candidates = [
        Path(__file__).resolve().parent.parent / ".env",
        Path.home() / "daily_stock_analysis" / ".env",
    ]
    for p in candidates:
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("TUSHARE_TOKEN="):
                    os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1].strip('"\'')
                    return


_load_env()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "fetch_tushare_history.log"),
            mode="a", encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)


def _iter_trade_dates(start: str, end: str):
    """逐日迭代 (跳过周末)，调用方自行跳过已存在数据的日期。"""
    d = date.fromisoformat(start)
    stop = date.fromisoformat(end)
    while d <= stop:
        if d.weekday() < 5:  # 0=Mon ... 4=Fri
            yield d.isoformat()
        d += timedelta(days=1)


def main(start: str = "2015-01-01"):
    from src.data.database import init_database, get_conn, DB_PATH
    from src.data.fetcher import (
        fetch_all_index_incremental,
        fetch_daily_basic_to_stock_daily,
        fetch_margin_history,
        fetch_northbound_history,
        fetch_bond_yield_history,
        fetch_m2_history,
    )

    if not os.environ.get("TUSHARE_TOKEN"):
        raise SystemExit("TUSHARE_TOKEN 未设置，无法拉取 tushare 数据")

    init_database()

    end = date.today().strftime("%Y-%m-%d")
    logger.info("=== 全量历史回补: %s ~ %s ===", start, end)

    # ── 1. 指数日行情 (全量, 按 index 增量) ───────────────────────────────
    logger.info("=== 1/6: index_daily ===")
    fetch_all_index_incremental()

    # ── 2. 全市场 K 线 + PE/PB/市值 (逐交易日, 可断点续传) ───────────────
    logger.info("=== 2/6: stock_daily (逐交易日回填) ===")
    total_written = 0
    skipped = 0
    done = 0
    dates = list(_iter_trade_dates(start, end))
    n = len(dates)
    t0 = time.time()
    for i, td in enumerate(dates):
        try:
            written = fetch_daily_basic_to_stock_daily(td)
        except Exception as e:
            logger.error("  stock_daily %s 异常: %s", td, str(e)[:80])
            written = 0
        if written and written > 0:
            total_written += written
        else:
            skipped += 1
        done += 1
        if (i + 1) % 50 == 0:
            elapsed = time.time() - t0
            logger.info("  stock_daily 进度 %d/%d (%.1f%%), 已写 %d 行, 用时 %.1fs",
                        i + 1, n, (i + 1) / n * 100, total_written, elapsed)
    logger.info("  stock_daily 完成: 新写 %d 行, 跳过 %d 个交易日", total_written, skipped)

    # ── 3. 融资融券 ───────────────────────────────────────────────────────
    logger.info("=== 3/6: margin_history ===")
    try:
        df = fetch_margin_history(start, end)
        logger.info("  margin_history: %d 行", 0 if df.empty else len(df))
    except Exception as e:
        logger.error("  margin_history 失败: %s", str(e)[:80])

    # ── 4. 北向资金 ───────────────────────────────────────────────────────
    logger.info("=== 4/6: northbound_history ===")
    try:
        df = fetch_northbound_history(start, end)
        logger.info("  northbound_history: %d 行", 0 if df.empty else len(df))
    except Exception as e:
        logger.error("  northbound_history 失败: %s", str(e)[:80])

    # ── 5. 国债收益率 (akshare) ───────────────────────────────────────────
    logger.info("=== 5/6: bond_yield ===")
    try:
        df = fetch_bond_yield_history(start, end)
        logger.info("  bond_yield: %d 行", 0 if df.empty else len(df))
    except Exception as e:
        logger.error("  bond_yield 失败: %s", str(e)[:80])

    # ── 6. M2 货币供应 ────────────────────────────────────────────────────
    logger.info("=== 6/6: m2_monthly ===")
    try:
        fetch_m2_history(start=start[:7], end=end)
    except Exception as e:
        logger.error("  m2_monthly 失败: %s", str(e)[:80])

    # ── 校验 ──────────────────────────────────────────────────────────────
    logger.info("=== 校验 ===")
    with get_conn(DB_PATH) as conn:
        for t in ["index_daily", "stock_daily", "margin_history",
                  "northbound_history", "bond_yield", "m2_monthly"]:
            try:
                r = conn.execute(
                    f"SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM {t}"
                ).fetchone()
                logger.info("  %s: %d 行, %s ~ %s", t, r[0], r[1], r[2])
            except Exception as e:
                logger.warning("  %s 校验失败: %s", t, str(e)[:60])

    logger.info("全量历史回补完成。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="拉取 tushare 全量历史数据")
    parser.add_argument("--start", default="2015-01-01", help="起始日期 (默认 2015-01-01)")
    args = parser.parse_args()
    main(start=args.start)
