"""Terminal workspace and standalone inline-SVG calibration frame."""
import html
import json
from pathlib import Path


def terminal(report, interactive=False, detail_root=None):
    from .calibration_output import read_details
    report = read_details(report, detail_root)
    from .calibration import costs
    p = report['parameters']
    while True:
        s = costs(report['events'], p, p['threshold'])
        title = 'LOCAL SCOUT | CALIBRATION | ESTIMATE'
        assumptions = f"{p['tokens_per_task']:g} tokens/task; {p['price_per_million']:g} {p['currency']}/1M; local {p['local_cost']:g}/attempt; setup {p['setup_cost']:g}; error {p['error_cost']:g}"
        lines = [title, 'Backend: '+report['backend']+(' | model unavailable → LSA' if report['model_fallback'] else ''), assumptions, f"Threshold {p['threshold']:.2f} | coverage {s['coverage']:.1%} | proxy errors {s['errors']} / {s['labeled_accepted']} | tokens {s['estimated_tokens_saved']:g} | net {s['estimated_net']} | break-even {s['break_even_n']}"]
        lines += ['Family       n    coverage   tokens est.    net est.']
        for f in report['families']:
            q = costs([e for e in report['events'] if e['family'] == f['id']], p, p['threshold'])
            lines.append(f"{f['id']:12} {q['count']:4} {q['coverage']:9.1%} {q['estimated_tokens_saved']:12g} {str(q['estimated_net']):>12}")
        lines.append('Threshold → coverage | proxy errors | estimated tokens')
        for c in report['curves']:
            lines.append(f"{c['threshold']:.2f} {'#'*round(c['coverage']*30):30} {str(c['errors']):>4} {c['estimated_tokens_saved']:10g}")
        lines += ['Timeline: R=new→remote, !=noticed, L=would handle locally', ''.join('L' if e['noticed'] and e['neighbor'] and e['similarity'] >= p['threshold'] else '!' if e['first_notice'] else 'R' for e in report['events']), report['protocol'], 'Errors proxy the nearest previous label, not model quality.']
        try:
            from rich.console import Console
            from rich.panel import Panel
            Console().print(Panel('\n'.join(lines), title=title), markup=False)
        except ImportError:
            print('\n'.join(lines))
        if not interactive:
            return
        answer = input('Threshold 0..1, q = quit > ').strip().lower()
        if answer == 'q':
            return
        try:
            number = float(answer)
            if 0 <= number <= 1:
                p['threshold'] = number
        except ValueError:
            pass


def write_html(report, path, detail_root=None):
    from .calibration_output import read_details
    report = read_details(report, detail_root)
    data = json.dumps(report, ensure_ascii=True, allow_nan=False).replace('<', '\\u003c')
    page = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Local Scout — local calibration</title>
<style>body{margin:0;background:#0a1020;color:#e6edf7;font:18px system-ui}main{max-width:1820px;margin:auto;padding:22px}h1{font-size:32px;margin:0}h2{font-size:22px;margin:8px 0}.muted{color:#a4b4cd}header{display:flex;justify-content:space-between;align-items:center}.badge{color:#6ce4bd}#assumptions{background:#16233b;padding:12px 18px;margin:14px 0;line-height:1.5}.kpis{display:flex;gap:16px;margin:14px 0}.kpi{flex:1;background:#16233b;padding:14px;border-radius:10px}.kpi b{display:block;font-size:28px;color:#6ce4bd}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.panel{background:#101b30;padding:12px 18px;border:1px solid #283955;border-radius:12px}svg{width:100%;height:190px}text{fill:#c6d2e4;font:19px system-ui}table{border-collapse:collapse;width:100%;font-size:16px}td,th{text-align:left;padding:6px;border-bottom:1px solid #283955}#families{max-height:150px;overflow:auto}input{width:220px;accent-color:#6ce4bd}footer{font-size:15px;margin-top:12px;color:#a4b4cd}@media(max-width:850px){.grid{grid-template-columns:1fr}.kpis{flex-wrap:wrap}}</style>
<main><header><div><h1>Local Scout / history calibration</h1><span class="muted">Prefix replay • local vectors • no content or PII in the report</span></div><div class="badge">OFFLINE · ESTIMATE<br><label>Threshold <input id="threshold" type="range" min="0" max="1" step=".01"><b id="th"></b></label></div></header>
<div id="assumptions"></div><div class="kpis" id="kpis"></div>
<div class="grid"><section class="panel"><h2>Coverage and proxy errors / threshold</h2><svg id="quality" viewBox="0 0 840 225"></svg></section><section class="panel"><h2>Estimated token reduction / threshold</h2><svg id="savings" viewBox="0 0 840 225"></svg></section><section class="panel"><h2>Timeline — task order</h2><svg id="timeline" viewBox="0 0 840 225"></svg></section><section class="panel"><h2>Families / 2D PCA (retrospective)</h2><svg id="map" viewBox="0 0 840 225"></svg></section></div>
<div class="panel" style="margin-top:14px"><h2>Evidence by family — no recommendation</h2><div id="families"></div></div>
<footer>Green: would handle locally · yellow: noticed, sent remote · grey: new → remote. Errors proxy the nearest earlier task label; missing labels are unknown. This does not establish trained recipe quality. Setup is counted once globally and separately for standalone families. PCA does not affect replay. Actual tokens and prices are not measured.</footer></main>
<script>const D=DATA;const P=D.parameters;const $=id=>document.getElementById(id);const fmt=x=>x===null?'unknown':Number(x).toLocaleString('en-US',{maximumFractionDigits:2});const pct=x=>(100*x).toFixed(1)+'%';const col=e=>e.noticed&&e.neighbor&&e.similarity>=P.threshold?'#6ce4bd':e.noticed?'#ffd079':'#6b7d99';
function stat(es){let ls=es.filter(e=>e.noticed&&e.neighbor&&e.similarity>=P.threshold),known=ls.filter(e=>e.wrong!==null),err=known.reduce((a,e)=>a+e.wrong,0),rate=known.length?err/known.length:0,cloud=P.tokens_per_task*P.price_per_million/1e6,coverage=es.length?ls.length/es.length:0,margin=coverage*cloud-P.local_cost-coverage*rate*P.error_cost;return {n:es.length,l:ls.length,coverage,errors:known.length?err:null,tokens:ls.length*P.tokens_per_task,net:ls.length!==known.length&&P.error_cost>0?null:es.length*margin-P.setup_cost,be:ls.length!==known.length&&P.error_cost>0?null:margin>0?Math.ceil(P.setup_cost/margin):null}}
function plot(id,series,maxY,unit){let svg=$ (id),axis='<path d="M65 15V175H815" fill="none" stroke="#65758e"/>';axis+='<text x="65" y="213">threshold 0</text><text x="748" y="213">1.0</text><text x="4" y="28">'+unit+'</text>';for(let i=0;i<=4;i++){let y=175-i*38;axis+='<text x="4" y="'+(y+5)+'">'+fmt(maxY*i/4)+'</text>'}series.forEach((s,j)=>{let points=D.curves.filter(c=>c[s.key]!==null).map(c=>(65+c.threshold*750)+','+(175-c[s.key]/(maxY||1)*152)).join(' ');axis+='<polyline points="'+points+'" fill="none" stroke="'+s.color+'" stroke-width="3"/><text x="'+(260+j*270)+'" y="213" style="fill:'+s.color+'">'+s.label+'</text>'});axis+='<line x1="'+(65+P.threshold*750)+'" x2="'+(65+P.threshold*750)+'" y1="15" y2="175" stroke="#fff" stroke-dasharray="4 4"/>';svg.innerHTML=axis}
function render(){P.threshold=Number($('threshold').value);$('th').textContent=P.threshold.toFixed(2);let s=stat(D.events);$('kpis').innerHTML=[['Tasks',s.n],['Coverage',pct(s.coverage)],['Proxy errors',fmt(s.errors)],['Tokens / est.',fmt(s.tokens)],['Net / est. '+P.currency,fmt(s.net)],['Break-even / tasks',fmt(s.be)]].map(([a,b])=>'<div class="kpi">'+a+'<b>'+b+'</b></div>').join('');plot('quality',[{key:'coverage',color:'#6ce4bd',label:'coverage 0..1'},{key:'accepted_error',color:'#ff8494',label:'accepted error 0..1'}],1,'fraction');plot('savings',[{key:'estimated_tokens_saved',color:'#7eb8ff',label:'tokens — ESTIMATE'}],Math.max(1,...D.curves.map(c=>c.estimated_tokens_saved)),'tokens');let families=D.families.map(f=>f.id),n=D.events.length;let timeline='<text x="8" y="211">1 → '+n+' | ! = first notice | N='+P.min_cases+'</text>';D.events.forEach(e=>{let x=45+(e.t-1)*760/Math.max(n-1,1),y=20+families.indexOf(e.family)*155/Math.max(families.length-1,1);timeline+='<circle cx="'+x+'" cy="'+y+'" r="3.5" fill="'+col(e)+'"><title>'+e.id+' / '+e.family+' / similarity '+e.similarity.toFixed(3)+'</title></circle>';if(e.first_notice)timeline+='<text x="'+x+'" y="'+Math.max(15,y-5)+'">!</text>'});families.forEach((f,i)=>{timeline+='<text x="4" y="'+(24+i*155/Math.max(families.length-1,1))+'" style="font-size:13px">'+f.replace('family-','F')+'</text>'});$('timeline').innerHTML=timeline;let xs=D.map.map(m=>m.x),ys=D.map.map(m=>m.y),lo=Math.min(...xs),hi=Math.max(...xs),bot=Math.min(...ys),top=Math.max(...ys);let labels=families.map(f=>{let ms=D.map.filter(m=>m.family===f),x=ms.reduce((a,m)=>a+m.x,0)/ms.length,y=ms.reduce((a,m)=>a+m.y,0)/ms.length;return '<text x="'+(30+(x-lo)*775/(hi-lo||1))+'" y="'+(20+(y-bot)*160/(top-bot||1))+'" style="font-size:14px;paint-order:stroke;stroke:#101b30;stroke-width:3px">'+f+'</text>'}).join('');$('map').innerHTML=D.map.map((m,i)=>'<circle cx="'+(30+(m.x-lo)*775/(hi-lo||1))+'" cy="'+(15+(m.y-bot)*160/(top-bot||1))+'" r="4" fill="'+col(D.events[i])+'"><title>'+m.id+' / '+m.family+'</title></circle>').join('')+labels+'<text x="30" y="213">PC1 / PC2 · hover for family</text>';$('families').innerHTML='<table><tr><th>Family</th><th>n</th><th>Coverage</th><th>Tokens est.</th><th>Net est. '+P.currency+'</th></tr>'+D.families.map(f=>{let z=stat(D.events.filter(e=>e.family===f.id));return '<tr><td>'+f.id+'</td><td>'+z.n+'</td><td>'+pct(z.coverage)+'</td><td>'+fmt(z.tokens)+'</td><td>'+fmt(z.net)+'</td></tr>'}).join('')+'</table>'}
$('assumptions').textContent='ASSUMPTIONS: '+P.tokens_per_task+' tokens/task · '+P.price_per_million+' '+P.currency+'/1M tokens · local cost '+P.local_cost+'/attempt · setup '+P.setup_cost+' · error cost '+P.error_cost+' · seed '+P.seed+' · family cosine ≥ '+P.family_similarity+' · backend '+D.backend+' · '+D.order;$('threshold').value=P.threshold;$('threshold').oninput=render;render();</script></html>'''
    Path(path).write_text(page.replace('DATA', data, 1), encoding='utf-8')


def insights_terminal(summary):
    """Plain local summary; no dataset identifiers or retained free text."""
    print(f"Scout Insights — last {summary['period_days']} days (local only)")
    print('Choices, training, predictions and calibration use retained Scout state; individual timestamps are unavailable.')
    print('Operations:')
    if summary['operations']:
        for tool, counts in sorted(summary['operations'].items()):
            errors = ', '.join(f'{code}={count}' for code, count in sorted(counts['errors'].items())) or 'none'
            print(f"  {tool}: ok={counts['ok']}; errors: {errors}; {counts['duration_ms']} ms; {counts['response_bytes']} response bytes")
    else:
        print('  no recorded tool operations')
    print('Observed category alerts: ' + str(summary.get('observed_categories', {})))
    print('PII masks by category: ' + (', '.join(f'{k}={v}' for k, v in sorted(summary['pii_masks'].items())) or 'none'))
    print('PII alerts by source: ' + (', '.join(f'{source}=' + ', '.join(f'{k}:{v}' for k, v in sorted(counts.items()))
                                              for source, counts in sorted(summary['pii_by_source'].items())) or 'none'))
    print(f"Predictions: accepted={summary['predictions']['accepted']}; refused={summary['predictions']['refused']}")
    methods = ', '.join(f"{family}: " + ', '.join(f'{method}={count}' for method, count in sorted(counts.items()))
                        for family, counts in sorted(summary['methods'].items()))
    print('Selected methods: ' + (methods or 'none'))
    print('Training outcomes: ' + (', '.join(f'{k}={v}' for k, v in sorted(summary['training'].items())) or 'none'))
    print(f"Calibration events: {summary['calibration_events']}")
    tokens = summary['tokens_observed']
    if tokens.get('input') is None:
        print('Observed tokens: not observed (' + str(tokens.get('reason', 'no accepted usage event')) + ')')
    else:
        print(f"Observed tokens: input={tokens['input']}; output={tokens['output']}")
    print('Hermes tools: ' + str(summary.get('hermes_operations', {})))
    measured_input = tokens.get('input')
    measured_note = f"; measured input usage={measured_input}" if measured_input is not None else '; measured input usage not observed'
    print('Estimated input tokens avoided: ' + str(summary.get('estimated_tokens_avoided', 0)) + measured_note)
    print('Abstained: ' + str(summary.get('abstained', 0)))
    print('Prompt PII category warnings: ' + str(summary.get('prompt_warnings', 0)))
    print('Prompt injection alerts (separate): ' + str(summary.get('prompt_injection_alerts', 0)))
    print('Net tokens: ' + str(summary.get('savings', {})))
    print('Handled locally: ' + ('not observed' if summary['handled_locally'] is None else str(summary['handled_locally']))
          + ' (sources: ' + str(summary.get('handling_sources', {})) + ')')
    local = summary.get('local_answers', {})
    print('Local answers (middleware): answered=' + str(local.get('answered', 0)) +
          '; forwarded=' + str(local.get('forwarded', 0)) +
          '; forwarded reasons=' + str(summary.get('forwarded_reasons', {})) +
          '; estimated input tokens avoided=' + str(local.get('estimated_input_tokens_avoided', 0)))
    print('Token estimate: estimate; one live comparison showed ~4.7× above measured input; not an upper bound guarantee.')


def write_insights_html(summary, path):
    """Write a standalone local HTML view, escaping every displayed value."""
    import html
    from pathlib import Path

    sections = [('Operations', summary['operations']), ('Hermes operations', summary.get('hermes_operations', {})),
                ('Savings estimate and agent cost', summary.get('savings', {})),
                ('Handle telemetry', {k: summary.get(k) for k in ('handled_locally', 'handling_sources', 'abstained', 'estimated_tokens_avoided', 'prompt_warnings', 'prompt_injection_alerts')}),
                ('Local answers (middleware)', {'answered': summary.get('local_answers', {}).get('answered', 0),
                    'forwarded': summary.get('local_answers', {}).get('forwarded', 0),
                    'forwarded reasons': summary.get('forwarded_reasons', {}),
                    'estimated input tokens avoided': summary.get('local_answers', {}).get('estimated_input_tokens_avoided', 0)}),
                ('PII masks', summary['pii_masks']), ('Observed category alerts', summary.get('observed_categories', {})),
                ('PII alerts by source', summary['pii_by_source']),
                ('Predictions', summary['predictions']), ('Selected methods', summary['methods']),
                ('Training', summary['training']), ('Calibration events', {'total': summary['calibration_events']}),
                ('Observed tokens', summary['tokens_observed'])]
    blocks = []
    for title, values in sections:
        rows = []
        for key, value in sorted(values.items()):
            if isinstance(value, dict):
                detail = ', '.join(f'{html.escape(str(k))}: {html.escape(str(v))}' for k, v in sorted(value.items()))
                rows.append(f'<tr><th>{html.escape(str(key))}</th><td>{detail}</td></tr>')
            else:
                rows.append(f'<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>')
        blocks.append(f'<section><h2>{html.escape(title)}</h2><table>{"".join(rows) or "<tr><td>None</td></tr>"}</table></section>')
    savings = 'not observed' if summary['handled_locally'] is None else str(summary['handled_locally'])
    token_view = summary['tokens_observed']
    token_text = ('Observed input usage: not observed (' + str(token_view.get('reason', 'no accepted usage event')) + ')'
                  if token_view.get('input') is None else 'Observed input usage: ' + str(token_view['input']))
    page = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>Scout Insights</title><style>body{font:16px system-ui;max-width:900px;margin:2rem auto;padding:0 1rem;background:#101827;color:#e8eef8}'
            'section{padding:1rem;margin:1rem 0;background:#19263a;border-radius:10px}table{width:100%;text-align:left}td,th{padding:.35rem;border-bottom:1px solid #34445e}h1{color:#80e2c1}</style>'
            f'<h1>Scout Insights — last {int(summary["period_days"])} days</h1><p>Local view. Retained choices, training, predictions and calibration have no individual timestamps.</p><p>Handled locally: {html.escape(savings)}</p><p>{html.escape(token_text)}</p><p>Token estimate: estimate; one live comparison showed ~4.7× above measured input; not an upper bound guarantee.</p>'
            + ''.join(blocks) + '</html>')
    Path(path).write_text(page, encoding='utf-8')
