import { createContext, useCallback, useContext, useEffect, useState, ReactNode } from 'react'
import { AUTH_EXPIRED_EVENT } from '../api'
import { setSessionCookieForBackend } from '../backendOrigin'

export type AppMode = 'single' | 'multi'

interface AuthContextValue {
  /**
   * API に付ける Authorization のトークン。single: NovelAI のアクセストークン(画面が持つ)。
   * multi: 秘密ではない目印(SESSION_TOKEN)。本物のトークンはサーバーが持ち、ブラウザから来た Authorization は
   * サーバーが捨てて本人のものに差し替える。各画面の「トークンがあれば使える」という判断をそのまま使うため。
   */
  token: string | null
  isAuthenticated: boolean
  mode: AppMode
  /** multi: NovelAI の永続トークンを登録しているか */
  hasApiToken: boolean
  /** multi: keystore の復号鍵をセッションに持っているか(公式のチャンク・物語の一覧に要る) */
  hasEncryptionKey: boolean
  login: (token: string) => Promise<void>
  logout: () => void
  /** multi: サーバーのセッションの状態を読み直す(永続トークンを登録した後など) */
  refreshSession: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

/** ログイン画面に出す「ログアウトした理由」(期限切れなど)。表示したら消す */
export const AUTH_MESSAGE_KEY = 'nai_auth_message'
const EXPIRED_MESSAGE = 'ログインの有効期限が切れました(NovelAI がトークンを受け付けません)。もう一度ログインしてください。'

/** multi で token に入れる目印(秘密ではない。サーバーは Authorization を使わない) */
export const SESSION_TOKEN = 'session'

const TOKEN_STORAGE = 'nai_token'
// single のときに画面が持っていた keystore の復号鍵(hooks/useChunkSync.ts・pages/Story.tsx)。multi では持たない
const ENCRYPTION_KEY_STORAGE = 'nai_encryption_key'

interface SessionInfo {
  mode: AppMode
  authenticated: boolean
  has_api_token: boolean
  has_encryption_key: boolean
}

async function fetchSession(signal?: AbortSignal): Promise<SessionInfo> {
  const res = await fetch('/api/auth/session', { signal })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as Promise<SessionInfo>
}

export function AuthProvider({ children }: { children: ReactNode }) {
  // 運営形態はサーバーに聞くまで分からない。聞けないとき(バックエンドが止まっている など)は single として動く
  const [session, setSession] = useState<SessionInfo | null>(null)
  const [token, setToken] = useState<string | null>(
    () => localStorage.getItem(TOKEN_STORAGE)
  )
  const mode: AppMode = session?.mode ?? 'single'
  const multiAuthenticated = mode === 'multi' && !!session?.authenticated

  useEffect(() => {
    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), 5000)
    fetchSession(controller.signal)
      .then(info => {
        if (info.mode === 'multi') {
          // multi では NovelAI のトークンも復号鍵もブラウザに置かない(single のときの残りも消す)
          localStorage.removeItem(TOKEN_STORAGE)
          localStorage.removeItem(ENCRYPTION_KEY_STORAGE)
          setToken(null)
          setSessionCookieForBackend(true)
        }
        setSession(info)
      })
      .catch(() => setSession({ mode: 'single', authenticated: false, has_api_token: false, has_encryption_key: false }))
      .finally(() => window.clearTimeout(timer))
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [])

  const refreshSession = useCallback(async () => {
    try {
      setSession(await fetchSession())
    } catch { /* 読めなければ今の状態のまま */ }
  }, [])

  const login = async (newToken: string) => {
    if (mode === 'multi') {
      // トークンはサーバーが持つ(Cookie で渡された)。状態を読み直す
      await refreshSession()
      return
    }
    localStorage.setItem(TOKEN_STORAGE, newToken)
    setToken(newToken)
  }

  const logout = () => {
    if (mode === 'multi') {
      setSession(s => s && { ...s, authenticated: false, has_api_token: false, has_encryption_key: false })
      void fetch('/api/auth/logout', { method: 'POST' }).catch(() => {})
      return
    }
    localStorage.removeItem(TOKEN_STORAGE)
    setToken(null)
  }

  // ログインのトークンは期限が切れても画面に残り、どの機能も NovelAI に拒否され続けていた。
  // 拒否されたら(各画面の API 呼び出しから知らせが来たら)ログアウトして、ログインし直してもらう
  useEffect(() => {
    const onExpired = () => {
      if (mode === 'multi') {
        if (!multiAuthenticated) return
        sessionStorage.setItem(AUTH_MESSAGE_KEY, EXPIRED_MESSAGE)
        logout()
        return
      }
      if (!localStorage.getItem(TOKEN_STORAGE)) return
      sessionStorage.setItem(AUTH_MESSAGE_KEY, EXPIRED_MESSAGE)
      localStorage.removeItem(TOKEN_STORAGE)
      setToken(null)
    }
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired)
  })

  const effectiveToken = mode === 'multi' ? (multiAuthenticated ? SESSION_TOKEN : null) : token
  const ready = session !== null

  // 開いたとき(ログイン済みなら)トークンがまだ使えるかを確かめる。401 のときだけ期限切れとみなす
  // (Cloudflare に遮られるなど、ほかの理由で確かめられないときはそのまま使う)
  useEffect(() => {
    if (!ready || !effectiveToken) return
    const controller = new AbortController()
    fetch('/api/user/subscription', { headers: { Authorization: `Bearer ${effectiveToken}` }, signal: controller.signal })
      .then(res => { if (res.status === 401) window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT)) })
      .catch(() => {})
    return () => controller.abort()
  }, [ready, effectiveToken])

  // サーバーに運営形態を聞くまでは何も出さない(multi なのにログイン画面へ飛ばす、などを避ける)
  if (!ready) return null

  return (
    <AuthContext.Provider
      value={{
        token: effectiveToken,
        isAuthenticated: !!effectiveToken,
        mode,
        hasApiToken: mode === 'multi' && !!session?.has_api_token,
        hasEncryptionKey: mode === 'multi' && !!session?.has_encryption_key,
        login,
        logout,
        refreshSession,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
