/**
 * 処理の終了をブラウザ通知で知らせる。
 *
 * - ページが動いている間は showNotification() でページから出す。
 * - スマホは画面を消すとページが止まるので、サーバーからの Web Push も購読しておき、
 *   ブラウザを閉じていても Service Worker(public/sw.js)が通知を出せるようにする。
 *
 * どちらも安全なコンテキスト(HTTPS か localhost)でしか使えない。スマホから LAN の IP で
 * 開くときの設定は docs/notifications_jp.md を参照。
 */

export type NotifySupport = 'granted' | 'default' | 'denied' | 'unsupported'

// Story ページと同じく、Vite のプロキシを通さずバックエンドへ直接アクセスする
const API_ORIGIN = `${window.location.protocol}//${window.location.hostname}:8000`
// バックエンドが応答しないときに、ボタンが押せないまま固まらないようにする
const TIMEOUT_MS = 10000

function withTimeout<T>(promise: Promise<T>, what: string): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_, reject) => setTimeout(() => reject(new Error(`${what}が${TIMEOUT_MS / 1000}秒たっても終わりません`)), TIMEOUT_MS)),
  ])
}

export function notifySupport(): NotifySupport {
  if (!window.isSecureContext || !('Notification' in window) || !('serviceWorker' in navigator)) {
    return 'unsupported'
  }
  return Notification.permission
}

// 「プッシュ通知をやめる」を選んだら、ページを開き直しても勝手に購読し直さない
const PUSH_OPT_OUT_KEY = 'nai_push_opt_out'

function pushOptedOut(): boolean {
  try {
    return localStorage.getItem(PUSH_OPT_OUT_KEY) === '1'
  } catch {
    return false
  }
}

function setPushOptOut(value: boolean) {
  try {
    if (value) localStorage.setItem(PUSH_OPT_OUT_KEY, '1')
    else localStorage.removeItem(PUSH_OPT_OUT_KEY)
  } catch {
    // 保存できなくても動作には影響しない
  }
}

let registration: Promise<ServiceWorkerRegistration | null> | null = null

function serviceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!registration) {
    registration = 'serviceWorker' in navigator && window.isSecureContext
      ? navigator.serviceWorker.register('/sw.js').then(() => navigator.serviceWorker.ready).catch(() => null)
      : Promise.resolve(null)
  }
  return registration
}

function base64UrlToBytes(value: string): Uint8Array<ArrayBuffer> {
  const base64 = (value + '='.repeat((4 - (value.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/')
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return bytes
}

/**
 * Web Push を購読してサーバーに登録する。サーバーの鍵が変わっていたら購読し直す。
 * 通知が許可済みのときだけ動く。auto(ページ読み込み時)は、やめると選んだ人には何もしない。
 */
export async function syncPushSubscription(auto = false): Promise<boolean> {
  try {
    await subscribePush(auto)
    return true
  } catch {
    return false
  }
}

/**
 * syncPushSubscription の本体。失敗したら理由を Error で投げる(画面に出すため)。
 * 通知が未許可、またはやめると選んだ人の自動実行のときは何もしない。
 */
export async function subscribePush(auto = false): Promise<void> {
  if (notifySupport() !== 'granted') throw new Error('通知が許可されていません')
  if (auto && pushOptedOut()) return
  if (!auto) setPushOptOut(false)
  const sw = await withTimeout(serviceWorker(), 'Service Worker の準備')
  if (!sw || !('pushManager' in sw)) throw new Error('このブラウザはプッシュ通知に対応していません')
  const res = await fetch(`${API_ORIGIN}/api/push/public-key`, { signal: AbortSignal.timeout(TIMEOUT_MS) })
    .catch(() => { throw new Error(`バックエンド(${API_ORIGIN})に接続できません`) })
  if (!res.ok) throw new Error(`バックエンドが通知に未対応です(HTTP ${res.status})。バックエンドを再起動してください`)
  const { public_key: publicKey } = await res.json()
  const serverKey = base64UrlToBytes(publicKey)

  let subscription = await sw.pushManager.getSubscription()
  const currentKey = subscription?.options.applicationServerKey
  if (subscription && currentKey && !sameBytes(new Uint8Array(currentKey), serverKey)) {
    await subscription.unsubscribe()
    subscription = null
  }
  subscription ??= await withTimeout(
    sw.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: serverKey }),
    'ブラウザのプッシュ登録',
  )

  const saved = await fetch(`${API_ORIGIN}/api/push/subscribe`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(subscription.toJSON()),
    signal: AbortSignal.timeout(TIMEOUT_MS),
  })
  if (!saved.ok) throw new Error(`購読をサーバーに保存できませんでした(HTTP ${saved.status})`)
}

function sameBytes(a: Uint8Array, b: Uint8Array): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i])
}

/** 通知の許可を求め、プッシュ通知も購読する。ボタン操作の中から呼ぶこと。 */
export async function enableNotifications(): Promise<NotifySupport> {
  if (notifySupport() === 'unsupported') return 'unsupported'
  const permission = await Notification.requestPermission()
  if (permission === 'granted') await syncPushSubscription()
  return permission
}

/** プッシュ通知の購読をやめる(ページからの通知は、ブラウザの許可を外さない限り出る)。 */
export async function disablePush(): Promise<void> {
  setPushOptOut(true)
  const sw = await serviceWorker()
  const subscription = await sw?.pushManager.getSubscription()
  if (!subscription) return
  await fetch(`${API_ORIGIN}/api/push/unsubscribe`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ endpoint: subscription.endpoint }),
  }).catch(() => {})
  await subscription.unsubscribe()
}

export async function isPushSubscribed(): Promise<boolean> {
  if (notifySupport() !== 'granted') return false
  const sw = await serviceWorker()
  return !!(await sw?.pushManager.getSubscription())
}

export async function sendTestPush(): Promise<number> {
  const res = await fetch(`${API_ORIGIN}/api/push/test`, { method: 'POST', signal: AbortSignal.timeout(30000) })
  if (!res.ok) throw new Error(await res.text())
  return (await res.json()).sent
}

/**
 * ページから通知を出す。tag が同じ通知は1件にまとまるので、サーバーからのプッシュと
 * 同じ tag(nai-job-<物語ID>)を付ければ二重には出ない。
 */
export async function showNotification(title: string, body: string, tag = 'nai-task', url = location.pathname + location.search): Promise<void> {
  if ('vibrate' in navigator) navigator.vibrate?.([200, 100, 200])
  if (notifySupport() !== 'granted') return

  const sw = await serviceWorker()
  await sw?.showNotification(title, { body, tag, data: { url } }).catch(() => {})
}
