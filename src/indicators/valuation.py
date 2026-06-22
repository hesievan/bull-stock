"""Valuation dimension: PE/PB composite, below-net rate, ERP"""
import logging
import pandas as pd
import numpy as np
from src.indicators.utils import _pct_rank, _score_with_fallback, _to_numeric

logger = logging.getLogger(__name__)


def calc_valuation_composite(calc) -> float | None:
    """估值复合指标 — PE×0.6 + PB×0.4"""
    pe = calc._calc_pe_percentile()
    pb = calc._calc_pb_percentile()
    if pe is None and pb is None:
        return None
    if pe is None:
        return pb
    if pb is None:
        return pe
    score = pe * 0.6 + pb * 0.4
    logger.info("Valuation composite: PE=%.1f, PB=%.1f, composite=%.1f", pe, pb, score)
    return _score_with_fallback(score)


def calc_pe_percentile(calc) -> float | None:
    """PE中位数历史分位 (沪深300+中证500成分股口径)"""
    try:
        conn = calc._conn()
        stocks_today = calc._get_stock_daily(calc.trade_date)
        if stocks_today.empty or "peTTM" not in stocks_today.columns:
            return None

        constituents = calc._get_hist_constituents(calc.trade_date)
        df = stocks_today[stocks_today["stock_code"].isin(constituents)].copy()
        df["peTTM"] = _to_numeric(df["peTTM"])
        df = df[(df["peTTM"] > 0) & (df["peTTM"] <= 500)].dropna(subset=["peTTM"])
        if len(df) < 50:
            return None

        current_pe_med = df["peTTM"].median()

        hist_pe = pd.read_sql('''
            SELECT trade_date, pe_med FROM index_daily_pe
            WHERE pe_med IS NOT NULL
              AND trade_date <= ?
            ORDER BY trade_date
        ''', conn, params=[calc.trade_date])

        if hist_pe.empty or len(hist_pe) < 60:
            return None

        score = _pct_rank(hist_pe["pe_med"], current_pe_med) * 100
        logger.info("PE percentile (precomputed): med=%.2f, score=%.1f, n=%d, hist=%d",
                    current_pe_med, score, len(df), len(hist_pe))
        return _score_with_fallback(score)
    except Exception as e:
        logger.warning("PE percentile calc failed: %s", e)
        return None


def calc_pb_percentile(calc) -> float | None:
    """PB中位数历史分位 (沪深300+中证500成分股口径)"""
    try:
        conn = calc._conn()
        stocks_today = calc._get_stock_daily(calc.trade_date)
        if stocks_today.empty or "pbMRQ" not in stocks_today.columns:
            return None

        constituents = calc._get_hist_constituents(calc.trade_date)
        df = stocks_today[stocks_today["stock_code"].isin(constituents)].copy()
        df["pbMRQ"] = _to_numeric(df["pbMRQ"])
        df = df[(df["pbMRQ"] > 0) & (df["pbMRQ"] <= 10)].dropna(subset=["pbMRQ"])
        if len(df) < 50:
            return None

        current_pb_med = df["pbMRQ"].median()

        hist_pb = pd.read_sql('''
            SELECT trade_date, pb_med FROM index_daily_pe
            WHERE pb_med IS NOT NULL
              AND trade_date <= ?
            ORDER BY trade_date
        ''', conn, params=[calc.trade_date])

        if hist_pb.empty or len(hist_pb) < 60:
            return None

        score = _pct_rank(hist_pb["pb_med"], current_pb_med) * 100
        logger.info("PB percentile (precomputed): med=%.2f, score=%.1f, n=%d, hist=%d",
                    current_pb_med, score, len(df), len(hist_pb))
        return _score_with_fallback(score)
    except Exception as e:
        logger.warning("PB percentile calc failed: %s", e)
        return None


def calc_below_net_rate(calc) -> float | None:
    """破净率历史分位（反向: 破净率高=市场便宜=低分）"""
    try:
        conn = calc._conn()
        hist = pd.read_sql(
            "SELECT trade_date, below_net_rate FROM daily_below_net ORDER BY trade_date",
            conn
        )
        if hist.empty or len(hist) < 60:
            return None
        hist_rate = _to_numeric(hist["below_net_rate"]).dropna()

        today_rate = conn.execute(
            "SELECT below_net_rate FROM daily_below_net WHERE trade_date=?",
            (calc.trade_date,)
        ).fetchone()
        if not today_rate or today_rate[0] is None:
            stocks = calc._get_stock_daily(calc.trade_date)
            if stocks.empty:
                return None
            pb = _to_numeric(stocks["pbMRQ"])
            total = ((pb > 0) & pb.notna()).sum()
            below = ((pb > 0) & (pb < 1)).sum()
            if total < 100:
                return None
            cur = below / total
        else:
            cur = today_rate[0]

        score = (1 - _pct_rank(hist_rate, cur)) * 100
        logger.info("Below net rate (precomputed): %.4f, score=%.1f", cur, score)
        return _score_with_fallback(score)
    except Exception as e:
        logger.warning("Below net rate calc failed: %s", e)
        return None


def calc_erp(calc) -> float | None:
    """股权风险溢价 ERP = 1/PE - 10Y国债 (反向: 高ERP=便宜=低分)"""
    try:
        conn = calc._conn()
        hist = pd.read_sql(
            "SELECT trade_date, erp FROM daily_erp ORDER BY trade_date",
            conn
        )
        if hist.empty or len(hist) < 60:
            return None
        hist_erp = _to_numeric(hist['erp'], errors='coerce').dropna()

        today = conn.execute(
            "SELECT erp FROM daily_erp WHERE trade_date=?",
            (calc.trade_date,)
        ).fetchone()
        if not today or today[0] is None:
            pe_row = conn.execute(
                "SELECT pe_med FROM index_daily_pe WHERE trade_date=?",
                (calc.trade_date,)
            ).fetchone()
            bond_row = conn.execute(
                "SELECT yield_rate FROM bond_yield WHERE curve_term=10 AND trade_date<=? ORDER BY trade_date DESC LIMIT 1",
                (calc.trade_date,)
            ).fetchone()
            if not pe_row or not bond_row or pe_row[0] is None or bond_row[0] is None:
                return None
            pe_med = pe_row[0]
            bond_10y = bond_row[0]
            if pe_med <= 0:
                return None
            erp = (1.0 / pe_med - bond_10y / 100.0) * 100
            score = (1 - _pct_rank(hist_erp, erp)) * 100
            logger.info("ERP (fallback): pe=%.2f, bond=%.2f%%, erp=%.2f%%, score=%.1f",
                       pe_med, bond_10y, erp, score)
            return _score_with_fallback(score)

        score = (1 - _pct_rank(hist_erp, today[0])) * 100
        logger.info("ERP: %.4f%%, score=%.1f", today[0], score)
        return _score_with_fallback(score)
    except Exception as e:
        logger.warning("ERP calc failed: %s", e)
        return None


def calc_valuation(calc) -> float | None:
    """计算估值维度得分"""
    v1 = calc_valuation_composite(calc)
    v4 = calc_below_net_rate(calc)
    v5 = calc_erp(calc)
    scores = [v1, v4, v5]
    valid = [s for s in scores if s is not None and not np.isnan(s)]
    if not valid:
        logger.warning("Valuation: all sub-indicators unavailable")
        return None
    if len(valid) >= 3:
        mean = np.mean(valid)
        std = np.std(valid)
        if std > 0:
            filtered = [v for v in valid if abs(v - mean) <= 3 * std]
            if len(filtered) >= 2:
                valid = filtered
    result = np.mean(valid)
    logger.info("Valuation: combined=%.1f (from %d indicators)", result, len(valid))
    return max(0, min(100, result))
