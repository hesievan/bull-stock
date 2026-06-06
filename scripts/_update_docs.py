#!/usr/bin/env python3
with open('reports/indicator_calculation.md') as f:
    content = f.read()

old_nb = '''### 7. 北向资金累计流入分位

| 项目 | 内容 |
|------|------|
| **计算公式** | `分位 = (历史30日累计净流入 < 当前30日累计净流入) / 历史窗口数 × 100` |
| **数据来源** | tushare `northbound_history.north_net` |
| **计算逻辑** | 1) 取最近30日累计净流入<br>2) 计算历史各30日窗口的累计流入<br>3) 当前值在历史中的分位 |
| **代表意义** | 分位高=外资持续流入=看好A股=高分 |
| **当前值** | 近30日累计=1978万, 分位=100% |'''

new_nb = '''### 7. 北向资金20日累计流入分位

| 项目 | 内容 |
|------|------|
| **计算公式** | `分位 = (历史250日窗口中20日累计 < 当前20日累计) / 窗口数 × 100` |
| **数据来源** | tushare `northbound_history.north_net` |
| **计算逻辑** | 1) 取最近20日累计净流入<br>2) 计算历史250日各窗口的20日累计流入<br>3) 当前值在历史中的分位 |
| **代表意义** | 分位高=外资持续流入=看好A股=高分 |
| **当前值** | 近20日累计=7819万, 分位=99.6% (历史最高) |'''

content = content.replace(old_nb, new_nb)

old_margin = '''### 8. 两融余额/流通市值比

| 项目 | 内容 |
|------|------|
| **计算公式** | `比值 = (融资余额rzye + 融券余额rqye) / 全市场流通市值total_circ_mv` |
| **数据来源** | tushare `margin_history` + `daily_circ_mv` (预计算表) |
| **计算逻辑** | 1) 计算每日两融余额/流通市值比<br>2) 近30日历史分位<br>3) rzye/rqye单位元, circ_mv单位万元, 需转换 |
| **代表意义** | 比值高=杠杆高=牛市特征 |
| **当前值** | 8.47%, 分位=99.2% |'''

new_margin = '''### 8. 融资余额占流通市值比

| 项目 | 内容 |
|------|------|
| **计算公式** | `比值 = 融资余额rzye / 全市场流通市值total_circ_mv` |
| **数据来源** | tushare `margin_history` + `daily_circ_mv` (预计算表) |
| **计算逻辑** | 1) 专注于融资余额(融券余额相对极小)<br>2) 3年历史窗口(750日)<br>3) rzye单位元, circ_mv单位万元, 需转换<br>4) 阈值: <2.5%温和, >4.0%过热 |
| **代表意义** | 比值高=杠杆高=牛市特征 |
| **当前值** | 2.80%, 分位=99.9% (历史最高) |'''

content = content.replace(old_margin, new_margin)

with open('reports/indicator_calculation.md', 'w') as f:
    f.write(content)
print('Updated indicator documentation')
