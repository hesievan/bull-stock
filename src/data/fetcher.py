"""
数据获取模块
所有数据通过 akshare 获取，支持增量更新
"""
import logging
import time
import json
from datetime import datetime, timedelta, date
from typing import Optional

import pandas as pd

from src.data.database import get_conn, get_latest_date, save_dataframe, DB_PATH

logger = logging.getLogger(__name__)

# 板块/指数代码映射
INDEX_CODES = {
    "sh000001": "上证综指",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000300": "沪深300",
    "sh000905": "中证500",
    "sh000852": "中证1000",
    "bj430047": "北证50",       # 北交所
}

# 申万一级行业指数代码（部分主要行业）
SW_INDUSTRY_CODES = {
    "801010": "农林牧渔", "801020": "采掘", "801030": "化工",
    "801040": "钢铁", "801050": "有色金属", "801080": "电子",
    "801110": "家用电器", "801120": "食品饮料", "801130": "纺织服装",
    "801140": "轻工制造", "801150": "医药生物", "801160": "公用事业",
    "801170": "交通运输", "801180": "房地产", "801200": "商业贸易",
    "801210": "休闲服务", "801230": "综合", "801710": "建筑材料",
    "801720": "建筑装饰", "801730": "电气设备", "801740": "国防军工",
    "801750": "计算机", "801760": "传媒", "801770": "通信",
    "801780": "银行", "801790": "非银金融", "801880": "汽车",
    "801890": "机械设备",
}


def _import_akshare():
    """延迟导入 akshare，以便在未安装时给出友好提示"""
    try:
        import akshare as ak
        return ak
    except ImportError:
        logger.error("akshare not installed. Run: pip install akshare")
        raise


def _safe_fetch(func, *args, max_retries=3, delay=2, **kwargs):
    """带重试的请求封装"""
    last_err = None
    for attempt in range(max_retries):
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                wait = (attempt + 1) * delay
                logger.warning("Request failed (attempt %d/%d): %s, retrying in %ds",
                               attempt + 1, max_retries, str(e)[:100], wait)
                time.sleep(wait)
            else:
                logger.error("Request failed after %d attempts: %s", max_retries, str(e)[:200])
    return None


def fetch_index_daily(index_code: str, start: str, end: str) -> pd.DataFrame:
    """获取指数日行情"""
    ak = _import_akshare()
    try:
        df = _safe_fetch(ak.stock_zh_index_daily, symbol=index_code)
        if df is None or df.empty:
            logger.warning("No data for index %s", index_code)
            return pd.DataFrame()
        df.rename(columns={"date": "trade_date"}, inplace=True)
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
        df["index_code"] = index_code
        df = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)]
        df["pct_change"] = df["close"].pct_change() * 100
        df["amount"] = None  # stock_zh_index_daily 无成交额列
        return df[["trade_date", "index_code", "open", "high", "low", "close", "volume", "amount", "pct_change"]]
    except Exception as e:
        logger.error("fetch_index_daily(%s) failed: %s", index_code, e)
        return pd.DataFrame()


def fetch_all_index_history(start: str = "2005-01-01", end: str = None):
    """获取所有指数的历史数据"""
    end = end or date.today().strftime("%Y-%m-%d")
    for code, name in INDEX_CODES.items():
        logger.info("Fetching index: %s (%s) from %s to %s", code, name, start, end)
        df = fetch_index_daily(code, start, end)
        if not df.empty:
            save_dataframe(df, "index_daily")


def fetch_all_index_incremental(db_path: str = None):
    """增量更新指数数据"""
    latest = get_latest_date("index_daily", db_path=db_path) or "2005-01-01"
    # 回退5天补漏（防止节假日或数据延迟）
    start_dt = datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=5)
    start = start_dt.strftime("%Y-%m-%d")
    end = date.today().strftime("%Y-%m-%d")

    logger.info("Incremental index update: %s → %s", start, end)
    for code, name in INDEX_CODES.items():
        df = fetch_index_daily(code, start, end)
        if not df.empty:
            save_dataframe(df, "index_daily", db_path=db_path)


def fetch_stock_a_spot(date_str: str) -> Optional[pd.DataFrame]:
    """获取 A 股当日全市场快照（东方财富）
    字段: 代码,名称,最新价,涨跌幅,涨跌额,成交量,成交额,振幅,最高,最低,今开,昨收,量比,换手率,市盈率-动态,市净率,总市值,流通市值,60日涨跌幅,年初至今涨跌幅
    """
    ak = _import_akshare()
    try:
        df = _safe_fetch(ak.stock_zh_a_spot_em)
        if df is None or df.empty:
            # 降级：使用 stock_zh_a_spot（新浪）
            logger.warning("stock_zh_a_spot_em returned empty, trying sina...")
            df = _safe_fetch(ak.stock_zh_a_spot)
        if df is None or df.empty:
            return None
        # 添加日期
        df["trade_date"] = date_str
        # 过滤ST和退市
        if "名称" in df.columns:
            df = df[~df["名称"].str.contains("ST|退|PT", na=False, regex=True)]
        return df
    except Exception as e:
        logger.error("fetch_stock_a_spot(%s) failed: %s", date_str, e)
        return None


def fetch_northbound_daily(start: str, end: str) -> pd.DataFrame:
    """获取北向资金日度净流入"""
    ak = _import_akshare()
    try:
        df = _safe_fetch(ak.stock_hsgt_north_net_flow_in_em, symbol="北上")
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        logger.error("fetch_northbound_daily failed: %s", e)
        return pd.DataFrame()


def fetch_northbound_history(start: str, end: str) -> pd.DataFrame:
    """获取北向资金历史"""
    return fetch_northbound_daily(start, end)


def fetch_margin_history(start: str, end: str) -> pd.DataFrame:
    """获取融资融券历史"""
    ak = _import_akshare()
    try:
        df = _safe_fetch(ak.stock_margin_sse_summary_em)
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        logger.error("fetch_margin_history failed: %s", e)
        return pd.DataFrame()


def fetch_ah_premium() -> pd.DataFrame:
    """获取AH溢价"""
    ak = _import_akshare()
    try:
        df = _safe_fetch(ak.stock_hk_shshp)
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        logger.error("fetch_ah_premium failed: %s", e)
        return pd.DataFrame()


def fetch_index_pe(index_code: str = "sh000001") -> pd.DataFrame:
    """获取指数PE/PB历史（东方财富/中证指数）"""
    ak = _import_akshare()
    try:
        df = _safe_fetch(ak.stock_zh_index_value_csindex, symbol=index_code)
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        logger.error("fetch_index_pe(%s) failed: %s", index_code, e)
        return pd.DataFrame()


def fetch_bond_yield_10y() -> float:
    """获取10年期国债收益率"""
    ak = _import_akshare()
    try:
        df = _safe_fetch(ak.bond_zh_us_rate)
        if df is not None and not df.empty:
            df = df.sort_values("日期", ascending=False)
            row = df.iloc[0]
            for col in row.index:
                if "10" in str(col) and ("国债" in str(col) or "收益率" in str(col)):
                    val = row[col]
                    if pd.notna(val):
                        return float(val)
        return 2.5  # 默认值
    except Exception as e:
        logger.error("fetch_bond_yield_10y failed: %s", e)
        return 2.5


def fetch_new_investors(date_str: str) -> Optional[int]:
    """获取新增投资者数（中国结算，月度数据）"""
    ak = _import_akshare()
    try:
        df = _safe_fetch(ak.stock_account_statistics_em)
        if df is not None and not df.empty:
            return int(df.iloc[-1, 1]) if len(df.columns) > 1 else None
        return None
    except Exception as e:
        logger.error("fetch_new_investors failed: %s", e)
        return None


def fetch_sw_industry_daily(industry_code: str, start: str, end: str) -> pd.DataFrame:
    """获取申万行业指数日行情"""
    ak = _import_akshare()
    try:
        df = _safe_fetch(ak.index_historical_fund_flow, symbol=industry_code)
        # 如果上面不行，尝试 stock_zh_index_daily
        if df is None or df.empty:
            code_map = {
                "801010": "sh000001",  # 此处需替换为申万行业指数代码
            }
            mapped = code_map.get(industry_code)
            if mapped:
                df = _safe_fetch(ak.stock_zh_index_daily, symbol=mapped)
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        logger.error("fetch_sw_industry_daily(%s) failed: %s", industry_code, e)
        return pd.DataFrame()


def fetch_all_history(start: str = "2015-01-01", end: str = None):
    """一次性拉取所有历史数据（初始化用）"""
    end = end or date.today().strftime("%Y-%m-%d")
    logger.info("Starting full history fetch: %s → %s", start, end)

    # 1. 指数行情
    logger.info("Step 1/6: Index daily data...")
    fetch_all_index_history(start, end)

    # 2. 融资融券
    logger.info("Step 2/6: Margin data...")
    df = fetch_margin_history(start, end)
    if not df.empty:
        save_dataframe(df, "margin_history")

    # 3. 北向资金
    logger.info("Step 3/6: Northbound data...")
    df = fetch_northbound_history(start, end)
    if not df.empty:
        save_dataframe(df, "northbound_history")

    # 4. AH溢价
    logger.info("Step 4/6: AH premium...")
    df = fetch_ah_premium()
    if not df.empty:
        save_dataframe(df, "ah_premium")

    logger.info("History fetch complete!")
