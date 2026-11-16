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
let costOn = () => true, cacheOn = () => true;

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
      state.data = d.result;
      state.jobId = d.id;
      render(d.result);
      setTimeout(() => { $('#progress').style.display = 'none'; }, 1200);
      return;
    }
    if (d.status === 'error') {
      setStatus('err', '失败');
      $('#run').disabled = false;
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
  const lines = data.lines || {};
  const keys = Object.keys(lines);
  if (!keys.length) { setStatus('err', '无结果'); return; }
  if (!keys.includes(state.line)) state.line = keys.includes('rotation') ? 'rotation' : keys[0];

  $('#results').style.display = 'block';
  $('#empty').style.display = 'none';
  $('#title').textContent = '回测结果';
  $('#subtitle').textContent =
    `${data.pool.first} → ${data.pool.last} · ${data.pool.count} 只 · ${data.pool.days} 个交易日 · ` +
    `持仓 ${data.params.top} 只 · ${data.params.cost ? '含成本' : '不含成本'}`;

  renderChips(keys);
  renderOverview();
  renderCompare(keys);
  renderYearly();
  renderHoldings();
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
  $('#holdMeta').textContent = `${hs.length} 只 ｜ ${L.dates[L.dates.length - 1]}`;
  let html = '<thead><tr><th>代码</th><th>股数</th><th>市值</th><th>权重</th></tr></thead><tbody>';
  hs.forEach(h => {
    html += `<tr><td class="mono">${h.code}</td><td>${h.shares.toLocaleString()}</td>
      <td>${(h.value || 0).toLocaleString()}</td><td>${fmtPct(h.weight)}</td></tr>`;
  });
  $('#tblHold').innerHTML = html + '</tbody>';
  const t = L.trade_stats;
  $('#tradeMeta').textContent = `共 ${t.count} 笔 ｜ 买 ${t.buy} / 卖 ${t.sell} ｜ 成交额 ${t.amount}`;
}

function renderDiag() {
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
  if (tab === 'diag') { renderDiag(); }
}

/* ------------------------------------------------------------ 文档 */
const DOC = `
<h3>这个工具做了什么</h3>
<p>把「多因子选股 + 行业轮动」两套策略跑在真实 A 股数据上，输出可复现的净值、回撤、分年度收益和统计诊断。
所有计算在本机完成，不联网上传任何东西。数据抓取用 akshare（腾讯行情 + 百度估值），落盘缓存，重跑不重复请求。</p>

<h3>指标怎么看</h3>
<ul>
<li><b>年化收益 / 最大回撤</b>：先看这一对。年化 25% 配 -40% 回撤，和年化 12% 配 -20% 回撤，后者更容易拿住。</li>
<li><b>年化超额</b>：减去等权基准的部分。这是唯一和你「选股能力」有关的数字，年化收益本身会骗人。</li>
<li><b>夏普 / 卡玛</b>：单位风险的收益。A 股长期夏普超过 0.8 已经很好，超过 1.5 的都要怀疑数据。</li>
<li><b>IC / ICIR</b>：打分和未来收益的秩相关。<b>关键是和 1/√N 比</b>——100 只样本的标准误是 0.10，IC 0.02 等于噪声。</li>
<li><b>年换手</b>：超过 10 倍时，成本会吃掉大部分超额。点开「计入交易成本」对比一次就明白。</li>
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
<li>不含退市股：样本只有「今天还活着」的股票，基准被系统性高估（诊断页有估算值）。</li>
<li>样本小：换种子重跑，收益就会明显变化——这说明差异来自抽样运气。</li>
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
        <div style="font-size:12.5px">${(r.finished || '').replace('T', ' ')} · ${p.pool === 'grid' ? '电网三只' : (p.n || '') + ' 只'} ·
          ${p.mode === 'both' ? '多因子+轮动' : (p.mode === 'factor' ? '多因子' : '轮动')}</div>
        <div style="color:var(--t3);font-size:11.5px">年化 ${fmtPct(h.cagr)} · 回撤 ${fmtPct(h.max_dd)} ·
          超额 ${fmtPct(h.excess)} · ${r.status}</div></div>
      <button class="ghost" data-id="${r.id}">载入</button>`;
    list.appendChild(div);
  });
}

async function loadHistory(id) {
  const st = await api('/api/replay?id=' + id);
  if (!st.result) { alert('这条记录没有可用结果'); return; }
  state.jobId = id;
  state.data = st.result;
  render(st.result);
  const t = new URLSearchParams(location.search).get('tab');
  if (t) showTab(t);
  setStatus('done', '已载入历史');
}

/* ------------------------------------------------------------ 初始化 */
function init() {
  $('#end').value = new Date().toISOString().slice(0, 10);

  seg('#segPool', (v) => {
    poolMode = v;
    $('#wrapN').style.display = v === 'sample' ? '' : 'none';
    $('#codes').style.display = v === 'custom' ? '' : 'none';
    $('#poolHint').textContent = {
      sample: '从当前全部 A 股中按种子抽样，含存活偏差',
      grid: '固定标的：平高电气 600312 / 思源电气 002028 / 特变电工 600089',
      custom: '只回测你指定的股票，涨跌停按板块规则自动识别',
    }[v];
  });
  costOn = switchEl('#swCost');
  cacheOn = switchEl('#swCache');
  $('#run').addEventListener('click', startRun);
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
