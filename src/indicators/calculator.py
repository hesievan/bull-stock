"""
热度指数计算引擎 — 三源合一版
数据源: baostock(stock_daily含peTTM/pbMRQ) + tushare(margin/northbound/bond) + 东方财富curl(HSAHP)

5维度 18子指标:
  估值(4): PE分位, PB分位, 破净率, 巴菲特指标
  资金(2): 融资买入占比, 北向资金方向
  情绪(5): 换手率, 上涨/下跌家数比, 涨停占比, 跌停占比, 波动率
  技术(4): 站上年线比, 创新高比, 均线偏离度, 量价背离
  结构(2): 行业分化度, AH股溢价指数(HSAHP)

权重规则: 等权 + 异常/0则舍弃, 其余重新等权归一
"""
import logging
import json
import sqlite3
from datetime import date, timedelta
from typing import Dict, Optional

import pandas as pd
import numpy as np

from src.data.database import read_dataframe, DB_PATH

logger = logging.getLogger(__name__)
LOOKBACK_YEARS = 10

# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _pct_rank(series: pd.Series, value: float) -> float:
    """历史分位 (0-1)"""
    if series.empty or pd.isna(value):
        return np.nan
    return (series.dropna() < value).sum() / max(len(series.dropna()), 1)


def _pct_rank_inv(series: pd.Series, value: float) -> float:
    """反向历史分位 — 值越高分位越低（ERP越高越便宜，分位越低）"""
    return 1 - _pct_rank(series, value)


def _safe_mean(values):
    valid = [v for v in values if v is not None and not np.isnan(v)]
    return np.mean(valid) if valid else None


def _score_with_fallback(score, fallback_reason=""):
    if score is None or np.isnan(score):
        return None
    return max(0, min(100, float(score)))


# ── 计算器 ────────────────────────────────────────────────────────────────────

class HeatIndexCalculator:
    def __init__(self, trade_date: str = None, db_path: str = None):
        self.trade_date = trade_date or date.today().strftime("%Y-%m-%d")
        self.db_path = db_path or DB_PATH
        self.lookback_start = (
            date.fromisoformat(self.trade_date) - timedelta(days=LOOKBACK_YEARS * 365)
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
            self._cache[key] = read_dataframe(
                "SELECT * FROM stock_daily WHERE trade_date=?",
                params=(td,), db_path=self.db_path
            )
        return self._cache[key]

    def _get_stock_daily_history(self) -> pd.DataFrame:
        """全量 stock_daily（含日期字段，用于历史分位计算）"""
        if "sd_hist" not in self._cache:
            self._cache["sd_hist"] = read_dataframe(
                "SELECT * FROM stock_daily WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
                params=(self.lookback_start, self.trade_date), db_path=self.db_path
            )
        return self._cache["sd_hist"]


    def _conn(self):
        """Get or reuse SQLite connection"""
        if not hasattr(self, "_db_conn") or self._db_conn is None:
            import sqlite3
            self._db_conn = sqlite3.connect(self.db_path)
        return self._db_conn
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

    # ── 估值维度 ───────────────────────────────────────────────────────────────

    def _load_hist_constituents(self):
        """预加载所有历史成分股到内存 {trade_date: set(codes)}"""
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

        # 构建 {month_end_date: set(codes)}
        month_ends = {}
        for _, row in df.iterrows():
            td = row['trade_date']
            month_ends.setdefault(td, set()).add(row['con_code'])

        sorted_me = sorted(month_ends.keys())
        self._hc_by_date = {}
        # 对 stock_daily 中每个交易日, 找最近月末的成分股
        all_trade_dates = pd.read_sql(
            "SELECT DISTINCT trade_date FROM stock_daily ORDER BY trade_date", conn
        )['trade_date'].tolist()

        for td in all_trade_dates:
            td_cmp = td.replace('-', '')
            # 找 <= td 的最近月末
            valid = [d for d in sorted_me if d <= td_cmp]
            if valid:
                self._hc_by_date[td] = month_ends[max(valid)]

        logger.info("Hist constituents loaded: %d month-ends, %d trade dates mapped",
                     len(sorted_me), len(self._hc_by_date))

    def _get_hist_constituents(self, trade_date: str) -> set:
        """获取指定日期的沪深300+中证500成分股"""
        self._load_hist_constituents()
        td_key = trade_date.replace('-', '')
        if td_key in self._hc_by_date:
            return self._hc_by_date[td_key]
        # fallback: 最近月末
        valid = [d for d in self._hc_by_date if d <= td_key]
        if valid:
            return self._hc_by_date[max(valid)]
        return set()

    def _calc_pe_percentile(self) -> Optional[float]:
        """PE中位数历史分位 (沪深300+中证500成分股口径)

        方案B v2: 查预计算汇总表 index_daily_pe
        - 历史成分股 PE 中位数已预计算, O(1) 查询
        - 当日值实时计算 (成分股截面)
        """
        try:
            conn = self._conn()
            stocks_today = self._get_stock_daily(self.trade_date)
            if stocks_today.empty or "peTTM" not in stocks_today.columns:
                return None

            constituents = self._get_hist_constituents(self.trade_date)
            df = stocks_today[stocks_today["stock_code"].isin(constituents)].copy()
            df["peTTM"] = pd.to_numeric(df["peTTM"], errors="coerce")
            df = df[(df["peTTM"] > 0) & (df["peTTM"] <= 500)].dropna(subset=["peTTM"])
            if len(df) < 50:
                return None

            current_pe_med = df["peTTM"].median()

            # 查预计算汇总表
            hist_pe = pd.read_sql('''
                SELECT trade_date, pe_med FROM index_daily_pe
                WHERE pe_med IS NOT NULL
                  AND trade_date <= ?
                ORDER BY trade_date
            ''', conn, params=[self.trade_date])

            if hist_pe.empty or len(hist_pe) < 60:
                return None

            score = _pct_rank(hist_pe["pe_med"], current_pe_med) * 100
            logger.info("PE percentile (precomputed): med=%.2f, score=%.1f, n=%d, hist=%d",
                        current_pe_med, score, len(df), len(hist_pe))
            return _score_with_fallback(score)
        except Exception as e:
            logger.error("PE percentile calc failed: %s", e)
            return None

    def _calc_pb_percentile(self) -> Optional[float]:
        """PB中位数历史分位 (沪深300+中证500成分股口径)

        方案B v2: 查预计算汇总表 index_daily_pe
        """
        try:
            conn = self._conn()
            stocks_today = self._get_stock_daily(self.trade_date)
            if stocks_today.empty or "pbMRQ" not in stocks_today.columns:
                return None

            constituents = self._get_hist_constituents(self.trade_date)
            df = stocks_today[stocks_today["stock_code"].isin(constituents)].copy()
            df["pbMRQ"] = pd.to_numeric(df["pbMRQ"], errors="coerce")
            df = df[(df["pbMRQ"] > 0) & (df["pbMRQ"] <= 10)].dropna(subset=["pbMRQ"])
            if len(df) < 50:
                return None

            current_pb_med = df["pbMRQ"].median()

            hist_pb = pd.read_sql('''
                SELECT trade_date, pb_med FROM index_daily_pe
                WHERE pb_med IS NOT NULL
                  AND trade_date <= ?
                ORDER BY trade_date
            ''', conn, params=[self.trade_date])

            if hist_pb.empty or len(hist_pb) < 60:
                return None

            score = _pct_rank(hist_pb["pb_med"], current_pb_med) * 100
            logger.info("PB percentile (precomputed): med=%.2f, score=%.1f, n=%d, hist=%d",
                        current_pb_med, score, len(df), len(hist_pb))
            return _score_with_fallback(score)
        except Exception as e:
            logger.error("PB percentile calc failed: %s", e)
            return None

    def _calc_below_net_rate(self) -> Optional[float]:
        """破净率 = PB<1个股占比 (成分股口径)

        注意: 破净率需要每日截面数据, 无法用预计算表
        改用全市场口径 (不用成分股过滤), 因为破净股本身就是全市场概念
        """
        try:
            stocks_today = self._get_stock_daily(self.trade_date)
            if stocks_today.empty or "pbMRQ" not in stocks_today.columns:
                return None

            # 全市场口径 (破净是市场整体现象)
            df = stocks_today.copy()
            df["pbMRQ"] = pd.to_numeric(df["pbMRQ"], errors="coerce")
            df = df[(df["pbMRQ"] > 0) & (df["pbMRQ"] <= 10)].dropna(subset=["pbMRQ"])
            if len(df) < 100:
                return None

            below_net = (df["pbMRQ"] < 1.0).sum() / len(df)

            hist = self._get_stock_daily_history()
            if hist.empty or len(hist["trade_date"].unique()) < 60:
                if below_net > 0.15: return 70
                if below_net > 0.10: return 50
                if below_net > 0.05: return 30
                return 10

            # 用全市场口径计算历史序列 (不用成分股过滤)
            hist_pb = pd.read_sql('''
                SELECT trade_date, pbMRQ FROM stock_daily
                WHERE pbMRQ > 0 AND pbMRQ <= 10
                  AND trade_date <= ?
                ORDER BY trade_date
            ''', self._conn(), params=[self.trade_date])

            if hist_pb.empty:
                return None

            hist_bnet = hist_pb.groupby("trade_date")["pbMRQ"].apply(
                lambda x: (x < 1.0).mean()
            ).dropna()

            if len(hist_bnet) < 60:
                return None

            # 反向: 破净率高=便宜=低分, 破净率低=贵=高分
            score = _pct_rank_inv(hist_bnet, below_net) * 100
            logger.info("Below net rate (all-market, inverted): %.4f, score=%.1f, n=%d",
                        below_net, score, len(df))
            return _score_with_fallback(score)
        except Exception as e:
            logger.error("Below net rate calc failed: %s", e)
            return None

    # ── 资金维度 ───────────────────────────────────────────────────────────────

    def _calc_margin_ratio(self) -> Optional[float]:
        """融资买入占总成交比例（杠杆热度）"""
        try:
            margin_df = self._get_margin()
            stocks_today = self._get_stock_daily(self.trade_date)
            if margin_df.empty or stocks_today.empty:
                return None

            rzmre = pd.to_numeric(margin_df["rzmre"], errors="coerce").iloc[-1]
            total_amount = pd.to_numeric(stocks_today["amount"], errors="coerce").sum()
            if pd.isna(rzmre) or total_amount <= 0:
                return None

            ratio = rzmre / total_amount * 100

            # 历史分位
            hist_margin = self._get_margin()
            if hist_margin.empty or len(hist_margin) < 60:
                return None

            hist_amount = self._get_stock_daily_history()
            if hist_amount.empty:
                return None

            # 历史融资买入占比（简化: 用融资买入额自身分位）
            hist_rzmre = pd.to_numeric(hist_margin["rzmre"], errors="coerce").dropna()
            if len(hist_rzmre) < 60:
                return None

            score = _pct_rank(hist_rzmre, rzmre) * 100
            logger.info("Margin ratio: %.4f%%, score=%.1f", ratio, score)
            return _score_with_fallback(score)
        except Exception as e:
            logger.error("Margin ratio calc failed: %s", e)
            return None

    def _calc_northbound(self) -> Optional[float]:
        """北向资金方向（净买入持续天数映射分数）"""
        try:
            nb = self._get_northbound()
            if nb.empty or "north_net" not in nb.columns:
                return None

            nb2 = nb.copy()
            nb2["north_net"] = pd.to_numeric(nb2["north_net"], errors="coerce")
            nb2["sign"] = (nb2["north_net"] > 0).astype(int)

            # 近20日净买入天数占比
            recent = nb2.tail(20)
            buy_ratio = recent["sign"].mean()
            score = buy_ratio * 100

            logger.info("Northbound: buy_ratio=%.2f, score=%.1f", buy_ratio, score)
            return _score_with_fallback(score)
        except Exception as e:
            logger.error("Northbound calc failed: %s", e)
            return None

    # ── 情绪维度 ───────────────────────────────────────────────────────────────

    def _calc_turnover(self) -> Optional[float]:
        """换手率（全市场成交额/流通市值）"""
        try:
            stocks = self._get_stock_daily(self.trade_date)
            if stocks.empty or "amount" not in stocks.columns:
                return None

            total_amount = pd.to_numeric(stocks["amount"], errors="coerce").sum()
            total_circ_mv = pd.to_numeric(stocks["circ_mv"], errors="coerce").sum()
            if total_circ_mv <= 0:
                return None

            # circ_mv 单位是万元(tushare), amount 单位是元(baostock), 统一为元
            turnover = total_amount / (total_circ_mv * 10000) * 100  # 百分比

            # 历史换手率分位
            hist = self._get_stock_daily_history()
            if hist.empty or len(hist["trade_date"].unique()) < 60:
                return None

            hist_turnover = hist.groupby("trade_date").apply(
                lambda g: pd.to_numeric(g["amount"], errors="coerce").sum() /
                          max(pd.to_numeric(g["circ_mv"], errors="coerce").sum() * 10000, 1) * 100
            ).dropna()

            if len(hist_turnover) < 60:
                return None

            score = _pct_rank(hist_turnover, turnover) * 100
            logger.info("Turnover: %.4f%%, score=%.1f", turnover, score)
            return _score_with_fallback(score)
        except Exception as e:
            logger.error("Turnover calc failed: %s", e)
            return None

    def _calc_up_down_ratio(self) -> Optional[float]:
        """上涨/下跌家数比（情绪）"""
        try:
            stocks = self._get_stock_daily(self.trade_date)
            if stocks.empty or "pct_change" not in stocks.columns:
                return None

            pct = pd.to_numeric(stocks["pct_change"], errors="coerce").dropna()
            if len(pct) < 100:
                return None

            up = (pct > 0).sum()
            down = (pct < 0).sum()
            if down == 0:
                return 100.0

            ratio = up / down

            # 历史分位
            hist = self._get_stock_daily_history()
            if hist.empty:
                return None

            def _calc_ratio(g):
                p = pd.to_numeric(g["pct_change"], errors="coerce").dropna()
                u = (p > 0).sum()
                d = (p < 0).sum()
                return u / d if d > 0 else 3.0

            hist_ratio = hist.groupby("trade_date").apply(_calc_ratio).dropna()
            if len(hist_ratio) < 60:
                return None

            score = _pct_rank(hist_ratio, ratio) * 100
            score = min(score, 100)  # 封顶
            logger.info("Up/Down ratio: %.2f, score=%.1f", ratio, score)
            return _score_with_fallback(score)
        except Exception as e:
            logger.error("Up/Down ratio calc failed: %s", e)
            return None

    def _calc_limit_up_ratio(self) -> Optional[float]:
        """涨停占比（涨停数/全市场）"""
        try:
            stocks = self._get_stock_daily(self.trade_date)
            if stocks.empty or "pct_change" not in stocks.columns:
                return None

            pct = pd.to_numeric(stocks["pct_change"], errors="coerce")
            total = len(pct.dropna())
            if total < 100:
                return None

            limit_up = (pct >= 9.9).sum()
            ratio = limit_up / total

            # 历史分位
            hist = self._get_stock_daily_history()
            if hist.empty:
                return None

            def _calc_lu(g):
                p = pd.to_numeric(g["pct_change"], errors="coerce")
                t = len(p.dropna())
                return (p >= 9.9).sum() / t if t > 0 else 0

            hist_lu = hist.groupby("trade_date").apply(_calc_lu).dropna()
            if len(hist_lu) < 60:
                return None

            score = _pct_rank(hist_lu, ratio) * 100
            logger.info("Limit-up ratio: %.4f (%d/%d), score=%.1f", ratio, limit_up, total, score)
            return _score_with_fallback(score)
        except Exception as e:
            logger.error("Limit-up ratio calc failed: %s", e)
            return None

    def _calc_limit_down_ratio(self) -> Optional[float]:
        """跌停占比（反向指标: 跌停越多热度越低）"""
        try:
            stocks = self._get_stock_daily(self.trade_date)
            if stocks.empty or "pct_change" not in stocks.columns:
                return None

            pct = pd.to_numeric(stocks["pct_change"], errors="coerce")
            total = len(pct.dropna())
            if total < 100:
                return None

            limit_down = (pct <= -9.9).sum()
            ratio = limit_down / total

            hist = self._get_stock_daily_history()
            if hist.empty:
                return None

            def _calc_ld(g):
                p = pd.to_numeric(g["pct_change"], errors="coerce")
                t = len(p.dropna())
                return (p <= -9.9).sum() / t if t > 0 else 0

            hist_ld = hist.groupby("trade_date").apply(_calc_ld).dropna()
            if len(hist_ld) < 60:
                return None

            # 跌停越多 → 热度越低 → 反向
            score = (1 - _pct_rank(hist_ld, ratio)) * 100
            logger.info("Limit-down ratio: %.4f (%d/%d), score=%.1f",
                        ratio, limit_down, total, score)
            return _score_with_fallback(score)
        except Exception as e:
            logger.error("Limit-down ratio calc failed: %s", e)
            return None

    def _calc_volatility(self) -> Optional[float]:
        """波动率变化（20日收益率标准差趋势, 替代VIX）"""
        try:
            idx = self._get_index_daily()
            if idx.empty or "sh000001" not in idx["index_code"].values:
                return None

            sh = idx[idx["index_code"] == "sh000001"].sort_values("trade_date")
            if len(sh) < 40:
                return None

            sh["pct"] = pd.to_numeric(sh["pct_change"], errors="coerce")
            vol20 = sh["pct"].rolling(20).std().dropna()
            if len(vol20) < 20:
                return None

            current_vol = vol20.iloc[-1]
            score = _pct_rank(vol20, current_vol) * 100
            logger.info("Volatility: %.4f, score=%.1f", current_vol, score)
            return _score_with_fallback(score)
        except Exception as e:
            logger.error("Volatility calc failed: %s", e)
            return None

    # ── 技术维度 ───────────────────────────────────────────────────────────────

    def _calc_above_ma250_ratio(self) -> Optional[float]:
        """站上年线(250日)个股占比"""
        try:
            stocks = self._get_stock_daily(self.trade_date)
            if stocks.empty or "stock_code" not in stocks.columns:
                return None

            # 需要个股250日历史（这里用当前close和历史均值近似）
            hist = self._get_stock_daily_history()
            if hist.empty:
                return None

            latest = hist[hist["trade_date"] == self.trade_date]
            if latest.empty:
                return None

            # 计算每只股票的250日均线
            stock_mean = hist.groupby("stock_code")["close"].mean().reset_index()
            stock_mean.columns = ["stock_code", "ma250_approx"]
            merged = latest.merge(stock_mean, on="stock_code", how="inner")

            above = (merged["close"] > merged["ma250_approx"]).sum()
            total = len(merged)
            if total < 100:
                return None

            ratio = above / total
            # 静态分位: 50%以上为高热度
            score = ratio * 100
            logger.info("Above MA250 ratio: %.4f (%d/%d), score=%.1f", ratio, above, total, score)
            return _score_with_fallback(score)
        except Exception as e:
            logger.error("Above MA250 ratio calc failed: %s", e)
            return None

    def _calc_new_high_ratio(self) -> Optional[float]:
        """创新高占比（250日最高close）— 全市场/成分股通用

        注意: 全市场 stock_daily 中 high/low/open 全为 NULL,
              统一用 close 的 250 日最大值作为基准。
              close >= 250日最高close * 0.98 视为准创新高。
        """
        try:
            hist = self._get_stock_daily_history()
            if hist.empty:
                return None

            latest = hist[hist["trade_date"] == self.trade_date][["stock_code", "close"]].copy()
            latest["close"] = pd.to_numeric(latest["close"], errors="coerce")
            latest = latest.dropna()
            if latest.empty:
                return None

            close_max_250d = (
                hist.groupby("stock_code")["close"]
                .apply(lambda s: pd.to_numeric(s, errors="coerce").rolling(250, min_periods=60).max().iloc[-1])
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
            logger.error("New high ratio calc failed: %s", e)
            return None

    def _calc_deviation_ma250(self) -> Optional[float]:
        """均线偏离度（上证综指 vs 250日均线）"""
        try:
            idx = self._get_index_daily()
            sh = idx[idx["index_code"] == "sh000001"].sort_values("trade_date")
            if len(sh) < 260:
                return None

            sh["close"] = pd.to_numeric(sh["close"], errors="coerce")
            ma250 = sh["close"].rolling(250).mean()
            deviation = (sh["close"].iloc[-1] / ma250.iloc[-1] - 1) * 100

            # 历史分位
            hist_dev = (sh["close"] / ma250 - 1).dropna() * 100
            if len(hist_dev) < 250:
                return None

            score = _pct_rank(hist_dev, deviation) * 100
            logger.info("MA250 deviation: %.2f%%, score=%.1f", deviation, score)
            return _score_with_fallback(score)
        except Exception as e:
            logger.error("MA250 deviation calc failed: %s", e)
            return None

    def _calc_price_volume_divergence(self) -> Optional[float]:
        """量价背离（放量滞涨/缩量上涨检测）"""
        try:
            idx = self._get_index_daily()
            sh = idx[idx["index_code"] == "sh000001"].sort_values("trade_date").tail(20)
            if len(sh) < 20:
                return None

            sh["pct"] = pd.to_numeric(sh["pct_change"], errors="coerce")
            sh["vol"] = pd.to_numeric(sh["volume"], errors="coerce")
            sh["vol_ma5"] = sh["vol"].rolling(5).mean()
            sh["vol_ratio"] = sh["vol"] / sh["vol_ma5"]

            # 近5日: 价格上涨但量比<0.8 → 量价背离(热度虚高)
            recent = sh.tail(5)
            price_up = recent["pct"].mean() > 0
            vol_shrink = recent["vol_ratio"].mean() < 0.8

            if price_up and vol_shrink:
                score = 35  # 量价背离，热度虚高，扣分
                logger.info("Price-volume divergence: UP+SHRINK → score=%.1f", score)
            elif not price_up and not vol_shrink:
                score = 65  # 放量下跌，恐慌
                logger.info("Price-volume: DOWN+EXPAND → score=%.1f", score)
            else:
                score = 50  # 正常

            return _score_with_fallback(score)
        except Exception as e:
            logger.error("Price-volume divergence calc failed: %s", e)
            return None

    # ── 结构维度 ───────────────────────────────────────────────────────────────

    def _calc_sector_divergence(self) -> Optional[float]:
        """申万一级行业分化度（各行业涨幅的标准差）"""
        try:
            stocks = self._get_stock_daily(self.trade_date)
            hist = self._get_stock_daily_history()
            if stocks.empty or hist.empty:
                return None

            # 获取行业分类
            industry_key = f"ind_{self.trade_date}"
            if industry_key not in self._cache:
                self._cache[industry_key] = read_dataframe(
                    "SELECT code, industry FROM stock_industry WHERE industry IS NOT NULL",
                    db_path=self.db_path
                )
            ind_df = self._cache[industry_key]
            if ind_df.empty:
                logger.warning("No industry data available")
                return None

            merged = stocks.merge(ind_df, left_on="stock_code", right_on="code", how="inner")
            if merged.empty:
                return None

            sector_ret = merged.groupby("industry")["pct_change"].mean()
            if len(sector_ret) < 5:
                return None

            divergence = sector_ret.std()
            # 反向: 低分化(普涨)=高分(牛市全面), 高分化(结构性)=低分
            if divergence > 3.0: score = 20
            elif divergence > 2.0: score = 40
            elif divergence > 1.0: score = 60
            else: score = 80

            logger.info("Sector divergence: %.4f%%, score=%.1f", divergence, score)
            return _score_with_fallback(score)
        except Exception as e:
            logger.error("Sector divergence calc failed: %s", e)
            return None

    def _calc_ah_premium_index(self) -> Optional[float]:
        """恒生AH股溢价指数 HSAHP 历史分位

        HSAHP 由东方财富编制，反映同股同权的A股相对港股的溢价程度。
        高溢价 = A股贵 = 高热度，低溢价 = A股便宜 = 低热度。

        分级参考: >150 极贵(历史牛市顶), 130-150 偏贵, 115-130 中性偏高,
                 100-115 中性, 85-100 偏便宜, <85 极便宜(历史底部附近)
        """
        try:
            conn = self._conn()
            ah = pd.read_sql(
                "SELECT trade_date, premium as close FROM ah_premium ORDER BY trade_date",
                conn,
            )
            if ah.empty or len(ah) < 60:
                logger.warning("AH premium: 数据不足 (%d 行)", len(ah))
                return None

            ah["close"] = pd.to_numeric(ah["close"], errors="coerce")
            ah = ah.dropna()

            today = conn.execute(
                "SELECT premium as close FROM ah_premium WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT 1",
                (self.trade_date,),
            ).fetchone()
            if not today or not today[0]:
                return None

            score = _pct_rank(ah["close"], float(today[0])) * 100
            logger.info("AH premium index: %.2f, score=%.1f", today[0], score)
            return _score_with_fallback(score)
        except Exception as e:
            logger.error("AH premium index calc failed: %s", e)
            return None

    # ── 维度合成 ───────────────────────────────────────────────────────────────

    def _series_pct_rank(self, series: pd.Series, value: float) -> float:
        """Forward percentile rank (0.0-1.0): how much of history <= value"""
        if series.empty or pd.isna(value):
            return 0.5
        return (series <= value).sum() / len(series)

    def _calc_buffett_ratio(self) -> Optional[float]:
        """
        Buffett Indicator = M2 / A-share total market cap
        Monthly M2 forward-filled to daily.
        Score: reverse percentile -> low ratio (expensive market) -> high heat
        """
        try:
            conn = self._conn()
            row_m2 = conn.execute(
                "SELECT m2_billion FROM m2_monthly WHERE month <= ? ORDER BY month DESC LIMIT 1",
                (self.trade_date[:7],)
            ).fetchone()
            if not row_m2 or not row_m2[0]:
                return None
            m2 = row_m2[0]
            row_mc = conn.execute(
                "SELECT total_mv FROM stock_market_cap WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT 1",
                (self.trade_date,)
            ).fetchone()
            if not row_mc or not row_mc[0]:
                return None
            total_mv = row_mc[0]
            if total_mv <= 0:
                return None
            # tushare total_mv 单位是万元, M2 单位是亿元, 统一为亿元
            total_mv_yi = total_mv / 10000.0
            ratio = m2 / total_mv_yi

            # Historical reverse percentile
            hist_mc = conn.execute(
                "SELECT trade_date, total_mv FROM stock_market_cap ORDER BY trade_date"
            ).fetchall()
            hist_m2 = conn.execute(
                "SELECT month, m2_billion FROM m2_monthly ORDER BY month"
            ).fetchall()
            m2_map = {m[0]: m[1] for m in hist_m2}
            hist_ratios = []
            for dt, mv in hist_mc:
                ym = dt[:7]
                v2 = None
                for mm in sorted(m2_map):
                    if mm <= ym:
                        v2 = m2_map[mm]
                if v2 and mv > 0:
                    hist_ratios.append((dt, v2 / (mv / 10000.0)))
            if len(hist_ratios) < 20:
                return None
            s = pd.Series({r[0]: r[1] for r in hist_ratios})
            score = (1.0 - self._series_pct_rank(s, ratio)) * 100
            logger.info("Buffett ratio: %.4f, score=%.1f", ratio, score)
            return max(0, min(100, score))
        except Exception as e:
            logger.error("Buffett ratio: %s", e)
            return None

    def _combine_dimension(self, scores: list, label: str) -> Optional[float]:
        """
        子指标合成（动态权重）
        - 过滤 None 和异常值(>3σ)
        - 剩余等权平均
        """
        valid = [s for s in scores if s is not None and not np.isnan(s)]
        if not valid:
            logger.warning("%s: all sub-indicators unavailable", label)
            return None

        # 异常值过滤 (3σ)
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

    # ── 主计算流程 ─────────────────────────────────────────────────────────────

    def calculate(self) -> dict:
        """计算综合热度指数"""
        logger.info("=" * 50)
        logger.info("Calculating heat index for %s", self.trade_date)
        logger.info("=" * 50)

        # 估值 (4项)
        v1 = self._calc_pe_percentile()
        v2 = self._calc_pb_percentile()
        v4 = self._calc_below_net_rate()
        v5 = self._calc_buffett_ratio()
        dim_val = self._combine_dimension([v1, v2, v4, v5], "Valuation")

        # 资金
        f1 = self._calc_margin_ratio()
        f2 = self._calc_northbound()
        dim_fund = self._combine_dimension([f1, f2], "Fund")

        # 情绪
        s1 = self._calc_turnover()
        s2 = self._calc_up_down_ratio()
        s3 = self._calc_limit_up_ratio()
        s4 = self._calc_limit_down_ratio()
        s5 = self._calc_volatility()
        dim_sent = self._combine_dimension([s1, s2, s3, s4, s5], "Sentiment")

        # 技术
        t1 = self._calc_above_ma250_ratio()
        t2 = self._calc_new_high_ratio()
        t3 = self._calc_deviation_ma250()
        t4 = self._calc_price_volume_divergence()
        dim_tech = self._combine_dimension([t1, t2, t3, t4], "Technical")

        # 结构 (2项)
        st1 = self._calc_sector_divergence()
        st2 = self._calc_ah_premium_index()
        dim_struct = self._combine_dimension([st1, st2], "Structure")

        # 综合热度（动态权重）
        composite = self._combine_dimension(
            [dim_val, dim_fund, dim_sent, dim_tech, dim_struct],
            "COMPOSITE"
        )

        result = {
            "trade_date": self.trade_date,
            "composite_score": composite,
            "dim_valuation": dim_val,
            "dim_fund": dim_fund,
            "dim_sentiment": dim_sent,
            "dim_technical": dim_tech,
            "dim_structure": dim_struct,
            "indicators": {
                "valuation": {
                    "PE_percentile": v1,
                    "PB_percentile": v2,
                    "below_net_rate": v4,
                    "buffett_ratio": v5,
                },
                "fund": {"margin_ratio": f1, "northbound": f2},
                "sentiment": {
                    "turnover": s1, "up_down_ratio": s2,
                    "limit_up_ratio": s3, "limit_down_ratio": s4,
                    "volatility": s5,
                },
                "technical": {
                    "above_ma250_ratio": t1, "new_high_ratio": t2,
                    "deviation_ma250": t3, "price_volume_divergence": t4,
                },
                "structure": {"sector_divergence": st1, "ah_premium_index": st2},
            },
        }

        logger.info("FINAL composite score: %s",
                    f"{composite:.1f}" if composite is not None else "FAILED")
        return result


def calculate_heat_index(trade_date: str = None, db_path: str = None) -> dict:
    calc = HeatIndexCalculator(trade_date=trade_date, db_path=db_path)
    return calc.calculate()


# ══════════════════════════════════════════════════════════════════════════════
# 板块热度计算引擎 — 证监会一级行业
# ══════════════════════════════════════════════════════════════════════════════

SECTOR_NAME_MAP = {
    "A01": "农业", "A02": "林业", "A03": "畜牧业", "A04": "渔业",
    "A05": "农林牧渔辅助", "B06": "煤炭开采", "B07": "石油天然气开采",
    "B08": "黑色金属矿采选", "B09": "有色金属矿采选", "B10": "非金属矿采选",
    "B11": "开采辅助", "B12": "其他采矿", "C13": "农副食品加工",
    "C14": "食品制造", "C15": "酒类饮料", "C17": "纺织业", "C18": "纺织服装",
    "C19": "皮革制品", "C21": "家具制造", "C22": "造纸", "C25": "石油加工炼焦",
    "C26": "化学原料", "C27": "医药制造", "C28": "化学纤维", "C29": "橡胶塑料",
    "C30": "非金属矿物制品", "C31": "黑色金属冶炼", "C32": "有色金属冶炼",
    "C33": "金属制品", "C34": "通用设备制造", "C35": "专用设备制造",
    "C36": "汽车制造", "C38": "电气机械器材", "C39": "计算机通信电子",
    "D44": "电力生产供应", "D45": "燃气生产供应", "D46": "水的生产供应",
    "E47": "房屋建筑", "E48": "土木工程", "E49": "建筑装饰",
    "F51": "批发业", "F52": "零售业", "G56": "航空运输", "G58": "道路运输",
    "G60": "仓储邮政", "I63": "电信广播电视", "I64": "互联网相关",
    "I65": "软件信息技术", "J66": "货币金融服务", "J67": "资本市场服务",
    "J68": "保险业", "J69": "其他金融", "K70": "房地产业",
    "L71": "租赁业", "L72": "商务服务", "M73": "研究和试验",
    "M75": "科技推广", "O79": "居民服务", "P82": "教育",
    "Q83": "卫生", "R85": "新闻传媒", "R87": "文化艺术",
    "R89": "娱乐业", "S90": "综合",
}


def _sector_name(code):
    return SECTOR_NAME_MAP.get(code[:3] if code else "", code or "未知")


def _sp_rank(series, value):
    """历史分位 0-1"""
    if series.empty or pd.isna(value):
        return 0.5
    s = series.dropna()
    return float((s < value).sum()) / max(len(s), 1)


def _sp_combine(scores):
    v = [x for x in scores if x is not None and not np.isnan(x)]
    return round(float(np.mean(v)), 1) if v else None


def _sect_valuation(scode, today_df, hist_pe_med, hist_pb_med):
    """估值: 行业中位数PE/PB历史分位(查预计算表)"""
    mem = today_df[today_df["industry"] == scode]
    if len(mem) < 5:
        return None
    out = []
    pe = pd.to_numeric(mem["peTTM"], errors="coerce").dropna().median()
    if pd.notna(pe) and pe > 0:
        h = hist_pe_med[hist_pe_med["industry"] == scode]["peTTM"].dropna()
        if len(h) > 20:
            out.append(_sp_rank(h, float(pe)) * 100)
    pb = pd.to_numeric(mem["pbMRQ"], errors="coerce").dropna().median()
    if pd.notna(pb) and pb > 0:
        h = hist_pb_med[hist_pb_med["industry"] == scode]["pbMRQ"].dropna()
        if len(h) > 20:
            out.append(_sp_rank(h, float(pb)) * 100)
    return _sp_combine(out)


def _sect_sentiment(scode, today_df, hist_to_mean, hist_up_ratio):
    """情绪: 行业换手率 + 涨跌家数比(查预计算表)"""
    mem = today_df[today_df["industry"] == scode]
    if len(mem) < 5:
        return None
    out = []
    tr = pd.to_numeric(mem["turnover_rate"], errors="coerce").dropna()
    if len(tr) > 0:
        ht = hist_to_mean[hist_to_mean["industry"] == scode]["turnover_rate"].dropna()
        if len(ht) > 20:
            out.append(_sp_rank(ht, float(tr.mean())) * 100)
    pc = pd.to_numeric(mem["pct_change"], errors="coerce").dropna()
    if len(pc) > 0:
        ur = float((pc > 0).sum()) / max(len(pc), 1)
        hu = hist_up_ratio[hist_up_ratio["industry"] == scode]["up_ratio"].dropna()
        if len(hu) > 20:
            out.append(_sp_rank(hu, ur) * 100)
    return _sp_combine(out)


def _sect_technical(scode, today_df, hist_df):
    """技术: 站上年线比例 + 创新高比例 (向量化)"""
    mem = today_df[today_df["industry"] == scode]
    if len(mem) < 10:
        return None
    out = []
    cv = mem[["stock_code", "close"]].copy()
    cv["c"] = pd.to_numeric(cv["close"], errors="coerce")
    cv = cv.dropna(subset=["c"])

    # 采样(最多50只)
    if len(cv) > 50:
        cv = cv.sample(50, random_state=42)

    # 构建 (stock_code, close) 对, 批量查历史
    codes = cv["stock_code"].tolist()
    hist_sub = hist_df[(hist_df["industry"] == scode) & (hist_df["stock_code"].isin(codes))].copy()
    hist_sub["c"] = pd.to_numeric(hist_sub["close"], errors="coerce")
    hist_sub = hist_sub.dropna(subset=["c"])

    # 站上年线: 每只股票 200 日均线
    above_n, total_ma = 0, 0
    nh_n, total_nh = 0, 0
    for code, grp in hist_sub.groupby("stock_code"):
        grp = grp.sort_values("trade_date")
        close_200 = grp["c"].tail(200)
        if len(close_200) >= 50:
            total_ma += 1
            ma200 = close_200.mean()
            cur = cv[cv["stock_code"] == code]["c"].values[0]
            if cur > ma200:
                above_n += 1
        close_250 = grp["c"].tail(250)
        if len(close_250) >= 100:
            total_nh += 1
            cur = cv[cv["stock_code"] == code]["c"].values[0]
            if cur >= close_250.max() * 0.99:
                nh_n += 1

    if total_ma >= 5:
        out.append(min(100.0, max(0.0, above_n / total_ma * 100)))
    if total_nh >= 5:
        out.append(min(100.0, max(0.0, nh_n / total_nh * 100)))

    return _sp_combine(out)


def calculate_sector_heat(trade_date: str, db_path: str) -> list:
    """计算所有行业热度, 返回按分数降序的 list[dict]"""
    logger.info("Calculating sector heat for %s ...", trade_date)
    conn = sqlite3.connect(db_path)

    ind_map = pd.read_sql(
        "SELECT code, industry FROM stock_industry WHERE industry IS NOT NULL AND industry != ''", conn
    )

    today = pd.read_sql(
        "SELECT * FROM stock_daily WHERE trade_date = ?", conn, params=[trade_date]
    )
    for col in ("pct_change", "peTTM", "pbMRQ", "close", "turnover_rate"):
        today[col] = pd.to_numeric(today[col], errors="coerce")
    today = today.merge(ind_map, left_on="stock_code", right_on="code", how="inner")
    if today.empty:
        logger.error("No stocks after industry join for %s", trade_date)
        conn.close()
        return []

    # 历史行情 — 仅取近1年, 只含行业分类的股票
    start = (pd.to_datetime(trade_date) - pd.DateOffset(years=1)).strftime("%Y-%m-%d")
    ind_codes = ind_map["code"].tolist()
    # 分批查(每批500个避免SQL过长)
    hist_parts = []
    batch_size = 500
    for i in range(0, len(ind_codes), batch_size):
        batch = ind_codes[i:i+batch_size]
        ph = ",".join(["?"] * len(batch))
        h = pd.read_sql(
            f"SELECT * FROM stock_daily WHERE trade_date >= ? AND trade_date <= ? AND stock_code IN ({ph})",
            conn, params=[start, trade_date] + batch,
        )
        hist_parts.append(h)
    hist = pd.concat(hist_parts, ignore_index=True) if hist_parts else pd.DataFrame()
    for col in ("pct_change", "peTTM", "pbMRQ", "close", "turnover_rate"):
        hist[col] = pd.to_numeric(hist[col], errors="coerce")
    hist = hist.merge(ind_map, left_on="stock_code", right_on="code", how="inner")
    conn.close()

    # 预计算历史分位数基准(避免每个行业重复计算)
    # 行业PE中位数时间序列
    hist_pe_med = hist.groupby(["trade_date", "industry"])["peTTM"].median().reset_index()
    hist_pb_med = hist.groupby(["trade_date", "industry"])["pbMRQ"].median().reset_index()
    hist_to_mean = hist.groupby(["trade_date", "industry"])["turnover_rate"].mean().reset_index()
    hist_up_ratio = hist.groupby(["trade_date", "industry"]).apply(
        lambda _x: float((pd.to_numeric(_x["pct_change"], errors="coerce").dropna() > 0).sum())
                   / max(len(pd.to_numeric(_x["pct_change"], errors="coerce").dropna()), 1)
    ).reset_index()
    hist_up_ratio.columns = ["trade_date", "industry", "up_ratio"]

    # 预计算历史基准表
    _hist_pm = hist.groupby(["trade_date", "industry"])["peTTM"].median().reset_index()
    _hist_bm = hist.groupby(["trade_date", "industry"])["pbMRQ"].median().reset_index()
    _hist_tm = hist.groupby(["trade_date", "industry"])["turnover_rate"].mean().reset_index()

    results = []
    for scode, members in today.groupby("industry"):
        n = len(members)
        if n < 5:
            continue
        val = _sect_valuation(scode, today, _hist_pm, _hist_bm)
        sent = _sect_sentiment(scode, today, _hist_tm, hist_up_ratio)
        tech = _sect_technical(scode, today, hist)

        ws, vs = [], []
        if val is not None: ws.append(0.4); vs.append(val)
        if sent is not None: ws.append(0.3); vs.append(sent)
        if tech is not None: ws.append(0.3); vs.append(tech)
        if not vs:
            continue

        comp = round(sum(v * w for v, w in zip(vs, ws)) / sum(ws), 1)
        comp = max(0.0, min(100.0, comp))
        label = "hot" if comp >= 70 else ("warm" if comp >= 40 else "cold")

        pc = members["pct_change"].dropna()
        avg_pct = round(float(pc.mean()), 2) if len(pc) > 0 else None
        up_r = round(float((pc > 0).sum() / max(len(pc), 1) * 100), 1) if len(pc) > 0 else None

        leader = None
        if len(pc) > 0:
            li = pc.idxmax()
            leader = {"code": str(members.loc[li, "stock_code"]), "pct": round(float(pc.loc[li]), 2)}

        results.append({
            "sector_code": scode,
            "sector_name": _sector_name(scode),
            "n_stocks": int(n),
            "composite_score": comp,
            "heat_label": label,
            "dim_valuation": val,
            "dim_sentiment": sent,
            "dim_technical": tech,
            "avg_pct_change": avg_pct,
            "up_ratio": up_r,
            "leader": leader,
        })

    results.sort(key=lambda x: x["composite_score"], reverse=True)
    for i, r in enumerate(results, 1):
        r["rank"] = i

    logger.info("Sector heat done: %d sectors", len(results))
    return results
