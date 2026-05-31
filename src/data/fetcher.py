"""
数据获取模块 - tushare + akshare 混合方案
tushare: 指数PE/PB、融资融券、北向资金、国债收益率、个股列表
akshare: 指数日行情、全市场个股快照、申万行业指数、涨停数据
"""
import logging
import time
import os
from datetime import datetime, timedelta, date
from typing import Optional

import pandas as pd

from src.data.database import get_conn, get_latest_date, save_dataframe, DB_PATH

logger = logging.getLogger(__name__)

# tushare token (从环境变量读取)
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")

# 指数代码 akshare 格式 ↔ tushare 格式
INDEX_CODE_AK_TO_TS = {
    "sh000001": "000001.SH",
    "sz399001": "399001.SZ",
    "sz399006": "399006.SZ",
    "sh000300": "000300.SH",
    "sh000905": "000905.SH",
    "sh000852": "000852.SH",
    "bj430047": "430047.BJ",
}
INDEX_CODE_TS_TO_AK = {v: k for k, v in INDEX_CODE_AK_TO_TS.items()}

# akshare 指数代码 → 中文名
INDEX_NAMES = {
    "sh000001": "上证综指",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000300": "沪深300",
    "sh000905": "中证500",
    "sh000852": "中证1000",
    "bj430047": "北证50",
}


def _ts_pro():
    """获取 tushare pro_api 实例"""
    import tushare as ts
    if not TUSHARE_TOKEN:
        raise RuntimeError("TUSHARE_TOKEN not set")
    return ts.pro_api(TUSHARE_TOKEN)


def _ak():
    """获取 akshare 模块"""
    import akshare as ak
    return ak


def _rate_limit(min_interval: float = 4.0):
    """tushare 频率限制器（最低间隔秒数）"""
    _rate_limit.last_call = getattr(_rate_limit, 'last_call', 0)
    elapsed = time.time() - _rate_limit.last_call
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _rate_limit.last_call = time.time()


# ============================================================
# 指数日行情 (akshare: stock_zh_index_daily)
# ============================================================

def fetch_index_daily(index_code: str, start: str, end: str) -> pd.DataFrame:
    """获取指数日行情"""
    try:
        ak = _ak()
        df = ak.stock_zh_index_daily(symbol=index_code)
        if df is None or df.empty:
            return pd.DataFrame()
        df.rename(columns={"date": "trade_date"}, inplace=True)
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
        df["index_code"] = index_code
        df = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)]
        df["pct_change"] = df["close"].pct_change() * 100
        df["amount"] = None
        return df[["trade_date", "index_code", "open", "high", "low", "close", "volume", "amount", "pct_change"]]
    except Exception as e:
        logger.error("fetch_index_daily(%s) failed: %s", index_code, e)
        return pd.DataFrame()


def fetch_all_index_history(start: str = "2005-01-01", end: str = None):
    """获取所有指数历史日行情"""
    end = end or date.today().strftime("%Y-%m-%d")
    for code, name in INDEX_NAMES.items():
        logger.info("Fetching index: %s (%s)", code, name)
        df = fetch_index_daily(code, start, end)
        if not df.empty:
            save_dataframe(df, "index_daily")


def fetch_all_index_incremental(db_path: str = None):
    """增量更新指数日行情"""
    latest = get_latest_date("index_daily", db_path=db_path) or "2005-01-01"
    start_dt = datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=5)
    start = start_dt.strftime("%Y-%m-%d")
    end = date.today().strftime("%Y-%m-%d")
    for code, name in INDEX_NAMES.items():
        df = fetch_index_daily(code, start, end)
        if not df.empty:
            save_dataframe(df, "index_daily", db_path=db_path)


# ============================================================
# 指数PE/PB (tushare: index_dailybasic) — 需 4秒/次 频率控制
# ============================================================

def fetch_index_pe_history(index_code: str, start: str, end: str) -> pd.DataFrame:
    """获取指数PE/PB历史"""
    try:
        _rate_limit(4.0)
        pro = _ts_pro()
        ts_code = INDEX_CODE_AK_TO_TS.get(index_code, index_code)
        df = pro.index_dailybasic(
            ts_code=ts_code,
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            fields="ts_code,trade_date,pe,pe_ttm,pb,total_mv,turnover_rate,turnover_rate_f"
        )
        if df is not None and not df.empty:
            df.rename(columns={"ts_code": "index_code", "total_mv": "total_mv_yi"}, inplace=True)
            df["index_code"] = INDEX_CODE_TS_TO_AK.get(df["index_code"].iloc[0], df["index_code"].iloc[0])
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        logger.error("fetch_index_pe_history(%s) failed: %s", index_code, e)
        return pd.DataFrame()


def fetch_all_index_pe_history():
    """拉取所有指数的PE/PB历史（初始化用）"""
    for code in INDEX_NAMES:
        logger.info("Fetching PE/PB for %s...", code)
        df = fetch_index_pe_history(code, "2005-01-01", date.today().strftime("%Y-%m-%d"))
        if not df.empty:
            save_dataframe(df, "index_pe_history")


# ============================================================
# 融资融券 (tushare: margin)
# ============================================================

def fetch_margin_history(start: str, end: str) -> pd.DataFrame:
    """获取沪深融资融券汇总"""
    try:
        _rate_limit(4.0)
        pro = _ts_pro()
        df = pro.margin(
            start_date=start.replace("-", ""),
            end_date=end.replace("-", "")
        )
        if df is not None and not df.empty:
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
            # 合并沪深北三市: 按 trade_date 聚合
            df = df.groupby("trade_date").agg({
                "rzye": "sum",   # 融资余额
                "rzmre": "sum",  # 融资买入额
                "rqye": "sum",   # 融券余额
            }).reset_index()
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        logger.error("fetch_margin_history failed: %s", e)
        return pd.DataFrame()


# ============================================================
# 北向资金 (tushare: moneyflow_hsgt)
# ============================================================

def fetch_northbound_history(start: str, end: str) -> pd.DataFrame:
    """获取北向资金日度净流入"""
    try:
        _rate_limit(4.0)
        pro = _ts_pro()
        df = pro.moneyflow_hsgt(
            start_date=start.replace("-", ""),
            end_date=end.replace("-", "")
        )
        if df is not None and not df.empty:
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
            # 北向 = 沪股通(hgt) + 深股通(sgt)
            df["north_net"] = pd.to_numeric(df["hgt"], errors="coerce").fillna(0) + \
                              pd.to_numeric(df["sgt"], errors="coerce").fillna(0)
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        logger.error("fetch_northbound_history failed: %s", e)
        return pd.DataFrame()


# ============================================================
# 国债收益率 (tushare: yc_cb)
# ============================================================

def fetch_bond_yield_history(start: str, end: str) -> pd.DataFrame:
    """获取10年期国债收益率历史"""
    try:
        _rate_limit(4.0)
        pro = _ts_pro()
        df = pro.yc_cb(
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            curve_type="1"   # 中债国债收益率曲线
        )
        if df is not None and not df.empty:
            # 筛选10年期
            df_10y = df[df["curve_term"] == 10.0].copy()
            df_10y["trade_date"] = pd.to_datetime(df_10y["trade_date"]).dt.strftime("%Y-%m-%d")
            df_10y = df_10y[["trade_date", "yield"]].copy()
            df_10y.rename(columns={"yield": "yield_rate"}, inplace=True)
            return df_10y
        return pd.DataFrame()
    except Exception as e:
        logger.error("fetch_bond_yield_history failed: %s", e)
        return pd.DataFrame()


# ============================================================
# 新增投资者 (tushare: new_share → 换算月度新增)
# ============================================================

def fetch_new_investors(start: str, end: str) -> pd.DataFrame:
    """获取新增投资者数据（中国结算月度）"""
    try:
        _rate_limit(4.0)
        pro = _ts_pro()
        df = pro.new_share(
            start_date=start.replace("-", ""),
            end_date=end.replace("-", "")
        )
        if df is not None and not df.empty:
            # new_share 是IPO数据，不直接等于新增投资者
            # 实际新增投资者需要从中国结算网站获取
            # 这里暂时返回IPO数量作为替代指标
            df["trade_date"] = pd.to_datetime(df["ipo_date"]).dt.strftime("%Y-%m-%d")
            return df
        return pd.DataFrame()
    except Exception as e:
        logger.error("fetch_new_investors failed: %s", e)
        return pd.DataFrame()


# ============================================================
# 全市场个股快照 (akshare: stock_zh_a_spot_em)
# 用于计算: 涨跌家数比、涨停占比、破净率、站上年线、创新高
# ============================================================

def fetch_stock_spot(date_str: str) -> Optional[pd.DataFrame]:
    """获取A股当日全市场快照"""
    try:
        ak = _ak()
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return None
        df["trade_date"] = date_str
        # 过滤ST和退市
        if "名称" in df.columns:
            df = df[~df["名称"].str.contains("ST|退|PT", na=False, regex=True)]
        return df
    except Exception as e:
        logger.error("fetch_stock_spot(%s) failed: %s", date_str, e)
        return None


# ============================================================
# 个股日行情 (tushare: daily + daily_basic — 需逐只查，慢)
# 用于初始化历史数据: 破净率、站上年线、创新高
# ============================================================

def fetch_stock_daily_basic(ts_code: str, start: str, end: str) -> pd.DataFrame:
    """获取单只股票每日指标（PE/PB/换手率等）"""
    try:
        _rate_limit(4.0)
        pro = _ts_pro()
        df = pro.daily_basic(
            ts_code=ts_code,
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            fields="ts_code,trade_date,pe_ttm,pb,ps,total_mv,circ_mv,turnover_rate,float_share"
        )
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        logger.error("fetch_stock_daily_basic(%s) failed: %s", ts_code, e)
        return pd.DataFrame()


def fetch_stock_list() -> pd.DataFrame:
    """获取全部上市股票列表"""
    try:
        _rate_limit(4.0)
        pro = _ts_pro()
        df = pro.stock_basic(
            exchange="", list_status="L",
            fields="ts_code,symbol,name,industry,market,list_date"
        )
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        logger.error("fetch_stock_list failed: %s", e)
        return pd.DataFrame()


# ============================================================
# AH溢价 (akshare: stock_hk_shshp)
# ============================================================

def fetch_ah_premium(start: str, end: str) -> pd.DataFrame:
    """获取AH溢价指数 (akshare: stock_zh_ah_spot_em)"""
    try:
        ak = _ak()
        for fn_name in ["stock_zh_ah_spot_em", "stock_zh_ah_spot"]:
            try:
                fn = getattr(ak, fn_name, None)
                if fn is None:
                    continue
                df = fn()
                if df is not None and not df.empty:
                    date_col = next((c for c in df.columns if "date" in c.lower() or "日期" in c), None)
                    premium_col = next((c for c in df.columns if "溢价" in c or "premium" in c.lower()), None)
                    if date_col and premium_col:
                        df["trade_date"] = pd.to_datetime(df[date_col]).dt.strftime("%Y-%m-%d")
                        df["premium"] = pd.to_numeric(df[premium_col], errors="coerce")
                        df = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)]
                        return df[["trade_date", "premium"]]
            except Exception:
                continue
        return pd.DataFrame()
    except Exception as e:
        logger.error("fetch_ah_premium failed: %s", e)
        return pd.DataFrame()


# ============================================================
# 初始化入口
# ============================================================

def fetch_all_history(start: str = "2015-01-01", end: str = None):
    """一次性拉取所有历史数据（初始化用）"""
    end = end or date.today().strftime("%Y-%m-%d")
    logger.info("Starting full history fetch: %s → %s", start, end)

    steps = [
        ("指数日行情", lambda: fetch_all_index_history(start, end)),
        ("融资融券", lambda: save_or_skip(fetch_margin_history(start, end), "margin_history")),
        ("北向资金", lambda: save_or_skip(fetch_northbound_history(start, end), "northbound_history")),
        ("国债收益率", lambda: _save_bond_yield(fetch_bond_yield_history(start, end))),
        ("AH溢价", lambda: save_or_skip(fetch_ah_premium(start, end), "ah_premium")),
        ("指数PE/PB", lambda: fetch_all_index_pe_history()),
    ]

    for name, func in steps:
        logger.info("Step: %s...", name)
        try:
            func()
        except Exception as e:
            logger.error("Step %s failed: %s", name, e)

    logger.info("History fetch complete!")



def _save_bond_yield(df: pd.DataFrame):
    """保存国债收益率 (只存10年期)"""
    if df.empty:
        return
    df_10y = df[df["curve_term"] == 10.0][["trade_date", "yield_rate"]].copy()
    if not df_10y.empty:
        save_dataframe(df_10y, "bond_yield")

def save_or_skip(df: pd.DataFrame, table: str):
    """辅助: 非空则保存"""
    if not df.empty:
        save_dataframe(df, table)
