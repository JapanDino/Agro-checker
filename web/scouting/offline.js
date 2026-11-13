'use strict';

const prefix = 'agropulse-scouting-';
const cacheName = prefix + '3';
const shell = '/scouting';
const pages = [shell, '/scouting/inspections', '/workstation/'];
const assets = [
  'app.js', 'map.js', 'plans.js', 'drafts.js', 'visit-drafts.js', 'review-card.js', 'design.js',
  'pulse.js', 'showcase.js', 'today.js', 'pulse.css', 'pulse-screens.css',
  'assets/fonts/manrope-cyrillic-wght-normal.woff2', 'assets/fonts/manrope-latin-wght-normal.woff2',
  'styles.css', 'workspace.css', 'theme.css', 'interior.css', 'experience.css',
  'experience-interior.css', 'hightech.css', 'design-controls.css', 'review-card.css', 'today.css',
  'assets/leaflet.js', 'assets/leaflet.css', 'assets/public-context.json', 'assets/basemap.jpg',
  'assets/wide-basemap.jpg', 'assets/terrain.png', 'assets/clay.png', 'assets/ph.png'
].map(path => shell + '/' + path).concat([
  '/workstation/workstation.js', '/workstation/workstation.css', '/workstation/farm-basemap.jpg'
]);

self.addEventListener('install', event => {
  event.waitUntil(caches.open(cacheName).then(cache => cache.addAll([...pages, ...assets])));
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
  const page = request.mode === 'navigate' && (url.pathname === '/' || pages.includes(url.pathname));
  if (!page && !assets.includes(url.pathname)) return;

  // Only public shell assets are cached here. Journal requests go straight to the server.
  event.respondWith((async () => {
    const cache = await caches.open(cacheName);
    const key = url.pathname === '/' ? shell : url.pathname;
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
