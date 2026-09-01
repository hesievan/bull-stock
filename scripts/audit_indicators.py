"""审计 V2 引擎每个指标的原始值真实性与底层表一致性"""

import sys, logging

sys.path.insert(0, "/Users/hesi/bull-market-heat-index")
logging.disable(logging.CRITICAL)

import sqlite3
from src.indicators.heat_index_v2 import compute_index_v2
from src.data.database import DB_PATH

TD = "2026-08-11"
conn = sqlite3.connect(DB_PATH)

# 1) 引擎实时值
res = compute_index_v2(trade_date=TD, db_path=DB_PATH)
raw = res["indicator_raw"]
scores = res["indicators"]

print("=" * 72)
print(f"引擎综合分={res['composite_score']}")
print("=" * 72)
print(f"{'指标':<16}{'引擎原始值':<16}{'底层表原始值':<16}{'得分':<7}一致?")
print("-" * 72)


def g(sql):
    r = conn.execute(sql, (TD,)).fetchone()
    return r[0] if r else None


checks = {}

# PE: index_daily_pe.pe_med
pe = g("SELECT pe_med FROM index_daily_pe WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1")
checks["pe"] = (raw.get("pe"), pe)

# buffett: 引擎自己算 stock_market_cap.total_mv / gdp; 直接读 stock_market_cap
buf_mv = g("SELECT total_mv FROM stock_market_cap WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1")
checks["buffett"] = (raw.get("buffett"), buf_mv)  # buffett raw 是比值, mv 是市值, 跳过直接比

# margin_ratio_v2: 引擎算 rzrqye/circ_mv; 读两表
m_mv = g("SELECT rzye+rqye FROM margin_history WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1")
c_mv = g("SELECT total_circ_mv FROM daily_circ_mv WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1")
checks["margin_ratio_v2"] = (raw.get("margin_ratio_v2"), (m_mv, c_mv))

# yield_spread: bond 10Y-2Y
y10 = conn.execute("SELECT yield_rate FROM bond_yield WHERE trade_date=? AND curve_term=10.0", (TD,)).fetchone()
y2 = conn.execute("SELECT yield_rate FROM bond_yield WHERE trade_date=? AND curve_term=2.0", (TD,)).fetchone()
checks["yield_spread"] = (raw.get("yield_spread"), (y10[0] if y10 else None, y2[0] if y2 else None))

# m1_m2_spread: m1_yoy - m2_yoy
m1 = g("SELECT m1_yoy FROM m1_monthly WHERE month<=? ORDER BY month DESC LIMIT 1")
m2 = g("SELECT m2_yoy FROM m2_monthly WHERE month<=? ORDER BY month DESC LIMIT 1")
checks["m1_m2_spread"] = (raw.get("m1_m2_spread"), (m1, m2))

# seal_rate: daily_seal_rate.seal_rate
sr = g("SELECT seal_rate FROM daily_seal_rate WHERE trade_date=?")
checks["seal_rate"] = (raw.get("seal_rate"), sr)

# turnover_m2: amount_sum / m2; 读
amt2 = g("SELECT SUM(amount) FROM stock_daily WHERE trade_date=? AND amount>0")
m2b = g("SELECT m2_billion FROM m2_monthly WHERE month<=? ORDER BY month DESC LIMIT 1")
checks["turnover_m2"] = (raw.get("turnover_m2"), (amt2, m2b))

# turnover: daily_turnover.turnover_rate
tr = g("SELECT turnover_rate FROM daily_turnover WHERE trade_date=?")
checks["turnover"] = (raw.get("turnover"), tr)

# new_high: daily_new_high.new_high_ratio
nh = g("SELECT new_high_ratio FROM daily_new_high WHERE trade_date=?")
checks["new_high"] = (raw.get("new_high"), nh)

# ma_alignment: daily_ma_alignment.ma_alignment_ratio
ma = g("SELECT ma_alignment_ratio FROM daily_ma_alignment WHERE trade_date=?")
checks["ma_alignment"] = (raw.get("ma_alignment"), ma)

# southbound: daily_hsgt_south.south_net (P1)
sbv = g("SELECT south_net FROM daily_hsgt_south WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1")
checks["southbound"] = (raw.get("southbound"), sbv)

# futures_discount: daily_futures_basis.basis_rate (P1)
fbv = g("SELECT basis_rate FROM daily_futures_basis WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1")
checks["futures_discount"] = (raw.get("futures_discount"), fbv)

# breadth: daily_updown.up_down_ratio (P1)
bdv = g("SELECT up_down_ratio FROM daily_updown WHERE trade_date<=? ORDER BY trade_date DESC LIMIT 1")
checks["breadth"] = (raw.get("breadth"), bdv)

# amplitude: 沪深300 (high-low)/prev_close (P3)
import math
import numpy as np
import pandas as pd

_amp_df = pd.read_sql(
    "SELECT trade_date, high, low, close FROM index_daily"
    " WHERE index_code='sh000300' AND high>0 AND low>0 AND close>0 AND trade_date<=? ORDER BY trade_date",
    conn,
    params=(TD,),
)
_amp_df["prev_close"] = _amp_df["close"].shift(1)
_amp_cur = float(((_amp_df["high"] - _amp_df["low"]) / _amp_df["prev_close"]).dropna().iloc[-1])
checks["amplitude"] = (raw.get("amplitude"), _amp_cur)

# realized_vol: 沪深300 20日对数收益std ×√250 (P3)
_vol_df = pd.read_sql(
    "SELECT trade_date, close FROM index_daily"
    " WHERE index_code='sh000300' AND close>0 AND trade_date<=? ORDER BY trade_date",
    conn,
    params=(TD,),
)
_vol_ret = np.log(_vol_df["close"]).diff()
_vol_cur = float((_vol_ret.rolling(20).std() * math.sqrt(250)).dropna().iloc[-1])
checks["realized_vol"] = (raw.get("realized_vol"), _vol_cur)

# margin_buy_ratio: rzmre / (turnover_rate × circ_mv × 100) (P3)
_mb_row = conn.execute(
    """
    SELECT m.rzmre / (t.turnover_rate * c.total_circ_mv * 100)
    FROM margin_history m
    JOIN daily_turnover t ON m.trade_date = t.trade_date AND t.turnover_rate > 0
    JOIN daily_circ_mv c ON m.trade_date = c.trade_date AND c.total_circ_mv > 0
    WHERE m.rzmre > 0 AND m.trade_date <= ? ORDER BY m.trade_date DESC LIMIT 1
    """,
    (TD,),
).fetchone()
checks["margin_buy_ratio"] = (raw.get("margin_buy_ratio"), _mb_row[0] if _mb_row else None)


# 打印对比
def fmt(v):
    if v is None:
        return "None"
    if isinstance(v, tuple):
        return " / ".join(f"{x:.4f}" if isinstance(x, (int, float)) else str(x) for x in v)
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


for k in [
    "pe",
    "buffett",
    "margin_ratio_v2",
    "yield_spread",
    "m1_m2_spread",
    "southbound",
    "seal_rate",
    "turnover_m2",
    "turnover",
    "futures_discount",
    "new_high",
    "ma_alignment",
    "breadth",
    "amplitude",
    "realized_vol",
    "margin_buy_ratio",
]:
    eng = raw.get(k)
    print(f"{k:<16}{fmt(eng):<16}{fmt(checks[k][1]):<16}{scores.get(k):<7}")

print()
print("=" * 72)
print("独立复算验证 (用底层表重算各指标原始值, 与引擎对比):")
print("=" * 72)

# margin_ratio_v2 复算
if m_mv and c_mv:
    print(
        f"margin_ratio_v2: 引擎={raw['margin_ratio_v2']:.4%}  复算={m_mv / c_mv:.4%}  两融余额={m_mv / 1e8:.0f}亿 流通市值={c_mv / 1e8:.0f}亿"
    )
# yield_spread 复算
if y10 and y2:
    print(f"yield_spread: 引擎={raw['yield_spread']:.4f}  复算10Y-2Y={y10[0] - y2[0]:.4f}  (10Y={y10[0]}, 2Y={y2[0]})")
# m1_m2 复算
if m1 is not None and m2 is not None:
    print(f"m1_m2_spread: 引擎={raw['m1_m2_spread']:.4%}  复算={m1 - m2:.4%}  (m1_yoy={m1}, m2_yoy={m2})")
# turnover_m2 复算: amount(千元) -> 元, m2(亿元) -> 元
if amt2 and m2b:
    amt_yuan = amt2 * 1000
    m2_yuan = m2b * 1e8
    print(
        f"turnover_m2: 引擎={raw['turnover_m2']:.4%}  复算=amount/m2={amt_yuan / m2_yuan:.4%}  (成交={amt2 / 1e8:.0f}亿千元? m2={m2b}亿)"
    )
# buffett 复算
gdp_all = conn.execute("SELECT quarter,gdp FROM gdp_quarterly WHERE gdp IS NOT NULL").fetchall()
yrs = {}
for q, g_ in gdp_all:
    yrs[int(q[:4])] = yrs.get(int(q[:4]), 0) + g_
cur_gdp = yrs.get(2025, 0) * 1e8
print(
    f"buffett: 引擎={raw['buffett']:.4%}  总市值={buf_mv * 1e4 / 1e12:.2f}万亿 GDP(2025)={cur_gdp / 1e12:.2f}万亿 比值={buf_mv * 1e4 / cur_gdp:.4%}"
)

# 范围/缺失检查: 每个历史表
print()
print("=" * 72)
print("各指标历史表 范围与缺失检查:")
print("=" * 72)
range_checks = [
    (
        "pe",
        "SELECT MIN(pe_med),MAX(pe_med),COUNT(*),SUM(pe_med IS NULL) FROM index_daily_pe WHERE trade_date>='2015-01-01'",
    ),
    ("buffett(总市值)", "SELECT MIN(total_mv),MAX(total_mv),COUNT(*) FROM stock_market_cap"),
    ("seal_rate", "SELECT MIN(seal_rate),MAX(seal_rate),COUNT(*),SUM(seal_rate IS NULL) FROM daily_seal_rate"),
    (
        "turnover",
        "SELECT MIN(turnover_rate),MAX(turnover_rate),COUNT(*),SUM(turnover_rate IS NULL) FROM daily_turnover",
    ),
    (
        "new_high",
        "SELECT MIN(new_high_ratio),MAX(new_high_ratio),COUNT(*),SUM(new_high_ratio IS NULL) FROM daily_new_high",
    ),
    (
        "ma_alignment",
        "SELECT MIN(ma_alignment_ratio),MAX(ma_alignment_ratio),COUNT(*),SUM(ma_alignment_ratio IS NULL) FROM daily_ma_alignment",
    ),
    ("bond_yield", "SELECT MIN(yield_rate),MAX(yield_rate),COUNT(*),SUM(yield_rate IS NULL) FROM bond_yield"),
    ("north_net", "SELECT MIN(north_net),MAX(north_net),COUNT(*) FROM northbound_history"),
    ("margin(rzrqye)", "SELECT MIN(rzye+rqye),MAX(rzye+rqye),COUNT(*) FROM margin_history"),
]
# 北向单位修复连续性: 2024-08-15~09-05 的 north_net 是否平滑
print()
print("北向 north_net 单位修复连续性 (2024-08-15~09-05, 百万元):")
for r in conn.execute(
    "SELECT trade_date, north_net FROM northbound_history WHERE trade_date BETWEEN '2024-08-15' AND '2024-09-05' ORDER BY trade_date"
):
    bar = "#" * int(abs(r[1]) / 200) if r[1] is not None else "?"
    print(f"  {r[0]}: {r[1]:>10.1f}  {bar}")
for name, sql in range_checks:
    r = conn.execute(sql).fetchone()
    print(f"  {name:<22} min={r[0]}  max={r[1]}  n={r[2]}" + (f"  null={r[3]}" if len(r) > 3 else ""))

conn.close()
