import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { syncPushSubscription } from './notify'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// 通知を許可済みなら、プッシュ通知の購読をサーバーと合わせておく(サーバーの鍵が変わった場合など)
void syncPushSubscription(true)
