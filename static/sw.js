/* Service worker de Inventario Pollos Lucho.
   Estrategia: si hay internet, SIEMPRE se sirve la última versión de los
   archivos (red primero). La caché solo se usa como respaldo sin conexión.
   Los datos (/api/) NUNCA se cachean, siempre van al servidor. */
const CACHE = 'pollos-lucho-v3';

const PRECACHE = [
    '/static/style.css',
    '/static/app.js',
    '/static/manifest.json',
    '/static/favicon.ico',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
    '/static/icons/apple-touch-icon.png',
    '/static/logos/logo_p22_61.png',
    '/static/fonts/jost-400.woff2',
    '/static/fonts/jost-600.woff2',
    '/static/fonts/jost-700.woff2',
    '/static/fonts/passionone-400.woff2',
    '/static/offline.html'
];

self.addEventListener('install', (e) => {
    e.waitUntil(
        caches.open(CACHE).then((cache) =>
            Promise.all(PRECACHE.map((u) => cache.add(u).catch(() => null)))
        ).then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (e) => {
    e.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
        ).then(() => self.clients.claim())
    );
});

async function networkFirst(req) {
    const cache = await caches.open(CACHE);
    try {
        const res = await fetch(req);
        if (res && res.ok) cache.put(req, res.clone());
        return res;
    } catch (_) {
        const cached = await cache.match(req);
        if (cached) return cached;
        throw _;
    }
}

self.addEventListener('fetch', (e) => {
    const req = e.request;
    if (req.method !== 'GET') return;

    const url = new URL(req.url);
    if (url.origin !== self.location.origin) return;

    /* Datos y sesión: siempre en vivo contra el servidor. */
    if (url.pathname.startsWith('/api/')) return;

    /* Navegación (páginas): red primero, aviso offline si no hay conexión. */
    if (req.mode === 'navigate') {
        e.respondWith(
            fetch(req).catch(() =>
                caches.match('/static/offline.html', { ignoreSearch: true })
            )
        );
        return;
    }

    /* Archivos estáticos: red primero, caché como respaldo offline. */
    if (url.pathname.startsWith('/static/')) {
        e.respondWith(networkFirst(req));
    }
});