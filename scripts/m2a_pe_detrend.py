#!/usr/bin/env python3
"""M2a-3: pe 去趋势再评估 — 去趋势 vs 原始 pe 百分位的 IC 对比

M1.3 对 pe 引入去趋势 (除以 3 年滚动中位数再取 1260 分位), #87 归因发现
pe 全样本 IC60≈0 但 seg2(2023-26) −0.170。本脚本在清洁口径上对比:
  A. det_pct  = 当前引擎口径 (去趋势后分位)
  B. raw_pct  = 原始 pe_med 分位 (M1.3 之前口径)
对 ret20/ret60 的 IC (全样本 + seg0/1/2) + 桶单调性。

用法: python scripts/m2a_pe_detrend.py
输出: reports/m2a_pe_detrend.json + stdout 摘要
"""

import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.data.database import DB_PATH
from src.indicators.heat_index_v2 import _detrend
from scripts.backtest_v2 import _pctr

OUT = "reports/m2a_pe_detrend.json"


def seg_of(d):
    return 0 if d < "2019-01-01" else (1 if d < "2023-01-01" else 2)


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
    csv = pd.read_csv("reports/backtest_v2_detail.csv")
    dates = csv["trade_date"].tolist()
    px = csv["close"].astype(float)
    ret20 = px.shift(-20) / px - 1
    ret60 = px.shift(-60) / px - 1
    seg = pd.Series([seg_of(d) for d in dates], index=csv.index)

    conn = sqlite3.connect(DB_PATH)
    pe_df = pd.read_sql("SELECT trade_date, pe_med, n_stocks FROM index_daily_pe WHERE pe_med IS NOT NULL", conn)
    conn.close()
    pe_df["trade_date"] = pe_df["trade_date"].astype(str)

    det_rows, raw_rows = [], []
    n_fallback = 0
    for i, td in enumerate(dates):
        if i % 500 == 0:
            print(f"  ...{i}/{len(dates)} ({td})")
        ty = int(td[:4])
        cur_row = pe_df[pe_df["trade_date"] <= td]
        if cur_row.empty:
            continue
        cur_pe = float(cur_row.iloc[-1]["pe_med"])
        cur_n = cur_row.iloc[-1]["n_stocks"] or 0
        hist = pe_df[(pe_df["trade_date"] >= str(ty - 10) + td[4:]) & (pe_df["trade_date"] <= td)].copy()
        if len(hist) < 60:
            continue
        if cur_n > 0:
            lo, hi = cur_n * 0.5, cur_n * 1.5
            if cur_n >= 600:
                lo = max(lo, 450)
            hist = hist[hist["n_stocks"].between(lo, hi)]
        if len(hist) < 60:
            continue
        # A. 去趋势 (M1.3 引擎口径)
        det, cur_det = _detrend(hist["pe_med"], cur_pe)
        if cur_det is None:
            det_pct = _pctr(hist["pe_med"], cur_pe)
            n_fallback += 1
        else:
            det_pct = _pctr(det.dropna(), cur_det)
        # B. 原始值分位
        raw_pct = _pctr(hist["pe_med"], cur_pe)
        det_rows.append((td, det_pct * 100))
        raw_rows.append((td, raw_pct * 100))

    ddf = pd.DataFrame(det_rows, columns=["trade_date", "pe_det"]).set_index("trade_date")
    rdf = pd.DataFrame(raw_rows, columns=["trade_date", "pe_raw"]).set_index("trade_date")
    res = pd.DataFrame({"trade_date": dates})
    res = res.merge(ddf, on="trade_date", how="left").merge(rdf, on="trade_date", how="left")
    res["ret20"] = ret20.values
    res["ret60"] = ret60.values
    res["seg"] = seg.values

    print(f"\npe 计算完成: {len(ddf)} 天 (fallback→raw 分支 {n_fallback} 天)")
    print(f"ind_pe(CSV, 引擎回测列) 与 pe_det 复刻对比: "
          f"mean|diff|={np.mean(np.abs(csv['ind_pe'].values - res['pe_det'].values)):.4f}")

    out = {}
    for name in ["pe_det", "pe_raw"]:
        x = res[name]
        row = {
            "ic20": spearman(x, res["ret20"]),
            "ic60": spearman(x, res["ret60"]),
            "seg0": spearman(x[res["seg"] == 0], res.loc[res["seg"] == 0, "ret60"]),
            "seg1": spearman(x[res["seg"] == 1], res.loc[res["seg"] == 1, "ret60"]),
            "seg2": spearman(x[res["seg"] == 2], res.loc[res["seg"] == 2, "ret60"]),
            "corr_with_det": spearman(x, res["pe_det"]),
        }
        out[name] = {k: round(v, 4) for k, v in row.items()}
        print(f"\n{name}:")
        for k, v in row.items():
            print(f"  {k}: {v:+.4f}")

    os.makedirs("reports", exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"fallback_days": n_fallback, **out}, f, ensure_ascii=False, indent=1)
    print(f"\n已写出 {OUT}")


if __name__ == "__main__":
    main()
