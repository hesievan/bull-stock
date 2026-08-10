"""Shared utilities and constants for indicator calculations"""
import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_config_cache = None


def get_config():
    global _config_cache
    if _config_cache is None:
        try:
            from src.config import load_config
            cfg = load_config()
            _config_cache = cfg if cfg is not None else {}
        except Exception as e:
            # 加载失败不要缓存空 dict（否则会永久掩盖配置错误且无法自愈）
            logger.warning("load_config failed, returning empty config this call: %s", e)
            return {}
    return _config_cache


def _pct_rank(series: pd.Series, value: float, scale: str = "0-1") -> float:
    """统一百分位计算 (ISSUE-7 修复: 统一三处不同实现)

    Args:
        series: 历史数据序列
        value: 当前要计算分位的值
        scale: 返回值范围 "0-1" (默认, 0~1) 或 "0-100" (0~100)

    使用含自身的 <= 比较, 确保值一定落在 [0, 1] 或 [0, 100] 内。
    """
    if series.empty or pd.isna(value):
        return np.nan
    clean = series.dropna()
    if clean.empty:
        return np.nan
    pct = (clean <= value).sum() / len(clean)
    if scale == "0-100":
        return pct * 100
    return pct


def _score_with_fallback(score, fallback_reason=""):
    if score is None or np.isnan(score):
        return None
    return max(0, min(100, float(score)))


def _to_numeric(series, errors="coerce", fillna=None):
    """安全转换为数值类型，无效值转为 NaN"""
    s = pd.to_numeric(series, errors=errors)
    return s.fillna(fillna) if fillna is not None else s



