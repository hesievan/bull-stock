import sys, logging, time
sys.path.insert(0, '.')
from src.indicators.calculator import calculate_heat_index
logging.basicConfig(level=logging.WARNING)

dates = [
    ('2015-06-12', '15牛市顶'),
    ('2020-07-10', '20牛市启'),
    ('2021-02-18', '21牛市顶'),
    ('2024-10-08', '24脉冲顶'),
    ('2018-12-28', '18熊底'),
    ('2025-05-29', '当前'),
]

orig = {
    '2015-06-12': 60.1,
    '2020-07-10': 61.5,
    '2021-02-18': 59.0,
    '2024-10-08': 73.5,
    '2018-12-28': 29.3,
    '2025-05-29': 54.7,
}

print("=== 方案B v2: 历史成分股口径 + 指标方向修复 ===")
results = {}
for dt, desc in dates:
    t0 = time.time()
    r = calculate_heat_index(trade_date=dt)
    elapsed = time.time() - t0
    c = r['composite_score'] or 0
    results[dt] = c
    old_c = orig.get(dt, 0)
    diff = c - old_c
    sign = "+" if diff > 0 else ""
    hit = " 🔴" if c >= 70 else (" 🟢" if c <= 35 else " 🟡" if c >= 65 else "")
    print("%s %s: %.1f (%s%.1f)%s (%.1fs)" % (dt, desc, c, sign, diff, hit, elapsed))

print()
print("=== 汇总 ===")
for dt, desc in dates:
    c = results.get(dt, 0)
    old_c = orig.get(dt, 0)
    diff = c - old_c
    sign = "+" if diff > 0 else ""
    print("%s: %.1f -> %.1f (%s%.1f)" % (desc, old_c, c, sign, diff))
