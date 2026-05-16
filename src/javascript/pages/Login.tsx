import { useState, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import './Login.css'

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.detail ?? 'ログインに失敗しました')
        return
      }
      login(data.access_token)
      navigate('/')
    } catch {
      setError('サーバーに接続できませんでした')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-root">
      <div className="login-bg" />
      <div className="login-card">
        <div className="login-logo">
          <span className="login-logo-text">NovelAI</span>
          <span className="login-logo-sub">Image Generation</span>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          <div className="login-field">
            <label htmlFor="email">メールアドレス</label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              placeholder="example@email.com"
              autoComplete="email"
              required
              disabled={loading}
            />
          </div>

          <div className="login-field">
            <label htmlFor="password">パスワード</label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder="••••••••"
              autoComplete="current-password"
              required
              disabled={loading}
            />
          </div>

          {error && <p className="login-error">{error}</p>}

          <button type="submit" className="login-btn" disabled={loading}>
            {loading ? (
              <span className="login-spinner" />
            ) : (
              'ログイン'
            )}
          </button>
        </form>

        <p className="login-note">
          API トークンは NovelAI アカウント設定の「Get Persistent API Token」から取得できます。
        </p>
      </div>
    </div>
  )
}
