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
            # fallback: 从 m2_monthly 计算
            logger.info("M1-M2: daily_macro has insufficient data, trying m2_monthly fallback")
            return _calc_macro_from_monthly(conn, calc.trade_date, field="scissors")

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
            # fallback: 从 m2_monthly 计算
            logger.info("M2 YoY: daily_macro has insufficient data, trying m2_monthly fallback")
            return _calc_macro_from_monthly(conn, calc.trade_date, field="m2_yoy")

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


def _calc_macro_from_monthly(conn, trade_date: str, field: str) -> float | None:
    """从 m2_monthly 月度表回退计算宏观指标"""
    try:
        m2 = pd.read_sql(
            "SELECT month, m2_yoy FROM m2_monthly WHERE m2_yoy IS NOT NULL ORDER BY month",
            conn
        )
        if m2.empty or len(m2) < 24:
            logger.warning("m2_monthly has insufficient data for macro fallback")
            return None

        m2["month"] = pd.to_datetime(m2["month"] + "-01")
        m2["m2_yoy"] = pd.to_numeric(m2["m2_yoy"], errors="coerce")

        # 前向填充到日频: 每个交易日使用其所在月的 M2 值
        td_month = trade_date[:7]
        cur_row = m2[m2["month"].dt.strftime("%Y-%m") == td_month]

        if cur_row.empty:
            # 使用最近月份
            cur_row = m2[m2["month"] <= pd.Timestamp(trade_date)]
            if cur_row.empty:
                return None
            cur_row = cur_row.iloc[[-1]]

        m2_yoy = float(cur_row["m2_yoy"].iloc[0])
        if pd.isna(m2_yoy):
            return None

        if field == "scissors":
            # 剪刀差 = M1 - M2, 当 M1 不可用时, 使用 0
            cur_val = 0.0
            hist_vals = m2["m2_yoy"].values
        elif field == "m2_yoy":
            cur_val = m2_yoy
            hist_vals = m2["m2_yoy"].values
        else:
            return None

        score = _pct_rank(list(hist_vals), cur_val) * 100
        logger.info("Macro %s (monthly fallback): %.2f, score=%.1f (n=%d)",
                     field, cur_val, score, len(hist_vals))
        return _score_with_fallback(score)
    except Exception as e:
        logger.warning("Macro monthly fallback failed: %s", e)
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
