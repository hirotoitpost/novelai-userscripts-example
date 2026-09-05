import { useState } from 'react'
import { useAuth } from '../context/AuthContext'
import './Chunks.css'

interface PromptMacro {
  id: string
  containerId: string
  label: string
  expansion: string
  color: string
  isCategory: boolean
  childOrder?: string[]
}

const KEY_STORAGE = 'nai_encryption_key'

export default function Chunks() {
  const { token } = useAuth()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [encryptionKey, setEncryptionKey] = useState<string | null>(() =>
    localStorage.getItem(KEY_STORAGE)
  )

  const [macros, setMacros] = useState<PromptMacro[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const computeKey = async () => {
    setError(null)
    setLoading(true)
    try {
      const res = await fetch('/api/chunks/encryption-key', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail ?? '鍵の計算に失敗しました')
      localStorage.setItem(KEY_STORAGE, data.encryption_key)
      setEncryptionKey(data.encryption_key)
      setPassword('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  const forgetKey = () => {
    localStorage.removeItem(KEY_STORAGE)
    setEncryptionKey(null)
    setMacros(null)
  }

  const fetchMacros = async () => {
    if (!token || !encryptionKey) return
    setError(null)
    setLoading(true)
    try {
      const res = await fetch(
        `/api/chunks/promptmacros?encryption_key=${encodeURIComponent(encryptionKey)}`,
        { headers: { Authorization: `Bearer ${token}` } }
      )
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail ?? 'チャンクの取得に失敗しました')
      setMacros(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="chunks-root">
      <div className="chunks-inner">
      <h1>プロンプトチャンク</h1>
      <p className="chunks-intro">
        NovelAI 公式の「プロンプトチャンク」を読み取り専用で同期します。
        <code>/user/keystore</code> は Persistent API Token を受け付けないため、
        ログイン画面で novelai.net から取得したセッショントークン（有効期限あり）でログインしている必要があります。
      </p>

      <section className="chunks-section">
        <h2>1. 復号鍵</h2>
        {encryptionKey ? (
          <div className="chunks-key-set">
            <span>✓ 設定済み</span>
            <button type="button" onClick={forgetKey}>削除</button>
          </div>
        ) : (
          <div className="chunks-key-form">
            <p>
              メール・パスワードから復号鍵をこのブラウザ内で計算します。ネットワークには送信されず、
              パスワード自体も保存されません。
            </p>
            <input
              type="email"
              placeholder="メールアドレス"
              value={email}
              onChange={e => setEmail(e.target.value)}
            />
            <input
              type="password"
              placeholder="パスワード"
              value={password}
              onChange={e => setPassword(e.target.value)}
            />
            <button type="button" onClick={computeKey} disabled={loading || !email || !password}>
              鍵を計算して保存
            </button>
          </div>
        )}
      </section>

      <section className="chunks-section">
        <h2>2. チャンク取得</h2>
        <button type="button" onClick={fetchMacros} disabled={loading || !encryptionKey}>
          {loading ? '取得中...' : 'チャンクを取得'}
        </button>
      </section>

      {error && <p className="chunks-error">{error}</p>}

      {macros && (
        <ul className="chunks-list">
          {macros.map(m => (
            <li key={m.id} className={m.isCategory ? 'chunks-item--category' : 'chunks-item'}>
              <span className="chunks-item-dot" style={{ background: m.color }} />
              <span className="chunks-item-label">{m.isCategory ? '📁' : '🏷️'} {m.label}</span>
              {!m.isCategory && <div className="chunks-item-expansion">{m.expansion}</div>}
            </li>
          ))}
        </ul>
      )}
      </div>
    </div>
  )
}
