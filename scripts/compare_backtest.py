#!/usr/bin/env python3
"""
V2 引擎关键日期回测对照 — 用于代码审查修复前后的信号对比

对 README 回测表的 8 个关键日期运行 compute_index_v2，
输出综合分/维度分/9 指标分到 JSON + Markdown，供前后对照。

用法:
  python scripts/compare_backtest.py                                # 8 个 README 关键日期
  python scripts/compare_backtest.py --dates 2024-10-08,2026-06-24  # 指定日期
  python scripts/compare_backtest.py --label baseline --out reports/backtest_baseline.json
"""
import sys
import os
import json
import time
import argparse
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.indicators.heat_index_v2 import compute_index_v2

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

# README 回测表 8 个关键日期 (日期, 市场状态, 描述, 修复前基线分)
README_KEY_DATES = [
    ("2015-06-12", "BULL_PEAK",   "2015牛市顶",     83.8),
    ("2018-12-28", "BEAR_BOTTOM", "2018熊底",       5.2),
    ("2020-07-10", "BULL_START",  "2020牛市启动",   70.4),
    ("2021-02-18", "BULL_PEAK",   "2021牛市顶",     74.1),
    ("2024-02-05", "BEAR_BOTTOM", "2024熊底",       23.5),
    ("2024-10-08", "PULSE_PEAK",  "2024脉冲顶",     49.1),
    ("2026-06-24", "CHOP",        "2026震荡市",     53.9),
    ("2026-06-25", "CHOP",        "2026震荡市",     54.6),
]

DIM_LABELS = {"valuation": "估值", "fund": "资金", "sentiment": "情绪", "structure": "结构"}


def run_one(trade_date: str, db_path: str = None) -> dict:
    """对单个日期计算 V2 综合分"""
    t0 = time.time()
    r = compute_index_v2(trade_date=trade_date, db_path=db_path)
    elapsed = time.time() - t0
    dims = r.get("dimensions", {})
    return {
        "trade_date": trade_date,
        "composite_score": r.get("composite_score"),
        "dimensions": {k: (dims.get(k) or {}).get("score") for k in dims},
        "indicators": {k: v for k, v in r.get("indicators", {}).items() if k != "qvix_components"},
        "elapsed_s": round(elapsed, 1),
    }


def main():
    parser = argparse.ArgumentParser(description="V2 引擎关键日期回测对照")
    parser.add_argument("--dates", help="逗号分隔的日期列表 (默认 README 8 关键日期)")
    parser.add_argument("--label", default="latest", help="对照标签, 如 baseline / after-m1")
    parser.add_argument("--out", help="输出 JSON 路径 (默认 reports/backtest_<label>.json)")
    parser.add_argument("--db", default=None, help="数据库路径 (默认 DB_PATH)")
    args = parser.parse_args()

    if args.dates:
        dates = [(d.strip(), "", "", None) for d in args.dates.split(",") if d.strip()]
    else:
        dates = README_KEY_DATES

    results = []
    for dt, state, desc, baseline in dates:
        try:
            row = run_one(dt, args.db)
            row["state"] = state
            row["desc"] = desc
            row["baseline_composite"] = baseline
            results.append(row)
            print("✅ %s [%s] composite=%s (%.1fs)" % (dt, state, row["composite_score"], row["elapsed_s"]))
        except Exception as e:
            logging.error("%s failed: %s", dt, e)
            results.append({"trade_date": dt, "state": state, "desc": desc, "error": str(e)[:120]})

    out_dir = os.path.join(os.path.dirname(__file__), "..", "reports")
    os.makedirs(out_dir, exist_ok=True)
    out_path = args.out or os.path.join(out_dir, f"backtest_{args.label}.json")
    payload = {"label": args.label, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "results": results}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # Markdown 对照表
    md_path = out_path.replace(".json", ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# V2 回测对照 — {args.label}\n\n")
        f.write(f"> 生成时间: {payload['generated_at']}\n\n")
        f.write("| 日期 | 市场状态 | 综合分 | 估值 | 资金 | 情绪 | 结构 | 基线 | 偏差 |\n")
        f.write("|------|---------|--------|------|------|------|------|------|------|\n")
        for r in results:
            if "error" in r:
                f.write(f"| {r['trade_date']} | {r.get('state','')} | ERROR: {r['error'][:40]} | | | | | | |\n")
                continue
            dims = r["dimensions"]
            comp = r["composite_score"]
            base = r.get("baseline_composite")
            diff = f"{comp - base:+.1f}" if (comp is not None and base is not None) else "-"
            comp_s = f"{comp:.1f}" if comp is not None else "None"
            f.write(f"| {r['trade_date']} | {r.get('desc','') or r.get('state','')} | {comp_s} "
                    f"| {dims.get('valuation')} | {dims.get('fund')} | {dims.get('sentiment')} | {dims.get('structure')} "
                    f"| {base} | {diff} |\n")

    print(f"\n结果已写入: {out_path}")
    print(f"对照表:     {md_path}")


if __name__ == "__main__":
    main()
