# 指标计算逻辑审计报告 v3.8

> 审计范围：19 个子指标 × 6 维度，含计算逻辑、数据管道、数据覆盖
> 审计日期：2026-06-20，数据库快照截至 2026-06-18
> 项目版本: v3.8

---

## 审计结论

**83 测试全部通过，6 维度 19 指标均可正常计算。**

发现的 7 个问题中：
- **P0 × 2** → 已修复（MA排列百分位、情绪 live fallback）
- **P1 × 3** → 已修复（_pct_rank ≤、AH溢价双源、daily_circ_mv）
- **P2 × 1** → 已修复（背离惩罚日志）
- **待跟进 × 1** → 预计算表 pipeline（v3.9）

---

## 数据覆盖概览

| 表 | 最晚日期 | 指标 | 修复后状态 |
|---|---|---|---|
| `stock_daily` | 06-18 | 换手率/创新高/破净率/涨跌比/涨停 | ✅ 实时计算 |
| `index_daily_pe` | 06-18 (每日更新) | PE/PB中位数/ERP | ✅ Step S25 |
| `bond_yield` | 06-12 | ERP | ✅ fallback 实时 |
| `ah_premium_monthly` | 2026-06 | AH溢价 | ✅ 优先读取 |
| `ah_premium` (日) | 06-19 | AH溢价 fallback | ✅ 月表无数据时 |
| `daily_macro` | 06-10 | M1-M2/M2 | ✅ ≤模糊匹配 |
| `daily_updown` | 06-10 | 涨跌家数比 | ✅ fallback 实时 |
| `daily_limit` | 06-10 | 涨停占比/涨跌停比 | ✅ fallback 实时 |
| `daily_ma_alignment` | 06-10 | MA排列比 | ✅ 百分位评分 |
| `qvix_daily` | 06-12 | QVIX | ⚠️ 需外部API |
| `daily_circ_mv` | 06-03 | 融资余额比 | ✅ Step S26新增 |

---

## P0 已修复

### 1. MA排列比分数非百分位

**文件**: `src/indicators/technical.py:29`
**修复**: `score = today[0] * 100` → `_pct_rank(hist_r, today[0]) * 100`

修复前：MA 排列比 27% → 绝对分 27
修复后：MA 排列比 27% → 历史百分位 ~62

### 2. 预计算表陈旧导致情绪指标返回 None

**文件**: `src/indicators/sentiment.py:45-175`
**修复**: 三个指标增加 live fallback，从 `stock_daily` 实时计算

- `calc_up_down_ratio`: 从 `stock_daily.pct_change` 计算涨跌比
- `calc_limit_up_ratio`: 从 `stock_daily.pct_change ≥ 9.9` 计算涨停占比
- `calc_limit_ratio`: 从 `stock_daily.pct_change` 计算涨跌停比

---

## P1 已修复

### 3. `_pct_rank` 使用 `<` 而非 `<=`

**文件**: `src/indicators/utils.py:41`
**修复**: `(clean < value).sum()` → `(clean <= value).sum()`

消除当前值等于历史值时被低估的系统性偏差。

### 4. AH溢价无 daily fallback

**文件**: `src/indicators/structure.py:43-77`
**修复**: `ah_premium_monthly` 无数据时从 `ah_premium` 日表聚合并计算百分位。

### 5. daily_circ_mv 不更新导致融资余额比陈数据

**文件**: `src/data/database.py` + `scripts/run_daily.py`
**修复**: 新增 `compute_daily_circ_mv()` 函数 + Step S26_circ_mv，在 stock_daily 更新后自动汇总。

---

## P2 已修复

### 6. 背离惩罚日志原值不准确

**文件**: `src/indicators/sentiment.py:173-178`
**修复**: 保存 `s1_orig` 变量，避免 penalty floor 触发后日志显示错误原值。

---

## 待跟进 (v3.9+)

### 7. 预计算表 pipeline

将 `daily_updown`、`daily_limit`、`daily_ma_alignment` 等预计算表的更新正式纳入 `run_daily.py`，替代临时 fallback 方案。目前 fallback 能正常工作，但预计算表 pipeline 是更优方案。

---

## 修复汇总

| 文件 | 变更行数 | 效果 |
|---|---|---|
| `src/indicators/technical.py` | 2 | MA排列百分位评分 |
| `src/indicators/utils.py` | 1 | _pct_rank ≤ |
| `src/indicators/sentiment.py` | 60 | 3个指标live fallback + 日志修复 |
| `src/indicators/structure.py` | 30 | AH溢价双源 fallback |
| `src/data/database.py` | 55 | schema新增 table + compute_circ_mv + aggregate_monthly |
| `scripts/run_daily.py` | 12 | Step S26_circ_mv |
| `tests/test_calculator.py` | 4 | 同步 _pct_rank 测试预期 |
| `BUG_REPORT.md` | — | 保留v3.5旧报告 |
| `AUDIT_v3.8.md` | 新建 | 本次审计报告 |
