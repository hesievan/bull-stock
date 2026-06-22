"""
市场结构断点检测 — CUSUM 法（无需 scipy）

替代固定10年历史窗口，根据市场结构变化动态选择评分参考区间。
消除2015年牛市、注册制改革等远古极端值对当前评分的稀释效应。

使用方式:
  detector = RegimeDetector()
  start_idx = detector.get_active_window(series)
  # 仅使用 series.iloc[start_idx:] 计算百分位
"""
import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class RegimeDetector:
    """CUSUM 变点检测器 — 检测时间序列的均值移位"""

    def __init__(self, threshold_sigma: float = 2.5, min_window: int = 252):
        self.threshold_sigma = threshold_sigma
        self.min_window = min_window

    def detect_changepoints(self, series: pd.Series) -> list[int]:
        """CUSUM 检测均值变点，返回变点在 series 中的索引位置"""
        values = series.dropna().values
        if len(values) < self.min_window * 2:
            return []

        n = len(values)
        overall_mean = np.mean(values)
        std = np.std(values)
        if std == 0:
            return []

        # CUSUM 累计和
        cumsum = np.cumsum(values - overall_mean)
        # 检测累计和超出阈值的位置（均值发生偏移）
        changepoints = []
        i = self.min_window
        while i < n:
            segment = cumsum[:i]
            ref = cumsum[i - self.min_window]
            deviation = abs(cumsum[i] - ref)
            # 阈值: sigma * sqrt(n_segment)
            threshold = self.threshold_sigma * std * np.sqrt(self.min_window)
            if deviation > threshold:
                changepoints.append(i)
                # 重置: 从此位置开始重新累计
                local_mean = np.mean(values[i:])
                cumsum[i:] = np.cumsum(values[i:] - local_mean)
                i += self.min_window
            else:
                i += 1

        if changepoints:
            logger.info("CUSUM detected %d changepoint(s) at indices: %s",
                        len(changepoints), changepoints[:5])

        return changepoints

    def get_active_window(self, series: pd.Series) -> tuple[int, int]:
        """获取评分用的有效窗口 (start, end)

        逻辑:
          1. 检测变点
          2. 取最后一个变点至末尾为有效区间
          3. 回退保证最短 min_window 长度
          4. 无变点时返回全量
        """
        total = len(series)
        if total < self.min_window:
            return (0, total)

        changepoints = self.detect_changepoints(series)
        if changepoints:
            last_bp = max(changepoints)
            start = max(0, last_bp)
            if total - start < self.min_window:
                start = max(0, total - self.min_window)
            logger.debug("Regime window: [%d:%d] (total=%d, last_bp=%d)",
                         start, total, total, last_bp)
            return (start, total)

        return (0, total)


_regime_cache: dict[str, RegimeDetector | None] = {}


def get_regime_detector():
    """获取或创建全局单例 RegimeDetector"""
    key = "default"
    if key not in _regime_cache:
        try:
            _regime_cache[key] = RegimeDetector()
        except Exception:
            _regime_cache[key] = None
    return _regime_cache[key]


def apply_dynamic_window(series: pd.Series, min_periods: int = 252) -> pd.Series:
    """将 series 裁剪为有效评分区间 — 基于结构断点检测"""
    if len(series) < min_periods:
        return series
    detector = get_regime_detector()
    if detector is None:
        return series
    start, _ = detector.get_active_window(series)
    if start > 0:
        return series.iloc[start:]
    return series
