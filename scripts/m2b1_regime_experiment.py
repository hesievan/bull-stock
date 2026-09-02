#!/usr/bin/env python3
"""m2b1_regime_experiment.py — M2b-1: regime 分层实验（v3.0 干净基准, #100）

背景: v3.0 方向修正后 9 键全样本 IC60 全负（方向一致）, 但 M2a D4 证据（旧 det 口径）
显示情绪/结构键在 bull 态 IC 为正(动量延续)、bear 态强负(反转)。本实验在干净 CSV
（引擎↔回测全一致, git 875e5af 后）上:
  E0 复现 v3.0 基线 IC60（应 ≈ −0.212）
  E1 趋势态(bull/bear)分组: 各键 IC60 + seg 细分 — 复验 D4 是否仍成立
  E2 温度带(level)分组: 各键 IC60 — 温度带是否独立于趋势态另有调节作用
  E3 关键键 regime 网格(趋势态 × 温度带) IC60 热区
  R  A/B 调制规则(激活开关, 非逐格拟合): 按 regime 开关键集合, 行重归一权重,
     对比单层 — 输出全样本 + seg0/1/2 分段 IC60 与档位单调表

Regime 定义(与既有口径一致):
  - 趋势态: close > SMA250 → bull / bear（NaN 段排除）
  - 温度带: 单层 v3.0 composite 展示档 red≥65 / orange 55-64 / yellow 40-54 / green<40
评估口径(与 evaluate 一致): spearman(分位, 未来60日收益), 行重归一 Σ(v·w)/Σw

用法: python scripts/m2b1_regime_experiment.py
输出: stdout + reports/m2b1_regime_experiment.json
"""

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.indicators.heat_index_v2 import INDICATOR_WEIGHTS

OUT = "reports/m2b1_regime_experiment.json"
CSV = "reports/backtest_v2_detail.csv"

# v3.0 9 计分键 + 展示档切点
KEYS = list(INDICATOR_WEIGHTS.keys())
W = dict(INDICATOR_WEIGHTS)
LEVEL_EDGES = {"green": (0, 40), "yellow": (40, 55), "orange": (55, 65), "red": (65, 101)}
LEVELS = ["green", "yellow", "orange", "red"]
SEG_NAMES = ["seg0<2019", "seg1 19-22", "seg2 23-26"]


def seg_of(d):
    return 0 if d < "2019-01-01" else (1 if d < "2023-01-01" else 2)


def spearman(a, b, min_n=60):
    m = pd.notna(a) & pd.notna(b)
    if m.sum() < min_n:
        return np.nan, 0
    ra = pd.Series(a[m]).rank()
    rb = pd.Series(b[m]).rank()
    if ra.std() == 0 or rb.std() == 0:
        return np.nan, 0
    ic = float(np.corrcoef(ra, rb)[0, 1])
    n = int(m.sum())
    t = ic * np.sqrt((n - 2) / max(1e-9, 1 - ic * ic)) if abs(ic) < 1 else 0.0
    return ic, t


def load():
    csv = pd.read_csv(CSV)
    csv["trade_date"] = csv["trade_date"].astype(str)
    px = csv["close"].astype(float)
    csv["ret60"] = px.shift(-60) / px - 1
    csv["sma250"] = px.rolling(250).mean()
    csv["bull"] = csv["close"] > csv["sma250"]  # NaN 段=无趋势态
    csv["seg"] = csv["trade_date"].apply(seg_of)
    for k in KEYS:
        col = f"ind_{k}"
        if col not in csv.columns:
            print(f"!! CSV 缺列 {col}"); sys.exit(1)
        csv[col] = pd.to_numeric(csv[col], errors="coerce")
    return csv


def ic_tab(csv, keys=KEYS, mask=None):
    """返回 {k: (ic60, t60, n)}; mask 为 bool Series 时在其内算"""
    m = pd.Series(True, index=csv.index) if mask is None else mask
    out = {}
    for k in keys:
        ic, t = spearman(csv.loc[m, f"ind_{k}"], csv.loc[m, "ret60"])
        out[k] = {"ic60": round(ic, 4) if ic == ic else None,
                  "t60": round(t, 2) if t == t else None,
                  "n": int(m.sum())}
    return out


def main():
    csv = load()
    n = len(csv)
    res = {"meta": {"rows": n, "date_range": [csv["trade_date"].iloc[0], csv["trade_date"].iloc[-1]]}}

    print("=" * 88)
    print("E0. v3.0 单层基线复现（9 键行重归一 composite_score → ret60）")
    print("=" * 88)
    ic0, t0 = spearman(csv["composite_score"], csv["ret60"])
    print(f"  全样本 IC60 = {ic0:+.4f} (t={t0:.1f}, n={int(pd.notna(csv['ret60']).sum())})")
    seg_ics = [spearman(csv["composite_score"][csv["seg"] == s],
                        csv.loc[csv["seg"] == s, "ret60"])[0] for s in range(3)]
    print("  分段: " + " | ".join(f"{nm}={ic:+.4f}" for nm, ic in zip(SEG_NAMES, seg_ics)))
    res["E0"] = {"ic60": round(ic0, 4), "segs": seg_ics}

    # ---- E1 趋势态分组 ----
    print("\n" + "=" * 88)
    print("E1. 趋势态分组 IC60（bull: close>SMA250）— 复验 D4")
    print("=" * 88)
    for bs in [True, False]:
        m = csv["bull"] == bs
        tab = ic_tab(csv, mask=m)
        nm = "bull" if bs else "bear"
        row = "  ".join(f"{k}:{v['ic60']:+.3f}" for k, v in tab.items())
        print(f"  {nm:<5} n={int(m.sum()):4d}  {row}")
        # bull/bear 内的 seg 细分 (防 seg 混淆)
        sub = {}
        for s in range(3):
            mm = m & (csv["seg"] == s)
            if mm.sum() > 100:
                sub[SEG_NAMES[s]] = ic_tab(csv, mask=mm)
        res.setdefault("E1", {})[nm] = tab
        res.setdefault("E1_seg", {})[nm] = sub
        if sub:
            print(f"    -- {nm} 内 seg 细分 turnover/ma/new_high/buffett/pe:")
            for segn, tb in sub.items():
                print(f"       {segn:<12} n={tb['turnover']['n']:4d}  " +
                      "  ".join(f"{k}:{tb[k]['ic60']:+.3f}"
                                for k in ["turnover", "ma_alignment", "new_high", "buffett", "pe"]))

    # ---- E2 温度带分组 ----
    print("\n" + "=" * 88)
    print("E2. 温度带(单层展示档)分组 IC60")
    print("=" * 88)
    lv = pd.Series(csv["level"], index=csv.index).astype(str)
    for lvl in LEVELS:
        m = lv == lvl
        tab = ic_tab(csv, mask=m)
        print(f"  {lvl:<6} n={int(m.sum()):4d}  " +
              "  ".join(f"{k}:{v['ic60']:+.3f}" for k, v in tab.items()))
        res.setdefault("E2", {})[lvl] = tab

    # ---- E3 趋势态 × 温度带 网格（关注情绪/结构键 + 估值对照） ----
    print("\n" + "=" * 88)
    print("E3. regime 网格: (bull/bear) × (green/其余/red) — 情绪/结构键 IC60 热区")
    print("=" * 88)
    grid_keys = ["turnover", "ma_alignment", "new_high", "pe", "buffett", "futures_discount"]
    bands = {"green": ("green",), "mid": ("yellow", "orange"), "red": ("red",)}
    e3 = {}
    for bs, bnm in [(True, "bull"), (False, "bear")]:
        for bname, lset in bands.items():
            m = (csv["bull"] == bs) & lv.isin(lset)
            if m.sum() < 60:
                print(f"  {bnm:>4}×{bname:<5} n={int(m.sum()):4d}  (样本不足)")
                continue
            parts = []
            for k in grid_keys:
                ic, t = spearman(csv.loc[m, f"ind_{k}"], csv.loc[m, "ret60"])
                parts.append(f"{k}:{ic:+.2f}" if ic == ic else f"{k}:NaN")
            print(f"  {bnm:>4}×{bname:<5} n={int(m.sum()):4d}  " + "  ".join(parts))
            e3[f"{bnm}_{bname}"] = {k: (spearman(csv.loc[m, f'ind_{k}'], csv.loc[m, 'ret60'])[0]) for k in grid_keys}
    res["E3"] = e3

    # ---- R 调制 A/B（激活开关, 非拟合） ----
    print("\n" + "=" * 88)
    print("R. 调制规则 A/B: 全样本 + walk-forward 分段 IC60（Δ<0 = 更强/优于单层基线）")
    print("=" * 88)
    senti_struct = ("turnover", "ma_alignment", "new_high")
    fund = ("yield_spread", "m1_m2_spread", "margin_buy_ratio", "futures_discount")
    hot = csv["level"].isin(["orange", "red"]).astype(bool)

    def apply_rule(keys_by_cond):
        """keys_by_cond: [(mask, keys), ...] 逐条独立计算, 后规则覆盖先规则; 未匹配行回退单层"""
        comp = csv["composite_score"].copy()
        for mask, keys in keys_by_cond:
            num = pd.Series(0.0, index=csv.index)
            den = pd.Series(0.0, index=csv.index)
            for k in keys:
                v = csv[f"ind_{k}"]
                num = num + v.fillna(0) * W[k]
                den = den + v.notna() * W[k]
            with np.errstate(divide="ignore", invalid="ignore"):
                cand = pd.Series(np.where(den > 0, num / np.where(den > 0, den, np.nan), np.nan),
                                 index=csv.index)
            comp = comp.where(~mask, cand)
        return comp

    rules = {
        "R0 单层9键(对照)": None,
        # bull 态去掉情绪/结构键(全 seg) — E1 观察: seg2 bull 内 turnover/ma 转正
        "R1 bull去情绪结构": [((csv["bull"] == True),  # noqa: E712
                             [k for k in KEYS if k not in senti_struct])],
        # R1 改良: 仅 seg2 慢牛 bull 去情绪结构; seg0/1 bull 保留(E1: 它们仍有效)
        "R1b seg2bull去情绪": [((csv["bull"] == True) & (csv["seg"] == 2),  # noqa: E712
                              [k for k in KEYS if k not in senti_struct])],
        # 更细: 仅高温(orange/red) bull 去 ma_alignment(E3: bull×red ma +0.41 最强正)
        "R1c hot-bull去ma": [((csv["bull"] == True) & hot,  # noqa: E712
                            [k for k in KEYS if k != "ma_alignment"])],
        # bear 去转正资金键(E1: bear 内 yield_spread/m1_m2 转正, 去掉应增强)
        "R2b bear去资金键": [((csv["bull"] == False),  # noqa: E712
                            [k for k in KEYS if k not in fund])],
        # 组合: seg2 bull 去情绪结构 + bear 去资金键
        "R3 组合调制": [((csv["bull"] == True) & (csv["seg"] == 2),  # noqa: E712
                       [k for k in KEYS if k not in senti_struct]),
                      ((csv["bull"] == False),  # noqa: E712
                       [k for k in KEYS if k not in fund])],
    }
    r_out = {}
    for rname, rule in rules.items():
        if rule is None:
            comp = csv["composite_score"]
        else:
            comp = apply_rule(rule)
        ic, t = spearman(comp, csv["ret60"])
        seg_ics = [spearman(comp[csv["seg"] == s], csv.loc[csv["seg"] == s, "ret60"])[0] for s in range(3)]
        # 档位表: 调制后 composite 重新分档的后60日表现
        lv2 = pd.cut(comp, [-1, 40, 55, 65, 101], labels=LEVELS)
        bucket = {}
        for lvl in ["green", "red", "orange", "yellow"]:
            m = lv2 == lvl
            if m.sum() >= 30:
                bucket[lvl] = {"n": int(m.sum()),
                             "ret60": round(float(csv.loc[m, "ret60"].mean()), 4) if csv.loc[m, "ret60"].notna().sum() else None,
                             "win": round(float((csv.loc[m, "ret60"] > 0).mean()), 3)}
        m75 = comp >= 75
        m80 = comp >= 80
        for nm, mm in [(">=75", m75), (">=80", m80)]:
            if mm.sum() >= 20:
                bucket[nm] = {"n": int(mm.sum()),
                              "ret60": round(float(csv.loc[mm, "ret60"].mean()), 4),
                              "win": round(float((csv.loc[mm, "ret60"] > 0).mean()), 3)}
        d = ic - ic0
        line = f"{rname:<22} IC60={ic:+.4f} (Δ{d:+.4f})  " + \
               " | ".join(f"{nm}={v:+.4f}" for nm, v in zip(SEG_NAMES, seg_ics))
        print(line)
        bstr = "  " + "  ".join(f"{nm}:n={b['n']} ret60={b['ret60']:+.2%} win={b['win']:.0%}"
                                for nm, b in bucket.items() if b.get("ret60") is not None)
        print(f"{'':28}{bstr}")
        r_out[rname] = {"ic60": round(ic, 4), "d_ic60": round(d, 4),
                        "segs": [round(v, 4) if v == v else None for v in seg_ics],
                        "buckets": bucket}
    res["R"] = r_out

    # ---- V. R1b 稳健性: seg2 内前后 split ----
    print("\n" + "=" * 88)
    print("V. R1b 稳健性: seg2(2023-26) 内前后 split（规则后半段是否仍有效=非前半拟合）")
    print("=" * 88)
    half = "2024-07-01"
    v_out = {}
    comp_r1b = apply_rule([((csv["bull"] == True) & (csv["seg"] == 2),  # noqa: E712
                            [k for k in KEYS if k not in senti_struct])])
    for hname, hmask in [("seg2前半<2024-07", csv["trade_date"] < half),
                         ("seg2后半>=2024-07", csv["trade_date"] >= half)]:
        m = hmask & (csv["seg"] == 2) & (csv["bull"] == True)  # noqa: E712
        if m.sum() < 60:
            print(f"  {hname:<16} n={int(m.sum()):4d}  (样本不足)"); continue
        ic0_h, _ = spearman(csv.loc[m, "composite_score"], csv.loc[m, "ret60"])
        ic1_h, _ = spearman(comp_r1b[m], csv.loc[m, "ret60"])
        print(f"  {hname:<16} n={int(m.sum()):4d}  R0={ic0_h:+.4f}  R1b={ic1_h:+.4f}  Δ={ic1_h - ic0_h:+.4f}")
        v_out[hname] = {"n": int(m.sum()), "r0": round(ic0_h, 4), "r1b": round(ic1_h, 4)}
    # 非 seg2 对照: R1b 不应影响 seg0/1(规则 mask 限定 seg2) — E 已证 seg0/1 IC 不变
    res["V"] = v_out

    with open(OUT, "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    print(f"\n已写出 {OUT}")


if __name__ == "__main__":
    main()
