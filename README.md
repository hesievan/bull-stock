# A股牛市热度指数

🌡️ 每日更新的量化指标，从估值、宏观、资金、情绪、技术、结构六个维度综合评估A股市场整体热度。

> **定位：仅提示离场/减仓，不发出进场或加仓信号。**

## 快速一览 (v3.1)

| 维度 | 得分 | 权重 | 说明 |
|------|------|------|------|
| 估值 | 52.0 | 20% | PE/PB分位+破净率+ERP |
| **宏观** | **40.7** | **20%** | M1-M2剪刀差+M2同比 |
| 资金 | 100.0 | 15% | 北向累计流入+两融余额比 |
| 情绪 | 94.6 | 20% | 换手率+涨跌家数比+涨停+涨跌停比 |
| 技术 | 46.2 | 10% | MA排列比+均线偏离度 |
| 结构 | 26.2 | 15% | 行业分化度+AH溢价指数 |
| **综合** | **58.9🟡** | 100% | 加权合成 |

---

## 项目结构

```
bull-market-heat-index/
├── src/
│   ├── data/
│   │   ├── database.py      # 16张表
│   │   └── fetcher.py       # tushare+akshare 数据获取
│   ├── indicators/
│   │   └── calculator.py    # 16子指标+加权合成
│   └── output/
│       └── json_writer.py   # JSON+飞书通知
├── scripts/
│   ├── run_daily.py         # 每日入口(9步)
│   ├── ah_premium.py        # AH溢价指数(akshare+tushare)
│   ├── backfill_history.py  # 全量历史回测
│   ├── backfill_weekly.py   # 周频采样回测
│   ├── gen_history_chart.py # 历史走势图
│   ├── gen_report.py        # 日报生成(MD+HTML+PNG)
│   └── update_sectors.py    # 板块热度更新
├── web/
│   ├── app.html             # 前端SPA
│   └── data/                # JSON输出
├── reports/
│   ├── indicator_spec.md    # 指标详细说明
│   ├── suggestions.md       # 项目评估
│   └── v3.1_adjustment.md   # v3.1调整方案
└── data/
    └── heat_index.db        # SQLite(~2GB)
```

---

## 数据源方案（双源合一）

| 数据源 | 负责数据 | 频率限制 |
|--------|---------|---------|
| **tushare** | 全市场K线/PE/PB/市值/融资融券/北向/成分股/行业 | 2000积分版 |
| **akshare** | M2月度数据 | 免费 |
| **恒生HSAHP** | AH溢价指数历史数据(用户上传CSV) | 无 |

---

## 指标体系 (v3.1: 6维度 16子指标)

### 估值维度 (20%)

| # | 指标 | 数据源 | 口径 |
|---|------|--------|------|
| 1 | PE历史分位 | tushare stock_daily.peTTM | 成分股口径 |
| 2 | PB历史分位 | tushare stock_daily.pbMRQ | 成分股口径 |
| 3 | 破净率(反向) | tushare stock_daily.pbMRQ | 全市场口径 |
| 4 | ERP股权风险溢价 | index_daily_pe + bond_yield | 1/PE - 10年国债 |

### 宏观维度 (20%) 🆕

| # | 指标 | 数据源 | 口径 |
|---|------|--------|------|
| 5 | M1-M2增速剪刀差 | akshare macro_china_money_supply | 月频 |
| 6 | M2同比增速 | akshare macro_china_money_supply | 月频 |

### 资金维度 (15%)

| # | 指标 | 数据源 | 口径 |
|---|------|--------|------|
| 7 | 北向资金累计流入分位 | tushare northbound_history | 60日窗口 |
| 8 | 两融余额/流通市值比 | tushare margin_history + daily_circ_mv | 250日窗口 |

### 情绪维度 (20%)

| # | 指标 | 数据源 | 口径 |
|---|------|--------|------|
| 9 | 换手率 | tushare stock_daily | 全市场 |
| 10 | 涨跌家数比 | tushare stock_daily | 全市场 |
| 11 | 涨停占比 | tushare stock_daily | 全市场 |
| 12 | 涨跌停比 | tushare stock_daily | 全市场 |

### 技术维度 (10%)

| # | 指标 | 数据源 | 口径 |
|---|------|--------|------|
| 13 | MA排列比(MA20>MA60>MA120) | tushare stock_daily | 全市场 |
| 14 | 均线偏离度 | tushare index_daily | 上证综指 |

### 结构维度 (15%)

| # | 指标 | 数据源 | 口径 |
|---|------|--------|------|
| 15 | 行业分化度(连续版) | tushare stock_daily+stock_industry | 84行业 |
| 16 | AH溢价指数 | 恒生HSAHP历史数据(CSV) | 2899天 |

---

## 维度权重

| 维度 | v3.0 | v3.1 | 理由 |
|------|------|------|------|
| 估值 | 25% | **20%** | 新增宏观维度 |
| 宏观 | 0% | **20%** | 牛市先导信号 |
| 资金 | 25% | **15%** | 北向数据量级差异 |
| 情绪 | 20% | 20% | 保持不变 |
| 技术 | 10% | 10% | 保持不变 |
| 结构 | 20% | **15%** | 文档不重视结构 |

---

## 热度区间

| 颜色 | 分数 | 含义 |
|------|------|------|
| 🟢 绿色安全 | 0–40 | 估值合理/偏低，情绪冷淡 |
| 🟡 黄色警惕 | 40–70 | 部分指标偏高，需关注 |
| 🔴 红色预警 | 70–100 | 多项指标历史高位，考虑减仓/离场 |

---

## 自动化

- `copaw cron` 每交易日16:30触发 `run_daily.py`
- 输出: JSON + 飞书通知(红区连续2天触发)
- 日报: MD + HTML(ECharts交互图) + PNG

---

## 依赖

```
tushare>=1.4.0       # 全市场K线/PE/PB/市值/融资融券/北向 (2000积分)
akshare>=1.15.0      # M2月度数据
pandas>=2.0.0
numpy>=1.24.0
```

---

## 许可证

MIT
