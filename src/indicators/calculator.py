"""
热度指数计算引擎 — 三源合一版
数据源: baostock(stock_daily含peTTM/pbMRQ) + tushare(margin/northbound/bond) + akshare(AH溢价)

5维度 18子指标:
  估值(4): PE分位, PB分位, 股债性价比ERP, 破净率
  资金(2): 融资买入占比, 北向资金方向
  情绪(6): 换手率, 上涨/下跌家数比, 涨停占比, 跌停占比, 波动率, 新增投资者
  技术(5): 站上年线比, 创新高比, 均线偏离度, 量价背离, 技术综合
  结构(1): AH溢价 + 行业分化度

权重规则: 等权 + 异常/0则舍弃, 其余重新等权归一
"""
import logging
import json
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
        self.db_path = db_path
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

    def _calc_pe_percentile(self) -> Optional[float]:
        """全市场PE中位数历史分位 (数据来源: stock_daily.peTTM 中位数)"""
        try:
            stocks_today = self._get_stock_daily(self.trade_date)
            if stocks_today.empty or "peTTM" not in stocks_today.columns:
                logger.warning("PE data not available in stock_daily")
                return None

            # 全市场PE中位数（当日）
            current_pe = pd.to_numeric(stocks_today["peTTM"], errors="coerce").dropna()
            if current_pe.empty:
                return None
            current_pe_med = current_pe.median()

            # 历史PE中位数序列
            hist = self._get_stock_daily_history()
            if hist.empty:
                return None
            hist_pe_by_date = hist.groupby("trade_date").apply(
                lambda g: pd.to_numeric(g["peTTM"], errors="coerce").median()
            ).dropna()
            if len(hist_pe_by_date) < 60:
                return None

            score = _pct_rank(hist_pe_by_date, current_pe_med) * 100
            logger.info("PE percentile: current_med=%.2f, score=%.1f", current_pe_med, score)
            return _score_with_fallback(score)
        except Exception as e:
            logger.error("PE percentile calc failed: %s", e)
            return None

    def _calc_pb_percentile(self) -> Optional[float]:
        """全市场PB中位数历史分位 (数据来源: stock_daily.pbMRQ)"""
        try:
            stocks_today = self._get_stock_daily(self.trade_date)
            if stocks_today.empty or "pbMRQ" not in stocks_today.columns:
                return None

            current_pb = pd.to_numeric(stocks_today["pbMRQ"], errors="coerce").dropna()
            if current_pb.empty:
                return None
            current_pb_med = current_pb.median()

            hist = self._get_stock_daily_history()
            if hist.empty:
                return None
            hist_pb_by_date = hist.groupby("trade_date").apply(
                lambda g: pd.to_numeric(g["pbMRQ"], errors="coerce").median()
            ).dropna()
            if len(hist_pb_by_date) < 60:
                return None

            score = _pct_rank(hist_pb_by_date, current_pb_med) * 100
            logger.info("PB percentile: current_med=%.2f, score=%.1f", current_pb_med, score)
            return _score_with_fallback(score)
        except Exception as e:
            logger.error("PB percentile calc failed: %s", e)
            return None

    def _calc_erp(self) -> Optional[float]:
        """股债性价比 ERP = 1/PE_中位数 - 10Y国债收益率"""
        try:
            stocks_today = self._get_stock_daily(self.trade_date)
            if stocks_today.empty or "peTTM" not in stocks_today.columns:
                return None

            pe_med = pd.to_numeric(stocks_today["peTTM"], errors="coerce").dropna().median()
            if pd.isna(pe_med) or pe_med <= 0:
                return None

            earnings_yield = 1.0 / pe_med * 100  # 百分比

            # 10Y国债收益率
            bond_df = self._get_bond()
            if bond_df.empty or "yield_rate" not in bond_df.columns:
                logger.warning("Bond yield not available, using default 2.5%")
                bond_yield = 2.5
            else:
                bond_yield = pd.to_numeric(bond_df["yield_rate"], errors="coerce").iloc[0]

            erp = earnings_yield - bond_yield

            # 历史ERP分位
            hist = self._get_stock_daily_history()
            if hist.empty:
                return None
            hist_pe_by_date = hist.groupby("trade_date").apply(
                lambda g: pd.to_numeric(g["peTTM"], errors="coerce").median()
            ).dropna()
            if len(hist_pe_by_date) < 60:
                return None

            hist_erp = 1.0 / hist_pe_by_date * 100 - bond_yield
            # ERP越高(股票越便宜) → 分数越低（估值低热度低）→ 反向
            score = _pct_rank_inv(hist_erp, erp) * 100
            logger.info("ERP: %.2f%% (E/P=%.2f%% Bond=%.2f%%), score=%.1f",
                        erp, earnings_yield, bond_yield, score)
            return _score_with_fallback(score)
        except Exception as e:
            logger.error("ERP calc failed: %s", e)
            return None

    def _calc_below_net_rate(self) -> Optional[float]:
        """破净率 = PB<1个股占比"""
        try:
            stocks_today = self._get_stock_daily(self.trade_date)
            if stocks_today.empty or "pbMRQ" not in stocks_today.columns:
                return None

            pb = pd.to_numeric(stocks_today["pbMRQ"], errors="coerce").dropna()
            if len(pb) < 100:
                return None

            below_net = (pb < 1.0).sum() / len(pb)

            # 历史分位
            hist = self._get_stock_daily_history()
            if hist.empty or len(hist["trade_date"].unique()) < 60:
                # 没历史就用静态阈值: 破净率>20%得高分(估值便宜)
                if below_net > 0.15: return 70
                if below_net > 0.10: return 50
                if below_net > 0.05: return 30
                return 10

            hist_bnet = hist.groupby("trade_date").apply(
                lambda g: (pd.to_numeric(g["pbMRQ"], errors="coerce").dropna() < 1.0).mean()
            ).dropna()
            if len(hist_bnet) < 60:
                return None

            score = _pct_rank(hist_bnet, below_net) * 100
            logger.info("Below net rate: %.4f, score=%.1f", below_net, score)
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

            turnover = total_amount / total_circ_mv * 100  # 百分比

            # 历史换手率分位
            hist = self._get_stock_daily_history()
            if hist.empty or len(hist["trade_date"].unique()) < 60:
                return None

            hist_turnover = hist.groupby("trade_date").apply(
                lambda g: pd.to_numeric(g["amount"], errors="coerce").sum() /
                          max(pd.to_numeric(g["circ_mv"], errors="coerce").sum(), 1) * 100
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
        """创新高占比（250日新高）"""
        try:
            hist = self._get_stock_daily_history()
            if hist.empty:
                return None

            latest = hist[hist["trade_date"] == self.trade_date]
            if latest.empty:
                return None

            # 250日最高价
            high_250d = hist.groupby("stock_code")["high"].max().rename("high_250d")
            merged = latest.merge(high_250d.reset_index(), on="stock_code", how="inner")
            merged["close"] = pd.to_numeric(merged["close"], errors="coerce")

            # 当前close >= 250日high的95%算创新高
            new_high = (merged["close"] >= merged["high_250d"] * 0.95).sum()
            total = len(merged)
            if total < 100:
                return None

            ratio = new_high / total
            score = ratio * 100
            logger.info("New high ratio: %.4f (%d/%d), score=%.1f", ratio, new_high, total, score)
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
            # 静态散度阈值: std>3%为高分化(热门赛道集中)
            if divergence > 3.0: score = 80
            elif divergence > 2.0: score = 60
            elif divergence > 1.0: score = 40
            else: score = 20

            logger.info("Sector divergence: %.4f%%, score=%.1f", divergence, score)
            return _score_with_fallback(score)
        except Exception as e:
            logger.error("Sector divergence calc failed: %s", e)
            return None

    # ── 维度合成 ───────────────────────────────────────────────────────────────

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

        # 估值
        v1 = self._calc_pe_percentile()
        v2 = self._calc_pb_percentile()
        v3 = self._calc_erp()
        v4 = self._calc_below_net_rate()
        dim_val = self._combine_dimension([v1, v2, v3, v4], "Valuation")

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

        # 结构
        st1 = self._calc_sector_divergence()
        dim_struct = self._combine_dimension([st1], "Structure")

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
                    "PE_percentile": v1, "PB_percentile": v2,
                    "below_net_rate": v4, "ERP": v3,
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
                "structure": {"sector_divergence": st1},
            },
        }

        logger.info("FINAL composite score: %s",
                    f"{composite:.1f}" if composite is not None else "FAILED")
        return result


def calculate_heat_index(trade_date: str = None, db_path: str = None) -> dict:
    calc = HeatIndexCalculator(trade_date=trade_date, db_path=db_path)
    return calc.calculate()
