#!/usr/bin/env python3
"""
V2 引擎历史基线重算脚本
- 读取现有 history.json 中的日期列表
- 用 V2 引擎逐日重算 composite_score + 4维度分数
- 输出新 history.json（覆盖原文件）
- 增量保存，支持中断续算
"""
import sys, os, json, time, logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sqlite3
from src.indicators.heat_index_v2 import (
    INDICATOR_WEIGHTS, INDICATOR_DIMENSIONS, DIMENSIONS,
    DIVERGENCE_CONFIG, NEW_HIGH_THRESHOLD,
    calc_pe, calc_erp_v2, calc_buffett,
    calc_margin_ratio_v2, calc_deposit_ratio,
    calc_turnover_m2, calc_turnover_v2,
    calc_new_high_v2, calc_ma_alignment_v2,
    calc_qvix_v2,
    _apply_sentiment_divergence, _apply_new_high_divergence,
)
from src.data.database import DB_PATH

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def v2_level(score):
    if score is None:
        return "unknown"
    if score >= 65:
        return "red"
    if score >= 55:
        return "orange"
    if score >= 40:
        return "yellow"
    return "green"


def compute_v2_for_date(conn, td):
    """直接使用已有 conn 调用 V2 各指标函数，避免反复连接/断开"""
    _raw = {}
    def _unpack(k, v):
        if v is None:
            _raw[k] = None
            return None
        if isinstance(v, tuple):
            _raw[k] = v[1]
            return v[0]
        _raw[k] = None
        return v

    scores = {}
    for k, fn in [
        ("pe", calc_pe),
        ("erp", calc_erp_v2),
        ("buffett", calc_buffett),
        ("margin_ratio", calc_margin_ratio_v2),
        ("deposit_ratio", calc_deposit_ratio),
        ("turnover_m2", calc_turnover_m2),
        ("turnover", calc_turnover_v2),
        ("new_high", calc_new_high_v2),
        ("ma_alignment", calc_ma_alignment_v2),
    ]:
        scores[k] = _unpack(k, fn(conn, td))

    # 背离惩罚
    sentiment_keys = {"turnover_m2", "turnover"}
    sentiment_scores = {k: scores[k] for k in sentiment_keys}
    sentiment_scores = _apply_sentiment_divergence(conn, td, sentiment_scores)
    for k, v in sentiment_scores.items():
        scores[k] = v
    scores["new_high"] = _apply_new_high_divergence(conn, td, scores["new_high"])

    # 维度分（平均）
    dim_scores = {}
    for dim_name in DIMENSIONS:
        ind_keys = [k for k, v in INDICATOR_DIMENSIONS.items() if v == dim_name]
        dim_vals = [scores[k] for k in ind_keys if scores[k] is not None]
        if dim_vals:
            dim_scores[dim_name] = round(sum(dim_vals) / len(dim_vals), 1)
        else:
            dim_scores[dim_name] = None

    # 综合加权分
    valid_scores = [(k, v) for k, v in scores.items() if v is not None]
    if not valid_scores:
        composite = None
    else:
        total_weight = sum(INDICATOR_WEIGHTS[k] for k, _ in valid_scores)
        composite = round(sum(v * INDICATOR_WEIGHTS[k] for k, v in valid_scores) / total_weight, 1) if total_weight > 0 else None

    # 维度标签
    dim_labels = {"valuation": "估值", "fund": "资金", "sentiment": "情绪", "structure": "结构"}
    return {
        "trade_date": td,
        "composite_score": composite,
        "level": v2_level(composite),
        "dimensions": {
            dim: {"score": dim_scores.get(dim), "label": dim_labels.get(dim, dim)}
            for dim in DIMENSIONS
        },
        "indicators_v2": {k: _raw.get(k) for k in scores if k != "qvix"},
        "qvix_display": None,
        "version": "v2",
        "updated_at": td + " 00:00:00",
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Recalc V2 history baseline")
    parser.add_argument("--input", default="web/data/history.json", help="Input V1 history JSON")
    parser.add_argument("--output", default="web/data/history.json", help="Output V2 history JSON")
    parser.add_argument("--checkpoint", default="web/data/.v2_recalc_checkpoint.json",
                        help="Checkpoint file for resume")
    parser.add_argument("--start-from", default=None,
                        help="Skip dates before this date (YYYY-MM-DD)")
    args = parser.parse_args()

    # 加载现有历史
    with open(args.input) as f:
        orig_history = json.load(f)
    all_dates = [r["trade_date"] for r in orig_history]

    # 已有 checkpoint
    v2_results = []
    done_dates = set()
    if os.path.exists(args.checkpoint):
        with open(args.checkpoint) as f:
            cp = json.load(f)
            v2_results = cp.get("results", [])
            done_dates = set(r["trade_date"] for r in v2_results)
        logger.warning("Resumed from checkpoint: %d dates already done", len(done_dates))

    # 过滤
    pending = [d for d in all_dates if d not in done_dates]
    if args.start_from:
        pending = [d for d in pending if d >= args.start_from]

    if not pending:
        logger.warning("No pending dates to compute!")
    else:
        logger.warning("Computing V2 for %d dates (total %d, %d already done)...",
                       len(pending), len(all_dates), len(done_dates))

    # 所有 dates（已做 + 待做）
    all_v2 = {r["trade_date"]: r for r in v2_results}

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA cache_size=-80000")  # ~80MB cache

    t_start = time.time()
    try:
        for i, d in enumerate(pending):
            t0 = time.time()
            try:
                result = compute_v2_for_date(conn, d)
                if result["composite_score"] is not None:
                    all_v2[d] = result
                else:
                    logger.warning("  [%d/%d] %s: V2 returned None", i+1, len(pending), d)
            except Exception as e:
                logger.warning("  [%d/%d] %s: ERROR %s", i+1, len(pending), d, e)

            dt = time.time() - t0
            elapsed = time.time() - t_start
            remaining = (len(pending) - i - 1) * dt if dt > 0 else 0
            logger.warning(
                "  [%d/%d] %s: composite=%s (%.1fs, elapsed=%.0fs, ETA=%.0fs)",
                i+1, len(pending), d,
                all_v2.get(d, {}).get("composite_score", "ERR"),
                dt, elapsed, remaining
            )

            # 每 20 个日期保存 checkpoint
            if (i + 1) % 20 == 0:
                cp = {"results": list(all_v2.values())}
                with open(args.checkpoint, "w") as f:
                    json.dump(cp, f, ensure_ascii=False)
                logger.warning("  Checkpoint saved (%d dates)", len(all_v2))
    finally:
        conn.close()

    # 重建最终输出（保留原顺序）
    final_history = []
    for r in orig_history:
        d = r["trade_date"]
        v2r = all_v2.get(d)
        if v2r and v2r.get("composite_score") is not None:
            final_history.append(v2r)
        else:
            # V2 无法计算此日期，保留原有 V1 记录但标记
            logger.warning("  %s: using V1 data (V2 unavailable)", d)
            final_history.append(r)

    # 写入
    with open(args.output, "w") as f:
        json.dump(final_history, f, ensure_ascii=False, indent=2)
    logger.warning("Done! Wrote %d records to %s (%.1f min)",
                   len(final_history), args.output, (time.time() - t_start) / 60)

    # 清除 checkpoint
    if os.path.exists(args.checkpoint):
        os.remove(args.checkpoint)
    logger.warning("Checkpoint removed.")


if __name__ == "__main__":
    main()
