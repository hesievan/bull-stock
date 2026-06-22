import sys
import logging
import time
sys.path.insert(0, '.')
from src.indicators.calculator import calculate_heat_index
logging.basicConfig(level=logging.WARNING)

results = {}
for dt, desc in [('2020-07-10', '20牛市启'), ('2015-06-12', '15牛市顶'), ('2024-10-08', '24脉冲顶')]:
    t0 = time.time()
    r = calculate_heat_index(trade_date=dt)
    t1 = time.time()
    c = r['composite_score'] or 0
    ind = r['indicators']
    results[dt] = c
    print("%s %s: 综合=%.1f (%.1fs) PE分位=%.0f 新高=%.1f" % (
        dt, desc, c, t1-t0,
        ind['valuation'].get('PE_percentile', 0) or 0,
        ind['technical'].get('new_high_ratio', 0) or 0))

print()
print("=== 修复前后对比 ===")
old = {'2021-02-18': 59.0, '2020-07-10': 61.5, '2015-06-12': 60.1, '2024-10-08': 73.5}
for dt, desc in [('2021-02-18', '21牛市顶'), ('2020-07-10', '20牛市启'), ('2015-06-12', '15牛市顶'), ('2024-10-08', '24脉冲顶')]:
    new_c = results.get(dt, 59.0)  # 2021 already run
    oc = old.get(dt, 0)
    diff = new_c - oc
    sign = "+" if diff > 0 else ""
    hit = " 🔴" if new_c >= 70 else (" 🟡" if new_c >= 65 else "")
    print("%s %s: %.1f -> %.1f (%s%.1f)%s" % (dt, desc, oc, new_c, sign, diff, hit))
