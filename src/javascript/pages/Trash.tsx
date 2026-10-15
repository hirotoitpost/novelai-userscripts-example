import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { API_ORIGIN, formatDate, getJson } from '../library'
import './Publications.css'

/**
 * ごみ箱: ギャラリー・本棚・キャラで消したもの。期限(30日)までは元に戻せる。期限を過ぎたものは自動で片付く。
 * 見た目は出版物のページと同じ(Publications.css)。
 */

interface Entry {
  id: number
  kind: string
  kind_label: string
  label: string
  created_at: string
  expires_at: string
  files: number
  bytes: number
  /** 見本に出す画像(預かったファイルの番号)。無ければ null */
  preview: number | null
}

function bytes(n: number): string {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(2)} GB`
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${Math.max(1, Math.round(n / 1024))} KB`
}

function daysLeft(iso: string): number {
  return Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000))
}

async function send(method: string, path: string): Promise<void> {
  const res = await fetch(`${API_ORIGIN}${path}`, { method })
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`)
}

export default function Trash() {
  const navigate = useNavigate()
  const [entries, setEntries] = useState<Entry[]>([])
  const [retention, setRetention] = useState(30)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState<number | 'all' | null>(null)

  const load = useCallback(async () => {
    try {
      const data = await getJson<{ retention_days: number; entries: Entry[] }>('/api/trash')
      setEntries(data.entries)
      setRetention(data.retention_days)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  async function run(id: number | 'all', work: () => Promise<string>) {
    setBusy(id)
    setError(null)
    setNotice(null)
    try {
      setNotice(await work())
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const restore = (entry: Entry) => run(entry.id, async () => {
    await send('POST', `/api/trash/${entry.id}/restore`)
    return `「${entry.label}」を元に戻しました。`
  })

  const purge = (entry: Entry) => {
    if (!window.confirm(`「${entry.label}」を完全に消します。元に戻せません。よろしいですか?`)) return
    void run(entry.id, async () => {
      await send('DELETE', `/api/trash/${entry.id}`)
      return `「${entry.label}」を完全に消しました。`
    })
  }

  const emptyAll = () => {
    if (!window.confirm(`ごみ箱の ${entries.length} 件をすべて完全に消します。元に戻せません。よろしいですか?`)) return
    void run('all', async () => {
      await send('DELETE', '/api/trash')
      return 'ごみ箱を空にしました。'
    })
  }

  const total = entries.reduce((sum, e) => sum + e.bytes, 0)
  return (
    <div className="pub-root">
      <header className="pub-header">
        <button type="button" className="pub-link" onClick={() => navigate('/')}>← ホーム</button>
        <h1>ごみ箱</h1>
        <span className="pub-count">{entries.length} 件 ・ {bytes(total)}</span>
        <button type="button" className="pub-link" onClick={() => navigate('/gallery')}>ギャラリーへ</button>
        <button type="button" className="pub-link" onClick={() => navigate('/bookshelf')}>本棚へ</button>
      </header>

      <main className="pub-main">
        <p className="pub-hint">
          ギャラリー・本棚・キャラで消したものは、ここに {retention} 日のあいだ残り、元に戻せます。期限を過ぎると自動で片付きます。
          同じ名前のキャラや、同じシーンの新しいコマなどが後からできていると、戻せないことがあります。
        </p>
        {error && <p className="pub-error" role="alert">{error}</p>}
        {notice && <p className="pub-hint" role="status">{notice}</p>}
        {!loading && entries.length === 0 && !error && <p className="pub-hint">ごみ箱は空です。</p>}
        {entries.length > 0 && (
          <div className="pub-actions">
            <button type="button" className="pub-danger" disabled={busy !== null} onClick={emptyAll}>ごみ箱を空にする</button>
          </div>
        )}
        <ul className="pub-list">
          {entries.map(entry => (
            <li key={entry.id} className="pub-item">
              {entry.preview !== null
                ? <img className="pub-cover" src={`${API_ORIGIN}/api/trash/${entry.id}/files/${entry.preview}`} alt="" loading="lazy"
                    onError={e => { e.currentTarget.style.visibility = 'hidden' }} />
                : <span className="pub-cover" aria-hidden />}
              <div className="pub-body">
                <strong>{entry.label || '(名前なし)'}</strong>
                <span className="pub-meta">{entry.kind_label} ・ {entry.files} ファイル ・ {bytes(entry.bytes)}</span>
                <span className="pub-meta">{formatDate(entry.created_at)} に消した ・ あと {daysLeft(entry.expires_at)} 日で片付きます</span>
              </div>
              <div className="pub-actions">
                <button type="button" className="pub-primary" disabled={busy !== null} onClick={() => void restore(entry)}>元に戻す</button>
                <button type="button" className="pub-danger" disabled={busy !== null} onClick={() => purge(entry)}>完全に消す</button>
              </div>
            </li>
          ))}
        </ul>
      </main>
    </div>
  )
}
