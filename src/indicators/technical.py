"""Technical dimension: MA alignment, deviation, momentum"""
import logging
import pandas as pd
import numpy as np
from src.indicators.utils import _pct_rank, _score_with_fallback, _to_numeric

logger = logging.getLogger(__name__)


def calc_ma_alignment(calc) -> float | None:
    """MA排列比 (MA20>MA60>MA120 的股票占比)"""
    try:
        conn = calc._conn()
        hist = pd.read_sql(
            "SELECT trade_date, ma_alignment_ratio FROM daily_ma_alignment ORDER BY trade_date",
            conn
        )
        if hist.empty or len(hist) < 60:
            return None
        hist_r = _to_numeric(hist['ma_alignment_ratio'], errors='coerce').dropna()

        today = conn.execute(
            "SELECT ma_alignment_ratio FROM daily_ma_alignment WHERE trade_date=?",
            (calc.trade_date,)
        ).fetchone()
        if not today or today[0] is None:
            return None

        score = _pct_rank(hist_r, today[0]) * 100
        logger.info("MA alignment ratio: %.4f, P%.1f, score=%.1f (n=%d)", today[0], _pct_rank(hist_r, today[0]) * 100, score, len(hist_r))
        return _score_with_fallback(score)
    except Exception as e:
        logger.warning("MA alignment calc failed: %s", e)
        return None


def calc_deviation_ma250(calc) -> float | None:
    """均线偏离度（上证综指 vs 250日均线）"""
    try:
        idx = calc._get_index_daily()
        sh = idx[idx["index_code"] == "sh000001"].sort_values("trade_date")
        if len(sh) < 260:
            return None

        sh["close"] = _to_numeric(sh["close"])
        ma250 = sh["close"].rolling(250).mean()
        ma_val = ma250.iloc[-1]
        if pd.isna(ma_val) or ma_val == 0:
            return None
        deviation = (sh["close"].iloc[-1] / ma_val - 1) * 100

        hist_dev = (sh["close"] / ma250 - 1).dropna() * 100
        if len(hist_dev) < 250:
            return None

        score = _pct_rank(hist_dev, deviation) * 100
        logger.info("MA250 deviation: %.2f%%, score=%.1f", deviation, score)
        return _score_with_fallback(score)
    except Exception as e:
        logger.warning("MA250 deviation calc failed: %s", e)
        return None


def calc_momentum_60d(calc) -> float | None:
    """60日涨幅历史分位"""
    try:
        idx = calc._get_index_daily()
        sh = idx[idx["index_code"] == "sh000001"].sort_values("trade_date")
        if len(sh) < 120:
            return None
        sh["close"] = _to_numeric(sh["close"])
        pct_60d = sh["close"].pct_change(60).dropna() * 100
        if len(pct_60d) < 60:
            return None
        cur = pct_60d.iloc[-1]
        score = _pct_rank(pct_60d, cur) * 100
        logger.info("Momentum 60d: %.2f%%, score=%.1f", cur, score)
        return _score_with_fallback(score)
    except Exception as e:
        logger.warning("Momentum 60d calc failed: %s", e)
        return None


def calc_momentum_20d(calc) -> float | None:
    """20日涨幅历史分位"""
    try:
        idx = calc._get_index_daily()
        sh = idx[idx["index_code"] == "sh000001"].sort_values("trade_date")
        if len(sh) < 60:
            return None
        sh["close"] = _to_numeric(sh["close"])
        pct_20d = sh["close"].pct_change(20).dropna() * 100
        if len(pct_20d) < 60:
            return None
        cur = pct_20d.iloc[-1]
        score = _pct_rank(pct_20d, cur) * 100
        logger.info("Momentum 20d: %.2f%%, score=%.1f", cur, score)
        return _score_with_fallback(score)
    except Exception as e:
        logger.warning("Momentum 20d calc failed: %s", e)
        return None


def calc_momentum_120d(calc) -> float | None:
    """120日涨幅历史分位"""
    try:
        idx = calc._get_index_daily()
        sh = idx[idx["index_code"] == "sh000001"].sort_values("trade_date")
        if len(sh) < 180:
            return None
        sh["close"] = _to_numeric(sh["close"])
        pct_120d = sh["close"].pct_change(120).dropna() * 100
        if len(pct_120d) < 60:
            return None
        cur = pct_120d.iloc[-1]
        score = _pct_rank(pct_120d, cur) * 100
        logger.info("Momentum 120d: %.2f%%, score=%.1f", cur, score)
        return _score_with_fallback(score)
    except Exception as e:
        logger.warning("Momentum 120d calc failed: %s", e)
        return None


def calc_technical(calc) -> float | None:
    """计算技术维度得分"""
    t1 = calc_ma_alignment(calc)
    t3 = calc_deviation_ma250(calc)
    t4 = calc_momentum_60d(calc)
    t5 = calc_momentum_20d(calc)
    t6 = calc_momentum_120d(calc)
    scores = [t1, t3, t4, t5, t6]
    valid = [s for s in scores if s is not None and not np.isnan(s)]
    if not valid:
        logger.warning("Technical: all sub-indicators unavailable")
        return None
    if len(valid) >= 3:
        mean = np.mean(valid)
        std = np.std(valid)
        if std > 0:
            filtered = [v for v in valid if abs(v - mean) <= 3 * std]
            if len(filtered) >= 2:
                valid = filtered
    result = np.mean(valid)
    logger.info("Technical: combined=%.1f (from %d indicators)", result, len(valid))
    return max(0, min(100, result))
