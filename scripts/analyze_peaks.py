import sys
import json
import logging
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

out = []
for dt, desc in dates:
    r = calculate_heat_index(trade_date=dt)
    row = {'date': dt, 'desc': desc, 'composite': round(r['composite_score'] or 0, 1)}
    for dim in ['valuation', 'fund', 'sentiment', 'technical', 'structure']:
        row[dim] = round(r.get('dim_' + dim) or 0, 1)
        row[dim + '_detail'] = {}
        for k, v in r['indicators'].get(dim, {}).items():
            row[dim + '_detail'][k] = round(v, 1) if v is not None else None
    out.append(row)

with open('data/peak_analysis.json', 'w') as f:
    json.dump(out, f, indent=2, ensure_ascii=False)
print('OK - %d dates analyzed' % len(out))
