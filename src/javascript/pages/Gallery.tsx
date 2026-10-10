import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useLocalStorage } from '../hooks/useLocalStorage'
import {
  deleteImages,
  downloadFile,
  fileUrl,
  formatDate,
  getJson,
  Rating,
  RATINGS,
  setAdult,
  setBookmark,
  thumbUrl,
} from '../library'
import FullscreenViewer from '../components/FullscreenViewer'
import './Gallery.css'

type Source = 'generate' | 'panel' | 'illustration' | 'dataset'

interface GalleryImage {
  key: string
  source: Source
  path: string
  created_at: string
  width: number | null
  height: number | null
  prompt: string
  seed: number | null
  model: string | null
  story_id: number | null
  story_title: string | null
  index: number | null
  label: string | null
  bookmarked: boolean
  /** 成人向けか(手動指定があればそれ、無ければ自動判定) */
  adult: boolean
  adult_auto: boolean
  adult_manual: boolean | null
}

interface SimilarImage {
  image: GalleryImage
  score: number
  reasons: string[]
}

interface GalleryResponse {
  items: GalleryImage[]
  total: number
  stories: { id: number; title: string | null }[]
}

const SOURCES: { id: Source; label: string }[] = [
  { id: 'generate', label: '画像生成' },
  { id: 'panel', label: '漫画のコマ' },
  { id: 'illustration', label: '挿絵ページ' },
  { id: 'dataset', label: 'データセット' },
]
const SOURCE_LABELS: Record<Source, string> = {
  generate: '画像生成', panel: '漫画のコマ', illustration: '挿絵ページ', dataset: 'データセット',
}
const PAGE_SIZE = 60
// スワイプとみなす横方向の移動量(px)
const SWIPE_THRESHOLD = 50

function describe(item: GalleryImage): string {
  if (item.source === 'panel' && item.index !== null) {
    return `シーン${item.index + 1}${item.label ? `: ${item.label}` : ''}`
  }
  if (item.source === 'illustration' && item.index !== null) return `ページ${item.index + 1}`
  // キャラ別データセットは「キャラ名 / generated」のようにキャラと保存先を出す
  if (item.source === 'dataset' && item.label) return item.label
  return ''
}

export default function Gallery() {
  const navigate = useNavigate()

  // 絞り込み・並び順は端末ごとに覚えておく
  const [sources, setSources] = useLocalStorage<Source[]>('nai_gallery_sources', [])
  const [storyId, setStoryId] = useLocalStorage<number | null>('nai_gallery_story', null)
  const [onlyBookmarked, setOnlyBookmarked] = useLocalStorage('nai_gallery_bookmarked', false)
  const [sort, setSort] = useLocalStorage<'new' | 'old'>('nai_gallery_sort', 'new')
  const [rating, setRating] = useLocalStorage<Rating>('nai_gallery_rating', 'all')
  // 成人向けの画像をぼかすか(端末ごと)。人前で開くこともあるので既定はぼかす
  const [blurAdult, setBlurAdult] = useLocalStorage('nai_library_blur_adult', true)
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')

  const [items, setItems] = useState<GalleryImage[]>([])
  const [total, setTotal] = useState(0)
  const [stories, setStories] = useState<GalleryResponse['stories']>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [viewing, setViewing] = useState<number | null>(null)
  // 見ている画像を全画面で出しているか
  const [fullscreen, setFullscreen] = useState(false)
  const [selecting, setSelecting] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [downloading, setDownloading] = useState(false)
  const [copied, setCopied] = useState(false)
  // ぼかしを外して見た画像(このページを開いている間だけ)
  const [revealed, setRevealed] = useState<Set<string>>(new Set())
  const [deleting, setDeleting] = useState(false)
  const [similar, setSimilar] = useState<SimilarImage[] | null>(null)

  const sentinel = useRef<HTMLDivElement | null>(null)
  const touchStart = useRef<number | null>(null)
  // 古い条件の応答が後から届いて一覧を上書きしないよう、条件ごとに番号を振る
  const requestId = useRef(0)

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query.trim()), 300)
    return () => clearTimeout(timer)
  }, [query])

  const filterQuery = useMemo(() => {
    const params = new URLSearchParams({ sort, limit: String(PAGE_SIZE) })
    if (sources.length > 0) params.set('source', sources.join(','))
    if (storyId !== null) params.set('story_id', String(storyId))
    if (onlyBookmarked) params.set('bookmarked', 'true')
    if (rating !== 'all') params.set('rating', rating)
    if (debouncedQuery) params.set('q', debouncedQuery)
    return params.toString()
  }, [sources, storyId, onlyBookmarked, rating, sort, debouncedQuery])

  const load = useCallback(
    async (offset: number) => {
      const id = ++requestId.current
      setLoading(true)
      setError(null)
      try {
        const data = await getJson<GalleryResponse>(`/api/library/images?${filterQuery}&offset=${offset}`)
        if (id !== requestId.current) return
        setItems(prev => {
          if (offset === 0) return data.items
          const loaded = new Set(prev.map(i => i.key))
          return [...prev, ...data.items.filter(i => !loaded.has(i.key))]
        })
        setTotal(data.total)
        setStories(data.stories)
      } catch (e) {
        if (id === requestId.current) setError(e instanceof Error ? e.message : String(e))
      } finally {
        if (id === requestId.current) setLoading(false)
      }
    },
    [filterQuery],
  )

  useEffect(() => {
    setItems([])
    void load(0)
  }, [load])

  // 一覧の末尾が見えたら続きを読む
  const hasMore = items.length < total
  useEffect(() => {
    const target = sentinel.current
    if (!target || !hasMore) return
    const observer = new IntersectionObserver(entries => {
      if (entries.some(e => e.isIntersecting) && !loading) void load(items.length)
    }, { rootMargin: '600px' })
    observer.observe(target)
    return () => observer.disconnect()
  }, [hasMore, loading, items.length, load])

  const current = viewing !== null ? items[viewing] ?? null : null
  const currentKey = current?.key ?? null

  // 見ている画像に似ている画像(共通のタグ・登場人物、同じ物語、生成日時から計算)
  useEffect(() => {
    if (currentKey === null) return
    setSimilar(null)
    let cancelled = false
    getJson<SimilarImage[]>(`/api/library/images/similar?key=${encodeURIComponent(currentKey)}&rating=${rating}`)
      .then(data => { if (!cancelled) setSimilar(data) })
      .catch(() => { if (!cancelled) setSimilar([]) })
    return () => { cancelled = true }
  }, [currentKey, rating])

  /** 似ている画像を開く。一覧に読み込んでいなければ、今の画像の次に差し込む(前後の移動が続けられる)。 */
  function openSimilar(image: GalleryImage) {
    const index = items.findIndex(i => i.key === image.key)
    if (index >= 0) {
      setViewing(index)
    } else if (viewing !== null) {
      setItems(prev => [...prev.slice(0, viewing + 1), image, ...prev.slice(viewing + 1)])
      setViewing(viewing + 1)
    }
    setCopied(false)
  }
  // 成人向けだけを表示しているときは、自分で選んで見ているのでぼかさない
  const blurs = (item: GalleryImage) => item.adult && blurAdult && rating !== 'adult'

  const step = useCallback(
    (delta: number) => {
      setViewing(v => {
        if (v === null) return v
        const next = v + delta
        if (next < 0 || next >= items.length) return v
        // 最後の数枚まで来たら続きを先読みする
        if (next >= items.length - 3 && hasMore && !loading) void load(items.length)
        return next
      })
      setCopied(false)
    },
    [items.length, hasMore, loading, load],
  )

  useEffect(() => {
    // 全画面のときは、全画面の表示がキーを受ける
    if (viewing === null || fullscreen) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'ArrowRight') step(1)
      else if (e.key === 'ArrowLeft') step(-1)
      else if (e.key === 'Escape') setViewing(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [viewing, step, fullscreen])

  function toggleSource(id: Source) {
    setSources(sources.includes(id) ? sources.filter(s => s !== id) : [...sources, id])
  }

  async function toggleBookmark(item: GalleryImage) {
    const next = !item.bookmarked
    // 先に表示を変え、失敗したら戻す
    setItems(prev => prev.map(i => (i.key === item.key ? { ...i, bookmarked: next } : i)))
    try {
      await setBookmark('image', item.key, next)
      if (onlyBookmarked && !next) {
        setItems(prev => prev.filter(i => i.key !== item.key))
        setTotal(t => t - 1)
        setViewing(null)
      }
    } catch (e) {
      setItems(prev => prev.map(i => (i.key === item.key ? { ...i, bookmarked: !next } : i)))
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  /** 一覧から画像を外す(削除したときや、絞り込みの条件から外れたとき)。 */
  function removeItems(keys: string[]) {
    const gone = new Set(keys)
    const viewingKey = viewing !== null ? items[viewing]?.key : undefined
    const rest = items.filter(i => !gone.has(i.key))
    setItems(rest)
    setTotal(t => Math.max(0, t - (items.length - rest.length)))
    setSelected(prev => new Set([...prev].filter(k => !gone.has(k))))
    if (viewing !== null) {
      // 見ていた画像が消えたら、同じ位置(次の画像)を表示する。無ければ閉じる
      if (viewingKey && gone.has(viewingKey)) setViewing(rest.length > 0 ? Math.min(viewing, rest.length - 1) : null)
      else setViewing(rest.findIndex(i => i.key === viewingKey))
    }
  }

  async function toggleAdult(item: GalleryImage) {
    const next = !item.adult
    // 自動判定と同じになるなら手動指定は外す(後でタグが変わったときに自動判定に従えるように)
    const manual = next === item.adult_auto ? null : next
    try {
      await setAdult('image', item.key, manual)
      setItems(prev => prev.map(i => (i.key === item.key ? { ...i, adult: next, adult_manual: manual } : i)))
      if ((rating === 'adult' && !next) || (rating === 'general' && next)) removeItems([item.key])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function deleteKeys(keys: string[]) {
    const message = keys.length === 1
      ? 'この画像を削除します。元に戻せません。よろしいですか？'
      : `${keys.length} 枚の画像を削除します。元に戻せません。よろしいですか？`
    const datasetCount = items.filter(i => keys.includes(i.key) && i.source === 'dataset').length
    const notes = ['(漫画のコマを消しても、合成済みのページはそのまま残ります)']
    if (datasetCount > 0) {
      notes.push(`※ データセットの画像 ${datasetCount} 枚は、学習用フォルダからも画像とキャプションが削除されます。`)
    }
    if (!window.confirm(`${message}\n${notes.join('\n')}`)) return
    setDeleting(true)
    setError(null)
    try {
      await deleteImages(keys)
      removeItems(keys)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setDeleting(false)
    }
  }

  function onTileClick(index: number) {
    const item = items[index]
    if (!selecting) {
      setViewing(index)
      setCopied(false)
      return
    }
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(item.key)) next.delete(item.key)
      else next.add(item.key)
      return next
    })
  }

  async function downloadSelected() {
    setDownloading(true)
    setError(null)
    try {
      await downloadFile('/api/library/images/download', 'gallery.zip', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keys: [...selected] }),
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setDownloading(false)
    }
  }

  async function downloadOne(item: GalleryImage) {
    try {
      await downloadFile(
        `/api/library/file?path=${encodeURIComponent(item.path)}&download=true`,
        `${item.key.replace(/:/g, '_')}.png`,
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function copyPrompt(prompt: string) {
    try {
      await navigator.clipboard.writeText(prompt)
      setCopied(true)
    } catch {
      setError('コピーできませんでした(http では使えないブラウザがあります)。')
    }
  }

  function exitSelecting() {
    setSelecting(false)
    setSelected(new Set())
  }

  const filtered =
    sources.length > 0 || storyId !== null || onlyBookmarked || rating !== 'all' || debouncedQuery !== ''

  return (
    <div className="gallery-root">
      <header className="gallery-header">
        <button type="button" className="gallery-back" onClick={() => navigate('/')}>← ホーム</button>
        <h1>ギャラリー</h1>
        <span className="gallery-count">{total.toLocaleString()} 枚</span>
        <button
          type="button"
          className={`gallery-select-toggle${selecting ? ' is-active' : ''}`}
          onClick={() => (selecting ? exitSelecting() : setSelecting(true))}
        >
          {selecting ? '完了' : '選択'}
        </button>
      </header>

      <div className="gallery-toolbar">
        <input
          type="search"
          className="gallery-search"
          placeholder="プロンプト・物語名で検索"
          value={query}
          onChange={e => setQuery(e.target.value)}
        />
        <div className="gallery-chips" role="group" aria-label="種類">
          {SOURCES.map(s => (
            <button
              key={s.id}
              type="button"
              className={`gallery-chip${sources.includes(s.id) ? ' is-on' : ''}`}
              aria-pressed={sources.includes(s.id)}
              onClick={() => toggleSource(s.id)}
            >
              {s.label}
            </button>
          ))}
          <button
            type="button"
            className={`gallery-chip gallery-chip--star${onlyBookmarked ? ' is-on' : ''}`}
            aria-pressed={onlyBookmarked}
            onClick={() => setOnlyBookmarked(!onlyBookmarked)}
          >
            ★ ブックマーク
          </button>
        </div>
        <div className="gallery-selects">
          <select
            value={storyId ?? ''}
            onChange={e => setStoryId(e.target.value ? Number(e.target.value) : null)}
            aria-label="物語で絞り込む"
          >
            <option value="">すべての物語</option>
            {stories.map(s => (
              <option key={s.id} value={s.id}>{s.title ?? `物語${s.id}`}</option>
            ))}
          </select>
          <select value={rating} onChange={e => setRating(e.target.value as Rating)} aria-label="成人向けで絞り込む">
            {RATINGS.map(r => <option key={r.id} value={r.id}>{r.label}</option>)}
          </select>
          <select value={sort} onChange={e => setSort(e.target.value as 'new' | 'old')} aria-label="並び順">
            <option value="new">新しい順</option>
            <option value="old">古い順</option>
          </select>
          <label className="gallery-blur-toggle">
            <input type="checkbox" checked={blurAdult} onChange={e => setBlurAdult(e.target.checked)} />
            成人向けをぼかす
          </label>
          {filtered && (
            <button
              type="button"
              className="gallery-clear"
              onClick={() => {
                setSources([])
                setStoryId(null)
                setOnlyBookmarked(false)
                setRating('all')
                setQuery('')
              }}
            >
              条件をクリア
            </button>
          )}
        </div>
      </div>

      {error && <div className="gallery-error">{error}</div>}

      {!loading && items.length === 0 && !error && (
        <p className="gallery-empty">{filtered ? '条件に合う画像がありません。' : '画像がまだありません。'}</p>
      )}

      <ul className="gallery-grid">
        {items.map((item, index) => {
          const isSelected = selected.has(item.key)
          const blurred = blurs(item)
          return (
            <li
              key={item.key}
              className={`gallery-tile${isSelected ? ' is-selected' : ''}${blurred ? ' is-blurred' : ''}`}
            >
              <button type="button" className="gallery-tile-button" onClick={() => onTileClick(index)}>
                <img
                  src={thumbUrl(item.path)}
                  loading="lazy"
                  alt={describe(item) || SOURCE_LABELS[item.source]}
                  style={item.width && item.height ? { aspectRatio: `${item.width} / ${item.height}` } : undefined}
                />
                {selecting && <span className="gallery-check" aria-hidden>{isSelected ? '✓' : ''}</span>}
                {item.bookmarked && !selecting && <span className="gallery-star" aria-label="ブックマーク済み">★</span>}
                {item.adult && <span className="gallery-r18">R18</span>}
              </button>
            </li>
          )
        })}
      </ul>
      <div ref={sentinel} className="gallery-sentinel">
        {loading && <span>読み込み中…</span>}
      </div>

      {selecting && (
        <div className="gallery-selectbar">
          <span>{selected.size} 枚選択</span>
          <button type="button" onClick={() => setSelected(new Set(items.map(i => i.key)))}>
            表示中をすべて選択
          </button>
          <button type="button" onClick={() => setSelected(new Set())} disabled={selected.size === 0}>
            解除
          </button>
          <button
            type="button"
            className="gallery-danger"
            onClick={() => void deleteKeys([...selected])}
            disabled={selected.size === 0 || deleting}
          >
            {deleting ? '削除中…' : '削除'}
          </button>
          <button
            type="button"
            className="gallery-primary"
            onClick={() => void downloadSelected()}
            disabled={selected.size === 0 || downloading}
          >
            {downloading ? '作成中…' : 'zipでダウンロード'}
          </button>
        </div>
      )}

      {current && viewing !== null && fullscreen && (
        <FullscreenViewer
          src={fileUrl(current.path)}
          alt={describe(current) || SOURCE_LABELS[current.source]}
          counter={`${viewing + 1} / ${total}`}
          onPrev={viewing > 0 ? () => step(-1) : undefined}
          onNext={viewing < items.length - 1 || hasMore ? () => step(1) : undefined}
          onClose={() => setFullscreen(false)}
        />
      )}

      {current && viewing !== null && (
        <div className="gallery-viewer" role="dialog" aria-modal="true" aria-label="画像を見る">
          <div className="gallery-viewer-top">
            <span>{viewing + 1} / {total}</span>
            <button
              type="button"
              className="gallery-viewer-full"
              onClick={() => setFullscreen(true)}
              disabled={blurs(current) && !revealed.has(current.key)}
              title={blurs(current) && !revealed.has(current.key) ? '成人向けの画像は、表示してから全画面にできます' : undefined}
            >
              ⛶ 全画面
            </button>
            <button type="button" className="gallery-viewer-close" onClick={() => setViewing(null)} aria-label="閉じる">
              ✕
            </button>
          </div>
          <div
            className="gallery-viewer-stage"
            onTouchStart={e => { touchStart.current = e.touches[0].clientX }}
            onTouchEnd={e => {
              if (touchStart.current === null) return
              const dx = e.changedTouches[0].clientX - touchStart.current
              touchStart.current = null
              if (Math.abs(dx) > SWIPE_THRESHOLD) step(dx < 0 ? 1 : -1)
            }}
          >
            <button type="button" className="gallery-nav gallery-nav--prev" onClick={() => step(-1)}
              disabled={viewing === 0} aria-label="前の画像">‹</button>
            <img
              key={current.key}
              src={fileUrl(current.path)}
              alt={describe(current) || SOURCE_LABELS[current.source]}
              className={blurs(current) && !revealed.has(current.key) ? 'is-blurred' : undefined}
            />
            {blurs(current) && !revealed.has(current.key) && (
              <button
                type="button"
                className="gallery-reveal"
                onClick={() => setRevealed(prev => new Set(prev).add(current.key))}
              >
                成人向けの画像です<br />タップして表示
              </button>
            )}
            <button type="button" className="gallery-nav gallery-nav--next" onClick={() => step(1)}
              disabled={viewing >= items.length - 1 && !hasMore} aria-label="次の画像">›</button>
          </div>
          <div className="gallery-info">
            <div className="gallery-info-actions">
              <button
                type="button"
                className={`gallery-star-button${current.bookmarked ? ' is-on' : ''}`}
                onClick={() => void toggleBookmark(current)}
                aria-pressed={current.bookmarked}
              >
                {current.bookmarked ? '★ ブックマーク済み' : '☆ ブックマーク'}
              </button>
              <button type="button" onClick={() => void downloadOne(current)}>ダウンロード</button>
              {current.story_id !== null && (
                <button type="button" onClick={() => navigate(`/bookshelf?book=${current.story_id}`)}>
                  本棚で読む
                </button>
              )}
              <button type="button" onClick={() => void toggleAdult(current)}>
                {current.adult ? '成人向けから外す' : '成人向けにする'}
              </button>
              <button
                type="button"
                className="gallery-danger"
                onClick={() => void deleteKeys([current.key])}
                disabled={deleting}
              >
                削除
              </button>
            </div>
            <dl className="gallery-meta">
              <dt>種類</dt>
              <dd>{SOURCE_LABELS[current.source]}{describe(current) && ` ・ ${describe(current)}`}</dd>
              {current.story_title && (<><dt>物語</dt><dd>{current.story_title}</dd></>)}
              <dt>日時</dt>
              <dd>{formatDate(current.created_at)}</dd>
              {current.width && current.height && (<><dt>サイズ</dt><dd>{current.width}×{current.height}</dd></>)}
              {current.seed !== null && (<><dt>シード</dt><dd>{current.seed}</dd></>)}
              {current.model && (<><dt>モデル</dt><dd>{current.model}</dd></>)}
              <dt>成人向け</dt>
              <dd>
                {current.adult ? 'はい' : 'いいえ'}
                {current.adult_manual !== null ? '(手動で指定)' : '(タグから自動判定)'}
              </dd>
            </dl>
            {current.prompt && (
              <div className="gallery-prompt">
                <div className="gallery-prompt-head">
                  <span>プロンプト</span>
                  <button type="button" onClick={() => void copyPrompt(current.prompt)}>
                    {copied ? 'コピーしました' : 'コピー'}
                  </button>
                </div>
                <p>{current.prompt}</p>
              </div>
            )}
            <div className="gallery-similar">
              <h3>似ている画像</h3>
              {similar === null && <p className="gallery-similar-muted">計算中…</p>}
              {similar?.length === 0 && <p className="gallery-similar-muted">見つかりませんでした。</p>}
              <ul>
                {similar?.map(({ image, reasons }) => {
                  // ぼかしている成人向けの画像は、露骨な語が出ることがあるタグの理由を出さない
                  const shown = blurs(image) ? reasons.filter(r => !r.startsWith('共通のタグ')) : reasons
                  return (
                  <li key={image.key}>
                    <button
                      type="button"
                      onClick={() => openSimilar(image)}
                      title={shown.join(' ・ ')}
                    >
                      <img
                        src={thumbUrl(image.path, 240)}
                        alt={describe(image) || SOURCE_LABELS[image.source]}
                        className={blurs(image) ? 'is-blurred' : undefined}
                        loading="lazy"
                      />
                      <span>{shown[0] ?? ''}</span>
                    </button>
                  </li>
                  )
                })}
              </ul>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

