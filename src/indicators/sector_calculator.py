"""
板块热度计算引擎 — 证监会一级行业

从 calculator.py 拆分, 职责: 按行业计算热度评分
"""
import logging

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

SECTOR_NAME_MAP = {
    "A01": "农业", "A02": "林业", "A03": "畜牧业", "A04": "渔业",
    "A05": "农林牧渔辅助", "B06": "煤炭开采", "B07": "石油天然气开采",
    "B08": "黑色金属矿采选", "B09": "有色金属矿采选", "B10": "非金属矿采选",
    "B11": "开采辅助", "B12": "其他采矿", "C13": "农副食品加工",
    "C14": "食品制造", "C15": "酒类饮料", "C17": "纺织业", "C18": "纺织服装",
    "C19": "皮革制品", "C21": "家具制造", "C22": "造纸", "C25": "石油加工炼焦",
    "C26": "化学原料", "C27": "医药制造", "C28": "化学纤维", "C29": "橡胶塑料",
    "C30": "非金属矿物制品", "C31": "黑色金属冶炼", "C32": "有色金属冶炼",
    "C33": "金属制品", "C34": "通用设备制造", "C35": "专用设备制造",
    "C36": "汽车制造", "C38": "电气机械器材", "C39": "计算机通信电子",
    "D44": "电力生产供应", "D45": "燃气生产供应", "D46": "水的生产供应",
    "E47": "房屋建筑", "E48": "土木工程", "E49": "建筑装饰",
    "F51": "批发业", "F52": "零售业", "G56": "航空运输", "G58": "道路运输",
    "G60": "仓储邮政", "I63": "电信广播电视", "I64": "互联网相关",
    "I65": "软件信息技术", "J66": "货币金融服务", "J67": "资本市场服务",
    "J68": "保险业", "J69": "其他金融", "K70": "房地产业",
    "L71": "租赁业", "L72": "商务服务", "M73": "研究和试验",
    "M75": "科技推广", "O79": "居民服务", "P82": "教育",
    "Q83": "卫生", "R85": "新闻传媒", "R87": "文化艺术",
    "R89": "娱乐业", "S90": "综合",
}


def _sector_name(code):
    return SECTOR_NAME_MAP.get(code[:3] if code else "", code or "未知")


def _sp_rank(series, value):
    """历史分位 0-1"""
    if series.empty or pd.isna(value):
        return 0.5
    s = series.dropna()
    return float((s < value).sum()) / max(len(s), 1)


def _sp_combine(scores):
    v = [x for x in scores if x is not None and not np.isnan(x)]
    return round(float(np.mean(v)), 1) if v else None


def _sect_valuation(scode, today_df, _hist_pm, _hist_bm):
    """估值: 行业中位数PE/PB历史分位(查预计算表)"""
    mem = today_df[today_df["industry"] == scode]
    if len(mem) < 5:
        return None
    out = []
    pe = pd.to_numeric(mem["peTTM"], errors="coerce").dropna().median()
    if pd.notna(pe) and pe > 0:
        h = _hist_pm[_hist_pm["industry"] == scode]["peTTM"].dropna()
        if len(h) > 20:
            out.append(_sp_rank(h, float(pe)) * 100)
    pb = pd.to_numeric(mem["pbMRQ"], errors="coerce").dropna().median()
    if pd.notna(pb) and pb > 0:
        h = _hist_bm[_hist_bm["industry"] == scode]["pbMRQ"].dropna()
        if len(h) > 20:
            out.append(_sp_rank(h, float(pb)) * 100)
    return _sp_combine(out)


def _sect_sentiment(scode, today_df, _hist_tm, _hist_up_ratio):
    """情绪: 行业换手率 + 涨跌家数比(查预计算表)"""
    mem = today_df[today_df["industry"] == scode]
    if len(mem) < 5:
        return None
    out = []
    tr = pd.to_numeric(mem["turnover_rate"], errors="coerce").dropna()
    if len(tr) > 0:
        ht = _hist_tm[_hist_tm["industry"] == scode]["turnover_rate"].dropna()
        if len(ht) > 20:
            out.append(_sp_rank(ht, float(tr.mean())) * 100)
    pc = pd.to_numeric(mem["pct_change"], errors="coerce").dropna()
    if len(pc) > 0:
        ur = float((pc > 0).sum()) / max(len(pc), 1)
        hu = _hist_up_ratio[_hist_up_ratio["industry"] == scode]["up_ratio"].dropna()
        if len(hu) > 20:
            out.append(_sp_rank(hu, ur) * 100)
    return _sp_combine(out)


def _sect_technical(scode, today_df, hist_df):
    """技术: 站上年线比例 + 创新高比例 (向量化)"""
    mem = today_df[today_df["industry"] == scode]
    if len(mem) < 10:
        return None
    out = []
    cv = mem[["stock_code", "close"]].copy()
    cv["c"] = pd.to_numeric(cv["close"], errors="coerce")
    cv = cv.dropna(subset=["c"])

    if len(cv) > 50:
        cv = cv.sample(50, random_state=42)

    codes = cv["stock_code"].tolist()
    hist_sub = hist_df[(hist_df["industry"] == scode) & (hist_df["stock_code"].isin(codes))].copy()
    hist_sub["c"] = pd.to_numeric(hist_sub["close"], errors="coerce")
    hist_sub = hist_sub.dropna(subset=["c"])

    above_n, total_ma = 0, 0
    nh_n, total_nh = 0, 0
    for code, grp in hist_sub.groupby("stock_code"):
        grp = grp.sort_values("trade_date")
        close_200 = grp["c"].tail(200)
        if len(close_200) >= 50:
            total_ma += 1
            ma200 = close_200.mean()
            cur = cv[cv["stock_code"] == code]["c"].values[0]
            if cur > ma200:
                above_n += 1
        close_250 = grp["c"].tail(250)
        if len(close_250) >= 100:
            total_nh += 1
            cur = cv[cv["stock_code"] == code]["c"].values[0]
            if cur >= close_250.max() * 0.99:
                nh_n += 1

    if total_ma >= 5:
        out.append(min(100.0, max(0.0, above_n / total_ma * 100)))
    if total_nh >= 5:
        out.append(min(100.0, max(0.0, nh_n / total_nh * 100)))

    return _sp_combine(out)


def calculate_sector_heat(trade_date: str, db_path: str) -> list:
    """计算所有行业热度, 返回按分数降序的 list[dict]"""
    from src.data.database import get_conn

    logger.info("Calculating sector heat for %s ...", trade_date)

    with get_conn(db_path) as conn:
        ind_map = pd.read_sql(
            "SELECT code, industry FROM stock_industry WHERE industry IS NOT NULL AND industry != ''", conn
        )

        today = pd.read_sql(
            """SELECT stock_code, close, pct_change, peTTM, pbMRQ, turnover_rate
               FROM stock_daily WHERE trade_date = ?""", conn, params=[trade_date]
        )
        # 如果指定日期没有数据，使用最新可用日期
        if today.empty:
            latest = pd.read_sql("SELECT MAX(trade_date) as d FROM stock_daily", conn)
            if not latest.empty and latest.iloc[0]["d"]:
                actual_date = latest.iloc[0]["d"]
                logger.info("Using latest available date: %s (requested: %s)", actual_date, trade_date)
                today = pd.read_sql(
                    """SELECT stock_code, close, pct_change, peTTM, pbMRQ, turnover_rate
                       FROM stock_daily WHERE trade_date = ?""", conn, params=[actual_date]
                )
                trade_date = actual_date

        for col in ("pct_change", "peTTM", "pbMRQ", "close", "turnover_rate"):
            today[col] = pd.to_numeric(today[col], errors="coerce")
        today = today.merge(ind_map, left_on="stock_code", right_on="code", how="inner")
        if today.empty:
            logger.error("No stocks after industry join for %s", trade_date)
            return []

        start = (pd.to_datetime(trade_date) - pd.DateOffset(years=1)).strftime("%Y-%m-%d")
        ind_codes = ind_map["code"].tolist()
        hist_parts = []
        batch_size = 500
        for i in range(0, len(ind_codes), batch_size):
            batch = ind_codes[i:i+batch_size]
            ph = ",".join(["?"] * len(batch))
            h = pd.read_sql(
                f"""SELECT stock_code, trade_date, close, pct_change, peTTM, pbMRQ, turnover_rate
                    FROM stock_daily WHERE trade_date >= ? AND trade_date <= ? AND stock_code IN ({ph})""",
                conn, params=[start, trade_date] + batch,
            )
            hist_parts.append(h)
        hist = pd.concat(hist_parts, ignore_index=True) if hist_parts else pd.DataFrame()
        for col in ("pct_change", "peTTM", "pbMRQ", "close", "turnover_rate"):
            hist[col] = pd.to_numeric(hist[col], errors="coerce")
        hist = hist.merge(ind_map, left_on="stock_code", right_on="code", how="inner")

    _hist_pm = hist.groupby(["trade_date", "industry"])["peTTM"].median().reset_index()
    _hist_bm = hist.groupby(["trade_date", "industry"])["pbMRQ"].median().reset_index()
    _hist_tm = hist.groupby(["trade_date", "industry"])["turnover_rate"].mean().reset_index()
    _hist_up_ratio = (
        hist.assign(up=lambda _df: (_df["pct_change"] > 0).astype(float))
        .groupby(["trade_date", "industry"])
        .agg(up_sum=("up", "sum"), total=("up", "count"))
        .reset_index()
    )
    _hist_up_ratio["up_ratio"] = _hist_up_ratio["up_sum"] / _hist_up_ratio["total"].clip(lower=1)

    results = []
    for scode, members in today.groupby("industry"):
        n = len(members)
        if n < 5:
            continue
        val = _sect_valuation(scode, today, _hist_pm, _hist_bm)
        sent = _sect_sentiment(scode, today, _hist_tm, _hist_up_ratio)
        tech = _sect_technical(scode, today, hist)

        ws, vs = [], []
        if val is not None: ws.append(0.4); vs.append(val)
        if sent is not None: ws.append(0.3); vs.append(sent)
        if tech is not None: ws.append(0.3); vs.append(tech)
        if not vs:
            continue

        comp = round(sum(v * w for v, w in zip(vs, ws)) / sum(ws), 1)
        comp = max(0.0, min(100.0, comp))
        label = "hot" if comp >= 70 else ("warm" if comp >= 40 else "cold")

        pc = members["pct_change"].dropna()
        avg_pct = round(float(pc.mean()), 2) if len(pc) > 0 else None
        up_r = round(float((pc > 0).sum() / max(len(pc), 1) * 100), 1) if len(pc) > 0 else None

        leader = None
        if len(pc) > 0:
            li = pc.idxmax()
            leader = {"code": str(members.loc[li, "stock_code"]), "pct": round(float(pc.loc[li]), 2)}

        results.append({
            "sector_code": scode,
            "sector_name": _sector_name(scode),
            "n_stocks": int(n),
            "composite_score": comp,
            "heat_label": label,
            "dim_valuation": val,
            "dim_sentiment": sent,
            "dim_technical": tech,
            "avg_pct_change": avg_pct,
            "up_ratio": up_r,
            "leader": leader,
        })

    results.sort(key=lambda x: x["composite_score"], reverse=True)
    for i, r in enumerate(results, 1):
        r["rank"] = i

    logger.info("Sector heat done: %d sectors", len(results))
    return results
