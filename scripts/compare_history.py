#!/usr/bin/env python3
"""
历史对比工具 — 对比当前热度与历史关键节点

用法:
  python scripts/compare_history.py                    # 对比最近一次牛/熊市
  python scripts/compare_history.py 2015-06-12         # 对比指定日期
  python scripts/compare_history.py --list              # 列出所有历史记录
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "web", "data")

# 历史关键节点
KEY_NODES = {
    "2015-06-12": "2015牛市顶",
    "2015-08-26": "2015股灾底",
    "2016-01-28": "2016熔断底",
    "2018-10-19": "2018政策底",
    "2018-12-28": "2018熊市底",
    "2019-04-19": "2019小牛市顶",
    "2020-03-23": "2020疫情底",
    "2020-07-10": "2020牛市启动",
    "2021-02-18": "2021核心资产顶",
    "2022-04-27": "2022上海底",
    "2022-10-31": "2022估值底",
    "2024-02-05": "2024市场底",
    "2024-10-08": "2024脉冲顶",
}

DIM_LABELS = {
    "valuation": "估值",
    "fund": "资金",
    "sentiment": "情绪",
    "technical": "技术",
    "structure": "结构",
}


def load_history():
    path = os.path.join(DATA_DIR, "history.json")
    if not os.path.exists(path):
        print("ERROR: history.json not found")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_current():
    path = os.path.join(DATA_DIR, "index.json")
    if not os.path.exists(path):
        print("ERROR: index.json not found")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_closest(history, target_date):
    for h in history:
        if h["trade_date"] == target_date:
            return h
    closest = None
    for h in history:
        if h["trade_date"] <= target_date:
            closest = h
    return closest


def compare(current, target, target_date):
    print(f"\n{'='*60}")
    print(f"历史对比: 当前 vs {KEY_NODES.get(target_date, target_date)}")
    print(f"{'='*60}\n")

    c_score = current.get("composite_score")
    t_score = target.get("composite_score")
    print(f"{'指标':<12} {'当前':>8} {'目标':>8} {'差异':>8}")
    print(f"{'-'*40}")
    print(f"{'综合热度':<12} {c_score:>8.1f} {t_score:>8.1f} {c_score-t_score:>+8.1f}")

    c_dims = current.get("dimensions", {})
    t_dims = target.get("dimensions", {})
    for key, label in DIM_LABELS.items():
        c_val = c_dims.get(key, {})
        t_val = t_dims.get(key, {})
        c_score = c_val.get("score", 0) if isinstance(c_val, dict) else (c_val or 0)
        t_score = t_val.get("score", 0) if isinstance(t_val, dict) else (t_val or 0)
        print(f"{label:<12} {c_score:>8.1f} {t_score:>8.1f} {c_score-t_score:>+8.1f}")

    print(f"\n当前日期: {current.get('trade_date')}")
    print(f"对比日期: {target.get('trade_date')} ({KEY_NODES.get(target_date, '')})")

    # 分析差异
    print(f"\n{'='*60}")
    print("差异分析:")
    print(f"{'='*60}")

    diff = c_score - t_score
    if abs(diff) < 5:
        print(f"  综合热度与{KEY_NODES.get(target_date, target_date)}接近 (差异{diff:+.1f})")
    elif diff > 0:
        print(f"  当前热度高于{KEY_NODES.get(target_date, target_date)} ({diff:+.1f})")
    else:
        print(f"  当前热度低于{KEY_NODES.get(target_date, target_date)} ({diff:+.1f})")

    for key, label in DIM_LABELS.items():
        c_val = c_dims.get(key, {})
        t_val = t_dims.get(key, {})
        c_score = c_val.get("score", 0) if isinstance(c_val, dict) else (c_val or 0)
        t_score = t_val.get("score", 0) if isinstance(t_val, dict) else (t_val or 0)
        d = c_score - t_score
        if abs(d) > 10:
            direction = "高于" if d > 0 else "低于"
            print(f"  {label}维度{direction}目标{abs(d):.0f}分")


def main():
    history = load_history()
    current = load_current()

    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        print("\n历史关键节点:")
        print(f"{'日期':<12} {'事件':<20} {'热度':>8}")
        print("-" * 44)
        for h in history:
            td = h["trade_date"]
            if td in KEY_NODES:
                score = h.get("composite_score", 0)
                print(f"{td:<12} {KEY_NODES[td]:<20} {score:>8.1f}")
        return

    if len(sys.argv) > 1:
        target_date = sys.argv[1]
    else:
        target_date = "2015-06-12"

    target = find_closest(history, target_date)
    if not target:
        print(f"ERROR: No data found near {target_date}")
        sys.exit(1)

    compare(current, target, target_date)


if __name__ == "__main__":
    main()
