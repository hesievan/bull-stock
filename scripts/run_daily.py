#!/usr/bin/env python3
"""
每日热度指数计算入口 (三源合一版)
数据源: baostock(指数/个股K线) + tushare(融资融券/北向/国债) + akshare(AH溢价)

用法:
  python scripts/run_daily.py                  # 计算今日
  python scripts/run_daily.py 2026-05-29       # 计算指定日期
  python scripts/run_daily.py --backfill       # 回测历史(2015-01-01起)
"""
import sys
import os
import logging
import json
import time
from datetime import date, timedelta

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
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("run_daily.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)


def run_daily(trade_date: str = None):
    from src.data.database import init_database, read_dataframe
    from src.data.fetcher import (
        bs_login, bs_logout,
        fetch_all_index_incremental,
        fetch_index_constituents,
        fetch_stocks_latest_day,
        fetch_daily_basic_to_stock_daily,
        fetch_margin_history,
        fetch_northbound_history,
        fetch_bond_yield_history,
        _save,
    )
    from src.indicators.calculator import calculate_heat_index
    from src.output.json_writer import save_results, build_feishu_notification, get_heat_level, send_feishu_webhook

    trade_date = trade_date or date.today().strftime("%Y-%m-%d")
    start_time = time.time()

    logger.info("=" * 60)
    logger.info("BULL MARKET HEAT INDEX — Daily Run")
    logger.info("Trade Date: %s", trade_date)
    logger.info("=" * 60)

    init_database()
    bs_login()

    try:
        # Step 1: 指数日行情 (baostock)
        logger.info("Step 1: Index daily (baostock)...")
        fetch_all_index_incremental()

        # Step 2: 成分股最新K线 (baostock)
        logger.info("Step 2: Stock daily K-lines (baostock)...")
        all_codes = set()
        for idx_name in ["hs300", "sz50", "zz500"]:
            df = fetch_index_constituents(idx_name)
            if not df.empty:
                all_codes.update(df["code"].tolist())
        logger.info("Fetching %d stocks for %s...", len(all_codes), trade_date)
        fetch_stocks_latest_day(list(all_codes), trade_date)

        # Step 3: tushare daily_basic (全市场 PE/PB/市值)
        logger.info("Step 3: Full market PE/PB (tushare daily_basic)...")
        fetch_daily_basic_to_stock_daily(trade_date)

        # Step 4: tushare (融资融券/北向/国债)
        logger.info("Step 4: Tushare data (margin/northbound/bond)...")
        _fetch_tushare_if_needed(trade_date)

    finally:
        bs_logout()

    # Step 5: 计算热度指数
    logger.info("Step 5: Calculating heat index...")
    result = calculate_heat_index(trade_date=trade_date)

    if result["composite_score"] is None:
        logger.error("Failed to calculate heat index!")
        return None

    # Step 6: 保存结果
    logger.info("Step 6: Saving results...")
    save_results(result)

    # Step 7: 红区飞书通知
    level = get_heat_level(result["composite_score"])
    if level == "red":
        history_file = os.path.join(os.path.dirname(__file__), "..", "web", "data", "history.json")
        red_days = 0
        if os.path.exists(history_file):
            with open(history_file, "r") as f:
                history = json.load(f)
            for h in reversed(history):
                if get_heat_level(h["composite_score"]) == "red":
                    red_days += 1
                else:
                    break
        msg = build_feishu_notification(result, red_days)
        logger.info("Red zone notification:\n%s", msg)
        notif_file = os.path.join(os.path.dirname(__file__), "..", "web", "data", "notification.txt")
        with open(notif_file, "w", encoding="utf-8") as f:
            f.write(msg)

    elapsed = time.time() - start_time
    logger.info("Completed in %.1f seconds", elapsed)
    logger.info("Composite Score: %.1f", result["composite_score"])
    return result


def _fetch_tushare_if_needed(trade_date: str):
    """当日数据已存在则跳过 tushare（带超时保护）"""
    from src.data.database import read_dataframe
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    def _call_with_timeout(fn, args, label, timeout=60):
        """在线程中执行 fn(*args)，超时返回 None"""
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(fn, *args)
                result = future.result(timeout=timeout)
            if result is None or (hasattr(result, 'empty') and result.empty):
                logger.warning("tushare %s: returned empty", label)
            else:
                logger.info("tushare %s: got %d rows", label,
                            len(result) if hasattr(result, '__len__') else 1)
            return result
        except FuturesTimeout:
            logger.warning("tushare %s: TIMEOUT after %ds, skipping", label, timeout)
            return None
        except Exception as e:
            logger.error("tushare %s: %s", label, str(e)[:100])
            return None

    from src.data.fetcher import fetch_margin_history, fetch_northbound_history, fetch_bond_yield_history, _save

    # 融资融券
    existing = read_dataframe(
        "SELECT 1 FROM margin_history WHERE trade_date=? LIMIT 1",
        params=(trade_date,))
    if existing.empty:
        df = _call_with_timeout(fetch_margin_history, (trade_date, trade_date), "margin", 60)
        if df is not None and not df.empty:
            _save(df, "margin_history")
        else:
            logger.warning("No margin data for %s", trade_date)

    # 北向资金
    existing = read_dataframe(
        "SELECT 1 FROM northbound_history WHERE trade_date=? LIMIT 1",
        params=(trade_date,))
    if existing.empty:
        df = _call_with_timeout(fetch_northbound_history, (trade_date, trade_date), "northbound", 60)
        if df is not None and not df.empty:
            _save(df, "northbound_history")
        else:
            logger.warning("No northbound data for %s", trade_date)

    # 国债收益率
    existing = read_dataframe(
        "SELECT 1 FROM bond_yield WHERE trade_date=? LIMIT 1",
        params=(trade_date,))
    if existing.empty:
        df = _call_with_timeout(fetch_bond_yield_history, (trade_date, trade_date), "bond_yield", 60)
        if df is not None and not df.empty:
            _save(df, "bond_yield")
        else:
            logger.warning("No bond_yield data for %s", trade_date)


def run_backfill(start_date: str = "2015-01-01", end_date: str = None):
    """历史数据回测"""
    from src.data.fetcher import fetch_all_history

    end_date = end_date or date.today().strftime("%Y-%m-%d")
    logger.info("=" * 60)
    logger.info("BACKFILL: %s to %s", start_date, end_date)
    logger.info("=" * 60)

    from src.data.database import init_database
    init_database()
    fetch_all_history(start_date, end_date)

    # 逐日计算
    from src.indicators.calculator import calculate_heat_index
    from src.output.json_writer import save_results

    current = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    results = []
    while current <= end:
        if current.weekday() < 5:
            td = current.strftime("%Y-%m-%d")
            try:
                r = calculate_heat_index(trade_date=td)
                if r["composite_score"] is not None:
                    results.append(r)
                    logger.info("%s: %.1f", td, r["composite_score"])
            except Exception as e:
                logger.warning("Failed for %s: %s", td, e)
        current += timedelta(days=1)

    history = [{"trade_date": r["trade_date"], "composite_score": r["composite_score"],
                "dim_valuation": r["dim_valuation"], "dim_fund": r["dim_fund"],
                "dim_sentiment": r["dim_sentiment"], "dim_technical": r["dim_technical"],
                "dim_structure": r["dim_structure"]} for r in results]

    output_dir = os.path.join(os.path.dirname(__file__), "..", "web", "data")
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "history.json"), "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    logger.info("Backfill complete: %d days", len(results))
    return results


if __name__ == "__main__":
    if "--backfill" in sys.argv:
        idx = sys.argv.index("--backfill")
        start = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "2015-01-01"
        run_backfill(start_date=start)
    else:
        date_arg = sys.argv[1] if len(sys.argv) > 1 else None
        run_daily(trade_date=date_arg)
