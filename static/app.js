'use strict';
/* ==========================================================================
   Davra — asosiy mantiq: kirish, WebSocket, suhbatlar ro'yxati, xabarlar,
   yuborish (rasm/fayl/ovoz), kontekst menyu, bildirishnomalar.
   Menyu, sozlamalar va oynalar panels.js da.
   ========================================================================== */

// ---------------------------------------------------------------- yordamchilar
const $ = id => document.getElementById(id);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'style') el.style.cssText = v;
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const lsGet = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch { return d; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* xotira yopiq bo'lishi mumkin */ } };
const lsDel = k => { try { localStorage.removeItem(k); } catch { /* */ } };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const MONTHS = ['yanvar', 'fevral', 'mart', 'aprel', 'may', 'iyun', 'iyul', 'avgust', 'sentabr', 'oktabr', 'noyabr', 'dekabr'];
const REACTIONS = ['👍', '❤️', '😂', '😮', '😢', '🔥', '👏', '✅'];
const EMOJIS = ('😀 😃 😄 😁 😆 😅 😂 🤣 😊 😇 🙂 😉 😍 🥰 😘 😋 😎 🤩 🥳 😏 😒 😔 😢 😭 😤 😡 🤯 😱 🤔 🤗 🤭 😴 🙄 😬 🤝 👍 👎 👏 🙏 💪 👌 ✌️ 🤞 👋 ❤️ 🧡 💛 💚 💙 💜 🖤 💔 🔥 ✨ 🎉 🎁 ✅ ❌ ⭐ 💯 🌹 ☀️ 🌙 ☕ 🍕 🍎 ⚽ 🚀').split(' ');

const S = {
  token: null, me: null,
  chats: [], rooms: [], invites: [],
  cur: null,                 // {kind:'direct'|'room', id}
  msgs: [], hasMore: false, hasNewer: false, detached: false, loadingOlder: false,
  members: {},               // room_id -> [{id,...}]
  reads: {},                 // room_id -> {uid: last_read_id}
  reply: null, attach: null, forwardingMsg: null,
  filter: 'all', settings: null, ws: null, wsOk: false, newWhileUp: 0,
  typing: {}, tmp: 0, drafts: lsGet('drafts', {}),
};

const DEFAULTS = () => ({
  notif: { direct: true, groups: true, channels: true, preview: true, sound: true },
  muted: [],
  chat: { textSize: 15, bg: 'pattern', enterSend: true, hour12: false },
  folders: [],
  advanced: { reduceMotion: false, scale: 100 },
});

// ---------------------------------------------------------------- tema va ko'rinish
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const meta = document.querySelector('meta[name=theme-color]');
  if (meta) meta.content = theme === 'light' ? '#ffffff' : '#17212b';
  const t = $('menuDarkToggle'); if (t) t.checked = theme !== 'light';
  lsSet('theme', theme);
}
function applySettings() {
  const s = S.settings || DEFAULTS();
  const root = document.documentElement;
  root.dataset.bg = s.chat.bg;
  root.style.setProperty('--msg-size', s.chat.textSize + 'px');
  root.style.setProperty('--ui-scale', String(s.advanced.scale / 100));
  document.body.classList.toggle('reduce-motion', !!s.advanced.reduceMotion);
}
function mergeSettings(raw) {
  const d = DEFAULTS(); raw = raw || {};
  return {
    notif: Object.assign(d.notif, raw.notif), muted: Array.isArray(raw.muted) ? raw.muted : [],
    chat: Object.assign(d.chat, raw.chat), folders: Array.isArray(raw.folders) ? raw.folders : [],
    advanced: Object.assign(d.advanced, raw.advanced),
  };
}
let saveTimer = null;
function saveSettings() {
  lsSet('settings:' + (S.me ? S.me.id : 0), S.settings);
  applySettings();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    api('/settings', { method: 'PUT', body: { data: S.settings } }).catch(() => { });
  }, 600);
}
async function loadSettings() {
  S.settings = mergeSettings(lsGet('settings:' + S.me.id, null));
  applySettings();
  try {
    const r = await api('/settings');
    if (r.data && Object.keys(r.data).length) { S.settings = mergeSettings(r.data); lsSet('settings:' + S.me.id, S.settings); applySettings(); }
  } catch { /* oflayn — kesh ishlatiladi */ }
}

// ---------------------------------------------------------------- vaqt, matn
function pad(n) { return n < 10 ? '0' + n : '' + n; }
function fmtTime(ts) {
  const d = new Date(ts * 1000);
  if (S.settings && S.settings.chat.hour12) { const hr = d.getHours() % 12 || 12; return hr + ':' + pad(d.getMinutes()) + (d.getHours() < 12 ? ' AM' : ' PM'); }
  return pad(d.getHours()) + ':' + pad(d.getMinutes());
}
function dayKey(ts) { const d = new Date(ts * 1000); return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
function dayLabel(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const diff = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()) - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 864e5);
  if (diff === 0) return 'Bugun';
  if (diff === 1) return 'Kecha';
  return d.getDate() + ' ' + MONTHS[d.getMonth()] + (d.getFullYear() !== now.getFullYear() ? ' ' + d.getFullYear() : '');
}
function listTime(ts) {
  if (!ts) return '';
  const diff = Math.round((Date.now() / 1000 - ts) / 86400);
  if (dayKey(ts) === dayKey(Date.now() / 1000)) return fmtTime(ts);
  if (diff < 7) return ['yak', 'dush', 'sesh', 'chor', 'pay', 'jum', 'shan'][new Date(ts * 1000).getDay()];
  const d = new Date(ts * 1000); return pad(d.getDate()) + '.' + pad(d.getMonth() + 1);
}
function lastSeenText(u) {
  if (!u) return '';
  if (u.online) return 'onlayn';
  if (u.hidden || !u.last_seen) return 'yaqinda onlayn bo‘lgan';
  const mins = Math.floor((Date.now() / 1000 - u.last_seen) / 60);
  if (mins < 1) return 'hozirgina onlayn edi';
  if (mins < 60) return mins + ' daqiqa oldin onlayn edi';
  const k = dayKey(u.last_seen), today = dayKey(Date.now() / 1000);
  if (k === today) return 'bugun ' + fmtTime(u.last_seen) + ' da onlayn edi';
  if (k === dayKey(Date.now() / 1000 - 86400)) return 'kecha ' + fmtTime(u.last_seen) + ' da onlayn edi';
  const d = new Date(u.last_seen * 1000); return d.getDate() + ' ' + MONTHS[d.getMonth()] + ' kuni onlayn edi';
}
function fmtSize(n) { if (n < 1024) return n + ' B'; if (n < 1048576) return (n / 1024).toFixed(0) + ' KB'; return (n / 1048576).toFixed(1) + ' MB'; }
function fmtDur(sec) { sec = Math.max(0, Math.round(sec || 0)); return Math.floor(sec / 60) + ':' + pad(sec % 60); }
function snip(t, n = 80) { t = (t || '').replace(/\s+/g, ' ').trim(); return t.length > n ? t.slice(0, n - 1) + '…' : t; }
function attLabel(a) { if (!a) return ''; return a.kind === 'image' ? '📷 Rasm' : a.kind === 'voice' ? '🎤 Ovozli xabar' : '📎 ' + (a.name || 'Fayl'); }
function msgPreview(m) { if (!m) return ''; if (m.deleted) return 'Xabar o‘chirildi'; const a = attLabel(m.attachment); return a ? (m.text ? a + ' ' + snip(m.text, 40) : a) : snip(m.text); }

const URL_RE = /(https?:\/\/[^\s<]+[^\s<.,;:!?)\]'"])|(@[a-zA-Z0-9_]{3,32})/g;
function richText(text) {
  const frag = document.createDocumentFragment();
  let last = 0; URL_RE.lastIndex = 0; let m;
  while ((m = URL_RE.exec(text))) {
    if (m.index > last) frag.append(text.slice(last, m.index));
    if (m[1]) frag.append(h('a', { href: m[1], target: '_blank', rel: 'noopener noreferrer nofollow', text: m[1] }));
    else {
      const me = S.me && m[2].slice(1).toLowerCase() === S.me.username;
      frag.append(h('span', { class: 'mention' + (me ? ' me' : ''), text: m[2] }));
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) frag.append(text.slice(last));
  return frag;
}

// ---------------------------------------------------------------- avatar
const COLORS = ['#e17076', '#e5a550', '#7bc862', '#4fb4c8', '#6c9ce6', '#a881d9', '#ee7aae', '#3fa49a'];
function avatarEl(entity, size) {
  const name = entity.display_name || entity.title || '?';
  const id = entity.id || 0;
  const el = h('div', { class: 'avatar', style: `background-color:${COLORS[id % COLORS.length]}` + (size ? `;width:${size}px;height:${size}px;font-size:${Math.round(size * .38)}px` : '') });
  el.append((Array.from(name.trim())[0] || '?').toUpperCase());
  if (entity.avatar_url) {
    const img = h('img', { class: 'av-img', src: entity.avatar_url, alt: '', loading: 'lazy' });
    img.addEventListener('error', () => img.remove());
    el.append(img);
  }
  return el;
}
const verifiedBadge = () => {
  const s = h('span', { class: 'verified-badge', title: 'Davra tomonidan tasdiqlangan', role: 'img', 'aria-label': 'Davra tomonidan tasdiqlangan' });
  // 8 qirrali "muhr" shakli + gradient + oq belgi (oddiy ptichka emas)
  s.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><g fill="url(#vbGrad)" stroke="url(#vbGrad)" stroke-width="1.2" stroke-linejoin="round"><rect x="4.6" y="4.6" width="14.8" height="14.8" rx="2.6"/><rect x="4.6" y="4.6" width="14.8" height="14.8" rx="2.6" transform="rotate(30 12 12)"/><rect x="4.6" y="4.6" width="14.8" height="14.8" rx="2.6" transform="rotate(60 12 12)"/></g><path d="M7.9 12.5l2.9 2.8 5.4-6" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  return s;
};

// ---------------------------------------------------------------- toast va qalqib chiquvchi menyu
let toastTimer;
function toast(msg, ms = 2600) {
  const el = $('toast'); el.textContent = msg; el.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}
function closePopups() { $('ctxMenu').hidden = true; $('attachMenu').hidden = true; }
document.addEventListener('pointerdown', e => {
  if (!e.target.closest('.popup') && !e.target.closest('#attachBtn') && !e.target.closest('#chatMoreBtn')) closePopups();
}, true);
window.addEventListener('resize', closePopups);
function showPopup(items, x, y, header) {
  const menu = $('ctxMenu'); menu.replaceChildren();
  if (header) menu.append(header);
  for (const it of items) {
    if (it === '-') { menu.append(h('hr')); continue; }
    menu.append(h('button', { type: 'button', role: 'menuitem', class: it.danger ? 'danger' : '', onclick: () => { closePopups(); it.run(); } }, h('span', { text: it.icon || '' }), it.label));
  }
  menu.hidden = false; menu.style.left = '0px'; menu.style.top = '0px';
  const r = menu.getBoundingClientRect();
  menu.style.left = Math.max(8, Math.min(x, innerWidth - r.width - 8)) + 'px';
  menu.style.top = Math.max(8, Math.min(y, innerHeight - r.height - 8)) + 'px';
}

// ---------------------------------------------------------------- API
async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  if (S.token) headers.Authorization = 'Bearer ' + S.token;
  let body = opts.body;
  if (body && typeof body === 'object' && !(body instanceof Blob) && !(body instanceof ArrayBuffer)) { body = JSON.stringify(body); headers['Content-Type'] = 'application/json'; }
  let res;
  try { res = await fetch('/api' + path, { method: opts.method || 'GET', headers, body }); }
  catch { throw new Error('Internet bilan aloqa yo‘q'); }
  let data = null; try { data = await res.json(); } catch { /* bo'sh javob */ }
  if (res.status === 401 && S.token && !opts.noAuthRedirect) { doLogout(true); throw new Error('Qaytadan kiring'); }
  if (!res.ok) {
    const d = data && data.detail;
    throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? 'Ma‘lumot noto‘g‘ri kiritildi' : 'Xatolik (' + res.status + ')');
  }
  return data;
}

// ---------------------------------------------------------------- kirish / ro'yxatdan o'tish
let regMode = false;
function setAuthMode(reg) {
  regMode = reg;
  $('tabLogin').classList.toggle('on', !reg); $('tabReg').classList.toggle('on', reg);
  $('aName').hidden = !reg; $('aBtn').textContent = reg ? 'Ro‘yxatdan o‘tish' : 'Kirish';
  $('aPass').autocomplete = reg ? 'new-password' : 'current-password'; $('aErr').textContent = '';
}
function showAuth() { $('app').hidden = true; $('auth').hidden = false; }
async function submitAuth(e) {
  e.preventDefault();
  const btn = $('aBtn'); btn.disabled = true; $('aErr').textContent = '';
  try {
    const body = { username: $('aUser').value, password: $('aPass').value };
    if (regMode) body.display_name = $('aName').value;
    const r = await api(regMode ? '/register' : '/login', { method: 'POST', body, noAuthRedirect: true });
    S.token = r.token; S.me = r.me; lsSet('token', r.token);
    $('aPass').value = '';
    startApp();
  } catch (err) { $('aErr').textContent = err.message; }
  btn.disabled = false;
}
async function doLogout(silent) {
  if (!silent && S.token) {
    try { await pushDropDevice(); } catch { /* */ }
    try { await api('/logout', { method: 'POST', noAuthRedirect: true }); } catch { /* */ }
  }
  if (S.ws) { S.ws.onclose = null; try { S.ws.close(); } catch { /* */ } S.ws = null; }
  clearInterval(pingTimer);
  lsDel('token');
  Object.assign(S, { token: null, me: null, chats: [], rooms: [], invites: [], cur: null, msgs: [], members: {}, reads: {}, reply: null, attach: null });
  $('chatView').hidden = true; $('empty').hidden = false; $('app').classList.remove('chat-open');
  closeAllDialogs();
  document.title = 'Davra'; if (navigator.clearAppBadge) navigator.clearAppBadge().catch(() => { });
  showAuth();
  if (silent) toast('Seans tugadi. Qaytadan kiring');
}
function closeAllDialogs() { $$('dialog[open]').forEach(d => { try { d.close(); } catch { /* */ } }); }

// ---------------------------------------------------------------- ishga tushirish
function boot() {
  applyTheme(lsGet('theme', 'dark'));
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => { });
    navigator.serviceWorker.addEventListener('message', e => { if (e.data && e.data.type === 'open-chat') openByKey(e.data.key); });
  }
  $('tabLogin').onclick = () => setAuthMode(false);
  $('tabReg').onclick = () => setAuthMode(true);
  $('authForm').addEventListener('submit', submitAuth);
  wireUi();
  const token = lsGet('token', null);
  if (!token) return showAuth();
  S.token = token;
  api('/me', { noAuthRedirect: true }).then(me => { S.me = me; startApp(); }).catch(err => {
    if (/Internet/.test(err.message)) { toast('Internet yo‘q. Qayta urinilmoqda…'); setTimeout(boot, 3000); } else { lsDel('token'); S.token = null; showAuth(); }
  });
}
async function startApp() {
  $('auth').hidden = true; $('app').hidden = false;
  await loadSettings();
  if (typeof refreshMenuProfile === 'function') refreshMenuProfile();
  await loadAll();
  connectWS();
  pushResync();
  const param = new URLSearchParams(location.search).get('chat');
  if (param) { openByKey(param); history.replaceState(null, '', '/'); }
  const jc = lsGet('joinCode');
  if (jc) {
    lsDel('joinCode');
    try { const r = await api('/join/' + encodeURIComponent(jc), { method: 'POST' }); upsertRoom(r); renderList(); openChat('room', r.id); toast('“' + r.title + '” ga qo‘shildingiz'); }
    catch (e) { toast(e.message); }
  }
}
{ const jp = new URLSearchParams(location.search).get('join'); if (jp) { lsSet('joinCode', jp); history.replaceState(null, '', '/'); } }
async function loadAll() {
  try {
    const [chats, rooms, invites] = await Promise.all([api('/chats'), api('/rooms'), api('/invites')]);
    S.chats = chats; S.rooms = rooms; S.invites = invites;
  } catch (e) { toast(e.message); }
  renderList();
}

// ---------------------------------------------------------------- WebSocket
let pingTimer = null, retry = 0;
function connectWS() {
  if (!S.token) return;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(S.token)}`);
  S.ws = ws;
  ws.onopen = () => {
    const wasDown = retry > 0; retry = 0; S.wsOk = true; $('conn').textContent = '';
    sendVisibility();
    clearInterval(pingTimer); pingTimer = setInterval(() => wsSend({ type: 'ping' }), 25000);
    if (wasDown) resync();
  };
  ws.onmessage = e => { let d; try { d = JSON.parse(e.data); } catch { return; } try { onWs(d); } catch (err) { console.error(err); } };
  ws.onclose = ev => {
  S.wsOk = false; clearInterval(pingTimer);
  if (ev.code === 4401) return doLogout(true);
   $('conn').textContent = 'ulanmoqda…';
    retry++;
    // Vercel tez uzadi, shuning uchun tezroq qayta ulanamiz (maks 2 soniya)
    setTimeout(() => { if (S.token) connectWS(); }, Math.min(2000, 300 * retry));
  };
  };

function wsSend(obj) { if (S.ws && S.ws.readyState === 1) { S.ws.send(JSON.stringify(obj)); return true; } return false; }
function sendVisibility() { wsSend({ type: 'visibility', visible: document.visibilityState === 'visible' }); }
document.addEventListener('visibilitychange', () => { sendVisibility(); if (document.visibilityState === 'visible') { markRead(); updateBadge(); } });
async function resync() {
  await loadAll();
  if (S.cur && !S.detached) { try { await loadMessages(); } catch { /* */ } }
}

// ---------------------------------------------------------------- suhbatlar ro'yxati
const keyOf = (kind, id) => kind === 'room' ? 'r:' + id : String(id);
const itemKey = it => it.kind === 'room' ? 'r:' + it.data.id : String(it.data.id);
const isMuted = key => S.settings && S.settings.muted.includes(key);
function allItems() {
  return [...S.chats.map(c => ({ kind: 'direct', data: c })), ...S.rooms.map(r => ({ kind: 'room', data: r }))];
}
function itemTime(it) { const lm = it.data.last_message; return lm ? lm.created_at : it.data.created_at || 0; }
function itemUnread(it) { return it.data.unread || 0; }
function filterItems(items) {
  const f = S.filter;
  if (f === 'all') return items;
  if (f === 'direct') return items.filter(i => i.kind === 'direct');
  if (f === 'group') return items.filter(i => i.kind === 'room' && i.data.room_type === 'group');
  if (f === 'channel') return items.filter(i => i.kind === 'room' && i.data.room_type === 'channel');
  const folder = S.settings.folders.find(x => x.id === f);
  return folder ? items.filter(i => folder.chats.includes(itemKey(i))) : items;
}
function renderFilters(items) {
  const defs = [{ id: 'all', name: 'Hammasi' }];
  if (S.chats.length) defs.push({ id: 'direct', name: 'Shaxsiy' });
  if (S.rooms.some(r => r.room_type === 'group')) defs.push({ id: 'group', name: 'Guruhlar' });
  if (S.rooms.some(r => r.room_type === 'channel')) defs.push({ id: 'channel', name: 'Kanallar' });
  for (const f of S.settings.folders) defs.push(f);
  if (!defs.some(d => d.id === S.filter)) S.filter = 'all';
  const box = $('chatFilters'); box.replaceChildren();
  box.hidden = defs.length < 2;
  for (const d of defs) {
    const n = (() => { const saved = S.filter; S.filter = d.id; const c = filterItems(items).filter(i => itemUnread(i) && !isMuted(itemKey(i))).length; S.filter = saved; return c; })();
    box.append(h('button', { type: 'button', class: d.id === S.filter ? 'active' : '', onclick: () => { S.filter = d.id; renderList(); } }, d.name, n ? h('span', { class: 'fbadge', text: n }) : null));
  }
}
function renderList() {
  if (!S.settings) return;
  const items = allItems();
  renderFilters(items);
  const list = filterItems(items).sort((a, b) => itemTime(b) - itemTime(a));
  const box = $('chatList'); box.replaceChildren();
  for (const inv of S.invites) {
    box.append(h('div', { class: 'invite-item' },
      h('div', { class: 'invite-info' }, h('b', { text: (inv.kind === 'channel' ? '📣 ' : '👥 ') + inv.title }), h('span', { text: '@' + inv.invited_by_username + ' taklif qildi' })),
      h('div', { class: 'invite-actions' },
        h('button', { class: 'b-ok', type: 'button', onclick: () => acceptInvite(inv) }, 'Qo‘shilish'),
        h('button', { class: 'b-no', type: 'button', onclick: () => declineInvite(inv) }, 'Rad etish'))));
  }
  if (!list.length && !S.invites.length) {
    box.append(h('div', { class: 'emptyl', text: S.filter === 'all' ? 'Hali suhbat yo‘q. Yuqoridan odam qidiring yoki guruh yarating.' : 'Bu papkada suhbat yo‘q.' }));
  }
  for (const it of list) box.append(chatItemEl(it));
  updateBadge();
}
function chatItemEl(it) {
  const d = it.data, key = itemKey(it);
  const isRoom = it.kind === 'room';
  const entity = isRoom ? d : d.peer;
  const lm = d.last_message;
  let pv = '';
  if (lm) {
    const mine = lm.sender_id === S.me.id;
    const who = mine ? 'Siz: ' : (isRoom && d.room_type === 'group' && lm.sender_name ? lm.sender_name.split(' ')[0] + ': ' : '');
    pv = who + msgPreview(lm);
  } else if (!isRoom && d.status === 'pending') pv = d.i_am_requester ? 'So‘rov yuborildi' : 'Yangi suhbat so‘rovi';
  else pv = isRoom ? (d.room_type === 'channel' ? 'Kanal yaratildi' : 'Guruh yaratildi') : 'Xabar yo‘q';
  const active = S.cur && S.cur.kind === it.kind && S.cur.id === d.id;
  const muted = isMuted(key);
  const draft = S.drafts[key];
  const nameRow = h('div', { class: 'nm' }, h('span', { class: 'name-label', text: isRoom ? d.title : d.peer.display_name }),
    (isRoom ? d.verified : d.peer.is_owner) ? verifiedBadge() : null, isRoom ? h('span', { class: 'room-type', text: d.room_type === 'channel' ? 'kanal' : 'guruh' }) : null,
    muted ? h('span', { class: 'mute-mark', text: '🔕' }) : null);
  const el = h('button', { type: 'button', class: 'item' + (active ? ' active' : ''), onclick: () => openChat(it.kind, d.id) },
    h('div', { style: 'position:relative;flex:none' }, avatarEl(entity), !isRoom && d.peer.online ? h('span', { class: 'dot' }) : null),
    h('div', { class: 'ib' },
      h('div', { class: 'row' }, nameRow, h('span', { class: 'tm', text: listTime(lm ? lm.created_at : 0) })),
      h('div', { class: 'row' },
        h('span', { class: 'pv' }, draft && !active ? [h('span', { class: 'you', text: 'Qoralama: ' }), snip(draft, 40)] : pv),
        d.unread ? h('span', { class: 'badge' + (muted ? ' muted' : ''), text: d.unread > 99 ? '99+' : d.unread }) : (!isRoom && d.status === 'pending' && !d.i_am_requester ? h('span', { class: 'req', text: 'so‘rov' }) : null))));
  return el;
}
async function acceptInvite(inv) {
  try {
    const room = await api('/invites/' + inv.id + '/accept', { method: 'POST' });
    S.invites = S.invites.filter(i => i.id !== inv.id); upsertRoom(room); renderList(); openChat('room', room.id);
  } catch (e) { toast(e.message); }
}
async function declineInvite(inv) {
  try { await api('/invites/' + inv.id + '/decline', { method: 'POST' }); S.invites = S.invites.filter(i => i.id !== inv.id); renderList(); } catch (e) { toast(e.message); }
}
function upsertChat(c) { const i = S.chats.findIndex(x => x.id === c.id); if (i >= 0) S.chats[i] = c; else S.chats.push(c); }
function upsertRoom(r) { const i = S.rooms.findIndex(x => x.id === r.id); if (i >= 0) S.rooms[i] = r; else S.rooms.push(r); }
const curData = () => !S.cur ? null : (S.cur.kind === 'room' ? S.rooms : S.chats).find(x => x.id === S.cur.id) || null;
function openByKey(key) {
  key = String(key || '');
  if (key.startsWith('r:')) { const id = parseInt(key.slice(2), 10); if (S.rooms.some(r => r.id === id)) openChat('room', id); }
  else { const id = parseInt(key, 10); if (S.chats.some(c => c.id === id)) openChat('direct', id); }
}
function updateBadge() {
  let n = 0;
  for (const it of allItems()) if (!isMuted(itemKey(it))) n += itemUnread(it);
  document.title = n ? `(${n}) Davra` : 'Davra';
  if (navigator.setAppBadge) { (n ? navigator.setAppBadge(n) : navigator.clearAppBadge()).catch(() => { }); }
}

// ---------------------------------------------------------------- qidiruv
let searchTimer;
function wireSearch() {
  $('search').addEventListener('input', e => {
    clearTimeout(searchTimer);
    const q = e.target.value.trim().replace(/^@/, '');
    const box = $('searchResults');
    if (q.length < 2) { box.hidden = true; box.replaceChildren(); return; }
    searchTimer = setTimeout(async () => {
      try {
        const users = await api('/users?q=' + encodeURIComponent(q));
        box.replaceChildren(h('div', { class: 'sec', text: 'Odamlar' }));
        if (!users.length) box.append(h('div', { class: 'emptyl', text: 'Hech kim topilmadi' }));
        for (const u of users) box.append(h('button', { type: 'button', class: 'item', onclick: () => { startChat(u.username); $('search').value = ''; box.hidden = true; } },
          avatarEl(u), h('div', { class: 'ib' }, h('div', { class: 'nm' }, u.display_name, u.is_owner ? verifiedBadge() : null), h('div', { class: 'pv', text: '@' + u.username }))));
        box.hidden = false;
      } catch (err) { toast(err.message); }
    }, 250);
  });
  $('messageSearch').addEventListener('input', e => {
    clearTimeout(searchTimer);
    const q = e.target.value.trim(); const box = $('messageResults');
    if (q.length < 2) { box.hidden = true; box.replaceChildren(); return; }
    searchTimer = setTimeout(async () => {
      try {
        const res = await api('/search?q=' + encodeURIComponent(q));
        box.replaceChildren(h('div', { class: 'sec', text: 'Xabarlar' }));
        if (!res.length) box.append(h('div', { class: 'emptyl', text: 'Xabar topilmadi' }));
        for (const r of res) box.append(h('button', { type: 'button', class: 'item', onclick: async () => { box.hidden = true; $('messageSearch').value = ''; await openChat(r.kind, r.id, { around: r.message_id }); } },
          h('div', { class: 'ib' }, h('div', { class: 'row' }, h('div', { class: 'nm', text: r.title }), h('span', { class: 'tm', text: listTime(r.created_at) })), h('div', { class: 'pv', text: snip(r.text, 70) }))));
        box.hidden = false;
      } catch (err) { toast(err.message); }
    }, 300);
  });
  $('browseChannels').onclick = async () => {
    const box = $('channelResults');
    if (!box.hidden) { box.hidden = true; return; }
    try {
      const list = await api('/channels');
      box.replaceChildren(h('div', { class: 'sec', text: 'Ochiq kanallar' }));
      if (!list.length) box.append(h('div', { class: 'emptyl', text: 'Hozircha ochiq kanal yo‘q' }));
      for (const c of list) box.append(h('div', { class: 'item', style: 'cursor:default' },
        avatarEl({ id: c.id, title: c.title }), h('div', { class: 'ib' }, h('div', { class: 'nm' }, c.title, c.verified ? verifiedBadge() : null), h('div', { class: 'pv', text: c.member_count + ' obunachi' })),
        c.joined ? h('button', { class: 'btn small', type: 'button', onclick: () => { box.hidden = true; openChat('room', c.id); } }, 'Ochish')
          : h('button', { class: 'btn small primary', type: 'button', onclick: async ev => { ev.target.disabled = true; try { const r = await api('/rooms/' + c.id + '/join', { method: 'POST' }); upsertRoom(r); renderList(); box.hidden = true; openChat('room', r.id); } catch (err) { toast(err.message); ev.target.disabled = false; } } }, 'Obuna')));
      box.hidden = false;
    } catch (e) { toast(e.message); }
  };
}
async function startChat(username) {
  try {
    const c = await api('/chats', { method: 'POST', body: { username } });
    upsertChat(c); renderList(); openChat('direct', c.id);
  } catch (e) { toast(e.message); }
}

// ---------------------------------------------------------------- suhbatni ochish
function saveDraft() {
  if (!S.cur) return;
  const key = keyOf(S.cur.kind, S.cur.id), v = $('input').value;
  if (v.trim()) S.drafts[key] = v; else delete S.drafts[key];
  lsSet('drafts', S.drafts);
}
async function openChat(kind, id, opts = {}) {
  saveDraft();
  closePopups(); $('emojiPanel').hidden = true;
  cancelReply(); clearAttach(); stopRecording(true);
  S.cur = { kind, id }; S.msgs = []; S.detached = false; S.hasMore = false; S.hasNewer = false; S.newWhileUp = 0;
  $('empty').hidden = true; $('chatView').hidden = false; $('app').classList.add('chat-open');
  $('input').value = S.drafts[keyOf(kind, id)] || ''; autoGrow(); syncSendBtn();
  $('msgs').replaceChildren(); updateToBottom();
  renderHead(); renderList();
  if (kind === 'room') loadRoomReads(id);
  try { await loadMessages(opts.around); }
  catch (e) { toast(e.message); return; }
  if (opts.around) jumpDom(opts.around); else scrollBottom(true);
  markRead();
  if (!matchMedia('(pointer:coarse)').matches && canWrite()) $('input').focus();
}
async function loadMessages(around) {
  const cur = S.cur; if (!cur) return;
  const base = cur.kind === 'room' ? '/rooms/' : '/chats/';
  const r = await api(base + cur.id + '/messages' + (around ? '?around=' + around : '?limit=60'));
  if (!S.cur || S.cur.kind !== cur.kind || S.cur.id !== cur.id) return;
  const pending = S.msgs.filter(m => m.temp);
  S.msgs = r.messages.concat(around ? [] : pending); S.hasMore = r.has_more; S.hasNewer = !!r.has_newer; S.detached = !!r.has_newer;
  renderMsgs();
}
async function loadOlder() {
  if (S.loadingOlder || !S.hasMore || !S.msgs.length || !S.cur) return;
  const first = S.msgs.find(m => !m.temp); if (!first) return;
  S.loadingOlder = true;
  const cur = S.cur, wrap = $('msgs');
  try {
    const base = cur.kind === 'room' ? '/rooms/' : '/chats/';
    const r = await api(base + cur.id + '/messages?limit=60&before=' + first.id);
    if (!S.cur || S.cur.id !== cur.id || S.cur.kind !== cur.kind) return;
    const prevH = wrap.scrollHeight, prevTop = wrap.scrollTop;
    S.msgs = r.messages.concat(S.msgs); S.hasMore = r.has_more;
    renderMsgs(true);
    wrap.scrollTop = prevTop + (wrap.scrollHeight - prevH);
  } catch (e) { toast(e.message); }
  S.loadingOlder = false;
}
async function loadNewer() {
  // "uzoq" oynadan eng so'nggi xabarlarga qaytish
  S.detached = false; S.hasNewer = false; S.newWhileUp = 0;
  await loadMessages(); scrollBottom(true); markRead(); updateToBottom();
}
async function loadRoomReads(id) {
  try { S.reads[id] = await api('/rooms/' + id + '/reads'); if (S.cur && S.cur.kind === 'room' && S.cur.id === id) renderMsgs(); } catch { /* */ }
}
function canWrite() {
  const d = curData(); if (!d) return false;
  if (S.cur.kind === 'room') return d.room_type === 'group' || !!(d.rights && d.rights.post_messages);
  if (d.status === 'blocked') return false;
  if (d.status === 'pending' && d.i_am_requester && d.my_count >= 1) return false;
  return true;
}
function canPin() {
  const d = curData(); if (!d) return false;
  if (S.cur.kind === 'room') return !!(d.rights && d.rights.pin_messages);
  return d.status !== 'blocked';
}
function renderHead() {
  const d = curData();
  if (!d) { $('chatView').hidden = true; $('empty').hidden = false; $('app').classList.remove('chat-open'); return; }
  const isRoom = S.cur.kind === 'room';
  const entity = isRoom ? d : d.peer;
  $('headAvatar').replaceChildren(avatarEl(entity));
  $('headName').replaceChildren(h('span', { class: 'name-label', text: isRoom ? d.title : d.peer.display_name }), ...((isRoom ? d.verified : d.peer.is_owner) ? [verifiedBadge()] : []));
  updateHeadStatus();
  $('inviteMember').hidden = !(isRoom && d.room_type === 'group' && d.rights && d.rights.add_members);
  const pin = d.pinned;
  $('pinBar').hidden = !pin;
  if (pin) $('pinText').textContent = pin.text || 'Xabar';
  $('pinClose').hidden = !canPin();
  // banner
  const banner = $('banner'); banner.replaceChildren(); banner.hidden = true;
  if (!isRoom && d.status === 'pending' && !d.i_am_requester) {
    banner.hidden = false;
    banner.append(h('span', { text: d.peer.display_name + ' sizga yozmoqchi. Javob yozsangiz so‘rov qabul qilinadi.' }),
      h('button', { class: 'b-ok', type: 'button', onclick: async () => { try { upsertChat(await api('/chats/' + d.id + '/accept', { method: 'POST' })); renderHead(); renderList(); } catch (e) { toast(e.message); } } }, 'Qabul qilish'),
      h('button', { class: 'b-no', type: 'button', onclick: async () => { try { await api('/chats/' + d.id + '/decline', { method: 'POST' }); removeChat(d.id); toast('So‘rov rad etildi'); } catch (e) { toast(e.message); } } }, 'Rad etish'));
  } else if (!isRoom && d.status === 'pending' && d.i_am_requester) {
    banner.hidden = false; banner.append(h('span', { text: d.my_count >= 1 ? 'So‘rov yuborildi. Qabul qilinguncha yana xabar yubora olmaysiz.' : 'Birinchi xabaringiz so‘rov sifatida yuboriladi.' }));
  } else if (isRoom && d.room_type === 'channel' && !canWrite()) {
    banner.hidden = false; banner.append(h('span', { text: 'Bu kanalda faqat adminlar post yozishi mumkin.' }));
  }
  const writable = canWrite();
  $('composerWrap').hidden = !writable && !(isRoom && d.room_type === 'channel');
  $('composer').hidden = !writable; $('emojiPanel').hidden = $('emojiPanel').hidden || !writable;
}
function updateHeadStatus() {
  const d = curData(); if (!d) return;
  const st = $('headSt'); st.className = 'st';
  const typingKey = keyOf(S.cur.kind, S.cur.id);
  if (S.typing[typingKey] && S.typing[typingKey] > Date.now()) { st.textContent = 'yozmoqda…'; st.classList.add('on'); return; }
  if (S.cur.kind === 'room') st.textContent = d.member_count + (d.room_type === 'channel' ? ' obunachi' : ' ishtirokchi');
  else { st.textContent = lastSeenText(d.peer); if (d.peer.online) st.classList.add('on'); }
}
function removeChat(id) {
  S.chats = S.chats.filter(c => c.id !== id);
  if (S.cur && S.cur.kind === 'direct' && S.cur.id === id) closeChat();
  renderList();
}
function closeChat() {
  saveDraft(); S.cur = null; S.msgs = [];
  $('chatView').hidden = true; $('empty').hidden = false; $('app').classList.remove('chat-open');
  renderList();
}

// ---------------------------------------------------------------- xabarlar chizish (diff)
function isMine(m) { return m.sender_id === S.me.id; }
function tickState(m) {
  if (!isMine(m)) return '';
  if (m.failed) return '!';
  if (m.pending || m.temp) return '🕓';
  if (S.cur && S.cur.kind === 'room') {
    const d = curData(); if (d && d.room_type === 'channel') return '';
    const reads = S.reads[S.cur.id] || {};
    const seen = Object.entries(reads).some(([uid, last]) => Number(uid) !== S.me.id && last >= m.id);
    return seen ? 'rr' : 'r1';
  }
  return m.read ? 'rr' : 'r1';
}
function tickEl(state) {
  const t = h('span', { class: 'tick' + (state === 'rr' ? ' r' : '') });
  t.textContent = state === '!' ? '⚠' : state === '🕓' ? '🕓' : state === 'rr' ? '✓✓' : state === 'r1' ? '✓' : '';
  return t;
}
function msgSig(m, showName) {
  return JSON.stringify([m.text, m.edited, m.deleted, m.reactions, m.attachment && m.attachment.key, m.reply, m.fwd, m.sender_tag, m.failed, m.pending, showName, m.sender_name, S.settings.chat.hour12]);
}
function buildMsg(m, showName) {
  const mine = isMine(m);
  const isRoom = S.cur.kind === 'room';
  const el = h('div', { class: 'msg ' + (mine ? 'out' : 'in') + (m.failed ? ' fail' : '') + (m.deleted ? ' deleted' : ''), 'data-id': m.id });
  if (showName && isRoom && (!mine || (curData() || {}).room_type === 'channel')) {
    el.append(h('div', { class: 'sender', style: `color:${COLORS[m.sender_id % COLORS.length]}` },
      h('span', { text: m.sender_name || 'Foydalanuvchi' }), m.sender_is_owner ? verifiedBadge() : null,
      m.sender_tag ? h('span', { class: 'tag-chip', text: m.sender_tag }) : null));
  }
  if (m.fwd && !m.deleted) el.append(h('div', { class: 'fwd', text: '↪ Yo‘naltirilgan: ' + m.fwd }));
  if (m.reply && !m.deleted) {
    el.append(h('div', { class: 'quote', onclick: ev => { ev.stopPropagation(); jumpTo(m.reply.id); } },
      h('b', { text: m.reply.sender_name || 'Xabar' }), h('span', { text: m.reply.text })));
  } else if (m.reply_to && !m.deleted && !m.reply) {
    el.append(h('div', { class: 'quote' }, h('b', { text: 'Xabar' }), h('span', { text: 'Xabar o‘chirilgan' })));
  }
  const a = m.attachment;
  if (a && !m.deleted) {
    if (a.kind === 'image') {
      const img = h('img', { class: 'media-img', src: '/api/files/' + a.key, alt: a.name || 'Rasm', loading: 'lazy' });
      if (a.w && a.h) { const w = Math.min(a.w, 340); img.width = w; img.height = Math.round(w * a.h / a.w); img.style.aspectRatio = a.w + '/' + a.h; img.style.maxWidth = '100%'; img.style.height = 'auto'; }
      img.addEventListener('click', ev => { ev.stopPropagation(); openLightbox(img.src, a.name); });
      el.append(img);
    } else if (a.kind === 'voice') el.append(voiceEl(a));
    else el.append(h('a', { class: 'file', href: '/api/files/' + a.key, download: a.name, onclick: ev => ev.stopPropagation() },
      h('span', { class: 'file-icon', text: '📄' }), h('span', { class: 'file-copy' }, h('b', { text: a.name }), h('span', { text: fmtSize(a.size) }))));
  }
  if (m.text) el.append(h('div', { class: 'msg-text' }, richText(m.text)));
  const meta = h('span', { class: 'meta' }, m.edited && !m.deleted ? 'tahrirlangan ' : '', fmtTime(m.created_at), tickEl(m.deleted ? '' : tickState(m)));
  el.append(meta);
  if (m.reactions && m.reactions.length && !m.deleted) {
    el.append(h('div', { class: 'reacts' }, m.reactions.map(r => h('button', { type: 'button', class: 'react' + (r.me ? ' me' : ''), onclick: ev => { ev.stopPropagation(); toggleReaction(m, r.emoji); } }, r.emoji + ' ' + r.count))));
  }
  return el;
}
function renderMsgs(keepScroll) {
  const wrap = $('msgs');
  const nearBottom = wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight < 120;
  const isRoom = S.cur && S.cur.kind === 'room';
  const nodes = []; let prev = null, prevDay = '';
  for (const m of S.msgs) {
    const dk = dayKey(m.created_at);
    if (dk !== prevDay) { nodes.push({ key: 'd:' + dk, sig: dayLabel(m.created_at), build: () => h('div', { class: 'day', text: dayLabel(m.created_at) }) }); prevDay = dk; prev = null; }
    const d = curData();
    const showName = isRoom && d && (d.room_type === 'group' || d.sign_messages) && (!prev || prev.sender_id !== m.sender_id || m.created_at - prev.created_at > 300);
    nodes.push({ key: 'm:' + m.id, sig: msgSig(m, showName), build: () => buildMsg(m, showName), msg: m });
    prev = m;
  }
  const existing = new Map(Array.from(wrap.children).map(c => [c.dataset.k, c]));
  let cursor = wrap.firstChild; const keep = new Set();
  for (const n of nodes) {
    let el = existing.get(n.key);
    if (!el || el.dataset.sig !== n.sig) {
      const fresh = n.build(); fresh.dataset.k = n.key; fresh.dataset.sig = n.sig;
      if (el) { releaseVoice(el); if (el === cursor) cursor = cursor.nextSibling; el.remove(); }
      el = fresh;
    }
    if (el === cursor) cursor = cursor.nextSibling; else wrap.insertBefore(el, cursor);
    keep.add(n.key);
    if (n.msg) {
      el.__msg = n.msg;
      const t = el.querySelector('.tick');
      if (t) { const fresh = tickEl(n.msg.deleted ? '' : tickState(n.msg)); t.className = fresh.className; t.textContent = fresh.textContent; }
    }
  }
  for (const [k, el] of existing) if (!keep.has(k) && el.parentNode === wrap) { releaseVoice(el); el.remove(); }
  if (!keepScroll && nearBottom && !S.detached) scrollBottom();
  updateToBottom();
}
function scrollBottom(force) { const w = $('msgs'); w.scrollTop = w.scrollHeight; if (force) requestAnimationFrame(() => { w.scrollTop = w.scrollHeight; }); }
function nearBottomNow() { const w = $('msgs'); return w.scrollHeight - w.scrollTop - w.clientHeight < 140; }
function updateToBottom() {
  const show = S.cur && (!nearBottomNow() || S.detached);
  $('toBottom').hidden = !show;
  const b = $('toBottomBadge'); b.hidden = !(show && S.newWhileUp); b.textContent = S.newWhileUp;
}
function jumpDom(id) {
  const el = $('msgs').querySelector(`[data-id="${id}"]`);
  if (!el) return false;
  el.scrollIntoView({ block: 'center' }); el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
  setTimeout(() => el.classList.remove('flash'), 1400);
  return true;
}
async function jumpTo(id) {
  if (!id) return;
  if (jumpDom(id)) return;
  try {
    const cur = S.cur; const base = cur.kind === 'room' ? '/rooms/' : '/chats/';
    const r = await api(base + cur.id + '/messages?around=' + id);
    if (!S.cur || S.cur.id !== cur.id) return;
    S.msgs = r.messages; S.hasMore = r.has_more; S.hasNewer = !!r.has_newer; S.detached = !!r.has_newer;
    $('msgs').replaceChildren(); renderMsgs(true);
    if (!jumpDom(id)) toast('Xabar topilmadi');
  } catch (e) { toast(e.message); }
}

// ---------------------------------------------------------------- ovozli xabar pleyeri
const VOICE = { cur: null };
function voiceEl(a) {
  const btn = h('button', { type: 'button', class: 'voice-btn', 'aria-label': 'Ijro etish', text: '▶' });
  const bar = h('u'); const track = h('div', { class: 'voice-track' }, bar);
  const time = h('span', { class: 'voice-time', text: fmtDur(a.dur) });
  const box = h('div', { class: 'voice' }, btn, track, time);
  box.__voice = { key: a.key, btn, bar, time, dur: a.dur };
  btn.addEventListener('click', ev => { ev.stopPropagation(); toggleVoice(box.__voice, a); });
  track.addEventListener('click', ev => {
    ev.stopPropagation();
    if (!VOICE.cur || VOICE.cur.key !== a.key) toggleVoice(box.__voice, a);
    const r = track.getBoundingClientRect(); const dur = VOICE.cur.audio.duration || a.dur || 0;
    if (dur) VOICE.cur.audio.currentTime = dur * Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width));
  });
  if (VOICE.cur && VOICE.cur.key === a.key) { VOICE.cur.ui = box.__voice; syncVoice(); }
  return box;
}
function toggleVoice(ui, a) {
  if (VOICE.cur && VOICE.cur.key === a.key) { const au = VOICE.cur.audio; if (au.paused) au.play().catch(() => { }); else au.pause(); return; }
  stopVoice();
  const audio = new Audio('/api/files/' + a.key); audio.preload = 'auto';
  VOICE.cur = { key: a.key, audio, ui };
  audio.addEventListener('timeupdate', syncVoice); audio.addEventListener('play', syncVoice); audio.addEventListener('pause', syncVoice);
  audio.addEventListener('ended', () => { audio.currentTime = 0; syncVoice(); });
  audio.addEventListener('error', () => { toast('Ovozni ijro etib bo‘lmadi'); stopVoice(); });
  audio.play().catch(() => { });
}
function syncVoice() {
  const c = VOICE.cur; if (!c || !c.ui) return;
  const au = c.audio, dur = au.duration && isFinite(au.duration) ? au.duration : c.ui.dur || 0;
  c.ui.btn.textContent = au.paused ? '▶' : '⏸';
  c.ui.bar.style.width = dur ? (au.currentTime / dur * 100) + '%' : '0';
  c.ui.time.textContent = fmtDur(au.paused && au.currentTime === 0 ? dur : au.currentTime);
}
function stopVoice() {
  const c = VOICE.cur; if (!c) return;
  c.audio.pause(); c.audio.removeAttribute('src'); c.audio.load();
  if (c.ui) { c.ui.btn.textContent = '▶'; c.ui.bar.style.width = '0'; c.ui.time.textContent = fmtDur(c.ui.dur); }
  VOICE.cur = null;
}
function releaseVoice(el) { const v = el.querySelector && el.querySelector('.voice'); if (v && VOICE.cur && VOICE.cur.ui === v.__voice) VOICE.cur.ui = null; }

// ---------------------------------------------------------------- reaksiya, kontekst menyu
async function toggleReaction(m, emoji) {
  if (m.temp) return;
  const base = S.cur.kind === 'room' ? '/rooms/' : '/chats/';
  try {
    const r = await api(base + S.cur.id + '/messages/' + m.id + '/reactions', { method: 'POST', body: { emoji } });
    m.reactions = r.reactions; renderMsgs(true);
  } catch (e) { toast(e.message); }
}
function openMsgMenu(m, x, y) {
  if (m.deleted) return;
  const isRoom = S.cur.kind === 'room', d = curData();
  if (m.failed || m.temp) {
    return showPopup([
      m.failed ? { icon: '🔁', label: 'Qayta yuborish', run: () => resend(m) } : null,
      { icon: '🗑', label: 'Bekor qilish', danger: true, run: () => { S.msgs = S.msgs.filter(z => z !== m); renderMsgs(true); } },
    ].filter(Boolean), x, y);
  }
  const header = (isRoom && d.reactions_enabled === false) ? null : h('div', { class: 'ctx-react' }, REACTIONS.map(e => h('button', { type: 'button', class: m.reactions && m.reactions.some(r => r.me && r.emoji === e) ? 'on' : '', text: e, onclick: () => { closePopups(); toggleReaction(m, e); } })));
  const admin = isRoom && !!(d.rights && d.rights.delete_messages);
  const pinned = d.pinned && d.pinned.id === m.id;
  const items = [];
  if (canWrite()) items.push({ icon: '↩', label: 'Javob berish', run: () => startReply(m) });
  if (m.text) items.push({ icon: '⧉', label: 'Nusxa olish', run: () => copyText(m.text) });
  items.push({ icon: '↪', label: 'Yo‘naltirish', run: () => openForward(m) });
  if (canPin()) items.push({ icon: '📌', label: pinned ? 'Qadashni olib tashlash' : 'Qadash', run: () => pinMessage(pinned ? null : m.id) });
  items.push({ icon: '🔖', label: 'Saqlash', run: () => saveMessage(m) });
  if (isMine(m)) items.push({ icon: '✏️', label: 'Tahrirlash', run: () => openEditMessage(m) });
  if (isMine(m) || admin) items.push('-', { icon: '🗑', label: 'O‘chirish', danger: true, run: () => deleteMessage(m) });
  showPopup(items, x, y, header);
}
async function copyText(t) {
  try { await navigator.clipboard.writeText(t); toast('Nusxa olindi'); }
  catch { const ta = h('textarea', { style: 'position:fixed;opacity:0' }); ta.value = t; document.body.append(ta); ta.select(); try { document.execCommand('copy'); toast('Nusxa olindi'); } catch { toast('Nusxa olib bo‘lmadi'); } ta.remove(); }
}
async function pinMessage(id) {
  const path = S.cur.kind === 'room' ? '/rooms/' + S.cur.id + '/pin' : '/chats/' + S.cur.id + '/pin';
  try {
    const r = await api(path, { method: 'POST', body: { message_id: id } });
    if (S.cur.kind === 'room') upsertRoom(r); else upsertChat(r);
    renderHead(); toast(id ? 'Xabar qadaldi' : 'Qadash olib tashlandi');
  } catch (e) { toast(e.message); }
}
async function saveMessage(m) {
  try { await api('/saved', { method: 'POST', body: { source: S.cur.kind, source_id: S.cur.id, message_id: m.id } }); toast('Saqlangan xabarlarga qo‘shildi'); }
  catch (e) { toast(e.message); }
}
async function deleteMessage(m) {
  const ok = await confirmBox({ title: 'Xabarni o‘chirish', text: 'Xabar ikkala tomon uchun o‘chiriladi. Bu amalni qaytarib bo‘lmaydi.', ok: 'O‘chirish', danger: true });
  if (!ok) return;
  const base = S.cur.kind === 'room' ? '/rooms/' : '/chats/';
  try { await api(base + S.cur.id + '/messages/' + m.id, { method: 'DELETE' }); } catch (e) { toast(e.message); }
}
function attachMsgInteractions() {
  const wrap = $('msgs'); let timer = null, sx = 0, sy = 0, fired = false;
  const msgOf = t => { const el = t.closest && t.closest('.msg'); return el && el.__msg ? el : null; };
  wrap.addEventListener('click', e => {
    if (fired) { fired = false; return; }
    if (e.target.closest('a,button,img,.voice,.quote')) return;
    if (window.getSelection && String(window.getSelection())) return;
    const el = msgOf(e.target); if (!el) return;
    const r = el.getBoundingClientRect(); openMsgMenu(el.__msg, Math.min(e.clientX, r.right - 10), e.clientY);
  });
  wrap.addEventListener('contextmenu', e => { const el = msgOf(e.target); if (!el) return; e.preventDefault(); openMsgMenu(el.__msg, e.clientX, e.clientY); });
  wrap.addEventListener('touchstart', e => {
    const el = msgOf(e.target); if (!el) return;
    const t = e.touches[0]; sx = t.clientX; sy = t.clientY; fired = false;
    timer = setTimeout(() => { fired = true; if (navigator.vibrate) navigator.vibrate(12); openMsgMenu(el.__msg, sx, sy); }, 450);
  }, { passive: true });
  const cancel = () => clearTimeout(timer);
  wrap.addEventListener('touchmove', e => { const t = e.touches[0]; if (Math.abs(t.clientX - sx) > 8 || Math.abs(t.clientY - sy) > 8) cancel(); }, { passive: true });
  wrap.addEventListener('touchend', () => { cancel(); setTimeout(() => { fired = false; }, 350); });
  wrap.addEventListener('scroll', () => {
    if (wrap.scrollTop < 120) loadOlder();
    if (nearBottomNow()) { S.newWhileUp = 0; if (!S.detached) markRead(); }
    updateToBottom();
  }, { passive: true });
}

// ---------------------------------------------------------------- javob
function startReply(m) {
  const name = isMine(m) ? 'Siz' : (m.sender_name || (S.cur.kind === 'direct' && curData().peer.display_name) || 'Xabar');
  S.reply = { id: m.id, name, text: msgPreview(m) };
  $('replyName').textContent = 'Javob: ' + name; $('replyText').textContent = S.reply.text; $('replyBar').hidden = false; $('input').focus();
}
function cancelReply() { S.reply = null; $('replyBar').hidden = true; }

// ---------------------------------------------------------------- yuborish
function autoGrow() { const i = $('input'); i.style.height = 'auto'; i.style.height = Math.min(i.scrollHeight, 140) + 'px'; }
function syncSendBtn() {
  const has = $('input').value.trim().length > 0 || !!S.attach;
  $('send').hidden = !has; $('micBtn').hidden = has;
}
let lastTyping = 0;
function onInput() {
  autoGrow(); syncSendBtn(); saveDraft();
  if (!S.cur || Date.now() - lastTyping < 2500 || !$('input').value) return;
  lastTyping = Date.now();
  wsSend(S.cur.kind === 'room' ? { type: 'room_typing', room_id: S.cur.id } : { type: 'typing', chat_id: S.cur.id });
}
function sendPayload(m) {
  const msg = S.cur.kind === 'room'
    ? { type: 'room_send', room_id: S.cur.id } : { type: 'send', chat_id: S.cur.id };
  Object.assign(msg, { text: m.text, temp_id: m.id });
  if (m.attachment) msg.attachment = m.attachment.key;
  if (m.reply_to) msg.reply_to = m.reply_to;
  return msg;
}
function submitMessage(text, attachment) {
  if (!S.cur || !canWrite()) return;
  const me = S.me, d = curData();
  const m = {
    id: 't' + (++S.tmp), temp: true, pending: true, sender_id: me.id, sender_name: me.display_name, text: text || '',
    created_at: Date.now() / 1000, attachment: attachment || null, reactions: [], read: false, edited: false, deleted: false,
    reply_to: S.reply ? S.reply.id : null, reply: S.reply ? { id: S.reply.id, sender_name: S.reply.name, text: S.reply.text } : null,
    _chat: { kind: S.cur.kind, id: S.cur.id },
  };
  void d;
  S.msgs.push(m); cancelReply(); renderMsgs(); scrollBottom(true);
  if (!wsSend(sendPayload(m))) { m.failed = true; m.pending = false; renderMsgs(true); toast('Aloqa yo‘q. Xabar yuborilmadi'); }
  return m;
}
function resend(m) {
  m.failed = false; m.pending = true; renderMsgs(true);
  if (!wsSend(sendPayload(m))) { m.failed = true; m.pending = false; renderMsgs(true); toast('Aloqa yo‘q'); }
}
async function sendCurrent() {
  if (!S.cur || !canWrite()) return;
  const input = $('input'); let text = input.value.trim();
  if (S.attach) {
    const att = S.attach;
    if (att.promise) { try { await att.promise; } catch { return; } }
    if (!att.meta) return;
    submitMessage(text, att.meta); clearAttach();
  } else if (text) submitMessage(text, null);
  else return;
  input.value = ''; autoGrow(); syncSendBtn(); saveDraft(); $('emojiPanel').hidden = true;
  if (S.detached) { await loadNewer(); }
  input.focus();
}

// ---------------------------------------------------------------- fayl yuklash
function uploadFile(blob, { kind, name, duration, onProgress }) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload');
    xhr.setRequestHeader('Authorization', 'Bearer ' + S.token);
    xhr.setRequestHeader('Content-Type', (blob.type || 'application/octet-stream').split(';')[0]);
    if (name) xhr.setRequestHeader('X-Filename', encodeURIComponent(name));
    if (kind) xhr.setRequestHeader('X-Kind', kind);
    if (duration) xhr.setRequestHeader('X-Duration', String(duration));
    xhr.upload.onprogress = e => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data = null; try { data = JSON.parse(xhr.responseText); } catch { /* */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error((data && typeof data.detail === 'string' && data.detail) || 'Yuklab bo‘lmadi (' + xhr.status + ')'));
    };
    xhr.onerror = () => reject(new Error('Internet bilan aloqa yo‘q'));
    xhr.send(blob);
  });
}
function attachFile(file, forceFile) {
  if (!S.cur || !canWrite()) return;
  clearAttach();
  const isImage = /^image\/(jpeg|png|webp)$/.test(file.type) && !forceFile;
  const att = { file, meta: null, promise: null, preview: isImage ? URL.createObjectURL(file) : null };
  S.attach = att;
  $('attachThumb').replaceChildren(isImage ? h('img', { src: att.preview, alt: '' }) : document.createTextNode('📄'));
  $('attachName').textContent = file.name || 'Fayl'; $('attachInfo').textContent = fmtSize(file.size) + ' · yuklanmoqda…';
  const bar = $('attachProgress').firstElementChild; bar.style.width = '0'; $('attachProgress').hidden = false;
  $('attachBar').hidden = false; syncSendBtn();
  att.promise = uploadFile(file, { kind: forceFile ? 'file' : '', name: file.name, onProgress: p => { bar.style.width = Math.round(p * 100) + '%'; } })
    .then(meta => { att.meta = meta; $('attachInfo').textContent = fmtSize(file.size) + ' · tayyor'; $('attachProgress').hidden = true; return meta; })
    .catch(e => { if (S.attach === att) { toast(e.message); clearAttach(); } throw e; });
  att.promise.catch(() => { });
}
function clearAttach() {
  if (S.attach && S.attach.preview) URL.revokeObjectURL(S.attach.preview);
  S.attach = null; $('attachBar').hidden = true; $('attachThumb').replaceChildren(); syncSendBtn();
}

// ---------------------------------------------------------------- ovoz yozish
const REC = { rec: null, chunks: [], stream: null, timer: null, start: 0, cancelled: false };
function pickMime() {
  const list = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus', 'audio/ogg'];
  return list.find(t => window.MediaRecorder && MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t)) || '';
}
async function startRecording() {
  if (!S.cur || !canWrite()) return;
  if (!navigator.mediaDevices || !window.MediaRecorder) return toast('Brauzeringiz ovoz yozishni qo‘llab-quvvatlamaydi');
  try { REC.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } }); }
  catch { return toast('Mikrofonga ruxsat berilmadi'); }
  const mime = pickMime();
  REC.chunks = []; REC.cancelled = false;
  REC.rec = new MediaRecorder(REC.stream, mime ? { mimeType: mime } : undefined);
  REC.rec.ondataavailable = e => { if (e.data && e.data.size) REC.chunks.push(e.data); };
  REC.rec.onstop = finishRecording;
  REC.rec.start(250); REC.start = Date.now();
  $('composer').hidden = true; $('recBar').hidden = false; $('recTime').textContent = '0:00';
  REC.timer = setInterval(() => {
    const s = (Date.now() - REC.start) / 1000; $('recTime').textContent = fmtDur(s);
    if (s >= 120) stopRecording(false);
  }, 250);
}
function stopRecording(cancel) {
  clearInterval(REC.timer);
  if (!REC.rec) { $('recBar').hidden = true; return; }
  REC.cancelled = !!cancel;
  if (REC.rec.state !== 'inactive') REC.rec.stop(); else finishRecording();
}
async function finishRecording() {
  const rec = REC.rec; REC.rec = null;
  if (REC.stream) REC.stream.getTracks().forEach(t => t.stop());
  REC.stream = null; $('recBar').hidden = true; if (canWrite()) $('composer').hidden = false;
  if (!rec || REC.cancelled) return;
  const dur = (Date.now() - REC.start) / 1000;
  if (dur < 0.7) return toast('Ovozli xabar juda qisqa');
  let blob = new Blob(REC.chunks, { type: (rec.mimeType || 'audio/webm').split(';')[0] });
  toast('Tayyorlanmoqda…', 1500);
  try { blob = await toWav(blob); } catch { /* asl format ishlatiladi */ }
  if (blob.size > 4 * 1024 * 1024) return toast('Ovozli xabar juda katta');
  try {
    const meta = await uploadFile(blob, { kind: 'voice', duration: dur.toFixed(1) });
    submitMessage('', meta);
  } catch (e) { toast(e.message); }
}


// Brauzer yozgan webm/mp4 har qurilmada bir xil ijro bo'lmaydi (davomiylik yo'q, siljitib bo'lmaydi, iPhone'da ochilmaydi).
// Shuning uchun ovoz 16 kHz mono WAV'ga aylantiriladi: hamma joyda ishlaydi, sifati nutq uchun yetarli.
async function toWav(blob) {
  const AC = window.AudioContext || window.webkitAudioContext;
  const ctx = new AC();
  const buf = await ctx.decodeAudioData(await blob.arrayBuffer());
  ctx.close();
  const RATE = 16000, len = Math.ceil(buf.duration * RATE);
  const off = new (window.OfflineAudioContext || window.webkitOfflineAudioContext)(1, len, RATE);
  const src = off.createBufferSource(); src.buffer = buf; src.connect(off.destination); src.start();
  const pcm = (await off.startRendering()).getChannelData(0);
  const out = new DataView(new ArrayBuffer(44 + pcm.length * 2));
  const w = (o, t) => { for (let i = 0; i < t.length; i++) out.setUint8(o + i, t.charCodeAt(i)); };
  w(0, 'RIFF'); out.setUint32(4, 36 + pcm.length * 2, true); w(8, 'WAVEfmt '); out.setUint32(16, 16, true); out.setUint16(20, 1, true);
  out.setUint16(22, 1, true); out.setUint32(24, RATE, true); out.setUint32(28, RATE * 2, true); out.setUint16(32, 2, true); out.setUint16(34, 16, true);
  w(36, 'data'); out.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) { const v = Math.max(-1, Math.min(1, pcm[i])); out.setInt16(44 + i * 2, v < 0 ? v * 0x8000 : v * 0x7fff, true); }
  return new Blob([out], { type: 'audio/wav' });
}

// ---------------------------------------------------------------- rasm ko'rish
function openLightbox(src, name) {
  $('lightboxImg').src = src; const a = $('lightboxDownload'); a.href = src; a.download = name || 'rasm.jpg';
  $('lightbox').showModal();
}

// ---------------------------------------------------------------- WS hodisalari
function inCurrent(kind, id) { return S.cur && S.cur.kind === kind && S.cur.id === id; }
function onWs(d) {
  switch (d.type) {
    case 'message': {
      const m = d.message; if (d.chat) upsertChat(d.chat);
      incoming('direct', m.chat_id, m, d.temp_id, d.chat);
      break;
    }
    case 'room_message': {
      const m = d.message; if (d.room) upsertRoom(d.room);
      incoming('room', m.room_id, m, d.temp_id, d.room);
      break;
    }
    case 'chat_update': upsertChat(d.chat); if (inCurrent('direct', d.chat.id)) renderHead(); renderList(); break;
    case 'room_update': upsertRoom(d.room); if (inCurrent('room', d.room.id)) { renderHead(); renderMsgs(true); } renderList(); if (typeof onRoomUpdated === 'function') onRoomUpdated(d.room); break;
    case 'room_invite': if (!S.invites.some(i => i.id === d.invite.id)) S.invites.unshift(d.invite); renderList(); toast('Yangi taklif: ' + d.invite.title); break;
    case 'room_removed':
      S.rooms = S.rooms.filter(r => r.id !== d.room_id);
      if (inCurrent('room', d.room_id)) { closeChat(); toast(d.reason === 'deleted' ? 'Suhbat o‘chirildi' : 'Siz suhbatdan chiqarildingiz'); }
      renderList(); break;
    case 'chat_gone': removeChat(d.chat_id); break;
    case 'read': {
      if (inCurrent('direct', d.chat_id)) { let ch = false; for (const m of S.msgs) if (isMine(m) && !m.read && !m.temp) { m.read = true; ch = true; } if (ch) renderMsgs(true); }
      break;
    }
    case 'read_self': { const c = S.chats.find(x => x.id === d.chat_id); if (c) { c.unread = 0; renderList(); } break; }
    case 'room_read_by': {
      (S.reads[d.room_id] = S.reads[d.room_id] || {})[d.user_id] = d.last_read_id;
      if (inCurrent('room', d.room_id)) renderMsgs(true);
      break;
    }
    case 'message_update': if (inCurrent('direct', d.chat_id)) replaceMsg(d.message); break;
    case 'room_message_update': if (inCurrent('room', d.room_id)) replaceMsg(d.message); break;
    case 'reaction': {
      const kind = d.room_id ? 'room' : 'direct', id = d.room_id || d.chat_id;
      if (inCurrent(kind, id)) { const m = S.msgs.find(x => x.id === d.message_id); if (m) { m.reactions = d.reactions; renderMsgs(true); } }
      break;
    }
    case 'profile_update': onProfileUpdate(d.user); break;
    case 'presence': {
      for (const c of S.chats) if (c.peer.id === d.user_id) { c.peer.online = d.online; c.peer.last_seen = d.last_seen; }
      if (S.cur && S.cur.kind === 'direct') { const c = curData(); if (c && c.peer.id === d.user_id) updateHeadStatus(); }
      renderList(); break;
    }
    case 'typing': {
      const c = S.chats.find(x => x.id === d.chat_id); if (c && c.peer.id === d.user_id) { S.typing[String(d.chat_id)] = Date.now() + 4000; if (inCurrent('direct', d.chat_id)) { updateHeadStatus(); setTimeout(updateHeadStatus, 4100); } }
      break;
    }
    case 'room_typing': {
      if (d.user_id === S.me.id) break;
      S.typing['r:' + d.room_id] = Date.now() + 4000; if (inCurrent('room', d.room_id)) { updateHeadStatus(); setTimeout(updateHeadStatus, 4100); }
      break;
    }
    case 'member_tag': {
      const list = S.members[d.room_id]; if (list) { const mm = list.find(x => x.id === d.user_id); if (mm) mm.tag = d.tag; }
      if (inCurrent('room', d.room_id)) { for (const m of S.msgs) if (m.sender_id === d.user_id) m.sender_tag = d.tag; renderMsgs(true); }
      break;
    }
    case 'error': {
      if (d.temp_id) { const m = S.msgs.find(x => x.id === d.temp_id); if (m) { m.failed = true; m.pending = false; renderMsgs(true); } }
      toast(d.message || 'Xatolik');
      break;
    }
    default: break;
  }
}
function replaceMsg(m) {
  const i = S.msgs.findIndex(x => x.id === m.id);
  if (i >= 0) { S.msgs[i] = Object.assign({}, S.msgs[i], m); renderMsgs(true); }
}
function incoming(kind, id, m, tempId, listItem) {
  void listItem;
  const viewing = inCurrent(kind, id);
  const mine = m.sender_id === S.me.id;
  if (viewing) {
    const ti = tempId ? S.msgs.findIndex(x => x.id === tempId) : -1;
    if (ti >= 0) S.msgs[ti] = m;
    else if (!S.msgs.some(x => x.id === m.id)) {
      if (S.detached) { if (!mine) S.newWhileUp++; }
      else {
        S.msgs.push(m);
        if (!mine && !nearBottomNow()) S.newWhileUp++;
      }
    }
    if (!S.detached) renderMsgs(mine);
    if (mine) scrollBottom(true);
    updateToBottom();
    if (!mine) { if (document.visibilityState === 'visible' && !S.detached && nearBottomNow()) markRead(); else notify(kind, id, m); }
  } else if (!mine) notify(kind, id, m);
  renderList();
}
function onProfileUpdate(u) {
  if (u.id === S.me.id) { S.me = Object.assign(S.me, u); if (typeof refreshMenuProfile === 'function') refreshMenuProfile(); }
  for (const c of S.chats) if (c.peer.id === u.id) c.peer = u;
  for (const k of Object.keys(S.members)) { const mm = S.members[k].find(x => x.id === u.id); if (mm) Object.assign(mm, { display_name: u.display_name, avatar_url: u.avatar_url }); }
  for (const m of S.msgs) if (m.sender_id === u.id && m.sender_name !== undefined) m.sender_name = u.display_name;
  if (S.cur) { renderHead(); renderMsgs(true); }
  renderList();
}
function markRead() {
  if (!S.cur || document.visibilityState !== 'visible' || S.detached) return;
  const d = curData(); if (!d || !d.unread) return;
  if (S.cur.kind === 'room') wsSend({ type: 'room_read', room_id: S.cur.id }); else wsSend({ type: 'read', chat_id: S.cur.id });
  d.unread = 0; renderList();
}

// ---------------------------------------------------------------- bildirishnoma (ilova ichida)
let audioCtx = null;
function beep() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const t = audioCtx.currentTime;
    [660, 880].forEach((f, i) => {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.frequency.value = f; o.type = 'sine'; o.connect(g); g.connect(audioCtx.destination);
      g.gain.setValueAtTime(0.0001, t + i * .12); g.gain.exponentialRampToValueAtTime(0.12, t + i * .12 + .02); g.gain.exponentialRampToValueAtTime(0.0001, t + i * .12 + .2);
      o.start(t + i * .12); o.stop(t + i * .12 + .22);
    });
  } catch { /* ovoz ruxsat etilmagan */ }
}
function notify(kind, id, m) {
  const s = S.settings; const key = keyOf(kind, id);
  const room = kind === 'room' ? S.rooms.find(r => r.id === id) : null;
  const cat = kind === 'direct' ? 'direct' : room && room.room_type === 'channel' ? 'channels' : 'groups';
  if (!s.notif[cat] || isMuted(key)) return;
  if (s.notif.sound) beep();
  if (document.visibilityState === 'visible') {
    const title = kind === 'room' ? (room ? room.title : 'Guruh') : ((S.chats.find(c => c.id === id) || { peer: { display_name: 'Xabar' } }).peer.display_name);
    toast(s.notif.preview ? title + ': ' + snip(msgPreview(m), 60) : title + ': yangi xabar', 3200);
  }
}

// ---------------------------------------------------------------- Push
const b64ToU8 = b64 => { const p = '='.repeat((4 - b64.length % 4) % 4); const s = atob((b64 + p).replace(/-/g, '+').replace(/_/g, '/')); return Uint8Array.from(s, c => c.charCodeAt(0)); };
async function pushStatus() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) return 'unsupported';
  if (Notification.permission === 'denied') return 'denied';
  try { const reg = await navigator.serviceWorker.ready; return (await reg.pushManager.getSubscription()) ? 'on' : 'off'; } catch { return 'unsupported'; }
}
async function pushEnable() {
  const st = await pushStatus();
  if (st === 'unsupported') throw new Error('Bu brauzer bildirishnomani qo‘llab-quvvatlamaydi. iPhone’da avval ilovani “Bosh ekranga qo‘shing”.');
  if (st === 'denied') throw new Error('Bildirishnoma brauzer sozlamalarida bloklangan. Sayt sozlamalaridan ruxsat bering.');
  const perm = await Notification.requestPermission();
  if (perm !== 'granted') throw new Error('Ruxsat berilmadi');
  const k = await api('/push/key');
  if (!k.enabled) throw new Error('Serverda push hali sozlanmagan');
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToU8(k.key) });
  const j = sub.toJSON();
  await api('/push/subscribe', { method: 'POST', body: { endpoint: j.endpoint, keys: j.keys } });
}
async function pushDisable() {
  const reg = await navigator.serviceWorker.ready; const sub = await reg.pushManager.getSubscription();
  if (sub) { try { await api('/push/unsubscribe', { method: 'POST', body: { endpoint: sub.endpoint } }); } catch { /* */ } await sub.unsubscribe(); }
}
async function pushDropDevice() { if ('serviceWorker' in navigator && 'PushManager' in window) await pushDisable(); }
async function pushResync() {
  // Kirgan akkauntga shu qurilma obunasini qayta bog'laydi (agar ruxsat berilgan bo'lsa)
  try {
    if ((await pushStatus()) !== 'on') return;
    const reg = await navigator.serviceWorker.ready; const sub = await reg.pushManager.getSubscription(); const j = sub.toJSON();
    await api('/push/subscribe', { method: 'POST', body: { endpoint: j.endpoint, keys: j.keys } });
  } catch { /* */ }
}

// ---------------------------------------------------------------- hodisalarni ulash
function wireUi() {
  wireSearch(); attachMsgInteractions();
  $('menuToggle').onclick = () => openDrawer();
  $('newGroup').onclick = () => openCreateRoom('group');
  $('newChannel').onclick = () => openCreateRoom('channel');
  $('back').onclick = () => closeChat();
  $('chatIdentity').onclick = () => { const d = curData(); if (!d) return; if (S.cur.kind === 'room') openRoomDialog(d.id); else openContact(d); };
  $('inviteMember').onclick = () => { const d = curData(); if (d) openRoomDialog(d.id, true); };
  $('chatMoreBtn').onclick = ev => {
    const d = curData(); if (!d) return; const r = ev.currentTarget.getBoundingClientRect(); const key = keyOf(S.cur.kind, S.cur.id);
    const items = [{ icon: 'ℹ️', label: S.cur.kind === 'room' ? 'Ma‘lumot' : 'Profil', run: () => $('chatIdentity').click() },
      { icon: isMuted(key) ? '🔔' : '🔕', label: isMuted(key) ? 'Ovozni yoqish' : 'Ovozsiz qilish', run: () => toggleMute(key) }];
    if (d.pinned) items.push({ icon: '📌', label: 'Qadalgan xabarga o‘tish', run: () => jumpTo(d.pinned.id) });
    if (S.cur.kind === 'direct') items.push('-', { icon: '⛔', label: 'Bloklash', danger: true, run: () => blockPeer(d) });
    showPopup(items, r.right - 220, r.bottom + 6);
  };
  $('pinJump').onclick = () => { const d = curData(); if (d && d.pinned) jumpTo(d.pinned.id); };
  $('pinClose').onclick = () => pinMessage(null);
  $('toBottom').onclick = () => { if (S.detached) loadNewer(); else { S.newWhileUp = 0; $('msgs').scrollTo({ top: $('msgs').scrollHeight, behavior: 'smooth' }); } };
  $('replyCancel').onclick = cancelReply;
  $('attachCancel').onclick = clearAttach;
  $('attachBtn').onclick = () => { $('attachMenu').hidden = !$('attachMenu').hidden; };
  $('pickImage').onclick = () => { $('attachMenu').hidden = true; $('imagePick').click(); };
  $('pickFile').onclick = () => { $('attachMenu').hidden = true; $('filePick').click(); };
  $('imagePick').onchange = e => { const f = e.target.files[0]; e.target.value = ''; if (f) attachFile(f, false); };
  $('filePick').onchange = e => { const f = e.target.files[0]; e.target.value = ''; if (f) attachFile(f, true); };
  $('emojiBtn').onclick = () => { const p = $('emojiPanel'); p.hidden = !p.hidden; };
  const panel = $('emojiPanel');
  for (const e of EMOJIS) panel.append(h('button', { type: 'button', text: e, onclick: () => { const i = $('input'); const a = i.selectionStart ?? i.value.length, b = i.selectionEnd ?? a; i.setRangeText(e, a, b, 'end'); i.focus(); onInput(); } }));
  $('composer').addEventListener('submit', e => { e.preventDefault(); sendCurrent(); });
  $('input').addEventListener('input', onInput);
  $('input').addEventListener('keydown', e => {
    if (e.key !== 'Enter' || e.isComposing) return;
    const enterSend = S.settings.chat.enterSend;
    if (enterSend ? !e.shiftKey : (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendCurrent(); }
  });
  $('input').addEventListener('paste', e => {
    const f = Array.from(e.clipboardData ? e.clipboardData.files : [])[0];
    if (f) { e.preventDefault(); attachFile(f, !/^image\//.test(f.type)); }
  });
  const view = $('msgsWrap'); let dragDepth = 0;
  view.addEventListener('dragenter', e => { if (e.dataTransfer && Array.from(e.dataTransfer.types).includes('Files') && canWrite()) { dragDepth++; $('dropHint').hidden = false; } });
  view.addEventListener('dragleave', () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $('dropHint').hidden = true; });
  view.addEventListener('dragover', e => e.preventDefault());
  view.addEventListener('drop', e => { e.preventDefault(); dragDepth = 0; $('dropHint').hidden = true; const f = e.dataTransfer.files[0]; if (f && canWrite()) attachFile(f, !/^image\/(jpeg|png|webp)$/.test(f.type)); });
  $('micBtn').onclick = startRecording;
  $('recCancel').onclick = () => stopRecording(true);
  $('recSend').onclick = () => stopRecording(false);
  $('lightboxClose').onclick = () => $('lightbox').close();
  $('lightbox').addEventListener('click', e => { if (e.target === $('lightbox')) $('lightbox').close(); });
  setInterval(() => { if (S.cur && S.cur.kind === 'direct') updateHeadStatus(); }, 30000);
  window.addEventListener('keydown', e => { if (e.key === 'Escape') { closePopups(); $('emojiPanel').hidden = true; } });
}
async function toggleMute(key) {
  const list = S.settings.muted; const i = list.indexOf(key);
  if (i >= 0) list.splice(i, 1); else list.push(key);
  saveSettings(); renderList(); toast(i >= 0 ? 'Ovoz yoqildi' : 'Ovozsiz qilindi');
}
async function blockPeer(d) {
  const ok = await confirmBox({ title: 'Bloklash', text: d.peer.display_name + ' sizga yoza olmaydi va suhbat ro‘yxatdan yo‘qoladi. Keyinroq Sozlamalar → Maxfiylik orqali blokdan chiqarishingiz mumkin.', ok: 'Bloklash', danger: true });
  if (!ok) return;
  try { await api('/chats/' + d.id + '/block', { method: 'POST' }); removeChat(d.id); toast('Bloklandi'); } catch (e) { toast(e.message); }
}