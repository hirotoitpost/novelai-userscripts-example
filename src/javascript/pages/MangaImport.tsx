import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { apiFetch } from '../api'
import './MangaImport.css'

/**
 * 作品の取り込み(構成の参考): PDF や画像を取り込み、コマごとの構成(人数・構図・セリフの量・役割・感情)を
 * 読み取る。セリフや筋書きは読まない。読み取った構成は漫画ドラフトで「コマ運びの参考」に使う。
 */

interface ImportSummary {
  id: number
  title: string
  page_count: number
  status: string
  panel_count: number
  created_at: string
}

interface PanelInfo {
  box: [number, number, number, number]
  people: number
  shot: string
  text_blocks: number
  role: string
  emotion: string
}

interface ImportDetail extends ImportSummary {
  // overlays: コマにまたがって描かれた絵(何段にもまたがって立つ人物など)の数
  analysis: { pages: { panels: PanelInfo[]; overlays?: number }[] } | null
}

interface Job {
  status: string
  message: string
  progress: number
  total: number
  detail: string | null
}

const SHOT_LABELS: Record<string, string> = {
  'close-up': '寄り', medium: '中', long: '引き', 'no humans': '人物なし',
}
const STATUS_LABELS: Record<string, string> = {
  imported: '取り込み済み', analyzing: '読み取り中', analyzed: '読み取り済み', error: 'エラー',
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

function textAmount(blocks: number): string {
  return blocks === 0 ? 'セリフなし' : blocks <= 2 ? 'セリフ少' : 'セリフ多'
}

export default function MangaImport() {
  const { token } = useAuth()
  const navigate = useNavigate()
  const [imports, setImports] = useState<ImportSummary[]>([])
  const [selected, setSelected] = useState<ImportDetail | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [title, setTitle] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [useVision, setUseVision] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadList = useCallback(async () => {
    if (!token) return
    try {
      setImports(await apiFetch<ImportSummary[]>(token, '/api/manga-import'))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [token])

  const open = useCallback(async (id: number) => {
    if (!token) return
    setSelected(await apiFetch<ImportDetail>(token, `/api/manga-import/${id}`))
    setJob(await apiFetch<Job | null>(token, `/api/manga-import/${id}/job`))
  }, [token])

  useEffect(() => { loadList() }, [loadList])

  // 読み取り中は進み具合と途中結果を数秒ごとに読み直す
  useEffect(() => {
    if (!token || !selected || selected.status !== 'analyzing') return
    const timer = setInterval(() => {
      open(selected.id).catch(() => {})
      loadList()
    }, 4000)
    return () => clearInterval(timer)
  }, [token, selected, open, loadList])

  const upload = async () => {
    if (!token || !files.length || !title.trim()) return
    setBusy(true)
    setError(null)
    try {
      const payload = await Promise.all(files.map(async f => ({ name: f.name, data: await readAsDataUrl(f) })))
      const created = await apiFetch<ImportSummary>(token, '/api/manga-import', {
        title: title.trim(), files: payload, use_vision: useVision,
      })
      setTitle('')
      setFiles([])
      await loadList()
      await open(created.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const remove = async (item: ImportSummary) => {
    if (!token || !window.confirm(`「${item.title}」と取り込んだページを削除します。よろしいですか?`)) return
    const res = await fetch(`/api/manga-import/${item.id}`, { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } })
    if (!res.ok) {
      setError(`削除できませんでした(HTTP ${res.status})`)
      return
    }
    if (selected?.id === item.id) setSelected(null)
    await loadList()
  }

  const pages = selected?.analysis?.pages ?? []

  return (
    <div className="mi-root">
      <header className="mi-header">
        <button type="button" className="mi-link" onClick={() => navigate('/')}>← ホーム</button>
        <h1>作品の取り込み(構成の参考)</h1>
        <button type="button" className="mi-link" onClick={() => navigate('/manga-draft')}>漫画ドラフトへ →</button>
      </header>

      <main className="mi-main">
        <section className="mi-section">
          <h2>取り込む</h2>
          <p className="mi-hint">
            PDF・画像・zip(複数可、選んだ順にページになります。zip の中はファイル名順)から、コマごとの構図・人数・セリフの量・役割だけを読み取ります。
            セリフや話の内容は読み取らず、新しく作る漫画にも使いません。
          </p>
          <label className="mi-field">
            <span>名前</span>
            <input value={title} onChange={e => setTitle(e.target.value)} placeholder="例: 参考にする4コマ集" />
          </label>
          <label className="mi-field">
            <span>ファイル</span>
            <input type="file" multiple accept="application/pdf,image/*,.zip,.cbz,application/zip"
              onChange={e => setFiles(Array.from(e.target.files ?? []))} />
          </label>
          {files.length > 0 && <p className="mi-hint">{files.length}ファイル: {files.map(f => f.name).join('、')}</p>}
          <label className="mi-check">
            <input type="checkbox" checked={useVision} onChange={e => setUseVision(e.target.checked)} />
            コマの役割・感情も読み取る(ローカルの画像モデル、1コマ十数秒)
          </label>
          <button type="button" className="mi-primary" onClick={upload} disabled={busy || !files.length || !title.trim()}>
            {busy ? '取り込み中…' : '取り込んで読み取る'}
          </button>
          {error && <p className="mi-error" role="alert">{error}</p>}
        </section>

        <section className="mi-section">
          <h2>取り込んだ作品</h2>
          {imports.length === 0 && <p className="mi-hint">まだありません。</p>}
          <ul className="mi-list">
            {imports.map(item => (
              <li key={item.id} className={selected?.id === item.id ? 'mi-item mi-item--on' : 'mi-item'}>
                <button type="button" className="mi-item-open" onClick={() => open(item.id)}>
                  <strong>{item.title}</strong>
                  <span>{item.page_count}ページ・{item.panel_count}コマ・{STATUS_LABELS[item.status] ?? item.status}</span>
                </button>
                <button type="button" className="mi-item-action" disabled={item.status !== 'analyzed'}
                  onClick={() => navigate(`/manga-draft?import=${item.id}`)}>
                  この構成で漫画を作る
                </button>
                <button type="button" className="mi-item-delete" onClick={() => remove(item)} title="削除">✕</button>
              </li>
            ))}
          </ul>
        </section>

        {selected && (
          <section className="mi-section">
            <h2>{selected.title}</h2>
            {job && job.status === 'running' && (
              <p className="mi-hint"><span className="mi-spinner" /> {job.message}({job.progress}/{job.total}ページ)</p>
            )}
            {job && job.status === 'error' && <p className="mi-error">読み取りに失敗しました: {job.detail}</p>}
            <div className="mi-pages">
              {Array.from({ length: selected.page_count }, (_, index) => {
                const panels = pages[index]?.panels
                return (
                  <figure className="mi-page" key={index}>
                    <div className="mi-page-image">
                      <img src={`/api/manga-import/${selected.id}/pages/${index}`} alt={`${index + 1}ページ`}
                        onLoad={e => {
                          const img = e.currentTarget
                          img.parentElement?.style.setProperty('--w', String(img.naturalWidth))
                          img.parentElement?.style.setProperty('--h', String(img.naturalHeight))
                        }} />
                      {panels?.map((p, k) => (
                        <span key={k} className="mi-box" style={{
                          left: `calc(${p.box[0]} / var(--w, 1) * 100%)`,
                          top: `calc(${p.box[1]} / var(--h, 1) * 100%)`,
                          width: `calc(${p.box[2]} / var(--w, 1) * 100%)`,
                          height: `calc(${p.box[3]} / var(--h, 1) * 100%)`,
                        }}>{k + 1}</span>
                      ))}
                    </div>
                    <figcaption>
                      <strong>{index + 1}ページ</strong>
                      {(pages[index]?.overlays ?? 0) > 0 && <span className="mi-hint">コマにまたがる絵あり</span>}
                      {panels ? (
                        <ol>
                          {panels.map((p, k) => (
                            <li key={k}>
                              {SHOT_LABELS[p.shot] ?? p.shot}・{p.people}人・{textAmount(p.text_blocks)}
                              {p.role && `・${p.role}`}{p.emotion && `(${p.emotion})`}
                            </li>
                          ))}
                        </ol>
                      ) : <span className="mi-hint">読み取り待ち</span>}
                    </figcaption>
                  </figure>
                )
              })}
            </div>
          </section>
        )}
      </main>
    </div>
  )
}
