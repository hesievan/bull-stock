"""
数据新鲜度管理器 — 陈旧指标自动权重衰减

月频数据（M2、M1-M2）线性插值到日频会扭曲当日评分。
当指标的实际数据日期显著早于计算日期时，自动衰减其权重，
并将权重重新分配给新鲜度更高的维度。
"""
import logging
import numpy as np

logger = logging.getLogger(__name__)

# 各指标的最大允许延迟（交易日数）
# 超过此阈值后开始指数衰减
MAX_LAG_DAYS = {
    "valuation": {
        "valuation_composite": 3,
        "below_net_rate": 3,
        "erp": 3,
    },
    "macro": {
        "m1m2_scissors": 22,
        "m2_yoy": 22,
    },
    "fund": {
        "northbound_cumflow": 3,
        "margin_ratio": 3,
    },
    "sentiment": {
        "turnover": 3,
        "up_down_ratio": 3,
        "limit_up_ratio": 3,
        "limit_ratio": 3,
        "qvix": 5,
    },
    "technical": {
        "ma_alignment": 3,
        "deviation_ma250": 3,
        "momentum_60d": 3,
        "momentum_20d": 3,
        "momentum_120d": 3,
    },
    "structure": {
        "new_high_ratio": 3,
        "ah_premium_index": 3,
    },
}

# 维度基权重
BASE_WEIGHTS = {
    "valuation": 0.25,
    "macro": 0.15,
    "fund": 0.15,
    "sentiment": 0.20,
    "technical": 0.10,
    "structure": 0.15,
}

DECAY_RATE = 0.15


def _compute_freshness(actual_date: str, target_date: str, max_lag: int) -> float:
    """计算单个指标的新鲜度 (0-1)"""
    from datetime import date
    if not actual_date:
        return 0.0
    lag = (date.fromisoformat(target_date) - date.fromisoformat(actual_date)).days
    if lag <= max_lag:
        return 1.0
    # 指数衰减
    decay = np.exp(-DECAY_RATE * (lag - max_lag))
    return max(0.0, decay)


def get_dimension_freshness(
    indicators: dict,
    target_date: str,
) -> dict:
    """计算每个维度的平均新鲜度 (0-1)"""
    dim_scores = {}
    for dim, sub_indicators in indicators.items():
        scores = []
        for name, value_info in sub_indicators.items():
            if isinstance(value_info, dict):
                actual = value_info.get("actual_date", target_date)
                max_lag = MAX_LAG_DAYS.get(dim, {}).get(name, 3)
            else:
                actual = target_date
                max_lag = MAX_LAG_DAYS.get(dim, {}).get(name, 3)
            scores.append(_compute_freshness(actual, target_date, max_lag))
        dim_scores[dim] = np.mean(scores) if scores else 0.0
    return dim_scores


def get_effective_weights(
    indicators: dict,
    target_date: str,
) -> tuple[dict, dict]:
    """根据新鲜度计算实际生效权重

    返回:
      (effective_weights, freshness_scores)
    """
    freshness = get_dimension_freshness(indicators, target_date)
    effective = {}
    for dim, base_w in BASE_WEIGHTS.items():
        f = freshness.get(dim, 1.0)
        effective[dim] = base_w * f

    total_w = sum(effective.values())
    if total_w > 0:
        for dim in effective:
            effective[dim] /= total_w
    else:
        effective = dict(BASE_WEIGHTS)

    return effective, freshness
