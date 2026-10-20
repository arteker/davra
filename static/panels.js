'use strict';
/* ==========================================================================
   Davra — yon menyu, sozlamalar, profil, kontaktlar, saqlangan xabarlar,
   guruh/kanal oynasi, yo'naltirish va boshqa dialoglar.
   app.js dan keyin yuklanadi (umumiy yordamchilar o'sha yerda).
   ========================================================================== */

// ---------------------------------------------------------------- 3D SVG GIFTS
const GIFT_SVGS = {
  rose: `<svg class="gift-svg" viewBox="0 0 100 100"><defs><linearGradient id="roseGrad" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#ff4b4b"/><stop offset="100%" stop-color="#b80000"/></linearGradient></defs><path d="M50 20 C30 20 20 40 30 60 C40 80 60 80 70 60 C80 40 70 20 50 20 Z" fill="url(#roseGrad)"/><path d="M50 30 C40 30 35 40 40 50 C45 60 55 60 60 50 C65 40 60 30 50 30 Z" fill="#ff7b7b" opacity="0.6"/><path d="M50 60 Q60 80 70 95 Q50 85 50 60 Z" fill="#2d8a39"/></svg>`,
  star: `<svg class="gift-svg" viewBox="0 0 100 100"><defs><linearGradient id="starGrad" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#ffe600"/><stop offset="100%" stop-color="#ff9900"/></linearGradient></defs><polygon points="50,10 61,40 95,40 68,60 78,90 50,70 22,90 32,60 5,40 39,40" fill="url(#starGrad)" stroke="#fff" stroke-width="2" stroke-linejoin="round"/></svg>`,
  bear: `<svg class="gift-svg" viewBox="0 0 100 100"><defs><linearGradient id="bearGrad" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#d4a373"/><stop offset="100%" stop-color="#8b5a2b"/></linearGradient></defs><circle cx="30" cy="30" r="15" fill="url(#bearGrad)"/><circle cx="70" cy="30" r="15" fill="url(#bearGrad)"/><circle cx="50" cy="55" r="30" fill="url(#bearGrad)"/><circle cx="40" cy="50" r="5" fill="#333"/><circle cx="60" cy="50" r="5" fill="#333"/><ellipse cx="50" cy="65" rx="10" ry="7" fill="#f4e4d4"/><circle cx="50" cy="62" r="3" fill="#333"/></svg>`,
  crown: `<svg class="gift-svg" viewBox="0 0 100 100"><defs><linearGradient id="crownGrad" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#f9d423"/><stop offset="100%" stop-color="#ff4e50"/></linearGradient></defs><path d="M15 75 L25 30 L50 50 L75 30 L85 75 Z" fill="url(#crownGrad)" stroke="#fff" stroke-width="2" stroke-linejoin="round"/><circle cx="25" cy="25" r="6" fill="#ffeb3b"/><circle cx="50" cy="45" r="6" fill="#ffeb3b"/><circle cx="75" cy="25" r="6" fill="#ffeb3b"/><rect x="15" y="75" width="70" height="10" rx="3" fill="#ffeb3b"/></svg>`,
  rocket: `<svg class="gift-svg" viewBox="0 0 100 100"><defs><linearGradient id="rocketGrad" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#00d2ff"/><stop offset="100%" stop-color="#3a7bd5"/></linearGradient></defs><path d="M50 10 C50 10 30 40 30 70 L50 85 L70 70 C70 40 50 10 50 10 Z" fill="url(#rocketGrad)"/><circle cx="50" cy="45" r="8" fill="#fff"/><path d="M40 70 L30 90 L50 85 Z" fill="#ff4b4b"/><path d="M60 70 L70 90 L50 85 Z" fill="#ff4b4b"/></svg>`
};

function showGiftToast(g) {
  const toastEl = $('toast');
  toastEl.innerHTML = `<div style="display:flex; align-items:center; gap:12px;"><div class="gift-toast-icon">${GIFT_SVGS[g.gift_type] || '🎁'}</div> <div style="font-size:13px;"><b>${g.sender_name}</b> sizga <br><b style="color:var(--accent)">${g.gift_name}</b> sovg'a qildi!</div></div>`;
  toastEl.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { toastEl.hidden = true; }, 5000);
}

async function openGiftStore(chat) {
  const peer = chat.peer;
  const dlg = h('dialog', { class: 'sheet narrow' }, h('div', { class: 'sheet-head' }, h('h2', { text: "Sovg'a yuborish" })), 
    h('p', { class: 'muted', style: 'text-align:center; padding: 0 16px;' }, `${peer.display_name} ga sovg'a tanlang`));
  
  const body = h('div', { class: 'sheet-body' });
  const grid = h('div', { class: 'gift-grid' });
  body.append(grid); dlg.append(body);
  document.body.append(dlg); dlg.showModal();

  try {
    const catalog = await api('/gifts/catalog');
    for (const [key, info] of Object.entries(catalog)) {
      const card = h('div', { class: 'gift-card' });
      card.innerHTML = `<div class="gift-icon-wrap">${GIFT_SVGS[key]}</div><div class="gift-name">${info.name}</div><div class="gift-price">${info.price} ✦</div>`;
      
      card.onclick = async () => {
        if (!await confirmBox({ title: "Sovg'a yuborish", text: `${peer.display_name} ga ${info.name} sovg'a qilish uchun ${info.price} Nur yechiladi.`, ok: 'Yuborish' })) return;
        card.style.opacity = '0.5';
        try {
          const r = await api('/gifts/send', { method: 'POST', body: { receiver_id: peer.id, gift_type: key } });
          if (S.nur) S.nur.balance = r.new_balance;
          toast('🎁 Sovg\'a muvaffaqiyatli yuborildi!');
          dlg.close();
        } catch (e) { toast(e.message); card.style.opacity = '1'; }
      };
      grid.append(card);
    }
  } catch (e) { toast('Do\'konni yuklab bo\'lmadi'); }
  dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close(); });
}

async function loadProfileGifts(username, container) {
  try {
    const gifts = await api('/gifts/received/' + username);
    container.replaceChildren();
    if (!gifts.length) { container.append(h('div', { class: 'muted', style:'text-align:center; width:100%; font-size:12px;' }, 'Hali sovg\'alar yo\'q')); return; }
    gifts.slice(0, 10).forEach(g => {
      const el = h('div', { class: 'mini-gift', title: `${g.sender_name} dan` });
      el.innerHTML = GIFT_SVGS[g.gift_type] || '';
      container.append(el);
    });
  } catch { /* ignore */ }
}

// ---------------------------------------------------------------- dialog yordamchilari
function openDlg(id) { const d = $(id); if (!d.open) d.showModal(); return d; }
function closeDlg(id) { const d = $(id); if (d.open) d.close(); }
function confirmBox({ title, text, ok = 'Davom etish', danger = false }) {
  return new Promise(resolve => {
    $('confirmTitle').textContent = title || 'Tasdiqlash'; $('confirmText').textContent = text || '';
    const accept = $('acceptConfirm'); accept.textContent = ok; accept.classList.toggle('danger-text', false);
    accept.style.background = danger ? 'var(--danger)' : ''; accept.style.borderColor = danger ? 'var(--danger)' : '';
    const dlg = openDlg('confirmDialog'); let result = false;
    const done = () => { dlg.removeEventListener('close', done); resolve(result); };
    dlg.addEventListener('close', done);
    accept.onclick = () => { result = true; dlg.close(); };
    $('cancelConfirm').onclick = () => dlg.close();
  });
}
function askText({ title, label, value = '', max = 50, ok = 'Saqlash', placeholder = '' }) {
  return new Promise(resolve => {
    const input = h('input', { maxlength: max, value, placeholder, 'aria-label': label || title });
    let result = null;
    const form = h('form', { class: 'pad' }, h('h2', { text: title }), label ? h('p', { class: 'muted', text: label }) : null,
      h('div', { style: 'margin-top:12px' }, input),
      h('div', { class: 'confirm-actions' }, h('button', { class: 'btn ghost', type: 'button', onclick: () => dlg.close() }, 'Bekor qilish'), h('button', { class: 'btn primary', type: 'submit' }, ok)));
    const dlg = h('dialog', { class: 'sheet narrow' }, form);
    form.addEventListener('submit', e => { e.preventDefault(); result = input.value; dlg.close(); });
    dlg.addEventListener('close', () => { dlg.remove(); resolve(result); });
    dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close(); });
    document.body.append(dlg); dlg.showModal(); input.focus(); input.select();
  });
}

// ---------------------------------------------------------------- yon menyu
function refreshMenuProfile() {
  const me = S.me; if (!me) return;
  $('menuAvatar').replaceChildren(avatarEl(me, 56));
  $('menuDisplayName').replaceChildren(me.display_name, me.is_owner ? verifiedBadge() : '', me.is_premium ? premiumBadge() : '');
  $('menuUsername').textContent = '@' + me.username;
  $('menuDarkToggle').checked = document.documentElement.dataset.theme !== 'light';
}
function openDrawer() { refreshMenuProfile(); openDlg('appMenu'); }
function inviteFriends() {
  const data = { title: 'Davra', text: 'Men Davra messenjeridaman. Qo‘shiling: @' + S.me.username, url: location.origin };
  if (navigator.share) navigator.share(data).catch(() => { });
  else copyText(data.text + ' ' + data.url).then(() => toast('Taklif havolasi nusxalandi'));
}

// ---------------------------------------------------------------- profil
let profileEdit = false;
function openProfile(edit) {
  fillProfile(); setProfileEdit(!!edit); openDlg('profileDialog');
}
function fillProfile() {
  const me = S.me;
  $('profileAvatarLarge').replaceChildren(avatarEl(me, 96));
  $('profileHeroName').replaceChildren(me.display_name, me.is_owner ? verifiedBadge() : '', me.is_premium ? premiumBadge() : '');
  $('profileViewUsername').textContent = '@' + me.username;
  $('profileViewBio').textContent = me.bio || 'Bio qo‘shilmagan';
  $('ownerVerified').hidden = !me.is_owner;
  $('profileName').value = me.display_name; $('profileUsername').value = '@' + me.username; $('profileBio').value = me.bio || '';
  
  // Eski giftBox o'rniga:
let giftBox = $('myProfileGifts');
if (!giftBox) { 
  giftBox = h('div', { id: 'myProfileGifts', class: 'profile-gifts' }); 
  $('profileViewCards').after(giftBox); 
}
loadProfileGifts(me.username, giftBox);
}
function setProfileEdit(on) {
  profileEdit = on; $('profileForm').hidden = !on; $('profileViewCards').hidden = on;
  $('editProfileToggle').textContent = on ? '👁' : '✏️'; $('editProfileToggle').title = on ? 'Ko‘rish' : 'Tahrirlash';
}
async function saveProfile(e) {
  e.preventDefault(); const btn = $('profileSave'); btn.disabled = true;
  try {
    S.me = await api('/profile', { method: 'PUT', body: { display_name: $('profileName').value, bio: $('profileBio').value } });
    fillProfile(); refreshMenuProfile(); setProfileEdit(false); toast('Profil saqlandi');
  } catch (err) { toast(err.message); }
  btn.disabled = false;
}
async function changeAvatar(file) {
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) return toast('Rasm 2 MB dan kichik bo‘lsin');
  try {
    const r = await api('/profile/avatar', { method: 'POST', body: file, headers: { 'Content-Type': file.type || 'image/jpeg' } });
    S.me.avatar_url = r.avatar_url; fillProfile(); refreshMenuProfile(); toast('Rasm yangilandi');
  } catch (err) { toast(err.message); }
}
async function removeAvatar() {
  try { await api('/profile/avatar', { method: 'DELETE' }); S.me.avatar_url = null; fillProfile(); refreshMenuProfile(); toast('Rasm olib tashlandi'); } catch (err) { toast(err.message); }
}

// ---------------------------------------------------------------- kontaktlar
function openContacts() {
  $('contactsSearchInput').value = ''; renderContacts(); openDlg('contactsDialog');
}
function renderContacts() {
  const q = $('contactsSearchInput').value.trim().toLowerCase().replace(/^@/, '');
  const list = S.chats.filter(c => c.status !== 'blocked' && (!q || c.peer.display_name.toLowerCase().includes(q) || c.peer.username.includes(q)))
    .sort((a, b) => a.peer.display_name.localeCompare(b.peer.display_name));
  const box = $('contactsList'); box.replaceChildren();
  if (!list.length) box.append(h('div', { class: 'emptyl', text: q ? 'Hech kim topilmadi' : 'Hali kontakt yo‘q. Qidiruvdan odam toping va suhbat boshlang.' }));
  for (const c of list) box.append(h('button', { type: 'button', class: 'item', onclick: () => { closeDlg('contactsDialog'); openChat('direct', c.id); } },
    avatarEl(c.peer), h('div', { class: 'ib' }, h('div', { class: 'nm' }, c.peer.display_name, c.peer.is_owner ? verifiedBadge() : null, c.peer.is_premium ? premiumBadge() : null), h('div', { class: 'pv', text: lastSeenText(c.peer) + ' · @' + c.peer.username }))));
}
function openContact(chat) {
  const p = chat.peer, key = keyOf('direct', chat.id);
  $('contactAvatar').replaceChildren(avatarEl(p, 96));
  $('contactTitle').replaceChildren(p.display_name, p.is_owner ? verifiedBadge() : '', p.is_premium ? premiumBadge() : '');
  $('contactStatus').textContent = lastSeenText(p); $('contactUsername').textContent = '@' + p.username;
  $('contactVerification').hidden = !p.is_owner; $('contactBio').textContent = p.bio || ''; $('contactBio').hidden = !p.bio;
  
  // Eski contactGifts o'rniga:
let giftBox = $('contactGifts');
if (!giftBox) { 
  giftBox = h('div', { id: 'contactGifts', class: 'profile-gifts' }); 
  $('contactBio').after(giftBox); 
}
loadProfileGifts(p.username, giftBox);
  
  $('contactStart').textContent = 'Xabar yozish'; $('contactStart').onclick = () => { closeDlg('contactDialog'); openChat('direct', chat.id); };
  const mute = $('contactMute'); mute.textContent = isMuted(key) ? '🔔 Ovozni yoqish' : '🔕 Ovozsiz qilish';
  mute.onclick = () => { toggleMute(key); closeDlg('contactDialog'); };
  $('contactBlock').onclick = () => { closeDlg('contactDialog'); blockPeer(chat); };
  openDlg('contactDialog');
}

// ---------------------------------------------------------------- saqlangan xabarlar
async function openSaved() { openDlg('savedDialog'); await renderSaved(); }
async function renderSaved() {
  const box = $('savedList');
  try {
    const items = await api('/saved'); box.replaceChildren();
    if (!items.length) box.append(h('div', { class: 'emptyl', text: 'Bu yerda o‘zingiz uchun eslatma va saqlangan xabarlar turadi. Xabarni bosib «Saqlash»ni tanlang.' }));
    for (const it of items) {
      const a = it.attachment;
      box.append(h('div', { class: 'saved-item' },
        it.source ? h('div', { class: 'saved-from', text: '↪ ' + it.source }) : null,
        a && a.kind === 'image' ? h('img', { class: 'media-img', src: '/api/files/' + a.key, alt: '', style: 'max-width:220px', onclick: () => openLightbox('/api/files/' + a.key, a.name) }) : null,
        a && a.kind === 'voice' ? voiceEl(a) : null,
        a && a.kind === 'file' ? h('a', { class: 'file', href: '/api/files/' + a.key, download: a.name }, h('span', { class: 'file-icon', text: '📄' }), h('span', { class: 'file-copy' }, h('b', { text: a.name }), h('span', { text: fmtSize(a.size) }))) : null,
        it.text ? h('div', { class: 'msg-text' }, richText(it.text)) : null,
        h('div', { class: 'saved-meta' }, h('span', { text: dayLabel(it.created_at) + ', ' + fmtTime(it.created_at) }),
          h('button', { type: 'button', onclick: async () => { try { await api('/saved/' + it.id, { method: 'DELETE' }); renderSaved(); } catch (e) { toast(e.message); } } }, 'O‘chirish'))));
    }
  } catch (e) { box.replaceChildren(h('div', { class: 'emptyl', text: e.message })); }
}
async function addSaved(e) {
  e.preventDefault(); const i = $('savedInput'); const text = i.value.trim(); if (!text) return;
  try { await api('/saved', { method: 'POST', body: { text } }); i.value = ''; renderSaved(); } catch (err) { toast(err.message); }
}

// ---------------------------------------------------------------- xabarni tahrirlash
function openEditMessage(m) {
  $('editMessageText').value = m.text || ''; const dlg = openDlg('editMessageDialog');
  $('editMessageForm').onsubmit = async e => {
    e.preventDefault();
    const text = $('editMessageText').value.trim();
    if (!text && !m.attachment) return toast('Xabar bo‘sh bo‘lmasin');
    const base = S.cur.kind === 'room' ? '/rooms/' : '/chats/';
    try { const r = await api(base + S.cur.id + '/messages/' + m.id, { method: 'PATCH', body: { text } }); replaceMsg(r); dlg.close(); } catch (err) { toast(err.message); }
  };
  $('editMessageText').focus();
}

// ---------------------------------------------------------------- yo'naltirish
let fwdSel = new Set();
function forwardTargets() {
  return allItems().filter(it => it.kind === 'room' ? (it.data.room_type === 'group' || it.data.role === 'owner' || it.data.role === 'admin') : it.data.status !== 'blocked' && !(it.data.status === 'pending' && it.data.i_am_requester && it.data.my_count >= 1));
}
function openForward(m) {
  S.forwardingMsg = { id: m.id, kind: S.cur.kind, source: S.cur.id }; fwdSel = new Set(); $('forwardSearch').value = '';
  renderForward(); syncForwardBtn(); openDlg('forwardDialog');
}
function renderForward() {
  const q = $('forwardSearch').value.trim().toLowerCase(); const box = $('forwardList'); box.replaceChildren();
  const items = forwardTargets().filter(it => !q || (it.kind === 'room' ? it.data.title : it.data.peer.display_name).toLowerCase().includes(q)).sort((a, b) => itemTime(b) - itemTime(a));
  if (!items.length) box.append(h('div', { class: 'emptyl', text: 'Mos suhbat topilmadi' }));
  for (const it of items) {
    const key = itemKey(it), ent = it.kind === 'room' ? it.data : it.data.peer;
    const cb = h('input', { type: 'checkbox', checked: fwdSel.has(key) });
    cb.addEventListener('change', () => { if (cb.checked) { if (fwdSel.size >= 5) { cb.checked = false; return toast('Ko‘pi bilan 5 ta suhbat'); } fwdSel.add(key); } else fwdSel.delete(key); syncForwardBtn(); });
    box.append(h('label', { class: 'fw-row' }, cb, avatarEl(ent), h('span', { class: 'fw-copy' }, h('b', {}, it.kind === 'room' ? it.data.title : it.data.peer.display_name, (it.kind === 'room' ? it.data.verified : it.data.peer.is_owner) ? verifiedBadge() : null), h('small', { text: it.kind === 'room' ? (it.data.room_type === 'channel' ? 'Kanal' : 'Guruh') : '@' + it.data.peer.username }))));
  }
}
function syncForwardBtn() { const b = $('forwardSend'); b.disabled = !fwdSel.size; b.textContent = fwdSel.size ? 'Yuborish (' + fwdSel.size + ')' : 'Yuborish'; }
async function doForward() {
  const f = S.forwardingMsg; if (!f || !fwdSel.size) return;
  const targets = Array.from(fwdSel).map(k => k.startsWith('r:') ? { kind: 'room', id: parseInt(k.slice(2), 10) } : { kind: 'direct', id: parseInt(k, 10) });
  $('forwardSend').disabled = true;
  try {
    const r = await api('/forward', { method: 'POST', body: { source: f.kind, source_id: f.source, message_id: f.id, targets } });
    closeDlg('forwardDialog');
    toast(r.errors.length ? r.sent + ' ta yuborildi, ' + r.errors.length + ' tasida xato: ' + r.errors[0].message : 'Xabar yo‘naltirildi');
  } catch (e) { toast(e.message); syncForwardBtn(); }
}

// ---------------------------------------------------------------- guruh / kanal yaratish
function openCreateRoom(kind) {
  $('createRoomKind').value = kind;
  $('createRoomTitle').textContent = kind === 'channel' ? 'Yangi kanal' : 'Yangi guruh';
  $('createRoomDescription').textContent = kind === 'channel' ? 'Kanalda faqat adminlar post yozadi, obunachilar o‘qiydi va reaksiya bildiradi. Kanal ochiq kanallar ro‘yxatida ko‘rinadi.' : 'Guruhda hamma a’zolar yoza oladi. A’zolarni keyinroq ham qo‘shishingiz mumkin.';
  $('createRoomMembersField').hidden = kind === 'channel';
  $('createRoomName').value = ''; $('createRoomMembers').value = '';
  openDlg('createRoomDialog'); $('createRoomName').focus();
}
async function submitCreateRoom(e) {
  e.preventDefault(); const btn = e.target.querySelector('.create-submit'); btn.disabled = true;
  const kind = $('createRoomKind').value;
  const usernames = kind === 'group' ? $('createRoomMembers').value.split(/[,\s]+/).map(s => s.trim().replace(/^@/, '')).filter(Boolean) : [];
  try {
    const r = await api('/rooms', { method: 'POST', body: { kind, title: $('createRoomName').value, usernames, is_public: true } });
    upsertRoom(r); renderList(); closeDlg('createRoomDialog'); openChat('room', r.id);
    if (r.invited) toast(r.invited + ' kishiga taklif yuborildi');
  } catch (err) { toast(err.message); }
  btn.disabled = false;
}

// ---------------------------------------------------------------- guruh / kanal oynasi (Telegram uslubida sahifalar)
const RP = { id: null, stack: [], members: null, bans: null };
const ROLE = { owner: 'Ega', admin: 'Admin', member: 'A‘zo' };
const RIGHT_LABELS = [
  ['change_info', 'Ma‘lumotni o‘zgartirish', 'Nom, tavsif va rasm'],
  ['post_messages', 'Xabar yozish', 'Kanalga post joylash'],
  ['delete_messages', 'Xabarlarni o‘chirish', 'Boshqalarning xabarlarini ham'],
  ['pin_messages', 'Xabarlarni qadash', null],
  ['add_members', 'A‘zo qo‘shish', 'Taklif va havola'],
  ['ban_users', 'A‘zolarni chiqarish', 'Chiqarish va qaytarish'],
  ['add_admins', 'Yangi adminlar tayinlash', 'Ehtiyot bo‘ling: bu admin boshqalarni ham tayinlay oladi'],
];
const curRoom = () => S.rooms.find(r => r.id === RP.id);
const rr = () => (curRoom() || {}).rights || {};
async function openRoomDialog(id, focusInvite) {
  RP.id = id; RP.stack = []; RP.members = null; RP.bans = null;
  roomPush('', roomMain); openDlg('roomDialog');
  if (focusInvite) roomAddMember();
}
function onRoomUpdated(room) { if (RP.id === room.id && $('roomDialog').open) { RP.members = null; renderRoomPage(); } }
function roomPush(title, build, arg) { RP.stack.push({ title, build, arg }); renderRoomPage(); }
function roomPop() { if (RP.stack.length > 1) { RP.stack.pop(); renderRoomPage(); } else closeDlg('roomDialog'); }
function renderRoomPage() {
  const room = curRoom(); if (!room) return closeDlg('roomDialog');
  const page = RP.stack[RP.stack.length - 1], body = $('roomBody');
  $('roomTitleBar').textContent = page.title || (room.room_type === 'channel' ? 'Kanal ma‘lumoti' : 'Guruh ma‘lumoti');
  $('roomBack').hidden = RP.stack.length < 2;
  const y = body.scrollTop; body.replaceChildren(); page.build(body, room, page.arg); body.scrollTop = y;
}
async function loadMembers(force) {
  if (RP.members && !force) return RP.members;
  RP.members = await api('/rooms/' + RP.id + '/members'); S.members[RP.id] = RP.members; return RP.members;
}
const savedRoom = r => { if (r && r.id) { upsertRoom(r); renderList(); if (S.cur) renderHead(); } };

function roomMain(body, room) {
  const isCh = room.room_type === 'channel', R = room.rights || {};
  body.append(h('div', { class: 'room-hero' }, avatarEl(room, 84),
    h('div', { class: 'sh-name' }, room.title, room.verified ? verifiedBadge() : null),
    h('div', { class: 'sh-un', text: (isCh ? 'Kanal' : 'Guruh') + ' · ' + room.member_count + (isCh ? ' obunachi' : ' ishtirokchi') })));
  if (room.description) body.append(h('div', { class: 'room-desc', text: room.description }));
  const rows = [];
  if (R.change_info) rows.push(lrow('✏️', 'Tahrirlash', 'Nom, tavsif, rasm va sozlamalar', { chev: true, onclick: () => roomPush('Tahrirlash', roomEdit) }));
  if (R.add_members) rows.push(lrow('🔗', 'Taklif havolasi', 'Havola orqali odam qo‘shish', { chev: true, onclick: () => roomPush('Taklif havolasi', roomLink) }));
  if (room.role === 'owner' || room.role === 'admin') rows.push(lrow('🛡', 'Administratorlar', 'Huquqlarni boshqarish', { chev: true, onclick: () => roomPush('Administratorlar', roomAdmins) }));
  rows.push(lrow('👥', isCh ? 'Obunachilar' : 'Ishtirokchilar', String(room.member_count), { chev: true, onclick: () => roomPush(isCh ? 'Obunachilar' : 'Ishtirokchilar', roomMembers) }));
  if (R.ban_users) rows.push(lrow('🚫', 'Chiqarilganlar', 'Qaytib kira olmaydiganlar', { chev: true, onclick: () => roomPush('Chiqarilganlar', roomBans) }));
  body.append(gap(), ...rows);
  const key = keyOf('room', room.id);
  body.append(gap(), switchRow('🔕', 'Ovozsiz', 'Bildirishnomalarni o‘chirish', isMuted(key), () => toggleMute(key)));
  if (!isCh) {
    const mine = h('div', { class: 'lrow' }, h('span', { class: 'li', text: '🏷' }), h('span', { class: 'lt' }, h('b', { text: 'Mening yorlig‘im' }), h('small', { text: 'Guruhda ismim yonida ko‘rinadi' })));
    const inp = h('input', { class: 'inline-input', maxlength: 16, placeholder: 'dizayner' });
    inp.addEventListener('change', async () => { try { await api('/rooms/' + room.id + '/members/' + S.me.id + '/tag', { method: 'PUT', body: { tag: inp.value } }); toast('Yorliq saqlandi'); RP.members = null; } catch (e) { toast(e.message); } });
    mine.append(inp); body.append(mine);
    loadMembers().then(ms => { const me = ms.find(m => m.id === S.me.id); if (me) inp.value = me.tag || ''; }).catch(() => { });
  }
  body.append(gap());
  if (room.role === 'owner') {
    body.append(lrow('👑', 'Egalikni topshirish', null, { onclick: transferRoom }),
      lrow('🗑', isCh ? 'Kanalni o‘chirish' : 'Guruhni o‘chirish', 'Barcha xabarlar bilan butunlay', { danger: true, onclick: deleteRoom }),
      h('div', { class: 'pad muted', text: 'Ega chiqishidan oldin egalikni topshirishi yoki suhbatni o‘chirishi kerak.' }));
  } else body.append(lrow('🚪', isCh ? 'Obunani bekor qilish' : 'Guruhdan chiqish', null, { danger: true, onclick: leaveRoom }));
  body.append(h('div', { style: 'height:16px' }));
}

// ---- tahrirlash
function roomEdit(body, room) {
  const isCh = room.room_type === 'channel';
  const av = h('div', { class: 'edit-av' });
  av.append(avatarEl(room, 84));
  body.append(h('div', { class: 'room-hero' }, av,
    h('div', { class: 'btn-row' },
      h('button', { class: 'btn small', type: 'button', onclick: () => $('roomAvatarFile').click() }, '📷 Rasm tanlash'),
      room.avatar_url ? h('button', { class: 'btn small ghost', type: 'button', onclick: async () => { try { savedRoom(await api('/rooms/' + room.id + '/avatar', { method: 'DELETE' })); toast('Rasm olib tashlandi'); } catch (e) { toast(e.message); } } }, 'Olib tashlash') : null)));
  const name = h('input', { maxlength: 80, value: room.title }), desc = h('textarea', { maxlength: 255, rows: 3, placeholder: 'Tavsif (ixtiyoriy)' });
  desc.value = room.description || '';
  const save = h('button', { class: 'btn primary', type: 'button' }, 'Saqlash');
  save.onclick = async () => {
    if (!name.value.trim()) return toast('Nom bo‘sh bo‘lmasin');
    save.disabled = true;
    try { await api('/rooms/' + room.id, { method: 'PATCH', body: { title: name.value, description: desc.value } }); S.rooms = await api('/rooms'); renderList(); if (S.cur) renderHead(); toast('Saqlandi'); renderRoomPage(); }
    catch (e) { toast(e.message); }
    save.disabled = false;
  };
  body.append(h('div', { class: 'pad form-stack' }, h('label', { class: 'field' }, h('span', { text: isCh ? 'Kanal nomi' : 'Guruh nomi' }), name), h('label', { class: 'field' }, h('span', { text: 'Tavsif' }), desc), save));
  const patch = async (k, v, input) => { try { await api('/rooms/' + room.id, { method: 'PATCH', body: { [k]: v } }); S.rooms = await api('/rooms'); renderList(); if (S.cur) { renderHead(); renderMsgs(true); } } catch (e) { toast(e.message); input.checked = !v; } };
  body.append(gap(), gTitle('Sozlamalar'),
    switchRow('🌐', 'Ommaviy', 'Qidiruvda ko‘rinadi, hamma qo‘shila oladi', room.is_public, (v, i) => patch('is_public', v, i)));
  if (isCh) body.append(switchRow('✍️', 'Postlarga imzo', 'Post muallifining ismi ko‘rsatiladi', room.sign_messages, (v, i) => patch('sign_messages', v, i)));
  body.append(switchRow('😊', 'Reaksiyalar', 'Xabarlarga reaksiya qo‘yish mumkin', room.reactions_enabled !== false, (v, i) => patch('reactions_enabled', v, i)), h('div', { style: 'height:16px' }));
}

// ---- taklif havolasi
function roomLink(body, room) {
  const box = h('div', { class: 'pad' }); body.append(box);
  const show = code => {
    const url = location.origin + '/?join=' + code;
    box.replaceChildren(h('div', { class: 'link-box', text: url }),
      h('div', { class: 'btn-row' },
        h('button', { class: 'btn', type: 'button', onclick: () => copyText(url) }, '⧉ Nusxa olish'),
        h('button', { class: 'btn ghost', type: 'button', onclick: async () => { if (!await confirmBox({ title: 'Havolani yangilash', text: 'Eski havola ishlamay qoladi.', ok: 'Yangilash', danger: true })) return; try { show((await api('/rooms/' + room.id + '/invite-link', { method: 'POST' })).code); toast('Yangi havola yaratildi'); } catch (e) { toast(e.message); } } }, '♻ Yangilash')),
      h('p', { class: 'muted', text: 'Havola orqali kirgan odam avtomatik a‘zo bo‘ladi. Chiqarilganlar qaytib kira olmaydi.' }));
  };
  api('/rooms/' + room.id + '/invite-link').then(r => show(r.code)).catch(e => box.replaceChildren(h('div', { class: 'emptyl', text: e.message })));
}

// ---- a'zolar
function roomMembers(body, room) {
  if (room.rights && room.rights.add_members) body.append(lrow('➕', 'A‘zo qo‘shish', 'Username orqali', { onclick: roomAddMember }), gap());
  const box = h('div'); body.append(box);
  loadMembers().then(ms => { box.replaceChildren(...ms.map(m => memberRow(room, m))); }).catch(e => box.replaceChildren(h('div', { class: 'emptyl', text: e.message })));
}
async function roomAddMember() {
  const u = await askText({ title: 'A‘zo qo‘shish', label: 'Username', max: 33, placeholder: '@username', ok: 'Qo‘shish' });
  if (!u) return;
  try { const r = await api('/rooms/' + RP.id + '/members', { method: 'POST', body: { username: u } }); toast(r.invite_state === 'invited' ? 'Taklif yuborildi' : 'A‘zo qo‘shildi'); savedRoom(r); RP.members = null; renderRoomPage(); }
  catch (e) { toast(e.message); }
}
function canManage(room, m) {
  if (m.id === S.me.id || m.role === 'owner') return false;
  if (m.role === 'admin') return room.role === 'owner' || m.promoted_by === S.me.id;
  return true;
}
function memberRow(room, m, opts = {}) {
  const isMe = m.id === S.me.id, R = room.rights || {};
  const row = h('div', { class: 'room-member' + (opts.onclick ? ' clickable' : ''), onclick: opts.onclick }, avatarEl(m, 40),
    h('div', { class: 'room-member-name' }, h('b', {}, m.display_name + (isMe ? ' (siz)' : ''), m.is_owner ? verifiedBadge() : null, m.is_premium ? premiumBadge() : null, m.tag ? h('span', { class: 'tag-chip', text: m.tag }) : null),
      h('span', { text: '@' + m.username })),
    m.role !== 'member' ? h('span', { class: 'role-pill', text: ROLE[m.role] }) : null);
  if (opts.onclick) return row;
  const tools = h('div', { class: 'member-tools' });
  if (canManage(room, m)) {
    if (R.add_admins) tools.append(h('button', { type: 'button', onclick: () => roomPush('Admin huquqlari', roomRights, m) }, m.role === 'admin' ? 'Huquqlar' : 'Admin qilish'));
    if (R.ban_users) tools.append(h('button', { type: 'button', class: 'danger', onclick: async () => { if (await confirmBox({ title: 'A‘zoni chiqarish', text: m.display_name + ' chiqariladi va qaytib kira olmaydi.', ok: 'Chiqarish', danger: true })) memberAct('/rooms/' + room.id + '/members/' + m.id, 'DELETE'); } }, 'Chiqarish'));
  }
  if (room.room_type === 'group' && (R.pin_messages || room.role === 'owner' || room.role === 'admin') && !isMe && m.role !== 'owner')
    tools.append(h('button', { type: 'button', onclick: async () => { const t = await askText({ title: 'Yorliq', label: m.display_name + ' uchun yorliq (16 belgigacha, bo‘sh = o‘chirish)', value: m.tag, max: 16 }); if (t !== null) memberAct('/rooms/' + room.id + '/members/' + m.id + '/tag', 'PUT', { tag: t }); } }, '🏷 Yorliq'));
  if (tools.childElementCount) row.querySelector('.room-member-name').append(tools);
  return row;
}
async function memberAct(path, method, body) {
  try { await api(path, { method, body }); RP.members = null; RP.bans = null; renderRoomPage(); } catch (e) { toast(e.message); }
}

// ---- administratorlar
function roomAdmins(body, room) {
  if (room.rights && room.rights.add_admins) body.append(lrow('➕', 'Administrator qo‘shish', 'A‘zolardan tanlang', { chev: true, onclick: () => roomPush('Administrator qo‘shish', roomPickAdmin) }), gap());
  body.append(gTitle('Administratorlar'));
  const box = h('div'); body.append(box);
  loadMembers().then(ms => {
    const admins = ms.filter(m => m.role !== 'member');
    box.replaceChildren(...admins.map(m => memberRow(room, m, {
      onclick: canManage(room, m) && room.rights && room.rights.add_admins ? () => roomPush('Admin huquqlari', roomRights, m) : null })));
    for (const el of box.children) { const m = admins[[...box.children].indexOf(el)]; const sub = el.querySelector('.room-member-name span'); if (sub && m.role === 'owner') sub.textContent = 'Egasi'; else if (sub && m.promoted_by) { const p = ms.find(x => x.id === m.promoted_by); if (p) sub.textContent = 'Tayinladi: ' + p.display_name; } }
  }).catch(e => box.replaceChildren(h('div', { class: 'emptyl', text: e.message })));
  body.append(h('div', { class: 'pad muted', text: 'Adminlar o‘ziga berilgan huquqlar doirasida ishlaydi. Egasi barcha huquqlarga ega.' }));
}
function roomPickAdmin(body, room) {
  const search = h('input', { class: 'search-in', placeholder: 'Ism yoki username', type: 'search' }); body.append(h('div', { class: 'pad' }, search));
  const box = h('div'); body.append(box);
  let list = [];
  const draw = () => {
    const q = search.value.trim().toLowerCase().replace(/^@/, '');
    const f = list.filter(m => !q || m.display_name.toLowerCase().includes(q) || m.username.toLowerCase().includes(q));
    box.replaceChildren(...(f.length ? f.map(m => memberRow(room, m, { onclick: () => { RP.stack.pop(); roomPush('Admin huquqlari', roomRights, m); } })) : [h('div', { class: 'emptyl', text: 'Mos a‘zo topilmadi' })]));
  };
  search.addEventListener('input', draw);
  loadMembers().then(ms => { list = ms.filter(m => m.role === 'member'); draw(); }).catch(e => box.replaceChildren(h('div', { class: 'emptyl', text: e.message })));
}
function roomRights(body, room, m) {
  const mine = room.rights || {};
  const isAdmin = m.role === 'admin';
  const cur = Object.assign({ change_info: true, post_messages: true, delete_messages: true, pin_messages: true, add_members: true, ban_users: true, add_admins: false }, isAdmin ? (m.rights || {}) : {});
  const draft = {};
  body.append(h('div', { class: 'room-hero' }, avatarEl(m, 72), h('div', { class: 'sh-name', text: m.display_name }), h('div', { class: 'sh-un', text: '@' + m.username })), gTitle('Admin nimalar qila oladi?'));
  for (const [k, title, sub] of RIGHT_LABELS) {
    if (k === 'post_messages' && room.room_type !== 'channel') continue;
    draft[k] = !!cur[k];
    const allowed = !!mine[k];
    const row = switchRow('', title, allowed ? sub : 'Sizda bu huquq yo‘q', draft[k], v => { draft[k] = v; });
    if (!allowed) row.querySelector('input').disabled = true;
    body.append(row);
  }
  const save = h('button', { class: 'btn primary wide', type: 'button' }, isAdmin ? 'Saqlash' : 'Admin qilish');
  save.onclick = async () => {
    save.disabled = true;
    try { await api('/rooms/' + room.id + '/members/' + m.id + '/admin', { method: 'PUT', body: { rights: draft } }); toast(isAdmin ? 'Huquqlar saqlandi' : m.display_name + ' admin qilindi'); RP.members = null; RP.stack.pop(); renderRoomPage(); }
    catch (e) { toast(e.message); save.disabled = false; }
  };
  body.append(h('div', { class: 'pad' }, save));
  if (isAdmin) body.append(gap(), lrow('⛔', 'Adminlikdan olish', null, { danger: true, onclick: async () => {
    if (!await confirmBox({ title: 'Adminlikdan olish', text: m.display_name + ' oddiy a‘zo bo‘lib qoladi.', ok: 'Olish', danger: true })) return;
    try { await api('/rooms/' + room.id + '/members/' + m.id + '/admin', { method: 'DELETE' }); toast('Adminlik olib tashlandi'); RP.members = null; RP.stack.pop(); renderRoomPage(); } catch (e) { toast(e.message); } } }));
  body.append(h('div', { style: 'height:16px' }));
}

// ---- chiqarilganlar
function roomBans(body, room) {
  const box = h('div'); body.append(box);
  api('/rooms/' + room.id + '/bans').then(list => {
    if (!list.length) return box.replaceChildren(h('div', { class: 'emptyl', text: 'Chiqarilgan foydalanuvchi yo‘q' }));
    box.replaceChildren(...list.map(b => h('div', { class: 'room-member' }, avatarEl(b.user, 40),
      h('div', { class: 'room-member-name' }, h('b', { text: b.user.display_name }), h('span', { text: '@' + b.user.username }),
        h('div', { class: 'member-tools' }, h('button', { type: 'button', onclick: async () => { try { await api('/rooms/' + room.id + '/bans/' + b.user.id, { method: 'DELETE' }); toast('Qaytarildi: endi qo‘shila oladi'); renderRoomPage(); } catch (e) { toast(e.message); } } }, 'Qaytarish'))))));
  }).catch(e => box.replaceChildren(h('div', { class: 'emptyl', text: e.message })));
}

async function transferRoom() {
  const u = await askText({ title: 'Egalikni topshirish', label: 'Yangi ega username’i (u suhbat a’zosi bo‘lishi kerak). Siz admin bo‘lib qolasiz.', max: 33, placeholder: '@username', ok: 'Topshirish' });
  if (!u) return;
  try { await api('/rooms/' + RP.id + '/transfer', { method: 'POST', body: { username: u } }); toast('Egalik topshirildi'); } catch (e) { toast(e.message); }
}
async function deleteRoom() {
  const room = curRoom();
  if (!await confirmBox({ title: 'O‘chirish', text: '“' + room.title + '” va undagi barcha xabarlar butunlay o‘chiriladi.', ok: 'O‘chirish', danger: true })) return;
  try { await api('/rooms/' + room.id, { method: 'DELETE' }); closeDlg('roomDialog'); } catch (e) { toast(e.message); }
}
async function leaveRoom() {
  const room = curRoom();
  if (!await confirmBox({ title: 'Chiqish', text: '“' + room.title + '” suhbatidan chiqmoqchimisiz?', ok: 'Chiqish', danger: true })) return;
  try {
    await api('/rooms/' + room.id + '/leave', { method: 'POST' });
    S.rooms = S.rooms.filter(r => r.id !== room.id); closeDlg('roomDialog'); if (inCurrent('room', room.id)) closeChat(); renderList();
  } catch (e) { toast(e.message); }
}
async function uploadRoomAvatar(file) {
  if (!file) return;
  try { savedRoom(await api('/rooms/' + RP.id + '/avatar', { method: 'POST', body: file, headers: { 'Content-Type': file.type || 'image/jpeg' } })); toast('Rasm yangilandi'); renderRoomPage(); }
  catch (e) { toast(e.message); }
}

// ---------------------------------------------------------------- SOZLAMALAR
const ST = { stack: [] };
function openSettings() { ST.stack = []; settingsPush('Sozlamalar', pageMain); openDlg('settingsDialog'); }
function settingsPush(title, build) { ST.stack.push({ title, build }); renderSettings(); }
function settingsPop() { if (ST.stack.length > 1) { ST.stack.pop(); renderSettings(); } else closeDlg('settingsDialog'); }
function renderSettings() {
  const page = ST.stack[ST.stack.length - 1]; const body = $('settingsBody');
  $('settingsTitle').textContent = page.title; $('settingsBack').hidden = ST.stack.length < 2;
  const y = body.scrollTop; body.replaceChildren(); page.build(body); if (page.keepScroll) body.scrollTop = y;
}
const lrow = (icon, title, sub, opt = {}) => {
  const tag = opt.onclick ? 'button' : 'div';
  return h(tag, { class: 'lrow' + (opt.danger ? ' danger' : '') + (opt.onclick ? '' : ' static'), type: opt.onclick ? 'button' : null, onclick: opt.onclick },
    icon ? h('span', { class: 'li', text: icon }) : null, h('span', { class: 'lt' }, h('b', { text: title }), sub ? h('small', { text: sub }) : null),
    opt.value ? h('span', { class: 'lv', text: opt.value }) : null, opt.chev ? h('span', { class: 'chev', text: '›' }) : null);
};
function switchRow(icon, title, sub, checked, onchange) {
  const input = h('input', { type: 'checkbox', checked: !!checked, 'aria-label': title });
  input.addEventListener('change', () => onchange(input.checked, input));
  return h('label', { class: 'lrow' }, icon ? h('span', { class: 'li', text: icon }) : null, h('span', { class: 'lt' }, h('b', { text: title }), sub ? h('small', { text: sub }) : null), h('span', { class: 'switch' }, input, h('span')));
}
const gTitle = t => h('div', { class: 'group-title', text: t });
const gap = () => h('div', { class: 'gap' });

function pageMain(body) {
  const me = S.me;
  body.append(h('button', { class: 'settings-hero', type: 'button', onclick: () => openProfile(false) }, avatarEl(me, 64),
    h('div', {}, h('div', { class: 'sh-name' }, me.display_name, me.is_owner ? verifiedBadge() : null, me.is_premium ? premiumBadge() : null), h('div', { class: 'sh-un', text: '@' + me.username }), h('div', { class: 'sh-bio', text: me.bio || 'Bio qo‘shilmagan' }))));
  body.append(gap(), gTitle('Sozlamalar'),
    lrow('👤', 'Mening akkauntim', 'Ism, bio va rasm', { chev: true, onclick: () => openProfile(true) }),
    lrow('🔔', 'Bildirishnomalar', 'Push, ovoz, ovozsiz suhbatlar', { chev: true, onclick: () => settingsPush('Bildirishnomalar', pageNotif) }),
    lrow('🔒', 'Maxfiylik va xavfsizlik', 'Oxirgi ko‘rilgan, parol, seanslar, bloklanganlar', { chev: true, onclick: () => settingsPush('Maxfiylik va xavfsizlik', pagePrivacy) }),
    lrow('💬', 'Suhbat sozlamalari', 'Matn o‘lchami, fon, Enter tugmasi, tungi rejim', { chev: true, onclick: () => settingsPush('Suhbat sozlamalari', pageChat) }),
    lrow('📁', 'Suhbat papkalari', S.settings.folders.length ? S.settings.folders.length + ' ta papka' : 'Suhbatlarni guruhlab saqlang', { chev: true, onclick: () => settingsPush('Suhbat papkalari', pageFolders) }),
    lrow('⚙️', 'Qo‘shimcha', 'Ko‘rinish o‘lchami, animatsiya, kesh', { chev: true, onclick: () => settingsPush('Qo‘shimcha', pageAdvanced) }),
    lrow('🌐', 'Til', 'O‘zbekcha', { chev: true, onclick: () => settingsPush('Til', pageLang) }));
  body.append(gap(), gTitle('Davra+'));
  body.append(
    h('button', { class: 'lrow', type: 'button', onclick: () => settingsPush('Davra Premium', pagePremium) },
      h('span', { class: 'grad premium', text: '★' }),
      h('span', { class: 'lt' }, h('b', { text: 'Davra Premium' }), h('small', { text: 'Maxsus belgi, katta fayllar va boshqalar' })),
      S.me.is_premium ? h('span', { class: 'lv', text: 'Faol' }) : h('span', { class: 'chev', text: '›' })
    )
  );
  body.append(h('button', { class: 'lrow', type: 'button', onclick: openNurPage },
    h('span', { class: 'grad coins', text: '✦' }),
    h('span', { class: 'lt' }, h('b', { text: 'Nur' }), h('small', { text: 'Davra valyutasi: kunlik bonus, o‘tkazmalar, tarix' })),
    h('span', { class: 'lv', text: S.nur ? fmtNur(S.nur.balance) + ' ✦' : '' }), h('span', { class: 'chev', text: '›' })));
  body.append(gap(), h('div', { class: 'lrow soon-row', 'aria-disabled': 'true' }, h('span', { class: 'grad gift', text: '🎁' }), h('span', { class: 'lt' }, h('b', { text: 'Sovg‘alar' }), h('small', { text: 'Do‘stlaringizga sovg‘a yuboring' })), h('span', { class: 'soon', text: 'Tez kunda' })), h('div', { style: 'height:16px' }));
}

// ---- Davra Premium
function pagePremium(body) {
  const isPrem = S.me.is_premium;
  const PRICE = 1000;

  body.append(h('div', { class: 'nur-hero', style: 'background:linear-gradient(160deg, rgba(169,120,255,0.2), transparent)' },
    h('div', { class: 'nur-star', text: '💎', style: 'background:linear-gradient(135deg,#a978ff,#5ea8ff)' }),
    h('h2', { style: 'margin-top:12px;' }, isPrem ? 'Siz Premimumsiz!' : 'Davra Premium'),
    h('div', { class: 'muted', text: isPrem ? 'Obunangiz faol. Barcha imkoniyatlar ochiq.' : 'Imkoniyatlaringizni oshiring' })
  ));

  body.append(gap(), gTitle('Imkoniyatlar'));
  const features = [
    ['💎', 'Maxsus Profil belgisi', 'Ismingiz yonida hashamatli olmos belgisi'],
    ['📁', 'Katta fayllar', '8 MB o\'rniga 50 MB gacha fayl yuklash'],
    ['🚀', 'Tezkor yuklash', 'Rasmlar va fayllar tezroq ishlanadi'],
    ['🎨', 'Kelajakdagi imtiyozlar', 'Barcha yangi funksiyalarga birinchilardan bo\'lib ega bo\'ling']
  ];
  for (const [i, t, s] of features) {
    body.append(h('div', { class: 'lrow static' }, h('span', { class: 'li', text: i }), h('span', { class: 'lt' }, h('b', { text: t }), h('small', { text: s }))));
  }

  body.append(gap());
  
  if (isPrem) {
    const d = new Date(S.me.premium_until * 1000);
    body.append(h('div', { class: 'pad muted', style: 'text-align:center;', text: `Amal qilish muddati: ${d.toLocaleDateString()}` }));
  } else {
    const btn = h('button', { class: 'btn primary wide', type: 'button', style: 'background:linear-gradient(90deg, #a978ff, #5ea8ff); border:none;' }, `Sotib olish — ${PRICE} ✦`);
    btn.onclick = async () => {
      if (!await confirmBox({ title: 'Premium sotib olish', text: `Hisobingizdan ${PRICE} Nur yechiladi. Davom etasizmi?`, ok: 'Sotib olish' })) return;
      btn.disabled = true;
      try {
        const r = await api('/premium/buy', { method: 'POST' });
        S.me.is_premium = true; S.me.premium_until = r.premium_until;
        if (S.nur) S.nur.balance = r.new_balance;
        toast('💎 Tabriklaymiz! Siz endi Premiumsiz!');
        renderSettings();
      } catch (e) { toast(e.message); btn.disabled = false; }
    };
    body.append(h('div', { class: 'pad' }, btn));
  }
}

// ---- bildirishnomalar
function pageNotif(body) {
  const n = S.settings.notif;
  const pushBox = h('div'); body.append(gTitle('Push bildirishnomalar (ilova yopiq bo‘lsa ham)'), pushBox);
  pushPanel(pushBox);
  body.append(gap(), gTitle('Qaysi suhbatlar'),
    switchRow('👤', 'Shaxsiy suhbatlar', null, n.direct, v => { n.direct = v; saveSettings(); }),
    switchRow('👥', 'Guruhlar', null, n.groups, v => { n.groups = v; saveSettings(); }),
    switchRow('📣', 'Kanallar', null, n.channels, v => { n.channels = v; saveSettings(); }),
    gap(), gTitle('Ko‘rinish'),
    switchRow('👁', 'Xabar matnini ko‘rsatish', 'O‘chirilsa faqat «Yangi xabar» deb chiqadi', n.preview, v => { n.preview = v; saveSettings(); }),
    switchRow('🔊', 'Ilova ichida ovoz', 'Ilova ochiq paytida yangi xabar signali', n.sound, v => { n.sound = v; saveSettings(); if (v) beep(); }),
    gap(), gTitle('Ovozsiz suhbatlar'));
  const muted = S.settings.muted;
  if (!muted.length) body.append(h('div', { class: 'pad muted', text: 'Ovozsiz qilingan suhbat yo‘q. Suhbat ichidagi ⋮ menyudan ovozsiz qilishingiz mumkin.' }));
  for (const key of muted) {
    const isRoom = key.startsWith('r:'), id = parseInt(isRoom ? key.slice(2) : key, 10);
    const name = isRoom ? (S.rooms.find(r => r.id === id) || {}).title : ((S.chats.find(c => c.id === id) || {}).peer || {}).display_name;
    body.append(h('div', { class: 'lrow' }, h('span', { class: 'li', text: '🔕' }), h('span', { class: 'lt' }, h('b', { text: name || 'Suhbat ' + key })),
      h('button', { class: 'btn small', type: 'button', onclick: () => { toggleMute(key); renderSettings(); } }, 'Yoqish')));
  }
}
async function pushPanel(box) {
  const st = await pushStatus(); box.replaceChildren();
  const info = {
    unsupported: 'Bu brauzer push’ni qo‘llab-quvvatlamaydi. iPhone/iPad’da avval Safari’da «Ulashish → Bosh ekranga qo‘shish» qiling, so‘ng ilovani shu yerdan oching.',
    denied: 'Brauzer sozlamalarida bu sayt uchun bildirishnoma bloklangan. Sayt sozlamalaridan ruxsat bering va sahifani yangilang.',
  }[st];
  if (info) { box.append(h('div', { class: 'pad muted', text: info })); return; }
  box.append(switchRow('🔔', 'Bildirishnomalarni yoqish', st === 'on' ? 'Bu qurilmada yoqilgan' : 'Ruxsat so‘raladi', st === 'on', async (v, input) => {
    input.disabled = true;
    try { if (v) { await pushEnable(); toast('Bildirishnoma yoqildi'); } else { await pushDisable(); toast('Bildirishnoma o‘chirildi'); } }
    catch (e) { toast(e.message, 4200); }
    pushPanel(box);
  }));
  if (st === 'on') box.append(h('div', { class: 'pad', style: 'padding-top:2px' }, h('button', { class: 'btn small', type: 'button', onclick: async ev => {
    ev.target.disabled = true;
    try { const r = await api('/push/test', { method: 'POST' }); toast(r.sent ? 'Sinov xabari yuborildi' : 'Yuborilmadi — qurilma obunasi eskirgan bo‘lishi mumkin'); } catch (e) { toast(e.message); }
    ev.target.disabled = false;
  } }, 'Sinov bildirishnomasi yuborish')));
}

// ---- maxfiylik
function pagePrivacy(body) {
  const me = S.me;
  const savePrivacy = async (patch, input) => {
    try { S.me = await api('/privacy', { method: 'PUT', body: Object.assign({ hide_seen: !!S.me.hide_seen, allow_requests: !!S.me.allow_requests }, patch) }); refreshMenuProfile(); }
    catch (e) { toast(e.message); if (input) input.checked = !input.checked; }
  };
  body.append(gTitle('Maxfiylik'),
    switchRow('👁', 'Oxirgi ko‘rilganimni yashirish', 'Boshqalar onlayn holatingizni ko‘rmaydi (siz ham ularnikini)', me.hide_seen, (v, i) => savePrivacy({ hide_seen: v }, i)),
    switchRow('📨', 'Notanishlardan so‘rov qabul qilish', 'O‘chirilsa yangi suhbat va guruh takliflari kelmaydi', me.allow_requests, (v, i) => savePrivacy({ allow_requests: v }, i)),
    gap(), gTitle('Parolni o‘zgartirish'));
  const old = h('input', { type: 'password', placeholder: 'Joriy parol', autocomplete: 'current-password' }), nw = h('input', { type: 'password', placeholder: 'Yangi parol (kamida 8 belgi)', autocomplete: 'new-password' });
  const form = h('form', { class: 'form', style: 'padding:6px 18px 16px' }, old, nw, h('button', { class: 'btn primary', type: 'submit' }, 'Parolni almashtirish'));
  form.addEventListener('submit', async e => {
    e.preventDefault();
    try { const r = await api('/password', { method: 'POST', body: { old_password: old.value, new_password: nw.value } }); old.value = nw.value = ''; toast('Parol o‘zgardi' + (r.closed ? '. Boshqa ' + r.closed + ' ta qurilmadan chiqarildi' : '')); }
    catch (err) { toast(err.message); }
  });
  body.append(form, gap(), gTitle('Faol seanslar'));
  const sess = h('div'), blk = h('div'); body.append(sess, gap(), gTitle('Bloklangan foydalanuvchilar'), blk);
  const loadSess = async () => {
    try {
      const list = await api('/sessions'); sess.replaceChildren();
      for (const s of list) sess.append(h('div', { class: 'sess' }, h('span', { class: 'si', text: /Android|iPhone|iPad/.test(s.device) ? '📱' : '💻' }),
        h('span', { class: 'st-copy' }, h('b', { text: s.device }), h('small', { text: (s.current ? 'Shu qurilma · ' : '') + dayLabel(s.created_at) + ', ' + fmtTime(s.created_at) })),
        s.current ? null : h('button', { class: 'btn small', type: 'button', onclick: async () => { try { await api('/sessions/' + s.id, { method: 'DELETE' }); loadSess(); } catch (e) { toast(e.message); } } }, 'Tugatish')));
      if (list.length > 1) sess.append(h('button', { class: 'lrow danger', type: 'button', onclick: async () => {
        if (!await confirmBox({ title: 'Boshqa seanslar', text: 'Shu qurilmadan boshqa hamma qurilmalardan chiqiladi.', ok: 'Tugatish', danger: true })) return;
        try { const r = await api('/sessions/terminate-others', { method: 'POST' }); toast(r.closed + ' ta seans tugatildi'); loadSess(); } catch (e) { toast(e.message); }
      } }, h('span', { class: 'li', text: '⏻' }), h('span', { class: 'lt' }, h('b', { text: 'Boshqa barcha seanslarni tugatish' }))));
    } catch (e) { sess.replaceChildren(h('div', { class: 'pad muted', text: e.message })); }
  };
  const loadBlk = async () => {
    try {
      const list = await api('/blocked'); blk.replaceChildren();
      if (!list.length) blk.append(h('div', { class: 'pad muted', text: 'Hech kim bloklanmagan.' }));
      for (const b of list) blk.append(h('div', { class: 'lrow' }, avatarEl(b.user, 38), h('span', { class: 'lt' }, h('b', { text: b.user.display_name }), h('small', { text: '@' + b.user.username })),
        h('button', { class: 'btn small', type: 'button', onclick: async () => { try { upsertChat(await api('/chats/' + b.chat_id + '/unblock', { method: 'POST' })); renderList(); loadBlk(); toast('Blokdan chiqarildi'); } catch (e) { toast(e.message); } } }, 'Blokdan chiqarish')));
    } catch (e) { blk.replaceChildren(h('div', { class: 'pad muted', text: e.message })); }
  };
  loadSess(); loadBlk();
}

// ---- suhbat sozlamalari
function pageChat(body) {
  const c = S.settings.chat;
  body.append(gTitle('Matn o‘lchami'));
  const out = h('output', { text: c.textSize + ' px' });
  const range = h('input', { type: 'range', min: 12, max: 22, step: 1, value: c.textSize, 'aria-label': 'Matn o‘lchami' });
  range.addEventListener('input', () => { c.textSize = parseInt(range.value, 10); out.textContent = c.textSize + ' px'; saveSettings(); });
  body.append(h('div', { class: 'slider-row' }, h('span', { text: 'A' }), range, out),
    h('div', { class: 'preview-chat' }, h('div', { class: 'msg in' }, h('div', { class: 'msg-text', text: 'Salom! Bugun uchrashamizmi?' }), h('span', { class: 'meta', text: '10:24' })),
      h('div', { class: 'msg out' }, h('div', { class: 'msg-text', text: 'Albatta, soat 18:00 da 👍' }), h('span', { class: 'meta' }, '10:25', h('span', { class: 'tick r', text: '✓✓' })))),
    gap(), gTitle('Suhbat foni'));
  const grid = h('div', { class: 'bg-grid' });
  const names = { pattern: 'Naqsh', plain: 'Oddiy', blue: 'Ko‘k', green: 'Yashil', violet: 'Binafsha', sunset: 'Shafaq' };
  for (const k of Object.keys(names)) {
    const sw = h('button', { type: 'button', class: 'bg-swatch' + (c.bg === k ? ' on' : ''), style: `--sw:${swatchCss(k)}`, text: names[k], onclick: () => { c.bg = k; saveSettings(); $$('.bg-swatch', grid).forEach(x => x.classList.remove('on')); sw.classList.add('on'); } });
    grid.append(sw);
  }
  body.append(grid, gTitle('Yozish'),
    switchRow('↵', 'Enter bilan yuborish', 'O‘chirilsa Enter — yangi qator, Ctrl+Enter — yuborish', c.enterSend, v => { c.enterSend = v; saveSettings(); }),
    switchRow('🕐', '12 soatlik vaqt (AM/PM)', null, c.hour12, v => { c.hour12 = v; saveSettings(); if (S.cur) renderMsgs(true); renderList(); }),
    gap(), gTitle('Mavzu'),
    switchRow('🌙', 'Tungi rejim', 'Qora mavzu', document.documentElement.dataset.theme !== 'light', v => applyTheme(v ? 'dark' : 'light')));
}
function swatchCss(k) {
  return { pattern: 'radial-gradient(var(--dots) 1.2px,transparent 1.2px) 0 0/14px 14px,var(--bg)', plain: 'var(--bg)', blue: 'var(--g-blue)', green: 'var(--g-green)', violet: 'var(--g-violet)', sunset: 'var(--g-sunset)' }[k];
}

// ---- papkalar
function pageFolders(body) {
  const folders = S.settings.folders;
  body.append(h('div', { class: 'pad muted', text: 'Papkalar suhbatlar ro‘yxati tepasida tablar sifatida ko‘rinadi.' }));
  for (const f of folders) body.append(lrow('📁', f.name, f.chats.length + ' ta suhbat', { chev: true, onclick: () => settingsPush(f.name, b => pageFolderEdit(b, f.id)) }));
  if (folders.length < 20) body.append(lrow('＋', 'Yangi papka', null, { onclick: async () => {
    const name = await askText({ title: 'Yangi papka', label: 'Papka nomi', max: 24, ok: 'Yaratish' });
    if (!name || !name.trim()) return;
    const id = Math.random().toString(36).slice(2, 10);
    folders.push({ id, name: name.trim().slice(0, 24), chats: [] }); saveSettings(); renderList();
    settingsPush(name.trim(), b => pageFolderEdit(b, id));
  } }));
}
function pageFolderEdit(body, id) {
  const f = S.settings.folders.find(x => x.id === id); if (!f) return;
  body.append(lrow('✏️', 'Nomini o‘zgartirish', f.name, { onclick: async () => {
    const name = await askText({ title: 'Papka nomi', value: f.name, max: 24 }); if (!name || !name.trim()) return;
    f.name = name.trim().slice(0, 24); ST.stack[ST.stack.length - 1].title = f.name; saveSettings(); renderList(); renderSettings();
  } }), gap(), gTitle('Papkadagi suhbatlar'));
  for (const it of allItems().sort((a, b) => itemTime(b) - itemTime(a))) {
    const key = itemKey(it), ent = it.kind === 'room' ? it.data : it.data.peer;
    const cb = h('input', { type: 'checkbox', checked: f.chats.includes(key) });
    cb.addEventListener('change', () => { if (cb.checked) f.chats.push(key); else f.chats = f.chats.filter(k => k !== key); saveSettings(); renderList(); });
    body.append(h('label', { class: 'pick-row' }, cb, avatarEl(ent), h('span', { class: 'lt' }, h('b', { text: it.kind === 'room' ? it.data.title : it.data.peer.display_name }))));
  }
  body.append(gap(), lrow('🗑', 'Papkani o‘chirish', null, { danger: true, onclick: async () => {
    if (!await confirmBox({ title: 'Papkani o‘chirish', text: '«' + f.name + '» papkasi o‘chiriladi (suhbatlar saqlanadi).', ok: 'O‘chirish', danger: true })) return;
    S.settings.folders = S.settings.folders.filter(x => x.id !== id); if (S.filter === id) S.filter = 'all'; saveSettings(); renderList(); settingsPop();
    ST.stack[ST.stack.length - 1].build = pageFolders; renderSettings();
  } }));
}

// ---- qo'shimcha
function pageAdvanced(body) {
  const a = S.settings.advanced;
  body.append(gTitle('Interfeys o‘lchami'));
  const out = h('output', { text: a.scale + '%' });
  const range = h('input', { type: 'range', min: 90, max: 130, step: 5, value: a.scale, 'aria-label': 'Interfeys o‘lchami' });
  range.addEventListener('input', () => { a.scale = parseInt(range.value, 10); out.textContent = a.scale + '%'; saveSettings(); });
  body.append(h('div', { class: 'slider-row' }, h('span', { text: '−' }), range, out),
    switchRow('🎞', 'Animatsiyalarni kamaytirish', 'Harakat va o‘tishlar o‘chiriladi', a.reduceMotion, v => { a.reduceMotion = v; saveSettings(); }),
    gap(), gTitle('Xotira'),
    lrow('🧹', 'Keshni tozalash', 'Ilova fayllari qayta yuklanadi (xabarlar saqlanib qoladi)', { onclick: async () => {
      try {
        if (window.caches) for (const k of await caches.keys()) await caches.delete(k);
        const regs = await navigator.serviceWorker.getRegistrations(); for (const r of regs) await r.unregister();
      } catch { /* */ }
      toast('Kesh tozalandi. Yangilanmoqda…'); setTimeout(() => location.reload(), 700);
    } }),
    lrow('↺', 'Ko‘rinish sozlamalarini asliga qaytarish', 'Matn o‘lchami, fon, o‘lcham', { onclick: () => {
      const d = DEFAULTS(); S.settings.chat = d.chat; S.settings.advanced = d.advanced; saveSettings(); renderSettings(); toast('Asl holatga qaytdi');
    } }));
}

// ---- til
function pageLang(body) {
  body.append(h('div', { class: 'lrow static' }, h('span', { class: 'li', text: '🇺🇿' }), h('span', { class: 'lt' }, h('b', { text: 'O‘zbekcha' })), h('span', { class: 'lv', text: '✓' })));
  for (const [f, n] of [['🇷🇺', 'Русский'], ['🇬🇧', 'English'], ['🇹🇷', 'Türkçe']])
    body.append(h('div', { class: 'lrow soon-row', 'aria-disabled': 'true' }, h('span', { class: 'li', text: f }), h('span', { class: 'lt' }, h('b', { text: n })), h('span', { class: 'soon', text: 'Tez kunda' })));
}

// ---------------------------------------------------------------- NUR (hamyon, bonus, o'tkazma)
const NURP = { items: [], more: false, loading: false, loaded: false };
const fmtNur = n => { n = Number(n) || 0; return (n < 0 ? '−' : '') + String(Math.abs(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ' '); };
const nurPageOpen = () => $('settingsDialog').open && ST.stack.length > 0 && ST.stack[ST.stack.length - 1].build === pageNur;
function openNurPage() {
  NURP.items = (S.nur && S.nur.history) || []; NURP.more = !!(S.nur && S.nur.has_more); NURP.loaded = false;
  ST.stack.push({ title: 'Nur', build: pageNur, keepScroll: true }); renderSettings(); nurReload();
}
async function nurReload() {
  const r = await loadNur();
  if (!r) return toast('Hamyonni yuklab bo‘lmadi');
  NURP.items = r.history || []; NURP.more = !!r.has_more; NURP.loaded = true;
  if (nurPageOpen()) renderSettings();
}
function onNurUpdate() { if (nurPageOpen()) nurReload(); }
async function nurMore() {
  if (NURP.loading || !NURP.items.length) return;
  NURP.loading = true;
  try {
    const r = await api('/nur/history?before=' + NURP.items[NURP.items.length - 1].id);
    NURP.items = NURP.items.concat(r.history); NURP.more = r.has_more;
    if (nurPageOpen()) renderSettings();
  } catch (e) { toast(e.message); }
  NURP.loading = false;
}
async function nurClaim(btn) {
  btn.disabled = true;
  try { const r = await api('/nur/bonus', { method: 'POST' }); toast('✦ +' + r.bonus + ' Nur: kunlik bonus'); await nurReload(); }
  catch (e) { toast(e.message); btn.disabled = false; nurReload(); }
}
function nurTitle(tx) {
  const who = tx.peer ? tx.peer.display_name : 'Foydalanuvchi';
  return { bonus: 'Kunlik bonus', grant: 'Davra sovg‘asi', sent: 'Yuborildi: ' + who, received: 'Olindi: ' + who, premium: 'Premium obuna', gift: 'Sovg\'a xaridi' }[tx.kind] || 'Amal';
}
function nurWait(ts) {
  const s = Math.max(0, Math.ceil(ts - Date.now() / 1000)), hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60);
  return hh ? hh + ' soat ' + mm + ' daqiqa' : Math.max(1, mm) + ' daqiqa';
}
function pageNur(body) {
  const n = S.nur;
  body.append(h('div', { class: 'nur-hero' }, h('div', { class: 'nur-star', text: '✦' }),
    h('div', { class: 'nur-balance', text: n ? fmtNur(n.balance) : '…' }), h('div', { class: 'muted', text: 'Nur balansingiz' })));
  if (!n) return;
  const claim = h('button', { class: 'btn primary wide', type: 'button' }, n.can_claim ? 'Kunlik bonusni olish  +' + n.next_bonus + ' ✦' : 'Bugungi bonus olingan');
  claim.disabled = !n.can_claim;
  if (n.can_claim) claim.onclick = () => nurClaim(claim);
  const hint = n.can_claim
    ? (n.streak ? '🔥 ' + n.streak + ' kun ketma-ket. Bugun olsangiz seriya davom etadi.' : 'Har kuni kirib bonus oling: ketma-ket kunlar uchun bonus oshib boradi (20 ✦ gacha).')
    : '🔥 ' + n.streak + ' kun ketma-ket · keyingi bonus (+' + n.next_bonus + ' ✦) ' + nurWait(n.next_claim_at) + 'dan keyin';
  body.append(h('div', { class: 'pad nur-claim' }, claim, h('div', { class: 'muted', text: hint })),
    gap(), lrow('💬', 'Do‘stga yuborish', 'Suhbatni oching → ⋮ → «Nur yuborish»'), gap(), gTitle('Tarix'));
  if (!NURP.items.length) body.append(h('div', { class: 'pad muted', text: NURP.loaded ? 'Hali amallar yo‘q. Birinchi kunlik bonusni oling!' : 'Yuklanmoqda…' }));
  for (const tx of NURP.items) {
    body.append(h('div', { class: 'lrow static nur-tx' },
      h('span', { class: 'li', text: { bonus: '🎁', grant: '🎉', sent: '📤', received: '📥', premium: '💎', gift: '🛍️' }[tx.kind] || '✦' }),
      h('span', { class: 'lt' }, h('b', { text: nurTitle(tx) }), h('small', { text: dayLabel(tx.created_at) + ', ' + fmtTime(tx.created_at) + (tx.note ? ' · ' + tx.note : '') })),
      h('span', { class: 'nur-amt ' + (tx.amount > 0 ? 'plus' : 'minus'), text: (tx.amount > 0 ? '+' : '−') + fmtNur(Math.abs(tx.amount)) + ' ✦' })));
  }
  if (NURP.more) body.append(lrow('⌄', 'Yana ko‘rsatish', null, { onclick: nurMore }));
  body.append(h('div', { style: 'height:16px' }));
}
function openSendNur(chat) {
  const peer = chat.peer, max = (S.nur && S.nur.max_transfer) || 5000;
  const bal = h('b', { text: S.nur ? fmtNur(S.nur.balance) + ' ✦' : '…' });
  const amount = h('input', { type: 'number', inputmode: 'numeric', min: 1, max, step: 1, placeholder: 'Miqdor', 'aria-label': 'Nur miqdori' });
  const note = h('input', { maxlength: 60, placeholder: 'Izoh (ixtiyoriy)', 'aria-label': 'Izoh' });
  const err = h('div', { class: 'err' });
  const send = h('button', { class: 'btn primary', type: 'submit' }, 'Yuborish');
  const chips = h('div', { class: 'nur-chips' }, [10, 50, 100, 500].map(v => h('button', { type: 'button', class: 'btn small', onclick: () => { amount.value = v; amount.focus(); } }, v + ' ✦')));
  const form = h('form', { class: 'pad nur-send' }, h('h2', { text: '✦ Nur yuborish' }),
    h('p', { class: 'muted' }, peer.display_name + ' ga yuboriladi. Sizda: ', bal),
    h('div', { class: 'form-stack', style: 'margin-top:12px' }, amount, chips, note, err),
    h('div', { class: 'confirm-actions' }, h('button', { class: 'btn ghost', type: 'button', onclick: () => dlg.close() }, 'Bekor qilish'), send));
  const dlg = h('dialog', { class: 'sheet narrow' }, form);
  form.addEventListener('submit', async e => {
    e.preventDefault(); err.textContent = '';
    const v = parseInt(amount.value, 10);
    if (!Number.isInteger(v) || v < 1) { err.textContent = 'Miqdorni kiriting'; return; }
    if (v > max) { err.textContent = 'Bir o‘tkazmada ko‘pi bilan ' + fmtNur(max) + ' ✦'; return; }
    if (S.nur && v > S.nur.balance) { err.textContent = 'Nur yetarli emas'; return; }
    send.disabled = true;
    try {
      const r = await api('/nur/transfer', { method: 'POST', body: { chat_id: chat.id, amount: v, note: note.value } });
      if (S.nur) S.nur.balance = r.balance;
      toast('✦ ' + fmtNur(v) + ' Nur yuborildi'); dlg.close();
    } catch (ex) { err.textContent = ex.message; send.disabled = false; }
  });
  dlg.addEventListener('close', () => dlg.remove());
  dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close(); });
  document.body.append(dlg); dlg.showModal(); amount.focus();
  loadNur().then(n => { if (n) bal.textContent = fmtNur(n.balance) + ' ✦'; });
}

// ---------------------------------------------------------------- ulash
function wirePanels() {
  const closers = { closeSettings: 'settingsDialog', closeContacts: 'contactsDialog', closeSaved: 'savedDialog', closeAbout: 'aboutDialog', closeRoomDialog: 'roomDialog', closeCreateRoom: 'createRoomDialog', closeForward: 'forwardDialog', cancelEditMessage: 'editMessageDialog', closeProfileBtn: 'profileDialog' };
  for (const [btn, dlg] of Object.entries(closers)) $(btn).onclick = () => closeDlg(dlg);
  $$('dialog.sheet').forEach(d => d.addEventListener('click', e => { if (e.target === d) d.close(); }));
  $('settingsBack').onclick = settingsPop;
  $('settingsDialog').addEventListener('close', () => { ST.stack = []; });
  const drawer = $('appMenu');
  drawer.addEventListener('click', e => { if (e.target === drawer) drawer.close(); });
  const via = (id, fn) => { $(id).onclick = () => { drawer.close(); fn(); }; };
  via('menuProfile', () => openProfile(false)); via('menuMyProfile', () => openProfile(false));
  via('menuNewGroup', () => openCreateRoom('group')); via('menuNewChannel', () => openCreateRoom('channel'));
  via('menuContacts', openContacts); via('menuSavedMessages', openSaved); via('menuSettings', openSettings);
  via('menuInviteFriends', inviteFriends); via('menuAbout', () => openDlg('aboutDialog'));
  $('menuNightMode').addEventListener('click', e => { if (e.target.closest('.switch')) return; applyTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'); });
  $('menuDarkToggle').addEventListener('change', e => applyTheme(e.target.checked ? 'dark' : 'light'));
  $('menuLogout').onclick = async () => { drawer.close(); if (await confirmBox({ title: 'Chiqish', text: 'Akkauntdan chiqmoqchimisiz?', ok: 'Chiqish', danger: true })) doLogout(); };
  $('editProfileToggle').onclick = () => setProfileEdit(!profileEdit);
  $('profileForm').addEventListener('submit', saveProfile);
  $('changeAvatar').onclick = () => $('avatarFile').click();
  $('avatarFile').onchange = e => { const f = e.target.files[0]; e.target.value = ''; changeAvatar(f); };
  $('removeAvatar').onclick = removeAvatar;
  $('copyUsername').onclick = () => copyText('@' + S.me.username);
  $('contactsSearchInput').addEventListener('input', renderContacts);
  $('savedForm').addEventListener('submit', addSaved);
  $('forwardSearch').addEventListener('input', renderForward);
  $('forwardSend').onclick = doForward;
  $('createRoomForm').addEventListener('submit', submitCreateRoom);
  $('roomBack').onclick = roomPop;
  $('roomAvatarFile').addEventListener('change', e => { const f = e.target.files[0]; e.target.value = ''; uploadRoomAvatar(f); });
}
wirePanels();