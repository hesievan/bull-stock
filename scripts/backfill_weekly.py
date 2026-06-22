#!/usr/bin/env python3
"""
周频采样回测：用新指标系统计算 ~580 个代表性日期
输出: web/data/history.json (精简版) + web/data/history_full.json (完整版)
"""
import sys
import os
import time
import json
import logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

import sqlite3
import pandas as pd
from src.indicators.calculator import calculate_heat_index
from src.data.database import DB_PATH

# ── 获取采样日期 ──────────────────────────────────────────────────────────
conn = sqlite3.connect(DB_PATH)
dates = pd.read_sql('SELECT DISTINCT trade_date FROM index_daily_pe ORDER BY trade_date', conn)['trade_date'].tolist()
conn.close()

# 每周取一个交易日（周五或最近的交易日）
df = pd.DataFrame({'date': pd.to_datetime(dates)})
df['week'] = df['date'].dt.isocalendar().week.astype(int)
df['year'] = df['date'].dt.year
weekly = df.groupby(['year', 'week']).last().reset_index()
sample_dates = sorted(weekly['date'].dt.strftime('%Y-%m-%d').tolist())

# 加上关键日期
key_dates = [
    '2015-06-12', '2015-07-09', '2018-10-18', '2018-12-28',
    '2020-02-03', '2020-07-09', '2021-02-18', '2022-04-26',
    '2024-09-30', '2024-10-08', '2025-09-11', '2026-01-12',
]
all_dates = sorted(set(sample_dates + key_dates))
logger.info(f"周频采样: {len(sample_dates)}天 + {len(key_dates)}关键日期 = {len(all_dates)}天")

# ── 计算 ──────────────────────────────────────────────────────────────────
t_start = time.time()
results = []
failed = 0

for i, td in enumerate(all_dates):
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
            logger.warning(f"{td}: composite_score is None")
    except Exception as e:
        failed += 1
        logger.error(f"{td}: {str(e)[:80]}")

    if (i + 1) % 100 == 0:
        elapsed = time.time() - t_start
        rate = (i + 1) / elapsed
        remaining = (len(all_dates) - i - 1) / rate
        logger.info(f"进度: {i+1}/{len(all_dates)} ({len(results)}成功/{failed}失败) "
                    f"[{elapsed:.0f}s, 剩余{remaining:.0f}s]")

elapsed = time.time() - t_start
logger.info(f"完成: {len(results)}成功, {failed}失败, 耗时{elapsed:.0f}s ({elapsed/60:.1f}分钟)")

# ── 保存 ──────────────────────────────────────────────────────────────────
DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'web', 'data')

# history.json (精简版，用于日报)
with open(os.path.join(DATA_DIR, 'history.json'), 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False)
logger.info(f"history.json: {len(results)}条, {os.path.getsize(os.path.join(DATA_DIR, 'history.json'))//1024}KB")

# 统计
scores = [r['composite_score'] for r in results]
red = sum(1 for s in scores if s >= 70)
yellow = sum(1 for s in scores if 40 <= s < 70)
green = sum(1 for s in scores if s < 40)
max_s = max(scores)
min_s = min(scores)
avg_s = sum(scores) / len(scores)
logger.info(f"统计: 均值{avg_s:.1f} | 最高{max_s} | 最低{min_s} | 🔴{red} 🟡{yellow} 🟢{green}")
