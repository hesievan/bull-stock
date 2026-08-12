"""
牛市热度指数 V2 — 精简版计算引擎

11 个核心指标 + QVIX 仅展示不计分

P0-1 去同源加权: 剔除与 PE/Buffett 共线的 ERP、存款市值比,
腾出权重接入「涨停封板率」(市场追涨情绪的独立信号)。

指标:
   估值(28%):  大盘PE(14%), 巴菲特指标(14%)
   资金(15%):  两融余额市值比(5%), 北向净流入比(4%), 国债期限利差(3%), M1-M2剪刀差(3%)
   情绪(42%):  涨停封板率(15%), 成交额M2比(15%), 换手率(12%)
   结构(15%):  创新高占比(10%), MA排列比(5%)

展示(不计分): QVIX恐慌指数

资金维度扩容 (2026-08): 原单一 margin_ratio(15%) 拆分为 4 个低相关标准化指标,
总权重仍为 15%。所有新指标均为比率/差分形式, 避免绝对额体量漂移。
  north_ratio   = 北向净流入 / 当日成交额   (百万元×1e6 / 千元×1e3 = north_net×1000/amount)
  yield_spread  = 10Y国债收益率 - 2Y国债收益率 (2s10s 期限利差; 1Y 历史不可用故用 2Y, 覆盖 2010~今)
  m1_m2_spread  = M1同比 - M2同比            (货币活化程度)
"""
import logging
import math
import sqlite3
import pandas as pd
import numpy as np
from datetime import date
from typing import Optional

from src.data.database import DB_PATH
from src.config import load_config
from src.indicators.utils import _pct_rank as _utils_pct_rank

logger = logging.getLogger(__name__)

# ── 指标权重配置 (内置默认值, 可被 config/*.yaml 的 v2_engine 覆盖) ─────────────
DEFAULT_WEIGHTS = {
    "pe": 0.14,                # 大盘PE
    "buffett": 0.14,           # 巴菲特指标
    "margin_ratio": 0.05,      # 两融余额市值比 (资金扩容: 15%→5%)
    "north_ratio": 0.04,       # 北向净流入比 (新增)
    "yield_spread": 0.03,      # 国债期限利差 10Y-2Y (新增)
    "m1_m2_spread": 0.03,      # M1-M2剪刀差 (新增)
    "seal_rate": 0.08,          # 涨停封板率 (降权: 15%→8%, 区分度3.1最低且日内噪声大)         # 涨停封板率 (回测调权: 28%→15%, 区分度3.1偏低)
    "turnover_m2": 0.15,       # 成交额M2比 (回测调权: 10%→15%, 区分度21.0最高)
    "turnover": 0.12,          # 换手率 (回测调权: 10%→12%, 区分度21.0)
    "new_high": 0.14,           # 创新高占比 (升权: 10%→14%, 区分度19.4)          # 创新高占比 (回测调权: 6%→10%, 区分度19.4)
    "ma_alignment": 0.08,       # MA排列比 (升权: 5%→8%, 区分度15.1)      # MA排列比 (回测调权: 4%→5%, 区分度15.1)
}

# 背离检测参数 (内置默认值, 可被 v2_engine.divergence 覆盖)
DEFAULT_DIVERGENCE = {
    "turnover_threshold": 70,       # 换手率超过此值才触发背离检查
    "decline_threshold": -1.5,      # 指数跌幅超过此值(%)触发惩罚
    "penalty_factor": 0.2,          # 每次背离扣除的分数（×100=20分，匹配README文档"最多20分"）
    "lookback_days": 20,            # 背离检测的回看天数
    "new_high_penalty": 15,         # 顶背离时扣除的结构分
}


def _load_v2_config() -> dict:
    """加载 config/*.yaml 的 v2_engine 配置块; 缺失/异常时返回空 dict 走默认值"""
    try:
        return load_config().get("v2_engine", {}) or {}
    except Exception:
        logger.warning("v2_engine config missing, using built-in defaults")
        return {}


_cfg = _load_v2_config()

INDICATOR_WEIGHTS = _cfg.get("weights") or DEFAULT_WEIGHTS

# 验证权重总和为1.0
assert abs(sum(INDICATOR_WEIGHTS.values()) - 1.0) < 0.001, \
    f"Indicator weights must sum to 1.0, got {sum(INDICATOR_WEIGHTS.values())}"

DIMENSIONS = ["valuation", "fund", "sentiment", "structure"]

# 新高占比判定: 收盘价达到250日最高价的此比例即视为"新高"（2%容差，过滤盘中冲高回落噪声）
NEW_HIGH_THRESHOLD = (_cfg.get("new_high") or {}).get("threshold", 0.98)

DIVERGENCE_CONFIG = {**DEFAULT_DIVERGENCE, **(_cfg.get("divergence") or {})}

# F3: 换手率历史百分位窗口 (年)
TURNOVER_WINDOW_YEARS = (_cfg.get("turnover") or {}).get("percentile_window_years", 10)

# F5: PE 历史序列 n_stocks 口径过滤 (比例范围 + 绝对下限)
_pe_cfg = _cfg.get("pe") or {}
PE_N_STOCKS_RATIO = tuple(_pe_cfg.get("n_stocks_filter_ratio", [0.5, 1.5]))
PE_N_STOCKS_MIN = int(_pe_cfg.get("n_stocks_filter_min", 450))

# F4: 两融高分位平滑饱和参数
_margin_cfg = _cfg.get("margin") or {}
SATURATION_CUTOFF = float(_margin_cfg.get("saturation_cutoff", 0.85))
SATURATION_HEADROOM = float(_margin_cfg.get("saturation_headroom", 0.15))

# 各指标所属维度
INDICATOR_DIMENSIONS = {
    "pe": "valuation",
    "buffett": "valuation",
    "margin_ratio": "fund",
    "north_ratio": "fund",
    "yield_spread": "fund",
    "m1_m2_spread": "fund",
    "seal_rate": "sentiment",
    "turnover_m2": "sentiment",
    "turnover": "sentiment",
    "new_high": "structure",
    "ma_alignment": "structure",
}


def _pct_rank(series, value) -> float:
    """百分位排名 (0~1) — 含自身的 <= 比较 (P1-3: 与 utils._pct_rank 口径统一)

    委托 utils._pct_rank 计算, 仅在空序列/NaN 时回退到 0.5 (防御性,
    正常流程各 calc_* 会在调用前保证序列非空)。
    """
    s = series if isinstance(series, pd.Series) else pd.Series(series)
    r = _utils_pct_rank(s, value)
    if r is None or (isinstance(r, float) and np.isnan(r)):
        return 0.5
    return float(r)


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

def calc_pe(conn, trade_date: str) -> Optional[tuple]:
    """大盘PE — index_daily_pe 中位数历史百分位 (高PE=贵=高热度)

    返回: (score: float, cur_pe: float) 或 None（失败时）
    注: 调度器 _unpack 会按 tuple 解包，故返回类型为 tuple 而非 float。
    """
    try:
        td = trade_date
        # 当前值
        cur = conn.execute(
            "SELECT pe_med, n_stocks FROM index_daily_pe WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1",
            (td,)
        ).fetchone()
        if not cur or cur[0] is None:
            return None

        cur_pe = cur[0]
        cur_n = cur[1] or 0

        # 历史序列 (10年), 过滤口径不一致的数据 (n_stocks相差超过50%)
        hist = pd.read_sql(
            "SELECT pe_med, n_stocks FROM index_daily_pe WHERE trade_date >= ? AND pe_med IS NOT NULL",
            conn, params=[str(int(td[:4]) - 10) + td[4:]]
        )
        if hist.empty or len(hist) < 120:
            return None

        # 只保留与当前n_stocks相近的历史记录 (排除全市场混入)
        # F5修复: 过滤范围从 ±80%/×3 收紧到 ±50%; 现代成分口径(cur_n>=600, hs300+zz500)
        # 才启用绝对下限450, 避免早期hs300-only(n≈300)数据混入; 早期日期(2015)不触发下限,
        # 防止过滤范围坍缩(cur_n=300时 hi=450 与下限450重合导致hist为空)
        if cur_n > 0:
            lo = cur_n * PE_N_STOCKS_RATIO[0]
            hi = cur_n * PE_N_STOCKS_RATIO[1]
            if cur_n >= 600:
                lo = max(lo, PE_N_STOCKS_MIN)
            hist = hist[hist["n_stocks"].between(lo, hi)]

        if len(hist) < 60:
            return None

        pct = _pct_rank(hist["pe_med"], cur_pe)
        score = pct * 100  # PE越高=越贵=热度越高
        logger.info("大盘PE: %.2f, score=%.1f (n=%d, hist=%d)", cur_pe, score, cur_n, len(hist))
        return max(0, min(100, score)), cur_pe
    except Exception as e:
        logger.warning("PE calc failed: %s", e)
        return None


def calc_seal_rate_v2(conn, trade_date: str) -> Optional[float]:
    """涨停封板率 = 封板数 / 触板数 (高封板率=追涨情绪强=高热度)

    数据来自 daily_seal_rate 预计算表 (tushare limit_list 聚合):
      触板涨停数 = 当日触及涨停的个股数 (limit=='U')
      封板成功数 = 其中 open_times==0 (全天未开板) 的个股数
      seal_rate = 封板成功数 / 触板涨停数

    方向: 封板率越高 → 追涨情绪越强 → 热度越高 (与 PE 同向)。
    P0-1: 替代与 PE/Buffett 共线的 ERP, 提供独立的市场情绪信号。
    """
    try:
        td = trade_date
        # 当前值
        row = conn.execute(
            "SELECT seal_rate FROM daily_seal_rate WHERE trade_date=?",
            (td,)
        ).fetchone()
        if not row or row[0] is None:
            return None
        cur = float(row[0])
        if not (0 <= cur <= 1):
            return None

        # 历史序列 (10年窗口)
        hist = pd.read_sql(
            "SELECT seal_rate FROM daily_seal_rate "
            "WHERE trade_date >= ? AND seal_rate IS NOT NULL "
            "ORDER BY trade_date",
            conn, params=[str(int(td[:4]) - 10) + td[4:]]
        )
        if hist.empty or len(hist) < 60:
            return None
        hist_vals = hist["seal_rate"].dropna()
        if len(hist_vals) < 60:
            return None

        pct = _pct_rank(hist_vals, cur)
        score = pct * 100  # 封板率越高=热度越高
        logger.info("涨停封板率: %.4f, pct=%.2f, score=%.1f (n=%d)", cur, pct, score, len(hist_vals))
        return max(0, min(100, score)), cur
    except Exception as e:
        logger.warning("Seal rate calc failed: %s", e)
        return None


def calc_buffett(conn, trade_date: str) -> Optional[float]:
    """巴菲特指标 = A股总市值 / 年度GDP (高=贵=高热度)

    年度GDP = 最近4个季度GDP之和
    使用预计算表 stock_market_cap 替代逐日 GROUP BY 以提升性能
    """
    try:
        td = trade_date

        # 总市值 — 优先用预计算表, 回退到实时计算
        mv_row = conn.execute(
            "SELECT total_mv FROM stock_market_cap WHERE trade_date=?",
            (td,)
        ).fetchone()
        if not mv_row or mv_row[0] is None:
            mv_row = conn.execute(
                "SELECT SUM(total_mv) FROM stock_daily WHERE trade_date=? AND total_mv > 0",
                (td,)
            ).fetchone()
        if not mv_row or mv_row[0] is None:
            logger.warning("Buffett index: no market cap data for %s, return None", td)
            return None
        total_mv = mv_row[0] * 10000  # 万元→元

        # 找到当日所属年份，用前一年的年度GDP（巴菲特指标的常规做法）
        td_year = int(td[:4])
        gdp_all = pd.read_sql(
            "SELECT quarter, gdp FROM gdp_quarterly WHERE gdp IS NOT NULL ORDER BY quarter",
            conn
        )
        if gdp_all.empty:
            logger.warning("Buffett index: gdp_quarterly table empty, return None")
            return None

        # 计算每年的年度GDP
        gdp_all["year"] = gdp_all["quarter"].str[:4].astype(int)
        annual_gdp = gdp_all.groupby("year")["gdp"].sum().to_dict()

        # 当前年度GDP: 最近一个完整年（始终用前一年度，避免使用当年不完整数据）
        available_years = sorted(annual_gdp.keys())
        cur_year = td_year - 1
        while cur_year not in annual_gdp and cur_year > min(available_years):
            cur_year -= 1
        if cur_year not in annual_gdp:
            logger.warning(
                "Buffett index: no GDP data for year %d or earlier. "
                "Available years: %s. Return None.",
                td_year - 1, available_years)
            return None
        if (td_year - 1 - cur_year) > 0:
            logger.info(
                "Buffett index: using GDP from year %d (latest available, "
                "%d year(s) behind)",
                cur_year, td_year - 1 - cur_year)
        cur_annual_gdp = annual_gdp[cur_year] * 1e8  # 亿元→元

        if cur_annual_gdp <= 0:
            logger.warning(
                "Buffett index: GDP for year %d is non-positive (%.2f), "
                "return None",
                cur_year, cur_annual_gdp)
            return None

        buffett_ratio = total_mv / cur_annual_gdp

        # 历史巴菲特指标 (使用 stock_market_cap 预计算表)
        mv_hist = pd.read_sql(
            "SELECT trade_date, total_mv FROM stock_market_cap "
            "WHERE trade_date >= ? AND total_mv > 0 ORDER BY trade_date",
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
                hist_ratios.append(m["total_mv"] * 10000 / (annual_gdp[gdp_year] * 1e8))

        if len(hist_ratios) < 60:
            return None

        pct = _pct_rank(hist_ratios, buffett_ratio)
        score = pct * 100  # 巴菲特指标越高=越贵=热度越高
        logger.info("巴菲特指标: %.4f (%s年GDP=%.0f亿), score=%.1f (n=%d)",
                     buffett_ratio, cur_year, cur_annual_gdp / 1e8, score, len(hist_ratios))
        return max(0, min(100, score)), buffett_ratio
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

        # 历史序列 (10年窗口; daily_circ_mv.total_circ_mv 万元→元 ×10000)
        # GROUP BY 防止 daily_circ_mv 表历史重复行导致 JOIN 膨胀
        hist = pd.read_sql("""
            SELECT m.trade_date, AVG((m.rzye + m.rqye)) / (c.total_circ_mv * 10000) as ratio
            FROM margin_history m
            JOIN (SELECT trade_date, MAX(total_circ_mv) as total_circ_mv FROM daily_circ_mv
                  WHERE total_circ_mv > 0 GROUP BY trade_date) c
              ON m.trade_date = c.trade_date
            WHERE m.trade_date >= ? AND m.rzye > 0
            GROUP BY m.trade_date
            ORDER BY m.trade_date
        """, conn, params=[str(int(td[:4]) - 10) + td[4:]])

        if hist.empty or len(hist) < 60:
            return None

        hist_ratios = hist["ratio"].dropna()
        if len(hist_ratios) < 60:
            return None

        pct = _pct_rank(hist_ratios, cur_ratio)
        # 杠杆上升=热度上升。F4修复: 高分位用平滑饱和函数保持单调递增,
        # 原线性递减(900*(1-pct))导致 pct=0.95 时分数骤降至45, 反直觉且与"顶部预警"设计矛盾
        # 0.85→85, 0.90→~94, 0.95→~98, 0.99→~99: 单调递增且平滑收敛
        if pct <= SATURATION_CUTOFF:
            score = pct * 100
        else:
            adjusted = SATURATION_CUTOFF + SATURATION_HEADROOM * (1 - math.exp(-(pct - SATURATION_CUTOFF) * 20))
            score = adjusted * 100
        logger.info("两融余额市值比: %.6f, pct=%.2f, score=%.1f (n=%d)", cur_ratio, pct, score, len(hist_ratios))
        return max(0, min(100, score)), cur_ratio
    except Exception as e:
        logger.warning("Margin ratio calc failed: %s", e)
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

        # 当日成交额 (stock_daily.amount 单位为千元, 转为元: ×1000)
        amt_row = conn.execute(
            "SELECT SUM(amount) FROM stock_daily WHERE trade_date=? AND amount > 0",
            (td,)
        ).fetchone()
        if not amt_row or amt_row[0] is None:
            return None
        amount = amt_row[0] * 1000  # 千元→元

        if m2 <= 0:
            return None

        cur_ratio = amount / m2

        # 历史序列 (月度M2 + 日均成交额)
        m2_all = pd.read_sql(
            "SELECT month, m2_billion FROM m2_monthly WHERE m2_billion IS NOT NULL ORDER BY month",
            conn
        )
        amt_monthly = pd.read_sql("""
            SELECT substr(trade_date, 1, 7) as month, AVG(daily_amt)*1000 as avg_daily_amt FROM (
                SELECT trade_date, SUM(amount) as daily_amt
                FROM stock_daily WHERE amount > 0 AND trade_date >= '2010-01-01'
                GROUP BY trade_date
            ) GROUP BY month ORDER BY month
        """, conn)

        merged = m2_all.merge(amt_monthly, on="month", how="inner")
        if merged.empty or len(merged) < 60:
            return None

        # avg_daily_amt 已转为元(千元→元×1000), m2_billion 亿元→元(×1e8)
        merged["ratio"] = merged["avg_daily_amt"] / (merged["m2_billion"] * 1e8)
        hist_ratios = merged["ratio"].dropna()

        pct = _pct_rank(hist_ratios, cur_ratio)
        score = pct * 100
        logger.info("成交额M2比: %.6f, score=%.1f (n=%d)", cur_ratio, score, len(hist_ratios))
        return max(0, min(100, score)), cur_ratio
    except Exception as e:
        logger.warning("Turnover/M2 calc failed: %s", e)
        return None


def calc_turnover_v2(conn, trade_date: str) -> Optional[float]:
    """换手率 = 成交额 / 流通市值 (10年窗口百分位, F3修复)"""
    try:
        td = trade_date
        ten_years_ago = (pd.Timestamp(td) - pd.DateOffset(years=TURNOVER_WINDOW_YEARS)).strftime("%Y-%m-%d")

        # 历史窗口: 查预计算表 daily_turnover (口径与当日值一致: Σamount/Σcirc_mv×10)
        hist = pd.read_sql(
            "SELECT trade_date, turnover_rate FROM daily_turnover "
            "WHERE trade_date >= ? AND trade_date <= ? AND turnover_rate IS NOT NULL "
            "ORDER BY trade_date",
            conn, params=(ten_years_ago, td)
        )
        if hist.empty or len(hist) < 60:
            return None
        hist_rates = hist["turnover_rate"].dropna()

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
        return max(0, min(100, score)), cur_rate
    except Exception as e:
        logger.warning("Turnover calc failed: %s", e)
        return None


def calc_new_high_v2(conn, trade_date: str) -> Optional[float]:
    """创新高占比 = 250日新高股票占比 (10年历史百分位赋分)"""
    try:
        td = trade_date
        # 当前值: 优先预计算表 daily_new_high, 无则保留 stock_daily 直接查询兜底
        row = conn.execute(
            "SELECT new_high_ratio FROM daily_new_high WHERE trade_date=?",
            (td,)
        ).fetchone()
        if not row or row[0] is None:
            today = pd.read_sql(
                "SELECT stock_code, close FROM stock_daily WHERE trade_date=? AND close > 0",
                conn, params=(td,)
            )
            if today.empty or len(today) < 100:
                today = pd.read_sql(
                    "SELECT stock_code, close FROM stock_daily WHERE trade_date = (SELECT MAX(trade_date) FROM stock_daily WHERE close > 0) AND close > 0",
                    conn
                )
            if today.empty or len(today) < 100:
                return None

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
            cur_ratio = (merged["close"] >= merged["max_close"] * NEW_HIGH_THRESHOLD).sum() / len(merged)
        else:
            cur_ratio = row[0]

        # 历史序列 (10年, 预计算表)
        hist = pd.read_sql(
            "SELECT new_high_ratio FROM daily_new_high WHERE trade_date >= ? AND new_high_ratio IS NOT NULL",
            conn, params=[str(int(td[:4]) - 10) + td[4:]]
        )
        if hist.empty or len(hist) < 60:
            # 历史不足时宁缺毋滥 (与 PE/ERP 行为一致), 不返回绝对分
            logger.warning("New high: insufficient historical data (%d records)", len(hist))
            return None

        pct = _pct_rank(hist["new_high_ratio"].dropna(), cur_ratio)
        score = pct * 100
        logger.info("创新高占比: %.4f, pct=%.2f, score=%.1f (n=%d)", cur_ratio, pct, score, len(hist))
        return max(0, min(100, score)), cur_ratio
    except Exception as e:
        logger.warning("New high calc failed: %s", e)
        return None


def calc_ma_alignment_v2(conn, trade_date: str) -> Optional[float]:
    """MA排列比 = MA20>MA60>MA120 多头排列占比 (历史百分位赋分)"""
    try:
        td = trade_date
        # 当前值
        row = conn.execute(
            "SELECT ma_alignment_ratio FROM daily_ma_alignment WHERE trade_date=?",
            (td,)
        ).fetchone()
        if not row or row[0] is None:
            row = conn.execute(
                "SELECT ma_alignment_ratio FROM daily_ma_alignment WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1",
                (td,)
            ).fetchone()
        if not row or row[0] is None:
            return None
        cur_val = float(row[0])

        # 历史序列 (10年)
        hist = pd.read_sql(
            "SELECT ma_alignment_ratio FROM daily_ma_alignment WHERE trade_date >= ? AND ma_alignment_ratio IS NOT NULL",
            conn, params=[str(int(td[:4]) - 10) + td[4:]]
        )
        if hist.empty or len(hist) < 60:
            # F6修复: 历史不足时收敛到[20,80], 原实现 cur_val*100 会给出异常高分
            logger.warning("MA alignment: insufficient historical data (%d records), using clamped fallback",
                           len(hist))
            score = max(20, min(cur_val * 100, 80))
            return score, cur_val

        pct = _pct_rank(hist["ma_alignment_ratio"], cur_val)
        score = pct * 100
        logger.info("MA排列比: %.4f, pct=%.2f, score=%.1f (n=%d)", cur_val, pct, score, len(hist))
        return max(0, min(100, score)), cur_val
    except Exception as e:
        logger.warning("MA alignment calc failed: %s", e)
        return None


def calc_north_ratio_v2(conn, trade_date: str) -> Optional[float]:
    """北向净流入比 = 北向净流入 / 当日成交额 (北向参与度, 比率形式)

    单位: north_net(百万元) × 1e6 / amount(千元) × 1e3 = north_net × 1000 / amount。
    除以同步放大的成交额抵消北向绝对额的体量漂移; 负值=外资净卖出。
    方向: 北向参与度越高=外资越积极=热度越高 (pos)。
    """
    try:
        td = trade_date
        nb = conn.execute(
            "SELECT north_net FROM northbound_history WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1",
            (td,)
        ).fetchone()
        if not nb or nb[0] is None:
            return None
        north_net = float(nb[0])  # 百万元
        amt = conn.execute(
            "SELECT SUM(amount) FROM stock_daily WHERE trade_date=? AND amount > 0",
            (td,)
        ).fetchone()
        if not amt or amt[0] is None or amt[0] <= 0:
            return None
        amount = float(amt[0])  # 千元
        cur_ratio = north_net * 1000.0 / amount

        win = str(int(td[:4]) - 10) + td[4:]
        hist = pd.read_sql("""
            SELECT n.trade_date, n.north_net * 1000.0 / a.amount AS ratio
            FROM northbound_history n
            JOIN (SELECT trade_date, SUM(amount) AS amount FROM stock_daily
                  WHERE amount>0 AND trade_date >= ? GROUP BY trade_date) a
              ON n.trade_date = a.trade_date
            WHERE n.trade_date >= ? AND n.north_net IS NOT NULL AND a.amount > 0
            ORDER BY n.trade_date
        """, conn, params=[win, win])
        if hist.empty or len(hist) < 60:
            return None
        hist_ratios = hist["ratio"].dropna()
        if len(hist_ratios) < 60:
            return None
        pct = _pct_rank(hist_ratios, cur_ratio)
        score = pct * 100
        logger.info("北向净流入比: %.6f, score=%.1f (n=%d)", cur_ratio, score, len(hist_ratios))
        return max(0, min(100, score)), cur_ratio
    except Exception as e:
        logger.warning("North ratio calc failed: %s", e)
        return None


def calc_yield_spread_v2(conn, trade_date: str) -> Optional[float]:
    """国债期限利差 = 10Y收益率 - 2Y收益率 (2s10s 曲线斜率)

    数据源 bond_zh_us_rate, 覆盖 2010~今。1Y 国债历史极短(仅 2020-2021), 故用 2Y 替代 1Y。
    方向修正(回测发现): A股实证中牛市期 10Y-2Y 利差偏低(短端对宽松更敏感、曲线走平),
    故利差越小=宽松/多头情绪=热度越高。因此用 -spread 做百分位, 使低利差→高分 (pos)。
    """
    try:
        td = trade_date
        row = conn.execute(
            "SELECT curve_term, yield_rate FROM bond_yield "
            "WHERE trade_date=? AND curve_term IN (2.0, 10.0)",
            (td,)
        ).fetchall()
        y2 = y10 = None
        for ct, yr in row:
            if ct == 2.0:
                y2 = yr
            elif ct == 10.0:
                y10 = yr
        if y2 is None or y10 is None:
            return None
        cur = float(y10) - float(y2)

        hist = pd.read_sql("""
            SELECT trade_date,
                   MAX(CASE WHEN curve_term=10.0 THEN yield_rate END) AS y10,
                   MAX(CASE WHEN curve_term=2.0 THEN yield_rate END) AS y2
            FROM bond_yield
            WHERE trade_date >= ? AND curve_term IN (2.0, 10.0)
            GROUP BY trade_date
        """, conn, params=[str(int(td[:4]) - 10) + td[4:]])
        if hist.empty or len(hist) < 60:
            return None
        hist["spread"] = hist["y10"] - hist["y2"]
        hist_ratios = hist["spread"].dropna()
        if len(hist_ratios) < 60:
            return None
        pct = _pct_rank(-hist_ratios, -cur)
        score = pct * 100
        logger.info("国债期限利差(10Y-2Y, 已翻转方向): %.4f, score=%.1f (n=%d)", cur, score, len(hist_ratios))
        return max(0, min(100, score)), cur
    except Exception as e:
        logger.warning("Yield spread calc failed: %s", e)
        return None


def calc_m1_m2_spread_v2(conn, trade_date: str) -> Optional[float]:
    """M1-M2剪刀差 = M1同比 - M2同比 (货币活化程度)

    数据源: m1_monthly(M1同比, akshare) + m2_monthly(M2同比, tushare), 按月关联。
    方向: 剪刀差扩大(企业活期资金占比上升)=资金活性增强=热度越高 (pos)。
    月频数据映射到每个交易日, 缺失月份沿用最近月 (ffill)。
    """
    try:
        td = trade_date
        td_month = td[:7]
        m1 = conn.execute(
            "SELECT m1_yoy FROM m1_monthly WHERE month<=? ORDER BY month DESC LIMIT 1",
            (td_month,)
        ).fetchone()
        m2 = conn.execute(
            "SELECT m2_yoy FROM m2_monthly WHERE month<=? ORDER BY month DESC LIMIT 1",
            (td_month,)
        ).fetchone()
        if not m1 or m1[0] is None or not m2 or m2[0] is None:
            return None
        cur = float(m1[0]) - float(m2[0])

        mser = pd.read_sql("""
            SELECT a.month, a.m1_yoy - b.m2_yoy AS spread
            FROM m1_monthly a JOIN m2_monthly b ON a.month = b.month
            WHERE a.m1_yoy IS NOT NULL AND b.m2_yoy IS NOT NULL
            ORDER BY a.month
        """, conn)
        if mser.empty:
            return None
        dates = pd.read_sql(
            "SELECT DISTINCT trade_date FROM stock_daily WHERE trade_date >= ? ORDER BY trade_date",
            conn, params=[str(int(td[:4]) - 10) + td[4:]]
        )
        if dates.empty:
            return None
        dates["month"] = dates["trade_date"].str[:7]
        merged = dates.merge(mser, on="month", how="left").sort_values("trade_date")
        merged["spread"] = merged["spread"].ffill()
        cur_row = merged[merged["trade_date"] <= td]
        if cur_row.empty:
            return None
        cur = float(cur_row.iloc[-1]["spread"])
        hist_ratios = merged["spread"].dropna()
        if len(hist_ratios) < 60:
            return None
        pct = _pct_rank(hist_ratios, cur)
        score = pct * 100
        logger.info("M1-M2剪刀差: %.4f, score=%.1f (n=%d)", cur, score, len(hist_ratios))
        return max(0, min(100, score)), cur
    except Exception as e:
        logger.warning("M1-M2 spread calc failed: %s", e)
        return None


def calc_qvix_v2(conn, trade_date: str) -> Optional[float]:
    """QVIX恐慌指数 — 仅展示不计分"""
    try:
        td = trade_date
        row = conn.execute(
            "SELECT COALESCE(panic_index, qvix) FROM qvix_daily WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1",
            (td,)
        ).fetchone()
        if row and row[0] is not None:
            return float(row[0])
        return None
    except Exception as e:
        logger.warning("QVIX calc failed: %s", e)
        return None


def calc_qvix_components_v2(conn, trade_date: str) -> Optional[dict]:
    """获取 QVIX 各成分值 (qvix_50, qvix_300, qvix_1000, concentration) — 仅展示不计分"""
    try:
        row = conn.execute(
            "SELECT qvix_50, qvix_300, qvix_1000, concentration FROM qvix_daily"
            " WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1",
            (trade_date,)
        ).fetchone()
        if row and any(v is not None for v in row):
            return {
                "qvix_50": float(row[0]) if row[0] is not None else None,
                "qvix_300": float(row[1]) if row[1] is not None else None,
                "qvix_1000": float(row[2]) if row[2] is not None else None,
                "concentration": float(row[3]) if row[3] is not None else None,
            }
        return None
    except Exception as e:
        logger.warning("QVIX components calc failed: %s", e)
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
        # 诊断: 关键预计算表记录数
        for tbl_name in ("index_daily_pe", "stock_market_cap", "daily_circ_mv",
                         "daily_seal_rate", "m2_monthly", "margin_history", "bond_yield",
                         "stock_daily", "daily_turnover", "qvix_daily"):
            try:
                cnt = conn.execute(f"SELECT COUNT(*) FROM {tbl_name}").fetchone()[0]
                date_col = "month" if tbl_name == "m2_monthly" else "trade_date"
                dates = conn.execute(f"SELECT COUNT(DISTINCT {date_col}) FROM {tbl_name}").fetchone()[0]
                logger.info("DIAG: %s — %d rows, %d distinct dates", tbl_name, cnt, dates)
            except Exception as e:
                logger.warning("DIAG: %s — ERROR: %s", tbl_name, e)
        # 诊断: n_stocks 分布
        try:
            n_dist = conn.execute(
                "SELECT MIN(n_stocks), MAX(n_stocks), AVG(n_stocks), COUNT(*) "
                "FROM index_daily_pe WHERE pe_med IS NOT NULL"
            ).fetchone()
            if n_dist:
                logger.info("DIAG: index_daily_pe n_stocks — min=%s max=%s avg=%.0f count=%s",
                            n_dist[0], n_dist[1], n_dist[2] if n_dist[2] else 0, n_dist[3])
            cur_n = conn.execute(
                "SELECT n_stocks FROM index_daily_pe ORDER BY trade_date DESC LIMIT 1"
            ).fetchone()
            if cur_n:
                logger.info("DIAG: n_stocks (latest)=%s", cur_n[0])
        except Exception as e:
            logger.warning("DIAG: n_stocks query failed: %s", e)

        # 计算所有指标 (每个函数返回 (分数, 原始值))
        _raw = {}
        def _unpack(k, v):
            if v is None:
                _raw[k] = None
                return None
            if isinstance(v, tuple):
                _raw[k] = v[1]
                return v[0]
            _raw[k] = None
            return v

        scores = {}
        for k, fn in [
            ("pe", calc_pe),
            ("buffett", calc_buffett),
            ("margin_ratio", calc_margin_ratio_v2),
            ("north_ratio", calc_north_ratio_v2),
            ("yield_spread", calc_yield_spread_v2),
            ("m1_m2_spread", calc_m1_m2_spread_v2),
            ("seal_rate", calc_seal_rate_v2),
            ("turnover_m2", calc_turnover_m2),
            ("turnover", calc_turnover_v2),
            ("new_high", calc_new_high_v2),
            ("ma_alignment", calc_ma_alignment_v2),
        ]:
            scores[k] = _unpack(k, fn(conn, td))

        qvix = calc_qvix_v2(conn, td)
        qvix_components = calc_qvix_components_v2(conn, td)

        # ── 背离惩罚 ────────────────────────────────────────────────────
        # 情绪背离: 高换手率 + 指数下跌
        sentiment_keys = {"turnover_m2", "turnover"}
        sentiment_scores = {k: scores[k] for k in sentiment_keys}
        sentiment_scores = _apply_sentiment_divergence(conn, td, sentiment_scores)
        for k, v in sentiment_scores.items():
            scores[k] = v

        # 新高顶背离: 指数涨 + 新高占比下降
        scores["new_high"] = _apply_new_high_divergence(conn, td, scores["new_high"])

        # 各维度分数计算 (按指标权重加权, 与综合分口径一致)
        dim_scores = {}
        for dim_name in DIMENSIONS:
            ind_keys = [k for k, v in INDICATOR_DIMENSIONS.items() if v == dim_name]
            available = [(k, scores[k]) for k in ind_keys if scores[k] is not None]
            if not available:
                dim_scores[dim_name] = None
                continue
            w = sum(INDICATOR_WEIGHTS[k] for k, _ in available)
            if w > 0:
                dim_scores[dim_name] = (
                    sum(v * INDICATOR_WEIGHTS[k] for k, v in available) / w)
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
                "buffett": scores["buffett"],
                "margin_ratio_v2": scores["margin_ratio"],
                "north_ratio": scores.get("north_ratio"),
                "yield_spread": scores.get("yield_spread"),
                "m1_m2_spread": scores.get("m1_m2_spread"),
                "seal_rate": scores["seal_rate"],
                "turnover_m2": scores["turnover_m2"],
                "turnover": scores["turnover"],
                "new_high": scores["new_high"],
                "ma_alignment": scores["ma_alignment"],
                "qvix": qvix,
                "qvix_components": qvix_components,
            },
            "indicator_raw": _raw | {"margin_ratio_v2": _raw.get("margin_ratio"),
                                     "north_ratio": _raw.get("north_ratio"),
                                     "yield_spread": _raw.get("yield_spread"),
                                     "m1_m2_spread": _raw.get("m1_m2_spread")},
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
            # F2修复(方案B): 只扣触发背离的换手率指标, 惩罚总额=20分(匹配README"最多20分")
            # 原实现对 turnover_m2 和 turnover 各扣20分=总40分, 惩罚翻倍导致信号失真
            for key in ("turnover",):
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

        # F8修复: 改用预计算表 daily_new_high, 替代原 4 次全量 stock_daily 查询
        now_row = conn.execute(
            "SELECT new_high_ratio FROM daily_new_high WHERE trade_date=?",
            (td,)
        ).fetchone()
        prev_row = conn.execute(
            "SELECT new_high_ratio FROM daily_new_high WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1",
            (prev_td,)
        ).fetchone()
        if not now_row or not prev_row or now_row[0] is None or prev_row[0] is None:
            return new_high_score
        now_val = now_row[0] * 100
        prev_val = prev_row[0] * 100

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
