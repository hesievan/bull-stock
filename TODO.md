# A股牛市热度指数 — 维护手册

> 更新: 2026-06-03 | 项目状态: v3.0 指标体系重构完成 + baostock已移除
> 当前核心: AH溢价数据补充 + 日常自动化 + 指标微调

---

## 📊 当前状态速览

| 维度 | 状态 | 说明 |
|------|------|------|
| 数据源 | ✅ tushare+akshare | 已移除baostock依赖，全市场覆盖5500+只 |
| 计算引擎 | ✅ v3.0 | 16子指标+加权合成(25/25/20/10/20) |
| 全量回测 | ✅ 完成 | 2015-01-05 ~ 2026-06-03, 2771天, 0失败 |
| 板块热度 | ✅ 就绪 | 71个行业, 3维度, 8.5s |
| 历史走势图 | ✅ 上线 | ECharts交互图+关键事件+极值标记+Zoom联动 |
| 日报生成 | ✅ 就绪 | MD + HTML(ECharts) + PNG, 含维度对比 |
| 前端SPA | ✅ 上线 | app.html 单页应用: 概览/拆解/板块/历史 |
| 自动化 | ✅ cron已配 | 每交易日16:30触发(copaw cron ID: 63be5c6c) |
| 飞书通知 | ✅ 已验证 | 红区连续2天触发, 含板块热度TOP5 |
| AH溢价数据 | ⚠️ 待补充 | 需人工从cn.investing.com下载CSV |
| GitHub | ❌ 未推送 | 需创建仓库+配置GitHub Actions |

---

## 📈 历史回测基准

**综合热度统计**: 均值44.8 | 最高73.4(2026-01-12) | 最低17.7(2018-07-04)
**天数分布**: 🔴红区10天(0.4%) | 🟡黄区1869天(67.4%) | 🟢绿区892天(32.2%)

| 日期 | 市场状态 | 综合 | 估值 | 资金 | 情绪 | 技术 | 结构 |
|------|---------|------|------|------|------|------|------|
| 2026-01-12 | 近期高点 | **75.6🔴** | 70.7 | 96.7 | 85.2 | 76.6 | 45.2 |
| 2015-06-12 | 牛市顶 | **69.0🟡** | 100.0 | 67.1 | 51.9 | 92.6 | 38.0 |
| 2020-07-09 | 疫情反弹 | **72.1🔴** | 55.6 | 92.8 | 77.0 | 64.3 | 66.1 |
| 2024-10-08 | 脉冲顶 | **63.2🟡** | 52.0 | 66.6 | 88.0 | 62.1 | 48.6 |
| 2018-12-28 | 熊底 | **30.8🟢** | 1.4 | 57.8 | 27.7 | 9.2 | 47.6 |
| 2026-06-02 | 当前 | **64.9🟡** | 60.1 | 98.4 | 58.1 | 55.7 | 40.3 |

---

## 🔴 待办事项

### 人工数据补充

| # | 任务 | 数据源 | 操作步骤 | 状态 |
|---|------|--------|---------|------|
| 1 | **AH溢价指数(HSAHP)历史数据** | cn.investing.com | 1) 打开 https://cn.investing.com/indices/hs-cahpi-historical-data<br>2) 筛选日期范围 2015-01-01 ~ 2026-06-02<br>3) 复制表格数据，格式: `日期,收盘价`<br>4) 粘贴给我，我写入数据库 | ⏳ |

### 待完成

| # | 任务 | 优先级 | 说明 |
|---|------|--------|------|
| 2 | GitHub Push | 高 | 创建仓库 + 推送代码 + 配置 GitHub Actions |
| 3 | 指标微调 | 中 | 北向金额化、量价背离连续化等 |
| 4 | 飞书日报加板块热度TOP5 | 低 | 在现有日报中增加板块段落 |

---

## ✅ 已完成 (2026-06-03)

### 指标体系 v3.0 重构
- 新增: northbound_cumflow(北向累计流入分位) + limit_ratio(涨跌停比)
- 舍弃: limit_down_ratio / new_high_ratio / price_volume_divergence
- 修复: margin_ratio → 两融余额/流通市值比, turnover → 全市场成交额, sector_divergence → 连续分位
- 权重: 估值25% / 资金25% / 情绪20% / 技术10% / 结构20%

### tushare 全市场替代 baostock
- 全市场K线: 5500+只(100%) vs 原baostock 800只(15%)
- 全量回填: 2770天, 0失败, 49.7分钟
- amount单位统一: tushare千元(消除baostock元单位混乱)

### 前端+日报
- SPA重构: app.html 4Tab单页应用
- 历史走势图: 2771天+关键事件+双图联动
- 日报v2: MD+HTML+ECharts+PNG, 含维度对比

### 数据层
- 三源合一 → 双源合一(tushare+akshare)
- 数据库18张表, stock_daily 1100万行

---

## 🟢 日常维护

### 每日 (自动)
- `copaw cron` 每交易日16:30触发 `run_daily.py`
- 自动拉取: tushare 全市场K线/PE/PB/市值 → 融资融券/北向/国债
- 自动计算: 16子指标(加权) → 综合热度 → JSON输出
- 自动通知: 红区(≥70) → 飞书推送（防抖: 连续2天）

### 每周检查
- [ ] 查看 `run_daily.log` 有无 ERROR
- [ ] 检查 stock_daily 最新日期的股票数（应 ≥ 5000）
- [ ] 检查 tushare API 调用是否触发频率限制

---

## 📁 项目结构

```
bull-market-heat-index/
├── src/
│   ├── data/
│   │   ├── database.py      # 18张表
│   │   └── fetcher.py       # tushare+akshare 数据获取
│   ├── indicators/
│   │   └── calculator.py    # 16子指标+加权合成
│   └── output/
│       └── json_writer.py   # JSON+飞书通知
├── scripts/
│   ├── run_daily.py         # 每日入口(9步)
│   ├── ah_premium.py        # AH溢价指数(akshare+tushare)
│   ├── backfill_history.py  # 全量历史回测
│   ├── backfill_tushare.py  # tushare全市场回填
│   ├── gen_history_chart.py # 历史走势图
│   ├── gen_report.py        # 日报生成(MD+HTML+PNG)
│   └── update_sectors.py    # 板块热度更新
├── web/
│   ├── app.html             # 前端SPA
│   ├── echarts.min.js       # ECharts离线版
│   └── data/                # JSON输出
├── reports/
│   ├── indicator_spec.md    # 指标详细说明
│   └── daily_*.*/           # 日报文件
├── data/
│   └── heat_index.db        # SQLite(~3GB)
└── requirements.txt         # tushare+akshare+pandas+numpy
```

---

## 📋 关键配置

| 配置 | 值 | 位置 |
|------|-----|------|
| 项目路径 | `/Users/hesi/bull-market-heat-index` | — |
| 数据库 | `data/heat_index.db` | 项目内 |
| tushare token | `473bc9...b389577` | `~/daily_stock_analysis/.env` |
| copaw cron ID | `63be5c6c-210f-4e2c-8393-414f41e97b3a` | copaw cron list |
| 红区阈值 | ≥ 70 | `json_writer.py` |
| 红区防抖 | 连续2天 | `json_writer.py` DEBOUNCE_RED_DAYS=2 |

---

## 📝 维护日志

| 日期 | 操作 | 备注 |
|------|------|------|
| 2026-06-03 | 指标体系v3.0重构 | 16指标+加权+修复3个+新增2个+舍弃3个 |
| 2026-06-03 | tushare全市场回填 | 2770天, 0失败, 49.7分钟 |
| 2026-06-03 | baostock全面移除 | fetcher.py重写, run_daily.py简化 |
| 2026-06-02 | 前端SPA+历史走势图+日报v2 | ECharts交互图, 关键事件标注 |
| 2026-06-02 | 板块热度指数 | 71个行业, 3维度, 8.5s |
| 2026-06-02 | AH溢价方案B | akshare H股+tushare A股 |
| 2026-06-02 | 资金维度异常修复 | margin_ratio/northbound计算逻辑修正 |
| 2026-06-01 | PE/PB历史成分股口径 | 回测通过: 2015🔴 2018🟢 |
| 2026-06-01 | 飞书日报验证成功 | 红区通知已推送 |

---

## 🎯 里程碑

| 日期 | 目标 | 状态 |
|------|------|------|
| 6/10 | MVP: 飞书通知+copaw cron+18子指标+AH溢价 | ✅ 完成 |
| 6/24 | Phase 2: 行业热度+板块热力图 | ✅ 完成 |
| 6/30 | 全量回测+历史走势图+baostock移除 | ✅ 完成 |
| 7/15 | 指标优化: 北向金额化+AH溢价稳定性 | ⏳ |
| 8/01 | v1.0.0: 单元测试+前端联调+文档完善 | ⏳ |
