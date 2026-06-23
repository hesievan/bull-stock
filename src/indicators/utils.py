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
            _config_cache = load_config()
        except Exception:
            _config_cache = {}
    return _config_cache


def get_weights():
    return get_config().get("dimension_weights", {})


def get_divergence():
    return get_config().get("divergence_penalty", {})


def get_lookback_years():
    return get_config().get("data", {}).get("lookback_years", 10)


def _pct_rank(series: pd.Series, value: float, use_dynamic_window: bool = False) -> float:
    """历史分位 (0-1)

    可选参数 use_dynamic_window: 使用结构断点检测动态截断历史窗口，
    消除远古极端值对当前评分的稀释。
    """
    if series.empty or pd.isna(value):
        return np.nan
    clean = series.dropna()
    if clean.empty:
        return np.nan
    if use_dynamic_window:
        from src.indicators.regime_detector import apply_dynamic_window
        clean = apply_dynamic_window(clean)
        if clean.empty:
            return np.nan
    return (clean <= value).sum() / len(clean)


def _pct_rank_inv(series: pd.Series, value: float, use_dynamic_window: bool = False) -> float:
    """反向历史分位 — 值越高分位越低"""
    return 1 - _pct_rank(series, value, use_dynamic_window=use_dynamic_window)


def _safe_mean(values):
    valid = [v for v in values if v is not None and not np.isnan(v)]
    return np.mean(valid) if valid else None


def _score_with_fallback(score, fallback_reason=""):
    if score is None or np.isnan(score):
        return None
    return max(0, min(100, float(score)))


def _to_numeric(series, errors="coerce", fillna=None):
    """安全转换为数值类型，无效值转为 NaN"""
    s = pd.to_numeric(series, errors=errors)
    return s.fillna(fillna) if fillna is not None else s


def _clip_outliers(series: pd.Series, n_sigma: float = 5.0) -> pd.Series:
    """极端值检测修正: 超出 mean ± n_sigma * std 的值用中位数替代"""
    s = pd.to_numeric(series, errors="coerce")
    mean = s.mean()
    std = s.std()
    if pd.isna(mean) or pd.isna(std) or std == 0:
        return s
    lower = mean - n_sigma * std
    upper = mean + n_sigma * std
    n_clipped = ((s < lower) | (s > upper)).sum()
    if n_clipped > 0:
        s = s.clip(lower=lower, upper=upper)
        logger.debug("Clipped %d outlier(s) at %.0fσ (range [%.2f, %.2f])",
                     n_clipped, n_sigma, lower, upper)
    return s


def _query_scalar(conn, query, params=None):
    """查询单个标量值，不存在则返回 None"""
    row = conn.execute(query, params or ()).fetchone()
    return row[0] if row and row[0] is not None else None


def _query_dataframe(conn, query, params=None, min_rows=0):
    """查询 DataFrame，行数不足则返回空 DataFrame"""
    df = pd.read_sql(query, conn, params=params or ())
    if len(df) < min_rows:
        return pd.DataFrame()
    return df
