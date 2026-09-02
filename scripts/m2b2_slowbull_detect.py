#!/usr/bin/env python3
"""m2b2_slowbull_detect.py — M2b-2: 慢牛状态检测（#101, 承接 R1b 泛化）

问题: M2b-1 的 R1b(seg2 bull 去情绪/结构键) 用日历 seg 硬编码, 不可落地。
本脚本检验能否用纯市场状态变量(close 序列, 无前视)泛化"慢牛"——
核心机制假设: "低波动/长时间持续的上行(慢牛)里, 情绪/结构键(turnover/
ma_alignment/new_high)失效甚至转正(动量延续), 高波动快牛里仍反指有效"。
若 seg0/1 内部的低波动子段同样转正 → 状态变量是真正的 regime 变量(可泛化);
否则 seg2 的失效是结构/风格漂移, 价格序列无法刻画(不可泛化, R1b 只能当
防御性开关)。

步骤:
  A. 状态变量计算: vol60(年化) / ret60 / slope250(SMA250 60日斜率) / bull_days
  B. 机制检验: 各 seg bull 按 vol60 中位分高低两半 -> 情绪/结构键 IC60
     (低波动半是否转正/近零, 高波动半是否维持负)
  C. 若 B 支持: slow_bull = bull & 状态变量条件 -> 覆盖/误伤矩阵(vs seg2 bull)
  D. 泛化规则 R_new A/B: R0 vs R1b vs R_new — 全样本 IC60 + seg 分段 + seg2 后半
用法: python scripts/m2b2_slowbull_detect.py
输出: stdout + reports/m2b2_slowbull_detect.json
"""

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.indicators.heat_index_v2 import INDICATOR_WEIGHTS

OUT = "reports/m2b2_slowbull_detect.json"
CSV = "reports/backtest_v2_detail.csv"
KEYS = list(INDICATOR_WEIGHTS.keys())
W = dict(INDICATOR_WEIGHTS)
SENTI_STRUCT = ("turnover", "ma_alignment", "new_high")
SEG_NAMES = ["seg0<2019", "seg1 19-22", "seg2 23-26"]


def seg_of(d):
    return 0 if d < "2019-01-01" else (1 if d < "2023-01-01" else 2)


def spearman(a, b, min_n=40):
    m = pd.notna(a) & pd.notna(b)
    if m.sum() < min_n:
        return np.nan
    ra = pd.Series(a[m]).rank()
    rb = pd.Series(b[m]).rank()
    if ra.std() == 0 or rb.std() == 0:
        return np.nan
    return float(np.corrcoef(ra, rb)[0, 1])


def load():
    csv = pd.read_csv(CSV)
    csv["trade_date"] = csv["trade_date"].astype(str)
    px = csv["close"].astype(float)
    r1 = px.pct_change()
    csv["vol60"] = r1.rolling(60).std() * np.sqrt(250)  # 年化波动率
    csv["sma250"] = px.rolling(250).mean()
    csv["bull"] = px > csv["sma250"]  # NaN 段=无趋势态
    csv["ret60"] = px.shift(-60) / px - 1  # 未来 60 日收益（反指评估用）
    csv["slope250"] = csv["sma250"].pct_change(60)
    streak, cur = [], 0
    for b in csv["bull"].fillna(False):
        cur = cur + 1 if b else 0
        streak.append(cur)
    csv["bull_days"] = streak
    csv["seg"] = csv["trade_date"].apply(seg_of)
    for k in KEYS:
        csv[f"ind_{k}"] = pd.to_numeric(csv[f"ind_{k}"], errors="coerce")
    return csv


def ic_of(csv, keys, mask):
    out = {}
    for k in keys:
        ic = spearman(csv.loc[mask, f"ind_{k}"], csv.loc[mask, "ret60"])
        out[k] = round(ic, 3) if ic == ic else None
    return out


def composite_regime(csv, mask, active_keys):
    """在 mask 行用 active_keys 行重归一算 composite, 其余行回退单层 composite_score"""
    comp = csv["composite_score"].copy()
    num = pd.Series(0.0, index=csv.index)
    den = pd.Series(0.0, index=csv.index)
    for k in active_keys:
        v = csv[f"ind_{k}"]
        num = num + v.fillna(0) * W[k]
        den = den + v.notna() * W[k]
    with np.errstate(divide="ignore", invalid="ignore"):
        cand = pd.Series(np.where(den > 0, num / np.where(den > 0, den, np.nan), np.nan), index=csv.index)
    return comp.where(~mask, cand)


def main():
    csv = load()
    bull = csv["bull"].fillna(False)
    res = {}

    print("=" * 88)
    print("B. 机制检验: seg×vol 高低两半 — 情绪/结构键 IC60（低波动半是否失效/转正）")
    print("=" * 88)
    b_out = {}
    for s, snm in enumerate(SEG_NAMES):
        m = (csv["seg"] == s) & bull
        if m.sum() < 120:
            print(f"  {snm}: bull n={int(m.sum()):4d} 样本不足")
            continue
        med = csv.loc[m, "vol60"].median()
        for half, hm in [("低vol半", m & (csv["vol60"] <= med)), ("高vol半", m & (csv["vol60"] > med))]:
            if hm.sum() < 80:
                continue
            ic = ic_of(csv, ["turnover", "ma_alignment", "new_high"], hm)
            n = int(hm.sum())
            print(f"  {snm} {half}: n={n:4d}  " + "  ".join(f"{k}:{v:+.3f}" for k, v in ic.items()))
            b_out[f"{snm}_{half}"] = {"n": n, "ic": ic}
        # 全段对照
        ic = ic_of(csv, ["turnover", "ma_alignment", "new_high"], m)
        print(
            f"  {snm} 全bull: n={int(m.sum()):4d}  "
            + "  ".join(f"{k}:{v:+.3f}" for k, v in ic.items())
            + "   (vol60 med "
            f"{csv.loc[m, 'vol60'].median():.1%})"
        )
        b_out[f"{snm}_all"] = {"ic": ic}
    res["B"] = b_out

    # C: slow_bull 候选条件（基于 B 观察; 阈值扫描见下）
    print("\n" + "=" * 88)
    print("C. slow_bull 候选: vol60 绝对/分位阈值 × bull — 覆盖 seg2 bull vs 误伤 seg0/1 bull")
    print("=" * 88)
    tgt = (csv["seg"] == 2) & bull  # 想覆盖(seg2 慢牛 bull)
    foe = (csv["seg"] != 2) & bull  # 不想误伤(seg0/1 bull)
    print(f"  目标 seg2 bull n={int(tgt.sum())} | 误伤池 seg0/1 bull n={int(foe.sum())}")
    c_out = {}
    for th in [0.12, 0.14, 0.16, 0.18, 0.20]:
        cond = bull & (csv["vol60"] <= th)
        cov = int((cond & tgt).sum()) / tgt.sum()
        mis = int((cond & foe).sum()) / foe.sum()
        extra = int((cond & ~bull).sum())  # bear 中被标记的(理论不该有)
        print(f"  vol60<={th:.0%}: 覆盖 seg2 bull={cov:5.1%}  误伤 seg0/1 bull={mis:5.1%}  非bull标记={extra}")
        c_out[f"vol{th:.0%}"] = {"cov": round(cov, 3), "mis": round(mis, 3)}
    # bull_days 组合: bull & vol<=16% & bull_days>=60
    for bd in [40, 60, 90]:
        cond = bull & (csv["vol60"] <= 0.16) & (csv["bull_days"] >= bd)
        cov = int((cond & tgt).sum()) / tgt.sum()
        mis = int((cond & foe).sum()) / foe.sum()
        print(f"  bull&vol<=16%&days>={bd:3d}: 覆盖={cov:5.1%}  误伤={mis:5.1%}")
        c_out[f"vol16_days{bd}"] = {"cov": round(cov, 3), "mis": round(mis, 3)}
    res["C"] = c_out

    # D: 泛化规则 A/B
    print("\n" + "=" * 88)
    print("D. A/B: R0(单层) vs R1b(seg2bull 去情绪结构, 日历版) vs R_new(状态变量版)")
    print("=" * 88)
    vol_th = 0.16
    slow_bull = bull & (csv["vol60"] <= vol_th) & (csv["bull_days"] >= 40)
    all6 = [k for k in KEYS if k not in SENTI_STRUCT]
    rules = {
        "R0 单层9键": None,
        "R1b seg2bull去情绪(日历)": [(tgt, all6)],
        "R_new slowbull去情绪(状态)": [(slow_bull, all6)],
        "R_all6 全历史6键(最简)": [(pd.Series(True, index=csv.index), all6)],
        "R_all6seg2后(6键仅seg2+)": [(csv["seg"] >= 2, all6)],
    }
    ret = csv["ret60"]
    d_out = {}
    for rname, rule in rules.items():
        comp = csv["composite_score"] if rule is None else composite_regime(csv, rule[0][0], rule[0][1])
        ic = spearman(comp, ret)
        seg_ics = [spearman(comp[csv["seg"] == s], ret[csv["seg"] == s]) for s in range(3)]
        # seg2 后半样本外(含 bear) + bull 内单独
        m2h = (csv["seg"] == 2) & (csv["trade_date"] >= "2024-07-01")
        m2hb = m2h & bull
        ic2h = spearman(comp[m2h], ret[m2h])
        ic2hb = spearman(comp[m2hb], ret[m2hb])
        line = (
            f"{rname:<26} IC60={ic:+.4f}  "
            + " | ".join(f"{nm}={v:+.4f}" for nm, v in zip(SEG_NAMES, seg_ics))
            + f" | seg2后半={ic2h:+.4f}(bull {ic2hb:+.4f})"
        )
        print(line)
        d_out[rname] = {
            "ic60": round(ic, 4),
            "segs": [round(v, 4) if v == v else None for v in seg_ics],
            "seg2h": round(ic2h, 4),
            "seg2hb": round(ic2hb, 4),
        }
    res["D"] = d_out

    with open(OUT, "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    print(f"\n已写出 {OUT}")


if __name__ == "__main__":
    main()
