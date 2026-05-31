"""
热度指数计算引擎 — tushare + akshare 混合数据源
- 18个子指标计算
- 标准化（历史分位）
- 异常检测 + 动态权重
- 综合热度合成
"""
import logging
import json
from datetime import date, timedelta
from typing import Dict, Optional, Tuple

import pandas as pd
import numpy as np

from src.data.database import read_dataframe, get_conn, DB_PATH

logger = logging.getLogger(__name__)

# 指数代码 (akshare 格式)
INDEX_ALL = "sh000001"
INDEX_HS300 = "sh000300"

LOOKBACK_YEARS = 10


def _percentile_rank(series: pd.Series, value: float) -> float:
    if series.empty or pd.isna(value):
        return np.nan
    return (series < value).sum() / len(series)


class HeatIndexCalculator:
    def __init__(self, trade_date: str = None, db_path: str = None):
        self.trade_date = trade_date or date.today().strftime("%Y-%m-%d")
        self.db_path = db_path
        self.lookback_start = (
            date.fromisoformat(self.trade_date) - timedelta(days=LOOKBACK_YEARS * 365)
        ).strftime("%Y-%m-%d")
        self._cache = {}

    def _get_index(self, code: str) -> pd.DataFrame:
        """指数日行情 (index_daily 表)"""
        if code not in self._cache:
            self._cache[code] = read_dataframe(
                "SELECT * FROM index_daily WHERE index_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
                params=(code, self.lookback_start, self.trade_date),
                db_path=self.db_path
            )
        return self._cache[code]

    def _get_index_pe(self, code: str) -> pd.DataFrame:
        """指数PE/PB历史 (index_pe_history 表, tushare 来源)"""
        key = f"pe_{code}"
        if key not in self._cache:
            self._cache[key] = read_dataframe(
                "SELECT * FROM index_pe_history WHERE index_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
                params=(code, self.lookback_start, self.trade_date),
                db_path=self.db_path
            )
        return self._cache[key]

    def _get_margin(self) -> pd.DataFrame:
        """融资融券 (margin_history 表, tushare 来源: rzye/rzmre)"""
        if "margin" not in self._cache:
            self._cache["margin"] = read_dataframe(
                "SELECT * FROM margin_history WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
                params=(self.lookback_start, self.trade_date), db_path=self.db_path
            )
        return self._cache["margin"]

    def _get_northbound(self) -> pd.DataFrame:
        """北向资金 (northbound_history 表, tushare 来源: hgt/sgt/north_net)"""
        if "nb" not in self._cache:
            self._cache["nb"] = read_dataframe(
                "SELECT * FROM northbound_history WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
                params=(self.lookback_start, self.trade_date), db_path=self.db_path
            )
        return self._cache["nb"]

    def _get_bond(self) -> pd.DataFrame:
        """国债收益率 (bond_yield 表, tushare 来源)"""
        if "bond" not in self._cache:
            self._cache["bond"] = read_dataframe(
                "SELECT * FROM bond_yield WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT 5",
                params=(self.trade_date,), db_path=self.db_path
            )
        return self._cache["bond"]

    def _get_stock_spot(self) -> pd.DataFrame:
        """全市场个股快照 (stock_daily / stock_spot 表, akshare 来源)"""
        if "spot" not in self._cache:
            # 优先用 stock_daily, 其次 stock_spot
            df = read_dataframe(
                "SELECT * FROM stock_daily WHERE trade_date=?",
                params=(self.trade_date,), db_path=self.db_path
            )
            self._cache["spot"] = df
        return self._cache["spot"]

    def _get_ah_premium(self) -> pd.DataFrame:
        """AH溢价 (ah_premium 表, akshare 来源)"""
        if "ah" not in self._cache:
            self._cache["ah"] = read_dataframe(
                "SELECT * FROM ah_premium WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
                params=(self.lookback_start, self.trade_date), db_path=self.db_path
            )
        return self._cache["ah"]

    def _get_limit_up(self) -> pd.DataFrame:
        """涨停数据 (limit_up_daily 表)"""
        if "lu" not in self._cache:
            self._cache["lu"] = read_dataframe(
                "SELECT trade_date, COUNT(*) as cnt FROM limit_up_daily "
                "WHERE trade_date BETWEEN ? AND ? GROUP BY trade_date",
                params=(self.lookback_start, self.trade_date), db_path=self.db_path
            )
        return self._cache["lu"]

    def _get_limit_up_today(self) -> int:
        """今日涨停家数"""
        df = read_dataframe(
            "SELECT COUNT(*) as cnt FROM limit_up_daily WHERE trade_date=?",
            params=(self.trade_date,), db_path=self.db_path
        )
        return int(df["cnt"].iloc[0]) if not df.empty else 0

    # ==========================================
    # ① 估值维度 (数据源: tushare index_dailybasic)
    # ==========================================

    def calc_pe_percentile(self) -> float:
        """全市场PE历史分位数"""
        try:
            df = self._get_index_pe(INDEX_ALL)
            if df.empty or "pe" not in df.columns:
                logger.warning("PE data not available in index_pe_history")
                return np.nan
            current_pe = pd.to_numeric(df["pe"], errors="coerce").iloc[-1]
            if pd.isna(current_pe):
                return np.nan
            hist_pe = pd.to_numeric(df["pe"], errors="coerce").dropna()
            score = _percentile_rank(hist_pe, current_pe) * 100
            logger.info("PE percentile: %.1f (PE=%.2f)", score, current_pe)
            return score
        except Exception as e:
            logger.error("calc_pe_percentile failed: %s", e)
            return np.nan

    def calc_pb_percentile(self) -> float:
        """全市场PB历史分位数"""
        try:
            df = self._get_index_pe(INDEX_ALL)
            if df.empty or "pb" not in df.columns:
                return np.nan
            current_pb = pd.to_numeric(df["pb"], errors="coerce").iloc[-1]
            if pd.isna(current_pb):
                return np.nan
            hist_pb = pd.to_numeric(df["pb"], errors="coerce").dropna()
            score = _percentile_rank(hist_pb, current_pb) * 100
            logger.info("PB percentile: %.1f (PB=%.2f)", score, current_pb)
            return score
        except Exception as e:
            logger.error("calc_pb_percentile failed: %s", e)
            return np.nan

    def calc_below_net_rate(self) -> float:
        """破净率: PB<1 个股占比 (数据源: akshare 全市场快照)"""
        try:
            stocks = self._get_stock_spot()
            if stocks.empty:
                return np.nan
            pb_col = "pb" if "pb" in stocks.columns else "市净率"
            if pb_col not in stocks.columns:
                return np.nan
            valid = pd.to_numeric(stocks[pb_col], errors="coerce").dropna()
            if valid.empty:
                return np.nan
            below = (valid < 1.0).sum()
            rate = below / len(valid) * 100
            score = 100 - rate  # 反向: 破净率越低→热度越高
            logger.info("Below net rate: %.1f%% (%d/%d), score=%.1f",
                        rate, below, len(valid), score)
            return score
        except Exception as e:
            logger.error("calc_below_net_rate failed: %s", e)
            return np.nan

    def calc_erp(self) -> float:
        """股债性价比 ERP = 沪深300 PE倒数 - 10年期国债收益率"""
        try:
            df_pe = self._get_index_pe(INDEX_HS300)
            if df_pe.empty or "pe" not in df_pe.columns:
                return np.nan
            pe = pd.to_numeric(df_pe["pe"], errors="coerce").iloc[-1]
            if pd.isna(pe) or pe <= 0:
                return np.nan
            earnings_yield = 1.0 / pe * 100

            bond_df = self._get_bond()
            if bond_df.empty or "yield_rate" not in bond_df.columns:
                logger.warning("Bond yield not available, using default 2.5%")
                bond_yield = 2.5
            else:
                bond_yield = pd.to_numeric(bond_df["yield_rate"], errors="coerce").iloc[0]

            erp = earnings_yield - bond_yield
            # 历史ERP分位数
            hist_pe = pd.to_numeric(df_pe["pe"], errors="coerce").dropna()
            if hist_pe.empty:
                return np.nan
            hist_erp = 1.0 / hist_pe * 100 - bond_yield
            score = (1 - _percentile_rank(hist_erp, erp)) * 100  # 反向
            logger.info("ERP: %.2f%% (EY=%.2f%%, Bond=%.2f%%), score=%.1f",
                        erp, earnings_yield, bond_yield, score)
            return score
        except Exception as e:
            logger.error("calc_erp failed: %s", e)
            return np.nan

    # ==========================================
    # ② 资金维度 (数据源: tushare margin / moneyflow_hsgt)
    # ==========================================

    def calc_margin_ratio(self) -> float:
        """融资买入额历史分位"""
        try:
            df = self._get_margin()
            if df.empty:
                return np.nan
            # tushare margin 表: rzmre=融资买入额(元)
            buy_col = "rzmre" if "rzmre" in df.columns else "margin_buy"
            if buy_col not in df.columns:
                return np.nan
            df[buy_col] = pd.to_numeric(df[buy_col], errors="coerce")
            current = df[buy_col].tail(5).mean()
            if pd.isna(current):
                return np.nan
            hist = df[buy_col].dropna()
            score = _percentile_rank(hist, current) * 100
            logger.info("Margin buy score: %.1f", score)
            return score
        except Exception as e:
            logger.error("calc_margin_ratio failed: %s", e)
            return np.nan

    def calc_northbound(self) -> float:
        """北向资金20日累计净流入分位"""
        try:
            df = self._get_northbound()
            if df.empty:
                return np.nan
            # tushare: north_net 列，或 hgt+sgt
            if "north_net" in df.columns:
                nb = pd.to_numeric(df["north_net"], errors="coerce")
            elif "hgt" in df.columns and "sgt" in df.columns:
                nb = pd.to_numeric(df["hgt"], errors="coerce").fillna(0) + \
                     pd.to_numeric(df["sgt"], errors="coerce").fillna(0)
            else:
                # 尝试其他列名
                nb_col = [c for c in df.columns if "north" in c.lower() or "净流入" in c]
                if not nb_col:
                    return np.nan
                nb = pd.to_numeric(df[nb_col[0]], errors="coerce")

            current_20d = nb.tail(20).sum()
            if pd.isna(current_20d):
                return np.nan
            hist_20d = nb.rolling(20).sum().dropna()
            score = _percentile_rank(hist_20d, current_20d) * 100
            logger.info("Northbound 20d: %.0f, score=%.1f", current_20d, score)
            return score
        except Exception as e:
            logger.error("calc_northbound failed: %s", e)
            return np.nan

    # ==========================================
    # ③ 情绪维度 (数据源: akshare 全市场快照)
    # ==========================================

    def calc_turnover(self) -> float:
        """换手率分位 (近似: 成交量/历史分位)"""
        try:
            df = self._get_index(INDEX_ALL)
            if df.empty:
                return np.nan
            vol = pd.to_numeric(df["volume"], errors="coerce")
            current = vol.tail(5).mean()
            if pd.isna(current):
                return np.nan
            score = _percentile_rank(vol.dropna(), current) * 100
            logger.info("Turnover score: %.1f", score)
            return score
        except Exception as e:
            logger.error("calc_turnover failed: %s", e)
            return np.nan

    def calc_limit_up_ratio(self) -> float:
        """涨停家数占比"""
        try:
            lu_today = self._get_limit_up_today()
            stocks = self._get_stock_spot()
            total = len(stocks) if not stocks.empty else 5000
            ratio = lu_today / total * 100

            lu_hist = self._get_limit_up()
            if lu_hist.empty:
                return np.nan
            hist_ratios = pd.to_numeric(lu_hist["cnt"], errors="coerce") / total * 100
            score = _percentile_rank(hist_ratios.dropna(), ratio) * 100
            logger.info("Limit up: %.2f%% (%d/%d), score=%.1f", ratio, lu_today, total, score)
            return score
        except Exception as e:
            logger.error("calc_limit_up_ratio failed: %s", e)
            return np.nan

    def calc_new_high_ratio(self) -> float:
        """创新高个股占比"""
        try:
            stocks = self._get_stock_spot()
            if stocks.empty:
                return np.nan
            close_col = "close" if "close" in stocks.columns else "最新价"
            code_col = "stock_code" if "stock_code" in stocks.columns else "代码"
            if close_col not in stocks.columns:
                return np.nan

            high_count, total = 0, 0
            for _, row in stocks.head(100).iterrows():
                code = row[code_col]
                hist = read_dataframe(
                    "SELECT close FROM stock_daily WHERE stock_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 250",
                    params=(code, self.trade_date), db_path=self.db_path
                )
                if len(hist) < 250:
                    continue
                if pd.to_numeric(row[close_col], errors="coerce") >= hist["close"].max():
                    high_count += 1
                total += 1
            if total == 0:
                return np.nan
            ratio = high_count / total * 100
            logger.info("New high ratio: %.1f%% (%d/%d)", ratio, high_count, total)
            return ratio
        except Exception as e:
            logger.error("calc_new_high_ratio failed: %s", e)
            return np.nan

    def calc_above_ma250_ratio(self) -> float:
        """站上年线个股比例"""
        try:
            stocks = self._get_stock_spot()
            if stocks.empty:
                return np.nan
            close_col = "close" if "close" in stocks.columns else "最新价"
            code_col = "stock_code" if "stock_code" in stocks.columns else "代码"
            if close_col not in stocks.columns:
                return np.nan

            above, total = 0, 0
            for _, row in stocks.head(100).iterrows():
                code = row[code_col]
                hist = read_dataframe(
                    "SELECT close FROM stock_daily WHERE stock_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 250",
                    params=(code, self.trade_date), db_path=self.db_path
                )
                if len(hist) < 250:
                    continue
                ma250 = pd.to_numeric(hist["close"], errors="coerce").mean()
                if pd.to_numeric(row[close_col], errors="coerce") > ma250:
                    above += 1
                total += 1
            if total == 0:
                return np.nan
            ratio = above / total * 100
            logger.info("Above MA250: %.1f%% (%d/%d)", ratio, above, total)
            return ratio
        except Exception as e:
            logger.error("calc_above_ma250_ratio failed: %s", e)
            return np.nan

    def calc_up_down_ratio(self) -> float:
        """涨跌家数比（市场宽度）"""
        try:
            stocks = self._get_stock_spot()
            if stocks.empty:
                return np.nan
            pct_col = "pct_change" if "pct_change" in stocks.columns else "涨跌幅"
            if pct_col not in stocks.columns:
                return np.nan
            pct = pd.to_numeric(stocks[pct_col], errors="coerce")
            up = (pct > 0).sum()
            down = (pct < 0).sum()
            if down == 0:
                return 100.0
            ratio = up / down
            score = min(100, max(0, (ratio - 0.33) / 2.67 * 100))
            logger.info("Up/Down ratio: %.2f (%d/%d), score=%.1f", ratio, up, down, score)
            return score
        except Exception as e:
            logger.error("calc_up_down_ratio failed: %s", e)
            return np.nan

    # ==========================================
    # ④ 技术维度 (数据源: index_daily 指数日行情)
    # ==========================================

    def calc_deviation_from_ma250(self) -> float:
        """指数偏离年线幅度"""
        try:
            df = self._get_index(INDEX_ALL)
            if df.empty or len(df) < 250:
                return np.nan
            close = pd.to_numeric(df["close"], errors="coerce")
            ma250 = close.rolling(250).mean()
            current_dev = (close.iloc[-1] / ma250.iloc[-1] - 1) * 100
            hist_dev = (close / ma250 - 1) * 100
            score = _percentile_rank(hist_dev.dropna(), current_dev) * 100
            logger.info("Deviation from MA250: %.1f%%, score=%.1f", current_dev, score)
            return score
        except Exception as e:
            logger.error("calc_deviation_from_ma250 failed: %s", e)
            return np.nan

    def calc_volatility_change(self) -> float:
        """20日波动率变化"""
        try:
            df = self._get_index(INDEX_ALL)
            if df.empty or len(df) < 30:
                return np.nan
            returns = pd.to_numeric(df["close"], errors="coerce").pct_change().dropna()
            vol_20 = returns.tail(20).std() * np.sqrt(252) * 100
            vol_prev = returns.tail(25).head(20).std() * np.sqrt(252) * 100
            change = vol_20 - vol_prev
            hist_vol = returns.rolling(20).std().dropna() * np.sqrt(252) * 100
            hist_change = hist_vol.diff().dropna()
            score = _percentile_rank(hist_change, change) * 100
            logger.info("Vol change: %.1f%% (%.1f→%.1f), score=%.1f", change, vol_prev, vol_20, score)
            return score
        except Exception as e:
            logger.error("calc_volatility_change failed: %s", e)
            return np.nan

    def calc_rsi(self) -> float:
        """RSI(14)"""
        try:
            df = self._get_index(INDEX_ALL)
            if df.empty or len(df) < 15:
                return np.nan
            close = pd.to_numeric(df["close"], errors="coerce")
            delta = close.diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            current_rsi = rsi.iloc[-1]
            if pd.isna(current_rsi):
                return np.nan
            logger.info("RSI(14): %.1f", current_rsi)
            return current_rsi
        except Exception as e:
            logger.error("calc_rsi failed: %s", e)
            return np.nan

    def calc_high_low_diff(self) -> float:
        """创新高 vs 创新低家数差"""
        try:
            stocks = self._get_stock_spot()
            if stocks.empty:
                return np.nan
            close_col = "close" if "close" in stocks.columns else "最新价"
            code_col = "stock_code" if "stock_code" in stocks.columns else "代码"
            if close_col not in stocks.columns:
                return np.nan

            new_high, new_low, total = 0, 0, 0
            for _, row in stocks.head(100).iterrows():
                code = row[code_col]
                hist = read_dataframe(
                    "SELECT close FROM stock_daily WHERE stock_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 250",
                    params=(code, self.trade_date), db_path=self.db_path
                )
                if len(hist) < 250:
                    continue
                if pd.to_numeric(row[close_col], errors="coerce") >= hist["close"].max():
                    new_high += 1
                if pd.to_numeric(row[close_col], errors="coerce") <= hist["close"].min():
                    new_low += 1
                total += 1
            if total == 0:
                return np.nan
            diff = new_high - new_low
            score = min(100, max(0, 50 + diff * 5))
            logger.info("High-Low diff: %d (%d-%d), score=%.1f", diff, new_high, new_low, score)
            return score
        except Exception as e:
            logger.error("calc_high_low_diff failed: %s", e)
            return np.nan

    # ==========================================
    # ⑤ 结构维度
    # ==========================================

    def calc_ah_premium_percentile(self) -> float:
        """AH溢价分位"""
        try:
            df = self._get_ah_premium()
            if df.empty:
                return np.nan
            current = pd.to_numeric(df["premium"], errors="coerce").iloc[-1]
            hist = pd.to_numeric(df["premium"], errors="coerce").dropna()
            score = _percentile_rank(hist, current) * 100
            logger.info("AH premium: %.1f, score=%.1f", current, score)
            return score
        except Exception as e:
            logger.error("calc_ah_premium_percentile failed: %s", e)
            return np.nan

    def calc_equal_weighted_diff(self) -> float:
        """等权 vs 加权涨幅差"""
        try:
            stocks = self._get_stock_spot()
            if stocks.empty:
                return np.nan
            pct_col = "pct_change" if "pct_change" in stocks.columns else "涨跌幅"
            if pct_col not in stocks.columns:
                return np.nan
            pct = pd.to_numeric(stocks[pct_col], errors="coerce")
            equal_avg = pct.mean()
            amt_col = None
            for c in ["amount", "成交额", "成交量"]:
                if c in stocks.columns:
                    amt_col = c
                    break
            if amt_col:
                weights = pd.to_numeric(stocks[amt_col], errors="coerce").fillna(0)
                weighted_avg = np.average(pct.dropna(), weights=weights[pct.notna()]) if weights.sum() > 0 else equal_avg
            else:
                weighted_avg = equal_avg
            diff = equal_avg - weighted_avg
            score = min(100, max(0, 50 + diff * 10))
            logger.info("EW diff: %.2f%% (equal=%.2f, weighted=%.2f), score=%.1f",
                        diff, equal_avg, weighted_avg, score)
            return score
        except Exception as e:
            logger.error("calc_equal_weighted_diff failed: %s", e)
            return np.nan

    # ==========================================
    # 综合计算
    # ==========================================

    def _filter_and_average(self, scores: Dict[str, float]) -> Tuple[float, int, int]:
        """过滤异常值后等权平均，返回 (平均分, 有效数, 排除数)"""
        valid = {}
        excluded = 0
        for name, score in scores.items():
            if pd.isna(score):
                excluded += 1
                continue
            valid[name] = score

        if not valid:
            return np.nan, 0, excluded

        # Z-score 异常检测
        if len(valid) >= 3:
            vals = list(valid.values())
            mean, std = np.mean(vals), np.std(vals)
            if std > 0:
                to_remove = [k for k, v in valid.items() if abs(v - mean) / std > 3]
                for k in to_remove:
                    del valid[k]
                    excluded += 1
                    logger.warning("Indicator %s excluded (z-score > 3)" % k)

        if not valid:
            return np.nan, 0, excluded

        avg = np.mean(list(valid.values()))
        return avg, len(valid), excluded

    def calculate(self) -> Dict:
        """计算综合热度指数"""
        logger.info("=" * 60)
        logger.info("Calculating Heat Index for %s", self.trade_date)
        logger.info("=" * 60)

        # ① 估值
        val_scores = {
            "PE_percentile": self.calc_pe_percentile(),
            "PB_percentile": self.calc_pb_percentile(),
            "below_net_rate": self.calc_below_net_rate(),
            "ERP": self.calc_erp(),
        }
        dim_val, n, excl = self._filter_and_average(val_scores)
        logger.info("Valuation: %.1f (%d indicators, %d excluded)", dim_val, n, excl)

        # ② 资金
        fund_scores = {
            "margin_ratio": self.calc_margin_ratio(),
            "northbound": self.calc_northbound(),
        }
        dim_fund, n, excl = self._filter_and_average(fund_scores)
        logger.info("Fund: %.1f (%d indicators, %d excluded)", dim_fund, n, excl)

        # ③ 情绪
        sent_scores = {
            "turnover": self.calc_turnover(),
            "limit_up_ratio": self.calc_limit_up_ratio(),
            "new_high_ratio": self.calc_new_high_ratio(),
            "above_ma250_ratio": self.calc_above_ma250_ratio(),
            "up_down_ratio": self.calc_up_down_ratio(),
        }
        dim_sent, n, excl = self._filter_and_average(sent_scores)
        logger.info("Sentiment: %.1f (%d indicators, %d excluded)", dim_sent, n, excl)

        # ④ 技术
        tech_scores = {
            "deviation_ma250": self.calc_deviation_from_ma250(),
            "volatility_change": self.calc_volatility_change(),
            "RSI": self.calc_rsi(),
            "high_low_diff": self.calc_high_low_diff(),
        }
        dim_tech, n, excl = self._filter_and_average(tech_scores)
        logger.info("Technical: %.1f (%d indicators, %d excluded)", dim_tech, n, excl)

        # ⑤ 结构
        struct_scores = {
            "ah_premium": self.calc_ah_premium_percentile(),
            "equal_weighted_diff": self.calc_equal_weighted_diff(),
        }
        dim_struct, n, excl = self._filter_and_average(struct_scores)
        logger.info("Structure: %.1f (%d indicators, %d excluded)", dim_struct, n, excl)

        # 综合
        dims = [dim_val, dim_fund, dim_sent, dim_tech, dim_struct]
        valid_dims = [s for s in dims if not pd.isna(s)]
        composite = round(np.mean(valid_dims), 1) if valid_dims else None

        result = {
            "trade_date": self.trade_date,
            "composite_score": composite,
            "dim_valuation": round(dim_val, 1) if not pd.isna(dim_val) else None,
            "dim_fund": round(dim_fund, 1) if not pd.isna(dim_fund) else None,
            "dim_sentiment": round(dim_sent, 1) if not pd.isna(dim_sent) else None,
            "dim_technical": round(dim_tech, 1) if not pd.isna(dim_tech) else None,
            "dim_structure": round(dim_struct, 1) if not pd.isna(dim_struct) else None,
            "indicators": {
                "valuation": {k: round(v, 1) if not pd.isna(v) else None for k, v in val_scores.items()},
                "fund": {k: round(v, 1) if not pd.isna(v) else None for k, v in fund_scores.items()},
                "sentiment": {k: round(v, 1) if not pd.isna(v) else None for k, v in sent_scores.items()},
                "technical": {k: round(v, 1) if not pd.isna(v) else None for k, v in tech_scores.items()},
                "structure": {k: round(v, 1) if not pd.isna(v) else None for k, v in struct_scores.items()},
            }
        }

        logger.info("COMPOSITE HEAT INDEX: %s", composite)
        return result


def calculate_heat_index(trade_date: str = None, db_path: str = None) -> Dict:
    return HeatIndexCalculator(trade_date=trade_date, db_path=db_path).calculate()


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    d = sys.argv[1] if len(sys.argv) > 1 else None
    r = calculate_heat_index(trade_date=d)
    print(json.dumps(r, ensure_ascii=False, indent=2))
