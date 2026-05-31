#!/usr/bin/env python3
"""
每日热度指数计算入口
使用方式：
  python run_daily.py                  # 计算今日
  python run_daily.py 2026-05-30       # 计算指定日期
  python run_daily.py --backfill       # 回测历史
"""
import sys
import os
import logging
import json
import time
from datetime import date, timedelta

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    from src.data.database import init_database, save_dataframe, get_conn, record_meta
    from src.data.fetcher import fetch_all_index_incremental
    from src.indicators.calculator import calculate_heat_index
    from src.output.json_writer import save_results, build_feishu_notification

    trade_date = trade_date or date.today().strftime("%Y-%m-%d")
    start_time = time.time()

    logger.info("=" * 60)
    logger.info("BULL MARKET HEAT INDEX — Daily Run")
    logger.info("Trade Date: %s", trade_date)
    logger.info("=" * 60)

    # 1. 确保数据库已初始化
    init_database()

    # 2. 拉取增量数据
    logger.info("Step 1: Fetching incremental data...")
    fetch_all_index_incremental()

    # 3. 计算热度指数
    logger.info("Step 2: Calculating heat index...")
    result = calculate_heat_index(trade_date=trade_date)

    if result["composite_score"] is None:
        logger.error("Failed to calculate heat index!")
        return None

    # 4. 保存结果
    logger.info("Step 3: Saving results...")
    index_data = save_results(result)

    # 5. 检查是否红区，生成飞书通知
    from src.output.json_writer import get_heat_level
    level = get_heat_level(result["composite_score"])
    if level == "red":
        # 计算连续红区天数
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
        notification_file = os.path.join(os.path.dirname(__file__), "..", "web", "data", "notification.txt")
        with open(notification_file, "w", encoding="utf-8") as f:
            f.write(msg)

    elapsed = time.time() - start_time
    logger.info("Completed in %.1f seconds", elapsed)
    logger.info("Composite Score: %.1f", result["composite_score"])

    return result


def run_backfill(start_date: str = "2015-01-01", end_date: str = None):
    """历史数据回测"""
    from src.data.database import init_database
    from src.data.fetcher import fetch_all_index_history
    from src.indicators.calculator import calculate_heat_index
    from src.output.json_writer import save_results

    end_date = end_date or date.today().strftime("%Y-%m-%d")

    logger.info("=" * 60)
    logger.info("BACKFILL: %s to %s", start_date, end_date)
    logger.info("=" * 60)

    init_database()
    fetch_all_index_history(start_date, end_date)

    # 逐日计算
    current = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    results = []
    while current <= end:
        if current.weekday() < 5:  # 仅交易日
            trade_date = current.strftime("%Y-%m-%d")
            try:
                result = calculate_heat_index(trade_date=trade_date)
                if result["composite_score"] is not None:
                    results.append(result)
                    logger.info("%s: %.1f", trade_date, result["composite_score"])
            except Exception as e:
                logger.warning("Failed for %s: %s", trade_date, e)
        current += timedelta(days=1)

    # 保存历史汇总
    history = []
    for r in results:
        history.append({
            "trade_date": r["trade_date"],
            "composite_score": r["composite_score"],
            "dim_valuation": r["dim_valuation"],
            "dim_fund": r["dim_fund"],
            "dim_sentiment": r["dim_sentiment"],
            "dim_technical": r["dim_technical"],
            "dim_structure": r["dim_structure"],
        })

    output_dir = os.path.join(os.path.dirname(__file__), "..", "web", "data")
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "history.json"), "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    logger.info("Backfill complete: %d days calculated", len(results))
    return results


if __name__ == "__main__":
    if "--backfill" in sys.argv:
        idx = sys.argv.index("--backfill")
        start = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "2015-01-01"
        run_backfill(start_date=start)
    else:
        date_arg = sys.argv[1] if len(sys.argv) > 1 else None
        run_daily(trade_date=date_arg)
