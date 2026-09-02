#!/usr/bin/env python3
"""m2b0_divergence_check.py — M2b-0 验证: 引擎↔CSV composite 差异是否源于
backtest_v2.py 未复刻引擎的两处背离惩罚 (sentiment/new_high divergence)。

假设: 引擎 compute_index_v2 在合成前调 _apply_sentiment_divergence (turnover>70
且 20日指数跌幅>1.5% → turnover 扣20分) 与 _apply_new_high_divergence (指数20日
涨>3% + 新高占比降>5pt 且当前<30% → new_high 扣15分); 而 backtest CSV 只存
原始得分 → 触发日引擎 composite 比 CSV 低 penalty×权重 (~2.7分), 即 2023 年
观测到的 0.2~2.8 分差。

步骤:
  1) 从 DB 复刻两个背离条件, 在全历史 CSV 日期上标记触发日;
  2) 触发日对 CSV 的 ind_turnover/ind_new_high 施加惩罚 → csv_patched composite;
  3) 抽样日期跑引擎 compute_index_v2, 对照 engine vs csv vs csv_patched。
"""
import contextlib
import csv
import io
import sqlite3
import sys

import pandas as pd

sys.path.insert(0, ".")
from src.indicators.heat_index_v2 import (
    INDICATOR_WEIGHTS,
    DIVERGENCE_CONFIG,
    compute_index_v2,
)

DB = "data/heat_index.db"
CSV = "reports/backtest_v2_detail.csv"
SCORING = list(INDICATOR_WEIGHTS.keys())  # 9 计分键
COL = {k: f"ind_{k}" for k in SCORING}

conn = sqlite3.connect(DB)

rows = list(csv.DictReader(open(CSV)))
dates = [r["trade_date"] for r in rows]


def idx_close_asof(td):
    """<=td 最近一条 sh000001 close"""
    cur = conn.execute(
        "SELECT close FROM index_daily WHERE index_code='sh000001' AND trade_date<=? "
        "ORDER BY trade_date DESC LIMIT 1",
        (td,),
    ).fetchone()
    return float(cur[0]) if cur else None


def new_high_ratio(td):
    cur = conn.execute(
        "SELECT new_high_ratio FROM daily_new_high WHERE trade_date<=? "
        "ORDER BY trade_date DESC LIMIT 1",
        (td,),
    ).fetchone()
    return float(cur[0]) if cur else None


sent_triggers, nh_triggers = set(), set()
for td in dates:
    # ── sentiment divergence ──
    prev = (pd.Timestamp(td) - pd.DateOffset(days=DIVERGENCE_CONFIG["lookback_days"])).strftime("%Y-%m-%d")
    c_now, c_prev = idx_close_asof(td), idx_close_asof(prev)
    # ── new_high divergence (需 now_row 恰在 td, prev 取 ~20 自然日前) ──
    nh_now = conn.execute("SELECT new_high_ratio FROM daily_new_high WHERE trade_date=?", (td,)).fetchone()
    nh_now = float(nh_now[0]) if nh_now and nh_now[0] is not None else None
    if c_now and c_prev:
        chg = (c_now / c_prev - 1) * 100
        if chg < DIVERGENCE_CONFIG["decline_threshold"]:
            sent_triggers.add(td)  # 还需 ind_turnover>70, 见下(逐行用 CSV 分判断)
        if chg > 3 and nh_now is not None:
            nh_prev = new_high_ratio(prev)
            if nh_prev is not None and (nh_prev * 100 - nh_now * 100) > 5 and nh_now * 100 < 30:
                nh_triggers.add(td)

# 结合 CSV 分的真实触发 (sentiment 需 turnover score>70); rows 建 date 索引加速
by_date = {r["trade_date"]: r for r in rows}
def _f(r, k):
    v = r.get(COL[k])
    return float(v) if v not in (None, "") else None

sent_days = [td for td in sent_triggers if by_date.get(td) and (_f(by_date[td], "turnover") or 0) > 70]

print(f"候选 sentiment 背离日(指数20日跌>1.5% & turnover分>70): {len(sent_days)}")
print("  示例:", sent_days[:10])
print(f"new_high 顶背离日: {len(nh_triggers)}")
print("  示例:", sorted(nh_triggers)[:10])

# composite helper (9键行重归一, 可选覆盖惩罚)
def composite(r, penalize=False):
    num = den = 0.0
    for k in SCORING:
        v = _f(r, k)
        if v is None:
            continue
        if penalize and r["trade_date"] in sent_days and k == "turnover":
            v = max(0, v - DIVERGENCE_CONFIG["penalty_factor"] * 100)
        if penalize and r["trade_date"] in nh_triggers and k == "new_high":
            v = max(0, v - DIVERGENCE_CONFIG["new_high_penalty"])
        num += v * INDICATOR_WEIGHTS[k]
        den += INDICATOR_WEIGHTS[k]
    return num / den if den else None

# 抽样: 全 sent/nh 触发日 + 等量随机非触发日 (上限 60)
sample = sorted(set(sent_days) | nh_triggers)
import random
random.seed(42)
nontrig = [td for td in dates if td not in sample]
sample += random.sample(nontrig, min(30, len(nontrig)))
sample = sorted(set(sample))
if len(sample) > 60:
    sample = sample[:60]
print(f"\n引擎对照抽样 {len(sample)} 日...")

buf = io.StringIO()
engine = {}
for i, td in enumerate(sample):
    with contextlib.redirect_stdout(buf):
        res = compute_index_v2(td, db_path=DB)
    engine[td] = res["composite_score"]

print(f"\n{'date':12s} {'csv':>6s} {'csv_pen':>8s} {'engine':>7s}  type")
n_diff_plain = n_diff_pen = 0
diff_plain_max = diff_pen_max = 0.0
for td in sample:
    r = by_date[td]
    c_plain = composite(r, penalize=False)
    c_pen = composite(r, penalize=True)
    e = engine[td]
    d_plain, d_pen = abs(e - c_plain), abs(e - c_pen)
    n_diff_plain += d_plain > 0.51
    n_diff_pen += d_pen > 0.51
    diff_plain_max = max(diff_plain_max, d_plain)
    diff_pen_max = max(diff_pen_max, d_pen)
    typ = "sent" if td in sent_days else ("nh" if td in nh_triggers else "none")
    if td in sent_days or td in nh_triggers or d_plain > 0.51:
        print(f"{td:12s} {c_plain:6.1f} {c_pen:8.1f} {e:7.1f}  {typ}")

print(f"\n=== 汇总 (n={len(sample)}) ===")
print(f"engine vs csv     : >0.51 的日数 {n_diff_plain}, maxΔ {diff_plain_max:.2f}")
print(f"engine vs csv_pen : >0.51 的日数 {n_diff_pen}, maxΔ {diff_pen_max:.2f}")
