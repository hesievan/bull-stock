# A股牛市热度指数 — 开发待办

> 更新: 2026-05-31 | 当前阶段: Phase 1 MVP 冲刺 (目标 6/10 上线)

## 🔴 P0 — 上线前必须完成

### 历史数据初始化
- [ ] 执行 `python scripts/init_history.py` 拉取全量历史数据
  - baostock: 指数日行情 + ~850只成分股K线 + 行业分类 (预计30-60分钟)
  - tushare: 融资融券 + 北向资金 + 国债收益率 (注意1次/小时频率限制)
  - akshare: AH溢价 (如果 TUN 环境不稳定可跳过，后续补)
- [ ] 数据完整性验证: 检查各表行数、日期范围、NULL 值比例
  ```bash
  python -c "
  import sqlite3
  conn = sqlite3.connect('data/heat_index.db')
  for t in ['index_daily','stock_daily','margin_history','northbound_history','bond_yield','stock_industry']:
      r = conn.execute(f'SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM {t}').fetchone()
      print(f'{t:25} rows={r[0]:8}  {r[1]} ~ {r[2]}')
  conn.close()
  ```

### 端到端完整验证
- [ ] 历史数据初始化后，跑 `python scripts/run_daily.py` 验证所有 18 个子指标
- [ ] 检查 `web/data/index.json` / `detail.json` / `history.json` 字段完整性
- [ ] 前端页面联调: 仪表盘 / 雷达图 / 历史走势图正常渲染

### GitHub Push + Actions 验证
- [ ] `git push` 代码到 GitHub 仓库
- [ ] 验证 GitHub Actions 触发 + 自动 push 流程
- [ ] GitHub Pages 托管前端 (可选)

### 飞书通知集成
- [ ] Webhook 方式接入飞书群通知
- [ ] 红区(≥70) / 恢复(连续<70) 消息测试

## 🟡 P1 — MVP 增强 (6/10 前)

### 计算引擎优化
- [ ] 验证所有 18 个子指标在完整数据集上的历史分位计算
- [ ] 动态权重边界测试: 某指标 NaN / 异常值时的 graceful fallback
- [ ] 红区防抖逻辑: 连续 N 天才切换状态(避免噪音)

### 数据源补充
- [ ] 新增投资者数据录入 (中国结算月度 PDF/Excel → 手动录入)
- [ ] AH溢价历史数据补充 (akshare 不稳定，考虑备用方案)

### 前端适配
- [ ] 移动端布局适配
- [ ] Loading / 空数据状态展示
- [ ] 历史走势日期范围选择器

### 自动化 (copaw cron)
- [ ] 配置 copaw cron 定时任务: 每交易日 16:30 触发
```bash
copaw cron add "A股热度指数" "cd /Users/hesi/bull-market-heat-index && .venv/bin/python scripts/run_daily.py" --schedule "30 16 * * 1-5" --tz "Asia/Shanghai"
```

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
| akshare stock_zh_a_spot_em 在 Clash TUN 下不可用 | 全市场个股快照 | ✅ 已用 baostock peTTM/pbMRQ 替代 |
| tushare 频率限制 1次/小时 | 融资融券/北向/国债只能日更一次 | ✅ run_daily 已检查当日数据是否存在则跳过 |
| stock_daily 只有成分股(~850只)非全市场 | PE/PB 中位数用成分股 proxy | 🟡 对全市场代表性约85%，可接受 |
| Git user.name/email 未配置 | commit 作者信息不正确 | 🟡 待修正 `git config --global` |
| ⏳ baostock 成分股历史K线批量拉取耗时 | 初始化需30-60分钟 | 🟡 Phase 2 并行化改进 |

## 📊 数据源状态

| 数据源 | Token/配置 | 状态 | 覆盖数据 |
|--------|-----------|------|---------|
| baostock | 无需token | ✅ 已验证可用 | 个股K线(PE/PB)/指数/成分股/行业分类 |
| tushare | `473bc9...b389577` | ✅ 已验证可用 | 融资融券/北向/国债/指数PE |
| akshare | 无需token | ⚠️ TUN环境不稳定 | AH溢价(备用) |

## 📁 关键文档

- **需求 V1.2**: https://my.feishu.cn/docx/Rm5Gd4J63oBvoAxSLKpcKNzqn0c
- **评估报告**: https://my.feishu.cn/docx/Hs8udA63FoBewIxp9ENcfqk7nBE
- **项目路径**: `/Users/hesi/bull-market-heat-index`
- **数据库路径**: `data/heat_index.db`
- **tushare token**: `~/daily_stock_analysis/.env`
