#!/usr/bin/env python3
"""
V2 热度指数全历史回测 — 内存优化版

策略: 先用批量 SQL 预计算所有指标的原始值, 再在内存中算百分位得分,
避免逐日重复加载 stock_daily (11M rows) 等大表。
"""

import json
import logging
import math
import os
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.data.database import DB_PATH
from src.indicators.utils import _pct_rank
from src.indicators.heat_index_v2 import INDICATOR_WEIGHTS
from src.common import timed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
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


# ── A 股已知牛熊周期 ──────────────────────────────────────────────────────
MARKET_PHASES = [
    ("2015-01-05", "2015-06-12", "bull_peak", "2015大牛市顶部 (5178点)"),
    ("2015-06-15", "2015-08-26", "bear_crash", "股灾1.0 (5178→2850)"),
    ("2015-08-27", "2015-12-31", "bounce", "股灾后反弹"),
    ("2016-01-01", "2016-01-28", "bear_bottom", "熔断底 (2638点)"),
    ("2016-01-29", "2016-11-30", "slow_bull", "慢牛修复"),
    ("2016-12-01", "2017-05-31", "correction", "震荡调整"),
    ("2017-06-01", "2018-01-29", "bull_peak", "蓝筹白马牛 (3587点)"),
    ("2018-01-30", "2018-10-19", "bear_crash", "贸易战熊市 (3587→2449)"),
    ("2018-10-20", "2019-04-19", "bull_rally", "春季躁动 (2449→3288)"),
    ("2019-04-22", "2019-08-09", "correction", "中美摩擦回调"),
    ("2019-08-12", "2020-01-20", "slow_bull", "科技牛慢涨"),
    ("2020-01-21", "2020-03-23", "bear_crash", "新冠疫情冲击 (3127→2646)"),
    ("2020-03-24", "2020-07-13", "bull_rally", "流动性牛快速反弹"),
    ("2020-07-14", "2021-02-18", "bull_peak", "核心资产牛顶 (3731点)"),
    ("2021-02-19", "2021-03-25", "correction", "茅指数回调"),
    ("2021-03-26", "2021-12-13", "bull_peak", "新能源结构牛 (创业板3576)"),
    ("2021-12-14", "2022-04-27", "bear_crash", "多因素熊市 (3700→2863)"),
    ("2022-04-28", "2022-07-05", "bounce", "超跌反弹"),
    ("2022-07-06", "2022-10-31", "bear_bottom", "二次探底 (2885)"),
    ("2022-11-01", "2023-05-09", "bull_rally", "疫后复苏行情"),
    ("2023-05-10", "2024-02-05", "bear_crash", "阴跌熊市 (3418→2635)"),
    ("2024-02-06", "2024-05-20", "bull_rally", "春季反弹 (2635→3174)"),
    ("2024-05-21", "2024-09-18", "bear_bottom", "缩量磨底 (3174→2689)"),
    ("2024-09-24", "2024-10-08", "bull_peak", "924行情急涨 (2689→3674)"),
    ("2024-10-09", "2024-11-27", "correction", "急涨后回调"),
    ("2024-11-28", "2025-07-11", "slow_bull", "震荡上行"),
    ("2025-07-14", "2026-08-07", "bull_peak", "持续上涨"),
]
BULL_PHASES = {"bull_peak", "bull_rally", "slow_bull", "bounce"}
BEAR_PHASES = {"bear_crash", "bear_bottom", "correction"}

# 指标权重 — 从 heat_index_v2 模块导入, 与 config/*.yaml 保持同步
WEIGHTS = INDICATOR_WEIGHTS
IND_DIMS = {
    "pe": "valuation",
    "buffett": "valuation",
    "margin_ratio": "fund",
    "north_ratio": "fund",
    "yield_spread": "fund",
    "m1_m2_spread": "fund",
    "seal_rate": "sentiment",
    "turnover_m2": "sentiment",
    "turnover": "sentiment",
    "new_high": "structure",
    "ma_alignment": "structure",
}
DIMS = ["valuation", "fund", "sentiment", "structure"]

IND_COLS = [
    "pe",
    "buffett",
    "margin_ratio",
    "north_ratio",
    "yield_spread",
    "m1_m2_spread",
    "seal_rate",
    "turnover_m2",
    "turnover",
    "new_high",
    "ma_alignment",
]

SATURATION_CUTOFF = 0.85
SATURATION_HEADROOM = 0.15


# 涨跌停因子
def _get_limit_factor(code):
    c = str(code).replace("sh", "").replace("sz", "").replace("bj", "")
    if c.startswith("300") or c.startswith("301") or c.startswith("688"):
        return 0.20
    if c.startswith("8") or c.startswith("4") or c.startswith("920"):
        return 0.30
    return 0.10


def get_phase(date_str):
    for start, end, phase, desc in MARKET_PHASES:
        if start <= date_str <= end:
            return phase, desc
    return "unknown", "未定义"


def _t_test(a, b):
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return 0.0, 1.0
    m1, m2 = float(np.mean(a)), float(np.mean(b))
    v1, v2 = float(np.var(a, ddof=1)), float(np.var(b, ddof=1))
    se = math.sqrt(v1 / n1 + v2 / n2)
    if se == 0:
        return 0.0, 1.0
    t = (m1 - m2) / se
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))
    return t, p


@timed("backtest_v2")
def run_backtest():
    print("=" * 70)
    print("V2 热度指数全历史回测 (内存优化版)")
    print("=" * 70)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA cache_size=-80000")

    all_dates = [
        r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM stock_daily ORDER BY trade_date").fetchall()
    ]
    print(f"\n总交易日: {len(all_dates)} ({all_dates[0]} ~ {all_dates[-1]})")

    # ── 批量预计算所有指标原始值 ────────────────────────────────────────
    print("\n>>> 预计算指标原始值...")

    # 1. index_daily_pe (PE)
    print("  [1/8] PE (index_daily_pe)...")
    pe_df = pd.read_sql("SELECT trade_date, pe_med, n_stocks FROM index_daily_pe WHERE pe_med IS NOT NULL", conn)
    pe_df["trade_date"] = pe_df["trade_date"].astype(str)
    print(f"        {len(pe_df)} rows")

    # 2. stock_market_cap (巴菲特指标)
    print("  [2/8] Buffett (stock_market_cap + gdp_quarterly)...")
    mvcap_df = pd.read_sql(
        "SELECT trade_date, total_mv FROM stock_market_cap WHERE total_mv > 0 ORDER BY trade_date", conn
    )
    mvcap_df["trade_date"] = mvcap_df["trade_date"].astype(str)
    gdp_df = pd.read_sql("SELECT quarter, gdp FROM gdp_quarterly WHERE gdp IS NOT NULL ORDER BY quarter", conn)
    gdp_df["year"] = gdp_df["quarter"].str[:4].astype(int)
    annual_gdp = gdp_df.groupby("year")["gdp"].sum().to_dict()
    available_years = sorted(annual_gdp.keys())

    def _get_gdp_year(td_year):
        gy = td_year - 1
        while gy not in annual_gdp and gy > min(available_years):
            gy -= 1
        return gy if gy in annual_gdp else None

    mvcap_df["gdp_year"] = mvcap_df["trade_date"].str[:4].astype(int).apply(_get_gdp_year)
    mvcap_df["annual_gdp"] = mvcap_df["gdp_year"].map(annual_gdp)
    mvcap_df = mvcap_df.dropna(subset=["annual_gdp"])
    mvcap_df["buffett_ratio"] = mvcap_df["total_mv"] * 10000 / (mvcap_df["annual_gdp"] * 1e8)
    print(f"        {len(mvcap_df)} rows")

    # 3. margin_ratio (两融余额市值比)
    print("  [3/8] Margin ratio...")
    margin_hist = pd.read_sql(
        """
        SELECT m.trade_date, AVG((m.rzye + m.rqye)) / (c.total_circ_mv * 10000) as ratio
        FROM margin_history m
        JOIN (SELECT trade_date, MAX(total_circ_mv) as total_circ_mv FROM daily_circ_mv
              WHERE total_circ_mv > 0 GROUP BY trade_date) c
          ON m.trade_date = c.trade_date
        WHERE m.rzye > 0
        GROUP BY m.trade_date
        ORDER BY m.trade_date
    """,
        conn,
    )
    margin_hist["trade_date"] = margin_hist["trade_date"].astype(str)
    print(f"        {len(margin_hist)} rows")

    # 4. daily_seal_rate
    print("  [4/8] Seal rate...")
    seal_df = pd.read_sql("SELECT trade_date, seal_rate FROM daily_seal_rate WHERE seal_rate IS NOT NULL", conn)
    seal_df["trade_date"] = seal_df["trade_date"].astype(str)
    print(f"        {len(seal_df)} rows")

    # 5. turnover_m2 (成交额/M2)
    print("  [5/8] Turnover/M2...")
    m2_all = pd.read_sql("SELECT month, m2_billion FROM m2_monthly WHERE m2_billion IS NOT NULL ORDER BY month", conn)
    amt_monthly = pd.read_sql(
        """
        SELECT substr(trade_date, 1, 7) as month, AVG(daily_amt)*1000 as avg_daily_amt FROM (
            SELECT trade_date, SUM(amount) as daily_amt
            FROM stock_daily WHERE amount > 0 AND trade_date >= '2010-01-01'
            GROUP BY trade_date
        ) GROUP BY month ORDER BY month
    """,
        conn,
    )
    m2_merged = m2_all.merge(amt_monthly, on="month", how="inner")
    m2_merged["ratio"] = m2_merged["avg_daily_amt"] / (m2_merged["m2_billion"] * 1e8)
    # 当日成交额
    daily_amt = pd.read_sql(
        "SELECT trade_date, SUM(amount)*1000 as amount FROM stock_daily WHERE amount > 0 GROUP BY trade_date", conn
    )
    daily_amt["trade_date"] = daily_amt["trade_date"].astype(str)
    daily_amt["month"] = daily_amt["trade_date"].str[:7]
    # FIX: 与引擎 calc_turnover_m2 保持一致 — M2 按月回填到最近可用月份
    # (原 left merge 要求精确月份匹配, 当 m2_monthly 缺最新月时 turnover_m2 整段缺失)
    import bisect as _bisect

    _m2_months = list(m2_all["month"])
    _m2_vals = list(m2_all["m2_billion"])
    daily_amt["m2_billion"] = daily_amt["month"].apply(
        lambda mm: (
            _m2_vals[_bisect.bisect_right(_m2_months, mm) - 1]
            if _bisect.bisect_right(_m2_months, mm) - 1 >= 0
            else None
        )
    )
    daily_amt["turnover_m2"] = daily_amt["amount"] / (daily_amt["m2_billion"] * 1e8)
    print(f"        {len(daily_amt)} rows")

    # 6. turnover (换手率)
    print("  [6/8] Turnover rate...")
    turnover_df = pd.read_sql(
        "SELECT trade_date, turnover_rate FROM daily_turnover WHERE turnover_rate IS NOT NULL", conn
    )
    turnover_df["trade_date"] = turnover_df["trade_date"].astype(str)
    print(f"        {len(turnover_df)} rows")

    # 7. new_high
    print("  [7/8] New high ratio...")
    newhigh_df = pd.read_sql(
        "SELECT trade_date, new_high_ratio FROM daily_new_high WHERE new_high_ratio IS NOT NULL", conn
    )
    newhigh_df["trade_date"] = newhigh_df["trade_date"].astype(str)
    print(f"        {len(newhigh_df)} rows")

    # 8. ma_alignment
    print("  [8/8] MA alignment...")
    ma_align_df = pd.read_sql(
        "SELECT trade_date, ma_alignment_ratio FROM daily_ma_alignment WHERE ma_alignment_ratio IS NOT NULL", conn
    )
    ma_align_df["trade_date"] = ma_align_df["trade_date"].astype(str)
    print(f"        {len(ma_align_df)} rows")

    # 9. north_ratio (北向净流入比 = north_net×1000/amount)
    print("  [9/11] North ratio...")
    north_hist = pd.read_sql(
        """
        SELECT n.trade_date, n.north_net * 1000.0 / a.amount AS ratio
        FROM northbound_history n
        JOIN (SELECT trade_date, SUM(amount) AS amount FROM stock_daily WHERE amount>0 GROUP BY trade_date) a
          ON n.trade_date = a.trade_date
        WHERE n.north_net IS NOT NULL AND a.amount > 0
        ORDER BY n.trade_date
    """,
        conn,
    )
    north_hist["trade_date"] = north_hist["trade_date"].astype(str)
    print(f"        {len(north_hist)} rows")

    # 10. yield_spread (10Y-2Y 期限利差)
    print("  [10/11] Yield spread (10Y-2Y)...")
    yspread_df = pd.read_sql(
        """
        SELECT trade_date,
               MAX(CASE WHEN curve_term=10.0 THEN yield_rate END) AS y10,
               MAX(CASE WHEN curve_term=2.0 THEN yield_rate END) AS y2
        FROM bond_yield WHERE curve_term IN (2.0, 10.0)
        GROUP BY trade_date
    """,
        conn,
    )
    yspread_df["trade_date"] = yspread_df["trade_date"].astype(str)
    yspread_df["spread"] = yspread_df["y10"] - yspread_df["y2"]
    print(f"        {len(yspread_df)} rows")

    # 11. m1_m2_spread (M1同比 - M2同比, 月频映射到交易日)
    print("  [11/11] M1-M2 spread...")
    mser = pd.read_sql(
        """
        SELECT a.month, a.m1_yoy - b.m2_yoy AS spread
        FROM m1_monthly a JOIN m2_monthly b ON a.month = b.month
        WHERE a.m1_yoy IS NOT NULL AND b.m2_yoy IS NOT NULL
    """,
        conn,
    )
    _m1m2 = pd.DataFrame({"trade_date": all_dates})
    _m1m2["month"] = _m1m2["trade_date"].str[:7]
    _m1m2 = _m1m2.merge(mser, on="month", how="left").sort_values("trade_date")
    _m1m2["spread"] = _m1m2["spread"].ffill()
    _m1m2["trade_date"] = _m1m2["trade_date"].astype(str)
    print(f"        {len(_m1m2)} rows")

    # 上证综指
    idx_df = pd.read_sql(
        "SELECT trade_date, close FROM index_daily WHERE index_code='sh000001' ORDER BY trade_date", conn
    )
    idx_df["trade_date"] = idx_df["trade_date"].astype(str)
    idx_df = idx_df.set_index("trade_date")
    idx_close = idx_df["close"]

    conn.close()
    print("\n预计算完成。开始逐日计算百分位得分...")

    # ── 逐日计算百分位得分 ────────────────────────────────────────────────
    results = []
    t_start = time.time()

    for i, td in enumerate(all_dates):
        td_year = int(td[:4])
        ten_years_ago = str(td_year - 10) + td[4:]

        scores = {}
        raws = {}

        # PE
        cur_pe_row = pe_df[pe_df["trade_date"] <= td]
        if len(cur_pe_row) > 0:
            cur_pe = cur_pe_row.iloc[-1]["pe_med"]
            cur_n = cur_pe_row.iloc[-1]["n_stocks"]
            hist_pe = pe_df[(pe_df["trade_date"] >= ten_years_ago) & (pe_df["pe_med"].notna())].copy()
            if cur_n > 0 and len(hist_pe) > 60:
                lo, hi = cur_n * 0.5, cur_n * 1.5
                if cur_n >= 600:
                    lo = max(lo, 450)
                hist_pe = hist_pe[hist_pe["n_stocks"].between(lo, hi)]
            if len(hist_pe) >= 60:
                pct = _pct_rank(hist_pe["pe_med"], cur_pe)
                scores["pe"] = max(0, min(100, pct * 100))
                raws["pe"] = cur_pe

        # Buffett
        cur_buffett_row = mvcap_df[mvcap_df["trade_date"] <= td]
        if len(cur_buffett_row) > 0:
            cur_br = cur_buffett_row.iloc[-1]["buffett_ratio"]
            hist_buffett = mvcap_df[(mvcap_df["trade_date"] >= ten_years_ago) & (mvcap_df["buffett_ratio"].notna())]
            if len(hist_buffett) >= 60:
                pct = _pct_rank(hist_buffett["buffett_ratio"], cur_br)
                scores["buffett"] = max(0, min(100, pct * 100))
                raws["buffett"] = cur_br

        # Margin ratio
        cur_margin_row = margin_hist[margin_hist["trade_date"] <= td]
        if len(cur_margin_row) > 0:
            cur_mr = cur_margin_row.iloc[-1]["ratio"]
            hist_margin = margin_hist[(margin_hist["trade_date"] >= ten_years_ago) & (margin_hist["ratio"].notna())]
            if len(hist_margin) >= 60:
                pct = _pct_rank(hist_margin["ratio"], cur_mr)
                if pct <= SATURATION_CUTOFF:
                    sc = pct * 100
                else:
                    adjusted = SATURATION_CUTOFF + SATURATION_HEADROOM * (1 - math.exp(-(pct - SATURATION_CUTOFF) * 20))
                    sc = adjusted * 100
                scores["margin_ratio"] = max(0, min(100, sc))
                raws["margin_ratio"] = cur_mr

        # Seal rate
        cur_seal = seal_df[seal_df["trade_date"] == td]
        if len(cur_seal) > 0:
            cur_sr = cur_seal.iloc[0]["seal_rate"]
            hist_seal = seal_df[(seal_df["trade_date"] >= ten_years_ago) & (seal_df["seal_rate"].notna())]
            if len(hist_seal) >= 60:
                pct = _pct_rank(hist_seal["seal_rate"], cur_sr)
                scores["seal_rate"] = max(0, min(100, pct * 100))
                raws["seal_rate"] = cur_sr

        # Turnover/M2
        cur_tm2 = daily_amt[daily_amt["trade_date"] == td]
        if len(cur_tm2) > 0 and pd.notna(cur_tm2.iloc[0]["turnover_m2"]):
            cur_tm2_val = cur_tm2.iloc[0]["turnover_m2"]
            if len(m2_merged) >= 60:
                pct = _pct_rank(m2_merged["ratio"], cur_tm2_val)
                scores["turnover_m2"] = max(0, min(100, pct * 100))
                raws["turnover_m2"] = cur_tm2_val

        # Turnover rate
        cur_turnover = turnover_df[turnover_df["trade_date"] == td]
        if len(cur_turnover) > 0:
            cur_tr = cur_turnover.iloc[0]["turnover_rate"]
            hist_tr = turnover_df[(turnover_df["trade_date"] >= ten_years_ago) & (turnover_df["turnover_rate"].notna())]
            if len(hist_tr) >= 60:
                pct = _pct_rank(hist_tr["turnover_rate"], cur_tr)
                scores["turnover"] = max(0, min(100, pct * 100))
                raws["turnover"] = cur_tr

        # New high
        cur_nh = newhigh_df[newhigh_df["trade_date"] == td]
        if len(cur_nh) > 0:
            cur_nh_val = cur_nh.iloc[0]["new_high_ratio"]
            hist_nh = newhigh_df[(newhigh_df["trade_date"] >= ten_years_ago) & (newhigh_df["new_high_ratio"].notna())]
            if len(hist_nh) >= 60:
                pct = _pct_rank(hist_nh["new_high_ratio"], cur_nh_val)
                scores["new_high"] = max(0, min(100, pct * 100))
                raws["new_high"] = cur_nh_val

        # MA alignment
        cur_ma = ma_align_df[ma_align_df["trade_date"] == td]
        if len(cur_ma) == 0:
            cur_ma = ma_align_df[ma_align_df["trade_date"] <= td]
        if len(cur_ma) > 0:
            cur_ma_val = cur_ma.iloc[-1]["ma_alignment_ratio"]
            hist_ma = ma_align_df[
                (ma_align_df["trade_date"] >= ten_years_ago) & (ma_align_df["ma_alignment_ratio"].notna())
            ]
            if len(hist_ma) >= 60:
                pct = _pct_rank(hist_ma["ma_alignment_ratio"], cur_ma_val)
                scores["ma_alignment"] = max(0, min(100, pct * 100))
                raws["ma_alignment"] = cur_ma_val

        # North ratio
        cur_north = north_hist[north_hist["trade_date"] <= td]
        if len(cur_north) > 0:
            cur_nr = cur_north.iloc[-1]["ratio"]
            hist_nr = north_hist[(north_hist["trade_date"] >= ten_years_ago) & (north_hist["ratio"].notna())]
            if len(hist_nr) >= 60:
                pct = _pct_rank(hist_nr["ratio"], cur_nr)
                scores["north_ratio"] = max(0, min(100, pct * 100))
                raws["north_ratio"] = cur_nr

        # Yield spread (10Y-2Y)
        cur_ys = yspread_df[yspread_df["trade_date"] <= td]
        if len(cur_ys) > 0 and pd.notna(cur_ys.iloc[-1]["spread"]):
            cur_ys_val = cur_ys.iloc[-1]["spread"]
            hist_ys = yspread_df[(yspread_df["trade_date"] >= ten_years_ago) & (yspread_df["spread"].notna())]
            if len(hist_ys) >= 60:
                pct = _pct_rank(-hist_ys["spread"], -cur_ys_val)
                scores["yield_spread"] = max(0, min(100, pct * 100))
                raws["yield_spread"] = cur_ys_val

        # M1-M2 spread
        cur_mm = _m1m2[_m1m2["trade_date"] <= td]
        if len(cur_mm) > 0 and pd.notna(cur_mm.iloc[-1]["spread"]):
            cur_mm_val = cur_mm.iloc[-1]["spread"]
            hist_mm = _m1m2[(_m1m2["trade_date"] >= ten_years_ago) & (_m1m2["spread"].notna())]
            if len(hist_mm) >= 60:
                pct = _pct_rank(hist_mm["spread"], cur_mm_val)
                scores["m1_m2_spread"] = max(0, min(100, pct * 100))
                raws["m1_m2_spread"] = cur_mm_val

        # 维度分
        dim_scores = {}
        for dim_name in DIMS:
            ind_keys = [k for k, v in IND_DIMS.items() if v == dim_name]
            available = [(k, scores[k]) for k in ind_keys if k in scores and scores[k] is not None]
            if not available:
                dim_scores[dim_name] = None
                continue
            w = sum(WEIGHTS[k] for k, _ in available)
            dim_scores[dim_name] = sum(v * WEIGHTS[k] for k, v in available) / w if w > 0 else None

        # 综合得分
        valid_scores = [(k, v) for k, v in scores.items() if v is not None]
        if not valid_scores:
            composite = None
        else:
            total_weight = sum(WEIGHTS[k] for k, _ in valid_scores)
            composite = sum(v * WEIGHTS[k] for k, v in valid_scores) / total_weight if total_weight > 0 else None

        results.append(
            {
                "trade_date": td,
                "composite_score": round(composite, 1) if composite is not None else None,
                "level": v2_level(composite),
                "dimensions": {
                    dim: round(dim_scores.get(dim), 1) if dim_scores.get(dim) is not None else None for dim in DIMS
                },
                "indicators": {k: round(v, 1) if v is not None else None for k, v in scores.items()},
                "indicator_raw": {k: round(v, 6) if v is not None else None for k, v in raws.items()},
            }
        )

        if (i + 1) % 500 == 0:
            elapsed = time.time() - t_start
            eta = elapsed / (i + 1) * (len(all_dates) - i - 1)
            print(
                f"  [{i + 1}/{len(all_dates)}] {td} composite={results[-1]['composite_score']} "
                f"({elapsed:.0f}s elapsed, ETA {eta:.0f}s)"
            )

    elapsed = time.time() - t_start
    print(f"\n计算完成: {len(results)} 日期 ({elapsed:.1f}s)")

    # ── 构建 DataFrame ────────────────────────────────────────────────────
    df = pd.DataFrame(results)
    df["trade_date"] = df["trade_date"].astype(str)
    df["close"] = df["trade_date"].map(idx_close)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["phase"] = df["trade_date"].apply(lambda d: get_phase(d)[0])
    df["phase_desc"] = df["trade_date"].apply(lambda d: get_phase(d)[1])
    df["is_bull"] = df["phase"].isin(BULL_PHASES)
    df["is_bear"] = df["phase"].isin(BEAR_PHASES)

    # 展开指标
    for ind in IND_COLS:
        df[f"ind_{ind}"] = df["indicators"].apply(lambda d: d.get(ind) if isinstance(d, dict) else None)

    # ── 分析 1: 各阶段热度分布 ───────────────────────────────────────────
    print("\n" + "=" * 70)
    print("分析 1: 各市场阶段热度分布")
    print("=" * 70)

    phase_stats = (
        df.groupby("phase")
        .agg(
            count=("composite_score", "count"),
            mean=("composite_score", "mean"),
            median=("composite_score", "median"),
            std=("composite_score", "std"),
            min=("composite_score", "min"),
            max=("composite_score", "max"),
            p25=("composite_score", lambda x: x.quantile(0.25)),
            p75=("composite_score", lambda x: x.quantile(0.75)),
        )
        .round(1)
    )
    phase_order = [
        "bull_peak",
        "bull_rally",
        "slow_bull",
        "bounce",
        "correction",
        "bear_crash",
        "bear_bottom",
        "unknown",
    ]
    phase_stats = phase_stats.reindex([p for p in phase_order if p in phase_stats.index])
    print(phase_stats.to_string())

    # ── 分析 2: 牛熊识别准确率 ───────────────────────────────────────────
    print("\n" + "=" * 70)
    print("分析 2: 牛熊识别准确率")
    print("=" * 70)

    bull_dates = df[df["is_bull"]]
    bear_dates = df[df["is_bear"]]
    bull_hit = (bull_dates["composite_score"] >= 55).sum()
    bull_total = len(bull_dates)
    bear_hit = (bear_dates["composite_score"] < 40).sum()
    bear_total = len(bear_dates)

    print(f"\n牛市期间 (n={bull_total}):")
    print(f"  热度>=55 (正确信号): {bull_hit} ({bull_hit / bull_total * 100:.1f}%)")
    print(
        f"  热度40-55 (中性):    {((bull_dates['composite_score'] >= 40).sum() - bull_hit)} ({((bull_dates['composite_score'] >= 40).sum() - bull_hit) / bull_total * 100:.1f}%)"
    )
    print(
        f"  热度<40 (错误信号):  {(bull_dates['composite_score'] < 40).sum()} ({(bull_dates['composite_score'] < 40).sum() / bull_total * 100:.1f}%)"
    )
    print(f"  平均热度: {bull_dates['composite_score'].mean():.1f}")

    print(f"\n熊市期间 (n={bear_total}):")
    print(f"  热度<40 (正确信号):  {bear_hit} ({bear_hit / bear_total * 100:.1f}%)")
    print(
        f"  热度40-55 (中性):    {((bear_dates['composite_score'] >= 40).sum() - bear_hit)} ({((bear_dates['composite_score'] >= 40).sum() - bear_hit) / bear_total * 100:.1f}%)"
    )
    print(
        f"  热度>=55 (错误信号): {(bear_dates['composite_score'] >= 55).sum()} ({(bear_dates['composite_score'] >= 55).sum() / bear_total * 100:.1f}%)"
    )
    print(f"  平均热度: {bear_dates['composite_score'].mean():.1f}")

    # ── 分析 3: 极值信号 → 后续市场表现 ─────────────────────────────────
    print("\n" + "=" * 70)
    print("分析 3: 极值信号 → 后续市场表现")
    print("=" * 70)

    extreme_high = df[df["composite_score"] >= 80].copy()
    extreme_low = df[df["composite_score"] <= 20].copy()

    def fwd_return(td, n_days):
        try:
            idx_pos = list(idx_df.index).index(td)
            if idx_pos + n_days < len(idx_df):
                fwd_close = idx_df.iloc[idx_pos + n_days]["close"]
                cur_close = idx_df.iloc[idx_pos]["close"]
                return (fwd_close / cur_close - 1) * 100
        except Exception:
            pass
        return None

    print(f"\n极热信号 (热度>=80, n={len(extreme_high)}):")
    for n in [5, 20, 60]:
        rets = extreme_high["trade_date"].apply(lambda d: fwd_return(d, n))
        valid = rets.dropna()
        if len(valid) > 0:
            print(
                f"  后{n}日收益: 均值={valid.mean():.1f}%  中位数={valid.median():.1f}%  正收益率={((valid > 0).mean() * 100):.0f}%  (n={len(valid)})"
            )

    print(f"\n极冷信号 (热度<=20, n={len(extreme_low)}):")
    for n in [5, 20, 60]:
        rets = extreme_low["trade_date"].apply(lambda d: fwd_return(d, n))
        valid = rets.dropna()
        if len(valid) > 0:
            print(
                f"  后{n}日收益: 均值={valid.mean():.1f}%  中位数={valid.median():.1f}%  正收益率={((valid > 0).mean() * 100):.0f}%  (n={len(valid)})"
            )

    # ── 分析 4: 热度与指数相关性 ─────────────────────────────────────────
    print("\n" + "=" * 70)
    print("分析 4: 热度与指数相关性")
    print("=" * 70)

    valid_both = df.dropna(subset=["composite_score", "close"])
    corr = valid_both["composite_score"].corr(valid_both["close"])
    print(f"\n热度 vs 上证收盘价 同期相关系数: {corr:.3f}")

    for lag in [1, 5, 10, 20, 60]:
        df[f"heat_lag{lag}"] = df["composite_score"].shift(lag)
        df[f"ret_lag{lag}"] = df["close"].pct_change(lag) * 100
        v = df.dropna(subset=[f"heat_lag{lag}", f"ret_lag{lag}"])
        if len(v) > 30:
            c = v[f"heat_lag{lag}"].corr(v[f"ret_lag{lag}"])
            print(f"  热度领先{lag:2d}日 → 指数{lag:2d}日收益相关: {c:.3f} (n={len(v)})")

    # ── 分析 5: 各指标牛熊区分度 ────────────────────────────────────────
    print("\n" + "=" * 70)
    print("分析 5: 各指标牛熊区分度")
    print("=" * 70)

    ind_cols = [
        "pe",
        "buffett",
        "margin_ratio",
        "north_ratio",
        "yield_spread",
        "m1_m2_spread",
        "seal_rate",
        "turnover_m2",
        "turnover",
        "new_high",
        "ma_alignment",
    ]
    print(f"\n{'指标':15s} | {'牛市均值':>8s} | {'熊市均值':>8s} | {'区分度':>8s} | {'t统计量':>8s} | {'p值':>10s}")
    print("-" * 75)
    for ind in ind_cols:
        col = f"ind_{ind}"
        bull_vals = df[df["is_bull"]][col].dropna()
        bear_vals = df[df["is_bear"]][col].dropna()
        if len(bull_vals) > 5 and len(bear_vals) > 5:
            b_mean = bull_vals.mean()
            s_mean = bear_vals.mean()
            sep = b_mean - s_mean
            t_stat, p_val = _t_test(bull_vals.values, bear_vals.values)
            sig = "***" if p_val < 0.001 else ("**" if p_val < 0.01 else ("*" if p_val < 0.05 else ""))
            print(f"  {ind:15s} | {b_mean:8.1f} | {s_mean:8.1f} | {sep:8.1f} | {t_stat:8.2f} | {p_val:.2e} {sig}")

    # ── 分析 6: 关键牛熊转折点 ──────────────────────────────────────────
    print("\n" + "=" * 70)
    print("分析 6: 关键牛熊转折点热度")
    print("=" * 70)

    key_dates = [
        ("2015-06-12", "5178大顶"),
        ("2015-08-26", "股灾底2850"),
        ("2016-01-28", "熔断底2638"),
        ("2018-01-29", "蓝筹牛顶3587"),
        ("2018-10-19", "贸易战底2449"),
        ("2019-04-19", "春季顶3288"),
        ("2020-03-23", "疫情底2646"),
        ("2021-02-18", "核心资产顶3731"),
        ("2021-12-13", "结构牛顶"),
        ("2022-04-27", "熊市底2863"),
        ("2024-02-05", "底部2635"),
        ("2024-09-24", "924行情起点"),
        ("2024-10-08", "924行情顶3674"),
        ("2026-08-11", "最新"),
    ]

    print(
        f"\n{'日期':12s} | {'事件':20s} | {'热度':>6s} | {'级别':6s} | {'上证':>8s} | {'估值':>6s} | {'资金':>6s} | {'情绪':>6s} | {'结构':>6s}"
    )
    print("-" * 100)
    for kd, desc in key_dates:
        row = df[df["trade_date"] == kd]
        if len(row) > 0:
            r = row.iloc[0]
            dims = r["dimensions"]
            print(
                f"  {kd} | {desc:20s} | {r['composite_score']:6.1f} | {r['level']:6s} | "
                f"{r['close']:8.0f} | {dims.get('valuation', 0) or 0:6.1f} | {dims.get('fund', 0) or 0:6.1f} | "
                f"{dims.get('sentiment', 0) or 0:6.1f} | {dims.get('structure', 0) or 0:6.1f}"
            )

    # ── 分析 7: 月度趋势 ────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("分析 7: 月度热度趋势 (关键月份)")
    print("=" * 70)
    df["month"] = df["trade_date"].str[:7]
    monthly = (
        df.groupby("month")
        .agg(
            heat_mean=("composite_score", "mean"),
            close_mean=("close", "mean"),
            n_days=("composite_score", "count"),
        )
        .round(1)
    )

    # 选取关键月份
    key_months = [
        "2015-06",
        "2015-07",
        "2015-08",  # 股灾
        "2016-01",
        "2016-02",  # 熔断
        "2018-01",
        "2018-02",
        "2018-10",  # 贸易战
        "2019-01",
        "2019-02",
        "2019-03",  # 春季躁动
        "2020-03",
        "2020-07",  # 疫情+反弹
        "2021-02",
        "2021-12",  # 顶部
        "2022-04",
        "2022-10",  # 熊市底
        "2024-02",
        "2024-09",
        "2024-10",  # 924行情
        "2025-06",
        "2025-07",
        "2026-07",
        "2026-08",  # 最新
    ]
    print(f"\n{'月份':8s} | {'月均热度':>8s} | {'月均上证':>8s} | {'天数':>4s}")
    print("-" * 40)
    for m in key_months:
        if m in monthly.index:
            r = monthly.loc[m]
            print(f"  {m} | {r['heat_mean']:8.1f} | {r['close_mean']:8.0f} | {int(r['n_days']):4d}")

    # ── 保存 ────────────────────────────────────────────────────────────
    csv_data = df[["trade_date", "composite_score", "level", "close", "phase", "phase_desc"]].copy()
    for ind in ind_cols:
        csv_data[f"ind_{ind}"] = df[f"ind_{ind}"]
    csv_data.to_csv("reports/backtest_v2_detail.csv", index=False)
    print(f"\n详细数据已保存: reports/backtest_v2_detail.csv ({len(csv_data)} rows)")

    summary = {
        "total_dates": len(results),
        "date_range": f"{all_dates[0]} ~ {all_dates[-1]}",
        "bull_mean_score": round(float(bull_dates["composite_score"].mean()), 1),
        "bear_mean_score": round(float(bear_dates["composite_score"].mean()), 1),
        "bull_correct_pct": round(bull_hit / bull_total * 100, 1) if bull_total > 0 else 0,
        "bear_correct_pct": round(bear_hit / bear_total * 100, 1) if bear_total > 0 else 0,
        "corr_heat_vs_index": round(float(corr), 3),
        "extreme_high_count": len(extreme_high),
        "extreme_low_count": len(extreme_low),
    }
    with open("reports/backtest_v2_summary.json", "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print("统计摘要已保存: reports/backtest_v2_summary.json")

    return df, summary


if __name__ == "__main__":
    df, summary = run_backtest()
