#!/usr/bin/env python3
"""
快速填充历史数据的宏观维度 — 直接从daily_macro表计算

用法:
  python scripts/fill_macro_history.py
"""
import sys
import os
import json
import sqlite3
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "web", "data")
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "heat_index.db")


def pct_rank(series, value):
    clean = [x for x in series if x is not None and not np.isnan(x)]
    if not clean:
        return np.nan
    return sum(1 for x in clean if x < value) / len(clean)


def calc_macro_score(scissors, m2_yoy, hist_scissors, hist_m2):
    """计算宏观维度得分"""
    scores = []
    if scissors is not None and hist_scissors:
        s = pct_rank(hist_scissors, scissors) * 100
        scores.append(s)
    if m2_yoy is not None and hist_m2:
        s = pct_rank(hist_m2, m2_yoy) * 100
        scores.append(s)
    if scores:
        return round(sum(scores) / len(scores), 1)
    return None


def main():
    history_file = os.path.join(DATA_DIR, "history.json")
    with open(history_file, encoding="utf-8") as f:
        history = json.load(f)

    # 加载daily_macro数据
    conn = sqlite3.connect(DB_PATH)
    macro_data = {}
    rows = conn.execute("SELECT trade_date, scissors, m2_yoy FROM daily_macro ORDER BY trade_date").fetchall()
    for r in rows:
        macro_data[r[0]] = {"scissors": r[1], "m2_yoy": r[2]}
    conn.close()

    print(f"daily_macro数据: {len(macro_data)}条")
    print(f"history数据: {len(history)}条")

    # 准备历史序列
    all_scissors = [v["scissors"] for v in macro_data.values() if v["scissors"] is not None]
    all_m2 = [v["m2_yoy"] for v in macro_data.values() if v["m2_yoy"] is not None]

    updated = 0
    skipped = 0

    for item in history:
        td = item["trade_date"]
        dims = item.get("dimensions", {})

        # 如果已经有macro数据，跳过
        if "macro" in dims:
            skipped += 1
            continue

        # 查找最近的macro数据
        macro = None
        for d in sorted(macro_data.keys(), reverse=True):
            if d <= td:
                macro = macro_data[d]
                break

        if macro is None:
            dims["macro"] = None
            continue

        # 计算宏观得分
        score = calc_macro_score(
            macro["scissors"], macro["m2_yoy"],
            all_scissors, all_m2
        )
        dims["macro"] = score
        updated += 1

    # 保存
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    print(f"更新: {updated}条, 跳过: {skipped}条")
    print(f"已保存到: {history_file}")


if __name__ == "__main__":
    main()
