// アプリ(PWA)の Service Worker。
// - ページからの通知: Android の Chrome は new Notification() を禁止しているため、ここ経由で出す。
// - サーバーからの Web Push: ページが止まっていても(スマホの画面を消していても)ここで受けて出す。
// - ホーム画面から開いたときに PC のサーバーにつながらなければ、「つながりません」のページを出す。
//   ほかの読み込みはキャッシュせずにそのまま通す(生成した画像や物語は常にサーバーの最新を見る)。

const OFFLINE_CACHE = 'nai-offline-v1'
const OFFLINE_FILES = ['/offline.html', '/icons/icon-192.png']

self.addEventListener('install', event => {
  event.waitUntil(caches.open(OFFLINE_CACHE).then(cache => cache.addAll(OFFLINE_FILES)).then(() => self.skipWaiting()))
})

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== OFFLINE_CACHE).map(key => caches.delete(key))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', event => {
  // ページを開くときだけ。つながらなければ「つながりません」のページ
  if (event.request.mode !== 'navigate') return
  event.respondWith(fetch(event.request).catch(() => caches.match('/offline.html')))
})

self.addEventListener('push', event => {
  let data = {}
  try {
    data = event.data ? event.data.json() : {}
  } catch {
    data = { body: event.data ? event.data.text() : '' }
  }
  event.waitUntil(
    self.registration.showNotification(data.title || '処理が終わりました', {
      body: data.body || '',
      tag: data.tag || 'nai-task',
      data: { url: data.url || '/story' },
    }),
  )
})

// 通知をタップしたら、開いているタブを前面に出して該当ページへ(無ければ開く)
self.addEventListener('notificationclick', event => {
  event.notification.close()
  const url = new URL((event.notification.data && event.notification.data.url) || '/story', self.location.origin).href
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(async windows => {
      // 同じページを開いているタブは、処理中かもしれないので読み込み直さずに前面へ出すだけ
      const path = new URL(url).pathname
      const same = windows.find(w => w.url === url) || windows.find(w => new URL(w.url).pathname === path)
      if (same) return same.focus()
      const any = windows[0]
      if (any) {
        await any.focus()
        return any.navigate(url).catch(() => self.clients.openWindow(url))
      }
      return self.clients.openWindow(url)
    }),
  )
})
