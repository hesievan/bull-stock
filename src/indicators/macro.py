"""Macro dimension: M1-M2 scissors gap, M2 YoY growth"""
import logging
import pandas as pd
import numpy as np
from src.indicators.utils import _pct_rank, _score_with_fallback, _to_numeric

logger = logging.getLogger(__name__)


def calc_m1m2_scissors(calc) -> float | None:
    """M1-M2增速剪刀差（宏观流动性，日频数据）"""
    try:
        conn = calc._conn()
        hist = pd.read_sql(
            "SELECT trade_date, scissors FROM daily_macro ORDER BY trade_date",
            conn
        )
        if hist.empty or len(hist) < 60:
            return None
        hist_s = _to_numeric(hist['scissors'], errors='coerce').dropna()
        if len(hist_s) < 60:
            return None

        today = conn.execute(
            "SELECT scissors FROM daily_macro WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1",
            (calc.trade_date,)
        ).fetchone()
        if not today or today[0] is None:
            return None

        score = _pct_rank(hist_s, today[0]) * 100
        logger.info("M1-M2 scissors (daily): %.2f, score=%.1f", today[0], score)
        return _score_with_fallback(score)
    except Exception as e:
        logger.warning("M1-M2 scissors calc failed: %s", e)
        return None


def calc_m2_yoy(calc) -> float | None:
    """M2同比增速（宏观流动性总量，日频数据）"""
    try:
        conn = calc._conn()
        hist = pd.read_sql(
            "SELECT trade_date, m2_yoy FROM daily_macro ORDER BY trade_date",
            conn
        )
        if hist.empty or len(hist) < 60:
            return None
        hist_m2 = _to_numeric(hist['m2_yoy'], errors='coerce').dropna()
        if len(hist_m2) < 60:
            return None

        today = conn.execute(
            "SELECT m2_yoy FROM daily_macro WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1",
            (calc.trade_date,)
        ).fetchone()
        if not today or today[0] is None:
            return None

        score = _pct_rank(hist_m2, today[0]) * 100
        logger.info("M2 YoY (daily): %.2f, score=%.1f", today[0], score)
        return _score_with_fallback(score)
    except Exception as e:
        logger.warning("M2 YoY calc failed: %s", e)
        return None


def calc_macro(calc) -> float | None:
    """计算宏观维度得分"""
    m1 = calc_m1m2_scissors(calc)
    m2 = calc_m2_yoy(calc)
    scores = [m1, m2]
    valid = [s for s in scores if s is not None and not np.isnan(s)]
    if not valid:
        logger.warning("Macro: all sub-indicators unavailable")
        return None
    result = np.mean(valid)
    logger.info("Macro: combined=%.1f (from %d indicators)", result, len(valid))
    return max(0, min(100, result))
