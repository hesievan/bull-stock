import sys, logging, time
sys.path.insert(0, '.')
from src.indicators.calculator import calculate_heat_index
logging.basicConfig(level=logging.WARNING)

dates = [
    ('2015-06-12', '15牛市顶', 5178),
    ('2020-07-10', '20牛市启', 3450),
    ('2021-02-18', '21牛市顶', 3731),
    ('2024-10-08', '24脉冲顶', 3489),
    ('2018-12-28', '18熊底',   2493),
    ('2024-02-05', '24年初底', 2702),
    ('2025-05-29', '当前',     3348),
]

print("=== 完整回测: 简单中位数PE + akshare融资(2015-2019) + close-only新高 ===")
print()
results = {}
for dt, desc, idx in dates:
    t0 = time.time()
    r = calculate_heat_index(trade_date=dt)
    elapsed = time.time() - t0
    c = r['composite_score'] or 0
    ind = r['indicators']
    pe = ind['valuation'].get('PE_percentile', 0) or 0
    margin = ind['fund'].get('margin_ratio', None)
    nh = ind['technical'].get('new_high_ratio', 0) or 0
    results[dt] = {'comp': c, 'pe': pe, 'margin': margin, 'nh': nh, 't': elapsed}

    hit = " 🔴" if c >= 70 else (" 🟢" if c <= 35 else " 🟡" if c >= 65 else "")
    margin_s = "%.1f" % margin if margin is not None else "N/A"
    print("%s %s: 综合=%.1f%s (%.1fs) | PE分位=%.0f margin=%s 新高=%.1f" % (
        dt, desc, c, hit, elapsed, pe, margin_s, nh))

print()
print("=== 与原始方案(无任何修复)对比 ===")
# 原始 baseline (成分股260只, 简单中位数, 无P1融资, 新高=0)
orig = {
    '2015-06-12': 60.1,
    '2020-07-10': 61.5,
    '2021-02-18': 59.0,
    '2024-10-08': 73.5,
    '2018-12-28': 29.3,
    '2024-02-05': 32.9,
    '2025-05-29': 54.7,
}
print("%-10s %-8s  %8s  %8s  %6s  %s" % ("日期","状态","原始","修复后","变化","判定"))
print("-" * 60)
for dt, desc, idx in dates:
    new_c = results[dt]['comp']
    old_c = orig.get(dt, 0)
    diff = new_c - old_c
    sign = "+" if diff > 0 else ""
    hit = results[dt]['comp']
    if hit >= 70: tag = "🔴红区"
    elif hit <= 35: tag = "🟢绿区"
    elif hit >= 65: tag = "🟡黄区"
    else: tag = "⚪中性"
    print("%-10s %-8s  %8.1f  %8.1f  %s%.1f  %s" % (dt, desc, old_c, new_c, sign, diff, tag))
