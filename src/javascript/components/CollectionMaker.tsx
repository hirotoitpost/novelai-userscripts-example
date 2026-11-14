import { useEffect, useState } from 'react'
import type { Character } from './StoryCharacters'

/**
 * 下着・水着のコレクション/からかいコレクション/ヌード写真集を、好きなキャラで物語にする(/api/collections)。
 * テーマ(衣装)とポーズの組み合わせで台本を作るだけなので、できた物語は編集画面でセリフ・タグを直してから描ける。
 * 成人キャラ(年齢を入れた 18 歳以上)だけを選べる。
 */

interface Catalog {
  kinds: { id: string; label: string }[]
  themes: Record<string, string[]>
  poses: { key: string; label: string; wedgie: boolean }[]
  /** ヌード写真集のポーズ(撮影場所 set ごと) */
  nude_poses?: { key: string; label: string; set: string; kind?: string }[]
  bodies: { id: string; label: string }[]
}

interface Props {
  apiOrigin: string
  /** 物語ができたら呼ぶ(呼び出し側で開く) */
  onCreated: (storyId: number) => void
  disabled?: boolean
}

const MAX_CAST = 4

async function errorDetail(res: Response): Promise<string> {
  const data = await res.json().catch(() => null)
  return (data && typeof data.detail === 'string' ? data.detail : null) ?? `HTTP ${res.status}`
}

function isAdult(c: Character): boolean {
  return c.age != null && c.age >= 18 && !c.is_minor
}

export default function CollectionMaker({ apiOrigin, onCreated, disabled }: Props) {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [characters, setCharacters] = useState<Character[]>([])
  const [kind, setKind] = useState('tease_lingerie')
  const [cast, setCast] = useState<number[]>([])
  const [body, setBody] = useState('')
  const [styleTags, setStyleTags] = useState('')
  const [explicit, setExplicit] = useState(true)
  const [lines, setLines] = useState(true)
  const [themes, setThemes] = useState<Set<string>>(new Set())
  const [poses, setPoses] = useState<Set<string>>(new Set())
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch(`${apiOrigin}/api/collections`).then(r => (r.ok ? r.json() : null)).then(setCatalog).catch(() => setCatalog(null))
    fetch(`${apiOrigin}/api/story/characters`).then(r => (r.ok ? r.json() : [])).then(setCharacters).catch(() => setCharacters([]))
  }, [apiOrigin])

  const tease = kind.startsWith('tease')
  const nude = kind.startsWith('nude')
  const themeNames = catalog?.themes[kind] ?? []
  const adults = characters.filter(isAdult)
  const pickedThemes = themeNames.filter(t => themes.size === 0 || themes.has(t))
  const pickedPoses = (catalog?.poses ?? []).filter(p => poses.size === 0 || poses.has(p.key))
  const nudePoses = (catalog?.nude_poses ?? []).filter(p => (p.kind ?? 'nude') === kind && (themes.size === 0 || themes.has(p.set)))
  const pickedNude = nudePoses.filter(p => poses.size === 0 || poses.has(p.key))
  const nudeSets = new Set(pickedNude.map(p => p.set)).size
  // コマ数の目安: みんなのコマ2枚 + (からかい: ポーズ×人数 / ヌード: 場所の見出し+ポーズ×人数 /
  // コレクション: テーマ×(見出し+人数×正面・背面))
  const panels = cast.length === 0 ? 0
    : 2 + (tease ? pickedPoses.length * cast.length
      : nude ? nudeSets + pickedNude.length * cast.length
        : pickedThemes.length * (1 + cast.length * 2))

  function toggle(set: Set<string>, value: string, update: (next: Set<string>) => void) {
    const next = new Set(set)
    if (next.has(value)) next.delete(value)
    else next.add(value)
    update(next)
  }

  async function create() {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch(`${apiOrigin}/api/collections`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          kind, character_ids: cast, title: title.trim() || null, body: body || null, style_tags: styleTags,
          explicit, lines, themes: [...themes].filter(t => themeNames.includes(t)),
          poses: tease || nude ? [...poses] : [],
        }),
      })
      if (!res.ok) throw new Error(await errorDetail(res))
      const story: { id: number } = await res.json()
      onCreated(story.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!catalog) return <p className="story-intro">コレクションの一覧を読み込めませんでした。</p>
  const off = busy || disabled
  return (
    <div className="collection-maker">
      <div className="story-row">
        <label>
          種類
          <select value={kind} disabled={off} onChange={e => { setKind(e.target.value); setThemes(new Set()); setPoses(new Set()) }}>
            {catalog.kinds.map(k => <option key={k.id} value={k.id}>{k.label}</option>)}
          </select>
        </label>
        <label>
          体つき
          <select value={body} disabled={off} onChange={e => setBody(e.target.value)}>
            <option value="">おまかせ({tease ? 'むっちり' : nude ? 'キャラシートのまま' : 'すらっと'})</option>
            {catalog.bodies.map(b => <option key={b.id} value={b.id}>{b.label}</option>)}
          </select>
        </label>
        <label>
          題名(任意)
          <input value={title} disabled={off} placeholder="空欄なら「〇〇の…コレクション」" onChange={e => setTitle(e.target.value)} />
        </label>
      </div>

      <fieldset className="collection-maker-group">
        <legend>キャラ(成人キャラだけ。{MAX_CAST}人まで、選んだ順に並びます)</legend>
        {adults.length === 0 && <p className="story-intro">年齢を入れた成人キャラがいません(キャラシートで年齢を入れてください)。</p>}
        {adults.map(c => (
          <label key={c.id} className="collection-maker-check">
            <input type="checkbox" checked={cast.includes(c.id)} disabled={off || (!cast.includes(c.id) && cast.length >= MAX_CAST)}
              onChange={() => setCast(prev => (prev.includes(c.id) ? prev.filter(id => id !== c.id) : [...prev, c.id]))} />
            {cast.includes(c.id) && <span className="collection-maker-order">{cast.indexOf(c.id) + 1}</span>}
            {c.name}
          </label>
        ))}
      </fieldset>

      <fieldset className="collection-maker-group">
        <legend>{nude ? '撮影場所(何も選ばなければ全部)' : 'テーマ(衣装。何も選ばなければ全部)'}</legend>
        {themeNames.map(t => (
          <label key={t} className="collection-maker-check">
            <input type="checkbox" checked={themes.has(t)} disabled={off} onChange={() => toggle(themes, t, setThemes)} />
            {t}
          </label>
        ))}
      </fieldset>

      {tease && (
        <fieldset className="collection-maker-group">
          <legend>からかいのポーズ(キャラ全員がそれぞれ演じます。何も選ばなければ全部)</legend>
          {catalog.poses.map(p => (
            <label key={p.key} className="collection-maker-check">
              <input type="checkbox" checked={poses.has(p.key)} disabled={off} onChange={() => toggle(poses, p.key, setPoses)} />
              {p.label}
            </label>
          ))}
        </fieldset>
      )}

      {nude && (
        <fieldset className="collection-maker-group">
          <legend>ポーズ(乳首・性器は描きません。キャラ全員がそれぞれ演じます。何も選ばなければ全部)</legend>
          {nudePoses.map(p => (
            <label key={p.key} className="collection-maker-check">
              <input type="checkbox" checked={poses.has(p.key)} disabled={off} onChange={() => toggle(poses, p.key, setPoses)} />
              {p.set}: {p.label}
            </label>
          ))}
        </fieldset>
      )}

      <div className="story-row">
        <label className="collection-maker-wide">
          画風のタグ(任意。全コマの作画タグに足します)
          <input value={styleTags} disabled={off} placeholder="anime coloring, cel shading, clean lineart"
            onChange={e => setStyleTags(e.target.value)} />
        </label>
      </div>
      <div className="story-row">
        {!nude && <label className="collection-maker-check">
          <input type="checkbox" checked={explicit} disabled={off} onChange={e => setExplicit(e.target.checked)} />
          explicit を足す(食い込み・肌の柔らかさが出ます。乳首・性器などはネガティブで打ち消します)
        </label>}
        <label className="collection-maker-check">
          <input type="checkbox" checked={lines} disabled={off} onChange={e => setLines(e.target.checked)} />
          ひとことセリフを入れる(あとで編集画面で直せます)
        </label>
      </div>
      <div className="story-actions">
        <button type="button" disabled={off || cast.length === 0} onClick={() => void create()}>
          {busy ? '作成中…' : `コレクションを作る${panels ? `(${panels}コマ)` : ''}`}
        </button>
      </div>
      {error && <div className="story-error" role="alert">{error}</div>}
    </div>
  )
}
