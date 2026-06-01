"""
输出模块
- 生成 index.json / detail.json / history.json
- 飞书通知文本生成（含防抖逻辑）
- 飞书 Webhook 推送
"""
import json
import os
import logging
import urllib.request
import urllib.error
from datetime import date, timedelta
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# 飞书 Webhook（可选，优先级低于 copaw channel 推送）
FEISHU_WEBHOOK = os.environ.get(
    "FEISHU_WEBHOOK",
    "https://www.feishu.cn/flow/api/trigger-webhook/18d944beda7772e52c8e326e34b40da0"
)

# 防抖配置: 连续 N 天才升级状态通知
DEBOUNCE_RED_DAYS = 2       # 红区连续2天才发"进入红区"通知
DEBOUNCE_RECOVER_DAYS = 1   # 恢复1天后发"脱离红区"通知


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

    def _round_score(v):
        """统一保留1位小数, None则保留None"""
        return round(float(v), 1) if v is not None else None

    index_data = {
        "trade_date": trade_date,
        "composite_score": _round_score(result["composite_score"]),
        "level": get_heat_level(result["composite_score"]),
        "dimensions": {
            "valuation": {"score": _round_score(result["dim_valuation"]), "label": "估值"},
            "fund": {"score": _round_score(result["dim_fund"]), "label": "资金"},
            "sentiment": {"score": _round_score(result["dim_sentiment"]), "label": "情绪"},
            "technical": {"score": _round_score(result["dim_technical"]), "label": "技术"},
            "structure": {"score": _round_score(result["dim_structure"]), "label": "结构"},
        },
        "updated_at": date.today().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(os.path.join(output_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, indent=2)

    detail_data = {**index_data, "indicators": result["indicators"]}
    with open(os.path.join(output_dir, "detail.json"), "w", encoding="utf-8") as f:
        json.dump(detail_data, f, ensure_ascii=False, indent=2)

    # 历史数据（去重追加）
    history_file = os.path.join(output_dir, "history.json")
    history = []
    if os.path.exists(history_file):
        with open(history_file, "r", encoding="utf-8") as f:
            try:
                history = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                history = []
    history = [h for h in history if h.get("trade_date") != trade_date]
    history.append(index_data)
    history.sort(key=lambda x: x["trade_date"])
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    logger.info("Results saved: score=%.1f level=%s", result["composite_score"], index_data["level"])
    return index_data


def analyze_state(history: list, current_level: str, current_date: str) -> Tuple[str, int]:
    """
    分析热度状态变化（含防抖）
    返回: (event_type, consecutive_days)
    event_type: 'enter_red' | 'in_red' | 'recover' | 'stable'
    """
    if not history:
        return "stable", 0

    # 统计连续处于当前状态的天数
    consecutive = 1
    for h in reversed(history[:-1]):  # 从倒数第二天往前
        if h.get("trade_date", "") >= current_date:
            continue
        if h.get("level") == current_level:
            consecutive += 1
        else:
            break

    # 判断事件类型
    prev_day = None
    for h in reversed(history[:-1]):
        if h.get("trade_date", "") < current_date:
            prev_day = h
            break

    if current_level == "red":
        if prev_day is None or prev_day.get("level") != "red":
            # 新进入红区
            if consecutive >= DEBOUNCE_RED_DAYS:
                return "enter_red", consecutive
            else:
                return "pending_red", consecutive  # 防抖中，暂不通知
        else:
            return "in_red", consecutive

    elif current_level in ("yellow", "green"):
        if prev_day and prev_day.get("level") == "red":
            # 脱离红区
            if consecutive >= DEBOUNCE_RECOVER_DAYS:
                return "recover", consecutive
            else:
                return "pending_recover", consecutive
        else:
            return "stable", consecutive

    return "stable", consecutive


def build_feishu_notification(result: Dict, history: list = None) -> Optional[str]:
    """
    构建飞书通知文本（含防抖逻辑）
    返回 None 表示无需通知（防抖期间或不重要变化）
    """
    score = result["composite_score"]
    trade_date = result["trade_date"]
    level = get_heat_level(score)
    level_cn = get_heat_level_cn(score)

    if score is None:
        return f"⚠️ A股热度指数 · {trade_date}\n计算失败，请检查日志。"

    event, consecutive = analyze_state(history or [], level, trade_date)

    # 维度拆解行
    dim_lines = []
    dim_labels = {"valuation": "估值", "fund": "资金", "sentiment": "情绪", "technical": "技术", "structure": "结构"}
    for key, label in dim_labels.items():
        d = result.get("dim_" + key)
        if d is not None:
            bar = "█" * int(d / 10) + "░" * (10 - int(d / 10))
            dim_lines.append(f"  {label}  {bar}  {d:.0f}")
        else:
            dim_lines.append(f"  {label}  -- 数据暂缺")

    sub_indicators = result.get("indicators", {})

    lines = [
        f"📊 A股牛市热度指数 · {trade_date}",
        f"",
        f"综合热度：{'🔴' if level=='red' else '🟡' if level=='yellow' else '🟢'} {score:.0f}  {level_cn}",
    ]

    if event == "enter_red":
        lines.append(f"⚠️ 连续 {consecutive} 天进入红色区间！请注意风险控制。")
    elif event == "in_red":
        lines.append(f"🔴 持续红色第 {consecutive} 天，保持警惕。")
    elif event == "recover":
        lines.append(f"📉 脱离红区，连续 {consecutive} 天回落。")
        lines.append(f"此前红色区间持续 {consecutive} 天。")

    lines.extend([
        f"",
        f"维度拆解：",
        *dim_lines,
    ])

    # 关键子指标亮点
    highlights = []
    vi = sub_indicators.get("valuation", {})
    si = sub_indicators.get("sentiment", {})
    ti = sub_indicators.get("technical", {})
    fi = sub_indicators.get("fund", {})

    if vi.get("PE_percentile") is not None and vi["PE_percentile"] > 80:
        highlights.append(f"PE分位 {vi['PE_percentile']:.0f}% (偏高)")
    if vi.get("below_net_rate") is not None and vi["below_net_rate"] < 20:
        highlights.append(f"破净率极低")
    if si.get("limit_up_ratio") is not None and si["limit_up_ratio"] > 80:
        highlights.append(f"涨停占比偏高")
    if si.get("volatility") is not None and si["volatility"] > 80:
        highlights.append(f"波动率偏高")
    if ti.get("deviation_ma250") is not None and ti["deviation_ma250"] > 80:
        highlights.append(f"均线偏离度大")
    if fi.get("margin_ratio") is not None and fi["margin_ratio"] > 80:
        highlights.append(f"融资买入占比高")

    if highlights:
        lines.extend([f"", f"⚠️ 关注指标：", *[f"  · {h}" for h in highlights]])

    lines.extend([
        f"",
        f"不构成投资建议，仅供参考。",
        f"详情：web/data/detail.json",
    ])

    msg = "\n".join(lines)

    # 防抖期间不发通知
    if event in ("pending_red", "pending_recover", "stable"):
        return None  # 不需要推送

    return msg


def send_feishu_webhook(message: str, webhook_url: str = None) -> bool:
    """通过飞书 Webhook 发送消息（纯文本）"""
    url = webhook_url or FEISHU_WEBHOOK
    if not url:
        logger.warning("No Feishu webhook URL configured")
        return False

    payload = json.dumps({"msg_type": "text", "content": {"text": message}}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        if result.get("code", 0) == 0:
            logger.info("Feishu webhook sent successfully")
            return True
        else:
            logger.error("Feishu webhook failed: %s", result)
            return False
    except (urllib.error.URLError, OSError) as e:
        logger.error("Feishu webhook send error: %s", e)
        return False
