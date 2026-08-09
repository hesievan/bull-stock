# 牛市热度指数项目代码审查报告

> 审查日期：2026-08-09  
> 审查范围：src/ 全部核心代码、config/、scripts/run_daily.py  
> 版本：v3.19（V2 引擎）

---

## 一、项目概览

本项目是一个 **A 股牛市热度指数**量化工具，v3.19。V2 引擎使用 **4 维度 9 核心指标 + CFFEX 恐慌指数**，通过 10 年历史百分位法对每个指标打分，再按权重合成综合热度分（0–100）。

定位：**仅提示离场/减仓，不发出进场信号。**

### V2 指标体系

| 维度 | 指标 | 权重 |
|------|------|------|
| 估值 | PE 中位数（hs300+zz500） | 14% |
| 估值 | 巴菲特指标（总市值/GDP） | 13% |
| 估值 | 股权风险溢价（ERP） | 13% |
| 流动性 | 换手率（自由流通市值加权） | 12% |
| 流动性 | 融资余额/流通市值 | 11% |
| 流动性 | MA 均线排列占比 | 11% |
| 结构 | 创新高个股占比 | 10% |
| 结构 | 行业估值热度 | 8% |
| 情绪 | 恐慌指数 CFFEX iVIX | 8% |

---

## 二、核心逻辑问题

### 🔴 问题 1：创新高占比使用绝对评分，而非历史百分位

**位置**：`src/indicators/heat_index_v2.py`，`calc_new_high_v2()` L536–539

**问题描述**：所有其他 8 个指标都使用 10 年历史百分位打分，唯独创新高占比直接 `ratio * 100`。A 股中个股创新高占比的正常范围是 0%–15%，导致该指标绝大多数时间得分不超过 15 分，在 2015 年那种极端牛市中也无法打出高分，系统性低估了结构维度的热度信号。

```python
# 当前代码
ratio = new_high / len(merged)
score = ratio * 100  # ❌ 绝对评分
```

**解决方案**：改为 10 年历史百分位法，与其他指标保持一致。

```python
# 修复方案
ratio = new_high / len(merged) if len(merged) > 0 else 0

# 从数据库查询历史新高占比序列
hist_sql = """SELECT trade_date, 
    SUM(CASE WHEN close >= max_close * :threshold THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS ratio
    FROM daily_prices
    WHERE trade_date >= :start_date
    GROUP BY trade_date"""
hist = pd.read_sql(hist_sql, conn, params={"threshold": NEW_HIGH_THRESHOLD, "start_date": ten_years_ago})

score = _pct_rank(hist["ratio"].dropna().values, ratio) * 100
```

**预期效果**：牛市顶部区域创新高占比达到历史极端值时能打出 80+ 的高分，结构维度信号强度回归正常。

---

### 🔴 问题 2：情绪背离惩罚存在双重扣分 bug

**位置**：`src/indicators/heat_index_v2.py`，`_apply_sentiment_divergence()` L773–781

**问题描述**：`penalty_factor = 0.2`，循环对 `turnover_m2` 和 `turnover` 两个指标各扣 20 分，实际惩罚 40 分。README 明确说"情绪得分扣减最多 20 分"——实际是文档描述的 2 倍。

```python
# 当前代码
penalty = DIVERGENCE_CONFIG["penalty_factor"]  # 0.2
for key in ("turnover_m2", "turnover"):  # ⚠️ 两个指标各扣20分 = 总40分
    if sentiment_scores.get(key) is not None:
        sentiment_scores[key] = max(0, sentiment_scores[key] - penalty * 100)
```

**解决方案**：对齐文档，两种修复方式可选。

**方案 A（推荐）**：只对流动性维度整体扣一次。

```python
# 对流动性维度的综合分扣一次
total_liquidity_penalty = DIVERGENCE_CONFIG["penalty_factor"] * 100  # 20
# 按权重分配扣减
liquidity_indicators = ["turnover", "margin_ratio", "ma_alignment"]
total_weight = sum(INDICATOR_WEIGHTS[k] for k in liquidity_indicators if k in sentiment_scores)
for key in liquidity_indicators:
    if sentiment_scores.get(key) is not None:
        share = INDICATOR_WEIGHTS.get(key, 0) / total_weight if total_weight > 0 else 0
        sentiment_scores[key] = max(0, sentiment_scores[key] - total_liquidity_penalty * share)
```

**方案 B**：只扣换手率（与文档描述"量价背离"一致），移除 turnover_m2 的扣减。

```python
for key in ("turnover",):  # 只扣一个指标
    if sentiment_scores.get(key) is not None:
        sentiment_scores[key] = max(0, sentiment_scores[key] - penalty * 100)
```

**预期效果**：惩罚总额与文档一致（20 分），避免信号失真。

---

### 🟠 问题 3：换手率使用 6 个月窗口百分位，与文档矛盾

**位置**：`src/indicators/heat_index_v2.py`，`calc_turnover_v2()` L460–461

**问题描述**：代码使用近 6 个月窗口计算历史百分位，但 README 和文档均声称所有指标使用"10 年历史百分位"。6 个月窗口是短期相对活跃度，在长期牛市中会逐渐适应高换手率（漂移基准），导致高换手率得分反而下降。

```python
# 当前代码
six_mo_ago = (pd.Timestamp(td) - pd.DateOffset(months=6)).strftime("%Y-%m-%d")
hist = pd.read_sql("... WHERE trade_date >= ? ...", params=(six_mo_ago, td))
```

**解决方案**：改为 10 年窗口，与 PE、ERP、融资等指标一致。

```python
# 修复方案
ten_years_ago = (pd.Timestamp(td) - pd.DateOffset(years=10)).strftime("%Y-%m-%d")
hist = pd.read_sql(
    """SELECT trade_date, turnover_rate 
       FROM index_daily 
       WHERE index_code = '000985' AND trade_date >= ? AND trade_date <= ?""",
    conn, params=(ten_years_ago, td)
)
score = _pct_rank(hist["turnover_rate"].values, cur_turnover) * 100
```

> **注意**：如果担心 10 年前 A 股换手率中枢与当前不可比，可改用 5 年窗口并在文档中统一标注。核心原则是与文档一致。

**预期效果**：换手率信号在牛市后期不会被漂移基准压低，与"越高越危险"的设计理念一致。

---

### 🟠 问题 4：两融余额市值比的高分位倒 V 递减过于陡峭

**位置**：`src/indicators/heat_index_v2.py`，`calc_margin_ratio_v2()` L322–326

**问题描述**：融资余额从历史 90% 分位涨到 95% 分位，分数从 90 暴跌到 45——这是一种"反直觉的信号反转"：杠杆资金继续涌入 → 热度指数反而下降。对于牛市顶部预警工具，所有指标应该单调递增。

```python
# 当前代码
if pct > 0.9:
    score = 900 * (1 - pct)  # 线性递减，pct=0.95 → 45分
```

**解决方案**：使用平滑的饱和函数，使信号单调递增但趋于平缓，不再骤降。

```python
# 方案 A：sigmoid 变体（推荐）
import math
if pct <= 0.85:
    score = pct * 100
else:
    # 0.85 → 85, 0.90 → 92, 0.95 → 95, 0.99 → 98
    saturation = 15  # 额外可用的饱和度
    adjusted = 0.85 + saturation * (1 - math.exp(-(pct - 0.85) * 20))
    score = adjusted * 100

# 方案 B：简单封顶（更保守）
score = min(pct * 100, 95)  # pct>0.95 后分数不再增长，也不下降
```

**预期效果**：融资余额突破历史极值后，热度分保持 95 分附近的高位不再下降，信号更符合直觉。

---

### 🟡 问题 5：PE 中位数 n_stocks 过滤范围过宽

**位置**：`src/indicators/heat_index_v2.py`，`calc_pe()` L123–124

**问题描述**：`n_stocks.between(cur_n * 0.2, cur_n * 3.0)` 意味着当前 n≈800 时保留 160–2400 的历史记录，几乎等于不过滤。早期只有沪深 300（n≈300）的 PE 中位数与当前 hs300+zz500（n≈800）不可比。

```python
# 当前代码
hist = hist[hist["n_stocks"].between(cur_n * 0.2, cur_n * 3.0)]
```

**解决方案**：收紧范围，或者按 n_stocks 分层计算。

```python
# 方案 A：收紧到 ±50%（推荐）
hist = hist[hist["n_stocks"].between(cur_n * 0.5, cur_n * 1.5)]

# 方案 B：分层
if cur_n < 500:      # 早期：仅 hs300 成分
    hist = hist[hist["n_stocks"] < 500]
else:                 # 当前：hs300+zz500
    hist = hist[hist["n_stocks"] > 500]
```

> **注意**：收紧范围后需验证 PE 百分位历史序列是否出现断档（无数据的日期段），如有断档需用插值补充。

**预期效果**：PE 中位数的历史百分位在不同成分股范围下具有可比性。

---

### 🟡 问题 6：MA 均线排列 fallback 时使用绝对评分

**位置**：`src/indicators/heat_index_v2.py`，`calc_ma_alignment_v2()` L570–572

**问题描述**：当日无法从数据库查到历史 MA 排列数据时，fallback 逻辑直接用 `cur_val * 100` 给分，与百分位法不一致。

```python
# 当前代码
if len(hist_data) == 0:
    return cur_val * 100  # ❌ 不一致的评分方式
```

**解决方案**：fallback 时也尽可能使用百分位，或输出 warning。

```python
if len(hist_data) < 20:  # 数据太少不足以做百分位
    logger.warning("MA alignment: insufficient historical data (%d records), using adjusted fallback", len(hist_data))
    # 如果完全没有历史，至少用当前值映射到合理区间
    # MA 排列比通常在 20%–80%，可以直接映射
    return min(max(cur_val * 100, 20), 80)  # 收敛到 20-80 区间降低极端影响
```

**预期效果**：新指数运行初期（历史数据不足）不会产生异常高分。

---

## 三、架构与工程问题

### 🟠 问题 7：配置文件与 V2 引擎完全脱节

**位置**：`config/dev.yaml`、`config/prod.yaml`

**问题描述**：配置文件定义的是 V1 的 6 维度权重，V2 引擎的 4 维度 9 指标权重完全硬编码在 `INDICATOR_WEIGHTS` 字典中，修改权重需要改源码 + 重新运行 `assert sum(weights) == 1.0` 校验。

**影响**：无法通过配置文件灵活调参，且配置文件会误导新开发者。

**解决方案**：将 V2 配置收敛到 YAML。

```yaml
# config/prod.yaml 新增
v2_engine:
  weights:
    pe_median: 0.14
    buffett_index: 0.13
    erp: 0.13
    turnover: 0.12
    margin_ratio: 0.11
    ma_alignment: 0.11
    new_high_ratio: 0.10
    sector_heat: 0.08
    cffex_ivix: 0.08

  divergence:
    sentiment:
      penalty_factor: 0.2
      span_days: 60
    new_high:
      penalty_factor: 0.15
      lookback_days: 20
      divergence_threshold_pct: 20

  thresholds:
    clear_warning: 80
    mild_warning: 70
    normal_low: 30

  turnover:
    percentile_window_years: 10  # 从 0.5(6个月) 改为 10

  pe:
    n_stocks_filter_ratio: [0.5, 1.5]  # 从 [0.2, 3.0] 收紧

  margin:
    max_score: 95  # 两融得分封顶
```

代码侧添加配置加载：

```python
# heat_index_v2.py
with open("config/prod.yaml") as f:
    _cfg = yaml.safe_load(f)["v2_engine"]
INDICATOR_WEIGHTS = _cfg["weights"]
DIVERGENCE_CONFIG = _cfg["divergence"]
TURNOVER_WINDOW_YEARS = _cfg["turnover"]["percentile_window_years"]
```

**预期效果**：调参无需改代码，配置即文档。

---

### 🟡 问题 8：新高顶背离检测重复计算

**位置**：`src/indicators/heat_index_v2.py`，`_apply_new_high_divergence()` L787–855

**问题描述**：`calc_new_high_v2()` 刚算完全市场个股新高占比，`_apply_new_high_divergence()` 又独立重查当日和 20 天前所有股票 250 日最高价并重新计算。每次运行多耗费 2 次全量 SQL 查询。

**解决方案**：`calc_new_high_v2()` 返回历史序列供复用。

```python
def calc_new_high_v2(td: str, conn, return_history: bool = False):
    # ... 原有逻辑 ...
    if return_history:
        return score, hist_series  # 返回 (分数, 历史序列)
    return score

# _apply_new_high_divergence 中：
_, new_high_series = calc_new_high_v2(td, conn, return_history=True)
divergence = _detect_divergence(new_high_series, idx_series)
```

**预期效果**：每次运行减少 2 次全量数据库查询，耗时降低约 5–10 秒。

---

### 🟡 问题 9：巴菲特指标 GDP 数据缺失时静默失效

**位置**：`src/indicators/heat_index_v2.py`，`calc_buffett()` L234–239

**问题描述**：数据库中最新的 GDP 数据是 2024 年，但当前日期是 2026 年，`cur_year` 找不到数据时返回 None，且无任何日志提示。

```python
# 当前代码
cur_year = td_year - 1
while cur_year not in annual_gdp and cur_year > min(available_years):
    cur_year -= 1
if cur_year not in annual_gdp:
    return None  # ❌ 无日志
```

**解决方案**：增加 warning 日志。

```python
if cur_year not in annual_gdp:
    logger.warning(
        "Buffett index: no GDP data found for year %d or earlier. "
        "Available years: %s. Indicator will return None.",
        td_year - 1, sorted(available_years)
    )
    return None

# 额外：如果数据延迟超过1年，也提示
if (td_year - 1 - cur_year) > 0:
    logger.info("Buffett index: using GDP from year %d (latest available, %d year(s) behind)", 
                cur_year, td_year - 1 - cur_year)
```

**预期效果**：GDP 数据缺失时运维可及时发现并补数据。

---

### 🟡 问题 10：尺寸分与综合分的关系令人困惑

**位置**：`src/indicators/heat_index_v2.py` L700–718

**问题描述**：维度分是维度内等权平均，综合分是按 `INDICATOR_WEIGHTS` 逐指标加权。同一个维度的两个分数可能不同，表格展示容易产生误解。

**解决方案**：在表格输出中增加一行注释说明。

```markdown
| 维度 | 等权平均分 | 在综合分中实际贡献 |
|------|-----------|-------------------|
| 估值 | 65.3      | 估值占40%（PE 14% + 巴菲特 13% + ERP 13%，按权重贡献约26.1分） |
```

或在代码中直接改为维度分也按指标权重计算：

```python
# 修复：维度分也按指标权重加权
dim_total_weight = sum(INDICATOR_WEIGHTS[k] for k in dim_indicators if k in scores)
dim_scores[dim_name] = sum(scores[k] * INDICATOR_WEIGHTS[k] 
                           for k in dim_indicators if k in scores) / dim_total_weight
```

**预期效果**：维度分与综合分中对应维度的贡献一致，消除理解偏差。

---

## 四、其他小问题

| # | 问题 | 位置 | 严重度 |
|---|------|------|--------|
| 11 | `index_heat.py` 量能评分 `_calc_volume_score()` 标注不参与评分，仍在日志输出 | `index_heat.py` | 低 |
| 12 | 中证红利(sh000922) README 提到有 PE/PB 分位，代码未见特殊处理 | `index_heat.py` | 低 |
| 13 | `focus_industries.py` `_fetch_index_data()` 逐次调用 akshare，可合并 | L81–99 | 低 |
| 14 | V1 代码（calculator.py 及其子模块）未清理，83 个 V1 测试仍在跑 | `src/indicators/` | 低 |
| 15 | V2 引擎 9 个核心指标函数零单元测试 | `tests/` | 高 |

---

## 五、修复优先级总览

### 🔴 高优先级（直接影响判断准确性）

| 序号 | 问题 | 影响 | 修复难度 |
|------|------|------|----------|
| 1 | 创新高占比改用历史百分位 | 结构维度系统性低估 | 中 |
| 2 | 情绪背离双重扣分 bug | 惩罚翻倍，信号失真 | 低 |
| 15 | V2 引擎核心指标增加单元测试 | 重构无安全保障 | 中 |

### 🟠 中优先级（影响信号一致性与可维护性）

| 序号 | 问题 | 影响 |
|------|------|------|
| 3 | 换手率窗口从 6 个月改为 10 年 | 信号与设计理念一致 |
| 4 | 两融高分递减平滑化 | 避免反直觉信号 |
| 7 | 配置文件与 V2 引擎同步 | 消除维护隐患 |

### 🟡 低优先级（改善体验）

| 序号 | 问题 |
|------|------|
| 5 | PE n_stocks 过滤收紧 |
| 6 | MA fallback 百分位一致性 |
| 8 | 新高背离避免重复计算 |
| 9 | 巴菲特指标 GDP 缺失日志 |
| 10 | 维度分/综合分展示一致性 |
| 11–14 | 代码清理和性能优化 |

---

## 六、建议的修复路线图

### 阶段一：修复核心信号准确度（1–2 天）

1. 修复情绪背离双重扣分（问题 2）
2. 创新高占比改用历史百分位（问题 1）
3. 运行 `scripts/run_daily.py` 对比修复前后 2024–2025 牛市周期信号差异，验证改善

### 阶段二：信号一致性对齐（1 天）

4. 换手率窗口统一为 10 年（问题 3）
5. 两融饱和封顶（问题 4）
6. PE n_stocks 过滤收紧（问题 5）
7. MA fallback 标准化（问题 6）

### 阶段三：工程优化（1–2 天）

8. 配置文件与 V2 同步（问题 7）
9. 新高背离去重计算（问题 8）
10. 巴菲特指标日志增强（问题 9）
11. V2 核心指标单元测试（问题 15）

### 阶段四：清理收尾（半天）

12. V1 代码归档/删除（问题 14）
13. 维度分展示对齐（问题 10）
14. focus_industries akshare 调用优化（问题 13）
