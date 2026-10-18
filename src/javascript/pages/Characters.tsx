import { useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import BackButton, { backState } from '../components/BackButton'
import FullscreenViewer from '../components/FullscreenViewer'
import { useLocalStorage } from '../hooks/useLocalStorage'
import { fileUrl, getJson, thumbUrl } from '../library'
import './Characters.css'

/**
 * キャラ一覧: 登録したキャラをカードで並べる。参照画像の拡大、キャラ別データセットへの移動、生成したデータセットの画像、
 * 出ている作品(本棚・物語のページ)へのリンク。移った先の「← キャラ一覧」で、ここへ戻れる。
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
    ])
      .then(([list, byCharacter, byCount]) => {
        setCharacters(list)
        setWorks(byCharacter)
        setCounts(byCount)
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
    return characters.filter(c => {
      const text = `${c.name} ${c.notes ?? ''} ${c.appearance_tags} ${c.trigger_word ?? ''}`.toLowerCase()
      return words.every(w => text.includes(w))
    })
  }, [characters, query])

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
                <article key={c.id} className="chars-card">
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
                      {c.is_adult && <span className="chars-badge">成人</span>}
                      {c.is_minor && <span className="chars-badge chars-badge--minor">未成年</span>}
                    </h2>
                    {c.notes && <p className="chars-notes">{c.notes}</p>}
                    <p className="chars-tags">{[c.appearance_tags, c.outfit_tags].filter(Boolean).join(', ')}</p>
                    <div className="chars-actions">
                      <button type="button" className="chars-small"
                        onClick={() => navigate(`/character-dataset?character=${c.id}`, { state: BACK })}>
                        データセットを作る
                      </button>
                      <button type="button" className="chars-small" disabled={count === 0} onClick={() => openDataset(c.id)}>
                        生成したデータ({count}枚)
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
