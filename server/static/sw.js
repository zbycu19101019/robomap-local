const CACHE='robomap-v10.7-disabled';
self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('robomap-')).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
