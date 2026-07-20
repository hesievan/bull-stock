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


def get_weights():
    return get_config().get("dimension_weights", {})


def get_divergence():
    return get_config().get("divergence_penalty", {})


def get_lookback_years():
    return get_config().get("data", {}).get("lookback_years", 10)


def _pct_rank(series: pd.Series, value: float) -> float:
    """历史分位 (0-1)"""
    if series.empty or pd.isna(value):
        return np.nan
    clean = series.dropna()
    if clean.empty:
        return np.nan
    return (clean <= value).sum() / len(clean)


def _score_with_fallback(score, fallback_reason=""):
    if score is None or np.isnan(score):
        return None
    return max(0, min(100, float(score)))


def _to_numeric(series, errors="coerce", fillna=None):
    """安全转换为数值类型，无效值转为 NaN"""
    s = pd.to_numeric(series, errors=errors)
    return s.fillna(fillna) if fillna is not None else s



