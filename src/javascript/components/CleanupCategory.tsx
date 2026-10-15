import { useState } from 'react'
import './CleanupCategory.css'

/**
 * ディスクの整理の1種類(使われていないコマ・合成したページの前の版 など)。件数と大きさを出し、一覧を開くと見本を見て
 * 選べる。片付けたものはごみ箱に入り、30日のあいだは元に戻せる(ディスクが空くのは、ごみ箱から消したとき)。
 */

interface FileItem {
  name: string
  path: string
  bytes: number
  modified_at: number
}

interface Props {
  backend: string
  category: string
  label: string
  files: number
  bytes: number
  /** 片付けたあと(件数を読み直す・知らせを出す) */
  onDone: (message: string) => void
}

function size(n: number): string {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(2)} GB`
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MB`
  if (n === 0) return '0 KB'
  return `${Math.max(1, Math.round(n / 1024))} KB`
}

export default function CleanupCategory({ backend, category, label, files, bytes, onDone }: Props) {
  const [list, setList] = useState<FileItem[] | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [shown, setShown] = useState(60)

  async function open() {
    if (list) {
      setList(null)
      return
    }
    setError(null)
    try {
      const res = await fetch(`${backend}/api/admin/cleanup/${category}/files`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setList((await res.json()).files)
      setPicked(new Set())
      setShown(60)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function clean(names: string[] | null) {
    const count = names ? names.length : files
    if (!window.confirm(`${label} ${count}個をごみ箱に移します(30日のあいだは元に戻せます。ディスクが空くのは、ごみ箱から消したときです)。よろしいですか?`)) return
    setBusy(true)
    setError(null)
    try {
      const res = await fetch(`${backend}/api/admin/cleanup/${category}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(names ? { names } : {}),
      })
      const body = await res.json().catch(() => null)
      if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `HTTP ${res.status}`)
      setList(null)
      onDone(`${body.deleted}個(${size(body.bytes)})をごみ箱に移しました`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const toggle = (name: string, on: boolean) => setPicked(prev => {
    const next = new Set(prev)
    if (on) next.add(name)
    else next.delete(name)
    return next
  })
  const pickedBytes = list?.filter(f => picked.has(f.name)).reduce((sum, f) => sum + f.bytes, 0) ?? 0

  return (
    <div className="cleanup-category">
      <div className="cleanup-head">
        <strong>{label}</strong>
        <span className="adm-hint">{files.toLocaleString()}個 ・ {size(bytes)}</span>
        <button type="button" disabled={files === 0 || busy} onClick={() => void open()}>{list ? '一覧を閉じる' : '一覧を見て選ぶ'}</button>
        <button type="button" className="adm-danger" disabled={files === 0 || busy} onClick={() => void clean(null)}>全部ごみ箱へ</button>
      </div>
      {error && <p className="adm-error">{error}</p>}
      {list && (
        <>
          <div className="cleanup-actions">
            <button type="button" onClick={() => setPicked(new Set(list.map(f => f.name)))}>全部選ぶ</button>
            <button type="button" onClick={() => setPicked(new Set())}>選択を外す</button>
            <button type="button" className="adm-danger" disabled={picked.size === 0 || busy} onClick={() => void clean([...picked])}>
              選んだ{picked.size}個({size(pickedBytes)})をごみ箱へ
            </button>
          </div>
          <ul className="cleanup-grid">
            {list.slice(0, shown).map(f => (
              <li key={f.name} className={picked.has(f.name) ? 'is-picked' : undefined}>
                <label>
                  <img src={`${backend}/api/library/thumb?path=${encodeURIComponent(f.path)}&w=200`} alt={f.name} loading="lazy" />
                  <span className="cleanup-name">
                    <input type="checkbox" checked={picked.has(f.name)} onChange={e => toggle(f.name, e.target.checked)} />
                    {' '}{f.name}
                  </span>
                  <span className="adm-hint">{size(f.bytes)} ・ {new Date(f.modified_at * 1000).toLocaleDateString('ja-JP')}</span>
                </label>
              </li>
            ))}
          </ul>
          {list.length > shown && (
            <button type="button" onClick={() => setShown(shown + 60)}>さらに表示({list.length - shown}個)</button>
          )}
        </>
      )}
    </div>
  )
}
