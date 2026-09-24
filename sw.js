const CACHE = 'mlsi-field-v13';
const ASSETS = ['/', '/index.html', '/manifest.webmanifest', '/icon.svg'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;
  if (event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request).catch(() => caches.match('/index.html')));
  } else if (ASSETS.includes(url.pathname)) {
    event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request)));
  }
});
self.addEventListener('sync', event => {
  if (event.tag === 'mlsi-sync') event.waitUntil(sendPending());
});
async function sendPending() {
  const sessionResponse = await fetch('/api/session', {credentials:'same-origin'});
  if (!sessionResponse.ok) return;
  const {user} = await sessionResponse.json();
  if (!user) return;
  const db = await new Promise((resolve, reject) => {
    const request = indexedDB.open('mlsi-field', 1);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  try {
    const events = await new Promise((resolve, reject) => {
      const request = db.transaction('events','readonly').objectStore('events').getAll();
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    for (const item of events.filter(item => item.userId === user.id)) {
      const response = await fetch(`/api/studies/${item.studyId}/${item.kind === 'refusal' ? 'refusals' : 'responses'}`, {
        method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, body:JSON.stringify(item.body)
      });
      if (!response.ok) {
        if (response.status >= 500) throw new Error('Сервер недоступен');
        return;
      }
      await new Promise((resolve, reject) => {
        const tx = db.transaction('events','readwrite');
        tx.objectStore('events').delete(item.id);
        tx.oncomplete = resolve;
        tx.onerror = () => reject(tx.error);
      });
    }
    const clients = await self.clients.matchAll({type:'window'});
    clients.forEach(client => client.postMessage('mlsi-synced'));
  } finally { db.close(); }
}
