#!/usr/bin/env python3
"""
回填所有历史日期的指数过热评分，生成 web/data/index_heat_history.json
供前端绘制6大指数历史趋势折线图。

用法:
  python scripts/backfill_index_heat_history.py
"""
import sys, os, json, logging, sqlite3
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "heat_index.db")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "web", "data")

# 与 index_heat.py 一致
TARGET_INDICES = {
    "sh000300": "沪深300",
    "sz399006": "创业板指",
    "sh000688": "科创50",
    "bj899050": "北证50",
    "sh000510": "中证A500",
    "sh000852": "中证1000",
}
SHORT_HISTORY = {"bj899050", "sh000510"}
INDEX_CODE_TO_TS = {
    "sh000300": "000300.SH",
    "sz399006": "399006.SZ",
    "sh000688": "000688.SH",
    "bj899050": "899050.BJ",
    "sh000510": "000510.SH",
    "sh000852": "000852.SH",
}


def _pct_rank(series, value):
    if len(series) < 10:
        return 0.5
    return (series < value).sum() / len(series)


def compute_scores_for_date(conn, trade_date):
    """对单个交易日计算所有6大指数的 composite_score。"""
    results = {}
    for ak_code, name in TARGET_INDICES.items():
        try:
            score = _compute_single(conn, ak_code, trade_date)
            if score is not None:
                results[ak_code] = round(score, 1)
        except Exception as e:
            logger.debug("  %s %s failed: %s", name, ak_code, e)
    return results


def _compute_single(conn, ak_code, trade_date):
    """对单个指数/日期计算 composite_score（简化版，只算有数据的维度）。"""
    # 指数日线
    idx_df = pd.read_sql(
        "SELECT * FROM index_daily WHERE index_code=? AND trade_date<=? ORDER BY trade_date",
        conn, params=(ak_code, trade_date)
    )
    if len(idx_df) < 200:
        return None

    close = pd.to_numeric(idx_df["close"], errors="coerce").dropna().values
    if len(close) < 200:
        return None

    tech_scores = []

    # MA250 偏离分位
    ma250 = pd.Series(close).rolling(250).mean().dropna().values
    if len(ma250) > 200:
        dev = (close[-len(ma250):] / ma250 - 1) * 100
        dev_clean = dev[~pd.isna(dev)]
        if len(dev_clean) > 100:
            current_dev = dev_clean[-1]
            tech_scores.append(_pct_rank(pd.Series(dev_clean), current_dev) * 100)

    is_short = ak_code in SHORT_HISTORY

    if is_short:
        # 相对强度 vs 沪深300
        bm = pd.read_sql(
            "SELECT trade_date, close FROM index_daily WHERE index_code='sh000300' AND trade_date<=? ORDER BY trade_date",
            conn, params=(trade_date,)
        )
        if not bm.empty:
            merged = pd.merge(
                idx_df[["trade_date", "close"]],
                bm[["trade_date", "close"]],
                on="trade_date", suffixes=("_idx", "_bm")
            )
            if len(merged) > 60:
                idx_ret = pd.to_numeric(merged["close_idx"], errors="coerce").pct_change()
                bm_ret = pd.to_numeric(merged["close_bm"], errors="coerce").pct_change()
                excess = idx_ret - bm_ret
                cum_excess = excess.rolling(60).sum().dropna()
                if len(cum_excess) > 20:
                    rs = _pct_rank(cum_excess, cum_excess.iloc[-1]) * 100
                    # 连续跑赢惩罚
                    n_beat = 0
                    for r in excess.iloc[-min(20, len(excess)):]:
                        if r > 0: n_beat += 1
                        else: n_beat = 0
                    tech_scores.append(min(rs + min(n_beat * 1.5, 15), 100))
    else:
        for days in [20, 60, 120]:
            if len(close) > days * 2:
                pct = pd.Series(close).pct_change(days).dropna() * 100
                if len(pct) > days:
                    current = pct.iloc[-1]
                    tech_scores.append(_pct_rank(pct, current) * 100)

    # PE分位
    val_scores = []
    ts_code = INDEX_CODE_TO_TS.get(ak_code)
    if ts_code:
        pe_df = pd.read_sql(
            "SELECT trade_date, pe_ttm, pb FROM index_pe_history WHERE index_code=? AND trade_date<=? ORDER BY trade_date",
            conn, params=(ak_code, trade_date)
        )
        if not pe_df.empty and len(pe_df) > 60:
            pe = pd.to_numeric(pe_df["pe_ttm"], errors="coerce").dropna()
            if len(pe) > 60:
                pe_score = _pct_rank(pe, pe.iloc[-1]) * 100
                val_scores.append(pe_score)
                pb = pd.to_numeric(pe_df["pb"], errors="coerce").dropna()
                if len(pb) > 60:
                    val_scores.append(_pct_rank(pb, pb.iloc[-1]) * 100)

    tech_avg = sum(tech_scores) / len(tech_scores) if tech_scores else None
    val_avg = sum(val_scores) / len(val_scores) if val_scores else None

    if tech_avg is not None and val_avg is not None:
        return tech_avg * 0.5 + val_avg * 0.5
    elif tech_avg is not None:
        return tech_avg
    elif val_avg is not None:
        return val_avg
    return None


def main():
    logger.info("Backfilling index heat history...")
    conn = sqlite3.connect(DB_PATH)

    # 获取所有有 index_daily 数据的交易日
    dates = pd.read_sql(
        "SELECT DISTINCT trade_date FROM index_daily WHERE index_code='sh000300' ORDER BY trade_date",
        conn
    )["trade_date"].tolist()
    logger.info("Total trading dates: %d", len(dates))

    # 检查已有数据
    out_path = os.path.join(DATA_DIR, "index_heat_history.json")
    existing = {}
    if os.path.exists(out_path):
        try:
            with open(out_path) as f:
                existing = json.load(f)
            logger.info("Existing records: %d dates", len(existing))
        except Exception:
            existing = {}

    result = dict(existing)
    count = 0
    skip = 0

    for td in dates:
        if td in result:
            skip += 1
            continue
        scores = compute_scores_for_date(conn, td)
        if scores:
            result[td] = scores
            count += 1
        if count % 100 == 0 and count > 0:
            logger.info("  %d dates processed (last=%s), saving checkpoint...", count, td)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False)

    conn.close()

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)

    logger.info("Done: %d new, %d skipped, %d total dates", count, skip, len(result))
    logger.info("Output: %s (%d KB)", out_path, os.path.getsize(out_path) // 1024)


if __name__ == "__main__":
    main()
