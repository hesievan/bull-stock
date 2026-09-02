#!/usr/bin/env python3
"""m2b3_mode_compare.py — M2b-3: engine_mode single9 vs single6 前向对比（#102）

输入: reports/backtest_v2_detail_single9.csv / _single6.csv
（由 backtest_v2.py --mode single6 生成后改名; single9 为 #99 重建基线）

评估口径与 m2b1/m2b2 一致: spearman(composite, 未来60日收益 ret60 = px.shift(-60)/px - 1)。
分段: seg0<2019 / seg1 2019-22 / seg2 2023-26; "近 2 年(前向可预期区)" = >= 2024-09-01。

输出: stdout + reports/m2b3_mode_compare.json
"""

import json

import numpy as np
import pandas as pd

CSV9 = "reports/backtest_v2_detail_single9.csv"
CSV6 = "reports/backtest_v2_detail_single6.csv"
OUT = "reports/m2b3_mode_compare.json"
SEG_NAMES = ["seg0<2019", "seg1 19-22", "seg2 23-26"]


BULL_PHASES = {"bull_peak", "bull_rally", "slow_bull", "bounce"}


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


def load(path):
    csv = pd.read_csv(path)
    csv["trade_date"] = csv["trade_date"].astype(str)
    px = csv["close"].astype(float)
    csv["ret60"] = px.shift(-60) / px - 1
    csv["seg"] = csv["trade_date"].apply(seg_of)
    return csv


def main():
    c9 = load(CSV9)
    c6 = load(CSV6)
    assert len(c9) == len(c6)
    assert (c9["trade_date"] == c6["trade_date"]).all(), "两份 CSV 日期集不一致"
    res = {"meta": {"rows": len(c9), "date_range": [c9["trade_date"].iloc[0], c9["trade_date"].iloc[-1]]}}

    # ── 1. 全样本 + 分段 IC60 ──────────────────────────────────────────
    print("=" * 96)
    print("A. IC60 对比 (spearman(composite, ret60)); 越负 = 反指预测力越强")
    print("=" * 96)
    seg_sets = [("全样本", pd.Series(True, index=c9.index))] + [(SEG_NAMES[s], c9["seg"] == s) for s in range(3)]
    for nm, m in seg_sets:
        ic9, _ = spearman(c9["composite_score"][m], c9.loc[m, "ret60"])
        ic6, _ = spearman(c6["composite_score"][m], c6.loc[m, "ret60"])
        delta = (ic6 - ic9) if (ic6 == ic6 and ic9 == ic9) else np.nan
        print(f"  {nm:<10} n={int(m.sum()):5d} | single9 {ic9:+.4f} | single6 {ic6:+.4f} | Δ {delta:+.4f}")
        res.setdefault("A_ic60", {})[nm] = {
            "single9": round(ic9, 4),
            "single6": round(ic6, 4),
            "delta": round(delta, 4),
        }

    # ── 2. 近 2 年 (>= 2024-09-01, 未来可预期区) ─────────────────────────
    print("\n" + "=" * 96)
    print("B. 近 2 年前向可预期区 (trade_date >= 2024-09-01)")
    print("=" * 96)
    m2y = c9["trade_date"] >= "2024-09-01"
    for nm, col in [("single9", "composite_score")]:
        pass
    ic9_2y, _ = spearman(c9["composite_score"][m2y], c9.loc[m2y, "ret60"])
    ic6_2y, _ = spearman(c6["composite_score"][m2y], c6.loc[m2y, "ret60"])
    print(f"  近2年 n={int(m2y.sum())}: single9 {ic9_2y:+.4f} | single6 {ic6_2y:+.4f} | Δ {ic6_2y - ic9_2y:+.4f}")
    res["B_2y"] = {
        "n": int(m2y.sum()),
        "single9": round(ic9_2y, 4),
        "single6": round(ic6_2y, 4),
        "delta": round(ic6_2y - ic9_2y, 4),
    }
    # 近2年复合/牛熊状态/温度分布
    m2y_bull = c9.loc[m2y, "phase"].isin(BULL_PHASES)
    for st, mm in [("全部", m2y), ("牛市段", m2y & m2y_bull), ("非牛市", m2y & ~m2y_bull)]:
        ic9x, _ = spearman(c9["composite_score"][mm], c9.loc[mm, "ret60"])
        ic6x, _ = spearman(c6["composite_score"][mm], c6.loc[mm, "ret60"])
        print(f"    {st} n={int(mm.sum()):4d}: 9 {ic9x:+.4f} | 6 {ic6x:+.4f} | Δ {ic6x - ic9x:+.4f}")
        res.setdefault("B_2y_sub", {})[st] = {
            "n": int(mm.sum()),
            "single9": round(ic9x, 4),
            "single6": round(ic6x, 4),
            "delta": round(ic6x - ic9x, 4),
        }

    # ── 3. composite 分布 / 展示档迁移 ──────────────────────────────────
    print("\n" + "=" * 96)
    print("C. composite 统计与展示档迁移 (全样本)")
    print("=" * 96)
    for nm, c in [("single9", c9), ("single6", c6)]:
        s = c["composite_score"]
        print(
            f"  {nm}: mean={s.mean():.1f} std={s.std():.1f} min={s.min():.0f} max={s.max():.0f} "
            f"| red≥65: {(s >= 65).sum():4d} ({(s >= 65).mean() * 100:.1f}%) | green<40: {(s < 40).sum():4d} ({(s < 40).mean() * 100:.1f}%)"
        )
        res.setdefault("C_dist", {})[nm] = {
            "mean": round(s.mean(), 2),
            "std": round(s.std(), 2),
            "red_n": int((s >= 65).sum()),
            "green_n": int((s < 40).sum()),
        }
    # 逐日差分布
    diff = c6["composite_score"] - c9["composite_score"]
    print(
        f"  逐日差 (single6 - single9): mean={diff.mean():+.2f} std={diff.std():.2f} "
        f"| |Δ|≥5: {(diff.abs() >= 5).sum()} 日 | |Δ|≥10: {(diff.abs() >= 10).sum()} 日"
    )
    res["C_diff"] = {
        "mean": round(float(diff.mean()), 3),
        "std": round(float(diff.std()), 3),
        "n_ge5": int((diff.abs() >= 5).sum()),
        "n_ge10": int((diff.abs() >= 10).sum()),
    }

    # 展示档迁移 (level 变化日)
    mig = c6["level"] != c9["level"]
    print(f"  展示档迁移日: {int(mig.sum())}/{len(c9)} ({(mig.mean() * 100):.1f}%)")
    res["C_level_mig"] = {"n": int(mig.sum()), "pct": round(mig.mean() * 100, 1)}
    if mig.any():
        from collections import Counter

        cnt = Counter(zip(c9.loc[mig, "level"], c6.loc[mig, "level"]))
        print("  迁移明细 (single9→single6): " + ", ".join(f"{a}→{b}:{n}" for (a, b), n in cnt.most_common()))

    with open(OUT, "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(f"\n已保存: {OUT}")


if __name__ == "__main__":
    main()
