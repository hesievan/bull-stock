#!/usr/bin/env python3
"""生成 A股牛市热度指数历史走势图 (2015至今)"""
import json, os

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'web', 'data')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'reports')
os.makedirs(OUTPUT_DIR, exist_ok=True)

history_file = os.path.join(DATA_DIR, 'history_full.json')
if not os.path.exists(history_file):
    print("ERROR: history_full.json not found."); exit(1)

with open(history_file) as f:
    hist = json.load(f)
print(f"Loaded {len(hist)} points: {hist[0]['trade_date']} ~ {hist[-1]['trade_date']}")

dates = [h['trade_date'] for h in hist]
scores = [h['composite_score'] for h in hist]
dim_keys = ['valuation','fund','sentiment','technical','structure']
dim_labels = {'valuation':'估值','fund':'资金','sentiment':'情绪','technical':'技术','structure':'结构'}
dim_colors = {'valuation':'#1890ff','fund':'#52c41a','sentiment':'#faad14','technical':'#722ed1','structure':'#eb2f96'}
dim_data = {k: [h.get('dimensions',{}).get(k) for h in hist] for k in dim_keys}

red_days = sum(1 for s in scores if s >= 70)
yellow_days = sum(1 for s in scores if 40 <= s < 70)
green_days = sum(1 for s in scores if s < 40)
avg_s = sum(scores)/len(scores)
max_s = max(scores); min_s = min(scores)
max_d = dates[scores.index(max_s)]; min_d = dates[scores.index(min_s)]
cur = scores[-1]
cur_color = '#ff4d4f' if cur >= 70 else ('#faad14' if cur >= 40 else '#52c41a')

# 采样
MAX_P = 600
step = max(len(dates)//MAX_P, 1)
idx = list(range(0,len(dates),step))
dates_s = [dates[i] for i in idx]
scores_s = [scores[i] for i in idx]
dim_data_s = {k: [dim_data[k][i] for i in idx] for k in dim_keys}

mark_pts = []
for i,(d,s) in enumerate(zip(dates,scores)):
    if s==max_s: mark_pts.append({'coord':[d,s],'value':f'最高{s}','itemStyle':{'color':'#ff4d4f'}})
    if s==min_s: mark_pts.append({'coord':[d,s],'value':f'最低{s}','itemStyle':{'color':'#52c41a'}})

JS = {
    'dates': json.dumps(dates_s),
    'scores': json.dumps(scores_s),
    'dimData': json.dumps({k:[round(v,1) if v is not None else None for v in vl] for k,vl in dim_data_s.items()}),
    'labels': json.dumps(dim_labels),
    'colors': json.dumps(dim_colors),
    'marks': json.dumps(mark_pts),
}

html_path = os.path.join(OUTPUT_DIR, 'history_chart.html')
echarts_js = open(os.path.join(os.path.dirname(__file__), '..', 'web', 'echarts.min.js')).read()

TEMPLATE = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><title>A股牛市热度指数历史走势</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0e17;color:#c9d1d9;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;padding:20px 12px}
.c{max-width:1200px;margin:0 auto}
h1{text-align:center;font-size:1.3em;color:#e0e6ed;margin-bottom:4px}
.sub{text-align:center;color:#6b7280;font-size:0.78em;margin-bottom:16px}
.stats{display:grid;grid-template-columns:repeat(7,1fr);gap:8px;margin-bottom:16px}
.sc{background:#111827;border:1px solid #1e293b;border-radius:6px;padding:10px 6px;text-align:center}
.sl{color:#6b7280;font-size:0.68em;margin-bottom:4px}
.sv{font-size:1.3em;font-weight:700}
.sv.r{color:#ff4d4f}.sv.y{color:#faad14}.sv.g{color:#52c41a}.sv.c{color:__CUR_COLOR__}
.cb{background:#111827;border:1px solid #1e293b;border-radius:8px;padding:14px;margin-bottom:14px}
.ct{color:#6b7280;font-size:0.75em;margin-bottom:6px}
.toggles{text-align:center;margin:10px 0}
.toggles button{background:#1e293b;color:#8899aa;border:1px solid #334155;border-radius:4px;padding:3px 10px;margin:0 3px;cursor:pointer;font-size:0.75em}
.toggles button.active{background:__CUR_COLOR__;color:#fff}
.footer{text-align:center;padding:16px 0;color:#374151;font-size:0.68em}
</style>
</head>
<body>
<div class="c">
<h1>📈 A股牛市热度指数历史走势</h1>
<div class="sub">2015-01-05 ~ 2026-06-02 · 共 __TOTAL__ 个交易日 · 采样 __SAMPLED__ 点</div>
<div class="stats">
<div class="sc"><div class="sl">当前</div><div class="sv c">__CUR__</div></div>
<div class="sc"><div class="sl">最高 __MAX_D__</div><div class="sv r">__MAX__</div></div>
<div class="sc"><div class="sl">最低 __MIN_D__</div><div class="sv g">__MIN__</div></div>
<div class="sc"><div class="sl">均值</div><div class="sv">__AVG__</div></div>
<div class="sc"><div class="sl">🔴红区</div><div class="sv r">__RED__天</div></div>
<div class="sc"><div class="sl">🟡黄区</div><div class="sv y">__YELLOW__天</div></div>
<div class="sc"><div class="sl">🟢绿区</div><div class="sv g">__GREEN__天</div></div>
</div>
<div class="cb"><div class="ct">综合热度指数（红区≥70 / 黄区≥40 / 绿区&lt;40）</div><div id="c-main" style="height:340px"></div></div>
<div class="cb">
  <div class="ct">五维度走势对比</div>
  <div class="toggles">
    <button onclick="td(this,'all')" class="active">全部</button>
    <button onclick="td(this,'valuation')">估值</button>
    <button onclick="td(this,'fund')">资金</button>
    <button onclick="td(this,'sentiment')">情绪</button>
    <button onclick="td(this,'technical')">技术</button>
    <button onclick="td(this,'structure')">结构</button>
  </div>
  <div id="c-dims" style="height:260px"></div>
</div>
<div class="footer">⚠️ 仅供参考 · bull-market-heat-index v2.0 · baostock+tushare+akshare</div>
</div>
<script>__ECHARTS__</script>
<script>
var dates=__DATES__, scores=__SCORES__, dimData=__DIMDATA__;
var labels=__LABELS__, colors=__COLORS__;
var mc=echarts.init(document.getElementById('c-main'));
mc.setOption({
  backgroundColor:'transparent',
  tooltip:{trigger:'axis',backgroundColor:'#1e293b',borderColor:'#334155',textStyle:{color:'#c9d1d9'},
    formatter:function(p){return p[0].axisValue+'<br/>'+p[0].marker+'综合热度: <b>'+p[0].value+'</b>';}},
  grid:{top:10,bottom:50,left:45,right:10},
  xAxis:{type:'category',data:dates,axisLine:{lineStyle:{color:'#334155'}},axisLabel:{color:'#6b7280',fontSize:9,
    formatter:function(v){var i=dates.indexOf(v);return i%Math.ceil(dates.length/10)===0?v.substring(0,7):'';}}},
  yAxis:{type:'value',min:0,max:100,splitLine:{lineStyle:{color:'#1e293b'}},axisLabel:{color:'#6b7280',fontSize:9}},
  dataZoom:[
    {type:'inside',start:70,end:100},
    {type:'slider',start:70,end:100,height:18,bottom:5,borderColor:'#1e293b',backgroundColor:'#111827',fillerColor:'__CUR_COLOR__22',handleStyle:{color:'__CUR_COLOR__'},textStyle:{color:'#6b7280',fontSize:9}}
  ],
  series:[{
    type:'line',data:scores,smooth:true,symbol:'none',
    lineStyle:{color:'__CUR_COLOR__',width:2},
    areaStyle:{color:{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:'__CUR_COLOR__44'},{offset:1,color:'transparent'}]}},
    markLine:{silent:true,lineStyle:{type:'dashed',width:1},data:[
      {yAxis:70,label:{formatter:'红区70',color:'#ff4d4f',fontSize:10},lineStyle:{color:'#ff4d4f44'}},
      {yAxis:40,label:{formatter:'黄区40',color:'#faad14',fontSize:10},lineStyle:{color:'#faad1444'}}
    ]},
    markPoint:{data:__MARKS__,symbolSize:36,label:{fontSize:9}}
  }]
});
var ds=Object.keys(labels).map(function(k){return{name:labels[k],type:'line',data:dimData[k],smooth:true,symbol:'none',lineStyle:{color:colors[k],width:1.5}};});
var dc=echarts.init(document.getElementById('c-dims'));
dc.setOption({
  backgroundColor:'transparent',tooltip:{trigger:'axis',backgroundColor:'#1e293b',borderColor:'#334155',textStyle:{color:'#c9d1d9'}},
  legend:{top:0,textStyle:{color:'#6b7280',fontSize:10},itemWidth:12,itemHeight:8},
  grid:{top:28,bottom:50,left:45,right:10},
  xAxis:{type:'category',data:dates,axisLine:{lineStyle:{color:'#334155'}},axisLabel:{color:'#6b7280',fontSize:9,
    formatter:function(v){var i=dates.indexOf(v);return i%Math.ceil(dates.length/10)===0?v.substring(0,7):'';}}},
  yAxis:{type:'value',min:0,max:100,splitLine:{lineStyle:{color:'#1e293b'}},axisLabel:{color:'#6b7280',fontSize:9}},
  dataZoom:[
    {type:'inside',start:70,end:100},
    {type:'slider',start:70,end:100,height:18,bottom:5,borderColor:'#1e293b',backgroundColor:'#111827',fillerColor:'__CUR_COLOR__22',handleStyle:{color:'__CUR_COLOR__'},textStyle:{color:'#6b7280',fontSize:9}}
  ],
  series:ds
});
function td(btn,k){
  document.querySelectorAll('.toggles button').forEach(function(b){b.classList.remove('active');});
  btn.classList.add('active');
  if(k==='all') dc.setOption({series:ds});
  else dc.setOption({series:ds.filter(function(s){return s.name===labels[k];})});
}
mc.on('dataZoom',function(e){var o=mc.getOption().dataZoom[0];dc.dispatchAction({type:'dataZoom',start:o.start,end:o.end});});
dc.on('dataZoom',function(e){var o=dc.getOption().dataZoom[0];mc.dispatchAction({type:'dataZoom',start:o.start,end:o.end});});
window.addEventListener('resize',function(){mc.resize();dc.resize();});
</script>
</body></html>'''

html = TEMPLATE
html = html.replace('__ECHARTS__', echarts_js)
html = html.replace('__DATES__', JS['dates'])
html = html.replace('__SCORES__', JS['scores'])
html = html.replace('__DIMDATA__', JS['dimData'])
html = html.replace('__LABELS__', JS['labels'])
html = html.replace('__COLORS__', JS['colors'])
html = html.replace('__MARKS__', JS['marks'])
html = html.replace('__CUR_COLOR__', cur_color)
html = html.replace('__CUR__', str(cur))
html = html.replace('__MAX__', str(max_s))
html = html.replace('__MAX_D__', max_d[:7])
html = html.replace('__MIN__', str(min_s))
html = html.replace('__MIN_D__', min_d[:7])
html = html.replace('__AVG__', f'{avg_s:.1f}')
html = html.replace('__RED__', str(red_days))
html = html.replace('__YELLOW__', str(yellow_days))
html = html.replace('__GREEN__', str(green_days))
html = html.replace('__TOTAL__', str(len(hist)))
html = html.replace('__SAMPLED__', str(len(dates_s)))

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(html)
print(f"HTML saved: {html_path}")
print(f"Stats: 当前{cur} | 最高{max_s}({max_d}) | 最低{min_s}({min_d}) | 均值{avg_s:.1f}")
print(f"🔴红区{red_days}天 🟡黄区{yellow_days}天 🟢绿区{green_days}天")
