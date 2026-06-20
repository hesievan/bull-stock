"""Sentiment dimension: turnover, up/down ratio, limit-up ratio, QVIX"""
import logging
import pandas as pd
import numpy as np
from src.indicators.utils import _pct_rank, _score_with_fallback, _to_numeric, get_divergence

logger = logging.getLogger(__name__)


def calc_turnover(calc) -> float | None:
    """换手率（全市场成交额/流通市值）— 近 6 个月窗口百分位评分"""
    try:
        conn = calc._conn()
        trade_date = calc.trade_date
        six_mo_ago = (pd.Timestamp(trade_date) - pd.DateOffset(months=6)).strftime("%Y-%m-%d")

        hist = pd.read_sql(
            "SELECT trade_date, SUM(amount) AS tot_amt, SUM(circ_mv) AS tot_circ "
            "FROM stock_daily "
            "WHERE trade_date >= ? AND trade_date < ? AND amount > 0 AND circ_mv > 0 "
            "GROUP BY trade_date ORDER BY trade_date",
            conn, params=(six_mo_ago, trade_date)
        )
        if hist.empty or len(hist) < 20:
            return None
        hist_t = hist["tot_amt"] / hist["tot_circ"] * 10

        stocks = calc._get_stock_daily(trade_date)
        if stocks.empty:
            return None
        total_amount = _to_numeric(stocks["amount"]).clip(lower=0).sum()
        total_circ = _to_numeric(stocks["circ_mv"]).clip(lower=0).sum()
        if total_circ <= 0:
            return None
        cur = total_amount / total_circ * 10

        score = _pct_rank(hist_t, cur) * 100
        logger.info("Turnover (6mo window): %.4f%%, score=%.1f (n=%d)", cur, score, len(hist_t))
        return _score_with_fallback(score)
    except Exception as e:
        logger.warning("Turnover calc failed: %s", e)
        return None


def calc_up_down_ratio(calc) -> float | None:
    """上涨/下跌家数比 — 预计算表, 失败时从 stock_daily 实时计算"""
    try:
        conn = calc._conn()
        hist = pd.read_sql(
            "SELECT trade_date, up_down_ratio FROM daily_updown ORDER BY trade_date",
            conn
        )
        if hist.empty or len(hist) < 60:
            return None
        hist_ratio = _to_numeric(hist["up_down_ratio"]).dropna()

        today = conn.execute(
            "SELECT up_down_ratio FROM daily_updown WHERE trade_date=?",
            (calc.trade_date,)
        ).fetchone()
        if not today or today[0] is None:
            stocks = calc._get_stock_daily(calc.trade_date)
            if stocks.empty or "pct_change" not in stocks.columns:
                return None
            pc = _to_numeric(stocks["pct_change"])
            up = (pc > 0).sum()
            dn = (pc < 0).sum()
            if dn == 0:
                return None
            cur = up / dn
            score = _pct_rank(hist_ratio, cur) * 100
            logger.info("Up/Down ratio (live): %.2f (%d/%d), score=%.1f", cur, up, dn, score)
            return _score_with_fallback(score)

        cur = today[0]
        score = _pct_rank(hist_ratio, cur) * 100
        logger.info("Up/Down ratio (precomputed): %.2f, score=%.1f", cur, score)
        return _score_with_fallback(score)
    except Exception as e:
        logger.warning("Up/Down ratio calc failed: %s", e)
        return None


def calc_limit_up_ratio(calc) -> float | None:
    """涨停占比 — 预计算表, 失败时从 stock_daily 实时计算"""
    try:
        conn = calc._conn()
        hist = pd.read_sql(
            "SELECT trade_date, limit_up_ratio FROM daily_limit ORDER BY trade_date",
            conn
        )
        if hist.empty or len(hist) < 60:
            return None
        hist_lr = _to_numeric(hist["limit_up_ratio"]).dropna()

        today = conn.execute(
            "SELECT limit_up_ratio FROM daily_limit WHERE trade_date=?",
            (calc.trade_date,)
        ).fetchone()
        if not today or today[0] is None:
            stocks = calc._get_stock_daily(calc.trade_date)
            if stocks.empty:
                return None
            pc = _to_numeric(stocks["pct_change"])
            total = pc.notna().sum()
            if total < 50:
                return None
            limit_up = ((pc >= 9.9) & pc.notna()).sum()
            cur = limit_up / total
            score = _pct_rank(hist_lr, cur) * 100
            logger.info("Limit-up ratio (live): %.4f (%d/%d), score=%.1f", cur, limit_up, total, score)
            return _score_with_fallback(score)

        score = _pct_rank(hist_lr, today[0]) * 100
        logger.info("Limit-up ratio (precomputed): %.4f, score=%.1f", today[0], score)
        return _score_with_fallback(score)
    except Exception as e:
        logger.warning("Limit-up ratio calc failed: %s", e)
        return None


def calc_limit_ratio(calc) -> float | None:
    """涨跌停比 — 预计算表, 失败时从 stock_daily 实时计算"""
    try:
        conn = calc._conn()
        hist = pd.read_sql(
            "SELECT trade_date, limit_ratio FROM daily_limit ORDER BY trade_date",
            conn
        )
        if hist.empty or len(hist) < 60:
            return None
        hist_lr = _to_numeric(hist["limit_ratio"]).dropna()

        today = conn.execute(
            "SELECT limit_ratio FROM daily_limit WHERE trade_date=?",
            (calc.trade_date,)
        ).fetchone()
        if not today or today[0] is None:
            stocks = calc._get_stock_daily(calc.trade_date)
            if stocks.empty:
                return None
            pc = _to_numeric(stocks["pct_change"])
            up = ((pc >= 9.9) & pc.notna()).sum()
            dn = ((pc <= -9.9) & pc.notna()).sum()
            if dn == 0:
                return None
            cur = up / dn
            score = _pct_rank(hist_lr, cur) * 100
            logger.info("Limit ratio (live): %.2f (%d/%d), score=%.1f", cur, up, dn, score)
            return _score_with_fallback(score)

        score = _pct_rank(hist_lr, today[0]) * 100
        logger.info("Limit ratio (precomputed): %.2f, score=%.1f", today[0], score)
        return _score_with_fallback(score)
    except Exception as e:
        logger.warning("Limit ratio calc failed: %s", e)
        return None


def calc_qvix(calc) -> float | None:
    """50ETF期权隐含波动率 (QVIX) — 恐慌指标 (反向)"""
    try:
        conn = calc._conn()
        hist = pd.read_sql(
            "SELECT trade_date, qvix FROM qvix_daily ORDER BY trade_date",
            conn
        )
        if hist.empty or len(hist) < 60:
            return None
        hist_qvix = _to_numeric(hist["qvix"]).dropna()

        today = conn.execute(
            "SELECT qvix FROM qvix_daily WHERE trade_date=?",
            (calc.trade_date,)
        ).fetchone()
        if not today or today[0] is None:
            today = conn.execute(
                "SELECT qvix FROM qvix_daily WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1",
                (calc.trade_date,)
            ).fetchone()
            if not today or today[0] is None:
                return None

        score = (1 - _pct_rank(hist_qvix, today[0])) * 100
        logger.info("QVIX: %.2f, score=%.1f", today[0], score)
        return _score_with_fallback(score)
    except Exception as e:
        logger.warning("QVIX calc failed: %s", e)
        return None


def calc_sentiment(calc) -> float | None:
    """计算情绪维度得分（含背离惩罚）"""
    s1 = calc_turnover(calc)
    s2 = calc_up_down_ratio(calc)
    s3 = calc_limit_up_ratio(calc)
    s5 = calc_limit_ratio(calc)
    s6 = calc_qvix(calc)

    d = get_divergence()
    to_threshold = d.get("turnover_threshold", 70)
    ud_threshold = d.get("updown_threshold", 30)
    penalty_factor = d.get("penalty_factor", 0.5)
    penalty_floor = d.get("penalty_floor", 30)
    if s1 is not None and s2 is not None:
        if s1 > to_threshold and s2 < ud_threshold:
            penalty = (s1 - to_threshold) * penalty_factor
            s1_orig = s1
            s1 = max(s1 - penalty, penalty_floor)
            logger.info("Sentiment divergence penalty: turnover %.1f -> %.1f (up_down=%.1f)",
                       s1_orig, s1, s2)

    scores = [s1, s2, s3, s5, s6]
    valid = [s for s in scores if s is not None and not np.isnan(s)]
    if not valid:
        logger.warning("Sentiment: all sub-indicators unavailable")
        return None
    if len(valid) >= 3:
        mean = np.mean(valid)
        std = np.std(valid)
        if std > 0:
            filtered = [v for v in valid if abs(v - mean) <= 3 * std]
            if len(filtered) >= 2:
                valid = filtered
    result = np.mean(valid)
    logger.info("Sentiment: combined=%.1f (from %d indicators)", result, len(valid))
    return max(0, min(100, result))
