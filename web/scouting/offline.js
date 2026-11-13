'use strict';

const prefix = 'agropulse-scouting-';
const cacheName = prefix + '1';
const shell = '/scouting';
const assets = [
  'app.js', 'map.js', 'plans.js', 'drafts.js', 'visit-drafts.js', 'review-card.js', 'design.js',
  'styles.css', 'workspace.css', 'theme.css', 'interior.css', 'experience.css',
  'experience-interior.css', 'hightech.css', 'design-controls.css', 'review-card.css', 'today.css',
  'assets/leaflet.js', 'assets/leaflet.css', 'assets/public-context.json', 'assets/basemap.jpg',
  'assets/wide-basemap.jpg', 'assets/terrain.png', 'assets/clay.png', 'assets/ph.png'
].map(path => shell + '/' + path);

self.addEventListener('install', event => {
  event.waitUntil(caches.open(cacheName).then(cache => cache.addAll([shell, ...assets])));
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) {
      if (name.startsWith(prefix) && name !== cacheName) await caches.delete(name);
    }
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  const {request} = event;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  const page = request.mode === 'navigate' && (url.pathname === '/' || url.pathname === shell);
  if (!page && !assets.includes(url.pathname)) return;

  // Only public shell assets are cached here. Journal requests go straight to the server.
  event.respondWith((async () => {
    const cache = await caches.open(cacheName);
    const key = page ? shell : url.pathname;
    try {
      const response = await fetch(request);
      if (response.ok) await cache.put(key, response.clone()).catch(() => {});
      return response;
    } catch (error) {
      const cached = await cache.match(key);
      if (cached) return cached;
      throw error;
    }
  })());
});
