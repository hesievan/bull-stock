#!/usr/bin/env python3
"""计分指标对牛熊状态预测贡献的系统评估（walk-forward / IC / 消融 / 转换敏感性）。
用法: python scripts/evaluate_indicators.py
输入: reports/backtest_v2_detail.csv  输出: reports/indicators_eval_data.json + stdout 各分析表
注意: 依赖 src.indicators.heat_index_v2 的权重配置, 权重变更后重跑即得新结论

数据: reports/backtest_v2_detail.csv (交易日 × 计分指标百分位 + close + phase)
方法:
  A. 单指标预测力: 全样本 + 3 段 walk-forward 的 IC(秩相关, 指标→未来20/60日收益)
  B. 领先/滞后: 指标与过去60日收益 vs 未来60日收益的秩相关
  C. 牛熊判别: close 相对 SMA250 定义牛/熊, 计算区分度 t
  D. 逐项剔除消融: leave-one-out 重归一化综合分的 IC60 对比基线
  E. 转换敏感性: close 上/下穿 SMA250 前 20 日指标读数 vs 全历史分布
"""

import json, math
import numpy as np
import pandas as pd
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.indicators.heat_index_v2 import INDICATOR_WEIGHTS, INDICATOR_DIMENSIONS

KEYS = list(INDICATOR_WEIGHTS.keys())
COL = {k: f"ind_{k}" for k in KEYS}
W = INDICATOR_WEIGHTS
DIM = INDICATOR_DIMENSIONS

df = pd.read_csv("reports/backtest_v2_detail.csv")
print("载入", len(df), "行", df.trade_date.iloc[0], "~", df.trade_date.iloc[-1])

for k in KEYS:
    df[COL[k]] = pd.to_numeric(df[COL[k]], errors="coerce")

# ---- 收盘价 / 未来收益 ----
px = df["close"].astype(float)
df["ret20"] = px.shift(-20) / px - 1
df["ret60"] = px.shift(-60) / px - 1
df["ret_past60"] = px / px.shift(60) - 1  # 过去60日收益

# ---- 牛熊状态: close vs SMA250 ----
df["sma250"] = px.rolling(250).mean()
df["bull_state"] = df["close"] > df["sma250"]


# ---- 时间分段 (walk-forward 3 段) ----
def seg_of(d):
    return 0 if d < "2019-01-01" else (1 if d < "2023-01-01" else 2)


df["seg"] = df["trade_date"].apply(seg_of)
SEG_LABEL = {0: "2015-2018", 1: "2019-2022", 2: "2023-2026"}


# ============ 工具 ============
def spearman(a, b):
    m = pd.notna(a) & pd.notna(b)
    if m.sum() < 60:
        return np.nan
    ra = pd.Series(a[m]).rank()
    rb = pd.Series(b[m]).rank()
    ra, rb = ra.values, rb.values
    if ra.std() == 0 or rb.std() == 0:
        return np.nan
    return float(np.corrcoef(ra, rb)[0, 1])


def bucket_ret(x, y, n=5):
    """按 x 分 n 桶, 返回每桶 y 均值"""
    m = pd.notna(x) & pd.notna(y)
    if m.sum() < 60:
        return None
    xx, yy = pd.Series(x[m]), pd.Series(y[m])
    q = pd.qcut(xx, n, labels=False, duplicates="drop")
    return [float(yy[q == i].mean()) for i in range(q.nunique())]


def tstat_bullbear(v, bull, bear):
    v = np.asarray(v, dtype=float)
    b = v[bull]
    r = v[bear]
    b = b[np.isfinite(b)]
    r = r[np.isfinite(r)]
    if len(b) < 30 or len(r) < 30:
        return np.nan
    s = np.sqrt(b.var() / len(b) + r.var() / len(r))
    return float((b.mean() - r.mean()) / s) if s > 0 else np.nan


# ============ A. 单指标预测力 ============
print("\n===== A. 单指标 IC (spearman: 指标百分位 → 未来收益) =====")
rowsA = []
for k in KEYS:
    x = df[COL[k]]
    ic20_all = spearman(x, df["ret20"])
    ic60_all = spearman(x, df["ret60"])
    # walk-forward 分段
    seg_ic = []
    for s in range(3):
        m = df["seg"] == s
        seg_ic.append(spearman(x[m], df.loc[m, "ret60"]))
    same_sign = all(v > 0 for v in seg_ic if not np.isnan(v)) or all(v < 0 for v in seg_ic if not np.isnan(v))
    # 单调性: 5桶首末差
    bk = bucket_ret(x, df["ret60"])
    mono = (bk[-1] - bk[0]) if bk else np.nan
    # 显著性: 全样本 IC 的 t 近似 = r*sqrt((n-2)/(1-r^2))
    m = pd.notna(x) & pd.notna(df["ret60"])
    n = int(m.sum())
    t = ic60_all * math.sqrt((n - 2) / (1 - ic60_all**2)) if (m.sum() > 60 and abs(ic60_all) < 1) else np.nan
    rowsA.append(
        dict(
            k=k,
            ic20=ic20_all,
            ic60=ic60_all,
            t60=t,
            seg0=seg_ic[0],
            seg1=seg_ic[1],
            seg2=seg_ic[2],
            stable=same_sign,
            mono=mono,
            n=n,
        )
    )
A = pd.DataFrame(rowsA).sort_values("ic60", key=lambda s: s.abs(), ascending=False)
pd.set_option("display.width", 200)
print(A.round(3).to_string(index=False))

# ============ B. 领先 / 滞后 ============
print("\n===== B. 领先 vs 滞后 (指标 T vs 过去/未来60日收益) =====")
rowsB = []
for k in KEYS:
    x = df[COL[k]]
    lead = spearman(x, df["ret60"])  # 预测未来
    lag = spearman(x, df["ret_past60"])  # 确认过去
    rowsB.append(dict(k=k, lead60=lead, lag60=lag, diff=lead - lag))
B = pd.DataFrame(rowsB).sort_values("diff", ascending=False)
print(B.round(3).to_string(index=False))

# ============ C. 牛熊判别 ============
print("\n===== C. 牛熊判别区分度 (bull: close>SMA250, n=%d) =====" % int(df.bull_state.sum()))
rowsC = []
bull_mask = df.bull_state.fillna(False).values
for k in KEYS:
    v = df[COL[k]]
    t = tstat_bullbear(v.values, bull_mask, ~bull_mask)
    rowsC.append(dict(k=k, t=t, bull=v[bull_mask].mean(), bear=v[~bull_mask].mean()))
C = pd.DataFrame(rowsC).sort_values("t", key=lambda s: s.abs(), ascending=False)
print(C.round(2).to_string(index=False))

# ============ D. 逐项剔除消融 ============
print("\n===== D. Leave-one-out 消融 (重归一化综合分 → IC60) =====")


def composite(sub):
    """综合分 = 可用键按行重归一化加权平均 (M2a 修正: 对齐引擎真实口径)。

    原实现用全局分母 sum(W[sub]) + pandas Series 加法的 NaN 传播 — 任一键缺失
    (如 futures_discount 2015-16 缺 557 行 / pe 缺 140 行) 会让整行 composite 为 NaN,
    导致基线 IC 系统性偏低 (实测 −0.0364 vs 引擎真实 −0.0520), 且与引擎
    heat_index_v2.compute_index_v2 的"可用键加权/可用权重和"口径不一致。
    现按行: num = Σ(非NaN键 v·w), den = Σ(非NaN键 w), composite = num/den。
    """
    ws = {k: W[k] for k in sub if k in COL}
    if not ws:
        return pd.Series(np.nan, index=df.index)
    wmap = {COL[k]: w for k, w in ws.items()}  # {'ind_pe': 权重, ...}
    cols = df[list(wmap)]
    num = cols.mul(wmap).sum(axis=1)  # dict 按列名广播; NaN 项被 sum(skipna) 跳过
    den = cols.notna().mul(wmap).sum(axis=1)
    comp = num / den.replace(0, np.nan)
    return comp.where(den > 0)


base = composite(KEYS)
base_ic60 = spearman(base, df["ret60"])
base_ic20 = spearman(base, df["ret20"])
print(f"基线 ({len(KEYS)}计分指标): IC20={base_ic20:.4f}  IC60={base_ic60:.4f}")
rowsD = []
for k in KEYS:
    sub = [x for x in KEYS if x != k]
    c = composite(sub)
    ic60 = spearman(c, df["ret60"])
    ic20 = spearman(c, df["ret20"])
    rowsD.append(dict(k=k, ic60=ic60, ic20=ic20, d60=ic60 - base_ic60, d20=ic20 - base_ic20))
D = pd.DataFrame(rowsD).sort_values("d60", ascending=False)
print(D.round(4).to_string(index=False))

# ============ E. 转换敏感性 (SMA250 上/下穿前 20 日) ============
print("\n===== E. 牛熊转换前 20 日指标读数 =====")
state = df["bull_state"].fillna(False).astype(int).values
dates = df.trade_date.values
down_ev = []  # bull→bear
up_ev = []  # bear→bull
for i in range(1, len(state)):
    if state[i - 1] == 1 and state[i] == 0:
        down_ev.append(i)
    if state[i - 1] == 0 and state[i] == 1:
        up_ev.append(i)
print("下穿(bull→bear)事件:", len(down_ev), [dates[i][:7] for i in down_ev])
print("上穿(bear→bull)事件:", len(up_ev), [dates[i][:7] for i in up_ev])

rowsE = []
for k in KEYS:
    v = df[COL[k]].values
    full = v[~np.isnan(v)]
    hist_mean, hist_std = full.mean(), full.std()
    out = dict(k=k)
    for nm, evs in [("down", down_ev), ("up", up_ev)]:
        wins = []
        for i in evs:
            seg = v[max(0, i - 20) : i]
            seg = seg[~np.isnan(seg)]
            if len(seg) >= 10:
                wins.append(seg.mean())
        if wins:
            z = (np.mean(wins) - hist_mean) / hist_std if hist_std > 0 else 0
            out[nm + "_mean"] = np.mean(wins)
            out[nm + "_z"] = z
        else:
            out[nm + "_mean"] = np.nan
            out[nm + "_z"] = np.nan
    rowsE.append(out)
E = pd.DataFrame(rowsE)
print("下穿(顶部)前20日 z 分(>0=偏高):")
print(E[["k", "down_mean", "down_z"]].sort_values("down_z", ascending=False).round(2).to_string(index=False))
print("上穿(底部)前20日 z 分(<0=偏低):")
print(E[["k", "up_mean", "up_z"]].sort_values("up_z").round(2).to_string(index=False))

# ============ 汇总输出 ============
out = {
    "A": A.round(4).to_dict("records"),
    "B": B.round(4).to_dict("records"),
    "C": C.round(4).to_dict("records"),
    "D": D.round(4).to_dict("records"),
    "E": E.round(3).to_dict("records"),
    "base": {"ic20": base_ic20, "ic60": base_ic60},
    "meta": {
        "n": len(df),
        "range": [df.trade_date.iloc[0], df.trade_date.iloc[-1]],
        "down_events": [dates[i] for i in down_ev],
        "up_events": [dates[i] for i in up_ev],
    },
}
with open("reports/indicators_eval_data.json", "w") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print("\n已写出 reports/indicators_eval_data.json")
