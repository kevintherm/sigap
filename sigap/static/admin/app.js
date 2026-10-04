// Sigap admin panel — vanilla JS single-page app over /api/admin.
'use strict';
const $ = (s, el = document) => el.querySelector(s);
const S = { me: null, courses: [], students: null, route: null, poll: null };

// ------------------------------------------------------------ helpers
function esc(v) { return String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function el(html) { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; }
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => t.hidden = true, 3200); }
const WIB = { timeZone: 'Asia/Jakarta' };
function fmtDT(iso) {
  if (!iso) return '–';
  const d = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + 'Z');
  return d.toLocaleString('id-ID', { ...WIB, day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}
function fmtDate(iso) { return new Date(iso + 'T00:00:00').toLocaleDateString('id-ID', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }); }
const STATUS = { open: 'Terbuka', in_progress: 'Diproses', answered: 'Dijawab', closed: 'Selesai' };
const TYPES = { assignment: 'Tugas', quiz: 'Kuis', project: 'Proyek', exam: 'Ujian' };
const CHANNELS = { telegram: 'Telegram', whatsapp: 'WhatsApp', mcp: 'Agen MCP', panel: 'Panel' };
const can = (...roles) => S.me && roles.includes(S.me.role);

async function api(method, path, body) {
  const r = await fetch('/api/admin' + path, {
    method, credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-Sigap-Request': '1' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (r.status === 401 && path !== '/auth/login') { showLogin(); throw new Error('Sesi berakhir, silakan masuk lagi'); }
  const data = r.headers.get('content-type')?.includes('json') ? await r.json() : null;
  if (!r.ok) {
    const d = data?.detail;
    throw new Error(Array.isArray(d) ? d.map(x => `${x.loc?.slice(-1)[0]}: ${x.msg}`).join('; ') : (d || `HTTP ${r.status}`));
  }
  return data;
}
async function guard(fn, okMsg) { try { const r = await fn(); if (okMsg) toast(okMsg); return r; } catch (e) { toast('⚠️ ' + e.message); throw e; } }
function formData(form) {
  const o = {};
  for (const [k, v] of new FormData(form)) { if (k.endsWith('[]')) (o[k.slice(0, -2)] ||= []).push(v); else o[k] = v; }
  form.querySelectorAll('input[type=checkbox]:not([name$="[]"])').forEach(c => o[c.name] = c.checked);
  form.querySelectorAll('[name$="[]"]').forEach(c => { const k = c.name.slice(0, -2); o[k] ||= []; });
  return o;
}
function dialog(html, onSubmit) {
  const d = el(`<dialog>${html}</dialog>`);
  document.body.append(d);
  d.addEventListener('close', () => d.remove());
  d.querySelectorAll('[data-close]').forEach(b => b.onclick = () => d.close());
  const f = d.querySelector('form');
  if (f && onSubmit) f.onsubmit = async e => { e.preventDefault(); const err = d.querySelector('.error'); try { if (await onSubmit(formData(f), d) !== false) d.close(); } catch (x) { if (err) err.textContent = x.message; } };
  d.showModal();
  return d;
}
function courseName(code) { const c = S.courses.find(c => c.code === code); return c ? c.name_id : code; }
function courseChecks(selected = [], only = null) {
  return `<div class="chips">${S.courses.filter(c => !only || only.includes(c.code)).map(c =>
    `<label class="check"><input type="checkbox" name="courses[]" value="${c.code}" ${selected.includes(c.code) ? 'checked' : ''}> ${esc(c.code)} ${esc(c.name_id)}</label>`).join('')}</div>`;
}

// ------------------------------------------------------------ routes
const ROUTES = [
  { id: 'inbox', label: '📥 Kotak masuk', roles: ['admin', 'staff'], render: viewInbox },
  { id: 'dashboard', label: '📊 Dasbor', roles: ['admin', 'staff'], render: viewDashboard },
  { id: 'students', label: '🎓 Mahasiswa', roles: ['admin', 'staff'], render: viewStudents },
  { id: 'schedule', label: '📅 Jadwal & tenggat', roles: ['admin', 'staff', 'lecturer'], render: viewSchedule },
  { id: 'calendar', label: '🗓️ Kalender akademik', roles: ['admin', 'staff', 'lecturer'], render: viewCalendar },
  { id: 'handbook', label: '📖 Pedoman', roles: ['admin', 'staff', 'lecturer'], render: viewHandbook },
  { id: 'users', label: '👥 Pengguna', roles: ['admin'], render: viewUsers },
  { id: 'testchat', label: '💬 Uji chat', roles: ['admin', 'staff', 'lecturer'], render: viewTestChat },
  { id: 'account', label: '🔑 Akun saya', roles: ['admin', 'staff', 'lecturer'], render: viewAccount },
];

function renderNav(counts = {}) {
  const nav = $('#nav'); nav.innerHTML = '';
  for (const r of ROUTES.filter(r => can(...r.roles))) {
    const a = el(`<a href="#${r.id}" class="${S.route === r.id ? 'active' : ''}">${r.label}${r.id === 'inbox' && counts.open ? `<span class="count">${counts.open}</span>` : ''}</a>`);
    a.onclick = () => $('.nav').classList.remove('open');
    nav.append(a);
  }
}
async function go() {
  const allowed = ROUTES.filter(r => can(...r.roles));
  const r = allowed.find(r => r.id === location.hash.slice(1)) || allowed[0];
  S.route = r.id; clearInterval(S.poll);
  $('#title').textContent = r.label.replace(/^\S+\s/, '');
  renderNav(S.counts);
  const view = $('#view'); view.innerHTML = '<p class="muted">Memuat…</p>';
  try { await r.render(view); } catch (e) { view.innerHTML = `<p class="error">${esc(e.message)}</p>`; }
}
window.addEventListener('hashchange', go);

// ------------------------------------------------------------ auth
function showLogin() { $('#app').hidden = true; $('#login').hidden = false; $('#login-form input[name=email]').focus(); }
$('#login-form').onsubmit = async e => {
  e.preventDefault(); $('#login-error').textContent = '';
  try { S.me = await api('POST', '/auth/login', formData(e.target)); e.target.reset(); await start(); }
  catch (x) { $('#login-error').textContent = x.message; }
};
$('#logout').onclick = async () => { await api('POST', '/auth/logout'); S.me = null; showLogin(); };
$('#menu').onclick = () => $('.nav').classList.toggle('open');

async function start() {
  $('#login').hidden = true; $('#app').hidden = false;
  $('#me').innerHTML = `<b>${esc(S.me.name)}</b><span class="muted">${esc(S.me.email)} · ${esc(S.me.role)}</span>`;
  S.courses = await api('GET', '/courses');
  refreshSync(); setInterval(refreshSync, 30000);
  if (can('admin', 'staff')) { const h = await api('GET', '/handoffs?status=open'); S.counts = h.counts; }
  go();
}
async function refreshSync() {
  try {
    const s = await api('GET', '/sync');
    $('#sync').innerHTML = `<span class="dot ${s.ok ? 'ok' : s.ok === false ? 'bad' : ''}"></span><span class="txt">Langflow: ${esc(s.detail)}${s.last_sync ? ' · ' + esc(s.last_sync.slice(11, 16)) : ''}</span>` +
      (can('admin', 'staff') ? ' <button class="btn ghost small" id="sync-now">Sinkronkan</button>' : '');
    const b = $('#sync-now'); if (b) b.onclick = async () => { await guard(() => api('POST', '/sync')); refreshSync(); };
  } catch { }
}
const syncSoon = () => setTimeout(refreshSync, 2500);

// ------------------------------------------------------------ inbox
async function viewInbox(view) {
  let filter = 'open', selected = null;
  view.innerHTML = `<div class="tabs" id="tabs"></div><div class="split"><div class="table-wrap" id="list"></div><div id="detail" class="card"><p class="muted">Pilih tiket untuk melihat detail.</p></div></div>`;
  async function loadList() {
    const data = await api('GET', '/handoffs' + (filter ? `?status=${filter}` : ''));
    S.counts = data.counts; renderNav(S.counts);
    const total = Object.values(data.counts).reduce((a, b) => a + b, 0);
    $('#tabs').innerHTML = [['open', 'Terbuka'], ['in_progress', 'Diproses'], ['answered', 'Dijawab'], ['closed', 'Selesai'], ['', 'Semua']]
      .map(([k, l]) => `<button class="${filter === k ? 'on' : ''}" data-f="${k}">${l} · ${k ? (data.counts[k] || 0) : total}</button>`).join('');
    $('#tabs').querySelectorAll('button').forEach(b => b.onclick = () => { filter = b.dataset.f; loadList(); });
    $('#list').innerHTML = data.items.length ? `<table><thead><tr><th>Tiket</th><th>Pertanyaan</th><th>Status</th></tr></thead><tbody>${data.items.map(h => `
      <tr class="click ${selected === h.id ? 'selected' : ''}" data-id="${h.id}"><td><code>${esc(h.reference)}</code><div class="muted small">${fmtDT(h.created_at)}</div></td>
      <td>${esc(h.question.slice(0, 110))}<div class="muted small">${esc(h.student ? h.student.name : 'Belum terhubung')} · ${esc(CHANNELS[h.channel] || h.channel)}</div></td>
      <td><span class="badge ${h.status}">${STATUS[h.status]}</span></td></tr>`).join('')}</tbody></table>`
      : '<p class="muted" style="padding:16px">Tidak ada tiket di sini.</p>';
    $('#list').querySelectorAll('tr[data-id]').forEach(tr => tr.onclick = () => { selected = +tr.dataset.id; loadDetail(); loadList(); });
  }
  async function loadDetail() {
    const h = await api('GET', `/handoffs/${selected}`);
    const d = $('#detail');
    d.innerHTML = `
      <div class="row"><code>${esc(h.reference)}</code><span class="badge ${h.status}">${STATUS[h.status]}</span><span class="spacer"></span>
        <select id="status" style="width:auto">${Object.entries(STATUS).map(([k, v]) => `<option value="${k}" ${k === h.status ? 'selected' : ''}>${v}</option>`).join('')}</select>
        <button class="btn small" id="mine">Ambil</button></div>
      <p class="question">${esc(h.question)}</p>
      <p class="muted small">${h.student ? `🎓 ${esc(h.student.name)} · NIM ${esc(h.student.number)}` : '🎓 Belum terhubung ke data mahasiswa'} · ${esc(CHANNELS[h.channel] || h.channel)} ·
        ${esc(h.office)} · ${esc(h.category)} · ${h.language === 'en' ? 'English' : 'Bahasa Indonesia'}</p>
      <h3>Riwayat</h3>
      <div class="timeline">${h.events.map(e => `<div class="event ${e.kind}"><div class="meta">${fmtDT(e.at)} · ${esc(e.by || 'Sigap')} ·
        ${e.kind === 'reply' ? (e.delivered ? 'balasan terkirim ✓' : 'balasan TIDAK terkirim') : e.kind === 'note' ? 'catatan internal' : 'status'}</div>
        ${e.kind === 'status' ? `→ ${esc(STATUS[e.body] || e.body)}` : esc(e.body)}</div>`).join('')}</div>
      <h3>Balas mahasiswa</h3>
      ${h.can_reply ? '' : `<p class="muted small">Balasan langsung tidak tersedia untuk kanal ini${h.channel === 'mcp' ? ' (mahasiswa melihatnya lewat get_my_tickets)' : ''}; balasan tetap disimpan.</p>`}
      <textarea id="reply" placeholder="Tulis balasan; dikirim ke chat mahasiswa dan status menjadi Dijawab."></textarea>
      <div class="row" style="margin-top:8px"><button class="btn primary" id="send">Kirim balasan</button><span class="spacer"></span></div>
      <h3>Catatan internal</h3>
      <textarea id="note" placeholder="Hanya terlihat oleh staf." style="min-height:60px"></textarea>
      <div class="row" style="margin-top:8px"><button class="btn" id="addnote">Simpan catatan</button></div>`;
    $('#status', d).onchange = async e => { await guard(() => api('PATCH', `/handoffs/${h.id}`, { status: e.target.value }), 'Status diperbarui'); loadDetail(); loadList(); };
    $('#mine', d).onclick = async () => { await guard(() => api('PATCH', `/handoffs/${h.id}`, { assign_to_me: true, status: h.status === 'open' ? 'in_progress' : null }), 'Tiket diambil'); loadDetail(); loadList(); };
    $('#send', d).onclick = async () => {
      const body = $('#reply', d).value.trim(); if (!body) return;
      const r = await guard(() => api('POST', `/handoffs/${h.id}/reply`, { body }));
      toast(r.delivered ? 'Balasan terkirim ke mahasiswa ✓' : 'Balasan disimpan, tetapi tidak terkirim (kanal tidak aktif)');
      loadDetail(); loadList();
    };
    $('#addnote', d).onclick = async () => { const body = $('#note', d).value.trim(); if (!body) return; await guard(() => api('POST', `/handoffs/${h.id}/notes`, { body }), 'Catatan disimpan'); loadDetail(); };
  }
  await loadList();
  S.poll = setInterval(loadList, 15000);
}

// ------------------------------------------------------------ dashboard
const OUTCOMES = { answered: 'Terjawab', not_found: 'Tidak ditemukan', offer: 'Ditawari aksi', action: 'Aksi dijalankan', declined: 'Ditolak', error: 'Galat', other: 'Lainnya' };
const TOOLS = { answer_campus_policy: 'Aturan kampus', get_my_deadlines: 'Tenggat & ujian', get_study_period: 'Periode studi',
  create_study_reminder: 'Pengingat', escalate_to_student_services: 'Teruskan ke staf' };
function hbars(obj, labels = {}) {
  const entries = Object.entries(obj).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return '<p class="muted small">Belum ada data.</p>';
  const max = Math.max(...entries.map(e => e[1]));
  return `<div class="hbars">${entries.map(([k, v]) => `<div class="hbar"><span>${esc(labels[k] || k)}</span>
    <div class="track"><div class="fill" style="width:${(v / max * 100).toFixed(1)}%"></div></div><span class="val">${v}</span></div>`).join('')}</div>`;
}
function dayChart(series) {
  // Single series: one hue, no legend; thin bars with 4px rounded tops, recessive grid, hover tooltip.
  const W = 640, H = 200, L = 30, B = 24, T = 10, n = series.length;
  const max = Math.max(4, ...series.map(d => d.count)), step = Math.pow(10, Math.floor(Math.log10(max))) * (max / Math.pow(10, Math.floor(Math.log10(max))) > 5 ? 2 : 1);
  const top = Math.ceil(max / step) * step, y = v => T + (H - T - B) * (1 - v / top);
  const slot = (W - L) / n, bw = Math.max(4, Math.min(22, slot * 0.55));
  let g = '';
  for (let v = 0; v <= top; v += step) g += `<line class="gridline" x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text class="axis" x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${v}</text>`;
  series.forEach((d, i) => {
    const cx = L + slot * (i + .5), h = H - B - y(d.count), r = Math.min(4, bw / 2, h);
    const label = new Date(d.date + 'T00:00:00').toLocaleDateString('id-ID', { day: 'numeric', month: 'short' });
    const bar = h > 0 ? `<path class="bar" d="M${cx - bw / 2},${H - B} V${H - B - h + r} Q${cx - bw / 2},${H - B - h} ${cx - bw / 2 + r},${H - B - h} H${cx + bw / 2 - r} Q${cx + bw / 2},${H - B - h} ${cx + bw / 2},${H - B - h + r} V${H - B} Z"/>` : '';
    g += `<g data-tip="${esc(label)}: ${d.count} pesan" data-x="${cx}" data-y="${y(d.count)}"><rect class="hit" x="${cx - slot / 2}" y="${T}" width="${slot}" height="${H - T - B}"/>${bar}</g>`;
    if (i % Math.ceil(n / 7) === 0 || i === n - 1) g += `<text class="axis" x="${cx}" y="${H - 6}" text-anchor="middle">${esc(label)}</text>`;
  });
  g += `<line class="baseline" x1="${L}" x2="${W}" y1="${H - B}" y2="${H - B}"/>`;
  const wrap = el(`<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Pesan per hari">${g}</svg><div class="tooltip" hidden></div></div>`);
  const tip = $('.tooltip', wrap), svg = $('svg', wrap);
  svg.querySelectorAll('g[data-tip]').forEach(gr => {
    gr.onmouseenter = () => { const k = svg.getBoundingClientRect().width / W; tip.textContent = gr.dataset.tip; tip.style.left = gr.dataset.x * k + 'px'; tip.style.top = gr.dataset.y * k + 'px'; tip.hidden = false; };
    gr.onmouseleave = () => tip.hidden = true;
  });
  return wrap;
}
async function viewDashboard(view) {
  const d = await api('GET', '/dashboard?days=14');
  const open = (d.handoffs.open || 0) + (d.handoffs.in_progress || 0);
  const nf = d.outcomes.not_found || 0;
  view.innerHTML = `
    <div class="tiles">
      <div class="tile"><div class="k">Pesan (14 hari)</div><div class="v">${d.total}</div><div class="s">dari semua kanal</div></div>
      <div class="tile"><div class="k">Mahasiswa terhubung</div><div class="v">${d.linked_students}<span class="muted" style="font-size:15px"> / ${d.students}</span></div><div class="s">punya akun chat tertaut</div></div>
      <div class="tile"><div class="k">Tiket aktif</div><div class="v">${open}</div><div class="s">terbuka + diproses</div></div>
      <div class="tile"><div class="k">Tidak terjawab</div><div class="v">${nf}</div><div class="s">${d.total ? Math.round(nf / d.total * 100) : 0}% pesan · lihat daftar di bawah</div></div>
      <div class="tile"><div class="k">Waktu respons</div><div class="v">${d.p50_ms ?? '–'}<span class="muted" style="font-size:14px"> ms</span></div><div class="s">median · p95 ${d.p95_ms ?? '–'} ms</div></div>
    </div>
    <div class="grid-2">
      <div class="card"><h2>Pesan per hari</h2><div id="chart"></div>
        <details class="small"><summary class="muted">Lihat sebagai tabel</summary><table><thead><tr><th>Tanggal</th><th class="num">Pesan</th></tr></thead><tbody>
        ${d.per_day.map(x => `<tr><td>${fmtDate(x.date)}</td><td class="num">${x.count}</td></tr>`).join('')}</tbody></table></details></div>
      <div class="card"><h2>Hasil percakapan</h2>${hbars(d.outcomes, OUTCOMES)}<h3>Kanal</h3>${hbars(d.channels, CHANNELS)}<h3>Bahasa</h3>${hbars(d.languages, { id: 'Bahasa Indonesia', en: 'English' })}</div>
      <div class="card"><h2>Alat yang dipakai</h2>${hbars(d.tools, TOOLS)}</div>
      <div class="card"><h2>Pertanyaan tidak terjawab</h2><p class="muted small">Pertanyaan tanpa jawaban di pedoman. Pola yang berulang = bagian pedoman yang perlu ditambah.</p>
        ${d.unanswered.length ? `<table><tbody>${d.unanswered.map(u => `<tr><td>${esc(u.question)}</td><td class="muted small">${fmtDT(u.at)}</td></tr>`).join('')}</tbody></table>` : '<p class="muted small">Belum ada.</p>'}</div>
    </div>`;
  $('#chart').append(dayChart(d.per_day));
}

// ------------------------------------------------------------ students
async function viewStudents(view) {
  const students = await api('GET', '/students');
  view.innerHTML = `<div class="row" style="margin-bottom:12px"><input id="q" placeholder="Cari nama atau NIM…" style="max-width:280px"><span class="spacer"></span><button class="btn primary" id="add">+ Tambah mahasiswa</button></div><div class="table-wrap" id="tbl"></div>`;
  const draw = () => {
    const q = $('#q').value.toLowerCase();
    const rows = students.filter(s => !q || s.name.toLowerCase().includes(q) || s.number.includes(q));
    $('#tbl').innerHTML = `<table><thead><tr><th>NIM</th><th>Nama</th><th>Mata kuliah</th><th>Chat</th><th></th></tr></thead><tbody>${rows.map(s => `
      <tr class="click" data-id="${s.id}"><td><code>${esc(s.number)}</code></td><td>${esc(s.name)}${s.active ? '' : ' <span class="badge">nonaktif</span>'}</td>
      <td><div class="chips">${s.courses.map(c => `<span class="badge">${esc(c)}</span>`).join('')}</div></td>
      <td>${s.channels.length ? s.channels.map(c => `<span class="badge ok">${esc(CHANNELS[c.channel] || c.channel)}</span>`).join(' ') : '<span class="badge">belum</span>'}</td>
      <td class="num"><button class="btn small" data-invite="${s.id}">Undangan</button></td></tr>`).join('')}</tbody></table>`;
    $('#tbl').querySelectorAll('tr[data-id]').forEach(tr => tr.onclick = e => { if (!e.target.dataset.invite) studentDialog(+tr.dataset.id); });
    $('#tbl').querySelectorAll('[data-invite]').forEach(b => b.onclick = () => inviteDialog(+b.dataset.invite));
  };
  $('#q').oninput = draw; draw();
  $('#add').onclick = () => dialog(`<form><h2>Tambah mahasiswa</h2><div class="form-grid">
      <label>NIM<input name="number" required></label><label>Nama<input name="name" required></label>
      <label>Email<input name="email" type="email"></label><label>Program<input name="program"></label>
      <div class="wide"><h3>Mata kuliah</h3>${courseChecks()}</div></div>
      <p class="error"></p><div class="row"><span class="spacer"></span><button type="button" class="btn" data-close>Batal</button><button class="btn primary">Simpan</button></div></form>`,
    async v => { await api('POST', '/students', { ...v, active: true }); toast('Mahasiswa ditambahkan'); go(); });
}
async function inviteDialog(id) {
  const inv = await guard(() => api('POST', `/students/${id}/invite`));
  const link = inv.telegram_link;
  const d = dialog(`<h2>Undangan Telegram</h2><p class="muted small">${esc(inv.instructions)}</p>
    <div class="secret">${link ? `<a href="${esc(link)}" target="_blank" rel="noopener">${esc(link)}</a>` : `Kirim ke bot: <code>/start ${esc(inv.code)}</code><br><span class="muted small">(Bot belum aktif, jadi belum ada tautan t.me)</span>`}</div>
    <div class="row" style="margin-top:12px"><span class="muted small">Berlaku sampai ${fmtDT(inv.expires_at)}</span><span class="spacer"></span><button class="btn" id="copy">Salin</button><button class="btn primary" data-close>Selesai</button></div>`);
  $('#copy', d).onclick = () => navigator.clipboard.writeText(link || `/start ${inv.code}`).then(() => toast('Disalin'));
}
async function studentDialog(id) {
  const s = await api('GET', `/students/${id}`);
  const d = dialog(`<form><h2>${esc(s.name)}</h2><div class="form-grid">
      <label>NIM<input name="number" value="${esc(s.number)}" required></label><label>Nama<input name="name" value="${esc(s.name)}" required></label>
      <label>Email<input name="email" type="email" value="${esc(s.email || '')}"></label><label>Program<input name="program" value="${esc(s.program || '')}"></label>
      <label class="check"><input type="checkbox" name="active" ${s.active ? 'checked' : ''}> Aktif</label>
      <div class="wide"><h3>Mata kuliah</h3>${courseChecks(s.courses)}</div></div>
      <h3>Akun chat</h3>${s.channels.length ? s.channels.map(c => `<div class="row small">${esc(CHANNELS[c.channel] || c.channel)} · ${esc(c.display_name || '')} · ${fmtDT(c.linked_at)}<span class="spacer"></span><button type="button" class="btn small danger" data-unlink="${c.id}">Putuskan</button></div>`).join('') : '<p class="muted small">Belum terhubung.</p>'}
      <h3>Token MCP</h3>${s.tokens.length ? s.tokens.map(t => `<div class="row small"><code>${esc(t.prefix)}…</code> ${t.revoked ? '<span class="badge bad">dicabut</span>' : `· dipakai ${fmtDT(t.last_used_at)}`}<span class="spacer"></span>${t.revoked ? '' : `<button type="button" class="btn small danger" data-revoke="${t.id}">Cabut</button>`}</div>`).join('') : '<p class="muted small">Tidak ada.</p>'}
      <h3>Tiket</h3>${s.handoffs.length ? s.handoffs.map(h => `<div class="small"><code>${esc(h.reference)}</code> <span class="badge ${h.status}">${STATUS[h.status]}</span> ${esc(h.question.slice(0, 80))}</div>`).join('') : '<p class="muted small">Tidak ada.</p>'}
      <p class="error"></p><div class="row"><button type="button" class="btn" id="inv">Buat undangan</button><span class="spacer"></span><button type="button" class="btn" data-close>Tutup</button><button class="btn primary">Simpan</button></div></form>`,
    async v => { await api('PATCH', `/students/${id}`, v); toast('Tersimpan'); go(); });
  d.querySelectorAll('[data-unlink]').forEach(b => b.onclick = async () => { if (!confirm('Putuskan akun chat ini?')) return; await guard(() => api('DELETE', `/students/${id}/channels/${b.dataset.unlink}`), 'Diputus'); d.close(); studentDialog(id); });
  d.querySelectorAll('[data-revoke]').forEach(b => b.onclick = async () => { if (!confirm('Cabut token ini?')) return; await guard(() => api('DELETE', `/tokens/${b.dataset.revoke}`), 'Token dicabut'); d.close(); studentDialog(id); });
  $('#inv', d).onclick = () => { d.close(); inviteDialog(id); };
}

// ------------------------------------------------------------ schedule
async function viewSchedule(view) {
  const editable = S.courses.filter(c => c.editable);
  view.innerHTML = `<div class="row" style="margin-bottom:12px"><select id="course" style="max-width:300px"><option value="">Semua mata kuliah${S.me.role === 'lecturer' ? ' saya' : ''}</option>
    ${(S.me.role === 'lecturer' ? editable : S.courses).map(c => `<option value="${c.code}">${esc(c.code)} · ${esc(c.name_id)}</option>`).join('')}</select>
    <span class="spacer"></span>${editable.length ? '<button class="btn primary" id="add">+ Tambah tenggat/ujian</button>' : ''}</div>
    <p class="muted small">Perubahan langsung disinkronkan ke Langflow; mahasiswa melihatnya di chat berikutnya.</p><div class="table-wrap" id="tbl"></div>`;
  const load = async () => {
    const c = $('#course').value, items = await api('GET', '/schedule' + (c ? `?course=${c}` : ''));
    $('#tbl').innerHTML = items.length ? `<table><thead><tr><th>Tenggat (WIB)</th><th>Mata kuliah</th><th>Item</th><th>Jenis</th><th></th></tr></thead><tbody>${items.map(i => `
      <tr><td>${fmtDate(i.due_date)}<div class="muted small">${esc(i.due_time)}${i.end_time ? '–' + esc(i.end_time) : ''}</div></td><td>${esc(courseName(i.course_code))}<div class="muted small">${esc(i.course_code)}</div></td>
      <td>${esc(i.title_id)}<div class="muted small"><code>${esc(i.item_id)}</code> · ${esc(i.source)}</div></td><td><span class="badge">${TYPES[i.type]}</span></td>
      <td class="num">${editable.some(c => c.code === i.course_code) ? `<button class="btn small" data-edit="${esc(i.item_id)}">Ubah</button>` : ''}</td></tr>`).join('')}</tbody></table>` : '<p class="muted" style="padding:16px">Belum ada item.</p>';
    $('#tbl').querySelectorAll('[data-edit]').forEach(b => b.onclick = () => itemDialog(items.find(i => i.item_id === b.dataset.edit), load));
  };
  $('#course').onchange = load;
  const add = $('#add'); if (add) add.onclick = () => itemDialog(null, load);
  await load();
}
function itemDialog(it, reload) {
  const editable = S.courses.filter(c => c.editable);
  const d = dialog(`<form><h2>${it ? 'Ubah item' : 'Tambah tenggat / ujian'}</h2><div class="form-grid">
    <label>ID item<input name="item_id" value="${esc(it?.item_id || '')}" ${it ? 'readonly' : ''} required placeholder="SD-T6"></label>
    <label>Mata kuliah<select name="course_code">${editable.map(c => `<option value="${c.code}" ${it?.course_code === c.code ? 'selected' : ''}>${esc(c.code)} · ${esc(c.name_id)}</option>`).join('')}</select></label>
    <label>Jenis<select name="type">${Object.entries(TYPES).map(([k, v]) => `<option value="${k}" ${it?.type === k ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
    <label>Judul (ID)<input name="title_id" value="${esc(it?.title_id || '')}" required></label><label>Judul (EN)<input name="title_en" value="${esc(it?.title_en || '')}" required></label>
    <label>Tanggal<input name="due_date" type="date" value="${esc(it?.due_date || '')}" required></label>
    <label>Jam (WIB)<input name="due_time" type="time" value="${esc(it?.due_time || '23:59')}" required></label><label>Selesai (opsional)<input name="end_time" type="time" value="${esc(it?.end_time || '')}"></label>
    <label>Tempat/cara kumpul<input name="where" value="${esc(it?.where || '')}"></label><label class="wide">Sumber<input name="source" value="${esc(it?.source || '')}" placeholder="RPS / pengumuman dosen"></label></div>
    <p class="error"></p><div class="row">${it ? '<button type="button" class="btn danger" id="del">Hapus</button>' : ''}<span class="spacer"></span><button type="button" class="btn" data-close>Batal</button><button class="btn primary">Simpan</button></div></form>`,
    async v => { await api(it ? 'PATCH' : 'POST', it ? `/schedule/${it.item_id}` : '/schedule', v); toast('Tersimpan · disinkronkan ke Langflow'); syncSoon(); reload(); });
  const del = $('#del', d);
  if (del) del.onclick = async () => { if (!confirm('Hapus item ini?')) return; await guard(() => api('DELETE', `/schedule/${it.item_id}`), 'Dihapus'); d.close(); syncSoon(); reload(); };
}

// ------------------------------------------------------------ calendar
async function viewCalendar(view) {
  const entries = await api('GET', '/calendar'), admin = can('admin');
  const KIND = { term: 'Semester', period: 'Periode', window: 'Jendela' };
  view.innerHTML = `<div class="row" style="margin-bottom:12px"><p class="muted small" style="margin:0">Sumber untuk "minggu ke berapa" dan "apa yang sedang dibuka".</p><span class="spacer"></span>${admin ? '<button class="btn primary" id="add">+ Tambah</button>' : ''}</div>
    <div class="table-wrap"><table><thead><tr><th>Jenis</th><th>Nama</th><th>Mulai</th><th>Selesai</th><th></th></tr></thead><tbody>${entries.map(e => `
    <tr><td><span class="badge">${KIND[e.kind]}</span></td><td>${esc(e.name_id)}<div class="muted small"><code>${esc(e.key)}</code> ${esc(e.note_id)}</div></td><td>${fmtDate(e.start)}</td><td>${fmtDate(e.end)}</td>
    <td class="num">${admin ? `<button class="btn small" data-edit="${e.id}">Ubah</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`;
  const open = e => {
    const d = dialog(`<form><h2>${e ? 'Ubah' : 'Tambah'} entri kalender</h2><div class="form-grid">
      <label>Jenis<select name="kind">${Object.entries(KIND).map(([k, v]) => `<option value="${k}" ${e?.kind === k ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
      <label>Kunci<input name="key" value="${esc(e?.key || '')}" required placeholder="cuti"></label>
      <label>Nama (ID)<input name="name_id" value="${esc(e?.name_id || '')}" required></label><label>Nama (EN)<input name="name_en" value="${esc(e?.name_en || '')}" required></label>
      <label>Mulai<input name="start" type="date" value="${esc(e?.start || '')}" required></label><label>Selesai<input name="end" type="date" value="${esc(e?.end || '')}" required></label>
      <label class="wide">Catatan (ID)<input name="note_id" value="${esc(e?.note_id || '')}"></label><label class="wide">Catatan (EN)<input name="note_en" value="${esc(e?.note_en || '')}"></label></div>
      <p class="error"></p><div class="row">${e ? '<button type="button" class="btn danger" id="del">Hapus</button>' : ''}<span class="spacer"></span><button type="button" class="btn" data-close>Batal</button><button class="btn primary">Simpan</button></div></form>`,
      async v => { await api(e ? 'PATCH' : 'POST', e ? `/calendar/${e.id}` : '/calendar', v); toast('Tersimpan · disinkronkan'); syncSoon(); go(); });
    const del = $('#del', d); if (del) del.onclick = async () => { if (!confirm('Hapus entri ini?')) return; await guard(() => api('DELETE', `/calendar/${e.id}`), 'Dihapus'); d.close(); syncSoon(); go(); };
  };
  if (admin) { $('#add').onclick = () => open(null); view.querySelectorAll('[data-edit]').forEach(b => b.onclick = () => open(entries.find(x => x.id === +b.dataset.edit))); }
}

// ------------------------------------------------------------ handbook
async function viewHandbook(view) {
  const docs = await api('GET', '/documents'), admin = can('admin');
  view.innerHTML = `<div class="stack"><div class="table-wrap"><table><thead><tr><th>Versi</th><th>Judul</th><th>Berlaku</th><th class="num">Pasal</th><th>Status</th><th></th></tr></thead><tbody>${docs.map(d => `
    <tr><td><code>v${esc(d.version)}</code></td><td>${esc(d.title)}<div class="muted small">diunggah ${fmtDT(d.uploaded_at)}</div></td><td>${fmtDate(d.effective)}</td><td class="num">${d.sections}</td>
    <td>${d.active ? '<span class="badge ok">aktif · dipakai bot</span>' : '<span class="badge">tidak dipakai</span>'}</td>
    <td class="num"><button class="btn small" data-view="${d.id}">Lihat</button>${admin && !d.active ? ` <button class="btn small" data-act="${d.id}">Aktifkan</button>` : ''}</td></tr>`).join('')}</tbody></table></div>
    ${admin ? `<div class="card"><h2>Unggah versi baru</h2><p class="muted small">Format Markdown seperti pedoman contoh: front matter (title_id, version, effective) lalu per pasal <code>## 4.2 | Judul | Title</code> diikuti baris <code>tags:</code>, <code>ID:</code>, <code>EN:</code>. Versi lama tetap tersimpan tetapi tidak dipakai bot (FR-10).</p>
      <div class="row" style="margin:8px 0"><input type="file" id="file" accept=".md,text/markdown,text/plain" style="max-width:320px"><label class="check"><input type="checkbox" id="activate" checked> Langsung aktifkan</label></div>
      <textarea id="content" style="min-height:160px" placeholder="…atau tempel isi Markdown di sini"></textarea><p class="error" id="uperr"></p>
      <button class="btn primary" id="upload">Validasi & unggah</button></div>` : ''}</div>`;
  view.querySelectorAll('[data-view]').forEach(b => b.onclick = async () => {
    const d = await api('GET', `/documents/${b.dataset.view}`);
    dialog(`<h2>${esc(d.title)} · v${esc(d.version)}</h2><pre class="mono small" style="white-space:pre-wrap;max-height:60vh;overflow:auto">${esc(d.content)}</pre><div class="row"><span class="spacer"></span><button class="btn" data-close>Tutup</button></div>`);
  });
  view.querySelectorAll('[data-act]').forEach(b => b.onclick = async () => { await guard(() => api('POST', `/documents/${b.dataset.act}/activate`), 'Versi diaktifkan · disinkronkan'); syncSoon(); go(); });
  if (admin) {
    $('#file').onchange = async e => { const f = e.target.files[0]; if (f) $('#content').value = await f.text(); };
    $('#upload').onclick = async () => {
      $('#uperr').textContent = '';
      try { const r = await api('POST', '/documents', { content: $('#content').value, activate: $('#activate').checked }); toast(`Diunggah: ${r.sections} pasal${r.active ? ' · aktif' : ''}`); syncSoon(); go(); }
      catch (e) { $('#uperr').textContent = e.message; }
    };
  }
}

// ------------------------------------------------------------ users
async function viewUsers(view) {
  const users = await api('GET', '/users');
  const ROLE = { admin: 'Admin', staff: 'Staf layanan', lecturer: 'Dosen' };
  view.innerHTML = `<div class="row" style="margin-bottom:12px"><span class="spacer"></span><button class="btn primary" id="add">+ Tambah pengguna</button></div>
    <div class="table-wrap"><table><thead><tr><th>Nama</th><th>Email</th><th>Peran</th><th>Mata kuliah</th><th></th></tr></thead><tbody>${users.map(u => `
    <tr><td>${esc(u.name)}${u.active ? '' : ' <span class="badge">nonaktif</span>'}</td><td>${esc(u.email)}</td><td><span class="badge">${ROLE[u.role]}</span></td>
    <td><div class="chips">${u.courses.map(c => `<span class="badge">${esc(c)}</span>`).join('')}</div></td><td class="num"><button class="btn small" data-edit="${u.id}">Ubah</button></td></tr>`).join('')}</tbody></table></div>`;
  const showPw = (email, pw) => dialog(`<h2>Kata sandi sementara</h2><p class="muted small">Untuk ${esc(email)}. Hanya ditampilkan sekali; minta pengguna menggantinya di "Akun saya".</p><div class="secret"><code>${esc(pw)}</code></div><div class="row" style="margin-top:12px"><span class="spacer"></span><button class="btn primary" data-close>Selesai</button></div>`);
  const open = u => {
    const d = dialog(`<form><h2>${u ? 'Ubah pengguna' : 'Tambah pengguna'}</h2><div class="form-grid">
      <label>Nama<input name="name" value="${esc(u?.name || '')}" required></label><label>Email<input name="email" type="email" value="${esc(u?.email || '')}" required></label>
      <label>Peran<select name="role">${Object.entries(ROLE).map(([k, v]) => `<option value="${k}" ${u?.role === k ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
      <label class="check"><input type="checkbox" name="active" ${!u || u.active ? 'checked' : ''}> Aktif</label>
      <div class="wide"><h3>Mata kuliah (khusus dosen)</h3>${courseChecks(u?.courses || [])}</div></div>
      <p class="error"></p><div class="row">${u ? '<button type="button" class="btn" id="reset">Reset kata sandi</button>' : ''}<span class="spacer"></span><button type="button" class="btn" data-close>Batal</button><button class="btn primary">Simpan</button></div></form>`,
      async v => { const r = await api(u ? 'PATCH' : 'POST', u ? `/users/${u.id}` : '/users', v); toast('Tersimpan'); go(); if (r.temporary_password) showPw(r.email, r.temporary_password); });
    const reset = $('#reset', d);
    if (reset) reset.onclick = async () => { if (!confirm('Buat kata sandi baru untuk pengguna ini?')) return; const r = await guard(() => api('POST', `/users/${u.id}/reset-password`)); d.close(); showPw(u.email, r.temporary_password); };
  };
  $('#add').onclick = () => open(null);
  view.querySelectorAll('[data-edit]').forEach(b => b.onclick = () => open(users.find(x => x.id === +b.dataset.edit)));
}

// ------------------------------------------------------------ test chat
function md(text) {
  const out = []; let list = null;
  for (const raw of text.split('\n')) {
    const line = esc(raw).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/(^|[^\w*])\*(?!\s)([^*]+?)\*(?![\w*])/g, '$1<i>$2</i>');
    if (/^\s*-\s+/.test(raw)) { (list ||= []).push(`<li>${line.replace(/^\s*-\s+/, '')}</li>`); continue; }
    if (list) { out.push(`<ul>${list.join('')}</ul>`); list = null; }
    if (raw.trim()) out.push(`<p>${line}</p>`);
  }
  if (list) out.push(`<ul>${list.join('')}</ul>`);
  return out.join('');
}
async function viewTestChat(view) {
  if (can('admin', 'staff') && !S.students) S.students = await api('GET', '/students');
  const opts = (S.students || []).map(s => `<option value="${s.id}">Sebagai ${esc(s.name)} (${s.courses.length} MK)</option>`).join('');
  view.innerHTML = `<div class="card chatbox"><div class="row" style="margin-bottom:10px">
      <select id="who" style="max-width:300px"><option value="">Mahasiswa belum terhubung</option>${opts}</select>
      <select id="lang" style="max-width:150px"><option value="id">Bahasa Indonesia</option><option value="en">English</option></select>
      <span class="spacer"></span><button class="btn small" id="reset">Reset</button></div>
      <p class="muted small" style="margin:0 0 8px">Sama dengan bot Telegram (memakai flow Langflow yang sama). Uji chat tidak dihitung di dasbor; tiket dan pengingat dari sini bertanda "Panel".</p>
      <div class="chatlog" id="log"></div>
      <form id="send" class="row"><input id="msg" placeholder="Tulis pertanyaan seperti mahasiswa…" autocomplete="off"><button class="btn primary">Kirim</button></form></div>`;
  const log = $('#log');
  const who = () => ({ student_id: $('#who').value ? +$('#who').value : null, language: $('#lang').value });
  const add = (cls, html) => { const m = el(`<div class="msg ${cls}">${html}</div>`); log.append(m); log.scrollTop = log.scrollHeight; return m; };
  async function send(payload, label) {
    add('user', `<div class="bubble">${esc(label)}</div>`);
    const r = await guard(() => api('POST', '/testchat', { ...who(), ...payload }));
    const m = add('bot', '');
    for (const b of r.blocks) {
      if (b.type === 'text') m.append(el(`<div class="bubble">${md(b.text)}</div>`));
      else if (b.type === 'deadlines') m.append(el(`<div class="bubble"><p><b>📅 ${esc(b.range)}</b></p>${b.items.length ? `<ul>${b.items.map(i => `<li>${i.due_today ? '⚠️ ' : ''}<b>${esc(i.item)}</b> — ${esc(i.course)} · ${esc(i.due_text)}</li>`).join('')}</ul>` : '<p>Tidak ada.</p>'}<p class="muted small">Sumber: ${esc(b.source)}</p></div>`));
      else if (b.type === 'answer') m.append(el(`<div class="bubble"><blockquote>${esc(b.text)}</blockquote><p class="muted small">📖 ${esc(b.citation)}</p></div>`));
      else if (b.type === 'confirm') {
        const row = el(`<div class="row"><button class="btn primary small">${esc(b.yes)}</button><button class="btn small">${esc(b.no)}</button></div>`);
        const [y, n] = row.querySelectorAll('button'), pid = r.pending_id;
        y.onclick = () => { y.disabled = n.disabled = true; send({ confirm: true, pending_id: pid }, b.yes); };
        n.onclick = () => { y.disabled = n.disabled = true; send({ confirm: false, pending_id: pid }, b.no); };
        m.append(row);
      } else if (b.type === 'links') m.append(el(`<div class="bubble small">${b.links.map(l => `<a href="${esc(l.href)}" target="_blank" rel="noopener">${esc(l.label)}</a>`).join('<br>')}</div>`));
    }
    if (r.tool_calls?.length) m.append(el(`<div class="trace">🔧 ${r.tool_calls.map(c => esc(c.tool) + (c.status ? ' (' + esc(c.status) + ')' : '')).join(' → ')}</div>`));
    log.scrollTop = log.scrollHeight;
  }
  $('#send').onsubmit = e => { e.preventDefault(); const t = $('#msg').value.trim(); if (!t) return; $('#msg').value = ''; send({ message: t }, t); };
  const reset = async () => { await api('POST', '/testchat/reset', who()); log.innerHTML = ''; };
  $('#reset').onclick = reset; $('#who').onchange = reset;
}

// ------------------------------------------------------------ account
async function viewAccount(view) {
  view.innerHTML = `<div class="card" style="max-width:420px"><h2>Ganti kata sandi</h2><form class="stack" id="pw">
    <label>Kata sandi sekarang<input type="password" name="current" autocomplete="current-password" required></label>
    <label>Kata sandi baru (min. 10 karakter)<input type="password" name="new" autocomplete="new-password" minlength="10" required></label>
    <p class="error" id="pwerr"></p><button class="btn primary">Simpan</button></form></div>`;
  $('#pw').onsubmit = async e => { e.preventDefault(); $('#pwerr').textContent = ''; try { await api('POST', '/auth/password', formData(e.target)); e.target.reset(); toast('Kata sandi diganti'); } catch (x) { $('#pwerr').textContent = x.message; } };
}

// Interactive dot grid (DESIGN.md): dots near the pointer light up. Mouse only, and off with reduced motion.
if (matchMedia('(pointer: fine)').matches && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
  addEventListener('pointermove', e => {
    for (const g of document.querySelectorAll('.dotgrid')) { g.style.setProperty('--mx', e.clientX + 'px'); g.style.setProperty('--my', e.clientY + 'px'); }
  }, { passive: true });
}

// ------------------------------------------------------------ boot
(async () => {
  try { S.me = await api('GET', '/auth/me'); await start(); } catch { showLogin(); }
})();
