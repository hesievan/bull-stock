import sys, logging, time
sys.path.insert(0, '.')
from src.indicators.calculator import calculate_heat_index
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

dates = [
    ('2015-06-12', '15牛市顶'),
    ('2021-02-18', '21牛市顶'),
    ('2024-10-08', '24脉冲顶'),
    ('2018-12-28', '18熊底'),
]

orig = {
    '2015-06-12': 60.1,
    '2021-02-18': 59.0,
    '2024-10-08': 73.5,
    '2018-12-28': 29.3,
}

print("=== 方案B: 历史成分股口径 PE/PB ===")
results = {}
for dt, desc in dates:
    t0 = time.time()
    r = calculate_heat_index(trade_date=dt)
    elapsed = time.time() - t0
    c = r['composite_score'] or 0
    ind = r['indicators']
    results[dt] = c

    pe = ind['valuation'].get('PE_percentile', 0) or 0
    pb = ind['valuation'].get('PB_percentile', 0) or 0
    old_c = orig.get(dt, 0)
    diff = c - old_c
    sign = "+" if diff > 0 else ""
    hit = " 🔴" if c >= 70 else (" 🟢" if c <= 35 else " 🟡" if c >= 65 else "")

    print()
    print("%s %s: 综合=%.1f (%s%.1f)%s (%.1fs)" % (dt, desc, c, sign, diff, hit, elapsed))
    print("  PE分位=%.0f PB分位=%.0f ERP=%.0f 破净=%.0f" % (
        pe, pb, ind['valuation'].get('ERP', 0) or 0, ind['valuation'].get('below_net_rate', 0) or 0))
    print("  margin=%.1f 北向=%.0f 新高=%.1f" % (
        ind['fund'].get('margin_ratio', 0) or 0,
        ind['fund'].get('northbound', 0) or 0,
        ind['sentiment'].get('new_high_ratio', 0) or 0))

print()
print("=== 汇总 ===")
for dt, desc in dates:
    c = results.get(dt, 0)
    old_c = orig.get(dt, 0)
    diff = c - old_c
    sign = "+" if diff > 0 else ""
    print("%s: %.1f -> %.1f (%s%.1f)" % (desc, old_c, c, sign, diff))
