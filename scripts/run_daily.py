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
import sys
import os
import logging
import json
import time
from datetime import date

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
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run_daily.log"),
            encoding="utf-8"
        ),
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
    from src.data.database import init_database, read_dataframe, DB_PATH
    from src.data.fetcher import (
        fetch_all_index_incremental,
        fetch_daily_basic_to_stock_daily,
        fetch_margin_history,
        fetch_northbound_history,
        fetch_bond_yield_history,
        _save,
    )
    from src.indicators.calculator import calculate_heat_index
    from src.output.json_writer import (
        save_results, build_feishu_notification, send_feishu_webhook
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

    # ── Step 2.5: 更新 index_daily_pe (PE/PB 中位数, 供 ERP 和估值使用) ──
    logger.info("Step 2.5: Updating index_daily_pe...")

    def _step25():
        from src.data.database import update_index_daily_pe
        return update_index_daily_pe(trade_date)

    _run_step(step_status, "S25_index_pe", _step25)

    # ── Step 2.6: 全市场流通市值 (daily_circ_mv, 供融资余额比使用) ─────────
    logger.info("Step 2.6: Computing daily_circ_mv...")

    def _step26():
        from src.data.database import compute_daily_circ_mv
        return compute_daily_circ_mv(trade_date)

    _run_step(step_status, "S26_circ_mv", _step26)

    # ── Step 2.7: 涨跌家数比 (daily_updown, 预计算表) ────────────────────────
    logger.info("Step 2.7: Computing daily_updown...")

    def _step27():
        from src.data.database import compute_daily_updown
        return compute_daily_updown(trade_date)

    _run_step(step_status, "S27_updown", _step27)

    # ── Step 2.8: 涨停占比和涨跌停比 (daily_limit, 预计算表) ──────────────────
    logger.info("Step 2.8: Computing daily_limit...")

    def _step28():
        from src.data.database import compute_daily_limit
        return compute_daily_limit(trade_date)

    _run_step(step_status, "S28_limit", _step28)

    # ── Step 2.9: 破净率 (daily_below_net, 预计算表) ─────────────────────────
    logger.info("Step 2.9: Computing daily_below_net...")

    def _step29():
        from src.data.database import compute_daily_below_net
        return compute_daily_below_net(trade_date)

    _run_step(step_status, "S29_below_net", _step29)

    # ── Step 2.10: 均线排列比 (daily_ma_alignment, 预计算表) ──────────────────
    logger.info("Step 2.10: Computing daily_ma_alignment...")

    def _step30():
        from src.data.database import compute_daily_ma_alignment
        return compute_daily_ma_alignment(trade_date)

    _run_step(step_status, "S30_ma_alignment", _step30)

    # ── Step 2.4: 预计算表陈旧检测 ────────────────────────────────────────────
    logger.info("Step 2.4: Checking precompute table staleness...")

    def _step24():
        from src.data.database import check_precompute_staleness
        stale_results = check_precompute_staleness(trade_date)
        stale_tables = [r for r in stale_results if r["stale"]]
        if stale_tables:
            logger.warning("Stale precompute tables (%d):", len(stale_tables))
            for r in stale_tables:
                fallback_info = "yes" if r["has_fallback"] else "NO"
                logger.warning(
                    "  %s (%s): latest=%s, gap=%sd, max=%sd, fallback=%s",
                    r["table"], r["desc"], r["latest_date"],
                    r["gap_days"], r["max_gap_days"], fallback_info,
                )
        else:
            logger.info("All precompute tables fresh")
        step_status["precompute_staleness"] = stale_results
        return True

    _run_step(step_status, "S24_precompute_check", _step24)

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
            "dim_valuation": None, "dim_macro": None, "dim_fund": None,
            "dim_sentiment": None, "dim_technical": None, "dim_structure": None,
            "indicators": {},
        }

    # ── Step 5.5: 指数牛市见顶预判 ────────────────────────────────────────────
    logger.info("Step 5.5: Computing index overheating scores...")

    def _step55():
        from src.indicators.index_heat import compute_index_heat
        idx_results = compute_index_heat(trade_date=trade_date)
        out_dir = os.path.join(os.path.dirname(__file__), "..", "web", "data")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "index_heat.json"), "w", encoding="utf-8") as f:
            json.dump(idx_results, f, ensure_ascii=False, indent=2)
        n_ok = sum(1 for r in idx_results if "error" not in r)
        logger.info("Index heat: %d/%d computed", n_ok, len(idx_results))
        return n_ok > 0

    _run_step(step_status, "S55_index_heat", _step55)

    # ── Step 6: 保存结果 ────────────────────────────────────────────────────
    logger.info("Step 6: Saving results...")

    def _step6():
        save_results(result)
        out_dir = os.path.join(os.path.dirname(__file__), "..", "web", "data")
        os.makedirs(out_dir, exist_ok=True)
        n_ok = sum(1 for v in step_status.values() if isinstance(v, dict) and v.get("status") == "OK")
        n_fail = sum(1 for v in step_status.values() if isinstance(v, dict) and v.get("status") == "FAILED")
        n_skip = sum(1 for v in step_status.values() if isinstance(v, dict) and v.get("status") == "SKIPPED")
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
        from src.indicators.sector_calculator import calculate_sector_heat
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

        # Bark 推送（完整信息，与飞书通知内容一致）
        try:
            from src.output.json_writer import send_bark, get_heat_level
            bark_status = "timeSensitive" if get_heat_level(result.get("composite_score", 0)) == "red" else "active"
            score = result.get("composite_score", 0)
            send_bark(
                title=f"🔥 热度指数 {score:.1f}",
                body=msg,
                level=bark_status,
                group="HeatIndex",
            )
        except Exception as bark_exc:
            logger.warning("Bark push failed: %s", str(bark_exc)[:80])
        return True

    _run_step(step_status, "S9_notify", _step9)

    # ── 最终汇总 ────────────────────────────────────────────────────────────
    elapsed = time.time() - t_start
    n_ok = sum(1 for v in step_status.values() if isinstance(v, dict) and v.get("status") == "OK")
    n_fail = sum(1 for v in step_status.values() if isinstance(v, dict) and v.get("status") == "FAILED")
    n_skip = sum(1 for v in step_status.values() if isinstance(v, dict) and v.get("status") == "SKIPPED")

    logger.info("=" * 60)
    logger.info("RUN SUMMARY: %d OK / %d FAILED / %d SKIPPED (%.1fs)",
                n_ok, n_fail, n_skip, elapsed)
    for sn, sv in step_status.items():
        if isinstance(sv, dict) and sv.get("status") != "OK":
            logger.info("  [%s] %s: %s", sv["status"], sn, sv.get("detail", ""))
    logger.info("=" * 60)

    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Daily Heat Index Calculation")
    parser.add_argument("trade_date", nargs="?", help="Trade date (YYYY-MM-DD)")
    args = parser.parse_args()
    run_daily(args.trade_date)
