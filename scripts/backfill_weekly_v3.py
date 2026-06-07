#!/usr/bin/env python3
"""
周频采样回测：每周取最后一个交易日，用v3.4指标体系计算
输出: web/data/history.json + reports/history_chart.html
"""
import sys, os, json, time, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

from src.data.database import DB_PATH
from src.indicators.calculator import calculate_heat_index
import sqlite3

# 获取采样日期（每周最后一个交易日）
conn = sqlite3.connect(DB_PATH)
rows = conn.execute("""
    SELECT trade_date FROM index_daily_pe 
    WHERE trade_date >= '2015-01-05' 
    ORDER BY trade_date
""").fetchall()
all_dates = [r[0] for r in rows]
conn.close()

# 按周分组，取每周最后一个交易日
from collections import defaultdict
import pandas as pd

df = pd.DataFrame({'date': pd.to_datetime(all_dates)})
df['year'] = df['date'].dt.isocalendar().year.astype(int)
df['week'] = df['date'].dt.isocalendar().week.astype(int)
weekly = df.groupby(['year', 'week']).last().reset_index()
sample_dates = sorted(weekly['date'].dt.strftime('%Y-%m-%d').tolist())

# 加入关键日期
key_dates = [
    '2015-06-12', '2015-07-09', '2018-10-18', '2018-12-28',
    '2020-02-03', '2020-07-09', '2021-02-18', '2022-04-26',
    '2024-09-30', '2024-10-08', '2025-09-11', '2026-01-12',
]
all_sample = sorted(set(sample_dates + key_dates))
logger.info(f"周频采样: {len(sample_dates)}周 + {len(key_dates)}关键 = {len(all_sample)}点")

# 计算
t_start = time.time()
results = []
failed = 0

for i, td in enumerate(all_sample):
    try:
        res = calculate_heat_index(trade_date=td)
        if res and res.get('composite_score') is not None:
            item = {
                'trade_date': td,
                'composite_score': round(float(res['composite_score']), 1),
                'level': res.get('level', 'unknown'),
                'dimensions': {}
            }
            for k in ['valuation', 'fund', 'sentiment', 'technical', 'structure']:
                v = res.get(f'dim_{k}')
                item['dimensions'][k] = round(float(v), 1) if v is not None else None
            results.append(item)
        else:
            failed += 1
    except Exception as e:
        failed += 1
        logger.error(f"{td}: {str(e)[:60]}")

    if (i + 1) % 100 == 0:
        elapsed = time.time() - t_start
        logger.info(f"进度: {i+1}/{len(all_sample)} ({len(results)}成功/{failed}失败) [{elapsed:.0f}s]")

elapsed = time.time() - t_start
logger.info(f"完成: {len(results)}成功, {failed}失败, 耗时{elapsed:.0f}s ({elapsed/60:.1f}分钟)")

# 保存
DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'web', 'data')
with open(os.path.join(DATA_DIR, 'history.json'), 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False)
logger.info(f"history.json: {len(results)}条")

# 统计
scores = [r['composite_score'] for r in results]
red = sum(1 for s in scores if s >= 65)
yellow = sum(1 for s in scores if 40 <= s < 65)
green = sum(1 for s in scores if s < 40)
logger.info(f"统计: 均值{sum(scores)/len(scores):.1f} | 最高{max(scores)} | 最低{min(scores)} | 🔴{red} 🟡{yellow} 🟢{green}")
