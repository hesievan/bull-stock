"""
输出模块
- 生成 index.json / detail.json / history.json
- 飞书通知文本生成（含防抖逻辑）
- 飞书 Webhook 推送
"""
import json
import os
import tempfile
import logging
import urllib.request
import urllib.error
import numpy as np
from datetime import date, timedelta
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _atomic_write_json(filepath: str, data):
    """原子写入 JSON 文件，先写临时文件再 rename，防止写入中途崩溃"""
    dir_name = os.path.dirname(filepath) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, filepath)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

# 加载配置（惰性)
_config_cache = None
def _get_config():
    global _config_cache
    if _config_cache is None:
        try:
            from src.config import load_config
            _config_cache = load_config()
        except Exception:
            _config_cache = {}
    return _config_cache


def _get_thresholds():
    hl = _get_config().get("heat_levels", {})
    return {
        "red": hl.get("red", {}).get("min", 65),
        "orange": hl.get("orange", {}).get("min", 55),
        "yellow": hl.get("yellow", {}).get("min", 40),
    }


def _get_debounce():
    db = _get_config().get("debounce", {})
    return db.get("red_days", 2), db.get("recover_days", 1)


def _get_notify_rules():
    return _get_config().get("notification", {}).get("notify_rules", [])


def _get_quiet_hours():
    return _get_config().get("notification", {}).get("quiet_hours", {})


# 飞书 Webhook（可选，通过环境变量）
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")

# Bark 推送配置
BARK_KEY = os.environ.get("BARK_KEY", os.environ.get("bark", "").replace("https://api.day.app/", "").rstrip("/"))
BARK_API = f"https://api.day.app/{BARK_KEY}" if BARK_KEY else ""


def get_heat_level(score: float) -> str:
    if score is None:
        return "unknown"
    t = _get_thresholds()
    if score >= t["red"]:
        return "red"
    elif score >= t["orange"]:
        return "orange"
    elif score >= t["yellow"]:
        return "yellow"
    else:
        return "green"


def get_heat_level_cn(score: float) -> str:
    level = get_heat_level(score)
    return {
        "red": "🔴 红色预警",
        "orange": "🟠 橙色关注",
        "yellow": "🟡 黄色警惕",
        "green": "🟢 绿色安全"
    }.get(level, "未知")


def build_data_quality_report(result: Dict) -> Dict:
    """构建数据质量报告

    根据每个维度的子指标可用性 + 新鲜度，输出质量评级和告警。
    """
    indicators = result.get("indicators", {})
    freshness = result.get("freshness_scores", {})
    effective_weights = result.get("effective_weights", {})

    dim_labels = {
        "valuation": "估值", "macro": "宏观", "fund": "资金",
        "sentiment": "情绪", "technical": "技术", "structure": "结构",
    }

    report = {
        "overall_quality": "good",
        "dimensions": {},
        "missing_indicators": [],
        "stale_dimensions": [],
    }

    stale_count = 0
    degraded_count = 0
    for dim, sub_indicators in indicators.items():
        total = len(sub_indicators)
        available = sum(1 for v in sub_indicators.values() if v is not None)
        ratio = available / total if total > 0 else 0

        f = freshness.get(dim, 1.0)
        is_stale = f < 0.8

        status = "ok"
        if ratio < 0.5 or is_stale:
            status = "poor"
            stale_count += 1
        elif ratio < 0.8:
            status = "degraded"
            degraded_count += 1

        report["dimensions"][dim] = {
            "label": dim_labels.get(dim, dim),
            "available": available,
            "total": total,
            "completeness": round(ratio, 2),
            "freshness": round(f, 2),
            "status": status,
        }

        if ratio < 1.0:
            for k, v in sub_indicators.items():
                if v is None:
                    report["missing_indicators"].append(f"{dim}.{k}")

        if is_stale:
            report["stale_dimensions"].append(dim)

    if stale_count > 0:
        report["overall_quality"] = "poor"
    elif degraded_count > 0:
        report["overall_quality"] = "degraded"

    return report


def save_results(result: Dict, output_dir: str = None):
    """保存计算结果到 JSON 文件"""
    output_dir = output_dir or os.path.join(os.path.dirname(__file__), "..", "..", "web", "data")
    os.makedirs(output_dir, exist_ok=True)

    trade_date = result["trade_date"]

    def _round_score(v):
        """统一保留1位小数, None/NaN/Inf则保留None"""
        if v is None:
            return None
        try:
            f = float(v)
            if np.isnan(f) or np.isinf(f):
                return None
            return round(f, 1)
        except (TypeError, ValueError):
            return None

    index_data = {
        "trade_date": trade_date,
        "composite_score": _round_score(result["composite_score"]),
        "level": get_heat_level(result["composite_score"]),
        "dimensions": {
            "valuation": {"score": _round_score(result["dim_valuation"]), "label": "估值"},
            "macro": {"score": _round_score(result.get("dim_macro")), "label": "宏观"},
            "fund": {"score": _round_score(result["dim_fund"]), "label": "资金"},
            "sentiment": {"score": _round_score(result["dim_sentiment"]), "label": "情绪"},
            "technical": {"score": _round_score(result["dim_technical"]), "label": "技术"},
            "structure": {"score": _round_score(result["dim_structure"]), "label": "结构"},
        },
        "effective_weights": result.get("effective_weights", {}),
        "data_quality": build_data_quality_report(result),
        "updated_at": date.today().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # 得分平滑: 3日移动平均
    history_file = os.path.join(output_dir, "history.json")
    if os.path.exists(history_file):
        try:
            with open(history_file, encoding="utf-8") as f:
                history = json.load(f)
            recent_scores = [h["composite_score"] for h in history[-2:]]
            recent_scores.append(result["composite_score"])
            valid_scores = [s for s in recent_scores if s is not None]
            if valid_scores:
                smoothed = sum(valid_scores) / len(valid_scores)
                index_data["composite_score_smoothed"] = round(smoothed, 1)
                index_data["level_smoothed"] = get_heat_level(smoothed)
        except Exception:
            pass

    # 附加板块热度数据
    sectors_file = os.path.join(output_dir, "sectors.json")
    sectors_top5 = []
    if os.path.exists(sectors_file):
        try:
            with open(sectors_file, "r", encoding="utf-8") as sf:
                sectors_all = json.load(sf)
            # 按 composite_score 降序取 TOP5
            sorted_sectors = sorted(
                [s for s in sectors_all if s.get("composite_score") is not None],
                key=lambda x: x["composite_score"],
                reverse=True,
            )
            for s in sorted_sectors[:5]:
                sectors_top5.append({
                    "industry": s.get("sector_name", s.get("industry", "")),
                    "score": s.get("composite_score"),
                    "avg_pct": s.get("avg_pct"),
                    "up_ratio": s.get("up_ratio"),
                    "leader": s.get("leader"),
                })
        except Exception as e:
            logger.warning("Failed to load sectors.json: %s", e)

    if sectors_top5:
        index_data["sectors_top5"] = sectors_top5
    _atomic_write_json(os.path.join(output_dir, "index.json"), index_data)

    detail_data = {**index_data, "indicators": result["indicators"]}
    _atomic_write_json(os.path.join(output_dir, "detail.json"), detail_data)

    # 写入数据库
    try:
        from src.data.database import save_heat_index_to_db
        result["composite_score_smoothed"] = index_data.get("composite_score_smoothed")
        save_heat_index_to_db(result)
    except Exception as e:
        logger.warning("Failed to save heat index to DB: %s", e)

    # 历史数据（去重追加）
    history_file = os.path.join(output_dir, "history.json")
    history = []
    if os.path.exists(history_file):
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError, OSError) as e:
            logger.warning("Failed to load history.json: %s", e)
            history = []
    history = [h for h in history if h.get("trade_date") != trade_date]
    history.append(index_data)
    history.sort(key=lambda x: x["trade_date"])
    _atomic_write_json(history_file, history)

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

    # 统计连续处于当前状态的天数（不包括最后一条历史记录）
    # 最后一条被视为"当天"状态，从倒数第二条往前数
    consecutive = 1
    for h in reversed(history[:-1]):
        if h.get("trade_date", "") >= current_date:
            continue
        if h.get("level") == current_level:
            consecutive += 1
        else:
            break

    # 找到倒数第二条历史记录（最近的"前一天"）
    # 注意: history[:-1] 排除了最后一条，从剩余中找最近的
    prev_day = None
    for h in reversed(history[:-1]):
        if h.get("trade_date", "") < current_date:
            prev_day = h
            break

    if current_level == "red":
        if prev_day is None or prev_day.get("level") != "red":
            red_days, _ = _get_debounce()
            if consecutive >= red_days:
                return "enter_red", consecutive
            else:
                return "pending_red", consecutive
        else:
            return "in_red", consecutive

    elif current_level in ("yellow", "green"):
        if prev_day and prev_day.get("level") == "red":
            _, recover_days = _get_debounce()
            if consecutive >= recover_days:
                return "recover", consecutive
            else:
                return "pending_recover", consecutive
        else:
            return "stable", consecutive

    return "stable", consecutive


def _should_notify(score: float, level: str, history: list, trade_date: str) -> bool:
    """根据配置的规则判断是否应该推送通知"""
    rules = _get_notify_rules()
    if not rules:
        return True

    # 静默时段检查
    qh = _get_quiet_hours()
    if qh.get("enabled"):
        from datetime import datetime
        now = datetime.now()
        start = qh.get("start", "22:00")
        end = qh.get("end", "08:00")
        start_h, start_m = map(int, start.split(":"))
        end_h, end_m = map(int, end.split(":"))
        current_minutes = now.hour * 60 + now.minute
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m
        if start_minutes > end_minutes:
            if current_minutes >= start_minutes or current_minutes < end_minutes:
                return False
        else:
            if start_minutes <= current_minutes < end_minutes:
                return False

    # 计算变化量
    prev_score = None
    prev_level = None
    if history:
        for h in reversed(history):
            if h.get("trade_date", "") < trade_date:
                prev_score = h.get("composite_score")
                prev_level = h.get("level")
                break
    change = score - prev_score if prev_score is not None else 0
    level_change = f"{prev_level}->{level}" if prev_level else ""

    # 评估每条规则
    for rule in rules:
        cond = rule.get("condition", "")
        if "score >=" in cond:
            try:
                threshold = float(cond.split("score >=")[1].split("AND")[0].strip())
                if score >= threshold:
                    return True
            except (ValueError, IndexError):
                pass
        elif "score <=" in cond:
            try:
                threshold = float(cond.split("score <=")[1].split("AND")[0].strip())
                if score <= threshold:
                    return True
            except (ValueError, IndexError):
                pass
        if "change >" in cond:
            try:
                threshold = float(cond.split("change >")[1].split("AND")[0].strip())
                if change > threshold:
                    return True
            except (ValueError, IndexError):
                pass
        elif "change <" in cond:
            try:
                threshold = float(cond.split("change <")[1].split("AND")[0].strip())
                if change < threshold:
                    return True
            except (ValueError, IndexError):
                pass
        if "level_change:" in cond:
            target = cond.split("level_change:")[1].strip()
            if level_change == target:
                return True

    return False


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

    # 使用自定义规则判断是否推送
    if not _should_notify(score, level, history or [], trade_date):
        return None

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
        f"综合热度：{get_heat_level_cn(score)} {score:.0f}",
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

    # 板块热度 TOP5
    sectors_top5 = result.get("sectors_top5", [])
    if sectors_top5:
        sec_lines = []
        for i, s in enumerate(sectors_top5, 1):
            name = s.get("sector_name", s.get("industry", ""))
            sc = s.get("score", 0)
            leader = s.get("leader", {})
            ldr_str = ""
            if leader:
                ldr_str = f"  龙头:{leader.get('code','')} +{leader.get('pct',0):.1f}%"
            sec_lines.append(f"  {i}. {name}  {sc:.0f}分{ldr_str}")
        lines.extend([f"", f"🔥 板块热度 TOP5：", *sec_lines])
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

    # 数据质量告警
    dq = result.get("data_quality") or {}
    if dq and dq.get("overall_quality") != "good":
        quality_lines = [f"", f"📊 数据质量 ({dq['overall_quality']})："]
        for dim_name, dim_info in dq.get("dimensions", {}).items():
            icon = "✅" if dim_info["status"] == "ok" else "⚠️" if dim_info["status"] == "degraded" else "❌"
            quality_lines.append(
                f"  {icon} {dim_info['label']}: {dim_info['available']}/{dim_info['total']} "
                f"(新鲜度 {dim_info['freshness']:.0%})"
            )
        if dq.get("missing_indicators"):
            quality_lines.append(f"  缺失: {', '.join(dq['missing_indicators'][:5])}")
        quality_lines.append("  提示: 评分可信度降低，建议关注数据恢复")
        lines.extend(quality_lines)

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
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        logger.error("Feishu webhook send error: %s", e)
        return False


def send_bark(title: str, body: str, level: str = "active", group: str = "HeatIndex") -> bool:
    """通过 Bark 推送通知到 iPhone"""
    if not BARK_API:
        logger.warning("Bark not configured (BARK_KEY env var missing)")
        return False
    payload = json.dumps({
        "title": title,
        "body": body,
        "group": group,
        "level": level,
    }).encode("utf-8")
    req = urllib.request.Request(
        BARK_API, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        if result.get("code") == 200:
            logger.info("Bark notification sent: %s", title)
            return True
        else:
            logger.error("Bark failed: %s", result)
            return False
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        logger.error("Bark send error: %s", e)
        return False
