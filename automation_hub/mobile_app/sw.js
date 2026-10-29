/* Service Worker — cache shell, background sync, offline support */
const CACHE_NAME = 'siyiji-mobile-v2';
const SHELL_URLS = [
  '/mobile/',
  '/mobile/css/app.css',
  '/mobile/js/api.js',
  '/mobile/js/stores/offline.js',
  '/mobile/js/stores/notes.js',
  '/mobile/js/stores/tasks.js',
  '/mobile/js/stores/sync.js',
  '/mobile/js/router.js',
  '/mobile/js/app.js',
  '/mobile/manifest.json',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(SHELL_URLS).catch(() => {});
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      );
    }).then(() => self.clients.claim())
  );
});

// Background sync for offline captures
self.addEventListener('sync', (event) => {
  if (event.tag === 'sync-captures') {
    event.waitUntil(replayOfflineQueue());
  }
});

async function replayOfflineQueue() {
  try {
    const clients = await self.clients.matchAll();
    for (const client of clients) {
      client.postMessage({ type: 'SYNC_OFFLINE' });
    }
  } catch {
    // Client may not be available
  }
}

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Only handle /mobile/ requests
  if (!url.pathname.startsWith('/mobile/')) return;

  // API POST requests: network only
  if (event.request.method !== 'GET') {
    event.respondWith(
      fetch(event.request).catch(() => {
        return new Response(JSON.stringify({ error: 'offline', queued: true }), {
          status: 202,
          headers: { 'Content-Type': 'application/json' },
        });
      })
    );
    return;
  }

  // API GET requests (not static): network first, cache fallback
  if (!url.pathname.startsWith('/mobile/static/')) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          // Cache successful API responses for offline fallback
          const clone = response.clone();
          caches.open(CACHE_NAME + '-api').then((cache) => {
            cache.put(event.request, clone);
          });
          return response;
        })
        .catch(() => {
          return caches.match(event.request).then((cached) => {
            if (cached) return cached;
            return new Response(JSON.stringify({ error: 'offline' }), {
              status: 503,
              headers: { 'Content-Type': 'application/json' },
            });
          });
        })
    );
    return;
  }

  // Static files: cache first, network fallback
  event.respondWith(
    caches.match(event.request).then((cached) => {
      return cached || fetch(event.request).catch(() => {
        return new Response('Offline', { status: 503 });
      });
    })
  );
});
