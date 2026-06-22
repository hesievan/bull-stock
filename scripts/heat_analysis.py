#!/usr/bin/env python3
"""
热度分析工具 — 归因、异常检测、趋势预测

用法:
  python scripts/heat_analysis.py                    # 今日分析
  python scripts/heat_analysis.py --attr             # 热度变化归因
  python scripts/heat_analysis.py --anomaly          # 异常检测
  python scripts/heat_analysis.py --predict          # 趋势预测
  python scripts/heat_analysis.py --all              # 全部分析
"""
import sys
import os
import json
from datetime import datetime
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "web", "data")

DIM_LABELS = {
    "valuation": "估值",
    "fund": "资金",
    "sentiment": "情绪",
    "technical": "技术",
    "structure": "结构",
}

KEY_NODES = {
    "2015-06-12": ("2015牛市顶", 72.6),
    "2018-12-28": ("2018熊市底", 17.2),
    "2020-07-10": ("2020牛市启动", 58.9),
    "2021-02-18": ("2021核心资产顶", 57.7),
    "2024-02-05": ("2024市场底", 10.8),
    "2024-10-08": ("2024脉冲顶", 53.5),
}


def load_json(filename):
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_history():
    data = load_json("history.json")
    return data if data else []


def load_current():
    return load_json("index.json")


def explain_change(current: Dict, history: List) -> str:
    """解释热度变化原因"""
    if not current or not history:
        return "数据不足，无法分析"

    today_score = current.get("composite_score")
    today_date = current.get("trade_date")
    today_dims = current.get("dimensions", {})

    prev = None
    for h in reversed(history):
        if h.get("trade_date", "") < today_date:
            prev = h
            break

    if not prev:
        return "无历史数据对比"

    prev_score = prev.get("composite_score")
    prev_dims = prev.get("dimensions", {})

    change = today_score - prev_score if today_score and prev_score else 0
    direction = "↑" if change > 0 else "↓" if change < 0 else "→"

    lines = [
        f"📊 热度变化归因 · {today_date}",
        "",
        f"今日热度: {today_score:.1f} ({direction} {abs(change):.1f})",
        f"昨日热度: {prev_score:.1f}",
        "",
        "维度变化:",
    ]

    changes = []
    for key, label in DIM_LABELS.items():
        t_val = today_dims.get(key, {}).get("score", 0) or 0
        p_val = prev_dims.get(key, {}).get("score", 0) or 0
        diff = t_val - p_val
        if abs(diff) > 2:
            d = "↑" if diff > 0 else "↓"
            changes.append((label, diff, d))
            lines.append(f"  {label}: {p_val:.1f} → {t_val:.1f} {d} ({diff:+.1f})")

    if not changes:
        lines.append("  各维度变化不大")

    # 找出主要驱动因素
    if changes:
        changes.sort(key=lambda x: abs(x[1]), reverse=True)
        top = changes[0]
        lines.extend([
            "",
            f"主要驱动: {top[0]}维度{top[2]}{abs(top[1]):.1f}分",
        ])

    return "\n".join(lines)


def detect_anomalies(current: Dict, history: List) -> str:
    """异常检测"""
    if not current or not history:
        return "数据不足，无法检测"

    today_score = current.get("composite_score")
    today_dims = current.get("dimensions", {})
    today_date = current.get("trade_date")

    lines = [
        f"🔍 异常检测 · {today_date}",
        "",
    ]

    anomalies = []

    # 1. 单日跳变检测
    prev = None
    for h in reversed(history):
        if h.get("trade_date", "") < today_date:
            prev = h
            break

    if prev:
        prev_score = prev.get("composite_score")
        if prev_score and today_score:
            change = today_score - prev_score
            if abs(change) > 5:
                anomalies.append(f"⚠️ 单日跳变 {change:+.1f} 分")

    # 2. 多维度同时高企
    high_dims = []
    for key, label in DIM_LABELS.items():
        val = today_dims.get(key, {}).get("score", 0) or 0
        if val > 70:
            high_dims.append(label)
    if len(high_dims) >= 2:
        anomalies.append(f"⚠️ 多维度同时偏高: {', '.join(high_dims)}")

    # 3. 历史分位极端值
    indicators = current.get("indicators", {})
    extreme = []
    for dim_key, dim_data in indicators.items():
        if isinstance(dim_data, dict):
            for ind_key, ind_val in dim_data.items():
                if isinstance(ind_val, (int, float)) and ind_val > 95:
                    extreme.append(f"{ind_key}={ind_val:.0f}%")
    if extreme:
        anomalies.append(f"⚠️ 极端高值: {', '.join(extreme[:3])}")

    # 4. 与历史关键节点对比
    for node_date, (node_name, node_score) in KEY_NODES.items():
        if today_score and abs(today_score - node_score) < 5:
            anomalies.append(f"📌 接近{node_name}水平 ({node_score:.0f})")

    if anomalies:
        lines.extend(anomalies)
    else:
        lines.append("✅ 未检测到异常")

    # 统计信息
    all_scores = [h.get("composite_score", 0) for h in history if h.get("composite_score") is not None]
    if all_scores:
        avg = sum(all_scores) / len(all_scores)
        std = (sum((s - avg) ** 2 for s in all_scores) / len(all_scores)) ** 0.5
        lines.extend([
            "",
            "统计信息:",
            f"  历史均值: {avg:.1f}",
            f"  历史标准差: {std:.1f}",
            f"  当前偏离: {(today_score - avg) / std:.1f}σ" if std > 0 else "",
        ])

    return "\n".join(lines)


def predict_trend(history: List) -> str:
    """趋势预测"""
    if not history or len(history) < 30:
        return "历史数据不足30天，无法预测"

    recent = history[-60:] if len(history) >= 60 else history
    scores = [h.get("composite_score", 0) for h in recent if h.get("composite_score") is not None]

    if len(scores) < 10:
        return "有效数据不足"

    # 简单移动平均
    ma5 = sum(scores[-5:]) / 5
    ma10 = sum(scores[-10:]) / 10
    ma20 = sum(scores[-20:]) / 20 if len(scores) >= 20 else ma10

    # 趋势判断
    if ma5 > ma10 > ma20:
        trend = "上升趋势"
        trend_emoji = "📈"
    elif ma5 < ma10 < ma20:
        trend = "下降趋势"
        trend_emoji = "📉"
    else:
        trend = "震荡整理"
        trend_emoji = "➡️"

    # 动量
    momentum = scores[-1] - scores[-5] if len(scores) >= 5 else 0

    # 波动率
    if len(scores) >= 20:
        returns = [scores[i] - scores[i-1] for i in range(1, len(scores[-20:]))]
        volatility = (sum(r**2 for r in returns) / len(returns)) ** 0.5
    else:
        volatility = 0

    lines = [
        "🔮 趋势预测",
        "",
        f"当前趋势: {trend_emoji} {trend}",
        f"当前热度: {scores[-1]:.1f}",
        "",
        "均线系统:",
        f"  MA5:  {ma5:.1f}",
        f"  MA10: {ma10:.1f}",
        f"  MA20: {ma20:.1f}",
        "",
        f"动量: {momentum:+.1f} (近5日变化)",
        f"波动率: {volatility:.1f}",
    ]

    # 简单预测
    if trend == "上升趋势" and momentum > 0:
        predict = min(100, scores[-1] + momentum * 0.5)
        lines.extend([
            "",
            f"预测: 未来5日可能升至 {predict:.0f}",
            "风险: 若突破65需警惕",
        ])
    elif trend == "下降趋势" and momentum < 0:
        predict = max(0, scores[-1] + momentum * 0.5)
        lines.extend([
            "",
            f"预测: 未来5日可能降至 {predict:.0f}",
            "机会: 若跌破40可关注",
        ])
    else:
        lines.extend([
            "",
            "预测: 短期维持震荡",
            "关注: 等待方向选择",
        ])

    # 相似历史模式
    lines.append("")
    lines.append("相似历史模式:")
    for node_date, (node_name, node_score) in KEY_NODES.items():
        if abs(scores[-1] - node_score) < 10:
            lines.append(f"  · 当前类似{node_name} ({node_score:.0f})")

    return "\n".join(lines)


def full_analysis():
    """完整分析"""
    current = load_current()
    history = load_history()

    output = []
    output.append("=" * 60)
    output.append("A股牛市热度指数 — 智能分析报告")
    output.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    output.append("=" * 60)

    output.append("")
    output.append(explain_change(current, history))
    output.append("")
    output.append("-" * 60)
    output.append("")
    output.append(detect_anomalies(current, history))
    output.append("")
    output.append("-" * 60)
    output.append("")
    output.append(predict_trend(history))

    return "\n".join(output)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Heat index analysis tools")
    parser.add_argument("--attr", action="store_true", help="Show change attribution")
    parser.add_argument("--anomaly", action="store_true", help="Detect anomalies")
    parser.add_argument("--predict", action="store_true", help="Predict trend")
    parser.add_argument("--all", action="store_true", help="Full analysis")
    args = parser.parse_args()

    if args.all or (not args.attr and not args.anomaly and not args.predict):
        print(full_analysis())
    else:
        current = load_current()
        history = load_history()
        if args.attr:
            print(explain_change(current, history))
        if args.anomaly:
            print(detect_anomalies(current, history))
        if args.predict:
            print(predict_trend(history))


if __name__ == "__main__":
    main()
