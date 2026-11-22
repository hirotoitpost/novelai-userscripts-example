import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { apiFetch, isAuthExpired, notifyAuthExpired } from '../api'
import type { MetadataGenerationSettings } from '../api'
import HelpLink from '../components/HelpLink'
import { useAuth } from '../context/AuthContext'
import { useAssistantContext } from '../assistantContext'
import { useLocalStorage } from '../hooks/useLocalStorage'
import { fileFetchUrl, fileUrl, getJson, thumbUrl } from '../library'
import { readPanelSettings, type PanelDrawSettings } from '../studioPanel'
import './Publications.css'
import './Studio.css'

/**
 * 対話生成: 文章モデル(NovelAI の GLM-4.6)と日本語で相談しながら、1枚の絵のタグを組み立てて直していく。
 * 左が会話、右が描いた絵とタグ。モデルは場面のタグだけを書き、キャラの見た目・普段の服・絵柄はキャラシートから
 * アプリが入れる(/api/studio。studio.py)。描くのは「このタグで生成」を押したとき(「案が出たらすぐ描く」を
 * 入れると、案が出るたびに描く)。会話は利用者のフォルダに残り、左上の一覧から開き直せる。
 * ?character=ID でキャラを選んだ状態で、&path=画像のパス で、その画像のタグとシードから始める。
 * ?scene=シーンID で、漫画のコマを描き直す(物語エディタの「対話で描き直す」)。タグはシーンの作画タグから始め、
 * 描いた絵はコマの候補に入る(コマの絵は変えない)。「このコマに採用」で、その絵をコマにし、タグをシーンに保存する。
 * 描き方(テンプレート・カラー・キャラ参照・画像の設定)は、ボタンを押したときの漫画のページの設定を使う。
 */

interface Character {
  id: number
  name: string
}

type Size = 'portrait' | 'landscape' | 'square'

interface Tags {
  scene: string
  negative: string
  size: Size
  use_outfit: boolean
}

interface Diff {
  added: string[]
  removed: string[]
  changed: string[]
}

interface Message {
  role: 'user' | 'assistant'
  /** 画面に出す文 */
  text: string
  /** モデルに渡す元の答え(assistant だけ。JSON のまま) */
  raw?: string
  changes?: string
  diff?: { scene: Diff; negative: Diff }
  /** この発言のあとで描いた絵 */
  imageIndex?: number
}

interface Shot extends Tags {
  /** 1枚の絵(生成履歴の ID) */
  history_id?: number
  /** コマの候補(候補の ID) */
  candidate_id?: number
  /** このコマに採用した */
  adopted?: boolean
  path: string
  seed: number
}

interface PanelLink {
  scene_id: number
  story_id: number
  draw: PanelDrawSettings
}

interface PanelInfo {
  scene_id: number
  story_id: number
  story_title: string
  scene_index: number
  title: string
  text: string
  prompt_tags: string
  absent_tags: string
  characters: Character[]
  panel: { image_path: string; seed: number } | null
}

interface SessionData {
  messages: Message[]
  tags: Tags | null
  shots: Shot[]
  seed: number | null
  lockSeed: boolean
  panel?: PanelLink | null
}

interface DrawResponse {
  candidate_id: number
  path: string
  seed: number
}

function shotLabel(s: Shot): string {
  return s.history_id !== undefined ? `#${s.history_id}` : `候補 ${s.candidate_id}`
}

interface SessionSummary {
  id: string
  title: string
  character_id: number | null
  updated_at: string
}

interface TurnResponse {
  proposal: Tags & { say: string; action: 'generate' | 'ask' | 'none'; changes: string }
  assistant_turn: string
  diff: { scene: Diff; negative: Diff }
}

interface GenerateResponse {
  history_id: number
  path: string
  image: string
  seed: number
  prompt: string
  negative: string
}

const EMPTY: SessionData = { messages: [], tags: null, shots: [], seed: null, lockSeed: false }
const SIZE_LABEL: Record<Size, string> = { portrait: '縦長', landscape: '横長', square: '正方形' }

async function send<T>(token: string, method: 'PUT' | 'DELETE', path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({ detail: res.statusText }))
  if (!res.ok) {
    if (isAuthExpired(res.status)) notifyAuthExpired()
    throw new Error(data.detail ?? `HTTP ${res.status}`)
  }
  return data as T
}

function DiffChips({ diff }: { diff: Diff }) {
  if (!diff.added.length && !diff.removed.length && !diff.changed.length) return null
  return (
    <span className="studio-diff">
      {diff.added.map(t => <span key={`a${t}`} className="studio-chip studio-chip--add">+ {t}</span>)}
      {diff.changed.map(t => <span key={`c${t}`} className="studio-chip studio-chip--change">± {t}</span>)}
      {diff.removed.map(t => <span key={`r${t}`} className="studio-chip studio-chip--remove">− {t}</span>)}
    </span>
  )
}

export default function Studio() {
  const { token } = useAuth()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [characters, setCharacters] = useState<Character[]>([])
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [sessionId, setSessionId] = useState<string | null>(params.get('session'))
  const [characterId, setCharacterId] = useState<number | null>(Number(params.get('character')) || null)
  const [data, setData] = useState<SessionData>(EMPTY)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState<'turn' | 'generate' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [viewing, setViewing] = useState<number | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [autoGenerate, setAutoGenerate] = useLocalStorage('studio.autoGenerate', false)
  const [strength, setStrength] = useLocalStorage('studio.referenceStrength', 0.55)
  const [useReference, setUseReference] = useLocalStorage('studio.useReference', true)
  const [panelInfo, setPanelInfo] = useState<PanelInfo | null>(null)
  const logRef = useRef<HTMLDivElement>(null)
  const startedFrom = useRef<string | null>(null)

  useAssistantContext({ characterId })

  const loadSessions = useCallback(() => {
    if (!token) return
    apiFetch<SessionSummary[]>(token, '/api/studio/sessions').then(setSessions).catch(() => undefined)
  }, [token])

  useEffect(() => {
    getJson<Character[]>('/api/story/characters').then(setCharacters).catch(() => undefined)
    loadSessions()
  }, [loadSessions])

  // 保存した会話を開く
  useEffect(() => {
    if (!token || !sessionId) return
    apiFetch<{ character_id: number | null; data: Partial<SessionData> }>(token, `/api/studio/sessions/${sessionId}`)
      .then(s => {
        setCharacterId(s.character_id)
        setData({ ...EMPTY, ...s.data })
        if (!s.data.panel) setPanelInfo(null)
        setViewing(s.data.shots?.length ? s.data.shots.length - 1 : null)
        setPreview(null)
      })
      .catch(e => setError(`会話を開けませんでした: ${e instanceof Error ? e.message : String(e)}`))
  }, [token, sessionId])

  // 画像から始める(キャラの画像のページの「対話で直す」)
  useEffect(() => {
    const path = params.get('path')
    if (!token || !path || startedFrom.current === path) return
    startedFrom.current = path
    ;(async () => {
      try {
        const res = await fetch(fileFetchUrl(path), { cache: 'no-store' })
        if (!res.ok) throw new Error(`画像を読み込めませんでした(HTTP ${res.status})`)
        const blob = await res.blob()
        const image = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader()
          reader.onload = () => resolve(reader.result as string)
          reader.onerror = () => reject(reader.error)
          reader.readAsDataURL(blob)
        })
        const settings = await apiFetch<MetadataGenerationSettings>(token, '/api/metadata/generation', { image })
        const cid = Number(params.get('character')) || null
        const { scene } = await apiFetch<{ scene: string }>(token, '/api/studio/from-image', { prompt: settings.prompt, character_id: cid })
        const w = settings.width ?? 832
        const h = settings.height ?? 1216
        const size: Size = w > h ? 'landscape' : w < h ? 'portrait' : 'square'
        const seed = settings.seed ?? (Number(params.get('seed')) || null)
        setSessionId(null)
        setData({
          ...EMPTY,
          tags: { scene, negative: settings.negative_prompt ?? '', size, use_outfit: false },
          seed,
          lockSeed: seed !== null,
          messages: [{ role: 'assistant', text: 'この画像のタグとシードから始めます。直したい所を書いてください。' }],
        })
        setPreview(path)
        setViewing(null)
      } catch (e) {
        setError(`画像のタグを読めませんでした: ${e instanceof Error ? e.message : String(e)}`)
      }
    })()
  }, [token, params])

  // コマを描き直す(物語エディタの「対話で描き直す」)
  useEffect(() => {
    const sceneId = Number(params.get('scene')) || null
    if (!token || !sceneId || startedFrom.current === `scene:${sceneId}`) return
    startedFrom.current = `scene:${sceneId}`
    apiFetch<PanelInfo>(token, `/api/studio/panel/${sceneId}`)
      .then(info => {
        setPanelInfo(info)
        setSessionId(null)
        setCharacterId(null)
        const seed = info.panel?.seed ?? null
        setData({
          ...EMPTY,
          tags: { scene: info.prompt_tags, negative: info.absent_tags, size: 'portrait', use_outfit: false },
          seed,
          lockSeed: false,
          panel: { scene_id: info.scene_id, story_id: info.story_id, draw: readPanelSettings() },
          messages: [{
            role: 'assistant',
            text: `「${info.story_title}」のシーン${info.scene_index + 1}${info.title ? `「${info.title}」` : ''}のコマを描き直します。`
              + `直したい所を書いてください。描いた絵はコマの候補に入り、「このコマに採用」を押すまでコマの絵は変わりません。`,
          }],
        })
        setPreview(info.panel?.image_path ?? null)
        setViewing(null)
      })
      .catch(e => setError(`コマを開けませんでした: ${e instanceof Error ? e.message : String(e)}`))
  }, [token, params])

  // 保存したコマの会話を開いたときも、シーンの情報(題・登場人物)を出す
  const panelSceneId = data.panel?.scene_id ?? null
  useEffect(() => {
    if (!token || !panelSceneId || panelInfo?.scene_id === panelSceneId) return
    apiFetch<PanelInfo>(token, `/api/studio/panel/${panelSceneId}`).then(setPanelInfo).catch(() => undefined)
  }, [token, panelSceneId, panelInfo])

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' })
  }, [data.messages.length, busy])

  const title = useMemo(() => data.messages.find(m => m.role === 'user')?.text.slice(0, 40) ?? '', [data.messages])

  /** 会話を保存する(初めての保存で会話を作る)。 */
  const save = useCallback(async (next: SessionData) => {
    if (!token || !next.messages.some(m => m.role === 'user')) return
    const first = next.messages.find(m => m.role === 'user')?.text.slice(0, 40) ?? ''
    const body = { title: next.panel ? `コマ: ${first}` : first, character_id: next.panel ? null : characterId, data: next }
    try {
      if (sessionId) {
        await send(token, 'PUT', `/api/studio/sessions/${sessionId}`, body)
      } else {
        const created = await apiFetch<{ id: string }>(token, '/api/studio/sessions', body)
        setSessionId(created.id)
        setParams(p => {
          const q = new URLSearchParams(p)
          q.set('session', created.id)
          q.delete('path')
          q.delete('scene')
          return q
        }, { replace: true })
      }
      loadSessions()
    } catch (e) {
      setError(`会話を保存できませんでした: ${e instanceof Error ? e.message : String(e)}`)
    }
  }, [token, characterId, sessionId, setParams, loadSessions])

  // 生成を待つあいだに変わった会話にも足せるよう、いまの会話はここから読む
  const dataRef = useRef(data)
  dataRef.current = data

  const update = useCallback((fn: (d: SessionData) => SessionData, persist = true) => {
    const next = fn(dataRef.current)
    dataRef.current = next
    setData(next)
    if (persist) void save(next)
  }, [save])

  function addShot(shot: Shot, base: SessionData) {
    update(d => {
      const messages = [...d.messages]
      for (let i = messages.length - 1; i >= 0; i--) {
        if (messages[i].role === 'assistant') {
          messages[i] = { ...messages[i], imageIndex: d.shots.length }
          break
        }
      }
      return { ...d, shots: [...d.shots, shot], seed: shot.seed, messages }
    })
    setViewing(base.shots.length)
    setPreview(null)
  }

  /** コマの候補をコマの絵にし、描いたときのタグをシーンに保存する。 */
  async function adopt(index: number) {
    const s = data.shots[index]
    const link = data.panel
    if (!token || !link || s.candidate_id === undefined) return
    setError(null)
    try {
      await apiFetch(token, `/api/studio/panel/${link.scene_id}/adopt`, {
        candidate_id: s.candidate_id, prompt_tags: s.scene, absent_tags: s.negative,
      })
      update(d => ({
        ...d,
        shots: d.shots.map((x, i) => ({ ...x, adopted: i === index })),
        messages: [...d.messages, {
          role: 'assistant',
          text: `${index + 1} 枚目をコマの絵にしました。タグもシーンに保存しました(それまでのコマの絵は候補に戻ります)。`
            + '吹き出しを入れ直すには、物語のページで合成し直してください。',
        }],
      }))
    } catch (e) {
      setError(`採用できませんでした: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  async function generate(tags: Tags, base: SessionData) {
    if (!token) return
    setBusy('generate')
    setError(null)
    try {
      if (base.panel) {
        const link = base.panel
        const r = await apiFetch<DrawResponse>(token, `/api/studio/panel/${link.scene_id}/draw`, {
          ...link.draw,
          prompt_tags: tags.scene,
          absent_tags: tags.negative,
          seed: base.lockSeed && base.seed !== null ? base.seed : null,
        })
        addShot({ ...tags, candidate_id: r.candidate_id, path: r.path, seed: r.seed }, base)
        return
      }
      const last = base.shots[base.shots.length - 1]
      const r = await apiFetch<GenerateResponse>(token, '/api/studio/generate', {
        ...tags,
        character_id: characterId,
        seed: base.lockSeed && base.seed !== null ? base.seed : null,
        use_reference: useReference,
        reference_strength: strength,
        based_on: last?.history_id ?? null,
      })
      addShot({ ...tags, history_id: r.history_id, path: r.path, seed: r.seed }, base)
    } catch (e) {
      setError(`生成できませんでした: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(null)
    }
  }

  async function submit() {
    const message = input.trim()
    if (!token || !message || busy) return
    setInput('')
    setError(null)
    const userMsg: Message = { role: 'user', text: message }
    const history = data.messages
      .filter(m => m.role === 'user' || m.raw)
      .map(m => ({ role: m.role, content: m.role === 'assistant' ? (m.raw ?? m.text) : m.text }))
    const withUser = { ...data, messages: [...data.messages, userMsg] }
    update(() => withUser, false)
    setBusy('turn')
    try {
      const r = await apiFetch<TurnResponse>(token, '/api/studio/turn', {
        message, history, character_id: characterId, current: data.tags, scene_id: data.panel?.scene_id ?? null,
      })
      const p = r.proposal
      const tags: Tags = { scene: p.scene, negative: p.negative, size: p.size, use_outfit: p.use_outfit }
      const reply: Message = {
        role: 'assistant', text: p.say || '(返事なし)', raw: r.assistant_turn,
        changes: p.changes, diff: p.action === 'generate' ? r.diff : undefined,
      }
      const next: SessionData = { ...withUser, messages: [...withUser.messages, reply], tags: p.action === 'generate' ? tags : (data.tags ?? tags) }
      update(() => next)
      setBusy(null)
      if (p.action === 'generate' && autoGenerate) await generate(tags, next)
    } catch (e) {
      setError(`答えをもらえませんでした: ${e instanceof Error ? e.message : String(e)}`)
      setBusy(null)
    }
  }

  function newSession() {
    setSessionId(null)
    setData(EMPTY)
    setViewing(null)
    setPreview(null)
    startedFrom.current = null
    setPanelInfo(null)
    setParams(characterId ? { character: String(characterId) } : {})
  }

  async function removeSession(id: string) {
    if (!token || !window.confirm('この会話を消しますか?(描いた絵はギャラリーに残ります)')) return
    await send(token, 'DELETE', `/api/studio/sessions/${id}`).catch(() => undefined)
    if (id === sessionId) newSession()
    loadSessions()
  }

  /** 描いた絵を土台にする(そのタグとシードに戻す)。 */
  function baseOnShot(index: number) {
    const s = data.shots[index]
    update(d => ({
      ...d,
      tags: { scene: s.scene, negative: s.negative, size: s.size, use_outfit: s.use_outfit },
      seed: s.seed,
      messages: [...d.messages, { role: 'assistant', text: `${index + 1} 枚目(${shotLabel(s)})のタグとシードに戻しました。` }],
    }))
  }

  const shot = viewing !== null ? data.shots[viewing] : null
  const shown = shot?.path ?? preview
  const tags = data.tags
  const lastDiffMsg = [...data.messages].reverse().find(m => m.diff)
  const panelMode = !!data.panel

  return (
    <div className="pub-root studio-root">
      <header className="pub-header">
        <button type="button" className="pub-link" onClick={() => navigate('/')}>← ホーム</button>
        <h1>対話生成<HelpLink slug="studio" /></h1>
        {panelMode && data.panel && (
          <span className="studio-panel-info">
            コマの描き直し: {panelInfo
              ? `「${panelInfo.story_title}」シーン${panelInfo.scene_index + 1}${panelInfo.title ? `「${panelInfo.title}」` : ''}`
                + (panelInfo.characters.length ? `(${panelInfo.characters.map(c => c.name).join('・')})` : '')
              : `シーン ${data.panel.scene_id}`}
            <button type="button" className="pub-link" onClick={() => navigate(`/story?story=${data.panel!.story_id}`)}>物語に戻る</button>
          </span>
        )}
        {!panelMode && <select
          className="studio-select"
          value={characterId ?? ''}
          onChange={e => {
            const id = Number(e.target.value) || null
            setCharacterId(id)
          }}
          aria-label="キャラ"
        >
          <option value="">キャラなし</option>
          {characters.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>}
        <select
          className="studio-select"
          value={sessionId ?? ''}
          onChange={e => (e.target.value ? setSessionId(e.target.value) : newSession())}
          aria-label="会話"
        >
          <option value="">{sessionId ? '新しい会話' : (title || '新しい会話')}</option>
          {sessions.map(s => <option key={s.id} value={s.id}>{s.title || '(無題)'} — {s.updated_at.slice(0, 10)}</option>)}
        </select>
        <button type="button" className="pub-link" onClick={newSession}>新しい会話</button>
        {sessionId && <button type="button" className="pub-link" onClick={() => removeSession(sessionId)}>この会話を消す</button>}
      </header>

      {error && <p className="pub-error studio-error" role="alert">{error}</p>}

      <main className="studio-main">
        <section className="studio-chat" aria-label="会話">
          <div className="studio-log" ref={logRef}>
            {data.messages.length === 0 && (
              <div className="studio-empty">
                <p>描きたい絵を日本語で書いてください。例:</p>
                <ul>
                  <li>白いビキニで四つん這い、下から見上げる構図で足まで入れて</li>
                  <li>教室の窓際で振り返って笑っている、夕方の光</li>
                </ul>
                <p>描いたあとは「顔が逆さま」「膝を床から離して」のように直したい所を書くと、タグを直します。「このタグの意味は?」と聞けば説明だけします。</p>
              </div>
            )}
            {data.messages.map((m, i) => (
              <div key={i} className={`studio-msg studio-msg--${m.role}`}>
                <div className="studio-bubble">{m.text}</div>
                {m.changes && <div className="studio-changes">変更: {m.changes}</div>}
                {m.diff && <DiffChips diff={m.diff.scene} />}
                {m.imageIndex !== undefined && data.shots[m.imageIndex] && (
                  <button type="button" className="studio-thumb-inline" onClick={() => setViewing(m.imageIndex!)}>
                    <img src={thumbUrl(data.shots[m.imageIndex].path, 240)} alt={`${m.imageIndex + 1} 枚目`} />
                  </button>
                )}
              </div>
            ))}
            {busy === 'turn' && <div className="studio-msg studio-msg--assistant"><div className="studio-bubble studio-wait">考えています…</div></div>}
          </div>
          <div className="studio-input">
            <textarea
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                  e.preventDefault()
                  void submit()
                }
              }}
              placeholder="描きたい絵・直したい所(Ctrl+Enter で送る)"
              rows={3}
              disabled={busy !== null}
            />
            <div className="studio-input-row">
              <label><input type="checkbox" checked={autoGenerate} onChange={e => setAutoGenerate(e.target.checked)} /> 案が出たらすぐ描く</label>
              <button type="button" className="studio-send" onClick={() => void submit()} disabled={busy !== null || !input.trim()}>送る</button>
            </div>
          </div>
        </section>

        <section className="studio-canvas" aria-label="絵とタグ">
          <div className="studio-image">
            {busy === 'generate' && <div className="studio-overlay">生成しています…</div>}
            {shown
              ? <a href={fileUrl(shown)} target="_blank" rel="noreferrer"><img src={fileUrl(shown)} alt="描いた絵" /></a>
              : <p className="studio-placeholder">まだ描いていません</p>}
          </div>
          {!shot && preview && panelMode && <div className="studio-shot-info"><span>いまのコマの絵</span></div>}
          {shot && (
            <div className="studio-shot-info">
              <span>{shotLabel(shot)} / シード {shot.seed}{panelMode ? '' : ` / ${SIZE_LABEL[shot.size]}`}{shot.adopted ? ' / コマに採用済み' : ''}</span>
              <span>
                {panelMode && shot.candidate_id !== undefined && !shot.adopted && (
                  <button type="button" className="studio-adopt" onClick={() => void adopt(viewing!)}>このコマに採用</button>
                )}
                <button type="button" className="pub-link" onClick={() => baseOnShot(viewing!)}>この絵を土台にする</button>
              </span>
            </div>
          )}
          {data.shots.length > 0 && (
            <div className="studio-strip" role="list">
              {data.shots.map((s, i) => (
                <button
                  key={s.history_id ?? `c${s.candidate_id}`}
                  type="button"
                  role="listitem"
                  className={`studio-strip-item${i === viewing ? ' is-active' : ''}`}
                  onClick={() => { setViewing(i); setPreview(null) }}
                  title={shotLabel(s)}
                >
                  <img src={thumbUrl(s.path, 240)} alt={`${i + 1} 枚目`} loading="lazy" />
                  <span>{i + 1}{s.adopted ? ' ✓' : ''}</span>
                </button>
              ))}
            </div>
          )}

          <div className="studio-tags">
            <label className="studio-field">
              <span>場面のタグ{lastDiffMsg?.diff && <DiffChips diff={lastDiffMsg.diff.scene} />}</span>
              <textarea
                value={tags?.scene ?? ''}
                onChange={e => update(d => ({ ...d, tags: { ...(d.tags ?? { negative: '', size: 'portrait', use_outfit: false }), scene: e.target.value } }), false)}
                onBlur={() => void save(data)}
                rows={5}
                placeholder="会話で決まると、ここに入ります(手で直してもかまいません)"
              />
            </label>
            <label className="studio-field">
              <span>{panelMode ? 'このコマに出てはいけない物(ネガティブに足す)' : 'ネガティブ'}</span>
              <textarea
                value={tags?.negative ?? ''}
                onChange={e => update(d => ({ ...d, tags: { ...(d.tags ?? { scene: '', size: 'portrait', use_outfit: false }), negative: e.target.value } }), false)}
                onBlur={() => void save(data)}
                rows={3}
              />
            </label>
            <div className="studio-options">
              {!panelMode && <label>
                向き
                <select
                  value={tags?.size ?? 'portrait'}
                  onChange={e => update(d => ({ ...d, tags: { ...(d.tags ?? { scene: '', negative: '', use_outfit: false }), size: e.target.value as Size } }))}
                >
                  {(Object.keys(SIZE_LABEL) as Size[]).map(s => <option key={s} value={s}>{SIZE_LABEL[s]}</option>)}
                </select>
              </label>}
              {characterId && !panelMode && (
                <>
                  <label><input type="checkbox" checked={tags?.use_outfit ?? false} onChange={e => update(d => ({ ...d, tags: { ...(d.tags ?? { scene: '', negative: '', size: 'portrait' }), use_outfit: e.target.checked } }))} /> 普段の服</label>
                  <label><input type="checkbox" checked={useReference} onChange={e => setUseReference(e.target.checked)} /> キャラ参照</label>
                  {useReference && (
                    <label>
                      強さ
                      <input type="number" min={0} max={1} step={0.05} value={strength} onChange={e => setStrength(Math.min(1, Math.max(0, Number(e.target.value))))} />
                    </label>
                  )}
                </>
              )}
              <label>
                <input type="checkbox" checked={data.lockSeed} onChange={e => update(d => ({ ...d, lockSeed: e.target.checked }))} /> シードを固定
              </label>
              {data.lockSeed && (
                <input
                  type="number"
                  className="studio-seed"
                  value={data.seed ?? ''}
                  min={0}
                  max={4294967295}
                  onChange={e => update(d => ({ ...d, seed: e.target.value === '' ? null : Number(e.target.value) }), false)}
                  aria-label="シード"
                />
              )}
            </div>
            <div className="studio-actions">
              <button
                type="button"
                className="studio-generate"
                disabled={busy !== null || !tags?.scene.trim()}
                onClick={() => tags && void generate(tags, data)}
              >
                {busy === 'generate' ? '生成しています…' : panelMode ? 'このタグで候補を描く' : 'このタグで生成'}
              </button>
              <span className="studio-hint">
                {panelMode
                  ? '登場人物の見た目・服・コマの形は、物語の設定から入ります。描いた絵はコマの候補に入ります。'
                  : 'キャラの見た目・絵柄はキャラシートから入ります。描いた絵はギャラリーとキャラの画像に並びます。'}
              </span>
            </div>
          </div>
        </section>
      </main>
    </div>
  )
}
