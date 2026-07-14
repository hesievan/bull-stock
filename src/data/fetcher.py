"""
数据获取模块 — tushare + akshare (无 baostock 依赖)

数据源分工:
  tushare(2000积分): 全市场日K线、PE/PB/市值、融资融券、北向资金、指数PE/PB、成分股、行业分类
  akshare:           M2月度数据、国债收益率、AH股溢价
"""
import logging
import time
import os
from datetime import date

import pandas as pd
import numpy as np

from src.data.database import get_conn

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────────

TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")
TUSHARE_TIMEOUT = 30
TUSHARE_RETRIES = 2

INDEX_CODE_MAP = {
    "sh000001": "000001.SH", "sz399001": "399001.SZ", "sz399006": "399006.SZ",
    "sh000300": "000300.SH", "sh000905": "000905.SH", "sh000852": "000852.SH",
    "sh000688": "000688.SH", "bj899050": "899050.BJ", "sh000510": "000510.SH",
    "sh000922": "000922.SH",
}
INDEX_NAMES = {
    "sh000001": "上证综指", "sz399001": "深证成指", "sz399006": "创业板指",
    "sh000300": "沪深300", "sh000905": "中证500", "sh000852": "中证1000",
    "sh000688": "科创50", "bj899050": "北证50", "sh000510": "中证A500",
    "sh000922": "中证红利",
}


def _ts_sleep():
    now = time.time()
    wait = 0.15 - (now - getattr(_ts_sleep, '_last', 0))
    if wait > 0:
        time.sleep(wait)
    _ts_sleep._last = time.time()


def _retry(fn, max_retries=3, base_delay=1):
    """指数退避重试装饰器。连续失败 max_retries 次后抛出异常。"""
    last_exc = None
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning("Retry %d/%d after %.1fs: %s", attempt + 1, max_retries, delay, str(e)[:80])
                time.sleep(delay)
    raise last_exc


def _save(df: pd.DataFrame, table: str):
    from src.data.database import save_dataframe as _sv
    _sv(df, table)


def ak_to_ts(code: str) -> str:
    """sh600000 → 600000.SH"""
    code = code.replace("sh.", "sh").replace("sz.", "sz")
    if code.startswith("sh"):
        return code[2:] + ".SH"
    elif code.startswith("sz"):
        return code[2:] + ".SZ"
    return code


def ts_to_ak(ts_code: str) -> str:
    """600000.SH → sh600000"""
    if ts_code.endswith(".SH"):
        return "sh" + ts_code.replace(".SH", "")
    elif ts_code.endswith(".SZ"):
        return "sz" + ts_code.replace(".SZ", "")
    elif ts_code.endswith(".BJ"):
        return "bj" + ts_code.replace(".BJ", "")
    return ts_code


def _get_pro():
    import tushare as ts
    return ts.pro_api(TUSHARE_TOKEN)


# ── tushare: 指数日行情 ──────────────────────────────────────────────────────

def fetch_index_daily(ak_code: str, start: str, end: str) -> pd.DataFrame:
    ts_code = INDEX_CODE_MAP.get(ak_code)
    if not ts_code:
        return pd.DataFrame()
    try:
        pro = _get_pro()
        df = pro.index_daily(ts_code=ts_code,
                             start_date=start.replace("-", ""),
                             end_date=end.replace("-", ""))
        _ts_sleep()
        if df is None or df.empty:
            return pd.DataFrame()
        df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
        df["index_code"] = ak_code
        df.rename(columns={"pct_chg": "pct_change", "vol": "volume"}, inplace=True)
        expected_cols = ["trade_date", "index_code", "open", "high", "low", "close", "volume", "amount", "pct_change"]
        for col in expected_cols:
            if col not in df.columns and col not in ("trade_date", "index_code"):
                df[col] = None
            elif col in df.columns and col not in ("trade_date", "index_code"):
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df[expected_cols]
    except Exception as e:
        logger.error("fetch_index_daily(%s) failed: %s", ak_code, str(e)[:80])
        return pd.DataFrame()


def fetch_all_index_incremental(db_path=None):
    from src.data.database import DB_PATH as _DB
    _db = db_path or _DB
    for ak_code in INDEX_CODE_MAP:
        with get_conn(_db) as conn:
            latest = conn.execute(
                "SELECT MAX(trade_date) FROM index_daily WHERE index_code=?",
                (ak_code,)
            ).fetchone()[0]
        start = latest or "2015-01-01"
        end = date.today().strftime("%Y-%m-%d")
        df = fetch_index_daily(ak_code, start, end)
        if not df.empty:
            _save(df, "index_daily")
    return True


# ── tushare: 融资融券 ──────────────────────────────────────────────────────

def fetch_margin_history(start: str, end: str) -> pd.DataFrame:
    try:
        pro = _get_pro()
        dfs = []
        # 从 start 所在月初开始，以月为单位迭代
        start_m = pd.Timestamp(start).replace(day=1)
        for dt in pd.date_range(start_m, end, freq="MS"):
            ds = dt.strftime("%Y%m%d")
            try:
                df = pro.margin(exchange="sse", start_date=ds,
                                end_date=(dt + pd.offsets.MonthEnd(0)).strftime("%Y%m%d"))
                _ts_sleep()
                if df is not None and not df.empty:
                    dfs.append(df)
            except Exception:
                pass
        if not dfs:
            return pd.DataFrame()
        result = pd.concat(dfs, ignore_index=True)
        result["trade_date"] = pd.to_datetime(result["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
        # 只保留 margin_history 表已有的列 (tushare 可能新加 exchange_id 等列)
        keep = {"trade_date", "rzye", "rzmre", "rzche", "rqye", "rqmcl", "rzrqye"}
        cols = [c for c in result.columns if c in keep]
        result = result[cols]
        # tushare margin 接口按交易所返回多行(exchange_id), 按日期汇总
        agg = {c: "sum" for c in cols if c != "trade_date"}
        result = result.groupby("trade_date", as_index=False).agg(agg)
        return result
    except Exception as e:
        logger.error("fetch_margin_history failed: %s", str(e)[:80])
        return pd.DataFrame()


# ── tushare: 北向资金 ──────────────────────────────────────────────────────

def fetch_northbound_history(start: str, end: str) -> pd.DataFrame:
    try:
        pro = _get_pro()
        dfs = []
        start_m = pd.Timestamp(start).replace(day=1)
        for dt in pd.date_range(start_m, end, freq="MS"):
            ds = dt.strftime("%Y%m%d")
            end_ds = (dt + pd.offsets.MonthEnd(0)).strftime("%Y%m%d")
            try:
                df = pro.moneyflow_hsgt(start_date=ds, end_date=end_ds)
                _ts_sleep()
                if df is not None and not df.empty:
                    dfs.append(df)
            except Exception:
                pass
        if not dfs:
            return pd.DataFrame()
        result = pd.concat(dfs, ignore_index=True)
        result["trade_date"] = pd.to_datetime(result["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
        # 只保留 northbound_history 表已有的列
        keep = {"trade_date", "hgt", "sgt", "north_net", "south_money"}
        cols = [c for c in result.columns if c in keep]
        return result[cols]
    except Exception as e:
        logger.error("fetch_northbound_history failed: %s", str(e)[:80])
        return pd.DataFrame()


# ── tushare: 国债收益率 ──────────────────────────────────────────────────────

def _fetch_bond_yield_akshare() -> pd.DataFrame:
    """从 akshare 获取全部历史国债收益率数据"""
    try:
        import akshare as ak
    except ImportError:
        return pd.DataFrame()
    df = ak.bond_zh_us_rate(start_date="20100101")
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={
        "日期": "trade_date",
        "中国国债收益率10年": "yield_rate"
    })
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
    df["curve_term"] = 10.0
    df["yield_rate"] = pd.to_numeric(df["yield_rate"], errors="coerce")
    df = df.dropna(subset=["yield_rate"])
    return df[["trade_date", "curve_term", "yield_rate"]]


def fetch_bond_yield_history(start: str, end: str) -> pd.DataFrame:
    """国债收益率 — 直接使用 akshare (tushare yc_cb 无权限)"""
    return _fetch_bond_yield_akshare()


# ── tushare: 全市场 PE/PB/市值 + K线 ────────────────────────────────────────

def fetch_daily_basic_to_stock_daily(trade_date: str, db_path: str = None) -> int:
    """
    拉取 tushare daily(全市场K线) + daily_basic(PE/PB/市值)
    合并写入 stock_daily 表
    """
    from src.data.database import DB_PATH as _DB
    if not TUSHARE_TOKEN:
        logger.warning("TUSHARE_TOKEN not set, skipping")
        return 0

    _db = db_path or _DB
    with get_conn(_db) as conn:
        existing = conn.execute(
            "SELECT COUNT(*) FROM stock_daily WHERE trade_date=? AND total_mv IS NOT NULL AND total_mv > 0 AND amount IS NOT NULL AND amount > 0",
            (trade_date,)
        ).fetchone()[0]
    if existing > 4000:
        logger.info("daily_basic %s: already has %d rows with full data, skipping", trade_date, existing)
        return 0

    ds = trade_date.replace("-", "")
    pro = _get_pro()

    try:
        df_daily = _retry(lambda: pro.daily(trade_date=ds), max_retries=2, base_delay=2)
        _ts_sleep()
    except Exception as e:
        logger.error("daily fetch failed for %s: %s", trade_date, str(e)[:80])
        return 0

    if df_daily is None or df_daily.empty:
        logger.info("daily %s: no data", trade_date)
        return 0

    try:
        df_basic = _retry(
            lambda: pro.daily_basic(trade_date=ds,
                fields='ts_code,pe_ttm,pb,total_mv,circ_mv,turnover_rate'),
            max_retries=2, base_delay=2,
        )
        _ts_sleep()
    except Exception as e:
        logger.warning("daily_basic fetch failed for %s: %s", trade_date, str(e)[:60])
        df_basic = None

    if df_basic is not None and not df_basic.empty:
        merged = df_daily.merge(df_basic, on='ts_code', how='left')
    else:
        merged = df_daily
        for col in ['pe_ttm', 'pb', 'total_mv', 'circ_mv', 'turnover_rate']:
            merged[col] = None

    def _f(v):
        if v is None or (isinstance(v, float) and (pd.isna(v) or np.isinf(v))):
            return None
        return float(v)

    rows = []
    for _, row in merged.iterrows():
        code = ts_to_ak(row.get("ts_code", ""))
        if not code:
            continue
        rows.append((
            _f(row.get("open")), _f(row.get("high")), _f(row.get("low")),
            _f(row.get("close")), _f(row.get("vol")), _f(row.get("amount")),
            _f(row.get("pct_chg")), _f(row.get("pe_ttm")), _f(row.get("pb")),
            _f(row.get("total_mv")), _f(row.get("circ_mv")),
            _f(row.get("turnover_rate")), trade_date, code,
        ))

    if not rows:
        return 0

    with get_conn(_db) as conn:
        conn.executemany("""
            INSERT INTO stock_daily (open, high, low, close, volume, amount, pct_change,
                                     peTTM, pbMRQ, total_mv, circ_mv, turnover_rate,
                                     trade_date, stock_code)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, stock_code) DO UPDATE SET
                open = excluded.open, high = excluded.high, low = excluded.low,
                close = excluded.close, volume = excluded.volume, amount = excluded.amount,
                pct_change = excluded.pct_change,
                peTTM = COALESCE(excluded.peTTM, stock_daily.peTTM),
                pbMRQ = COALESCE(excluded.pbMRQ, stock_daily.pbMRQ),
                total_mv = COALESCE(excluded.total_mv, stock_daily.total_mv),
                circ_mv = COALESCE(excluded.circ_mv, stock_daily.circ_mv),
                turnover_rate = COALESCE(excluded.turnover_rate, stock_daily.turnover_rate)
        """, rows)
    written = len(rows)
    logger.info("daily_basic %s: wrote %d stocks", trade_date, written)
    return written


# ── M2月度数据 (tushare cn_m) ──────────────────────────────────────────────────

def fetch_m2_history(start: str = "2008-01-01", end: str = None):
    """获取M2货币供应数据 (tushare cn_m 接口)"""
    try:
        pro = _get_pro()
        start_m = start.replace("-", "")[:6] if start else "200801"
        end_m = end.replace("-", "")[:6] if end else date.today().strftime("%Y%m")
        df = pro.cn_m(start_m=start_m, end_m=end_m)
        _ts_sleep()
        if df is None or df.empty:
            logger.warning("cn_m returned empty data")
            return
        # 映射列名: month(YYYYMM) → month(YYYY-MM), 只保留 m2_monthly 表需要的列
        df["month"] = pd.to_datetime(df["month"], format="%Y%m").dt.strftime("%Y-%m")
        df = df[["month", "m2", "m2_yoy"]].rename(columns={"m2": "m2_billion"})
        _save(df, "m2_monthly")
        logger.info("M2 data saved: %d rows from %s to %s", len(df), df["month"].min(), df["month"].max())
    except Exception as e:
        logger.error("fetch_m2_history (tushare) failed: %s", str(e)[:80])
