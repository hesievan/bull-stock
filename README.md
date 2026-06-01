# A股牛市热度指数

🌡️ 每日更新的量化指标，从估值、资金、情绪、技术、结构五个维度综合评估A股市场整体热度。

> **定位：仅提示离场/减仓，不发出进场或加仓信号。**

## 项目结构

```
├── src/
│   ├── data/
│   │   ├── database.py      # SQLite 数据库管理 (18张表)
│   │   └── fetcher.py       # 三源合一数据获取 (baostock+tushare+akshare)
│   ├── indicators/
│   │   └── calculator.py    # 20个子指标 + 动态权重 + 综合热度合成
│   └── output/
│       └── json_writer.py   # JSON 输出 + 飞书通知生成
├── scripts/
│   ├── run_daily.py         # 每日计算入口
│   ├── init_history.py      # 历史数据一次性初始化 (baostock)
│   ├── import_investors.py  # 新增投资者数据录入
│   ├── fetch_hist_constituents.py  # 拉取历史成分股截面 (tushare index_weight)
│   ├── precompute_const_pe.py      # 预计算每日成分股PE/PB中位数
│   ├── verify_all_fixes.py # 回测验证脚本
│   ├── step1_bond_yield.py  # tushare 国债收益率拉取
│   ├── step2_index_pe.py    # tushare 指数PE/PB历史
│   ├── step3_northbound.py  # tushare 北向资金全量
│   ├── step4_daily_basic.py # tushare 全市场PE/PB/市值
│   └── step5b_circ_mv_by_stock.py  # tushare 流通市值补全
├── web/
│   ├── index.html           # 前端页面（深色主题 + ECharts）
│   └── data/                # 每日生成的 JSON 数据 (gitignore)
├── docs/
│   └── pe_pb_solution.md    # PE/PB分位数方案决策记录
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
| **baostock** | 指数日行情、个股K线(PE/PB/价格/成交量)、成分股列表、行业分类、交易日历 | 不限频，控制间隔 0.3s | `sh600000` |
| **tushare** | 融资融券、北向资金、国债收益率、指数PE/PB、全市场daily_basic(PE/PB/总市值/流通市值)、历史成分股(index_weight) | 200次/分钟(daily_basic)，其他不限 | `600000.SH` |
| **akshare** | M2月度货币供应量、AH溢价（备用）、2015-2019融资融券补充 | 不稳定(TUN环境) | `sh000001` |

内部统一使用 baostock 格式（`sh600000`），函数 `ak_to_bs()`/`ak_to_ts()`/`bs_to_ak()` 自动转换。

### baostock 覆盖的数据
- ✅ 指数日行情 (open/high/low/close/volume/amount/pctChg)
- ✅ 个股K线 (peTTM/pbMRQ/pctChg/volume/amount)
- ✅ 成分股列表 (hs300/sz50/zz500)
- ✅ 行业分类 (证监会行业分类)
- ✅ 交易日历
- ✅ 资产负债表 (bps/净资产)
- ❌ 融资融券、北向资金、总市值

### tushare 覆盖的数据
- ✅ 融资融券日汇总 (`margin`: rzye/rzmre/rqye/rzrqye) — 2019年至今
- ✅ 北向资金净流入 (`moneyflow_hsgt`: hgt/sgt) — 2015年至今全量
- ✅ 中债国债收益率 (`yc_cb`: 10年期) — 2018年至今
- ✅ 指数PE/PB/总市值 (`index_dailybasic`) — 2015年至今
- ✅ 全市场个股PE/PB/市值 (`daily_basic`) — 2015年至今
- ✅ 历史成分股截面 (`index_weight`: 沪深300+中证500 每月末) — 2015~2026

### akshare 覆盖的数据
- ✅ M2月度货币供应量 (`macro_china_money_supply`)
- ✅ 融资融券补充 (`stock_margin_sse`: 2015-01~2019-01)
- ⚠️ AH溢价 (`stock_zh_ah_spot_em`): TUN环境下不稳定，仅做补充

## 指标体系 (5维度 20子指标)

> **设计原则**: 每个子指标都经过"牛市顶应高分、熊底应低分"的回测验证。
> 指标方向: 数值越高 = 市场越热 = 越应警惕。

### 估值维度 (4项)

| # | 指标 | 数据源 | 口径 | 标准化 | 选取意义 |
|---|------|--------|------|--------|---------|
| 1 | **PE历史分位** | stock_daily.peTTM | 沪深300+中证500成分股口径，用历史成分股截面(月末)避免 survivorship bias | 10年分位 | 全市场口径被小盘股低PE稀释（银行PE=5-10拉低中位数），成分股口径更准确反映核心资产估值贵贱 |
| 2 | **PB历史分位** | stock_daily.pbMRQ | 同上 | 10年分位 | 与PE互补，PE受短期盈利波动影响大，PB更稳定；2021年牛市PE分位高但PB分位低（结构性分化），两者结合更全面 |
| 3 | **破净率** | stock_daily.pbMRQ | 全市场口径，PB<1占比 | 10年分位(反向) | 破净率高=市场便宜=低分；牛市顶峰几乎无破净股→得高分；全市场口径因破净本身就是市场整体现象 |
| 4 | **巴菲特指标** | M2 / A股总市值 | akshare M2 + tushare总市值 | 10年分位 | 总市值/GDP的变体，用M2替代GDP更及时；衡量市场整体杠杆和泡沫程度 |

> **PE/PB口径演进**: 全市场简单中位数(被小盘股稀释) → 市值加权(被大票低PE拉低失真) → 截尾中位数(与简单中位数几乎一样) → **历史成分股口径(最终方案)**。详见 [docs/pe_pb_solution.md](docs/pe_pb_solution.md)

### 资金维度 (2项)

| # | 指标 | 数据源 | 标准化 | 选取意义 |
|---|------|--------|--------|---------|
| 7 | **融资买入占比** | tushare margin.rzmre / 全市场成交额 | 10年分位 | 杠杆资金情绪的直接度量；2015杠杆牛中融资余额比达历史峰值，是牛市核心驱动力 |
| 8 | **北向资金方向** | tushare moneyflow_hsgt | 近20日净买入比 | 外资对A股的配置意愿；持续净流入=看好=高分，持续净流出=看空=低分 |

### 情绪维度 (5项)

| # | 指标 | 数据源 | 标准化 | 选取意义 |
|---|------|--------|--------|---------|
| 9 | **换手率** | amount / circ_mv | 10年分位 | 市场活跃度的最直接指标；牛市顶峰换手率极高（2015年日换手率>3%） |
| 10 | **上涨/下跌家数比** | pctChg>0 占比 / pctChg<0 占比 | 10年分位 | 市场广度指标；牛市普涨时比值高，结构性牛市时比值低（2021年仅0.39） |
| 11 | **涨停占比** | pctChg ≥ 9.9% | 10年分位 | 极端乐观情绪；涨停潮=情绪过热=高分 |
| 12 | **跌停占比** | pctChg ≤ -9.9% | 10年分位(反向) | 极端恐慌情绪；跌停潮=情绪过冷=低分（反向计分） |
| 13 | **波动率** | 20日收益率标准差 | 10年分位 | VIX替代指标；高波动=分歧大/恐慌=高分（牛末熊初波动均大） |

### 技术维度 (4项)

| # | 指标 | 数据源 | 标准化 | 选取意义 |
|---|------|--------|--------|---------|
| 14 | **站上年线比例** | close > MA250 占比 | 静态分位 | 市场整体趋势强度；牛市末期>90%，熊市末期<10% |
| 15 | **创新高占比** | close = 250日最高 占比 | 静态分位 | 市场动量；牛市顶峰大量股票创新高，熊市几乎为0 |
| 16 | **均线偏离度** | close / MA250 - 1 | 10年分位 | 价格偏离长期均值的程度；过度偏离=泡沫=高分 |
| 17 | **量价背离** | 价格趋势 vs 量比 | 状态打分 | 价涨量缩=背离=高分(牛市末期常见)，价跌量缩=低分 |

### 结构维度 (1项+预留)

| # | 指标 | 数据源 | 标准化 | 选取意义 |
|---|------|--------|--------|---------|
| 18 | **行业分化度** | 各行业pctChg 标准差 | 静态阈值(反向) | 低分化(普涨)=全面牛市=高分；高分化(结构性)=局部行情=低分 |
| 19 | **AH溢价** *(预留)* | akshare | 10年分位 | AH溢价高=A股相对H股贵=高分 |
| 20 | **新增投资者** *(预留)* | 中国结算月度 | 10年分位 | 新开户数=场外资金入场意愿；牛市顶峰开户激增 |

### 热度区间

| 颜色 | 分数 | 含义 |
|------|------|------|
| 🟢 绿色安全 | 0–40 | 估值合理/偏低，情绪冷淡，减仓信号远 |
| 🟡 黄色警惕 | 40–70 | 部分指标偏高，需关注 |
| 🔴 红色预警 | 70–100 | 多项指标历史高位，考虑减仓/离场 |

### 权重规则

- **维度内**: 等权合成，若某子指标异常(>3σ)或为0/None则舍弃，其余重新等权归一
- **维度间**: 等权合成（估值/资金/情绪/技术/结构 各占20%）
- **防抖**: 红区连续2天才发"进入红区"通知，恢复1天后发"脱离红区"通知

## 数据库表结构 (18张表)

```
index_daily             — 指数日行情 (trade_date, index_code, open/high/low/close/volume/amount/pct_change)
stock_daily             — 个股日行情 (trade_date, stock_code, open/high/low/close, peTTM, pbMRQ, pct_change, volume, amount, total_mv, circ_mv)
stock_industry          — 个股行业分类 (code, code_name, industry, industry_classification, update_date)
stock_balance           — 个股资产负债表 (stock_code, report_date, bps)
margin_history          — 融资融券汇总 (trade_date, rzye, rzmre, rzche, rqye, rqmcl, rzrqye)
northbound_history      — 北向资金 (trade_date, hgt, sgt, north_net, south_money)
bond_yield              — 国债收益率 (trade_date, curve_term, yield_rate)
index_pe_history        — 指数PE/PB/总市值/换手率 (trade_date, index_code, pe_ttm, pb, total_mv, turnover_rate)
m2_monthly              — M2月度货币供应量 (month, m2_billion, m2_yoy)
stock_market_cap        — 全市场每日总市值 (trade_date, total_mv, stock_count)
limit_up_daily          — 涨停明细 (trade_date, stock_code)
ah_premium              — AH溢价 (trade_date, premium)
new_investors           — 新增投资者 (week_end_date, new_accounts)
heat_index              — 热度指数结果 (trade_date, composite_score, dim_*, detail_json)
sector_heat             — 板块热度 Phase 2 (trade_date, sector_code, composite_score, detail_json)
metadata                — 元数据 (key, value, updated_at)
index_constituents      — 当前成分股列表 (index_code, stock_code, stock_name, update_date)
index_constituents_hist — 历史成分股截面 (index_code, con_code, trade_date, weight)
index_daily_pe          — 每日成分股PE/PB中位数预计算 (trade_date, pe_med, pb_med, n_stocks, const_date)
```

### 新增表说明 (2026-06-01)

**index_constituents_hist** — 历史成分股截面
- 来源: tushare `index_weight` 接口，每月末截面
- 数据: 138个月末 × 2指数(hs300+zz500) = 106,000行
- 用途: 避免 survivorship bias（用当前成分股回看历史数据不准确）

**index_daily_pe** — 每日成分股PE/PB中位数预计算
- 计算: 对每个交易日，用最近月末成分股截面计算PE/PB中位数
- 数据: 2,750行（2015-01-30 ~ 2026-05-29）
- 用途: 将PE/PB分位数计算从实时groupby(>60s)优化到查表(<2s)

## 数据完整性 (2026-06-01 快照)

| 表 | 行数 | 日期范围 | 备注 |
|----|------|---------|------|
| index_daily | 16,632 | 2014-12-29 ~ 2026-05-29 | 6指数 × 2,772日 |
| stock_daily | ~14,000,000 | 2015-01-05 ~ 2026-05-29 | 全市场~5,200只/日，PE/PB有效率>95% |
| stock_industry | 5,528 | — | 84个行业 |
| margin_history | 1,602 | 2019-10-21 ~ 2026-05-29 | akshare补充2015-2019数据(984行) |
| northbound_history | 2,682 | 2015-01-05 ~ 2026-05-29 | 全量 |
| bond_yield | 1,894 | 2018-12-30 ~ 2026-05-29 | |
| index_pe_history | 13,845 | 2015-01-05 ~ 2026-05-29 | 6指数 |
| m2_monthly | 220 | 2008-01 ~ 2026-04 | |
| stock_market_cap | 2,753 | 2015-01-05 ~ 2026-05-29 | |
| index_constituents_hist | 106,000 | 2015-03 ~ 2026-06 | 138月末截面 × 2指数 |
| index_daily_pe | 2,750 | 2015-01-30 ~ 2026-05-29 | 成分股PE/PB中位数预计算 |
| new_investors | 133 | 2015-04 ~ 2026-04 | |

## 计算流程

```
交易日 16:30 触发 (copaw cron / GitHub Actions)
  │
  ├─ Step 1: baostock 拉取指数日行情 (增量)
  ├─ Step 2: baostock 拉取~800只成分股当日K线
  ├─ Step 3: tushare daily_basic 全市场PE/PB/市值
  ├─ Step 4: tushare 融资融券/北向/国债 (当日已存在则跳过)
  │
  ├─ Step 5: calculate_heat_index()
  │     ├─ 计算 20 个子指标当前值
  │     ├─ PE/PB/ERP 查预计算表 index_daily_pe (O(1))
  │     ├─ 与 10 年历史对比 → 分位数 (0-100)
  │     ├─ Z-score 异常过滤 (3σ)
  │     ├─ 维度内等权合成 → 5维度分数
  │     └─ 维度间等权合成 → 综合热度 (0-100)
  │
  ├─ Step 6: save_results() → web/data/index.json + detail.json + history.json
  └─ Step 7: 红区(≥70) → 飞书通知 (含防抖: 连续2天才发)
```

## 回测验证

| 日期 | 市场状态 | 综合得分 | 状态 | 合理性 |
|------|---------|---------|------|--------|
| 2015-06-12 | 牛市顶(5178) | **71.8** | 🔴红区 | ✅ 全面泡沫，估值/资金/情绪/技术全高分 |
| 2020-07-10 | 牛市启动(3450) | **66.1** | 🟡黄区 | ✅ 资金面极强，估值/技术刚起步 |
| 2021-02-18 | 牛市顶(3731) | **63.5** | ⚪中性 | ✅ 结构性牛市，核心资产贵但银行/地产便宜 |
| 2024-10-08 | 脉冲顶(3489) | **63.2** | ⚪中性 | ✅ 政策脉冲，情绪极高但估值刚从底部反弹 |
| 2018-12-28 | 熊底(2493) | **32.0** | 🟢绿区 | ✅ 估值/情绪/技术全面低迷 |
| 2025-05-29 | 当前(3348) | **58.4** | 🟡黄区 | ✅ 资金面强，但估值/情绪/技术中性 |

## 使用指南

### 环境配置

```bash
cd bull-market-heat-index
uv venv && uv pip install -r requirements.txt
export TUSHARE_TOKEN="your_token_here"  # 或写入 ~/daily_stock_analysis/.env
```

### 每日运行

```bash
python scripts/run_daily.py                 # 计算今日
python scripts/run_daily.py 2026-05-29      # 计算指定日期
python scripts/run_daily.py --backfill      # 历史回测(2015-01-01起)
```

### 回测验证

```bash
python scripts/verify_all_fixes.py          # 快速回测关键日期
```

### 前端预览

```bash
cd web && python -m http.server 8080
```

### 自动化 (copaw cron)

```bash
copaw cron list                    # 查看任务
copaw cron run 63be5c6c            # 手动触发 (任务ID: 63be5c6c)
```

## 依赖

```
akshare>=1.15.0      # M2月度数据、融资融券补充
baostock>=0.8.8      # 个股/指数行情数据（主力，不限频）
tushare>=1.4.0       # 融资融券/北向/国债/daily_basic/成分股
pandas>=2.0.0
numpy>=1.24.0
```

## Git 提交历史

| Commit | 内容 |
|--------|------|
| `8cfc756` | docs: PE/PB分位数方案决策记录 |
| `f9b8255` | fix: PE/PB分位数改用历史成分股口径 + 修复指标方向 |
| `ac5a5f0` | P1修复: akshare融资补充 + 回退PE加权方案 |
| `7b9ee8b` | fix: new_high_ratio 改用close代替high |
| `bf03c58` | 全市场PE/PB回填完成 (2769天, 1099万行) |
| `83dc995` | fix: 单位修正 + circ_mv 补全 + 全指标验证通过 |
| `7fe654a` | feat: tushare全量数据 + 巴菲特指标/股债比输出修复 |
| `7499fae` | feat: 新增巴菲特指标 + 沪深300股债比 + 批量拉取优化 |
| `9b952b1` | 三源合一 fetcher + 计算引擎改进 |

## 路线图

- [x] Phase 0: 项目骨架 + 三源合一数据层 + 计算引擎 + 前端
- [x] Phase 1 (6/10): MVP 上线 — 全市场数据扩展 + 历史成分股口径 + 指标方向修复
- [ ] Phase 2 (6/24): 行业热度指数 + 板块热力图 + 更多结构指标
- [ ] Phase 3: 单元测试 + GitHub Actions + 文档完善

## 关键文档

- 需求 V1.2: https://my.feishu.cn/docx/Rm5Gd4J63oBvoAxSLKpcKNzqn0c
- 评估报告: https://my.feishu.cn/docx/Hs8udA63FoBewIxp9ENcfqk7nBE
- PE/PB方案决策: [docs/pe_pb_solution.md](docs/pe_pb_solution.md)

## 许可证

MIT
