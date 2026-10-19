/// <reference types="vite/client" />
/**
 * バックエンドの場所(画面から API を呼ぶ先)。
 *
 * - 開発中(npm run dev。Vite の開発サーバー :5173 で配る): バックエンドは同じホストの :8000
 * - ビルドした画面をバックエンドが配るとき(Docker。SERVE_FRONTEND=1): 画面と同じ場所(ポートを変えて公開しても届く)
 */
export const BACKEND_ORIGIN: string = import.meta.env.DEV
  ? `${window.location.protocol}//${window.location.hostname}:8000`
  : window.location.origin

/**
 * 複数ユーザー(APP_MODE=multi)では、ログインのセッションを Cookie で渡す。画面と同じ配信元への fetch には
 * ブラウザが自動で付けるが、開発中に :8000 へ直接送る fetch(Vite の中継だと SSE が詰まるため。Story.tsx を参照)は
 * 別の配信元なので、credentials: 'include' を付けないと Cookie が送られない。101 か所の呼び出しを書き換える
 * 代わりに、multi のときだけ(AuthContext が決める)ここで付ける。single では何もしない。
 * バックエンド側では、その配信元(http://localhost:5173 など)を .env の APP_ALLOWED_ORIGINS に書いておく。
 */
let sendSessionCookie = false

export function setSessionCookieForBackend(on: boolean): void {
  sendSessionCookie = on
}

if (BACKEND_ORIGIN !== window.location.origin) {
  const originalFetch = window.fetch.bind(window)
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    if (sendSessionCookie && init?.credentials === undefined) {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (url.startsWith(`${BACKEND_ORIGIN}/`)) return originalFetch(input, { ...init, credentials: 'include' })
    }
    return originalFetch(input, init)
  }
}
