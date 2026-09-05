import { useState, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { getRecaptchaToken } from '../recaptcha'
import './Login.css'

export default function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const [tokenInput, setTokenInput] = useState('')
  const [showTokenLogin, setShowTokenLogin] = useState(false)

  const handleTokenLogin = (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    // DevTools からのコピー時に前後の引用符・空白が混ざりやすいので取り除く
    const cleaned = tokenInput.trim().replace(/^["']|["']$/g, '')
    if (!cleaned) return
    if (cleaned.split('.').length !== 3) {
      setError('トークンの形式が正しくありません(JWT形式ではありません)。前後の引用符や空白が混ざっていないか確認してください')
      return
    }
    login(cleaned)
    navigate('/')
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      let recaptcha: string
      try {
        recaptcha = await getRecaptchaToken('login')
      } catch {
        setError('reCAPTCHA の読み込みに失敗しました。ページを再読み込みしてください')
        return
      }

      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, recaptcha }),
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

        <button
          type="button"
          className="login-token-toggle"
          onClick={() => setShowTokenLogin(v => !v)}
        >
          {showTokenLogin ? '閉じる' : 'アクセストークンで直接ログイン'}
        </button>

        {showTokenLogin && (
          <form className="login-form" onSubmit={handleTokenLogin}>
            <div className="login-field">
              <label htmlFor="token">アクセストークン</label>
              <input
                id="token"
                type="password"
                value={tokenInput}
                onChange={e => setTokenInput(e.target.value)}
                placeholder="novelai.net にログインして取得したトークン"
                autoComplete="off"
              />
            </div>
            <p className="login-note">
              メール+パスワードのログインは reCAPTCHA の制約で失敗する場合があります。
              novelai.net に直接ログインした際のアクセストークン（有効期限あり）を貼り付けてください。
            </p>
            {error && <p className="login-error">{error}</p>}
            <button type="submit" className="login-btn">
              このトークンでログイン
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
