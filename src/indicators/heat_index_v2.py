"""
牛市热度指数 V2 — 精简版计算引擎

9 个核心指标 + QVIX 仅展示不计分

指标:
  估值(35%):  大盘PE, ERP, 巴菲特指标
  资金(25%):  两融余额市值比, 存款市值比
  情绪(25%):  成交额M2比, 换手率
  结构(15%):  创新高占比, MA排列比

展示(不计分): QVIX恐慌指数
"""
import logging
import sqlite3
import pandas as pd
import numpy as np
from datetime import date, timedelta
from typing import Dict, Optional

from src.data.database import DB_PATH, read_dataframe

logger = logging.getLogger(__name__)

# ── 指标权重配置 ─────────────────────────────────────────────────────────────
INDICATOR_WEIGHTS = {
    "pe": 0.14,                # 大盘PE
    "erp": 0.13,               # ERP 股权风险溢价
    "buffett": 0.13,           # 巴菲特指标
    "margin_ratio": 0.15,      # 两融余额市值比
    "deposit_ratio": 0.15,     # 存款市值比
    "turnover_m2": 0.10,       # 成交额M2比
    "turnover": 0.10,          # 换手率
    "new_high": 0.06,          # 创新高占比
    "ma_alignment": 0.04,      # MA排列比
}

# 验证权重总和为1.0
assert abs(sum(INDICATOR_WEIGHTS.values()) - 1.0) < 0.001, \
    f"Indicator weights must sum to 1.0, got {sum(INDICATOR_WEIGHTS.values())}"

DIMENSION_WEIGHTS = {
    "valuation": 0.40,   # 估值(↑)
    "fund": 0.30,        # 资金(↑)
    "sentiment": 0.20,   # 情绪(↓)
    "structure": 0.10,   # 结构(↓)
}

# 背离检测参数
DIVERGENCE_CONFIG = {
    "turnover_threshold": 70,       # 换手率超过此值才触发背离检查
    "decline_threshold": -1.5,      # 指数跌幅超过此值(%)触发惩罚
    "penalty_factor": 0.3,          # 每次背离扣除的分数
    "lookback_days": 20,            # 背离检测的回看天数
    "new_high_penalty": 15,         # 顶背离时扣除的结构分
}

# 各指标所属维度
INDICATOR_DIMENSIONS = {
    "pe": "valuation",
    "erp": "valuation",
    "buffett": "valuation",
    "margin_ratio": "fund",
    "deposit_ratio": "fund",
    "turnover_m2": "sentiment",
    "turnover": "sentiment",
    "new_high": "structure",
    "ma_alignment": "structure",
}


def _pct_rank(series, value) -> float:
    """百分位排名 (0~1)"""
    clean = [x for x in series if x is not None and not (isinstance(x, float) and np.isnan(x))]
    if not clean or value is None:
        return 0.5
    return sum(1 for x in clean if x < value) / len(clean)


def _to_numeric(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _get_conn(db_path: str = None):
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ═══════════════════════════════════════════════════════════════════════════
# 各指标计算函数
# ═══════════════════════════════════════════════════════════════════════════

def calc_pe(conn, trade_date: str) -> Optional[float]:
    """大盘PE — index_daily_pe 中位数历史百分位反向赋分"""
    try:
        td = trade_date
        # 当前值
        cur = conn.execute(
            "SELECT pe_med FROM index_daily_pe WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1",
            (td,)
        ).fetchone()
        if not cur or cur[0] is None:
            return None

        # 历史序列 (10年)
        hist = pd.read_sql(
            "SELECT pe_med FROM index_daily_pe WHERE trade_date >= ? AND pe_med IS NOT NULL",
            conn, params=[str(int(td[:4]) - 10) + td[4:]]
        )
        if hist.empty or len(hist) < 120:
            return None

        pct = _pct_rank(hist["pe_med"], cur[0])
        score = (1 - pct) * 100  # PE越高分越低
        logger.info("大盘PE: %.2f, score=%.1f (n=%d)", cur[0], score, len(hist))
        return max(0, min(100, score))
    except Exception as e:
        logger.warning("PE calc failed: %s", e)
        return None


def calc_erp_v2(conn, trade_date: str) -> Optional[float]:
    """ERP 股权风险溢价 = 1/PE - 10Y国债 (反向: 高ERP=便宜=低分)"""
    try:
        td = trade_date
        # 当前ERP (从 daily_erp 或实时计算)
        row = conn.execute(
            "SELECT erp FROM daily_erp WHERE trade_date=?",
            (td,)
        ).fetchone()
        if row and row[0] is not None:
            cur_erp = row[0]
        else:
            # 实时计算
            pe_row = conn.execute(
                "SELECT pe_med FROM index_daily_pe WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1",
                (td,)
            ).fetchone()
            bond_row = conn.execute(
                "SELECT yield_rate FROM bond_yield WHERE curve_term=10 AND trade_date<=? ORDER BY trade_date DESC LIMIT 1",
                (td,)
            ).fetchone()
            if not pe_row or not bond_row or pe_row[0] is None or bond_row[0] is None:
                return None
            cur_erp = (1.0 / pe_row[0] - bond_row[0] / 100.0) * 100

        # 历史序列 (从 daily_erp 或实时构建)
        hist = pd.read_sql(
            "SELECT erp FROM daily_erp WHERE trade_date >= ? AND erp IS NOT NULL",
            conn, params=[str(int(td[:4]) - 10) + td[4:]]
        )
        if hist.empty or len(hist) < 120:
            # fallback: 从 index_daily_pe + bond_yield 构建
            pe_hist = pd.read_sql(
                "SELECT p.trade_date, p.pe_med, b.yield_rate "
                "FROM index_daily_pe p "
                "LEFT JOIN bond_yield b ON b.curve_term=10 AND b.trade_date = ("
                "  SELECT MAX(b2.trade_date) FROM bond_yield b2 WHERE b2.curve_term=10 AND b2.trade_date <= p.trade_date"
                ") WHERE p.pe_med > 0 AND p.trade_date >= ?",
                conn, params=[str(int(td[:4]) - 10) + td[4:]]
            )
            if pe_hist.empty or len(pe_hist) < 120:
                return None
            pe_hist["erp"] = (1.0 / pe_hist["pe_med"] - pe_hist["yield_rate"] / 100.0) * 100
            hist_vals = pe_hist["erp"].dropna()
        else:
            hist_vals = hist["erp"].dropna()

        if len(hist_vals) < 120:
            return None

        pct = _pct_rank(hist_vals, cur_erp)
        score = (1 - pct) * 100
        logger.info("ERP: %.4f, score=%.1f (n=%d)", cur_erp, score, len(hist_vals))
        return max(0, min(100, score))
    except Exception as e:
        logger.warning("ERP calc failed: %s", e)
        return None


def calc_buffett(conn, trade_date: str) -> Optional[float]:
    """巴菲特指标 = A股总市值 / 年度GDP (反向: 越高=越贵=分越低)

    年度GDP = 最近4个季度GDP之和
    """
    try:
        td = trade_date
        # 总市值 (stock_daily.total_mv 单位为万元, 转为元: ×10000)
        mv_row = conn.execute(
            "SELECT SUM(total_mv) * 10000 FROM stock_daily WHERE trade_date=? AND total_mv > 0",
            (td,)
        ).fetchone()
        if not mv_row or mv_row[0] is None:
            mv_row = conn.execute(
                "SELECT SUM(total_mv) * 10000 FROM stock_daily WHERE trade_date = (SELECT MAX(trade_date) FROM stock_daily WHERE total_mv > 0)",
            ).fetchone()
        if not mv_row or mv_row[0] is None:
            return None
        total_mv = mv_row[0]  # 元

        # 找到当日所属年份，用前一年的年度GDP（巴菲特指标的常规做法）
        td_year = int(td[:4])
        gdp_all = pd.read_sql(
            "SELECT quarter, gdp FROM gdp_quarterly WHERE gdp IS NOT NULL ORDER BY quarter",
            conn
        )
        if gdp_all.empty:
            return None

        # 计算每年的年度GDP
        gdp_all["year"] = gdp_all["quarter"].str[:4].astype(int)
        annual_gdp = gdp_all.groupby("year")["gdp"].sum().to_dict()

        # 当前年度GDP: 最近一个完整年
        available_years = sorted(annual_gdp.keys())
        cur_year = td_year
        while cur_year not in annual_gdp and cur_year > min(available_years):
            cur_year -= 1
        if cur_year not in annual_gdp:
            return None
        cur_annual_gdp = annual_gdp[cur_year] * 1e8  # 亿元→元

        if cur_annual_gdp <= 0:
            return None

        buffett_ratio = total_mv / cur_annual_gdp

        # 历史巴菲特指标
        mv_hist = pd.read_sql(
            "SELECT trade_date, SUM(total_mv) * 10000 as tot_mv FROM stock_daily WHERE total_mv > 0 AND trade_date >= ? GROUP BY trade_date ORDER BY trade_date",
            conn, params=[str(td_year - 10) + td[4:]]
        )
        if mv_hist.empty:
            return None

        hist_ratios = []
        for _, m in mv_hist.iterrows():
            my = int(m["trade_date"][:4])
            # 用前一年GDP
            gdp_year = my - 1
            while gdp_year not in annual_gdp and gdp_year > min(available_years):
                gdp_year -= 1
            if gdp_year in annual_gdp and annual_gdp[gdp_year] > 0:
                hist_ratios.append(m["tot_mv"] / (annual_gdp[gdp_year] * 1e8))

        if len(hist_ratios) < 60:
            return None

        pct = _pct_rank(hist_ratios, buffett_ratio)
        score = (1 - pct) * 100
        logger.info("巴菲特指标: %.4f (%s年GDP=%.0f亿), score=%.1f (n=%d)",
                     buffett_ratio, cur_year, cur_annual_gdp / 1e8, score, len(hist_ratios))
        return max(0, min(100, score))
    except Exception as e:
        logger.warning("Buffett calc failed: %s", e)
        return None


def calc_margin_ratio_v2(conn, trade_date: str) -> Optional[float]:
    """两融余额市值比 = (融资余额+融券余额) / 流通市值"""
    try:
        td = trade_date
        # 两融数据
        margin = conn.execute(
            "SELECT rzye, rqye FROM margin_history WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1",
            (td,)
        ).fetchone()
        if not margin:
            return None
        rzye = float(margin[0]) if margin[0] else 0
        rqye = float(margin[1]) if margin[1] else 0

        # 流通市值 (daily_circ_mv.total_circ_mv 单位为万元, 转为元: ×10000)
        mv_row = conn.execute(
            "SELECT total_circ_mv FROM daily_circ_mv WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1",
            (td,)
        ).fetchone()
        if not mv_row or mv_row[0] is None or mv_row[0] <= 0:
            return None
        total_circ = mv_row[0] * 10000  # 万元→元

        cur_ratio = (rzye + rqye) / total_circ

        # 历史序列 (daily_circ_mv.total_circ_mv 万元→元 ×10000)
        hist = pd.read_sql("""
            SELECT m.trade_date, (m.rzye + m.rqye) / (c.total_circ_mv * 10000) as ratio
            FROM margin_history m
            JOIN daily_circ_mv c ON m.trade_date = c.trade_date AND c.total_circ_mv > 0
            WHERE m.trade_date >= ? AND m.rzye > 0 AND c.total_circ_mv > 0
            ORDER BY m.trade_date
        """, conn, params=[str(int(td[:4]) - 5) + td[4:]])

        if hist.empty or len(hist) < 60:
            return None

        hist_ratios = hist["ratio"].dropna()
        if len(hist_ratios) < 60:
            return None

        pct = _pct_rank(hist_ratios, cur_ratio)
        # 杠杆上升=热度上升, 但>90%分位时转为减分
        if pct > 0.9:
            score = (1 - pct) * 100
        else:
            score = pct * 100
        logger.info("两融余额市值比: %.6f, score=%.1f (n=%d)", cur_ratio, score, len(hist_ratios))
        return max(0, min(100, score))
    except Exception as e:
        logger.warning("Margin ratio calc failed: %s", e)
        return None


def calc_deposit_ratio(conn, trade_date: str) -> Optional[float]:
    """存款市值比 = M2 / A股总市值 (反向: 比值越低=资金流入股市=热度越高)"""
    try:
        td = trade_date
        td_month = td[:7]

        # M2 (m2_billion 单位为亿元, 转为元: ×1e8)
        m2_row = conn.execute(
            "SELECT m2_billion FROM m2_monthly WHERE month<=? ORDER BY month DESC LIMIT 1",
            (td_month,)
        ).fetchone()
        if not m2_row or m2_row[0] is None:
            return None
        m2 = m2_row[0] * 1e8  # 亿元→元

        # 总市值 (stock_daily.total_mv 单位为万元, 转为元: ×10000)
        mv_row = conn.execute(
            "SELECT SUM(total_mv) * 10000 FROM stock_daily WHERE trade_date=? AND total_mv > 0",
            (td,)
        ).fetchone()
        if not mv_row or mv_row[0] is None:
            return None
        total_mv = mv_row[0]  # 元

        if total_mv <= 0:
            return None

        cur_ratio = m2 / total_mv

        # 历史序列 (月度)
        m2_all = pd.read_sql(
            "SELECT month, m2_billion FROM m2_monthly WHERE m2_billion IS NOT NULL ORDER BY month",
            conn
        )
        mv_monthly = pd.read_sql("""
            SELECT substr(trade_date, 1, 7) as month, AVG(total_mv) as avg_mv
            FROM stock_daily WHERE total_mv > 0 AND trade_date >= '2010-01-01'
            GROUP BY month ORDER BY month
        """, conn)

        merged = m2_all.merge(mv_monthly, on="month", how="inner")
        if merged.empty or len(merged) < 60:
            return None

        # m2_billion: 亿元→元(×1e8), avg_mv: 万元→元(×10000)
        merged["ratio"] = (merged["m2_billion"] * 1e8) / (merged["avg_mv"] * 10000)
        hist_ratios = merged["ratio"].dropna()

        pct = _pct_rank(hist_ratios, cur_ratio)
        score = (1 - pct) * 100  # 存款市值比越低=资金搬家到股市=热度越高
        logger.info("存款市值比: %.2f, score=%.1f (n=%d)", cur_ratio, score, len(hist_ratios))
        return max(0, min(100, score))
    except Exception as e:
        logger.warning("Deposit ratio calc failed: %s", e)
        return None


def calc_turnover_m2(conn, trade_date: str) -> Optional[float]:
    """成交额M2比 = 日成交额 / M2"""
    try:
        td = trade_date
        td_month = td[:7]

        # M2 (m2_billion 单位为亿元, 转为元: ×1e8)
        m2_row = conn.execute(
            "SELECT m2_billion FROM m2_monthly WHERE month<=? ORDER BY month DESC LIMIT 1",
            (td_month,)
        ).fetchone()
        if not m2_row or m2_row[0] is None:
            return None
        m2 = m2_row[0] * 1e8  # 亿元→元

        # 当日成交额 (stock_daily.amount 单位为元)
        amt_row = conn.execute(
            "SELECT SUM(amount) FROM stock_daily WHERE trade_date=? AND amount > 0",
            (td,)
        ).fetchone()
        if not amt_row or amt_row[0] is None:
            return None
        amount = amt_row[0]  # 元

        if m2 <= 0:
            return None

        cur_ratio = amount / m2

        # 历史序列 (月度M2 + 日均成交额)
        m2_all = pd.read_sql(
            "SELECT month, m2_billion FROM m2_monthly WHERE m2_billion IS NOT NULL ORDER BY month",
            conn
        )
        amt_monthly = pd.read_sql("""
            SELECT substr(trade_date, 1, 7) as month, AVG(amount) as avg_amt
            FROM stock_daily WHERE amount > 0 AND trade_date >= '2010-01-01'
            GROUP BY month ORDER BY month
        """, conn)

        merged = m2_all.merge(amt_monthly, on="month", how="inner")
        if merged.empty or len(merged) < 60:
            return None

        # avg_amt 单位为元, m2_billion 亿元→元(×1e8)
        merged["ratio"] = merged["avg_amt"] / (merged["m2_billion"] * 1e8)
        hist_ratios = merged["ratio"].dropna()

        pct = _pct_rank(hist_ratios, cur_ratio)
        score = pct * 100
        logger.info("成交额M2比: %.6f, score=%.1f (n=%d)", cur_ratio, score, len(hist_ratios))
        return max(0, min(100, score))
    except Exception as e:
        logger.warning("Turnover/M2 calc failed: %s", e)
        return None


def calc_turnover_v2(conn, trade_date: str) -> Optional[float]:
    """换手率 = 成交额 / 流通市值 (近6个月窗口百分位)"""
    try:
        td = trade_date
        six_mo_ago = (pd.Timestamp(td) - pd.DateOffset(months=6)).strftime("%Y-%m-%d")

        # 历史窗口
        hist = pd.read_sql(
            "SELECT trade_date, SUM(amount) as amt, SUM(circ_mv) as mv "
            "FROM stock_daily WHERE trade_date >= ? AND trade_date < ? AND amount > 0 AND circ_mv > 0 "
            "GROUP BY trade_date ORDER BY trade_date",
            conn, params=(six_mo_ago, td)
        )
        if hist.empty or len(hist) < 20:
            return None
        hist_rates = (hist["amt"] / hist["mv"] * 10).dropna()

        # 当日
        today = pd.read_sql(
            "SELECT SUM(amount) as amt, SUM(circ_mv) as mv "
            "FROM stock_daily WHERE trade_date=? AND amount > 0 AND circ_mv > 0",
            conn, params=(td,)
        )
        if today.empty or today["mv"].iloc[0] is None or today["mv"].iloc[0] <= 0:
            # fallback: 最近日期
            today = pd.read_sql(
                "SELECT SUM(amount) as amt, SUM(circ_mv) as mv "
                "FROM stock_daily WHERE trade_date = (SELECT MAX(trade_date) FROM stock_daily WHERE circ_mv > 0) "
                "AND amount > 0 AND circ_mv > 0",
                conn
            )
        if today.empty or today["mv"].iloc[0] is None or today["mv"].iloc[0] <= 0:
            return None

        cur_rate = today["amt"].iloc[0] / today["mv"].iloc[0] * 10

        pct = _pct_rank(hist_rates, cur_rate)
        score = pct * 100
        logger.info("换手率: %.4f%%, score=%.1f (n=%d)", cur_rate, score, len(hist_rates))
        return max(0, min(100, score))
    except Exception as e:
        logger.warning("Turnover calc failed: %s", e)
        return None


def calc_new_high_v2(conn, trade_date: str) -> Optional[float]:
    """创新高占比 = 250日新高股票占比"""
    try:
        td = trade_date
        # 当日所有股票收盘价
        today = pd.read_sql(
            "SELECT stock_code, close FROM stock_daily WHERE trade_date=? AND close > 0",
            conn, params=(td,)
        )
        if today.empty or len(today) < 100:
            # fallback: 最近日期
            today = pd.read_sql(
                "SELECT stock_code, close FROM stock_daily WHERE trade_date = (SELECT MAX(trade_date) FROM stock_daily WHERE close > 0) AND close > 0",
                conn
            )
        if today.empty or len(today) < 100:
            return None

        # 250日最高价
        hist = pd.read_sql("""
            SELECT stock_code, MAX(close) as max_close
            FROM stock_daily
            WHERE trade_date <= ? AND trade_date >= date(?, '-250 days')
              AND close > 0
            GROUP BY stock_code
        """, conn, params=(td, td))

        if hist.empty:
            return None

        merged = today.merge(hist, on="stock_code", how="inner").dropna()
        if len(merged) < 100:
            return None

        new_high = (merged["close"] >= merged["max_close"] * 0.98).sum()
        ratio = new_high / len(merged)
        score = ratio * 100
        logger.info("创新高占比: %.4f (%d/%d), score=%.1f", ratio, new_high, len(merged), score)
        return max(0, min(100, score))
    except Exception as e:
        logger.warning("New high calc failed: %s", e)
        return None


def calc_ma_alignment_v2(conn, trade_date: str) -> Optional[float]:
    """MA排列比 = MA20>MA60>MA120 多头排列占比"""
    try:
        td = trade_date
        # 直接从预计算表读取
        row = conn.execute(
            "SELECT ma_alignment_ratio FROM daily_ma_alignment WHERE trade_date=?",
            (td,)
        ).fetchone()
        if row and row[0] is not None:
            score = float(row[0])
            logger.info("MA排列比 (precomputed): %.2f", score)
            return max(0, min(100, score))

        # fallback: 用最近日期
        row = conn.execute(
            "SELECT ma_alignment_ratio FROM daily_ma_alignment WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1",
            (td,)
        ).fetchone()
        if row and row[0] is not None:
            score = float(row[0])
            logger.info("MA排列比 (最近): %.2f", score)
            return max(0, min(100, score))
        return None
    except Exception as e:
        logger.warning("MA alignment calc failed: %s", e)
        return None


def calc_qvix_v2(conn, trade_date: str) -> Optional[float]:
    """QVIX恐慌指数 — 仅展示不计分"""
    try:
        td = trade_date
        row = conn.execute(
            "SELECT qvix FROM qvix_daily WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1",
            (td,)
        ).fetchone()
        if row and row[0] is not None:
            return float(row[0])
        return None
    except Exception as e:
        logger.warning("QVIX calc failed: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# 主计算引擎
# ═══════════════════════════════════════════════════════════════════════════

def compute_index_v2(trade_date: str = None, db_path: str = None) -> dict:
    """计算新版热度指数，返回包含所有指标和分数的字典"""
    td = trade_date or date.today().strftime("%Y-%m-%d")
    db = db_path or DB_PATH

    conn = _get_conn(db)
    try:
        # 计算所有指标
        scores = {
            "pe": calc_pe(conn, td),
            "erp": calc_erp_v2(conn, td),
            "buffett": calc_buffett(conn, td),
            "margin_ratio": calc_margin_ratio_v2(conn, td),
            "deposit_ratio": calc_deposit_ratio(conn, td),
            "turnover_m2": calc_turnover_m2(conn, td),
            "turnover": calc_turnover_v2(conn, td),
            "new_high": calc_new_high_v2(conn, td),
            "ma_alignment": calc_ma_alignment_v2(conn, td),
        }

        qvix = calc_qvix_v2(conn, td)

        # ── 背离惩罚 ────────────────────────────────────────────────────
        # 情绪背离: 高换手率 + 指数下跌
        sentiment_keys = {"turnover_m2", "turnover"}
        sentiment_scores = {k: scores[k] for k in sentiment_keys}
        sentiment_scores = _apply_sentiment_divergence(conn, td, sentiment_scores)
        for k, v in sentiment_scores.items():
            scores[k] = v

        # 新高顶背离: 指数涨 + 新高占比下降
        scores["new_high"] = _apply_new_high_divergence(conn, td, scores["new_high"])

        # 各维度分数计算
        dim_scores = {}
        for dim_name in DIMENSION_WEIGHTS:
            ind_keys = [k for k, v in INDICATOR_DIMENSIONS.items() if v == dim_name]
            dim_vals = [scores[k] for k in ind_keys if scores[k] is not None]
            if dim_vals:
                dim_scores[dim_name] = sum(dim_vals) / len(dim_vals)
            else:
                dim_scores[dim_name] = None

        # 综合得分
        valid_scores = [(k, v) for k, v in scores.items() if v is not None]
        if not valid_scores:
            composite = None
        else:
            total_weight = sum(INDICATOR_WEIGHTS[k] for k, _ in valid_scores)
            if total_weight > 0:
                composite = sum(v * INDICATOR_WEIGHTS[k] for k, v in valid_scores) / total_weight
            else:
                composite = None

        # 构建输出
        result = {
            "trade_date": td,
            "composite_score": round(composite, 1) if composite is not None else None,
            "dimensions": {
                "valuation": {"score": round(dim_scores.get("valuation"), 1) if dim_scores.get("valuation") is not None else None, "label": "估值"},
                "fund": {"score": round(dim_scores.get("fund"), 1) if dim_scores.get("fund") is not None else None, "label": "资金"},
                "sentiment": {"score": round(dim_scores.get("sentiment"), 1) if dim_scores.get("sentiment") is not None else None, "label": "情绪"},
                "structure": {"score": round(dim_scores.get("structure"), 1) if dim_scores.get("structure") is not None else None, "label": "结构"},
            },
            "indicators": {
                "pe": scores["pe"],
                "erp": scores["erp"],
                "buffett": scores["buffett"],
                "margin_ratio_v2": scores["margin_ratio"],
                "deposit_ratio": scores["deposit_ratio"],
                "turnover_m2": scores["turnover_m2"],
                "turnover": scores["turnover"],
                "new_high": scores["new_high"],
                "ma_alignment": scores["ma_alignment"],
                "qvix": qvix,
            },
            "updated_at": date.today().strftime("%Y-%m-%d %H:%M:%S"),
        }
        return result
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
# 背离惩罚与评分调整
# ═══════════════════════════════════════════════════════════════════════════

def _apply_sentiment_divergence(conn, trade_date: str,
                                 sentiment_scores: dict) -> dict:
    """情绪背离惩罚: 高活跃度(换手率高) + 指数下跌 = 减分"""
    try:
        td = trade_date
        idx_close = pd.read_sql("""
            SELECT trade_date, close FROM index_daily
            WHERE index_code='sh000001' AND trade_date <= ? AND trade_date >= date(?, ?)
            ORDER BY trade_date DESC LIMIT 2
        """, conn, params=(td, td, f'-{DIVERGENCE_CONFIG["lookback_days"]} days'))

        if len(idx_close) < 2:
            return sentiment_scores

        pct_change = (idx_close.iloc[0]["close"] / idx_close.iloc[-1]["close"] - 1) * 100

        turnover_score = sentiment_scores.get("turnover")
        if (turnover_score is not None
                and turnover_score > DIVERGENCE_CONFIG["turnover_threshold"]
                and pct_change < DIVERGENCE_CONFIG["decline_threshold"]):

            penalty = DIVERGENCE_CONFIG["penalty_factor"]
            logger.info("情绪背离惩罚: 换手率=%.1f, 指数%.1f%%, 减%.1f分",
                        turnover_score, pct_change, penalty)
            for key in ("turnover_m2", "turnover"):
                if sentiment_scores.get(key) is not None:
                    sentiment_scores[key] = max(0, sentiment_scores[key] - penalty * 100)
    except Exception as e:
        logger.warning("Sentiment divergence check failed: %s", e)
    return sentiment_scores


def _apply_new_high_divergence(conn, trade_date: str,
                                new_high_score: float) -> float:
    """创新高顶背离: 指数涨 + 新高占比下降 = 扣分"""
    if new_high_score is None:
        return new_high_score
    try:
        td = trade_date
        lookback = DIVERGENCE_CONFIG["lookback_days"]
        prev_td = (pd.Timestamp(td) - pd.DateOffset(days=lookback)).strftime("%Y-%m-%d")

        # 简化的新高占比变化
        now_ratio = conn.execute(
            "SELECT AVG(ratio) FROM ("
            "  SELECT SUM(CASE WHEN close >= 0.98 * max_close THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as ratio"
            "  FROM ("
            "    SELECT a.stock_code, a.close, MAX(b.close) as max_close"
            "    FROM stock_daily a"
            "    JOIN stock_daily b ON a.stock_code = b.stock_code"
            "       AND b.trade_date <= a.trade_date"
            "       AND b.trade_date >= date(a.trade_date, '-250 days')"
            "    WHERE a.trade_date = ?"
            "    GROUP BY a.stock_code"
            "  )"
            ")",
            (td,)
        ).fetchone()
        prev_ratio = conn.execute(
            "SELECT AVG(ratio) FROM ("
            "  SELECT SUM(CASE WHEN close >= 0.98 * max_close THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as ratio"
            "  FROM ("
            "    SELECT a.stock_code, a.close, MAX(b.close) as max_close"
            "    FROM stock_daily a"
            "    JOIN stock_daily b ON a.stock_code = b.stock_code"
            "       AND b.trade_date <= a.trade_date"
            "       AND b.trade_date >= date(a.trade_date, '-250 days')"
            "    WHERE a.trade_date = ?"
            "    GROUP BY a.stock_code"
            "  )"
            ")",
            (prev_td,)
        ).fetchone()

        if not now_ratio or not prev_ratio or now_ratio[0] is None or prev_ratio[0] is None:
            return new_high_score

        now_val = float(now_ratio[0]) * 100
        prev_val = float(prev_ratio[0]) * 100

        # 指数涨跌
        idx = conn.execute(
            "SELECT close FROM index_daily WHERE index_code='sh000001' AND trade_date <= ? ORDER BY trade_date DESC LIMIT 1",
            (td,)
        ).fetchone()
        idx_prev = conn.execute(
            "SELECT close FROM index_daily WHERE index_code='sh000001' AND trade_date <= ? ORDER BY trade_date DESC LIMIT 1",
            (prev_td,)
        ).fetchone()
        if not idx or not idx_prev:
            return new_high_score

        idx_change = (idx[0] / idx_prev[0] - 1) * 100

        # 顶背离: 指数涨>3%, 新高占比下降>5%, 且当前<30%
        if idx_change > 3 and prev_val - now_val > 5 and now_val < 30:
            penalty = DIVERGENCE_CONFIG["new_high_penalty"]
            logger.info("新高顶背离: 指数+%.1f%%, 新高%.1f→%.1f%%, 扣%.0f分",
                        idx_change, prev_val, now_val, penalty)
            return max(0, new_high_score - penalty)
    except Exception as e:
        logger.warning("New high divergence check failed: %s", e)
    return new_high_score
