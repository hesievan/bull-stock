#!/usr/bin/env python3
"""M2a-4: turnover / ma_alignment 2023-26(seg2) 未兑现"转负"预期 — 排查

背景: M1.3 去趋势后全样本 turnover IC60=−0.070 / ma_alignment=−0.133 (负=符合热度假设),
但 seg2(2023-26) turnover=+0.049 / ma_alignment=+0.002 (未转负甚至反向)。
本脚本:
  A. seg2 行为分析: 分年段 IC、5 桶 ret60 单调性、与过去60日收益(动量)关系、牛/熊态内 IC
  B. raw(不去趋势) 复算: 对比 det vs raw 的 seg0/1/2 IC — 判断去趋势本身是否 seg2 失效主因
用法: python scripts/m2a_seg_probe.py   输出: stdout + reports/m2a_seg_probe.json
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

OUT = "reports/m2a_seg_probe.json"
KEYS = ["turnover", "ma_alignment"]


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


def bucket_ret(x, y, n=5):
    m = pd.notna(x) & pd.notna(y)
    if m.sum() < 60:
        return None
    xx, yy = pd.Series(x[m]), pd.Series(y[m])
    q = pd.qcut(xx, n, labels=False, duplicates="drop")
    return [float(yy[q == i].mean()) for i in range(q.nunique())]


def main():
    csv = pd.read_csv("reports/backtest_v2_detail.csv")
    dates = csv["trade_date"].tolist()
    px = csv["close"].astype(float)
    csv["ret60"] = px.shift(-60) / px - 1
    csv["ret_past60"] = px / px.shift(60) - 1
    csv["seg"] = csv["trade_date"].apply(seg_of)
    csv["year"] = csv["trade_date"].str[:4]
    csv["sma250"] = px.rolling(250).mean()
    csv["bull"] = csv["close"] > csv["sma250"]

    conn = sqlite3.connect(DB_PATH)
    raw_tables = {
        "turnover": pd.read_sql(
            "SELECT trade_date, turnover_rate FROM daily_turnover WHERE turnover_rate IS NOT NULL", conn
        ),
        "ma_alignment": pd.read_sql(
            "SELECT trade_date, ma_alignment_ratio FROM daily_ma_alignment WHERE ma_alignment_ratio IS NOT NULL",
            conn,
        ),
    }
    conn.close()
    for k, df in raw_tables.items():
        df["trade_date"] = df["trade_date"].astype(str)

    print("=" * 80)
    print("A. CSV(去趋势)口径 seg2 内部分析")
    print("=" * 80)
    res = {"A": {}, "B": {}}
    for k in KEYS:
        col = f"ind_{k}"
        x = pd.to_numeric(csv[col], errors="coerce")
        seg2 = csv["seg"] == 2
        print(f"\n--- {k} ---")
        print(f"  seg2 总 IC60 = {spearman(x[seg2], csv.loc[seg2, 'ret60']):+.4f}")
        # 分年段 (seg2 = 2023..2026)
        for y in ["2023", "2024", "2025", "2026"]:
            m = seg2 & (csv["year"] == y)
            n = int(m.sum())
            ic = spearman(x[m], csv.loc[m, "ret60"])
            print(f"  {y}: n={n:4d} IC60={ic:+.4f}")
        # 5 桶
        bk = bucket_ret(x[seg2], csv.loc[seg2, "ret60"])
        if bk:
            print("  seg2 5桶 ret60均值: " + " ".join(f"{v:+.2%}" for v in bk))
        # 与动量关系 (高 turnover 是否由过去60日大涨驱动)
        hi = seg2 & (x >= x[seg2].quantile(0.8))
        lo = seg2 & (x <= x[seg2].quantile(0.2))
        if hi.sum() > 20 and lo.sum() > 20:
            print(
                f"  seg2 top20%分位日: 过去60日收益均值={csv.loc[hi, 'ret_past60'].mean():+.2%}  "
                f"未来60日={csv.loc[hi, 'ret60'].mean():+.2%}"
            )
            print(
                f"  seg2 bot20%分位日: 过去60日收益均值={csv.loc[lo, 'ret_past60'].mean():+.2%}  "
                f"未来60日={csv.loc[lo, 'ret60'].mean():+.2%}"
            )
        # bull/bear 内 IC
        for bs in [True, False]:
            m = seg2 & (csv["bull"] == bs)
            if m.sum() > 100:
                print(
                    f"  seg2 {'bull' if bs else 'bear'}态: n={int(m.sum()):4d} IC60={spearman(x[m], csv.loc[m, 'ret60']):+.4f}"
                )
        res["A"][k] = {
            "seg2_ic60": spearman(x[seg2], csv.loc[seg2, "ret60"]),
            "bucket": bk,
        }

    print("\n" + "=" * 80)
    print("B. raw(不去趋势) vs det 复算 — 3 段 IC60")
    print("=" * 80)
    for k in KEYS:
        df = raw_tables[k]
        val_col = "turnover_rate" if k == "turnover" else "ma_alignment_ratio"
        rows = []
        for i, td in enumerate(dates):
            if i % 700 == 0:
                print(f"  ...{k} {i}/{len(dates)}")
            ty = int(td[:4])
            cur_row = df[df["trade_date"] <= td]
            if cur_row.empty:
                continue
            cur_v = float(cur_row.iloc[-1][val_col])
            hist = df[(df["trade_date"] >= str(ty - 10) + td[4:]) & (df["trade_date"] <= td)][val_col]
            hist = hist.dropna()
            if len(hist) < 60:
                continue
            det, cur_det = _detrend(hist, cur_v)
            if cur_det is None:
                det_pct = _pctr(hist, cur_v)
            else:
                det_pct = _pctr(det.dropna(), cur_det)
            raw_pct = _pctr(hist, cur_v)
            rows.append((td, det_pct * 100, raw_pct * 100))
        pdf = pd.DataFrame(rows, columns=["trade_date", f"{k}_det", f"{k}_raw"]).set_index("trade_date")
        j = csv.set_index("trade_date")
        j = j.join(pdf)
        print(f"\n--- {k}: det(引擎回测) vs raw ---")
        for nm in [f"{k}_det", f"{k}_raw"]:
            x = pd.to_numeric(j[nm], errors="coerce")
            ic60 = spearman(x, j["ret60"])
            segs = [spearman(x[j["seg"] == s], j.loc[j["seg"] == s, "ret60"]) for s in range(3)]
            print(f"  {nm:16s} ic60={ic60:+.4f}  seg0={segs[0]:+.4f}  seg1={segs[1]:+.4f}  seg2={segs[2]:+.4f}")
        # 与 CSV det 列对齐校验
        csv_col = f"ind_{k}"
        cmp = j[[csv_col, f"{k}_det"]].dropna()
        if len(cmp):
            print(f"  复刻校验 mean|diff| vs CSV = {np.mean(np.abs(cmp[csv_col] - cmp[f'{k}_det'])):.4f}")

    os.makedirs("reports", exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    print(f"\n已写出 {OUT}")


if __name__ == "__main__":
    main()
