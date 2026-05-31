# A股牛市热度指数

🌡️ 每日更新的量化指标，从估值、资金、情绪、技术、结构五个维度综合评估A股市场整体热度。

> **定位：仅提示离场/减仓，不发出进场或加仓信号。**

## 项目结构

```
├── src/
│   ├── data/
│   │   ├── database.py      # SQLite 数据库管理 (14张表)
│   │   └── fetcher.py       # 三源合一数据获取 (baostock+tushare+akshare)
│   ├── indicators/
│   │   └── calculator.py    # 18个子指标 + 动态权重 + 综合热度合成
│   └── output/
│       └── json_writer.py   # JSON 输出 + 飞书通知生成
├── scripts/
│   ├── run_daily.py         # 每日计算入口
│   └── init_history.py      # 历史数据一次性初始化
├── web/
│   ├── index.html           # 前端页面（深色主题 + ECharts）
│   └── data/                # 每日生成的 JSON 数据 (gitignore)
├── config/                  # 配置文件
├── .github/workflows/
│   └── daily.yml            # GitHub Actions 自动更新 (交易日 16:30)
├── data/
│   └── heat_index.db        # SQLite 数据库 (gitignore)
├── requirements.txt
├── README.md
└── TODO.md
```

## 数据源方案（三源合一）

| 数据源 | 负责数据 | 频率限制 | 代码格式 |
|--------|---------|---------|---------|
| **baostock** | 指数日行情、个股K线(PE/PB/价格/成交量)、成分股列表、行业分类、交易日历 | 不限频，控制间隔 0.3s | `sh.000001` |
| **tushare** | 融资融券、北向资金、国债收益率、指数PE/PB(备用) | 1次/小时 | `000001.SH` |
| **akshare** | AH溢价（备用） | 不稳定(TUN环境) | `sh000001` |

内部统一使用 akshare 格式（`sh000001`/`sh.600000`），函数 `ak_to_bs()`/`ak_to_ts()` 自动转换。

### baostock 覆盖的数据
- ✅ 指数日行情 (`query_history_k_data_plus`: open/high/low/close/volume/amount/pctChg)
- ✅ 个股K线 (peTTM/pbMRQ/pctChg/volume/amount)
- ✅ 成分股列表 (hs300/sz50/zz500)
- ✅ 行业分类 (`query_stock_industry`: 证监会行业分类)
- ✅ 交易日历
- ✅ 资产负债表 (bps/净资产)
- ❌ 融资融券、北向资金 (baostock无此接口)

### tushare 覆盖的数据
- ✅ 融资融券日汇总 (`margin`: rzye/rzmre/rqye/rzrqye)
- ✅ 北向资金净流入 (`moneyflow_hsgt`: hgt/sgt)
- ✅ 中债国债收益率 (`yc_cb`: 10年期)
- ✅ 指数PE/PB备用 (`index_dailybasic`: pe_ttm/pb/total_mv/turnover_rate)

### akshare 覆盖的数据
- ✅ AH溢价 (`stock_zh_ah_spot_em`)
- ⚠️ Clash TUN 环境下可能不稳定，仅做补充

## 指标体系 (5维度 18子指标)

| 维度 | # | 指标 | 主要数据源 | 标准化 |
|------|---|------|-----------|--------|
| **估值** | 1 | PE历史分位 | baostock stock_daily.peTTM 中位数 | 10年分位 |
| | 2 | PB历史分位 | baostock stock_daily.pbMRQ 中位数 | 10年分位 |
| | 3 | 股债性价比 ERP | baostock PE中位数 + tushare 10Y国债 | 10年分位(反向) |
| | 4 | 破净率 | baostock stock_daily.pbMRQ < 1 占比 | 10年分位 |
| **资金** | 5 | 融资买入占比 | tushare margin.rzmre / 全市场成交额 | 10年分位 |
| | 6 | 北向资金方向 | tushare moneyflow_hsgt.north_net | 近20日净买入比 |
| **情绪** | 7 | 换手率 | baostock amount/circ_mv | 10年分位 |
| | 8 | 上涨/下跌家数比 | baostock pctChg | 10年分位 |
| | 9 | 涨停占比 | baostock pctChg ≥ 9.9 | 10年分位 |
| | 10 | 跌停占比 | baostock pctChg ≤ -9.9 | 10年分位(反向) |
| | 11 | 波动率(VIX替代) | baostock 指数20日收益率标准差 | 10年分位 |
| | 12 | 新增投资者 | 中国结算月度(手动录入) | 10年分位 |
| **技术** | 13 | 站上年线比例 | baostock close vs 250日均值 | 静态分位 |
| | 14 | 创新高占比 | baostock close vs 250日最高 | 静态分位 |
| | 15 | 均线偏离度 | 上证综指 close / MA250 - 1 | 10年分位 |
| | 16 | 量价背离 | 上证综指 价格趋势 vs 量比 | 状态打分 |
| **结构** | 17 | 行业分化度 | baostock 各行业pctChg 标准差 | 静态阈值 |
| | 18 | AH溢价 | akshare stock_zh_ah_spot_em | 10年分位 |

> 新增(#8上涨/下跌家数比)和(#16量价背离)为 V1.2 新增指标；去掉了新发偏股基金份额和主力资金净流入占比。

## 数据库表结构 (14张表)

```
index_daily        — 指数日行情 (trade_date, index_code, open/high/low/close/volume/amount/pct_change)
stock_daily        — 个股日行情 (trade_date, stock_code, close, peTTM, pbMRQ, pct_change, volume, amount, total_mv, circ_mv)
stock_industry     — 个股行业分类 (code, code_name, industry, industry_classification, update_date)
stock_balance      — 个股资产负债表 (stock_code, report_date, bps)
margin_history     — 融资融券汇总 (trade_date, rzye, rzmre, rzche, rqye, rqmcl, rzrqye)
northbound_history — 北向资金 (trade_date, hgt, sgt, north_net, south_money)
bond_yield         — 国债收益率 (trade_date, curve_term, yield_rate)
index_pe_history   — 指数PE/PB备用 (trade_date, index_code, pe_ttm, pb, total_mv, turnover_rate)
limit_up_daily     — 涨停明细 (trade_date, stock_code)
ah_premium         — AH溢价 (trade_date, premium)
new_investors      — 新增投资者 (week_end_date, new_accounts)
heat_index         — 热度指数结果 (trade_date, composite_score, dim_valuation/fund/sentiment/technical/structure, detail_json)
sector_heat        — 板块热度 Phase 2 (trade_date, sector_code, composite_score, detail_json)
metadata           — 元数据 (key, value, updated_at)
```

## 计算流程

```
交易日 16:30 触发 (GitHub Actions / copaw cron)
  │
  ├─ 1. baostock 登录
  ├─ 2. fetch_all_index_incremental()   增量拉取指数日行情 (baostock)
  ├─ 3. fetch_index_constituents()      获取成分股列表 (hs300/sz50/zz500)
  ├─ 4. fetch_stocks_latest_day()       拉取~850只成分股当日K线 (baostock)
  ├─ 5. tushare 数据 (当日已存在则跳过)
  │     ├─ fetch_margin_history()       融资融券
  │     ├─ fetch_northbound_history()   北向资金
  │     └─ fetch_bond_yield_history()   国债收益率
  ├─ 6. baostock 登出
  │
  ├─ 7. calculate_heat_index()
  │     ├─ 计算 18 个子指标当前值
  │     ├─ 与 10 年历史对比 → 分位数 (0-100)
  │     ├─ Z-score 异常过滤 (3σ)
  │     ├─ 维度内等权合成 → 5维度分数
  │     └─ 维度间等权合成 → 综合热度 (0-100)
  │
  ├─ 8. save_results() → web/data/index.json + detail.json + history.json
  └─ 9. 红区(≥70) → 飞书通知
```

### 热度区间

| 颜色 | 分数 | 含义 |
|------|------|------|
| 🟢 绿色安全 | 0–40 | 估值合理/偏低，情绪冷淡，减仓信号远 |
| 🟡 黄色警惕 | 40–70 | 部分指标偏高，需关注 |
| 🔴 红色预警 | 70–100 | 多项指标历史高位，考虑减仓/离场 |

## 使用指南

### 环境配置

```bash
# 安装依赖 (推荐用 uv)
uv venv && uv pip install -r requirements.txt

# tushare token (也可写入 ~/daily_stock_analysis/.env)
export TUSHARE_TOKEN="your_token_here"
```

### 首次初始化（一次性，约30-60分钟）

```bash
python scripts/init_history.py              # 默认从 2015-01-01
python scripts/init_history.py 2010-01-01   # 指定起始日期
```

> ⚠️ tushare 有频率限制（1次/小时），初始化脚本已自动处理。baostock 成分股历史K线（~850只×11年）是最耗时步骤。

### 每日运行

```bash
python scripts/run_daily.py                 # 计算今日
python scripts/run_daily.py 2026-05-29      # 计算指定日期
python scripts/run_daily.py --backfill      # 历史回测(2015-01-01起)
```

### 前端预览

```bash
cd web && python -m http.server 8080
# 打开 http://localhost:8080
```

### 自动化 (GitHub Actions)

推送到 GitHub 后，每个交易日 16:30 (北京时间) 自动运行并 push 结果。

`.github/workflows/daily.yml` 配置触发条件。

### 自动化 (copaw cron)

```bash
# 已在 copaw 创建定时任务，每日 16:30 北京时间触发
# 任务 ID: (见 copaw cron list)
```

## 依赖

```
akshare>=1.15.0      # AH溢价数据 (备用)
baostock>=0.8.8      # 个股/指数行情数据（主力，不限频）
tushare>=1.4.0       # 融资融券/北向资金/国债数据
pandas>=2.0.0
numpy>=1.24.0
```

## Git 提交历史

| Commit | 内容 |
|--------|------|
| `9b952b1` | 三源合一 fetcher + 计算引擎改进 |
| `xxxxxxx` | 三源合一改造 — database/fetcher/calculator/run_daily 全面更新 |

## 路线图

- [x] Phase 0: 项目骨架 + 三源合一数据层 + 计算引擎 + 前端
- [ ] Phase 1 (6/10): MVP 上线 — 飞书通知 + GitHub Actions + 历史数据初始化
- [ ] Phase 2 (6/24): 行业热度指数 + 板块热力图 + 更多结构指标

## 关键文档

- 需求 V1.2: https://my.feishu.cn/docx/Rm5Gd4J63oBvoAxSLKpcKNzqn0c
- 评估报告: https://my.feishu.cn/docx/Hs8udA63FoBewIxp9ENcfqk7nBE

## 许可证

MIT
