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
from src.indicators.heat_index_v2 import (
    INDICATOR_WEIGHTS,
    ROLLING_PCT_WINDOW,
    _apply_new_high_divergence,
    _apply_sentiment_divergence,
    _detrend,
)
from src.output.json_writer import get_heat_level
from src.common import timed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _pctr(series, value, window=ROLLING_PCT_WINDOW):
    """滚动窗口百分位 — 与引擎 _pct_rank 口径一致

    P2.1: 默认 tail(1260 交易日≈5年)。
    M1.1 (2026-09): 增加 window 参数 — 月频序列调用传 60 (60 个月窗口),
    委托 utils._pct_rank 的 window 语义 (len>window 才 tail, 短序列退化为全量)。
    """
    return _pct_rank(series, value, window=window)


def v2_level(score):
    """展示档色标 — M1.6: 统一委托 json_writer.get_heat_level (heat_levels 单源),
    消灭回测脚本内 65/55/40 硬编码与展示层漂移。"""
    return get_heat_level(score)


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
    "yield_spread": "fund",
    "m1_m2_spread": "fund",
    "margin_buy_ratio": "fund",
    "turnover": "sentiment",
    "futures_discount": "sentiment",
    "new_high": "structure",
    "ma_alignment": "structure",
}
DIMS = ["valuation", "fund", "sentiment", "structure"]

IND_COLS = [
    "pe",
    "buffett",
    "margin_ratio",
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
    # M2b-0: 当日值同引擎口径 (stock_daily 当日 Σamount/Σcirc_mv×10, 而非 daily_turnover 存储值 —
    # 两者系统性差 ~0.8 分; 单次 GROUP BY 预计算, 逐日用 .get(td))
    _cur_rate = pd.read_sql(
        "SELECT trade_date, SUM(amount)/SUM(circ_mv)*10 AS r FROM stock_daily"
        " WHERE amount > 0 AND circ_mv > 0 GROUP BY trade_date",
        conn,
    )
    _cur_rate["trade_date"] = _cur_rate["trade_date"].astype(str)
    cur_rate_map = _cur_rate.set_index("trade_date")["r"]
    print(f"        cur_rate_map {len(cur_rate_map)} rows")

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
        ORDER BY a.month
    """,
        conn,
    )
    _m1m2 = pd.DataFrame({"trade_date": all_dates})
    _m1m2["month"] = _m1m2["trade_date"].str[:7]
    _m1m2 = _m1m2.merge(mser, on="month", how="left").sort_values("trade_date")
    _m1m2["spread"] = _m1m2["spread"].ffill()
    _m1m2["trade_date"] = _m1m2["trade_date"].astype(str)
    print(f"        {len(_m1m2)} rows")

    # 12. breadth (涨跌家数广度, P1)
    print("  [12/13] Breadth (daily_updown)...")
    breadth_df = pd.read_sql(
        "SELECT trade_date, up_down_ratio FROM daily_updown WHERE up_down_ratio IS NOT NULL AND up_down_ratio > 0",
        conn,
    )
    breadth_df["trade_date"] = breadth_df["trade_date"].astype(str)
    print(f"        {len(breadth_df)} rows")

    # 13. southbound (南向净买额, P1)
    print("  [13/13] Southbound (daily_hsgt_south)...")
    south_df = pd.read_sql("SELECT trade_date, south_net FROM daily_hsgt_south WHERE south_net IS NOT NULL", conn)
    south_df["trade_date"] = south_df["trade_date"].astype(str)
    print(f"        {len(south_df)} rows")

    # 14. futures basis (IF基差, P1)
    print("  [14/17] Futures basis (daily_futures_basis)...")
    basis_df = pd.read_sql("SELECT trade_date, basis_rate FROM daily_futures_basis WHERE basis_rate IS NOT NULL", conn)
    basis_df["trade_date"] = basis_df["trade_date"].astype(str)
    print(f"        {len(basis_df)} rows")

    # 15. amplitude (振幅热度, P3) — 沪深300 日内振幅 (high-low)/prev_close
    print("  [15/17] Amplitude (sh000300 振幅)...")
    amp_all = pd.read_sql(
        "SELECT trade_date, high, low, close FROM index_daily"
        " WHERE index_code='sh000300' AND high>0 AND low>0 AND close>0 ORDER BY trade_date",
        conn,
    )
    amp_all["trade_date"] = amp_all["trade_date"].astype(str)
    amp_all["prev_close"] = amp_all["close"].shift(1)
    amp_all["amplitude"] = (amp_all["high"] - amp_all["low"]) / amp_all["prev_close"]
    print(f"        {len(amp_all)} rows")

    # 16. realized_vol (已实现波动率, P3) — 沪深300 20日对数收益std ×√250
    print("  [16/17] Realized vol (sh000300 20日年化波动)...")
    vol_all = pd.read_sql(
        "SELECT trade_date, close FROM index_daily WHERE index_code='sh000300' AND close>0 ORDER BY trade_date",
        conn,
    )
    vol_all["trade_date"] = vol_all["trade_date"].astype(str)
    vol_all["ret"] = np.log(vol_all["close"]).diff()
    vol_all["realized_vol"] = vol_all["ret"].rolling(20).std() * math.sqrt(250)
    print(f"        {len(vol_all)} rows")

    # 17. margin_buy_ratio (融资买入占比, P3) — rzmre / (turnover_rate × circ_mv × 100)
    print("  [17/17] Margin buy ratio...")
    mbuy_df = pd.read_sql(
        """
        SELECT m.trade_date, m.rzmre / (t.turnover_rate * c.total_circ_mv * 100) AS ratio
        FROM margin_history m
        JOIN daily_turnover t ON m.trade_date = t.trade_date AND t.turnover_rate > 0
        JOIN daily_circ_mv c ON m.trade_date = c.trade_date AND c.total_circ_mv > 0
        WHERE m.rzmre > 0
        ORDER BY m.trade_date
    """,
        conn,
    )
    mbuy_df["trade_date"] = mbuy_df["trade_date"].astype(str)
    mbuy_df["ratio"] = pd.to_numeric(mbuy_df["ratio"], errors="coerce")
    mbuy_df = mbuy_df.dropna(subset=["ratio"])
    print(f"        {len(mbuy_df)} rows")

    # 上证综指
    idx_df = pd.read_sql(
        "SELECT trade_date, close FROM index_daily WHERE index_code='sh000001' ORDER BY trade_date", conn
    )
    idx_df["trade_date"] = idx_df["trade_date"].astype(str)
    idx_df = idx_df.set_index("trade_date")
    idx_close = idx_df["close"]

    conn.close()
    print("\n预计算完成。开始逐日计算百分位得分...")

    # ── M2b-0 (2026-09-02): 统一按 trade_date 排序 ──────────────────────
    # 此前各表加载无 ORDER BY → pandas 位置语义(滚动中位数 / ≤td 取最近行 .iloc[-1])
    # 全部跑在物理乱序上: ma_alignment 2023-05-15 差 33 分(_detrend 顺序敏感),
    # pe cur 取错行(2026-08-03 取 24.3477 而真当日 24.2072) → composite 与引擎逐日
    # 差 0.2~2.8 (个别日更大)。排序后与引擎 calc_* 的 SQL 上界查询(天然 PK=trade_date
    # 序) 完全同构。amp_all/vol_all/idx/_m1m2 已在 SQL 内 ORDER BY, 无需处理。
    for _df in (
        pe_df,
        mvcap_df,
        margin_hist,
        seal_df,
        turnover_df,
        daily_amt,
        newhigh_df,
        ma_align_df,
        yspread_df,
        breadth_df,
        south_df,
        basis_df,
        mbuy_df,
    ):
        _df.sort_values("trade_date", inplace=True, kind="mergesort")

    # 背离惩罚需按日查 index_daily/daily_new_high → 重开专用只读连接 (预加载 conn 已关)
    dv_conn = sqlite3.connect(DB_PATH)

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
            hist_pe = pe_df[
                (pe_df["trade_date"] >= ten_years_ago) & (pe_df["trade_date"] <= td) & (pe_df["pe_med"].notna())
            ].copy()
            # M2b-0: 与引擎 calc_pe 门限一致 — 过滤前 hist≥120 否则 None (2015 早期
            # 引擎 None vs 回测有值, 权重 21.2% 直接改变综合分), cur_n>0 即启用过滤
            if len(hist_pe) >= 120:
                if cur_n > 0:
                    lo, hi = cur_n * 0.5, cur_n * 1.5
                    if cur_n >= 600:
                        lo = max(lo, 450)
                    hist_pe = hist_pe[hist_pe["n_stocks"].between(lo, hi)]
                if len(hist_pe) >= 60:
                    # M2a D2: 回退 M1.3 去趋势 — 原始 pe_med 分位 (与引擎同口径)
                    pct = _pctr(hist_pe["pe_med"], cur_pe)
                    scores["pe"] = max(0, min(100, pct * 100))
                    raws["pe"] = cur_pe

        # Buffett
        cur_buffett_row = mvcap_df[mvcap_df["trade_date"] <= td]
        if len(cur_buffett_row) > 0:
            cur_br = cur_buffett_row.iloc[-1]["buffett_ratio"]
            hist_buffett = mvcap_df[
                (mvcap_df["trade_date"] >= ten_years_ago)
                & (mvcap_df["trade_date"] <= td)
                & (mvcap_df["buffett_ratio"].notna())
            ]
            if len(hist_buffett) >= 60:
                pct = _pctr(hist_buffett["buffett_ratio"], cur_br)
                scores["buffett"] = max(0, min(100, pct * 100))
                raws["buffett"] = cur_br

        # Margin ratio
        cur_margin_row = margin_hist[margin_hist["trade_date"] <= td]
        if len(cur_margin_row) > 0:
            cur_mr = cur_margin_row.iloc[-1]["ratio"]
            hist_margin = margin_hist[
                (margin_hist["trade_date"] >= ten_years_ago)
                & (margin_hist["trade_date"] <= td)
                & (margin_hist["ratio"].notna())
            ]
            if len(hist_margin) >= 60:
                pct = _pctr(hist_margin["ratio"], cur_mr)
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
            hist_seal = seal_df[
                (seal_df["trade_date"] >= ten_years_ago)
                & (seal_df["trade_date"] <= td)
                & (seal_df["seal_rate"].notna())
            ]
            if len(hist_seal) >= 60:
                pct = _pctr(hist_seal["seal_rate"], cur_sr)
                scores["seal_rate"] = max(0, min(100, pct * 100))
                raws["seal_rate"] = cur_sr

        # Turnover/M2 (M1.1+M1.8: 与引擎同口径 — 月频序列 60 个月窗口, 只用 <= td 所在月, 无未来泄漏)
        cur_tm2 = daily_amt[daily_amt["trade_date"] == td]
        if len(cur_tm2) > 0 and pd.notna(cur_tm2.iloc[0]["turnover_m2"]):
            cur_tm2_val = cur_tm2.iloc[0]["turnover_m2"]
            m2_le = m2_merged[m2_merged["month"] <= td[:7]]
            if len(m2_le) >= 60:
                pct = _pctr(m2_le["ratio"], cur_tm2_val, window=60)
                scores["turnover_m2"] = max(0, min(100, pct * 100))
                raws["turnover_m2"] = cur_tm2_val

        # Turnover rate — cur 用引擎同口径 (stock_daily 当日 Σamt/Σmv×10)
        cur_tr = cur_rate_map.get(td)
        if cur_tr is not None and not np.isnan(cur_tr):
            hist_tr = turnover_df[
                (turnover_df["trade_date"] >= ten_years_ago)
                & (turnover_df["trade_date"] <= td)
                & (turnover_df["turnover_rate"].notna())
            ]
            if len(hist_tr) >= 60:
                # M2a D3: 回退 M1.3 去趋势 — 原始换手率分位 (与引擎同口径)
                pct = _pctr(hist_tr["turnover_rate"], cur_tr)
                scores["turnover"] = max(0, min(100, pct * 100))
                raws["turnover"] = cur_tr

        # New high
        cur_nh = newhigh_df[newhigh_df["trade_date"] == td]
        if len(cur_nh) > 0:
            cur_nh_val = cur_nh.iloc[0]["new_high_ratio"]
            hist_nh = newhigh_df[
                (newhigh_df["trade_date"] >= ten_years_ago)
                & (newhigh_df["trade_date"] <= td)
                & (newhigh_df["new_high_ratio"].notna())
            ]
            if len(hist_nh) >= 60:
                pct = _pctr(hist_nh["new_high_ratio"], cur_nh_val)
                scores["new_high"] = max(0, min(100, pct * 100))
                raws["new_high"] = cur_nh_val

        # MA alignment
        cur_ma = ma_align_df[ma_align_df["trade_date"] == td]
        if len(cur_ma) == 0:
            cur_ma = ma_align_df[ma_align_df["trade_date"] <= td]
        if len(cur_ma) > 0:
            cur_ma_val = cur_ma.iloc[-1]["ma_alignment_ratio"]
            hist_ma = ma_align_df[
                (ma_align_df["trade_date"] >= ten_years_ago)
                & (ma_align_df["trade_date"] <= td)
                & (ma_align_df["ma_alignment_ratio"].notna())
            ]
            if len(hist_ma) >= 60:
                det, cur_det = _detrend(hist_ma["ma_alignment_ratio"], cur_ma_val)  # M1.3, 与引擎同口径
                if cur_det is None:  # 历史不足 3 年 → 退化原始值分位
                    pct = _pctr(hist_ma["ma_alignment_ratio"], cur_ma_val)
                else:
                    pct = _pctr(det.dropna(), cur_det)
                scores["ma_alignment"] = max(0, min(100, pct * 100))
                raws["ma_alignment"] = cur_ma_val
            else:
                # M2b-0: 与引擎 calc_ma_alignment_v2 同口径 — 历史不足 60 条时 clamp [20,80]
                # (原实现直接跳过=NaN, 早期 2015 段 composite 与引擎差 1.3~15 分)
                scores["ma_alignment"] = max(20, min(cur_ma_val * 100, 80))
                raws["ma_alignment"] = cur_ma_val

        # Yield spread (10Y-2Y)
        cur_ys = yspread_df[yspread_df["trade_date"] <= td]
        if len(cur_ys) > 0 and pd.notna(cur_ys.iloc[-1]["spread"]):
            cur_ys_val = cur_ys.iloc[-1]["spread"]
            hist_ys = yspread_df[
                (yspread_df["trade_date"] >= ten_years_ago)
                & (yspread_df["trade_date"] <= td)
                & (yspread_df["spread"].notna())
            ]
            if len(hist_ys) >= 60:
                pct = _pctr(hist_ys["spread"], cur_ys_val)  # M2a D1: 方向翻转 (利差高=高分)
                scores["yield_spread"] = max(0, min(100, pct * 100))
                raws["yield_spread"] = cur_ys_val

        # M1-M2 spread (M1.1+M1.8: 与引擎同口径 — 月频序列按 60 个月窗口取分位, 只用 <= td 所在月, 无未来泄漏)
        cur_mm = _m1m2[_m1m2["trade_date"] <= td]
        if len(cur_mm) > 0 and pd.notna(cur_mm.iloc[-1]["spread"]):
            cur_mm_val = cur_mm.iloc[-1]["spread"]
            mser_le = mser[mser["month"] <= td[:7]]
            if len(mser_le) >= 12:
                pct = _pctr(-mser_le["spread"], -cur_mm_val, window=60)  # M2a D1: 方向翻转
                scores["m1_m2_spread"] = max(0, min(100, pct * 100))
                raws["m1_m2_spread"] = cur_mm_val

        # Southbound (P1)
        cur_sb = south_df[south_df["trade_date"] <= td]
        if len(cur_sb) > 0:
            cur_sb_val = cur_sb.iloc[-1]["south_net"]
            hist_sb = south_df[
                (south_df["trade_date"] >= ten_years_ago)
                & (south_df["trade_date"] <= td)
                & (south_df["south_net"].notna())
            ]
            if len(hist_sb) >= 60:
                pct = _pctr(hist_sb["south_net"], cur_sb_val)
                scores["southbound"] = max(0, min(100, pct * 100))
                raws["southbound"] = cur_sb_val

        # Futures basis (P1)
        cur_fb = basis_df[basis_df["trade_date"] <= td]
        if len(cur_fb) > 0:
            cur_fb_val = cur_fb.iloc[-1]["basis_rate"]
            hist_fb = basis_df[
                (basis_df["trade_date"] >= ten_years_ago)
                & (basis_df["trade_date"] <= td)
                & (basis_df["basis_rate"].notna())
            ]
            if len(hist_fb) >= 60:
                pct = _pctr(hist_fb["basis_rate"], cur_fb_val)
                scores["futures_discount"] = max(0, min(100, pct * 100))
                raws["futures_discount"] = cur_fb_val

        # Breadth (P1)
        cur_bd = breadth_df[breadth_df["trade_date"] == td]
        if len(cur_bd) > 0:
            cur_bd_val = cur_bd.iloc[0]["up_down_ratio"]
            hist_bd = breadth_df[
                (breadth_df["trade_date"] >= ten_years_ago)
                & (breadth_df["trade_date"] <= td)
                & (breadth_df["up_down_ratio"].notna())
            ]
            if len(hist_bd) >= 60:
                pct = _pctr(hist_bd["up_down_ratio"], cur_bd_val)
                scores["breadth"] = max(0, min(100, pct * 100))
                raws["breadth"] = cur_bd_val

        # Amplitude (P3) — 方向 pos
        hist_amp = amp_all[(amp_all["trade_date"] >= ten_years_ago) & (amp_all["trade_date"] <= td)]["amplitude"]
        hist_amp = hist_amp.dropna()
        if len(hist_amp) >= 60:
            cur_amp = float(hist_amp.iloc[-1])
            pct = _pctr(hist_amp, cur_amp)
            scores["amplitude"] = max(0, min(100, pct * 100))
            raws["amplitude"] = cur_amp

        # Realized vol (P3) — 方向 neg (波动越低=越贪婪=热度越高, 翻转)
        hist_vol = vol_all[(vol_all["trade_date"] >= ten_years_ago) & (vol_all["trade_date"] <= td)]["realized_vol"]
        hist_vol = hist_vol.dropna()
        if len(hist_vol) >= 60:
            cur_vol = float(hist_vol.iloc[-1])
            pct = _pctr(-hist_vol, -cur_vol)
            scores["realized_vol"] = max(0, min(100, pct * 100))
            raws["realized_vol"] = cur_vol

        # Margin buy ratio (P3) — M2a D1: 方向翻转 (占比低=高分)
        cur_mb = mbuy_df[mbuy_df["trade_date"] <= td]
        if len(cur_mb) > 0:
            cur_mb_val = float(cur_mb.iloc[-1]["ratio"])
            hist_mb = mbuy_df[(mbuy_df["trade_date"] >= ten_years_ago) & (mbuy_df["trade_date"] <= td)]["ratio"]
            if len(hist_mb) >= 60:
                pct = _pctr(-hist_mb, -cur_mb_val)
                scores["margin_buy_ratio"] = max(0, min(100, pct * 100))
                raws["margin_buy_ratio"] = cur_mb_val

        # ── 背离惩罚 (M2b-0: 复刻引擎, 回测此前缺失 → 触发日 composite 差 ~2.7 分) ──
        sk = {"turnover_m2": scores.get("turnover_m2"), "turnover": scores.get("turnover")}
        sk = _apply_sentiment_divergence(dv_conn, td, sk)
        if sk.get("turnover") is not None:
            scores["turnover"] = sk["turnover"]
        if sk.get("turnover_m2") is not None:
            scores["turnover_m2"] = sk["turnover_m2"]
        if scores.get("new_high") is not None:
            scores["new_high"] = _apply_new_high_divergence(dv_conn, td, scores["new_high"])

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

        # 综合得分 (M1.4+M1.5: 仅计分键参与; scores 含 16 展示键)
        valid_scores = [(k, v) for k, v in scores.items() if v is not None and k in WEIGHTS]
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
