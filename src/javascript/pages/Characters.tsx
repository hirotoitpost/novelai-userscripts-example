import { useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import BackButton, { backState } from '../components/BackButton'
import BodyBaseBatch from '../components/BodyBaseBatch'
import FullscreenViewer from '../components/FullscreenViewer'
import { useAuth } from '../context/AuthContext'
import { useLocalStorage } from '../hooks/useLocalStorage'
import { fileUrl, getJson, readErrorDetail, thumbUrl } from '../library'
import './Characters.css'

/**
 * キャラ一覧: 登録したキャラをカードで並べる。参照画像の拡大、キャラ別データセットへの移動、生成したデータセットの画像、
 * 出ている作品(本棚・物語のページ)へのリンク。移った先の「← キャラ一覧」で、ここへ戻れる。
 * 衣装を登録したキャラは、カードで着せ替え(普段の衣装を選ぶ)ができる。
 * 「素体を一括で描く」で、カードで選んだキャラの素体を1人ずつ順に描く(BodyBaseBatch.tsx)。
 * キャラが増えても探せるように、並べ替え・絞り込み(年齢区分・参照画像・出ている作品)・小さく表示・成長段階のたたみを
 * 付ける(選んだ表示は端末に残す)。
 */

interface Character {
  id: number
  name: string
  appearance_tags: string
  notes: string | null
  reference_image_path: string | null
  trigger_word: string | null
  outfit_tags: string | null
  is_adult: boolean
  is_minor: boolean
  /** 成長段階: 元のキャラの ID(元のキャラ自身は null)・段階の名前・並べる順 */
  growth_of: number | null
  growth_label: string | null
  growth_order: number | null
  /** 年齢(歳)。入れてあれば年齢区分はこれで決まる */
  age: number | null
  /** 普段の衣装(衣装のタブで登録した服。null なら選んでいない) */
  outfit_id: number | null
}

interface Outfit { id: number; character_id: number; name: string; tags: string }

/** 元のキャラのすぐ後ろに、その成長段階を幼い順に並べる(元のキャラが見つからない段階は、ふつうのキャラと同じ扱い) */
function withStagesGrouped(list: Character[]): Character[] {
  const ids = new Set(list.map(c => c.id))
  const isStage = (c: Character) => c.growth_of !== null && ids.has(c.growth_of)
  const stagesOf = new Map<number, Character[]>()
  for (const c of list) {
    if (isStage(c)) stagesOf.set(c.growth_of!, [...(stagesOf.get(c.growth_of!) ?? []), c])
  }
  const big = Number.MAX_SAFE_INTEGER
  const byOrder = (a: Character, b: Character) =>
    (a.age ?? big) - (b.age ?? big) || (a.growth_order ?? big) - (b.growth_order ?? big)
  return list.filter(c => !isStage(c)).flatMap(c => [c, ...(stagesOf.get(c.id) ?? []).sort(byOrder)])
}

interface Work {
  story_id: number
  title: string
  scenes: number
  volume_no: number | null
  series_id: number | null
  series_title: string | null
}

interface DatasetImage {
  key: string
  path: string
  label: string | null
  created_at: string
  adult: boolean
}

type SortKey = 'id' | 'new' | 'name' | 'age_asc' | 'age_desc'
type Filter3 = 'all' | 'with' | 'without'

interface ViewPrefs {
  sort: SortKey
  age: 'all' | 'adult' | 'minor' | 'unset'
  ref: Filter3
  works: Filter3
  /** カードを参照画像・名前だけにする(カードごとに「詳しく」で開ける) */
  compact: boolean
  /** 成長段階を、元のキャラのカードで開くまで隠す */
  foldStages: boolean
}

const DEFAULT_VIEW: ViewPrefs = { sort: 'id', age: 'all', ref: 'all', works: 'all', compact: false, foldStages: true }

const SORT_LABELS: Record<SortKey, string> = {
  id: '登録順', new: '新しい順', name: '名前順', age_asc: '年齢の若い順', age_desc: '年齢の上の順',
}

/** 年齢区分(年齢を入れてあれば年齢で決まる。無ければ成人・未成年の印) */
function ageClass(c: Character): 'adult' | 'minor' | 'unset' {
  if (c.age !== null) return c.age >= 18 ? 'adult' : 'minor'
  if (c.is_adult) return 'adult'
  if (c.is_minor) return 'minor'
  return 'unset'
}

function compareBy(sort: SortKey): (a: Character, b: Character) => number {
  const big = Number.MAX_SAFE_INTEGER
  switch (sort) {
    case 'new': return (a, b) => b.id - a.id
    case 'name': return (a, b) => a.name.localeCompare(b.name, 'ja') || a.id - b.id
    // 年齢の無いキャラは後ろ
    case 'age_asc': return (a, b) => (a.age ?? big) - (b.age ?? big) || a.id - b.id
    case 'age_desc': return (a, b) => (b.age ?? -1) - (a.age ?? -1) || a.id - b.id
    default: return (a, b) => a.id - b.id
  }
}

/** カードに出す作品の数(多いときは「ほか n 作」) */
const WORKS_SHOWN = 4
const BACK = backState('キャラ一覧')

export default function Characters() {
  const navigate = useNavigate()
  const location = useLocation()
  const { token } = useAuth()
  const [searchParams, setSearchParams] = useSearchParams()
  const datasetId = Number(searchParams.get('dataset')) || null

  const [characters, setCharacters] = useState<Character[]>([])
  const [works, setWorks] = useState<Record<string, Work[]>>({})
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [outfits, setOutfits] = useState<Outfit[]>([])
  const [wearing, setWearing] = useState<number | null>(null)
  const [query, setQuery] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  // 拡大表示: 参照画像(1枚)か、データセットの何枚目か
  const [zoomRef, setZoomRef] = useState<Character | null>(null)
  const [images, setImages] = useState<DatasetImage[] | null>(null)
  const [zoomIndex, setZoomIndex] = useState<number | null>(null)
  const [blurAdult, setBlurAdult] = useLocalStorage('nai_library_blur_adult', true)
  // 素体を一括で描く: 選ぶ表示にしているか・選んだキャラ
  const [batchMode, setBatchMode] = useState(false)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [storedView, setStoredView] = useLocalStorage<Partial<ViewPrefs>>('nai_chars_view', DEFAULT_VIEW)
  const view: ViewPrefs = { ...DEFAULT_VIEW, ...storedView }
  const setView = (patch: Partial<ViewPrefs>) => setStoredView({ ...view, ...patch })
  // 成長段階を開いた元のキャラ・小さく表示で開いたカード
  const [openStages, setOpenStages] = useState<Set<number>>(new Set())
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  useEffect(() => {
    Promise.all([
      getJson<Character[]>('/api/story/characters'),
      getJson<Record<string, Work[]>>('/api/story/character-works'),
      getJson<Record<string, number>>('/api/library/character-datasets'),
      getJson<Outfit[]>('/api/wardrobe/outfits'),
    ])
      .then(([list, byCharacter, byCount, allOutfits]) => {
        setCharacters(list)
        setWorks(byCharacter)
        setCounts(byCount)
        setOutfits(allOutfits)
      })
      .catch(e => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    setImages(null)
    setZoomIndex(null)
    if (datasetId === null) return
    getJson<DatasetImage[]>(`/api/library/character-datasets/${datasetId}`)
      .then(setImages)
      .catch(e => setError(e instanceof Error ? e.message : String(e)))
  }, [datasetId])

  /**
   * 表示するカード: 元のキャラを並べ替え、その後ろに成長段階(幼い順)。成長段階は、たたむ設定なら元のキャラで開いたとき
   * だけ出す(元のキャラが条件に合わず、成長段階だけが合うときは、その段階を出す)。
   */
  const { shown, folded } = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean)
    const matches = (c: Character) => {
      const text = `${c.name} ${c.notes ?? ''} ${c.appearance_tags} ${c.trigger_word ?? ''} ${c.growth_label ?? ''}`.toLowerCase()
      if (!words.every(w => text.includes(w))) return false
      if (view.age !== 'all' && ageClass(c) !== view.age) return false
      if (view.ref !== 'all' && Boolean(c.reference_image_path) !== (view.ref === 'with')) return false
      const inWorks = (works[String(c.id)] ?? []).length > 0
      return view.works === 'all' || inWorks === (view.works === 'with')
    }
    const grouped = withStagesGrouped(characters)
    const ids = new Set(characters.map(c => c.id))
    const isStage = (c: Character) => c.growth_of !== null && ids.has(c.growth_of)
    const parents = grouped.filter(c => !isStage(c)).sort(compareBy(view.sort))
    const list: Character[] = []
    let hidden = 0
    for (const parent of parents) {
      const stages = grouped.filter(c => c.growth_of === parent.id && isStage(c) && matches(c))
      const parentShown = matches(parent)
      const open = !view.foldStages || openStages.has(parent.id) || !parentShown
      if (parentShown) list.push(parent)
      if (open) list.push(...stages)
      else hidden += stages.length
    }
    return { shown: list, folded: hidden }
  }, [characters, works, query, view.age, view.ref, view.works, view.sort, view.foldStages, openStages])
  const filtering = view.age !== 'all' || view.ref !== 'all' || view.works !== 'all'
  const nameOf = useMemo(() => new Map(characters.map(c => [c.id, c.name])), [characters])
  const outfitsOf = (id: number) => outfits.filter(o => o.character_id === id)

  // 着せ替え: 普段の衣装を選ぶ(キャラシートの普段の服装が、その衣装のタグになる)
  async function wear(c: Character, outfitId: number) {
    setWearing(c.id)
    setError(null)
    try {
      const res = await fetch(`/api/wardrobe/characters/${c.id}/outfit`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ outfit_id: outfitId }),
      })
      if (!res.ok) throw new Error(await readErrorDetail(res))
      const data: { outfit_id: number | null; outfit_tags: string } = await res.json()
      setCharacters(list => list.map(x => (x.id === c.id ? { ...x, outfit_id: data.outfit_id, outfit_tags: data.outfit_tags } : x)))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setWearing(null)
    }
  }
  const stageCount = useMemo(() => {
    const counts = new Map<number, number>()
    for (const c of characters) if (c.growth_of !== null) counts.set(c.growth_of, (counts.get(c.growth_of) ?? 0) + 1)
    return counts
  }, [characters])

  const openDataset = (id: number) => setSearchParams({ dataset: String(id) }, { state: { fromCards: true } })
  const closeDataset = () => {
    // カードから開いたなら履歴を1つ戻す(ブラウザの戻ると同じ)。URL を直接開いたときはカードに置き換える
    if ((location.state as { fromCards?: boolean } | null)?.fromCards) navigate(-1)
    else setSearchParams({}, { replace: true })
  }

  function toggleIn(setter: (f: (prev: Set<number>) => Set<number>) => void, id: number) {
    setter(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleSelected(id: number) {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }
  // 描く順は一覧の並び(元のキャラ → 成長段階)。検索で隠れたキャラも、選んだままなら描く
  const batchTargets = withStagesGrouped(characters).filter(c => selected.has(c.id)).map(c => ({ id: c.id, name: c.name }))

  const datasetCharacter = characters.find(c => c.id === datasetId) ?? null
  const zoomed = zoomIndex !== null && images ? images[zoomIndex] : null

  return (
    <div className="chars-root">
      <header className="chars-header">
        <BackButton className="chars-back" />
        <h1>キャラ一覧</h1>
        <span className="chars-count">
          {loading ? '読み込み中…' : `${shown.length} / ${characters.length} 人`}
          {folded > 0 && `(成長段階 ${folded} 件をたたんでいます)`}
        </span>
        {datasetId === null && (
          <button type="button" className={batchMode ? 'chars-small chars-small--on' : 'chars-small'} aria-pressed={batchMode}
            onClick={() => setBatchMode(on => !on)}>
            素体を一括で描く
          </button>
        )}
      </header>
      {error && <p className="chars-error" role="alert">{error}</p>}

      {datasetId !== null ? (
        <section className="chars-dataset">
          <div className="chars-dataset-head">
            <button type="button" className="chars-back" onClick={closeDataset}>← キャラ一覧</button>
            <h2>{datasetCharacter?.name ?? `キャラ ${datasetId}`} の生成したデータ</h2>
            <span className="chars-count">{images ? `${images.length} 枚` : '読み込み中…'}</span>
            {images?.some(i => i.adult) && (
              <label className="chars-check">
                <input type="checkbox" checked={blurAdult} onChange={e => setBlurAdult(e.target.checked)} />
                成人向けをぼかす
              </label>
            )}
            <button type="button" className="chars-small"
              onClick={() => navigate(`/character-dataset?character=${datasetId}`, { state: BACK })}>データセットを作る</button>
          </div>
          {images && images.length === 0 && <p className="chars-hint">まだ生成したデータはありません。</p>}
          <div className="chars-grid-images">
            {images?.map((image, index) => (
              <button type="button" key={image.key} className="chars-thumb" onClick={() => setZoomIndex(index)}
                title={image.label ?? ''}>
                <img src={thumbUrl(image.path, 240)} alt={image.label ?? ''} loading="lazy"
                  className={image.adult && blurAdult ? 'chars-blur' : undefined} />
              </button>
            ))}
          </div>
        </section>
      ) : (
        <>
          <div className="chars-toolbar">
            <input className="chars-search" type="search" placeholder="名前・メモ・タグで探す" value={query}
              onChange={e => setQuery(e.target.value)} />
            <label className="chars-field">
              並べ替え
              <select value={view.sort} onChange={e => setView({ sort: e.target.value as SortKey })}>
                {(Object.keys(SORT_LABELS) as SortKey[]).map(k => <option key={k} value={k}>{SORT_LABELS[k]}</option>)}
              </select>
            </label>
            <label className="chars-field">
              年齢
              <select value={view.age} onChange={e => setView({ age: e.target.value as ViewPrefs['age'] })}>
                <option value="all">すべて</option>
                <option value="adult">成人</option>
                <option value="minor">未成年</option>
                <option value="unset">年齢区分なし</option>
              </select>
            </label>
            <label className="chars-field">
              参照画像
              <select value={view.ref} onChange={e => setView({ ref: e.target.value as Filter3 })}>
                <option value="all">すべて</option>
                <option value="with">あり</option>
                <option value="without">なし</option>
              </select>
            </label>
            <label className="chars-field">
              出ている作品
              <select value={view.works} onChange={e => setView({ works: e.target.value as Filter3 })}>
                <option value="all">すべて</option>
                <option value="with">あり</option>
                <option value="without">なし</option>
              </select>
            </label>
            <label className="chars-check">
              <input type="checkbox" checked={view.compact} onChange={e => { setView({ compact: e.target.checked }); setExpanded(new Set()) }} />
              小さく表示
            </label>
            <label className="chars-check">
              <input type="checkbox" checked={view.foldStages} onChange={e => setView({ foldStages: e.target.checked })} />
              成長段階をたたむ
            </label>
            {filtering && (
              <button type="button" className="chars-small" onClick={() => setView({ age: 'all', ref: 'all', works: 'all' })}>
                絞り込みを外す
              </button>
            )}
          </div>
          {batchMode && (
            <>
              <BodyBaseBatch characters={batchTargets} token={token} />
              <div className="chars-select-bar">
                <span className="chars-count">{selected.size} 人を選んでいます</span>
                <button type="button" className="chars-small"
                  onClick={() => setSelected(prev => new Set([...prev, ...shown.map(c => c.id)]))}>
                  表示中のキャラをすべて選ぶ
                </button>
                <button type="button" className="chars-small" disabled={selected.size === 0} onClick={() => setSelected(new Set())}>
                  選択を外す
                </button>
              </div>
            </>
          )}
          {!loading && characters.length === 0 && <p className="chars-hint">まだキャラが登録されていません。</p>}
          {!loading && characters.length > 0 && shown.length === 0 && <p className="chars-hint">条件に合うキャラがいません。</p>}
          <div className={view.compact ? 'chars-grid chars-grid--compact' : 'chars-grid'}>
            {shown.map(c => {
              const list = works[String(c.id)] ?? []
              const count = counts[String(c.id)] ?? 0
              const detailed = !view.compact || expanded.has(c.id)
              const stages = stageCount.get(c.id) ?? 0
              return (
                <article key={c.id} className={[
                  'chars-card',
                  c.growth_of !== null && nameOf.has(c.growth_of) ? 'chars-card--stage' : '',
                  batchMode && selected.has(c.id) ? 'chars-card--chosen' : '',
                  detailed ? '' : 'chars-card--compact',
                ].filter(Boolean).join(' ')}>
                  {c.reference_image_path ? (
                    <button type="button" className="chars-ref" onClick={() => setZoomRef(c)} title="参照画像を拡大">
                      <img src={thumbUrl(c.reference_image_path, 360)} alt={`${c.name}の参照画像`} loading="lazy" />
                    </button>
                  ) : (
                    <div className="chars-ref chars-ref--none">参照画像なし</div>
                  )}
                  <div className="chars-body">
                    {batchMode && (
                      <label className="chars-check chars-pick">
                        <input type="checkbox" checked={selected.has(c.id)} onChange={() => toggleSelected(c.id)} />
                        選ぶ
                      </label>
                    )}
                    <h2>
                      {c.name}
                      {c.age !== null && <span className="chars-badge chars-badge--age">{c.age}歳</span>}
                      {c.is_adult && <span className="chars-badge">成人</span>}
                      {c.is_minor && <span className="chars-badge chars-badge--minor">未成年</span>}
                    </h2>
                    {c.growth_of !== null && nameOf.has(c.growth_of) && (
                      <p className="chars-growth">{nameOf.get(c.growth_of)} の成長段階{c.growth_label ? `: ${c.growth_label}` : ''}</p>
                    )}
                    {stages > 0 && (view.foldStages ? (
                      <button type="button" className="chars-small chars-stages" aria-expanded={openStages.has(c.id)}
                        onClick={() => toggleIn(setOpenStages, c.id)}>
                        {openStages.has(c.id) ? `成長段階 ${stages} 件をたたむ` : `成長段階 ${stages} 件を表示`}
                      </button>
                    ) : (
                      <p className="chars-growth">成長段階 {stages} 件(この後ろに並んでいます)</p>
                    ))}
                    {view.compact && (
                      <button type="button" className="chars-small chars-stages" aria-expanded={detailed}
                        onClick={() => toggleIn(setExpanded, c.id)}>
                        {detailed ? '小さくする' : '詳しく'}
                      </button>
                    )}
                    {detailed && (
                      <>
                        {c.notes && <p className="chars-notes">{c.notes}</p>}
                        <p className="chars-tags">{[c.appearance_tags, c.outfit_tags].filter(Boolean).join(', ')}</p>
                        {outfitsOf(c.id).length > 0 && (
                          <label className="chars-outfit">
                            着せ替え
                            <select value={c.outfit_id ?? ''} disabled={wearing === c.id} onChange={e => void wear(c, Number(e.target.value))}>
                              {c.outfit_id === null && <option value="">(選んでいない)</option>}
                              {outfitsOf(c.id).map(o => <option key={o.id} value={o.id} title={o.tags}>{o.name}</option>)}
                            </select>
                          </label>
                        )}
                        <div className="chars-actions">
                          <button type="button" className="chars-small"
                            onClick={() => navigate(`/character-dataset?character=${c.id}`, { state: BACK })}>
                            データセットを作る
                          </button>
                          <button type="button" className="chars-small"
                            onClick={() => navigate(`/character-dataset?character=${c.id}#faces`, { state: BACK })}>
                            顔・表情シート
                          </button>
                          <button type="button" className="chars-small"
                            onClick={() => navigate(`/character-dataset?character=${c.id}#body`, { state: BACK })}>
                            素体
                          </button>
                          <button type="button" className="chars-small"
                            onClick={() => navigate(`/character-dataset?character=${c.id}#wardrobe`, { state: BACK })}>
                            衣装
                          </button>
                          <button type="button" className="chars-small" disabled={count === 0} onClick={() => openDataset(c.id)}>
                            生成したデータ({count}枚)
                          </button>
                          <button type="button" className="chars-small"
                            onClick={() => navigate(`/character-images?character=${c.id}`, { state: BACK })}>
                            生成した画像・参照画像の選び直し
                          </button>
                        </div>
                        <div className="chars-works">
                          <span className="chars-label">出ている作品{list.length > 0 && `(${list.length})`}</span>
                          {list.length === 0 && <span className="chars-hint">まだありません</span>}
                          <ul>
                            {list.slice(0, WORKS_SHOWN).map(w => (
                              <li key={w.story_id}>
                                <span className="chars-work-title">
                                  {w.series_title && w.volume_no ? `${w.series_title} ${w.volume_no}巻: ` : ''}{w.title}
                                </span>
                                <span className="chars-work-links">
                                  <button type="button" onClick={() => navigate(`/bookshelf?book=${w.story_id}`, { state: BACK })}>読む</button>
                                  <button type="button" onClick={() => navigate(`/story?story=${w.story_id}`, { state: BACK })}>物語</button>
                                </span>
                              </li>
                            ))}
                          </ul>
                          {list.length > WORKS_SHOWN && <span className="chars-hint">ほか {list.length - WORKS_SHOWN} 作</span>}
                        </div>
                      </>
                    )}
                  </div>
                </article>
              )
            })}
          </div>
        </>
      )}

      {zoomRef?.reference_image_path && (
        <FullscreenViewer src={fileUrl(zoomRef.reference_image_path)} alt={`${zoomRef.name}の参照画像`}
          onClose={() => setZoomRef(null)} />
      )}
      {zoomed && images && zoomIndex !== null && (
        <FullscreenViewer
          src={fileUrl(zoomed.path)}
          alt={zoomed.label ?? ''}
          counter={`${zoomIndex + 1} / ${images.length}`}
          onPrev={zoomIndex > 0 ? () => setZoomIndex(zoomIndex - 1) : undefined}
          onNext={zoomIndex < images.length - 1 ? () => setZoomIndex(zoomIndex + 1) : undefined}
          onClose={() => setZoomIndex(null)}
        />
      )}
    </div>
  )
}
