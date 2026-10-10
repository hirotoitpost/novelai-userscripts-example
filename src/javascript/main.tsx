import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { syncPushSubscription } from './notify'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// ホーム画面へのインストール(PWA)と通知のために、Service Worker を最初に登録する(https のときだけ使える)
if ('serviceWorker' in navigator && window.isSecureContext) {
  void navigator.serviceWorker.register('/sw.js').catch(() => {/* 登録できなくても画面は使える */})
}

// 通知を許可済みなら、プッシュ通知の購読をサーバーと合わせておく(サーバーの鍵が変わった場合など)
void syncPushSubscription(true)
