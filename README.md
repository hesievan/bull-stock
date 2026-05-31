# A股牛市热度指数

🌡️ 每日更新的量化指标，从估值、资金、情绪、技术、结构五个维度综合评估A股市场整体热度。

**定位：仅提示离场/减仓，不发出进场或加仓信号。**

## 项目结构

```
├── src/
│   ├── data/
│   │   ├── database.py      # SQLite 数据库管理
│   │   └── fetcher.py       # akshare 数据获取
│   ├── indicators/
│   │   └── calculator.py    # 18个子指标 + 综合热度计算
│   └── output/
│       └── json_writer.py   # JSON 输出 + 飞书通知生成
├── scripts/
│   └── run_daily.py         # 每日计算入口
├── web/
│   ├── index.html           # 前端页面（纯静态）
│   └── data/                # 每日生成的 JSON 数据
├── config/                  # 配置文件
├── .github/workflows/
│   └── daily.yml            # GitHub Actions 自动更新
└── requirements.txt
```

## 使用

### 初始化（一次性）
```bash
pip install -r requirements.txt
python -m src.data.database    # 初始化数据库
python scripts/run_daily.py --backfill 2015-01-01  # 回测历史
```

### 每日运行
```bash
python scripts/run_daily.py              # 计算今日
python scripts/run_daily.py 2026-05-30   # 计算指定日期
```

### 自动部署
推送到 GitHub 后，GitHub Actions 会在每个交易日 16:30（北京时间）自动运行。

## 数据架构

- 本地 SQLite 数据库存储所有历史数据
- 每日增量更新（不再全量拉取）
- 支持异常检测 + 动态权重调整

## 指标说明

| 维度 | 指标数 | 子指标 |
|------|--------|--------|
| 估值 | 4 | PE分位、PB分位、破净率、ERP |
| 资金 | 2 | 融资买入占比、北向资金 |
| 情绪 | 6 | 换手率、涨停占比、创新高占比、新增投资者、站上年线比例、涨跌家数比 |
| 技术 | 5 | 偏离年线、波动率变化、RSI、创新高新低差、量价背离(P2) |
| 结构 | 3 | AH溢价、等权加权涨幅差、行业轮动速度(P2) |

## 许可证

MIT
