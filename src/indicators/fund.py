"""Fund dimension: margin ratio change rate, northbound cumulative flow"""
import logging
import pandas as pd
import numpy as np
from src.indicators.utils import _pct_rank, _score_with_fallback, _to_numeric

logger = logging.getLogger(__name__)


def calc_margin_ratio(calc) -> float | None:
    """融资余额占流通市值比变化率"""
    try:
        margin_df = calc._get_margin()
        if margin_df.empty or len(margin_df) < 60:
            return None

        margin_df = margin_df.copy()
        margin_df["rzye"] = _to_numeric(margin_df["rzye"])
        margin_df["rqye"] = _to_numeric(margin_df["rqye"]).fillna(0)

        conn = calc._conn()
        daily_circ = pd.read_sql(
            "SELECT trade_date, total_circ_mv FROM daily_circ_mv WHERE total_circ_mv > 0",
            conn
        )

        merged = margin_df[["trade_date", "rzye", "rqye"]].merge(
            daily_circ, on="trade_date", how="inner"
        )
        if len(merged) < 60:
            return None

        merged["ratio"] = (merged["rzye"] + merged["rqye"]) / (merged["total_circ_mv"] * 10000)
        merged["change_rate"] = merged["ratio"].pct_change() * 100
        merged["change_rate"] = merged["change_rate"].replace([np.inf, -np.inf], np.nan)

        hist_cr = merged["change_rate"].tail(750).dropna()
        if len(hist_cr) < 60:
            return None

        cur_cr = merged["change_rate"].iloc[-1]
        if pd.isna(cur_cr):
            return None

        score = _pct_rank(hist_cr, cur_cr) * 100
        logger.info("Margin ratio change rate: %.2f%%, score=%.1f", cur_cr, score)
        return _score_with_fallback(score)
    except Exception as e:
        logger.warning("Margin ratio calc failed: %s", e)
        return None


def calc_northbound_cumflow(calc) -> float | None:
    """北向资金20日累计流入变化率"""
    try:
        nb = calc._get_northbound()
        if nb.empty or "north_net" not in nb.columns or len(nb) < 60:
            return None

        nb2 = nb.copy()
        # 先转数值（不 dropna），仅按列丢弃缺失行，避免索引错位导致 rolling 算错
        nb2["north_net"] = _to_numeric(nb2["north_net"])
        nb2 = nb2.dropna(subset=["north_net"])
        nb2["cum_20d"] = nb2["north_net"].rolling(20).sum()
        nb2["change_rate"] = nb2["cum_20d"].pct_change() * 100
        nb2["change_rate"] = nb2["change_rate"].replace([np.inf, -np.inf], np.nan)

        cur = nb2["change_rate"].iloc[-1]
        if pd.isna(cur):
            return None

        hist = nb2["change_rate"].tail(250).dropna()
        if len(hist) < 60:
            return None

        score = _pct_rank(hist, cur) * 100
        logger.info("Northbound change rate: %.2f%%, score=%.1f", cur, score)
        return _score_with_fallback(score)
    except Exception as e:
        logger.warning("Northbound change rate calc failed: %s", e)
        return None


def calc_fund(calc) -> float | None:
    """计算资金维度得分"""
    f3 = calc_northbound_cumflow(calc)
    f1 = calc_margin_ratio(calc)
    scores = [f3, f1]
    valid = [s for s in scores if s is not None and not np.isnan(s)]
    if not valid:
        logger.warning("Fund: all sub-indicators unavailable")
        return None
    result = np.mean(valid)
    logger.info("Fund: combined=%.1f (from %d indicators)", result, len(valid))
    return max(0, min(100, result))
