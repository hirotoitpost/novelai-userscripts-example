import { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import { AUTH_EXPIRED_EVENT } from '../api'

interface AuthContextValue {
  token: string | null
  isAuthenticated: boolean
  login: (token: string) => void
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

/** ログイン画面に出す「ログアウトした理由」(期限切れなど)。表示したら消す */
export const AUTH_MESSAGE_KEY = 'nai_auth_message'
const EXPIRED_MESSAGE = 'ログインの有効期限が切れました(NovelAI がトークンを受け付けません)。もう一度ログインしてください。'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(
    () => localStorage.getItem('nai_token')
  )

  const login = (newToken: string) => {
    localStorage.setItem('nai_token', newToken)
    setToken(newToken)
  }

  const logout = () => {
    localStorage.removeItem('nai_token')
    setToken(null)
  }

  // ログインのトークンは期限が切れても画面に残り、どの機能も NovelAI に拒否され続けていた。
  // 拒否されたら(各画面の API 呼び出しから知らせが来たら)ログアウトして、ログインし直してもらう
  useEffect(() => {
    const onExpired = () => {
      if (!localStorage.getItem('nai_token')) return
      sessionStorage.setItem(AUTH_MESSAGE_KEY, EXPIRED_MESSAGE)
      localStorage.removeItem('nai_token')
      setToken(null)
    }
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired)
  }, [])

  // 開いたとき(ログイン済みなら)トークンがまだ使えるかを確かめる。401 のときだけ期限切れとみなす
  // (Cloudflare に遮られるなど、ほかの理由で確かめられないときはそのまま使う)
  useEffect(() => {
    if (!token) return
    const controller = new AbortController()
    fetch('/api/user/subscription', { headers: { Authorization: `Bearer ${token}` }, signal: controller.signal })
      .then(res => { if (res.status === 401) window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT)) })
      .catch(() => {})
    return () => controller.abort()
  }, [token])

  return (
    <AuthContext.Provider value={{ token, isAuthenticated: !!token, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
