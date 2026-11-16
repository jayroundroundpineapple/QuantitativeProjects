"""把回测结果写成一个自包含的 HTML 报告（图表走 CDN，无其他依赖）。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .engine import Result

_TPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>多因子回测报告</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
  :root{
    --bg:#17181a; --panel:#1f2023; --line:rgba(255,255,255,.10);
    --t1:#e8e8e4; --t2:#a9a9a3; --red:#e05555; --green:#2fae7a; --blue:#4f8ff0; --amber:#d99a2b;
  }
  *{box-sizing:border-box}
  body{margin:0;padding:32px 28px 56px;background:var(--bg);color:var(--t1);
       font-family:-apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
       font-size:14px;line-height:1.6}
  h1{font-size:20px;font-weight:500;margin:0 0 4px}
  .sub{color:var(--t2);font-size:13px;margin-bottom:28px}
  h2{font-size:15px;font-weight:500;margin:32px 0 12px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
  .card{background:var(--panel);border-radius:10px;padding:14px 16px}
  .card .k{color:var(--t2);font-size:12px}
  .card .v{font-size:22px;font-weight:500;margin-top:2px;font-variant-numeric:tabular-nums}
  .pos{color:var(--red)} .neg{color:var(--green)}
  .chart{background:var(--panel);border-radius:12px;padding:16px;margin-top:8px}
  .wrap{position:relative;width:100%;height:320px}
  table{width:100%;border-collapse:collapse;background:var(--panel);
        border-radius:12px;overflow:hidden}
  th,td{padding:10px 14px;text-align:right;font-variant-numeric:tabular-nums}
  th:first-child,td:first-child{text-align:left;color:var(--t2)}
  thead th{font-weight:500;color:var(--t2);font-size:12px;
           border-bottom:1px solid var(--line)}
  tbody tr+tr td{border-top:1px solid var(--line)}
  .note{color:var(--t2);font-size:12px;margin-top:10px}
</style></head><body>
<h1>多因子选股回测报告</h1>
<div class="sub">__PERIOD__ ｜ 月度调仓 ｜ 等权 ｜ 次日开盘成交 ｜ 含双边成本</div>

<div class="grid" id="metrics"></div>

<h2>净值曲线</h2>
<div class="chart"><div class="wrap"><canvas id="c1"></canvas></div></div>

<h2>回撤</h2>
<div class="chart"><div class="wrap" style="height:220px"><canvas id="c2"></canvas></div></div>

<h2>分年度收益</h2>
<div class="chart"><div class="wrap" style="height:260px"><canvas id="c3"></canvas></div></div>

<h2>分年度明细</h2>
<table><thead><tr><th>年度</th><th>策略</th><th>基准</th><th>超额</th></tr></thead>
<tbody>__YEARLY__</tbody></table>

<h2>期末持仓（前 15）</h2>
<table><thead><tr><th>代码</th><th>股数</th><th>市值</th><th>权重</th></tr></thead>
<tbody>__HOLD__</tbody></table>

<div class="note">__NOTE__</div>

<script>
const D = __DATA__;
const dark = true;
const grid = 'rgba(255,255,255,.08)', tick = '#a9a9a3';
Chart.defaults.color = tick;
Chart.defaults.font.family = '-apple-system,"PingFang SC",sans-serif';
Chart.defaults.font.size = 11;

const cards = [
  ['年化收益', pct(D.m.cagr), D.m.cagr],
  ['累计收益', pct(D.m.total_return), D.m.total_return],
  ['年化波动', pct(D.m.ann_vol), null],
  ['夏普比率', num(D.m.sharpe), D.m.sharpe],
  ['最大回撤', pct(D.m.max_dd), D.m.max_dd],
  ['卡玛比率', num(D.m.calmar), D.m.calmar],
  ['月度胜率', pct(D.m.monthly_win), null],
  ['年换手（倍）', num(D.m.turnover_annual), null],
  ['基准年化', pct(D.m.bench_cagr), D.m.bench_cagr],
  ['年化超额', pct(D.m.excess_cagr), D.m.excess_cagr],
  ['IC 均值', num(D.m.ic_mean), D.m.ic_mean],
  ['ICIR', num(D.m.icir), D.m.icir],
];
document.getElementById('metrics').innerHTML = cards.map(([k,v,s])=>{
  let cls=''; if(s!==null&&s!==undefined&&!isNaN(s)) cls = s>=0?'pos':'neg';
  return `<div class="card"><div class="k">${k}</div><div class="v ${cls}">${v}</div></div>`;
}).join('');

function pct(x){ return (x===undefined||x===null||isNaN(x))?'—':(x*100).toFixed(1)+'%'; }
function num(x){ return (x===undefined||x===null||isNaN(x))?'—':Number(x).toFixed(2); }

const tsOpts = {
  responsive:true, maintainAspectRatio:false, animation:false,
  interaction:{mode:'index',intersect:false},
  plugins:{legend:{display:true,labels:{boxWidth:10,boxHeight:10,usePointStyle:true}}},
  scales:{
    x:{grid:{color:grid,drawTicks:false},border:{display:false},
       ticks:{maxTicksLimit:8,autoSkip:true,maxRotation:0}},
    y:{grid:{color:grid,drawTicks:false},border:{display:false}}
  },
  elements:{point:{radius:0},line:{borderWidth:1.5}}
};

new Chart(document.getElementById('c1'),{
  type:'line',
  data:{labels:D.dates,datasets:[
    {label:'策略',data:D.equity,borderColor:'#e05555',backgroundColor:'#e05555'},
    {label:'等权基准',data:D.bench,borderColor:'#4f8ff0',backgroundColor:'#4f8ff0'}]},
  options:tsOpts});

new Chart(document.getElementById('c2'),{
  type:'line',
  data:{labels:D.dates,datasets:[{label:'回撤',data:D.dd,borderColor:'#d99a2b',
    backgroundColor:'rgba(217,154,43,.18)',fill:true}]},
  options:{...tsOpts,plugins:{legend:{display:false}},
    scales:{...tsOpts.scales,y:{...tsOpts.scales.y,ticks:{callback:v=>(v*100).toFixed(0)+'%'}}}}});

new Chart(document.getElementById('c3'),{
  type:'bar',
  data:{labels:D.years,datasets:[
    {label:'策略',data:D.year_strategy,backgroundColor:'#e05555'},
    {label:'基准',data:D.year_bench,backgroundColor:'#4f8ff0'}]},
  options:{responsive:true,maintainAspectRatio:false,animation:false,
    plugins:{legend:{labels:{boxWidth:10,boxHeight:10,usePointStyle:true}}},
    scales:{x:{grid:{display:false},border:{display:false}},
            y:{grid:{color:grid,drawTicks:false},border:{display:false},
               ticks:{callback:v=>(v*100).toFixed(0)+'%'}}}}});
</script>
</body></html>
"""


def _cls(x: float) -> str:
    return "pos" if x >= 0 else "neg"


def build(res: Result, path: str | Path, note: str = "") -> Path:
    eq = res.equity.dropna()
    base = eq.iloc[0]
    bench = res.benchmark.reindex(eq.index).ffill().dropna()
    bench_n = bench / (bench.iloc[0] or 1)
    dates = [d.strftime("%Y-%m-%d") for d in eq.index]
    dd = (eq / eq.cummax() - 1).round(4).tolist()

    def fmt(v):
        return "—" if pd.isna(v) else f"{v * 100:.1f}%"

    rows = []
    for y, r in res.yearly.iterrows():
        s, b, e = r["策略"], r["基准"], r["超额"]
        rows.append(
            f"<tr><td>{y}</td><td class='{_cls(s)}'>{fmt(s)}</td>"
            f"<td>{fmt(b)}</td><td class='{_cls(0 if pd.isna(e) else e)}'>{fmt(e)}</td></tr>"
        )
    yearly_rows = "".join(rows)

    last = res.holdings[res.holdings["date"] == res.holdings["date"].max()]
    tot = last["value"].sum() or 1
    hold_rows = "".join(
        f"<tr><td>{r.code}</td><td>{int(r.shares):,}</td>"
        f"<td>{r.value:,.0f}</td><td>{r.value/tot*100:.1f}%</td></tr>"
        for r in last.sort_values("value", ascending=False).head(15).itertuples()
    )

    payload = {
        "m": res.metrics,
        "dates": dates,
        "equity": (eq / base).round(4).tolist(),
        "bench": bench_n.reindex(eq.index).ffill().round(4).tolist(),
        "dd": dd,
        "years": [str(y) for y in res.yearly.index],
        "year_strategy": [None if v != v else round(v, 4)
                          for v in res.yearly["策略"]],
        "year_bench": [None if v != v else round(v, 4)
                       for v in res.yearly["基准"]],
    }
    html = (_TPL
            .replace("__PERIOD__", f"{eq.index[0]:%Y-%m-%d} → {eq.index[-1]:%Y-%m-%d}"
                     f"（{res.metrics['years']} 年）")
            .replace("__YEARLY__", yearly_rows)
            .replace("__HOLD__", hold_rows)
            .replace("__NOTE__", note)
            .replace("__DATA__", json.dumps(payload, ensure_ascii=False)))
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    return p
