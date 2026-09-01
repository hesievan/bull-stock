"""
数据获取模块 — tushare + akshare (无 baostock 依赖)

数据源分工:
  tushare(2000积分): 全市场日K线、PE/PB/市值、融资融券、北向资金、指数PE/PB、成分股、行业分类
  akshare:           M2月度数据、国债收益率、AH股溢价
"""

from __future__ import annotations

import logging
import time
import os
from datetime import date
from typing import Any, Callable

import pandas as pd
import numpy as np

from src.data.database import get_conn

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────────

TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")
TUSHARE_TIMEOUT = 30
TUSHARE_RETRIES = 2

INDEX_CODE_MAP = {
    "sh000001": "000001.SH",
    "sz399001": "399001.SZ",
    "sz399006": "399006.SZ",
    "sh000300": "000300.SH",
    "sh000905": "000905.SH",
    "sh000852": "000852.SH",
    "sh000688": "000688.SH",
    "bj899050": "899050.BJ",
    "sh000510": "000510.SH",
    "sh000922": "000922.SH",
}
INDEX_NAMES = {
    "sh000001": "上证综指",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000300": "沪深300",
    "sh000905": "中证500",
    "sh000852": "中证1000",
    "sh000688": "科创50",
    "bj899050": "北证50",
    "sh000510": "中证A500",
    "sh000922": "中证红利",
}


def _ts_sleep() -> None:
    now = time.time()
    wait = 0.15 - (now - getattr(_ts_sleep, "_last", 0))
    if wait > 0:
        time.sleep(wait)
    _ts_sleep._last = time.time()


def _retry(fn: Callable, max_retries: int = 3, base_delay: int = 1) -> Any:
    """指数退避重试装饰器。连续失败 max_retries 次后抛出异常。"""
    last_exc = None
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if attempt < max_retries - 1:
                delay = base_delay * (2**attempt)
                logger.warning("Retry %d/%d after %.1fs: %s", attempt + 1, max_retries, delay, str(e)[:80])
                time.sleep(delay)
    raise last_exc


def _save(df: pd.DataFrame, table: str) -> None:
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


def _get_pro() -> Any:
    import tushare as ts

    return ts.pro_api(TUSHARE_TOKEN)


# ── tushare: 指数日行情 ──────────────────────────────────────────────────────


def fetch_index_daily(ak_code: str, start: str, end: str) -> pd.DataFrame:
    ts_code = INDEX_CODE_MAP.get(ak_code)
    if not ts_code:
        return pd.DataFrame()
    try:
        pro = _get_pro()
        df = pro.index_daily(ts_code=ts_code, start_date=start.replace("-", ""), end_date=end.replace("-", ""))
        _ts_sleep()
        if df is not None and not df.empty:
            df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
            df["index_code"] = ak_code
            df.rename(columns={"pct_chg": "pct_change", "vol": "volume"}, inplace=True)
            expected_cols = [
                "trade_date",
                "index_code",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "pct_change",
            ]
            for col in expected_cols:
                if col not in df.columns and col not in ("trade_date", "index_code"):
                    df[col] = None
                elif col in df.columns and col not in ("trade_date", "index_code"):
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return df[expected_cols]
    except Exception as e:
        logger.warning("fetch_index_daily tushare(%s) failed: %s", ak_code, str(e)[:80])

    try:
        import akshare as ak

        logger.info("fetch_index_daily(%s): falling back to akshare stock_zh_index_daily_tx", ak_code)
        df = ak.stock_zh_index_daily_tx(symbol=ak_code)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.rename(columns={"date": "trade_date"})
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
        df = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)].copy()
        if df.empty:
            return pd.DataFrame()
        df = df.sort_values("trade_date").reset_index(drop=True)
        df["index_code"] = ak_code
        df["pct_change"] = (df["close"] / df["close"].shift(1) - 1) * 100
        df["volume"] = None
        expected_cols = ["trade_date", "index_code", "open", "high", "low", "close", "volume", "amount", "pct_change"]
        for col in expected_cols:
            if col not in df.columns:
                df[col] = None
            elif col not in ("trade_date", "index_code"):
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df[expected_cols]
    except Exception as e:
        logger.error("fetch_index_daily akshare(%s) failed: %s", ak_code, str(e)[:80])
        return pd.DataFrame()


def fetch_all_index_incremental(db_path: str | None = None) -> None:
    from src.data.database import DB_PATH as _DB

    _db = db_path or _DB
    for ak_code in INDEX_CODE_MAP:
        with get_conn(_db) as conn:
            latest = conn.execute("SELECT MAX(trade_date) FROM index_daily WHERE index_code=?", (ak_code,)).fetchone()[
                0
            ]
        start = latest or "2015-01-01"
        end = date.today().strftime("%Y-%m-%d")
        df = fetch_index_daily(ak_code, start, end)
        if not df.empty:
            _save(df, "index_daily")
    return True


# ── tushare: 融资融券 ──────────────────────────────────────────────────────


def fetch_margin_history(start: str, end: str) -> pd.DataFrame:
    """拉取融资融券历史数据 — 沪深北三市合并

    ISSUE-9 修复: 原只拉 sse, 现改为拉取全部 exchange=""(沪深北合并),
    再按 trade_date 去重聚合。tushare margin 接口 exchange 为空时返回三市合并数据。
    """
    try:
        pro = _get_pro()
        dfs = []
        start_m = pd.Timestamp(start).replace(day=1)
        for dt in pd.date_range(start_m, end, freq="MS"):
            ds = dt.strftime("%Y%m%d")
            try:
                # exchange="" 返回沪深北三市合并日汇总
                df = pro.margin(start_date=ds, end_date=(dt + pd.offsets.MonthEnd(0)).strftime("%Y%m%d"))
                _ts_sleep()
                if df is not None and not df.empty:
                    dfs.append(df)
            except Exception as e:
                logger.warning("fetch_margin_history month %s failed: %s", ds, str(e)[:80])
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
            except Exception as e:
                logger.warning("fetch_northbound_history month %s failed: %s", ds, str(e)[:80])
        if not dfs:
            return pd.DataFrame()
        result = pd.concat(dfs, ignore_index=True)
        result["trade_date"] = pd.to_datetime(result["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
        # 只保留 northbound_history 表已有的列
        # BUG-3 修复: tushare moneyflow_hsgt 返回列名为 north_money(北向净流入), 非 north_net
        keep = {"trade_date", "hgt", "sgt", "north_money", "south_money"}
        cols = [c for c in result.columns if c in keep]
        result = result[cols]
        # BUG-3 修复: tushare 返回列名 north_money 映射到 DB 列 north_net
        if "north_money" in result.columns:
            result = result.rename(columns={"north_money": "north_net"})
        # 单位断裂修复: tushare moneyflow_hsgt 自 2024-08-19(沪深港通暂停披露北向净买额)起
        # north_money/hgt/sgt/south_money 量级突增约 127 倍(无实际意义), 与历史(百万元)单位不一致。
        # 统一回缩到历史单位, 否则 north_ratio 的百分位会失真(近期恒为满分)。
        # 断裂窗口: 2024-08-19~08-30 原始值暴涨(8万~17万), 2024-09 起 tushare 持续以新量级返回,
        # 故截止点取断裂起点 2024-08-19(该日及之后拉取的全部原始值均为膨胀量级, 需回缩)。
        # 因子=post中位数/pre中位数。旧库已对 >=2024-09-01 做过同样修复, 此处起点前移以覆盖 8 月缺口。
        _NORTH_UNIT_BREAK = "2024-08-19"
        _NORTH_UNIT_FACTOR = 126.66
        flow_cols = ["north_net", "hgt", "sgt", "south_money"]
        mask = result["trade_date"] >= _NORTH_UNIT_BREAK
        for col in flow_cols:
            if col in result.columns:
                result.loc[mask, col] = pd.to_numeric(result.loc[mask, col], errors="coerce") / _NORTH_UNIT_FACTOR
        return result
    except Exception as e:
        logger.error("fetch_northbound_history failed: %s", str(e)[:80])
        return pd.DataFrame()


# ── tushare: 国债收益率 ──────────────────────────────────────────────────────


def _fetch_bond_yield_akshare() -> pd.DataFrame:
    """从 akshare 获取国债收益率曲线 (2年 + 10年) — 用于期限利差 yield_spread = 10Y - 2Y

    注: 原方案想用 10Y-1Y, 但 akshare 的 1Y 国债历史极短(仅 2020-2021 一年),
    故改用 2s10s 利差 (10Y-2Y), 两者均来自 bond_zh_us_rate 且覆盖 2010-01-04~今, 完整。
    """
    try:
        import akshare as ak
    except ImportError:
        return pd.DataFrame()
    df = ak.bond_zh_us_rate(start_date="20100101")
    if df is None or df.empty:
        return pd.DataFrame()
    df["trade_date"] = pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d")
    recs = []
    for term, col in [(2.0, "中国国债收益率2年"), (10.0, "中国国债收益率10年")]:
        sub = df[["trade_date", col]].copy().rename(columns={col: "yield_rate"})
        sub["curve_term"] = term
        sub["yield_rate"] = pd.to_numeric(sub["yield_rate"], errors="coerce")
        sub = sub.dropna(subset=["yield_rate"])
        recs.append(sub)
    if not recs:
        return pd.DataFrame()
    out = pd.concat(recs, ignore_index=True)
    return out[["trade_date", "curve_term", "yield_rate"]]


def fetch_bond_yield_history(start: str, end: str) -> pd.DataFrame:
    """国债收益率 — 直接使用 akshare (tushare yc_cb 无权限)

    注: start/end 为接口兼容签名；akshare 接口返回全量历史，
    调用方按需自行按区间 slice（本函数不按区间过滤）。
    """
    return _fetch_bond_yield_akshare()


# ── 南向通净买额 (P1.2, akshare 东方财富) ──────────────────────────────────


def fetch_southbound_history(start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """南向通当日净买额 → daily_hsgt_south (单位: 亿元)

    akshare stock_hsgt_hist_em(symbol='南向资金') 返回全历史 (2014-11-17~今),
    南向 2024-08 港交所停止披露北向后仍正常披露。全量抓取 + upsert, 无需增量逻辑。
    """
    from src.data.database import save_dataframe as _sv

    try:
        import akshare as ak
    except ImportError:
        logger.error("akshare not installed, cannot fetch southbound")
        return pd.DataFrame()
    try:
        df = ak.stock_hsgt_hist_em(symbol="南向资金")
    except Exception as e:
        logger.error("fetch_southbound_history failed: %s", str(e)[:80])
        return pd.DataFrame()
    if df is None or df.empty or "日期" not in df.columns:
        logger.warning("fetch_southbound_history: empty/invalid data")
        return pd.DataFrame()
    out = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d"),
            "south_net": pd.to_numeric(df["当日成交净买额"], errors="coerce"),
        }
    ).dropna(subset=["south_net"])
    if start:
        out = out[out["trade_date"] >= start]
    if end:
        out = out[out["trade_date"] <= end]
    if out.empty:
        return pd.DataFrame()
    _sv(out, "daily_hsgt_south")
    logger.info("southbound saved: %d rows (%s ~ %s)", len(out), out["trade_date"].min(), out["trade_date"].max())
    return out


# ── 股指期货基差 (P1.3, akshare 新浪 IF0 + 本库沪深300现货) ─────────────────


def fetch_futures_basis_history(start: str | None = None, end: str | None = None, db_path: str = None) -> pd.DataFrame:
    """IF 主力连续 vs 沪深300 现货基差率 → daily_futures_basis

    期货: akshare futures_main_sina(symbol='IF0') 全历史 (2017-01~今)。
    现货: 库内 index_daily(index_code='sh000300'), 由 tushare/akshare 日常步骤维护。
    basis_rate = (期货收盘 - 现货收盘) / 现货收盘; 正=升水(乐观) 负=贴水(对冲/谨慎)。
    换月日主力跳变带来的 ±0.1% 级噪声在 10 年百分位下可接受。
    """
    from src.data.database import save_dataframe as _sv, get_conn, DB_PATH as _DB

    try:
        import akshare as ak
    except ImportError:
        logger.error("akshare not installed, cannot fetch futures basis")
        return pd.DataFrame()
    try:
        fut = ak.futures_main_sina(symbol="IF0")
    except Exception as e:
        logger.error("fetch_futures_basis_history (futures) failed: %s", str(e)[:80])
        return pd.DataFrame()
    if fut is None or fut.empty or "日期" not in fut.columns:
        logger.warning("fetch_futures_basis_history: futures empty/invalid")
        return pd.DataFrame()
    fut["trade_date"] = pd.to_datetime(fut["日期"]).dt.strftime("%Y-%m-%d")
    fut_close = pd.to_numeric(fut["收盘价"], errors="coerce")
    fut = pd.DataFrame({"trade_date": fut["trade_date"], "fut_close": fut_close}).dropna()

    with get_conn(db_path or _DB) as conn:
        spot_rows = conn.execute(
            "SELECT trade_date, close FROM index_daily WHERE index_code='sh000300' ORDER BY trade_date"
        ).fetchall()
    spot = pd.DataFrame(spot_rows, columns=["trade_date", "spot_close"])
    if spot.empty:
        logger.warning("fetch_futures_basis_history: no sh000300 in index_daily")
        return pd.DataFrame()
    spot["trade_date"] = spot["trade_date"].astype(str)
    spot["spot_close"] = pd.to_numeric(spot["spot_close"], errors="coerce")

    merged = fut.merge(spot, on="trade_date", how="inner")
    merged = merged[(merged["fut_close"] > 0) & (merged["spot_close"] > 0)]
    if merged.empty:
        logger.warning("fetch_futures_basis_history: no overlapping dates")
        return pd.DataFrame()
    merged["basis_rate"] = (merged["fut_close"] - merged["spot_close"]) / merged["spot_close"]
    out = merged[["trade_date", "fut_close", "spot_close", "basis_rate"]].round(6)
    if start:
        out = out[out["trade_date"] >= start]
    if end:
        out = out[out["trade_date"] <= end]
    if out.empty:
        return pd.DataFrame()
    _sv(out, "daily_futures_basis")
    logger.info("futures basis saved: %d rows (%s ~ %s)", len(out), out["trade_date"].min(), out["trade_date"].max())
    return out


# ── 新增投资者开户数 (P3, 月频, akshare 中国结算) ───────────────────────────

# 宽基 ETF 跟踪清单 (P3 份额快照统计范围: 沪深主要宽基 + 双创)
BROAD_ETF_CODES = [
    "510050", "510300", "510500", "510880", "563300",  # 上证50/沪深300/中证500/红利/A500
    "159901", "159915", "159922", "159949", "159952",  # 深100/创业板/中证500/创业板50/创投
    "512100", "512500", "512880", "512400",            # 中证1000/中证500/证券/有色金属
    "588000", "588080",                                # 科创50/科创50(华安)
]


def fetch_account_statistics(db_path: str = None) -> pd.DataFrame:
    """月度新增投资者开户数 → monthly_accounts (单位: 万户)

    P3 (2026-09): 散户 FOMO 低频锚, 仅展示不入分。
    已知局限: 东财/中国结算源自 2023-08 停止更新, 采集到的最新月份以此为准。
    """
    from src.data.database import save_dataframe as _sv

    try:
        import akshare as ak
    except ImportError:
        logger.error("akshare not installed, cannot fetch account statistics")
        return pd.DataFrame()
    try:
        df = ak.stock_account_statistics_em()
    except Exception as e:
        logger.error("fetch_account_statistics failed: %s", str(e)[:80])
        return pd.DataFrame()
    if df is None or df.empty or "数据日期" not in df.columns:
        logger.warning("fetch_account_statistics: empty/invalid data")
        return pd.DataFrame()
    out = pd.DataFrame(
        {
            "month": df["数据日期"].astype(str).str[:7],
            "new_accounts": pd.to_numeric(df["新增投资者-数量"], errors="coerce"),
        }
    ).dropna(subset=["new_accounts"])
    if out.empty:
        return pd.DataFrame()
    _sv(out, "monthly_accounts")
    logger.info("monthly_accounts saved: %d rows (%s ~ %s)", len(out), out["month"].min(), out["month"].max())
    return out


def fetch_etf_flow_snapshot(trade_date: str | None = None, db_path: str = None) -> pd.DataFrame:
    """宽基 ETF 总份额日度快照 → daily_etf_flow (单位: 亿份)

    P3 (2026-09): 份额历史无法免费回填, 自本日起每日快照积累;
    份额变动×价格≈净申赎, 待历史 ≥180 日后再评估是否入分。
    份额 = 总市值 / 最新价, 按只加总。
    """
    from src.data.database import save_dataframe as _sv

    try:
        import akshare as ak
    except ImportError:
        logger.error("akshare not installed, cannot fetch etf flow")
        return pd.DataFrame()
    try:
        df = ak.fund_etf_spot_em()
    except Exception as e:
        logger.error("fetch_etf_flow_snapshot failed: %s", str(e)[:80])
        return pd.DataFrame()
    if df is None or df.empty or "代码" not in df.columns:
        logger.warning("fetch_etf_flow_snapshot: empty/invalid data")
        return pd.DataFrame()
    sub = df[df["代码"].astype(str).str.zfill(6).isin(BROAD_ETF_CODES)].copy()
    if sub.empty:
        logger.warning("fetch_etf_flow_snapshot: no broad ETF matched")
        return pd.DataFrame()
    price = pd.to_numeric(sub["最新价"], errors="coerce")
    mv = pd.to_numeric(sub.get("总市值"), errors="coerce")
    shares = (mv / price).dropna()  # 总市值(元)/最新价(元) = 份额(份)
    if shares.empty:
        return pd.DataFrame()
    total_shares = round(float(shares.sum()) / 1e8, 4)  # 份→亿份
    out = pd.DataFrame(
        [
            {
                "trade_date": trade_date or date.today().strftime("%Y-%m-%d"),
                "total_shares": total_shares,
                "n_funds": int(len(shares)),
            }
        ]
    )
    _sv(out, "daily_etf_flow")
    logger.info("daily_etf_flow %s: %.2f 亿份 (%d funds)", out.iloc[0]["trade_date"], total_shares, len(shares))
    return out


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
            (trade_date,),
        ).fetchone()[0]
    if existing > 7000:  # ISSUE-12 修复: A股超5000只, 原 4000 过低会错误跳过
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
            lambda: pro.daily_basic(trade_date=ds, fields="ts_code,pe_ttm,pb,total_mv,circ_mv,turnover_rate"),
            max_retries=2,
            base_delay=2,
        )
        _ts_sleep()
    except Exception as e:
        logger.warning("daily_basic fetch failed for %s: %s", trade_date, str(e)[:60])
        df_basic = None

    if df_basic is not None and not df_basic.empty:
        merged = df_daily.merge(df_basic, on="ts_code", how="left")
    else:
        merged = df_daily
        for col in ["pe_ttm", "pb", "total_mv", "circ_mv", "turnover_rate"]:
            merged[col] = None

    def _f(v: Any) -> float | None:
        if v is None or (isinstance(v, float) and (pd.isna(v) or np.isinf(v))):
            return None
        return float(v)

    rows = []
    for _, row in merged.iterrows():
        code = ts_to_ak(row.get("ts_code", ""))
        if not code:
            continue
        rows.append(
            (
                _f(row.get("open")),
                _f(row.get("high")),
                _f(row.get("low")),
                _f(row.get("close")),
                _f(row.get("vol")),
                _f(row.get("amount")),
                _f(row.get("pct_chg")),
                _f(row.get("pe_ttm")),
                _f(row.get("pb")),
                _f(row.get("total_mv")),
                _f(row.get("circ_mv")),
                _f(row.get("turnover_rate")),
                trade_date,
                code,
            )
        )

    if not rows:
        return 0

    with get_conn(_db) as conn:
        conn.executemany(
            """
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
        """,
            rows,
        )
    written = len(rows)
    logger.info("daily_basic %s: wrote %d stocks", trade_date, written)
    return written


# ── M2月度数据 (tushare cn_m) ──────────────────────────────────────────────────


def fetch_m2_history(start: str = "2008-01-01", end: str | None = None) -> None:
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


# ── M1月度数据 (akshare macro_china_money_supply) ─────────────────────────────


def fetch_m1_history(start: str = "2008-01-01", end: str | None = None) -> None:
    """获取 M1 货币供应数据 (akshare macro_china_money_supply), 写入 m1_monthly 表

    用于资金维度新指标 m1_m2_spread = M1同比 - M2同比。
    M2同比仍由 fetch_m2_history (tushare cn_m) 写入 m2_monthly, 两表按月关联。
    """
    from src.data.database import save_dataframe as _sv

    try:
        import akshare as ak
    except ImportError:
        logger.error("akshare not installed, cannot fetch M1")
        return
    try:
        df = ak.macro_china_money_supply()
    except Exception as e:
        logger.error("fetch_m1_history (akshare) failed: %s", str(e)[:80])
        return
    if df is None or df.empty:
        logger.warning("macro_china_money_supply returned empty")
        return
    df["month"] = df["月份"].str.replace("年", "-").str.replace("月份", "")
    df = df.rename(
        columns={
            "货币(M1)-数量(亿元)": "m1_billion",
            "货币(M1)-同比增长": "m1_yoy",
        }
    )
    df["m1_billion"] = pd.to_numeric(df["m1_billion"], errors="coerce")
    df["m1_yoy"] = pd.to_numeric(df["m1_yoy"], errors="coerce")
    df = df.dropna(subset=["m1_yoy"])[["month", "m1_billion", "m1_yoy"]]
    _sv(df, "m1_monthly")
    logger.info("M1 data saved: %d rows from %s to %s", len(df), df["month"].min(), df["month"].max())


# ── 申万行业分类 ───────────────────────────────────────────────────────────────

SHENWAN_FOCUS_INDUSTRIES = [
    {"sw_code": "801780", "sw_name": "银行"},
    {"sw_code": "801790", "sw_name": "非银金融"},
    {"sw_code": "801120", "sw_name": "食品饮料"},
    {"sw_code": "801150", "sw_name": "医药生物"},
    {"sw_code": "801730", "sw_name": "电力设备"},
    {"sw_code": "801080", "sw_name": "电子"},
    {"sw_code": "801050", "sw_name": "有色金属"},
    {"sw_code": "801160", "sw_name": "公用事业"},
    {"sw_code": "801180", "sw_name": "房地产"},
    {"sw_code": "801950", "sw_name": "煤炭"},
]

# 申万二级细分行业 (重点行业热度的细分赛道) — parent 为所属一级代码
SHENWAN_L2_FOCUS = [
    {"sw_code": "801125", "sw_name": "白酒", "parent": "801120"},
    {"sw_code": "801194", "sw_name": "保险", "parent": "801790"},
]


def _normalize_stock_code(raw_code: str) -> str:
    """将 6 位纯数字代码转为 akshare 格式 (sh/sz/bj 前缀)
    BUG-4 修复: 处理 pandas 返回浮点数代码 (如 600000.0), 先转 int 再 zfill
    """
    try:
        code = str(int(float(str(raw_code)))).zfill(6)
    except (ValueError, TypeError):
        code = str(raw_code).replace(".", "").zfill(6)
    if code.startswith("6"):
        return "sh" + code
    elif code.startswith("8") or code.startswith("4"):
        return "bj" + code
    else:
        return "sz" + code


def fetch_shenwan_industry() -> pd.DataFrame:
    """从 akshare 拉取申万一级 + 二级细分行业成分股映射，保存到 stock_shenwan 表

    一级行业写入 sw_code/sw_name；二级细分行业 (SHENWAN_L2_FOCUS) 的成分股
    其 sw_code 仍记父一级，同时写 sw_l2_code/sw_l2_name，便于按二级赛道单独算热度。
    """
    from src.data.database import save_dataframe as _sv

    try:
        import akshare as ak
    except ImportError:
        logger.error("akshare not installed, cannot fetch Shenwan industry")
        return pd.DataFrame()

    today_str = date.today().strftime("%Y-%m-%d")
    rows = {}  # stock_code -> dict (按 stock_code 去重, 二级覆盖写入 l2 字段)

    def _fetch_constituents(sw_code: str) -> list[str]:
        try:
            df = ak.index_component_sw(symbol=sw_code)
        except Exception as e:
            logger.warning("fetch_shenwan_industry: index_component_sw %s failed: %s", sw_code, str(e)[:80])
            return []
        if df is None or df.empty:
            return []
        code_col = next((c for c in ["stock_code", "证券代码"] if c in df.columns), None)
        if code_col is None:
            logger.warning("fetch_shenwan_industry: no stock code column in %s", df.columns)
            return []
        return [str(c) for c in df[code_col].dropna().unique()]

    # 1. 一级行业
    for ind in SHENWAN_FOCUS_INDUSTRIES:
        sw_code = ind["sw_code"]
        sw_name = ind["sw_name"]
        codes = _fetch_constituents(sw_code)
        for c in codes:
            sc = _normalize_stock_code(c)
            rows[sc] = {
                "stock_code": sc,
                "sw_code": sw_code,
                "sw_name": sw_name,
                "sw_l2_code": None,
                "sw_l2_name": None,
                "update_date": today_str,
            }
        logger.info("fetch_shenwan_industry: %s(%s): %d stocks", sw_name, sw_code, len(codes))

    # 2. 二级细分行业 (成分股 sw_code 记父一级, 同时写 l2 字段)
    parent_name = {i["sw_code"]: i["sw_name"] for i in SHENWAN_FOCUS_INDUSTRIES}
    for ind in SHENWAN_L2_FOCUS:
        l2_code = ind["sw_code"]
        l2_name = ind["sw_name"]
        pcode = ind["parent"]
        codes = _fetch_constituents(l2_code)
        for c in codes:
            sc = _normalize_stock_code(c)
            if sc not in rows:
                # 兜底: 父一级未覆盖时(理论不发生)按父级写入
                rows[sc] = {
                    "stock_code": sc,
                    "sw_code": pcode,
                    "sw_name": parent_name.get(pcode),
                    "sw_l2_code": l2_code,
                    "sw_l2_name": l2_name,
                    "update_date": today_str,
                }
            else:
                rows[sc]["sw_l2_code"] = l2_code
                rows[sc]["sw_l2_name"] = l2_name
        logger.info("fetch_shenwan_industry: %s(%s, 父%s): %d stocks", l2_name, l2_code, pcode, len(codes))

    if not rows:
        logger.error("fetch_shenwan_industry: no records fetched")
        return pd.DataFrame()

    result = pd.DataFrame(list(rows.values()))
    _sv(result, "stock_shenwan")
    logger.info("fetch_shenwan_industry: saved %d records", len(result))
    return result


# ── 证监会行业分类 ────────────────────────────────────────────────────────────


def fetch_stock_industry(trade_date: str = None) -> pd.DataFrame:
    """BUG-2 修复: 从 tushare stock_basic 拉取全市场行业分类, 保存到 stock_industry 表

    列映射:
      ts_code(600000.SH) → code(sh600000, 匹配 stock_daily.stock_code)
      name               → code_name (股票名称)
      industry           → industry (行业名称, tushare返回的是申万一级行业名)
      list_date          → update_date
    """
    from src.data.database import save_dataframe as _sv

    if not TUSHARE_TOKEN:
        logger.warning("TUSHARE_TOKEN not set, skipping stock_industry fetch")
        return pd.DataFrame()

    try:
        pro = _get_pro()
    except Exception as e:
        logger.error("Cannot get tushare pro for stock_industry: %s", str(e)[:80])
        return pd.DataFrame()

    try:
        df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name,industry,list_date")
        _ts_sleep()
    except Exception as e:
        logger.error("fetch_stock_industry tushare failed: %s", str(e)[:80])
        return pd.DataFrame()

    if df is None or df.empty:
        logger.warning("fetch_stock_industry: stock_basic returned empty")
        return pd.DataFrame()

    # 转换代码格式: 600000.SH → sh600000
    records = []
    today_str = trade_date or date.today().strftime("%Y-%m-%d")
    for _, row in df.iterrows():
        code = ts_to_ak(str(row.get("ts_code", "")))
        if not code or not code[2:].isdigit():
            continue
        records.append(
            {
                "code": code,  # sh600000 格式, 匹配 stock_daily
                "code_name": str(row.get("name", "")),
                "industry": str(row.get("industry", "")),
                "industry_classification": str(row.get("industry", "")),
                "update_date": today_str,
            }
        )

    if not records:
        logger.error("fetch_stock_industry: no valid records after conversion")
        return pd.DataFrame()

    result = pd.DataFrame(records)
    _sv(result, "stock_industry")
    logger.info("fetch_stock_industry: saved %d records", len(result))
    return result


# ── 涨停封板率 (P0-1: 本地计算, 无需 tushare API) ────────────────────────────────


def _get_limit_factor(code: str) -> float:
    """根据股票代码返回涨跌停幅度。ST 股 (5%) 无法从代码识别, 不纳入。"""
    c = str(code).replace("sh", "").replace("sz", "").replace("bj", "")
    if c.startswith("300") or c.startswith("301") or c.startswith("688"):
        return 0.20
    if c.startswith("8") or c.startswith("4") or c.startswith("920"):
        return 0.30
    return 0.10


def fetch_limit_list(trade_date: str, db_path: str = None) -> bool:
    """从 stock_daily 本地计算涨停封板率, 写入 daily_seal_rate 表。

    封板率 = 收盘涨停数 / 盘中触板数
    - 触板: 最高价 >= 涨停价 (盘中触及涨停)
    - 涨停: 收盘价 >= 涨停价 (收盘封住涨停)
    - 涨停价 = round(pre_close * (1 + limit_factor), 2)

    limit_factor 基于 stock_code:
    - 300/301/688 (创业板/科创板): 20%
    - 8/4/920 (北交所): 30%
    - 其他 (主板): 10%

    注意: ST 股 (5%限制) 无法从代码识别, 不纳入计算。
    方法论对所有日期一致, V2 百分位排名不受绝对值偏差影响。

    Returns True if data saved successfully.
    """
    from src.data.database import get_conn, DB_PATH as _db_path
    import sqlite3

    conn = sqlite3.connect(db_path or _db_path)
    try:
        # 获取前一交易日
        prev_row = conn.execute(
            "SELECT MAX(trade_date) FROM stock_daily WHERE trade_date < ?",
            (trade_date,),
        ).fetchone()
        prev_date = prev_row[0] if prev_row else None
        if not prev_date:
            logger.warning("fetch_limit_list %s: no previous trade date found", trade_date)
            return False

        # JOIN 当日与前日数据
        df = pd.read_sql(
            "SELECT a.stock_code, a.high, a.close, b.close AS pre_close "
            "FROM stock_daily a "
            "INNER JOIN stock_daily b ON a.stock_code = b.stock_code AND b.trade_date = ? "
            "WHERE a.trade_date = ? AND a.amount > 0",
            conn,
            params=[prev_date, trade_date],
        )
    finally:
        conn.close()

    if df.empty:
        logger.warning("fetch_limit_list %s: no data after join", trade_date)
        return False

    # 计算涨停价
    df["limit_factor"] = df["stock_code"].apply(_get_limit_factor)
    df["up_limit"] = (df["pre_close"] * (1 + df["limit_factor"])).round(2)

    # 触板: 最高价 >= 涨停价 - 0.01 (容差)
    touched = df["high"] >= df["up_limit"] - 0.01
    # 涨停: 收盘价 == 涨停价 (容差 0.01)
    closed_limit = (df["close"] - df["up_limit"]).abs() <= 0.01

    touched_count = int(touched.sum())
    closed_count = int(closed_limit.sum())

    if touched_count == 0:
        logger.warning("fetch_limit_list %s: no limit touches", trade_date)
        return False

    seal_rate = round(closed_count / touched_count, 6)

    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO daily_seal_rate "
            "(trade_date, seal_rate, limit_up_count, sealed_count) VALUES (?, ?, ?, ?)",
            (trade_date, seal_rate, touched_count, closed_count),
        )
    logger.info(
        "daily_seal_rate %s: seal_rate=%.4f (%d 涨停 / %d 触板)",
        trade_date,
        seal_rate,
        closed_count,
        touched_count,
    )
    return True


# ── 指数估值 (P0-2: tushare index_dailybasic → index_pe_history) ────────────────

# 与 backfill_index_heat_history.py 的 INDEX_CODE_TO_TS 保持一致
_INDEX_PE_TARGETS = {
    "sh000300": "000300.SH",
    "sz399006": "399006.SZ",
    "sh000688": "000688.SH",
    "bj899050": "899050.BJ",
    "sh000510": "000510.SH",
    "sh000852": "000852.SH",
    "sh000922": "000922.SH",
}


def fetch_index_dailybasic(trade_date: str, db_path: str = None) -> bool:
    """抓取当日指数估值数据 (PE/PB/市值), 写入 index_pe_history 表。

    tushare index_dailybasic 一次调用返回当日全市场指数数据,
    筛选 7 个目标指数后映射为 ak_code 格式保存。

    Returns True if data saved successfully.
    """
    if not TUSHARE_TOKEN:
        logger.warning("TUSHARE_TOKEN not set, skipping index_dailybasic fetch")
        return False

    ds = trade_date.replace("-", "")
    try:
        pro = _get_pro()
    except Exception as e:
        logger.error("Cannot get tushare pro for index_dailybasic: %s", str(e)[:80])
        return False

    try:
        df = _retry(lambda: pro.index_dailybasic(trade_date=ds))
        _ts_sleep()
    except Exception as e:
        logger.error("fetch_index_dailybasic tushare failed: %s", str(e)[:80])
        return False

    if df is None or df.empty:
        logger.warning("index_dailybasic %s: no data returned", trade_date)
        return False

    # 反向映射: ts_code → ak_code
    ts_to_ak_map = {ts: ak for ak, ts in _INDEX_PE_TARGETS.items()}

    records = []
    for _, row in df.iterrows():
        ts_code = str(row.get("ts_code", ""))
        ak_code = ts_to_ak_map.get(ts_code)
        if not ak_code:
            continue
        # tushare total_mv 单位为万元, 转换为亿元
        total_mv = row.get("total_mv")
        if total_mv is not None and not (isinstance(total_mv, float) and np.isnan(total_mv)):
            total_mv = round(float(total_mv) / 10000, 4)
        records.append(
            {
                "trade_date": trade_date,
                "index_code": ak_code,
                "pe_ttm": _safe_float(row.get("pe_ttm_a") or row.get("pe")),
                "pb": _safe_float(row.get("pb")),
                "total_mv": total_mv,
            }
        )

    if not records:
        logger.warning("index_dailybasic %s: no target indices matched", trade_date)
        return False

    result = pd.DataFrame(records)
    _save(result, "index_pe_history")
    logger.info("index_pe_history %s: saved %d indices", trade_date, len(result))
    return True


def _safe_float(v: Any) -> float | None:
    """安全转 float, NaN/None → None"""
    if v is None:
        return None
    try:
        f = float(v)
        return None if np.isnan(f) else round(f, 6)
    except (ValueError, TypeError):
        return None
