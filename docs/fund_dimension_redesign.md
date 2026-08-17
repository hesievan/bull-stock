# 资金维度扩容方案：设计与代码修改计划

> ⚠️ **状态更新（2026-08）**：本方案中的 `north_ratio`（北向净流入比）已于 2026-08 退役。原因：自 2024-08-19 起港交所停止披露北向净买入额，tushare `moneyflow_hsgt` 此后返回失真的膨胀量级，且 CI 日常抓取长期因 tushare 频率限制不稳定，线上 `index.json` 的 `north_ratio` 长期为 null，已名存实亡。退役后其 4% 权重重配给 `margin_ratio_v2`(5%→7%)、`yield_spread`(3%→4%)、`m1_m2_spread`(3%→4%)，资金维度仍合计 15%；资金维度由"4 指标"变为"3 指标"，全系统由 11 指标变为 10 指标。下文保留原始设计记录，仅供参考。

> 目标：把当前"资金"维度唯一的 `margin_ratio_v2`（两融余额市值比，占 15%）拆分为 **4 个低相关、已标准化**的资金/流动性指标，消除单点脆弱性。
> 原则：① 所有新指标必须是**比率/标准化**形式，避免绝对额的体量漂移；② 不改变资金维度总权重（仍 15%）与综合分总权重（仍 100%），只在内部分配。

---

## 1. 整体方案：4 指标定义

| # | 指标 key | 标准化定义 | 数据源 | 方向 | 占综合权重 |
|---|----------|-----------|--------|------|-----------|
| 1 | `margin_ratio_v2` | 两融余额 ÷ 流通市值（已是比率） | `margin_history`（已有） | pos | **5%** |
| 2 | `north_ratio`（已退役） | 北向净流入(`north_net`) ÷ 当日两市成交额 | `northbound_history` + `stock_daily`(amount) | pos | **4%（已重配，见上方说明）** |
| 3 | `yield_spread` | 10Y 国债收益率 − 1Y 国债收益率（单位：bp/百分点） | `bond_china_yield`（国债曲线） | pos | **3%** |
| 4 | `m1_m2_spread` | M1 同比 − M2 同比（单位：百分点） | tushare `macro_china`(M1) + `m2_monthly` | pos | **3%** |

资金维度总权重 = 5 + 4 + 3 + 3 = **15%**（不变；退役后变为 7 + 4 + 4 = 15%，见上方说明）。

### 为什么这样标准化（直接回应"量级漂移"问题）
- **`margin_ratio_v2`**：已是比率，天然平稳，原样保留。
- **`north_ratio`**：已验证原始 `north_net` 近 300× 漂移（2020≈1.0万 → 2026≈37.8万）。除以**当日成交额**（同样随市场放大）即可抵消漂移，得到"北向参与度"比率。负值为外资净卖出。
- **`yield_spread` / `m1_m2_spread`**：本身就是**利差/增速差**（差分），构造上平稳，无漂移问题。

---

## 2. 权重与维度计算

`INDICATOR_WEIGHTS`（总和 = 1.0）改为：

```
pe             0.14
buffett        0.14
margin_ratio_v2 0.05   # 原 0.15
seal_rate      0.15
turnover_m2    0.15
turnover       0.12
new_high       0.10
ma_alignment   0.05
north_ratio    0.04   # 新增
yield_spread   0.03   # 新增
m1_m2_spread   0.03   # 新增
```

`IND_DIMS` 增加映射：`north_ratio / yield_spread / m1_m2_spread → 'fund'`。

引擎现有逻辑（维度分 = 维度内指标百分位按权重加权平均）**自动生效**，无需改维度聚合代码：
- 资金维度分 = `(5·margin% + 4·north% + 3·yield% + 3·m1m2%) / 15`，落在 0–100。
- 综合分 = 各指标百分位 × 权重之和（维持 100）。

> 验证：原 8 指标权重和 = 1.00；现 11 指标权重和 = 0.14+0.14+0.05+0.15+0.15+0.12+0.10+0.05+0.04+0.03+0.03 = **1.00** ✓

---

## 3. 代码修改计划（分 Phase）

### Phase 0 — 数据接入与回填
**`src/data/fetcher.py`**
- 扩展 `_fetch_bond_yield_akshare` / `fetch_bond_yield_history`：改用 `ak.bond_china_yield()`，过滤 `曲线名称` 含 **"国债"** 的行（排除中短期票据/商业银行债等信用曲线），取 `1年` 与 `10年` 两列，写入 `bond_yield`（`curve_term ∈ {1.0, 10.0}` 并存）。
- 新增 `fetch_m1_history(start, end)`：用 tushare `pro.macro_china(item=<M1 item>)` 抓 M1 同比，写 `m1_monthly(month, m1_billion, m1_yoy)`。（具体 item 码需实测确认）
- `northbound_history` 已在 `fetch_northbound_history`：确保 `run_daily.py` 或 backfill 调用它，**回填至今天**（当前 DB 止于 2026-06-25；已实测 tushare 返回到 2026-08-11）。

**`src/data/database.py`**
- 新增表 `m1_monthly(month TEXT PRIMARY KEY, m1_billion REAL, m1_yoy REAL)`。
- 新增预计算表（供引擎/回测快速读取，避免逐日重算）：
  - `daily_north_ratio(trade_date, north_net, amount, ratio)`
  - `daily_yield_spread(trade_date, spread)`
  - `daily_m1_m2_spread(trade_date, spread)`（按月映射）

**新建 `scripts/backfill_fund_indicators.py`**
- 回填北向至今天；抓 1Y 收益率 + M1；计算上述三张比率/差分表全历史。

**Phase 0 验证门槛**
- 三张新序列覆盖 ≥ 2015（收益率受 akshare 覆盖限制可能仅近年，见风险）。
- `north_ratio` 平稳性：其 10 年百分位分布不应被钉在 0 或 100。
- `yield_spread` / `m1_m2_spread` 牛熊方向正确（牛 > 熊，回测验证）。

### Phase 1 — 引擎
**`src/indicators/heat_index_v2.py`**
- 改 `INDICATOR_WEIGHTS`、`IND_DIMS`（见第 2 节）。
- 在 `calc_heat_index_v2` 增加三分支，mirror 现有逻辑：取对应预计算表、建 10 年窗口、用 `_pct_rank` 算百分位。
- `indicators_v2` 原始值附上三指标原始值（`north_ratio`、`yield_spread`、`m1_m2_spread`）。
- 确认 `config/dev.yaml` / `config/prod.yaml` 无覆盖权重的死配置（若有则同步改）。

### Phase 2 — 回测验证（把关）
**`scripts/backtest_v2.py`**
- 预计算 + 逐日循环加入三新序列；`ind_cols` 增加 `north_ratio / yield_spread / m1_m2_spread`。
- 重跑，与 baseline 对比：牛熊区分度、同期相关系数、牛/熊识别率。
- 重点观察：**今日资金维度**由 98.4 降为四指标加权平均（应明显回落），综合分是否仍处合理区（红/橙）。

**回测验收门槛**（任一不满足则回 Phase 1 调权重）
- 牛熊区分度 ≥ 14.2（不退化，最好 ↑）
- 同期相关系数 ≥ 0.845
- 牛/熊识别率不降
- 资金维度不再由单指标主导（今日资金分 < 98.4，且四子项有分化）
- 三个新指标各自牛熊均值差方向正确（pos：牛 > 熊）

### Phase 3 — 展示
**`src/output/json_writer.py`**
- `indicators_v2` / `indicator_history` 增加三键。
**`scripts/backfill_indicator_history.py`**
- 指标键列表 8 → 11，重算全历史（CI 也会自动重算）。
**`web/app.html`**
- `indMeta` 与 `indKeys` 增加三项（标签 / 单位 / 方向 / 配色 / 解释 / 格式化函数）。

### Phase 4 — 测试与文档
**`tests/`**
- 更新断言：指标数 8 → 11、`INDICATOR_WEIGHTS` 和 = 1.0、维度映射。
- 新增三指标单测（百分位计算、方向、缺失处理）。
**`README.md` / `docs`**
- 更新指标体系表（资金维度 4 子指标 + 权重）；刷新回测验证表。

---

## 4. 风险与缓解

| 风险 | 说明 | 缓解 |
|------|------|------|
| 收益率历史覆盖有限 | `bond_china_yield` 实测约 738 行，可能仅近年；10 年百分位窗口前段样本少 | 该指标早期不贡献（引擎 <60 样本自动跳过）；接受部分覆盖，或换更全长源 |
| M1 源不确定 | akshare 无 `macro_china_m1`；改用 tushare `macro_china`，需确认 item 码 | Phase 0 实测确认 |
| 方向误设 | 某指标 pos/rev 搞反 | 回测牛熊均值差验证，错了翻 sign |
| 稀释信号 | 加 3 指标后区分度下降 | 回测不达标则回调权重（如 margin 提到 7%、north 3%） |
| 北向近期缺口 | 已实测 tushare 到今天，回填后无缺口 | Phase 0 回填 + 校验 max(trade_date) |

---

## 5. 改动文件清单

| 文件 | 改动类型 |
|------|----------|
| `src/data/fetcher.py` | 改：bond_yield 扩展 1Y；新增 fetch_m1_history |
| `src/data/database.py` | 改：新增 m1_monthly + 三张预计算表 |
| `scripts/backfill_fund_indicators.py` | 新：回填北向/1Y/M1 + 计算比率差表 |
| `src/indicators/heat_index_v2.py` | 改：权重、维度映射、三指标计算 |
| `scripts/backtest_v2.py` | 改：三新序列 + ind_cols |
| `src/output/json_writer.py` | 改：indicators_v2 / indicator_history 加键 |
| `scripts/backfill_indicator_history.py` | 改：指标键 8→11 |
| `web/app.html` | 改：indMeta / indKeys 加三项 |
| `tests/` | 改：权重/维度断言；新增三指标单测 |
| `README.md` | 改：指标体系表 + 回测表 |
| `config/dev.yaml` `config/prod.yaml` | 查：若有权重死配置则同步改 |

---

## 6. 实施顺序建议

**Phase 0（数据）→ Phase 1（引擎）→ Phase 2（回测把关）→ Phase 3（展示）→ Phase 4（测试文档）**。
每个 Phase 结束后用回测/单测验证再进入下一 Phase；Phase 2 是质量闸门，不达标回到 Phase 1 调权重。
