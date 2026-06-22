# A股牛市热度指数

> 每日更新的量化指标，从估值、宏观、资金、情绪、技术、结构六个维度综合评估A股市场整体热度，
> 并对沪深300/创业板/科创50/北证50/A500/中证1000六大核心指数单独给出牛市见顶预判信号。
> **定位：仅提示离场/减仓，不发出进场或加仓信号。**

---

## 最近改进 (v3.11)

- **市场结构断点检测**: CUSUM 法自动检测均值变点，替代固定10年窗口，消除远古极端值对评分的稀释 (`src/indicators/regime_detector.py`)
- **数据新鲜度与权重衰减**: 月频数据陈旧时自动衰减其维度权重，权重重新分配给新鲜指标 (`src/data/freshness.py`)
- **短历史指数相对强弱**: 北证50/中证A500 改用 vs 沪深300 的相对强弱评分，替代噪声大的绝对动量分位
- **数据质量报告**: 每日输出 `data_quality` 区块，飞书通知增加质量警告（含新鲜度、缺失指标）
- **极端值检测**: 超出 mean±5σ 的值自动截断 (`utils._clip_outliers`)
- **预计算表陈旧检测**: S24_precompute_check 步骤，每日监测10张预计算表的陈旧状态

---

## 目录

- [项目简介](#项目简介)
- [指标体系](#指标体系)
- [数据来源](#数据来源)
- [计算方法](#计算方法)
- [使用方法](#使用方法)
- [API 接口](#api-接口)
- [量化策略](#量化策略)
- [项目结构](#项目结构)
- [开发指南](#开发指南)

---

## 项目简介

### 核心功能

A股牛市热度指数是一个量化分析工具，通过6个维度、19个子指标综合评估A股市场的整体热度水平，并额外对6大核心指数（沪深300/创业板/科创50/北证50/A500/中证1000）逐一输出牛市见顶预判信号。系统每日自动更新，通过飞书通知推送风险预警。

### 热度区间

| 颜色 | 分数 | 含义 | 行动建议 |
|------|------|------|---------|
| 🟢 绿色安全 | 0–40 | 估值合理/偏低，情绪冷淡 | 安全区间 |
| 🟡 黄色警惕 | 40–55 | 部分指标偏高 | 需关注 |
| 🟠 橙色关注 | 55–65 | 多项指标偏高 | 考虑减仓 |
| 🔴 红色预警 | 65–100 | 多项指标历史高位 | 考虑离场 |

### 回测验证

| 日期 | 市场状态 | 综合 | 热度 | 信号 |
|------|---------|------|------|------|
| 2015-06-12 | 牛市顶 | 72.6 | 🔴 | 正确触发红区 |
| 2018-12-28 | 熊底 | 17.2 | 🟢 | 正确触发绿区 |
| 2020-07-10 | 牛市启动 | 58.9 | 🟠 | 正确触发橙区 |
| 2021-02-18 | 牛市顶 | 57.7 | 🟠 | 正确触发橙区 |
| 2024-10-08 | 脉冲顶 | 53.5 | 🟡 | 黄色警惕 |
| 2024-02-05 | 熊底 | 10.8 | 🟢 | 正确触发绿区 |
| 2026-06-18 | 震荡市 | 52.5 | 🟡 | 黄色警惕 |

### 指数牛市见顶预判信号 (v3.10)

除全市场热度外，系统对 6 大核心指数单独计算过热信号：

| 指数 | 代码 | 技术指标 | 估值指标 | 评分原理 |
|------|------|---------|---------|---------|
| 沪深300 | 000300.SH | MA偏离+20/60/120动量 | PE+PB分位 | 50%技术+50%估值 |
| 创业板指 | 399006.SZ | MA偏离+20/60/120动量 | PE+PB分位 | 50%技术+50%估值 |
| 科创50 | 000688.SH | MA偏离+20/60/120动量 | — | 纯技术评分 |
| 北证50 | 899050.BJ | MA偏离+20/60/120动量 | — | 纯技术评分 |
| 中证A500 | 000510.SH | MA偏离+20/60/120动量 | — | 纯技术评分 |
| 中证1000 | 000852.SH | MA偏离+20/60/120动量 | — | 纯技术评分 |

- **量能评分**: 仅作参考，不参与技术综合（tushare 近年量价数据单位不统一）
- **估值缺失**: 科创板/北证/A500/中证1000 无指数 PE/PB 数据源，使用纯技术评分
- **输出**: `web/data/index_heat.json`

---

## 指标体系

### 总览

| 维度 | 权重 | 子指标数 | 说明 |
|------|------|---------|------|
| 估值 | 25% | 3 | PE/PB复合、破净率、ERP |
| 宏观 | 15% | 2 | M1-M2剪刀差、M2同比 |
| 资金 | 15% | 2 | 北向变化率、融资余额比变化率 |
| 情绪 | 20% | 5 | 换手率、涨跌家数比、涨停占比、涨跌停比、QVIX |
| 技术 | 10% | 5 | MA排列比、均线偏离度、20/60/120日动量 |
| 结构 | 15% | 2 | 创新高比例、AH溢价指数 |

### 估值维度 (25%)

| # | 指标 | 计算方法 | 数据源 |
|---|------|---------|--------|
| 1 | **PE/PB复合** | PE分位×0.6 + PB分位×0.4 | tushare stock_daily.peTTM/pbMRQ |
| 2 | **破净率(反向)** | PB<1股票占比，反向计分 | tushare stock_daily.pbMRQ |
| 3 | **ERP股权风险溢价** | (1/PE - 10年国债收益率) × 100 | index_daily_pe + bond_yield |

**PE/PB分位计算**:
- 口径: 沪深300+中证500成分股
- 方法: 成分股PE中位数 vs 历史10年分位
- 公式: `分位 = (历史中位数 ≤ 当前中位数) / 历史样本数 × 100`

**ERP计算**:
- 公式: `ERP = (1/PE - 10年国债收益率/100) × 100`
- 含义: ERP高=股票相对债券便宜=低分(反向)

### 宏观维度 (15%)

| # | 指标 | 计算方法 | 数据源 |
|---|------|---------|--------|
| 4 | **M1-M2剪刀差** | M1同比 - M2同比 | tushare cn_m |
| 5 | **M2同比增速** | M2同比增长率 | tushare cn_m |

**数据处理**: 月频数据插值到日频，10年历史分位

### 资金维度 (15%)

| # | 指标 | 计算方法 | 数据源 |
|---|------|---------|--------|
| 6 | **北向20日累计变化率** | (当前20日累计 - 前20日累计) / 前20日累计 × 100 | tushare moneyflow_hsgt |
| 7 | **融资余额比变化率** | (当前比值 - 前一期比值) / 前一期比值 × 100 | tushare margin + daily_circ_mv |

**变化率方案**: 避免极端值锁定，反映趋势变化

### 情绪维度 (20%)

| # | 指标 | 计算方法 | 数据源 |
|---|------|---------|--------|
| 8 | **换手率** | 全市场成交额/流通市值 × 10 | tushare stock_daily |
| 9 | **涨跌家数比** | 上涨家数/下跌家数 | tushare stock_daily.pct_change |
| 10 | **涨停占比** | 涨停股票数/总股票数 | tushare stock_daily.pct_change |
| 11 | **涨跌停比** | 涨停数/max(跌停数,1)，封顶10 | tushare stock_daily.pct_change |
| 12 | **QVIX恐慌指标** | 50ETF期权隐含波动率，反向计分 | akshare index_option_50etf_qvix |

**背离惩罚**: 换手率>70且涨跌家数比<30时，对换手率施加惩罚

### 技术维度 (10%)

| # | 指标 | 计算方法 | 数据源 |
|---|------|---------|--------|
| 13 | **MA排列比** | MA20>MA60>MA120的股票占比, 历史百分位评分 | tushare stock_daily.close |
| 14 | **均线偏离度** | (上证综指/MA250 - 1) × 100 历史百分位 | tushare index_daily |
| 15 | **20日动量** | 上证综指20日涨幅历史分位 | tushare index_daily |
| 16 | **60日动量** | 上证综指60日涨幅历史分位 | tushare index_daily |
| 17 | **120日动量** | 上证综指120日涨幅历史分位 | tushare index_daily |

### 结构维度 (15%)

| # | 指标 | 计算方法 | 数据源 |
|---|------|---------|--------|
| 18 | **创新高比例** | close≥250日最高价×0.98的股票占比 | tushare stock_daily.close |
| 19 | **AH溢价指数** | 15只核心AH股 A/H价格比中位数 | akshare stock_hk_daily + tushare daily |

---

## 数据来源

### 数据源总览

| 数据源 | 接口 | 数据 | 积分/费用 |
|--------|------|------|----------|
| tushare | daily | 全市场K线 | 2000积分 |
| tushare | daily_basic | PE/PB/市值 | 2000积分 |
| tushare | margin | 融资融券 | 2000积分 |
| tushare | moneyflow_hsgt | 北向资金 | 2000积分 |
| tushare | index_dailybasic | 指数PE/PB | 2000积分 |
| tushare | cn_m | M2货币供应 | 2000积分 |
| tushare | index_weight | 成分股 | 2000积分 |
| tushare | stock_basic | 行业分类 | 2000积分 |
| akshare | bond_zh_us_rate | 国债收益率 | 免费 |
| akshare | stock_hk_daily | H股数据 | 免费 |
| akshare | index_option_50etf_qvix | 50ETF QVIX | 免费 |

### 数据表结构

| 数据表 | 行数 | 日期范围 | 用途 |
|--------|------|---------|------|
| stock_daily | 11,037,122 | 2015-01-05 ~ 2026-06-18 | 全市场K线/PE/PB/市值 |
| index_daily | 19,624 | 2014-12-29 ~ 2026-06-12 | 6大指数行情（含科创50/北证50/A500） |
| margin_history | 2,587 | 2015-01-05 ~ 2026-06-01 | 融资融券余额 |
| northbound_history | 2,688 | 2015-01-05 ~ 2026-06-10 | 北向资金净流入 |
| daily_turnover | 1,384 | 2020-09-16 ~ 2026-06-10 | 换手率(备, 现用实时计算) |
| daily_updown | 2,775 | 2015-01-05 ~ 2026-06-10 | 涨跌家数比(备, 有fallback) |
| daily_limit | 2,775 | 2015-01-05 ~ 2026-06-10 | 涨停/跌停(备, 有fallback) |
| daily_ma_alignment | 2,716 | 2015-04-03 ~ 2026-06-10 | MA排列比 |
| index_daily_pe | 2,757 | 2015-01-30 ~ 2026-06-18 | 成分股PE/PB中位数(每日更新) |
| bond_yield | 2,109 | 2018-01-02 ~ 2026-06-12 | 国债收益率 |
| qvix_daily | 2,746 | 2015-02-09 ~ 2026-06-12 | 50ETF QVIX |
| ah_premium | 2,906 | 2015-01-05 ~ 2026-06-19 | AH股溢价(日频, 15对AH股) |
| ah_premium_monthly | 138 | 2015-01 ~ 2026-06 | AH溢价指数(月频, SSE指数) |
| daily_circ_mv | 2,772 | 2015-01-05 ~ 2026-06-03 | 全市场流通市值(每日更新) |
| m2_monthly | 220 | 2008-01 ~ 2026-04 | M2货币供应 |
| index_constituents_hist | 106,000 | 2015-01 ~ 2026-05 | hs300+zz500成分股历史 |

---

## 计算方法

### 综合得分计算

```
综合得分 = 估值×25% + 宏观×15% + 资金×15% + 情绪×20% + 技术×10% + 结构×15%
```

### 维度内合成

每个维度内的子指标**等权合成**:
1. 过滤 None 和异常值(>3σ)
2. 剩余指标等权平均
3. 结果限制在 [0, 100] 区间

### 历史分位计算

所有指标使用**10年历史窗口**进行分位排名:
```python
分位 = (历史值 ≤ 当前值) / 历史样本数 × 100
```

### 数据陈旧处理

部分预计算表（`daily_updown`、`daily_limit`、`daily_below_net`、`daily_erp`）的非每日更新可能导致当日无数据：

| 指标 | 处理方式 |
|------|---------|
| 涨跌家数比、涨停占比、涨跌停比 | 从 `stock_daily` 实时 fallback 计算 |
| 破净率 | 从 `stock_daily.pbMRQ` 实时 fallback |
| ERP | 从 `index_daily_pe` + `bond_yield` 实时计算 |
| M1-M2剪刀差、M2同比 | ≤ 模糊匹配最邻近日期 |
| MA排列比 | 需 `daily_ma_alignment` 预计算表有当日数据 |
| QVIX | 需 `qvix_daily` 有当日数据（外部API） |

### 特殊处理

| 场景 | 处理方法 |
|------|---------|
| PE/PB合并 | PE×0.6 + PB×0.4 (减少信息重叠) |
| 换手率背离 | 换手率>70且涨跌家数比<30时惩罚 |
| 涨跌停比 | 跌停数=0时封顶为10 |
| QVIX | 反向计分(高QVIX=恐慌=低分) |
| ERP | 实时计算: (1/PE - 10年国债收益率) × 100 |
| MA排列比 | 历史百分位评分（非绝对比例） |
| AH溢价 | 优先月表(SSE指数), 回退到日表(15对AH股) |
| 预计算表陈旧 | 自动从 stock_daily 实时 fallback (涨跌比/涨停/破净/ERP) |

---

## 使用方法

### 安装依赖

```bash
# 克隆项目
git clone <repo_url>
cd bull-market-heat-index

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 TUSHARE_TOKEN
```

### 每日计算

```bash
# 计算今日热度
python scripts/run_daily.py

# 计算指定日期
python scripts/run_daily.py 2026-06-10
```

每日运行流程 (`S0`–`S9` + S24–S30 + S55):
```
S0_init_db     → 数据库初始化
S1_index       → 指数日行情获取（含科创50/北证50/A500）
S2_market      → 全市场K线+PE/PB/市值
S25_index_pe   → 成分股PE/PB中位数 (供ERP)
S26_circ_mv    → 全市场流通市值 (供融资余额比)
S27_updown     → 涨跌家数预计算
S28_limit      → 涨停跌停预计算
S29_below_net  → 破净率预计算
S30_ma_alignment → MA排列比预计算
S24_precompute_check → 预计算表陈旧检测 (v3.11)
S3_tushare     → 融资融券/北向/国债
S4_ah_premium  → AH溢价指数
S5_calc        → 计算19子指标+加权合成 (含新鲜度权重衰减)
S55_index_heat → 六大指数牛市见顶预判 (含相对强弱评分)
S6_save        → 保存JSON+DB落库 (含 data_quality 数据质量报告)
S7_sectors     → 板块热度
S8_final_save  → 最终保存
S9_notify      → 飞书推送 (含新鲜度告警)
```

### 查看结果

```bash
# 查看最新热度
cat web/data/index.json

# 查看详细指标
cat web/data/detail.json

# 查看指数过热预判（沪深300/创业板/科创50/北证50/A500/中证1000）
cat web/data/index_heat.json

# 查看历史数据
cat web/data/history.json
```

### 启动 API 服务

```bash
# 安装 API 依赖
pip install fastapi uvicorn

# 启动服务
python scripts/api_server.py

# 访问 API
curl http://localhost:8000/api/heat
curl http://localhost:8000/api/strategy
```

### 运行测试

```bash
# 安装测试依赖
pip install pytest

# 运行测试
python -m pytest tests/ -v
```

### 数据库维护

```bash
# 查看数据库状态
python scripts/db_maintenance.py

# 压缩数据库
python scripts/db_maintenance.py --vacuum

# 归档旧数据
python scripts/db_maintenance.py --archive 2020

# 补充换手率数据
python scripts/fix_turnover.py

# 补充QVIX数据
python scripts/fetch_qvix.py

# 补充国债收益率
python scripts/backfill_bond_yield.py
```

---

## API 接口

### 基础信息

- 基础URL: `http://localhost:8000`
- 数据格式: JSON
- 请求方法: GET

### 接口列表

| 接口 | 说明 | 参数 |
|------|------|------|
| `/api/heat` | 最新热度指数 | - |
| `/api/history` | 历史数据 | `days` (默认30) |
| `/api/sectors` | 板块热度 | - |
| `/api/detail` | 详细指标拆解 | - |
| `/api/strategy` | 策略信号 | - |
| `/api/health` | 健康检查 | - |

### 示例响应

**GET /api/heat**
```json
{
  "trade_date": "2026-06-10",
  "composite_score": 46.5,
  "level": "yellow",
  "dimensions": {
    "valuation": {"score": 56.1, "label": "估值"},
    "macro": {"score": 47.1, "label": "宏观"},
    "fund": {"score": 59.3, "label": "资金"},
    "sentiment": {"score": 49.1, "label": "情绪"},
    "technical": {"score": 31.9, "label": "技术"},
    "structure": {"score": 26.5, "label": "结构"}
  }
}
```

**GET /api/strategy**
```json
{
  "signal": "hold",
  "signal_cn": "持有",
  "level": "yellow",
  "level_cn": "黄色警惕",
  "target_position": 67.5,
  "reason": "热度中性，维持当前仓位",
  "risk_level": "low",
  "risk_cn": "低风险"
}
```

---

## 量化策略

### 信号体系

| 信号 | 条件 | 行动 |
|------|------|------|
| hold | 热度<55 | 维持当前仓位 |
| reduce | 热度≥55 或 估值≥80 | 分批减仓 |
| add | 热度≤35 且 估值≤40 | 加仓 |
| clear | 热度≥70 且 估值≥90 | 清仓 |

### 仓位管理

```
目标仓位 = 基准仓位 × 热度调整系数

基准仓位 (基于估值分位):
- 估值<30%: 95%
- 估值30-60%: 75%
- 估值60-80%: 55%
- 估值>80%: 25%

热度调整系数:
- 热度<30: 1.0
- 热度30-50: 0.9
- 热度50-60: 0.8
- 热度60-70: 0.6
- 热度>70: 0.3
```

### 回测结果

| 指标 | 策略 | 买入持有 |
|------|------|---------|
| 年化收益 | 12.5% | 8.2% |
| 最大回撤 | -28.3% | -46.7% |
| 夏普比率 | 0.85 | 0.42 |

详细策略说明请参考 [STRATEGY.md](STRATEGY.md)

---

## 项目结构

```
bull-market-heat-index/
├── src/
│   ├── config.py                   # 配置加载
│   ├── data/
│   │   ├── database.py             # SQLite管理 (schema, CRUD, migration)
│   │   ├── fetcher.py              # tushare+akshare 数据获取
│   │   └── freshness.py            # 数据新鲜度与权重衰减 (v3.11)
│   ├── indicators/
│   │   ├── calculator.py           # 编排入口 (6维度加权合成 + 数据质量报告)
│   │   ├── utils.py                # 共享工具函数 (含极端值检测)
│   │   ├── regime_detector.py      # CUSUM 市场结构断点检测 (v3.11)
│   │   ├── valuation.py            # 估值维度 (PE/PB/破净率/ERP)
│   │   ├── macro.py                # 宏观维度 (M1-M2剪刀差/M2同比)
│   │   ├── fund.py                 # 资金维度 (北向/融资余额比)
│   │   ├── sentiment.py            # 情绪维度 (换手率/涨跌比/涨停/QVIX)
│   │   ├── technical.py            # 技术维度 (MA排列/偏离度/动量)
│   │   ├── structure.py            # 结构维度 (创新高/AH溢价)
│   │   ├── index_heat.py           # 六大指数牛市见顶预判 (含相对强弱评分)
│   │   └── sector_calculator.py    # 板块热度计算
│   └── output/
│       └── json_writer.py         # JSON输出+飞书通知
├── scripts/
│   ├── run_daily.py               # 每日计算入口 (S0-S9, 含S25/S26)
│   ├── api_server.py              # REST API (FastAPI)
│   ├── ah_premium.py              # AH溢价日频计算
│   ├── fetch_qvix.py              # QVIX数据获取
│   ├── precompute_const_pe.py     # 成分股PE/PB中位数批量回填
│   ├── fetch_hist_constituents.py # 历史成分股下载
│   ├── data_manager.py            # 统一数据管理
│   └── ... (维护工具)
├── tests/                         # 单元测试 (83个, 全部通过)
├── config/                        # 配置文件 (dev.yaml/prod.yaml)
├── web/                           # 前端SPA
├── reports/                       # 日报/回测报告
├── .github/workflows/             # CI/CD (daily/CI/backtest)
├── STRATEGY.md                    # 量化策略
├── BUG_REPORT.md                  # Bug审计报告
├── ITERATION_PLAN.md              # 迭代计划
└── requirements.txt               # 依赖
```

---

## 开发指南

### 环境要求

- Python 3.10+
- tushare 账号 (2000+积分)
- SQLite

### 安装开发环境

```bash
# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt
pip install -r requirements-dev.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 TUSHARE_TOKEN
```

### 运行测试

```bash
python -m pytest tests/ -v
```

### 代码规范

```bash
# Lint
ruff check src/ scripts/ --select E,F,W

# Format
ruff format src/ scripts/
```

### 添加新指标

1. 在 `calculator.py` 中添加计算方法
2. 在 `calculate()` 方法中调用
3. 在 `indicators` 字典中添加输出
4. 添加单元测试

---

## 常见问题

### Q: 为什么某些指标返回 None?

A: 可能原因:
1. 数据不足 (历史数据<60天)
2. 数据源暂时不可用
3. 计算异常 (已自动跳过)

### Q: 如何补充缺失数据?

A: 运行对应的补充脚本:
```bash
python scripts/backfill_bond_yield.py  # 国债收益率
python scripts/fetch_qvix.py          # QVIX
python scripts/fix_turnover.py         # 换手率
```

### Q: 如何自定义热度阈值?

A: 修改 `src/output/json_writer.py` 中的 `get_heat_level()` 函数

---

## 许可证

MIT

---

*文档版本: v3.11 | 更新时间: 2026-06-20*
