import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { apiFetch } from '../api'
import { useLocalStorage } from '../hooks/useLocalStorage'
import { TaskStatusDialog, useTaskStatus } from '../components/TaskStatus'
import type { Character } from '../components/StoryCharacters'
import './MangaImport.css'

/**
 * 作品の取り込み: PDF や画像を取り込み、コマごとの構成(人数・構図・セリフの量・役割・感情)を読み取る。
 * 取り込むときに使い方を選ぶ:
 *  - similar(似た漫画を作る): コマ運び・場面・所作・セリフの型まで読み、新しい話の漫画にする。セリフの文面は残さない
 *  - rebuild(自分の作品を作り直す): セリフの文面も読み、セリフごと台本に起こして描き直す
 */

type Purpose = 'similar' | 'rebuild'

interface ImportSummary {
  id: number
  title: string
  page_count: number
  status: string
  // structure は、使い方を選べるようになる前の取り込み(構成だけ)
  purpose: Purpose | 'structure'
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
  // 場面・所作のタグとセリフ(使い方を選んで取り込んだとき)。セリフの文面(text)は rebuild のときだけ
  detailed?: boolean
  scene_tags?: string[]
  action_tags?: string[]
  lines?: { chars: number; ending: string; polite: boolean; text?: string }[]
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
  // 似た漫画を作ったときの物語
  story_id?: number | null
}

const SHOT_LABELS: Record<string, string> = {
  'close-up': '寄り', medium: '中', long: '引き', 'no humans': '人物なし',
}
const PURPOSE_LABELS: Record<string, string> = { similar: '似た漫画用', rebuild: '作り直し用', structure: '構成のみ' }
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
  // 似た漫画を作る(取り込んだページの舞台・見た目と、コマ割りを使って新しい話を漫画にする)
  const [autoManga, setAutoManga] = useLocalStorage('nai_import_auto_manga', false)
  // 取り込みの使い方
  const [purpose, setPurpose] = useLocalStorage<Purpose>('nai_import_purpose', 'similar')
  // 作り直すとき、台本(物語)を作るところまでにする(セリフや割り振りを直してから絵を生成できる)
  const [scriptOnly, setScriptOnly] = useLocalStorage('nai_import_script_only', false)
  // 手直し中のセリフ(キーは ページ-コマ)
  const [lineDrafts, setLineDrafts] = useState<Record<string, string>>({})
  const [autoPages, setAutoPages] = useLocalStorage('nai_import_auto_pages', 4)
  const [autoCast, setAutoCast] = useState<number[]>([])
  const [characters, setCharacters] = useState<Character[]>([])
  const [made, setMade] = useState<{ storyId: number; message: string } | null>(null)
  const task = useTaskStatus()

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
  useEffect(() => {
    if (!token) return
    apiFetch<Character[]>(token, '/api/story/characters').then(setCharacters).catch(() => {})
  }, [token])

  const makeOptions = (rebuild: boolean) => ({
    character_ids: autoCast, max_pages: autoPages, make_images: !(rebuild && scriptOnly),
  })

  /**
   * 似た漫画を作るジョブの進み具合を、処理状況のダイアログに出す。ページを離れても読み続ける
   * (画面の後片付けで止めない)ので、ほかのページからもトレイで見られる。
   */
  const followSimilar = async (id: number, rebuild = false) => {
    if (!token) return
    task.start(rebuild ? '漫画を作り直す' : '似た漫画を作る')
    setMade(null)
    while (true) {
      await new Promise(r => setTimeout(r, 3000))
      const current = await apiFetch<Job | null>(token, `/api/manga-import/${id}/job`).catch(() => undefined)
      if (current === undefined) continue
      if (!current) {
        task.finish('error', '処理の状況が見つかりません(サーバーを再起動した可能性があります)')
        return
      }
      task.update(current.message, current.progress, current.total)
      if (current.status === 'running') continue
      if (current.status === 'done' && current.story_id) {
        task.finish('done', current.message)
        setMade({ storyId: current.story_id, message: current.message })
      } else {
        task.finish('error', current.detail ?? current.message)
      }
      void loadList()
      return
    }
  }

  const makeSimilar = async (item: ImportSummary, rebuild = false) => {
    if (!token) return
    setError(null)
    try {
      await apiFetch(token, `/api/manga-import/${item.id}/${rebuild ? 'rebuild' : 'similar'}`, makeOptions(rebuild))
      void followSimilar(item.id, rebuild)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

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
        title: title.trim(), files: payload, use_vision: useVision, purpose,
        auto_manga: autoManga ? makeOptions(purpose === 'rebuild') : null,
      })
      setTitle('')
      setFiles([])
      await loadList()
      await open(created.id)
      // 読み取りが終わると、同じ処理のまま漫画を作る
      if (autoManga) void followSimilar(created.id, purpose === 'rebuild')
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

  /** 読み取り直す(使い方を変えられる)。読み取り直すと、手直ししたセリフは消える。 */
  const reread = async (next: Purpose) => {
    if (!token || !selected) return
    const note = next === 'rebuild'
      ? 'セリフの文面も読み取ります(自分の作品を作り直すため)。'
      : 'セリフの文面は残さず、場面・所作・セリフの型だけを読み取ります。'
    if (!window.confirm(`「${selected.title}」を読み取り直します。${note}手直ししたセリフは消えます。よろしいですか?`)) return
    setError(null)
    try {
      const res = await fetch(`/api/manga-import/${selected.id}/analyze?use_vision=${useVision}&purpose=${next}`, {
        method: 'POST', headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`)
      setLineDrafts({})
      await loadList()
      await open(selected.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  /** 読み取ったセリフの手直しを保存する(1行に1つのセリフ)。 */
  const saveLines = async (pageIndex: number, panelIndex: number) => {
    if (!token || !selected) return
    const key = `${pageIndex}-${panelIndex}`
    const draft = lineDrafts[key]
    if (draft === undefined) return
    setError(null)
    try {
      const res = await fetch(`/api/manga-import/${selected.id}/pages/${pageIndex}/panels/${panelIndex}/lines`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ lines: draft.split('\n').map(l => l.trim()).filter(Boolean).slice(0, 8) }),
      })
      if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`)
      setSelected(await res.json())
      setLineDrafts(({ [key]: _done, ...rest }) => rest)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const pages = selected?.analysis?.pages ?? []

  return (
    <div className="mi-root">
      <header className="mi-header">
        <button type="button" className="mi-link" onClick={() => navigate('/')}>← ホーム</button>
        <h1>作品の取り込み</h1>
        <button type="button" className="mi-link" onClick={() => navigate('/manga-draft')}>漫画ドラフトへ →</button>
      </header>

      <main className="mi-main">
        <section className="mi-section">
          <h2>取り込む</h2>
          <p className="mi-hint">
            PDF・画像・zip(複数可、選んだ順にページになります。zip の中はファイル名順)から、コマ割りと、コマごとの構図・人数・場面・所作・セリフを読み取ります。
            何のために取り込むかを選んでください。
          </p>
          <div className="mi-purpose" role="radiogroup" aria-label="取り込みの使い方">
            <label className={purpose === 'similar' ? 'mi-purpose-item mi-purpose-item--on' : 'mi-purpose-item'}>
              <input type="radio" name="mi-purpose" checked={purpose === 'similar'} onChange={() => setPurpose('similar')} />
              <strong>似た漫画を作る</strong>
              <span>コマ運び・場面・所作を再現し、話とセリフは新しく作ります。セリフは数・長さ・調子だけを読み、文面は保存しません。
                セリフのない漫画も、絵から読み取った場面と所作で雰囲気を再現します。</span>
            </label>
            <label className={purpose === 'rebuild' ? 'mi-purpose-item mi-purpose-item--on' : 'mi-purpose-item'}>
              <input type="radio" name="mi-purpose" checked={purpose === 'rebuild'} onChange={() => setPurpose('rebuild')} />
              <strong>自分の作品を作り直す</strong>
              <span>セリフの文面も読み取り、セリフごと台本に起こして描き直します。読み取ったセリフは画面で直せます。
                自分の作品(権利のある作品)に使ってください。</span>
            </label>
          </div>
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
          <label className="mi-check">
            <input type="checkbox" checked={autoManga} onChange={e => setAutoManga(e.target.checked)} />
            読み取ったら続けて{purpose === 'rebuild' ? '作り直す' : '似た漫画を作る'}
          </label>
          <div className="mi-similar">
            <p className="mi-hint">
              {purpose === 'rebuild'
                ? '作り直し: 読み取ったセリフをそのまま使い、話し手とコマの人物を文章モデルで決めて、同じコマ割りで描き直します。'
                : '似た漫画: 取り込んだページの絵から舞台・人物の見た目・コマごとの場面と所作を読み取り、新しい話を作って、同じコマ割り(1ページ=1話)で漫画にします。作品のキャラそのものやセリフ、性的な要素は写しません。'}
              {' '}NovelAI の文章・画像生成を使います。
            </p>
            {purpose === 'rebuild' && (
              <label className="mi-check">
                <input type="checkbox" checked={scriptOnly} onChange={e => setScriptOnly(e.target.checked)} />
                台本(物語)を作るところまでにする(コマの絵は、物語のページで確かめてから生成)
              </label>
            )}
            <label className="mi-field mi-field--inline">
              <span>冒頭</span>
              <input type="number" min={1} max={10} value={autoPages}
                onChange={e => setAutoPages(Math.min(10, Math.max(1, Number(e.target.value) || 1)))} />
              <span>ページまで</span>
            </label>
            <div className="mi-field">
              <span>使うキャラ(選ばなければ、取り込んだ絵の人物から新しく作ります。参照画像も自動)</span>
              <div className="mi-chips">
                {characters.map(c => (
                  <button
                    type="button"
                    key={c.id}
                    className={autoCast.includes(c.id) ? 'mi-chip mi-chip--on' : 'mi-chip'}
                    onClick={() => setAutoCast(autoCast.includes(c.id)
                      ? autoCast.filter(i => i !== c.id)
                      : autoCast.length < 4 ? [...autoCast, c.id] : autoCast)}
                  >
                    {c.name}
                  </button>
                ))}
              </div>
            </div>
          </div>
          <button type="button" className="mi-primary" onClick={upload} disabled={busy || !files.length || !title.trim()}>
            {busy ? '取り込み中…' : '取り込んで読み取る'}
          </button>
          {error && <p className="mi-error" role="alert">{error}</p>}
          {made && (
            <p className="mi-made">
              {made.message}
              <button type="button" className="mi-item-action" onClick={() => navigate(`/bookshelf?book=${made.storyId}`)}>本棚で読む</button>
              <button type="button" className="mi-item-action" onClick={() => navigate(`/story?story=${made.storyId}`)}>物語で手直しする</button>
            </p>
          )}
        </section>

        <section className="mi-section">
          <h2>取り込んだ作品</h2>
          {imports.length === 0 && <p className="mi-hint">まだありません。</p>}
          <ul className="mi-list">
            {imports.map(item => (
              <li key={item.id} className={selected?.id === item.id ? 'mi-item mi-item--on' : 'mi-item'}>
                <button type="button" className="mi-item-open" onClick={() => open(item.id)}>
                  <strong>{item.title}</strong>
                  <span>{item.page_count}ページ・{item.panel_count}コマ・{PURPOSE_LABELS[item.purpose] ?? item.purpose}・{STATUS_LABELS[item.status] ?? item.status}</span>
                </button>
                <button type="button" className="mi-item-action" disabled={item.status !== 'analyzed'}
                  onClick={() => navigate(`/manga-draft?import=${item.id}`)}>
                  この構成で漫画を作る
                </button>
                <button type="button" className="mi-item-action" disabled={item.status !== 'analyzed' || task.busy}
                  onClick={() => void makeSimilar(item)}
                  title="舞台・見た目・コマ割り・場面と所作を写して、新しい話の漫画を自動で作ります(上のページ数・キャラの設定を使います)">
                  似た漫画を作る
                </button>
                {item.purpose === 'rebuild' && (
                  <button type="button" className="mi-item-action" disabled={item.status !== 'analyzed' || task.busy}
                    onClick={() => void makeSimilar(item, true)}
                    title="読み取ったセリフをそのまま使って、同じコマ割りで描き直します(上のページ数・キャラの設定を使います)">
                    作り直す
                  </button>
                )}
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
            <p className="mi-hint">
              {selected.purpose === 'rebuild' && 'セリフの文面を読み取ってあります。間違いはコマごとの欄で直せます(1行に1つのセリフ)。'}
              {selected.purpose === 'similar' && 'セリフは数・長さ・調子だけを読み取ってあります(文面は保存していません)。'}
              {selected.purpose === 'structure' && '構成だけを読み取ってあります(場面・所作・セリフは読んでいません)。'}
              {' '}
              <button type="button" className="mi-item-action" disabled={selected.status === 'analyzing'} onClick={() => void reread('similar')}>似た漫画用に読み取り直す</button>
              <button type="button" className="mi-item-action" disabled={selected.status === 'analyzing'} onClick={() => void reread('rebuild')}>作り直し用に読み取り直す(セリフも読む)</button>
            </p>
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
                          {panels.map((p, k) => {
                            const key = `${index}-${k}`
                            const text = (p.lines ?? []).map(l => l.text ?? '').join('\n')
                            const draft = lineDrafts[key]
                            return (
                              <li key={k}>
                                {SHOT_LABELS[p.shot] ?? p.shot}・{p.people}人・
                                {p.detailed ? ((p.lines?.length ?? 0) === 0 ? 'セリフなし' : `セリフ${p.lines?.length}個`) : textAmount(p.text_blocks)}
                                {p.role && `・${p.role}`}{p.emotion && `(${p.emotion})`}
                                {(p.scene_tags?.length ?? 0) > 0 && <div className="mi-tags">場面: {p.scene_tags?.join(', ')}</div>}
                                {(p.action_tags?.length ?? 0) > 0 && <div className="mi-tags">所作: {p.action_tags?.join(', ')}</div>}
                                {selected.purpose === 'similar' && (p.lines?.length ?? 0) > 0 && (
                                  <div className="mi-tags">
                                    セリフの型: {p.lines?.map(l => `${l.chars}字${l.ending !== 'ふつう' ? `・${l.ending}` : ''}${l.polite ? '・丁寧' : ''}`).join(' / ')}
                                  </div>
                                )}
                                {selected.purpose === 'rebuild' && p.detailed && (
                                  <div className="mi-lines">
                                    <textarea rows={Math.max(2, (draft ?? text).split('\n').length)} value={draft ?? text}
                                      placeholder="(セリフなし。1行に1つのセリフ)"
                                      onChange={e => setLineDrafts({ ...lineDrafts, [key]: e.target.value })} />
                                    {draft !== undefined && draft !== text && (
                                      <button type="button" className="mi-item-action" onClick={() => void saveLines(index, k)}>セリフを保存</button>
                                    )}
                                  </div>
                                )}
                              </li>
                            )
                          })}
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

      <TaskStatusDialog
        status={task.status}
        minimized={task.minimized}
        onMinimize={task.setMinimized}
        onClose={task.dismiss}
      />
    </div>
  )
}
