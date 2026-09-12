#!/usr/bin/env python3
"""What-if 回测: 广度熔断 (breadth circuit breaker)

问题: 2026-08-11 涨1615/跌3777 (up_down_ratio=0.43), 结构分仅10.2, 但综合分71.3=红色预警。
用户质疑: "涨少跌多却给红牌, 不像是牛市"。

假设: 当市场广度崩溃时, 综合热度不应触发"红色预警"(减仓) 信号。

口径说明 (P0-5/P1-11 修复, 2026-09-12)
──────────────────────────────────────
旧实现复制了 backtest_v2 的整条逐日管线, 存在三重问题:
  1. 7 处 hist_* 窗口漏 `<= td` 上界 → 前视泄漏;
  2. 各表 read_sql 无 ORDER BY → "<= td 取 .iloc[-1]" 跑在 SQLite 物理乱序上;
  3. v3.0 计分键由 11→9 后, 旧键 (margin_ratio/seal_rate/turnover_m2) 已不在 WEIGHTS 中
     → 直接 KeyError, 脚本在 v3.0 下根本无法运行。
现改为直接消费权威产物 `reports/backtest_v2_detail.csv` (由 backtest_v2.py 生成, 已与
生产引擎全历史同构 Δ=0.00):
  - BASE 综合分 = CSV `composite_score` (含背离惩罚, 与引擎严格一致)
  - 结构维分    = ind_new_high / ind_ma_alignment 按 v3.0 权重加权 (与旧口径同义)
  - 涨跌比      = daily_updown (仅作闸门输入, 不参与计分)
这样 what-if 的 BASE 与引擎严格一致, 并彻底消除重复实现的漂移风险。

施加"广度熔断":
  GATE: 若 breadth 弱 (up_down_ratio < 0.5, 或 结构维分 < 30), 则
        composite = min(composite, CAP)   # CAP=64 仅消除红区; CAP=55 连橙区也消除

对比指标 (与旧版一致口径):
  - 牛熊均值差 (区分度), 牛/熊识别准确率
  - 热度 vs 上证 同期相关系数
  - 极热(>=80)/极冷(<=20) 信号后 60 日收益与胜率
  - 红色(>=65)减仓信号后 60 日收益与胜率
  - 被熔断"摘红"的天数, 及其后 60 日真实表现 (验证熔断是否合理)
  - 关键牛熊转折点对比

输出: reports/whatif_detail.csv, reports/whatif_summary.json (不覆盖生产回测产物)
"""

import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import backtest_v2 as bt  # 仅导入常量与函数, 不执行 run_backtest()

DB_PATH = bt.DB_PATH
WEIGHTS = bt.WEIGHTS
IND_DIMS = bt.IND_DIMS
v2_level = bt.v2_level
BULL_PHASES = bt.BULL_PHASES
BEAR_PHASES = bt.BEAR_PHASES

CSV_IN = "reports/backtest_v2_detail.csv"
OUT_CSV = "reports/whatif_detail.csv"
OUT_SUMMARY = "reports/whatif_summary.json"

# What-if 配置: (名称, 闸门类型, 阈值, 封顶)
# gate_type: 'up_down' 用 up_down_ratio; 'structure' 用结构维分
CONFIGS = [
    ("BASE", None, None, None),
    ("UD<0.5|cap64", "up_down", 0.5, 64),  # 仅消除红区
    ("UD<0.5|cap55", "up_down", 0.5, 55),  # 红区+橙区都消除
    ("STR<30|cap64", "structure", 30, 64),  # 结构维分替代口径
]

# 结构维的计分键 (v3.0: new_high + ma_alignment)
STRUCT_KEYS = [k for k, v in IND_DIMS.items() if v == "structure"]


def _load_updown() -> dict:
    """涨跌比 (daily_updown 可能有重复, 按日取均值) — 仅作闸门输入"""
    conn = sqlite3.connect(DB_PATH)
    try:
        ud = pd.read_sql("SELECT trade_date, up_down_ratio FROM daily_updown", conn)
    finally:
        conn.close()
    ud["trade_date"] = ud["trade_date"].astype(str)
    return ud.groupby("trade_date")["up_down_ratio"].mean().to_dict()


def main():
    df = pd.read_csv(CSV_IN)
    df["trade_date"] = df["trade_date"].astype(str)
    df.sort_values("trade_date", inplace=True, kind="mergesort")
    df.reset_index(drop=True, inplace=True)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")

    n_days = len(df)
    print(f"载入权威回测产物: {n_days} 天 {df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}")

    # ── 结构维分: ind_new_high / ind_ma_alignment 按权重加权 ──
    def _struct_row(r):
        avail = [(k, r[f"ind_{k}"]) for k in STRUCT_KEYS if pd.notna(r.get(f"ind_{k}"))]
        if not avail:
            return None
        w = sum(WEIGHTS[k] for k, _ in avail)
        return sum(v * WEIGHTS[k] for k, v in avail) / w if w > 0 else None

    df["structure_dim"] = df.apply(_struct_row, axis=1)

    ud_map = _load_updown()
    df["up_down_ratio"] = df["trade_date"].map(ud_map)

    # ── 施加 what-if 闸门 (BASE 即 CSV 的 composite_score) ──
    gated = {}
    for name, gtype, thr, cap in CONFIGS[1:]:
        if gtype == "up_down":
            fire = df["up_down_ratio"].notna() & (df["up_down_ratio"] < thr)
        elif gtype == "structure":
            fire = df["structure_dim"].notna() & (df["structure_dim"] < thr)
        else:  # pragma: no cover - 配置表写错时显式报错, 不静默放过
            raise ValueError(f"unknown gate type: {gtype}")
        gated[name] = df["composite_score"].where(~fire, np.minimum(df["composite_score"], cap)).round(1)
        df[f"g_{name}"] = gated[name]
        df[f"gl_{name}"] = df[f"g_{name}"].apply(lambda v: v2_level(v) if pd.notna(v) else None)

    df["is_bull"] = df["phase"].isin(BULL_PHASES)
    df["is_bear"] = df["phase"].isin(BEAR_PHASES)

    # ── 未来 60 日收益 (按已排序的 close 序列取位置偏移) ──
    px = df["close"].to_numpy(dtype=float)

    def _fwd(pos, n=60):
        j = pos + n
        if j < len(px) and np.isfinite(px[pos]) and np.isfinite(px[j]) and px[pos] > 0:
            return (px[j] / px[pos] - 1) * 100
        return np.nan

    df["ret60"] = [_fwd(i, 60) for i in range(n_days)]

    # ── 指标计算 ──
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
            r = s["ret60"].dropna()
            return (round(r.mean(), 1), round((r > 0).mean() * 100), len(r)) if len(r) else (None, None, 0)

        return {
            "bull_mean": round(bull[col].mean(), 1),
            "bear_mean": round(bear[col].mean(), 1),
            "disc": round(bull[col].mean() - bear[col].mean(), 1),
            "bull_hit_pct": round(bull_hit / len(bull) * 100, 1),
            "bear_hit_pct": round(bear_hit / len(bear) * 100, 1),
            "corr": round(corr, 3),
            "extreme_high_n": len(eh),
            "extreme_high_60d": fwr(eh),
            "extreme_low_n": len(el),
            "extreme_low_60d": fwr(el),
            "red_n": len(red),
            "red_60d": fwr(red),
        }

    print("\n" + "=" * 96)
    print("WHAT-IF 回测对比: 广度熔断  (BASE = backtest_v2 权威 composite_score)")
    print("=" * 96)
    print(f"{'指标':22s}" + "".join(f"{c:>20s}" for c in cols))
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
        flipped = df[(df["composite_score"] >= 65) & (df[f"g_{name}"] < 65)]
        if len(flipped) == 0:
            print(f"  {name}: 无摘红天数")
            continue
        r = flipped["ret60"].dropna()
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
    df.to_csv(OUT_CSV, index=False)
    summary = {c: M[c] for c in cols}
    flip = {}
    for name in gated:
        f = df[(df["composite_score"] >= 65) & (df[f"g_{name}"] < 65)]
        r = f["ret60"].dropna()
        flip[name] = {
            "n_flipped": len(f),
            "fwd60_mean": round(r.mean(), 1) if len(r) else None,
            "fwd60_win": round((r > 0).mean() * 100) if len(r) else None,
        }
    summary["_flip"] = flip
    summary["_meta"] = {
        "source": CSV_IN,
        "n_days": n_days,
        "date_range": [df["trade_date"].iloc[0], df["trade_date"].iloc[-1]],
        "engine_mode": bt.ENGINE_MODE,
        "note": "BASE = backtest_v2 权威 composite_score; 结构维分由 ind_new_high/ind_ma_alignment 加权",
    }
    with open(OUT_SUMMARY, "w") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n已保存: {OUT_CSV}  {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
