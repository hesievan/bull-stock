"""
热度指数计算引擎 — 编排入口
数据源: tushare(全市场K线/PE/PB/市值) + tushare(margin/northbound) + akshare(AH溢价)

6维度 19子指标:
  估值(3): PE/PB复合, 破净率, ERP
  宏观(2): M1-M2剪刀差, M2同比
  资金(2): 北向变化率, 融资余额比变化率
  情绪(5): 换手率, 涨跌家数比, 涨停占比, 涨跌停比, QVIX
  技术(5): MA排列比, 均线偏离度, 20/60/120日动量
  结构(2): 创新高比例, AH溢价指数

权重规则: 加权合成 + 异常值3σ过滤
"""
import logging
import sqlite3
from datetime import date, timedelta
from typing import Dict, Optional

import pandas as pd
import numpy as np

from src.data.database import read_dataframe, DB_PATH
from src.indicators.utils import (get_weights, get_lookback_years)
from src.data.freshness import get_effective_weights
from src.indicators.valuation import calc_valuation, calc_valuation_composite, calc_below_net_rate, calc_erp
from src.indicators.macro import calc_macro, calc_m1m2_scissors, calc_m2_yoy
from src.indicators.fund import calc_fund, calc_northbound_cumflow, calc_margin_ratio
from src.indicators.sentiment import calc_sentiment, calc_turnover, calc_up_down_ratio, calc_limit_up_ratio, calc_limit_ratio, calc_qvix
from src.indicators.technical import calc_technical, calc_ma_alignment, calc_deviation_ma250, calc_momentum_60d, calc_momentum_20d, calc_momentum_120d
from src.indicators.structure import calc_structure, calc_new_high_ratio, calc_ah_premium_index

logger = logging.getLogger(__name__)


class HeatIndexCalculator:
    def __init__(self, trade_date: str = None, db_path: str = None):
        self.trade_date = trade_date or date.today().strftime("%Y-%m-%d")
        self.db_path = db_path or DB_PATH
        self.lookback_start = (
            date.fromisoformat(self.trade_date) - timedelta(days=get_lookback_years() * 365)
        ).strftime("%Y-%m-%d")
        self._cache: Dict[str, pd.DataFrame] = {}

    # ── 数据加载 ───────────────────────────────────────────────────────────────

    def _get_index_daily(self) -> pd.DataFrame:
        if "idx" not in self._cache:
            self._cache["idx"] = read_dataframe(
                "SELECT * FROM index_daily WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
                params=(self.lookback_start, self.trade_date), db_path=self.db_path
            )
        return self._cache["idx"]

    def _get_stock_daily(self, trade_date: str = None) -> pd.DataFrame:
        td = trade_date or self.trade_date
        key = f"sd_{td}"
        if key not in self._cache:
            df = read_dataframe(
                "SELECT * FROM stock_daily WHERE trade_date=?",
                params=(td,), db_path=self.db_path
            )
            if df.empty:
                latest = read_dataframe(
                    "SELECT MAX(trade_date) as d FROM stock_daily",
                    db_path=self.db_path
                )
                if not latest.empty and latest.iloc[0]["d"]:
                    td_latest = latest.iloc[0]["d"]
                    df = read_dataframe(
                        "SELECT * FROM stock_daily WHERE trade_date=?",
                        params=(td_latest,), db_path=self.db_path
                    )
            self._cache[key] = df
        return self._cache[key]

    def _get_stock_daily_history(self) -> pd.DataFrame:
        if "sd_hist" not in self._cache:
            self._cache["sd_hist"] = read_dataframe(
                """SELECT trade_date, stock_code, close, pct_change, peTTM, pbMRQ,
                   circ_mv, turnover_rate
                   FROM stock_daily
                   WHERE trade_date BETWEEN ? AND ?
                   ORDER BY trade_date""",
                params=(self.lookback_start, self.trade_date), db_path=self.db_path
            )
        return self._cache["sd_hist"]

    def _conn(self):
        """Get or reuse SQLite connection (WAL mode)"""
        if not hasattr(self, "_db_conn") or self._db_conn is None:
            self._db_conn = sqlite3.connect(self.db_path)
            self._db_conn.execute("PRAGMA journal_mode=WAL")
            self._db_conn.execute("PRAGMA synchronous=NORMAL")
        return self._db_conn

    def close(self):
        if hasattr(self, "_db_conn") and self._db_conn is not None:
            self._db_conn.close()
            self._db_conn = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _get_margin(self) -> pd.DataFrame:
        if "margin" not in self._cache:
            self._cache["margin"] = read_dataframe(
                "SELECT * FROM margin_history WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
                params=(self.lookback_start, self.trade_date), db_path=self.db_path
            )
        return self._cache["margin"]

    def _get_northbound(self) -> pd.DataFrame:
        if "nb" not in self._cache:
            self._cache["nb"] = read_dataframe(
                "SELECT * FROM northbound_history WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
                params=(self.lookback_start, self.trade_date), db_path=self.db_path
            )
        return self._cache["nb"]

    def _get_bond(self) -> pd.DataFrame:
        if "bond" not in self._cache:
            self._cache["bond"] = read_dataframe(
                "SELECT * FROM bond_yield WHERE trade_date <= ? AND curve_term=10 ORDER BY trade_date DESC LIMIT 5",
                params=(self.trade_date,), db_path=self.db_path
            )
        return self._cache["bond"]

    def _get_index_pe(self) -> pd.DataFrame:
        if "idx_pe" not in self._cache:
            self._cache["idx_pe"] = read_dataframe(
                "SELECT * FROM index_pe_history WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
                params=(self.lookback_start, self.trade_date), db_path=self.db_path
            )
        return self._cache["idx_pe"]

    # ── 历史成分股加载 ─────────────────────────────────────────────────────────

    def _load_hist_constituents(self):
        if hasattr(self, "_hc_by_date"):
            return
        conn = self._conn()
        df = pd.read_sql('''
            SELECT trade_date, con_code
            FROM index_constituents_hist
            WHERE index_code IN ('hs300','zz500')
            ORDER BY trade_date
        ''', conn)
        if df.empty:
            df = pd.read_sql(
                "SELECT DISTINCT stock_code as con_code FROM index_constituents WHERE index_code IN ('hs300','zz500')",
                conn
            )
            self._hc_by_date = {self.trade_date: set(df['con_code'])}
            return

        month_ends = df.groupby('trade_date')['con_code'].apply(set).to_dict()
        sorted_me = sorted(month_ends.keys())
        self._hc_by_date = {}
        all_trade_dates = pd.read_sql(
            "SELECT DISTINCT trade_date FROM stock_daily ORDER BY trade_date", conn
        )['trade_date'].tolist()

        for td in all_trade_dates:
            td_cmp = td.replace('-', '')
            valid = [d for d in sorted_me if d <= td_cmp]
            if valid:
                self._hc_by_date[td] = month_ends[max(valid)]

        logger.info("Hist constituents loaded: %d month-ends, %d trade dates mapped",
                     len(sorted_me), len(self._hc_by_date))

    def _get_hist_constituents(self, trade_date: str) -> set:
        self._load_hist_constituents()
        td_key = trade_date.replace('-', '')
        if td_key in self._hc_by_date:
            return self._hc_by_date[td_key]
        valid = [d for d in self._hc_by_date if d <= td_key]
        if valid:
            return self._hc_by_date[max(valid)]
        return set()

    # ── 维度计算方法（委托到各模块） ─────────────────────────────────────────

    def _calc_valuation_composite(self) -> Optional[float]:
        return calc_valuation_composite(self)

    def _calc_pe_percentile(self) -> Optional[float]:
        from src.indicators.valuation import calc_pe_percentile
        return calc_pe_percentile(self)

    def _calc_pb_percentile(self) -> Optional[float]:
        from src.indicators.valuation import calc_pb_percentile
        return calc_pb_percentile(self)

    def _calc_below_net_rate(self) -> Optional[float]:
        return calc_below_net_rate(self)

    def _calc_erp(self) -> Optional[float]:
        return calc_erp(self)

    def _calc_margin_ratio(self) -> Optional[float]:
        return calc_margin_ratio(self)

    def _calc_northbound_cumflow(self) -> Optional[float]:
        return calc_northbound_cumflow(self)

    def _calc_turnover(self) -> Optional[float]:
        return calc_turnover(self)

    def _calc_up_down_ratio(self) -> Optional[float]:
        return calc_up_down_ratio(self)

    def _calc_limit_up_ratio(self) -> Optional[float]:
        return calc_limit_up_ratio(self)

    def _calc_limit_ratio(self) -> Optional[float]:
        return calc_limit_ratio(self)

    def _calc_qvix(self) -> Optional[float]:
        return calc_qvix(self)

    def _calc_new_high_ratio(self) -> Optional[float]:
        return calc_new_high_ratio(self)

    def _calc_deviation_ma250(self) -> Optional[float]:
        return calc_deviation_ma250(self)

    def _calc_momentum_60d(self) -> Optional[float]:
        return calc_momentum_60d(self)

    def _calc_momentum_20d(self) -> Optional[float]:
        return calc_momentum_20d(self)

    def _calc_momentum_120d(self) -> Optional[float]:
        return calc_momentum_120d(self)

    def _calc_ma_alignment(self) -> Optional[float]:
        return calc_ma_alignment(self)

    def _calc_ah_premium_index(self) -> Optional[float]:
        return calc_ah_premium_index(self)

    def _calc_m1m2_scissors(self) -> Optional[float]:
        return calc_m1m2_scissors(self)

    def _calc_m2_yoy(self) -> Optional[float]:
        return calc_m2_yoy(self)

    def _calc_valuation(self) -> Optional[float]:
        return calc_valuation(self)

    def _calc_macro(self) -> Optional[float]:
        return calc_macro(self)

    def _calc_fund(self) -> Optional[float]:
        return calc_fund(self)

    def _calc_sentiment(self) -> Optional[float]:
        return calc_sentiment(self)

    def _calc_technical(self) -> Optional[float]:
        return calc_technical(self)

    def _calc_structure(self) -> Optional[float]:
        return calc_structure(self)

    # ── 维度合成 ───────────────────────────────────────────────────────────────

    def _series_pct_rank(self, series: pd.Series, value: float) -> float:
        """Forward percentile rank (0.0-1.0): how much of history <= value"""
        if series.empty or pd.isna(value):
            return 0.5
        clean = series.dropna()
        if clean.empty:
            return 0.5
        return (clean < value).sum() / len(clean)

    def _combine_dimension(self, scores: list, label: str) -> Optional[float]:
        valid = [s for s in scores if s is not None and not np.isnan(s)]
        if not valid:
            logger.warning("%s: all sub-indicators unavailable", label)
            return None
        if len(valid) >= 3:
            mean = np.mean(valid)
            std = np.std(valid)
            if std > 0:
                filtered = [v for v in valid if abs(v - mean) <= 3 * std]
                if len(filtered) >= 2:
                    valid = filtered
        if not valid:
            return None
        result = np.mean(valid)
        logger.info("%s: combined=%.1f (from %d indicators: %s)",
                    label, result, len(valid), [f"{v:.1f}" for v in valid])
        return max(0, min(100, result))

    def _calc_composite(self, dims: list, indicators: dict = None) -> tuple:
        """计算加权综合得分，返回 (composite_score, effective_weights, freshness_scores)

        当数据陈旧时自动衰减权重，将权重重新分配给新鲜维度。
        """
        w = get_weights()
        w_val = w.get("valuation", 0.25)
        w_macro = w.get("macro", 0.15)
        w_fund = w.get("fund", 0.15)
        w_sent = w.get("sentiment", 0.20)
        w_tech = w.get("technical", 0.10)
        w_struct = w.get("structure", 0.15)
        weights = [w_val, w_macro, w_fund, w_sent, w_tech, w_struct]
        dim_names = ["valuation", "macro", "fund", "sentiment", "technical", "structure"]

        # 新鲜度调整
        effective_weights = dict(zip(dim_names, weights))
        freshness_scores = {}
        if indicators:
            eff_w, fresh = get_effective_weights(indicators, self.trade_date)
            effective_weights.update(eff_w)
            freshness_scores = fresh

        valid = [(d, effective_weights.get(n, w))
                 for d, n, w in zip(dims, dim_names, weights)
                 if d is not None and effective_weights.get(n, w) > 0]
        if valid:
            composite = sum(d * w for d, w in valid) / sum(w for _, w in valid)
        else:
            composite = None

        return composite, effective_weights, freshness_scores

    def _build_data_quality(self, dim_names, dims, freshness_scores):
        """构建数据质量报告"""
        getattr(self, '_indicators_data', {})
        dim_labels = {"valuation":"估值","macro":"宏观","fund":"资金",
                      "sentiment":"情绪","technical":"技术","structure":"结构"}
        report = {"overall_quality": "good", "dimensions": {}}
        stale_count = 0
        degraded_count = 0
        for i, name in enumerate(dim_names):
            f = freshness_scores.get(name, 1.0)
            is_stale = f < 0.8
            dim_score = dims[i]
            available = 1 if dim_score is not None else 0
            total = 1
            status = "ok"
            if available == 0 or is_stale:
                status = "poor" if (available == 0 or f < 0.5) else "degraded"
            if status == "poor":
                stale_count += 1
            elif status == "degraded":
                degraded_count += 1
            report["dimensions"][name] = {
                "label": dim_labels.get(name, name),
                "available": available,
                "total": total,
                "freshness": round(f, 2),
                "status": status,
            }
        if stale_count > 0:
            report["overall_quality"] = "poor"
        elif degraded_count > 0:
            report["overall_quality"] = "degraded"
        return report

    def calculate(self) -> dict:
        logger.info("=" * 50)
        logger.info("Calculating heat index for %s", self.trade_date)
        logger.info("=" * 50)

        dim_val = self._calc_valuation()
        dim_macro = self._calc_macro()
        dim_fund = self._calc_fund()
        dim_sent = self._calc_sentiment()
        dim_tech = self._calc_technical()
        dim_struct = self._calc_structure()

        dims = [dim_val, dim_macro, dim_fund, dim_sent, dim_tech, dim_struct]
        dim_names = ["valuation", "macro", "fund", "sentiment", "technical", "structure"]

        v1 = self._calc_valuation_composite()
        v4 = self._calc_below_net_rate()
        v5 = self._calc_erp()
        m1 = self._calc_m1m2_scissors()
        m2 = self._calc_m2_yoy()
        f1 = self._calc_margin_ratio()
        f3 = self._calc_northbound_cumflow()
        s1 = self._calc_turnover()
        s2 = self._calc_up_down_ratio()
        s3 = self._calc_limit_up_ratio()
        s5 = self._calc_limit_ratio()
        s6 = self._calc_qvix()
        t1 = self._calc_ma_alignment()
        t3 = self._calc_deviation_ma250()
        t4 = self._calc_momentum_60d()
        t5 = self._calc_momentum_20d()
        t6 = self._calc_momentum_120d()
        st1 = self._calc_new_high_ratio()
        st2 = self._calc_ah_premium_index()

        indicators_dict = {
            "valuation": {
                "valuation_composite": {"value": v1},
                "below_net_rate": {"value": v4},
                "erp": {"value": v5},
            },
            "macro": {
                "m1m2_scissors": {"value": m1},
                "m2_yoy": {"value": m2},
            },
            "fund": {
                "northbound_cumflow": {"value": f3},
                "margin_ratio": {"value": f1},
            },
            "sentiment": {
                "turnover": {"value": s1},
                "up_down_ratio": {"value": s2},
                "limit_up_ratio": {"value": s3},
                "limit_ratio": {"value": s5},
                "qvix": {"value": s6},
            },
            "technical": {
                "ma_alignment": {"value": t1},
                "deviation_ma250": {"value": t3},
                "momentum_60d": {"value": t4},
                "momentum_20d": {"value": t5},
                "momentum_120d": {"value": t6},
            },
            "structure": {
                "new_high_ratio": {"value": st1},
                "ah_premium_index": {"value": st2},
            },
        }

        composite, effective_weights, freshness_scores = self._calc_composite(dims, indicators_dict)

        result = {
            "trade_date": self.trade_date,
            "composite_score": composite,
            "effective_weights": effective_weights,
            "freshness_scores": freshness_scores,
            "data_quality": self._build_data_quality(dim_names, dims, freshness_scores),
            "dim_valuation": dim_val,
            "dim_macro": dim_macro,
            "dim_fund": dim_fund,
            "dim_sentiment": dim_sent,
            "dim_technical": dim_tech,
            "dim_structure": dim_struct,
            "indicators": {
                "valuation": {
                    "valuation_composite": v1,
                    "below_net_rate": v4,
                    "erp": v5,
                },
                "macro": {
                    "m1m2_scissors": m1,
                    "m2_yoy": m2,
                },
                "fund": {"northbound_cumflow": f3, "margin_ratio": f1},
                "sentiment": {
                    "turnover": s1, "up_down_ratio": s2,
                    "limit_up_ratio": s3, "limit_ratio": s5,
                    "qvix": s6,
                },
                "technical": {
                    "ma_alignment": t1,
                    "deviation_ma250": t3,
                    "momentum_60d": t4,
                    "momentum_20d": t5,
                    "momentum_120d": t6,
                },
                "structure": {"new_high_ratio": st1, "ah_premium_index": st2},
            },
        }

        logger.info("FINAL composite score: %s",
                    f"{composite:.1f}" if composite is not None else "FAILED")
        return result


def calculate_heat_index(trade_date: str = None, db_path: str = None) -> dict:
    with HeatIndexCalculator(trade_date=trade_date, db_path=db_path) as calc:
        return calc.calculate()


from src.indicators.sector_calculator import calculate_sector_heat  # noqa: F401
