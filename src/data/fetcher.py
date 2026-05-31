"""
数据获取模块 — 三源合一 (baostock + tushare + akshare)

数据源分工:
  baostock : 指数日行情、个股K线(PE/PB/价格/成交量)、成分股列表、行业分类、交易日历
             → 不限频，只需控制间隔 ~0.3s
  tushare  : 融资融券、北向资金、国债收益率、指数PE/PB(备用)
             → 频率限制 1次/小时，需缓存规避
  akshare  : AH溢价 (东方财富接口，TUN 环境下可能不稳定，仅做补充)

代码约定:
  - 内部统一用 akshare 格式: sh000001, sz399006, sh.600000, sz.000001
  - baostock 格式:            sh.000001, sz.399006 (自动转换)
  - tushare 格式:             000001.SH, 399006.SZ (自动转换)
"""
import logging
import time
import os
from datetime import datetime, timedelta, date
from typing import Optional, List

import pandas as pd
import numpy as np

from src.data.database import get_conn, get_latest_date, save_dataframe, DB_PATH
import sqlite3

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────────

TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")

# 指数代码映射: akshare格式 → (baostock格式, tushare格式)
INDEX_CODE_MAP = {
    "sh000001": ("sh.000001", "000001.SH"),   # 上证综指
    "sz399001": ("sz.399001", "399001.SZ"),   # 深证成指
    "sz399006": ("sz.399006", "399006.SZ"),   # 创业板指
    "sh000300": ("sh.000300", "000300.SH"),   # 沪深300
    "sh000905": ("sh.000905", "000905.SH"),   # 中证500
    "sh000852": ("sh.000852", "000852.SH"),   # 中证1000
}
INDEX_NAMES = {
    "sh000001": "上证综指", "sz399001": "深证成指", "sz399006": "创业板指",
    "sh000300": "沪深300", "sh000905": "中证500", "sh000852": "中证1000",
}

BS_INTERVAL = 0.3   # baostock 请求间隔(秒)
TS_INTERVAL = 4.0   # tushare 请求间隔(秒), 实际限制1次/小时


# ── 内部工具 ──────────────────────────────────────────────────────────────────

def _bs_sleep():
    now = time.time()
    wait = BS_INTERVAL - (now - getattr(_bs_sleep, '_last', 0))
    if wait > 0:
        time.sleep(wait)
    _bs_sleep._last = time.time()

def _ts_sleep():
    now = time.time()
    wait = TS_INTERVAL - (now - getattr('_ts_sleep', '_last', 0))
    if wait > 0:
        time.sleep(wait)
    _ts_sleep._last = time.time()

def _save(df: pd.DataFrame, table: str):
    if not df.empty:
        save_dataframe(df, table)
        logger.info("  → %s: %d rows", table, len(df))

def ak_to_bs(code: str) -> str:
    """akshare代码(sh000001或sh.600000) → baostock代码(sh.000001)"""
    if code.startswith(("sh.", "sz.", "bj.")): return code  # already baostock
    if code.startswith("sh"): return "sh." + code[2:]
    if code.startswith("sz"): return "sz." + code[2:]
    if code.startswith("bj"): return "bj." + code[2:]
    return code
def bs_to_ak(code: str) -> str:
    """baostock代码 → akshare代码: sh.000001 → sh000001"""
    if code.startswith("sh."): return "sh" + code[3:]
    if code.startswith("sz."): return "sz" + code[3:]
    if code.startswith("bj."): return "bj" + code[3:]
    return code

def ak_to_ts(code: str) -> str:
    """akshare代码 → tushare代码: sh000001 → 000001.SH"""
    pure = code.split(".")[-1] if "." in code else code[2:]
    if code.startswith(("sh", "sh.")): return pure + ".SH"
    if code.startswith(("sz", "sz.")): return pure + ".SZ"
    if code.startswith(("bj", "bj.")): return pure + ".BJ"
    return code

def _bs_to_df(rs) -> pd.DataFrame:
    """baostock ResultSet → DataFrame"""
    if rs is None or rs.error_code != '0':
        return pd.DataFrame()
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    return pd.DataFrame(rows, columns=rs.fields) if rows else pd.DataFrame()


# ── baostock 连接管理 ─────────────────────────────────────────────────────────

def bs_login():
    import baostock as bs
    lg = bs.login()
    if lg.error_code != '0':
        logger.warning("baostock login: %s", lg.error_msg)
    return lg

def bs_logout():
    import baostock as bs
    bs.logout()


# ── baostock: 指数日行情 ──────────────────────────────────────────────────────

def fetch_index_daily(ak_code: str, start: str, end: str) -> pd.DataFrame:
    """
    获取指数日行情 (baostock query_history_k_data_plus)
    ak_code: akshare 格式 (sh000001)
    返回列: trade_date, index_code, open, high, low, close, volume, amount, pct_change
    """
    import baostock as bs
    bs_code = INDEX_CODE_MAP.get(ak_code, (ak_code,))[0]
    try:
        _bs_sleep()
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume,amount,pctChg",
            start_date=start, end_date=end,
            frequency="d", adjustflag="3"
        )
        df = _bs_to_df(rs)
        if not df.empty:
            df.rename(columns={"date": "trade_date", "pctChg": "pct_change"}, inplace=True)
            df["index_code"] = ak_code
            for col in ["open", "high", "low", "close", "volume", "amount", "pct_change"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception as e:
        logger.error("fetch_index_daily(%s): %s", ak_code, e)
        return pd.DataFrame()

def fetch_all_index_history(start="2015-01-01", end=None, db_path=None):
    end = end or date.today().strftime("%Y-%m-%d")
    for ak_code, name in INDEX_NAMES.items():
        logger.info("Index history: %s (%s)", ak_code, name)
        df = fetch_index_daily(ak_code, start, end)
        _save(df, "index_daily")

def fetch_all_index_incremental(db_path=None):
    """增量更新指数日行情"""
    latest = get_latest_date("index_daily", db_path=db_path) or "2015-01-01"
    start = (datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y-%m-%d")
    end = date.today().strftime("%Y-%m-%d")
    for ak_code in INDEX_NAMES:
        df = fetch_index_daily(ak_code, start, end)
        _save(df, "index_daily")


# ── baostock: 成分股列表 ──────────────────────────────────────────────────────

def fetch_index_constituents(index="hs300") -> pd.DataFrame:
    """
    获取指数成分股 (baostock)
    index: hs300 / sz50 / zz500
    返回: DataFrame[code(ak格式), code_name, ...]
    """
    import baostock as bs
    fn_map = {"hs300": bs.query_hs300_stocks, "sz50": bs.query_sz50_stocks, "zz500": bs.query_zz500_stocks}
    try:
        _bs_sleep()
        rs = fn_map[index]()
        df = _bs_to_df(rs)
        if not df.empty and "code" in df.columns:
            df["code"] = df["code"].apply(bs_to_ak)
        return df
    except Exception as e:
        logger.error("fetch_index_constituents(%s): %s", index, e)
        return pd.DataFrame()

def fetch_all_stock_list(day: str = None) -> pd.DataFrame:
    """获取全部A股代码列表 (baostock query_all_stock)"""
    import baostock as bs
    day = day or date.today().strftime("%Y-%m-%d")
    try:
        _bs_sleep()
        rs = bs.query_all_stock(day=day.replace("-", ""))
        df = _bs_to_df(rs)
        if not df.empty:
            df["code"] = df["code"].apply(bs_to_ak)
            # 只保留A股 (排除B股/基金等)
            df = df[df["code"].str.match(r'^(sh6|sz[03]|bj)')]
            # 过滤ST/退市
            if "code_name" in df.columns:
                df = df[~df["code_name"].str.contains(r'ST|退|PT|N ', na=False)]
        return df
    except Exception as e:
        logger.error("fetch_all_stock_list: %s", e)
        return pd.DataFrame()


# ── baostock: 个股K线 ────────────────────────────────────────────────────────

def fetch_stock_kline(ak_code: str, start: str, end: str,
                      fields="date,close,peTTM,pbMRQ,pctChg,volume,amount") -> pd.DataFrame:
    """
    获取单只股票日K线 (baostock)
    ak_code: akshare 格式 (sh.600000 或 sh600000 均可)
    返回列: trade_date, close, peTTM, pbMRQ, pct_change, volume, amount, stock_code
    """
    import baostock as bs
    bs_code = ak_to_bs(ak_code)
    try:
        _bs_sleep()
        rs = bs.query_history_k_data_plus(
            bs_code, fields,
            start_date=start, end_date=end,
            frequency="d", adjustflag="3"
        )
        df = _bs_to_df(rs)
        if not df.empty:
            df.rename(columns={"pctChg": "pct_change", "date": "trade_date"}, inplace=True)
            for col in ["close", "peTTM", "pbMRQ", "pct_change", "volume", "amount"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            df["stock_code"] = ak_code
        return df
    except Exception as e:
        logger.error("fetch_stock_kline(%s): %s", ak_code, e)
        return pd.DataFrame()

def fetch_stocks_kline_batch(codes: List[str], start: str, end: str,
                              db_path=None, label="") -> pd.DataFrame:
    """
    批量获取多只股票K线，每50只分批存入 stock_daily
    避免全部拉完再一次性写入导致中途失败数据丢失
    """
    BATCH_SIZE = 50
    total = len(codes)
    grand_total = 0
    for batch_start in range(0, total, BATCH_SIZE):
        batch_codes = codes[batch_start:batch_start + BATCH_SIZE]
        batch_dfs = []
        for code in batch_codes:
            df = fetch_stock_kline(code, start, end)
            if not df.empty:
                batch_dfs.append(df)
        if batch_dfs:
            result = pd.concat(batch_dfs, ignore_index=True)
            save_dataframe(result, "stock_daily")
            grand_total += len(result)
        done = min(batch_start + BATCH_SIZE, total)
        logger.info("  Stock kline progress: %d/%d %s (saved %d files, %d total rows)",
                     done, total, label, len(batch_dfs) if batch_dfs else 0, grand_total)
        for h in logger.handlers:
            h.flush()

    logger.info("fetch_stocks_kline_batch complete: %d total stock-day rows saved", grand_total)
    return pd.DataFrame()

def fetch_stocks_latest_day(stock_codes: List[str], trade_date: str) -> pd.DataFrame:
    """获取多只股票最近交易日K线（增量更新用）"""
    start = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    return fetch_stocks_kline_batch(stock_codes, start, trade_date, label=trade_date)


# ── baostock: 行业分类 ───────────────────────────────────────────────────────

def fetch_stock_industry() -> pd.DataFrame:
    """获取个股行业分类 (baostock query_stock_industry)"""
    import baostock as bs
    try:
        bs.login()
        _bs_sleep()
        rs = bs.query_stock_industry()
        df = _bs_to_df(rs)
        if not df.empty:
            # 标准化列名: code → ak格式
            if "code" in df.columns:
                df["code"] = df["code"].apply(bs_to_ak)
            df.rename(columns={
                "code_name": "code_name",
                "industry": "industry",
                "industryClassification": "industry_classification",
                "updateDate": "update_date"
            }, inplace=True, errors='ignore')
        return df
    except Exception as e:
        logger.error("fetch_stock_industry: %s", e)
        return pd.DataFrame()


# ── baostock: 交易日历 ───────────────────────────────────────────────────────

def fetch_trade_dates(start: str, end: str) -> pd.DataFrame:
    import baostock as bs
    try:
        _bs_sleep()
        rs = bs.query_trade_dates(
            start_date=start.replace("-", ""),
            end_date=end.replace("-", "")
        )
        return _bs_to_df(rs)
    except Exception as e:
        logger.error("fetch_trade_dates: %s", e)
        return pd.DataFrame()


# ── tushare: 融资融券 ────────────────────────────────────────────────────────

def fetch_margin_history(start: str, end: str) -> pd.DataFrame:
    """融资融券日汇总 (tushare margin 接口)"""
    try:
        import tushare as ts
        _ts_sleep()
        pro = ts.pro_api(TUSHARE_TOKEN)
        df = pro.margin(start_date=start.replace("-", ""), end_date=end.replace("-", ""))
        if df is not None and not df.empty:
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
            df = df.groupby("trade_date", as_index=False).agg({
                "rzye": "sum", "rzmre": "sum", "rzche": "sum",
                "rqye": "sum", "rqmcl": "sum", "rzrqye": "sum",
            })
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        logger.error("fetch_margin_history: %s", e)
        return pd.DataFrame()


# ── tushare: 北向资金 ─────────────────────────────────────────────────────────

def fetch_northbound_history(start: str, end: str) -> pd.DataFrame:
    """北向资金日度净流入 (tushare moneyflow_hsgt 接口)"""
    try:
        import tushare as ts
        _ts_sleep()
        pro = ts.pro_api(TUSHARE_TOKEN)
        df = pro.moneyflow_hsgt(start_date=start.replace("-", ""), end_date=end.replace("-", ""))
        if df is not None and not df.empty:
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
            df["north_net"] = pd.to_numeric(df.get("hgt", 0), errors="coerce").fillna(0) + \
                              pd.to_numeric(df.get("sgt", 0), errors="coerce").fillna(0)
            # 只保留数据库表中的列
            _keep = ["trade_date", "hgt", "sgt", "north_net", "south_money"]
            df = df[[c for c in _keep if c in df.columns]]
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        logger.error("fetch_northbound_history: %s", e)
        return pd.DataFrame()


# ── tushare: 国债收益率 ──────────────────────────────────────────────────────

def fetch_bond_yield_history(start: str, end: str) -> pd.DataFrame:
    """中债国债收益率 (tushare yc_cb 接口)"""
    try:
        import tushare as ts
        _ts_sleep()
        pro = ts.pro_api(TUSHARE_TOKEN)
        df = pro.yc_cb(
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            curve_type="1"  # 国债
        )
        if df is not None and not df.empty:
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
            return df[["trade_date", "curve_term", "yield"]].rename(
                columns={"yield": "yield_rate"}
            ).copy()
        return pd.DataFrame()
    except Exception as e:
        logger.error("fetch_bond_yield_history: %s", e)
        return pd.DataFrame()


# ── tushare: 指数PE/PB (备用/baostock 无此字段时用) ────────────────────────────

def fetch_index_pe_history(start: str, end: str) -> pd.DataFrame:
    """指数PE/PB历史 (tushare index_dailybasic 接口)"""
    try:
        import tushare as ts
        _ts_sleep()
        pro = ts.pro_api(TUSHARE_TOKEN)
        all_dfs = []
        for ak_code, (bs_code, ts_code) in INDEX_CODE_MAP.items():
            df = pro.index_dailybasic(
                ts_code=ts_code,
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
                fields="trade_date,pe_ttm,pb,total_mv,turnover_rate"
            )
            if df is not None and not df.empty:
                df["index_code"] = ak_code
                all_dfs.append(df)
        return pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()
    except Exception as e:
        logger.error("fetch_index_pe_history: %s", e)
        return pd.DataFrame()


# ── akshare: AH溢价 ──────────────────────────────────────────────────────────

def fetch_ah_premium(start: str, end: str) -> pd.DataFrame:
    """AH溢价指数 (akshare stock_zh_ah_spot_em)"""
    try:
        import akshare as ak
        # 东方财富 AH 溢价实时行情
        df = ak.stock_zh_ah_spot_em()
        if df is not None and not df.empty:
            # 找日期和溢价率列
            date_col = next((c for c in df.columns if "日期" in c or "date" in c.lower()), None)
            premium_col = next((c for c in df.columns if "溢价" in c or "premium" in c.lower()), None)
            if date_col and premium_col:
                df2 = df[[date_col, premium_col]].copy()
                df2.columns = ["trade_date", "premium"]
                df2["trade_date"] = pd.to_datetime(df2["trade_date"]).dt.strftime("%Y-%m-%d")
                df2["premium"] = pd.to_numeric(df2["premium"], errors="coerce")
                df2 = df2.dropna().groupby("trade_date")["premium"].mean().reset_index()
                df2 = df2[(df2["trade_date"] >= start) & (df2["trade_date"] <= end)]
                return df2
        return pd.DataFrame()
    except Exception as e:
        logger.error("fetch_ah_premium: %s", e)
        return pd.DataFrame()


# ── 统一初始化入口 ────────────────────────────────────────────────────────────

# -------------------------------------------------------------------
# akshare: M2 月度货币供应量
# -------------------------------------------------------------------

def fetch_m2_history(start: str = "2008-01-01", end: str = None):
    try:
        import akshare as ak
        df = ak.macro_china_money_supply()
        if df is None or df.empty:
            return pd.DataFrame()
        col_month = df.columns[0]
        col_m2 = df.columns[1]   # M2总量(亿元)
        col_yoy = df.columns[2]  # M2同比增速
        result = df[[col_month, col_m2, col_yoy]].copy()
        result.columns = ["month", "m2_billion", "m2_yoy"]
        # "2026年04月份" -> "2026-04"
        result["month"] = result["month"].str.replace("年", "-").str.replace("月份", "").str.strip()
        result["m2_billion"] = pd.to_numeric(result["m2_billion"], errors="coerce")
        result["m2_yoy"] = pd.to_numeric(result["m2_yoy"], errors="coerce")
        result = result.dropna(subset=["month", "m2_billion"])
        if start:
            result = result[result["month"] >= start[:7]]
        if end:
            result = result[result["month"] <= end[:7]]
        logger.info("fetch_m2_history: %d rows", len(result))
        return result
    except Exception as e:
        logger.error("fetch_m2_history: %s", e)
        return pd.DataFrame()


# -------------------------------------------------------------------
# A股总市值: rebuild from stock_daily total_mv
# -------------------------------------------------------------------

def rebuild_market_cap(db_path: str = None):
    try:
        from src.data.database import DB_PATH as _DB
        conn = sqlite3.connect(db_path or _DB)
        rows = conn.execute("""
            SELECT trade_date,
                   SUM(total_mv) as total_mv,
                   COUNT(DISTINCT stock_code) as stock_count
            FROM stock_daily
            WHERE total_mv IS NOT NULL AND total_mv > 0
            GROUP BY trade_date ORDER BY trade_date
        """).fetchall()
        conn.executemany(
            "INSERT OR REPLACE INTO stock_market_cap VALUES(?, ?, ?)", rows)
        conn.commit()
        conn.close()
        logger.info("rebuild_market_cap: %d rows", len(rows))
    except Exception as e:
        logger.error("rebuild_market_cap: %s", e)


# -------------------------------------------------------------------
# tushare daily_basic: 全市场 PE/PB/市值 → stock_daily
# -------------------------------------------------------------------

def fetch_daily_basic_to_stock_daily(trade_date: str, db_path: str = None) -> int:
    """
    拉取 tushare daily_basic (全市场 PE/PB/市值) + daily (涨跌幅)
    写入 stock_daily 表: peTTM, pbMRQ, total_mv, circ_mv, pct_change, close, turnover_rate
    已有 baostock K线的股票仅更新 PE/PB/市值字段，不覆盖 OHLCV。
    返回写入行数。
    """
    from src.data.database import DB_PATH as _DB
    if not TUSHARE_TOKEN:
        logger.warning("TUSHARE_TOKEN not set, skipping daily_basic")
        return 0

    _db = db_path or _DB
    conn = sqlite3.connect(_db)

    # 检查该日期是否已有 tushare PE/PB 数据
    existing = conn.execute(
        "SELECT COUNT(*) FROM stock_daily WHERE trade_date=? AND total_mv IS NOT NULL AND total_mv > 0",
        (trade_date,)
    ).fetchone()[0]
    if existing > 100:
        logger.info("daily_basic %s: already has %d rows with total_mv, skipping", trade_date, existing)
        conn.close()
        return 0

    ds = trade_date.replace("-", "")

    # 1. daily_basic: PE/PB/市值/换手率
    try:
        import tushare as ts
        pro = ts.pro_api(TUSHARE_TOKEN)
        df_basic = pro.daily_basic(trade_date=ds,
            fields='ts_code,close,turnover_rate,pe_ttm,pb,total_mv,circ_mv')
        time.sleep(0.35)
    except Exception as e:
        logger.error("daily_basic fetch failed for %s: %s", trade_date, str(e)[:80])
        conn.close()
        return 0

    if df_basic is None or df_basic.empty:
        logger.info("daily_basic %s: no data", trade_date)
        conn.close()
        return 0

    # 2. daily: 涨跌幅 (pct_chg)
    pct_map = {}
    try:
        df_daily = pro.daily(trade_date=ds, fields='ts_code,pct_chg')
        time.sleep(0.35)
        if df_daily is not None and not df_daily.empty:
            pct_map = dict(zip(df_daily['ts_code'], df_daily['pct_chg']))
    except Exception as e:
        logger.warning("daily pct_chg fetch failed for %s: %s", trade_date, str(e)[:60])

    # 转换 ts_code → baostock 格式
    def _ts_to_bs(ts_code):
        if ts_code.endswith(".SH"):
            return "sh" + ts_code.replace(".SH", "")
        elif ts_code.endswith(".SZ"):
            return "sz" + ts_code.replace(".SZ", "")
        return None

    rows = []
    for _, row in df_basic.iterrows():
        code = _ts_to_bs(row.get("ts_code", ""))
        if not code:
            continue
        pe = row.get("pe_ttm")
        pb = row.get("pb")
        tmv = row.get("total_mv")
        cmv = row.get("circ_mv")
        close = row.get("close")
        tr = row.get("turnover_rate")
        pct = pct_map.get(row.get("ts_code"))

        def _f(v):
            if v is None or (isinstance(v, float) and (pd.isna(v) or np.isinf(v))):
                return None
            return float(v)

        rows.append((
            _f(pe), _f(pb), _f(tmv), _f(cmv), _f(pct), _f(close), _f(tr),
            trade_date, code
        ))

    if not rows:
        conn.close()
        return 0

    conn.executemany("""
        INSERT INTO stock_daily (peTTM, pbMRQ, total_mv, circ_mv, pct_change, close, turnover_rate, trade_date, stock_code)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(trade_date, stock_code) DO UPDATE SET
            peTTM = COALESCE(excluded.peTTM, stock_daily.peTTM),
            pbMRQ = COALESCE(excluded.pbMRQ, stock_daily.pbMRQ),
            total_mv = COALESCE(excluded.total_mv, stock_daily.total_mv),
            circ_mv = COALESCE(excluded.circ_mv, stock_daily.circ_mv),
            pct_change = COALESCE(excluded.pct_change, stock_daily.pct_change),
            close = COALESCE(excluded.close, stock_daily.close),
            turnover_rate = COALESCE(excluded.turnover_rate, stock_daily.turnover_rate)
    """, rows)
    conn.commit()

    written = len(rows)
    logger.info("daily_basic %s: wrote %d stocks (PE/PB/mv/pct/tr)", trade_date, written)
    conn.close()
    return written


def backfill_full_market_pe(start: str = "2015-01-01", end: str = None,
                            db_path: str = None) -> dict:
    """
    批量回填全市场 PE/PB 到 stock_daily (通过 tushare daily_basic)
    用于一次性初始化或补全历史数据。
    返回 {"dates": N, "written": N, "errors": N}
    """
    from src.data.database import DB_PATH as _DB
    _db = db_path or _DB
    end = end or date.today().strftime("%Y-%m-%d")

    # 获取日期范围内所有交易日 (从 index_daily 表)
    conn = sqlite3.connect(_db)
    trade_dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM index_daily WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
        (start, end)
    ).fetchall()]
    conn.close()
    if not trade_dates:
        logger.warning("No trade dates found for %s ~ %s", start, end)
        return {"dates": 0, "written": 0, "errors": 0}

    logger.info("backfill_full_market_pe: %d trade dates (%s ~ %s)", len(trade_dates), start, end)

    total_written = 0
    errors = 0
    conn = sqlite3.connect(_db)
    for i, td in enumerate(trade_dates):
        try:
            # 清除该日期的 total_mv 标记, 强制重新拉取
            conn.execute("UPDATE stock_daily SET total_mv=NULL WHERE trade_date=?", (td,))
            conn.commit()
            w = fetch_daily_basic_to_stock_daily(td, db_path=_db)
            total_written += w
        except Exception as e:
            errors += 1
            if errors <= 10:
                logger.error("backfill %s: %s", td, str(e)[:80])
            time.sleep(1)

        if (i + 1) % 100 == 0:
            logger.info("backfill progress: %d/%d (written: %d, errors: %d)",
                        i + 1, len(trade_dates), total_written, errors)
    conn.close()

    logger.info("backfill_full_market_pe DONE: %d dates, %d written, %d errors",
                len(trade_dates), total_written, errors)
    return {"dates": len(trade_dates), "written": total_written, "errors": errors}


def fetch_all_history(start: str = "2015-01-01", end: str = None):
    """
    一次性拉取所有历史数据 (初始化用)
    步骤:
      1. baostock 登录
      2. 指数日行情 (baostock)
      3. 成分股列表 (baostock)
      4. 个股历史K线 (baostock, 最耗时~850只×11年)
      5. 行业分类 (baostock)
      6. 融资融券 + 北向 + 国债 (tushare, 注意频率限制)
      7. 指数PE/PB (tushare)
      8. AH溢价 (akshare)
      9. baostock 登出
    """
    end = end or date.today().strftime("%Y-%m-%d")
    logger.info("=" * 60)
    logger.info("FULL HISTORY INIT: %s → %s", start, end)
    logger.info("=" * 60)

    bs_login()
    try:
        # 1. 指数日行情
        logger.info("Step 1/6: Index daily (baostock)...")
        fetch_all_index_history(start, end)

        # 2+3. 成分股列表 → 个股K线
        logger.info("Step 2/6: Constituent stocks (baostock)...")
        all_codes = set()
        for idx_name in ["hs300", "sz50", "zz500"]:
            df = fetch_index_constituents(idx_name)
            if not df.empty:
                all_codes.update(df["code"].tolist())
        logger.info("Total unique stocks: %d", len(all_codes))

        logger.info("Step 3/6: Stock klines (baostock, this may take a while)...")
        fetch_stocks_kline_batch(list(all_codes), start, end, label="history")

        # 4. 行业分类
        logger.info("Step 4/6: Industry classification (baostock)...")
        df_ind = fetch_stock_industry()
        save_dataframe(df_ind, "stock_industry")

        # 5. tushare (融资融券/北向/国债)
        logger.info("Step 5/6: Tushare (margin/northbound/bond/index_pe)...")
        _save(fetch_margin_history(start, end), "margin_history")
        _save(fetch_northbound_history(start, end), "northbound_history")
        _save(fetch_bond_yield_history(start, end), "bond_yield")
        _save(fetch_index_pe_history(start, end), "index_pe_history")

        # 6/7. akshare AH溢价
        logger.info("Step 6/7: AH premium (akshare)...")
        _save(fetch_ah_premium(start, end), "ah_premium")

        # 7/7. M2 + rebuild market cap
        logger.info("Step 7/7: M2 + market cap rebuild...")
        _save(fetch_m2_history("2008-01-01", end), "m2_monthly")
        rebuild_market_cap()

    finally:
        bs_logout()

    logger.info("History init complete!")
