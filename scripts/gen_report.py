#!/usr/bin/env python3
"""生成牛市热度指数日报 (MD + HTML)"""
import json, os, sys
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'web', 'data')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'reports')

os.makedirs(OUTPUT_DIR, exist_ok=True)

with open(os.path.join(DATA_DIR, 'index.json')) as f:
    idx = json.load(f)
with open(os.path.join(DATA_DIR, 'detail.json')) as f:
    det = json.load(f)
with open(os.path.join(DATA_DIR, 'sectors.json')) as f:
    sectors = json.load(f)
with open(os.path.join(DATA_DIR, 'history.json')) as f:
    hist = json.load(f)
with open(os.path.join(DATA_DIR, 'run_status.json')) as f:
    status = json.load(f)

trade_date = idx['trade_date']
score = idx['composite_score']
level = idx['level']
now_str = datetime.now().strftime('%Y-%m-%d %H:%M')

LEVEL_EMOJI = {'red': '🔴', 'yellow': '🟡', 'green': '🟢'}
LEVEL_CN = {'red': '红色预警', 'yellow': '黄色警惕', 'green': '绿色安全'}
level_emoji = LEVEL_EMOJI.get(level, '⚪')
level_cn = LEVEL_CN.get(level, '未知')

# ── 维度 ──
DIM_LABELS = {'valuation': '估值', 'fund': '资金', 'sentiment': '情绪', 'technical': '技术', 'structure': '结构'}
dim_scores = {k: v['score'] for k, v in idx['dimensions'].items()}

def bar(v, width=20):
    if v is None: return '░' * width
    filled = int(v / 100 * width)
    return '█' * filled + '░' * (width - filled)

# ── 历史走势 (近30天) ──
hist30 = hist[-30:] if len(hist) >= 30 else hist

def sparkline(values, height=4, width=30):
    """ASCII sparkline"""
    if not values: return ''
    mn, mx = min(values), max(values)
    if mx == mn: mx = mn + 1
    chars = '▁▂▃▄▅▆▇█'
    # 采样到 width 个点
    if len(values) > width:
        step = len(values) / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = values
    result = ''
    for v in sampled:
        idx_char = int((v - mn) / (mx - mn) * (len(chars) - 1))
        result += chars[min(idx_char, len(chars) - 1)]
    return result

# ── 板块 TOP10 ──
sectors_sorted = sorted([s for s in sectors if s.get('composite_score') is not None],
                        key=lambda x: x['composite_score'], reverse=True)
top10 = sectors_sorted[:10]
hot5 = [s for s in sectors_sorted if s.get('heat_label') == 'hot'][:5]
cold5 = [s for s in reversed(sectors_sorted) if s.get('heat_label') == 'cold'][:5]

# ── 子指标 ──
ind = det.get('indicators', {})

# ══════════════════════════════════════════════════════════════
# MD 版
# ══════════════════════════════════════════════════════════════
md_lines = []
md_lines.append(f"# 📊 A股牛市热度指数日报")
md_lines.append(f"")
md_lines.append(f"> **交易日**: {trade_date}  |  **生成时间**: {now_str}  |  **报告周期**: 日频")
md_lines.append(f"")
md_lines.append(f"---")
md_lines.append(f"")
md_lines.append(f"## 综合热度: {level_emoji} {score}  {level_cn}")
md_lines.append(f"")
md_lines.append(f"{bar(score)} {score:.0f}/100")
md_lines.append(f"")

# 与昨日对比
if len(hist) >= 2:
    prev = hist[-2]['composite_score']
    delta = score - prev
    arrow = '↑' if delta > 0 else ('↓' if delta < 0 else '→')
    md_lines.append(f"**较上一交易日 ({hist[-2]['trade_date']}): {arrow} {abs(delta):.1f} 分**")
    md_lines.append(f"")

md_lines.append(f"---")
md_lines.append(f"")
md_lines.append(f"## 维度拆解")
md_lines.append(f"")
md_lines.append(f"| 维度 | 得分 | 评估 |")
md_lines.append(f"|------|------|------|")
for k, label in DIM_LABELS.items():
    s = dim_scores.get(k)
    if s is None:
        md_lines.append(f"| {label} | — | 数据暂缺 |")
        continue
    if s >= 70: eval_text = '🔴 偏高'
    elif s >= 40: eval_text = '🟡 中性'
    else: eval_text = '🟢 偏低'
    md_lines.append(f"| {label} | {s:.0f} | {eval_text} |")
md_lines.append(f"")
md_lines.append(f"```")
for k, label in DIM_LABELS.items():
    s = dim_scores.get(k)
    v_str = f'{s:5.1f}' if s is not None else '  — '
    md_lines.append(f"  {label}  {bar(s)}  {v_str}")
md_lines.append(f"```")
md_lines.append(f"")

# 子指标亮点
md_lines.append(f"### 关键指标")
md_lines.append(f"")

vi = ind.get('valuation', {})
fi = ind.get('fund', {})
si = ind.get('sentiment', {})
ti = ind.get('technical', {})
sti = ind.get('structure', {})

# 共享 highlights (纯文本)
_hl = []
if vi.get('PE_percentile') is not None:
    _hl.append(f"PE历史分位 {vi['PE_percentile']:.0f}% (近10年)")
if vi.get('PB_percentile') is not None:
    _hl.append(f"PB历史分位 {vi['PB_percentile']:.0f}% (近10年)")
if vi.get('below_net_rate') is not None:
    _hl.append(f"破净率 {vi['below_net_rate']:.1f}%")
if fi.get('northbound') is not None:
    _hl.append(f"北向资金250日分位 {fi['northbound']:.0f}%")
if si.get('up_down_ratio') is not None:
    _hl.append(f"涨跌家数比分位 {si['up_down_ratio']:.0f}%")
if si.get('limit_up_ratio') is not None:
    _hl.append(f"涨停家数比分位 {si['limit_up_ratio']:.0f}%")
if ti.get('new_high_ratio') is not None:
    nh = ti['new_high_ratio']
    _hl.append(f"250日新高比例 {nh:.1f}% ({'偏低' if nh < 5 else ('正常' if nh < 15 else '偏高')})")
if ti.get('above_ma250_ratio') is not None:
    _hl.append(f"站上年线比例 {ti['above_ma250_ratio']:.1f}%")
if ti.get('deviation_ma250') is not None:
    _hl.append(f"上证250日均线偏离度分位 {ti['deviation_ma250']:.0f}%")
if sti.get('sector_divergence') is not None:
    _hl.append(f"行业分化度 {sti['sector_divergence']:.0f}分 ({'低分化/普涨' if sti['sector_divergence'] >= 60 else '高分化/结构性'})")

# MD 版: 加粗关键数字
highlights_md = []
for h in _hl:
    # 给数字加粗
    import re
    h_bold = re.sub(r'([\d.]+%)', r'**\1**', h)
    h_bold = re.sub(r'([\d.]+分)', r'**\1**', h_bold)
    highlights_md.append(h_bold)

for h in highlights_md:
    md_lines.append(f"- {h}")

# HTML 版
highlines_html = ''
for h in _hl:
    highlines_html += f'<li>{h}</li>\n'
md_lines.append(f"")

# 板块热度
md_lines.append(f"---")
md_lines.append(f"")
md_lines.append(f"## 板块热度 TOP10")
md_lines.append(f"")
md_lines.append(f"| 排名 | 行业 | 得分 | 状态 | 龙头股 | 涨跌幅 |")
md_lines.append(f"|------|------|------|------|--------|--------|")
for i, s in enumerate(top10, 1):
    sc = s.get('composite_score', 0)
    label = '🔴热' if sc >= 70 else ('🟡温' if sc >= 40 else '🟢冷')
    ldr = s.get('leader', {})
    ldr_code = ldr.get('code', '—') if ldr else '—'
    ldr_pct = f"{ldr.get('pct', 0):+.1f}%" if ldr else '—'
    sname = s.get('sector_name', s.get('industry', ''))
    md_lines.append(f"| {i} | {sname} | {sc:.0f} | {label} | {ldr_code} | {ldr_pct} |")
md_lines.append(f"")

if hot5:
    hot_names = '、'.join([s.get('sector_name', '') for s in hot5])
    md_lines.append(f"🔥 **热门行业**: {hot_names}")
if cold5:
    cold_names = '、'.join([s.get('sector_name', '') for s in cold5])
    md_lines.append(f"❄️ **冷门行业**: {cold_names}")
md_lines.append(f"")

# 历史走势
md_lines.append(f"---")
md_lines.append(f"")
md_lines.append(f"## 历史走势 (近{len(hist30)}个交易日)")
md_lines.append(f"")
hist_scores = [h['composite_score'] for h in hist30]
md_lines.append(f"```")
md_lines.append(f"  100 ┤")
md_lines.append(f"      │  {sparkline(hist_scores)}")
md_lines.append(f"    0 ┤")
md_lines.append(f"      └{'─' * 30}")
md_lines.append(f"       {hist30[0]['trade_date'][5:]}  →  {hist30[-1]['trade_date'][5:]}")
md_lines.append(f"```")
md_lines.append(f"")

# 近10天表格
md_lines.append(f"| 日期 | 得分 | 状态 |")
md_lines.append(f"|------|------|------|")
for h in hist[-10:]:
    le = LEVEL_EMOJI.get(h['level'], '⚪')
    md_lines.append(f"| {h['trade_date']} | {h['composite_score']} | {le} {h['level']} |")
md_lines.append(f"")

# 回测参考
md_lines.append(f"---")
md_lines.append(f"")
md_lines.append(f"## 历史参考")
md_lines.append(f"")
md_lines.append(f"| 日期 | 市场状态 | 上证 | 综合得分 |")
md_lines.append(f"|------|---------|------|---------|")
md_lines.append(f"| 2015-06-12 | 牛市顶 | 5178 | 🔴 73.8 |")
md_lines.append(f"| 2021-02-18 | 牛市顶 | 3731 | ⚪ 66.2 |")
md_lines.append(f"| 2024-10-08 | 脉冲顶 | 3489 | ⚪ 65.1 |")
md_lines.append(f"| 2018-12-28 | 熊底 | 2493 | 🟢 28.5 |")
md_lines.append(f"| **{trade_date}** | **当前** | **—** | **{level_emoji} {score}** |")
md_lines.append(f"")

# 运行状态
md_lines.append(f"---")
md_lines.append(f"")
md_lines.append(f"## 运行状态")
md_lines.append(f"")
n_ok = sum(1 for v in status['steps'].values() if v['status'] == 'OK')
n_fail = sum(1 for v in status['steps'].values() if v['status'] == 'FAILED')
n_skip = sum(1 for v in status['steps'].values() if v['status'] == 'SKIPPED')
md_lines.append(f"- 数据更新: **{n_ok} OK / {n_fail} FAILED / {n_skip} SKIPPED**")
for sn, sv in status['steps'].items():
    icon = '✅' if sv['status'] == 'OK' else ('⏭️' if sv['status'] == 'SKIPPED' else '❌')
    md_lines.append(f"  - {icon} {sn}: {sv['status']} ({sv.get('elapsed', 0):.1f}s)")
md_lines.append(f"")
md_lines.append(f"---")
md_lines.append(f"")
md_lines.append(f"*不构成投资建议，仅供参考。*")
md_lines.append(f"*项目: bull-market-heat-index | 数据源: baostock + tushare + akshare*")

md_content = '\n'.join(md_lines)

md_path = os.path.join(OUTPUT_DIR, f'daily_{trade_date.replace("-", "")}.md')
with open(md_path, 'w', encoding='utf-8') as f:
    f.write(md_content)
print(f'MD saved: {md_path}')

# ══════════════════════════════════════════════════════════════
# HTML 版
# ══════════════════════════════════════════════════════════════
html_path = os.path.join(OUTPUT_DIR, f'daily_{trade_date.replace("-", "")}.html')

# 级别颜色
LEVEL_COLOR = {'red': '#ff4d4f', 'yellow': '#faad14', 'green': '#52c41a'}
lc = LEVEL_COLOR.get(level, '#888')

# 维度卡片
dim_cards_html = ''
for k, label in DIM_LABELS.items():
    s = dim_scores.get(k)
    if s is None:
        dim_cards_html += f'<div class="dim-card"><div class="dim-label">{label}</div><div class="dim-score null">—</div></div>\n'
        continue
    dc = '#ff4d4f' if s >= 70 else ('#faad14' if s >= 40 else '#52c41a')
    pct = min(max(s, 0), 100)
    dim_cards_html += f'''<div class="dim-card">
      <div class="dim-label">{label}</div>
      <div class="dim-score" style="color:{dc}">{s:.0f}</div>
      <div class="dim-bar"><div class="dim-bar-fill" style="width:{pct}%;background:{dc}"></div></div>
    </div>\n'''

# 板块表格
sector_rows = ''
for i, s in enumerate(top10, 1):
    sc = s.get('composite_score', 0)
    scolor = '#ff4d4f' if sc >= 70 else ('#faad14' if sc >= 40 else '#52c41a')
    ldr = s.get('leader', {})
    ldr_code = ldr.get('code', '—') if ldr else '—'
    ldr_pct = f"{ldr.get('pct', 0):+.1f}%" if ldr else '—'
    lpct_color = '#ff4d4f' if (ldr.get('pct') or 0) > 0 else '#52c41a'
    sname = s.get('sector_name', s.get('industry', ''))
    sector_rows += f'<tr class="{"hot" if sc >= 70 else ("cold" if sc < 40 else "")}"><td>{i}</td><td>{sname}</td><td style="color:{scolor};font-weight:bold">{sc:.0f}</td><td>{ldr_code}</td><td style="color:{lpct_color}">{ldr_pct}</td></tr>\n'

# 历史走势数据 (JS)
hist_dates = [h['trade_date'][5:] for h in hist30]
hist_vals = [h['composite_score'] for h in hist30]
hist_js_dates = json.dumps(hist_dates)
hist_js_vals = json.dumps(hist_vals)
hist_js_max_hist = json.dumps(max(hist_scores) if hist_scores else 100)

# 运行状态
status_html = ''
for sn, sv in status['steps'].items():
    icon = '✅' if sv['status'] == 'OK' else ('⏭️' if sv['status'] == 'SKIPPED' else '❌')
    status_html += f'<div class="status-item">{icon} <code>{sn}</code> <span class="{sv["status"].lower()}">{sv["status"]}</span> <span class="elapsed">{sv.get("elapsed",0):.1f}s</span></div>\n'

# 昨日对比
prev_html = ''
if len(hist) >= 2:
    prev = hist[-2]['composite_score']
    delta = score - prev
    arrow = '↑' if delta > 0 else ('↓' if delta < 0 else '→')
    dc = '#ff4d4f' if delta > 0 else ('#52c41a' if delta < 0 else '#888')
    prev_html = f'<div class="delta">较上一交易日 <span style="color:{dc};font-size:1.2em">{arrow} {abs(delta):.1f}</span></div>'

html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股牛市热度指数日报 · {trade_date}</title>
<script src="echarts.min.js"></script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#0a0e17; color:#e0e6ed; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
.container {{ max-width:960px; margin:0 auto; padding:24px 16px; }}
.header {{ text-align:center; padding:32px 0 24px; border-bottom:1px solid #1e293b; margin-bottom:32px; }}
.header h1 {{ font-size:1.6em; color:#e0e6ed; margin-bottom:8px; }}
.header .sub {{ color:#8899aa; font-size:0.85em; }}
.header .sub span {{ margin:0 8px; }}

.score-hero {{ text-align:center; padding:32px 0; }}
.score-big {{ font-size:4em; font-weight:800; color:{lc}; line-height:1; }}
.score-level {{ font-size:1.1em; color:{lc}; margin-top:8px; }}
.score-bar-wrap {{ width:60%; max-width:400px; margin:16px auto; height:8px; background:#1e293b; border-radius:4px; overflow:hidden; }}
.score-bar-fill {{ height:100%; background:{lc}; border-radius:4px; width:{min(max(score,0),100)}%; }}
.delta {{ color:#8899aa; margin-top:12px; }}

.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; margin:24px 0; }}
.dim-card {{ background:#111827; border:1px solid #1e293b; border-radius:8px; padding:16px; text-align:center; }}
.dim-label {{ color:#8899aa; font-size:0.8em; margin-bottom:8px; }}
.dim-score {{ font-size:2em; font-weight:700; }}
.dim-score.null {{ color:#4a5568; }}
.dim-bar {{ height:4px; background:#1e293b; border-radius:2px; margin-top:8px; }}
.dim-bar-fill {{ height:100%; border-radius:2px; }}

h2 {{ color:#e0e6ed; font-size:1.1em; margin:32px 0 16px; padding-bottom:8px; border-bottom:1px solid #1e293b; }}
.highlights {{ list-style:none; padding:0; }}
.highlights li {{ padding:8px 16px; background:#111827; border-left:3px solid #faad14; margin-bottom:8px; border-radius:0 4px 4px 0; font-size:0.9em; }}

table {{ width:100%; border-collapse:collapse; font-size:0.85em; }}
th {{ padding:10px 12px; text-align:left; color:#8899aa; border-bottom:2px solid #1e293b; font-weight:500; }}
td {{ padding:10px 12px; border-bottom:1px solid #1a2035; }}
tr:hover {{ background:#111827; }}
tr.hot td:first-child {{ color:#ff4d4f; }}
tr.cold td:first-child {{ color:#52c41a; }}

#chart {{ width:100%; height:240px; margin:16px 0; }}

.status-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:8px; }}
.status-item {{ padding:6px 10px; background:#111827; border-radius:4px; font-size:0.8em; }}
.status-item .ok {{ color:#52c41a; }}
.status-item .failed {{ color:#ff4d4f; }}
.status-item .skipped {{ color:#faad14; }}
.status-item .elapsed {{ color:#4a5568; float:right; }}

.ref-table td:first-child {{ color:#8899aa; }}
.ref-table tr:last-child td {{ color:{lc}; font-weight:bold; }}

.footer {{ text-align:center; padding:40px 0 20px; color:#4a5568; font-size:0.75em; }}
</style>
</head>
<body>
<div class="container">

<div class="header">
  <h1>📊 A股牛市热度指数日报</h1>
  <div class="sub">
    <span>📅 交易日: {trade_date}</span>
    <span>🕐 生成: {now_str}</span>
    <span>📈 日频报告</span>
  </div>
</div>

<div class="score-hero">
  <div class="score-big">{score}</div>
  <div class="score-level">{level_emoji} {level_cn}</div>
  <div class="score-bar-wrap"><div class="score-bar-fill"></div></div>
  {prev_html}
</div>

<h2>五维度拆解</h2>
<div class="grid">
{dim_cards_html}</div>

<h2>关键指标</h2>
<ul class="highlights">
{highlines_html}</ul>

<h2>板块热度 TOP10</h2>
<table>
<tr><th>排名</th><th>行业</th><th>得分</th><th>龙头股</th><th>涨跌幅</th></tr>
{sector_rows}</table>

<div style="height:16px"></div>
<h2>历史走势</h2>
<div id="chart"></div>

<h2>数据运行状态 ({n_ok}✅ {n_fail}❌ {n_skip}⏭️)</h2>
<div class="status-grid">
{status_html}</div>

<h2>历史参考 (回测基准)</h2>
<table class="ref-table">
<tr><th>日期</th><th>市场状态</th><th>综合得分</th></tr>
<tr><td>2015-06-12</td><td>牛市顶 (上证5178)</td><td>🔴 73.8</td></tr>
<tr><td>2021-02-18</td><td>牛市顶 (上证3731)</td><td>⚪ 66.2</td></tr>
<tr><td>2024-10-08</td><td>脉冲顶 (上证3489)</td><td>⚪ 65.1</td></tr>
<tr><td>2018-12-28</td><td>熊底 (上证2493)</td><td>🟢 28.5</td></tr>
<tr><td><b>{trade_date}</b></td><td><b>当前</b></td><td><b>{level_emoji} {score}</b></td></tr>
</table>

<div class="footer">
  <p>⚠️ 不构成投资建议，仅供参考</p>
  <p>bull-market-heat-index · 数据源: baostock + tushare + akshare · {now_str}</p>
</div>

</div>

<script>
const chart = echarts.init(document.getElementById('chart'));
chart.setOption({{
  backgroundColor: 'transparent',
  tooltip: {{ trigger: 'axis', backgroundColor: '#1e293b', borderColor: '#334155', textStyle: {{ color: '#e0e6ed' }} }},
  grid: {{ top: 20, bottom: 30, left: 50, right: 20 }},
  xAxis: {{
    type: 'category',
    data: {hist_js_dates},
    axisLine: {{ lineStyle: {{ color: '#334155' }} }},
    axisLabel: {{ color: '#8899aa', fontSize: 10, interval: Math.floor({len(hist_dates)} / 8) }}
  }},
  yAxis: {{
    type: 'value',
    min: 0, max: 100,
    splitLine: {{ lineStyle: {{ color: '#1e293b' }} }},
    axisLabel: {{ color: '#8899aa', fontSize: 10 }}
  }},
  series: [{{
    type: 'line',
    data: {hist_js_vals},
    smooth: true,
    symbol: 'none',
    lineStyle: {{ color: '{lc}', width: 2 }},
    areaStyle: {{
      color: {{
        type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
        colorStops: [
          {{ offset: 0, color: '{lc}33' }},
          {{ offset: 1, color: 'transparent' }}
        ]
      }}
    }},
    // 红/黄/绿区分界线
    markLine: {{
      silent: true,
      lineStyle: {{ type: 'dashed', width: 1 }},
      data: [
        {{ yAxis: 70, label: {{ formatter: '红区 70', color: '#ff4d4f' }}, lineStyle: {{ color: '#ff4d4f44' }} }},
        {{ yAxis: 40, label: {{ formatter: '黄区 40', color: '#faad14' }}, lineStyle: {{ color: '#faad1444' }} }}
      ]
    }}
  }}]
}});
window.addEventListener('resize', () => chart.resize());
</script>
</body>
</html>'''

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(html_content)
print(f'HTML saved: {html_path}')
