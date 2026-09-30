import { useCallback, useEffect, useMemo, useState } from 'react'
import { useChunkSync } from '../hooks/useChunkSync'

interface Chunk {
  id: string
  label: string
  expansion: string
  color: string | null
  is_category: boolean
  child_order: string[] | null
}

interface Props {
  /** クリックされたチャンクの展開後テキストを受け取る。 */
  onInsert: (text: string) => void
}

function ChunkLeaf({ node, onInsert }: { node: Chunk; onInsert: (text: string) => void }) {
  const empty = !node.expansion.trim()
  return (
    <button
      type="button"
      className="ig-chunk-leaf"
      onClick={() => onInsert(node.expansion.trim())}
      disabled={empty}
      title={empty ? '中身が空のチャンクです' : `クリックでプロンプトへ挿入\n\n${node.expansion}`}
    >
      <span className="ig-chunk-dot" style={{ background: node.color ?? '#888' }} />
      <span className="ig-chunk-text">
        <span className="ig-chunk-label">{node.label}</span>
        <span className="ig-chunk-expansion">{empty ? '(空)' : node.expansion}</span>
      </span>
    </button>
  )
}

function ChunkCategory({
  node, byId, onInsert,
}: { node: Chunk; byId: Map<string, Chunk>; onInsert: (text: string) => void }) {
  return (
    <details className="ig-chunk-category">
      <summary>{node.label}</summary>
      <div className="ig-chunk-children">
        {(node.child_order ?? []).map(childId => {
          const child = byId.get(childId)
          if (!child) return null
          return child.is_category
            ? <ChunkCategory key={childId} node={child} byId={byId} onInsert={onInsert} />
            : <ChunkLeaf key={childId} node={child} onInsert={onInsert} />
        })}
      </div>
    </details>
  )
}

/**
 * NovelAI 公式のプロンプトチャンクをローカルDBへ取り込むボタン。
 * 復号鍵が未設定ならメール・パスワード入力欄を出し、鍵を計算してから続けて取り込む。
 */
function OfficialImport({ onImported }: { onImported: () => Promise<unknown> }) {
  const { encryptionKey, computeKey, forgetKey, sync, loading, error } = useChunkSync()
  const [formOpen, setFormOpen] = useState(false)
  const [email,    setEmail]    = useState('')
  const [password, setPassword] = useState('')
  const [message,  setMessage]  = useState<string | null>(null)

  const runImport = async (key?: string) => {
    setMessage(null)
    const count = await sync(key)
    if (count === null) return
    await onImported()
    setMessage(`公式から ${count} 件を読み込みました`)
  }

  const submitKey = async () => {
    const key = await computeKey(email, password)
    if (!key) return
    setPassword('')
    setFormOpen(false)
    await runImport(key)
  }

  return (
    <div className="ig-chunk-import">
      <div className="ig-chunk-import-row">
        <button
          type="button"
          className="ig-preset-btn"
          onClick={() => (encryptionKey ? runImport() : setFormOpen(!formOpen))}
          disabled={loading}
          title="NovelAI に保存されているプロンプトチャンクを取得してローカルに保存します"
        >
          {loading ? '読み込み中…' : '⇩ 公式から読み込み'}
        </button>
        {encryptionKey && (
          <button
            type="button"
            className="ig-icon-btn"
            onClick={() => { forgetKey(); setMessage('保存していた復号鍵を削除しました') }}
            disabled={loading}
            title="保存している復号鍵を削除する"
          >
            鍵を削除
          </button>
        )}
      </div>

      {formOpen && !encryptionKey && (
        <form
          className="ig-chunk-key-form"
          onSubmit={e => { e.preventDefault(); submitKey() }}
        >
          <p className="ig-chunk-note">
            チャンクの復号に NovelAI のメールアドレスとパスワードを使います。
            保存されるのは計算した復号鍵だけで、パスワードは保存しません。
          </p>
          <input
            type="email"
            className="ig-input-number"
            placeholder="メールアドレス"
            autoComplete="username"
            value={email}
            onChange={e => setEmail(e.target.value)}
          />
          <input
            type="password"
            className="ig-input-number"
            placeholder="パスワード"
            autoComplete="current-password"
            value={password}
            onChange={e => setPassword(e.target.value)}
          />
          <button type="submit" className="ig-preset-btn" disabled={loading || !email || !password}>
            鍵を計算して読み込む
          </button>
        </form>
      )}

      {error   && <p className="ig-chunk-note ig-chunk-note--error">{error}</p>}
      {message && !error && <p className="ig-chunk-note">{message}</p>}
    </div>
  )
}

/** インポート済みプロンプトチャンクをツリー/検索で確認し、クリックで挿入する。 */
export default function ChunkPicker({ onInsert }: Props) {
  const [chunks,  setChunks]  = useState<Chunk[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)
  const [query,   setQuery]   = useState('')

  const loadChunks = useCallback(() => {
    return fetch('/api/chunks/imported')
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: Chunk[]) => { setChunks(data); setError(null) })
      .catch(e => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { loadChunks() }, [loadChunks])

  const byId = useMemo(() => new Map(chunks.map(c => [c.id, c])), [chunks])

  // どのカテゴリからも参照されていないものがツリーの根。
  // NovelAI の "Root Category" は中身が無いので一段飛ばして子を並べる。
  // カテゴリに属さないチャンクは末尾の「未分類」にまとめる。
  const { rootCategories, looseLeaves } = useMemo(() => {
    const referenced = new Set(chunks.flatMap(c => c.child_order ?? []))
    const roots = chunks
      .filter(c => !referenced.has(c.id))
      .flatMap(c => (c.id === 'default' ? (c.child_order ?? []).flatMap(id => byId.get(id) ?? []) : [c]))
    return {
      rootCategories: roots.filter(c => c.is_category),
      looseLeaves:    roots.filter(c => !c.is_category),
    }
  }, [chunks, byId])

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return []
    return chunks.filter(c =>
      !c.is_category && (c.label.toLowerCase().includes(q) || c.expansion.toLowerCase().includes(q)),
    )
  }, [chunks, query])

  if (loading) return <p className="ig-chunk-note">読み込み中…</p>
  if (error)   return <p className="ig-chunk-note ig-chunk-note--error">チャンクを取得できませんでした: {error}</p>

  return (
    <div className="ig-chunk-picker">
      <OfficialImport onImported={loadChunks} />
      {chunks.length === 0 ? (
        <p className="ig-chunk-note">インポート済みのチャンクがありません。「公式から読み込み」で取り込めます。</p>
      ) : (
      <>
      <input
        type="search"
        className="ig-input-number"
        placeholder="名前・中身で検索"
        aria-label="プロンプトチャンクを検索"
        value={query}
        onChange={e => setQuery(e.target.value)}
      />
      <div className="ig-chunk-list">
        {query.trim()
          ? (matches.length > 0
              ? matches.map(c => <ChunkLeaf key={c.id} node={c} onInsert={onInsert} />)
              : <p className="ig-chunk-note">該当するチャンクはありません</p>)
          : <>
              {rootCategories.map(c => <ChunkCategory key={c.id} node={c} byId={byId} onInsert={onInsert} />)}
              {looseLeaves.length > 0 && (
                <details className="ig-chunk-category">
                  <summary>未分類</summary>
                  <div className="ig-chunk-children">
                    {looseLeaves.map(c => <ChunkLeaf key={c.id} node={c} onInsert={onInsert} />)}
                  </div>
                </details>
              )}
            </>}
      </div>
      </>
      )}
    </div>
  )
}
