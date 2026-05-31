"""
热度指数计算引擎
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

# 指数代码
INDEX_ALL = "sh000001"          # 上证综指作为全市场代理
INDEX_CHINEXT = "sz399006"      # 创业板
INDEX_HS300 = "sh000300"        # 沪深300
INDEX_ZZ500 = "sh000905"        # 中证500
INDEX_ZZ1000 = "sh000852"       # 中证1000
INDEX_BSE = "bj430047"          # 北证50

LOOKBACK_YEARS = 10             # 历史分位窗口（年）
MIN_HISTORY_DAYS = 250          # 最少需要的历史交易日


def _percentile_rank(series: pd.Series, value: float) -> float:
    """计算 value 在 series 中的分位数（0-1）"""
    if series.empty or pd.isna(value):
        return np.nan
    return (series < value).sum() / len(series)


def _safe_divide(a, b):
    """安全除法"""
    if b and b != 0:
        return a / b
    return np.nan


def _zscore_filter(value: float, series: pd.Series, threshold: float = 3.0) -> bool:
    """Z-Score 异常检测，返回 True 表示异常"""
    if series.empty or len(series) < 20:
        return False
    mean = series.mean()
    std = series.std()
    if std == 0:
        return False
    z = abs(value - mean) / std
    return z > threshold


class HeatIndexCalculator:
    """热度指数计算器"""

    def __init__(self, trade_date: str = None, db_path: str = None):
        self.trade_date = trade_date or date.today().strftime("%Y-%m-%d")
        self.db_path = db_path
        self.lookback_start = (
            date.fromisoformat(self.trade_date) - timedelta(days=LOOKBACK_YEARS * 365)
        ).strftime("%Y-%m-%d")

        # 缓存
        self._index_data = {}
        self._stock_data = None
        self._results = {}  # {indicator_name: heat_score}

    def _get_index(self, code: str) -> pd.DataFrame:
        """获取指数历史数据"""
        if code not in self._index_data:
            df = read_dataframe(
                "SELECT * FROM index_daily WHERE index_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
                params=(code, self.lookback_start, self.trade_date),
                db_path=self.db_path
            )
            self._index_data[code] = df
        return self._index_data[code]

    def _get_stock_latest(self) -> pd.DataFrame:
        """获取最新交易日全市场个股数据"""
        if self._stock_data is None:
            self._stock_data = read_dataframe(
                "SELECT * FROM stock_daily WHERE trade_date=?",
                params=(self.trade_date,),
                db_path=self.db_path
            )
        return self._stock_data

    def _get_stock_history(self, code: str) -> pd.DataFrame:
        """获取单只股票历史数据"""
        return read_dataframe(
            "SELECT * FROM stock_daily WHERE stock_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
            params=(code, self.lookback_start, self.trade_date),
            db_path=self.db_path
        )

    # ==========================================
    # ① 估值维度
    # ==========================================

    def calc_pe_percentile(self) -> float:
        """全市场PE历史分位数（近10年）"""
        try:
            df = self._get_index(INDEX_ALL)
            if df.empty or "pe" not in df.columns:
                logger.warning("PE data not available")
                return np.nan
            current_pe = df["pe"].iloc[-1]
            if pd.isna(current_pe):
                return np.nan
            hist_pe = df["pe"].dropna()
            score = _percentile_rank(hist_pe, current_pe) * 100
            logger.info("PE percentile: %.1f (current PE=%.2f)", score, current_pe)
            return score
        except Exception as e:
            logger.error("calc_pe_percentile failed: %s", e)
            return np.nan

    def calc_pb_percentile(self) -> float:
        """全市场PB历史分位数"""
        try:
            df = self._get_index(INDEX_ALL)
            if df.empty or "pb" not in df.columns:
                return np.nan
            current_pb = df["pb"].iloc[-1]
            if pd.isna(current_pb):
                return np.nan
            hist_pb = df["pb"].dropna()
            score = _percentile_rank(hist_pb, current_pb) * 100
            logger.info("PB percentile: %.1f (current PB=%.2f)", score, current_pb)
            return score
        except Exception as e:
            logger.error("calc_pb_percentile failed: %s", e)
            return np.nan

    def calc_below_net_rate(self) -> float:
        """破净率：股价低于每股净资产的个股占比"""
        try:
            stocks = self._get_stock_latest()
            if stocks.empty or "pb" not in stocks.columns:
                return np.nan
            valid = stocks["pb"].dropna()
            if valid.empty:
                return np.nan
            below = (valid < 1.0).sum()
            rate = below / len(valid) * 100  # 百分比
            # 破净率越高 = 市场越冷 = 热度越低，需要反向
            # 但破净率高本身代表低估值/低热度，所以直接映射：破净率高→热度低
            score = 100 - rate  # 反向：破净率越低，热度越高
            logger.info("Below net rate: %.1f%% (%d/%d), heat score=%.1f",
                        rate, below, len(valid), score)
            return score
        except Exception as e:
            logger.error("calc_below_net_rate failed: %s", e)
            return np.nan

    def calc_erp(self) -> float:
        """股债性价比（ERP）= 沪深300 PE倒数 - 10年期国债收益率"""
        try:
            # 获取沪深300 PE
            df_300 = self._get_index(INDEX_HS300)
            if df_300.empty or "pe" not in df_300.columns:
                return np.nan
            pe_300 = df_300["pe"].iloc[-1]
            if pd.isna(pe_300) or pe_300 <= 0:
                return np.nan
            earnings_yield = 1.0 / pe_300 * 100  # 百分比

            # 获取10年期国债收益率（从本地数据库）
            bond_df = read_dataframe(
                "SELECT yield_10y FROM bond_yield WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT 1",
                params=(self.trade_date,), db_path=self.db_path
            )
            if bond_df.empty:
                logger.warning("Bond yield data not available, using default 2.5%")
                bond_yield = 2.5
            else:
                bond_yield = bond_df["yield_10y"].iloc[0]

            erp = earnings_yield - bond_yield
            # ERP越高 = 股票越便宜 = 热度越低，反向映射
            # 使用历史ERP分位
            hist_pe = df_300["pe"].dropna()
            if hist_pe.empty:
                return np.nan
            hist_erp = 1.0 / hist_pe * 100 - bond_yield  # 简化：用当前债券收益率
            score = (1 - _percentile_rank(hist_erp, erp)) * 100  # 反向
            logger.info("ERP: %.2f%% (EY=%.2f%%, Bond=%.2f%%), score=%.1f",
                        erp, earnings_yield, bond_yield, score)
            return score
        except Exception as e:
            logger.error("calc_erp failed: %s", e)
            return np.nan

    # ==========================================
    # ② 资金维度
    # ==========================================

    def calc_margin_ratio(self) -> float:
        """融资买入额占全市场成交额比例"""
        try:
            margin_df = read_dataframe(
                "SELECT * FROM margin_daily WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
                params=(self.lookback_start, self.trade_date), db_path=self.db_path
            )
            if margin_df.empty:
                return np.nan
            # 5日移动平均
            margin_df["margin_buy"] = pd.to_numeric(margin_df["margin_buy"], errors="coerce")
            current = margin_df["margin_buy"].tail(5).mean()
            if pd.isna(current):
                return np.nan
            hist = margin_df["margin_buy"].dropna()
            score = _percentile_rank(hist, current) * 100
            logger.info("Margin buy ratio score: %.1f", score)
            return score
        except Exception as e:
            logger.error("calc_margin_ratio failed: %s", e)
            return np.nan

    def calc_northbound(self) -> float:
        """北向资金20日累计净流入"""
        try:
            nb_df = read_dataframe(
                "SELECT * FROM northbound_daily WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
                params=(self.lookback_start, self.trade_date), db_path=self.db_path
            )
            if nb_df.empty:
                return np.nan
            nb_df["净流入"] = pd.to_numeric(nb_df["净流入"], errors="coerce")
            current_20d = nb_df["净流入"].tail(20).sum()
            if pd.isna(current_20d):
                return np.nan
            # 历史20日累计
            hist_20d = nb_df["净流入"].rolling(20).sum().dropna()
            if hist_20d.empty:
                return np.nan
            score = _percentile_rank(hist_20d, current_20d) * 100
            logger.info("Northbound 20d: %.1f亿, score=%.1f", current_20d, score)
            return score
        except Exception as e:
            logger.error("calc_northbound failed: %s", e)
            return np.nan

    # ==========================================
    # ③ 情绪维度
    # ==========================================

    def calc_turnover(self) -> float:
        """全A换手率（5日均值）"""
        try:
            df = self._get_index(INDEX_ALL)
            if df.empty:
                return np.nan
            # 用成交额/总市值近似换手率
            if "volume" in df.columns:
                vol = df["volume"].tail(5).mean()
                hist_vol = df["volume"].dropna()
                score = _percentile_rank(hist_vol, vol) * 100
                logger.info("Turnover score: %.1f", score)
                return score
            return np.nan
        except Exception as e:
            logger.error("calc_turnover failed: %s", e)
            return np.nan

    def calc_limit_up_ratio(self) -> float:
        """涨停家数占比"""
        try:
            lu_df = read_dataframe(
                "SELECT COUNT(*) as cnt FROM limit_up_daily WHERE trade_date=?",
                params=(self.trade_date,), db_path=self.db_path
            )
            if lu_df.empty:
                return np.nan
            lu_count = lu_df["cnt"].iloc[0]

            stocks = self._get_stock_latest()
            total = len(stocks) if not stocks.empty else 5000
            ratio = lu_count / total * 100

            # 历史分位
            hist_lu = read_dataframe(
                "SELECT trade_date, COUNT(*) as cnt FROM limit_up_daily WHERE trade_date BETWEEN ? AND ? GROUP BY trade_date",
                params=(self.lookback_start, self.trade_date), db_path=self.db_path
            )
            if hist_lu.empty:
                return np.nan
            hist_ratios = hist_lu["cnt"] / total * 100
            score = _percentile_rank(hist_ratios, ratio) * 100
            logger.info("Limit up ratio: %.2f%% (%d/%d), score=%.1f", ratio, lu_count, total, score)
            return score
        except Exception as e:
            logger.error("calc_limit_up_ratio failed: %s", e)
            return np.nan

    def calc_new_high_ratio(self) -> float:
        """近一年创新高个股占比"""
        try:
            stocks = self._get_stock_latest()
            if stocks.empty:
                return np.nan
            # 简化：用当日收盘价 > 250日均线作为近似
            # 精确计算需要每只股票的历史数据
            high_count = 0
            total = 0
            for _, row in stocks.head(100).iterrows():  # MVP 先算前100只
                code = row["stock_code"]
                hist = self._get_stock_history(code)
                if len(hist) < 250:
                    continue
                ma250 = hist["close"].tail(250).mean()
                if row["close"] > ma250:
                    high_count += 1
                total += 1
            if total == 0:
                return np.nan
            ratio = high_count / total * 100
            score = ratio  # 直接使用比例作为热度分（0-100）
            logger.info("New high ratio: %.1f%% (%d/%d), score=%.1f", ratio, high_count, total, score)
            return score
        except Exception as e:
            logger.error("calc_new_high_ratio failed: %s", e)
            return np.nan

    def calc_above_ma250_ratio(self) -> float:
        """站上年线个股比例"""
        try:
            stocks = self._get_stock_latest()
            if stocks.empty:
                return np.nan
            above = 0
            total = 0
            for _, row in stocks.head(100).iterrows():
                code = row["stock_code"]
                hist = self._get_stock_history(code)
                if len(hist) < 250:
                    continue
                ma250 = hist["close"].tail(250).mean()
                if row["close"] > ma250:
                    above += 1
                total += 1
            if total == 0:
                return np.nan
            ratio = above / total * 100
            score = ratio  # 直接使用比例
            logger.info("Above MA250 ratio: %.1f%% (%d/%d), score=%.1f", ratio, above, total, score)
            return score
        except Exception as e:
            logger.error("calc_above_ma250_ratio failed: %s", e)
            return np.nan

    def calc_up_down_ratio(self) -> float:
        """上涨家数/下跌家数比（市场宽度）"""
        try:
            stocks = self._get_stock_latest()
            if stocks.empty or "pct_change" not in stocks.columns:
                return np.nan
            up = (stocks["pct_change"] > 0).sum()
            down = (stocks["pct_change"] < 0).sum()
            if down == 0:
                return 100.0
            ratio = up / down
            # 映射到 0-100：ratio=1 → 50, ratio=3 → 100, ratio=0.33 → 0
            score = min(100, max(0, (ratio - 0.33) / 2.67 * 100))
            logger.info("Up/Down ratio: %.2f (%d up / %d down), score=%.1f", ratio, up, down, score)
            return score
        except Exception as e:
            logger.error("calc_up_down_ratio failed: %s", e)
            return np.nan

    # ==========================================
    # ④ 技术维度
    # ==========================================

    def calc_deviation_from_ma250(self) -> float:
        """全市场等权指数偏离年线幅度"""
        try:
            df = self._get_index(INDEX_ALL)
            if df.empty or len(df) < 250:
                return np.nan
            ma250 = df["close"].tail(250).mean()
            current = df["close"].iloc[-1]
            deviation = (current / ma250 - 1) * 100  # 百分比偏离
            # 历史分位
            hist_close = df["close"]
            hist_ma250 = hist_close.rolling(250).mean()
            hist_dev = (hist_close / hist_ma250 - 1) * 100
            hist_dev = hist_dev.dropna()
            if hist_dev.empty:
                return np.nan
            score = _percentile_rank(hist_dev, deviation) * 100
            logger.info("Deviation from MA250: %.1f%%, score=%.1f", deviation, score)
            return score
        except Exception as e:
            logger.error("calc_deviation_from_ma250 failed: %s", e)
            return np.nan

    def calc_volatility_change(self) -> float:
        """20日历史波动率变化"""
        try:
            df = self._get_index(INDEX_ALL)
            if df.empty or len(df) < 25:
                return np.nan
            returns = df["close"].pct_change().dropna()
            vol_20 = returns.tail(20).std() * np.sqrt(252) * 100  # 年化波动率
            vol_prev = returns.tail(25).head(20).std() * np.sqrt(252) * 100
            change = vol_20 - vol_prev
            # 历史分位
            hist_vol = returns.rolling(20).std().dropna() * np.sqrt(252) * 100
            hist_change = hist_vol.diff().dropna()
            if hist_change.empty:
                return np.nan
            score = _percentile_rank(hist_change, change) * 100
            logger.info("Volatility change: %.1f%% (%.1f%% -> %.1f%%), score=%.1f",
                        change, vol_prev, vol_20, score)
            return score
        except Exception as e:
            logger.error("calc_volatility_change failed: %s", e)
            return np.nan

    def calc_rsi(self) -> float:
        """RSI（14日）"""
        try:
            df = self._get_index(INDEX_ALL)
            if df.empty or len(df) < 15:
                return np.nan
            delta = df["close"].diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            current_rsi = rsi.iloc[-1]
            if pd.isna(current_rsi):
                return np.nan
            # RSI 本身就是 0-100，直接作为热度分
            score = current_rsi
            logger.info("RSI(14): %.1f, score=%.1f", current_rsi, score)
            return score
        except Exception as e:
            logger.error("calc_rsi failed: %s", e)
            return np.nan

    def calc_high_low_diff(self) -> float:
        """创新高与创新低家数之差标准化"""
        try:
            stocks = self._get_stock_latest()
            if stocks.empty:
                return np.nan
            new_high = 0
            new_low = 0
            for _, row in stocks.head(100).iterrows():
                code = row["stock_code"]
                hist = self._get_stock_history(code)
                if len(hist) < 250:
                    continue
                current = row["close"]
                if current == hist["close"].tail(250).max():
                    new_high += 1
                if current == hist["close"].tail(250).min():
                    new_low += 1
            diff = new_high - new_low
            # 映射到 0-100
            score = min(100, max(0, (diff + 50) / 100 * 100))
            logger.info("High-Low diff: %d (%d high - %d low), score=%.1f", diff, new_high, new_low, score)
            return score
        except Exception as e:
            logger.error("calc_high_low_diff failed: %s", e)
            return np.nan

    # ==========================================
    # ⑤ 结构维度
    # ==========================================

    def calc_ah_premium_percentile(self) -> float:
        """AH溢价指数分位数"""
        try:
            df = read_dataframe(
                "SELECT * FROM ah_premium WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
                params=(self.lookback_start, self.trade_date), db_path=self.db_path
            )
            if df.empty:
                return np.nan
            current = df["premium"].iloc[-1]
            hist = df["premium"].dropna()
            score = _percentile_rank(hist, current) * 100
            logger.info("AH premium: %.1f, percentile score=%.1f", current, score)
            return score
        except Exception as e:
            logger.error("calc_ah_premium_percentile failed: %s", e)
            return np.nan

    def calc_equal_weighted_diff(self) -> float:
        """等权加权涨幅差"""
        try:
            stocks = self._get_stock_latest()
            if stocks.empty or "pct_change" not in stocks.columns:
                return np.nan
            equal_avg = stocks["pct_change"].mean()
            # 加权平均（用成交额加权）
            if "amount" in stocks.columns:
                weighted_avg = np.average(
                    stocks["pct_change"].dropna(),
                    weights=stocks.loc[stocks["pct_change"].notna(), "amount"].fillna(0)
                )
            else:
                weighted_avg = equal_avg
            diff = equal_avg - weighted_avg
            # 差值越大 = 小盘越强 = 市场越热
            # 映射到 0-100
            score = min(100, max(0, 50 + diff * 10))
            logger.info("Equal-weighted diff: %.2f%% (equal=%.2f%%, weighted=%.2f%%), score=%.1f",
                        diff, equal_avg, weighted_avg, score)
            return score
        except Exception as e:
            logger.error("calc_equal_weighted_diff failed: %s", e)
            return np.nan

    # ==========================================
    # 综合计算
    # ==========================================

    def _filter_and_average(self, scores: Dict[str, float]) -> Tuple[float, Dict[str, float]]:
        """过滤异常值后等权平均"""
        valid = {}
        excluded = {}
        for name, score in scores.items():
            if pd.isna(score) or score == 0:
                excluded[name] = score
                continue
            # Z-score 异常检测
            all_scores = [s for s in scores.values() if not pd.isna(s) and s != 0]
            if len(all_scores) >= 3:
                mean = np.mean(all_scores)
                std = np.std(all_scores)
                if std > 0 and abs(score - mean) / std > 3:
                    excluded[name] = score
                    logger.warning("Indicator %s excluded (score=%.1f, mean=%.1f, std=%.1f)",
                                   name, score, mean, std)
                    continue
            valid[name] = score

        if not valid:
            return np.nan, excluded

        avg = np.mean(list(valid.values()))
        logger.info("Dimension average: %.1f (from %d indicators, %d excluded)",
                    avg, len(valid), len(excluded))
        return avg, excluded

    def calculate(self) -> Dict:
        """计算综合热度指数"""
        logger.info("=" * 60)
        logger.info("Calculating Heat Index for %s", self.trade_date)
        logger.info("=" * 60)

        # ① 估值维度
        valuation_scores = {
            "PE_percentile": self.calc_pe_percentile(),
            "PB_percentile": self.calc_pb_percentile(),
            "below_net_rate": self.calc_below_net_rate(),
            "ERP": self.calc_erp(),
        }
        dim_valuation, excl = self._filter_and_average(valuation_scores)
        logger.info("Valuation dimension: %.1f", dim_valuation)

        # ② 资金维度
        fund_scores = {
            "margin_ratio": self.calc_margin_ratio(),
            "northbound": self.calc_northbound(),
        }
        dim_fund, excl = self._filter_and_average(fund_scores)
        logger.info("Fund dimension: %.1f", dim_fund)

        # ③ 情绪维度
        sentiment_scores = {
            "turnover": self.calc_turnover(),
            "limit_up_ratio": self.calc_limit_up_ratio(),
            "new_high_ratio": self.calc_new_high_ratio(),
            "above_ma250_ratio": self.calc_above_ma250_ratio(),
            "up_down_ratio": self.calc_up_down_ratio(),
        }
        dim_sentiment, excl = self._filter_and_average(sentiment_scores)
        logger.info("Sentiment dimension: %.1f", dim_sentiment)

        # ④ 技术维度
        technical_scores = {
            "deviation_ma250": self.calc_deviation_from_ma250(),
            "volatility_change": self.calc_volatility_change(),
            "RSI": self.calc_rsi(),
            "high_low_diff": self.calc_high_low_diff(),
        }
        dim_technical, excl = self._filter_and_average(technical_scores)
        logger.info("Technical dimension: %.1f", dim_technical)

        # ⑤ 结构维度
        structure_scores = {
            "ah_premium": self.calc_ah_premium_percentile(),
            "equal_weighted_diff": self.calc_equal_weighted_diff(),
        }
        dim_structure, excl = self._filter_and_average(structure_scores)
        logger.info("Structure dimension: %.1f", dim_structure)

        # 综合热度
        dim_scores = [dim_valuation, dim_fund, dim_sentiment, dim_technical, dim_structure]
        valid_dims = [s for s in dim_scores if not pd.isna(s)]
        if not valid_dims:
            composite = np.nan
        else:
            composite = np.mean(valid_dims)

        result = {
            "trade_date": self.trade_date,
            "composite_score": round(composite, 1) if not pd.isna(composite) else None,
            "dim_valuation": round(dim_valuation, 1) if not pd.isna(dim_valuation) else None,
            "dim_fund": round(dim_fund, 1) if not pd.isna(dim_fund) else None,
            "dim_sentiment": round(dim_sentiment, 1) if not pd.isna(dim_sentiment) else None,
            "dim_technical": round(dim_technical, 1) if not pd.isna(dim_technical) else None,
            "dim_structure": round(dim_structure, 1) if not pd.isna(dim_structure) else None,
            "indicators": {
                "valuation": {k: round(v, 1) if not pd.isna(v) else None for k, v in valuation_scores.items()},
                "fund": {k: round(v, 1) if not pd.isna(v) else None for k, v in fund_scores.items()},
                "sentiment": {k: round(v, 1) if not pd.isna(v) else None for k, v in sentiment_scores.items()},
                "technical": {k: round(v, 1) if not pd.isna(v) else None for k, v in technical_scores.items()},
                "structure": {k: round(v, 1) if not pd.isna(v) else None for k, v in structure_scores.items()},
            }
        }

        logger.info("COMPOSITE HEAT INDEX: %.1f", composite)
        return result


def calculate_heat_index(trade_date: str = None, db_path: str = None) -> Dict:
    """便捷函数"""
    calc = HeatIndexCalculator(trade_date=trade_date, db_path=db_path)
    return calc.calculate()


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    date = sys.argv[1] if len(sys.argv) > 1 else None
    result = calculate_heat_index(trade_date=date)
    print(json.dumps(result, ensure_ascii=False, indent=2))
