#!/usr/bin/env python3
"""
refill_history_v2.py — 全量重建 web/data/history.json (2015-2026), 16 键满配

高效实现: 复用 reports/backtest_v2_detail.csv 的每日 16 指标百分位 + composite_score,
配合 web/data/indicator_history.json 的原始值 (单一数据源, 由 backfill_indicator_history.py
统一维护), 无需再补算缺失原始值。秒级完成 (不逐日调用引擎, 避免 10 年百分位窗口重复计算)。

采样: 历史按 ISO 周取最后一个交易日 (与既有 history.json 周频口径一致),
**最近 TAIL_CALENDAR_DAYS 自然日保留日频尾巴** (含末日), 避免丢近期日频点
(json_writer 每日 append 也写日频, refill 后保持同类粒度)。

输出每条记录 indicators_v2 恒含 16 键 (缺值用 null 占位, 不再缺键),
并带 engine_version / indicator_count (=16 键中非空原始值数) 元数据,
使前端/分析可按统一口径消费, 消除历史"缺键→重归一化"造成的口径断裂。

不含 MA10/MA20 均线 (主趋势线只保留 composite_score + 阈值带)。
"""

import csv
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.indicators.heat_index_v2 import (
    ENGINE_VERSION,
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

# 采样: 最近 N 自然日保留日频尾巴, 更早历史按 ISO 周取末日
TAIL_CALENDAR_DAYS = 90

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
# indicators_v2 原始值键全集 = 引擎 16 键 (从 indicator_history.json 单一数据源读取)
# 注: 键名与 json_writer 输出保持一致 (margin_ratio_v2 而非 margin_ratio)
RAW_KEYS = [
    "pe",
    "buffett",
    "margin_ratio_v2",
    "yield_spread",
    "m1_m2_spread",
    "southbound",
    "margin_buy_ratio",
    "seal_rate",
    "turnover_m2",
    "turnover",
    "futures_discount",
    "amplitude",
    "realized_vol",
    "new_high",
    "ma_alignment",
    "breadth",
]
assert len(RAW_KEYS) == 16 and len(set(RAW_KEYS)) == 16


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


def sample_dates(dates):
    """历史 ISO 周取末日 + 最近 TAIL_CALENDAR_DAYS 自然日保留日频尾巴 (含末日)"""
    dates = sorted(set(dates))
    if not dates:
        return []
    last_dt = date.fromisoformat(dates[-1])
    cutoff_dt = last_dt - timedelta(days=TAIL_CALENDAR_DAYS)
    head, tail = [], []
    for d in dates:
        (tail if date.fromisoformat(d) >= cutoff_dt else head).append(d)
    weeks = {}
    for d in head:
        y, w, _ = date(int(d[:4]), int(d[5:7]), int(d[8:10])).isocalendar()
        key = (y, w)
        if key not in weeks or d > weeks[key]:
            weeks[key] = d
    out = sorted(set(weeks.values()) | set(tail))
    if dates[-1] not in out:
        out = [d for d in out if d <= dates[-1]] + [dates[-1]]
    return sorted(set(out))


def main():
    rows = load_csv()
    dates = sorted(rows.keys())
    sample = sample_dates(dates)
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
        # 原始值: 16 键恒在, 缺值 null 占位 (单一数据源 indicator_history.json)
        day_raw = ind_hist.get(d, {})
        raw = {k: day_raw.get(k) for k in RAW_KEYS}
        n_avail = sum(1 for v in raw.values() if v is not None)
        out.append(
            {
                "trade_date": d,
                "composite_score": cs,
                "level": lvl,
                "dimensions": {dim: {"score": dims[dim], "label": DIM_LABEL[dim]} for dim in DIMENSIONS},
                "indicators_v2": raw,
                "engine_version": ENGINE_VERSION,
                "indicator_count": n_avail,
                "version": VERSION,
                "updated_at": date.today().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    out.sort(key=lambda x: x["trade_date"])
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    full16 = sum(1 for r in out if r["indicator_count"] == 16)
    print(
        f"wrote {len(out)} entries ({out[0]['trade_date']} ~ {out[-1]['trade_date']}) -> {OUT}\n"
        f"  16键满配记录: {full16}/{len(out)} | 末段日频尾巴: "
        f"{TAIL_CALENDAR_DAYS} 自然日"
    )


if __name__ == "__main__":
    main()
