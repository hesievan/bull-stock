#!/usr/bin/env python3
"""
回填所有历史日期的 V2 指标数据
生成 web/data/indicator_history.json 供前端绘制9指标趋势图

用法:
  python scripts/backfill_indicator_history.py
"""
import sys
import os
import json
import logging
import sqlite3
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "heat_index.db")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "web", "data")


def _pct_rank(series, value):
    clean = [x for x in series if x is not None and not (isinstance(x, float) and np.isnan(x))]
    if not clean or value is None:
        return 0.5
    return sum(1 for x in clean if x < value) / len(clean)


def main():
    logger.info("Generating indicator history...")
    conn = sqlite3.connect(DB_PATH)

    # 1. 大盘PE
    logger.info("1/8 大盘PE...")
    pe = pd.read_sql("SELECT trade_date, pe_med FROM index_daily_pe WHERE pe_med>0 ORDER BY trade_date", conn)
    pe["pe_score"] = pe["pe_med"].expanding().apply(lambda x: (1 - _pct_rank(x.dropna(), x.iloc[-1])) * 100, raw=False)
    pe_dict = dict(zip(pe["trade_date"], pe["pe_score"].round(1)))

    # 2. ERP
    logger.info("2/8 ERP...")
    erp = pd.read_sql("SELECT trade_date, erp FROM daily_erp WHERE erp IS NOT NULL ORDER BY trade_date", conn)
    erp["erp_score"] = erp["erp"].expanding().apply(lambda x: (1 - _pct_rank(x.dropna(), x.iloc[-1])) * 100, raw=False)
    erp_dict = dict(zip(erp["trade_date"], erp["erp_score"].round(1)))

    # 3. 两融余额市值比
    logger.info("3/8 两融余额市值比...")
    margin = pd.read_sql("""
        SELECT m.trade_date, (m.rzye+m.rqye)/(c.total_circ_mv*10000) as ratio
        FROM margin_history m
        JOIN daily_circ_mv c ON m.trade_date=c.trade_date AND c.total_circ_mv>0
        WHERE m.rzye>0 ORDER BY m.trade_date
    """, conn)
    margin_dict = {}
    if not margin.empty:
        ratios = margin["ratio"].values
        for i, (_, r) in enumerate(margin.iterrows()):
            pct = sum(1 for x in ratios[:i+1] if x < r["ratio"]) / (i + 1)
            score = (1 - pct) * 100 if pct > 0.9 else pct * 100
            margin_dict[r["trade_date"]] = round(score, 1)

    # 4. 存款市值比 (M2/市值)
    logger.info("4/8 存款市值比(M2/总市值)...")
    m2_all = pd.read_sql("SELECT month, m2_billion FROM m2_monthly WHERE m2_billion IS NOT NULL ORDER BY month", conn)
    mv_monthly = pd.read_sql("""
        SELECT substr(trade_date,1,7) as month, AVG(total_mv)*10000 as avg_mv
        FROM stock_daily WHERE total_mv>0 AND trade_date>='2010-01-01'
        GROUP BY month ORDER BY month
    """, conn)
    merged = m2_all.merge(mv_monthly, on="month", how="inner")
    merged["ratio"] = (merged["m2_billion"] * 1e8) / merged["avg_mv"]
    # Map monthly ratio to daily
    month_ratio_map = dict(zip(merged["month"], merged["ratio"]))
    mv_dates = pd.read_sql("SELECT DISTINCT trade_date FROM stock_daily WHERE trade_date>='2010-01-01' ORDER BY trade_date", conn)
    ratios_list = list(merged["ratio"].values)
    deposit_dict = {}
    for _, row in mv_dates.iterrows():
        td = row["trade_date"]
        m = td[:7]
        if m in month_ratio_map:
            cur = month_ratio_map[m]
            pct = sum(1 for x in ratios_list if x < cur) / len(ratios_list)
            score = (1 - pct) * 100
            deposit_dict[td] = round(score, 1)

    # 5. 成交额M2比
    logger.info("5/8 成交额M2比...")
    amt_monthly = pd.read_sql("""
        SELECT substr(trade_date,1,7) as month, AVG(amount) as avg_amt
        FROM stock_daily WHERE amount>0 AND trade_date>='2010-01-01'
        GROUP BY month ORDER BY month
    """, conn)
    merged2 = m2_all.merge(amt_monthly, on="month", how="inner")
    merged2["t_m2_ratio"] = merged2["avg_amt"] / (merged2["m2_billion"] * 1e8)
    tm2_map = dict(zip(merged2["month"], merged2["t_m2_ratio"]))
    tm2_list = list(merged2["t_m2_ratio"].values)
    tm2_dict = {}
    for _, row in mv_dates.iterrows():
        m = row["trade_date"][:7]
        if m in tm2_map:
            cur = tm2_map[m]
            pct = sum(1 for x in tm2_list if x < cur) / len(tm2_list)
            score = pct * 100
            tm2_dict[row["trade_date"]] = round(score, 1)

    # 6. 换手率
    logger.info("6/8 换手率...")
    turnover = pd.read_sql("""
        SELECT trade_date, SUM(amount)/SUM(circ_mv)*10 as rate
        FROM stock_daily WHERE amount>0 AND circ_mv>0 AND trade_date>='2015-01-01'
        GROUP BY trade_date ORDER BY trade_date
    """, conn)
    turnover_dict = {}
    if not turnover.empty:
        rates = turnover["rate"].values
        for i, (_, r) in enumerate(turnover.iterrows()):
            window_start = max(0, i - 125)  # ~6 months
            window = rates[window_start:i+1]
            pct = sum(1 for x in window if x < r["rate"]) / len(window)
            turnover_dict[r["trade_date"]] = round(pct * 100, 1)

    # 7. MA排列比
    logger.info("7/8 MA排列比...")
    ma = pd.read_sql("SELECT trade_date, ma_alignment_ratio FROM daily_ma_alignment ORDER BY trade_date", conn)
    ma_dict = dict(zip(ma["trade_date"], (ma["ma_alignment_ratio"] * 100).round(1)))

    # 8. 巴菲特指标
    logger.info("8/8 巴菲特指标...")
    gdp_all = pd.read_sql("SELECT quarter, gdp FROM gdp_quarterly WHERE gdp IS NOT NULL ORDER BY quarter", conn)
    gdp_all["year"] = gdp_all["quarter"].str[:4].astype(int)
    annual_gdp = gdp_all.groupby("year")["gdp"].sum().to_dict()
    years = sorted(annual_gdp.keys())

    buffett_dict = {}
    daily_mv = pd.read_sql("""
        SELECT trade_date, SUM(total_mv)*10000 as tot_mv
        FROM stock_daily WHERE total_mv>0 AND trade_date>='2010-01-01'
        GROUP BY trade_date ORDER BY trade_date
    """, conn)
    hist_ratios = []
    for _, row in daily_mv.iterrows():
        y = int(row["trade_date"][:4])
        gdp_y = y - 1
        while gdp_y not in annual_gdp and gdp_y > min(years):
            gdp_y -= 1
        if gdp_y in annual_gdp and annual_gdp[gdp_y] > 0:
            ratio = row["tot_mv"] / (annual_gdp[gdp_y] * 1e8)
            hist_ratios.append(ratio)

    for i, (_, row) in enumerate(daily_mv.iterrows()):
        y = int(row["trade_date"][:4])
        gdp_y = y - 1
        while gdp_y not in annual_gdp and gdp_y > min(years):
            gdp_y -= 1
        if gdp_y in annual_gdp and annual_gdp[gdp_y] > 0:
            cur = row["tot_mv"] / (annual_gdp[gdp_y] * 1e8)
            window = hist_ratios[:i+1]
            pct = sum(1 for x in window if x < cur) / len(window)
            buffett_dict[row["trade_date"]] = round((1 - pct) * 100, 1)

    # 9. 创新高占比 (使用SQLite窗口函数, 按stock_code分区后取250日最高价)
    logger.info("9/9 创新高占比...")
    # SQLite 3.25+ 支持窗口函数
    nh_dict = {}
    try:
        nh = pd.read_sql("""
            SELECT trade_date,
                   SUM(CASE WHEN close >= max_250d * 0.98 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as ratio
            FROM (
                SELECT trade_date, stock_code, close,
                       MAX(close) OVER (
                           PARTITION BY stock_code
                           ORDER BY trade_date
                           ROWS BETWEEN 249 PRECEDING AND CURRENT ROW
                       ) as max_250d
                FROM stock_daily
                WHERE close > 0 AND trade_date >= '2014-01-01'
            )
            WHERE max_250d > 0 AND trade_date >= '2015-01-01'
            GROUP BY trade_date
            ORDER BY trade_date
        """, conn)
        for _, r in nh.iterrows():
            nh_dict[r["trade_date"]] = round(r["ratio"], 1)
        logger.info("  创新高占比: %d dates", len(nh_dict))
    except Exception as e:
        logger.warning("  创新高占比计算失败(窗口函数不支持或数据问题): %s", str(e)[:60])

    conn.close()

    # 合并为统一结构: {"2015-06-12": {"pe": 42.8, "erp": 13.9, ...}, ...}
    all_dates = sorted(set(
        list(pe_dict.keys()) + list(erp_dict.keys()) + list(margin_dict.keys())
        + list(deposit_dict.keys()) + list(tm2_dict.keys()) + list(turnover_dict.keys())
        + list(ma_dict.keys()) + list(buffett_dict.keys()) + list(nh_dict.keys())
    ))
    result = {}
    for td in all_dates:
        entry = {}
        for key, src in [
            ("pe", pe_dict), ("erp", erp_dict), ("margin_ratio_v2", margin_dict),
            ("deposit_ratio", deposit_dict), ("turnover_m2", tm2_dict),
            ("turnover", turnover_dict), ("ma_alignment", ma_dict), ("buffett", buffett_dict),
            ("new_high", nh_dict),
        ]:
            if td in src:
                entry[key] = src[td]
        if entry:
            result[td] = entry

    out_path = os.path.join(DATA_DIR, "indicator_history.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    logger.info("Done: %d dates, %d KB -> %s", len(result), os.path.getsize(out_path)//1024, out_path)

    # Also update save_results_v2 to append indicator history on each run
    print(f"\nSummary: {len(result)} dates with indicator data")
    for k in ["pe","erp","buffett","margin_ratio_v2","deposit_ratio","turnover_m2","turnover","ma_alignment","new_high"]:
        cnt = sum(1 for v in result.values() if k in v)
        print(f"  {k}: {cnt} dates")


if __name__ == "__main__":
    main()
