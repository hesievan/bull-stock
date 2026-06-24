"""Structure dimension: new-high ratio, AH premium index"""
import logging
import pandas as pd
import numpy as np
from src.indicators.utils import _pct_rank, _score_with_fallback, _to_numeric

logger = logging.getLogger(__name__)


def calc_new_high_ratio(calc) -> float | None:
    """创新高占比（250日最高close）"""
    try:
        stocks_today = calc._get_stock_daily(calc.trade_date)
        if stocks_today.empty:
            return None

        latest = stocks_today[["stock_code", "close"]].copy()
        latest["close"] = _to_numeric(latest["close"])
        latest = latest.dropna()
        if latest.empty:
            return None

        hist = calc._get_stock_daily_history()
        if hist.empty:
            return None

        close_max_250d = (
            hist.groupby("stock_code")["close"]
            .apply(lambda s: _to_numeric(s).rolling(250, min_periods=60).max().iloc[-1])
            .rename("close_max_250d")
        )
        merged = latest.merge(close_max_250d.reset_index(), on="stock_code", how="inner")
        merged = merged.dropna(subset=["close", "close_max_250d"])
        if len(merged) < 100:
            return None

        new_high = (merged["close"] >= merged["close_max_250d"] * 0.98).sum()
        ratio = new_high / len(merged)
        score = ratio * 100
        logger.info("New high ratio: %.4f (%d/%d), score=%.1f", ratio, new_high, len(merged), score)
        return _score_with_fallback(score)
    except Exception as e:
        logger.warning("New high ratio calc failed: %s", e)
        return None


def calc_ah_premium_index(calc) -> float | None:
    """AH股溢价 — 取当月溢价值, 全历史百分位赋分 (反向: 溢价越高分越低)"""
    try:
        conn = calc._conn()
        cur_month = calc.trade_date[:7]

        row = conn.execute(
            "SELECT premium FROM ah_premium_monthly WHERE trade_date=?",
            (cur_month,)
        ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT premium FROM ah_premium_monthly WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1",
                (cur_month,)
            ).fetchone()
        if row and row[0] is not None:
            cur = float(row[0])
            hist = pd.read_sql(
                "SELECT premium FROM ah_premium_monthly ORDER BY trade_date", conn
            )["premium"]
            hist = pd.to_numeric(hist, errors="coerce").dropna()
            if len(hist) < 12:
                return None
            pct = (hist <= cur).sum() / len(hist)
            score = (1 - pct) * 100
            logger.info("AH premium (monthly): cur=%.1f, P%.1f, score=%.1f (n=%d)",
                         cur, pct * 100, score, len(hist))
            return _score_with_fallback(score)

        # fallback: compute from daily ah_premium
        daily = pd.read_sql(
            "SELECT trade_date, premium FROM ah_premium WHERE premium > 0.5 AND premium < 3.0 ORDER BY trade_date",
            conn
        )
        if daily.empty or len(daily) < 12:
            return None
        daily_hist = pd.to_numeric(daily["premium"], errors="coerce").dropna()
        latest_daily = daily_hist.iloc[-1]
        score = (1 - _pct_rank(daily_hist, latest_daily)) * 100
        logger.info("AH premium (daily fallback): cur=%.4f, score=%.1f (n=%d)",
                     latest_daily, score, len(daily_hist))
        return _score_with_fallback(score)
    except Exception as e:
        logger.warning("AH premium index calc failed: %s", e)
        return None


def calc_structure(calc) -> float | None:
    """计算结构维度得分"""
    st1 = calc_new_high_ratio(calc)
    st2 = calc_ah_premium_index(calc)
    scores = [st1, st2]
    valid = [s for s in scores if s is not None and not np.isnan(s)]
    if not valid:
        logger.warning("Structure: all sub-indicators unavailable")
        return None
    result = np.mean(valid)
    logger.info("Structure: combined=%.1f (from %d indicators)", result, len(valid))
    return max(0, min(100, result))
