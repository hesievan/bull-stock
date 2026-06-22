"""
Per-index bull market overheating analysis for major A-share indices.

For each target index, computes:
  - Technical overheating score (MA deviation, momentum, volume)
  - Valuation score (PE/PB percentile, where available)
  - Composite overheating score
  - Signal level (green/yellow/orange/red)

The "overheating" concept here is bullish — higher scores mean the index
is more extended/extreme and closer to a potential bull market top.
"""
import logging
from collections import OrderedDict
from typing import Optional

import pandas as pd
import numpy as np

from src.data.database import read_dataframe
from src.indicators.utils import _pct_rank, _to_numeric

logger = logging.getLogger(__name__)

TARGET_INDICES = OrderedDict({
    "sh000300": "沪深300",
    "sz399006": "创业板指",
    "sh000688": "科创50",
    "bj899050": "北证50",
    "sh000510": "中证A500",
    "sh000852": "中证1000",
})

# 使用相对强弱评分替代绝对动量评分的指数（短历史 < 1000 交易日）
SHORT_HISTORY_INDICES = {"bj899050", "sh000510"}

_INDEX_CODE_TO_TS = {
    "sh000300": "000300.SH",
    "sz399006": "399006.SZ",
    "sh000688": "000688.SH",
    "bj899050": "899050.BJ",
    "sh000510": "000510.SH",
    "sh000852": "000852.SH",
}


def get_overheat_level(score: float) -> str:
    if score is None:
        return "unknown"
    if score >= 65:
        return "red"
    if score >= 55:
        return "orange"
    if score >= 40:
        return "yellow"
    return "green"


def compute_index_heat(trade_date: str = None, db_path: str = None) -> list[dict]:
    """Compute overheating scores for all target indices."""
    from datetime import date
    trade_date = trade_date or date.today().strftime("%Y-%m-%d")

    results = []
    for ak_code, name in TARGET_INDICES.items():
        try:
            row = _analyze_single_index(ak_code, name, trade_date, db_path)
            results.append(row)
        except Exception as e:
            logger.warning("Index heat %s (%s) failed: %s", name, ak_code, e)
            results.append({
                "index_code": ak_code,
                "index_name": name,
                "error": str(e)[:80],
            })
    return results


def _get_index_daily(ak_code: str, trade_date: str, lookback_years: int = 10, db_path: str = None) -> pd.DataFrame:
    """Get index daily data with sufficient history for analysis."""
    from datetime import date, timedelta
    td = date.fromisoformat(trade_date)
    start = (td - timedelta(days=lookback_years * 365)).strftime("%Y-%m-%d")
    df = read_dataframe(
        "SELECT * FROM index_daily WHERE index_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
        params=(ak_code, start, trade_date), db_path=db_path
    )
    df["close"] = _to_numeric(df["close"])
    df["volume"] = _to_numeric(df["volume"])
    df["amount"] = _to_numeric(df["amount"])
    return df


def _get_index_pe_history(ak_code: str, trade_date: str, db_path: str = None) -> pd.DataFrame:
    """Get index PE/PB history."""
    ts_code = _INDEX_CODE_TO_TS.get(ak_code)
    if not ts_code:
        return pd.DataFrame()
    df = read_dataframe(
        "SELECT trade_date, pe_ttm, pb FROM index_pe_history "
        "WHERE index_code=? AND trade_date <= ? ORDER BY trade_date",
        params=(ak_code, trade_date), db_path=db_path
    )
    return df


def _calc_deviation_score(index_df: pd.DataFrame) -> Optional[float]:
    """Score based on how far price is above 250-day MA (percentile)."""
    if len(index_df) < 260:
        return None
    close = index_df["close"].values
    ma250 = pd.Series(close).rolling(250).mean().values
    dev = (close / ma250 - 1) * 100
    dev_clean = pd.Series(dev).dropna()
    if len(dev_clean) < 200:
        return None
    current_dev = dev_clean.iloc[-1]
    score = _pct_rank(dev_clean, current_dev) * 100
    return score


def _calc_momentum_score(index_df: pd.DataFrame, days: int = 60) -> Optional[float]:
    """Score based on N-day momentum percentile."""
    if len(index_df) < days * 2:
        return None
    close = index_df["close"].values
    pct = pd.Series(close).pct_change(days).dropna() * 100
    if len(pct) < days:
        return None
    current = pct.iloc[-1]
    score = _pct_rank(pct, current) * 100
    return score


def _calc_relative_strength(index_df: pd.DataFrame, benchmark_df: pd.DataFrame, window: int = 60) -> Optional[float]:
    """相对强弱评分：目标指数 vs 沪深300 的滚动超额收益分位

    适用于短历史指数（科创50/北证50/A500），替代噪声较大的绝对动量分位。
    """
    if index_df.empty or benchmark_df.empty:
        return None
    # 对齐日期
    merged = pd.merge(index_df[["trade_date", "close"]],
                      benchmark_df[["trade_date", "close"]],
                      on="trade_date", suffixes=("_idx", "_bm"))
    if len(merged) < window + 10:
        return None
    idx_ret = merged["close_idx"].pct_change()
    bm_ret = merged["close_bm"].pct_change()
    excess = idx_ret - bm_ret
    cumulative_excess = excess.rolling(window).sum().dropna()
    if len(cumulative_excess) < 20:
        return None
    current = cumulative_excess.iloc[-1]
    # 仅在自身短历史内评分
    score = _pct_rank(cumulative_excess, current) * 100
    # 连续跑赢惩罚
    consecutive_beat = 0
    for ret in excess.iloc[-min(20, len(excess)):]:
        if ret > 0:
            consecutive_beat += 1
        else:
            consecutive_beat = 0
    bonus = min(consecutive_beat * 1.5, 15)
    return min(score + bonus, 100)


def _calc_volume_score(index_df: pd.DataFrame, days: int = 60) -> Optional[float]:
    """Score based on amount vs recent average (percentile).
    
    NOTE: index_daily volume/amount data after mid-2025 appears to have
    incorrect units from tushare. This score is kept as informational
    only and NOT included in the technical composite.
    """
    if len(index_df) < days + 10:
        return None
    amt = pd.Series(index_df["amount"].values)
    recent_avg = amt.rolling(days).mean().dropna()
    if len(recent_avg) < 10:
        return None
    ratio_series = amt.iloc[-len(recent_avg):] / recent_avg
    current_ratio = (amt.iloc[-1] / recent_avg.iloc[-1]) if len(recent_avg) > 0 else 0
    score = _pct_rank(ratio_series, current_ratio) * 100
    return score


def _calc_pe_score(index_df_pe: pd.DataFrame) -> Optional[float]:
    """Score based on PE percentile (higher PE = more overheated)."""
    if index_df_pe.empty or len(index_df_pe) < 60:
        return None
    pe = pd.to_numeric(index_df_pe["pe_ttm"], errors="coerce").dropna()
    if len(pe) < 60:
        return None
    current = pe.iloc[-1]
    if pd.isna(current):
        return None
    score = _pct_rank(pe, current) * 100
    return score


def _calc_pb_score(index_df_pe: pd.DataFrame) -> Optional[float]:
    """Score based on PB percentile (higher PB = more overheated)."""
    if index_df_pe.empty or len(index_df_pe) < 60:
        return None
    pb = pd.to_numeric(index_df_pe["pb"], errors="coerce").dropna()
    if len(pb) < 60:
        return None
    current = pb.iloc[-1]
    if pd.isna(current):
        return None
    score = _pct_rank(pb, current) * 100
    return score


def _analyze_single_index(ak_code: str, name: str, trade_date: str, db_path: str = None) -> dict:
    """Analyze a single index for overheating signals."""
    idx_df = _get_index_daily(ak_code, trade_date, db_path=db_path)
    if idx_df.empty:
        return {
            "index_code": ak_code,
            "index_name": name,
            "error": "no index_daily data",
        }

    pe_df = _get_index_pe_history(ak_code, trade_date, db_path=db_path)

    tech_scores = []
    val_scores = []
    detail = {}

    dev_score = _calc_deviation_score(idx_df)
    if dev_score is not None:
        tech_scores.append(dev_score)
        detail["ma250_deviation"] = round(dev_score, 1)

    is_short_history = ak_code in SHORT_HISTORY_INDICES
    if is_short_history:
        bm_df = _get_index_daily("sh000300", trade_date, lookback_years=5, db_path=db_path)
        rs_score = _calc_relative_strength(idx_df, bm_df, window=60)
        if rs_score is not None:
            tech_scores.append(rs_score)
            detail["relative_strength_60d"] = round(rs_score, 1)
            logger.info("  %s: using relative strength (%.1f) instead of absolute momentum",
                        name, rs_score)
    else:
        for days, label in [(20, "momentum_20d"), (60, "momentum_60d"), (120, "momentum_120d")]:
            mom_score = _calc_momentum_score(idx_df, days)
            if mom_score is not None:
                tech_scores.append(mom_score)
                detail[label] = round(mom_score, 1)

    vol_score = _calc_volume_score(idx_df)
    if vol_score is not None:
        detail["volume"] = round(vol_score, 1)

    val_scores = []
    pe_score = _calc_pe_score(pe_df)
    if pe_score is not None:
        val_scores.append(pe_score)
        detail["pe"] = round(pe_score, 1)
        detail["pe_current"] = float(pd.to_numeric(pe_df["pe_ttm"], errors="coerce").dropna().iloc[-1])

    pb_score = _calc_pb_score(pe_df)
    if pb_score is not None:
        val_scores.append(pb_score)
        detail["pb"] = round(pb_score, 1)
        detail["pb_current"] = float(pd.to_numeric(pe_df["pb"], errors="coerce").dropna().iloc[-1])

    tech_avg = float(np.mean(tech_scores)) if tech_scores else None
    val_avg = float(np.mean(val_scores)) if val_scores else None

    if tech_avg is not None and val_avg is not None:
        composite = tech_avg * 0.5 + val_avg * 0.5
    elif tech_avg is not None:
        composite = tech_avg
    elif val_avg is not None:
        composite = val_avg
    else:
        composite = None

    latest = idx_df.iloc[-1]
    result = {
        "index_code": ak_code,
        "index_name": name,
        "trade_date": str(latest["trade_date"]),
        "close": float(latest["close"]),
        "pct_change": float(latest["pct_change"]) if pd.notna(latest.get("pct_change")) else None,
        "tech_score": round(tech_avg, 1) if tech_avg is not None else None,
        "val_score": round(val_avg, 1) if val_avg is not None else None,
        "composite_score": round(composite, 1) if composite is not None else None,
        "level": get_overheat_level(composite) if composite is not None else "unknown",
        "n_tech_indicators": len(tech_scores),
        "n_val_indicators": len(val_scores),
        "detail": detail,
    }
    if composite is not None:
        logger.info("Index %s (%s): composite=%.1f level=%s tech=%s val=%s",
                    name, ak_code, composite, result["level"],
                    tech_scores, val_scores)
    else:
        logger.info("Index %s (%s): insufficient data (tech=%d val=%d)",
                    name, ak_code, len(tech_scores), len(val_scores))
    return result
