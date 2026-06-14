#!/usr/bin/env python3
"""
牛市热度指数日报生成器 v3.0
输出: MD + HTML(带ECharts交互图) + PNG(精简信息图)
"""
import json, os, re, sys
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'web', 'data')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'reports')
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), '..', 'web', 'report_template.html')
os.makedirs(OUTPUT_DIR, exist_ok=True)

with open(os.path.join(DATA_DIR, 'index.json')) as f: idx = json.load(f)
with open(os.path.join(DATA_DIR, 'detail.json')) as f: det = json.load(f)
with open(os.path.join(DATA_DIR, 'sectors.json')) as f: sectors = json.load(f)
with open(os.path.join(DATA_DIR, 'history.json')) as f: hist = json.load(f)
with open(os.path.join(DATA_DIR, 'run_status.json')) as f: status = json.load(f)

trade_date = idx['trade_date']
score = idx['composite_score']
level = idx['level']
ind = det.get('indicators', {})
now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
td_clean = trade_date.replace('-', '')

DIM_ORDER = ['valuation','macro','fund','sentiment','technical','structure']
DIM_LABELS = {'valuation':'估值','macro':'宏观','fund':'资金','sentiment':'情绪','technical':'技术','structure':'结构'}
DIM_WEIGHTS = {'valuation':'25%','macro':'15%','fund':'15%','sentiment':'20%','technical':'10%','structure':'15%'}
LEVEL_EMOJI = {'red':'🔴','yellow':'🟡','green':'🟢'}
LEVEL_CN = {'red':'红色预警','yellow':'黄色警惕','green':'绿色安全'}
LEVEL_LABEL = {'red':'牛市过热','yellow':'中性偏热','green':'安全低估'}
LEVEL_COLOR_HEX = {'red':'#ff4d4f','yellow':'#faad14','green':'#52c41a'}
LEVEL_BG_HEX = {'red':'#2a1215','yellow':'#2a2010','green':'#0f2a12'}
level_emoji = LEVEL_EMOJI.get(level,'⚪')
level_cn = LEVEL_CN.get(level,'未知')
level_label = LEVEL_LABEL.get(level,'')
level_color = LEVEL_COLOR_HEX.get(level,'#888')
level_bg = LEVEL_BG_HEX.get(level,'#0a0e17')
DIM_COLORS = {'valuation':'#1890ff','macro':'#13c2c2','fund':'#52c41a','sentiment':'#faad14','technical':'#722ed1','structure':'#eb2f96'}
dim_scores = {k: v['score'] for k, v in idx['dimensions'].items()}
prev_score, prev_date = None, None
if len(hist) >= 2:
    prev_score = hist[-2]['composite_score']
    prev_date = hist[-2]['trade_date']
prev_dims = {}
if len(hist) >= 2 and 'dimensions' in hist[-2]:
    for k, v in hist[-2]['dimensions'].items():
        prev_dims[k] = v.get('score') if isinstance(v, dict) else v
sectors_sorted = sorted([s for s in sectors if s.get('composite_score') is not None], key=lambda x: x['composite_score'], reverse=True)
hist_dates = [h['trade_date'][5:] for h in hist]
hist_scores = [h['composite_score'] for h in hist]
dim_hist = {k: [] for k in DIM_ORDER}
for h in hist:
    if 'dimensions' in h:
        for k in DIM_ORDER:
            v = h.get('dimensions', {}).get(k)
            dim_hist[k].append(v.get('score') if isinstance(v, dict) else v)

def score_color(v):
    if v is None: return '#374151'
    return '#ff4d4f' if v >= 70 else ('#faad14' if v >= 40 else '#52c41a')
def score_label(v):
    if v is None: return '—'
    return '偏高' if v >= 70 else ('中性' if v >= 40 else '偏低')
def bar_md(v, w=10):
    if v is None: return '░'*w
    n = int(v/100*w)
    return '█'*n+'░'*(w-n)
def delta_md(cur, prev):
    if prev is None or cur is None: return ''
    d = cur-prev
    a = '↑' if d > 0 else ('↓' if d < 0 else '→')
    return ' %s%.1f' % (a, abs(d))
def dim_delta_str(cur, prev):
    if prev is None: return ''
    d = cur-prev
    a = '▲' if d > 0 else ('▼' if d < 0 else '─')
    c = '#ff4d4f' if d > 0 else ('#52c41a' if d < 0 else '#888')
    return ' <span style="color:%s;font-weight:600;font-size:0.7em">%s%.1f</span>' % (c, a, abs(d))

_hl = []
vi = ind.get('valuation',{}); fi = ind.get('fund',{}); si = ind.get('sentiment',{})
ti = ind.get('technical',{}); sti = ind.get('structure',{}); mi = ind.get('macro',{})
if vi.get('PE_percentile') is not None: _hl.append(('PE历史分位','%.0f%%'%vi['PE_percentile'],'近10年'))
if vi.get('PB_percentile') is not None: _hl.append(('PB历史分位','%.0f%%'%vi['PB_percentile'],'近10年'))
if vi.get('below_net_rate') is not None: _hl.append(('破净率','%.1f%%'%vi['below_net_rate'],'全市场'))
if mi.get('m1m2_scissors') is not None: _hl.append(('M1-M2剪刀差','%.1f'%mi['m1m2_scissors'],'月频'))
if mi.get('m2_yoy') is not None: _hl.append(('M2同比','%.1f%%'%mi['m2_yoy'],'月频'))
nbv = fi.get('northbound_cumflow', fi.get('northbound'))
if nbv is not None: _hl.append(('北向资金','%.1f'%nbv,'变化率'))
mr = fi.get('margin_ratio')
if mr is not None: _hl.append(('融资余额','%.1f'%mr,'变化率'))
if si.get('turnover') is not None: _hl.append(('换手率','%.2f%%'%si['turnover'],'全市场'))
if si.get('up_down_ratio') is not None: _hl.append(('涨跌家数比','%.2f'%si['up_down_ratio'],'涨/跌'))
if si.get('limit_up_ratio') is not None: _hl.append(('涨停占比','%.2f%%'%si['limit_up_ratio'],'全市场'))
if si.get('limit_ratio') is not None: _hl.append(('涨跌停比','%.1f'%si['limit_ratio'],'涨停/跌停'))
ma_val = ti.get('above_ma250_ratio', ti.get('ma_alignment'))
if ma_val is not None: _hl.append(('MA排列比','%.1f%%'%ma_val,'MA20>60>120'))
if ti.get('deviation_ma250') is not None: _hl.append(('均线偏离','%.1f%%'%ti['deviation_ma250'],'vsMA250'))
if sti.get('sector_divergence') is not None: _hl.append(('行业分化','%.1f分'%sti['sector_divergence'],'月频'))
ah = sti.get('ah_premium_index', sti.get('ah_premium'))
if ah is not None: _hl.append(('AH溢价','%.0f'%ah,'恒生HSAHP'))

n_ok = sum(1 for v in status['steps'].values() if v['status']=='OK')
n_fail = sum(1 for v in status['steps'].values() if v['status']=='FAILED')
n_skip = sum(1 for v in status['steps'].values() if v['status']=='SKIPPED')

# ── 1. MD ──
md = []
md.append('# %s A股牛市热度指数日报' % level_emoji)
md.append('> 📅 **%s**  ·  🕐 %s  ·  📊 日频' % (trade_date, now_str))
md.append('---')
md.append('## 综合热度: %s %.1f — %s' % (level_emoji, score, level_cn))
md.append('```')
md.append('  %s  %.0f/100' % (bar_md(score), score))
md.append('```')
if prev_score is not None:
    d = score-prev_score
    a = '↑' if d > 0 else ('↓' if d < 0 else '→')
    md.append('**较上一交易日 (%s): %s %.1f 分**' % (prev_date, a, abs(d)))
md.append('---')
md.append('## 五维度拆解')
md.append('| 维度 | 权重 | 得分 | 较昨日 | 评估 |')
md.append('|:-----|:----:|-----:|:------:|:----:|')
for k in DIM_ORDER:
    s = dim_scores.get(k)
    ps = prev_dims.get(k)
    if s is None:
        md.append('| %s | %s | — | — | — |' % (DIM_LABELS[k], DIM_WEIGHTS[k]))
    else:
        ev = '🔴 偏高' if s>=70 else ('🟡 中性' if s>=40 else '🟢 偏低')
        md.append('| **%s** | %s | **%.0f** | %s | %s |' % (DIM_LABELS[k], DIM_WEIGHTS[k], s, delta_md(s,ps), ev))
md.append('```')
for k in DIM_ORDER:
    s = dim_scores.get(k)
    ps = prev_dims.get(k)
    md.append('  %s  %s  %s  %s' % (DIM_LABELS[k], bar_md(s), '%5.1f'%s if s else '  — ', delta_md(s,ps)))
md.append('```')
md.append('### 📌 关键指标')
for name, val, note in _hl:
    md.append('- **%s** `%s` (%s)' % (name, val, note))
md.append('---')
md.append('## 🔥 板块热度 TOP10')
md.append('| # | 行业 | 得分 | 龙头股 | 涨跌 |')
md.append('|:--|:-----|-----:|:------:|-----:|')
for i, s in enumerate(sectors_sorted[:10], 1):
    ldr = s.get('leader',{})
    md.append('| %d | %s | **%.0f** | %s | %s |' % (i, s.get('sector_name',''), s.get('composite_score',0), ldr.get('code','—') if ldr else '—', '%+.1f%%'%ldr.get('pct',0) if ldr else '—'))
md.append('---')
md.append('## 📈 历史走势 (共%d个交易日)' % len(hist))
md.append('| 日期 | 得分 | 状态 |')
md.append('|:-----|-----:|:----:|')
for h in hist[-10:]:
    md.append('| %s | %.1f | %s %s |' % (h['trade_date'], h['composite_score'], LEVEL_EMOJI.get(h['level'],'⚪'), h['level']))
md.append('---')
md.append('## 📊 历史参考')
md.append('| 日期 | 市场状态 | 综合得分 |')
md.append('|:-----|:---------|--------:|')
md.append('| 2015-06-12 | 牛市顶 (上证5178) | 🔴 73.8 |')
md.append('| 2021-02-18 | 牛市顶 (上证3731) | ⚪ 66.2 |')
md.append('| 2024-10-08 | 脉冲顶 (上证3489) | ⚪ 65.1 |')
md.append('| 2018-12-28 | 熊底 (上证2493) | 🟢 28.5 |')
md.append('| **%s** | **★ 当前** | **%s %.1f** |' % (trade_date, level_emoji, score))
md.append('---')
md.append('## ⚙️ 运行状态: %d✅ %d❌ %d⏭️' % (n_ok, n_fail, n_skip))
for sn, sv in status['steps'].items():
    ic = '✅' if sv['status']=='OK' else ('⏭️' if sv['status']=='SKIPPED' else '❌')
    md.append('- %s `%s` %s (%.1fs)' % (ic, sn, sv['status'], sv.get('elapsed',0)))
md.append('---')
md.append('> ⚠️ 不构成投资建议，仅供参考')
md.append('> bull-market-heat-index v3.4 · tushare + akshare + 东方财富')
with open(os.path.join(OUTPUT_DIR,'daily_%s.md'%td_clean),'w',encoding='utf-8') as f: f.write('\n'.join(md))
print('MD saved: daily_%s.md' % td_clean)

# ── 2. HTML ──
if os.path.exists(TEMPLATE_PATH):
    tpl = open(TEMPLATE_PATH).read()
else:
    tpl = '<html><body><pre>{{CONTENT}}</pre></body></html>'

dim_cards = ''
for k in DIM_ORDER:
    s = dim_scores.get(k); dc = score_color(s); pct = min(max(s,0),100) if s else 0
    ps = prev_dims.get(k); sl = score_label(s); d_html = dim_delta_str(s,ps) if ps else ''
    if s is None:
        dim_cards += '<div class="dim-card na"><div class="dim-header"><span class="dim-name">%s</span><span class="dim-weight">%s</span></div><div class="dim-score">—</div><div class="dim-eval">数据暂缺</div></div>' % (DIM_LABELS[k],DIM_WEIGHTS[k])
    else:
        dim_cards += '<div class="dim-card"><div class="dim-header"><span class="dim-name">%s</span><span class="dim-weight">%s</span></div><div class="dim-score" style="color:%s">%.0f<span class="dim-delta">%s</span></div><div class="dim-eval" style="color:%s">%s</div><div class="dim-bar"><div class="dim-bar-fill" style="width:%d%%;background:%s"></div></div></div>' % (DIM_LABELS[k],DIM_WEIGHTS[k],dc,s,d_html,dc,sl,int(pct),dc)

sector_rows = ''
for i, s in enumerate(sectors_sorted[:10], 1):
    sc = s.get('composite_score',0); scol = score_color(sc); ldr = s.get('leader',{})
    lc_code = ldr.get('code','—') if ldr else '—'; lp = ldr.get('pct',0) if ldr else 0
    lc_str = ('%+.1f%%'%lp) if lc_code!='—' else '—'; lcol = '#ff4d4f' if lp>0 else '#52c41a'
    sector_rows += '<tr><td class="rank">%d</td><td>%s</td><td style="color:%s;font-weight:700">%.0f</td><td><code>%s</code></td><td style="color:%s;font-weight:600">%s</td></tr>\n' % (i,s.get('sector_name',''),scol,sc,lc_code,lcol,lc_str)

hl_html = ''.join(['<li><span class="hl-name">%s</span><span class="hl-val">%s</span><span class="hl-note">%s</span></li>\n' % (n,v,t) for n,v,t in _hl])

delta_html = ''
if prev_score is not None:
    d=score-prev_score; arrow='▲' if d>0 else ('▼' if d<0 else '─'); dc='#ff4d4f' if d>0 else ('#52c41a' if d<0 else '#888')
    delta_html = '<div class="delta">较上一交易日 <span style="color:%s">%s %.1f</span></div>' % (dc,arrow,abs(d))

status_items = ''.join(['<div class="status-item">%s <code>%s</code> <span class="%s">%s</span> <span class="elapsed">%.1fs</span></div>' % ('✅' if v['status']=='OK' else ('⏭️' if v['status']=='SKIPPED' else '❌'),sn,v['status'].lower(),v['status'],v.get('elapsed',0)) for sn,v in status['steps'].items()])

dim_hist_js = {k: [round(v,1) if v is not None else None for v in dim_hist[k]] for k in dim_hist}

def rep(s, old, new):
    return s.replace(old, new)

out = tpl
out = rep(out, '__TRADE_DATE__', trade_date)
out = rep(out, '__NOW_STR__', now_str)
out = rep(out, '__LEVEL_COLOR__', level_color)
out = rep(out, '__LEVEL_BG__', level_bg)
out = rep(out, '__LEVEL_EMOJI__', level_emoji)
out = rep(out, '__LEVEL_CN__', level_cn)
out = rep(out, '__LEVEL_LABEL__', level_label)
out = rep(out, '__SCORE__', '%.1f' % score)
out = rep(out, '__SCORE_PCT__', str(min(max(int(score),0),100)))
out = rep(out, '__SCORE_DASH__', str(score*4.4))
out = rep(out, '__DELTA_HTML__', delta_html)
out = rep(out, '__DIM_CARDS__', dim_cards)
out = rep(out, '__HL_HTML__', hl_html)
out = rep(out, '__SECTOR_ROWS__', sector_rows)
out = rep(out, '__STATUS_ITEMS__', status_items)
out = rep(out, '__N_OK__', str(n_ok))
out = rep(out, '__N_FAIL__', str(n_fail))
out = rep(out, '__N_SKIP__', str(n_skip))
out = rep(out, '__HIST_DATES__', json.dumps(hist_dates))
out = rep(out, '__HIST_SCORES__', json.dumps(hist_scores))
out = rep(out, '__DIM_COLORS__', json.dumps([DIM_COLORS[k] for k in DIM_ORDER]))
out = rep(out, '__DIM_NAMES__', json.dumps([DIM_LABELS[k] for k in DIM_ORDER]))
out = rep(out, '__DIM_DATA__', json.dumps([dim_hist_js[k] for k in DIM_ORDER]))

with open(os.path.join(OUTPUT_DIR,'daily_%s.html'%td_clean),'w',encoding='utf-8') as f: f.write(out)
print('HTML saved: daily_%s.html' % td_clean)

# ── 3. PNG ──
try:
    from PIL import Image, ImageDraw, ImageFont as IF
    _font_path = next((p for p in ['/System/Library/Fonts/PingFang.ttc','/System/Library/Fonts/Hiragino Sans GB.ttc','/System/Library/Fonts/STHeiti Medium.ttc'] if os.path.exists(p)), None)
    def _f(sz):
        try: return IF.truetype(_font_path, sz)
        except: return IF.load_default()
    C = {'bg':(10,14,23),'card':(17,24,39),'border':(30,41,59),'text':(201,209,217),'bright':(224,230,237),'dim':(107,114,128),'muted':(55,65,81),'red':(255,77,79),'yellow':(250,173,20),'green':(82,196,26)}
    _lc = score_color(score); W,PAD = 880,36; TOTAL_H=2200
    img=Image.new('RGB',(W,TOTAL_H),C['bg']); draw=ImageDraw.Draw(img); y=PAD
    def _txt(s,x,y,color=C['text'],size=14,bold=False):
        font=_f(size+(4 if bold else 0)); draw.text((x,y),s,fill=color,font=font); return y+size+6
    def _bar(cx,cy,w,sc,color,h=6):
        draw.rectangle([(cx,cy),(cx+w,cy+h)],fill=C['border'])
        fw=int(w*min(max(sc,0),100)/100)
        if fw>0: draw.rectangle([(cx,cy),(cx+fw,cy+h)],fill=color)
    def _rr(xy,r=6,fill=None,outline=None): draw.rounded_rectangle(xy,radius=r,fill=fill,outline=outline,width=1)
    _rr([(PAD,y),(W-PAD,y+72)],r=10,fill=C['card'],outline=C['border'])
    t="A股牛市热度指数日报"; bbox=_f(20).getbbox(t); draw.text(((W-(bbox[2]-bbox[0]))//2,y+12),t,fill=C['bright'],font=_f(20))
    m="📅 %s  ·  🕐 %s  ·  📈 日频"%(trade_date,now_str); bbox=_f(11).getbbox(m); draw.text(((W-(bbox[2]-bbox[0]))//2,y+42),m,fill=C['dim'],font=_f(11))
    y+=88
    _rr([(PAD,y),(W-PAD,y+180)],r=10,fill=C['card'],outline=C['border'])
    ss="%.1f"%score; bbox=_f(64).getbbox(ss); draw.text(((W-(bbox[2]-bbox[0]))//2,y+16),ss,fill=_lc,font=_f(64))
    bbox=_f(16).getbbox(level_cn); draw.text(((W-(bbox[2]-bbox[0]))//2,y+90),level_cn,fill=_lc,font=_f(16))
    if level_label:
        bbox=_f(11).getbbox(level_label); draw.text(((W-(bbox[2]-bbox[0]))//2,y+114),level_label,fill=C['dim'],font=_f(11))
    bar_y=y+134; _bar(PAD+60,bar_y,W-2*PAD-120,score,_lc,8)
    for tick,tx in [(0,'0'),(40,'40'),(65,'65'),(100,'100')]:
        tx_x=PAD+60+int((W-2*PAD-120)*tick/100)-8; draw.text((tx_x,bar_y+12),tx,fill=C['muted'],font=_f(9))
    if prev_score is not None:
        d=score-prev_score; arrow='▲' if d>0 else ('▼' if d<0 else '─'); dc=C['red'] if d>0 else C['green']
        ds="较上一交易日: %s %.1f"%(arrow,abs(d)); bbox=_f(11).getbbox(ds); draw.text(((W-(bbox[2]-bbox[0]))//2,y+156),ds,fill=dc,font=_f(11))
    y+=196
    y=_txt("五维度拆解",PAD,y,C['bright'],16,True); y+=4
    cw=(W-2*PAD-5*8)//6; ch=88; cy0=y
    for i,dk in enumerate(DIM_ORDER):
        s=dim_scores.get(dk); sc=s if s else 0; scol=score_color(s)
        cx=PAD+i*(cw+8); _rr([(cx,cy0),(cx+cw,cy0+ch)],r=8,fill=C['card'],outline=C['border'])
        draw.text((cx+8,cy0+8),DIM_LABELS[dk],fill=C['dim'],font=_f(11))
        bbox=_f(9).getbbox(DIM_WEIGHTS[dk]); draw.text((cx+cw-(bbox[2]-bbox[0])-8,cy0+10),DIM_WEIGHTS[dk],fill=C['muted'],font=_f(9))
        ss2="%.0f"%sc if s else "—"; bbox=_f(32).getbbox(ss2); draw.text((cx+(cw-(bbox[2]-bbox[0]))//2,cy0+26),ss2,fill=scol,font=_f(32))
        sl=score_label(s); bbox=_f(10).getbbox(sl); draw.text((cx+(cw-(bbox[2]-bbox[0]))//2,cy0+62),sl,fill=scol,font=_f(10))
        _bar(cx+8,cy0+76,cw-16,sc,scol,4)
    y=cy0+ch+20
    y=_txt("关键指标",PAD,y,C['bright'],16,True); y+=4
    col_w=(W-2*PAD-12)//2
    for idx2,(name,val,note) in enumerate(_hl[:10]):
        col=idx2%2; row=idx2//2; mx=PAD+col*(col_w+12); my=y+row*32
        _rr([(mx,my),(mx+col_w,my+28)],r=4,fill=C['card'],outline=C['border'])
        draw.text((mx+10,my+6),name,fill=C['dim'],font=_f(11))
        draw.text((mx+100,my+6),val,fill=C['bright'],font=_f(12))
        draw.text((mx+col_w-60,my+6),note,fill=C['muted'],font=_f(10))
    y+=((len(_hl[:10])+1)//2)*32+16
    y=_txt("板块热度 TOP10",PAD,y,C['bright'],16,True); y+=4
    _rr([(PAD,y),(W-PAD,y+24)],r=4,fill=C['border'])
    draw.text((PAD+12,y+5),"#",fill=C['dim'],font=_f(10)); draw.text((PAD+50,y+5),"行业",fill=C['dim'],font=_f(10))
    draw.text((PAD+310,y+5),"得分",fill=C['dim'],font=_f(10)); draw.text((PAD+380,y+5),"龙头股",fill=C['dim'],font=_f(10))
    draw.text((PAD+520,y+5),"涨跌幅",fill=C['dim'],font=_f(10)); y+=28
    for i,s in enumerate(sectors_sorted[:10],1):
        sc=s.get('composite_score',0); scol=score_color(sc); ldr=s.get('leader',{})
        lcode=ldr.get('code','') if ldr else ''; lp=ldr.get('pct',0) if ldr else 0
        ls2=('%+.1f%%'%lp) if lcode else ''; lcol=C['red'] if lp>0 else C['green']
        if i%2==0: _rr([(PAD,y),(W-PAD,y+22)],r=0,fill=C['card'])
        draw.text((PAD+12,y+3),str(i),fill=scol if i<=3 else C['dim'],font=_f(12))
        draw.text((PAD+50,y+3),s.get('sector_name','')[:16],fill=C['text'],font=_f(12))
        draw.text((PAD+310,y+3),"%.0f"%sc,fill=scol,font=_f(12))
        draw.text((PAD+380,y+3),lcode,fill=C['dim'],font=_f(11))
        draw.text((PAD+520,y+3),ls2,fill=lcol,font=_f(12)); y+=24
    y+=12
    y=_txt("历史参考",PAD,y,C['bright'],14,True); y+=4
    refs=[("2015-06-12","牛市顶 (上证5178)","73.8",C['red']),("2021-02-18","牛市顶 (上证3731)","66.2",C['dim']),
          ("2024-10-08","脉冲顶 (上证3489)","65.1",C['dim']),("2018-12-28","熊底 (上证2493)","28.5",C['green']),
          (trade_date,"★ 当前","%.1f"%score,_lc)]
    for dt2,st2,sc2,sc2c in refs:
        _rr([(PAD,y),(W-PAD,y+24)],r=4,fill=C['card'] if dt2!=trade_date else C['border'])
        draw.text((PAD+12,y+5),dt2,fill=sc2c,font=_f(12))
        draw.text((PAD+120,y+5),st2,fill=C['text'],font=_f(12))
        bbox=_f(12).getbbox(sc2); draw.text((W-PAD-12-(bbox[2]-bbox[0]),y+5),sc2,fill=sc2c,font=_f(12)); y+=28
    y+=16
    draw.line([(PAD,y+10),(W-PAD,y+10)],fill=C['border'],width=1); y+=20
    ft="运行状态: %d OK / %d FAILED / %d SKIPPED  ·  %d个板块"%(n_ok,n_fail,n_skip,len(sectors))
    bbox=_f(10).getbbox(ft); draw.text(((W-(bbox[2]-bbox[0]))//2,y),ft,fill=C['muted'],font=_f(10)); y+=18
    wt="⚠️ 不构成投资建议，仅供参考  ·  v3.4"
    bbox=_f(10).getbbox(wt); draw.text(((W-(bbox[2]-bbox[0]))//2,y),wt,fill=C['muted'],font=_f(10)); y+=24
    img.crop((0,0,W,y+12)).save(os.path.join(OUTPUT_DIR,'daily_%s.png'%td_clean),'PNG',optimize=True)
    print('PNG saved: daily_%s.png' % td_clean)
except ImportError:
    print('Pillow not installed, skipping PNG')

print('Done: daily_%s.{md,html,png}' % td_clean)
