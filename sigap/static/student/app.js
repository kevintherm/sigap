// Sigap student portal: email-code sign-in, consent, linking a chat account, self-service.
'use strict';
const $ = (s, el = document) => el.querySelector(s);
const view = $('#view');
const LINK = new URLSearchParams(location.search).get('link');
function esc(v) { return String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function toast(m) { const t = $('#toast'); t.textContent = m; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => t.hidden = true, 3500); }
function fmt(iso) { if (!iso) return '–'; const d = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + 'Z');
  return d.toLocaleString('id-ID', { timeZone: 'Asia/Jakarta', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }); }
const STATUS = { open: 'Terbuka', in_progress: 'Diproses', answered: 'Dijawab', closed: 'Selesai' };
const CH = { telegram: 'Telegram', whatsapp: 'WhatsApp' };

async function api(method, path, body) {
  const r = await fetch('/api/student' + path, { method, credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-Sigap-Request': '1' }, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = r.headers.get('content-type')?.includes('json') ? await r.json() : null;
  if (!r.ok) { const e = new Error(typeof data?.detail === 'string' ? data.detail : `HTTP ${r.status}`); e.status = r.status; throw e; }
  return data;
}

// ------------------------------------------------------------ sign in
function showLogin(step = 1, identifier = '') {
  $('#logout').hidden = true;
  view.innerHTML = `<div class="card stack">
    <div><h2>Masuk</h2><p class="muted small" style="margin:0">Kami kirim kode 6 digit ke email kampus yang terdaftar.</p></div>
    ${LINK ? '<div class="notice">🔗 Kamu sedang menghubungkan akun chat ke Sigap. Setelah masuk, kamu akan diminta konfirmasi.</div>' : ''}
    <form id="f1" class="stack" ${step === 1 ? '' : 'hidden'}>
      <label>NIM atau email kampus<input name="identifier" autocomplete="username" required value="${esc(identifier)}" placeholder="2401001 atau nama@student.und.ac.id"></label>
      <p class="error" id="e1"></p><button class="btn primary big">Kirim kode</button></form>
    <form id="f2" class="stack" ${step === 2 ? '' : 'hidden'}>
      <p class="small" id="sent"></p>
      <label>Kode dari email<input name="code" class="code-input" inputmode="numeric" autocomplete="one-time-code" pattern="\\d{6}" maxlength="6" required></label>
      <p class="error" id="e2"></p><button class="btn primary big">Masuk</button>
      <button type="button" class="btn ghost small" id="back">Ganti NIM / kirim ulang</button></form></div>`;
  $('#f1').onsubmit = async e => {
    e.preventDefault(); $('#e1').textContent = '';
    const id = e.target.identifier.value.trim();
    try { const r = await api('POST', '/login/start', { identifier: id }); showLogin(2, id); $('#sent').textContent = r.message; $('#f2 input').focus(); }
    catch (x) { $('#e1').textContent = x.message; }
  };
  $('#f2').onsubmit = async e => {
    e.preventDefault(); $('#e2').textContent = '';
    try { await api('POST', '/login/verify', { identifier, code: e.target.code.value.trim() }); boot(); }
    catch (x) { $('#e2').textContent = x.message; }
  };
  $('#back').onclick = () => showLogin(1, identifier);
}

// ------------------------------------------------------------ consent
function showConsent(me) {
  view.innerHTML = `<div class="card stack"><div><h2>Pemberitahuan privasi</h2><p class="muted small" style="margin:0">Versi ${esc(me.consent.version)} · baca sebelum memakai Sigap.</p></div>
    <div class="privacy">
      <p><b>Sigap</b> adalah asisten AI layanan akademik. Untuk bekerja, Sigap memproses:</p>
      <ul><li>Data akademik dari kampus: nama, NIM, email kampus, dan mata kuliah yang kamu ambil.</li>
      <li>Pesan yang kamu kirim ke bot. Pesan diproses oleh sistem Sigap dan oleh penyedia AI (Google Gemini) untuk memahami pertanyaanmu. <b>Nama, NIM, dan akun chat-mu tidak dikirim ke penyedia AI</b>, tetapi isi pesan dikirim — jangan menulis data sensitif (kesehatan, nomor identitas, kata sandi).</li>
      <li>Pertanyaan yang kamu teruskan ke staf, beserta balasannya.</li>
      <li>Statistik pemakaian tanpa isi pesan (kecuali pertanyaan yang tidak terjawab, disimpan tanpa identitasmu untuk memperbaiki pedoman).</li></ul>
      <p>Sigap tidak menjual data, tidak menampilkan iklan, dan tidak membuat keputusan akademik — keputusan tetap di tangan staf. Jawaban bisa salah; selalu cek sumber yang disebutkan.</p>
      <p>Sesuai UU PDP No. 27/2022 kamu berhak mengakses, memperbaiki, dan meminta penghapusan data pribadimu lewat portal ini atau Layanan Akademik (layanan.akademik@und.ac.id).</p>
      <p class="muted">Prototipe hackathon: semua data mahasiswa di demo ini sintetis.</p></div>
    <label class="check"><input type="checkbox" id="agree"> Saya sudah membaca dan setuju.</label>
    <p class="error" id="ce"></p><button class="btn primary big" id="ok" disabled>Setuju dan lanjutkan</button></div>`;
  $('#agree').onchange = e => $('#ok').disabled = !e.target.checked;
  $('#ok').onclick = async () => { try { await api('POST', '/consent', { accept: true }); boot(); } catch (x) { $('#ce').textContent = x.message; } };
}

// ------------------------------------------------------------ link confirmation
async function showLink(me) {
  let info;
  try { info = await api('GET', `/link/${encodeURIComponent(LINK)}`); }
  catch (x) { history.replaceState(null, '', '/student'); toast(x.message); return showPortal(me, x.message); }
  if (info.already_linked_to_you) { await api('POST', `/link/${encodeURIComponent(LINK)}/confirm`); return done(info, me); }
  view.innerHTML = `<div class="card stack"><h2>Hubungkan akun chat</h2>
    <p class="big">Hubungkan <b>${esc(CH[info.channel] || info.channel)}: ${esc(info.display_name || 'akun tanpa nama')}</b> ke akun Sigap <b>${esc(me.name)}</b> (NIM ${esc(me.number)})?</p>
    <div class="warn">⚠️ Lanjutkan hanya jika itu akun ${esc(CH[info.channel] || info.channel)}-mu sendiri dan kamu yang menekan tombol Masuk di bot. Jika seseorang mengirimimu tautan ini, tekan Batal.</div>
    ${info.currently_linked_to_other ? '<div class="notice">Akun chat ini sebelumnya terhubung ke mahasiswa lain dan akan dipindahkan ke akunmu.</div>' : ''}
    <p class="error" id="le"></p>
    <div class="row"><button class="btn primary big" id="yes">Ya, hubungkan</button><button class="btn" id="no">Batal</button></div></div>`;
  $('#yes').onclick = async () => { try { await api('POST', `/link/${encodeURIComponent(LINK)}/confirm`); done(info, me); } catch (x) { $('#le').textContent = x.message; } };
  $('#no').onclick = () => { history.replaceState(null, '', '/student'); showPortal(me); };
}
function done(info, me) {
  history.replaceState(null, '', '/student');
  view.innerHTML = `<div class="card stack" style="text-align:center"><div style="font-size:42px">✅</div><h2>Terhubung!</h2>
    <p>${esc(CH[info.channel] || info.channel)} sekarang terhubung ke akun <b>${esc(me.name)}</b>. Kembali ke chat — Sigap sudah mengirim pesan konfirmasi.</p>
    ${me.bot_username ? `<a class="btn primary big" href="https://t.me/${esc(me.bot_username)}">Buka Telegram</a>` : ''}
    <button class="btn ghost" id="portal">Lihat portal</button></div>`;
  $('#portal').onclick = boot;
}

// ------------------------------------------------------------ portal
function showPortal(me, warning) {
  view.innerHTML = `
    ${warning ? `<div class="warn">${esc(warning)}</div>` : ''}
    <div class="card"><h2>${esc(me.name)}</h2><p class="muted small" style="margin:0">NIM ${esc(me.number)} · ${esc(me.email || '')}${me.program ? ' · ' + esc(me.program) : ''}</p>
      <div class="chips" style="margin-top:8px">${me.courses.map(c => `<span class="badge">${esc(c)}</span>`).join('')}</div></div>
    <div class="card"><h2>Akun chat terhubung</h2>
      ${me.channels.length ? me.channels.map(c => `<div class="item"><span>${esc(CH[c.channel] || c.channel)} · ${esc(c.display_name || '')}</span><span class="muted small">${fmt(c.linked_at)}</span><span class="spacer"></span><button class="btn small danger" data-unlink="${c.id}">Putuskan</button></div>`).join('')
        : `<p class="muted small">Belum ada. Buka bot ${me.bot_username ? `<a href="https://t.me/${esc(me.bot_username)}">@${esc(me.bot_username)}</a>` : 'Sigap di Telegram'} dan tekan <b>Masuk</b> (atau ketik /login).</p>`}</div>
    <div class="card"><h2>Pertanyaanku ke staf</h2>
      ${me.tickets.length ? me.tickets.map(t => `<div class="item" style="flex-direction:column;align-items:stretch;gap:4px"><div class="row"><code>${esc(t.reference)}</code><span class="badge ${t.status}">${STATUS[t.status] || t.status}</span><span class="spacer"></span><span class="muted small">${fmt(t.created_at)}</span></div>
        <div>${esc(t.question)}</div>${t.reply ? `<div class="small">💬 ${esc(t.reply)}</div>` : ''}</div>`).join('') : '<p class="muted small">Belum ada.</p>'}</div>
    <div class="card"><h2>Token agen AI (MCP)</h2>
      <p class="muted small">Untuk menghubungkan agen AI-mu sendiri (Bob, Claude Code) ke Sigap. Token bertindak sebagai kamu; jangan bagikan.</p>
      ${me.tokens.map(t => `<div class="item"><code>${esc(t.prefix)}…</code><span class="muted small">${esc(t.label || '')} · ${t.revoked ? 'dicabut' : 'dipakai ' + fmt(t.last_used_at)}</span><span class="spacer"></span>${t.revoked ? '' : `<button class="btn small danger" data-revoke="${t.id}">Cabut</button>`}</div>`).join('')}
      <div id="newtok"></div><button class="btn" id="mk" style="margin-top:8px">+ Buat token</button></div>
    <div class="card"><h2>Privasi</h2>
      <p class="small">Kamu menyetujui pemberitahuan privasi pada ${fmt(me.consent.at)} (versi ${esc(me.consent.version)}).</p>
      <button class="btn danger" id="del">Minta penghapusan data pribadi</button></div>`;
  view.querySelectorAll('[data-unlink]').forEach(b => b.onclick = async () => { if (!confirm('Putuskan akun chat ini?')) return; await api('DELETE', `/channels/${b.dataset.unlink}`); toast('Diputus'); boot(); });
  view.querySelectorAll('[data-revoke]').forEach(b => b.onclick = async () => { if (!confirm('Cabut token ini?')) return; await api('DELETE', `/tokens/${b.dataset.revoke}`); toast('Token dicabut'); boot(); });
  $('#mk').onclick = async () => {
    try {
      const { token } = await api('POST', '/tokens', { label: 'via portal' });
      $('#newtok').innerHTML = `<div class="secret" style="margin-top:8px"><b>Salin sekarang — hanya ditampilkan sekali:</b><br><code>${esc(token)}</code><br><br>
        <span class="small">Claude Code:</span><br><code>claude mcp add --transport http sigap ${esc(me.mcp_url)} --header "Authorization: Bearer ${esc(token)}"</code></div>`;
      $('#mk').hidden = true;
    } catch (x) { toast(x.message); }
  };
  $('#del').onclick = async () => {
    if (!confirm('Kirim permintaan penghapusan data pribadi ke Layanan Akademik?')) return;
    const r = await api('POST', '/deletion-request');
    toast(r.existing ? `Permintaanmu sudah tercatat: ${r.reference}` : `Permintaan dikirim: ${r.reference}`); boot();
  };
}

// Interactive dot grid (DESIGN.md): dots near the pointer light up. Mouse only, and off with reduced motion.
if (matchMedia('(pointer: fine)').matches && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
  addEventListener('pointermove', e => {
    for (const g of document.querySelectorAll('.dotgrid')) { g.style.setProperty('--mx', e.clientX + 'px'); g.style.setProperty('--my', e.clientY + 'px'); }
  }, { passive: true });
}

$('#logout').onclick = async () => { await api('POST', '/logout'); showLogin(); };

async function boot() {
  let me;
  try { me = await api('GET', '/me'); } catch (x) { return showLogin(); }
  $('#logout').hidden = false;
  if (!me.consent.accepted) return showConsent(me);
  if (LINK && new URLSearchParams(location.search).get('link')) return showLink(me);
  showPortal(me);
}
boot();
