#!/usr/bin/env python3
"""
refill_history_v2.py — 用最新 11 项指标, 周频回填 web/data/history.json (2015-2026)

高效实现: 复用 reports/backtest_v2_detail.csv 的每日 11 指标百分位 + composite_score,
配合 web/data/indicator_history.json 的原始值, 仅需 1 次 bulk SQL 补算 margin_ratio_v2 /
turnover_m2 两个缺失原始值。秒级完成 (不逐日调用引擎, 避免 10 年百分位窗口重复计算)。

采样: ISO 周最后一个交易日 (含末日), 即与既有 history.json 周频口径一致。
不含 MA10/MA20 均线 (主趋势线只保留 composite_score + 阈值带)。
"""

import csv
import json
import os
import sqlite3
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.indicators.heat_index_v2 import (
    INDICATOR_WEIGHTS,
    INDICATOR_DIMENSIONS,
    DIMENSIONS,
)
from src.output.json_writer import get_heat_level

DB = "data/heat_index.db"
CSV = "reports/backtest_v2_detail.csv"
IND_RAW = "web/data/indicator_history.json"
OUT = "web/data/history.json"
VERSION = "v2"

DIM_LABEL = {"valuation": "估值", "fund": "资金", "sentiment": "情绪", "structure": "结构"}

# CSV 的 ind_* 列 -> 引擎内部 key (margin 在引擎内部叫 margin_ratio, 输出用 margin_ratio_v2)
COL2KEY = {
    "ind_pe": "pe",
    "ind_buffett": "buffett",
    "ind_margin_ratio": "margin_ratio",
    "ind_yield_spread": "yield_spread",
    "ind_m1_m2_spread": "m1_m2_spread",
    "ind_southbound": "southbound",
    "ind_margin_buy_ratio": "margin_buy_ratio",
    "ind_seal_rate": "seal_rate",
    "ind_turnover_m2": "turnover_m2",
    "ind_turnover": "turnover",
    "ind_futures_discount": "futures_discount",
    "ind_amplitude": "amplitude",
    "ind_realized_vol": "realized_vol",
    "ind_new_high": "new_high",
    "ind_ma_alignment": "ma_alignment",
    "ind_breadth": "breadth",
}
# indicator_history.json 中可直接取的原始值键
RAW_KEYS = [
    "pe",
    "buffett",
    "seal_rate",
    "turnover",
    "ma_alignment",
    "new_high",
    "yield_spread",
    "m1_m2_spread",
    "southbound",
    "futures_discount",
    "amplitude",
    "realized_vol",
    "breadth",
]


def _f(x):
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_csv():
    rows = {}
    with open(CSV, newline="") as f:
        for row in csv.DictReader(f):
            rows[row["trade_date"]] = row
    return rows


def weekly_sample(dates):
    """每个 ISO 周取最后一个交易日, 并保证含末日"""
    weeks = {}
    for d in dates:
        y, w, _ = date(int(d[:4]), int(d[5:7]), int(d[8:10])).isocalendar()
        key = (y, w)
        if key not in weeks or d > weeks[key]:
            weeks[key] = d
    out = sorted(set(weeks.values()))
    if dates[-1] not in out:
        out = [d for d in out if d <= dates[-1]] + [dates[-1]]
    return sorted(set(out))


def bulk_raw(conn, trading_days):
    """补算 margin_ratio_v2 / turnover_m2 原始值 (元/元比率)。

    与引擎一致: 对每个交易日取 <=当日 的最近可用值 (margin_history 早于 2026-08-11 截止,
    daily_circ_mv / stock_daily 在部分日期缺失, 用最近值回填)。
    """
    import bisect

    # ── margin_ratio_v2: (rzye+rqye) / (total_circ_mv*10000) ──
    mrows = conn.execute(
        "SELECT trade_date, rzye, rqye FROM margin_history WHERE rzye > 0 ORDER BY trade_date"
    ).fetchall()
    m_dates = [r[0] for r in mrows]
    m_vals = [(r[1] or 0) + (r[2] or 0) for r in mrows]
    crows = conn.execute(
        "SELECT trade_date, total_circ_mv FROM daily_circ_mv WHERE total_circ_mv > 0 ORDER BY trade_date"
    ).fetchall()
    c_dates = [r[0] for r in crows]
    c_vals = [r[1] for r in crows]
    mr = {}
    for t in trading_days:
        i = bisect.bisect_right(m_dates, t) - 1
        j = bisect.bisect_right(c_dates, t) - 1
        if i >= 0 and j >= 0 and m_vals[i] and c_vals[j] > 0:
            mr[t] = round(m_vals[i] / (c_vals[j] * 10000), 6)

    # ── turnover_m2: 当日成交额(元) / M2(元) ──
    m2rows = conn.execute(
        "SELECT month, m2_billion FROM m2_monthly WHERE m2_billion IS NOT NULL ORDER BY month"
    ).fetchall()
    m2months = [r[0] for r in m2rows]
    m2vals = [r[1] * 1e8 for r in m2rows]
    arows = conn.execute(
        "SELECT trade_date, SUM(amount) * 1000 FROM stock_daily WHERE amount > 0 GROUP BY trade_date ORDER BY trade_date"
    ).fetchall()
    adates = [r[0] for r in arows]
    amap = {r[0]: r[1] for r in arows}
    tm2 = {}
    for t in trading_days:
        i = bisect.bisect_right(adates, t) - 1
        if i < 0:
            continue
        td0 = adates[i]
        mm = td0[:7]
        k = bisect.bisect_right(m2months, mm) - 1
        if k >= 0 and m2vals[k] > 0 and amap.get(td0) is not None:
            tm2[t] = round(amap[td0] / m2vals[k], 8)
    return mr, tm2


def main():
    rows = load_csv()
    dates = sorted(rows.keys())
    sample = weekly_sample(dates)
    conn = sqlite3.connect(DB)
    mr_raw, tm2_raw = bulk_raw(conn, dates)
    ind_hist = json.load(open(IND_RAW, encoding="utf-8"))

    out = []
    for d in sample:
        row = rows.get(d)
        if not row:
            continue
        # 百分位分
        pct = {k: _f(row.get(col)) for col, k in COL2KEY.items()}
        # 维度分 (维度内指标按权重加权, 与引擎口径一致)
        dims = {}
        for dim in DIMENSIONS:
            keys = [k for k, v in INDICATOR_DIMENSIONS.items() if v == dim]
            avail = [(k, pct[k]) for k in keys if pct.get(k) is not None]
            if not avail:
                dims[dim] = None
                continue
            w = sum(INDICATOR_WEIGHTS[k] for k, _ in avail)
            dims[dim] = round(sum(v * INDICATOR_WEIGHTS[k] for k, v in avail) / w, 1) if w > 0 else None
        cs = _f(row["composite_score"])
        lvl = get_heat_level(cs) if cs is not None else "unknown"
        # 原始值
        raw = {}
        for k in RAW_KEYS:
            v = ind_hist.get(d, {}).get(k)
            raw[k] = v
        raw["margin_ratio_v2"] = mr_raw.get(d)
        raw["turnover_m2"] = tm2_raw.get(d)
        out.append(
            {
                "trade_date": d,
                "composite_score": cs,
                "level": lvl,
                "dimensions": {dim: {"score": dims[dim], "label": DIM_LABEL[dim]} for dim in DIMENSIONS},
                "indicators_v2": raw,
                "version": VERSION,
                "updated_at": date.today().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    out.sort(key=lambda x: x["trade_date"])
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print(f"wrote {len(out)} weekly entries ({out[0]['trade_date']} ~ {out[-1]['trade_date']}) -> {OUT}")


if __name__ == "__main__":
    main()
