#!/usr/bin/env python3
"""
每日热度指数计算入口 (tushare + akshare, 无 baostock 依赖)

容错原则:
  每个 Step 内部 try/except, 失败记录到 step_status, 不中断后续 Step。

数据源: tushare(全市场K线/PE/PB/融资融券/北向/成分股/行业分类) + akshare(M2/AH溢价)

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


def _clean_nan(o):
    """递归清洗 NaN/±Inf → None，避免产出非法 JSON 破坏前端 fetchJSON。"""
    if isinstance(o, float):
        if o != o or abs(o) == float("inf"):  # NaN 或 ±Inf
            return None
        return o
    if isinstance(o, dict):
        return {k: _clean_nan(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean_nan(v) for v in o]
    return o


from src.config import load_dotenv_safe
from src.common import setup_logging


def _engine_mode_for_log() -> str:
    """引擎计分模式 (仅用于启动日志; 取值失败不影响主流程)"""
    try:
        from src.indicators.heat_index_v2 import ENGINE_MODE

        return ENGINE_MODE
    except Exception:
        return "unknown"


load_dotenv_safe()

_log_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# P3-E1 (#16): 统一走 common.setup_logging — HEAT_LOG_JSON=true 时 stdout 与
# run_daily.log 均输出单行 JSON (JsonFormatter, 支持 extra 结构化字段),
# 否则保持原有文本格式。行为与旧手写初始化等价 (双 handler 同 formatter)。
setup_logging(
    json_logs=bool(os.environ.get("HEAT_LOG_JSON")),
    log_file=os.path.join(_log_dir, "run_daily.log"),
)
logger = logging.getLogger(__name__)


_STEP_SEQ = {"n": 0}

# ── P0-1: step 分级 ─────────────────────────────────────────────────────────
# 致命: 失败则当日产物不可信/不可用 → 退出码 1, 阻断后续 commit 与部署
CRITICAL_STEPS = frozenset(
    {
        "init_db",  # 库不可用则一切无效
        "S1_index",  # 指数日线缺失 → regime / 指数热度全空
        "S2_market",  # 无全市场日线 → 9 计分键中多键为 None, 重归一化后不可比
        "S5_calc",  # 核心计算
        "S6_save",  # 首轮产物写出
        "S8_final_save",  # 最终产物写出
    }
)
# 降级: 失败只影响部分展示/辅助数据 → 退出码 0, 但在通知与 run_status 中标注
DEGRADED_STEPS = frozenset(
    {
        "S24_precompute_check",
        "S24c_m2",
        "S24d_m1",
        "S24f_south",
        "S24g_futures",
        "S24h_accounts",
        "S24i_etf_flow",
        "S25_index_pe",
        "S26_circ_mv",
        "S26b_total_mv",
        "S27_updown",
        "S28_limit",
        "S29_below_net",
        "S3_bond_yield",
        "S30_ma_alignment",
        "S30b_new_high",
        "S30c_turnover",
        "S3_industry",
        "S31_qvix",
        "S31b_seal_rate",
        "S31c_index_pe",
        "S3_margin",
        "S3_shenwan",
        "S3_tushare",
        "S55_index_heat",
        "S7_sectors",
        "S75_focus",
        "S9_notify",
        "S10_analyze",
    }
)
# 注: 未列入上述两集合的 step 一律按"降级"处理(非阻断), 新增 step 不会意外卡住 CI。

_DIMENSION_LABELS = {"valuation": "估值", "fund": "资金", "sentiment": "情绪", "structure": "结构"}


def _run_step(step_status, step_name, fn, *args, **kwargs):
    """执行单个 step 并把三态结果写入 step_status。

    返回语义 (P1-7):
      - ``False`` → SKIPPED (确认"无需数据", 如"表已是最新")
      - ``None``  → FAILED  (旧实现记成 OK —— 什么都没产出却显示成功)
      - 其他值    → OK
      - 抛异常    → FAILED
    """
    _STEP_SEQ["n"] += 1
    seq = _STEP_SEQ["n"]
    t0 = time.time()
    try:
        result = fn(*args, **kwargs)
        elapsed = time.time() - t0
        if result is False:
            status, detail = "SKIPPED", "no data needed"
        elif result is None:
            status, detail = "FAILED", "returned None (no result produced)"
        else:
            status, detail = "OK", ""
        step_status[step_name] = {"status": status, "detail": detail, "elapsed": elapsed}
        logger.log(
            logging.ERROR if status == "FAILED" else logging.INFO,
            "  [%02d] step %s: %s (%.1fs)%s",
            seq,
            step_name,
            status,
            elapsed,
            f" -- {detail}" if detail else "",
        )
        return result
    except Exception as exc:
        elapsed = time.time() - t0
        msg = str(exc)[:120]
        step_status[step_name] = {"status": "FAILED", "detail": msg, "elapsed": elapsed}
        logger.error("  [%02d] step %s: FAILED -- %s", seq, step_name, msg)
        return None


def _failed_steps(step_status: dict) -> set:
    """从 step_status 中筛出 FAILED 的 step 名 (忽略 precompute_staleness 等非 step 值)。"""
    return {k for k, v in step_status.items() if isinstance(v, dict) and v.get("status") == "FAILED"}


def _exit_code_for(step_status: dict) -> int:
    """按 step 分级给出进程退出码 (P0-1)。

    致命 step 失败 → 1 (阻断后续 commit/部署); 非致命失败或全部成功 → 0。
    """
    return 1 if (_failed_steps(step_status) & CRITICAL_STEPS) else 0


def _build_data_quality(result: dict, step_status: dict) -> dict:
    """合成当日数据质量摘要 (P0-2)。

    此前 ``json_writer.build_feishu_notification`` 一直在读 ``result["data_quality"]``,
    但全流程没有任何生产者 —— 那段质量告警永远不会显示, 数据缺一半时通知看起来
    一切正常。

    维度可用率取自**引擎实际参与计分的键**(engine_mode 决定 9 键或 6 键): 指标缺失
    会触发行重归一化, 使当日分数与历史不可比 —— 这比"哪个 step 失败"更贴近使用者
    关心的问题。step 级失败另附, 用于定位根因。
    """
    scoring_keys: list = []
    present: list = []
    missing: list = []
    dims: dict = {}
    stats_ok = True
    try:
        from src.indicators.heat_index_v2 import (
            DIMENSIONS,
            INDICATOR_DIMENSIONS,
            _weights_for,
        )

        weights = _weights_for(result.get("engine_mode"))
        inds = result.get("indicators") or {}
        scoring_keys = [k for k in INDICATOR_DIMENSIONS if k in weights]
        present = [k for k in scoring_keys if inds.get(k) is not None]
        missing = [k for k in scoring_keys if inds.get(k) is None]

        for dim in DIMENSIONS:
            keys = [k for k in scoring_keys if INDICATOR_DIMENSIONS[k] == dim]
            got = [k for k in keys if inds.get(k) is not None]
            if len(got) == len(keys):
                status = "ok"
            elif got:
                status = "degraded"
            else:
                status = "bad"
            dims[dim] = {
                "status": status,
                "label": _DIMENSION_LABELS.get(dim, dim),
                "available": len(got),
                "total": len(keys),
                "missing": [k for k in keys if inds.get(k) is None],
            }
    except Exception as e:  # 质量统计本身失败不应影响主流程, 但必须体现为"质量降级"
        stats_ok = False
        logger.warning("data_quality 指标维度统计失败: %s", e)

    # step_status 里除 step 记录外还混有 precompute_staleness 等非 step 值, 需过滤
    steps = {k: v for k, v in step_status.items() if isinstance(v, dict) and "status" in v}
    failed = sorted(k for k, v in steps.items() if v.get("status") == "FAILED")
    skipped = sorted(k for k, v in steps.items() if v.get("status") == "SKIPPED")
    critical_failed = [k for k in failed if k in CRITICAL_STEPS]

    if critical_failed:
        overall = "poor"
    elif failed or missing or not stats_ok:
        overall = "degraded"
    else:
        overall = "good"

    return {
        "overall_quality": overall,
        "stats_ok": stats_ok,
        "indicator_available": len(present),
        "indicator_total": len(scoring_keys),
        "missing_indicators": missing,
        "dimensions": dims,
        "failed_steps": failed,
        "skipped_steps": skipped,
        "critical_failed_steps": critical_failed,
        "degraded_failed_steps": [k for k in failed if k not in CRITICAL_STEPS],
    }


def run_daily(trade_date=None):
    from src.data.database import init_database, DB_PATH, SCHEMA_VERSION
    from src.common import runtime_meta
    from src.data.fetcher import (
        fetch_all_index_incremental,
        fetch_daily_basic_to_stock_daily,
        fetch_margin_history,
        fetch_bond_yield_history,
        _save,
    )
    from src.output.json_writer import (
        build_feishu_notification,
        get_feishu_webhook,
        save_results_v2,
        send_feishu_webhook,
    )

    trade_date = trade_date or date.today().strftime("%Y-%m-%d")
    t_start = time.time()
    step_status = {}
    _STEP_SEQ["n"] = 0

    logger.info("=" * 60)
    logger.info("BULL MARKET HEAT INDEX -- Daily Run v3 (tushare only)")
    logger.info("Trade Date: %s", trade_date)
    # P0-6: 数据库路径解析结果必须可见, 否则 "算到了别的库" 无从排查
    logger.info("Database: %s", DB_PATH)
    logger.info("Engine mode: %s", _engine_mode_for_log())
    logger.info("=" * 60)

    # ── Step 0: 基础设施 ───────────────────────────────────────────────────
    # init_database 返回 None (纯建表/迁移), 需显式包装成 True 才不会误判为 FAILED
    def _step0():
        init_database()
        return True

    _run_step(step_status, "init_db", _step0)

    # ── Step 1: 指数日行情 (tushare) ───────────────────────────────────────
    logger.info("Step 1: Index daily (tushare)...")

    def _step1():
        return fetch_all_index_incremental()

    _run_step(step_status, "S1_index", _step1)

    # ── Step 2: 全市场K线+PE/PB/市值 (tushare daily + daily_basic) ────────
    logger.info("Step 2: Full market daily + daily_basic (tushare)...")

    def _step2():
        from src.data.database import DB_PATH
        import sqlite3

        conn = sqlite3.connect(DB_PATH)
        latest = conn.execute("SELECT MAX(trade_date) FROM stock_daily").fetchone()[0]
        conn.close()
        if latest is None:
            n = fetch_daily_basic_to_stock_daily(trade_date)
            return n if n else False  # 0 行写入 = 无数据可写, 语义上等同 SKIPPED
        import datetime

        cursor = latest
        total = 0
        while cursor <= trade_date:
            n = fetch_daily_basic_to_stock_daily(cursor)
            if n:
                total += n
            cursor = (datetime.date.fromisoformat(cursor) + datetime.timedelta(days=1)).isoformat()
        return total if total > 0 else False

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

    # ── Step 2.6b: 全市场总市值 (stock_market_cap, 供巴菲特指标使用) ──────────
    logger.info("Step 2.6b: Computing daily_total_mv...")

    def _step26b():
        from src.data.database import compute_daily_total_mv

        return compute_daily_total_mv(trade_date)

    _run_step(step_status, "S26b_total_mv", _step26b)

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

    # ── Step 2.10b: 250日新高占比 (daily_new_high, 预计算表) ────────────────
    logger.info("Step 2.10b: Computing daily_new_high...")

    def _step30b():
        from src.data.database import compute_daily_new_high

        return compute_daily_new_high(trade_date)

    _run_step(step_status, "S30b_new_high", _step30b)

    # ── Step 2.10c: 换手率 (daily_turnover, 预计算表, F3 10年窗口) ──────────
    logger.info("Step 2.10c: Computing daily_turnover...")

    def _step30c():
        from scripts.backfill_precompute import _compute_daily_turnover
        from src.data.database import DB_PATH as _DB

        return _compute_daily_turnover(trade_date, _DB)

    _run_step(step_status, "S30c_turnover", _step30c)

    # ── Step 2.11: QVIX恐慌指数更新 ──────────────────────────────────────────
    logger.info("Step 2.11: Updating QVIX panic index...")

    def _step31():
        from src.data.qvix_fetcher import fetch_panic_index
        from src.data.database import DB_PATH as _DB
        import sqlite3
        import pandas as _pd
        import math as _math

        df = fetch_panic_index(timeout=60)
        if df.empty:
            return False
        conn = sqlite3.connect(_DB)
        try:
            qvix_dates = df.index.sort_values()
            target = _pd.Timestamp(trade_date)
            if target in df.index:
                row = df.loc[target]
            else:
                prev = qvix_dates[qvix_dates <= target]
                if len(prev) == 0:
                    logger.warning("QVIX step31 %s: no prior data", trade_date)
                    return False
                row = df.loc[prev[-1]]

            def _v(x):
                return None if (x is None or (isinstance(x, float) and _math.isnan(x))) else round(float(x), 4)

            conn.execute(
                """
                INSERT OR REPLACE INTO qvix_daily
                    (trade_date, qvix, qvix_50, qvix_300, qvix_1000, panic_index, concentration)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    trade_date,
                    _v(row.get("panic_index")),
                    _v(row.get("qvix_50")),
                    _v(row.get("qvix_300")),
                    _v(row.get("qvix_1000")),
                    _v(row.get("panic_index")),
                    _v(row.get("concentration")),
                ),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    _run_step(step_status, "S31_qvix", _step31)

    # ── Step 3.1b: 涨停封板率 (tushare limit_list, P0-1) ────────────────────
    logger.info("Step 3.1b: Fetching limit_list (seal rate)...")

    def _step31b():
        from src.data.fetcher import fetch_limit_list

        return fetch_limit_list(trade_date)

    _run_step(step_status, "S31b_seal_rate", _step31b)

    # ── Step 3.1c: 指数估值 (tushare index_dailybasic, P0-2) ────────────────
    logger.info("Step 3.1c: Fetching index_dailybasic (index PE/PB)...")

    def _step31c():
        from src.data.fetcher import fetch_index_dailybasic

        return fetch_index_dailybasic(trade_date)

    _run_step(step_status, "S31c_index_pe", _step31c)

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
                    r["table"],
                    r["desc"],
                    r["latest_date"],
                    r["gap_days"],
                    r["max_gap_days"],
                    fallback_info,
                )
        else:
            logger.info("All precompute tables fresh")
        step_status["precompute_staleness"] = stale_results
        return True

    _run_step(step_status, "S24_precompute_check", _step24)

    # ── Step 2.4c: M2货币供应量 (月度, tushare cn_m) ──────────────────────
    logger.info("Step 2.4c: Fetching M2 monthly data...")

    def _step24c():
        from src.data.fetcher import fetch_m2_history
        import datetime

        # 检查数据库是否已有最新M2 (当月或上月)
        from src.data.database import read_dataframe

        latest = read_dataframe(
            "SELECT MAX(month) FROM m2_monthly",
        )
        if not latest.empty and latest.iloc[0, 0] is not None:
            latest_m = latest.iloc[0, 0]
            td_month = trade_date[:7]
            # 若已包含本月或上月，说明数据已最新
            if latest_m >= td_month or latest_m >= (
                datetime.date.today().replace(day=1) - datetime.timedelta(days=35)
            ).strftime("%Y-%m"):
                logger.info("M2 already up-to-date (latest=%s)", latest_m)
                return True
        fetch_m2_history(start="2020-01-01", end=trade_date)
        return True

    _run_step(step_status, "S24c_m2", _step24c)

    # ── Step 2.4d: M1货币供应量 (月度, akshare macro_china_money_supply) ───
    # 修复: m1_m2_spread 依赖 m1_monthly, 但此前日常流程从不抓取该表,
    # 仅 backfill_fund_indicators.py 调用, 而该脚本未接入 workflow -> Actions 库为空。
    logger.info("Step 2.4d: Fetching M1 monthly data...")

    def _step24d():
        from src.data.fetcher import fetch_m1_history
        import datetime
        from src.data.database import read_dataframe

        latest = read_dataframe("SELECT MAX(month) FROM m1_monthly")
        if not latest.empty and latest.iloc[0, 0] is not None:
            latest_m = latest.iloc[0, 0]
            # 若已覆盖本月或上月, 视为最新 (月度数据发布滞后)
            cutoff = (datetime.date.today().replace(day=1) - datetime.timedelta(days=35)).strftime("%Y-%m")
            if latest_m >= cutoff:
                logger.info("M1 already up-to-date (latest=%s)", latest_m)
                return True
        fetch_m1_history()
        return True

    _run_step(step_status, "S24d_m1", _step24d)

    # ── Step 2.4f: 南向通净买额 (P1.2, akshare 全量 upsert) ────────────────
    logger.info("Step 2.4f: Fetching southbound net flow...")

    def _step24f():
        from src.data.fetcher import fetch_southbound_history

        df = fetch_southbound_history()
        return True if (df is not None and not df.empty) else False

    _run_step(step_status, "S24f_south", _step24f)

    # ── Step 2.4g: 股指期货基差 (P1.3, akshare IF0 + 库内沪深300) ──────────
    logger.info("Step 2.4g: Fetching futures basis...")

    def _step24g():
        from src.data.fetcher import fetch_futures_basis_history

        df = fetch_futures_basis_history()
        return True if (df is not None and not df.empty) else False

    _run_step(step_status, "S24g_futures", _step24g)

    # ── Step 2.4h: 新增投资者开户数 (P3, 月频, 仅展示不入分) ────────────────
    logger.info("Step 2.4h: Fetching new investor accounts (monthly)...")

    def _step24h():
        from src.data.fetcher import fetch_account_statistics

        df = fetch_account_statistics()
        return True if (df is not None and not df.empty) else False

    _run_step(step_status, "S24h_accounts", _step24h)

    # ── Step 2.4i: 宽基ETF份额日快照 (P3, 仅收集不入分) ───────────────────
    logger.info("Step 2.4i: Fetching broad ETF shares snapshot...")

    def _step24i():
        from src.data.fetcher import fetch_etf_flow_snapshot

        df = fetch_etf_flow_snapshot(trade_date=trade_date)
        return True if (df is not None and not df.empty) else False

    _run_step(step_status, "S24i_etf_flow", _step24i)

    # ── Step 3: tushare 融资融券/国债 (akshare) — catch-up 窗口 ─────────────
    logger.info("Step 3: Tushare margin / bond_yield (catch-up window)...")

    def _step3():
        """拉取 margin/bond 缺口 (D1 修复: 由"仅抓当天"改为"表内 MAX 回看 7 天 ~ td")

        背景: 原实现 fetch(trade_date, trade_date), 若当日数据尚未发布(如 T+1
        发布日界)或当日请求失败, 缺口会永久遗留且无人补课 —— 2026-08-13~09-01
        的 16 个交易日 margin 缺口即由此类失败窗口累积而成。
        现改为: 以表内 MAX(trade_date) 为基准回看 7 个自然日, 一直抓到 trade_date;
        save_dataframe 为 INSERT OR REPLACE, 重复 upsert 幂等, 窗口内的既有空洞
        会随每日运行自动自愈。表空时回看 30 天作为首次兜底。
        """
        from datetime import timedelta
        from src.data.database import get_latest_date

        any_fetched = False
        td_dt = date.fromisoformat(trade_date)

        for label, table, fetch_fn, is_full_hist in [
            # is_full_hist: 接口返回全量历史(akshare), 需按窗口过滤后再写
            ("margin", "margin_history", fetch_margin_history, False),
            ("bond_yield", "bond_yield", fetch_bond_yield_history, True),
        ]:
            latest = get_latest_date(table)
            latest_dt = date.fromisoformat(latest) if latest else None
            if latest_dt is not None and latest_dt >= td_dt:
                step_status["S3_" + label] = {"status": "SKIPPED", "detail": "already up-to-date", "elapsed": 0}
                continue
            # 起点: 表内 MAX 回看 7 天(容错发布日界与窗口内空洞); 表空则回看 30 天
            base_dt = latest_dt if latest_dt is not None else td_dt - timedelta(days=30)
            start_dt = base_dt - timedelta(days=7)
            if start_dt > td_dt:
                start_dt = td_dt
            start_s, end_s = start_dt.strftime("%Y-%m-%d"), trade_date

            def _fetch():
                df = fetch_fn(start_s, end_s)
                if df is None or df.empty:
                    return False  # 无新数据 → SKIPPED
                if is_full_hist:
                    df = df[(df["trade_date"] >= start_s) & (df["trade_date"] <= end_s)]
                    if df.empty:
                        return False
                return df

            sub = _run_step(step_status, "S3_" + label, _fetch)
            if sub is not None and hasattr(sub, "empty") and not sub.empty:
                _save(sub, table)
                any_fetched = True
        return any_fetched

    _run_step(step_status, "S3_tushare", _step3)

    # ── Step 3.5: 申万行业分类 ─────────────────────────────────────────────
    logger.info("Step 3.5: Fetching Shenwan industry classification...")

    def _step35():
        from src.indicators.focus_industries import FOCUS_SW_CODES
        from src.data.database import read_dataframe

        existing = read_dataframe("SELECT COUNT(DISTINCT sw_code) as n FROM stock_shenwan")
        if not existing.empty and existing.iloc[0]["n"] >= len(FOCUS_SW_CODES):
            logger.info("Shenwan classification already loaded (%d industries)", existing.iloc[0]["n"])
            return True
        from src.data.fetcher import fetch_shenwan_industry

        result = fetch_shenwan_industry()
        if result is None or result.empty:
            raise RuntimeError("Failed to fetch Shenwan industry data")
        return True

    _run_step(step_status, "S3_shenwan", _step35)

    # ── Step 3.6: 证监会行业分类 (BUG-2 修复: 填充 stock_industry 表) ──────
    logger.info("Step 3.6: Fetching CSRC stock industry classification...")

    def _step36():
        from src.data.database import read_dataframe

        existing = read_dataframe("SELECT COUNT(*) as n FROM stock_industry")
        if not existing.empty and existing.iloc[0]["n"] > 1000:
            logger.info("stock_industry already loaded (%d stocks)", existing.iloc[0]["n"])
            return True
        from src.data.fetcher import fetch_stock_industry

        result = fetch_stock_industry(trade_date)
        if result is None or result.empty:
            raise RuntimeError("Failed to fetch stock industry data")
        return True

    _run_step(step_status, "S3_industry", _step36)

    # ── Step 5: 计算热度指数 V2 (11指标) ──────────────────────────────────
    logger.info("Step 5: Calculating heat index v2...")

    def _step5():
        from src.indicators.heat_index_v2 import compute_index_v2

        res = compute_index_v2(trade_date=trade_date)
        if res is None or res.get("composite_score") is None:
            raise RuntimeError("heat index v2 composite_score is None")
        return res

    result = _run_step(step_status, "S5_calc", _step5)

    if result is None or result.get("composite_score") is None:
        logger.error("S5 FAILED -- writing fallback result for debug")
        result = {
            "trade_date": trade_date,
            "composite_score": None,
            "dimensions": {
                "valuation": {"score": None, "label": "估值"},
                "fund": {"score": None, "label": "资金"},
                "sentiment": {"score": None, "label": "情绪"},
                "structure": {"score": None, "label": "结构"},
            },
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
            clean = [r for r in idx_results if "error" not in r]
            json.dump(_clean_nan(clean), f, ensure_ascii=False, indent=2)
        n_ok = sum(1 for r in idx_results if "error" not in r)
        logger.info("Index heat: %d/%d computed", n_ok, len(idx_results))
        return n_ok > 0

    _run_step(step_status, "S55_index_heat", _step55)

    # ── 补充展示指标 (涨跌家数比/涨停占比/破净率, 不参与计算仅供展示) ──────
    try:
        import sqlite3 as _sqlite3

        _conn = _sqlite3.connect(DB_PATH)
        _row = _conn.execute("SELECT up_down_ratio FROM daily_updown WHERE trade_date=?", (trade_date,)).fetchone()
        if _row:
            result["display_up_down_ratio"] = round(_row[0], 4)
        _row = _conn.execute(
            "SELECT limit_up_ratio, limit_ratio FROM daily_limit WHERE trade_date=?", (trade_date,)
        ).fetchone()
        if _row:
            result["display_limit_up_ratio"] = round(_row[0], 4)
            result["display_limit_ratio"] = round(_row[1], 4)
        _row = _conn.execute("SELECT below_net_rate FROM daily_below_net WHERE trade_date=?", (trade_date,)).fetchone()
        if _row:
            result["display_below_net_rate"] = round(_row[0], 4)
        # P3 低频锚展示: 新增开户数 (月频) + 宽基ETF份额快照
        _row = _conn.execute("SELECT month, new_accounts FROM monthly_accounts ORDER BY month DESC LIMIT 1").fetchone()
        if _row:
            result["display_new_accounts_month"] = _row[0]
            result["display_new_accounts"] = round(_row[1], 2)
        _row = _conn.execute(
            "SELECT total_shares FROM daily_etf_flow WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1",
            (trade_date,),
        ).fetchone()
        if _row:
            result["display_etf_shares"] = round(_row[0], 2)
        _conn.close()
    except Exception:
        pass

    # ── Step 6: 保存结果 ────────────────────────────────────────────────────
    logger.info("Step 6: Saving results...")

    def _step6():
        # P0-2: 注入数据质量摘要 (消费方: build_feishu_notification)
        result["data_quality"] = _build_data_quality(result, step_status)
        save_results_v2(result)
        out_dir = os.path.join(os.path.dirname(__file__), "..", "web", "data")
        os.makedirs(out_dir, exist_ok=True)
        n_ok = sum(1 for v in step_status.values() if isinstance(v, dict) and v.get("status") == "OK")
        n_fail = sum(1 for v in step_status.values() if isinstance(v, dict) and v.get("status") == "FAILED")
        n_skip = sum(1 for v in step_status.values() if isinstance(v, dict) and v.get("status") == "SKIPPED")
        status_out = {
            "trade_date": trade_date,
            "generated_at": date.today().strftime("%Y-%m-%d %H:%M:%S"),
            "steps": dict(step_status),
            "n_ok": n_ok,
            "n_failed": n_fail,
            "n_skipped": n_skip,
            "schema_version": SCHEMA_VERSION,
            **runtime_meta(),
        }
        with open(os.path.join(out_dir, "run_status.json"), "w", encoding="utf-8") as sf:
            json.dump(_clean_nan(status_out), sf, ensure_ascii=False, indent=2)
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
                json.dump(_clean_nan(sector_results), _f, ensure_ascii=False, indent=2)
            logger.info("Step 7: Wrote %d sectors", len(sector_results))
            return sector_results
        return None

    _run_step(step_status, "S7_sectors", _step7)

    # ── Step 7.5: 重点行业热度 ──────────────────────────────────────────────
    logger.info("Step 7.5: Computing focus industries (Shenwan top 6)...")

    def _step75():
        from src.indicators.focus_industries import compute_focus_industries

        focus_results = compute_focus_industries(trade_date, DB_PATH)
        if focus_results:
            out_dir = os.path.join(os.path.dirname(__file__), "..", "web", "data")
            os.makedirs(out_dir, exist_ok=True)
            focus_results = _clean_nan(focus_results)
            with open(os.path.join(out_dir, "focus_industries.json"), "w", encoding="utf-8") as _f:
                json.dump(focus_results, _f, ensure_ascii=False, indent=2)
            logger.info("Step 7.5: Wrote %d focus industries", len(focus_results) - 1)
            return focus_results
        return None

    _run_step(step_status, "S75_focus", _step75)

    # ── Step 8: 最终保存 (含板块热度) ────────────────────────────────────────
    logger.info("Step 8: Saving final results (with sectors)...")

    def _step8():
        sector_results = None
        sectors_file = os.path.join(os.path.dirname(__file__), "..", "web", "data", "sectors.json")
        if os.path.exists(sectors_file):
            with open(sectors_file) as f:
                sector_results = json.load(f)
        result["sectors_top5"] = (sector_results or [])[:5]
        save_results_v2(result)
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
        webhook_url = get_feishu_webhook()  # P1-14: 兼容两个历史变量名
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

    # ── Step 10: 刷新查询规划器统计 (ANALYZE, 轻量非阻塞) ─────────────────────
    # 每日数据写入后更新统计信息, 让新建索引(v12)与大数据表查询计划保持最优。
    # 注意: VACUUM(重, 锁库) 不在此处, 改由 daily.yml 每周一步或 db_tools.py vacuum 手动执行。
    def _step10():
        from src.data.database import get_conn

        with get_conn(DB_PATH) as conn:
            conn.execute("ANALYZE")
        logger.info("Step 10: ANALYZE done (refreshed query planner statistics)")
        return True

    _run_step(step_status, "S10_analyze", _step10)

    # ── 最终汇总 ────────────────────────────────────────────────────────────
    elapsed = time.time() - t_start
    n_ok = sum(1 for v in step_status.values() if isinstance(v, dict) and v.get("status") == "OK")
    n_fail = sum(1 for v in step_status.values() if isinstance(v, dict) and v.get("status") == "FAILED")
    n_skip = sum(1 for v in step_status.values() if isinstance(v, dict) and v.get("status") == "SKIPPED")

    critical_failed = sorted(
        k for k, v in step_status.items() if isinstance(v, dict) and v.get("status") == "FAILED" and k in CRITICAL_STEPS
    )

    logger.info("=" * 60)
    logger.info("RUN SUMMARY: %d OK / %d FAILED / %d SKIPPED (%.1fs)", n_ok, n_fail, n_skip, elapsed)
    for sn, sv in step_status.items():
        if isinstance(sv, dict) and sv.get("status") != "OK":
            logger.info("  [%s] %s: %s", sv["status"], sn, sv.get("detail", ""))
    if critical_failed:
        logger.error("CRITICAL FAILED: %s —— 当日产物不可信", critical_failed)
    elif n_fail:
        logger.warning("DEGRADED: %d 个非致命 step 失败, 数据部分降级", n_fail)
    logger.info("=" * 60)

    return result, step_status


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Daily Heat Index Calculation")
    parser.add_argument("trade_date", nargs="?", help="Trade date (YYYY-MM-DD)")
    args = parser.parse_args()

    _, _step_status = run_daily(args.trade_date)

    _failed = _failed_steps(_step_status)
    _critical_failed = sorted(_failed & CRITICAL_STEPS)
    if _critical_failed:
        logger.error("致命步骤失败, 退出码 1 (阻断后续 commit/部署): %s", _critical_failed)
        sys.exit(_exit_code_for(_step_status))
    _degraded_failed = sorted(_failed - CRITICAL_STEPS)
    if _degraded_failed:
        logger.warning("非致命步骤失败 (数据降级, 退出码 0): %s", _degraded_failed)
    sys.exit(_exit_code_for(_step_status))
