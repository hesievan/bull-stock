# 牛市热度指数 · 后续改进路线图

> 文档版本: v1.0 | 建立: 2026-09-01
> 适用范围: V2 引擎(10 指标 / 4 维度)+ GitHub Pages 前端
> 状态说明: 本文为**规划文档**,不含已落地部分。已完成的北向退役(2026-08)、前端优化 A–F(2026-08)见 README「版本」小节。

---

## 一、设计原则

1. **四维度总权重恒定**:估值 28% / 资金 15% / 情绪 35% / 结构 22%。新增或退役指标只在维度**内部**腾挪权重(沿用 `north_ratio` 退役的成功做法)。
2. **数据源免费优先**:一律走 akshare(GitHub 开源、无需 token)或项目**已入库的现成数据**;tushare 免费额度仅作备份。
3. **低相关才准入**:避免与现有 PE / Buffett / 换手率共线(这是当初移除 ERP、存款市值比的判断标准)。
4. **每阶段必过回测门禁**:区分度、同期/领先相关、极值后续收益三项不退化才合并。

---

## 二、现状基线

### 2.1 10 指标体系

| 维度 | 指标 | 权重 | 数据源 |
|---|---|---|---|
| 估值 28% | 大盘 PE | 14% | tushare `index_dailybasic` |
| | 巴菲特指标 | 14% | tushare 全市场市值 / GDP |
| 资金 15% | 两融余额市值比 | 7% | tushare `margin` |
| | 国债期限利差 10Y-2Y | 4% | tushare `yc_cb` |
| | M1-M2 剪刀差 | 4% | akshare M1/M2 |
| 情绪 35% | 涨停封板率 | 8% | tushare `limit_list` |
| | 成交额 / M2 | 15% | tushare 成交额 + M2 |
| | 换手率 | 12% | tushare `daily_basic` |
| 结构 22% | 创新高占比 | 14% | `stock_daily` 250 日新高 |
| | MA 排列比 | 8% | `stock_daily` 均线 |

### 2.2 现成但未启用的数据(零成本金矿)

| 数据 | 表 / 字段 | 计算步骤 | 现状 |
|---|---|---|---|
| 涨跌家数比 | `daily_updown.up_down_ratio` | S27 | 已入库、**未进指标** |
| 破净率 | `daily_below_net.below_net_rate` | S29 | 已入库、**未进指标** |

> 两者均已在 `json_writer` 中以 `display_*` 键输出,前端加卡片无需改数据流。
> 注:破净率属估值极端值,与 Buffett 指标共线,**不建议进分**,仅作展示参考。

### 2.3 方法学锚点

`_pct_rank`(`src/indicators/heat_index_v2.py:116`)被全部 10 个 `calc_*_v2` 函数调用(行 186/228/319/382/448/495/553/589/638/695)。
→ 滚动分位改造**只需动这一处**,是全项目杠杆率最高的一处改动。

### 2.4 已识别的两类缺口

1. **宽度信号缺失**:new_high 只看「高度」,没有「宽度」。广度背离是牛市顶部最经典警告。
2. **跨境 / 杠杆资金立场缺失**:`north_ratio` 退役后,资金维度少了「聪明钱」情绪代理。

---

## 三、路线图总览

| 阶段 | 主题 | 交付物 | 工作量 |
|---|---|---|---|
| **P0** | 稳健性收尾 | 清理遗留、验证 echarts、Lighthouse 门禁 | S |
| **P1** | 指标补强 Tier1 | 涨跌家数广度、南向净买入、股指期货升贴水 | M |
| **P2** | 方法学升级 | 滚动窗口分位、市态标签 + 双轨风险 | M |
| **P3** | 进阶指标 | iVIX、融资买入占比、振幅热度、开户数、ETF 申赎 | L |
| **P4** | 可选增强 | 论坛 NLP 情绪、搜索指数 | XL |

---

## 四、P0 — 稳健性收尾(低风险,可立即做)

| # | 内容 | 改动点 |
|---|---|---|
| 0.1 | 清理遗留文件:`web/*.bak_20260812`(3 个)、`reports/*.bak`、历史 `reports/daily_*.html`(10 份,非当前 CI 产出) | 文件系统 |
| 0.2 | 验证 `web/echarts.custom.min.js`(650KB 定制 bundle)无组件遗漏——部署后点「指标拆解」tab 趋势图 + 概览指数 mini 图,排查 `Component ... is not registered` | 线上验证 |
| 0.3 | `.github/workflows/daily.yml` 加一步 Lighthouse CI 对 Pages 站评分,设性能 / SEO / a11y 阈值 | `.github/workflows/daily.yml` |

---

## 五、P1 — 指标补强 Tier1(免费源,高 ROI)

> 三项在「候选指标评估」与「GitHub 同类项目对比」中**双重验证**;覆盖 2.4 的两类缺口。

### 1.1 涨跌家数广度(Breadth)—— 零成本

- **数据源**:`daily_updown.up_down_ratio`(已每天计算,零新增抓取成本)
- **新增**:`calc_breadth_v2(conn, trade_date)` → 取历史序列 → `_pct_rank` → score
- **维度**:结构(补「宽度」,与 new_high「高度」互补)
- **权重**:结构 22% = new_high **12%** / ma_alignment **6%** / **breadth 4%**(new_high 14→12、ma 8→6)
- **改动清单**:
  - `src/indicators/heat_index_v2.py`:`CALC_FUNCS`、`DEFAULT_WEIGHTS`、`INDICATOR_DIMENSIONS`、`indicators` / `indicator_raw` 输出 dict
  - `config/prod.yaml` + `config/dev.yaml`:`v2_engine.weights`
  - `src/config.py`:`_EXPECTED_WEIGHT_KEYS`、`EngineWeights`
  - `src/output/json_writer.py`:`v2_highlights`
  - `scripts/`:`backtest_v2.py`、`refill_history_v2.py`、`regen_today_snapshot.py`、`show_engine_live.py`、`backfill_indicator_history.py`、`audit_indicators.py`
  - `web/app.html`:`indMeta` / `indKeys`(已有 `display_up_down_ratio` 可直接展示)
  - `tests/test_heat_index_v2.py`、`tests/test_config.py`

### 1.2 南向通净买入(Southbound)—— 补 north_ratio 退役坑

- **数据源**:akshare `stock_hsgt_south_net_flow_in_em()`(日频,南向 2024-08 后仍正常披露)
- **新增**:`daily_hsgt_south` 表 + fetcher step(仿原 northbound 结构);口径 `south_net / amount`,单位对齐参照原 `_NORTH_UNIT_FACTOR` 做法
- **维度**:资金(跨境「聪明钱」情绪代理)
- **权重**:资金 15% = margin **6%** / yield 4% / m1_m2 4% / **southbound 1%**(margin 7→6 释放 1%)
- **改动清单**:同 1.1 套件 + 新建 fetcher / db 表定义
- **注意**:南向数据需先跑一段历史回填,确认量级正常再进分

### 1.3 股指期货升贴水(Futures discount)—— 机构杠杆立场

- **数据源**:akshare `futures_main_sina(symbol="IF0")` + 沪深300 现货(tushare `index_daily`,已有)
- **基差率** = (IF 主力 − HS300) / HS300;需处理主力换月跳变(连续主力或换月平滑)
- **维度**:情绪(机构 / 杠杆资金真实预期,前瞻性强,与零售情绪低相关)
- **权重**:情绪 35% = seal **7%** / turnover_m2 **14%** / turnover **10%** / **futures 4%**(各让 1–2%)
- **风险**:akshare 期货接口偶有延迟 → 复用 `database.py` 的 `fallback: True` + `max_gap_days` 机制

---

## 六、P2 — 方法学升级(不动指标结构也能做)

### 2.1 滚动窗口分位替代全历史分位

- **改动**:`_pct_rank(series, value)` → `_rolling_pct_rank(series, value, window=1260)`(≈5 年交易日)
- **收益**:解决 regime drift——10 年前的极端高低点会稀释当下极值;顶 / 底区分更尖锐,对近期状态更敏感
- **注意**:
  - 序列头部 window 不足时退化为全历史
  - 必须回测校准,确认极值后续收益不退化(不能为灵敏牺牲区分度)
  - 全历史分位与滚动分位建议**并行跑一段**对比,再决定是否切换

### 2.2 市态标签 + 双轨风险

- **复用**:现有 divergence 惩罚已含「破位」思想,显式拆出「结构破位风险」线
- **新增**:综合分之上叠加市态标签(过热 / 分歧 / 冰点 / 修复),阈值规则生成;`web/app.html` 加状态徽章
- **参考**:ashare-sentiment 的 7 态机、MarketMonitoring 的「拥挤 + 破位」双轨

---

## 七、P3 — 进阶指标(工程较重,按价值排序)

| 指标 | 数据源(免费) | 维度 | 说明 |
|---|---|---|---|
| iVIX 期权波动率 | akshare `option_risk_indicator_sse` | 波动(进分) | QVIX 已展示不计分;iVIX 可作对照或替代进分 |
| 融资买入占比(非余额) | tushare `margin_detail` | 资金 | 「融资买入额 / 成交额」比余额更灵敏,与 `margin_ratio` 互补不共线 |
| 振幅热度 | 日高低差 / 昨收 | 情绪 | 计算极简(多空博弈强度),情绪维度轻量补充 |
| 新增开户数 | akshare `stock_account_statistics_em` | 情绪(低频锚) | 月频,插值为日后百分位;散户 FOMO 黄金指标 |
| ETF 净申赎 | akshare `fund_etf_spot_em` | 资金 | 份额变动 × 净值;与 `turnover_m2` 略有重合 |

> **月频类(开户数)不建议硬塞日频合成**,优先做「低频锚」展示或单独情绪分,避免污染日频热度的灵敏度。

---

## 八、P4 — 可选增强(工程最大,谨慎)

- **4.1 论坛 / 股吧 NLP 情绪**:唯一「非价格」数据源,强反向指标。需爬东方财富股吧 / 雪球 + 金融情感模型(SnowNLP / HanLP)。**建议二期**。
- **4.2 搜索指数(FOMO)**:akshare `baidu_search_index`(需 cookie,易失效)。信号独特但脆弱,**优先级最低**。

---

## 九、权重重配总表(Tier1 落地后)

| 维度 | 现状 | → 改后 |
|---|---|---|
| 估值 28% | PE 14 / Buffett 14 | PE 14 / Buffett 14 |
| 资金 15% | margin 7 / yield 4 / m1_m2 4 | margin **6** / yield 4 / m1_m2 4 / **southbound 1** |
| 情绪 35% | seal 8 / turnover_m2 15 / turnover 12 | seal **7** / turnover_m2 **14** / turnover **10** / **futures 4** |
| 结构 22% | new_high 14 / ma_alignment 8 | new_high **12** / ma_alignment **6** / **breadth 4** |

合计仍 100%,四维度权重不变,指标数 10 → 13。

---

## 十、数据源可靠性工程(贯穿全程)

- **主力用 akshare**,优先 `*_em`(东方财富)系列——源最稳、字段最规整
- 所有新抓取套用 `database.py` 的 `fallback: True` + `max_gap_days` 重试机制(已验证可用)
- 关键指标保留 tushare 免费额度作备份(如 PE 用 `index_dailybasic`)
- akshare 公开源改版致接口临时失效时,**降级为缺失而非报错**(参照前端 fetchJSON 的容错设计)

**已知局限**:akshare 个别接口会因源站改版临时失效、字段命名不统一。这是接受免费源的代价,靠 fallback + 容忍缺失规避。

---

## 十一、验证与回测门禁(每阶段收尾必跑)

```
backtest_v2.py   全历史回测:区分度 / 同期相关 / 领先60日相关 / 极值后续收益
                 对照基线 15.3 / 0.810 / −0.231 / 极热后60日 −8.3%、极冷后 +7.5%
pytest           新增 calc 函数单测 + 部分数据 None 重归一化测试
ruff check / ruff format
regen_today_snapshot.py   重生 JSON,确认指标数、无 null 维度
Lighthouse CI    (P0.3 落地后)性能 / SEO / a11y 阈值门禁
```

---

## 十二、取舍总结

**优先做**
- P0 清理 + **1.1 涨跌家数广度**(零成本、数据库现成、跨项目共识)—— 当天可上线
- 其次是 **1.2 南向**(补退役坑)+ **2.1 滚动分位**(不动结构、立竿见影)

**谨慎投入**
- 1.3 期货升贴水(需处理主力换月)
- P3 / P4(工程重、易失效)

**明确不加**
- AH 溢价、破净率进分(与估值共线)
- 龙虎榜 / 个股主力净流入(与换手率重叠,噪声大)
- 公募基金新发规模、股东户数(月 / 季频、获取不稳,ROI 低)

---

## 十三、参考来源

**同类项目(GitHub)**
- `cuicui-V5/bull_top_index_Dashboard`(牛市逃顶指数,四维度含振幅热度 / 上涨比例)
- `hyan1985/MarketMonitoring`(结构拥挤 + 破位风险双轨)
- `gmz9976/ashare-sentiment`(20+ 情绪特征,7 态市态机)
- `iwanlebron/stock-analysis`(Go F&G,252 日滚动分位归一化)
- 雪球经典恐贪 6 因子(期权波动率 / 北向 / 创新高占比 / **股指期货升贴水** / 股债回报差 / 融资买入占比)

**数据源**
- akshare(github.com/akfamily/akshare,19.7k★,MIT,免费无 token)—— 主力
- baostock、mootdx / pytdx —— 备份行情源
- tushare 免费额度 —— 补充(需 token,有积分限制)
