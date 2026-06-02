#!/usr/bin/env python3
"""
生成 A股牛市热度指数历史走势图 v2
- 关键事件标注
- 综合得分 + 五维度双图
- 数据 Zoom 联动
- 各维度极值标记
"""
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

# 统计
red_days = sum(1 for s in scores if s >= 70)
yellow_days = sum(1 for s in scores if 40 <= s < 70)
green_days = sum(1 for s in scores if s < 40)
avg_s = sum(scores)/len(scores)
max_s = max(scores); min_s = min(scores)
max_i = scores.index(max_s); min_i = scores.index(min_s)
max_d = dates[max_i]; min_d = dates[min_i]
cur = scores[-1]
cur_color = '#ff4d4f' if cur >= 70 else ('#faad14' if cur >= 40 else '#52c41a')

# 关键事件标注
key_events = [
    {'date':'2015-06-12','label':'牛市顶\\n5178','color':'#ff4d4f'},
    {'date':'2016-01-27','label':'熔断底\\n2638','color':'#52c41a'},
    {'date':'2018-12-28','label':'贸易战底\\n2493','color':'#52c41a'},
    {'date':'2020-03-23','label':'新冠底\\n2660','color':'#52c41a'},
    {'date':'2020-07-09','label':'疫情反弹\\n3450','color':'#faad14'},
    {'date':'2021-02-18','label':'核心资产顶\\n3731','color':'#ff4d4f'},
    {'date':'2022-04-26','label':'上海疫情底\\n2886','color':'#52c41a'},
    {'date':'2024-09-30','label':'政策脉冲\\n3350','color':'#faad14'},
    {'date':'2024-10-08','label':'脉冲顶\\n3489','color':'#faad14'},
    {'date':max_d if max_d not in ['2015-06-12'] else '','label':f'最高分\\n{max_s}','color':'#ff4d4f'},
]

# 极值标记
mark_pts = []
mark_pts.append({'coord':[max_d,max_s],'value':f'最高 {max_s}','itemStyle':{'color':'#ff4d4f'}})
mark_pts.append({'coord':[min_d,min_s],'value':f'最低 {min_s}','itemStyle':{'color':'#52c41a'}})

# JS 数据
JS = {
    'dates': json.dumps(dates),
    'scores': json.dumps(scores),
    'dimData': json.dumps({k:[round(v,1) if v is not None else None for v in vl] for k,vl in dim_data.items()}),
    'labels': json.dumps(dim_labels),
    'colors': json.dumps(dim_colors),
    'marks': json.dumps(mark_pts),
    'events': json.dumps([e for e in key_events if e['date']]),
}

html_path = os.path.join(OUTPUT_DIR, 'history_chart.html')
echarts_js = open(os.path.join(os.path.dirname(__file__), '..', 'web', 'echarts.min.js')).read()

TPL = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><title>A股牛市热度指数历史走势 (2015-2026)</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0e17;color:#c9d1d9;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;padding:16px 10px}
.c{max-width:1280px;margin:0 auto}
h1{text-align:center;font-size:1.25em;color:#e0e6ed;margin-bottom:4px}
.sub{text-align:center;color:#5a6478;font-size:0.72em;margin-bottom:14px}
.sr{display:grid;grid-template-columns:repeat(7,1fr);gap:6px;margin-bottom:14px}
.sc{background:#111827;border:1px solid #1e293b;border-radius:6px;padding:8px 4px;text-align:center}
.sl{color:#5a6478;font-size:0.62em;margin-bottom:3px}
.sv{font-size:1.2em;font-weight:700}.sv.r{color:#ff4d4f}.sv.y{color:#faad14}.sv.g{color:#52c41a}.sv.c{color:__CC__}
.cb{background:#111827;border:1px solid #1e293b;border-radius:8px;padding:12px;margin-bottom:12px}
.ct{color:#5a6478;font-size:0.72em;margin-bottom:5px}
.tg{text-align:center;margin:8px 0}
.tg button{background:#1e293b;color:#6b7280;border:1px solid #334155;border-radius:4px;padding:2px 8px;margin:0 2px;cursor:pointer;font-size:0.7em}
.tg button.active{background:__CC__;color:#fff}
.f{text-align:center;padding:12px 0;color:#374151;font-size:0.65em}
</style>
</head>
<body>
<div class="c">
<h1>📈 A股牛市热度指数历史走势</h1>
<div class="sub">2015-01-05 ~ 2026-06-02 · 共 __N__ 个交易日 · 数据源: baostock+tushare+akshare</div>
<div class="sr">
<div class="sc"><div class="sl">当前</div><div class="sv c">__CUR__</div></div>
<div class="sc"><div class="sl">最高 __MD__</div><div class="sv r">__MAX__</div></div>
<div class="sc"><div class="sl">最低 __MID__</div><div class="sv g">__MIN__</div></div>
<div class="sc"><div class="sl">均值</div><div class="sv">__AVG__</div></div>
<div class="sc"><div class="sl">🔴红区≥70</div><div class="sv r">__R__天</div></div>
<div class="sc"><div class="sl">🟡黄区≥40</div><div class="sv y">__Y__天</div></div>
<div class="sc"><div class="sl">🟢绿区&lt;40</div><div class="sv g">__G__天</div></div>
</div>
<div class="cb"><div class="ct">综合热度指数（红区≥70 / 黄区≥40 / 绿区&lt;40）· 关键事件标注</div><div id="cm" style="height:350px"></div></div>
<div class="cb">
  <div class="ct">五维度走势对比</div>
  <div class="tg">
    <button onclick="td(this,'all')" class="active">全部</button>
    <button onclick="td(this,'valuation')">估值</button>
    <button onclick="td(this,'fund')">资金</button>
    <button onclick="td(this,'sentiment')">情绪</button>
    <button onclick="td(this,'technical')">技术</button>
    <button onclick="td(this,'structure')">结构</button>
  </div>
  <div id="cd" style="height:260px"></div>
</div>
<div class="f">⚠️ 仅供参考 · bull-market-heat-index v2</div>
</div>
<script>__E__</script>
<script>
var D=__DATES__,S=__SCORES__,DD=__DD__,LB=__LB__,CL=__CL__,MK=__MK__,EV=__EV__;
var mc=echarts.init(document.getElementById('cm'));
mc.setOption({
  backgroundColor:'transparent',
  tooltip:{trigger:'axis',backgroundColor:'#1e293b',borderColor:'#334155',textStyle:{color:'#c9d1d9'},
    formatter:function(p){var s=p[0].axisValue+'<br/>';p.forEach(function(x){s+=x.marker+x.seriesName+': <b>'+x.value+'</b><br/>';});return s;}},
  grid:{top:15,bottom:55,left:50,right:10},
  xAxis:{type:'category',data:D,axisLine:{lineStyle:{color:'#334155'}},axisLabel:{color:'#5a6478',fontSize:9,
    formatter:function(v){var i=D.indexOf(v);return i%Math.ceil(D.length/12)===0?v.substring(0,7):'';}}},
  yAxis:{type:'value',min:0,max:100,splitLine:{lineStyle:{color:'#1e293b'}},axisLabel:{color:'#5a6478',fontSize:9}},
  dataZoom:[
    {type:'inside',start:60,end:100},
    {type:'slider',start:60,end:100,height:16,bottom:5,borderColor:'#1e293b',backgroundColor:'#111827',fillerColor:'__CC__22',handleStyle:{color:'__CC__'},textStyle:{color:'#5a6478',fontSize:9}}
  ],
  series:[
    {name:'综合热度',type:'line',data:S,smooth:0.3,symbol:'none',
    lineStyle:{color:'__CC__',width:2},
    areaStyle:{color:{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:'__CC__33'},{offset:1,color:'transparent'}]}},
    markLine:{silent:true,lineStyle:{type:'dashed',width:1},
      data:[
        {yAxis:70,label:{formatter:'红区 70',color:'#ff4d4f',fontSize:9},lineStyle:{color:'#ff4d4f33'}},
        {yAxis:40,label:{formatter:'黄区 40',color:'#faad14',fontSize:9},lineStyle:{color:'#faad1433'}}
      ]},
    markPoint:{data:MK,symbolSize:40,label:{fontSize:9,color:'#c9d1d9'}},
    markArea:{
      silent:true,
      data:EV.filter(function(e){return e.date;}).map(function(e){
        return[{xAxis:e.date,itemStyle:{color:e.color+'15',opacity:1}},{xAxis:e.date}];
      })
    }
  }]
});

// 维度图
var ds=Object.keys(LB).map(function(k){return{name:LB[k],type:'line',data:DD[k],smooth:0.3,symbol:'none',lineStyle:{color:CL[k],width:1.5}};});
var dc=echarts.init(document.getElementById('cd'));
dc.setOption({
  backgroundColor:'transparent',
  tooltip:{trigger:'axis',backgroundColor:'#1e293b',borderColor:'#334155',textStyle:{color:'#c9d1d9'}},
  legend:{top:0,textStyle:{color:'#5a6478',fontSize:10},itemWidth:12,itemHeight:8},
  grid:{top:28,bottom:55,left:50,right:10},
  xAxis:{type:'category',data:D,axisLine:{lineStyle:{color:'#334155'}},axisLabel:{color:'#5a6478',fontSize:9,
    formatter:function(v){var i=D.indexOf(v);return i%Math.ceil(D.length/12)===0?v.substring(0,7):'';}}},
  yAxis:{type:'value',min:0,max:100,splitLine:{lineStyle:{color:'#1e293b'}},axisLabel:{color:'#5a6478',fontSize:9}},
  dataZoom:[
    {type:'inside',start:60,end:100},
    {type:'slider',start:60,end:100,height:16,bottom:5,borderColor:'#1e293b',backgroundColor:'#111827',fillerColor:'__CC__22',handleStyle:{color:'__CC__'},textStyle:{color:'#5a6478',fontSize:9}}
  ],
  series:ds
});

function td(btn,k){
  document.querySelectorAll('.tg button').forEach(function(b){b.classList.remove('active');});
  btn.classList.add('active');
  if(k==='all') dc.setOption({series:ds});
  else dc.setOption({series:ds.filter(function(s){return s.name===LB[k];})});
}
mc.on('dataZoom',function(e){var o=mc.getOption().dataZoom[0];dc.dispatchAction({type:'dataZoom',start:o.start,end:o.end});});
dc.on('dataZoom',function(e){var o=dc.getOption().dataZoom[0];mc.dispatchAction({type:'dataZoom',start:o.start,end:o.end});});
window.addEventListener('resize',function(){mc.resize();dc.resize();});
</script>
</body></html>'''

html = TPL
html = html.replace('__E__', echarts_js)
html = html.replace('__DATES__', JS['dates'])
html = html.replace('__SCORES__', JS['scores'])
html = html.replace('__DD__', JS['dimData'])
html = html.replace('__LB__', JS['labels'])
html = html.replace('__CL__', JS['colors'])
html = html.replace('__MK__', JS['marks'])
html = html.replace('__EV__', JS['events'])
html = html.replace('__CC__', cur_color)
html = html.replace('__CUR__', str(cur))
html = html.replace('__MAX__', str(max_s)).replace('__MD__', max_d[:7])
html = html.replace('__MIN__', str(min_s)).replace('__MID__', min_d[:7])
html = html.replace('__AVG__', f'{avg_s:.1f}')
html = html.replace('__R__', str(red_days))
html = html.replace('__Y__', str(yellow_days))
html = html.replace('__G__', str(green_days))
html = html.replace('__N__', str(len(hist)))

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(html)
print(f"✅ history_chart.html ({os.path.getsize(html_path)//1024}KB)")
print(f"数据: {len(hist)}天 | 最高{max_s}({max_d}) | 最低{min_s}({min_d}) | 均值{avg_s:.1f}")
print(f"🔴{red_days} 🟡{yellow_days} 🟢{green_days}")
