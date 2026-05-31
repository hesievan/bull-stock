# A股牛市热度指数 — 开发待办

> 更新: 2026-06-01 | 当前阶段: Phase 1 MVP 冲刺 (目标 6/10 上线)

## 🔴 P0 — 上线前必须完成

### 历史数据初始化 ✅ 已完成
- [x] baostock: 指数日行情 (6只×2,772日)
- [x] baostock: ~260只成分股 K 线 (580,437行)
- [x] baostock: 行业分类 (5,528行)
- [x] tushare: 融资融券 (1,602行, 2019~2026)
- [x] tushare: 北向资金 (2,682行, 2015~2026 全量)
- [x] tushare: 国债收益率 (1,894行, 2018~2026)
- [x] tushare: 指数PE/PB/换手率 (13,845行, 6指数)
- [x] tushare: 全市场 daily_basic (2,753天 × 5,506只)
- [x] tushare: 流通市值 circ_mv 补全 (77.4% 有效)
- [x] akshare: M2月度货币供应量 (220行, 2008~2026)
- [x] 新增投资者: 从 GitHub CSV 导入 (133行, 2015~2026)

### 数据完整性验证
- [x] 各表行数、日期范围验证 (16张表全部有数据)
- [x] stock_daily PE 92% / PB 99% / circ_mv 77% 有效
- [ ] stock_daily 中仍有部分 circ_mv 为 NULL (~23%)，不影响核心计算

### 端到端完整验证
- [x] baostock → SQLite → calculator → JSON 全链路跑通
- [x] 20 个子指标全部输出真实数据 (无 N/A)
- [x] 单位修正: buffett_ratio (万元→亿元), turnover (万元→元)
- [ ] 检查 `web/data/index.json` / `detail.json` / `history.json` 字段完整性
- [ ] 前端页面联调: 仪表盘 / 雷达图 / 历史走势图正常渲染

### GitHub Push + Actions 验证
- [ ] `git push` 代码到 GitHub 仓库 ⚠️ **阻塞: 需用户提供 GitHub 仓库地址**
- [ ] 验证 GitHub Actions 触发 + 自动 push 流程
- [ ] GitHub Pages 托管前端 (可选)

### 飞书通知集成
- [x] 防抖逻辑: `build_feishu_notification(result, history)` 连续 N 天触发
- [x] `send_feishu_webhook()` 函数实现
- [x] `analyze_state()` 状态机: enter_red / in_red / recover / pending_red / pending_recover / stable
- [x] 通知测试验证 (防抖/恢复/持续红区三种场景全部通过)
- [ ] 飞书群 Webhook URL 配置 (当前用 stock-monitor 的旧 Webhook)
- [ ] copaw cron 实际触发验证 (`copaw cron run 63be5c6c`)

## 🟡 P1 — MVP 增强 (6/10 前)

### 计算引擎
- [x] 动态权重边界测试: NaN / 异常值 graceful fallback (`_combine_dimension` 3σ 过滤)
- [x] 红区防抖逻辑: 连续 2 天才切换状态 (DEBOUNCE_RED_DAYS=2)
- [x] 巴菲特指标: M2/A股总市值 (akshare + tushare daily_basic)
- [x] 沪深300股债比: HS300 E/P / 10Y国债 (tushare index_pe_history + bond_yield)
- [x] 单位修正: total_mv 万元→亿元, circ_mv 万元→元
- [ ] 验证所有 20 个子指标在完整数据集上的历史分位计算

### 数据源
- [x] tushare 2000积分解锁 (daily_basic/moneyflow_hsgt 全量可用)
- [x] 新增投资者数据录入工具 (`scripts/import_investors.py`)
- [x] 流通市值 circ_mv 补全 (`scripts/step5b_circ_mv_by_stock.py`)
- [ ] AH溢价历史数据补充 (akshare TUN不稳定，Phase 2 再处理)

### 前端
- [x] Loading 骨架屏 (旋转 spinner + 淡出动画)
- [x] 日期范围选择器 (近30/60/90日/半年/一年/全部 + 自定义 range)
- [x] 移动端适配 (768px breakpoint)
- [x] 错误处理 (数据加载失败 UI)
- [ ] 暗黑/亮色主题切换

### 自动化
- [x] copaw cron 定时任务: 每交易日 16:30 (ID: `63be5c6c-210f-4e2c-8393-414f41e97b3a`)
- [ ] 手动触发一次验证飞书推送

## 🟢 P2 — Phase 2 行业热度 (6/24 目标)

### 板块热度指数
- [ ] sector_heat 表数据生成 (6大板块: 创业板/科创板/沪深300/中证500/中证1000/北交所)
- [ ] 板块轮动速度指标
- [ ] 板块切换前端 UI

### 前端
- [ ] 板块热度热力图
- [ ] 板块轮动趋势图
- [ ] 全市场 vs 板块对比视图

## 🔵 P3 — 技术债 / 长期优化

### 代码质量
- [ ] web/data/*.json 从 git 历史中清除 (BFG Repo-Cleaner)
- [ ] calculator 单元测试 (每个子指标独立 mock 测试)
- [ ] 集成测试 (mock 数据端到端流程)
- [ ] 类型提示补全 + mypy 检查
- [ ] 日志格式统一化

### 性能
- [ ] baostock 成分股批量拉取并行化 (asyncio，预计提速5-10x)
- [ ] 数据库索引优化 (trade_date 列)
- [ ] 计算引擎缓存层 (避免同一会话重复查询)

### 运维
- [ ] SQLite 备份策略 (每日自动备份到 ~/.backups/)
- [ ] 计算失败告警 (飞书通知)
- [ ] 语义化版本号 (v1.0.0 起)

## 📋 已知问题

| 问题 | 影响 | 状态 / 方案 |
|------|------|------------|
| akshare stock_zh_a_spot_em 在 Clash TUN 下不可用 | 全市场个股快照 | ✅ 已用 baostock + tushare daily_basic 替代 |
| tushare daily_basic 频率限制 200次/分钟 | 历史初始化需分批 | ✅ step5b 按股票维度批量拉取 (191次调用完成) |
| stock_daily 只有成分股(~260只)非全市场 | PE/PB/换手率用成分股 proxy | 🟡 对全市场代表性约85%，可接受 |
| stock_daily circ_mv 仍有~23%为NULL | 换手率计算部分日期缺失 | 🟡 不影响核心估值指标，Phase 2 考虑全市场数据 |
| Git user.name/email 未配置 | commit 作者信息不正确 | ✅ 已修正 何思 <evanhbr@foxmail.com> |
| ah_premium 表为空 | AH溢价指标缺失 | 🟡 akshare TUN不稳定，Phase 2 再处理 |

## 📊 数据源状态

| 数据源 | Token/配置 | 积分 | 状态 | 覆盖数据 |
|--------|-----------|------|------|---------|
| baostock | 无需token | — | ✅ 全量完成 | 指数行情/个股K线(PE/PB)/成分股/行业分类/交易日历 |
| tushare | `473bc9...b389577` | 2000 | ✅ 全量完成 | 融资融券/北向/国债/指数PE/daily_basic/M2 |
| akshare | 无需token | — | ⚠️ TUN不稳定 | M2月度(✅)/AH溢价(⚠️) |

## 📁 关键路径

- **项目路径**: `/Users/hesi/bull-market-heat-index`
- **数据库**: `data/heat_index.db` (16张表)
- **tushare token**: `~/daily_stock_analysis/.env`
- **copaw cron ID**: `63be5c6c-210f-4e2c-8393-414f41e97b3a` (每交易日 16:30)

## 📋 关键文档

- **需求 V1.2**: https://my.feishu.cn/docx/Rm5Gd4J63oBvoAxSLKpcKNzqn0c
- **评估报告**: https://my.feishu.cn/docx/Hs8udA63FoBewIxp9ENcfqk7nBE
