const CACHE_NAME = 'trysearch-shell-v10';
const APP_SHELL = [
  '/',
  '/offline.html',
  '/styles.css',
  '/analytics.css',
  '/prompt_intelligence.css',
  '/visibility_tracking.css',
  '/content_studio.css',
  '/workspace.css',
  '/script.js',
  '/analytics.js',
  '/prompt_intelligence.js',
  '/visibility_tracking.js',
  '/content_studio.js',
  '/workspace.js',
  '/three-home.js',
  '/pwa.js',
  '/manifest.webmanifest',
  '/trysearch-logo.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((key) => key.startsWith('trysearch-') && key !== CACHE_NAME).map((key) => caches.delete(key))
  )).then(() => self.clients.claim()));
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;

  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).then((response) => {
      const copy = response.clone();
      caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
      return response;
    }).catch(() => caches.match(request).then((cached) => cached || caches.match('/offline.html'))));
    return;
  }

  event.respondWith(caches.match(request).then((cached) => cached || fetch(request).then((response) => {
    if (response.ok) {
      const copy = response.clone();
      caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
    }
    return response;
  })));
});
