/* 量化工作台前端：无框架、无 CDN，图表自己画。 */

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const COLORS = {
  factor: '#e05555', rotation: '#d99a2b', sector_only: '#8f7bf0',
  bench: '#4f8ff0', score: '#d99a2b', market: '#4f8ff0',
};
const NAMES = { factor: '多因子选股', rotation: '电网轮动', sector_only: '纯持电网三只' };

const state = {
  jobId: null, timer: null, data: null, params: null,
  line: 'factor', tab: 'overview', t0: 0,
  stress: null, stressId: null,
};

/* ------------------------------------------------------------ 图表内核 */
function fmtPct(v, d = 1) { return v == null ? '—' : (v * 100).toFixed(d) + '%'; }
function fmtNum(v, d = 2) { return v == null ? '—' : Number(v).toFixed(d); }

function mount(el, height) {
  el.innerHTML = '';
  el.style.height = height + 'px';
  const cv = document.createElement('canvas');
  el.appendChild(cv);
  const ctx = cv.getContext('2d');
  const render = () => {
    const w = el.clientWidth || 640, dpr = window.devicePixelRatio || 1;
    cv.width = Math.max(1, Math.floor(w * dpr));
    cv.height = Math.floor(height * dpr);
    cv.style.width = '100%';
    cv.style.height = height + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, height);
    return { w, h: height };
  };
  el._render = render;
  const ro = new ResizeObserver(() => {
    const g = render();
    if (el._draw) el._draw(ctx, g.w, g.h);
  });
  ro.observe(el);
  return { cv, ctx, render, el };
}

function niceRange(min, max) {
  if (!isFinite(min) || !isFinite(max)) return [0, 1];
  if (min === max) { min -= 0.01; max += 0.01; }
  const pad = (max - min) * 0.08;
  return [min - pad, max + pad];
}

function axisTicks(lo, hi, n = 4) {
  const span = hi - lo;
  if (!(span > 0)) return [lo, hi];
  const raw = span / n;
  const p10 = Math.pow(10, Math.floor(Math.log10(raw)));
  const m = raw / p10;
  const step = (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p10;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-6; v += step) {
    out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  }
  return out.length >= 2 ? out : [lo, hi];
}

function drawGrid(ctx, g, pad, yTicks, lo, hi, yFmt, xLabels, xs) {
  ctx.save();
  ctx.font = '10.5px -apple-system,"PingFang SC",sans-serif';
  ctx.fillStyle = '#6f6f6a';
  ctx.strokeStyle = 'rgba(255,255,255,.055)';
  ctx.lineWidth = 1;
  yTicks.forEach((v) => {
    const y = Math.round(pad.t + (1 - (v - lo) / (hi - lo)) * (g.h - pad.t - pad.b)) + .5;
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(g.w - pad.r, y); ctx.stroke();
    ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    ctx.fillText(yFmt(v), pad.l - 8, y);
  });
  ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  const n = xLabels.length;
  const every = Math.max(1, Math.ceil(n / 6));
  for (let i = 0; i < n; i += every) {
    ctx.fillText(xLabels[i], xs(i), g.h - pad.b + 7);
  }
  ctx.restore();
}

/** 多序列折线图。series: [{name,data,color,axis:'left'|'right',fill,width}] */
function lineChart(el, opt) {
  const { labels, series, height = 260, yFmt = fmtPct, zero = false,
          clampMax = null, xFmt = (v) => String(v).slice(0, 7) } = opt;
  const pad = { l: 52, r: series.some(s => s.axis === 'right') ? 50 : 14, t: 14, b: 26 };
  const m = mount(el, height);
  m.el._draw = (ctx, w, h) => {
    const left = series.filter(s => s.axis !== 'right');
    const right = series.filter(s => s.axis === 'right');
    const lv = left.flatMap(s => s.data).filter(v => v != null);
    let [lo, hi] = niceRange(Math.min(...lv), Math.max(...lv));
    if (zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    if (clampMax != null) hi = Math.min(hi, clampMax);
    const rv = right.flatMap(s => s.data).filter(v => v != null);
    const [rlo, rhi] = rv.length ? niceRange(Math.min(...rv), Math.max(...rv)) : [0, 1];

    const X = (i) => pad.l + (i / Math.max(labels.length - 1, 1)) * (w - pad.l - pad.r);
    const Y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (h - pad.t - pad.b);
    const YR = (v) => pad.t + (1 - (v - rlo) / (rhi - rlo)) * (h - pad.t - pad.b);

    drawGrid(ctx, { w, h }, pad, axisTicks(lo, hi, 4), lo, hi, yFmt, labels.map(xFmt), X);

    if (zero && lo < 0 && hi > 0) {
      ctx.save(); ctx.strokeStyle = 'rgba(255,255,255,.18)';
      ctx.beginPath(); ctx.moveTo(pad.l, Y(0)); ctx.lineTo(w - pad.r, Y(0)); ctx.stroke(); ctx.restore();
    }

    series.forEach((s) => {
      const yf = s.axis === 'right' ? YR : Y;
      ctx.save();
      ctx.strokeStyle = s.color; ctx.lineWidth = s.width || 1.6;
      ctx.lineJoin = 'round';
      if (s.fill) {
        const base = (lo < 0 && hi > 0) ? Y(0) : (hi <= 0 ? pad.t : h - pad.b);
        const idx = s.data.map((v, i) => v == null ? -1 : i).filter(i => i >= 0);
        if (idx.length > 1) {
          const grad = ctx.createLinearGradient(0, pad.t, 0, h - pad.b);
          grad.addColorStop(0, s.color + '38'); grad.addColorStop(1, s.color + '05');
          ctx.beginPath();
          ctx.moveTo(X(idx[0]), base);
          idx.forEach(i => ctx.lineTo(X(i), yf(s.data[i])));
          ctx.lineTo(X(idx[idx.length - 1]), base);
          ctx.closePath(); ctx.fillStyle = grad; ctx.fill();
        }
      }
      ctx.beginPath();
      let started = false;
      s.data.forEach((v, i) => {
        if (v == null) return;
        if (!started) { ctx.moveTo(X(i), yf(v)); started = true; } else ctx.lineTo(X(i), yf(v));
      });
      ctx.stroke();
      ctx.restore();
    });

    // 右侧轴
    if (right.length) {
      ctx.save(); ctx.font = '10.5px -apple-system,sans-serif';
      ctx.fillStyle = '#6f6f6a'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
      axisTicks(rlo, rhi, 4).forEach((v) => ctx.fillText(fmtNum(v, 0), w - pad.r + 8, YR(v)));
      ctx.restore();
    }

    el._geo = { X, Y, YR, pad, w, h, labels, series, lo, hi };
  };
  const g = m.render();
  m.el._draw(m.ctx, g.w, g.h);
  attachHover(el, yFmt);
  return m;
}

function attachHover(el, yFmt) {
  const tip = $('#tip');
  const cv = el.querySelector('canvas');
  cv.onmousemove = (e) => {
    const geo = el._geo; if (!geo) return;
    const r = cv.getBoundingClientRect();
    const x = e.clientX - r.left;
    const n = geo.labels.length;
    let i = Math.round((x - geo.pad.l) / ((geo.w - geo.pad.l - geo.pad.r) / Math.max(n - 1, 1)));
    i = Math.max(0, Math.min(n - 1, i));
    el._render(); el._draw(cv.getContext('2d'), geo.w, geo.h);
    const ctx = cv.getContext('2d');
    ctx.save();
    ctx.strokeStyle = 'rgba(255,255,255,.22)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(geo.X(i), geo.pad.t); ctx.lineTo(geo.X(i), geo.h - geo.pad.b); ctx.stroke();
    geo.series.forEach((s) => {
      const v = s.data[i]; if (v == null) return;
      const y = (s.axis === 'right' ? geo.YR : geo.Y)(v);
      ctx.beginPath(); ctx.arc(geo.X(i), y, 3, 0, Math.PI * 2);
      ctx.fillStyle = s.color; ctx.fill();
      ctx.strokeStyle = '#1c1d20'; ctx.lineWidth = 1.5; ctx.stroke();
    });
    ctx.restore();

    let html = `<b>${geo.labels[i]}</b>`;
    geo.series.forEach((s) => {
      const v = s.data[i];
      html += `<div style="display:flex;gap:10px;justify-content:space-between;margin-top:2px">
        <span style="color:${s.color}">${s.name}</span>
        <span>${v == null ? '—' : (s.fmt ? s.fmt(v) : yFmt(v))}</span></div>`;
    });
    tip.innerHTML = html;
    tip.style.opacity = 1;
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    let left = e.clientX + 14, top = e.clientY - th - 10;
    if (left + tw > window.innerWidth - 10) left = e.clientX - tw - 14;
    if (top < 8) top = e.clientY + 16;
    tip.style.left = left + 'px'; tip.style.top = top + 'px';
  };
  cv.onmouseleave = () => { tip.style.opacity = 0; const g = el._geo; if (g) { el._render(); el._draw(cv.getContext('2d'), g.w, g.h); } };
}

/** 分组柱状图。groups: [{name,data,color}] */
function barChart(el, { labels, groups, height = 240 }) {
  const pad = { l: 52, r: 14, t: 16, b: 26 };
  const m = mount(el, height);
  const draw = (ctx, w, h) => {
    const vals = groups.flatMap(g => g.data).filter(v => v != null);
    let [lo, hi] = niceRange(Math.min(...vals, 0), Math.max(...vals, 0));
    lo = Math.min(lo, 0); hi = Math.max(hi, 0);
    const X = (i) => pad.l + (i / Math.max(labels.length, 1)) * (w - pad.l - pad.r);
    const bw = (w - pad.l - pad.r) / Math.max(labels.length, 1);
    const Y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (h - pad.t - pad.b);

    drawGrid(ctx, { w, h }, pad, axisTicks(lo, hi, 4), lo, hi, fmtPct, labels,
      (i) => X(i) + bw / 2);

    const gw = Math.min(18, (bw - 12) / groups.length);
    groups.forEach((g, gi) => {
      g.data.forEach((v, i) => {
        if (v == null) return;
        const x = X(i) + bw / 2 - (groups.length * gw) / 2 + gi * gw;
        const y0 = Y(0), y1 = Y(v);
        ctx.fillStyle = g.color;
        ctx.fillRect(x + 1, Math.min(y0, y1), gw - 2, Math.max(Math.abs(y1 - y0), 1));
      });
    });
    el._geo = null;
  };
  const g0 = m.render();
  draw(m.ctx, g0.w, g0.h);
  new ResizeObserver(() => { const g = m.render(); draw(m.ctx, g.w, g.h); }).observe(el);
}

/* ------------------------------------------------------------ 交互 */
function seg(id, cb) {
  const wrap = $(id);
  wrap.addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    [...wrap.children].forEach(c => c.classList.toggle('on', c === b));
    cb(b.dataset.v);
  });
}
function switchEl(id) {
  const el = $(id);
  el.addEventListener('click', () => el.classList.toggle('on'));
  return () => el.classList.contains('on');
}

let poolMode = 'sample';
let costOn = () => true, cacheOn = () => true, fundOn = () => true;

function readParams() {
  return {
    pool: poolMode,
    n: +$('#n').value || 100,
    codes: $('#codes').value,
    start: $('#start').value,
    end: $('#end').value || new Date().toISOString().slice(0, 10),
    top: +$('#top').value || 20,
    mode: $('#segMode .on').dataset.v,
    seed: +$('#seed').value || 42,
    capital: +$('#capital').value || 1000000,
    threshold: +$('#threshold').value || 0,
    cost: costOn(),
    use_cache: cacheOn(),
    fundamentals: fundOn(),
  };
}

function setStatus(kind, text) {
  const p = $('#status');
  p.className = 'pill' + (kind ? ' ' + kind : '');
  p.innerHTML = `<span class="dot"></span>${text}`;
}

async function api(path, body) {
  const opt = body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {};
  const r = await fetch(path, opt);
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
  return d;
}

async function startRun() {
  const p = readParams();
  if (p.start >= p.end) { alert('开始日期必须早于结束日期'); return; }
  $('#run').disabled = true;
  $('#runStress').disabled = true;
  $('#empty').style.display = 'none';
  $('#results').style.display = 'none';
  $('#progress').style.display = 'block';
  $('#log').textContent = '';
  $('#pbar').style.width = '0%';
  setStatus('run', '运行中');
  state.t0 = Date.now();
  try {
    const { id } = await api('/api/run', p);
    state.jobId = id;
    state.params = p;
    poll();
  } catch (e) {
    setStatus('err', '启动失败');
    $('#step').textContent = e.message;
    $('#run').disabled = false;
    $('#runStress').disabled = false;
  }
}

async function startStress() {
  const p = readParams();
  if (p.start >= p.end) { alert('开始日期必须早于结束日期'); return; }
  const poolSize = Math.min(600, Math.max(300, Math.round(p.n * 4)));
  if (!confirm(`压力测试会用 ${poolSize} 只股票作母体，反复抽 ${p.n} 只、每次持仓 ${p.top} 只、共 12 次。\n` +
               `首次要抓 ${poolSize} 只的行情与财报，约 3–5 分钟（之后走缓存）。开始吗？`)) return;
  $('#run').disabled = true;
  $('#runStress').disabled = true;
  $('#empty').style.display = 'none';
  $('#results').style.display = 'block';
  $('#progress').style.display = 'block';
  $('#log').textContent = '';
  $('#pbar').style.width = '0%';
  setStatus('run', '压力测试中');
  state.t0 = Date.now();
  try {
    const { id } = await api('/api/stress', { ...p, draws: 12, pool_size: poolSize });
    state.jobId = id;
    state.params = p;
    showTab('stress');
    poll();
  } catch (e) {
    setStatus('err', '启动失败');
    $('#step').textContent = e.message;
    $('#run').disabled = false;
    $('#runStress').disabled = false;
  }
}

async function poll() {
  clearTimeout(state.timer);
  try {
    const d = await api('/api/job?id=' + state.jobId);
    $('#step').textContent = d.step || '';
    $('#pbar').style.width = (d.progress || 0) + '%';
    $('#timer').textContent = ((Date.now() - state.t0) / 1000).toFixed(0) + ' 秒';
    renderLog(d.logs || []);

    if (d.status === 'done') {
      setStatus('done', '完成 · ' + d.elapsed + 's');
      $('#run').disabled = false;
      $('#runStress').disabled = false;
      setTimeout(() => { $('#progress').style.display = 'none'; }, 1200);
      if (d.result && d.result.kind === 'stress') {
        state.stress = d.result;
        state.stressId = d.id;
        $('#results').style.display = 'block';
        $('#title').textContent = '抽样压力测试';
        renderStress();
        showTab('stress');
        return;
      }
      state.data = d.result;
      state.jobId = d.id;
      render(d.result);
      return;
    }
    if (d.status === 'error') {
      setStatus('err', '失败');
      $('#run').disabled = false;
      $('#runStress').disabled = false;
      $('#step').textContent = '失败：' + d.error;
      return;
    }
    state.timer = setTimeout(poll, 1100);
  } catch (e) {
    state.timer = setTimeout(poll, 2000);
  }
}

function renderLog(logs) {
  const el = $('#log');
  el.innerHTML = logs.slice(-14).map(l => `<b>${l.replace(/^\[(\d\d:\d\d:\d\d)\]/, '[$1]')}</b>`).join('\n');
  el.scrollTop = el.scrollHeight;
}

/* ------------------------------------------------------------ 渲染 */
function render(data) {
  if (!data) return;
  if (data.kind === 'stress') {          // 压力测试结果走另一套渲染
    state.stress = data;
    $('#results').style.display = 'block';
    $('#title').textContent = '抽样压力测试';
    renderStress();
    showTab('stress');
    return;
  }
  const lines = data.lines || {};
  const keys = Object.keys(lines);
  if (!keys.length) { setStatus('err', '无结果'); return; }
  if (!keys.includes(state.line)) state.line = keys.includes('rotation') ? 'rotation' : keys[0];

  $('#results').style.display = 'block';
  $('#empty').style.display = 'none';
  $('#title').textContent = '回测结果';
  const poolLabel = (data.pool && data.pool.label) || `${data.pool.count} 只`;
  $('#subtitle').textContent =
    `${data.pool.first} → ${data.pool.last} · ${poolLabel} · ${data.pool.days} 个交易日 · ` +
    `持仓 ${data.params.top} 只 · ${data.params.cost ? '含成本' : '不含成本'}`;

  renderChips(keys);
  renderOverview();
  renderCompare(keys);
  renderYearly();
  renderHoldings();
  renderRanking();
  renderDiag();
  renderDoc();
  showTab(state.tab);
  bindDownloads();
}

function renderChips(keys) {
  const box = $('#lineChips');
  box.innerHTML = keys.map(k =>
    `<button class="chip ${k === state.line ? 'on' : ''}" data-k="${k}">
      <i style="color:${COLORS[k]}"></i>${NAMES[k] || k}</button>`).join('');
  box.onclick = (e) => {
    const b = e.target.closest('.chip'); if (!b) return;
    state.line = b.dataset.k;
    renderChips(keys); renderOverview(); renderYearly(); renderHoldings();
  };
}

function renderOverview() {
  const L = state.data.lines[state.line];
  const m = L.metrics;
  const cards = [
    ['年化收益', fmtPct(m.cagr), m.cagr],
    ['累计收益', fmtPct(m.total_return), m.total_return],
    ['年化超额', fmtPct(m.excess_cagr), m.excess_cagr],
    ['最大回撤', fmtPct(m.max_dd), null],
    ['夏普比率', fmtNum(m.sharpe), m.sharpe],
    ['卡玛比率', fmtNum(m.calmar), m.calmar],
    ['年化波动', fmtPct(m.ann_vol), null],
    ['月度胜率', fmtPct(m.monthly_win, 0), null],
    ['年换手', fmtNum(m.turnover_annual) + 'x', null],
    ['IC 均值', fmtNum(m.ic_mean, 3), m.ic_mean],
    ['ICIR', fmtNum(m.icir, 2), m.icir],
    ['平均持仓', (m.avg_holdings || 0) + ' 只', null],
  ];
  $('#kpis').innerHTML = cards.map(([k, v, s]) => {
    let cls = '';
    if (s != null && !isNaN(s)) cls = s >= 0 ? 'pos' : 'neg';
    return `<div class="kpi"><div class="k">${k}</div><div class="v ${cls}">${v}</div></div>`;
  }).join('');

  $('#ovMeta').textContent = `${NAMES[state.line]} ｜ ${m.years} 年 ｜ 基准 ${fmtPct(m.bench_cagr)}`;

  lineChart($('#chartNav'), {
    labels: L.dates, height: 300, yFmt: (v) => Number(v).toFixed(2),
    series: [
      { name: '净值', data: L.equity, color: COLORS[state.line], width: 1.8, fmt: (v) => v.toFixed(3) },
      { name: '等权基准', data: L.bench, color: COLORS.bench, width: 1.2, fmt: (v) => v.toFixed(3) },
    ],
  });
  $('#legendNav').innerHTML =
    `<span><i style="background:${COLORS[state.line]}"></i>策略净值</span>
     <span><i style="background:${COLORS.bench}"></i>等权基准</span>
     <span style="color:var(--t3)">纵向是净值倍数，起点 = 1.000</span>`;

  lineChart($('#chartDD'), {
    labels: L.dates, height: 200, zero: true, clampMax: 0,
    series: [{ name: '回撤', data: L.dd, color: '#d99a2b', fill: true, width: 1.2 }],
  });
}

function renderCompare(keys) {
  const cmp = state.data.compare;
  const names = keys.map(k => ({ name: NAMES[k], data: cmp.series[k], color: COLORS[k] }));
  lineChart($('#chartCmp'), { labels: cmp.dates, height: 300, series: names,
    yFmt: (v) => Number(v).toFixed(2) });
  $('#legendCmp').innerHTML = names.map(s =>
    `<span><i style="background:${s.color}"></i>${s.name}</span>`).join('');

  const rows = [
    ['年化收益', 'cagr', fmtPct], ['年化超额', 'excess_cagr', fmtPct],
    ['年化波动', 'ann_vol', fmtPct], ['最大回撤', 'max_dd', fmtPct],
    ['夏普', 'sharpe', fmtNum], ['卡玛', 'calmar', fmtNum],
    ['月度胜率', 'monthly_win', (v) => fmtPct(v, 0)],
    ['年换手', 'turnover_annual', (v) => fmtNum(v) + 'x'],
    ['IC 均值', 'ic_mean', (v) => fmtNum(v, 3)],
  ];
  let html = '<thead><tr><th>指标</th>' + keys.map(k => `<th>${NAMES[k]}</th>`).join('') + '</tr></thead><tbody>';
  rows.forEach(([label, key, f]) => {
    html += `<tr><td>${label}</td>` + keys.map(k => {
      const v = state.data.lines[k].metrics[key];
      const cls = key === 'max_dd' || key === 'ann_vol' ? '' : (v >= 0 ? 'pos' : 'neg');
      return `<td class="${cls}">${f(v)}</td>`;
    }).join('') + '</tr>';
  });
  html += '</tbody>';
  $('#tblCmp').innerHTML = html;
}

function renderYearly() {
  const L = state.data.lines[state.line];
  const yrs = L.yearly.map(r => String(r.year));
  $('#yearMeta').textContent = NAMES[state.line];
  barChart($('#chartYear'), {
    labels: yrs, height: 250,
    groups: [
      { name: '策略', data: L.yearly.map(r => r.strategy), color: COLORS[state.line] },
      { name: '基准', data: L.yearly.map(r => r.bench), color: COLORS.bench },
    ],
  });
  let html = '<thead><tr><th>年度</th><th>策略</th><th>基准</th><th>超额</th></tr></thead><tbody>';
  L.yearly.forEach(r => {
    const c = (v) => v == null ? '' : (v >= 0 ? 'pos' : 'neg');
    html += `<tr><td>${r.year}</td><td class="${c(r.strategy)}">${fmtPct(r.strategy)}</td>
      <td class="${c(r.bench)}">${fmtPct(r.bench)}</td>
      <td class="${c(r.excess)}">${fmtPct(r.excess)}</td></tr>`;
  });
  $('#tblYear').innerHTML = html + '</tbody>';
}

function renderHoldings() {
  const L = state.data.lines[state.line];
  const hs = L.holdings || [];
  const poolN = (state.data.pool || {}).count || 0;
  const top = (state.data.params || {}).top || 0;
  const warn = poolN && top && poolN < top ? ` ｜ 池内只有 ${poolN} 只，少于持仓 ${top} 只` : '';
  $('#holdMeta').textContent = `${hs.length} 只 ｜ ${L.dates[L.dates.length - 1]}${warn}`;
  let html = '<thead><tr><th>代码</th><th>股数</th><th>市值</th><th>权重</th></tr></thead><tbody>';
  hs.forEach(h => {
    html += `<tr><td class="mono">${h.code}</td><td>${h.shares.toLocaleString()}</td>
      <td>${(h.value || 0).toLocaleString()}</td><td>${fmtPct(h.weight)}</td></tr>`;
  });
  $('#tblHold').innerHTML = html + '</tbody>';
  const t = L.trade_stats;
  $('#tradeMeta').textContent = `共 ${t.count} 笔 ｜ 买 ${t.buy} / 卖 ${t.sell} ｜ 成交额 ${t.amount}`;
}

function renderRanking() {
  const r = state.data.ranking;
  const tb = $('#tblRank');
  if (!tb) return;
  if (!r) {
    $('#rankMeta').textContent = '无数据';
    tb.innerHTML = '<thead><tr><th>这一池股票没有可交易标的</th></tr></thead>';
    return;
  }
  const onlyTop = $('#rankOnlyTop') && $('#rankOnlyTop').checked;
  const rows = onlyTop ? r.rows.filter(x => x.selected) : r.rows;
  const liveNames = { value: '价值', quality: '质量', growth: '成长', momentum: '动量' };
  const live = (r.live || []).map(k => liveNames[k]).join(' + ') || '无';
  $('#rankMeta').textContent =
    `${r.date} 打分 ｜ 池内 ${r.total} 只 · 可交易 ${r.eligible} 只 ｜ ` +
    `取前 ${r.top} ｜ 生效因子：${live}`;

  const pct = (v) => v == null ? '—' : Math.round(v * 100) + '%';
  let html = '<thead><tr><th>名次</th><th>代码</th><th>名称</th><th>行业</th><th>总分</th>' +
    '<th>价值</th><th>质量</th><th>成长</th><th>动量</th><th>状态</th></tr></thead><tbody>';
  rows.forEach(x => {
    let tag = '<span style="color:var(--t3)">未入选</span>';
    if (x.held && x.selected) tag = '<span style="color:var(--green)">持有</span>';
    else if (x.held) tag = '<span style="color:var(--amber)">已跌出前 ' + r.top + '</span>';
    else if (x.selected) tag = '<span style="color:var(--blue)">新进</span>';
    const bg = x.selected ? ' style="background:rgba(79,143,240,.07)"' : '';
    html += `<tr${bg}><td>${x.rank}</td><td class="mono">${x.code}</td>
      <td style="color:var(--t1)">${x.name || '—'}</td>
      <td style="color:var(--t3);font-size:11.5px">${x.industry || '—'}</td>
      <td style="color:var(--t1)">${x.score == null ? '—' : x.score.toFixed(2)}</td>
      <td>${pct(x.value)}</td><td>${pct(x.quality)}</td>
      <td>${pct(x.growth)}</td><td>${pct(x.momentum)}</td><td>${tag}</td></tr>`;
  });
  tb.innerHTML = html + '</tbody>';
}

function renderStress() {
  const s = state.stress;
  if (!s) {
    $('#stressEmpty').style.display = 'block';
    $('#stressBody').style.display = 'none';
    $('#stressTableCard').style.display = 'none';
    return;
  }
  const st = s.stats, rows = s.rows || [];
  $('#stressEmpty').style.display = 'none';
  $('#stressBody').style.display = 'block';
  $('#stressTableCard').style.display = 'block';
  $('#stressMeta').textContent =
    `${s.pool.first} → ${s.pool.last} ｜ 母体 ${s.pool.size} 只 · 每次抽 ${s.pool.n} 只 · ` +
    `持仓 ${s.pool.top} 只 · 共 ${st.draws} 次`;

  const cards = [
    ['年化中位数', fmtPct(st.cagr_med), st.cagr_med],
    ['年化 10 分位', fmtPct(st.cagr_p10), st.cagr_p10],
    ['年化 90 分位', fmtPct(st.cagr_p90), st.cagr_p90],
    ['最差一次', fmtPct(st.cagr_min), st.cagr_min],
    ['最好一次', fmtPct(st.cagr_max), st.cagr_max],
    ['超额中位', fmtPct(st.excess_med), st.excess_med],
    ['跑赢基准占比', fmtPct(st.excess_pos_pct, 0), null],
    ['最差回撤', fmtPct(st.dd_worst), null],
    ['夏普中位', fmtNum(st.sharpe_med), st.sharpe_med],
    ['IC 中位', fmtNum(st.ic_med, 3), st.ic_med],
    ['年化标准差', fmtPct(st.cagr_std), null],
    ['抽样次数', st.draws + ' 次', null],
  ];
  $('#stressKpis').innerHTML = cards.map(([k, v, c]) => {
    let cls = '';
    if (c != null && !isNaN(c)) cls = c >= 0 ? 'pos' : 'neg';
    return `<div class="kpi"><div class="k">${k}</div><div class="v ${cls}">${v}</div></div>`;
  }).join('');

  lineChart($('#chartStress'), {
    labels: rows.map(r => '#' + r.draw), height: 280, zero: true,
    yFmt: (v) => fmtPct(v, 0),
    series: [
      { name: '年化', data: rows.map(r => r.cagr), color: '#4f8ff0', width: 1.8,
        fmt: (v) => fmtPct(v) },
      { name: '中位数', data: rows.map(() => st.cagr_med), color: '#d99a2b', width: 1.2,
        fmt: (v) => fmtPct(v) },
    ],
  });

  $('#stressTblMeta').textContent = '同一套参数，只换了股票';
  let h = '<thead><tr><th>抽样</th><th>年化</th><th>超额</th><th>最大回撤</th>' +
    '<th>夏普</th><th>年换手</th><th>IC</th></tr></thead><tbody>';
  rows.forEach(r => {
    const c = (v) => v == null ? '' : (v >= 0 ? 'pos' : 'neg');
    h += `<tr><td>第 ${r.draw} 次</td>
      <td class="${c(r.cagr)}">${fmtPct(r.cagr)}</td>
      <td class="${c(r.excess_cagr)}">${fmtPct(r.excess_cagr)}</td>
      <td>${fmtPct(r.max_dd)}</td><td>${fmtNum(r.sharpe)}</td>
      <td>${fmtNum(r.turnover_annual)}x</td><td>${fmtNum(r.ic_mean, 3)}</td></tr>`;
  });
  $('#tblStress').innerHTML = h + '</tbody>';
  $('#dlStress').href = `/api/export?id=${state.stressId}&kind=stress`;
}

function renderSources() {
  const s = (state.data && state.data.sources) || [];
  const tb = $('#tblSrc');
  if (!tb) return;
  if (!s.length) {
    tb.innerHTML = '<thead><tr><th>暂无数据来源信息</th></tr></thead>';
    $('#srcMeta').textContent = '';
    return;
  }
  $('#srcMeta').textContent = '全是免费公开接口，本地抓取、本地计算';
  let html = '<thead><tr><th>类型</th><th>接口</th><th>频率</th><th>最新到</th><th>说明</th></tr></thead><tbody>';
  s.forEach(x => {
    html += `<tr><td style="color:var(--t1)">${x.k}</td>
      <td class="mono" style="font-size:11.5px">${x.src}</td>
      <td style="color:var(--t3);font-size:11.5px">${x.freq}</td>
      <td>${x.asof || '—'}</td>
      <td style="text-align:left;color:var(--t2);font-size:11.5px;line-height:1.6">${x.note}</td></tr>`;
  });
  tb.innerHTML = html + '</tbody>';
}

function renderDiag() {
  renderSources();
  const s = state.data.strength;
  const card = $('#sigCard');
  if (!s) { card.style.display = 'none'; }
  else {
    card.style.display = 'block';
    $('#sigNow').textContent = `${s.last.date} ｜ 强度分 ${s.last.score > 0 ? '+' : ''}${s.last.score} → ${s.last.signal}`;
    lineChart($('#chartSig'), {
      labels: s.dates, height: 230, zero: true, yFmt: (v) => fmtNum(v, 1),
      series: [
        { name: '强度分', data: s.score, color: COLORS.score, width: 1.5, fmt: (v) => fmtNum(v, 2) },
      ],
    });
  }

  const c = state.data.caveats || [];
  const icon = { warn: '!', ok: '✓', info: 'i' };
  $('#caveats').innerHTML = c.map(x =>
    `<div class="note ${x.level}"><div class="ic">${icon[x.level] || '·'}</div>
      <div><b>${x.title}</b>${x.text}</div></div>`).join('');
}

function bindDownloads() {
  const id = state.jobId;
  const ln = state.line;
  $('#dlTrades').href = `/api/export?id=${id}&kind=trades`;
  $('#dlHold').href = `/api/export?id=${id}&kind=holdings`;
  $('#dlNav').href = `/api/export?id=${id}&kind=nav`;
  const dr = $('#dlRank');
  if (dr) dr.href = `/api/export?id=${id}&kind=ranking`;
  const rp = (state.data.reports || {})[ln] || (state.data.reports || {})[Object.keys(state.data.reports)[0]];
  const openBtn = $('#openReport');
  if (rp) { openBtn.style.display = ''; openBtn.href = `/report?id=${id}&line=${ln}`; }
  else openBtn.style.display = 'none';
}

function showTab(tab) {
  state.tab = tab;
  $$('.tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === tab));
  $$('.pane').forEach(p => p.classList.toggle('on', p.id === 'pane-' + tab));
  if (tab === 'overview') { renderOverview(); }
  if (tab === 'compare') { renderCompare(Object.keys(state.data.lines)); }
  if (tab === 'yearly') { renderYearly(); }
  if (tab === 'holdings') { renderHoldings(); }
  if (tab === 'rank') { renderRanking(); }
  if (tab === 'stress') { renderStress(); }
  if (tab === 'diag') { renderDiag(); }
}

/* ------------------------------------------------------------ 文档 */
const DOC = `
<h3>这个工具做了什么</h3>
<p>把「多因子选股 + 行业轮动」两套策略跑在真实 A 股数据上，输出可复现的净值、回撤、分年度收益和统计诊断。
所有计算在本机完成，不联网上传任何东西。数据抓取用 akshare，落盘缓存，重跑不重复请求。</p>

<h3>数据从哪来（都在「诊断」页第一张表里）</h3>
<ul>
<li><b>行情</b>：腾讯财经日线，前复权。最新到最近一个交易日收盘——15:00 之后再跑就含当天。</li>
<li><b>估值</b>：百度股市通每日 PE(TTM)，和行情同日对齐。</li>
<li><b>财报</b>：东方财富「业绩报表」，一次一个报告期，含 ROE、净利同比、所属行业。</li>
<li><b>退市名单</b>：上交所/深交所暂停终止上市口径，只用来估存活偏差，不进样本。</li>
</ul>
<p><b>关于「是不是最新」</b>：默认勾选「复用本地缓存」，所以看到的可能是上次抓的数据。
要点「用最新数据」就把这个开关关掉，会重新请求。财报还有个天然延迟——
本工具按<b>法定披露截止日</b>（Q1 4/30、H1 8/31、Q3 10/31、年报次年 4/30）才让财报进入打分，
宁可晚用不可早用，所以不会偷看未来，但也会漏掉提前披露的信息量。</p>

<h3>四个因子分别在说什么</h3>
<ul>
<li><b>价值 1/PE</b>：同样的盈利，谁卖得便宜。注意 PE 为负（亏损）的直接剔除，不是排最后。</li>
<li><b>质量 ROE</b>：同样的净资产，谁赚得多。用的是年内累计 ROE × 4/季度序号 的年化近似。</li>
<li><b>成长 净利同比</b>：利润在变多还是变少。极端值靠排名百分位压住，不会因为一个 3000% 就乱套。</li>
<li><b>动量 60 日涨幅</b>：最近三个月谁在走强。它和「价值」经常打架，这正是组合的意义。</li>
</ul>
<p>四个因子各自转成 0~100% 的排名百分位，再加权（0.30 / 0.25 / 0.25 / 0.20）。用排名而不是原始值，
是因为 A 股的财务数据极端值太多，均值方差会被一两个异常值带跑。某个因子没数据时，
它的权重会让给其余因子，而不是按中性 50% 硬填——后者会让你以为它算过了。</p>

<h3>指标怎么看</h3>
<ul>
<li><b>年化收益 / 最大回撤</b>：先看这一对。年化 25% 配 -40% 回撤，和年化 12% 配 -20% 回撤，后者更容易拿住。</li>
<li><b>年化超额</b>：减去等权基准的部分。这是唯一和你「选股能力」有关的数字，年化收益本身会骗人。</li>
<li><b>夏普 / 卡玛</b>：单位风险的收益。A 股长期夏普超过 0.8 已经很好，超过 1.5 的都要怀疑数据。</li>
<li><b>IC / ICIR</b>：打分和未来收益的秩相关。<b>关键是和 1/√N 比</b>——100 只样本的标准误是 0.10，IC 0.02 等于噪声。</li>
<li><b>年换手</b>：超过 10 倍时，成本会吃掉大部分超额。关掉「计入交易成本」对比一次就明白。</li>
</ul>

<h3>压力测试在回答什么问题</h3>
<p>单次回测最容易被误读的地方是：<b>把「这一百只股票涨得好」当成「这套方法有效」</b>。
压力测试不调参数、不改因子，只从一个大样本池里反复抽不同的股票，看同一套规则的年化散得多开。</p>
<ul>
<li>散得窄 → 规则对「买到谁」不敏感，那个年化更可信。</li>
<li>散得宽 → 你看到的收益里大部分是抽样运气。中间 80% 的区间才是它真实的能力范围。</li>
<li>「跑赢基准占比」接近 50% → 这个策略其实没在选股，只是在承担波动。</li>
</ul>

<h3>每周怎么用（约 5 分钟）</h3>
<ul>
<li><b>每周五</b>：跑一次默认参数，看 IC 有没有掉、超额有没有连续为负。</li>
<li><b>每月最后一个交易日</b>：看「诊断」页的强度分，&gt; 0 持电网设备，&lt; 0 回全市场。</li>
<li><b>每季度</b>：如果 IC 连续 3 个月为负，停用这套信号——不要为它辩护。</li>
</ul>

<h3>三个必须提前写下来的作废条件</h3>
<ul>
<li>滚动 12 个月超额为负 → 降半仓观察。</li>
<li>IC 连续 3 期为负 → 信号停用，回宽基。</li>
<li>最大回撤超过历史最大值 1.5 倍 → 检查是策略失效还是风格极端，不要加仓摊平。</li>
</ul>

<h3>它不能做什么</h3>
<ul>
<li>不含退市股：免费源只给「今天还活着」的股票，基准被系统性高估（诊断页有估算值）。
    要真正解决得用 tushare 的退市全样本，见 README「接 tushare」。</li>
<li>财报用披露截止日近似，不是真实公告日；ROE 是年化近似值。</li>
<li>样本小：换一批股票重跑收益就会明显变化——压力测试页就是在量这件事。</li>
<li>不预测：它只告诉你历史上什么条件成立过，不告诉你明天。</li>
</ul>
<p style="color:var(--t3);margin-top:16px">回测仅供研究，不构成投资建议。市场有风险，投资需谨慎。</p>`;

function renderDoc() { $('#docBody').innerHTML = DOC; }

/* ------------------------------------------------------------ 历史记录 */
async function openHistory() {
  const rows = await api('/api/history');
  const ov = document.createElement('div');
  ov.style.cssText = `position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:50;
    display:flex;align-items:center;justify-content:center;padding:40px`;
  ov.innerHTML = `<div style="background:var(--panel);border:1px solid var(--line);border-radius:14px;
      width:min(760px,100%);max-height:76vh;overflow:auto;padding:20px 22px">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
      <div style="font-size:14px">历史回测</div>
      <button class="ghost" data-x="1">关闭</button></div>
    <div id="hlist">${rows.length ? '' : '<div style="color:var(--t3)">还没有记录</div>'}</div>
  </div>`;
  document.body.appendChild(ov);
  ov.addEventListener('click', (e) => {
    if (e.target === ov || e.target.dataset.x) ov.remove();
    const b = e.target.closest('[data-id]');
    if (b) { loadHistory(b.dataset.id); ov.remove(); }
  });
  const list = ov.querySelector('#hlist');
  rows.forEach(r => {
    const h = r.headline || {};
    const p = r.params || {};
    const div = document.createElement('div');
    div.style.cssText = 'display:flex;justify-content:space-between;gap:12px;align-items:center;' +
      'padding:10px 0;border-top:1px solid var(--line)';
    div.innerHTML = `<div>
        <div style="font-size:12.5px">${(r.finished || '').replace('T', ' ')} ·
          ${r.kind === 'stress' ? '抽样压力测试'
            : (p.pool === 'grid' ? '电网三只' : (p.n || '') + ' 只') + ' · ' +
              (p.mode === 'both' ? '多因子+轮动' : (p.mode === 'factor' ? '多因子' : '轮动'))}</div>
        <div style="color:var(--t3);font-size:11.5px">年化 ${fmtPct(h.cagr)} · 回撤 ${fmtPct(h.max_dd)} ·
          超额 ${fmtPct(h.excess)} · ${r.status}</div></div>
      <button class="ghost" data-id="${r.id}">载入</button>`;
    list.appendChild(div);
  });
}

async function loadHistory(id) {
  const st = await api('/api/replay?id=' + id);
  if (!st.result) { alert('这条记录没有可用结果'); return; }
  if (st.result.kind === 'stress') {
    state.stress = st.result;
    state.stressId = id;
    $('#empty').style.display = 'none';
    $('#results').style.display = 'block';
    renderStress();
    showTab('stress');
    setStatus('done', '已载入历史');
    return;
  }
  state.jobId = id;
  state.data = st.result;
  render(st.result);
  const t = new URLSearchParams(location.search).get('tab');
  if (t) showTab(t);
  setStatus('done', '已载入历史');
}

/* ------------------------------------------------------------ 初始化 */
function updatePoolHint() {
  const el = $('#poolHint');
  let txt = '', warn = false;
  if (poolMode === 'sample') {
    const n = parseInt($('#n').value, 10) || 0;
    txt = `从当前全部 A 股中按种子抽样 ${n} 只，含存活偏差`;
  } else if (poolMode === 'grid') {
    txt = '固定标的：平高电气 600312 / 思源电气 002028 / 特变电工 600089';
  } else {
    const codes = ($('#codes').value || '').split(/[,，\s]+/).filter(Boolean);
    const top = parseInt($('#top').value, 10) || 20;
    if (!codes.length) { txt = '请填写 6 位股票代码，逗号分隔'; warn = true; }
    else if (codes.length < 3) { txt = `已填 ${codes.length} 只 —— 少于 3 只无法组成组合`; warn = true; }
    else if (codes.length < top) { txt = `已填 ${codes.length} 只，少于持仓 ${top} 只，实际只会持有 ${codes.length} 只`; warn = true; }
    else { txt = `已填 ${codes.length} 只 · 本模式下「只股票」数量框不生效`; }
  }
  el.textContent = txt;
  el.style.color = warn ? 'var(--amber)' : '';
}

function init() {
  $('#end').value = new Date().toISOString().slice(0, 10);
  updatePoolHint();

  seg('#segPool', (v) => {
    poolMode = v;
    $('#wrapN').style.display = v === 'sample' ? '' : 'none';
    $('#codes').style.display = v === 'custom' ? '' : 'none';
    updatePoolHint();
  });
  $('#codes').addEventListener('input', updatePoolHint);
  $('#n').addEventListener('input', updatePoolHint);
  $('#rankOnlyTop').addEventListener('change', renderRanking);
  costOn = switchEl('#swCost');
  cacheOn = switchEl('#swCache');
  fundOn = switchEl('#swFund');
  $('#run').addEventListener('click', startRun);
  $('#runStress').addEventListener('click', startStress);
  $('#btnHistory').addEventListener('click', openHistory);
  $('#btnReveal').addEventListener('click', () => {
    api('/api/reveal', { id: state.jobId || '' }).catch(() => {});
  });
  $$('.tabs').forEach(t => t.addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (b) showTab(b.dataset.tab);
  }));

  api('/api/meta').then(m => {
    $('#ver').textContent = 'v' + m.version;
    if (m.jobs === 0) {
      $('#subtitle').textContent = `Python ${m.python} ｜ 缓存 ${m.cache.prices} 份行情、${m.cache.pe} 份估值 ｜ 首次运行约 40–90 秒`;
    }
  }).catch(() => {});
  fetch('/api/history').then(r => r.json()).then(rows => {
    if (state.data) return;                       // 已经载入了某次结果，别覆盖标题
    if (rows.length && rows[0].status === 'done') {
      $('#subtitle').textContent = '上次运行：' + (rows[0].finished || '').replace('T', ' ') +
        '（点「历史记录」可载入）';
    }
  }).catch(() => {});

  // 支持 ?job=<id> 直接打开某次结果，便于收藏或对比
  const q = new URLSearchParams(location.search);
  if (q.get('job')) loadHistory(q.get('job'));
}

document.addEventListener('DOMContentLoaded', init);
window.addEventListener('resize', () => { $('#tip').style.opacity = 0; });
