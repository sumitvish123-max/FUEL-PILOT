'use strict';
/* FuelPilot frontend – talks to the FastAPI backend (see backend/main.py).
   Served by the backend at http://localhost:8000, or open index.html anywhere and it calls localhost:8000. */

// ───────────────────────── config & state ─────────────────────────
const API = window.FUELPILOT_API ?? ((location.protocol === 'file:' || (['localhost', '127.0.0.1'].includes(location.hostname) && location.port !== '8000')) ? 'http://127.0.0.1:8000' : '');
const state = { role: null, page: 'overview', online: true, lastReport: null };
const Auth = {
  get token() { try { return sessionStorage.getItem('fp-token'); } catch { return null; } },   // cleared when the tab closes
  set token(v) { try { v ? sessionStorage.setItem('fp-token', v) : sessionStorage.removeItem('fp-token'); } catch { /* ignore */ } },
};
const NAV = [
  ['overview', '▦', 'Overview', 'both'], ['sales', '₹', 'Shifts & Cash', 'both'], ['inventory', '◫', 'Tanks & Stock', 'both'],
  ['staff', '♙', 'Employees', 'both'], ['equipment', '⚙', 'Equipment', 'owner'], ['maintenance', '⚒', 'Maintenance', 'both'],
  ['compliance', '✓', 'Compliance', 'owner'], ['audit', '◉', 'Audit & Alerts', 'owner'],
];
const TITLES = { overview: 'Station Overview', sales: 'Shifts, Sales & Cash Reconciliation', inventory: 'Tank Inventory & Deliveries',
  staff: 'Employees & Face Attendance', equipment: 'Equipment Health', maintenance: 'Maintenance Tickets & PM Calendar',
  compliance: 'Statutory Compliance', audit: 'Audit & Alerts' };

// ───────────────────────── helpers ─────────────────────────
const $ = (s, r = document) => r.querySelector(s);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (v, d = 0) => Number(v ?? 0).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
const money = (v, d = 0) => `₹${num(v, d)}`;
const dt = (iso) => iso ? new Date(iso).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '–';
const tm = (iso) => iso ? new Date(iso).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }) : '–';
const dd = (iso) => iso ? new Date(iso + 'T00:00').toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '–';
const u = (x) => (x === 'KG' ? 'kg' : x);
const unit = (p) => (p === 'CNG' ? 'kg' : 'L');
const pretty = (s) => String(s ?? '').replace(/_/g, ' ').toLowerCase().replace(/^\w|\s\w/g, (c) => c.toUpperCase()).replace(/\bCng\b/g, 'CNG').replace(/\bAmc\b/g, 'AMC').replace(/\bPm\b/g, 'PM').replace(/\bUpi\b/g, 'UPI');

function showToast(msg, error = false) {
  const t = $('#toast'); t.textContent = msg; t.classList.toggle('error', error); t.classList.add('show');
  clearTimeout(showToast.t); showToast.t = setTimeout(() => t.classList.remove('show'), error ? 5000 : 2800);
}
function setOnline(ok) {
  state.online = ok;
  $('#apiDot').classList.toggle('off', !ok); $('#livePill').classList.toggle('off', !ok);
  $('#apiState').textContent = ok ? 'Backend connected' : 'Backend offline';
  $('#livePill').lastChild.textContent = ok ? ' LIVE' : ' OFFLINE';
}
async function api(path, { method = 'GET', body } = {}) {
  let res;
  try {
    const headers = body ? { 'Content-Type': 'application/json' } : {};
    if (Auth.token) headers.Authorization = `Bearer ${Auth.token}`;
    res = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  } catch (e) { setOnline(false); throw new Error('Cannot reach the FuelPilot server. Start it with: uvicorn backend.main:app --port 8000'); }
  setOnline(true);
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && path !== '/api/auth/login' && path !== '/api/attendance/verify') { logout('Session expired – please sign in again'); throw new Error('Login required'); }
  if (!res.ok) {
    const d = data.detail;
    throw new Error(Array.isArray(d) ? d.map((x) => `${(x.loc || []).slice(-1)[0]}: ${x.msg}`).join('; ') : d || `Request failed (${res.status})`);
  }
  return data;
}
const badge = (tone, text) => `<span class="badge ${tone}">${esc(text)}</span>`;
const toneOf = { OK: 'green', GREEN: 'green', YELLOW: 'amber', RED: 'red', LOW: 'amber', DRY_RISK: 'red', NO_DATA: 'grey', SHORT: 'red', OVER: 'amber',
  PRESENT: 'green', LATE: 'amber', OPERATIONAL: 'green', WATCH: 'amber', DOWN: 'red', CRITICAL: 'red', HIGH: 'amber', MEDIUM: 'blue', LOW_: 'grey' };

// ───────────────────────── theme ─────────────────────────
function applyTheme(t) { document.documentElement.dataset.theme = t; try { localStorage.setItem('fp-theme', t); } catch { /* private mode */ } }
applyTheme((() => { try { return localStorage.getItem('fp-theme'); } catch { return null; } })() || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
$('#themeToggle').addEventListener('click', () => applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));

// ───────────────────────── audio feedback ─────────────────────────
let audioCtx;
function beep(ok) {
  try {
    audioCtx ??= new (window.AudioContext || window.webkitAudioContext)();
    const seq = ok ? [[880, 0, .12], [1320, .14, .18]] : [[220, 0, .35]];
    seq.forEach(([f, at, len]) => {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.type = ok ? 'sine' : 'sawtooth'; o.frequency.value = f; g.gain.value = .15;
      o.connect(g); g.connect(audioCtx.destination); o.start(audioCtx.currentTime + at); o.stop(audioCtx.currentTime + at + len);
    });
  } catch { /* audio blocked – visual feedback still shows */ }
}

// ───────────────────────── camera ─────────────────────────
const Cam = {
  stream: null,
  async start(video) {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('Camera needs HTTPS or localhost. Open http://localhost:8000');
    this.stop();
    try { this.stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user', width: { ideal: 960 }, height: { ideal: 720 } }, audio: false }); }
    catch (e) { throw new Error(e.name === 'NotAllowedError' ? 'Camera permission denied – allow it in the browser address bar' : 'No camera available'); }
    video.srcObject = this.stream; await video.play();
  },
  stop() { this.stream?.getTracks().forEach((t) => t.stop()); this.stream = null; },
  grab(video, maxW = 640, q = 0.85) {   // raw (un-mirrored) frame as JPEG data-URL
    const c = document.createElement('canvas'), r = Math.min(1, maxW / video.videoWidth);
    c.width = video.videoWidth * r; c.height = video.videoHeight * r;
    c.getContext('2d').drawImage(video, 0, 0, c.width, c.height);
    return c.toDataURL('image/jpeg', q);
  },
};
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// Face attendance modal -------------------------------------------------
const cam = { open: false, busy: false };
async function openCamModal() {
  $('#camModal').classList.remove('hidden'); cam.open = true;
  setCamStatus('Starting camera…'); $('#camScan').disabled = true; $('#camStage').className = 'cam-stage';
  try { await Cam.start($('#camVideo')); setCamStatus('Position your face inside the oval, then press Scan Face'); $('#camScan').disabled = false; }
  catch (e) { setCamStatus(e.message, 'fail'); }
}
function closeCamModal() { cam.open = false; Cam.stop(); $('#camModal').classList.add('hidden'); }
function setCamStatus(msg, tone = '') { const s = $('#camStatus'); s.textContent = msg; s.className = `cam-status ${tone}`; }
async function scanFace() {
  if (cam.busy) return; cam.busy = true; $('#camScan').disabled = true;
  const stage = $('#camStage'), v = $('#camVideo');
  stage.className = 'cam-stage scanning'; setCamStatus('Scanning… hold still');
  try {
    const frames = []; for (let i = 0; i < 3; i++) { frames.push(Cam.grab(v)); await wait(350); }
    const r = await api('/api/attendance/verify', { method: 'POST', body: { frames, action: $('#camAction').value } });
    stage.className = `cam-stage ${r.verified ? 'ok' : 'fail'}`; setCamStatus(r.message, r.verified ? 'ok' : 'fail'); beep(r.verified);
    if (r.verified) { setTimeout(() => { if (cam.open) closeCamModal(); if (state.role) render(); }, 2200); }
  } catch (e) { stage.className = 'cam-stage fail'; setCamStatus(e.message, 'fail'); beep(false); }
  finally { cam.busy = false; if (cam.open) setTimeout(() => { $('#camScan').disabled = false; if (!stage.classList.contains('ok')) stage.className = 'cam-stage'; }, 1500); }
}
$('#scanBtn').addEventListener('click', openCamModal);
$('#camX').addEventListener('click', closeCamModal);
$('#camScan').addEventListener('click', scanFace);

// ───────────────────────── generic form modal ─────────────────────────
function fld(name, label, o = {}) {
  const { type = 'text', value = '', req = true, full = false, opts, step, min, ph = '', rows = 2 } = o;
  const attrs = `name="${name}" id="f_${name}" ${req ? 'required' : ''} ${step ? `step="${step}"` : ''} ${min != null ? `min="${min}"` : ''} placeholder="${esc(ph)}"`;
  let input;
  if (opts) input = `<select class="text-input" ${attrs}>${opts.map((x) => { const [v, l] = Array.isArray(x) ? x : [x, pretty(x)]; return `<option value="${esc(v)}" ${String(v) === String(value) ? 'selected' : ''}>${esc(l)}</option>`; }).join('')}</select>`;
  else if (type === 'textarea') input = `<textarea class="text-input" rows="${rows}" ${attrs}>${esc(value)}</textarea>`;
  else input = `<input class="text-input" type="${type}" value="${esc(value)}" ${attrs} ${type === 'number' ? 'inputmode="decimal"' : ''}>`;
  return `<div class="${full ? 'full' : ''}"><label class="field-label" for="f_${name}">${esc(label)}</label>${input}</div>`;
}
let formCtx = null;
function openForm({ eyebrow, title, html, submit = 'Save', wide = false, onOpen, onClose, onSubmit }) {
  formCtx = { onClose, onSubmit };
  $('#formEyebrow').textContent = eyebrow; $('#formTitle').textContent = title; $('#formBody').innerHTML = html;
  $('#formSubmit').textContent = submit; $('#formSubmit').classList.toggle('hidden', !onSubmit); $('#formBox').classList.toggle('wide', wide);
  $('#formModal').classList.remove('hidden'); onOpen?.($('#formEl'));
}
function closeForm() { formCtx?.onClose?.(); formCtx = null; Cam.stop(); $('#formModal').classList.add('hidden'); }
$('#formX').addEventListener('click', closeForm); $('#formCancel').addEventListener('click', closeForm);
$('#formEl').addEventListener('submit', async (ev) => {
  ev.preventDefault(); const form = ev.target;
  if (!form.reportValidity()) return;
  const btn = $('#formSubmit'), old = btn.textContent; btn.disabled = true; btn.textContent = 'Saving…';
  try { const done = await formCtx.onSubmit(Object.fromEntries(new FormData(form)), form); if (done !== false) { closeForm(); render(); } }
  catch (e) { showToast(e.message, true); }
  finally { btn.disabled = false; btn.textContent = old; }
});
const n = (v) => (v === '' || v == null ? 0 : parseFloat(v));
const nz = (v) => (v === '' || v == null ? null : parseFloat(v));

// ───────────────────────── layout / navigation ─────────────────────────
function renderNav() {
  $('#nav').innerHTML = NAV.filter((x) => x[3] === 'both' || state.role === 'owner').map(([id, ic, label]) =>
    `<button class="nav-item ${state.page === id ? 'active' : ''}" data-page="${id}"><span>${ic}</span> ${label}</button>`).join('');
}
function setHeader() {
  $('#pageEyebrow').textContent = state.role === 'owner' ? 'OWNER CONTROL CENTRE' : 'EMPLOYEE OPERATIONS';
  $('#pageTitle').textContent = state.role === 'employee' && state.page === 'overview' ? 'My Shift' : TITLES[state.page];
  $('#userName').textContent = state.role === 'owner' ? 'Station Owner' : 'Shift Staff';
  $('#userRole').textContent = state.role === 'owner' ? 'Owner' : 'Employee';
  
  renderNav();
}
let renderSeq = 0;
async function render() {
  if (!state.role) return;
  const seq = ++renderSeq; setHeader();
  const pages = { overview, sales, inventory, staff, equipment, maintenance, compliance, audit };
  try {
    const html = await (state.role === 'employee' && state.page === 'overview' ? employeeHome : pages[state.page])();
    if (seq === renderSeq) $('#content').innerHTML = html;
    if (state.role === 'owner') api('/api/alerts').then((a) => { $('#alertCount').textContent = a.filter((x) => x.severity !== 'info').length; }).catch(() => {});
  } catch (e) {
    if (seq === renderSeq) $('#content').innerHTML = `<div class="banner"><strong>Could not load data.</strong> ${esc(e.message)}</div>`;
  }
}
const pageHead = (title, sub, actions = '') => `<div class="page-head"><div><h2>${title}</h2><p>${sub}</p></div><div class="btn-row">${actions}</div></div>`;
const kpi = (label, value, sub = '', tone = 'good') => `<div class="card kpi"><span class="label">${label}</span><div class="value">${value}</div><span class="delta ${tone}">${sub}</span></div>`;
const alertRow = (sev, title, body) => `<div class="alert"><div class="alert-icon ${sev}">${{ critical: '⚠', warning: '!', info: 'i' }[sev] || '!'}</div><div><h4>${esc(title)}</h4><p>${esc(body)}</p></div></div>`;
const table = (heads, rows, empty = 'Nothing to show yet') => rows.length
  ? `<div class="table-wrap"><table><thead><tr>${heads.map((h) => `<th class="${h.startsWith('#') ? 'num' : ''}">${h.replace(/^#/, '')}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`
  : `<div class="empty">${empty}</div>`;

// ───────────────────────── 6. Overview (owner command centre) ─────────────────────────
async function overview() {
  const [d, alerts] = await Promise.all([api('/api/dashboard'), api('/api/alerts')]);
  const max = Math.max(...d.last7.map((x) => x.collections), 1);
  const varTone = d.variance_today < -100 ? 'bad' : 'good';
  return `
    ${pageHead(`Good ${new Date().getHours() < 12 ? 'morning' : new Date().getHours() < 17 ? 'afternoon' : 'evening'}, Owner 👋`,
      `${dd(d.day)} • ${d.open_shift ? `${pretty(d.open_shift.shift_name)} shift open since ${tm(d.open_shift.opened_at)}` : 'No shift open'}`,
      `<button class="btn primary" data-act="${d.open_shift ? 'closeShift' : 'openShift'}">${d.open_shift ? '✓ Close current shift' : '▶ Open shift'}</button>`)}
    <div class="kpis">
      ${kpi("TODAY'S FUEL SOLD", `${num(d.total_fuel_litres)} L`, `Petrol ${num(d.fuel_sold.PETROL)} • Diesel ${num(d.fuel_sold.DIESEL)}`, 'blue')}
      ${kpi('CNG SOLD', `${num(d.cng_kg)} kg`, 'closed shifts today', 'blue')}
      ${kpi('GROSS COLLECTIONS', money(d.gross_collections), `Expected ${money(d.expected_revenue)} • variance ${money(d.variance_today)}`, varTone)}
      ${kpi('CASH vs DIGITAL', `${d.cash_pct}% / ${d.digital_pct}%`, `Cash ${money(d.cash)} • UPI ${money(d.upi)} • Card ${money(d.card)}`, 'blue')}
    </div>
    <div class="kpis">
      ${kpi('OUTSTANDING CREDIT', money(d.outstanding_receivables), 'Fleet / credit receivables', d.outstanding_receivables ? 'warn' : 'good')}
      ${kpi('STAFF ON DUTY', d.active_staff_count, d.active_staff.map((s) => esc(s.name.split(' ')[0])).join(', ') || 'Nobody checked in', d.active_staff_count ? 'good' : 'warn')}
      ${kpi('OPEN TICKETS', d.open_tickets, `${d.critical_tickets} high / critical`, d.critical_tickets ? 'bad' : 'good')}
      ${kpi('ACTIVE ALERTS', d.alert_count, 'See Audit & Alerts', d.alert_count ? 'warn' : 'good')}
    </div>
    <div class="grid-2">
      <div class="card panel"><div class="panel-head"><h3>Collections – last 7 days</h3><span>Closed shifts</span></div>
        <div class="chart">${d.last7.map((x) => `<div class="bar-wrap"><span class="bar-value">${x.collections ? '₹' + num(x.collections / 1000) + 'k' : ''}</span><div class="bar" style="height:${Math.max(x.collections / max * 100, 2)}%"></div><span class="bar-label">${new Date(x.day + 'T00:00').toLocaleDateString('en-IN', { weekday: 'short' })}</span></div>`).join('')}</div>
      </div>
      <div class="card panel"><div class="panel-head"><h3>Live exceptions</h3><span>${alerts.length} open</span></div>
        ${alerts.slice(0, 5).map((a) => alertRow(a.severity, a.title, a.detail)).join('') || '<div class="empty">All clear ✓</div>'}</div>
    </div>
    <div class="grid-even section-gap">
      <div class="card panel"><div class="panel-head"><h3>Tank stock</h3><span>From latest dip</span></div>${d.tank_status.map((t) => `
        <div class="metric-row"><span>${esc(t.code)} • ${pretty(t.product)}</span><strong>${t.stock == null ? 'No dip yet' : `${num(t.stock)} ${u(t.unit)} ${badge(toneOf[t.status], pretty(t.status))}`}</strong></div>
        <div class="progress ${t.status === 'DRY_RISK' ? 'red' : t.status === 'LOW' ? 'amber' : ''}"><i style="width:${t.fill_pct ?? 0}%"></i></div>`).join('')}</div>
      <div class="card panel"><div class="panel-head"><h3>On duty now</h3><span>Face-verified</span></div>
        ${d.active_staff.map((s) => `<div class="metric-row"><span>${esc(s.name)} <small class="hint">${esc(s.role)}</small></span><span>${badge(toneOf[s.status], 'since ' + tm(s.since))}</span></div>`).join('') || '<div class="empty">No staff checked in</div>'}</div>
    </div>`;
}

async function employeeHome() {
  const [open, tanks, active, tickets] = await Promise.all([api('/api/shifts?status=OPEN'), api('/api/tanks'), api('/api/attendance/active'), api('/api/tickets')]);
  const d = { open_shift: open[0] || null, active_staff_count: active.length, open_tickets: tickets.filter((t) => t.status !== 'RESOLVED').length };
  return `${pageHead('Shift Operations', 'Everything needed to run the shift and leave an auditable trail.', `<button class="btn primary" data-act="${d.open_shift ? 'closeShift' : 'openShift'}">${d.open_shift ? '✓ Submit shift closing' : '▶ Open shift'}</button>`)}
    <div class="kpis">${kpi('SHIFT', d.open_shift ? pretty(d.open_shift.shift_name) : 'None', d.open_shift ? 'Open since ' + tm(d.open_shift.opened_at) : 'Open a shift to begin', d.open_shift ? 'good' : 'warn')}
      ${kpi('STAFF ON DUTY', d.active_staff_count, 'Face-verified')}${kpi('OPEN TICKETS', d.open_tickets, 'Equipment issues', d.open_tickets ? 'warn' : 'good')}${kpi('TANKS NEEDING ATTENTION', tanks.filter((t) => ['LOW', 'DRY_RISK'].includes(t.status) || t.water_alert).length, 'Low stock / water', 'warn')}</div>
    <div class="card panel"><div class="panel-head"><h3>Quick entry</h3><span>Creates an audit trail</span></div><div class="feature-grid">
      ${[['📷', 'Face attendance', 'Shift in / out by face scan', 'scan'], ['◫', 'Record tank dip', 'Opening / closing / water dip', 'recordDip'], ['🚚', 'Log tanker delivery', 'Invoice vs received', 'logDelivery'], ['⚠', 'Raise breakdown ticket', 'Nozzle, hose, compressor…', 'newTicket']]
        .map(([i, t, b, a]) => `<div class="feature"><div class="feature-icon">${i}</div><h4>${t}</h4><p>${b}</p><button class="btn secondary sm section-gap" data-act="${a}">Open</button></div>`).join('')}</div></div>`;
}

// ───────────────────────── 2. Shifts & reconciliation ─────────────────────────
async function sales() {
  if (state.role !== 'owner') {            // employees: shift entry only, no revenue history
    const open = (await api('/api/shifts?status=OPEN'))[0];
    return `${pageHead('Shift Entry', 'Open the shift at start and enter closing readings and collections at the end.', `<button class="btn primary" data-act="${open ? 'closeShift' : 'openShift'}">${open ? '✓ Close shift' : '▶ Open shift'}</button>`)}
      ${open ? `<div class="card panel"><div class="panel-head"><h3>${pretty(open.shift_name)} shift open</h3><span>since ${tm(open.opened_at)}</span></div>${table(['Nozzle', 'Product', '#Opening'], open.readings.map((r) => `<tr><td>${esc(r.nozzle_code)}</td><td>${pretty(r.product)}</td><td class="num">${num(r.opening, 2)}</td></tr>`))}</div>` : '<div class="card empty">No shift is open</div>'}`;
  }
  const [shifts, rates, credit] = await Promise.all([api('/api/shifts?limit=15'), api('/api/rates'), api('/api/credit/customers')]);
  const open = shifts.find((s) => s.status === 'OPEN');
  return `
    ${pageHead('Shift & Payment Reconciliation', 'Totaliser litres × rate = expected revenue, compared with cash, UPI, card and credit collected.',
      `<button class="btn secondary" data-act="setRates">Update rates</button><button class="btn primary" data-act="${open ? 'closeShift' : 'openShift'}">${open ? '✓ Close shift' : '▶ Open shift'}</button>`)}
    <div class="kpis">${['PETROL', 'DIESEL', 'CNG'].map((p) => kpi(`${pretty(p).toUpperCase()} RATE`, `₹${num(rates[p], 2)}`, `per ${unit(p)}`, 'blue')).join('')}
      ${kpi('CURRENT SHIFT', open ? pretty(open.shift_name) : 'None', open ? 'Open since ' + tm(open.opened_at) : 'No shift open', open ? 'good' : 'warn')}</div>
    ${open ? `<div class="card panel section-gap"><div class="panel-head"><h3>Opening totaliser readings</h3><span>Shift #${open.id}</span></div>${table(['Nozzle', 'Product', '#Opening', 'Rate', 'Check'],
      open.readings.map((r) => `<tr><td>${esc(r.nozzle_code)}</td><td>${pretty(r.product)}</td><td class="num">${num(r.opening, 2)}</td><td class="num">₹${num(r.rate, 2)}</td><td>${r.opening_mismatch ? badge('red', 'Mismatch') : badge('green', 'OK')}</td></tr>`))}</div>` : ''}
    <div class="card panel section-gap"><div class="panel-head"><h3>Shift history</h3><span>Variance = collected − expected</span></div>
      ${table(['Shift', 'Date', '#Expected', '#Collected', '#Variance', 'Status', ''], shifts.filter((s) => s.status === 'CLOSED').map((s) => `<tr><td>${pretty(s.shift_name)} #${s.id}</td><td>${dd(s.day)}</td><td class="num">${money(s.expected_revenue)}</td><td class="num">${money(s.total_collections)}</td>
        <td class="num ${s.variance < -s.tolerance ? 'bad' : ''}"><strong>${s.variance >= 0 ? '+' : '−'}${money(Math.abs(s.variance))}</strong></td>
        <td>${badge(s.severity === 'critical' ? 'red' : s.severity === 'warning' ? 'amber' : 'green', s.variance_status)}</td><td><button class="btn secondary sm" data-act="viewShift" data-id="${s.id}">Details</button></td></tr>`), 'No closed shifts yet')}</div>
    <div class="card panel section-gap"><div class="panel-head"><h3>Outstanding fleet / credit receivables</h3><span>${money(credit.reduce((a, c) => a + c.outstanding, 0))} total</span></div>
      ${table(['Customer', '#Vouchers', '#Outstanding', 'Oldest', ''], credit.map((c) => `<tr><td>${esc(c.customer_name)}</td><td class="num">${c.vouchers}</td><td class="num">${money(c.outstanding)}</td><td>${dd(c.oldest)}</td><td><button class="btn secondary sm" data-act="settle" data-name="${esc(c.customer_name)}">Mark settled</button></td></tr>`), 'No outstanding credit')}</div>`;
}

const actions = {};
actions.setRates = async () => {
  const r = await api('/api/rates');
  openForm({ eyebrow: 'FUEL RATES', title: 'Update retail rates', submit: 'Save rates',
    html: `<div class="form-grid">${['PETROL', 'DIESEL', 'CNG'].map((p) => fld(p, `${pretty(p)} (₹ per ${unit(p)})`, { type: 'number', step: '0.01', value: r[p] ?? '' })).join('')}</div><p class="hint">New rates apply from the next shift opened.</p>`,
    onSubmit: async (v) => { for (const p of ['PETROL', 'DIESEL', 'CNG']) if (n(v[p]) !== r[p]) await api('/api/rates', { method: 'POST', body: { product: p, rate: n(v[p]) } }); showToast('Rates updated'); } });
};
actions.openShift = async () => {
  const [nozzles, staffList] = await Promise.all([api('/api/nozzles'), api('/api/staff')]);
  openForm({ eyebrow: 'SHIFT START', title: 'Open shift', submit: 'Open shift', wide: true,
    html: `<div class="form-grid">${fld('shift_name', 'Shift', { opts: ['MORNING', 'EVENING', 'NIGHT', 'GENERAL'] })}
      ${fld('manager_id', 'Shift manager', { req: false, opts: [['', '— none —'], ...staffList.map((s) => [s.id, `${s.name} (${s.role})`])] })}</div>
      <label class="field-label">Opening totaliser readings</label><p class="hint">Pre-filled from the previous shift's closing. Change only if the meter shows a different value – a mismatch is flagged to the owner.</p>
      <div class="form-grid">${nozzles.map((z) => fld('o_' + z.code, `${z.code} – ${pretty(z.product)} (${z.unit})`, { type: 'number', step: '0.01', value: z.last_closing ?? '', ph: z.last_closing == null ? 'Enter current reading' : '' })).join('')}</div>`,
    onSubmit: async (v) => {
      const openings = {}; nozzles.forEach((z) => { if (v['o_' + z.code] !== '') openings[z.code] = n(v['o_' + z.code]); });
      const r = await api('/api/shifts', { method: 'POST', body: { shift_name: v.shift_name, manager_id: v.manager_id ? +v.manager_id : null, openings } });
      showToast(r.warnings.length ? r.warnings[0] : 'Shift opened', !!r.warnings.length);
    } });
};
actions.closeShift = async () => {
  const sh = (await api('/api/shifts?status=OPEN'))[0];
  if (!sh) return showToast('No open shift', true);
  const rows = sh.readings.map((r) => `<tr data-nozzle="${r.nozzle_code}" data-open="${r.opening}" data-rate="${r.rate}">
      <td>${esc(r.nozzle_code)}<br><small class="hint">${pretty(r.product)} @ ₹${num(r.rate, 2)}</small></td><td class="num">${num(r.opening, 2)}</td>
      <td><input class="text-input" type="number" step="0.01" min="0" name="c_${r.nozzle_code}" required inputmode="decimal" style="min-width:110px"></td>
      <td><input class="text-input" type="number" step="0.1" min="0" name="t_${r.nozzle_code}" value="${r.unit === 'L' ? 5 : 0}" style="width:70px"></td><td class="num" data-sold>–</td></tr>`).join('');
  openForm({ eyebrow: `SHIFT #${sh.id} CLOSING`, title: 'Close shift & reconcile', submit: 'Close shift', wide: true,
    html: `<label class="field-label">Closing totaliser readings</label>
      <div class="table-wrap"><table><thead><tr><th>Nozzle</th><th class="num">Opening</th><th>Closing</th><th>Test (L)</th><th class="num">Sold</th></tr></thead><tbody>${rows}</tbody></table></div>
      <label class="field-label">Collections</label>
      <div class="form-grid">${fld('cash', 'Cash received (₹)', { type: 'number', step: '0.01', min: 0, value: 0 })}${fld('upi', 'UPI transactions (₹)', { type: 'number', step: '0.01', min: 0, value: 0 })}
        ${fld('card', 'POS / card slips (₹)', { type: 'number', step: '0.01', min: 0, value: 0 })}${fld('credit', 'Credit (only if no vouchers listed)', { type: 'number', step: '0.01', min: 0, value: 0, req: false })}</div>
      <label class="field-label">Fleet / credit vouchers</label><div id="vouchers"></div>
      <button type="button" class="btn secondary sm" id="addVoucher">+ Add voucher</button>
      <div class="variance-box section-gap" id="varBox"><div class="hint">Expected revenue</div><strong id="vExp">₹0</strong> &nbsp;•&nbsp; <span class="hint">Collected</span> <strong id="vCol">₹0</strong>
        <div class="variance" id="vVar">₹0</div><div class="hint" id="vMsg">Enter closing readings and collections to see the variance live.</div></div>
      ${fld('notes', 'Notes', { type: 'textarea', req: false, full: true })}`,
    onOpen: (form) => {
      const calc = () => {
        let exp = 0;
        form.querySelectorAll('tr[data-nozzle]').forEach((tr) => {
          const c = form.elements['c_' + tr.dataset.nozzle].value, t = n(form.elements['t_' + tr.dataset.nozzle].value);
          const sold = c === '' ? null : parseFloat(c) - parseFloat(tr.dataset.open) - t;
          tr.querySelector('[data-sold]').innerHTML = sold == null ? '–' : sold < 0 ? '<span class="bad">invalid</span>' : num(sold, 2);
          if (sold > 0) exp += sold * parseFloat(tr.dataset.rate);
        });
        const vsum = [...form.querySelectorAll('[data-vamt]')].reduce((a, i) => a + n(i.value), 0);
        const col = n(form.elements.cash.value) + n(form.elements.upi.value) + n(form.elements.card.value) + (vsum || n(form.elements.credit.value));
        const v = col - exp, tol = sh.tolerance, box = $('#varBox');
        $('#vExp').textContent = money(exp, 2); $('#vCol').textContent = money(col, 2);
        $('#vVar').textContent = `${v >= 0 ? '+' : '−'}${money(Math.abs(v), 2)}`;
        const cls = Math.abs(v) <= tol ? 'ok' : v < -5 * tol ? 'critical' : 'warning';
        box.className = `variance-box section-gap ${cls}`;
        $('#vMsg').textContent = cls === 'ok' ? 'Within tolerance' : v < 0 ? `⚠ Cash SHORT by ${money(-v, 2)} (tolerance ${money(tol)})` : `Over by ${money(v, 2)} – check for unrecorded sales or entry errors`;
      };
      form.addEventListener('input', calc);
      $('#addVoucher').addEventListener('click', () => {
        const d = document.createElement('div'); d.className = 'form-grid';
        d.innerHTML = `<div><input class="text-input" name="vc" placeholder="Customer / fleet" required></div><div><input class="text-input" name="vv" placeholder="Vehicle no."></div>
          <div><input class="text-input" name="vn" placeholder="Voucher no."></div><div><input class="text-input" data-vamt name="va" type="number" step="0.01" min="0.01" placeholder="Amount ₹" required></div>`;
        $('#vouchers').append(d); calc();
      });
    },
    onSubmit: async (v, form) => {
      const body = { readings: sh.readings.map((r) => ({ nozzle_code: r.nozzle_code, closing: n(v['c_' + r.nozzle_code]), test_litres: n(v['t_' + r.nozzle_code]) })),
        cash: n(v.cash), upi: n(v.upi), card: n(v.card), credit: n(v.credit), notes: v.notes || null,
        vouchers: [...form.querySelectorAll('#vouchers .form-grid')].map((g) => ({ customer_name: g.querySelector('[name=vc]').value, vehicle_no: g.querySelector('[name=vv]').value || null, voucher_no: g.querySelector('[name=vn]').value || null, amount: n(g.querySelector('[name=va]').value) })) };
      const r = await api(`/api/shifts/${sh.id}/close`, { method: 'POST', body });
      closeForm(); showShiftResult(r); render(); return false;
    } });
};
async function showShiftResult(s) {
  const tone = s.severity === 'critical' ? 'critical' : s.severity === 'warning' ? 'warning' : 'ok';
  const rep = await api(`/api/shifts/${s.id}/report`); state.lastReport = rep.text;
  beep(tone === 'ok');
  $('#closeSummary').innerHTML = `<div class="variance-box ${tone}" style="margin-bottom:10px"><div class="hint">${pretty(s.variance_status)} • variance vs expected sales</div><div class="variance">${s.variance >= 0 ? '+' : '−'}${money(Math.abs(s.variance), 2)}</div>
    ${tone === 'critical' ? '<strong class="bad">⚠ Cash shortage exceeds tolerance – investigate before handover</strong>' : ''}</div>
    <div class="summary-grid"><div><span>Expected</span><strong>${money(s.expected_revenue)}</strong></div><div><span>Collected</span><strong>${money(s.total_collections)}</strong></div>
    <div><span>Cash / UPI</span><strong>${money(s.cash_amount)} / ${money(s.upi_amount)}</strong></div><div><span>Card / Credit</span><strong>${money(s.card_amount)} / ${money(s.credit_amount)}</strong></div></div>
    ${s.warnings.map((w) => `<p class="warn hint">⚠ ${esc(w)}</p>`).join('')}`;
  $('#closeModal').classList.remove('hidden');
}
actions.viewShift = async ({ id }) => {
  const [s, rec] = await Promise.all([api(`/api/shifts/${id}`), api(`/api/shifts/${id}/stock-reconciliation`)]);
  openForm({ eyebrow: `SHIFT #${s.id}`, title: `${pretty(s.shift_name)} • ${dd(s.day)}`, wide: true,
    html: `<div class="variance-box ${s.severity || 'ok'}"><span class="hint">Variance</span><div class="variance">${s.variance >= 0 ? '+' : '−'}${money(Math.abs(s.variance), 2)}</div><span class="hint">Manager: ${esc(s.manager_name || '–')} • ${dt(s.opened_at)} → ${dt(s.closed_at)}</span></div>
      <h3>Meter readings</h3>${table(['Nozzle', '#Opening', '#Closing', '#Test', '#Sold', '#Amount'], s.readings.map((r) => `<tr><td>${esc(r.nozzle_code)} ${r.opening_mismatch ? badge('red', 'opening mismatch') : ''}</td><td class="num">${num(r.opening, 2)}</td><td class="num">${num(r.closing, 2)}</td><td class="num">${num(r.test_litres, 1)}</td><td class="num">${num(r.sold_qty, 2)} ${unit(r.product)}</td><td class="num">${money(r.expected_amount, 2)}</td></tr>`))}
      <h3>Dip-based stock reconciliation</h3>${table(['Product', '#Opening dip', '#Receipts', '#Metered sales', '#Book closing', '#Closing dip', '#Variance', 'Result'], rec.map((r) => `<tr><td>${pretty(r.product)}</td><td class="num">${num(r.opening_dip)}</td><td class="num">${num(r.receipts)}</td><td class="num">${num(r.metered_sales)}</td><td class="num">${num(r.book_closing)}</td><td class="num">${num(r.closing_dip)}</td><td class="num">${r.variance == null ? '–' : num(r.variance, 1)}</td><td>${badge(r.status === 'OK' ? 'green' : r.status === 'INCOMPLETE' ? 'grey' : 'red', r.status === 'INCOMPLETE' ? 'Needs opening+closing dips' : r.status)}</td></tr>`))}`,
  });
  $('#formSubmit').classList.add('hidden');
};
actions.settle = async ({ name }) => { if (confirm(`Mark all vouchers of ${name} as settled?`)) { await api(`/api/credit/settle?customer_name=${encodeURIComponent(name)}`, { method: 'POST' }); showToast('Settled'); render(); } };

// ───────────────────────── 3. Tanks, dips & deliveries ─────────────────────────
async function inventory() {
  const [tanks, deliveries, dips] = await Promise.all([api('/api/tanks'), api('/api/deliveries?limit=10'), api('/api/dips?limit=12')]);
  return `${pageHead('Tank Inventory', 'Dip-based stock, water checks, tanker receipts and dry-out forecasting.',
    '<button class="btn secondary" data-act="logDelivery">🚚 Log tanker delivery</button><button class="btn primary" data-act="recordDip">+ Record dip</button>')}
    <div class="cards-auto">${tanks.map((t) => `<div class="card tank"><div class="tank-top"><div><strong>${esc(t.code)}</strong><div class="hint">${pretty(t.product)} • cap ${num(t.capacity)} ${u(t.unit)}</div></div>${badge(toneOf[t.status], pretty(t.status))}</div>
      <div class="variance" style="font-size:22px;margin-top:10px">${t.stock == null ? '—' : num(t.stock)} <small class="hint">${u(t.unit)}</small></div>
      <div class="progress tank-gauge ${t.status === 'DRY_RISK' ? 'red' : t.status === 'LOW' ? 'amber' : ''}"><i style="width:${t.fill_pct ?? 0}%"></i><span class="min" style="left:${Math.min(t.min_stock / t.capacity * 100, 100)}%" title="Minimum stock"></span></div>
      <div class="metric-row"><span>Avg daily sales</span><strong>${t.avg_daily_sales == null ? '–' : num(t.avg_daily_sales) + ' ' + u(t.unit)}</strong></div>
      <div class="metric-row"><span>Days of stock left</span><strong class="${t.days_left != null && t.days_left < 2 ? 'bad' : ''}">${t.days_left ?? '–'}</strong></div>
      <div class="metric-row"><span>Reorder at</span><strong>${num(t.reorder_level)} ${u(t.unit)}</strong></div>
      ${t.kind === 'UNDERGROUND' ? `<div class="metric-row"><span>Water</span><strong>${num(t.water_mm)} mm ${t.water_alert ? badge(t.water_alert === 'CRITICAL' ? 'red' : 'amber', t.water_alert) : ''}</strong></div>` : ''}
      <div class="hint">Last dip: ${dt(t.last_dip_at)}</div></div>`).join('')}</div>
    <div class="card panel section-gap"><div class="panel-head"><h3>Tanker delivery log</h3><span>Received = (post − pre dip volume) + sales during unloading</span></div>
      ${table(['Date', 'Invoice', 'Tank', '#Invoiced', '#Received', '#Variance', 'Status'], deliveries.map((d) => `<tr><td>${dt(d.ts)}</td><td>${esc(d.invoice_no)}<br><small class="hint">${esc(d.supplier || '')} ${esc(d.vehicle_no || '')}</small></td><td>${esc(d.tank_code)}</td>
        <td class="num">${num(d.invoiced_qty)} L</td><td class="num">${num(d.received_qty)} L</td><td class="num ${d.variance_qty < 0 ? 'bad' : ''}">${num(d.variance_qty, 1)} (${d.variance_pct}%)</td>
        <td>${badge(d.status === 'OK' ? 'green' : 'red', pretty(d.status))}${d.observed_density ? `<br><small class="hint">density ${d.observed_density} / inv ${d.invoice_density ?? '–'}</small>` : ''}</td></tr>`), 'No deliveries logged')}</div>
    <div class="card panel section-gap"><div class="panel-head"><h3>Recent dip readings</h3></div>
      ${table(['Time', 'Tank', 'Type', '#Dip (mm)', '#Volume', '#Water (mm)'], dips.map((d) => `<tr><td>${dt(d.ts)}</td><td>${esc(d.tank_code)}</td><td>${pretty(d.reading_type)}</td><td class="num">${num(d.dip_mm, 1)}</td><td class="num">${num(d.volume)} ${unit(d.product)}</td><td class="num">${num(d.water_mm, 1)} ${d.water_alert ? badge(d.water_alert === 'CRITICAL' ? 'red' : 'amber', d.water_alert) : ''}</td></tr>`), 'No dips recorded')}</div>`;
}
actions.recordDip = async () => {
  const [tanks, staffList] = await Promise.all([api('/api/tanks'), api('/api/staff')]);
  openForm({ eyebrow: 'TANK DIP', title: 'Record dip reading', submit: 'Save dip',
    html: `<div class="form-grid">${fld('tank_code', 'Tank', { opts: tanks.map((t) => [t.code, `${t.code} – ${pretty(t.product)}`]) })}${fld('reading_type', 'Reading type', { opts: [['OPENING', 'Opening dip'], ['CLOSING', 'Closing dip'], ['ADHOC', 'Ad-hoc check']] })}
      ${fld('dip_mm', 'Product dip (mm) – CNG: quantity in kg', { type: 'number', step: '0.1', min: 0 })}${fld('water_mm', 'Water dip (mm)', { type: 'number', step: '0.1', min: 0, value: 0 })}
      ${fld('by', 'Recorded by', { req: false, opts: [['', '—'], ...staffList.map((s) => [s.id, s.name])], full: true })}</div>
      <p class="hint">Opening/closing dips are linked to the open shift for stock reconciliation. Water ≥ 10 mm warns, ≥ 25 mm is critical.</p>`,
    onSubmit: async (v) => { const r = await api('/api/dips', { method: 'POST', body: { tank_code: v.tank_code, reading_type: v.reading_type, dip_mm: n(v.dip_mm), water_mm: n(v.water_mm), recorded_by_id: v.by ? +v.by : null } });
      showToast(`Volume ${num(r.volume)} ${unit(r.product)}${r.water_alert ? ' – WATER ' + r.water_alert : ''}`, !!r.water_alert); } });
};
actions.logDelivery = async () => {
  const tanks = (await api('/api/tanks')).filter((t) => t.kind === 'UNDERGROUND');
  openForm({ eyebrow: 'TANKER UNLOADING', title: 'Log delivery', submit: 'Save delivery', wide: true,
    html: `<div class="form-grid">${fld('tank_code', 'Receiving tank', { opts: tanks.map((t) => [t.code, `${t.code} – ${pretty(t.product)}`]) })}${fld('invoice_no', 'Invoice / challan no.')}
      ${fld('supplier', 'Supplier / depot', { req: false })}${fld('vehicle_no', 'Tanker vehicle no.', { req: false })}
      ${fld('invoiced_qty', 'Invoiced quantity (L)', { type: 'number', step: '1', min: 1 })}${fld('sales_during', 'Sales during unloading (L)', { type: 'number', step: '0.1', min: 0, value: 0 })}
      ${fld('pre_dip_mm', 'Dip BEFORE unloading (mm)', { type: 'number', step: '0.1', min: 0 })}${fld('post_dip_mm', 'Dip AFTER unloading (mm)', { type: 'number', step: '0.1', min: 0 })}
      ${fld('invoice_density', 'Density on invoice (g/cc)', { type: 'number', step: '0.0001', req: false, ph: '0.745' })}${fld('observed_density', 'Density observed at site', { type: 'number', step: '0.0001', req: false, ph: '0.746' })}
      ${fld('notes', 'Notes (seal numbers, remarks)', { type: 'textarea', req: false, full: true })}</div>`,
    onSubmit: async (v) => { const r = await api('/api/deliveries', { method: 'POST', body: { tank_code: v.tank_code, invoice_no: v.invoice_no, supplier: v.supplier || null, vehicle_no: v.vehicle_no || null, invoiced_qty: n(v.invoiced_qty), pre_dip_mm: n(v.pre_dip_mm), post_dip_mm: n(v.post_dip_mm), sales_during: n(v.sales_during), invoice_density: nz(v.invoice_density), observed_density: nz(v.observed_density), notes: v.notes || null } });
      showToast(r.status === 'OK' ? `Received ${num(r.received_qty)} L – matches invoice` : `⚠ ${pretty(r.status)}: received ${num(r.received_qty)} L vs invoiced ${num(r.invoiced_qty)} L (${r.variance_pct}%)`, r.status !== 'OK'); } });
};

// ───────────────────────── 1. Staff & face attendance ─────────────────────────
async function staff() {
  const [list, att, log, face] = await Promise.all([api('/api/staff'), api('/api/attendance'), state.role === 'owner' ? api('/api/attendance/scan-log?limit=12') : [], api('/api/face/status')]);
  const byStaff = Object.fromEntries(att.map((a) => [a.staff_id, a]));
  return `${pageHead('Employees & Attendance', 'Face-verified shift in / out. Biometric vectors are stored, not photos.',
    `${state.role === 'owner' ? '<button class="btn secondary" data-act="registerStaff">+ Register staff</button>' : ''}<button class="btn primary" data-act="scan">📷 Scan face</button>`)}
    ${face.ready ? (face.secure ? '' : '<div class="banner">Face engine is the insecure <strong>demo</strong> engine. Install face_recognition (dlib) before real use.</div>') : `<div class="banner"><strong>Face engine unavailable.</strong> ${esc(face.error)}</div>`}
    <div class="card panel"><div class="panel-head"><h3>Roster</h3><span>${list.length} registered • engine: ${esc(face.engine || '–')}</span></div>
      ${table(['Code', 'Name', 'Role', 'Shift', 'Nozzles', 'Today', ''], list.map((s) => { const a = byStaff[s.id]; return `<tr><td>${esc(s.employee_code)}</td><td>${esc(s.name)}</td><td>${esc(s.role)}</td><td>${esc(s.shift_start)}–${esc(s.shift_end)}</td><td>${esc(s.assigned_nozzles.join(', ') || '–')}</td>
        <td>${!a ? badge('grey', 'Absent') : a.out_time ? badge('blue', `Out ${tm(a.out_time)}`) : badge(toneOf[a.status], `${a.status === 'LATE' ? 'Late' : 'On duty'} ${tm(a.in_time)}`)}</td>
        <td>${state.role === 'owner' ? `<button class="btn danger sm" data-act="removeStaff" data-id="${s.id}" data-name="${esc(s.name)}">Remove</button>` : ''}</td></tr>`; }), 'No staff yet – register the first employee with a face photo')}</div>
    <div class="grid-even section-gap">
      <div class="card panel"><div class="panel-head"><h3>Today's attendance</h3><span>${att.length} records</span></div>${table(['Name', 'In', 'Out', '#Worked', 'Status'], att.map((a) => `<tr><td>${esc(a.staff_name)}</td><td>${tm(a.in_time)}</td><td>${tm(a.out_time)}</td><td class="num">${a.worked_minutes == null ? '–' : Math.floor(a.worked_minutes / 60) + 'h ' + a.worked_minutes % 60 + 'm'}</td><td>${badge(toneOf[a.status], a.status)}</td></tr>`), 'No scans today')}</div>
      ${state.role === 'owner' ? `<div class="card panel"><div class="panel-head"><h3>Scan audit log</h3><span>Includes failed attempts</span></div>${table(['Time', 'Who', 'Result', '#Distance'], log.map((l) => `<tr><td>${dt(l.ts)}</td><td>${esc(l.staff_name || 'Unknown')}</td><td>${badge(l.success ? 'green' : 'red', l.success ? 'OK' : 'Failed')} <small class="hint">${esc(l.reason)}</small></td><td class="num">${l.distance ?? '–'}</td></tr>`), 'No scans yet')}</div>` : ''}
    </div>`;
}
actions.scan = openCamModal;
actions.removeStaff = async ({ id, name }) => { if (confirm(`Remove ${name}? Their face data is deleted; attendance history is kept.`)) { await api(`/api/staff/${id}`, { method: 'DELETE' }); showToast('Staff removed'); render(); } };
actions.registerStaff = async () => {
  const nozzles = await api('/api/nozzles'); const photos = [];
  const drawThumbs = () => { $('#thumbs').innerHTML = photos.map((p, i) => `<div><img src="${p}" alt="Face photo ${i + 1}"><button type="button" data-rm="${i}" aria-label="Remove photo">×</button></div>`).join(''); if (!$('#photoCount').textContent) $('#photoCount').textContent = `${photos.length} photo(s)`; };
  openForm({ eyebrow: 'FACE REGISTRATION', title: 'Register staff member', submit: 'Register', wide: true,
    html: `<div class="form-grid">${fld('name', 'Full name')}${fld('role', 'Role', { opts: ['Attendant', 'Cashier', 'Shift Manager', 'CNG Operator', 'Supervisor'].map((r) => [r, r]) })}
      ${fld('phone', 'Mobile', { req: false })}${fld('shift_name', 'Shift', { opts: ['MORNING', 'EVENING', 'NIGHT', 'GENERAL'] })}
      ${fld('shift_start', 'Shift starts', { type: 'time', value: '06:00' })}${fld('shift_end', 'Shift ends', { type: 'time', value: '14:00' })}
      <div class="full"><label class="field-label">Assigned nozzles</label><div class="btn-row">${nozzles.map((z) => `<label class="check" style="margin:0"><input type="checkbox" name="nz" value="${z.code}"> ${z.code}</label>`).join('')}</div></div></div>
      <label class="field-label">Face photos</label>
      <div class="cam-stage cam-mini"><video id="regVideo" autoplay playsinline muted></video><div class="cam-guide"></div></div>
      <div class="btn-row section-gap"><button type="button" class="btn secondary" id="capPhoto">📸 Capture photo</button><label class="btn secondary" for="upPhoto">⬆ Upload photo</label><input type="file" id="upPhoto" accept="image/*" multiple class="hidden"></div>
      <div class="thumbs" id="thumbs"></div><p class="hint" id="photoCount"></p>
      <label class="check"><input type="checkbox" name="consent" required> <span>The employee has given consent for their facial biometric data to be stored for attendance (DPDP Act 2023).</span></label>`,
    onOpen: (form) => {
      drawThumbs();
      // ── AUTO-CAPTURE: every ~0.8 s send a frame to /api/face/check; when the server says the face is
      //    clear (1 face, big enough, lit, sharp) that exact frame is kept. Repeats until 3 photos.
      const TARGET = 3, HINTS = ['Look straight at the camera', 'Turn your head slightly LEFT', 'Turn your head slightly RIGHT'];
      const stage = $('#regVideo').parentElement; let alive = true, busy = false;
      formCtx.onClose = () => { alive = false; };
      const say = (msg, good) => { $('#photoCount').textContent = msg; stage.classList.toggle('good', !!good); };
      const tick = async () => {
        if (!alive) return;
        const v = $('#regVideo');
        if (photos.length >= TARGET || !v?.videoWidth || busy) return setTimeout(tick, 800);
        busy = true;
        try {
          const frame = Cam.grab(v, 640, .9);
          const q = await api('/api/face/check', { method: 'POST', body: { frame } });
          if (q.ok && alive) {
            photos.push(frame); drawThumbs(); beep(true);
            say(photos.length >= TARGET ? `✓ ${TARGET} photos captured – fill the details and press Register` : `✓ Photo ${photos.length}/${TARGET} taken. ${HINTS[photos.length]}…`, true);
            busy = false; return setTimeout(tick, 1800);          // pause so the next photo has a different angle
          }
          say(`${q.reason} (photo ${photos.length + 1}/${TARGET})`, false);
        } catch (e) { say(e.message, false); }
        busy = false; setTimeout(tick, 800);
      };
      Cam.start($('#regVideo')).then(() => { say(`${HINTS[0]} – photos are taken automatically`, false); tick(); })
        .catch((e) => showToast(e.message + ' – you can still upload photos', true));
      $('#capPhoto').addEventListener('click', () => { const v = $('#regVideo'); if (!v.videoWidth) return showToast('Camera not ready', true); if (photos.length >= 5) return showToast('Maximum 5 photos', true); photos.push(Cam.grab(v, 800, .92)); drawThumbs(); });
      $('#upPhoto').addEventListener('change', (e) => [...e.target.files].slice(0, 5 - photos.length).forEach((f) => { const r = new FileReader(); r.onload = () => { photos.push(r.result); drawThumbs(); }; r.readAsDataURL(f); }));
      $('#thumbs').addEventListener('click', (e) => { const b = e.target.closest('[data-rm]'); if (b) { photos.splice(+b.dataset.rm, 1); drawThumbs(); } });
    },
    onSubmit: async (v, form) => {
      if (!photos.length) throw new Error('Capture or upload at least one clear face photo');
      const r = await api('/api/staff/register', { method: 'POST', body: { name: v.name, role: v.role, phone: v.phone || null, shift_name: v.shift_name, shift_start: v.shift_start, shift_end: v.shift_end, assigned_nozzles: [...form.querySelectorAll('[name=nz]:checked')].map((c) => c.value), photos, consent: !!v.consent } });
      showToast(r.message); beep(true);
    } });
};

// ───────────────────────── 4. Equipment, tickets & PM ─────────────────────────
async function equipment() {
  const eq = await api('/api/equipment');
  return `${pageHead('Equipment Health', 'Dispensers, compressor and gauges with AMC and preventive-maintenance status.', '<button class="btn secondary" data-act="addEquipment">+ Add equipment</button>')}
    <div class="cards-auto">${eq.map((e) => `<div class="card tank"><div class="tank-top"><div><strong>${esc(e.name)}</strong><div class="hint">${esc(e.code)} • ${pretty(e.category)}</div></div>${badge(toneOf[e.status], pretty(e.status))}</div>
      <div class="metric-row"><span>Next preventive service</span><strong>${e.next_pm_due ? dd(e.next_pm_due) : '–'} ${e.pm_state !== 'OK' ? badge(e.pm_state === 'OVERDUE' ? 'red' : 'amber', pretty(e.pm_state)) : ''}</strong></div>
      <div class="metric-row"><span>AMC (${esc(e.amc_vendor || 'none')})</span><strong>${e.amc_expiry ? dd(e.amc_expiry) : '–'} ${['EXPIRING', 'EXPIRED'].includes(e.amc_state) ? badge(e.amc_state === 'EXPIRED' ? 'red' : 'amber', pretty(e.amc_state)) : ''}</strong></div>
      <div class="metric-row"><span>Open tickets</span><strong>${e.open_tickets}</strong></div>
      <div class="btn-row section-gap"><button class="btn secondary sm" data-act="serviced" data-id="${e.id}">✓ Mark serviced</button><button class="btn danger sm" data-act="newTicket" data-eq="${e.id}">⚠ Report fault</button></div></div>`).join('')}</div>`;
}
actions.addEquipment = () => openForm({ eyebrow: 'EQUIPMENT', title: 'Add equipment', html: `<div class="form-grid">${fld('code', 'Code', { ph: 'DSP-03' })}${fld('name', 'Name')}${fld('category', 'Category', { opts: ['DISPENSER', 'COMPRESSOR', 'ATG', 'OTHER'] })}${fld('pm_interval_days', 'PM interval (days)', { type: 'number', value: 90, min: 1 })}${fld('last_service', 'Last service date', { type: 'date', req: false })}${fld('amc_vendor', 'AMC vendor', { req: false })}${fld('amc_expiry', 'AMC expiry', { type: 'date', req: false })}</div>`,
  onSubmit: async (v) => { await api('/api/equipment', { method: 'POST', body: { code: v.code, name: v.name, category: v.category, pm_interval_days: +v.pm_interval_days, last_service: v.last_service || null, amc_vendor: v.amc_vendor || null, amc_expiry: v.amc_expiry || null } }); showToast('Equipment added'); } });
actions.serviced = async ({ id }) => { await api(`/api/equipment/${id}/service`, { method: 'POST', body: {} }); showToast('Service recorded – next PM date updated'); render(); };

async function maintenance() {
  const [tickets, cal] = await Promise.all([api('/api/tickets'), api('/api/maintenance/calendar')]);
  const flow = ['OPEN', 'TECHNICIAN_ASSIGNED', 'PARTS_REPLACED', 'RESOLVED'];
  const card = (t) => { const i = flow.indexOf(t.status); return `<div class="ticket"><div class="tank-top"><h4>${esc(t.ticket_no)} – ${esc(t.title)}</h4>${badge(toneOf[t.priority] || 'grey', t.priority)}</div>
    <p>${pretty(t.category)} • ${esc(t.equipment_name || 'General')} • open ${num(t.age_hours, 1)} h</p>${t.technician_name ? `<p>👷 ${esc(t.technician_name)} ${esc(t.technician_phone || '')}</p>` : ''}${t.parts_replaced ? `<p>🔩 ${esc(t.parts_replaced)}</p>` : ''}
    <div class="steps">${flow.map((_, k) => `<i class="${k <= i ? 'on' : ''}"></i>`).join('')}</div><p><strong>${pretty(t.status)}</strong></p>
    ${t.status !== 'RESOLVED' ? `<div class="btn-row"><button class="btn primary sm" data-act="advance" data-id="${t.id}" data-to="${flow[i + 1]}" data-parts="${t.parts_required}">→ ${pretty(flow[i + 1])}</button>${i === 1 && !t.parts_required ? `<button class="btn secondary sm" data-act="advance" data-id="${t.id}" data-to="RESOLVED">Resolve (no parts)</button>` : ''}</div>` : ''}</div>`; };
  const open = tickets.filter((t) => t.status !== 'RESOLVED');
  return `${pageHead('Maintenance (CMMS)', 'OPEN → TECHNICIAN ASSIGNED → PARTS REPLACED → RESOLVED', '<button class="btn primary" data-act="newTicket">+ Raise ticket</button>')}
    <div class="panel-head"><h3>Open tickets (${open.length})</h3></div><div class="cards-auto">${open.map(card).join('') || '<div class="card empty">No open tickets 🎉</div>'}</div>
    <div class="card panel section-gap"><div class="panel-head"><h3>Preventive maintenance & AMC calendar</h3><span>Sorted by date</span></div>
      ${table(['Date', 'Type', 'Equipment', 'Status'], cal.map((c) => `<tr><td>${dd(c.date)}</td><td>${c.kind === 'PM' ? 'Preventive service' : 'AMC expiry' + (c.vendor ? ' – ' + esc(c.vendor) : '')}</td><td>${esc(c.equipment)}</td><td>${badge(c.state === 'OK' ? 'green' : c.state === 'DUE_SOON' || c.state === 'EXPIRING' ? 'amber' : 'red', `${pretty(c.state)} (${c.days < 0 ? -c.days + ' d ago' : c.days + ' d'})`)}</td></tr>`))}</div>
    ${tickets.some((t) => t.status === 'RESOLVED') ? `<div class="card panel section-gap"><div class="panel-head"><h3>Recently resolved</h3></div>${table(['Ticket', 'Issue', 'Technician', 'Parts', '#Hours'], tickets.filter((t) => t.status === 'RESOLVED').slice(0, 8).map((t) => `<tr><td>${esc(t.ticket_no)}</td><td>${esc(t.title)}</td><td>${esc(t.technician_name || '–')}</td><td>${esc(t.parts_replaced || '–')}</td><td class="num">${num(t.age_hours, 1)}</td></tr>`))}</div>` : ''}`;
}
actions.newTicket = async (d = {}) => {
  const eq = await api('/api/equipment');
  openForm({ eyebrow: 'BREAKDOWN TICKET', title: 'Raise a ticket', submit: 'Raise ticket',
    html: `<div class="form-grid">${fld('equipment_id', 'Equipment', { opts: [['', '— general —'], ...eq.map((e) => [e.id, e.name])], value: d.eq || '', req: false })}
      ${fld('category', 'Fault type', { opts: ['NOZZLE_LEAK', 'HOSE_BURST', 'SLOW_DISPENSING', 'COMPRESSOR_LOW_PRESSURE', 'TOTALISER_JUMPING', 'DISPLAY_FAULT', 'ELECTRICAL', 'OTHER'] })}
      ${fld('title', 'Short title', { full: true, ph: 'e.g. Nozzle D1-N2 dripping' })}${fld('priority', 'Priority', { opts: ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'], value: 'MEDIUM' })}${fld('reported_by', 'Reported by', { req: false })}
      ${fld('description', 'Details', { type: 'textarea', req: false, full: true })}</div>
      <label class="check"><input type="checkbox" name="parts" checked> Replacement parts likely needed</label><p class="hint">HIGH / CRITICAL tickets mark the equipment as DOWN.</p>`,
    onSubmit: async (v, f) => { const r = await api('/api/tickets', { method: 'POST', body: { equipment_id: v.equipment_id ? +v.equipment_id : null, category: v.category, title: v.title, priority: v.priority, reported_by: v.reported_by || null, description: v.description || null, parts_required: f.elements.parts.checked } }); showToast(`${r.ticket_no} raised`); } });
};
actions.advance = ({ id, to, parts }) => {
  const f = { TECHNICIAN_ASSIGNED: fld('technician_name', 'Technician name') + fld('technician_phone', 'Phone', { req: false }), PARTS_REPLACED: fld('parts_replaced', 'Parts replaced', { full: true, ph: 'e.g. Nozzle assembly, hose 3/4"' }), RESOLVED: fld('note', 'Resolution notes', { type: 'textarea', req: false, full: true }) }[to];
  openForm({ eyebrow: 'TICKET WORKFLOW', title: `Move to ${pretty(to)}`, html: `<div class="form-grid">${f}</div>`, submit: 'Confirm',
    onSubmit: async (v) => { await api(`/api/tickets/${id}/advance`, { method: 'POST', body: { to_status: to, technician_name: v.technician_name || null, technician_phone: v.technician_phone || null, parts_replaced: v.parts_replaced || null, note: v.note || null } }); showToast('Ticket updated'); } });
};

// ───────────────────────── 5. Compliance ─────────────────────────
async function compliance() {
  const docs = await api('/api/compliance');
  return `${pageHead('Statutory Compliance', 'Green: more than 30 days • Yellow: within 30 days • Red: expired', '<button class="btn primary" data-act="addDoc">+ Add document</button>')}
    <div class="card panel">${table(['Document', 'Number', 'Authority', 'Expiry', 'Days left', 'Status', ''], docs.map((c) => `<tr><td>${esc(c.title)}<br><small class="hint">${pretty(c.doc_type)}</small></td><td>${esc(c.number || '–')}</td><td>${esc(c.authority || '–')}</td><td>${dd(c.expiry_date)}</td>
      <td class="num ${c.status === 'RED' ? 'bad' : ''}"><strong>${c.days_left < 0 ? 'Expired ' + -c.days_left + ' d ago' : c.days_left + ' d'}</strong></td><td>${badge(toneOf[c.status], { GREEN: 'Valid', YELLOW: 'Expiring soon', RED: 'Expired' }[c.status])}</td>
      <td class="btn-row"><button class="btn secondary sm" data-act="renew" data-id="${c.id}">Renew</button><button class="btn danger sm" data-act="delDoc" data-id="${c.id}">✕</button></td></tr>`), 'No documents tracked')}</div>`;
}
actions.addDoc = () => openForm({ eyebrow: 'COMPLIANCE', title: 'Track a document', html: `<div class="form-grid">${fld('doc_type', 'Type', { opts: ['WM_CALIBRATION', 'FIRE_NOC', 'EXPLOSIVES_LICENSE', 'POLLUTION', 'OTHER'] })}${fld('title', 'Title')}${fld('number', 'Certificate / licence no.', { req: false })}${fld('authority', 'Issuing authority', { req: false })}${fld('issue_date', 'Issued on', { type: 'date', req: false })}${fld('expiry_date', 'Valid until', { type: 'date' })}</div>`,
  onSubmit: async (v) => { await api('/api/compliance', { method: 'POST', body: { doc_type: v.doc_type, title: v.title, number: v.number || null, authority: v.authority || null, issue_date: v.issue_date || null, expiry_date: v.expiry_date } }); showToast('Document added'); } });
actions.renew = ({ id }) => openForm({ eyebrow: 'RENEWAL', title: 'Record renewal', submit: 'Save', html: `<div class="form-grid">${fld('expiry_date', 'New expiry date', { type: 'date' })}${fld('number', 'New number (if changed)', { req: false })}</div>`,
  onSubmit: async (v) => { await api(`/api/compliance/${id}/renew`, { method: 'PUT', body: { expiry_date: v.expiry_date, number: v.number || null } }); showToast('Renewed'); } });
actions.delDoc = async ({ id }) => { if (confirm('Delete this document record?')) { await api(`/api/compliance/${id}`, { method: 'DELETE' }); render(); } };

// ───────────────────────── Audit & alerts ─────────────────────────
async function audit() {
  const a = await api('/api/alerts');
  const groups = [['critical', 'Critical'], ['warning', 'Warnings'], ['info', 'Info']];
  return `${pageHead('Audit & Alerts', 'Exception-first view: cash variance, stock, water, deliveries, compliance, AMC, tickets and failed face scans.')}
    ${groups.map(([sev, label]) => { const items = a.filter((x) => x.severity === sev); return items.length ? `<div class="card panel section-gap"><div class="panel-head"><h3>${label}</h3><span>${items.length}</span></div>${items.map((x) => `<div class="alert"><div class="alert-icon ${sev}">${sev === 'critical' ? '⚠' : '!'}</div><div style="flex:1"><h4>${esc(x.title)}</h4><p>${esc(x.detail)}</p></div>${badge('grey', pretty(x.category))}</div>`).join('')}</div>` : ''; }).join('') || '<div class="card empty">All systems normal ✓</div>'}`;
}

// ───────────────────────── global events ─────────────────────────
document.addEventListener('click', async (e) => {
  const nav = e.target.closest('.nav-item[data-page]');
  if (nav) { state.page = nav.dataset.page; $('#sidebar').classList.remove('open'); render(); return; }
  const b = e.target.closest('[data-act]');
  if (b && actions[b.dataset.act]) { try { await actions[b.dataset.act](b.dataset); } catch (err) { showToast(err.message, true); } }
});
function logout(msg = '') {
  Auth.token = null; state.role = null; closeForm(); closeReport();
  $('#nav').innerHTML = ''; $('#content').innerHTML = ''; $('#alertCount').textContent = '0';
  $('#loginErr').textContent = msg; $('#loginPw').value = ''; $('#loginModal').classList.remove('hidden');
}
function enter(role) {
  state.role = role === 'owner' ? 'owner' : 'employee'; state.page = 'overview';
  $('#loginModal').classList.add('hidden'); $('#notifications').classList.toggle('hidden', state.role !== 'owner'); render();
}
$('#roleSwitch').addEventListener('click', () => logout());
$('#kioskBtn').addEventListener('click', openCamModal);
$('#loginForm').addEventListener('submit', async (e) => {
  e.preventDefault(); const btn = $('#loginBtn'); btn.disabled = true; $('#loginErr').textContent = '';
  try {
    const r = await api('/api/auth/login', { method: 'POST', body: { role: $('#loginRole').value, password: $('#loginPw').value } });
    Auth.token = r.token; $('#loginPw').value = ''; enter(r.role);
  } catch (err) { $('#loginErr').textContent = err.message; }
  finally { btn.disabled = false; }
});
$('#mobileMenu').addEventListener('click', () => $('#sidebar').classList.toggle('open'));
$('#notifications').addEventListener('click', () => { state.page = state.role === 'owner' ? 'audit' : 'overview'; render(); });
const closeReport = () => $('#closeModal').classList.add('hidden');
$('#closeModalX').addEventListener('click', closeReport); $('#cancelClose').addEventListener('click', closeReport);
$('#sendWhatsApp').addEventListener('click', () => {
  const phone = $('#ownerWhatsApp').value.replace(/\D/g, '');
  if (phone.length < 10) return showToast('Enter a valid WhatsApp number', true);
  window.open(`https://wa.me/${phone}?text=${encodeURIComponent(state.lastReport || 'FuelPilot report')}`, '_blank', 'noopener,noreferrer'); closeReport();
});
for (const id of ['#formModal', '#camModal', '#closeModal']) $(id).addEventListener('mousedown', (e) => { if (e.target.id === id.slice(1)) { id === '#camModal' ? closeCamModal() : id === '#formModal' ? closeForm() : closeReport(); } });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closeForm(); if (cam.open) closeCamModal(); closeReport(); } });

// Auto-refresh the overview while no dialog is open
setInterval(() => { if (state.page === 'overview' && !document.querySelector('.modal-backdrop:not(.hidden)')) render(); }, 30000);
// Boot: resume an existing session, otherwise show the login screen
(async () => {
  if (Auth.token) { try { const m = await api('/api/auth/me'); if (m.role) return enter(m.role); } catch { /* fall through */ } }
  logout();
})();
