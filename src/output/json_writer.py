"""
输出模块
- 生成 index.json 和 detail.json
- 生成飞书通知消息
"""
import json
import os
import logging
from datetime import date
from typing import Dict

logger = logging.getLogger(__name__)


def get_heat_level(score: float) -> str:
    if score is None:
        return "unknown"
    if score >= 70:
        return "red"
    elif score >= 40:
        return "yellow"
    else:
        return "green"


def get_heat_level_cn(score: float) -> str:
    level = get_heat_level(score)
    return {"red": "🔴 红色预警", "yellow": "🟡 黄色警惕", "green": "🟢 绿色安全"}.get(level, "未知")


def save_results(result: Dict, output_dir: str = None):
    """保存计算结果到 JSON 文件"""
    output_dir = output_dir or os.path.join(os.path.dirname(__file__), "..", "..", "web", "data")
    os.makedirs(output_dir, exist_ok=True)

    trade_date = result["trade_date"]
    date_compact = trade_date.replace("-", "")

    # index.json（最新指数，用于首页展示）
    index_data = {
        "trade_date": trade_date,
        "composite_score": result["composite_score"],
        "level": get_heat_level(result["composite_score"]),
        "dimensions": {
            "valuation": {"score": result["dim_valuation"], "label": "估值"},
            "fund": {"score": result["dim_fund"], "label": "资金"},
            "sentiment": {"score": result["dim_sentiment"], "label": "情绪"},
            "technical": {"score": result["dim_technical"], "label": "技术"},
            "structure": {"score": result["dim_structure"], "label": "结构"},
        },
        "updated_at": date.today().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(os.path.join(output_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)

    # detail.json（包含所有子指标，用于拆解页）
    detail_data = {
        **index_data,
        "indicators": result["indicators"],
    }
    with open(os.path.join(output_dir, "detail.json"), "w", encoding="utf-8") as f:
        json.dump(detail_data, f, ensure_ascii=False, indent=2)

    # 历史数据（追加模式）
    history_file = os.path.join(output_dir, "history.json")
    history = []
    if os.path.exists(history_file):
        with open(history_file, "r", encoding="utf-8") as f:
            history = json.load(f)
    # 去重后追加
    history = [h for h in history if h.get("trade_date") != trade_date]
    history.append(index_data)
    history.sort(key=lambda x: x["trade_date"])
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    logger.info("Results saved to %s", output_dir)
    return index_data


def build_feishu_notification(result: Dict, red_days: int = 0) -> str:
    """构建飞书通知文本"""
    score = result["composite_score"]
    level = get_heat_level(score)
    level_cn = get_heat_level_cn(score)

    if level == "red":
        emoji = "🚨"
        action = "请注意风险控制"
    elif level == "yellow":
        emoji = "⚠️"
        action = "建议保持警惕"
    else:
        emoji = "✅"
        action = "市场状态良好"

    lines = [
        f"{emoji} A股牛市热度指数 · {result['trade_date']}",
        f"",
        f"综合热度：{score}  {level_cn}",
    ]
    if level == "red" and red_days > 0:
        lines.append(f"已进入红色区间 {red_days} 天")

    lines.extend([
        f"",
        f"维度拆解：",
        f"  估值：{result['dim_valuation']}",
        f"  资金：{result['dim_fund']}",
        f"  情绪：{result['dim_sentiment']}",
        f"  技术：{result['dim_technical']}",
        f"  结构：{result['dim_structure']}",
        f"",
        f"{action}",
        f"不构成投资建议，仅供参考。",
    ])
    return "\n".join(lines)
