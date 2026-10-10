import { KeyboardEvent, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import './Writer.css'

interface TextModel {
  id: string
  label: string
  note: string
  default: boolean
}

interface WriterSettings {
  model: string | null
  max_tokens: number
  temperature: number
  top_p: number
}

interface DraftSummary {
  id: number
  title: string
  story_id: number | null
  length: number
  preview: string
  updated_at: string
}

interface Draft {
  id: number
  title: string
  memory: string
  author_note: string
  text: string
  settings: WriterSettings
  story_id: number | null
  /** シリーズの次の巻として書いている下書きなら、そのシリーズと巻番号 */
  series_id: number | null
  volume_no: number | null
}

// Story ページと同じ理由(Vite の dev プロキシは SSE をバッファしてしまう)で、
// バックエンド(ポート8000)へ直接つなぐ。
const API_ORIGIN = `${window.location.protocol}//${window.location.hostname}:8000`
const AUTOSAVE_MS = 1200

async function readErrorDetail(res: Response): Promise<string> {
  const text = await res.text()
  try {
    return JSON.parse(text).detail ?? text
  } catch {
    return text || res.statusText
  }
}

/**
 * NovelAI の文章生成モデルと対話しながら物語を書くエディタ。
 * 本文の末尾から続きを生成して書き足し、自由に書き直せる。書き上げたら「漫画にする」で
 * 物語ページ(シーン分割 → 漫画v2)へ送る。
 */
export default function Writer() {
  const navigate = useNavigate()
  const { token } = useAuth()
  const [models, setModels] = useState<TextModel[]>([])
  const [drafts, setDrafts] = useState<DraftSummary[]>([])
  const [draft, setDraft] = useState<Draft | null>(null)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState('')
  // 生成前の本文(元に戻す用)。最後の生成の直前の本文は「やり直し」にも使う。
  const [undoStack, setUndoStack] = useState<string[]>([])
  const [showList, setShowList] = useState(false)

  const abortRef = useRef<AbortController | null>(null)
  const textRef = useRef<HTMLTextAreaElement>(null)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const dirty = useRef(false)

  const authHeaders: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {}

  function loadDrafts() {
    fetch(`${API_ORIGIN}/api/writer/drafts`).then(r => r.json()).then(setDrafts).catch(() => {})
  }

  useEffect(() => {
    fetch(`${API_ORIGIN}/api/writer/models`).then(r => r.json()).then(setModels).catch(() => {})
    fetch(`${API_ORIGIN}/api/writer/drafts`)
      .then(r => r.json())
      .then((list: DraftSummary[]) => {
        setDrafts(list)
        // 本棚の「続きの巻を書く」などから ?draft=ID で来たら、その下書きを開く
        const requested = Number(new URLSearchParams(window.location.search).get('draft'))
        if (requested) void openDraft(requested)
        else if (list.length > 0) void openDraft(list[0].id)
      })
      .catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 入力が止まってから自動保存する
  useEffect(() => {
    if (!draft || !dirty.current) return
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => void save(draft), AUTOSAVE_MS)
    return () => { if (saveTimer.current) clearTimeout(saveTimer.current) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft])

  async function save(current: Draft) {
    dirty.current = false
    const res = await fetch(`${API_ORIGIN}/api/writer/drafts/${current.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: current.title,
        memory: current.memory,
        author_note: current.author_note,
        text: current.text,
        settings: current.settings,
      }),
    })
    if (res.ok) {
      setStatus('保存しました')
      loadDrafts()
    }
  }

  function edit(patch: Partial<Draft>) {
    if (!draft) return
    dirty.current = true
    setDraft({ ...draft, ...patch })
  }

  async function openDraft(id: number) {
    if (draft && dirty.current) await save(draft)
    const res = await fetch(`${API_ORIGIN}/api/writer/drafts/${id}`)
    if (!res.ok) return
    setDraft(await res.json())
    setUndoStack([])
    setShowList(false)
    setError(null)
  }

  async function newDraft() {
    if (draft && dirty.current) await save(draft)
    const res = await fetch(`${API_ORIGIN}/api/writer/drafts`, { method: 'POST' })
    if (!res.ok) return
    const created: Draft = await res.json()
    const defaultModel = models.find(m => m.default)?.id ?? null
    setDraft({ ...created, settings: { ...created.settings, model: created.settings.model ?? defaultModel } })
    setUndoStack([])
    setShowList(false)
    loadDrafts()
  }

  /** 別名で保存: 今の内容を保存してから複製し、複製の方を開く(元の下書きは加筆前のまま残る) */
  async function saveAs() {
    if (!draft) return
    const title = window.prompt('別名で保存します。新しい下書きの名前:', `${draft.title || '(無題)'} (続き)`)
    if (title === null) return
    if (dirty.current) await save(draft)
    const res = await fetch(`${API_ORIGIN}/api/writer/drafts/${draft.id}/duplicate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    })
    if (!res.ok) {
      setError(await readErrorDetail(res))
      return
    }
    const copy: Draft = await res.json()
    setDraft(copy)
    setUndoStack([])
    setStatus(`「${copy.title}」として保存しました(元の下書きはそのまま残っています)`)
    loadDrafts()
  }

  async function removeDraft(id: number) {
    if (!window.confirm('この下書きを削除しますか?')) return
    await fetch(`${API_ORIGIN}/api/writer/drafts/${id}`, { method: 'DELETE' })
    if (draft?.id === id) setDraft(null)
    loadDrafts()
  }

  /** base の末尾から続きを生成して書き足す。 */
  async function generateFrom(base: string) {
    if (!draft || generating) return
    setError(null)
    setGenerating(true)
    setStatus('生成中...')
    const controller = new AbortController()
    abortRef.current = controller
    let text = base
    setDraft(d => (d ? { ...d, text } : d))
    try {
      const res = await fetch(`${API_ORIGIN}/api/writer/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders },
        body: JSON.stringify({ text: base, memory: draft.memory, author_note: draft.author_note, settings: draft.settings }),
        signal: controller.signal,
      })
      if (!res.ok || !res.body) throw new Error(await readErrorDetail(res))
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let sep: number
        while ((sep = buffer.indexOf('\n\n')) >= 0) {
          const raw = buffer.slice(0, sep)
          buffer = buffer.slice(sep + 2)
          let event = 'message'
          let data = ''
          for (const line of raw.split('\n')) {
            if (line.startsWith('event: ')) event = line.slice(7).trim()
            else if (line.startsWith('data: ')) data += line.slice(6)
          }
          if (!data) continue
          const payload = JSON.parse(data)
          if (event === 'delta') {
            text += payload.text
            setDraft(d => (d ? { ...d, text } : d))
            const area = textRef.current
            if (area) area.scrollTop = area.scrollHeight
          } else if (event === 'error') {
            throw new Error(payload.detail)
          } else if (event === 'done') {
            setStatus(`${payload.chars}文字を書き足しました`)
          }
        }
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') {
        setStatus('停止しました')
      } else {
        setError(e instanceof Error ? e.message : String(e))
        setStatus('')
      }
    } finally {
      setGenerating(false)
      abortRef.current = null
      dirty.current = true
      setDraft(d => (d ? { ...d, text } : d))
    }
  }

  function generate() {
    if (!draft) return
    setUndoStack(stack => [...stack, draft.text].slice(-30))
    void generateFrom(draft.text)
  }

  function retry() {
    const base = undoStack[undoStack.length - 1]
    if (base === undefined) return
    void generateFrom(base)
  }

  function undo() {
    const base = undoStack[undoStack.length - 1]
    if (base === undefined || !draft) return
    setUndoStack(stack => stack.slice(0, -1))
    edit({ text: base })
    setStatus('最後の生成を取り消しました')
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault()
      generate()
    }
  }

  async function toManga() {
    if (!draft) return
    if (dirty.current) await save(draft)
    const res = await fetch(`${API_ORIGIN}/api/writer/drafts/${draft.id}/to-story`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ panels_per_page: 4 }),
    })
    if (!res.ok) {
      setError(await readErrorDetail(res))
      return
    }
    const { story_id } = await res.json()
    navigate(`/story?story=${story_id}`)
  }

  const settings = draft?.settings
  const setSettings = (patch: Partial<WriterSettings>) => settings && edit({ settings: { ...settings, ...patch } })
  const currentModel = models.find(m => m.id === (settings?.model ?? models.find(x => x.default)?.id))

  return (
    <div className="writer-root">
      <header className="writer-header">
        <button type="button" className="writer-link" onClick={() => navigate('/')}>← ホーム</button>
        <h1>物語エディタ</h1>
        <button type="button" className="writer-link" onClick={() => setShowList(v => !v)}>
          {showList ? '閉じる' : `下書き(${drafts.length})`}
        </button>
      </header>

      <div className="writer-layout">
        <aside className={`writer-drafts${showList ? ' writer-drafts--open' : ''}`}>
          <button type="button" className="writer-primary" onClick={() => void newDraft()} disabled={generating}>
            ＋ 新しい物語
          </button>
          <ul>
            {drafts.map(d => (
              <li key={d.id} className={d.id === draft?.id ? 'writer-draft writer-draft--active' : 'writer-draft'}>
                <button type="button" className="writer-draft-open" onClick={() => void openDraft(d.id)} disabled={generating}>
                  <span className="writer-draft-title">{d.title || d.preview || '(無題)'}</span>
                  <span className="writer-draft-meta">
                    {d.length.toLocaleString()}字{d.story_id ? ' ・ 漫画化済み' : ''}
                  </span>
                </button>
                <button type="button" className="writer-draft-delete" onClick={() => void removeDraft(d.id)} disabled={generating}>
                  削除
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <main className="writer-main">
          {!draft ? (
            <div className="writer-empty">
              <p>「＋ 新しい物語」から書き始めてください。</p>
              <button type="button" className="writer-primary" onClick={() => void newDraft()}>＋ 新しい物語</button>
            </div>
          ) : (
            <>
              {draft.series_id !== null && draft.volume_no !== null && (
                <p className="writer-series-note">
                  シリーズの第{draft.volume_no}巻として書いています。メモリに「これまでのあらすじ」と
                  「前巻の結び」が入っているので、その続きから書き始めてください。「漫画にする」とこの巻になります。
                </p>
              )}
              <input
                className="writer-title"
                type="text"
                value={draft.title}
                placeholder="タイトル(省略可)"
                onChange={e => edit({ title: e.target.value })}
              />
              <textarea
                ref={textRef}
                className="writer-text"
                value={draft.text}
                readOnly={generating}
                placeholder="書き出しを入力して「続きを生成」(Ctrl+Enter)。生成された文も自由に書き直せます。"
                onChange={e => edit({ text: e.target.value })}
                onKeyDown={onKeyDown}
              />

              {error && <p className="writer-error">{error}</p>}

              <div className="writer-toolbar">
                {generating ? (
                  <button type="button" className="writer-stop" onClick={() => abortRef.current?.abort()}>■ 停止</button>
                ) : (
                  <button type="button" className="writer-primary" onClick={generate} disabled={!draft.text.trim() && !draft.memory.trim()}>
                    ✎ 続きを生成
                  </button>
                )}
                <button type="button" onClick={retry} disabled={generating || undoStack.length === 0}>↻ やり直し</button>
                <button type="button" onClick={undo} disabled={generating || undoStack.length === 0}>↶ 元に戻す</button>
                <span className="writer-status">
                  {draft.text.length.toLocaleString()}字 {status && `・ ${status}`}
                </span>
                <button type="button" onClick={() => void saveAs()} disabled={generating}>
                  別名で保存
                </button>
                <button type="button" className="writer-manga" onClick={() => void toManga()} disabled={generating || !draft.text.trim()}>
                  漫画にする →
                </button>
              </div>

              <details className="writer-panel" open={!draft.text}>
                <summary>メモリ・作者メモ</summary>
                <label>
                  メモリ(常に先頭に入る設定: 世界観・登場人物・文体など)
                  <textarea rows={4} value={draft.memory} onChange={e => edit({ memory: e.target.value })} />
                </label>
                <label>
                  作者メモ(直近の展開への指示。本文の末尾の少し手前に入る)
                  <textarea rows={2} value={draft.author_note} onChange={e => edit({ author_note: e.target.value })} />
                </label>
              </details>

              {settings && (
                <details className="writer-panel">
                  <summary>生成の設定({currentModel?.label ?? '既定のモデル'})</summary>
                  <label>
                    モデル
                    <select value={settings.model ?? ''} onChange={e => setSettings({ model: e.target.value || null })}>
                      {models.map(m => (
                        <option key={m.id} value={m.id}>{m.label}{m.default ? '(既定)' : ''} — {m.note}</option>
                      ))}
                    </select>
                  </label>
                  <div className="writer-row">
                    <label>
                      1回の長さ(トークン): {settings.max_tokens}
                      <input type="range" min={40} max={600} step={20} value={settings.max_tokens}
                        onChange={e => setSettings({ max_tokens: Number(e.target.value) })} />
                    </label>
                    <label>
                      ランダム性(temperature): {settings.temperature.toFixed(2)}
                      <input type="range" min={0.3} max={1.5} step={0.05} value={settings.temperature}
                        onChange={e => setSettings({ temperature: Number(e.target.value) })} />
                    </label>
                    <label>
                      top_p: {settings.top_p.toFixed(2)}
                      <input type="range" min={0.5} max={1} step={0.01} value={settings.top_p}
                        onChange={e => setSettings({ top_p: Number(e.target.value) })} />
                    </label>
                  </div>
                </details>
              )}
            </>
          )}
        </main>
      </div>
    </div>
  )
}
