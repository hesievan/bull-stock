#!/usr/bin/env python3
"""M2a-2: 三错配键方向翻转 A/B 实验 (M2a 方向体检首批)

在同一份清洁口径回测 CSV 上, 对 IC60 为正(与热度假设错配)的键做方向翻转:
    flipped 键的得分 = 100 - ind_k   (rank 精确逆序, 与引擎内 _pct_rank(-hist,-cur) 等价)
比较综合分 (重归一化加权百分位) 的 IC20/IC60 (全样本 + seg0/1/2)。

注意: 这是分析实验, 不改生产引擎/权重。结果供 M2a-5 方向体检报告决策用。

用法: python scripts/m2a_flip_ab.py
输出: reports/m2a_flip_ab.json + stdout 摘要
"""

import itertools
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.indicators.heat_index_v2 import INDICATOR_WEIGHTS

CSV = "reports/backtest_v2_detail.csv"
OUT = "reports/m2a_flip_ab.json"

KEYS = list(INDICATOR_WEIGHTS.keys())  # 9 计分键
W = INDICATOR_WEIGHTS
COL = {k: f"ind_{k}" for k in KEYS}

# IC60 与热度假设错配 (为正) 的键 — M2a 方向体检对象
MISMATCH = ["yield_spread", "margin_buy_ratio", "m1_m2_spread"]


def seg_of(d):
    return 0 if d < "2019-01-01" else (1 if d < "2023-01-01" else 2)


SEG_LABEL = {0: "2015-2018", 1: "2019-2022", 2: "2023-2026"}


def spearman(a, b):
    m = pd.notna(a) & pd.notna(b)
    if m.sum() < 60:
        return np.nan
    ra = pd.Series(a[m]).rank()
    rb = pd.Series(b[m]).rank()
    if ra.std() == 0 or rb.std() == 0:
        return np.nan
    return float(np.corrcoef(ra, rb)[0, 1])


def main():
    df = pd.read_csv(CSV)
    print(f"载入 {len(df)} 行 {df.trade_date.iloc[0]} ~ {df.trade_date.iloc[-1]}")
    for k in KEYS:
        df[COL[k]] = pd.to_numeric(df[COL[k]], errors="coerce")

    px = df["close"].astype(float)
    df["ret20"] = px.shift(-20) / px - 1
    df["ret60"] = px.shift(-60) / px - 1
    df["seg"] = df["trade_date"].apply(seg_of)

    def composite(flipped):
        """flipped: set of key names 翻转为 100-x"""
        cols = {}
        for k in KEYS:
            cols[k] = 100.0 - df[COL[k]] if k in flipped else df[COL[k]]
        # 按行重归一化 (对齐引擎可用键口径)
        out = []
        for i in range(len(df)):
            num, den = 0.0, 0.0
            for k in KEYS:
                v = cols[k].iloc[i]
                if pd.notna(v):
                    num += v * W[k]
                    den += W[k]
            out.append(num / den if den > 0 else np.nan)
        return pd.Series(out, index=df.index)

    def ic_table(comp):
        return {
            "ic20": spearman(comp, df["ret20"]),
            "ic60": spearman(comp, df["ret60"]),
            "seg0": spearman(comp[df["seg"] == 0], df.loc[df["seg"] == 0, "ret60"]),
            "seg1": spearman(comp[df["seg"] == 1], df.loc[df["seg"] == 1, "ret60"]),
            "seg2": spearman(comp[df["seg"] == 2], df.loc[df["seg"] == 2, "ret60"]),
        }

    rows = []

    # 基线: 无翻转
    rows.append({"flip": tuple(), **ic_table(composite(set()))})

    # 全部 2^3 = 8 种翻转组合
    for r in range(1, 4):
        for combo in itertools.combinations(MISMATCH, r):
            rows.append({"flip": combo, **ic_table(composite(set(combo)))})

    # 扩展: 翻转所有 IC60 为正的键 (全键方向校准上界)
    all_pos = [k for k in KEYS if spearman(df[COL[k]], df["ret60"]) > 0]
    rows.append({"flip": ("ALL_POS",) + tuple(all_pos), **ic_table(composite(set(all_pos)))})

    # 输出
    print("\nflip 组合                     IC20    IC60    seg0     seg1     seg2")
    res = []
    for r in rows:
        tag = "+".join(r["flip"]) if r["flip"] else "(基线,无翻转)"
        print(f"{tag:28s} {r['ic20']:+.4f} {r['ic60']:+.4f}  {r['seg0']:+.4f}  {r['seg1']:+.4f}  {r['seg2']:+.4f}")
        res.append({"flip": list(r["flip"]), **{k: round(v, 4) for k, v in r.items() if k != "flip"}})

    os.makedirs("reports", exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"keys": KEYS, "weights": W, "mismatch": MISMATCH, "rows": res}, f, ensure_ascii=False, indent=1)
    print(f"\n已写出 {OUT}")


if __name__ == "__main__":
    main()
