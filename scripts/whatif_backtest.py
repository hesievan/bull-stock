#!/usr/bin/env python3
"""
What-if 回测: 广度熔断 (breadth circuit breaker)

问题: 2026-08-11 涨1615/跌3777 (up_down_ratio=0.43), 结构分仅10.2, 但综合分71.3=红色预警。
用户质疑: "涨少跌多却给红牌, 不像是牛市"。

假设: 当市场广度崩溃时, 综合热度不应触发"红色预警"(减仓) 信号。
本脚本复用 backtest_v2.run_backtest 的逐日百分位计算管线 (完全一致), 在综合分上施加"广度熔断":

  GATE: 若 breadth 弱 (up_down_ratio < 0.5, 或 结构维分 < 30), 则
        composite = min(composite, CAP)   # CAP=64 仅消除红区; CAP=55 连橙区也消除

对比指标 (与原版完全一致口径):
  - 牛熊均值差 (区分度), 牛/熊识别准确率
  - 热度 vs 上证 同期相关系数
  - 极热(>=80)/极冷(<=20) 信号后 60 日收益与胜率
  - 红色(>=65)减仓信号后 60 日收益与胜率
  - 被熔断"摘红"的天数, 及其后 60 日真实表现 (验证熔断是否合理)
  - 关键牛熊转折点对比

输出: reports/whatif_detail.csv, reports/whatif_summary.json (不覆盖生产回测产物)
"""

import json
import math
import os
import sqlite3
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import backtest_v2 as bt  # 仅导入常量与函数, 不执行 run_backtest()

DB_PATH = bt.DB_PATH
WEIGHTS = bt.WEIGHTS
IND_DIMS = bt.IND_DIMS
DIMS = bt.DIMS
SATURATION_CUTOFF = bt.SATURATION_CUTOFF
SATURATION_HEADROOM = bt.SATURATION_HEADROOM
_pct_rank = bt._pct_rank
v2_level = bt.v2_level
BULL_PHASES = bt.BULL_PHASES
BEAR_PHASES = bt.BEAR_PHASES

# What-if 配置: (名称, 闸门类型, 阈值, 封顶)
# gate_type: 'up_down' 用 up_down_ratio; 'structure' 用结构维分
CONFIGS = [
    ("BASE", None, None, None),
    ("UD<0.5|cap64", "up_down", 0.5, 64),  # 仅消除红区
    ("UD<0.5|cap55", "up_down", 0.5, 55),  # 红区+橙区都消除
    ("STR<30|cap64", "structure", 30, 64),  # 结构维分替代口径
]


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA cache_size=-80000")

    all_dates = [
        r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM stock_daily ORDER BY trade_date").fetchall()
    ]

    # ── 批量预计算所有指标原始值 (与 backtest_v2 完全一致) ──
    pe_df = pd.read_sql("SELECT trade_date, pe_med, n_stocks FROM index_daily_pe WHERE pe_med IS NOT NULL", conn)
    pe_df["trade_date"] = pe_df["trade_date"].astype(str)

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

    seal_df = pd.read_sql("SELECT trade_date, seal_rate FROM daily_seal_rate WHERE seal_rate IS NOT NULL", conn)
    seal_df["trade_date"] = seal_df["trade_date"].astype(str)

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
    daily_amt = pd.read_sql(
        "SELECT trade_date, SUM(amount)*1000 as amount FROM stock_daily WHERE amount > 0 GROUP BY trade_date", conn
    )
    daily_amt["trade_date"] = daily_amt["trade_date"].astype(str)
    daily_amt["month"] = daily_amt["trade_date"].str[:7]
    daily_amt = daily_amt.merge(m2_all, on="month", how="left")
    daily_amt["turnover_m2"] = daily_amt["amount"] / (daily_amt["m2_billion"] * 1e8)

    turnover_df = pd.read_sql(
        "SELECT trade_date, turnover_rate FROM daily_turnover WHERE turnover_rate IS NOT NULL", conn
    )
    turnover_df["trade_date"] = turnover_df["trade_date"].astype(str)
    newhigh_df = pd.read_sql(
        "SELECT trade_date, new_high_ratio FROM daily_new_high WHERE new_high_ratio IS NOT NULL", conn
    )
    newhigh_df["trade_date"] = newhigh_df["trade_date"].astype(str)
    ma_align_df = pd.read_sql(
        "SELECT trade_date, ma_alignment_ratio FROM daily_ma_alignment WHERE ma_alignment_ratio IS NOT NULL", conn
    )
    ma_align_df["trade_date"] = ma_align_df["trade_date"].astype(str)

    idx_df = pd.read_sql(
        "SELECT trade_date, close FROM index_daily WHERE index_code='sh000001' ORDER BY trade_date", conn
    )
    idx_df["trade_date"] = idx_df["trade_date"].astype(str)
    idx_df = idx_df.set_index("trade_date").sort_index()
    idx_close = idx_df["close"]

    # 广度: 涨跌比 (daily_updown 可能有重复, 按日取均值)
    ud = pd.read_sql("SELECT trade_date, up_down_ratio, up_count, down_count FROM daily_updown", conn)
    ud["trade_date"] = ud["trade_date"].astype(str)
    ud_agg = ud.groupby("trade_date").agg(
        up_down_ratio=("up_down_ratio", "mean"),
        up_count=("up_count", "mean"),
        down_count=("down_count", "mean"),
    )
    ud_map = ud_agg["up_down_ratio"].to_dict()

    conn.close()

    # ── 逐日计算 (与 backtest_v2 一致) + 施加 what-if 闸门 ──
    results = []
    t0 = time.time()
    for i, td in enumerate(all_dates):
        td_year = int(td[:4])
        ten_years_ago = str(td_year - 10) + td[4:]
        scores = {}
        raws = {}

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
                scores["pe"] = max(0, min(100, _pct_rank(hist_pe["pe_med"], cur_pe) * 100))
                raws["pe"] = cur_pe

        cur_buffett_row = mvcap_df[mvcap_df["trade_date"] <= td]
        if len(cur_buffett_row) > 0:
            cur_br = cur_buffett_row.iloc[-1]["buffett_ratio"]
            hist_buffett = mvcap_df[(mvcap_df["trade_date"] >= ten_years_ago) & (mvcap_df["buffett_ratio"].notna())]
            if len(hist_buffett) >= 60:
                scores["buffett"] = max(0, min(100, _pct_rank(hist_buffett["buffett_ratio"], cur_br) * 100))
                raws["buffett"] = cur_br

        cur_margin_row = margin_hist[margin_hist["trade_date"] <= td]
        if len(cur_margin_row) > 0:
            cur_mr = cur_margin_row.iloc[-1]["ratio"]
            hist_margin = margin_hist[(margin_hist["trade_date"] >= ten_years_ago) & (margin_hist["ratio"].notna())]
            if len(hist_margin) >= 60:
                pct = _pct_rank(hist_margin["ratio"], cur_mr)
                sc = (
                    pct * 100
                    if pct <= SATURATION_CUTOFF
                    else (SATURATION_CUTOFF + SATURATION_HEADROOM * (1 - math.exp(-(pct - SATURATION_CUTOFF) * 20)))
                    * 100
                )
                scores["margin_ratio"] = max(0, min(100, sc))
                raws["margin_ratio"] = cur_mr

        cur_seal = seal_df[seal_df["trade_date"] == td]
        if len(cur_seal) > 0:
            cur_sr = cur_seal.iloc[0]["seal_rate"]
            hist_seal = seal_df[(seal_df["trade_date"] >= ten_years_ago) & (seal_df["seal_rate"].notna())]
            if len(hist_seal) >= 60:
                scores["seal_rate"] = max(0, min(100, _pct_rank(hist_seal["seal_rate"], cur_sr) * 100))
                raws["seal_rate"] = cur_sr

        cur_tm2 = daily_amt[daily_amt["trade_date"] == td]
        if len(cur_tm2) > 0 and pd.notna(cur_tm2.iloc[0]["turnover_m2"]):
            cur_tm2_val = cur_tm2.iloc[0]["turnover_m2"]
            if len(m2_merged) >= 60:
                scores["turnover_m2"] = max(0, min(100, _pct_rank(m2_merged["ratio"], cur_tm2_val) * 100))
                raws["turnover_m2"] = cur_tm2_val

        cur_turnover = turnover_df[turnover_df["trade_date"] == td]
        if len(cur_turnover) > 0:
            cur_tr = cur_turnover.iloc[0]["turnover_rate"]
            hist_tr = turnover_df[(turnover_df["trade_date"] >= ten_years_ago) & (turnover_df["turnover_rate"].notna())]
            if len(hist_tr) >= 60:
                scores["turnover"] = max(0, min(100, _pct_rank(hist_tr["turnover_rate"], cur_tr) * 100))
                raws["turnover"] = cur_tr

        cur_nh = newhigh_df[newhigh_df["trade_date"] == td]
        if len(cur_nh) > 0:
            cur_nh_val = cur_nh.iloc[0]["new_high_ratio"]
            hist_nh = newhigh_df[(newhigh_df["trade_date"] >= ten_years_ago) & (newhigh_df["new_high_ratio"].notna())]
            if len(hist_nh) >= 60:
                scores["new_high"] = max(0, min(100, _pct_rank(hist_nh["new_high_ratio"], cur_nh_val) * 100))
                raws["new_high"] = cur_nh_val

        cur_ma = ma_align_df[ma_align_df["trade_date"] == td]
        if len(cur_ma) == 0:
            cur_ma = ma_align_df[ma_align_df["trade_date"] <= td]
        if len(cur_ma) > 0:
            cur_ma_val = cur_ma.iloc[-1]["ma_alignment_ratio"]
            hist_ma = ma_align_df[
                (ma_align_df["trade_date"] >= ten_years_ago) & (ma_align_df["ma_alignment_ratio"].notna())
            ]
            if len(hist_ma) >= 60:
                scores["ma_alignment"] = max(0, min(100, _pct_rank(hist_ma["ma_alignment_ratio"], cur_ma_val) * 100))
                raws["ma_alignment"] = cur_ma_val

        dim_scores = {}
        for dim_name in DIMS:
            ind_keys = [k for k, v in IND_DIMS.items() if v == dim_name]
            available = [(k, scores[k]) for k in ind_keys if k in scores and scores[k] is not None]
            if not available:
                dim_scores[dim_name] = None
                continue
            w = sum(WEIGHTS[k] for k, _ in available)
            dim_scores[dim_name] = sum(v * WEIGHTS[k] for k, v in available) / w if w > 0 else None

        valid_scores = [(k, v) for k, v in scores.items() if v is not None]
        composite = (
            sum(v * WEIGHTS[k] for k, v in valid_scores) / sum(WEIGHTS[k] for k, _ in valid_scores)
            if valid_scores
            else None
        )

        # ── 施加 what-if 闸门 ──
        gated = {}
        udr = ud_map.get(td)
        struct = dim_scores.get("structure")
        for name, gtype, thr, cap in CONFIGS[1:]:
            g = composite
            if g is not None:
                fire = False
                if gtype == "up_down" and udr is not None and udr < thr:
                    fire = True
                elif gtype == "structure" and struct is not None and struct < thr:
                    fire = True
                if fire:
                    g = min(g, cap)
            gated[name] = round(g, 1) if g is not None else None

        rec = {
            "trade_date": td,
            "composite_score": round(composite, 1) if composite is not None else None,
            "level": v2_level(composite),
            "structure_dim": round(struct, 1) if struct is not None else None,
            "up_down_ratio": round(udr, 4) if udr is not None else None,
            "close": idx_close.get(td),
        }
        for name in gated:
            rec[f"g_{name}"] = gated[name]
            rec[f"gl_{name}"] = v2_level(gated[name])
        results.append(rec)

        if (i + 1) % 500 == 0:
            el = time.time() - t0
            print(f"  [{i + 1}/{len(all_dates)}] {td} ({el:.0f}s)", flush=True)

    print(f"计算完成: {len(results)} 天 ({time.time() - t0:.1f}s)")
    df = pd.DataFrame(results)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["phase"] = df["trade_date"].apply(lambda d: bt.get_phase(d)[0])
    df["phase_desc"] = df["trade_date"].apply(lambda d: bt.get_phase(d)[1])
    df["is_bull"] = df["phase"].isin(BULL_PHASES)
    df["is_bear"] = df["phase"].isin(BEAR_PHASES)

    # ── 指标计算 ──
    def fwd_return(td, n):
        try:
            pos = list(idx_df.index).index(td)
            if pos + n < len(idx_df):
                return (idx_df.iloc[pos + n]["close"] / idx_df.iloc[pos]["close"] - 1) * 100
        except Exception:
            pass
        return None

    cols = ["composite_score"] + [f"g_{n}" for n in gated]

    def metrics(col):
        d = df.dropna(subset=[col, "close"])
        bull = d[d["is_bull"]]
        bear = d[d["is_bear"]]
        bull_hit = (bull[col] >= 55).sum()
        bear_hit = (bear[col] < 40).sum()
        corr = d[col].corr(d["close"])
        eh = d[d[col] >= 80]
        el = d[d[col] <= 20]
        red = d[d[col] >= 65]

        def fwr(s):
            r = s["trade_date"].apply(lambda x: fwd_return(x, 60))
            r = r.dropna()
            return (round(r.mean(), 1), round((r > 0).mean() * 100), len(r)) if len(r) else (None, None, 0)

        eh_r = fwr(eh)
        el_r = fwr(el)
        red_r = fwr(red)
        return {
            "bull_mean": round(bull[col].mean(), 1),
            "bear_mean": round(bear[col].mean(), 1),
            "disc": round(bull[col].mean() - bear[col].mean(), 1),
            "bull_hit_pct": round(bull_hit / len(bull) * 100, 1),
            "bear_hit_pct": round(bear_hit / len(bear) * 100, 1),
            "corr": round(corr, 3),
            "extreme_high_n": len(eh),
            "extreme_high_60d": eh_r,
            "extreme_low_n": len(el),
            "extreme_low_60d": el_r,
            "red_n": len(red),
            "red_60d": red_r,
        }

    print("\n" + "=" * 96)
    print("WHAT-IF 回测对比: 广度熔断")
    print("=" * 96)
    hdr = f"{'指标':22s}" + "".join(f"{c:>20s}" for c in cols)
    print(hdr)
    print("-" * 96)
    M = {c: metrics(c) for c in cols}
    rows = [
        ("牛市均值", "bull_mean"),
        ("熊市均值", "bear_mean"),
        ("牛熊区分度", "disc"),
        ("牛市识别率%(>=55)", "bull_hit_pct"),
        ("熊市识别率%(<40)", "bear_hit_pct"),
        ("相关系数(同期)", "corr"),
        ("极热天数(>=80)", "extreme_high_n"),
        ("极热后60日均值%", lambda m: m["extreme_high_60d"][0]),
        ("极热后60日胜率%", lambda m: m["extreme_high_60d"][1]),
        ("极冷天数(<=20)", "extreme_low_n"),
        ("极冷后60日均值%", lambda m: m["extreme_low_60d"][0]),
        ("极冷后60日胜率%", lambda m: m["extreme_low_60d"][1]),
        ("红色天数(>=65)", "red_n"),
        ("红后60日均值%", lambda m: m["red_60d"][0]),
        ("红后60日胜率%", lambda m: m["red_60d"][1]),
    ]
    for label, key in rows:
        line = f"{label:22s}"
        for c in cols:
            v = M[c][key] if isinstance(key, str) else key(M[c])
            line += f"{str(v):>20s}"
        print(line)

    # ── 熔断"摘红"分析 ──
    print("\n" + "=" * 96)
    print("熔断影响: 被摘红 (BASE红>=65 → what-if<65) 的天数及后续真实表现")
    print("=" * 96)
    for name in gated:
        col = f"g_{name}"
        flipped = df[(df["composite_score"] >= 65) & (df[col] < 65)]
        if len(flipped) == 0:
            print(f"  {name}: 无摘红天数")
            continue
        r = flipped["trade_date"].apply(lambda x: fwd_return(x, 60)).dropna()
        print(
            f"  {name}: 摘红 {len(flipped)} 天 | 其后60日 均值 {r.mean():.1f}% 胜率 {(r > 0).mean() * 100:.0f}% (n={len(r)})"
        )

    # ── 关键转折点对比 ──
    print("\n" + "=" * 96)
    print("关键牛熊转折点 (BASE vs 两个主 what-if)")
    print("=" * 96)
    key_dates = [
        ("2015-06-12", "5178大顶"),
        ("2015-08-26", "股灾底2850"),
        ("2016-01-28", "熔断底2638"),
        ("2018-01-29", "蓝筹牛顶3587"),
        ("2019-04-19", "春季顶3288"),
        ("2021-02-18", "核心资产顶3731"),
        ("2021-12-13", "结构牛顶"),
        ("2024-02-05", "底部2635"),
        ("2024-09-24", "924起点"),
        ("2024-10-08", "924顶3674"),
        ("2026-08-07", "最新(基准日)"),
        ("2026-08-11", "今日"),
    ]
    print(
        f"{'日期':12s} | {'事件':14s} | {'BASE':>6s} | {'UD|64':>6s} | {'UD|55':>6s} | {'结构':>6s} | {'涨跌比':>7s} | {'上证':>7s}"
    )
    print("-" * 88)
    for kd, desc in key_dates:
        row = df[df["trade_date"] == kd]
        if len(row) == 0:
            continue
        r = row.iloc[0]

        def fmt(v):
            return f"{v:.1f}" if pd.notna(v) else "—"

        udr = r["up_down_ratio"]
        print(
            f"  {kd} | {desc:14s} | {fmt(r['composite_score']):>6s} | {fmt(r['g_UD<0.5|cap64']):>6s} | "
            f"{fmt(r['g_UD<0.5|cap55']):>6s} | {fmt(r['structure_dim']):>6s} | "
            f"{(f'{udr:.2f}' if pd.notna(udr) else '—'):>7s} | {fmt(r['close']):>7s}"
        )

    # ── 保存 ──
    out_csv = "reports/whatif_detail.csv"
    df.to_csv(out_csv, index=False)
    summary = {c: M[c] for c in cols}
    # 摘红统计
    flip = {}
    for name in gated:
        col = f"g_{name}"
        f = df[(df["composite_score"] >= 65) & (df[col] < 65)]
        r = f["trade_date"].apply(lambda x: fwd_return(x, 60)).dropna()
        flip[name] = {
            "n_flipped": len(f),
            "fwd60_mean": round(r.mean(), 1) if len(r) else None,
            "fwd60_win": round((r > 0).mean() * 100) if len(r) else None,
        }
    summary["_flip"] = flip
    with open("reports/whatif_summary.json", "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n已保存: {out_csv}  reports/whatif_summary.json")


if __name__ == "__main__":
    main()
