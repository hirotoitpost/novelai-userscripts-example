import { useCallback, useState } from 'react'
import { SESSION_TOKEN, useAuth } from '../context/AuthContext'

const KEY_STORAGE = 'nai_encryption_key'

/**
 * NovelAI 公式のプロンプトチャンクをローカルDBへ取り込む(同期する)ための状態と操作。
 * single: 復号鍵はメール+パスワードからバックエンドで計算し、鍵だけを localStorage に保存する。
 * multi: 鍵はログインのときにサーバーが作ってセッションに持つ(ブラウザには置かない。keyInSession が true)。
 */
export function useChunkSync() {
  const { token, mode, hasEncryptionKey } = useAuth()
  const keyInSession = mode === 'multi'
  const [storedKey, setEncryptionKey] = useState<string | null>(() =>
    keyInSession ? null : localStorage.getItem(KEY_STORAGE)
  )
  const encryptionKey = keyInSession ? (hasEncryptionKey ? SESSION_TOKEN : null) : storedKey
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)

  /** 鍵を計算して保存する。成功したら鍵を、失敗したら null を返す。 */
  const computeKey = useCallback(async (email: string, password: string): Promise<string | null> => {
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
      return data.encryption_key as string
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return null
    } finally {
      setLoading(false)
    }
  }, [])

  const forgetKey = useCallback(() => {
    localStorage.removeItem(KEY_STORAGE)
    setEncryptionKey(null)
  }, [])

  /**
   * 公式から取得してローカルDBへ upsert する。取り込んだ件数を返し、失敗時は null。
   * 鍵を計算した直後は state がまだ古いので、その鍵を key で直接渡せる。
   */
  const sync = useCallback(async (key: string | null = encryptionKey): Promise<number | null> => {
    if (!token) {
      setError('ログインしていないため読み込めません')
      return null
    }
    if (!key) {
      if (keyInSession) setError('復号の鍵がありません。ログインし直してください')
      return null
    }
    setError(null)
    setLoading(true)
    try {
      // 鍵は URL に入れない(アクセスログなどに残るため)。multi ではサーバーがセッションの鍵を使う
      const res = await fetch('/api/chunks/promptmacros', {
        headers: { Authorization: `Bearer ${token}`, ...(keyInSession ? {} : { 'X-NAI-Encryption-Key': key }) },
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail ?? 'チャンクの取得に失敗しました')
      return Array.isArray(data) ? data.length : 0
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return null
    } finally {
      setLoading(false)
    }
  }, [token, encryptionKey, keyInSession])

  return { encryptionKey, keyInSession, computeKey, forgetKey, sync, loading, error, setError }
}
