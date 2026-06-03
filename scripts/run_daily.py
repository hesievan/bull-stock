#!/usr/bin/env python3
"""
每日热度指数计算入口 (tushare + akshare, 无 baostock 依赖)

容错原则:
  每个 Step 内部 try/except, 失败记录到 step_status, 不中断后续 Step。

数据源: tushare(全市场K线/PE/PB/融资融券/北向/成分股/行业) + akshare(M2/AH溢价)

用法:
  python scripts/run_daily.py                  # 计算今日
  python scripts/run_daily.py 2026-05-29       # 计算指定日期
"""
import sys, os, logging, json, time
from datetime import date, timedelta

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
        logging.FileHandler("run_daily.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)


def _run_step(step_status, step_name, fn, *args, **kwargs):
    t0 = time.time()
    try:
        result = fn(*args, **kwargs)
        elapsed = time.time() - t0
        if result is False:
            step_status[step_name] = {"status": "SKIPPED", "detail": "no data needed", "elapsed": elapsed}
            logger.info("  step %s: SKIPPED (%.1fs)", step_name, elapsed)
        else:
            step_status[step_name] = {"status": "OK", "detail": "", "elapsed": elapsed}
            logger.info("  step %s: OK (%.1fs)", step_name, elapsed)
        return result
    except Exception as exc:
        elapsed = time.time() - t0
        msg = str(exc)[:120]
        step_status[step_name] = {"status": "FAILED", "detail": msg, "elapsed": elapsed}
        logger.error("  step %s: FAILED -- %s", step_name, msg)
        return None


def run_daily(trade_date=None):
    from src.data.database import init_database, read_dataframe
    from src.data.fetcher import (
        fetch_all_index_incremental,
        fetch_index_constituents,
        fetch_daily_basic_to_stock_daily,
        fetch_margin_history,
        fetch_northbound_history,
        fetch_bond_yield_history,
        _save, DB_PATH,
    )
    from src.indicators.calculator import calculate_heat_index
    from src.output.json_writer import (
        save_results, build_feishu_notification, get_heat_level, send_feishu_webhook
    )

    trade_date = trade_date or date.today().strftime("%Y-%m-%d")
    t_start = time.time()
    step_status = {}

    logger.info("=" * 60)
    logger.info("BULL MARKET HEAT INDEX -- Daily Run v3 (tushare only)")
    logger.info("Trade Date: %s", trade_date)
    logger.info("=" * 60)

    # ── Step 0: 基础设施 ───────────────────────────────────────────────────
    _run_step(step_status, "init_db", init_database)

    # ── Step 1: 指数日行情 (tushare) ───────────────────────────────────────
    logger.info("Step 1: Index daily (tushare)...")

    def _step1():
        return fetch_all_index_incremental()

    _run_step(step_status, "S1_index", _step1)

    # ── Step 2: 全市场K线+PE/PB/市值 (tushare daily + daily_basic) ────────
    logger.info("Step 2: Full market daily + daily_basic (tushare)...")

    def _step2():
        return fetch_daily_basic_to_stock_daily(trade_date)

    _run_step(step_status, "S2_market", _step2)

    # ── Step 3: tushare 融资融券/北向/国债 ──────────────────────────────────
    logger.info("Step 3: Tushare margin/northbound/bond...")

    def _step3():
        any_fetched = False
        for label, table, fn in [
            ("margin",       "margin_history",    lambda: fetch_margin_history(trade_date, trade_date)),
            ("northbound",   "northbound_history",lambda: fetch_northbound_history(trade_date, trade_date)),
            ("bond_yield",   "bond_yield",        lambda: fetch_bond_yield_history(trade_date, trade_date)),
        ]:
            already = read_dataframe(
                "SELECT 1 FROM " + table + " WHERE trade_date=? LIMIT 1",
                params=(trade_date,))
            if not already.empty:
                step_status["S3_" + label] = {"status": "SKIPPED", "detail": "already in db", "elapsed": 0}
                continue
            sub = _run_step(step_status, "S3_" + label, fn)
            if sub is not None and not sub.empty:
                _save(sub, table)
                any_fetched = True
        return any_fetched

    _run_step(step_status, "S3_tushare", _step3)

    # ── Step 4: AH溢价 (akshare) ──────────────────────────────────────────
    logger.info("Step 4: AH premium index (akshare)...")

    def _step4():
        from scripts.ah_premium import fetch_ah_premium_index
        td, premium = fetch_ah_premium_index(trade_date)
        if premium is None:
            logger.warning("AH premium: 计算失败, 使用已有数据")
            return False
        logger.info("AH premium: %s -> %.4f", td, premium)
        return True

    _run_step(step_status, "S4_ah_premium", _step4)

    # ── Step 5: 计算热度指数 ────────────────────────────────────────────────
    logger.info("Step 5: Calculating heat index...")

    def _step5():
        res = calculate_heat_index(trade_date=trade_date)
        if res is None or res.get("composite_score") is None:
            raise RuntimeError("heat index composite_score is None")
        return res

    result = _run_step(step_status, "S5_calc", _step5)

    if result is None or result.get("composite_score") is None:
        logger.error("S5 FAILED -- writing fallback result for debug")
        result = {
            "trade_date": trade_date, "composite_score": None,
            "dim_valuation": None, "dim_fund": None, "dim_sentiment": None,
            "dim_technical": None, "dim_structure": None, "indicators": {},
        }

    # ── Step 6: 保存结果 ────────────────────────────────────────────────────
    logger.info("Step 6: Saving results...")

    def _step6():
        save_results(result)
        out_dir = os.path.join(os.path.dirname(__file__), "..", "web", "data")
        os.makedirs(out_dir, exist_ok=True)
        n_ok = sum(1 for v in step_status.values() if v["status"] == "OK")
        n_fail = sum(1 for v in step_status.values() if v["status"] == "FAILED")
        n_skip = sum(1 for v in step_status.values() if v["status"] == "SKIPPED")
        status_out = {
            "trade_date": trade_date,
            "generated_at": date.today().strftime("%Y-%m-%d %H:%M:%S"),
            "steps": dict(step_status),
            "n_ok": n_ok, "n_failed": n_fail, "n_skipped": n_skip,
        }
        with open(os.path.join(out_dir, "run_status.json"), "w", encoding="utf-8") as sf:
            json.dump(status_out, sf, ensure_ascii=False, indent=2)
        return True

    _run_step(step_status, "S6_save", _step6)

    # ── Step 7: 板块热度 ────────────────────────────────────────────────────
    logger.info("Step 7: Sector heat...")

    def _step7():
        from src.indicators.calculator import calculate_sector_heat
        sector_results = calculate_sector_heat(trade_date, DB_PATH)
        if sector_results:
            out_dir = os.path.join(os.path.dirname(__file__), "..", "web", "data")
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, "sectors.json"), "w", encoding="utf-8") as _f:
                json.dump(sector_results, _f, ensure_ascii=False, indent=2)
            logger.info("Step 7: Wrote %d sectors", len(sector_results))
            return sector_results
        return None

    _run_step(step_status, "S7_sectors", _step7)

    # ── Step 8: 最终保存 (含板块热度) ────────────────────────────────────────
    logger.info("Step 8: Saving final results (with sectors)...")

    def _step8():
        sector_results = None
        sectors_file = os.path.join(os.path.dirname(__file__), "..", "web", "data", "sectors.json")
        if os.path.exists(sectors_file):
            with open(sectors_file) as f:
                sector_results = json.load(f)
        result["sectors_top5"] = (sector_results or [])[:5]
        save_results(result)
        return True

    _run_step(step_status, "S8_final_save", _step8)

    # ── Step 9: 飞书通知 ────────────────────────────────────────────────────
    logger.info("Step 9: Feishu notification...")

    def _step9():
        history_file = os.path.join(os.path.dirname(__file__), "..", "web", "data", "history.json")
        history = []
        if os.path.exists(history_file):
            with open(history_file) as f:
                try:
                    history = json.load(f)
                except Exception:
                    history = []
        msg = build_feishu_notification(result, history=history)
        if msg is None:
            logger.info("  Notification suppressed by debounce logic")
            return False
        notif_file = os.path.join(os.path.dirname(__file__), "..", "web", "data", "notification.txt")
        with open(notif_file, "w", encoding="utf-8") as nf:
            nf.write(msg)
        webhook_url = os.environ.get("FEISHU_WEBHOOK", "")
        if webhook_url:
            try:
                send_feishu_webhook(msg, webhook_url=webhook_url)
            except Exception as hook_exc:
                logger.warning("Feishu webhook failed: %s", str(hook_exc)[:80])
        return True

    _run_step(step_status, "S9_notify", _step9)

    # ── 最终汇总 ────────────────────────────────────────────────────────────
    elapsed = time.time() - t_start
    n_ok = sum(1 for v in step_status.values() if v["status"] == "OK")
    n_fail = sum(1 for v in step_status.values() if v["status"] == "FAILED")
    n_skip = sum(1 for v in step_status.values() if v["status"] == "SKIPPED")

    logger.info("=" * 60)
    logger.info("RUN SUMMARY: %d OK / %d FAILED / %d SKIPPED (%.1fs)",
                n_ok, n_fail, n_skip, elapsed)
    for sn, sv in step_status.items():
        if sv["status"] != "OK":
            logger.info("  [%s] %s: %s", sv["status"], sn, sv.get("detail", ""))
    logger.info("=" * 60)

    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Daily Heat Index Calculation")
    parser.add_argument("trade_date", nargs="?", help="Trade date (YYYY-MM-DD)")
    args = parser.parse_args()
    run_daily(args.trade_date)
