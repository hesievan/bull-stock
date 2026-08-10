#!/usr/bin/env python3
"""
回填所有历史日期的 V2 指标原始值
生成 web/data/indicator_history.json 供前端绘制8指标真实值趋势图

用法:
  python scripts/backfill_indicator_history.py
"""
import sys
import os
import json
import logging
import sqlite3

import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
DB_PATH = os.path.join(os.path.dirname(__file__),"..","data","heat_index.db")
DATA_DIR = os.path.join(os.path.dirname(__file__),"..","web","data")

def main():
    logger.info("Generating raw indicator history...")
    conn = sqlite3.connect(DB_PATH)

    # 1. 大盘PE (实际值: pe_med 倍)
    logger.info("1/9 大盘PE...")
    pe = pd.read_sql("SELECT trade_date, pe_med FROM index_daily_pe WHERE pe_med>0 ORDER BY trade_date", conn)
    pe_d = dict(zip(pe["trade_date"], pe["pe_med"].round(2)))

    # 2. 涨停封板率 (实际值: seal_rate 0-1)
    logger.info("2/8 涨停封板率...")
    sr = pd.read_sql("SELECT trade_date, seal_rate FROM daily_seal_rate WHERE seal_rate IS NOT NULL ORDER BY trade_date", conn)
    sr_d = dict(zip(sr["trade_date"], sr["seal_rate"].round(6)))

    # 3. 两融余额市值比 (实际值: ratio)
    logger.info("3/8 两融余额市值比...")
    mg = pd.read_sql("""
        SELECT m.trade_date, (m.rzye+m.rqye)/(c.total_circ_mv*10000) as ratio
        FROM margin_history m JOIN daily_circ_mv c ON m.trade_date=c.trade_date
        WHERE c.total_circ_mv>0 AND m.rzye>0 ORDER BY m.trade_date
    """, conn)
    mg_d = dict(zip(mg["trade_date"], (mg["ratio"]).round(6)))  # 小数

    # 4. 成交额M2比 (实际值: 日总成交额(元)/M2(元))
    logger.info("4/8 成交额M2比...")
    # amount(千元→元×1000), M2(亿元→元×1e8)
    amt_m = pd.read_sql("""
        SELECT m, AVG(daily_amt*1000) / (SELECT MAX(m2_billion)*1e8 FROM m2_monthly WHERE m2_monthly.month=m) as ratio
        FROM (SELECT substr(trade_date,1,7) as m, SUM(amount) as daily_amt FROM stock_daily WHERE amount>0 GROUP BY trade_date)
        GROUP BY m ORDER BY m
    """, conn)
    # Handle potential division by zero
    amt_m = amt_m.dropna(subset=['ratio'])
    tm2_map = dict(zip(amt_m["m"], amt_m["ratio"]))
    tm2_d = {}
    for td in pd.read_sql("SELECT DISTINCT trade_date FROM stock_daily WHERE trade_date>='2010-01-01' ORDER BY trade_date", conn)["trade_date"]:
        m = td[:7]
        if m in tm2_map: tm2_d[td] = round(tm2_map[m], 6)

    logger.info("5/8 换手率...")
    to = pd.read_sql("""
        SELECT trade_date, SUM(amount)/SUM(circ_mv)*10 as rate
        FROM stock_daily WHERE amount>0 AND circ_mv>0 AND trade_date>='2015-01-01'
        GROUP BY trade_date ORDER BY trade_date
    """, conn)
    to_d = dict(zip(to["trade_date"], to["rate"].round(4)))

    # 6. MA排列比 (实际值: %)
    logger.info("6/8 MA排列比...")
    ma = pd.read_sql("SELECT trade_date, ma_alignment_ratio FROM daily_ma_alignment ORDER BY trade_date", conn)
    ma_d = dict(zip(ma["trade_date"], ma["ma_alignment_ratio"].round(4)))  # 小数

    # 7. 巴菲特指标 (总市值/年度GDP, 倍数)
    logger.info("7/8 巴菲特指标...")
    gdp = pd.read_sql("SELECT quarter, gdp FROM gdp_quarterly WHERE gdp IS NOT NULL ORDER BY quarter", conn)
    gdp["year"] = gdp["quarter"].str[:4].astype(int)
    annual_gdp = gdp.groupby("year")["gdp"].sum().to_dict()
    years = sorted(annual_gdp.keys())
    daily_mv = pd.read_sql("""
        SELECT trade_date, SUM(total_mv)*10000 as tot_mv
        FROM stock_daily WHERE total_mv>0 AND trade_date>='2010-01-01'
        GROUP BY trade_date ORDER BY trade_date
    """, conn)
    bf_d = {}
    for _, row in daily_mv.iterrows():
        y = int(row["trade_date"][:4])
        gdp_y = y-1
        while gdp_y not in annual_gdp and gdp_y > min(years): gdp_y -= 1
        if gdp_y in annual_gdp and annual_gdp[gdp_y] > 0:
            bf_d[row["trade_date"]] = round(row["tot_mv"]/(annual_gdp[gdp_y]*1e8), 4)

    # 8. 创新高占比 (实际值: %)
    logger.info("8/8 创新高占比...")
    nh_d = {}
    try:
        nh = pd.read_sql("""
            SELECT trade_date, SUM(CASE WHEN close>=max_250d*0.98 THEN 1 ELSE 0 END)*1.0/COUNT(*) as ratio
            FROM (SELECT trade_date, stock_code, close, MAX(close) OVER (
                PARTITION BY stock_code ORDER BY trade_date ROWS BETWEEN 249 PRECEDING AND CURRENT ROW
            ) as max_250d FROM stock_daily WHERE close>0 AND trade_date>='2014-01-01')
            WHERE max_250d>0 AND trade_date>='2015-01-01' GROUP BY trade_date ORDER BY trade_date
        """, conn)
        nh_d = dict(zip(nh["trade_date"], nh["ratio"].round(4)))  # 小数
        logger.info("  创新高占比: %d dates", len(nh_d))
    except Exception as e:
        logger.warning("创新高占比失败: %s", str(e)[:60])

    conn.close()

    # 合并输出
    all_dates = sorted(set(pe_d)|set(sr_d)|set(mg_d)|set(tm2_d)|set(to_d)|set(ma_d)|set(bf_d)|set(nh_d))
    result = {}
    for td in all_dates:
        entry = {}
        if td in pe_d: entry["pe"] = pe_d[td]
        if td in sr_d: entry["seal_rate"] = sr_d[td]
        if td in mg_d: entry["margin_ratio_v2"] = mg_d[td]
        if td in tm2_d: entry["turnover_m2"] = tm2_d[td]
        if td in to_d: entry["turnover"] = to_d[td]
        if td in ma_d: entry["ma_alignment"] = ma_d[td]
        if td in bf_d: entry["buffett"] = bf_d[td]
        if td in nh_d: entry["new_high"] = nh_d[td]
        if entry: result[td] = entry

    out_path = os.path.join(DATA_DIR, "indicator_history.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    logger.info("Done: %d dates, %d KB", len(result), os.path.getsize(out_path)//1024)

    # 统计各指标覆盖
    for k in ["pe","seal_rate","buffett","margin_ratio_v2","turnover_m2","turnover","new_high","ma_alignment"]:
        cnt = sum(1 for v in result.values() if k in v)
        logger.info("  %s: %d dates", k, cnt)

if __name__ == "__main__":
    main()
