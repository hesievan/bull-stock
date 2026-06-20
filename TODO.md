# A股牛市热度指数 — 维护手册

> 更新: 2026-06-20 | 项目状态: v3.9 完成

---

## 📊 当前状态

| 维度 | 状态 | 说明 |
|------|------|------|
| 数据源 | ✅ tushare+akshare+恒生HSAHP | 全市场5500+只 |
| 计算引擎 | ✅ v3.9 | 19子指标全可用+每日pipeline自动更新预计算表 |
| 热度区间 | ✅ 4档 | 绿(0-40)/黄(40-55)/橙(55-65)/红(65+) |
| 单元测试 | ✅ 83个测试 | calculator/utils/valuation/macro/fund/sentiment/technical/structure/database/config/json_writer/run_daily |
| CI/CD | ✅ GitHub Actions | lint+test+daily update+backtest validation |
| Bug修复 | ✅ 已审计 | P0-P3 全部修复, 见 AUDIT_v3.8.md |
| 飞书通知 | ✅ 已验证 | 橙区连续2天触发(防抖) |
| 预计算表 | ✅ 每日自动更新 | daily_updown/daily_limit/daily_below_net/daily_ma_alignment 四表随 pipeline 刷新 |
| 指标覆盖率 | ✅ 100% | 19/19 指标有值 (MA排列比+创新高不再返回 null) |

---

## 📈 回测基准 (v3.6)

| 日期 | 市场状态 | 综合 | 热度 | 估值 | 资金 | 情绪 | 技术 | 结构 |
|------|---------|------|------|------|------|------|------|------|
| 2015-06-12 | 牛市顶 | 72.6 | 🔴 | 100 | 60 | 82 | 90 | 39 |
| 2018-12-28 | 熊底 | 17.2 | 🟢 | 3 | 28 | 22 | 14 | 25 |
| 2020-07-10 | 牛市启动 | 58.9 | 🟠 | 65 | 60 | 68 | 75 | 23 |
| 2021-02-18 | 牛市顶 | 57.7 | 🟠 | 54 | 72 | 75 | 66 | 17 |
| 2024-10-08 | 脉冲顶 | 53.5 | 🟡 | 53 | 87 | 88 | 68 | 14 |
| 2024-02-05 | 熊底 | 10.8 | 🟢 | 3 | 8 | 15 | 7 | 5 |

---

## 🔴 待办事项

### 短期优化
- [ ] **预计算表 schema 完善**: 加入 daily_ma_alignment 计算中的高级多头排列条件(MA5>MA10>MA20>MA120)

### 中期改进
- [ ] **多市场监控**: 增加恒生指数/美股标普500热度对比
- [ ] **板块轮动追踪**: 在 sector_heat 基础上增加资金流向板块归因
- [ ] **回测全量重算脚本**: 定期(每月)用最新逻辑重算全历史 score，确保回测基准跟踪最新算法
- [ ] **ERP评分校准**: 当前 ERP 评分 14.7, 历史百分位偏低, 需核实 ERP 历史值计算逻辑
- [ ] **指标独立 mock 测试**: 每个维度模块的独立单元测试 (估值/情绪/技术/结构)
- [ ] **QVIX 自动更新**: Step 接入 fetch_qvix.py (需 akshare 依赖)
- [ ] **stock_high_250d 预计算表**: 优化创新高比例指标性能 (当前 live 计算耗时 ~20s)

### 已完成 (v3.9)
- [x] **预计算表 pipeline (4表)**: daily_updown/daily_limit/daily_below_net/daily_ma_alignment 正式纳入日跑 Steps S27-S30
- [x] **创新高比例 fallback**: calc_new_high_ratio 改用 _get_stock_daily + 最新可用日期, 不再返回 null
- [x] **MA排列比不再为 null**: 预计算表每日自动更新 → 技术维度 5/5 指标全可用
- [x] **数据库 schema 补全**: 4个预计算表 CREATE TABLE IF NOT EXISTS 加入 SCHEMA
- [x] **全部19子指标可用**: 验证 06-18 跑测, 0 FAILED, 全指标有值

### 已完成 (v3.8)
- [x] **MA排列比改为百分位评分**: 从绝对分 `ratio×100` 改为历史百分位，消除分值偏差（26→62）
- [x] **_pct_rank 改为 ≤**: 消除 `<` 带来的系统性低估偏差，同步测试
- [x] **情绪指标 live fallback**: 涨跌家数比/涨停占比/涨跌停比在预计算表无当日数据时自动从 stock_daily 实时计算
- [x] **AH溢价双源 fallback**: 月表(SSE指数)无数据时回退到日表(15对AH股)
- [x] **daily_circ_mv 自动更新**: 新 Step S26_circ_mv 在 stock_daily 更新后自动汇总流通市值
- [x] **数据库 schema 完善**: 新增 daily_circ_mv/index_daily_pe/ah_premium_monthly 表定义到 SCHEMA
- [x] **背离惩罚日志修复**: 保存 `s1_orig` 确保日志原值准确
- [x] **全部83测试通过**: 新增模块测试覆盖

---

## ✅ 已完成 (v3.6)

### 指标优化 ✅
- [x] **PE/PB合并**: `_calc_valuation_composite()` = PE×0.6 + PB×0.4，减少信息重叠
- [x] **替换行业分化度**: 用创新高比例(`_calc_new_high_ratio`)替代行业分化度(相关性仅0.12)
- [x] **调整红区阈值**: 红≥65, 橙≥55, 黄≥40, 绿<40 (原红≥70)
- [x] **得分平滑**: 3日移动平均，输出 `composite_score_smoothed`
- [x] **新增橙色预警**: 4档热度区间(绿/黄/橙/红)

### Bug修复 ✅

#### P0 — 影响计算正确性
- [x] **inf未过滤**: `pct_change()` 后添加 `replace([np.inf, -np.inf], np.nan)`
- [x] **pct_rank返回0**: 全NaN序列返回 `np.nan` 而非 `0.0`
- [x] **数据未保存**: `fetch_all_history` 中 margin/northbound 获取后调用 `_save()`
- [x] **JSON NaN**: `_round_score` 处理 NaN/Inf 返回 None

#### P1 — 影响用户体验
- [x] **emoji错误**: 橙色等级改用 `get_heat_level_cn()` 获取正确emoji
- [x] **连接泄漏**: `HeatIndexCalculator` 添加 `close()` 方法和上下文管理器
- [x] **连接泄漏**: `fetch_daily_basic_to_stock_daily` 使用 try/finally
- [x] **回退缺字段**: `run_daily.py` 回退结果添加 `dim_macro`
- [x] **webhook异常**: 添加 `json.JSONDecodeError` 到 except
- [x] **env引号**: `.env` 解析添加 `.strip('"\'')`

#### P2 — 代码质量
- [x] **SQL注入**: `save_dataframe` 添加表名白名单 `_ALLOWED_TABLES`
- [x] **除零风险**: `_calc_deviation_ma250` 添加 `ma_val == 0` 检查
- [x] **pct_rank不一致**: `_series_pct_rank` 改用 `<` 与 `_pct_rank` 一致
- [x] **buffett_ratio**: 改用 `_score_with_fallback(score)`
- [x] **硬编码日期**: `fetch_index_constituents` 添加 `start_year` 参数

### 工程化 ✅ (v3.5)
- [x] CI依赖修复: requirements.txt 添加 pyyaml
- [x] 添加 pyproject.toml: 标准化Python包管理
- [x] 添加单元测试: 73个测试全部通过
- [x] 拆分 calculator.py: 板块热度独立为 sector_calculator.py
- [x] 修复 TUSHARE_TOKEN 加载: 多路径探测
- [x] 数据库版本迁移: SCHEMA_VERSION + _migrate()
- [x] 60日动量指标: 技术维度新增
- [x] 多环境配置: dev.yaml + prod.yaml + HEAT_INDEX_ENV
- [x] REST API: api_server.py (FastAPI, 5个端点)
- [x] 前端响应式: 3个断点(1024/768/480px)

---

## 📝 维护日志

| 日期 | 操作 | 备注 |
|------|------|------|
| 2026-06-20 | v3.9: 预计算表 pipeline | 4表每日自动更新, 创新高fallback, 19/19全指标可用 |
| 2026-06-20 | v3.8: Bug审计+live fallback | MA排列百分位/情绪fallback/AH溢价双源/S26_circ_mv, 83测试 |
| 2026-06-20 | v3.7: 架构重构 | calculator.py拆为7模块, 惰性加载, 统一DB连接 |
| 2026-06-14 | v3.6: 指标优化+Bug修复 | PE/PB合并、行业分化度替换、16个Bug修复 |
| 2026-06-14 | v3.5: 工程化优化 | P0-P4全部完成, 73个测试, CI完整 |
| 2026-06-06 | v3.4: 资金指标改为变化率 | 北向99.6%→68.4%, 两融99.3%→59.1% |
| 2026-06-05 | v3.3: 回测优化 | 宏观日频+资金窗口+红区阈值 |
| 2026-06-05 | v3.2: AH溢价月频+区间赋分 | 5个区间赋分 |
| 2026-06-04 | v3.1: 文档建议指标调整 | 新增M1-M2/ERP/MA排列比 |
| 2026-06-03 | v3.0: 16指标+加权 | tushare全市场替代baostock |

---

## 🎯 里程碑

| 日期 | 目标 | 状态 |
|------|------|------|
| 6/10 | MVP: 飞书通知+16子指标 | ✅ |
| 6/24 | 行业热度+板块热力图 | ✅ |
| 6/30 | 全量回测+baostock移除 | ✅ |
| 7/15 | v3.1: 文档建议指标调整 | ✅ |
| 7/15 | v3.2: AH溢价月频+区间赋分 | ✅ |
| 7/15 | v3.3: 回测优化 | ✅ |
| 8/01 | v3.4: 资金指标优化+回测验证 | ✅ |
| 8/15 | GitHub推送+单元测试 | ✅ |
| 6/14 | v3.5: 工程化优化(P0-P4) | ✅ |
| 6/14 | v3.6: 指标优化+Bug修复 | ✅ |
| 6/20 | v3.7: 架构重构(caculator拆分+模块化) | ✅ |
| 6/20 | v3.8: Bug审计+live fallback | ✅ |
| 6/20 | v3.9: 预计算表 pipeline+19指标全可用 | ✅ |

---

## 📊 指标变更记录

| 版本 | 变更 | 影响 |
|------|------|------|
| v3.9 | 预计算表每日更新(4表) | 消除预计算表陈数据问题, MA排列比/涨跌比/涨停/破净不再依赖手写 backfill |
| v3.9 | 创新高比例 fallback | 结构维度 new_high_ratio 不再返回 null, 2/2 指标全可用 |
| v3.6 | PE/PB合并为复合指标 | 估值维度从4项减至3项 |
| v3.6 | 创新高比例替代行业分化度 | 结构维度更有效(相关性提升) |
| v3.6 | 红区阈值70→65, 新增橙区55 | 更及时的预警信号 |
| v3.6 | 得分平滑(3日移动平均) | 减少单日波动 |
| v3.5 | 新增60日动量指标 | 技术维度从2项增至3项 |
| v3.4 | 资金指标改为变化率 | 避免极端值锁定 |
| v3.1 | 新增宏观维度(M1-M2/M2) | 从5维度增至6维度 |
