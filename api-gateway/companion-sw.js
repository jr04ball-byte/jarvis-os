const CACHE="jarvis-companion-v2";
self.addEventListener("install",event=>{self.skipWaiting();event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(["/companion"]))) });
self.addEventListener("activate",event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key!==CACHE).map(key=>caches.delete(key))))));
self.addEventListener("fetch",event=>{if(event.request.method!=="GET"||event.request.url.includes("/v1/"))return;event.respondWith(fetch(event.request).catch(()=>caches.match(event.request)))});
