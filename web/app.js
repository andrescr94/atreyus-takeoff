// Takeoff web app, no build step. Three hash routes:
//   #/                     upload a plan set, watch every sheet being read
//   #/p/<id>/s/<sheet>     one sheet: what the AI read (sheet, legend, rooms, devices, by type), review optional
//   #/p/<id>/results       counts by type, sheet and room; exports
// Nothing waits on a person: the AI's devices count as soon as a sheet is read; every device says whether the
// AI put it there or a person confirmed, changed or added it. Device coordinates are PDF points; the plan image
// covers the plan frame, so the SVG overlay uses the frame as its viewBox and markers stay put at any zoom.

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const plural = (n, one, many = one + 's') => `${n} ${n === 1 ? one : many}`;

async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET' };
  if (opts.json !== undefined) {
    init.method = opts.method || 'POST';
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(opts.json);
  } else if (opts.body) {
    init.body = opts.body;
  }
  const r = await fetch('/api' + path, init);
  const isJson = (r.headers.get('content-type') || '').includes('json');
  const body = isJson ? await r.json() : await r.text();
  if (!r.ok) throw new Error((isJson && body.detail) || `${r.status} ${r.statusText}`);
  return body;
}

let toastTimer;
function toast(msg, err = false) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'show' + (err ? ' err' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = err ? 'err' : ''; }, err ? 5000 : 2200);
}

// ---- vocabulary ----

const ICON = {
  logo: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20 12 4l8 16"/><path d="M8 13h8"/></svg>',
  up: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 16V4"/><path d="m7 9 5-5 5 5"/><path d="M5 20h14"/></svg>',
  down: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4v12"/><path d="m7 11 5 5 5-5"/><path d="M5 20h14"/></svg>',
  left: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m15 6-6 6 6 6"/></svg>',
  right: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg>',
  minus: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M5 12h14"/></svg>',
  plus: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M5 12h14"/><path d="M12 5v14"/></svg>',
  add: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M12 8v8"/><path d="M8 12h8"/></svg>',
  check: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12 5 5 9-10"/></svg>',
  alert: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="flex:none;margin-top:1px"><circle cx="12" cy="12" r="9"/><path d="M12 7v6"/><path d="M12 16.5v.5"/></svg>',
};

// Where a device's answer comes from: the AI, or a person (confirmed, changed, added, removed).
const PROV = {
  review: ['AI', 'p-ai'], suggested: ['AI suggestion', 'p-ai'], agreed: ['AI', 'p-ai'],
  verified: ['Confirmed', 'p-ok'], edited: ['Changed by you', 'p-you'], added: ['Added by you', 'p-you'],
  removed: ['Removed', 'p-gone'],
};
const OPEN = ['review', 'suggested'];
const CHECKED = ['verified', 'edited', 'added', 'removed'];
const CONFIRMED = ['verified', 'edited', 'added'];
const COUNTED = ['review', 'agreed', 'verified', 'edited', 'added'];
const LOW = 0.9; // below this a device needs a look: takeoff/review.py LOW, from measured accuracy per evidence bucket
// The brief asks for fans, lights, switches and receptacles; the rest is reported apart.
const FAMILIES = [
  ['fan', 'Fans', true], ['light', 'Lights', true], ['switch', 'Switches', true], ['receptacle', 'Receptacles', true],
  ['detector', 'Detectors', false], ['junction', 'Junction boxes', false], ['other', 'Other', false],
];
const BRIEF = new Set(FAMILIES.filter((f) => f[2]).map((f) => f[0]));
const ZOOMS = [0.5, 0.75, 1, 1.5, 2, 3, 4];
const CLOSEUP = 4; // px per PDF point in the close-up
const TILE = 3; // px per PDF point in the by-type tiles
const ROOM_HOW = { inside: 'inside the walls', nudged: 'on a wall, moved in', 'nearest-label': 'nearest label (weak)', 'read by the model': 'read by the model' };

let CONFIG = { method: '', types: [] };
const LABEL = {};
const label = (slug) => LABEL[slug] || String(slug).replace(/_/g, ' ');
// Flags are written with type slugs ("The printed label says switch_3way_dimmer"); show the type names instead.
function prettyFlag(text) {
  return String(text ?? '').replace(/\b[a-z][a-z0-9]*(?:_[a-z0-9]+)*\b/g, (w) => {
    const name = LABEL[w];
    if (!name) return w;
    const first = name.split(/[ ,]/)[0];
    return first === first.toUpperCase() ? name : name[0].toLowerCase() + name.slice(1);
  });
}
const pct = (c) => (c == null ? '–' : `${Math.round(c * 100)}%`);
const needsLook = (d) => OPEN.includes(d.status);

function color(d) {
  if (needsLook(d)) return '#f59e0b';
  if (d.status === 'agreed') return 'rgba(107,63,247,.45)';
  if (d.status === 'verified') return '#6b3ff7';
  if (d.status === 'removed') return '#c9c5d4';
  return '#0d9488';
}

function provChip(d) {
  const [text, cls] = PROV[d.status];
  return `<span class="chip ${cls}">${text}</span>`;
}

function fmtSecs(s) {
  if (s == null) return '';
  if (s < 90) return `${Math.round(s)} s`;
  return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
}

function fmtTokens(n) {
  if (n == null) return '';
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e4) return `${Math.round(n / 1e3)}k`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(n);
}

function runFacts(s) {
  const out = [`<span>Method <b>${esc(s.method)}</b></span>`];
  if (s.model_calls) {
    out.push(`<span><b>${plural(s.model_calls, 'model call')}</b></span>`);
    if (s.model_seconds != null) out.push(`<span>Model time <b>${fmtSecs(s.model_seconds)}</b></span>`);
    if (s.input_tokens != null) out.push(`<span>Tokens <b>${fmtTokens(s.input_tokens)} in · ${fmtTokens(s.output_tokens)} out</b></span>`);
  } else if (s.status === 'ready') {
    out.push('<span><b>No model calls</b></span>');
  }
  if (s.elapsed_seconds != null) out.push(`<span>Read in <b>${fmtSecs(s.elapsed_seconds)}</b></span>`);
  return out.join('');
}

function topbar(project, inner = '') {
  const proj = project
    ? `<div class="proj"><b>${esc(project.name)}</b><span>${esc(project.filename)} · ${plural(project.sheets.length, 'sheet')}</span></div>`
    : '';
  return `<header class="top"><a class="brand" href="#/"><span class="logo">${ICON.logo}</span><b>Takeoff</b></a>${proj}${inner}</header>`;
}

function errorCard(s, retry = true) {
  return `<div class="alert" role="alert">${ICON.alert}<div style="flex:1;min-width:0"><b>${esc(s.title)} could not be read</b>
    <div class="msg">${esc(s.error)}</div></div>${retry ? `<button class="btn" data-retry="${s.id}">Try again</button>` : ''}</div>`;
}

async function retrySheet(id) {
  try { await api(`/sheets/${id}/retry`, { method: 'POST' }); toast('Reading the sheet again'); }
  catch (e) { toast(e.message, true); }
}

// ---- router ----

let view = null;
async function route() {
  const [path, query] = location.hash.slice(1).split('?');
  const params = new URLSearchParams(query || '');
  view?.destroy?.();
  view = null;
  hideTip();
  window.scrollTo(0, 0);
  let m;
  if ((m = path.match(/^\/p\/(\d+)\/s\/(\d+)$/))) view = Review(+m[1], +m[2], params);
  else if ((m = path.match(/^\/p\/(\d+)\/results$/))) view = Results(+m[1], params);
  else if ((m = path.match(/^\/p\/(\d+)$/))) view = openProject(+m[1]);
  else view = Home();
}

function openProject(pid) {
  api(`/projects/${pid}`).then((p) => {
    const ready = p.sheets.filter((s) => s.status === 'ready');
    const next = ready.find((s) => s.to_review > 0) || ready[0] || p.sheets[0];
    location.replace(next ? `#/p/${pid}/s/${next.id}` : '#/');
  }).catch((e) => { toast(e.message, true); location.replace('#/'); });
  return null;
}

// ---- tooltip ----

function showTip(html, x, y) {
  const t = $('#tip');
  t.innerHTML = html;
  t.hidden = false;
  const w = t.offsetWidth;
  const h = t.offsetHeight;
  t.style.left = `${Math.min(x, window.innerWidth - w - 12)}px`;
  t.style.top = `${y + h + 12 > window.innerHeight ? y - h - 24 : y}px`;
}

function hideTip() {
  const t = $('#tip');
  if (t) t.hidden = true;
}

function deviceTip(d) {
  const [prov] = PROV[d.status];
  const low = d.confidence != null && d.confidence < LOW;
  return `<b>${esc(label(d.type))}</b><span>${esc(d.room || 'No room')}</span>
    <span><i class="${low ? 'lowc' : ''}">${pct(d.confidence)} confidence</i> · ${prov}${['review', 'agreed', 'suggested'].includes(d.status) ? ` (${esc(d.source)})` : ''}</span>
    ${d.flag ? `<span class="tipflag">${esc(prettyFlag(d.flag))}</span>` : ''}`;
}

// ---- home ----

function Home() {
  const root = $('#app');
  let timer = null;
  let alive = true;
  const twoReads = CONFIG.method === 'vision-consensus';
  const lead = twoReads
    ? 'Upload a plan set. Every sheet is read twice, independently, and the counts are ready as soon as it is read. Look at the devices where the two reads disagree if you want, then export.'
    : `Upload a plan set. Every sheet is read with the ${esc(CONFIG.method)} method and checked against the drawing; the counts are ready as soon as it is read. Look at what was flagged if you want, then export.`;
  root.innerHTML = `${topbar(null)}
  <div class="home">
    <div class="hero"><h1>Electrical takeoff, checked by you</h1><p>${lead}</p></div>
    <label class="drop" id="drop">
      <span class="ico">${ICON.up}</span>
      <span class="t" id="dropT">Drop a plan set here</span>
      <span class="s" id="dropS">PDF, vector or scanned. About a minute per sheet.</span>
      <span class="fake">Choose a PDF</span>
      <input type="file" id="file" accept="application/pdf,.pdf">
    </label>
    <div id="uperr"></div>
    <div class="steps">
      <div class="step"><span class="n">1</span><span><b>Upload</b><span>Sheets and legends are found for you.</span></span></div>
      <div class="step"><span class="n">2</span><span><b>${twoReads ? 'Two reads per sheet' : 'One read per sheet, then checks'}</b><span>Each device gets a type, a room, a place on the plan and a confidence.</span></span></div>
      <div class="step"><span class="n amber">3</span><span><b>Check what you want</b><span>Low-confidence devices are marked. Counts and exports never wait for you.</span></span></div>
    </div>
    <section class="sets" aria-label="Plan sets"><h2>Plan sets</h2><div class="tablebox" id="sets"><div class="empty">Loading…</div></div></section>
  </div>`;

  const drop = $('#drop');
  const input = $('#file');
  input.addEventListener('change', () => input.files[0] && upload(input.files[0]));
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (e) => {
    e.preventDefault();
    drop.classList.remove('over');
    if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]);
  });
  root.onclick = async (e) => {
    const r = e.target.closest('[data-retry]');
    if (r) { await retrySheet(+r.dataset.retry); refresh(); }
  };

  async function upload(file) {
    $('#uperr').innerHTML = '';
    drop.classList.add('busy');
    $('#dropT').textContent = `Uploading ${file.name}…`;
    const fd = new FormData();
    fd.append('file', file);
    try {
      const p = await api('/projects', { method: 'POST', body: fd });
      toast(`${p.name}: ${plural(p.sheets.length, 'page')} queued`);
    } catch (e) {
      $('#uperr').innerHTML = `<div class="alert" role="alert">${ICON.alert}<div><b>${esc(file.name)} was not uploaded</b>${esc(e.message)}</div></div>`;
    } finally {
      drop.classList.remove('busy');
      $('#dropT').textContent = 'Drop a plan set here';
      input.value = '';
      refresh();
    }
  }

  function statusCell(p) {
    const { done, total, reading } = p.progress;
    if (done < total) {
      const width = Math.round(((done + reading * 0.5) / total) * 100);
      const stage = p.sheets.find((s) => s.status === 'reading')?.stage || 'Waiting for a worker';
      return `<span style="display:flex;flex-direction:column;gap:6px;min-width:200px">
        <span style="font-size:12px;color:var(--ink-3)">Reading sheet ${Math.min(done + 1, total)} of ${total}</span>
        <span class="bar live"><span style="width:${Math.max(width, 6)}%"></span></span>
        <span style="font-size:11px;color:var(--muted)">${esc(stage)}</span></span>`;
    }
    const pills = [];
    if (p.errors) pills.push(`<span class="pill red">${plural(p.errors, 'sheet')} failed</span>`);
    if (p.live) pills.push('<span class="pill purple">Counts ready</span>');
    if (p.to_review) pills.push(`<span class="pill amber">${p.to_review} need a look</span>`);
    return `<span style="display:flex;gap:6px;flex-wrap:wrap">${pills.join('')}</span>`;
  }

  function sheetChip(p, s) {
    let text = '';
    if (s.status === 'ready') text = `${s.live} devices${s.to_review ? ` · <span style="color:var(--amber-ink)">${s.to_review} need a look</span>` : ''}`;
    else if (s.status === 'reading') text = esc(s.stage || 'Reading');
    else if (s.status === 'queued') text = 'Waiting';
    else return '';
    const inner = `<span class="dot ${s.status === 'ready' ? '' : s.status}"></span><b>${esc(s.title)}</b>${text}`;
    return s.status === 'ready' ? `<a class="sheetchip" href="#/p/${p.id}/s/${s.id}">${inner}</a>` : `<span class="sheetchip">${inner}</span>`;
  }

  function render(projects) {
    if (!projects.length) {
      $('#sets').innerHTML = '<div class="empty">No plan sets yet. Upload one above.</div>';
      return;
    }
    const rows = projects.map((p) => {
      const ready = p.sheets.some((s) => s.status === 'ready');
      const action = ready
        ? `<span style="display:inline-flex;gap:8px"><a class="btn" href="#/p/${p.id}/results">Counts</a><a class="btn dark" href="#/p/${p.id}">Open</a></span>`
        : '<span style="color:var(--muted);font-size:12px">We\'ll show it here when ready</span>';
      const errs = p.sheets.filter((s) => s.status === 'error');
      return `<tr class="jobrow">
          <td><span class="name"><b>${esc(p.name)}</b><span>${esc(p.filename)} · ${esc(p.method)}</span></span></td>
          <td>${p.sheets.length}</td>
          <td style="text-align:right" class="mono">${p.progress.done ? p.live : '…'}</td>
          <td>${statusCell(p)}</td>
          <td style="text-align:right">${action}</td></tr>
        <tr><td class="sub" colspan="5"><div class="sheetchips">${p.sheets.map((s) => sheetChip(p, s)).join('')}</div>
          ${errs.length ? `<div class="errcards">${errs.map((s) => errorCard(s)).join('')}</div>` : ''}</td></tr>`;
    }).join('');
    $('#sets').innerHTML = `<table class="jobs" style="min-width:760px"><thead><tr>
      <th>Plan set</th><th>Sheets</th><th style="text-align:right">Devices</th><th>Status</th><th><span class="sr">Action</span></th>
      </tr></thead><tbody>${rows}</tbody></table>`;
  }

  async function refresh() {
    clearTimeout(timer);
    try {
      const projects = await api('/projects');
      if (!alive) return;
      render(projects);
      if (projects.some((p) => p.progress.done < p.progress.total)) timer = setTimeout(refresh, 1500);
    } catch (e) {
      toast(e.message, true);
    }
  }
  refresh();
  return { destroy() { alive = false; clearTimeout(timer); root.onclick = null; } };
}

// ---- one sheet ----

const TABS = [['sheet', 'Sheet'], ['legend', 'Legend'], ['rooms', 'Rooms'], ['devices', 'Devices'], ['types', 'By type']];

function Review(pid, sid, params) {
  const root = $('#app');
  const S = {
    project: null, sheet: null, devices: [], counts: null, prevStrip: {}, bom: null, showBom: false,
    tab: params.get('tab') || 'devices', filter: null, zoom: 1, fit: 1, adding: false, sel: null, galleryType: null,
    hl: params.get('type') || params.get('room') ? { type: params.get('type'), room: params.get('room') } : null,
  };
  let alive = true;
  let poll = null;
  const byId = (id) => S.devices.find((d) => d.id === id);
  const planUrl = () => `/api/sheets/${sid}/plan.png`;
  const frame = () => S.sheet.plan_frame;
  const fw = () => frame().x1 - frame().x0;
  const fh = () => frame().y1 - frame().y0;
  const scale = () => S.fit * S.zoom;
  const live = () => S.devices.filter((d) => COUNTED.includes(d.status));

  async function load() {
    const [project, sheet, counts] = await Promise.all([
      api(`/projects/${pid}`), api(`/sheets/${sid}`), api(`/projects/${pid}/counts`),
    ]);
    if (!alive) return;
    S.bom = await api(`/projects/${pid}/bom?sheet=${sid}`).catch(() => null);
    S.project = project;
    S.sheet = sheet;
    S.devices = sheet.devices;
    S.counts = counts;
    if (sheet.status !== 'ready') return renderNotReady();
    const asked = +params.get('d');
    S.filter = S.hl || !lookIds().length ? 'all' : 'look';
    if (asked && byId(asked)) {
      S.sel = asked;
      if (!listIds().includes(asked)) S.filter = 'all';
    } else {
      S.sel = listIds()[0] ?? S.devices[0]?.id ?? null;
    }
    skeleton();
  }

  // ---- lists ----

  const openCount = () => S.devices.filter(needsLook).length;
  const aiCount = (type) => S.devices.filter((d) => d.status === 'agreed' && (!type || d.type === type)).length;
  const matchesHl = (d) => !S.hl || ((!S.hl.type || d.type === S.hl.type) && (!S.hl.room || d.room === S.hl.room));
  const rank = { review: 0, suggested: 1 };

  function lookIds() {
    return S.devices.filter((d) => d.initial_status in rank)
      .sort((a, b) => needsLook(b) - needsLook(a) || rank[a.initial_status] - rank[b.initial_status] || a.confidence - b.confidence || a.id - b.id)
      .map((d) => d.id);
  }

  function allIds() {
    return S.devices.filter(matchesHl)
      .sort((a, b) => needsLook(b) - needsLook(a) || String(a.room).localeCompare(String(b.room))
        || label(a.type).localeCompare(label(b.type)) || a.id - b.id)
      .map((d) => d.id);
  }

  function galleryIds() {
    return S.devices.filter((d) => d.type === S.galleryType).sort((a, b) => a.y - b.y || a.x - b.x).map((d) => d.id);
  }

  function listIds() {
    if (S.tab === 'types') return galleryIds();
    return S.filter === 'look' ? lookIds() : allIds();
  }

  function hlFn(spec) {
    const q = new URLSearchParams(spec);
    return (d) => ['type', 'room', 'family'].every((k) => !q.get(k) || d[k] === q.get(k));
  }

  // ---- layout ----

  function renderNotReady() {
    const s = S.sheet;
    const body = s.status === 'error'
      ? `<div class="statecard err" role="alert"><h2 style="margin:0">${esc(s.title)} could not be read</h2>
          <div class="msg">${esc(s.error)}</div>
          <div style="display:flex;gap:8px"><button class="btn" data-retry="${s.id}">Try again</button><a class="btn" href="#/">Back to plan sets</a></div></div>`
      : `<div class="statecard"><div class="spinner"></div><h2 style="margin:0;font-size:18px">Reading ${esc(s.title)}</h2>
          <p class="lead" style="margin:0;color:var(--ink-3)">${esc(s.stage || 'Waiting for a worker')}</p></div>`;
    root.innerHTML = `<div class="app">${header()}<div class="centerview" style="flex:1">${body}</div></div>`;
    root.onclick = async (e) => {
      const r = e.target.closest('[data-retry]');
      if (r) { await retrySheet(+r.dataset.retry); load(); }
    };
    clearTimeout(poll);
    if (s.status !== 'error') poll = setTimeout(() => alive && load().catch((e) => toast(e.message, true)), 2000);
  }

  function header() {
    const tabs = S.project.sheets.map((s) => {
      let badge;
      if (s.status === 'error') badge = '<span class="badge red" title="Could not be read">!</span>';
      else if (s.status !== 'ready') badge = '<span class="badge grey">…</span>';
      else {
        const n = s.id === sid && S.sheet.status === 'ready' ? openCount() : s.to_review;
        badge = n ? `<span class="badge amber" title="${n} need a look">${n}</span>` : `<span class="badge done">${ICON.check}</span>`;
      }
      return `<a class="tab ${s.id === sid ? 'on' : ''}" href="#/p/${pid}/s/${s.id}">${esc(s.title)}${badge}</a>`;
    }).join('');
    let meter = '';
    if (S.sheet.status === 'ready') {
      const total = live().length;
      const ok = S.devices.filter((d) => CONFIRMED.includes(d.status)).length;
      meter = `<div class="meter"><span><b>${ok}</b> of ${total} confirmed by you</span>
        <span class="bar"><span style="width:${total ? Math.round((ok / total) * 100) : 0}%"></span></span></div>`;
    }
    return topbar(S.project, `<nav class="tabs" aria-label="Sheets">${tabs}</nav><div class="spacer"></div>${meter}
      <a class="btn" href="#/p/${pid}/results">Counts</a>
      <a class="btn dark" href="#/p/${pid}/results">Export ${ICON.down}</a>`);
  }

  function skeleton() {
    root.innerHTML = `<div class="app">
      <div id="hdr"></div>
      <div class="stagebar" id="stagebar"></div>
      <div class="ws">
        <aside class="pane-l" aria-label="Devices" id="left"></aside>
        <main class="pane-c">
          <div id="planview" style="flex:1 1 0;min-height:0;display:flex;flex-direction:column">
            <div class="toolbar" id="toolbar"></div>
            <div class="planscroll" id="scroller"><div class="planpad"><div class="layer" id="layer">
              <img id="planimg" alt="Electrical plan of sheet ${esc(S.sheet.title)}" src="${planUrl()}">
              <svg id="marks" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none"></svg>
            </div></div></div>
            <div class="addbar" id="addbar" hidden>Click the plan where the missed device is. It is added as a receptacle; set its type on the right. <kbd style="background:rgba(255,255,255,.18);color:#fff">Esc</kbd> cancels.</div>
          </div>
          <div class="centerview" id="centerview" hidden></div>
          <div class="countstrip" id="strip" aria-live="polite"></div>
        </main>
        <aside class="pane-r" aria-label="Details" id="right"></aside>
      </div></div>`;
    const sc = $('#scroller');
    S.fit = Math.min((sc.clientWidth - 64) / fw(), (sc.clientHeight - 64) / fh());
    if (!(S.fit > 0)) S.fit = 1;
    sizeLayer();
    root.onclick = onClick;
    root.onchange = onChange;
    root.onmouseover = onHover;
    root.onmouseleave = () => { setHot([]); hideTip(); };
    renderAll();
    requestAnimationFrame(() => scrollToSel(true));
  }

  function renderAll() {
    $('#hdr').innerHTML = header();
    renderStagebar();
    renderLeft();
    renderCenter();
    renderRight();
    renderStrip();
  }

  // ---- "What it read": one tab per stage, any order, none blocking ----

  function roomPlaced() {
    const l = live();
    const good = l.filter((d) => ['inside', 'read by the model'].includes(d.evidence.room_method) || d.status === 'added').length;
    return l.length ? good / l.length : 1;
  }

  function tabStatus(key) {
    const s = S.sheet;
    const ok = s.confirmed?.[key];
    if (key === 'sheet') return [ok ? 'looks right' : `found by ${s.frames_source === 'model' ? 'the model' : 'rules'}`, ok ? 'ok' : s.frames_source === 'model' ? 'mid' : 'hi'];
    if (key === 'legend') {
      const n = new Set(live().map((d) => d.type)).size;
      return [ok ? 'looks right' : plural(n, 'type'), ok ? 'ok' : 'hi'];
    }
    if (key === 'rooms') {
      const p = roomPlaced();
      return [ok ? 'looks right' : `${Math.round(p * 100)}% placed`, ok ? 'ok' : p < 0.9 ? 'mid' : 'hi'];
    }
    if (key === 'devices') {
      const n = openCount();
      return [n ? `${n} need a look` : 'nothing flagged', n ? 'mid' : 'ok'];
    }
    const n = aiCount();
    return [n ? `${n} not confirmed` : 'all confirmed', n ? 'hi' : 'ok'];
  }

  function renderStagebar() {
    const html = TABS.map(([key, name], i) => {
      const [text, level] = tabStatus(key);
      return `<button class="stg ${S.tab === key ? 'on' : ''}" data-tab="${key}">
        <span class="n ${level === 'ok' ? 'okn' : ''}">${level === 'ok' ? ICON.check : i + 1}</span>
        <span class="stx">${name}<small class="lv-${level}">${esc(text)}</small></span></button>`;
    }).join('');
    $('#stagebar').innerHTML = `<span class="grouphead" style="padding:0 6px 0 0">What it read</span><div class="stages">${html}</div>
      <div class="spacer"></div><div class="runinfo">${runFacts(S.sheet)}</div>`;
  }

  // ---- left pane: devices, needs a look first; or the sheet's counts ----

  function renderLeft() {
    const left = $('#left');
    const keepScroll = $('#leftlist')?.scrollTop || 0;
    const ids = listIds();
    let head = '';
    if (S.tab === 'types') {
      head = `<div style="padding:14px 14px 10px"><div class="grouphead" style="padding:0">${esc(label(S.galleryType))} · ${ids.length}</div></div>`;
    } else {
      head = `<div style="padding:14px 14px 10px"><div class="seg three" role="group" aria-label="Show">
        <button data-filter="look" class="${S.filter === 'look' ? 'on' : ''}">Needs a look · ${openCount()}</button>
        <button data-filter="all" class="${S.filter === 'all' ? 'on' : ''}">All · ${S.devices.filter((d) => d.status !== 'removed').length}</button>
        <button data-filter="counts" class="${S.filter === 'counts' ? 'on' : ''}">Counts</button></div></div>`;
      if (S.hl && S.filter === 'all') {
        const what = [S.hl.type && label(S.hl.type), S.hl.room && `in ${S.hl.room}`].filter(Boolean).join(' ');
        head += `<div class="note filter"><span>Showing ${ids.length} × ${esc(what)}</span><button class="btn" style="height:30px" data-clearhl>Show all</button></div>`;
      }
      if (S.filter === 'look' && !openCount()) {
        head += !aiCount()
          ? '<div class="note"><b>Every device on this sheet is confirmed</b><span>The counts and exports already include every change you made.</span></div>'
          : '<div class="note"><b>Nothing needs a look</b><span>The AI counts are already in the totals and exports. Confirm more if you want, by type or one by one.</span></div>';
      }
    }
    let body;
    if (S.filter === 'counts' && S.tab !== 'types') body = countsList();
    else {
      let lastRoom = null;
      body = ids.map((id) => {
        const d = byId(id);
        let group = '';
        if (S.filter === 'all' && S.tab !== 'types' && !needsLook(d) && d.room !== lastRoom) {
          lastRoom = d.room;
          group = `<div class="grouphead">${esc(d.room || 'No room')}</div>`;
        }
        const low = d.confidence != null && d.confidence < LOW;
        const sub = d.flag && (needsLook(d) || S.filter === 'look') ? prettyFlag(d.flag) : (d.room || '');
        return `${group}<button class="row ${id === S.sel ? 'on' : ''}" data-pick="${id}">
          <span class="dot" style="background:${color(d)}"></span>
          <span class="txt"><span class="lbl ${d.status === 'removed' ? 'gone' : ''}">${esc(label(d.type))}</span><span class="sub ${needsLook(d) ? 'issue' : ''}">${esc(sub)}</span></span>
          <span class="side"><span class="conf ${low ? 'lowc' : ''}">${pct(d.confidence)}</span>${provChip(d)}</span></button>`;
      }).join('') || '<div class="empty">Nothing here.</div>';
    }
    const n = aiCount(S.tab === 'types' ? S.galleryType : null);
    const card = n && S.tab !== 'types'
      ? `<div class="card"><div><b>${plural(n, 'AI device')} not confirmed</b><p>They already count. Spot-check them on the plan or by type, then confirm them all if you want.</p></div>
         <button class="btn" data-accept-all style="height:38px">Confirm all ${n}</button></div>`
      : '';
    left.innerHTML = `${head}<div class="list" id="leftlist">${body}</div>${card}`;
    $('#leftlist').scrollTop = keepScroll;
    const on = $('#leftlist .row.on');
    if (on) {
      const box = $('#leftlist').getBoundingClientRect();
      const r = on.getBoundingClientRect();
      if (r.top < box.top || r.bottom > box.bottom) on.scrollIntoView({ block: 'nearest' });
    }
  }

  function typeCounts() {
    const out = {};
    for (const d of live()) {
      const t = (out[d.type] = out[d.type] || { n: 0, ok: 0, family: d.family, rooms: {}, raw: new Set(), conf: [] });
      t.n += 1;
      if (CONFIRMED.includes(d.status)) t.ok += 1;
      t.rooms[d.room] = (t.rooms[d.room] || 0) + 1;
      if (d.evidence.raw_type) t.raw.add(d.evidence.raw_type);
      if (d.confidence != null) t.conf.push(d.confidence);
    }
    return out;
  }

  function orderedTypes(tc) {
    const order = CONFIG.types.map((t) => t.slug).concat(['other']);
    const fam = FAMILIES.map((f) => f[0]);
    return Object.keys(tc).sort((a, b) => fam.indexOf(tc[a].family) - fam.indexOf(tc[b].family) || order.indexOf(a) - order.indexOf(b));
  }

  function countsList() {
    const tc = typeCounts();
    const types = orderedTypes(tc);
    const fam = (f, name) => {
      const ts = types.filter((t) => tc[t].family === f);
      if (!ts.length) return '';
      const total = ts.reduce((a, t) => a + tc[t].n, 0);
      return `<div class="crow fam" data-hl="family=${f}"><span>${name}</span><b>${total}</b></div>${ts.map((t) => `
        <button class="crow" data-hl="type=${t}" data-filtertype="${t}"><span class="txt"><span class="lbl">${esc(label(t))}</span>
          <span class="sub">${tc[t].ok} of ${tc[t].n} confirmed</span></span><b>${tc[t].n}</b></button>`).join('')}`;
    };
    const brief = FAMILIES.filter((f) => f[2]).map(([f, name]) => fam(f, name)).join('');
    const extra = FAMILIES.filter((f) => !f[2]).map(([f, name]) => fam(f, name)).join('');
    return `<div class="grouphead">Asked for by the brief</div>${brief || '<div class="empty">None found.</div>'}
      ${extra ? `<div class="grouphead">Also found</div>${extra}` : ''}
      <p class="fine" style="padding:10px">Hover a count to see those devices on the plan; click it to list them.</p>`;
  }

  // ---- centre ----

  function renderCenter() {
    const plan = S.tab === 'devices';
    $('#planview').style.display = plan ? 'flex' : 'none';
    $('#centerview').hidden = plan;
    if (plan) {
      renderToolbar();
      renderMarks();
      return;
    }
    const keep = $('#centerview').scrollTop;
    $('#centerview').innerHTML = { sheet: sheetView, legend: legendView, rooms: roomsView, types: galleryView }[S.tab]();
    $('#centerview').scrollTop = keep;
  }

  function sheetView() {
    const s = S.sheet;
    const box = (f, cls, text) => `<rect x="${f.x0}" y="${f.y0}" width="${f.x1 - f.x0}" height="${f.y1 - f.y0}" class="${cls}"/>
      <text x="${f.x0 + 14}" y="${f.y0 + 44}" class="${cls}-t">${text}</text>`;
    return `<div class="centerinner" style="max-width:1100px">
      <div><h2>Sheet ${esc(s.title)}</h2><p class="lead" style="margin-top:6px">Where the electrical plan and its legend were found on the page. Devices are only looked for inside the plan frame, and typed against the legend.</p></div>
      <div class="thumb"><img src="/api/sheets/${sid}/page.png" alt="Whole page of sheet ${esc(s.title)}">
        <svg viewBox="0 0 ${s.width_pt} ${s.height_pt}" preserveAspectRatio="none">${box(s.plan_frame, 'fr-plan', 'ELECTRICAL PLAN')}${box(s.legend_frame, 'fr-legend', 'LEGEND')}</svg></div>
      <div class="facts four">
        <div class="fact"><span class="k">Sheet id</span><span class="v">${esc(s.title)}</span><span class="s">read from the title block</span></div>
        <div class="fact"><span class="k">Frames found by</span><span class="v">${s.frames_source === 'model' ? 'Model' : 'Rules'}</span><span class="s">${s.frames_source === 'model' ? 'the title rules failed; Claude located them' : 'titles and the sheet grid'}</span></div>
        <div class="fact"><span class="k">Page</span><span class="v">${Math.round(s.width_pt / 72)} × ${Math.round(s.height_pt / 72)} in</span><span class="s">${Math.round(s.width_pt)} × ${Math.round(s.height_pt)} pt</span></div>
        <div class="fact"><span class="k">Plan frame</span><span class="v">${Math.round(fw() / 72)} × ${Math.round(fh() / 72)} in</span><span class="s">purple box above</span></div>
      </div></div>`;
  }

  function legendView() {
    const tc = typeCounts();
    const types = orderedTypes(tc);
    const row = (t) => {
      const c = tc[t];
      const mean = c.conf.length ? c.conf.reduce((a, b) => a + b, 0) / c.conf.length : null;
      const raw = c.raw.size ? `<span class="sub">legend says: ${esc([...c.raw].join(', '))}</span>` : '';
      return `<div class="trow2"><span class="txt"><span class="lbl">${esc(label(t))}</span>${raw}</span>
        <span class="conf ${mean != null && mean < LOW ? 'lowc' : ''}">${pct(mean)}</span><span class="sub">${c.ok}/${c.n} confirmed</span><b>${c.n}</b></div>`;
    };
    const brief = types.filter((t) => BRIEF.has(tc[t].family));
    const extra = types.filter((t) => !BRIEF.has(tc[t].family));
    const n = (ts) => ts.reduce((a, t) => a + tc[t].n, 0);
    return `<div class="centerinner" style="max-width:1100px">
      <div><h2>Legend</h2><p class="lead" style="margin-top:6px">Every symbol on ${esc(S.sheet.title)} is typed against this legend. Next to it, the types found on this sheet with their mean confidence.</p></div>
      <div class="legendgrid"><img class="legendimg" src="/api/sheets/${sid}/legend.png" alt="Legend of sheet ${esc(S.sheet.title)}">
      <div style="display:flex;flex-direction:column;gap:14px;min-width:0">
      <div class="typebox"><div class="tb-h"><b>Asked for by the brief</b><span>fans, lights, switches, receptacles · ${n(brief)}</span></div>${brief.map(row).join('') || '<div class="empty">None found.</div>'}</div>
      ${extra.length ? `<details class="typebox"><summary class="tb-h"><b>Also found</b><span>detectors, junction boxes, other · ${n(extra)}</span></summary>${extra.map(row).join('')}</details>` : ''}
    </div></div></div>`;
  }

  function roomsView() {
    const per = {};
    for (const r of S.sheet.rooms) per[r] = { n: 0, ok: 0, how: {} };
    for (const d of live()) {
      const r = (per[d.room || 'No room'] = per[d.room || 'No room'] || { n: 0, ok: 0, how: {} });
      r.n += 1;
      if (CONFIRMED.includes(d.status)) r.ok += 1;
      const how = d.status === 'added' ? 'placed by you' : d.evidence.room_method || 'unknown';
      r.how[how] = (r.how[how] || 0) + 1;
    }
    const rows = Object.entries(per).map(([room, r]) => {
      const how = Object.entries(r.how).map(([k, v]) => `<span class="howchip ${k === 'nearest-label' ? 'weak' : ''}">${v} ${esc(ROOM_HOW[k] || k)}</span>`).join('');
      return `<tr data-hl="room=${encodeURIComponent(room)}"><td><b>${esc(room)}</b></td><td class="mono">${r.n}</td><td class="mono">${r.ok}/${r.n}</td><td>${how || '<span class="sub">no devices</span>'}</td></tr>`;
    }).join('');
    return `<div class="centerinner" style="max-width:1100px">
      <div><h2>Rooms</h2><p class="lead" style="margin-top:6px">${plural(S.sheet.rooms.length, 'room')} on this plan. Each device is counted in one, placed by the wall outlines and the printed room names. "Nearest label" means no wall closed around it: worth a look.</p></div>
      <div class="tablebox" style="background:#fff"><table class="roomtable"><thead><tr><th>Room</th><th>Devices</th><th>Confirmed</th><th>How they were placed</th></tr></thead><tbody>${rows}</tbody></table></div>
    </div>`;
  }

  function galleryView() {
    const tc = {};
    for (const d of S.devices) {
      if (d.status === 'removed' && d.initial_status === 'suggested') continue;
      tc[d.type] = tc[d.type] || { n: 0, ai: 0, family: d.family };
      if (d.status !== 'removed') tc[d.type].n += 1;
      if (d.status === 'agreed') tc[d.type].ai += 1;
    }
    const order = orderedTypes(tc);
    if (!S.galleryType || !tc[S.galleryType]) S.galleryType = order[0];
    const chips = order.map((t) => `<button class="typechip ${t === S.galleryType ? 'on' : ''} ${BRIEF.has(tc[t].family) ? '' : 'extra'}" data-gtype="${t}">
      ${esc(label(t))} <span class="k">${tc[t].n}</span></button>`).join('');
    const ids = galleryIds();
    const f = frame();
    const tiles = ids.map((id) => {
      const d = byId(id);
      const tx = -(d.x - f.x0) * TILE;
      const ty = -(d.y - f.y0) * TILE;
      return `<button class="tile ${needsLook(d) ? 'look' : ''} ${d.status === 'removed' ? 'gone' : ''} ${id === S.sel ? 'on' : ''}" data-pick="${id}">
        <span style="position:absolute;left:50%;top:46px;width:${fw() * TILE}px;height:${fh() * TILE}px;background:url(${planUrl()}) 0 0/100% 100% no-repeat;transform:translate(${tx}px,${ty}px)"></span>
        <span class="ring" style="border-color:${color(d)}"></span>
        <span class="cap"><span class="d" style="background:${color(d)}"></span><span>${esc(d.room || '')}</span><span class="conf ${d.confidence < LOW ? 'lowc' : ''}">${pct(d.confidence)}</span></span></button>`;
    }).join('');
    const ai = aiCount(S.galleryType);
    return `<div class="centerinner" style="max-width:1100px">
      <div><h2>Every device, by type</h2><p class="lead" style="margin-top:6px">A wrong symbol stands out next to twenty right ones. Hover or click a tile to look closer; confirm the type when they all match.</p></div>
      <div class="typechips">${chips}</div>
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <b style="font-size:16px">${esc(label(S.galleryType))}</b><span style="color:var(--muted)">${plural(ids.filter((id) => COUNTED.includes(byId(id).status)).length, 'device')} on this sheet</span>
        <div class="spacer"></div>
        <button class="btn primary tall" data-accept-type="${S.galleryType}" ${ai ? '' : 'disabled'}>${ai ? `Confirm all ${ai} AI ${ai === 1 ? 'device' : 'devices'}` : 'All confirmed'}</button>
      </div>
      <div class="tiles">${tiles}</div></div>`;
  }

  function renderToolbar() {
    $('#toolbar').innerHTML = `<div class="zoom" role="group" aria-label="Zoom">
        <button aria-label="Zoom out" data-zoom="-1">${ICON.minus}</button><span class="pct">${Math.round(S.zoom * 100)}%</span>
        <button aria-label="Zoom in" data-zoom="1">${ICON.plus}</button><button class="fit" data-zoom="0">Fit</button></div>
      <button class="btn ${S.adding ? 'on' : ''}" data-add>${ICON.add}${S.adding ? 'Cancel adding' : 'Add a missed device'}</button>
      <button class="btn ${S.showBom ? 'on' : ''}" data-bom>${S.showBom ? 'Hide' : 'Show'} bill of materials</button>
      <div class="spacer"></div>
      <div class="key" aria-label="Marker key">
        <span><i style="border:2.5px solid #f59e0b;background:rgba(245,158,11,.15)"></i>Needs a look</span>
        <span><i style="border:1.5px solid rgba(107,63,247,.6)"></i>AI</span>
        <span><i style="border:2px solid #6b3ff7;background:rgba(107,63,247,.18)"></i>Confirmed</span>
        <span><i style="border:2px solid #0d9488;background:rgba(13,148,136,.16)"></i>Changed or added by you</span></div>`;
    $('#addbar').hidden = !S.adding;
    $('#layer').classList.toggle('adding', S.adding);
  }

  function sizeLayer() {
    const layer = $('#layer');
    layer.style.width = `${fw() * scale()}px`;
    layer.style.height = `${fh() * scale()}px`;
  }

  function markerStyle(d) {
    if (d.status === 'review') return { stroke: '#f59e0b', w: 2.5, fill: 'rgba(245,158,11,.14)' };
    if (d.status === 'suggested') return { stroke: '#f59e0b', w: 2.5, fill: 'rgba(245,158,11,.08)', dash: '3 2' };
    if (d.status === 'agreed') return { stroke: 'rgba(107,63,247,.6)', w: 1.5, fill: 'transparent' };
    if (d.status === 'verified') return { stroke: '#6b3ff7', w: 2, fill: 'rgba(107,63,247,.18)' };
    if (d.status === 'removed') return { stroke: '#a9a4b8', w: 2, fill: 'transparent', dash: '3 2' };
    return { stroke: '#0d9488', w: 2, fill: 'rgba(13,148,136,.16)' };
  }

  function renderMarks() {
    const f = frame();
    const s = scale();
    const svg = $('#marks');
    svg.setAttribute('viewBox', `${f.x0} ${f.y0} ${fw()} ${fh()}`);
    const shown = S.devices.filter((d) => d.status !== 'removed' || d.id === S.sel)
      .sort((a, b) => (a.id === S.sel) - (b.id === S.sel));
    const fs = 9 / s;
    const bomSvg = S.showBom && S.bom ? [
      ...S.bom.runs.map((r) => {
        const [x1, y1] = r.from; const [x2, y2] = r.to;
        const c = r.cable === '14/3' ? '#d92d20' : '#2563eb';
        return `<g class="bomrun"><line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${c}" stroke-width="1.5" vector-effect="non-scaling-stroke"/>
          <text x="${(x1 + x2) / 2}" y="${(y1 + y2) / 2 - 2 / s}" font-size="${fs}" fill="${c}" text-anchor="middle" paint-order="stroke" stroke="#fff" stroke-width="${3 / s}">${r.cable} · ${r.feet} ft</text></g>`;
      }),
      ...S.bom.boxes.filter((b) => b.kind.includes('switch') || b.kind.includes('fan-rated')).map((b) => {
        const h = 9 / s;
        const w = h * Math.max(1, b.gang);
        const lbl = b.kind.includes('fan-rated') ? 'fan box' : `${b.gang}G`;
        return `<g class="bombox"><rect x="${b.x - w}" y="${b.y - h}" width="${2 * w}" height="${2 * h}" fill="none" stroke="#111" stroke-width="1.5" stroke-dasharray="4 2" vector-effect="non-scaling-stroke"/>
          <text x="${b.x}" y="${b.y - h - 2 / s}" font-size="${fs}" fill="#111" text-anchor="middle" paint-order="stroke" stroke="#fff" stroke-width="${3 / s}">${lbl}</text></g>`;
      }),
    ].join('') : '';
    svg.innerHTML = bomSvg + shown.map((d) => {
      const on = d.id === S.sel;
      const st = markerStyle(d);
      const r = (on ? 12 : 9) / s;
      const dim = !on && S.hl && S.filter === 'all' && !matchesHl(d) ? 'dim' : '';
      return `<g class="mk ${on ? 'sel' : ''} ${dim}" data-pick="${d.id}">
        <circle class="halo" cx="${d.x}" cy="${d.y}" r="${r + 5 / s}" fill="none" stroke="rgba(107,63,247,.3)" stroke-width="7" vector-effect="non-scaling-stroke"/>
        <circle class="ring" cx="${d.x}" cy="${d.y}" r="${r}" fill="${st.fill}" stroke="${on ? '#6b3ff7' : st.stroke}"
        stroke-width="${on ? 3 : st.w}" ${st.dash ? `stroke-dasharray="${st.dash}"` : ''} vector-effect="non-scaling-stroke"/></g>`;
    }).join('');
  }

  function scrollToSel(force = false) {
    if (S.tab !== 'devices') return;
    const d = byId(S.sel);
    const el = $('#scroller');
    if (!d || !el) return;
    const s = scale();
    const cx = 32 + (d.x - frame().x0) * s;
    const cy = 32 + (d.y - frame().y0) * s;
    const pad = 80;
    if (force || cx < el.scrollLeft + pad || cx > el.scrollLeft + el.clientWidth - pad
        || cy < el.scrollTop + pad || cy > el.scrollTop + el.clientHeight - pad) {
      el.scrollTo({ left: cx - el.clientWidth / 2, top: cy - el.clientHeight / 2, behavior: force ? 'auto' : 'smooth' });
    }
  }

  function setZoom(dir) {
    const el = $('#scroller');
    const old = scale();
    const cx = (el.scrollLeft + el.clientWidth / 2 - 32) / old;
    const cy = (el.scrollTop + el.clientHeight / 2 - 32) / old;
    if (dir === 0) S.zoom = 1;
    else S.zoom = ZOOMS[Math.max(0, Math.min(ZOOMS.length - 1, ZOOMS.indexOf(S.zoom) + dir))];
    sizeLayer();
    renderToolbar();
    renderMarks();
    const s = scale();
    el.scrollLeft = cx * s + 32 - el.clientWidth / 2;
    el.scrollTop = cy * s + 32 - el.clientHeight / 2;
  }

  // ---- right pane ----

  const STAGE_TEXT = {
    sheet: ['Sheet and frames', (s) => (s.frames_source === 'model'
      ? ['Medium', 'The title and grid rules could not read this page, so the model located the plan and the legend. Check the boxes sit on the right drawings.']
      : ['High', 'Found from the sheet\'s own titles and grid lines: ELECTRICAL PLAN and LEGEND.'])],
    legend: ['Legend and types', () => ['High', 'Types come from the legend rows. "Also found" lists what the brief did not ask for, with the legend\'s own words for anything outside the type list.']],
    rooms: ['Rooms', () => {
      const p = roomPlaced();
      return [p >= 0.9 ? 'High' : 'Medium', `${Math.round(p * 100)}% of the devices sit inside a room's walls or were placed by the model; the rest were moved off a wall or went to the nearest label.`];
    }],
  };

  function renderRight() {
    const right = $('#right');
    if (STAGE_TEXT[S.tab]) {
      const [title, fn] = STAGE_TEXT[S.tab];
      const [level, why] = fn(S.sheet);
      const at = S.sheet.confirmed?.[S.tab];
      right.innerHTML = `<div class="sel-pos">What it read · ${esc(title)}</div>
        <div class="sel-title"><span class="chip ${level === 'High' ? 'p-ok' : 'look'}">${level} confidence</span><h2>${esc(title)}</h2></div>
        <p class="fine" style="font-size:13px;color:var(--ink-3)">${esc(why)}</p>
        ${at ? `<div class="why done"><b>${ICON.check} You said this looks right</b><span class="meta">${esc(new Date(at).toLocaleString())}</span></div>`
          : `<button class="btn primary big" data-confirm-stage="${S.tab}">Looks right<kbd>Enter</kbd></button>`}
        <p class="fine">Optional. The counts never wait for this; it is recorded with the export so others see what a person checked.</p>
        <button class="btn mid" data-tab="devices">Go to the devices ${ICON.right}</button>`;
      return;
    }
    const d = byId(S.sel);
    if (!d) {
      right.innerHTML = '<p class="fine">Hover or select a device on the plan or in the list.</p>';
      return;
    }
    const ids = listIds();
    const pos = ids.indexOf(d.id);
    const f = frame();
    const tx = -(d.x - f.x0) * CLOSEUP;
    const ty = -(d.y - f.y0) * CLOSEUP;
    const types = CONFIG.types.map((t) => `<option value="${t.slug}" ${t.slug === d.type ? 'selected' : ''}>${esc(t.label)}</option>`);
    if (!CONFIG.types.some((t) => t.slug === d.type)) types.unshift(`<option value="${esc(d.type)}" selected>${esc(label(d.type))}</option>`);
    const rooms = [...new Set([...(S.sheet.rooms || []), d.room].filter(Boolean))];
    const ev = d.evidence || {};
    const ai = ['review', 'agreed', 'suggested'].includes(d.status);
    const prov = ai
      ? `Read by AI: ${esc(d.source)}${ev.model ? ` (${esc(ev.model)})` : ''}`
      : { verified: 'Confirmed by you; the AI read it the same way', edited: `Changed by you; the AI read it as ${esc(label(ev.model_type || d.type))}`, added: 'Added by you', removed: 'Removed by you; it no longer counts' }[d.status];
    let why = '';
    if (d.flag) {
      let runs = '';
      const first = label(ev.model_type || d.type);
      if (ev.agreement === 'type differs') runs = [['RUN 1', first], ['RUN 2', label(d.alternatives[0])]];
      else if (ev.agreement === '1/2') runs = [['RUN 1', first], ['RUN 2', 'Not found']];
      else if (d.initial_status === 'suggested') runs = [['MAIN READ', 'Not found'], [String(d.source).toUpperCase(), first]];
      const grid = runs ? `<div class="runs">${runs.map(([k, v]) => `<div><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`).join('')}</div>` : '';
      why = `<div class="why ${needsLook(d) ? '' : 'done'}"><span class="k mono" style="font-size:11px;color:var(--muted)">${needsLook(d) ? 'LIKELY ISSUE' : 'WAS FLAGGED'}</span><b>${esc(prettyFlag(d.flag))}</b>${grid}</div>`;
    }
    const low = d.confidence != null && d.confidence < LOW;
    const primary = d.status === 'suggested' ? 'Yes, it is a device' : 'Looks right, next';
    const actions = d.status === 'removed'
      ? '<button class="btn big" data-restore>Restore this device</button>'
      : `<div style="display:flex;flex-direction:column;gap:8px">
          <button class="btn primary big" data-confirm>${primary}<kbd>Enter</kbd></button>
          <button class="btn mid" data-reject>Not a device<kbd>X</kbd></button></div>`;
    right.innerHTML = `<div class="sel-head"><span class="sel-pos">${pos >= 0 ? `Device ${pos + 1} of ${ids.length}` : `Device #${d.id}`}</span><div class="spacer"></div>
        <button class="icon-btn" aria-label="Previous device" data-step="-1">${ICON.left}</button>
        <button class="icon-btn" aria-label="Next device" data-step="1">${ICON.right}</button></div>
      <div class="sel-title"><span style="display:flex;gap:6px;flex-wrap:wrap">${provChip(d)}${needsLook(d) ? '<span class="chip look">Needs a look</span>' : ''}</span>
        <h2>${esc(label(d.type))}</h2><span class="room">${esc(d.room || 'No room')}</span></div>
      <div class="confrow"><span class="cbar"><span style="width:${Math.round((d.confidence ?? 0) * 100)}%;background:${low ? '#f59e0b' : '#6b3ff7'}"></span></span>
        <span class="conf ${low ? 'lowc' : ''}"><b>${pct(d.confidence)}</b> confidence${low ? ' · low' : ''}</span></div>
      <div class="provline">${prov}</div>
      <div class="closeup"><span style="position:absolute;left:50%;top:50%;width:${fw() * CLOSEUP}px;height:${fh() * CLOSEUP}px;background:url(${planUrl()}) 0 0/100% 100% no-repeat;transform:translate(${tx}px,${ty}px)"></span>
        <span class="ring"></span><span class="tag">Close-up</span></div>
      ${why}
      <div style="display:flex;flex-direction:column;gap:12px">
        <label class="field">Device type<select data-field="type">${types.join('')}</select></label>
        <label class="field">Room<select data-field="room">${rooms.map((r) => `<option ${r === d.room ? 'selected' : ''}>${esc(r)}</option>`).join('')}</select></label>
      </div>
      ${actions}
      <p class="fine">Changing the type or room marks the device as changed by you. Every change is listed with the export.</p>
      <div class="meta">room ${esc(ROOM_HOW[ev.room_method] || ev.room_method || 'set by you')} · #${d.id}</div>`;
  }

  // ---- counts strip ----

  function renderStrip() {
    const rows = (S.counts?.rows || []).filter((r) => r.sheet_id === sid);
    const fam = {};
    let total = 0;
    let ok = 0;
    for (const r of rows) { fam[r.family] = (fam[r.family] || 0) + r.n; total += r.n; ok += r.confirmed; }
    const d = byId(S.sel);
    const roomN = d ? rows.filter((r) => r.room === d.room).reduce((a, r) => a + r.n, 0) : null;
    const vals = { total, ok, ...fam, room: `${d?.room}:${roomN}` };
    const changed = (k) => (S.prevStrip[k] !== undefined && S.prevStrip[k] !== vals[k] ? 'flash' : '');
    const chip = ([k, name, brief]) => `<span class="c ${brief ? '' : 'extra'} ${changed(k)}" data-hl="family=${k}"><b>${fam[k]}</b>${name.toLowerCase()}</span>`;
    $('#strip').innerHTML = `<span class="lab">Counted on this sheet</span><span class="c ${changed('total')}"><b>${total}</b>devices</span>
      <span class="c ${changed('ok')}" style="color:var(--muted)">${ok} confirmed</span><span class="vsep"></span>
      ${FAMILIES.filter(([k]) => fam[k]).map(chip).join('')}
      <div class="spacer"></div>${d && d.room ? `<span class="c room ${changed('room')}" data-hl="room=${encodeURIComponent(d.room)}"><b>${roomN}</b>in ${esc(d.room)}</span>` : ''}`;
    S.prevStrip = vals;
  }

  // ---- hover: list row, marker, tile and count cells all point at the same devices ----

  function setHot(ids, focus = false) {
    for (const el of root.querySelectorAll('.hot')) el.classList.remove('hot');
    $('#marks')?.classList.toggle('focus', focus && ids.length > 0);
    for (const id of ids) for (const el of root.querySelectorAll(`[data-pick="${id}"]`)) el.classList.add('hot');
  }

  function onHover(e) {
    const p = e.target.closest('[data-pick]');
    const h = e.target.closest('[data-hl]');
    if (p) {
      const d = byId(+p.dataset.pick);
      if (!d) return;
      setHot([d.id]);
      const mk = p.closest('svg') ? p : $(`#marks [data-pick="${d.id}"]`);
      const r = (mk && S.tab === 'devices' ? mk : p).getBoundingClientRect();
      const visible = mk && S.tab === 'devices' && r.width && r.bottom > 0 && r.top < window.innerHeight;
      if (p.closest('svg') || !visible) showTip(deviceTip(d), r.right + 10, r.top);
      else showTip(deviceTip(d), r.right + 10, r.top - 4);
    } else if (h) {
      const fn = hlFn(h.dataset.hl);
      const ids = S.devices.filter((d) => COUNTED.includes(d.status) && fn(d)).map((d) => d.id);
      setHot(ids, true);
      hideTip();
    } else {
      setHot([]);
      hideTip();
    }
  }

  // ---- actions ----

  function select(id, scroll = true) {
    S.sel = id;
    S.adding = false;
    if (S.tab !== 'devices' && S.tab !== 'types') S.tab = 'devices';
    renderStagebar();
    renderLeft();
    renderCenter();
    renderRight();
    renderStrip();
    if (scroll) scrollToSel();
  }

  function step(dir) {
    const ids = listIds();
    if (!ids.length) return;
    const pos = ids.indexOf(S.sel);
    select(ids[(pos + dir + ids.length) % ids.length]);
  }

  function advance() {
    const ids = listIds();
    const pos = ids.indexOf(S.sel);
    const rest = ids.slice(pos + 1).concat(ids.slice(0, Math.max(pos, 0)));
    const wanted = S.tab === 'types' ? ['agreed', ...OPEN] : OPEN;
    const next = rest.find((id) => wanted.includes(byId(id).status));
    if (next !== undefined) return select(next);
    if (pos >= 0 && pos < ids.length - 1) return select(ids[pos + 1]);
    renderAll();
  }

  function setTab(tab) {
    S.tab = tab;
    S.adding = false;
    if (tab === 'types') {
      galleryView();
      if (!galleryIds().includes(S.sel)) S.sel = galleryIds()[0] ?? null;
    } else if (tab === 'devices' && !listIds().includes(S.sel)) {
      S.sel = listIds()[0] ?? S.sel;
    }
    renderAll();
    if (tab === 'devices') requestAnimationFrame(() => scrollToSel(true));
  }

  async function refreshMeta() {
    try {
      const [project, counts, bomData] = await Promise.all([api(`/projects/${pid}`), api(`/projects/${pid}/counts`), api(`/projects/${pid}/bom?sheet=${sid}`).catch(() => null)]);
      if (!alive) return;
      S.project = project;
      S.counts = counts;
      S.bom = bomData;
      if (S.showBom) renderMarks();
      $('#hdr').innerHTML = header();
      renderStrip();
    } catch (e) { toast(e.message, true); }
  }

  function upsert(d) {
    const i = S.devices.findIndex((x) => x.id === d.id);
    if (i >= 0) S.devices[i] = d; else S.devices.push(d);
  }

  async function mutate(call, after) {
    try {
      const d = await call;
      if (d && d.id) upsert(d);
      if (after) after(d); else renderAll();
      refreshMeta();
    } catch (e) {
      toast(e.message, true);
    }
  }

  function confirm() {
    const d = byId(S.sel);
    if (!d || d.status === 'removed') return;
    if (['review', 'agreed', 'suggested'].includes(d.status)) mutate(api(`/devices/${d.id}/verify`, { method: 'POST' }), advance);
    else advance();
  }

  function reject() {
    const d = byId(S.sel);
    if (!d || d.status === 'removed') return;
    mutate(api(`/devices/${d.id}`, { method: 'DELETE' }), () => { toast(`${label(d.type)} removed · it can be restored`); advance(); });
  }

  async function acceptAgreed(type) {
    try {
      const r = await api(`/sheets/${sid}/accept-agreed`, { json: type ? { type } : {} });
      const sheet = await api(`/sheets/${sid}`);
      S.devices = sheet.devices;
      toast(`${plural(r.verified, 'device')} confirmed`);
      renderAll();
      refreshMeta();
    } catch (e) { toast(e.message, true); }
  }

  async function confirmStage(stage) {
    try {
      const s = await api(`/sheets/${sid}/confirm`, { json: { stage } });
      S.sheet.confirmed = s.confirmed;
      toast('Recorded: looks right');
      renderStagebar();
      renderRight();
    } catch (e) { toast(e.message, true); }
  }

  function onClick(e) {
    const t = e.target.closest('[data-pick],[data-tab],[data-filter],[data-filtertype],[data-zoom],[data-add],[data-bom],[data-confirm],[data-reject],[data-restore],[data-step],[data-accept-all],[data-accept-type],[data-gtype],[data-confirm-stage],[data-clearhl],[data-retry]');
    if (!t) {
      if (S.adding && e.target.closest('#layer')) addAt(e);
      return;
    }
    if (S.adding && t.closest('#layer')) return addAt(e);
    const ds = t.dataset;
    if ('bom' in ds) { S.showBom = !S.showBom; renderToolbar(); renderMarks(); return; }
    if (ds.pick) select(+ds.pick, !t.closest('#layer'));
    else if (ds.tab) setTab(ds.tab);
    else if (ds.filtertype) {
      S.hl = { type: ds.filtertype, room: null };
      S.filter = 'all';
      S.sel = listIds()[0] ?? S.sel;
      setTab('devices');
    } else if (ds.filter) {
      S.filter = ds.filter;
      if (S.filter !== 'counts' && !listIds().includes(S.sel)) S.sel = listIds()[0] ?? S.sel;
      renderAll();
      scrollToSel();
    } else if (ds.zoom) setZoom(+ds.zoom);
    else if ('add' in ds) { S.adding = !S.adding; renderToolbar(); }
    else if ('confirm' in ds) confirm();
    else if ('reject' in ds) reject();
    else if ('restore' in ds) mutate(api(`/devices/${S.sel}/restore`, { method: 'POST' }));
    else if (ds.step) step(+ds.step);
    else if ('acceptAll' in ds) acceptAgreed(null);
    else if (ds.acceptType) acceptAgreed(ds.acceptType);
    else if (ds.gtype) {
      S.galleryType = ds.gtype;
      S.sel = galleryIds()[0] ?? null;
      renderAll();
    } else if (ds.confirmStage) confirmStage(ds.confirmStage);
    else if ('clearhl' in ds) {
      S.hl = null;
      renderAll();
    }
  }

  function addAt(e) {
    const r = $('#layer').getBoundingClientRect();
    const s = scale();
    const x = frame().x0 + (e.clientX - r.left) / s;
    const y = frame().y0 + (e.clientY - r.top) / s;
    S.adding = false;
    mutate(api(`/sheets/${sid}/devices`, { json: { type: 'receptacle', x, y } }), (d) => {
      S.filter = 'all';
      S.hl = null;
      S.sel = d.id;
      renderAll();
      toast(`Added a receptacle in ${d.room}; set its type on the right`);
    });
  }

  function onChange(e) {
    const field = e.target.dataset.field;
    if (!field || !S.sel) return;
    mutate(api(`/devices/${S.sel}`, { method: 'PATCH', json: { [field]: e.target.value } }));
  }

  function onKey(e) {
    if (!S.sheet || S.sheet.status !== 'ready' || !$('#right') || e.metaKey || e.ctrlKey || e.altKey) return;
    const tag = e.target.tagName;
    if (tag === 'SELECT' || tag === 'INPUT' || tag === 'TEXTAREA') return;
    if (e.key === 'Escape' && S.adding) { S.adding = false; renderToolbar(); return; }
    if (STAGE_TEXT[S.tab]) {
      if (e.key === 'Enter') { e.preventDefault(); if (!S.sheet.confirmed?.[S.tab]) confirmStage(S.tab); }
      return;
    }
    if (e.key === 'Enter') { e.preventDefault(); confirm(); }
    else if (e.key === 'x' || e.key === 'X') { e.preventDefault(); reject(); }
    else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); step(1); }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { e.preventDefault(); step(-1); }
  }

  function onResize() {
    const sc = $('#scroller');
    if (!sc || !S.sheet?.plan_frame || !sc.clientWidth) return;
    S.fit = Math.min((sc.clientWidth - 64) / fw(), (sc.clientHeight - 64) / fh()) || S.fit;
    sizeLayer();
    renderMarks();
  }

  document.addEventListener('keydown', onKey);
  window.addEventListener('resize', onResize);
  root.innerHTML = `${topbar(null)}<div class="statecard"><div class="spinner"></div><p class="lead">Opening the sheet…</p></div>`;
  load().catch((e) => {
    root.innerHTML = `${topbar(null)}<div class="statecard err" role="alert"><h2 style="margin:0">This sheet could not be opened</h2><div class="msg">${esc(e.message)}</div><a class="btn" href="#/">Back to plan sets</a></div>`;
  });
  return {
    destroy() {
      alive = false;
      clearTimeout(poll);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', onResize);
      root.onclick = null;
      root.onchange = null;
      root.onmouseover = null;
      root.onmouseleave = null;
    },
  };
}

// ---- results ----

function Results(pid, params) {
  const root = $('#app');
  let alive = true;
  const scope = params.get('sheet') ? +params.get('sheet') : 'all';
  root.innerHTML = `${topbar(null)}<div class="statecard"><div class="spinner"></div><p class="lead">Counting…</p></div>`;

  async function load() {
    const [project, counts, events] = await Promise.all([
      api(`/projects/${pid}`), api(`/projects/${pid}/counts`), api(`/projects/${pid}/events`),
    ]);
    const bomData = await api(`/projects/${pid}/bom${scope === 'all' ? '' : `?sheet=${scope}`}`).catch(() => null);
    const ready = project.sheets.filter((s) => s.status === 'ready');
    const details = await Promise.all(ready.map((s) => api(`/sheets/${s.id}`)));
    if (alive) render(project, counts, events, ready, details, bomData);
  }

  function render(project, counts, events, ready, details, bomData) {
    const devices = details.flatMap((s) => s.devices);
    const devMap = Object.fromEntries(devices.map((d) => [d.id, d]));
    const sheetName = Object.fromEntries(project.sheets.map((s) => [s.id, s.title]));
    const flagged = devices.filter((d) => OPEN.includes(d.initial_status)).length;
    const stillOpen = devices.filter(needsLook).length;

    const mine = events.filter((e) => e.actor === 'you' && ['create', 'update', 'delete', 'restore'].includes(e.action) && e.after?.status !== 'verified');
    const editedTypes = new Set();
    for (const e of mine) {
      if (scope !== 'all' && e.sheet_id !== scope) continue;
      if (e.after?.type) editedTypes.add(e.after.type);
      if (e.before?.type) editedTypes.add(e.before.type);
    }

    // columns: sheets, or the rooms of one sheet
    const detail = scope === 'all' ? null : details.find((s) => s.id === scope);
    const rows = counts.rows.filter((r) => scope === 'all' || r.sheet_id === scope);
    const total = rows.reduce((a, r) => a + r.n, 0);
    const confirmed = rows.reduce((a, r) => a + r.confirmed, 0);
    const cols = scope === 'all'
      ? ready.map((s) => ({ name: s.title, match: (r) => r.sheet_id === s.id, href: (t) => `#/p/${pid}/s/${s.id}?type=${t}` }))
      : [...new Set([...(detail?.rooms || []), ...rows.map((r) => r.room)])].filter((room) => rows.some((r) => r.room === room))
        .map((room) => ({ name: room, match: (r) => r.room === room, href: (t) => `#/p/${pid}/s/${scope}?type=${t}&room=${encodeURIComponent(room)}` }));
    const sum = (fn, key = 'n') => rows.filter(fn).reduce((a, r) => a + r[key], 0);
    const typeOrder = CONFIG.types.map((t) => t.slug).concat(['other']);
    const present = [...new Set(rows.map((r) => r.type))].sort((a, b) => typeOrder.indexOf(a) - typeOrder.indexOf(b));
    const famOf = Object.fromEntries(rows.map((r) => [r.type, r.family]));
    const cell = (v, ok, href) => (v ? `<a href="${href}" title="${v} ${v === 1 ? 'device' : 'devices'}, ${ok} confirmed by a person. Click to see them on the plan.">${v}</a>` : '<span class="zero">0</span>');
    const group = ([f, name]) => {
      const types = present.filter((t) => famOf[t] === f);
      if (!types.length) return '';
      const sub = cols.map((c) => sum((r) => types.includes(r.type) && c.match(r)));
      const subT = sum((r) => types.includes(r.type));
      return `<tbody><tr class="fam"><td>${name}</td>${sub.map((v) => `<td>${v}</td>`).join('')}<td class="okcol">${sum((r) => types.includes(r.type), 'confirmed')} of ${subT}</td><td>${subT}</td></tr>
        ${types.map((t) => {
          const n = sum((r) => r.type === t);
          const ok = sum((r) => r.type === t, 'confirmed');
          return `<tr class="trow"><td>${esc(label(t))}${editedTypes.has(t) ? '<span class="edited">edited</span>' : ''}</td>
          ${cols.map((c) => `<td>${cell(sum((r) => r.type === t && c.match(r)), sum((r) => r.type === t && c.match(r), 'confirmed'), c.href(t))}</td>`).join('')}
          <td class="okcol ${ok === n ? 'all' : ''}">${ok} of ${n}</td><td>${n}</td></tr>`;
        }).join('')}</tbody>`;
    };
    const briefBody = FAMILIES.filter((f) => f[2]).map(group).join('');
    const extraBody = FAMILIES.filter((f) => !f[2]).map(group).join('');
    const width = cols.length + 3;
    const foot = cols.map((c) => sum(c.match));
    const table = present.length
      ? `<table class="counts" style="min-width:${Math.max(600, 340 + cols.length * 90)}px"><thead><tr><th>Device</th>${cols.map((c) => `<th>${esc(c.name)}</th>`).join('')}<th>Confirmed</th><th>Total</th></tr></thead>
        <tbody><tr class="section"><td colspan="${width}">Asked for by the brief</td></tr></tbody>${briefBody}
        ${extraBody ? `<tbody><tr class="section"><td colspan="${width}">Also found (not asked for by the brief)</td></tr></tbody>${extraBody}` : ''}
        <tfoot><tr><td>All devices</td>${foot.map((v) => `<td>${v}</td>`).join('')}<td class="okcol">${confirmed} of ${total}</td><td>${total}</td></tr></tfoot></table>`
      : '<div class="empty">No devices counted yet.</div>';

    const seg = [['all', 'All sheets'], ...ready.map((s) => [s.id, `${s.title} by room`])].map(([k, t]) =>
      `<button class="${k === scope ? 'on' : ''}" data-scope="${k}">${esc(t)}</button>`).join('');

    const describe = (e) => {
      const d = devMap[e.device_id];
      const name = label(e.after?.type || d?.type || '');
      if (e.action === 'create') return [`Added ${name}`, e.after?.room];
      if (e.action === 'delete') return [`Removed ${name}`, d?.room];
      if (e.action === 'restore') return [`Restored ${name}`, d?.room];
      const parts = [];
      if (e.before?.type) parts.push(`${label(e.before.type)} → ${label(e.after.type)}`);
      if (e.before && 'room' in e.before) parts.push(`${e.before.room} → ${e.after.room}`);
      if (e.before && 'x' in e.before) parts.push(`Moved ${name}`);
      if (!parts.length) parts.push(`${name}: ${PROV[e.after?.status]?.[0] || 'changed'}`);
      return [parts.join(' · '), e.before && 'room' in e.before ? label(d?.type) : d?.room];
    };
    const changes = mine.slice().reverse().slice(0, 60).map((e) => {
      const [what, sub] = describe(e);
      return `<a class="change" href="#/p/${pid}/s/${e.sheet_id}?d=${e.device_id}"><span class="s">${esc(sheetName[e.sheet_id] || '')}</span>
        <span class="w"><b>${esc(what)}</b><span>${esc(sub || '')}</span></span></a>`;
    }).join('');

    const runs = project.sheets.map((s) => `<div class="runrow ${s.status === 'error' ? 'err' : ''}">
        <div class="h"><b>${esc(s.title)}</b>${s.status === 'ready' ? `<span class="pill grey">${s.live} devices</span><span class="pill grey">frames by ${s.frames_source === 'model' ? 'model' : 'rules'}</span>` : `<span class="pill ${s.status === 'error' ? 'red' : 'amber'}">${s.status}</span>`}
        ${s.status === 'error' ? `<div class="spacer"></div><button class="btn" style="height:30px" data-retry="${s.id}">Try again</button>` : ''}</div>
        ${s.status === 'error' ? `<span class="mono" style="font-size:12px;word-break:break-word">${esc(s.error)}</span>` : `<span class="runinfo">${runFacts(s)}</span>`}</div>`).join('');

    const money = (v) => `$${v.toFixed(2)}`;
    const gangs = {};
    for (const b of bomData?.boxes || []) gangs[b.kind] = (gangs[b.kind] || 0) + 1;
    const feet = (c) => bomData.runs.filter((r) => r.cable === c).reduce((a, r) => a + r.feet, 0);
    const bomHtml = !bomData ? '' : `<section class="bom" aria-label="Bill of materials"><h2>Bill of materials${scope === 'all' ? '' : `, ${esc(sheetName[scope] || '')}`}</h2>
      <p>Derived from the devices above, so it changes with every correction. Boxes: ${Object.entries(gangs).map(([k, n]) => `${n} × ${esc(k)}`).join(' · ')}.
      Cable: ${feet('14/2').toFixed(0)} ft 14/2 in ${bomData.runs.filter((r) => r.cable === '14/2').length} switch legs, ${feet('14/3').toFixed(0)} ft 14/3 between 3-way switches. Boxes and runs are drawn on each plan (Bill of materials layer).</p>
      <table class="counts bomtable"><thead><tr><th>Item</th><th>Qty</th><th>Unit</th><th>Total</th><th>Price source</th></tr></thead>
      <tbody>${bomData.lines.map((l) => `<tr class="trow"><td>${esc(l.name)}</td><td>${l.qty}</td><td>${money(l.unit_price)}</td><td>${money(l.total)}</td><td class="src">${esc(l.source)}</td></tr>`).join('')}</tbody>
      <tfoot><tr><td>Total materials</td><td></td><td></td><td>${money(bomData.total)}</td><td></td></tr></tfoot></table>
      <ul class="assume">${bomData.assumptions.map((a) => `<li>${esc(a)}</li>`).join('')}</ul></section>`;

    const badge = `<span class="pill purple" style="align-self:flex-start">${ICON.check}Counts ready · ${confirmed} of ${total} confirmed by you</span>`;

    root.innerHTML = `${topbar(project, `<div class="spacer"></div><a class="btn" href="#/p/${pid}">${ICON.left}Back to the plans</a>`)}
    <div class="results">
      <div class="rhead"><div class="t">${badge}<h1>Device counts</h1><p>By type and ${scope === 'all' ? 'sheet' : 'room'}, straight from the AI plus every change you made. Click a number to see those devices on the plan.</p></div>
        <div class="exports">
          <a class="btn tall" href="/api/projects/${pid}/export.pdf" download>Marked-up plans (PDF)</a>
          <a class="btn tall" href="/api/projects/${pid}/export.json" download>JSON with audit trail</a>
          <a class="btn primary tall" href="/api/projects/${pid}/export.csv" download>${ICON.down}Download CSV</a></div></div>
      ${project.errors ? `<div class="errcards">${project.sheets.filter((s) => s.status === 'error').map((s) => errorCard(s)).join('')}</div>` : ''}
      <div class="rbody">
        <div class="rmain"><div class="viewseg" role="group" aria-label="Columns">${seg}</div><div class="tablebox">${table}</div>${bomHtml}</div>
        <aside class="raside">
          <div class="facts">
            <div class="fact"><span class="k">Flagged by checks</span><span class="v">${flagged}</span><span class="s">${stillOpen ? `${stillOpen} still need a look` : 'all looked at'}</span></div>
            <div class="fact"><span class="k">Confirmed</span><span class="v">${confirmed}</span><span class="s">of ${total} counted devices</span></div>
          </div>
          <section class="panel" aria-label="Your changes"><div class="ph"><h2>Your changes</h2><span class="badge done">${mine.length}</span><div class="spacer"></div><span>In every export</span></div>
            ${changes || '<div class="empty">No changes yet. The counts are the AI\'s.</div>'}</section>
          <section class="panel" aria-label="How each sheet was read"><div class="ph"><h2>How each sheet was read</h2></div>${runs}</section>
        </aside>
      </div>
    </div>`;
  }

  root.onclick = async (e) => {
    const s = e.target.closest('[data-scope]');
    if (s) location.hash = s.dataset.scope === 'all' ? `#/p/${pid}/results` : `#/p/${pid}/results?sheet=${s.dataset.scope}`;
    const r = e.target.closest('[data-retry]');
    if (r) { await retrySheet(+r.dataset.retry); load(); }
  };
  load().catch((e) => toast(e.message, true));
  return { destroy() { alive = false; root.onclick = null; } };
}

// ---- start ----

(async function start() {
  try {
    CONFIG = await api('/config');
    for (const t of CONFIG.types) LABEL[t.slug] = t.label;
    LABEL.other = 'Other (not in the type list)';
  } catch (e) {
    toast(`The server did not answer: ${e.message}`, true);
  }
  window.addEventListener('hashchange', route);
  route();
})();
