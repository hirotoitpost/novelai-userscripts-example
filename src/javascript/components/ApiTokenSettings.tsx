import { FormEvent, useState } from 'react'
import { useAuth } from '../context/AuthContext'

/**
 * 複数ユーザー(APP_MODE=multi)で、NovelAI の永続 API トークン(pst-...)を登録・削除する。
 * トークンはサーバーが暗号化して持ち、画面には戻さない。登録すると、ログインのトークンの期限が切れても続けられる。
 * single では出さない(single の永続トークンは .env の NOVELAI_API_TOKEN)。
 */
export default function ApiTokenSettings() {
  const { mode, hasApiToken, refreshSession } = useAuth()
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  if (mode !== 'multi') return null

  const send = async (method: 'PUT' | 'DELETE', done: string) => {
    setBusy(true)
    setError(null)
    setMessage(null)
    try {
      const res = await fetch('/api/auth/api-token', {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: method === 'PUT' ? JSON.stringify({ token: value.trim() }) : undefined,
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail ?? `HTTP ${res.status}`)
      }
      setValue('')
      setMessage(done)
      await refreshSession()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (value.trim()) void send('PUT', '登録しました')
  }

  return (
    <form className="home-api-token" onSubmit={submit}>
      <h2>NovelAI の永続 API トークン</h2>
      <p>
        {hasApiToken
          ? '✓ 登録済み。ログインの期限が切れても、このトークンで続けて使えます(keystore を読む機能を除く)。'
          : '登録すると、ログインの期限(30 日)が切れても続けて使えます。NovelAI のアカウント設定の「Get Persistent API Token」で作れます。'}
      </p>
      <div className="home-api-token-row">
        <input
          type="password"
          value={value}
          onChange={e => setValue(e.target.value)}
          placeholder={hasApiToken ? '新しいトークンで置き換える(pst-...)' : 'pst-...'}
          autoComplete="off"
          disabled={busy}
        />
        <button type="submit" className="home-logout-btn" disabled={busy || !value.trim()}>
          {hasApiToken ? '置き換える' : '登録'}
        </button>
        {hasApiToken && (
          <button type="button" className="home-logout-btn" disabled={busy} onClick={() => void send('DELETE', '削除しました')}>
            削除
          </button>
        )}
      </div>
      {message && !error && <p>{message}</p>}
      {error && <p className="home-api-token-error">{error}</p>}
    </form>
  )
}
