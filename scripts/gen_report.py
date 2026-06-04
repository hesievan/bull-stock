#!/usr/bin/env python3
"""
牛市热度指数日报生成器 v2
输出: MD + HTML(带ECharts交互图) + PNG(精简信息图)
"""
import json, os, re, sys
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'web', 'data')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'reports')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── 读取数据 ─────────────────────────────────────────────────────────────────
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
ind = det.get('indicators', {})
now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
td_clean = trade_date.replace('-', '')

# ── 常量 ─────────────────────────────────────────────────────────────────────
DIM_LABELS = {'valuation': '估值', 'fund': '资金', 'sentiment': '情绪', 'technical': '技术', 'structure': 'structure'}
LEVEL_EMOJI = {'red': '🔴', 'yellow': '🟡', 'green': '🟢'}
LEVEL_CN = {'red': '红色预警', 'yellow': '黄色警惕', 'green': '绿色安全'}
LEVEL_COLOR_HEX = {'red': '#ff4d4f', 'yellow': '#faad14', 'green': '#52c41a'}
level_emoji = LEVEL_EMOJI.get(level, '⚪')
level_cn = LEVEL_CN.get(level, '未知')
level_color = LEVEL_COLOR_HEX.get(level, '#888')

DIM_COLORS_HEX = {
    'valuation': '#1890ff', 'fund': '#52c41a', 'sentiment': '#faad14',
    'technical': '#722ed1', 'structure': '#eb2f96',
}

dim_scores = {k: v['score'] for k, v in idx['dimensions'].items()}

# 昨日对比
prev_score = None
prev_date = None
if len(hist) >= 2:
    prev_score = hist[-2]['composite_score']
    prev_date = hist[-2]['trade_date']

# 维度昨日对比
prev_dims = {}
if len(hist) >= 2 and 'dimensions' in hist[-2]:
    for k, v in hist[-2]['dimensions'].items():
        prev_dims[k] = v.get("score") if isinstance(v, dict) else v

# 板块排序
sectors_sorted = sorted(
    [s for s in sectors if s.get('composite_score') is not None],
    key=lambda x: x['composite_score'], reverse=True
)

# 历史数据 (用于图表)
hist_dates = [h['trade_date'][5:] for h in hist]  # MM-DD
hist_scores = [h['composite_score'] for h in hist]
# 各维度历史
dim_hist = {k: [] for k in DIM_LABELS}
for h in hist:
    if 'dimensions' in h:
        for k in DIM_LABELS:
            v = h.get('dimensions', {}).get(k)
            if isinstance(v, dict):
                dim_hist[k].append(v.get('score'))
            else:
                dim_hist[k].append(v)

# ── 工具函数 ─────────────────────────────────────────────────────────────────
def bar_md(v, w=12):
    if v is None: return '░' * w
    n = int(v / 100 * w)
    return '█' * n + '░' * (w - n)

def delta_str(cur, prev, suffix=''):
    if prev is None: return ''
    d = cur - prev
    arrow = '↑' if d > 0 else ('↓' if d < 0 else '→')
    color = 'red' if d > 0 else ('green' if d < 0 else 'gray')
    return f'<span style="color:{color};font-size:0.85em">{arrow}{abs(d):.1f}{suffix}</span>'

def delta_md(cur, prev):
    if prev is None: return ''
    d = cur - prev
    arrow = '↑' if d > 0 else ('↓' if d < 0 else '→')
    return f' {arrow}{abs(d):.1f}'

# ═══════════════════════════════════════════════════════════════════════════
# 1. MD 版
# ═══════════════════════════════════════════════════════════════════════════
md = []
md.append(f'# 📊 A股牛市热度指数日报')
md.append(f'')
md.append(f'> **交易日**: {trade_date}  |  **生成时间**: {now_str}')
md.append(f'')
md.append(f'---')
md.append(f'')
md.append(f'## 综合热度: {level_emoji} {score}  {level_cn}')
md.append(f'')
md.append(f'{bar_md(score)} {score:.0f}/100')
md.append(f'')
if prev_score is not None:
    d = score - prev_score
    arrow = '↑' if d > 0 else ('↓' if d < 0 else '→')
    md.append(f'**较上一交易日 ({prev_date}): {arrow} {abs(d):.1f} 分**')
md.append(f'')
md.append(f'---')
md.append(f'')
md.append(f'## 维度拆解')
md.append(f'')
md.append(f'| 维度 | 得分 | 较昨日 | 评估 |')
md.append(f'|------|------|--------|------|')
for k, label in DIM_LABELS.items():
    s = dim_scores.get(k)
    if s is None:
        md.append(f'| {label} | — | — | 数据暂缺 |')
        continue
    pd_s = prev_dims.get(k)
    d_str = delta_md(s, pd_s) if pd_s is not None else ''
    eval_t = '🔴 偏高' if s >= 70 else ('🟡 中性' if s >= 40 else '🟢 偏低')
    md.append(f'| {label} | {s:.0f} | {d_str} | {eval_t} |')
md.append(f'')
md.append(f'```')
for k, label in DIM_LABELS.items():
    s = dim_scores.get(k)
    v_str = f'{s:5.1f}' if s is not None else '  — '
    pd_s = prev_dims.get(k)
    d_str = delta_md(s, pd_s) if pd_s is not None else ''
    md.append(f'  {label}  {bar_md(s)}  {v_str}  {d_str}')
md.append(f'```')
md.append(f'')

# 关键指标
md.append(f'### 关键指标')
md.append(f'')
_hl = []
vi = ind.get('valuation', {}); fi = ind.get('fund', {})
si = ind.get('sentiment', {}); ti = ind.get('technical', {}); sti = ind.get('structure', {})
if vi.get('PE_percentile') is not None: _hl.append(f"PE历史分位 **{vi['PE_percentile']:.0f}%** (近10年)")
if vi.get('PB_percentile') is not None: _hl.append(f"PB历史分位 **{vi['PB_percentile']:.0f}%** (近10年)")
if vi.get('below_net_rate') is not None: _hl.append(f"破净率 **{vi['below_net_rate']:.1f}%**")
if vi.get('buffett_ratio') is not None: _hl.append(f"巴菲特指标 **{vi['buffett_ratio']:.0f}%**")
if fi.get('northbound') is not None: _hl.append(f"北向资金250日分位 **{fi['northbound']:.0f}%**")
if si.get('up_down_ratio') is not None: _hl.append(f"涨跌家数比分位 **{si['up_down_ratio']:.0f}%**")
if si.get('limit_up_ratio') is not None: _hl.append(f"涨停家数比分位 **{si['limit_up_ratio']:.0f}%**")
if ti.get('new_high_ratio') is not None: _hl.append(f"250日新高比例 **{ti['new_high_ratio']:.1f}%**")
if ti.get('above_ma250_ratio') is not None: _hl.append(f"站上年线比例 **{ti['above_ma250_ratio']:.1f}%**")
if ti.get('deviation_ma250') is not None: _hl.append(f"均线偏离度分位 **{ti['deviation_ma250']:.0f}%**")
if sti.get('sector_divergence') is not None: _hl.append(f"行业分化度 **{sti['sector_divergence']:.0f}分**")
if sti.get('ah_premium_index') is not None: _hl.append(f"AH溢价分位 **{sti['ah_premium_index']:.0f}%**")
for h in _hl: md.append(f'- {h}')
md.append(f'')

# 板块
md.append(f'---')
md.append(f'')
md.append(f'## 板块热度 TOP10')
md.append(f'')
md.append(f'| 排名 | 行业 | 得分 | 龙头股 | 涨跌幅 |')
md.append(f'|------|------|------|--------|--------|')
for i, s in enumerate(sectors_sorted[:10], 1):
    sc = s.get('composite_score', 0)
    sname = s.get('sector_name', '')
    ldr = s.get('leader', {})
    ldr_code = ldr.get('code', '—') if ldr else '—'
    ldr_pct = f"{ldr.get('pct', 0):+.1f}%" if ldr else '—'
    md.append(f'| {i} | {sname} | {sc:.0f} | {ldr_code} | {ldr_pct} |')
md.append(f'')
hot5 = [s for s in sectors_sorted if s.get('heat_label') == 'hot'][:3]
cold5 = list(reversed([s for s in sectors_sorted if s.get('heat_label') == 'cold']))[:3]
if hot5: md.append(f'🔥 **热门**: {"、".join([s["sector_name"] for s in hot5])}')
if cold5: md.append(f'❄️ **冷门**: {"、".join([s["sector_name"] for s in cold5])}')
md.append(f'')

# 历史走势
md.append(f'---')
md.append(f'')
md.append(f'## 历史走势 (共{len(hist)}个交易日)')
md.append(f'')
md.append(f'| 日期 | 得分 | 状态 |')
md.append(f'|------|------|------|')
for h in hist[-10:]:
    le = LEVEL_EMOJI.get(h['level'], '⚪')
    md.append(f"| {h['trade_date']} | {h['composite_score']} | {le} {h['level']} |")
md.append(f'')

# 回测参考
md.append(f'---')
md.append(f'')
md.append(f'## 历史参考')
md.append(f'')
md.append(f'| 日期 | 市场状态 | 综合得分 |')
md.append(f'|------|---------|---------|')
md.append(f'| 2015-06-12 | 牛市顶 (上证5178) | 🔴 73.8 |')
md.append(f'| 2021-02-18 | 牛市顶 (上证3731) | ⚪ 66.2 |')
md.append(f'| 2024-10-08 | 脉冲顶 (上证3489) | ⚪ 65.1 |')
md.append(f'| 2018-12-28 | 熊底 (上证2493) | 🟢 28.5 |')
md.append(f'| **{trade_date}** | **★ 当前** | **{level_emoji} {score}** |')
md.append(f'')

# 运行状态
n_ok = sum(1 for v in status['steps'].values() if v['status'] == 'OK')
n_fail = sum(1 for v in status['steps'].values() if v['status'] == 'FAILED')
n_skip = sum(1 for v in status['steps'].values() if v['status'] == 'SKIPPED')
md.append(f'---')
md.append(f'')
md.append(f'## 运行状态: {n_ok}✅ {n_fail}❌ {n_skip}⏭️')
md.append(f'')
for sn, sv in status['steps'].items():
    icon = '✅' if sv['status'] == 'OK' else ('⏭️' if sv['status'] == 'SKIPPED' else '❌')
    md.append(f'- {icon} {sn}: {sv["status"]} ({sv.get("elapsed", 0):.1f}s)')
md.append(f'')
md.append(f'---')
md.append(f'')
md.append(f'*不构成投资建议，仅供参考。*')
md.append(f'*bull-market-heat-index v2.0 · baostock + tushare + akshare*')

with open(os.path.join(OUTPUT_DIR, f'daily_{td_clean}.md'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(md))
print(f'MD saved: daily_{td_clean}.md')

# ═══════════════════════════════════════════════════════════════════════════
# 2. HTML 版 (带 ECharts)
# ═══════════════════════════════════════════════════════════════════════════
html_path = os.path.join(OUTPUT_DIR, f'daily_{td_clean}.html')

# 维度卡片 HTML
dim_cards = ''
for k, label in DIM_LABELS.items():
    s = dim_scores.get(k)
    if s is None:
        dim_cards += f'<div class="dim-card"><div class="dim-name">{label}</div><div class="dim-score na">—</div></div>'
        continue
    dc = LEVEL_COLOR_HEX['red'] if s >= 70 else (LEVEL_COLOR_HEX['yellow'] if s >= 40 else LEVEL_COLOR_HEX['green'])
    pct = min(max(s, 0), 100)
    pd_s = prev_dims.get(k)
    d_html = delta_str(s, pd_s) if pd_s is not None else ''
    dim_cards += f'''<div class="dim-card">
      <div class="dim-name">{label}</div>
      <div class="dim-score" style="color:{dc}">{s:.0f}{d_html}</div>
      <div class="dim-bar"><div class="dim-bar-fill" style="width:{pct}%;background:{dc}"></div></div>
    </div>'''

# 板块表格
sector_rows = ''
for i, s in enumerate(sectors_sorted[:10], 1):
    sc = s.get('composite_score', 0)
    scolor = LEVEL_COLOR_HEX['red'] if sc >= 70 else (LEVEL_COLOR_HEX['yellow'] if sc >= 40 else LEVEL_COLOR_HEX['green'])
    sname = s.get('sector_name', '')
    ldr = s.get('leader', {})
    ldr_code = ldr.get('code', '—') if ldr else '—'
    ldr_pct_v = ldr.get('pct', 0) if ldr else 0
    ldr_str = f"{ldr_pct_v:+.1f}%" if ldr_code != '—' else '—'
    lcolor = '#ff4d4f' if ldr_pct_v > 0 else '#52c41a'
    sector_rows += f'<tr><td>{i}</td><td>{sname}</td><td style="color:{scolor};font-weight:700">{sc:.0f}</td><td>{ldr_code}</td><td style="color:{lcolor}">{ldr_str}</td></tr>\n'

# 关键指标
hl_html = ''
for h in _hl:
    h_plain = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', h)
    hl_html += f'<li>{h_plain}</li>\n'

# 昨日对比
delta_html = ''
if prev_score is not None:
    d = score - prev_score
    arrow = '↑' if d > 0 else ('↓' if d < 0 else '→')
    dc = '#ff4d4f' if d > 0 else ('#52c41a' if d < 0 else '#888')
    delta_html = f'<div class="delta">较上一交易日 <span style="color:{dc}">{arrow} {abs(d):.1f}</span></div>'

# 维度历史数据 (JS)
dim_hist_js = {}
for k in DIM_LABELS:
    vals = [v for v in dim_hist[k] if v is not None]
    dim_hist_js[k] = vals

html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股牛市热度指数日报 · {trade_date}</title>
<script src="echarts.min.js"></script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#0a0e17; color:#c9d1d9; font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif; font-size:14px; }}
.container {{ max-width:960px; margin:0 auto; padding:24px 16px; }}
h1 {{ text-align:center; font-size:1.5em; color:#e0e6ed; padding:20px 0 8px; }}
.sub {{ text-align:center; color:#6b7280; font-size:0.8em; margin-bottom:24px; }}
.sub span {{ margin:0 8px; }}

/* 综合得分 */
.hero {{ text-align:center; padding:28px 0; }}
.score-big {{ font-size:4.5em; font-weight:800; color:{level_color}; line-height:1; }}
.score-level {{ font-size:1.1em; color:{level_color}; margin-top:6px; }}
.bar-wrap {{ width:55%; max-width:360px; margin:14px auto; height:8px; background:#1e293b; border-radius:4px; overflow:hidden; }}
.bar-fill {{ height:100%; background:{level_color}; border-radius:4px; width:{min(max(score,0),100)}%; }}
.delta {{ color:#6b7280; margin-top:10px; font-size:0.9em; }}

h2 {{ color:#e0e6ed; font-size:1.05em; margin:28px 0 14px; padding-bottom:8px; border-bottom:1px solid #1e293b; }}

/* 维度卡片 */
.dims {{ display:grid; grid-template-columns:repeat(5,1fr); gap:10px; margin:16px 0; }}
.dim-card {{ background:#111827; border:1px solid #1e293b; border-radius:8px; padding:14px 10px; text-align:center; }}
.dim-name {{ color:#6b7280; font-size:0.75em; margin-bottom:6px; }}
.dim-score {{ font-size:1.8em; font-weight:700; }}
.dim-score.na {{ color:#374151; }}
.dim-bar {{ height:4px; background:#1e293b; border-radius:2px; margin-top:6px; }}
.dim-bar-fill {{ height:100%; border-radius:2px; }}

/* 图表 */
.chart-box {{ background:#111827; border:1px solid #1e293b; border-radius:8px; padding:16px; margin:16px 0; }}
.chart-title {{ color:#6b7280; font-size:0.8em; margin-bottom:8px; }}
.chart {{ width:100%; height:220px; }}

/* 关键指标 */
.highlights {{ list-style:none; padding:0; }}
.highlights li {{ padding:7px 14px; background:#111827; border-left:3px solid #faad14; margin-bottom:6px; border-radius:0 4px 4px 0; font-size:0.88em; }}

/* 表格 */
table {{ width:100%; border-collapse:collapse; font-size:0.82em; }}
th {{ padding:9px 10px; text-align:left; color:#6b7280; border-bottom:2px solid #1e293b; font-weight:500; }}
td {{ padding:9px 10px; border-bottom:1px solid #1a2035; }}
tr:hover {{ background:#111827; }}

/* 状态 */
.status-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)); gap:6px; }}
.status-item {{ padding:5px 10px; background:#111827; border-radius:4px; font-size:0.78em; }}
.status-item .ok {{ color:#52c41a; }} .status-item .failed {{ color:#ff4d4f; }} .status-item .skipped {{ color:#faad14; }}
.status-item .elapsed {{ color:#374151; float:right; }}

.ref-table td:first-child {{ color:#6b7280; }}
.ref-table tr:last-child td {{ color:{level_color}; font-weight:bold; }}

.footer {{ text-align:center; padding:36px 0 16px; color:#374151; font-size:0.72em; }}
</style>
</head>
<body>
<div class="container">

<h1>📊 A股牛市热度指数日报</h1>
<div class="sub"><span>📅 {trade_date}</span><span>🕐 {now_str}</span><span>📈 日频</span></div>

<div class="hero">
  <div class="score-big">{score}</div>
  <div class="score-level">{level_emoji} {level_cn}</div>
  <div class="bar-wrap"><div class="bar-fill"></div></div>
  {delta_html}
</div>

<h2>五维度拆解</h2>
<div class="dims">{dim_cards}</div>

<!-- 综合得分历史走势 -->
<div class="chart-box">
  <div class="chart-title">综合得分历史走势</div>
  <div id="chart-score" class="chart"></div>
</div>

<!-- 五维度历史走势 -->
<div class="chart-box">
  <div class="chart-title">五维度历史走势</div>
  <div id="chart-dims" class="chart"></div>
</div>

<h2>关键指标</h2>
<ul class="highlights">{hl_html}</ul>

<h2>板块热度 TOP10</h2>
<table>
<tr><th>排名</th><th>行业</th><th>得分</th><th>龙头股</th><th>涨跌幅</th></tr>
{sector_rows}</table>

<h2>历史参考</h2>
<table class="ref-table">
<tr><th>日期</th><th>市场状态</th><th>综合得分</th></tr>
<tr><td>2015-06-12</td><td>牛市顶 (上证5178)</td><td>🔴 73.8</td></tr>
<tr><td>2021-02-18</td><td>牛市顶 (上证3731)</td><td>⚪ 66.2</td></tr>
<tr><td>2024-10-08</td><td>脉冲顶 (上证3489)</td><td>⚪ 65.1</td></tr>
<tr><td>2018-12-28</td><td>熊底 (上证2493)</td><td>🟢 28.5</td></tr>
<tr><td><b>{trade_date}</b></td><td><b>★ 当前</b></td><td><b>{level_emoji} {score}</b></td></tr>
</table>

<h2>运行状态 ({n_ok}✅ {n_fail}❌ {n_skip}⏭️)</h2>
<div class="status-grid">
{''.join(f'<div class="status-item">{"✅" if v["status"]=="OK" else ("⏭️" if v["status"]=="SKIPPED" else "❌")} <code>{sn}</code> <span class="{v["status"].lower()}">{v["status"]}</span> <span class="elapsed">{v.get("elapsed",0):.1f}s</span></div>' for sn, v in status['steps'].items())}
</div>

<div class="footer">
  <p>⚠️ 不构成投资建议，仅供参考</p>
  <p>bull-market-heat-index v2.0 · baostock + tushare + akshare · {now_str}</p>
</div>
</div>

<script>
// 综合得分走势
const scoreChart = echarts.init(document.getElementById('chart-score'));
scoreChart.setOption({{
  backgroundColor: 'transparent',
  tooltip: {{ trigger:'axis', backgroundColor:'#1e293b', borderColor:'#334155', textStyle:{{color:'#c9d1d9'}} }},
  grid: {{ top:15, bottom:25, left:45, right:15 }},
  xAxis: {{
    type:'category', data:{json.dumps(hist_dates)},
    axisLine:{{lineStyle:{{color:'#334155'}}}}, axisLabel:{{color:'#6b7280',fontSize:10,interval:Math.max(Math.floor({len(hist_dates)}/8),1)}}
  }},
  yAxis: {{
    type:'value', min:0, max:100,
    splitLine:{{lineStyle:{{color:'#1e293b'}}}}, axisLabel:{{color:'#6b7280',fontSize:10}}
  }},
  series:[{{
    type:'line', data:{json.dumps(hist_scores)},
    smooth:true, symbol:'none',
    lineStyle:{{color:'{level_color}',width:2}},
    areaStyle:{{color:{{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{{offset:0,color:'{level_color}33'}},{{offset:1,color:'transparent'}}]}}}},
    markLine:{{silent:true,lineStyle:{{type:'dashed',width:1}},
      data:[
        {{yAxis:70,label:{{formatter:'红区 70',color:'#ff4d4f'}},lineStyle:{{color:'#ff4d4f44'}}}},
        {{yAxis:40,label:{{formatter:'黄区 40',color:'#faad14'}},lineStyle:{{color:'#faad1444'}}}}
      ]
    }}
  }}]
}});

// 五维度走势
const dimChart = echarts.init(document.getElementById('chart-dims'));
dimChart.setOption({{
  backgroundColor: 'transparent',
  tooltip: {{ trigger:'axis', backgroundColor:'#1e293b', borderColor:'#334155', textStyle:{{color:'#c9d1d9'}} }},
  legend: {{ top:0, textStyle:{{color:'#6b7280',fontSize:10}}, itemWidth:12, itemHeight:8 }},
  grid: {{ top:30, bottom:25, left:45, right:15 }},
  xAxis: {{
    type:'category', data:{json.dumps(hist_dates)},
    axisLine:{{lineStyle:{{color:'#334155'}}}}, axisLabel:{{color:'#6b7280',fontSize:10,interval:Math.max(Math.floor({len(hist_dates)}/8),1)}}
  }},
  yAxis: {{
    type:'value', min:0, max:100,
    splitLine:{{lineStyle:{{color:'#1e293b'}}}}, axisLabel:{{color:'#6b7280',fontSize:10}}
  }},
  series: [
    {','.join(f'''{{
      name:'{label}',
      type:'line', data:{json.dumps(dim_hist_js[k])},
      smooth:true, symbol:'none',
      lineStyle:{{color:'{DIM_COLORS_HEX[k]}',width:1.5}}
    }}''' for k, label in DIM_LABELS.items())}
  ]
}});

window.addEventListener('resize', () => {{ scoreChart.resize(); dimChart.resize(); }});
</script>
</body>
</html>'''

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(html)
print(f'HTML saved: daily_{td_clean}.html')

# ═══════════════════════════════════════════════════════════════════════════
# 3. PNG 版 (Pillow 渲染)
# ═══════════════════════════════════════════════════════════════════════════
try:
    from PIL import Image, ImageDraw, ImageFont
    _has_pillow = True
except ImportError:
    _has_pillow = False

if _has_pillow:
    png_path = os.path.join(OUTPUT_DIR, f'daily_{td_clean}.png')

    _font_candidates = [
        '/System/Library/Fonts/PingFang.ttc',
        '/System/Library/Fonts/Hiragino Sans GB.ttc',
        '/System/Library/Fonts/STHeiti Medium.ttc',
    ]
    _font_path = next((p for p in _font_candidates if os.path.exists(p)), None)

    def _f(size):
        try: return ImageFont.truetype(_font_path, size)
        except: return ImageFont.load_default()

    C = {
        'bg':(255,255,255),'dark':(30,30,30),'gray':(120,120,120),'light':(180,180,180),
        'line':(232,232,232),'red':(255,77,79),'yellow':(250,173,20),'green':(82,196,26),
        'blue':(24,144,255),'card':(248,249,250),'header_bg':(15,20,35),
        'val':(24,144,255),'fund':(82,196,26),'sent':(250,173,20),'tech':(114,46,209),'struct':(235,47,150),
    }
    _lc = C['red'] if score >= 70 else (C['yellow'] if score >= 40 else C['green'])
    _dim_color_map = {'valuation':C['val'],'fund':C['fund'],'sentiment':C['sent'],'technical':C['tech'],'structure':C['struct']}

    W, PAD = 800, 32
    TOTAL_H = 2200
    img = Image.new('RGB', (W, TOTAL_H), C['bg'])
    draw = ImageDraw.Draw(img)
    y = x = PAD

    def _txt(s, x, y, color=C['dark'], size=14):
        draw.text((x,y), s, fill=color, font=_f(size))
        return y + size + 4

    def _hline(y, pad=8):
        draw.line([(x,y+pad),(W-x,y+pad)], fill=C['line'], width=1)
        return y + pad*2 + 2

    def _bar(cx, cy, w, sc, color, h=5):
        draw.rectangle([(cx,cy),(cx+w,cy+h)], fill=(230,230,230))
        draw.rectangle([(cx,cy),(cx+int(w*min(sc,100)/100),cy+h)], fill=color)

    # HEADER
    y = _txt("A股牛市热度指数日报", x, y, C['dark'], 18)
    y = _txt(f"交易日: {trade_date}    生成: {now_str}", x, y, C['gray'], 11)
    y = _hline(y)

    # HERO
    y += 6
    bs = _f(56)
    bbox = bs.getbbox(f"{score:.1f}")
    sw = bbox[2]-bbox[0]
    draw.text(((W-sw)//2, y), f"{score:.1f}", fill=_lc, font=bs)
    y += 64
    ls = _f(14)
    bbox = ls.getbbox(level_cn)
    lw = bbox[2]-bbox[0]
    draw.text(((W-lw)//2, y), level_cn, fill=_lc, font=ls)
    y += 24
    _bar(x+40, y, W-2*x-80, score, _lc, 8)
    y += 20
    if prev_score is not None:
        d = score - prev_score
        arrow = '↑' if d > 0 else ('↓' if d < 0 else '→')
        dc = C['red'] if d > 0 else C['green']
        label = f"较上一交易日: {arrow} {abs(d):.1f}"
        bbox = _f(12).getbbox(label)
        draw.text(((W-(bbox[2]-bbox[0]))//2, y), label, fill=dc, font=_f(12))
        y += 18
    y = _hline(y)

    # DIMENSIONS
    y += 4
    y = _txt("五维度拆解", x, y, C['dark'], 15)
    y += 8
    cw = (W - 2*x - 4*8) // 5
    ch = 76
    cy0 = y
    for i, (dk, label) in enumerate(DIM_LABELS.items()):
        s = dim_scores.get(dk)
        cx = x + i*(cw+8)
        sc = s if s is not None else 0
        scolor = C['red'] if sc >= 70 else (C['yellow'] if sc >= 40 else C['green'])
        draw.rectangle([(cx,cy0),(cx+cw,cy0+ch)], fill=C['card'], outline=C['line'])
        bbox = _f(12).getbbox(label)
        draw.text((cx+(cw-(bbox[2]-bbox[0]))//2, cy0+6), label, fill=C['gray'], font=_f(12))
        sc_str = f"{sc:.0f}" if s is not None else "—"
        bbox = _f(28).getbbox(sc_str)
        draw.text((cx+(cw-(bbox[2]-bbox[0]))//2, cy0+22), sc_str, fill=scolor, font=_f(28))
        _bar(cx+8, cy0+64, cw-16, sc, scolor)
    y = cy0 + ch + 12

    # 维度详细 + 对比
    for dk, label in DIM_LABELS.items():
        s = dim_scores.get(dk)
        sc = s if s is not None else 0
        scolor = C['red'] if sc >= 70 else (C['yellow'] if sc >= 40 else C['green'])
        pd_s = prev_dims.get(dk)
        draw.text((x, y), label, fill=C['dark'], font=_f(12))
        draw.text((x+50, y), f"{sc:.1f}", fill=scolor, font=_f(12))
        if pd_s is not None:
            d = sc - pd_s
            arrow = '↑' if d > 0 else ('↓' if d < 0 else '→')
            dc = C['red'] if d > 0 else C['green']
            draw.text((x+95, y), f"{arrow}{abs(d):.1f}", fill=dc, font=_f(11))
        _bar(x+160, y+2, 120, sc, scolor)
        y += 22
    y = _hline(y)

    # KEY METRICS
    y += 4
    y = _txt("关键指标", x, y, C['dark'], 15)
    y += 6
    for h in _hl[:8]:
        # 解析 "标签 数值 (备注)" 格式
        m = re.match(r'(.+?)\s+([\d.]+%?)\s*(.*)', h)
        if m:
            lbl, val, note = m.groups()
            draw.text((x+10, y), "•", fill=C['yellow'], font=_f(12))
            draw.text((x+24, y), lbl, fill=C['dark'], font=_f(12))
            # 数值颜色
            vcolor = C['dark']
            try:
                nv = float(re.search(r'[\d.]+', val).group())
                if any(k in lbl for k in ['北向','涨停','涨跌','偏离','PE','PB','巴菲特']):
                    vcolor = C['red'] if nv > 80 else (C['yellow'] if nv > 50 else C['green'])
                elif '新高' in lbl:
                    vcolor = C['red'] if nv > 15 else (C['yellow'] if nv > 5 else C['green'])
            except: pass
            draw.text((x+195, y), val, fill=vcolor, font=_f(12))
            if note:
                draw.text((x+275, y), note, fill=C['light'], font=_f(11))
        y += 20
    y = _hline(y)

    # SECTORS
    y += 4
    y = _txt("板块热度 TOP10", x, y, C['dark'], 15)
    y += 8
    draw.text((x,y),"排名",fill=C['gray'],font=_f(10))
    draw.text((x+45,y),"行业",fill=C['gray'],font=_f(10))
    draw.text((x+300,y),"得分",fill=C['gray'],font=_f(10))
    draw.text((x+370,y),"龙头股",fill=C['gray'],font=_f(10))
    draw.text((x+500,y),"涨跌幅",fill=C['gray'],font=_f(10))
    y += 16
    draw.line([(x,y),(W-x,y)], fill=C['line'], width=1)
    y += 4
    for i, s in enumerate(sectors_sorted[:10], 1):
        sc = s.get('composite_score', 0)
        scolor = C['red'] if sc >= 70 else (C['yellow'] if sc >= 40 else C['green'])
        sname = s.get('sector_name','')[:14]
        ldr = s.get('leader',{})
        lc_code = ldr.get('code','') if ldr else ''
        lc_pct = ldr.get('pct',0) if ldr else 0
        lc_str = f"{lc_pct:+.1f}%" if lc_code else ''
        lcolor = C['red'] if lc_pct > 0 else C['green']
        draw.text((x,y), str(i), fill=scolor if sc>=70 else C['gray'], font=_f(12))
        draw.text((x+45,y), sname, fill=C['dark'], font=_f(12))
        draw.text((x+300,y), f"{sc:.0f}", fill=scolor, font=_f(12))
        draw.text((x+370,y), lc_code, fill=C['gray'], font=_f(11))
        draw.text((x+500,y), lc_str, fill=lcolor, font=_f(12))
        y += 20
    y = _hline(y)

    # HISTORY SPARKLINE
    y += 4
    y = _txt(f"历史走势 (共{len(hist)}个交易日)", x, y, C['dark'], 15)
    y += 8
    if len(hist) >= 2 and len(hist_scores) >= 2:
        cx0 = x + 25; cy0 = y + 5; cw = W - 2*cx0; ch = 80
        draw.rectangle([(cx0-2,cy0-2),(cx0+cw+2,cy0+ch+2)], fill=(250,250,252), outline=C['line'])
        mn_v, mx_v = min(hist_scores), max(hist_scores)
        if mx_v == mn_v: mx_v += 10
        step = cw / max(len(hist_scores)-1, 1)
        pts = []
        for i, v in enumerate(hist_scores):
            px = cx0 + int(i*step)
            py = cy0 + ch - int((v-mn_v)/(mx_v-mn_v)*(ch-8)) - 4
            pts.append((px,py))
        for rv, rc in [(70,C['red']+(40,)),(40,C['yellow']+(40,))]:
            ry = cy0+ch - int((rv-mn_v)/(mx_v-mn_v)*(ch-8)) - 4
            if cy0<=ry<=cy0+ch:
                draw.line([(cx0,ry),(cx0+cw,ry)], fill=rc, width=1)
        for i in range(len(pts)-1):
            draw.line([pts[i],pts[i+1]], fill=_lc, width=2)
        for px,py in pts:
            draw.ellipse([(px-3,py-3),(px+3,py+3)], fill=_lc)
        for i in range(0,len(hist_dates),max(len(hist_dates)//5,1)):
            px = cx0 + int(i*step)
            draw.text((px-12, cy0+ch+4), hist_dates[i], fill=C['light'], font=_f(9))
    y = cy0 + ch + 24

    # 历史参考
    y = _txt("历史参考", x, y, C['dark'], 13)
    y += 6
    refs = [
        ("2015-06-12","牛市顶 (上证5178)","73.8",C['red']),
        ("2021-02-18","牛市顶 (上证3731)","66.2",C['gray']),
        ("2024-10-08","脉冲顶 (上证3489)","65.1",C['gray']),
        ("2018-12-28","熊底 (上证2493)","28.5",C['green']),
        (trade_date,"★ 当前",f"{score:.1f}",_lc),
    ]
    for dt2,st2,sc2,sc2c in refs:
        draw.text((x+10,y), dt2, fill=sc2c, font=_f(12))
        draw.text((x+110,y), st2, fill=C['dark'], font=_f(12))
        draw.text((x+370,y), sc2, fill=sc2c, font=_f(12))
        y += 20

    y = _hline(y)
    y += 6
    n_ok = sum(1 for v in status['steps'].values() if v['status']=='OK')
    n_fail = sum(1 for v in status['steps'].values() if v['status']=='FAILED')
    n_skip = sum(1 for v in status['steps'].values() if v['status']=='SKIPPED')
    y = _txt(f"运行状态: {n_ok} OK / {n_fail} FAILED / {n_skip} SKIPPED    板块: {len(sectors)}个行业", x, y, C['gray'], 11)
    y = _txt("⚠️ 不构成投资建议，仅供参考    bull-market-heat-index v2.0", x, y, C['light'], 10)

    img.crop((0,0,W,y+24)).save(png_path, 'PNG')
    print(f'PNG saved: daily_{td_clean}.png')
else:
    print('Pillow not installed, skipping PNG')
