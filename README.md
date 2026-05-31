# A股牛市热度指数

🌡️ 每日更新的量化指标，从估值、资金、情绪、技术、结构五个维度综合评估A股市场整体热度。

> **定位：仅提示离场/减仓，不发出进场或加仓信号。**

## 项目结构

```
├── src/
│   ├── data/
│   │   ├── database.py      # SQLite 数据库管理 (16张表)
│   │   └── fetcher.py       # 三源合一数据获取 (baostock+tushare+akshare)
│   ├── indicators/
│   │   └── calculator.py    # 20个子指标 + 动态权重 + 综合热度合成
│   └── output/
│       └── json_writer.py   # JSON 输出 + 飞书通知生成
├── scripts/
│   ├── run_daily.py         # 每日计算入口
│   ├── init_history.py      # 历史数据一次性初始化 (baostock)
│   ├── import_investors.py  # 新增投资者数据录入
│   ├── step1_bond_yield.py  # tushare 国债收益率拉取
│   ├── step2_index_pe.py    # tushare 指数PE/PB历史
│   ├── step3_northbound.py  # tushare 北向资金全量
│   ├── step4_daily_basic.py # tushare 全市场PE/PB/市值
│   └── step5b_circ_mv_by_stock.py  # tushare 流通市值补全
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

## 数据源方案（三源合一，2000积分）

| 数据源 | 负责数据 | 频率限制 | 代码格式 |
|--------|---------|---------|---------|
| **baostock** | 指数日行情、个股K线(PE/PB/价格/成交量)、成分股列表、行业分类、交易日历 | 不限频，控制间隔 0.3s | `sh600000` |
| **tushare** | 融资融券、北向资金、国债收益率、指数PE/PB、全市场daily_basic(PE/PB/总市值/流通市值)、M2(经akshare) | 200次/分钟(daily_basic)，其他不限 | `600000.SH` |
| **akshare** | M2月度货币供应量、AH溢价（备用） | 不稳定(TUN环境) | `sh000001` |

内部统一使用 baostock 格式（`sh600000`），函数 `ak_to_bs()`/`ak_to_ts()`/`bs_to_ak()` 自动转换。

### baostock 覆盖的数据
- ✅ 指数日行情 (`query_history_k_data_plus`: open/high/low/close/volume/amount/pctChg)
- ✅ 个股K线 (peTTM/pbMRQ/pctChg/volume/amount)
- ✅ 成分股列表 (hs300/sz50/zz500)
- ✅ 行业分类 (`query_stock_industry`: 证监会行业分类)
- ✅ 交易日历
- ✅ 资产负债表 (bps/净资产)
- ❌ 融资融券、北向资金、总市值 (baostock无此接口)

### tushare 覆盖的数据 (2000积分)
- ✅ 融资融券日汇总 (`margin`: rzye/rzmre/rqye/rzrqye)
- ✅ 北向资金净流入 (`moneyflow_hsgt`: hgt/sgt)
- ✅ 中债国债收益率 (`yc_cb`: 10年期)
- ✅ 指数PE/PB/总市值/换手率 (`index_dailybasic`)
- ✅ 全市场个股PE/PB/总市值/流通市值 (`daily_basic`)
- ✅ 全市场每日总市值 (`stock_market_cap` 表，由 daily_basic 汇总)

### akshare 覆盖的数据
- ✅ M2月度货币供应量 (`macro_china_money_supply`)
- ⚠️ AH溢价 (`stock_zh_ah_spot_em`): Clash TUN 环境下不稳定，仅做补充

## 指标体系 (5维度 20子指标)

| 维度 | # | 指标 | 主要数据源 | 标准化 |
|------|---|------|-----------|--------|
| **估值** | 1 | PE历史分位 | baostock stock_daily.peTTM 中位数 | 10年分位 |
| | 2 | PB历史分位 | baostock stock_daily.pbMRQ 中位数 | 10年分位 |
| | 3 | 股债性价比 ERP | baostock PE中位数 + tushare 10Y国债 | 10年分位(反向) |
| | 4 | 破净率 | baostock stock_daily.pbMRQ < 1 占比 | 10年分位 |
| | 5 | **巴菲特指标** | akshare M2 / tushare A股总市值 | 10年分位(反向) |
| | 6 | **沪深300股债比** | tushare HS300 E/P / 10Y国债收益率 | 10年分位(反向) |
| **资金** | 7 | 融资买入占比 | tushare margin.rzmre / 全市场成交额 | 10年分位 |
| | 8 | 北向资金方向 | tushare moneyflow_hsgt.north_net | 近20日净买入比 |
| **情绪** | 9 | 换手率 | tushare circ_mv + baostock amount | 10年分位 |
| | 10 | 上涨/下跌家数比 | baostock pctChg | 10年分位 |
| | 11 | 涨停占比 | baostock pctChg ≥ 9.9 | 10年分位 |
| | 12 | 跌停占比 | baostock pctChg ≤ -9.9 | 10年分位(反向) |
| | 13 | 波动率(VIX替代) | baostock 指数20日收益率标准差 | 10年分位 |
| **技术** | 14 | 站上年线比例 | baostock close vs 250日均值 | 静态分位 |
| | 15 | 创新高占比 | baostock close vs 250日最高 | 静态分位 |
| | 16 | 均线偏离度 | 上证综指 close / MA250 - 1 | 10年分位 |
| | 17 | 量价背离 | 上证综指 价格趋势 vs 量比 | 状态打分 |
| **结构** | 18 | 行业分化度 | baostock 各行业pctChg 标准差 | 静态阈值 |
| | 19 | AH溢价 | akshare stock_zh_ah_spot_em | 10年分位 |
| | 20 | 新增投资者 | 中国结算月度(手动录入) | 10年分位 |

> V1.2+ 新增: #5巴菲特指标、#6沪深300股债比、#10上涨/下跌家数比、#17量价背离。去掉了新发偏股基金份额和主力资金净流入占比。

## 数据库表结构 (16张表)

```
index_daily        — 指数日行情 (trade_date, index_code, open/high/low/close/volume/amount/pct_change)
stock_daily        — 个股日行情 (trade_date, stock_code, open/high/low/close, peTTM, pbMRQ, pct_change, volume, amount, total_mv, circ_mv)
stock_industry     — 个股行业分类 (code, code_name, industry, industry_classification, update_date)
stock_balance      — 个股资产负债表 (stock_code, report_date, bps)
margin_history     — 融资融券汇总 (trade_date, rzye, rzmre, rzche, rqye, rqmcl, rzrqye)  1,602行 (2019~2026)
northbound_history — 北向资金 (trade_date, hgt, sgt, north_net, south_money)  2,682行 (2015~2026 全量)
bond_yield         — 国债收益率 (trade_date, curve_term, yield_rate)  1,894行 (2018~2026)
index_pe_history   — 指数PE/PB/总市值/换手率 (trade_date, index_code, pe_ttm, pb, total_mv, turnover_rate)  13,845行
m2_monthly         — M2月度货币供应量 (month, m2_billion, m2_yoy)  220行 (2008~2026)
stock_market_cap   — 全市场每日总市值 (trade_date, total_mv, stock_count)  2,753行 (2015~2026)
limit_up_daily     — 涨停明细 (trade_date, stock_code)
ah_premium         — AH溢价 (trade_date, premium)
new_investors      — 新增投资者 (week_end_date, new_accounts)  133行 (2015~2026)
heat_index         — 热度指数结果 (trade_date, composite_score, dim_valuation/fund/sentiment/technical/structure, detail_json)
sector_heat        — 板块热度 Phase 2 (trade_date, sector_code, composite_score, detail_json)
metadata           — 元数据 (key, value, updated_at)
```

## 数据完整性 (2026-05-29 快照)

| 表 | 行数 | 日期范围 | 备注 |
|----|------|---------|------|
| index_daily | 16,632 | 2014-12-29 ~ 2026-05-29 | 6指数 × 2,772日 |
| stock_daily | 580,437 | 2015-01-05 ~ 2026-05-29 | ~260只成分股，PE 92% / PB 99% / circ_mv 77% 有效 |
| stock_industry | 5,528 | — | 84个行业 |
| margin_history | 1,602 | 2019-10-21 ~ 2026-05-29 | |
| northbound_history | 2,682 | 2015-01-05 ~ 2026-05-29 | 全量 |
| bond_yield | 1,894 | 2018-12-30 ~ 2026-05-29 | |
| index_pe_history | 13,845 | 2015-01-05 ~ 2026-05-29 | 6指数 |
| m2_monthly | 220 | 2008-01 ~ 2026-04 | |
| stock_market_cap | 2,753 | 2015-01-05 ~ 2026-05-29 | 5,506只/日 |
| new_investors | 133 | 2015-04 ~ 2026-04 | |

## 计算流程

```
交易日 16:30 触发 (copaw cron / GitHub Actions)
  │
  ├─ 1. baostock 登录
  ├─ 2. fetch_all_index_incremental()   增量拉取指数日行情 (baostock)
  ├─ 3. fetch_index_constituents()      获取成分股列表 (hs300/sz50/zz500)
  ├─ 4. fetch_stocks_latest_day()       拉取~260只成分股当日K线 (baostock)
  ├─ 5. tushare 数据 (当日已存在则跳过)
  │     ├─ fetch_margin_history()       融资融券
  │     ├─ fetch_northbound_history()   北向资金
  │     └─ fetch_bond_yield_history()   国债收益率
  ├─ 6. baostock 登出
  │
  ├─ 7. calculate_heat_index()
  │     ├─ 计算 20 个子指标当前值
  │     ├─ 与 10 年历史对比 → 分位数 (0-100)
  │     ├─ Z-score 异常过滤 (3σ)
  │     ├─ 维度内等权合成 → 5维度分数
  │     └─ 维度间等权合成 → 综合热度 (0-100)
  │
  ├─ 8. save_results() → web/data/index.json + detail.json + history.json
  └─ 9. 红区(≥70) → 飞书通知 (含防抖逻辑)
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
cd bull-market-heat-index
uv venv && uv pip install -r requirements.txt

# tushare token (也可写入 ~/daily_stock_analysis/.env)
export TUSHARE_TOKEN="your_token_here"
```

### 历史数据初始化（已完成）

```bash
# baostock 数据（指数/成分股/行业） — 已完成
python scripts/init_history.py

# tushare 数据 — 已完成（step1~5b）
python scripts/step1_bond_yield.py    # 国债收益率 1,894行
python scripts/step2_index_pe.py      # 指数PE/PB 13,845行
python scripts/step3_northbound.py    # 北向资金 2,682行
python scripts/step4_daily_basic.py   # 全市场PE/PB/市值 (后台运行~15分钟)
python scripts/step5b_circ_mv_by_stock.py  # 流通市值补全 (后台运行~5分钟)

# 新增投资者数据（手动/GitHub CSV）
python scripts/import_investors.py --from-github hesievan/stock
```

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

### 自动化 (copaw cron)

```bash
# 已创建定时任务，每交易日 16:30 北京时间触发
# 任务 ID: 63be5c6c-210f-4e2c-8393-414f41e97b3a
copaw cron list                    # 查看任务
copaw cron run 63be5c6c            # 手动触发
```

## 依赖

```
akshare>=1.15.0      # M2月度数据、AH溢价(备用)
baostock>=0.8.8      # 个股/指数行情数据（主力，不限频）
tushare>=1.4.0       # 融资融券/北向/国债/daily_basic (2000积分)
pandas>=2.0.0
numpy>=1.24.0
```

## Git 提交历史

| Commit | 内容 |
|--------|------|
| `83dc995` | fix: 单位修正 + circ_mv 补全 + 全指标验证通过 |
| `7fe654a` | feat: tushare全量数据 + 巴菲特指标/股债比输出修复 |
| `7499fae` | feat: 新增巴菲特指标 + 沪深300股债比 + 批量拉取优化 |
| `9b952b1` | 三源合一 fetcher + 计算引擎改进 |

## 路线图

- [x] Phase 0: 项目骨架 + 三源合一数据层 + 计算引擎 + 前端
- [ ] Phase 1 (6/10): MVP 上线 — 飞书通知 + copaw cron + 历史数据初始化
- [ ] Phase 2 (6/24): 行业热度指数 + 板块热力图 + 更多结构指标

## 关键文档

- 需求 V1.2: https://my.feishu.cn/docx/Rm5Gd4J63oBvoAxSLKpcKNzqn0c
- 评估报告: https://my.feishu.cn/docx/Hs8udA63FoBewIxp9ENcfqk7nBE

## 许可证

MIT
