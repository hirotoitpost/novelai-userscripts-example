import { existsSync, readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 開発中の既定は http。環境変数 NAI_HTTPS=1(dev-ctl.ps1 -Https が付ける)なら、LAN 用の証明書
// (scripts/make_lan_cert.py で作る)を使って https で開く。スマホへのインストール(PWA)と通知(Service Worker)は
// https でしか使えないため。バックエンド(:8000)も同じ方式にそろえること。
const certDir = 'data/certs'
const https = process.env.NAI_HTTPS === '1' && existsSync(`${certDir}/server.crt`) && existsSync(`${certDir}/server.key`)
  ? { cert: readFileSync(`${certDir}/server.crt`), key: readFileSync(`${certDir}/server.key`) }
  : undefined

export default defineConfig({
  plugins: [react()],
  root: '.',
  server: {
    host: true,
    https,
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
