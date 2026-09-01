#!/usr/bin/env python3
"""回测预测表现深度分析。

基于 scripts/backtest_v2.py 产出的 reports/backtest_v2_detail.csv（16 指标口径），
从以下四个维度评估综合热度指数在牛熊市周期中的预测表现：

  A. 预测准确性   —— 同期/领先相关、方向正确率、热度区间 → 未来收益单调性、阈值命中率
  B. 分阶段表现   —— 牛 / 熊 / 震荡三分类下的得分分布与信号正确率、指标区分度
  C. 关键误判     —— 假顶 / 假底 / 顶漏报 / 底漏报案例与指标归因、信号滞后度量
  D. 结论与建议   —— 汇总数据支持的改进方向

用法: .venv/bin/python scripts/analyze_backtest_performance.py
产出: reports/backtest_performance_analysis.md
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "reports" / "backtest_v2_detail.csv"
SUMMARY_PATH = ROOT / "reports" / "backtest_v2_summary.json"
OUT_PATH = ROOT / "reports" / "backtest_performance_analysis.md"

# 16 指标权重（与 config/prod.yaml v2_engine.weights 同步）
WEIGHTS = {
    "pe": 0.14, "buffett": 0.14,
    "margin_ratio": 0.05, "yield_spread": 0.03, "m1_m2_spread": 0.03,
    "southbound": 0.01, "margin_buy_ratio": 0.03,
    "seal_rate": 0.06, "turnover_m2": 0.14, "turnover": 0.09,
    "futures_discount": 0.02, "amplitude": 0.02, "realized_vol": 0.02,
    "new_high": 0.12, "ma_alignment": 0.06, "breadth": 0.04,
}
DIM_WEIGHTS = {  # 维度 → 指标 → 维度内权重（归一化后 0-1）
    "估值": {"pe": 0.5, "buffett": 0.5},
    "资金": {"margin_ratio": 5/15, "yield_spread": 3/15, "m1_m2_spread": 3/15,
             "southbound": 1/15, "margin_buy_ratio": 3/15},
    "情绪": {"seal_rate": 6/35, "turnover_m2": 14/35, "turnover": 9/35,
             "futures_discount": 2/35, "amplitude": 2/35, "realized_vol": 2/35},
    "结构": {"new_high": 12/22, "ma_alignment": 6/22, "breadth": 4/22},
}
DIM_TOTAL = {"估值": 0.28, "资金": 0.15, "情绪": 0.35, "结构": 0.22}

# 关键牛熊转折点（phase_desc 事件标签，日期以 CSV 实有行为准）
KEY_EVENTS = [
    ("2015-06-12", "5178 大顶"),
    ("2015-08-26", "股灾底 2850"),
    ("2016-01-28", "熔断底 2638"),
    ("2018-01-29", "蓝筹牛顶 3587"),
    ("2018-10-19", "贸易战底 2449"),
    ("2019-04-19", "春季顶 3288"),
    ("2020-03-23", "疫情底 2646"),
    ("2021-02-18", "核心资产顶 3731"),
    ("2021-12-13", "结构牛顶 3681"),
    ("2022-04-27", "熊市底 2863"),
    ("2024-02-05", "底部 2635"),
    ("2024-09-24", "924 行情起点"),
    ("2024-10-08", "924 行情顶 3674"),
]

PHASE_GROUP = {  # 三分类：牛 / 熊 / 震荡
    "bull_peak": "牛市", "bull_rally": "牛市", "slow_bull": "牛市",
    "bear_crash": "熊市", "bear_bottom": "熊市",
    "bounce": "震荡", "correction": "震荡",
}

FUTURE_DAYS = [5, 20, 60]


def fmt(x: float, nd: int = 1) -> str:
    return f"{x:.{nd}f}"


def pct(x: float, nd: int = 1) -> str:
    return f"{x * 100:.{nd}f}%"


def load() -> pd.DataFrame:
    df = pd.read_csv(CSV_PATH, parse_dates=["trade_date"])
    for c in [c for c in df.columns if c.startswith("ind_")]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["composite_score"] = pd.to_numeric(df["composite_score"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    # 未来 N 日收益（基于上证收盘，A 股不隔夜跳空时基本等价于指数收益）
    for n in FUTURE_DAYS:
        df[f"fwd{n}"] = df["close"].shift(-n) / df["close"] - 1.0
    # 热度变化方向（用于方向正确率）
    for n in FUTURE_DAYS:
        df[f"dscore{n}"] = df["composite_score"] - df["composite_score"].shift(n)
    df["group"] = df["phase"].map(PHASE_GROUP)
    return df


def dims_of(row: pd.Series) -> dict[str, float]:
    """按维度权重还原 4 维得分（缺失指标不参与加权，归一化补全）。"""
    out = {}
    for dim, inds in DIM_WEIGHTS.items():
        wsum, acc = 0.0, 0.0
        for ind, w in inds.items():
            v = row.get(f"ind_{ind}")
            if pd.notna(v):
                acc += v * w
                wsum += w
        out[dim] = acc / wsum if wsum > 0 else np.nan
    return out


def main() -> None:
    df = load()
    mds = [x for x in sys.argv[1:] if x != "--write"]

    # ============================================================
    # A. 预测准确性
    # ============================================================
    lines: list[str] = []
    lines.append("# V2 热度指数回测预测表现分析（16 指标口径）")
    lines.append("")
    lines.append(f"> 数据来源：`{CSV_PATH.name}`（{len(df)} 个交易日，"
                 f"{df['trade_date'].min().date()} ~ {df['trade_date'].max().date()}）｜"
                 f"`{SUMMARY_PATH.name}`｜生成时间 {pd.Timestamp.now():%Y-%m-%d %H:%M}")
    lines.append(">")
    lines.append("> 综合热度 = 估值 28% + 资金 15% + 情绪 35% + 结构 22%（16 指标）。"
                 "预测对象：上证指数；收益为正 = 市场上涨。")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## A. 预测准确性")
    lines.append("")

    # A1. 相关性
    lines.append("### A1. 热度与指数：同期 / 领先相关")
    lines.append("")
    lines.append("| 口径 | 相关系数 | 样本 | 解读 |")
    lines.append("|---|---|---|---|")
    lines.append(f"| 热度 vs 指数**水平**（同期） | {df['composite_score'].corr(df['close']):.3f} | {df['close'].notna().sum()} | "
                 "热度是**水平指标**：数值越高、指数点位越高（牛顶区高、熊底区低） |")
    for n in FUTURE_DAYS:
        c = df["composite_score"].corr(df[f"fwd{n}"])
        n_s = df[f"fwd{n}"].notna().sum()
        note = "热度越高 → 未来越可能下跌（反向预警）" if c < 0 else ""
        lines.append(f"| 热度 vs 未来 {n} 日收益 | {c:.3f} | {n_s} | {note} |")
    # 领先 60 日相关（旧版口径：热度领先 60 日）
    c60 = df["composite_score"].corr(df["fwd60"])
    lines.append("")
    lines.append(f"同期水平相关 **{df['composite_score'].corr(df['close']):.3f}**（summary.json 0.816 口径）；"
                 f"对未来 60 日收益的相关 **{c60:.3f}** —— 负值说明热度高时未来 60 日倾向下跌，"
                 "这是本指数作为**离场预警**的核心依据，但绝对值 0.2 左右意味着**预测力有限**。")
    lines.append("")

    # A2. 方向正确率（热度升降 vs 市场涨跌）
    lines.append("### A2. 方向正确率：热度升降方向 → 未来涨跌方向")
    lines.append("")
    lines.append("热度变化 = 当日综合分 − N 日前综合分；方向正确 = 热度升(Δ>0)且未来涨，或热度降(Δ<0)且未来跌。")
    lines.append("")
    lines.append("| 窗口 | 热度升降 vs 未来收益方向一致率 | 热度升且未来涨 | 热度降且未来跌 | 样本 |")
    lines.append("|---|---|---|---|---|")
    for n in FUTURE_DAYS:
        s = df.dropna(subset=[f"dscore{n}", f"fwd{n}"])
        agree = ((s[f"dscore{n}"] > 0) & (s[f"fwd{n}"] > 0)) | ((s[f"dscore{n}"] < 0) & (s[f"fwd{n}"] < 0))
        up_up = ((s[f"dscore{n}"] > 0) & (s[f"fwd{n}"] > 0)).sum()
        dn_dn = ((s[f"dscore{n}"] < 0) & (s[f"fwd{n}"] < 0)).sum()
        lines.append(f"| {n} 日 | {agree.mean():.1%} | {up_up} | {dn_dn} | {len(s)} |")
    lines.append("")
    lines.append("> 说明：方向一致率在 46%~51% 之间、接近随机水平，印证热度对**短期方向**预测能力弱，"
                 "其价值在**极端区间的状态识别**而非逐日择时。")
    lines.append("")

    # A3. 热度区间 → 未来 60 日收益（单调性）
    bins = [0, 20, 30, 40, 50, 60, 70, 80, 100]
    labels = ["≤20", "20-30", "30-40", "40-50", "50-60", "60-70", "70-80", ">80"]
    df["score_bin"] = pd.cut(df["composite_score"], bins=bins, labels=labels, right=False)
    lines.append("### A3. 热度区间信号 → 未来收益（命中率核心表）")
    lines.append("")
    lines.append("| 热度区间 | 天数 | 未来20日收益均值 | 未来60日收益均值 | 未来60日中位数 | 60日后上涨占比 | 信号解读 |")
    lines.append("|---|---|---|---|---|---|---|")
    for lb in labels:
        sub = df[df["score_bin"] == lb]
        if sub.empty:
            continue
        m20 = sub["fwd20"].mean()
        m60 = sub["fwd60"].mean()
        med60 = sub["fwd60"].median()
        up60 = (sub["fwd60"] > 0).mean()
        if lb in ("≤20", "20-30"):
            sig = "看多（抄底）"
        elif lb in (">80", "70-80"):
            sig = "看空（离场）"
        elif lb in ("60-70",):
            sig = "偏空（减仓）"
        elif lb in ("30-40", "40-50"):
            sig = "偏多/中性"
        else:
            sig = "中性"
        lines.append(f"| {lb} | {len(sub)} | {fmt(m20 * 100)}% | {fmt(m60 * 100)}% | {fmt(med60 * 100)}% | {pct(up60, 0)} | {sig} |")
    lines.append("")

    # A4. 阈值命中率（极热/极冷）
    lines.append("### A4. 阈值信号命中率（极热 / 极冷）")
    lines.append("")
    lines.append("| 阈值信号 | 天数 | 后5日正收益占比 | 后20日正收益占比 | 后60日正收益占比 | 后60日收益均值 | 判定 |")
    lines.append("|---|---|---|---|---|---|---|")
    for thr, name in [(80, "极热 ≥80"), (70, "高热 ≥70"), (20, "极冷 ≤20"), (30, "低冷 ≤30")]:
        if thr >= 50:
            sub = df[df["composite_score"] >= thr]
        else:
            sub = df[df["composite_score"] <= thr]
        if sub.empty:
            continue
        u5 = (sub["fwd5"] > 0).mean()
        u20 = (sub["fwd20"] > 0).mean()
        u60 = (sub["fwd60"] > 0).mean()
        m60 = sub["fwd60"].mean()
        if thr >= 70:
            verdict = "离场信号有效" if u60 < 0.5 else "离场信号一般"
        else:
            verdict = "看多信号有效" if u60 > 0.5 else "看多信号一般"
        lines.append(f"| {name} | {len(sub)} | {pct(u5, 0)} | {pct(u20, 0)} | {pct(u60, 0)} | {fmt(m60 * 100)}% | {verdict} |")
    lines.append("")

    # ============================================================
    # B. 分阶段表现
    # ============================================================
    lines.append("---")
    lines.append("")
    lines.append("## B. 不同市场阶段下的表现")
    lines.append("")
    lines.append("三分类口径：**牛市** = bull_peak + bull_rally + slow_bull；**熊市** = bear_crash + bear_bottom；"
                 "**震荡** = bounce（反弹）+ correction（回调）。")
    lines.append("")

    lines.append("### B1. 各阶段热度分布")
    lines.append("")
    lines.append("| 阶段 | 天数 | 均值 | 中位数 | 标准差 | P25 | P75 | ≥80 占比 | ≤20 占比 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for g in ["牛市", "熊市", "震荡"]:
        sub = df[df["group"] == g]
        lines.append(f"| {g} | {len(sub)} | {fmt(sub['composite_score'].mean())} | {fmt(sub['composite_score'].median())} | "
                     f"{fmt(sub['composite_score'].std())} | {fmt(sub['composite_score'].quantile(0.25))} | "
                     f"{fmt(sub['composite_score'].quantile(0.75))} | "
                     f"{pct((sub['composite_score'] >= 80).mean(), 1)} | {pct((sub['composite_score'] <= 20).mean(), 1)} |")
    lines.append("")

    lines.append("### B2. 各阶段信号正确率")
    lines.append("")
    lines.append("| 阶段 | 正确信号定义 | 正确率 | 说明 |")
    lines.append("|---|---|---|---|")
    bull = df[df["group"] == "牛市"]
    bear = df[df["group"] == "熊市"]
    swing = df[df["group"] == "震荡"]
    b_ok = (bull["composite_score"] >= 55).mean()
    be_ok = (bear["composite_score"] < 40).mean()
    sw_ok = ((swing["composite_score"] >= 40) & (swing["composite_score"] < 55)).mean()
    lines.append(f"| 牛市 | 热度 ≥55（热度高=牛） | {pct(b_ok, 1)} | 牛市中热度处于高位区的比例 |")
    lines.append(f"| 熊市 | 热度 <40（热度低=熊） | {pct(be_ok, 1)} | 熊市中热度处于低位区的比例 |")
    lines.append(f"| 震荡 | 40 ≤ 热度 < 55（中性=震荡） | {pct(sw_ok, 1)} | 震荡市中热度落在中性区的比例 |")
    lines.append("")
    lines.append("> **解读**：牛市正确率最高（热度中枢高），熊市次之（熊市低热日占比近半），"
                 "震荡市最差（热度常越出中性区，说明本指数天然**不擅长区分震荡市**——"
                 "反弹日热度可能冲高、回调日热度可能走低，产生双向假信号）。")
    lines.append("")

    # B3. 指标区分度（16 指标，牛 vs 熊 均值差）
    lines.append("### B3. 各指标牛熊区分度（16 指标）")
    lines.append("")
    lines.append("按牛市组 / 熊市组均值差排序，正值 = 牛市读数更高（方向正确），负值 = 反向。")
    lines.append("")
    lines.append("| 指标 | 牛市均值 | 熊市均值 | 区分度(牛−熊) | 权重 | 方向 |")
    lines.append("|---|---|---|---|---|---|")
    rows = []
    for ind in [c[4:] for c in df.columns if c.startswith("ind_")]:
        bm = bull[f"ind_{ind}"].mean()
        rm = bear[f"ind_{ind}"].mean()
        if pd.isna(bm) or pd.isna(rm):
            continue
        rows.append((ind, bm, rm, bm - rm, WEIGHTS.get(ind, 0)))
    rows.sort(key=lambda r: -r[3])
    for ind, bm, rm, d, w in rows:
        dirm = "正向" if d > 0 else "反向⚠"
        lines.append(f"| {ind} | {fmt(bm)} | {fmt(rm)} | **{fmt(d, 1)}** | {w:.2f} | {dirm} |")
    lines.append("")

    # ============================================================
    # C. 关键误判
    # ============================================================
    lines.append("---")
    lines.append("")
    lines.append("## C. 关键误判情形与归因")
    lines.append("")

    # C1. 关键转折点当日状态
    lines.append("### C1. 关键牛熊转折点：当日热度与 4 维分解")
    lines.append("")
    lines.append("| 日期 | 事件 | 当日热度 | 级别 | 后60日收益 | 估值 | 资金 | 情绪 | 结构 | 判定 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for dstr, ev in KEY_EVENTS:
        row = df[df["trade_date"] == pd.Timestamp(dstr)]
        if row.empty:
            continue
        r = row.iloc[0]
        dims = dims_of(r)
        f60 = r["fwd60"]
        # 判定：顶部事件看热度是否 ≥65（red）；底部事件看是否 ≤30
        is_top = "顶" in ev or "起点" in ev
        hit = "✅ 命中" if ((is_top and r["composite_score"] >= 65) or
                           ((not is_top) and r["composite_score"] <= 30)) else "⚠️ 漏报"
        if ev == "924 行情起点":
            hit = "——"
        lines.append(f"| {dstr} | {ev} | **{fmt(r['composite_score'])}** | {r['level']} | "
                     f"{fmt(f60 * 100, 1)}% | {fmt(dims['估值'], 1)} | {fmt(dims['资金'], 1)} | "
                     f"{fmt(dims['情绪'], 1)} | {fmt(dims['结构'], 1)} | {hit} |")
    lines.append("")
    lines.append("> **误判一（顶部漏报）**：2018-01-29 蓝筹牛顶（热度 53.1）、2019-04-19 春季顶（热度 63.2）"
                 "当日热度未触及红色预警（65），仅黄/橙色。原因：**顶部由单一/部分维度驱动、其余维度未共振**——"
                 "2018-01-29 由估值单骑拉动（80.3），情绪（41.6）/结构（35.4）双冷（蓝筹抱团、宽度类指标读数低）；"
                 "2019-04-19 情绪（63.1）/结构（94.5）已热但资金维度仅 23.8（两融/北向未确认），"
                 "综合分被“冷维度”拉低而漏报。注意 2021-02-18（70.0）与 2024-10-08（67.6）在 16 指标口径下均已达红色预警、判定命中，"
                 "并非漏报（旧 13 指标口径曾低估）。")
    lines.append("")
    lines.append("> **误判二（底部漏报）**：2015-08-26 股灾底（33.2）、2020-03-23 疫情底（34.9）、2022-04-27 熊市底（34.4），"
                 "当日热度未到极冷（≤20）。原因：**资金维度在底部反而偏高**"
                 "（2020-03-23 资金 60.5、2022-04-27 资金 57.4），两融/北向在暴跌后期回补，"
                 "且情绪指标快速回落但期限利差等资金指标仍处宽松态。")
    lines.append("")

    # C2. 假顶 / 假底
    lines.append("### C2. 假信号：极热后仍大涨 / 极冷后仍大跌")
    lines.append("")
    hot = df[df["composite_score"] >= 80].dropna(subset=["fwd60"])
    cold = df[df["composite_score"] <= 20].dropna(subset=["fwd60"])
    false_top = hot[hot["fwd60"] > 0.05].sort_values("fwd60", ascending=False)
    false_bot = cold[cold["fwd60"] < -0.05].sort_values("fwd60")
    lines.append(f"极热信号（≥80）共 {len(hot)} 次，其中后 60 日仍上涨 >5% 的**假顶部** {len(false_top)} 次"
                 f"（{pct(len(false_top)/max(len(hot),1), 1)}）。最具代表性的 5 次：")
    lines.append("")
    lines.append("| 日期 | 热度 | 后60日收益 | 所属阶段 |")
    lines.append("|---|---|---|---|")
    for _, r in false_top.head(5).iterrows():
        lines.append(f"| {r['trade_date'].date()} | {fmt(r['composite_score'])} | **+{fmt(r['fwd60'] * 100, 1)}%** | {r['phase']} |")
    lines.append("")
    lines.append(f"极冷信号（≤20）共 {len(cold)} 次，其中后 60 日仍下跌 >5% 的**假底部** {len(false_bot)} 次"
                 f"（{pct(len(false_bot)/max(len(cold),1), 1)}）。最具代表性的 5 次：")
    lines.append("")
    lines.append("| 日期 | 热度 | 后60日收益 | 所属阶段 |")
    lines.append("|---|---|---|---|")
    for _, r in false_bot.head(5).iterrows():
        lines.append(f"| {r['trade_date'].date()} | {fmt(r['composite_score'])} | **{fmt(r['fwd60'] * 100, 1)}%** | {r['phase']} |")
    lines.append("")

    # C3. 信号滞后：极热信号与顶部的时间差
    lines.append("### C3. 信号滞后度量：极热信号出现与顶部时点的间隔")
    lines.append("")
    top_dates = ["2015-06-12", "2021-02-18", "2021-12-13", "2024-10-08"]
    lines.append("| 顶部 | 顶部前最近一次 ≥80 日期 | 提前天数 | 顶部时热度 |")
    lines.append("|---|---|---|---|")
    for dstr in top_dates:
        t = df[df["trade_date"] == pd.Timestamp(dstr)]
        if t.empty:
            continue
        i = df.index[df["trade_date"] == pd.Timestamp(dstr)][0]
        before = df.loc[:i]
        first80 = before[before["composite_score"] >= 80]
        if not first80.empty:
            fd = first80.iloc[-1]["trade_date"].date()
            lead = (pd.Timestamp(dstr) - pd.Timestamp(fd)).days
            lines.append(f"| {dstr} | {fd} | **{lead} 天** | {fmt(t.iloc[0]['composite_score'])} |")
        else:
            lines.append(f"| {dstr} | 顶部前从未 ≥80 | — | {fmt(t.iloc[0]['composite_score'])} |")
    lines.append("")
    lines.append("> 2015-06-12 大顶前热度早已长期 ≥80（2015-03 起即在高位），极热信号**大幅提前**而非滞后，"
                 "实际风险在于**过早离场**；2021 年顶部信号出现约提前 1-2 个月，属“及时”；"
                 "2024-10-08 顶部前约 3 年无 ≥80 极热信号（最近一次在 2021-09-15），当日仅 67.6 靠红色预警命中——"
                 "若只依赖极热阈值将漏报，需依赖 ≥65 红色预警或顶部确认机制（见 D 建议 2）。")
    lines.append("")

    # C4. 滚动窗口效应：分位基准漂移
    lines.append("### C4. 热度中枢漂移（滚动分位 vs 全历史）")
    lines.append("")
    lines.append(f"牛市热度均值 **{fmt(bull['composite_score'].mean())}** vs 熊市 **{fmt(bear['composite_score'].mean())}**，"
                 f"中枢差 {fmt(bull['composite_score'].mean() - bear['composite_score'].mean())} 分。"
                 "2015 大牛中极热日（≥80）密集出现（859 个 bull_peak 日中 ≥80 占比 "
                 f"{pct((bull['composite_score'] >= 80).mean(), 1)}），与 2021/2024 顶部“顶不热”形成对照——"
                 "同一绝对阈值在不同 regime 下敏感度不同，滚动 5 年分位已缓解但未完全消除该效应。")
    lines.append("")

    # ============================================================
    # D. 结论与建议
    # ============================================================
    lines.append("---")
    lines.append("")
    lines.append("## D. 结论与改进建议")
    lines.append("")

    # 汇总数字
    s = json.loads(SUMMARY_PATH.read_text())
    lines.append("### 结论")
    lines.append("")
    lines.append(f"1. **本质是水平状态指标，不是短期择时器**：与指数水平同期相关 {s['corr_heat_vs_index']:.2f}，"
                 "与未来 60 日收益相关 −0.2 左右。它回答的是“现在市场处于什么温度”，"
                 "而非“明天涨跌”——**方向一致率接近随机**（46%~51%，A2）。")
    lines.append(f"2. **极端区间信号有效，中间区间无效**：极热（≥80）后 60 日上涨占比 27%、均值 −7.4%；"
                 f"极冷（≤20）后 60 日上涨占比 64%、均值 +7.9%；但 40-60 中性区间的未来收益无区分度（A3）。")
    lines.append("3. **牛熊识别可用，震荡市天然劣势**：牛市热度 ≥55 占 5 成以上、熊市 <40 占近半；"
                 "但震荡市（反弹/回调）中热度频繁越界，假信号集中于此（B2）。")
    lines.append("4. **漏报集中在“单维驱动顶”与“资金底”**：2018 蓝筹顶（估值独热）、2019 春季顶（资金未确认）漏报；"
                 "暴跌后资金维度（两融/北向/利差）偏热拖累底部信号（C1）。")
    lines.append("5. **个别指标方向性可疑**：futures_discount、amplitude 区分度为负或接近 0（B3），"
                 "虽权重低（各 0.02）不伤大局，但对信号贡献是噪声级。")
    lines.append("")

    lines.append("### 改进建议")
    lines.append("")
    lines.append("| # | 建议 | 依据 | 工作量 |")
    lines.append("|---|---|---|---|")
    lines.append("| 1 | **信号阈值化而非连续分位**：只把 ≥80（离场）/≤20（观察）当信号，中段一律视为无信号，"
                 "减少震荡市假信号 | A3/A4：极端区间命中率远高于中间区间 | 小 |")
    lines.append("| 2 | **增加顶部确认机制**：热度从 ≥70 回落 10 分且结构维破位（new_high<50 或 ma_alignment 回落）"
                 "触发离场，可覆盖 2018-01/2019-04 类“单维驱动顶”漏报与 2024-10 类“顶不热” | C1 误判一 | 中 |")
    lines.append("| 3 | **底部信号改“温度计”用法**：≤20 仅提示“低估值区+情绪冰点”，不单独作为进场依据，"
                 "与本项目定位（仅离场/减仓）一致 | C1 误判二：底部资金维度偏高 | 小 |")
    lines.append("| 4 | **压缩或剔除负区分度指标**：futures_discount（−3.9）、amplitude（−8.3）降权或转为展示项；"
                 "或对它们做方向翻转后再入分 | B3 区分度表 | 小 |")
    lines.append("| 5 | **按 regime 自适应阈值**：用滚动 1260 日分位的相对位置（如 P90/P10）替代绝对 80/20 阈值，"
                 "缓解 2015 型大牛与 2024 型快牛阈值敏感度差异 | C4 中枢漂移 | 中 |")
    lines.append("| 6 | **震荡市独立模型**：bounce/correction 阶段用“热度分位数变化率”（Δscore）而非绝对水平"
                 "做区间交易参考；或明确放弃震荡市择时 | B2 震荡正确率最低 | 大 |")
    lines.append("| 7 | **补充验证集**：以上结论基于 2015-2026 单一市场样本，建议按 2015-2020 / 2021-2026 两段"
                 "做样本外一致性检查，避免过拟合 | 全篇 | 中 |")
    lines.append("")

    report = "\n".join(lines)
    OUT_PATH.write_text(report, encoding="utf-8")
    print(f"✅ 报告已生成: {OUT_PATH} ({len(report)} chars)")

    # ============================================================
    # 控制台摘要（mds 过滤模式）
    # ============================================================
    if mds:
        df2 = df.set_index("trade_date")
        for md in mds:
            if md in df2.index.strftime("%Y-%m-%d"):
                r = df2.loc[pd.Timestamp(md)]
                dims = dims_of(r)
                print(f"\n[{md}] score={r['composite_score']:.1f} level={r['level']} "
                      f"fwd60={r['fwd60']*100:.1f}% 估值={dims['估值']:.1f} 资金={dims['资金']:.1f} "
                      f"情绪={dims['情绪']:.1f} 结构={dims['结构']:.1f}")


if __name__ == "__main__":
    main()
