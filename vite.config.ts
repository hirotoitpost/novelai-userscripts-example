import { existsSync, readFileSync } from 'node:fs'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

// 開発中の既定は http。環境変数 NAI_HTTPS=1(dev-ctl.ps1 -Https が付ける)なら、LAN 用の証明書
// (scripts/make_lan_cert.py で作る)を使って https で開く。スマホへのインストール(PWA)と通知(Service Worker)は
// https でしか使えないため。バックエンド(:8000)も同じ方式にそろえること。
const certDir = 'data/certs'
const https = process.env.NAI_HTTPS === '1' && existsSync(`${certDir}/server.crt`) && existsSync(`${certDir}/server.key`)
  ? { cert: readFileSync(`${certDir}/server.crt`), key: readFileSync(`${certDir}/server.key`) }
  : undefined

// 開発サーバーの自動更新(HMR: コードを書き換えると、開いているページがその場で更新される仕組み)。既定はオフ。
// オンだと、ページと開発サーバーの接続(WebSocket)が切れて戻ったときに、Vite がページを読み込み直す。スマホでは
// ファイル選択の画面を開く・別のアプリに切り替えるだけで接続が切れ、戻った瞬間に再読み込みされて、選んだファイルや
// 書きかけの入力が消える(実機の報告と、接続を閉じるテストで確認)。オフの間は、コードを変えたら手で読み込み直す。
// フロントのコードを続けて書き換えるときは、環境変数 NAI_HMR=1 でオンにできる。
const hmr = process.env.NAI_HMR === '1'

// 接続の受け口(WebSocket)を止めている間、WebSocket の接続要求を受けたらすぐ閉じる。Vite は受け口を止めても
// 接続要求を受け取ったまま何もしないので、その接続が相手から切られる(RST)と、だれも拾わないエラーになって
// 開発サーバーごと落ちる(実機で、開発サーバーが何度も止まった。接続要求を送って切るテストで再現)。
// 接続要求は、ページを開くたびに Vite のクライアントが送ってくる(受け口が無くても一度は試す)。
const closeUpgrades: Plugin = {
  name: 'nai-close-upgrades',
  configureServer(server) {
    server.httpServer?.on('upgrade', (_req, socket) => {
      socket.on('error', () => {})
      socket.destroy()
    })
  },
}

export default defineConfig({
  plugins: [react(), ...(hmr ? [] : [closeUpgrades])],
  root: '.',
  server: {
    host: true,
    https,
    hmr,
    // HMR をオフにするだけでは、ページは開発サーバーへ接続し続け、切れて戻ると読み込み直す(Vite 6.4 で確認)。
    // 接続の受け口(WebSocket)ごと止めると、ページは最初から接続できず、読み込み直しも起きない
    ...(hmr ? {} : { ws: false as const }),
    // LAN 内の DNS(別リポジトリの lan-dns)で付けた名前(novelai.lan など)でも開けるようにする。
    // 先頭のドットは「.lan で終わる名前すべて」
    allowedHosts: ['.lan'],
    proxy: {
      '/api': {
        target: https ? 'https://127.0.0.1:8000' : 'http://127.0.0.1:8000',
        changeOrigin: true,
        // 自前の認証局で発行した証明書なので、中継では検証しない(同じ PC の中だけの通信)
        secure: false,
        // 元の接続元をバックエンドに伝える(X-Forwarded-For)。開発管理者の API は、これが LAN の外なら断る
        xfwd: true,
      },
    },
  },
})
