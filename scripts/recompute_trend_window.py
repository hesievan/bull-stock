"""
重算指定区间 [start, end] 的逐日综合热度走势, 并把 11 指标原始值/得分 + 4 维度 + 综合分落表,
同时做异常诊断 (各指标 min/max/均值/缺失数/是否冻结)。

用法: python scripts/recompute_trend_window.py [start] [end]
"""
import os
import sys
import csv
import json
import sqlite3
import logging
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
_orig_read_sql = pd.read_sql
_RSQL_CACHE = {}

def _cached_read_sql(sql, con, params=None, **kw):
    """缓存相同 (sql, params) 的 read_sql 结果, 避免 10 年历史聚合被重复扫描"""
    key = (sql, str(params))
    if key in _RSQL_CACHE:
        return _RSQL_CACHE[key]
    r = _orig_read_sql(sql, con, params=params, **kw)
    _RSQL_CACHE[key] = r
    return r

pd.read_sql = _cached_read_sql

from src.indicators.heat_index_v2 import (
    compute_index_v2, INDICATOR_WEIGHTS, INDICATOR_DIMENSIONS, DIMENSIONS,
)
from src.output.json_writer import get_heat_level
from src.data.database import DB_PATH

logging.disable(logging.CRITICAL)  # 关掉引擎内部 DIAG 噪声

START = sys.argv[1] if len(sys.argv) > 1 else "2025-07-01"
END = sys.argv[2] if len(sys.argv) > 2 else "2026-08-10"

IND_KEY_MAP = {k: ("margin_ratio_v2" if k == "margin_ratio" else k)
               for k in INDICATOR_WEIGHTS}


def get_trade_dates(db, start, end):
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM stock_daily "
        "WHERE trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
        (start, end),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def main():
    db = DB_PATH
    tds = get_trade_dates(db, START, END)
    print(f"区间 {START}~{END}: 共 {len(tds)} 个交易日", flush=True)

    out_rows = []
    for i, td in enumerate(tds):
        res = compute_index_v2(trade_date=td, db_path=db)
        if res is None or res.get("composite_score") is None:
            print(f"  SKIP {td}: 无综合分", flush=True)
            continue
        raw = res["indicator_raw"]
        ind = res["indicators"]
        dims = {d: res["dimensions"][d]["score"] for d in DIMENSIONS}
        row = {
            "trade_date": td,
            "composite": res["composite_score"],
            "level": get_heat_level(res["composite_score"]),
            "valuation": dims["valuation"],
            "fund": dims["fund"],
            "sentiment": dims["sentiment"],
            "structure": dims["structure"],
        }
        for k in INDICATOR_WEIGHTS:
            ik = IND_KEY_MAP[k]
            row[f"{k}_score"] = ind.get(ik)
            row[f"{k}_raw"] = raw.get(k)
        out_rows.append(row)
        if (i + 1) % 20 == 0 or i == len(tds) - 1:
            print(f"  ...{td} ({i+1}/{len(tds)}) composite={res['composite_score']}", flush=True)

    # 写 CSV
    os.makedirs("reports", exist_ok=True)
    csv_path = f"reports/heat_trend_{START}_{END}.csv"
    fields = ["trade_date", "composite", "level", "valuation", "fund", "sentiment", "structure"]
    for k in INDICATOR_WEIGHTS:
        fields += [f"{k}_score", f"{k}_raw"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out_rows)
    print(f"\n✓ 已写出 {csv_path} ({len(out_rows)} 行)", flush=True)

    # ── 异常诊断 ──
    import statistics
    comps = [r["composite"] for r in out_rows if r["composite"] is not None]
    print("\n==== 综合分分布 ====")
    print(f"  n={len(comps)}  min={min(comps):.1f}  max={max(comps):.1f}  "
          f"mean={statistics.mean(comps):.1f}  median={statistics.median(comps):.1f}")
    print(f"  各档位占比: " + ", ".join(
        f"{lv}={sum(1 for c in comps if get_heat_level(c)==lv)}"
        for lv in ['green','yellow','orange','red']))

    print("\n==== 各指标诊断 (raw/score 的 min/max/mean, None数, 冻结值) ====")
    print(f"{'指标':<16}{'raw_min':>12}{'raw_max':>12}{'score_min':>10}{'score_max':>10}"
          f"{'score_mean':>11}{'None':>6}{'冻结?':>8}")
    for k in INDICATOR_WEIGHTS:
        raws = [r[f"{k}_raw"] for r in out_rows if r[f"{k}_raw"] is not None]
        scs = [r[f"{k}_score"] for r in out_rows if r[f"{k}_score"] is not None]
        n_none = sum(1 for r in out_rows if r[f"{k}_score"] is None)
        if raws:
            rmin, rmax = min(raws), max(raws)
            rmean = statistics.mean(raws)
        else:
            rmin = rmax = rmean = float('nan')
        if scs:
            smin, smax = min(scs), max(scs)
            smean = statistics.mean(scs)
        else:
            smin = smax = smean = float('nan')
        # 冻结检测: 区间后半段 score 是否几乎恒定
        frozen = "—"
        if len(scs) > 40:
            tail = scs[-20:]
            if max(tail) - min(tail) < 0.5:
                frozen = "可能冻结"
        print(f"{k:<16}{rmin:>12.4f}{rmax:>12.4f}{smin:>10.1f}{smax:>10.1f}"
              f"{smean:>11.1f}{n_none:>6}{frozen:>8}")

    # 维度分布
    print("\n==== 维度均值 ====")
    for d in DIMENSIONS:
        vals = [r[d] for r in out_rows if r[d] is not None]
        if vals:
            print(f"  {d:<10}: mean={statistics.mean(vals):.1f}  max={max(vals):.1f}")


if __name__ == "__main__":
    main()
