import sys
import json
sys.path.insert(0, '.')
import logging
logging.basicConfig(level=logging.WARNING)

with open('data/peak_analysis.json') as f:
    data = json.load(f)

# 对比: 脉冲顶(触发红区) vs 牛市顶(未触发)
print("=" * 90)
print("=== 各维度贡献对比 (权重: 估值35% + 资金25% + 情绪20% + 技术15% + 结构5%)")
print("=" * 90)
print()
print("%-10s %-8s  %5s  | %4s*35%% %4s*25%% %4s*20%% %4s*15%% %3s*5%% = %5s" % (
    "日期", "状态", "总分", "估值", "资金", "情绪", "技术", "结构", "总分"))
print("-" * 90)
for d in data:
    v = d['valuation']
    f = d['fund']
    s = d['sentiment']
    t = d['technical']
    st = d['structure']
    c = d['composite']
    print("%-10s %-8s  %5.1f  | %5.1f  %5.1f  %4.1f  %4.1f  %4.1f = %5.1f" % (
        d['date'], d['desc'], c, v*0.35, f*0.25, s*0.20, t*0.15, st*0.05, c))

print()
print("=" * 90)
print("=== 差距分析: 牛市顶 vs 脉冲顶 (差多少才能到70?)")
print("=" * 90)
print()
surge = next(d for d in data if d['composite'] >= 70)
for d in data:
    if d['composite'] >= 70:
        continue
    gap = 70 - d['composite']
    print("%s %s: 综合 %.1f, 差 %.1f 到红区" % (d['date'], d['desc'], d['composite'], gap))
    # 每个维度需要提升多少才能弥补差距
    for dim_name, weight in [('valuation', 0.35), ('fund', 0.25), ('sentiment', 0.20), ('technical', 0.15)]:
        dim_val = d[dim_name]
        needed = gap / weight
        print("  -> %s 需从 %.0f 提升到 %.0f (+%.0f)" % (dim_name, dim_val, dim_val + needed, needed))
    print()

print("=" * 90)
print("=== 子指标问题诊断: 哪些子指标异常?")
print("=" * 90)
print()
for d in data:
    print("%s %s:" % (d['date'], d['desc']))
    # 估值
    vd = d['valuation_detail']
    if vd.get('PE_percentile', 0) < 50:
        print("  [!] PE分位仅 %.0f -- 全市场小盘股拉高分母" % vd['PE_percentile'])
    if vd.get('equity_bond_ratio') is None:
        print("  [!] 股债比 = N/A -- 国债收益率缺失")
    if vd.get('below_net_rate', 100) > 70:
        print("  [~] 净资产以下 %.0f%% -- 高值但拉低估值分位")
    # 资金
    fd = d['fund_detail']
    if fd.get('margin_ratio') is None:
        print("  [!] 融资比 = N/A -- 2015年融资融券数据缺失")
    if fd.get('northbound', 0) < 70:
        print("  [~] 北向占比仅 %.0f" % fd['northbound'])
    # 情绪
    sd = d['sentiment_detail']
    if sd.get('up_down_ratio', 100) < 50:
        print("  [!] 涨跌家数比仅 %.0f -- 多数股票下跌" % sd['up_down_ratio'])
    if sd.get('limit_down_ratio', 0) > 50:
        print("  [!] 跌停比例 %.0f -- 市场恐慌" % sd['limit_down_ratio'])
    # 技术
    td = d['technical_detail']
    if td.get('new_high_ratio', 0) == 0:
        print("  [!!!] new_high_ratio = 0 -- BUG: 该指标永远为0")
    if td.get('deviation_ma250') is None:
        print("  [!] deviation_ma250 = N/A -- 2015年数据不足")
    if td.get('above_ma250_ratio', 100) < 30:
        print("  [~] 年线上方仅 %.0f%% -- 多数股票未走牛" % td['above_ma250_ratio'])
    print()
