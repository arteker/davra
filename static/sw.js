// Davra service worker: tarmoq birinchi, oflayn bo'lsa keshdan; push bildirishnomalar.
const CACHE = 'davra-v11';
const SHELL = ['/', '/app.css', '/app.js', '/panels.js', '/manifest.json', '/icon-192.png', '/icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => Promise.allSettled(SHELL.map(u => c.add(u)))));
  self.skipWaiting();
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== location.origin || u.pathname.startsWith('/api') || u.pathname === '/ws') return;
  e.respondWith(
    fetch(e.request)
      .then(r => { if (r.ok) { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); } return r; })
      .catch(() => caches.match(e.request).then(r => r || caches.match('/')))
  );
});

self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { body: e.data ? e.data.text() : '' }; }
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    if (wins.some(w => w.visibilityState === 'visible' && w.focused) && d.tag !== 'davra-test') return;
    await self.registration.showNotification(d.title || 'Davra', {
      body: d.body || '', tag: d.tag || 'davra', renotify: true,
      icon: '/icon-192.png', badge: '/icon-192.png', data: { key: d.key },
    });
  })());
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const key = e.notification.data && e.notification.data.key;
  e.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    if (wins.length) {
      const w = wins[0]; await w.focus();
      if (key != null) w.postMessage({ type: 'open-chat', key: String(key) });
      return;
    }
    await self.clients.openWindow(key != null ? '/?chat=' + encodeURIComponent(key) : '/');
  })());
});