#!/usr/bin/env python3
"""
每日热度指数计算入口 (三源合一版 v2 -- Step 级容错重构)

容错原则:
  每个 Step 内部 try/except, 失败记录到 step_status, 不中断后续 Step。
  最终汇总 step_status 决定是否可计算 & 通知用户哪些 Step 跳过了。

数据源: baostock(指数/个股K线) + tushare(融资融券/北向/国债) + 东方财富curl(AH溢价HSAHP)

用法:
  python scripts/run_daily.py                  # 计算今日
  python scripts/run_daily.py 2026-05-29       # 计算指定日期
  python scripts/run_daily.py --backfill       # 回测历史(2015-01-01起)
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


# ── Step 执行器 ────────────────────────────────────────────────────────────────

def _run_step(step_status, step_name, fn, *args, **kwargs):
    """
    通用 Step 执行器: 失败记入 step_status, 不中断后续步骤。
    step_status[step_name] = {"status": "OK"|"FAILED"|"SKIPPED", "detail": str, "elapsed": float}
    """
    t0 = time.time()
    try:
        result = fn(*args, **kwargs)
        elapsed = time.time() - t0
        if result is False:
            detail = "no data needed"
            step_status[step_name] = {"status": "SKIPPED", "detail": detail, "elapsed": elapsed}
            logger.info("  step %s: SKIPPED (%s) %.1fs", step_name, detail, elapsed)
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


# ── 主流程 ────────────────────────────────────────────────────────────────────

def run_daily(trade_date=None):
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
        fetch_ah_premium,
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
    logger.info("BULL MARKET HEAT INDEX -- Daily Run v2")
    logger.info("Trade Date: %s", trade_date)
    logger.info("=" * 60)

    # ── Step 0: 基础设施 ───────────────────────────────────────────────────
    _run_step(step_status, "init_db", init_database)

    # ── Step 1: 指数日行情 (baostock) ───────────────────────────────────────
    logger.info("Step 1: Index daily (baostock)...")

    def _step1():
        _run_step(step_status, "bs_login", bs_login)
        _run_step(step_status, "fetch_index", fetch_all_index_incremental)

    _run_step(step_status, "S1_index", _step1)

    # ── Step 2: 成分股最新K线 (baostock) ────────────────────────────────────
    # 仅在 stock_daily 当天数据不足时拉取(避免每次 280只×0.3s ≈ 90s)
    logger.info("Step 2: Stock daily K-lines (baostock, if needed)...")

    def _step2():
        existing = read_dataframe(
            "SELECT COUNT(*) as cnt FROM stock_daily WHERE trade_date=?",
            params=(trade_date,))
        if not existing.empty and int(existing.iloc[0]["cnt"]) > 100:
            logger.info("  stock_daily already has %d rows for %s, skip baostock stock fetch",
                        int(existing.iloc[0]["cnt"]), trade_date)
            return False
        # 只有数据不足时才拉成分股K线
        all_codes = set()
        for idx_name in ["hs300", "sz50", "zz500"]:
            df = fetch_index_constituents(idx_name)
            if df is not None and not df.empty:
                all_codes.update(df["code"].tolist())
        if not all_codes:
            logger.warning("S2: No constituent codes, skipping stock fetch")
            return False
        logger.info("Fetching %d stocks for %s...", len(all_codes), trade_date)
        return fetch_stocks_latest_day(list(all_codes), trade_date)

    _run_step(step_status, "S2_stocks", _step2)

    # ── Step 3: tushare daily_basic ──────────────────────────────────────────
    logger.info("Step 3: PE/PB via tushare daily_basic...")

    def _step3():
        existing = read_dataframe(
            "SELECT COUNT(*) as cnt FROM stock_daily WHERE trade_date=?",
            params=(trade_date,))
        if not existing.empty and int(existing.iloc[0]["cnt"]) > 100:
            n = int(existing.iloc[0]["cnt"])
            logger.info("  stock_daily already has %d rows for %s, skip tushare", n, trade_date)
            return False
        return fetch_daily_basic_to_stock_daily(trade_date)

    _run_step(step_status, "S3_daily_basic", _step3)

    # ── Step 4: tushare 融资融券/北向/国债 ──────────────────────────────────
    logger.info("Step 4: Tushare margin/northbound/bond...")

    def _step4():
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
                step_status["S4_" + label] = {"status": "SKIPPED", "detail": "already in db", "elapsed": 0}
                continue
            sub = _run_step(step_status, "S4_" + label, fn)
            if sub is not None and not sub.empty:
                _save(sub, table)
                any_fetched = True
        return any_fetched

    _run_step(step_status, "S4_tushare", _step4)

    # ── baostock 安全登出 ───────────────────────────────────────────────────
    try:
        bs_logout()
        step_status["bs_logout"] = {"status": "OK", "detail": "", "elapsed": 0}
    except Exception:
        step_status["bs_logout"] = {"status": "SKIPPED", "detail": "login may have failed", "elapsed": 0}

    # ── Step 4.5: AH溢价 ────────────────────────────────────────────────────
    logger.info("Step 4.5: AH premium index (HSAHP)...")

    def _step45():
        ah_df = fetch_ah_premium()
        if ah_df is None or ah_df.empty:
            logger.warning("AH premium: empty, use existing data")
            return False
        import sqlite3 as _sq3
        conn2 = _sq3.connect(DB_PATH)
        conn2.execute("DELETE FROM ah_premium")
        ah_df.to_sql("ah_premium", conn2, if_exists="append", index=False)
        conn2.commit()
        conn2.close()
        logger.info("AH premium: %d rows updated", len(ah_df))
        return True

    _run_step(step_status, "S45_ah_premium", _step45)

    # ── Step 5: 计算热度指数 ────────────────────────────────────────────────
    logger.info("Step 5: Calculating heat index...")

    def _step5():
        res = calculate_heat_index(trade_date=trade_date)
        if res is None or res.get("composite_score") is None:
            raise RuntimeError("heat index composite_score is None")
        return res

    result = _run_step(step_status, "S5_calc", _step5)

    # 降级: 核心计算失败也写出 status 供排查, 不返回 None给上层
    if result is None or result.get("composite_score") is None:
        logger.error("S5 FAILED -- writing fallback result for debug")
        result = {
            "trade_date": trade_date,
            "composite_score": None,
            "dim_valuation": None, "dim_fund": None,
            "dim_sentiment": None, "dim_technical": None, "dim_structure": None,
            "indicators": {},
        }

    # ── Step 6: 保存结果 ────────────────────────────────────────────────────
    logger.info("Step 6: Saving results...")

    def _step6():
        save_results(result)
        # 写出 run_status.json 供前端/debug
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

    # ── Step 7: 飞书通知 (仅红区) ──────────────────────────────────────────
    composite_score = result.get("composite_score")
    if composite_score is not None:
        logger.info("Step 7: Notification check (score=%.1f)...", composite_score)

        def _step7():
            level = get_heat_level(composite_score)
            if level != "red":
                logger.info("  Not red zone (%.1f), skip notification", composite_score)
                return False
            history_file = os.path.join(os.path.dirname(__file__), "..", "web", "data", "history.json")
            history = []
            if os.path.exists(history_file):
                with open(history_file, "r") as hf:
                    try:
                        history = json.load(hf)
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
                    logger.warning("Feishu webhook failed: %s (msg saved)", str(hook_exc)[:80])
            return True

        _run_step(step_status, "S7_notify", _step7)
    else:
        logger.warning("Step 7: SKIP (composite_score None)")

    # ── Step 8: 板块热度 ────────────────────────────────────────────────────
    logger.info("Step 8: Sector heat...")
    try:
        from src.indicators.calculator import calculate_sector_heat
        sector_results = calculate_sector_heat(trade_date, DB_PATH)
        if sector_results:
            for r in sector_results:
                r["_trade_date"] = trade_date
            out_dir = os.path.join(os.path.dirname(__file__), "..", "web", "data")
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, "sectors.json"), "w", encoding="utf-8") as _f:
                json.dump(sector_results, _f, ensure_ascii=False, indent=2)
            step_status["S8_sectors"] = {
                "status": "OK", "detail": str(len(sector_results)) + " sectors", "elapsed": 0}
            logger.info("Step 8: Wrote %d sectors", len(sector_results))
        else:
            step_status["S8_sectors"] = {"status": "SKIPPED", "detail": "no results", "elapsed": 0}
    except Exception as exc:
        step_status["S8_sectors"] = {"status": "FAILED", "detail": str(exc)[:120], "elapsed": 0}
        logger.error("S8 FAILED: %s", str(exc)[:80])

    # ── 最终汇总 ────────────────────────────────────────────────────────────
    elapsed = time.time() - t_start
    n_ok = sum(1 for v in step_status.values() if v["status"] == "OK")
    n_fail = sum(1 for v in step_status.values() if v["status"] == "FAILED")
    n_skip = sum(1 for v in step_status.values() if v["status"] == "SKIPPED")

    logger.info("=" * 60)
    logger.info("RUN SUMMARY for %s: %d OK / %d FAILED / %d SKIPPED (%.1fs)",
                trade_date, n_ok, n_fail, n_skip, elapsed)
    for sn, sv in step_status.items():
        if sv["status"] != "OK":
            logger.info("  [%s] %s: %s", sv["status"], sn, sv["detail"])
    cs = result.get("composite_score")
    logger.info("Composite Score: %s", "%.1f" % cs if cs is not None else "FAILED")
    logger.info("=" * 60)

    result["_step_status"] = step_status
    return result


# ── 回测入口 ──────────────────────────────────────────────────────────────────

def run_backfill(start_date="2015-01-01", end_date=None):
    """历史数据回测 (Step 粒度容错)"""
    from src.data.fetcher import fetch_all_history
    end_date = end_date or date.today().strftime("%Y-%m-%d")
    logger.info("=" * 60)
    logger.info("BACKFILL: %s to %s", start_date, end_date)
    logger.info("=" * 60)

    init_database()
    fetch_all_history(start_date, end_date)

    from src.indicators.calculator import calculate_heat_index
    from src.output.json_writer import save_results

    current = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    results, fail_dates = [], []
    while current <= end:
        if current.weekday() < 5:
            td = current.strftime("%Y-%m-%d")
            try:
                r = calculate_heat_index(trade_date=td)
                if r["composite_score"] is not None:
                    results.append(r)
                    logger.info("%s: %.1f", td, r["composite_score"])
            except Exception as e:
                fail_dates.append(td)
                logger.warning("Backfill failed for %s: %s", td, str(e)[:60])
        current += timedelta(days=1)

    if fail_dates:
        logger.warning("Backfill: %d dates failed: %s",
                       len(fail_dates), str(fail_dates[:10]))

    history = [
        {
            "trade_date": r["trade_date"], "composite_score": r["composite_score"],
            "dim_valuation": r["dim_valuation"], "dim_fund": r["dim_fund"],
            "dim_sentiment": r["dim_sentiment"], "dim_technical": r["dim_technical"],
            "dim_structure": r["dim_structure"],
        }
        for r in results
    ]

    output_dir = os.path.join(os.path.dirname(__file__), "..", "web", "data")
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "history.json"), "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    logger.info("Backfill complete: %d days (%d failed)", len(results), len(fail_dates))
    return results


# ── CLI 入口 ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--backfill" in sys.argv:
        idx = sys.argv.index("--backfill")
        start = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "2015-01-01"
        run_backfill(start_date=start)
    else:
        date_arg = sys.argv[1] if len(sys.argv) > 1 else None
        run_daily(trade_date=date_arg)
