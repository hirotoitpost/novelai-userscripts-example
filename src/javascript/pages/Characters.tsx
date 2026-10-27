import { useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import BackButton, { backState } from '../components/BackButton'
import FullscreenViewer from '../components/FullscreenViewer'
import { useLocalStorage } from '../hooks/useLocalStorage'
import { fileUrl, getJson, readErrorDetail, thumbUrl } from '../library'
import './Characters.css'

/**
 * キャラ一覧: 登録したキャラをカードで並べる。参照画像の拡大、キャラ別データセットへの移動、生成したデータセットの画像、
 * 出ている作品(本棚・物語のページ)へのリンク。移った先の「← キャラ一覧」で、ここへ戻れる。
 * 衣装を登録したキャラは、カードで着せ替え(普段の衣装を選ぶ)ができる。
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

/** カードに出す作品の数(多いときは「ほか n 作」) */
const WORKS_SHOWN = 4
const BACK = backState('キャラ一覧')

export default function Characters() {
  const navigate = useNavigate()
  const location = useLocation()
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

  const shown = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean)
    return withStagesGrouped(characters).filter(c => {
      const text = `${c.name} ${c.notes ?? ''} ${c.appearance_tags} ${c.trigger_word ?? ''} ${c.growth_label ?? ''}`.toLowerCase()
      return words.every(w => text.includes(w))
    })
  }, [characters, query])
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

  const datasetCharacter = characters.find(c => c.id === datasetId) ?? null
  const zoomed = zoomIndex !== null && images ? images[zoomIndex] : null

  return (
    <div className="chars-root">
      <header className="chars-header">
        <BackButton className="chars-back" />
        <h1>キャラ一覧</h1>
        <span className="chars-count">{loading ? '読み込み中…' : `${shown.length} / ${characters.length} 人`}</span>
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
          <input className="chars-search" type="search" placeholder="名前・メモ・タグで探す" value={query}
            onChange={e => setQuery(e.target.value)} />
          {!loading && characters.length === 0 && <p className="chars-hint">まだキャラが登録されていません。</p>}
          <div className="chars-grid">
            {shown.map(c => {
              const list = works[String(c.id)] ?? []
              const count = counts[String(c.id)] ?? 0
              return (
                <article key={c.id} className={c.growth_of !== null && nameOf.has(c.growth_of) ? 'chars-card chars-card--stage' : 'chars-card'}>
                  {c.reference_image_path ? (
                    <button type="button" className="chars-ref" onClick={() => setZoomRef(c)} title="参照画像を拡大">
                      <img src={thumbUrl(c.reference_image_path, 360)} alt={`${c.name}の参照画像`} loading="lazy" />
                    </button>
                  ) : (
                    <div className="chars-ref chars-ref--none">参照画像なし</div>
                  )}
                  <div className="chars-body">
                    <h2>
                      {c.name}
                      {c.age !== null && <span className="chars-badge chars-badge--age">{c.age}歳</span>}
                      {c.is_adult && <span className="chars-badge">成人</span>}
                      {c.is_minor && <span className="chars-badge chars-badge--minor">未成年</span>}
                    </h2>
                    {c.growth_of !== null && nameOf.has(c.growth_of) && (
                      <p className="chars-growth">{nameOf.get(c.growth_of)} の成長段階{c.growth_label ? `: ${c.growth_label}` : ''}</p>
                    )}
                    {(stageCount.get(c.id) ?? 0) > 0 && (
                      <p className="chars-growth">成長段階 {stageCount.get(c.id)} 件(この後ろに並んでいます)</p>
                    )}
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
