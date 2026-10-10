import { useCallback, useState } from 'react'
import { useAuth } from '../context/AuthContext'

const KEY_STORAGE = 'nai_encryption_key'

/**
 * NovelAI 公式のプロンプトチャンクをローカルDBへ取り込む(同期する)ための状態と操作。
 * 復号鍵はメール+パスワードからバックエンドで計算し、鍵だけを localStorage に保存する。
 */
export function useChunkSync() {
  const { token } = useAuth()
  const [encryptionKey, setEncryptionKey] = useState<string | null>(() =>
    localStorage.getItem(KEY_STORAGE)
  )
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
    if (!key) return null
    setError(null)
    setLoading(true)
    try {
      const res = await fetch(
        `/api/chunks/promptmacros?encryption_key=${encodeURIComponent(key)}`,
        { headers: { Authorization: `Bearer ${token}` } }
      )
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail ?? 'チャンクの取得に失敗しました')
      return Array.isArray(data) ? data.length : 0
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return null
    } finally {
      setLoading(false)
    }
  }, [token, encryptionKey])

  return { encryptionKey, computeKey, forgetKey, sync, loading, error, setError }
}
