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
