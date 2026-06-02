#!/usr/bin/env python3
"""
全量历史回测：计算 2015-01-05 至今每个交易日的热度指数
输出: web/data/history_full.json + 历史走势图
"""
import sys, os, json, time, logging
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("backfill_history.log", encoding="utf-8")]
)
logger = logging.getLogger(__name__)

from src.data.database import init_database, DB_PATH
from src.indicators.calculator import calculate_heat_index

# ── 获取全部交易日 ──────────────────────────────────────────────────────────
import sqlite3
conn = sqlite3.connect(DB_PATH)
# 从 stock_daily 获取所有交易日（有 PE/PB 数据的日子）
rows = conn.execute("""
    SELECT DISTINCT trade_date FROM stock_daily 
    WHERE peTTM IS NOT NULL AND peTTM > 0
    ORDER BY trade_date
""").fetchall()
all_dates = [r[0] for r in rows]
conn.close()

logger.info(f"共 {len(all_dates)} 个交易日有 PE/PB 数据")

# ── 已有结果 (断点续传) ─────────────────────────────────────────────────────
HISTORY_FILE = os.path.join(os.path.dirname(__file__), '..', 'web', 'data', 'history_full.json')
existing = {}
if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE) as f:
        for item in json.load(f):
            existing[item['trade_date']] = item
logger.info(f"已有 {len(existing)} 个交易日计算结果")

# ── 逐日计算 ────────────────────────────────────────────────────────────────
results = []
skipped = 0
failed = 0
t_start = time.time()

for i, td in enumerate(all_dates):
    if td in existing:
        skipped += 1
        continue

    try:
        res = calculate_heat_index(trade_date=td)
        if res and res.get('composite_score') is not None:
            # 精简存储
            item = {
                'trade_date': td,
                'composite_score': round(float(res['composite_score']), 1),
                'level': res.get('level', 'unknown'),
                'dimensions': {}
            }
            for k in ['valuation', 'fund', 'sentiment', 'technical', 'structure']:
                v = res.get(f'dim_{k}')
                item['dimensions'][k] = round(float(v), 1) if v is not None else None
            existing[td] = item
            results.append(item)
        else:
            failed += 1
            logger.warning(f"{td}: composite_score is None")
    except Exception as e:
        failed += 1
        logger.error(f"{td}: {str(e)[:80]}")

    # 每 100 天保存一次
    if (i + 1) % 100 == 0:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(sorted(existing.values(), key=lambda x: x['trade_date']), f, ensure_ascii=False)
        elapsed = time.time() - t_start
        rate = (i + 1) / elapsed
        remaining = (len(all_dates) - i - 1) / rate
        logger.info(f"进度: {i+1}/{len(all_dates)} ({len(results)}新计算, {skipped}跳过, {failed}失败) "
                     f"[{elapsed:.0f}s, 预计剩余 {remaining:.0f}s]")

# 最终保存
with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
    json.dump(sorted(existing.values(), key=lambda x: x['trade_date']), f, ensure_ascii=False)

elapsed = time.time() - t_start
logger.info(f"完成! 总计 {len(existing)} 个交易日, 新计算 {len(results)}, 跳过 {skipped}, 失败 {failed}, 耗时 {elapsed:.0f}s")

# ── 更新 history.json (供日报使用) ──────────────────────────────────────────
history_out = os.path.join(os.path.dirname(__file__), '..', 'web', 'data', 'history.json')
with open(history_out, 'w', encoding='utf-8') as f:
    json.dump(sorted(existing.values(), key=lambda x: x['trade_date']), f, ensure_ascii=False)
logger.info(f"history.json 已更新: {len(existing)} 条")
