import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  root: '.',
  server: {
    host: true,
    // LAN 内の DNS(別リポジトリの lan-dns)で付けた名前(novelai.lan など)でも開けるようにする。
    // 先頭のドットは「.lan で終わる名前すべて」
    allowedHosts: ['.lan'],
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
