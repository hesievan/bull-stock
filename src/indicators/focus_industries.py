"""
重点行业热度 — 申万一级行业（6大核心行业）
"""
import logging
from datetime import date, timedelta
import numpy as np
import pandas as pd

from src.data.database import get_conn, DB_PATH

logger = logging.getLogger(__name__)

FOCUS_SW_CODES = ["801780", "801790", "801120", "801150", "801730", "801080"]

# 申万指数代码 (带后缀用于 akshare)
SW_INDEX_CODES = {
    "801780": "801780.SI",
    "801790": "801790.SI",
    "801120": "801120.SI",
    "801150": "801150.SI",
    "801730": "801730.SI",
    "801080": "801080.SI",
}

SW_NAME_MAP = {
    "801780": "银行",
    "801790": "非银金融",
    "801120": "食品饮料",
    "801150": "医药生物",
    "801730": "电力设备",
    "801080": "电子",
}


def _sp_rank(series, value):
    """历史百分位 0-100"""
    if series.empty or pd.isna(value):
        return 50.0
    s = series.dropna()
    if len(s) < 10:
        return 50.0
    return float((s < value).sum()) / max(len(s), 1) * 100


def _get_hist_industry_data(conn, trade_date, sw_codes, lookback_days=365):
    """从 stock_daily + stock_shenwan 获取行业历史数据用于百分位计算"""
    start = (pd.Timestamp(trade_date) - pd.DateOffset(days=lookback_days)).strftime("%Y-%m-%d")

    placeholders = ",".join(["?"] * len(sw_codes))
    hist = pd.read_sql(
        f"""SELECT sd.trade_date, sd.stock_code, sd.close, sd.pct_change,
                   sd.peTTM, sd.pbMRQ, sd.turnover_rate, sd.total_mv,
                   ss.sw_code, ss.sw_name
            FROM stock_daily sd
            JOIN stock_shenwan ss ON sd.stock_code = ss.stock_code
            WHERE sd.trade_date >= ? AND sd.trade_date <= ?
              AND ss.sw_code IN ({placeholders})
              AND sd.close IS NOT NULL AND sd.close > 0""",
        conn, params=[start, trade_date] + sw_codes,
    )
    for col in ("close", "pct_change", "peTTM", "pbMRQ", "turnover_rate", "total_mv"):
        if col in hist.columns:
            hist[col] = pd.to_numeric(hist[col], errors="coerce")
    return hist


def _fetch_index_data(trade_date: str) -> dict:
    """从 akshare 拉取申万行业指数最新行情 + PE/PB

    Returns: {sw_code: {close, pct_change, pe, pe_ttm, pb, div_yield}}
    """
    try:
        import akshare as ak
    except ImportError:
        logger.warning("akshare not available for index data")
        return {}

    result = {}
    # 行情数据：拉取最近 3 日计算涨跌幅
    try:
        for sw_code in FOCUS_SW_CODES:
            df = ak.index_hist_sw(symbol=sw_code, period="day")
            if df is None or df.empty:
                continue
            df = df.sort_values("日期")
            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) >= 2 else latest
            close = float(latest["收盘"])
            prev_close = float(prev["收盘"])
            pct = round((close / prev_close - 1) * 100, 2) if prev_close > 0 else None
            result[sw_code] = {
                "index_close": close,
                "index_pct_change": pct,
                "index_pe": None,
                "index_pe_ttm": None,
                "index_pb": None,
                "index_div_yield": None,
            }
    except Exception as e:
        logger.warning("fetch index hist failed: %s", str(e)[:80])

    # PE/PB 数据
    try:
        info = ak.sw_index_first_info()
        if info is not None and not info.empty:
            for _, row in info.iterrows():
                idx_code = str(row.get("行业代码", "")).replace(".SI", "")
                if idx_code in result:
                    def _f(v):
                        try:
                            return round(float(v), 2) if pd.notna(v) and v != "--" else None
                        except (ValueError, TypeError):
                            return None
                    result[idx_code].update({
                        "index_pe": _f(row.get("静态市盈率")),
                        "index_pe_ttm": _f(row.get("TTM(滚动)市盈率")),
                        "index_pb": _f(row.get("市净率")),
                        "index_div_yield": _f(row.get("静态股息率")),
                    })
    except Exception as e:
        logger.warning("fetch index info failed: %s", str(e)[:80])

    return result


def compute_focus_industries(trade_date: str, db_path: str = None) -> list:
    """计算6个重点申万行业的当日热度数据

    Returns: list[dict] sorted by composite_score descending
    """
    db = db_path or DB_PATH
    with get_conn(db) as conn:
        # 1. 今日数据 (关联 stock_industry 取股票名称)
        today = pd.read_sql(
            """SELECT sd.stock_code, sd.close, sd.pct_change, sd.peTTM, sd.pbMRQ,
                      sd.turnover_rate, sd.total_mv, sd.amount,
                      si.code_name AS stock_name
               FROM stock_daily sd
               LEFT JOIN stock_industry si ON sd.stock_code = si.code
               WHERE sd.trade_date = ?""",
            conn, params=[trade_date],
        )
        if today.empty:
            latest = conn.execute("SELECT MAX(trade_date) as d FROM stock_daily").fetchone()
            if latest and latest["d"]:
                actual = latest["d"]
                logger.info("focus_industries: using latest date %s instead of %s", actual, trade_date)
                trade_date = actual
                today = pd.read_sql(
                    """SELECT stock_code, close, pct_change, peTTM, pbMRQ,
                              turnover_rate, total_mv, amount
                       FROM stock_daily WHERE trade_date = ?""",
                    conn, params=[trade_date],
                )
        if today.empty:
            logger.error("focus_industries: no stock_daily data for %s", trade_date)
            return []

        for col in ("close", "pct_change", "peTTM", "pbMRQ", "turnover_rate", "total_mv", "amount"):
            today[col] = pd.to_numeric(today[col], errors="coerce")

        # 2. 关联申万分类
        shenwan = pd.read_sql(
            "SELECT stock_code, sw_code, sw_name FROM stock_shenwan",
            conn,
        )
        merged = today.merge(shenwan, on="stock_code", how="inner")
        if merged.empty:
            logger.error("focus_industries: no stocks matched with shenwan classification")
            return []

        # 3. 获取历史数据用于百分位计算
        hist = _get_hist_industry_data(conn, trade_date, FOCUS_SW_CODES)

    # 4. 获取行业指数行情 + PE/PB
    index_data = _fetch_index_data(trade_date)

    # 5. 计算每个行业的指标
    results = []
    market_up_count = int((today["pct_change"].dropna() > 0).sum())
    market_total = max(len(today["pct_change"].dropna()), 1)
    market_avg_pct = float(today["pct_change"].dropna().mean() or 0)

    for sw_code in FOCUS_SW_CODES:
        sw_name = SW_NAME_MAP.get(sw_code, sw_code)
        members = merged[merged["sw_code"] == sw_code]
        if members.empty:
            logger.warning("focus_industries: %s has no members with data today", sw_name)
            continue

        n_stocks = len(members)

        # 涨跌统计
        pc = members["pct_change"].dropna()
        avg_pct = round(float(pc.mean()), 2) if len(pc) > 0 else None
        up_count = int((pc > 0).sum()) if len(pc) > 0 else 0
        down_count = int((pc < 0).sum()) if len(pc) > 0 else 0
        up_ratio = round(up_count / max(len(pc), 1) * 100, 1)

        # 对比市场
        vs_market = round(avg_pct - market_avg_pct, 2) if avg_pct is not None else None

        # 估值
        pe_vals = members["peTTM"].dropna()
        pe_vals = pe_vals[(pe_vals > 0) & (pe_vals <= 500)]
        pb_vals = members["pbMRQ"].dropna()
        pb_vals = pb_vals[(pb_vals > 0) & (pb_vals <= 10)]
        med_pe = round(float(pe_vals.median()), 2) if len(pe_vals) > 5 else None
        med_pb = round(float(pb_vals.median()), 2) if len(pb_vals) > 5 else None

        # 总市值
        mv = members["total_mv"].dropna()
        total_mv = round(float(mv.sum()), 2) if len(mv) > 0 else None
        avg_mv = round(float(mv.mean()), 2) if len(mv) > 0 else None

        # 成交额
        amt = members["amount"].dropna()
        total_amount = round(float(amt.sum()), 2) if len(amt) > 0 else None

        # Top 3 领涨
        sorted_up = members.sort_values("pct_change", ascending=False).head(3)
        top_gainers = []
        for _, r in sorted_up.iterrows():
            if pd.notna(r.get("pct_change")):
                name = r.get("stock_name", "")
                if isinstance(name, str) and name.lower() != "nan":
                    display_name = name
                else:
                    display_name = str(r["stock_code"])
                top_gainers.append({
                    "stock_code": str(r["stock_code"]),
                    "stock_name": display_name,
                    "pct_change": round(float(r["pct_change"]), 2),
                })

        # Top 3 领跌
        sorted_down = members.sort_values("pct_change", ascending=True).head(3)
        top_losers = []
        for _, r in sorted_down.iterrows():
            if pd.notna(r.get("pct_change")):
                name = r.get("stock_name", "")
                if isinstance(name, str) and name.lower() != "nan":
                    display_name = name
                else:
                    display_name = str(r["stock_code"])
                top_losers.append({
                    "stock_code": str(r["stock_code"]),
                    "stock_name": display_name,
                    "pct_change": round(float(r["pct_change"]), 2),
                })

        # 热度评分（估值百分位 + 情绪百分位）
        hist_ind = hist[hist["sw_code"] == sw_code] if not hist.empty else pd.DataFrame()
        dim_scores = []

        # 估值分: PE 百分位
        if med_pe is not None and not hist_ind.empty:
            hist_pe = hist_ind.groupby("trade_date")["peTTM"].median().dropna()
            pe_score = _sp_rank(hist_pe, med_pe)
            dim_scores.append(pe_score)
        else:
            pe_score = None

        # 情绪分: 换手率百分位
        tr_mean = float(members["turnover_rate"].dropna().mean()) if len(members["turnover_rate"].dropna()) > 0 else None
        if tr_mean is not None and tr_mean > 0 and not hist_ind.empty:
            hist_tr = hist_ind.groupby("trade_date")["turnover_rate"].mean().dropna()
            tr_score = _sp_rank(hist_tr, tr_mean)
            dim_scores.append(tr_score)
        else:
            tr_score = None

        # 涨跌家数比分
        if len(pc) > 0 and not hist_ind.empty:
            hist_ur = (
                hist_ind.groupby("trade_date")
                .apply(lambda g: float((g["pct_change"].dropna() > 0).sum()) / max(len(g["pct_change"].dropna()), 1))
                .dropna()
            )
            ur_score = _sp_rank(hist_ur, up_count / max(len(pc), 1))
            dim_scores.append(ur_score)
        else:
            ur_score = None

        composite = round(float(np.mean(dim_scores)), 1) if dim_scores else None
        heat_label = "hot" if composite is not None and composite >= 70 else (
            "warm" if composite is not None and composite >= 40 else "cold"
        )

        idx = index_data.get(sw_code, {})

        results.append({
            "sw_code": sw_code,
            "sw_name": sw_name,
            "trade_date": trade_date,
            "n_stocks": n_stocks,
            "n_with_data": len(pc),
            "avg_pct_change": avg_pct,
            "vs_market_pct": vs_market,
            "up_count": up_count,
            "down_count": down_count,
            "up_ratio": up_ratio,
            "med_pe": med_pe,
            "med_pb": med_pb,
            "total_mv": total_mv,
            "avg_mv": avg_mv,
            "total_amount": total_amount,
            "composite_score": composite,
            "heat_label": heat_label,
            "score_valuation": round(pe_score, 1) if pe_score is not None else None,
            "score_turnover": round(tr_score, 1) if tr_score is not None else None,
            "score_up_ratio": round(ur_score, 1) if ur_score is not None else None,
            "top_gainers": top_gainers[:3],
            "top_losers": top_losers[:3],
            "index_close": idx.get("index_close"),
            "index_pct_change": idx.get("index_pct_change"),
            "index_pe": idx.get("index_pe"),
            "index_pe_ttm": idx.get("index_pe_ttm"),
            "index_pb": idx.get("index_pb"),
            "index_div_yield": idx.get("index_div_yield"),
        })

    # 补充市场参考
    results.append({
        "sw_code": "__market__",
        "sw_name": "全市场",
        "trade_date": trade_date,
        "n_stocks": market_total,
        "n_with_data": market_total,
        "avg_pct_change": round(market_avg_pct, 2),
        "vs_market_pct": 0,
        "up_count": market_up_count,
        "down_count": market_total - market_up_count,
        "up_ratio": round(market_up_count / max(market_total, 1) * 100, 1),
        "med_pe": None, "med_pb": None,
        "total_mv": None, "avg_mv": None, "total_amount": None,
        "composite_score": None, "heat_label": None,
        "score_valuation": None, "score_turnover": None, "score_up_ratio": None,
        "top_gainers": [], "top_losers": [],
    })

    results.sort(key=lambda x: (
        0 if x["sw_code"] == "__market__" else
        (-x["composite_score"] if x["composite_score"] is not None else 0)
    ))
    for i, r in enumerate(results):
        r["rank"] = i + 1 if r["sw_code"] != "__market__" else None

    logger.info("focus_industries: %d industries computed for %s", len(results) - 1, trade_date)
    return results
