"""
数据获取模块
所有数据通过 akshare 获取，支持增量更新
"""
import logging
from datetime import datetime, timedelta, date
from typing import Optional

import pandas as pd
import akshare as ak

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


def fetch_index_daily(index_code: str, start: str, end: str) -> pd.DataFrame:
    """获取指数日行情"""
    try:
        df = ak.stock_zh_index_daily(symbol=index_code)
        if df is None or df.empty:
            logger.warning("No data for index %s", index_code)
            return pd.DataFrame()
        df.rename(columns={"date": "trade_date"}, inplace=True)
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
        df["index_code"] = index_code
        df = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)]
        df["pct_change"] = df["close"].pct_change() * 100
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
    start = (datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y-%m-%d")  # 回退5天补漏
    end = date.today().strftime("%Y-%m-%d")
    logger.info("Incremental fetch index data from %s to %s", start, end)
    for code, name in INDEX_CODES.items():
        df = fetch_index_daily(code, start, end)
        if not df.empty:
            save_dataframe(df, "index_daily", if_exists="append", db_path=db_path)


def fetch_stock_daily_batch(start: str, end: str, db_path: str = None):
    """
    获取全市场个股日行情（含PE/PB）
    使用 akshare 的 stock_zh_a_daily 接口
    分批获取以避免内存溢出
    """
    try:
        # 获取A股列表
        stock_list = ak.stock_zh_a_spot_em()
        codes = stock_list["代码"].tolist()[:50]  # MVP 先处理前50只，后续扩展
        logger.info("Fetching stock daily data for %d stocks", len(codes))

        for code in codes:
            try:
                df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq")
                if df is None or df.empty:
                    continue
                df.rename(columns={
                    "日期": "trade_date", "开盘": "open", "最高": "high",
                    "最低": "low", "收盘": "close", "成交量": "volume",
                    "成交额": "amount", "涨跌幅": "pct_change",
                    "市盈率-动态": "pe", "市净率": "pb",
                    "总市值": "total_mv", "流通市值": "circ_mv"
                }, inplace=True)
                df["stock_code"] = code
                df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
                df = df[["trade_date", "stock_code", "open", "high", "low", "close",
                         "volume", "amount", "pct_change", "pe", "pb", "total_mv", "circ_mv"]]
                save_dataframe(df, "stock_daily", if_exists="append", db_path=db_path)
            except Exception as e:
                logger.warning("fetch stock %s failed: %s", code, e)
    except Exception as e:
        logger.error("fetch_stock_daily_batch failed: %s", e)


def fetch_margin_daily(start: str, end: str, db_path: str = None):
    """获取融资融券数据"""
    try:
        df = ak.stock_margin_sse_summary(start_date=start.replace("-", ""), end_date=end.replace("-", ""))
        if df is None or df.empty:
            return
        df.rename(columns={"信用交易日期": "trade_date", "融资余额": "margin_balance",
                            "融资买入额": "margin_buy"}, inplace=True)
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
        save_dataframe(df[["trade_date", "margin_balance", "margin_buy"]], "margin_daily", db_path=db_path)
    except Exception as e:
        logger.error("fetch_margin_daily failed: %s", e)


def fetch_northbound_daily(start: str, end: str, db_path: str = None):
    """获取北向资金数据"""
    try:
        df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
        if df is None or df.empty:
            return
        df.rename(columns={"日期": "trade_date", "净流入": "净流入"}, inplace=True)
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
        df = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)]
        df["净流入"] = pd.to_numeric(df["净流入"], errors="coerce") / 1e8  # 转为亿元
        save_dataframe(df[["trade_date", "净流入"]], "northbound_daily", db_path=db_path)
    except Exception as e:
        logger.error("fetch_northbound_daily failed: %s", e)


def fetch_bond_yield(start: str = "2010-01-01", end: str = None, db_path: str = None):
    """获取国债收益率"""
    try:
        df = ak.bond_china_yten()
        if df is None or df.empty:
            return
        # akshare 返回当日数据，历史需要循环
        # 使用 macrotrends 接口获取更长历史
        df = ak.bond_zh_us_rate(start_date=start.replace("-", ""))
        if df is None or df.empty:
            return
        logger.info("Bond yield data: %d rows", len(df))
    except Exception as e:
        logger.error("fetch_bond_yield failed: %s", e)


def fetch_limit_up_daily(trade_date: str, db_path: str = None):
    """获取涨停数据"""
    try:
        df = ak.stock_zt_pool_em(date=trade_date.replace("-", ""))
        if df is None or df.empty:
            return pd.DataFrame()
        df["trade_date"] = trade_date
        df = df[["trade_date", "代码"]].rename(columns={"代码": "stock_code"})
        save_dataframe(df, "limit_up_daily", db_path=db_path)
        return df
    except Exception as e:
        logger.warning("fetch_limit_up_daily(%s) failed: %s", trade_date, e)
        return pd.DataFrame()


def fetch_ah_premium(start: str = "2010-01-01", end: str = None, db_path: str = None):
    """获取AH溢价指数"""
    try:
        df = ak.stock_ah_index_daily(symbol="gx100", adjust="")
        if df is None or df.empty:
            return
        df["trade_date"] = pd.to_datetime(df.index).strftime("%Y-%m-%d")
        df = df[(df["trade_date"] >= start) & (df["trade_date"] <= (end or "2099-12-31"))]
        df.rename(columns={"收盘": "premium"}, inplace=True)
        df["premium"] = pd.to_numeric(df["premium"], errors="coerce")
        save_dataframe(df[["trade_date", "premium"]], "ah_premium", db_path=db_path)
    except Exception as e:
        logger.error("fetch_ah_premium failed: %s", e)


def run_initial_history(db_path: str = None):
    """一次性初始化历史数据"""
    start = "2015-01-01"
    end = date.today().strftime("%Y-%m-%d")
    logger.info("=" * 60)
    logger.info("INITIALIZING HISTORICAL DATA: %s to %s", start, end)
    logger.info("=" * 60)

    fetch_all_index_history(start, end, db_path)
    # fetch_margin_daily(start, end, db_path)  # 按需启用
    # fetch_northbound_daily(start, end, db_path)
    # fetch_ah_premium(start, end, db_path)

    logger.info("Historical data initialization complete")


def run_daily_update(db_path: str = None):
    """每日增量更新"""
    logger.info("=" * 60)
    logger.info("DAILY INCREMENTAL UPDATE: %s", date.today())
    logger.info("=" * 60)

    fetch_all_index_incremental(db_path)
    # 其他增量更新...

    logger.info("Daily update complete")


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "init"
    if cmd == "init":
        run_initial_history()
    elif cmd == "daily":
        run_daily_update()
    else:
        print("Usage: python fetcher.py [init|daily]")
