import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import type { Character } from '../components/StoryCharacters'
import { apiFetch, ImagePreset } from '../api'
import './MangaDraft.css'

/**
 * 漫画ドラフト: テーマ・ジャンル・登場キャラ → 大枠シナリオ案(3つ)を選ぶ → 1話(4コマ)ずつ台本を
 * 書き進める(セリフ・話し手・作画タグを手直しできる) → 「漫画にする」で物語作成・コマ生成・合成まで行う。
 * 文章は NovelAI の GLM-4.6(/api/manga-draft)。書きかけはブラウザに残す。
 */

interface Outline {
  title: string
  logline: string
  episodes: string[]
}

interface DraftLine {
  speaker: string
  kind: 'speech' | 'thought'
  text: string
}

interface DraftPanel {
  characters: string[]
  lines: DraftLine[]
  narration: string
  sfx: string[]
  prompt_tags: string
}

interface Series {
  id: number
  title: string
  volumes: { volume_no: number }[]
}

interface ImportSummary {
  id: number
  title: string
  status: string
  panel_count: number
}

interface Template {
  id: string
  label: string
}

interface DraftState {
  theme: string
  genre: string
  characterIds: number[]
  profiles: Record<number, string>
  episodes: number
  notes: string
  seriesId: number | null
  // 構成(コマ運び)の参考にする取り込み(/api/manga-import)
  importId: number | null
  outlines: Outline[]
  outline: Outline | null
  names: { id: number; name: string }[]
  scripts: (DraftPanel[] | null)[]
  // 「漫画にする」で作った物語。コマの生成などで失敗して押し直したとき、作り直さずに続きから進める。
  // 台本や大枠を変えたら消す(変えた台本は新しい物語にする)
  pendingStoryId?: number | null
}

interface Job {
  status: string
  message: string
  progress: number
  total: number
  detail: string | null
}

const STORAGE_KEY = 'nai_manga_draft'
const GENRES = ['日常コメディ', '夫婦の日常コメディ', '学園コメディ', 'ラブコメ', 'ほのぼの', 'ちょっといい話', 'ドタバタギャグ']
// 全年齢の漫画なので、露出を避けるタグを既定で入れる(品質タグはコマ生成の既定値と同じ)
const DEFAULT_NEGATIVE =
  'lowres, artistic error, scan artifacts, worst quality, bad quality, jpeg artifacts, very displeasing, ' +
  'watermark, signature, nsfw, nude, cleavage, underwear, sexually suggestive'
const PRESET_KEYS = ['model', 'steps', 'scale', 'sampler', 'noise_schedule', 'cfg_rescale', 'complexity'] as const

const EMPTY: DraftState = {
  theme: '',
  genre: GENRES[0],
  characterIds: [],
  profiles: {},
  episodes: 3,
  notes: '',
  seriesId: null,
  importId: null,
  outlines: [],
  outline: null,
  names: [],
  scripts: [],
  pendingStoryId: null,
}

function loadDraft(): DraftState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? { ...EMPTY, ...(JSON.parse(raw) as DraftState) } : EMPTY
  } catch {
    return EMPTY
  }
}

/** 送る前に、書きかけの空のセリフを除く(サーバーは空のセリフを受け付けない)。 */
function cleanPanels(panels: DraftPanel[]): DraftPanel[] {
  return panels.map(p => ({ ...p, lines: p.lines.filter(l => l.text.trim()) }))
}

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export default function MangaDraft() {
  const { token } = useAuth()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  // 取り込みページの「この構成で漫画を作る」から来たら、その取り込みを構成の参考にする
  const [draft, setDraft] = useState<DraftState>(() => {
    const loaded = loadDraft()
    const fromImport = Number(searchParams.get('import'))
    return fromImport ? { ...loaded, importId: fromImport } : loaded
  })
  const [imports, setImports] = useState<ImportSummary[]>([])
  const [characters, setCharacters] = useState<Character[]>([])
  const [seriesList, setSeriesList] = useState<Series[]>([])
  const [templates, setTemplates] = useState<Template[]>([])
  const [presets, setPresets] = useState<ImagePreset[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // 漫画にするときの設定
  const [template, setTemplate] = useState('vertical4')
  const [color, setColor] = useState(false)
  const [useReference, setUseReference] = useState(false)
  const [presetId, setPresetId] = useState<number | null>(null)
  const [negative, setNegative] = useState(DEFAULT_NEGATIVE)
  const [job, setJob] = useState<Job | null>(null)
  const [result, setResult] = useState<{ storyId: number; finalImage: string } | null>(null)

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(draft))
    } catch {
      // 保存できなくても編集は続けられる
    }
  }, [draft])

  useEffect(() => {
    if (!token) return
    apiFetch<Character[]>(token, '/api/story/characters').then(setCharacters).catch(() => {})
    apiFetch<Series[]>(token, '/api/series').then(setSeriesList).catch(() => {})
    apiFetch<Template[]>(token, '/api/manga-v2/templates').then(setTemplates).catch(() => {})
    apiFetch<ImportSummary[]>(token, '/api/manga-import').then(setImports).catch(() => {})
    apiFetch<ImagePreset[]>(token, '/api/image/presets').then(setPresets).catch(() => {})
  }, [token])

  const update = useCallback((patch: Partial<DraftState>) => setDraft(d => ({ ...d, ...patch })), [])

  const toggleCharacter = (c: Character) => {
    const selected = draft.characterIds.includes(c.id)
    update({
      characterIds: selected ? draft.characterIds.filter(i => i !== c.id) : [...draft.characterIds, c.id].slice(0, 4),
      profiles: selected ? draft.profiles : { ...draft.profiles, [c.id]: draft.profiles[c.id] ?? c.notes ?? '' },
    })
  }

  const common = () => ({
    character_ids: draft.characterIds,
    profiles: draft.profiles,
    notes: draft.notes,
    series_id: draft.seriesId,
    import_id: draft.importId,
  })

  const makeOutlines = async () => {
    if (!token) return
    setBusy('大枠シナリオを考えています(数十秒)…')
    setError(null)
    try {
      const res = await apiFetch<{ outlines: Outline[]; characters: { id: number; name: string }[]; episodes: number }>(
        token, '/api/manga-draft/outlines',
        { ...common(), theme: draft.theme, genre: draft.genre, episodes: draft.episodes },
      )
      update({
        outlines: res.outlines, names: res.characters, episodes: res.episodes, outline: null, scripts: [], pendingStoryId: null,
      })
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(null)
    }
  }

  const chooseOutline = (o: Outline) => {
    update({ outline: { ...o, episodes: [...o.episodes] }, scripts: o.episodes.map(() => null), pendingStoryId: null })
    setResult(null)
  }

  const editOutline = (patch: Partial<Outline>) => {
    if (draft.outline) update({ outline: { ...draft.outline, ...patch } })
  }

  /** 1話分の台本を作る。直前の話のコマを渡して流れをつなげる。 */
  const makeEpisode = async (index: number, scripts: (DraftPanel[] | null)[]): Promise<DraftPanel[] | null> => {
    if (!token || !draft.outline) return null
    const res = await apiFetch<{ panels: DraftPanel[] }>(token, '/api/manga-draft/episode', {
      ...common(),
      outline: draft.outline,
      episode_index: index,
      previous_panels: index > 0 ? cleanPanels(scripts[index - 1] ?? []) : [],
    })
    return res.panels
  }

  const makeEpisodes = async (indexes: number[]) => {
    if (!draft.outline) return
    setError(null)
    let scripts = [...draft.scripts]
    try {
      for (const index of indexes) {
        setBusy(`第${index + 1}話の台本を書いています…`)
        const panels = await makeEpisode(index, scripts)
        scripts = scripts.map((s, i) => (i === index ? panels : s))
        setDraft(d => ({ ...d, scripts, pendingStoryId: null }))
      }
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(null)
    }
  }

  const editPanel = (episode: number, panel: number, patch: Partial<DraftPanel>) => {
    setDraft(d => ({
      ...d,
      pendingStoryId: null,
      scripts: d.scripts.map((s, e) =>
        e !== episode || !s ? s : s.map((p, i) => (i === panel ? { ...p, ...patch } : p))),
    }))
  }

  const editLine = (episode: number, panel: number, line: number, patch: Partial<DraftLine> | null) => {
    const p = draft.scripts[episode]?.[panel]
    if (!p) return
    const lines = patch === null
      ? p.lines.filter((_, i) => i !== line)
      : p.lines.map((l, i) => (i === line ? { ...l, ...patch } : l))
    editPanel(episode, panel, { lines })
  }

  const allWritten = draft.scripts.length > 0 && draft.scripts.every(s => s && s.length > 0)

  const waitJob = async (storyId: number) => {
    while (true) {
      const current = await apiFetch<Job | null>(token!, `/api/story/${storyId}/job`)
      setJob(current)
      if (!current || current.status !== 'running') return current
      await new Promise(r => setTimeout(r, 3000))
    }
  }

  /** 物語を作り、コマを生成して合成する。 */
  const makeManga = async () => {
    if (!token || !draft.outline || !allWritten) return
    setError(null)
    setResult(null)
    try {
      // 前に作った物語があれば(途中で失敗して押し直したとき)作り直さず、まだ絵の無いコマから続ける
      let storyId = draft.pendingStoryId ?? null
      if (storyId != null) {
        try {
          await apiFetch(token, `/api/story/${storyId}`)
        } catch {
          storyId = null  // 消されていたら作り直す
        }
      }
      if (storyId == null) {
        setBusy('物語を作っています…')
        const created = await apiFetch<{ id: number }>(token, '/api/manga-draft/create', {
          title: draft.outline.title,
          character_ids: draft.characterIds,
          profiles: draft.profiles,
          episodes: draft.scripts.map(s => cleanPanels(s ?? [])),
          series_id: draft.seriesId,
        })
        storyId = created.id
        update({ pendingStoryId: storyId })
      }
      const story = { id: storyId }
      const preset = presets.find(p => p.id === presetId)
      const settings: Record<string, unknown> = { negative_prompt: negative.trim() || null }
      if (preset) {
        for (const key of PRESET_KEYS) {
          const value = preset.settings[key]
          if (value != null) settings[key] = value
        }
      }
      const total = draft.scripts.reduce((n, s) => n + (s?.length ?? 0), 0)
      const done = await apiFetch<unknown[]>(token, `/api/manga-v2/${story.id}/panels`)
      if (done.length < total) {
        setBusy('コマの絵を生成しています(1コマ十秒前後)…')
        await apiFetch(token, `/api/manga-v2/${story.id}/panels`, {
          template, color, use_character_reference: useReference, vary_seed: true, skip_existing: true, settings,
        })
        const finished = await waitJob(story.id)
        if (finished?.status === 'error') throw new Error(finished.detail ?? 'コマの生成に失敗しました')
      }
      setBusy('ページに合成しています…')
      const composed = await apiFetch<{ final_image_path: string }>(token, `/api/manga-v2/${story.id}/compose`, { template })
      setResult({ storyId: story.id, finalImage: composed.final_image_path })
      update({ pendingStoryId: null })
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(null)
      setJob(null)
    }
  }

  const reset = () => {
    if (window.confirm('ドラフトを最初からやり直します。よろしいですか?')) {
      setDraft(EMPTY)
      setResult(null)
    }
  }

  const names = draft.names.length
    ? draft.names
    : characters.filter(c => draft.characterIds.includes(c.id)).map(c => ({ id: c.id, name: c.name }))
  const selectedSeries = seriesList.find(s => s.id === draft.seriesId)

  return (
    <div className="md-root">
      <header className="md-header">
        <button type="button" className="md-link" onClick={() => navigate('/')}>← ホーム</button>
        <h1>漫画ドラフト</h1>
        <button type="button" className="md-link" onClick={reset}>最初から</button>
      </header>

      <main className="md-main">
        {/* 1. 設定 */}
        <section className="md-section">
          <h2>1. テーマと登場人物</h2>
          <label className="md-field">
            <span>テーマ</span>
            <input value={draft.theme} onChange={e => update({ theme: e.target.value })}
              placeholder="例: 秋の紅葉狩り、初めての料理教室、雨の日の相合傘" />
          </label>
          <div className="md-row">
            <label className="md-field">
              <span>ジャンル</span>
              <input list="md-genres" value={draft.genre} onChange={e => update({ genre: e.target.value })} />
              <datalist id="md-genres">{GENRES.map(g => <option key={g} value={g} />)}</datalist>
            </label>
            <label className="md-field md-field--narrow">
              <span>話数(1話=4コマ)</span>
              <input type="number" min={1} max={10} value={draft.episodes} disabled={draft.importId != null}
                title={draft.importId != null ? '構成の参考のコマ数から決まります' : undefined}
                onChange={e => update({ episodes: Math.min(10, Math.max(1, Number(e.target.value) || 1)) })} />
            </label>
            <label className="md-field">
              <span>シリーズ(続編にする)</span>
              <select value={draft.seriesId ?? ''} onChange={e => update({ seriesId: e.target.value ? Number(e.target.value) : null })}>
                <option value="">単発の作品</option>
                {seriesList.map(s => (
                  <option key={s.id} value={s.id}>{s.title}(次は{s.volumes.length + 1}巻)</option>
                ))}
              </select>
            </label>
          </div>
          {selectedSeries && (
            <p className="md-hint">シリーズの人物設定と既刊のあらすじを前提に、続編として作ります。</p>
          )}
          <label className="md-field">
            <span>構成の参考(取り込んだ作品)</span>
            <select value={draft.importId ?? ''} onChange={e => update({ importId: e.target.value ? Number(e.target.value) : null })}>
              <option value="">使わない</option>
              {imports.filter(i => i.status === 'analyzed' || i.id === draft.importId).map(i => (
                <option key={i.id} value={i.id}>{i.title}({i.panel_count}コマ)</option>
              ))}
            </select>
          </label>
          {draft.importId != null && (
            <p className="md-hint">
              取り込んだ作品のコマ運び(構図・人数・セリフの量・役割)だけを参考にします。話の内容・セリフは新しく作ります。
              話数はコマ数から決まります(4コマずつ、最大10話)。
            </p>
          )}

          <div className="md-field">
            <span>登場人物(最大4人)</span>
            <div className="md-chips">
              {characters.map(c => (
                <button type="button" key={c.id}
                  className={draft.characterIds.includes(c.id) ? 'md-chip md-chip--on' : 'md-chip'}
                  onClick={() => toggleCharacter(c)}>
                  {c.name}
                </button>
              ))}
            </div>
          </div>
          {draft.characterIds.map(id => {
            const c = characters.find(x => x.id === id)
            return (
              <label className="md-field" key={id}>
                <span>{c?.name ?? id} の人物像(性格・口調・呼び方)</span>
                <input value={draft.profiles[id] ?? ''}
                  onChange={e => update({ profiles: { ...draft.profiles, [id]: e.target.value } })}
                  placeholder="例: 恥ずかしがり屋ですぐ赤面する。丁寧語で、夫を「先生」と呼ぶ" />
              </label>
            )
          })}
          <label className="md-field">
            <span>補足(任意)</span>
            <textarea rows={2} value={draft.notes} onChange={e => update({ notes: e.target.value })}
              placeholder="入れたい場面・避けたいこと・オチの方向など" />
          </label>
          <button type="button" className="md-primary" onClick={makeOutlines}
            disabled={!!busy || !draft.theme.trim() || draft.characterIds.length === 0}>
            大枠シナリオを3案作る
          </button>
        </section>

        {/* 2. 大枠シナリオ */}
        {draft.outlines.length > 0 && (
          <section className="md-section">
            <h2>2. 大枠シナリオを選ぶ</h2>
            <div className="md-outlines">
              {draft.outlines.map((o, i) => (
                <button type="button" key={i}
                  className={draft.outline?.title === o.title ? 'md-outline md-outline--on' : 'md-outline'}
                  onClick={() => chooseOutline(o)}>
                  <strong>{o.title}</strong>
                  <span>{o.logline}</span>
                  <ol>{o.episodes.map((e, k) => <li key={k}>{e}</li>)}</ol>
                </button>
              ))}
            </div>
            {draft.outline && (
              <div className="md-edit-outline">
                <label className="md-field">
                  <span>タイトル</span>
                  <input value={draft.outline.title} onChange={e => editOutline({ title: e.target.value })} />
                </label>
                {draft.outline.episodes.map((e, i) => (
                  <label className="md-field" key={i}>
                    <span>第{i + 1}話</span>
                    <textarea rows={2} value={e}
                      onChange={ev => editOutline({ episodes: draft.outline!.episodes.map((x, k) => (k === i ? ev.target.value : x)) })} />
                  </label>
                ))}
                <button type="button" className="md-primary" disabled={!!busy}
                  onClick={() => makeEpisodes(draft.outline!.episodes.map((_, i) => i))}>
                  全話の台本を書く
                </button>
              </div>
            )}
          </section>
        )}

        {/* 3. 台本 */}
        {draft.outline && draft.scripts.some(Boolean) && (
          <section className="md-section">
            <h2>3. 台本を手直しする</h2>
            {draft.scripts.map((panels, e) => (
              <div className="md-episode" key={e}>
                <div className="md-episode-head">
                  <h3>第{e + 1}話</h3>
                  <span>{draft.outline!.episodes[e]}</span>
                  <button type="button" disabled={!!busy} onClick={() => makeEpisodes([e])}>
                    {panels ? '作り直す' : '書く'}
                  </button>
                </div>
                {panels?.map((p, i) => (
                  <div className="md-panel" key={i}>
                    <div className="md-panel-no">{i + 1}</div>
                    <div className="md-panel-body">
                      <div className="md-chips">
                        {names.map(n => (
                          <button type="button" key={n.id}
                            className={p.characters.includes(n.name) ? 'md-chip md-chip--on' : 'md-chip'}
                            onClick={() => editPanel(e, i, {
                              characters: p.characters.includes(n.name)
                                ? p.characters.filter(x => x !== n.name) : [...p.characters, n.name],
                            })}>
                            {n.name}
                          </button>
                        ))}
                        <span className="md-hint">(このコマに描く人)</span>
                      </div>
                      {p.lines.map((l, k) => (
                        <div className="md-line" key={k}>
                          <select value={l.speaker} onChange={ev => editLine(e, i, k, { speaker: ev.target.value })}>
                            <option value="">(不明)</option>
                            {names.map(n => <option key={n.id} value={n.name}>{n.name}</option>)}
                          </select>
                          <select value={l.kind} onChange={ev => editLine(e, i, k, { kind: ev.target.value as DraftLine['kind'] })}>
                            <option value="speech">セリフ</option>
                            <option value="thought">心の声</option>
                          </select>
                          <input value={l.text} onChange={ev => editLine(e, i, k, { text: ev.target.value })} />
                          <button type="button" onClick={() => editLine(e, i, k, null)} title="このセリフを消す">✕</button>
                        </div>
                      ))}
                      <button type="button" className="md-small"
                        onClick={() => editPanel(e, i, { lines: [...p.lines, { speaker: '', kind: 'speech', text: '' }] })}>
                        ＋ セリフ
                      </button>
                      <div className="md-row">
                        <label className="md-field">
                          <span>ナレーション</span>
                          <input value={p.narration} onChange={ev => editPanel(e, i, { narration: ev.target.value })} />
                        </label>
                        <label className="md-field">
                          <span>効果音(、区切り)</span>
                          <input value={p.sfx.join('、')}
                            onChange={ev => editPanel(e, i, { sfx: ev.target.value.split(/[、,]/).map(s => s.trim()).filter(Boolean) })} />
                        </label>
                      </div>
                      <label className="md-field">
                        <span>作画タグ(英語)</span>
                        <input value={p.prompt_tags} onChange={ev => editPanel(e, i, { prompt_tags: ev.target.value })} />
                      </label>
                    </div>
                  </div>
                ))}
              </div>
            ))}
          </section>
        )}

        {/* 4. 漫画にする */}
        {allWritten && (
          <section className="md-section">
            <h2>4. 漫画にする</h2>
            <div className="md-row">
              <label className="md-field">
                <span>コマ割り</span>
                <select value={template} onChange={e => setTemplate(e.target.value)}>
                  {templates.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}
                </select>
              </label>
              <label className="md-field">
                <span>画像のプリセット</span>
                <select value={presetId ?? ''} onChange={e => setPresetId(e.target.value ? Number(e.target.value) : null)}>
                  <option value="">既定(V5)</option>
                  {presets.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </label>
            </div>
            <div className="md-row">
              <label className="md-check"><input type="checkbox" checked={color} onChange={e => setColor(e.target.checked)} /> カラー</label>
              <label className="md-check">
                <input type="checkbox" checked={useReference} onChange={e => setUseReference(e.target.checked)} />
                キャラ参照を使う(V4.5、1人あたり Anlas 追加)
              </label>
            </div>
            <label className="md-field">
              <span>ネガティブ</span>
              <textarea rows={2} value={negative} onChange={e => setNegative(e.target.value)} />
            </label>
            <p className="md-hint">
              全{draft.scripts.reduce((n, s) => n + (s?.length ?? 0), 0)}コマを生成します(NovelAI の Anlas を使います)。
              {draft.seriesId != null && ` シリーズの${(selectedSeries?.volumes.length ?? 0) + 1}巻になります。`}
            </p>
            <button type="button" className="md-primary" onClick={makeManga} disabled={!!busy}>
              漫画にする
            </button>
          </section>
        )}

        {(busy || error || result) && (
          <section className="md-status" aria-live="polite">
            {busy && (
              <p>
                <span className="md-spinner" /> {busy}
                {job && job.total > 0 && ` (${job.progress}/${job.total})`}
              </p>
            )}
            {error && <p className="md-error" role="alert">{error}</p>}
            {result && (
              <div className="md-result">
                <p>漫画ができました。</p>
                <img src={`/api/story/manga-file?path=${encodeURIComponent(result.finalImage)}`} alt="できあがった漫画" />
                <button type="button" className="md-primary" onClick={() => navigate(`/story?story=${result.storyId}`)}>
                  物語ページで開く(描き直し・吹き出しの調整)
                </button>
              </div>
            )}
          </section>
        )}
      </main>
    </div>
  )
}
