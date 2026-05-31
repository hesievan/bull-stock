#!/usr/bin/env python3
"""
热度指数历史回测 — 验证指标在牛熊转换中的表现
测试日期覆盖: 2015牛市顶 → 2018熊底 → 2020疫情底 → 2021牛市顶 → 2022熊底 → 2024反弹
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TUSHARE_TOKEN", "473bc93a521c11cac2f5136b08bccbcb819d220fcee5d8f04b389577")

from src.indicators.calculator import calculate_heat_index
import logging
logging.basicConfig(level=logging.WARNING)

# 关键历史节点 (日期, 上证综指, 市场状态, 描述)
KEY_DATES = [
    ("2015-06-12", 5178, "BULL_PEAK",    "2015杠杆牛市顶峰"),
    ("2015-07-09", 3826, "CRASH",         "2015股灾暴跌中"),
    ("2015-08-26", 2850, "BEAR_LOW",      "2015股灾后低点"),
    ("2016-01-28", 2638, "BEAR_LOW",      "2016熔断后低点"),
    ("2017-12-29", 3307, "RECOVERY",      "2017蓝筹慢牛"),
    ("2018-01-29", 3559, "BULL_PEAK",     "2018年初高点"),
    ("2018-10-19", 2550, "BEAR_LOW",      "2018熊市底部区域"),
    ("2018-12-28", 2493, "BEAR_BOTTOM",   "2018熊市绝对底"),
    ("2019-04-19", 3270, "RECOVERY",      "2019反弹高点"),
    ("2020-01-23", 2976, "PRE_CRASH",     "2020疫情前"),
    ("2020-03-23", 2646, "CRASH_BOTTOM",  "2020疫情底"),
    ("2020-07-10", 3450, "BULL",          "2020牛市启动"),
    ("2021-02-18", 3731, "BULL_PEAK",     "2021牛市顶峰"),
    ("2021-12-31", 3640, "LATE_BULL",     "2021年末高位"),
    ("2022-04-27", 2863, "BEAR_BOTTOM",   "2022上海封城底"),
    ("2022-10-31", 2885, "BEAR_BOTTOM",   "2022熊市二次底"),
    ("2023-06-30", 3202, "RECOVERY",      "2023年中反弹"),
    ("2024-02-05", 2702, "BEAR_BOTTOM",   "2024年初低点"),
    ("2024-09-24", 2861, "PRE_SURGE",     "2024反弹前夜"),
    ("2024-10-08", 3489, "SURGE_PEAK",    "2024脉冲式反弹顶"),
    ("2025-03-28", 3350, "RECOVERY",      "2025年3月"),
    ("2025-05-29", 3348, "CURRENT",       "当前"),
]

results = []
for dt, idx_close, state, desc in KEY_DATES:
    t0 = time.time()
    try:
        r = calculate_heat_index(trade_date=dt)
        elapsed = time.time() - t0
        results.append({
            "date": dt, "sh_close": idx_close, "state": state, "desc": desc,
            "composite": r["composite_score"],
            "valuation": r["dim_valuation"],
            "fund": r["dim_fund"],
            "sentiment": r["dim_sentiment"],
            "technical": r["dim_technical"],
            "structure": r["dim_structure"],
            "indicators": r["indicators"],
            "elapsed": round(elapsed, 1),
        })
        print("✅ %s [%s] composite=%.1f val=%.1f fund=%.1f sent=%.1f tech=%.1f (%.1fs)" % (
            dt, state, r["composite_score"] or 0, r["dim_valuation"] or 0,
            r["dim_fund"] or 0, r["dim_sentiment"] or 0, r["dim_technical"] or 0, elapsed
        ), flush=True)
    except Exception as e:
        print("❌ %s: %s" % (dt, str(e)[:80]), flush=True)
        results.append({"date": dt, "sh_close": idx_close, "state": state, "desc": desc, "error": str(e)[:100]})

# 保存完整结果
with open("data/backtest_results.json", "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False, default=str)

# 输出汇总表
print("\n" + "=" * 120)
print("日期          指数   状态           综合   估值   资金   情绪   技术   结构")
print("=" * 120)
for r in results:
    if "error" in r:
        print("%s  %5d  %-14s  ERROR: %s" % (r["date"], r["sh_close"], r["state"], r.get("error","")[:50]))
    else:
        print("%s  %5d  %-14s  %5.1f  %5.1f  %5.1f  %5.1f  %5.1f  %5.1f" % (
            r["date"], r["sh_close"], r["state"],
            r["composite"] or 0, r["valuation"] or 0, r["fund"] or 0,
            r["sentiment"] or 0, r["technical"] or 0, r["structure"] or 0
        ))
print("=" * 120)
print("Done! %d dates tested." % len(results))
