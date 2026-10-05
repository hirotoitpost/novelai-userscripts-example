import { CSSProperties, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { useLocalStorage } from '../hooks/useLocalStorage'
import {
  deleteBook,
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
import './Bookshelf.css'

interface Book {
  id: number
  title: string
  origin: string | null
  status: string
  created_at: string
  updated_at: string
  cover_path: string | null
  manga: 'v2' | 'v1' | null
  page_count: number
  scene_count: number
  char_count: number
  bookmarked: boolean
  /** 成人向けか(手動指定があればそれ、無ければタグ・本文から自動判定) */
  adult: boolean
  adult_auto: boolean
  adult_manual: boolean | null
}

interface BookDetail extends Book {
  pages: string[]
  text: string
}

type Kind = 'all' | 'manga' | 'text'
type Sort = 'updated' | 'created' | 'title'
type ReaderTab = 'manga' | 'text'

const KINDS: { id: Kind; label: string }[] = [
  { id: 'all', label: 'すべて' },
  { id: 'manga', label: '漫画' },
  { id: 'text', label: '小説' },
]
const SWIPE_THRESHOLD = 50

/** 表紙の無い本の色。物語IDから決めるので、毎回同じ色になる。 */
function coverHue(id: number): number {
  return (id * 137) % 360
}

function bookSummary(book: Book): string {
  if (book.manga) return `漫画 ${book.page_count}P`
  return `小説 ${book.char_count.toLocaleString()}字`
}

export default function Bookshelf() {
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const openId = Number(searchParams.get('book')) || null

  const [kind, setKind] = useLocalStorage<Kind>('nai_bookshelf_kind', 'all')
  const [sort, setSort] = useLocalStorage<Sort>('nai_bookshelf_sort', 'updated')
  const [onlyBookmarked, setOnlyBookmarked] = useLocalStorage('nai_bookshelf_bookmarked', false)
  const [rating, setRating] = useLocalStorage<Rating>('nai_bookshelf_rating', 'all')
  // 成人向けの表紙をぼかすか(ギャラリーと共通・端末ごと)。既定はぼかす
  const [blurAdult, setBlurAdult] = useLocalStorage('nai_library_blur_adult', true)
  // 物語ID → 最後に開いていた漫画のページ(0始まり)
  const [progress, setProgress] = useLocalStorage<Record<string, number>>('nai_bookshelf_progress', {})
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')

  const [books, setBooks] = useState<Book[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query.trim()), 300)
    return () => clearTimeout(timer)
  }, [query])

  const requestId = useRef(0)
  const loadBooks = useCallback(async () => {
    const id = ++requestId.current
    const params = new URLSearchParams({ kind, sort })
    if (onlyBookmarked) params.set('bookmarked', 'true')
    if (rating !== 'all') params.set('rating', rating)
    if (debouncedQuery) params.set('q', debouncedQuery)
    setLoading(true)
    setError(null)
    try {
      const data = await getJson<Book[]>(`/api/library/books?${params}`)
      if (id === requestId.current) setBooks(data)
    } catch (e) {
      if (id === requestId.current) setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (id === requestId.current) setLoading(false)
    }
  }, [kind, sort, onlyBookmarked, rating, debouncedQuery])

  useEffect(() => {
    void loadBooks()
  }, [loadBooks])

  async function toggleBookmark(book: Pick<Book, 'id' | 'bookmarked'>) {
    const next = !book.bookmarked
    setBooks(prev => prev.map(b => (b.id === book.id ? { ...b, bookmarked: next } : b)))
    try {
      await setBookmark('book', String(book.id), next)
      if (onlyBookmarked && !next) setBooks(prev => prev.filter(b => b.id !== book.id))
      return next
    } catch (e) {
      setBooks(prev => prev.map(b => (b.id === book.id ? { ...b, bookmarked: !next } : b)))
      setError(e instanceof Error ? e.message : String(e))
      return !next
    }
  }

  async function toggleAdult(book: Pick<Book, 'id' | 'adult' | 'adult_auto'>) {
    const next = !book.adult
    // 自動判定と同じになるなら手動指定は外す(後で内容が変わったときに自動判定に従えるように)
    const manual = next === book.adult_auto ? null : next
    try {
      await setAdult('book', String(book.id), manual)
      setBooks(prev =>
        prev
          .map(b => (b.id === book.id ? { ...b, adult: next, adult_manual: manual } : b))
          // 絞り込みの条件から外れた本は棚から下ろす
          .filter(b => b.id !== book.id || rating === 'all' || next === (rating === 'adult')),
      )
      return { adult: next, adult_manual: manual }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return null
    }
  }

  async function removeBook(book: Pick<Book, 'id' | 'title'>) {
    const ok = window.confirm(
      `「${book.title}」を削除します。元に戻せません。よろしいですか？\n` +
        '(本文・コマ・挿絵・合成した漫画のページも消えます。物語エディタの下書きは残ります)',
    )
    if (!ok) return false
    try {
      await deleteBook(book.id)
      setBooks(prev => prev.filter(b => b.id !== book.id))
      const nextProgress = { ...progress }
      delete nextProgress[String(book.id)]
      setProgress(nextProgress)
      setSearchParams({}, { replace: true })
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return false
    }
  }

  function openBook(id: number) {
    // URL に残すので、ブラウザの「戻る」(スマホの戻る操作)で本棚に戻れる
    setSearchParams({ book: String(id) }, { state: { fromShelf: true } })
  }

  function closeBook() {
    // 本棚から開いたなら履歴を1つ戻す(「戻る」と同じ)。ギャラリー等から直接開いたときに戻ると
    // 本棚ではなく前のページへ行ってしまうので、その場合は本を閉じた本棚に置き換える。
    if ((location.state as { fromShelf?: boolean } | null)?.fromShelf) navigate(-1)
    else setSearchParams({}, { replace: true })
  }

  const filtered = kind !== 'all' || onlyBookmarked || rating !== 'all' || debouncedQuery !== ''

  return (
    <div className="shelf-root">
      <header className="shelf-header">
        <button type="button" className="shelf-back" onClick={() => navigate('/')}>← ホーム</button>
        <h1>本棚</h1>
        <span className="shelf-count">{books.length} 冊</span>
      </header>

      <div className="shelf-toolbar">
        <input
          type="search"
          className="shelf-search"
          placeholder="タイトルで検索"
          value={query}
          onChange={e => setQuery(e.target.value)}
        />
        <div className="shelf-controls">
          <div className="shelf-chips" role="group" aria-label="種類">
            {KINDS.map(k => (
              <button
                key={k.id}
                type="button"
                className={`shelf-chip${kind === k.id ? ' is-on' : ''}`}
                aria-pressed={kind === k.id}
                onClick={() => setKind(k.id)}
              >
                {k.label}
              </button>
            ))}
            <button
              type="button"
              className={`shelf-chip shelf-chip--star${onlyBookmarked ? ' is-on' : ''}`}
              aria-pressed={onlyBookmarked}
              onClick={() => setOnlyBookmarked(!onlyBookmarked)}
            >
              ★ ブックマーク
            </button>
          </div>
          <div className="shelf-selects">
            <select value={rating} onChange={e => setRating(e.target.value as Rating)} aria-label="成人向けで絞り込む">
              {RATINGS.map(r => <option key={r.id} value={r.id}>{r.label}</option>)}
            </select>
            <select value={sort} onChange={e => setSort(e.target.value as Sort)} aria-label="並び順">
              <option value="updated">更新が新しい順</option>
              <option value="created">作成が新しい順</option>
              <option value="title">タイトル順</option>
            </select>
            <label className="shelf-blur-toggle">
              <input type="checkbox" checked={blurAdult} onChange={e => setBlurAdult(e.target.checked)} />
              成人向けをぼかす
            </label>
          </div>
        </div>
      </div>

      {error && <div className="shelf-error">{error}</div>}
      {!loading && books.length === 0 && !error && (
        <p className="shelf-empty">{filtered ? '条件に合う本がありません。' : '本がまだありません。'}</p>
      )}

      <div className="rack" aria-busy={loading}>
        {books.map(book => {
          const page = progress[String(book.id)]
          const readRatio = book.manga && page !== undefined && book.page_count > 0
            ? (page + 1) / book.page_count
            : null
          // 成人向けだけを表示しているときは、自分で選んで見ているのでぼかさない
          const blurred = book.adult && blurAdult && rating !== 'adult'
          return (
            <div key={book.id} className={`rack-slot${blurred ? ' is-blurred' : ''}`}>
              <button type="button" className="rack-cover" onClick={() => openBook(book.id)} aria-label={`${book.title}を読む`}>
                {book.cover_path ? (
                  <img src={thumbUrl(book.cover_path, 480)} loading="lazy" alt="" />
                ) : (
                  <span
                    className="rack-cover-plain"
                    style={{ '--hue': coverHue(book.id) } as CSSProperties}
                  >
                    <span className="rack-cover-plain-title">{book.title}</span>
                  </span>
                )}
                <span className="rack-masthead">
                  {book.origin && <span className="rack-origin">{book.origin}</span>}
                  <span className="rack-title">{book.title}</span>
                </span>
                {book.bookmarked && <span className="rack-star" aria-label="ブックマーク済み">★</span>}
                {book.adult && <span className="rack-r18">R18</span>}
                {readRatio !== null && (
                  <span className="rack-progress" aria-label={`${page + 1}ページまで読んだ`}>
                    <span style={{ width: `${Math.min(readRatio, 1) * 100}%` }} />
                  </span>
                )}
              </button>
              <div className="rack-ledge" aria-hidden />
              <div className="rack-label">
                <span>{bookSummary(book)}</span>
                <span>{formatDate(book.updated_at).split(' ')[0]}</span>
              </div>
            </div>
          )
        })}
      </div>

      {openId !== null && (
        <Reader
          key={openId}
          bookId={openId}
          initialPage={progress[String(openId)] ?? 0}
          onPage={page => setProgress({ ...progress, [String(openId)]: page })}
          onClose={closeBook}
          onToggleBookmark={toggleBookmark}
          onToggleAdult={toggleAdult}
          onDelete={removeBook}
          onEdit={() => navigate(`/story?story=${openId}`)}
        />
      )}
    </div>
  )
}

interface ReaderProps {
  bookId: number
  initialPage: number
  onPage: (page: number) => void
  onClose: () => void
  onToggleBookmark: (book: Pick<Book, 'id' | 'bookmarked'>) => Promise<boolean>
  onToggleAdult: (
    book: Pick<Book, 'id' | 'adult' | 'adult_auto'>,
  ) => Promise<Pick<Book, 'adult' | 'adult_manual'> | null>
  onDelete: (book: Pick<Book, 'id' | 'title'>) => Promise<boolean>
  onEdit: () => void
}

function Reader({
  bookId, initialPage, onPage, onClose, onToggleBookmark, onToggleAdult, onDelete, onEdit,
}: ReaderProps) {
  const [book, setBook] = useState<BookDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<ReaderTab>('manga')
  const [page, setPage] = useState(0)
  const [downloading, setDownloading] = useState<string | null>(null)
  const [fontSize, setFontSize] = useLocalStorage('nai_bookshelf_font_size', 18)
  const [vertical, setVertical] = useLocalStorage('nai_bookshelf_vertical', false)
  const touchStart = useRef<number | null>(null)

  useEffect(() => {
    getJson<BookDetail>(`/api/library/books/${bookId}`)
      .then(data => {
        setBook(data)
        setTab(data.pages.length > 0 ? 'manga' : 'text')
        // 読み終わったページで開くと最後のページから始まってしまうので、最後なら最初に戻す
        const start = initialPage >= data.pages.length - 1 ? 0 : initialPage
        setPage(Math.max(0, Math.min(start, data.pages.length - 1)))
      })
      .catch(e => setError(e instanceof Error ? e.message : String(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookId])

  const pageCount = book?.pages.length ?? 0
  const goTo = useCallback(
    (next: number) => {
      if (next < 0 || next >= pageCount) return
      setPage(next)
      onPage(next)
    },
    [pageCount, onPage],
  )

  // 漫画は右から左へ読むので、← が次のページ、→ が前のページ
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
      if (tab !== 'manga') return
      if (e.key === 'ArrowLeft') goTo(page + 1)
      else if (e.key === 'ArrowRight') goTo(page - 1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [tab, page, goTo, onClose])

  // 次のページを先に読み込んでおく
  useEffect(() => {
    if (!book || page + 1 >= book.pages.length) return
    const img = new Image()
    img.src = fileUrl(book.pages[page + 1])
  }, [book, page])

  async function download(format: 'pdf' | 'zip' | 'txt') {
    if (!book) return
    setDownloading(format)
    setError(null)
    try {
      await downloadFile(`/api/library/books/${book.id}/download?format=${format}`, `story${book.id}.${format}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setDownloading(null)
    }
  }

  async function toggleBookmark() {
    if (!book) return
    const next = await onToggleBookmark(book)
    setBook({ ...book, bookmarked: next })
  }

  async function toggleAdult() {
    if (!book) return
    const next = await onToggleAdult(book)
    if (next) setBook({ ...book, ...next })
  }

  const paragraphs = useMemo(() => (book?.text ?? '').split(/\n{2,}/).filter(p => p.trim()), [book])
  const atEnd = tab === 'manga' && pageCount > 0 && page === pageCount - 1

  return (
    <div className="reader" role="dialog" aria-modal="true" aria-label="本を読む">
      <div className="reader-bar">
        <button type="button" className="reader-close" onClick={onClose}>← 本棚</button>
        <span className="reader-title">{book?.title ?? '読み込み中…'}</span>
        {book && (
          <button
            type="button"
            className={`reader-star${book.bookmarked ? ' is-on' : ''}`}
            onClick={() => void toggleBookmark()}
            aria-pressed={book.bookmarked}
            aria-label={book.bookmarked ? 'ブックマークを外す' : 'ブックマークする'}
          >
            {book.bookmarked ? '★' : '☆'}
          </button>
        )}
      </div>

      {error && <div className="shelf-error reader-error">{error}</div>}

      {book && (
        <>
          <div className="reader-tools">
            {book.pages.length > 0 && book.text && (
              <div className="reader-tabs" role="tablist">
                <button type="button" role="tab" aria-selected={tab === 'manga'}
                  className={tab === 'manga' ? 'is-on' : ''} onClick={() => setTab('manga')}>漫画</button>
                <button type="button" role="tab" aria-selected={tab === 'text'}
                  className={tab === 'text' ? 'is-on' : ''} onClick={() => setTab('text')}>文章</button>
              </div>
            )}
            {tab === 'text' && (
              <div className="reader-textopts">
                <button type="button" onClick={() => setFontSize(Math.max(13, fontSize - 1))} aria-label="文字を小さく">A−</button>
                <button type="button" onClick={() => setFontSize(Math.min(28, fontSize + 1))} aria-label="文字を大きく">A＋</button>
                <button type="button" className={vertical ? 'is-on' : ''} onClick={() => setVertical(!vertical)}>
                  {vertical ? '縦書き' : '横書き'}
                </button>
              </div>
            )}
            <div className="reader-actions">
              {book.pages.length > 0 && (
                <>
                  <button type="button" disabled={downloading !== null} onClick={() => void download('pdf')}>
                    {downloading === 'pdf' ? '作成中…' : 'PDF'}
                  </button>
                  <button type="button" disabled={downloading !== null} onClick={() => void download('zip')}>
                    {downloading === 'zip' ? '作成中…' : '画像zip'}
                  </button>
                </>
              )}
              {book.text && (
                <button type="button" disabled={downloading !== null} onClick={() => void download('txt')}>
                  テキスト
                </button>
              )}
              <button type="button" onClick={onEdit}>編集</button>
              <button type="button" onClick={() => void toggleAdult()}>
                {book.adult ? '成人向けから外す' : '成人向けにする'}
              </button>
              <button type="button" className="reader-danger" onClick={() => void onDelete(book)}>削除</button>
            </div>
          </div>

          {tab === 'manga' && pageCount > 0 && (
            <>
              <div
                className="reader-stage"
                onTouchStart={e => { touchStart.current = e.touches[0].clientX }}
                onTouchEnd={e => {
                  if (touchStart.current === null) return
                  const dx = e.changedTouches[0].clientX - touchStart.current
                  touchStart.current = null
                  // 右から左へ読むので、右へなぞると次のページ
                  if (Math.abs(dx) > SWIPE_THRESHOLD) goTo(dx > 0 ? page + 1 : page - 1)
                }}
              >
                <img key={book.pages[page]} src={fileUrl(book.pages[page])} alt={`${page + 1}ページ`} />
                <button type="button" className="reader-zone reader-zone--next" onClick={() => goTo(page + 1)}
                  aria-label="次のページ" disabled={page >= pageCount - 1} />
                <button type="button" className="reader-zone reader-zone--prev" onClick={() => goTo(page - 1)}
                  aria-label="前のページ" disabled={page === 0} />
              </div>
              <div className="reader-pager">
                <button type="button" onClick={() => goTo(page + 1)} disabled={page >= pageCount - 1}>‹ 次</button>
                {/* 右から左へ読むので、スライダーも右端が1ページ目 */}
                <input type="range" dir="rtl" min={0} max={pageCount - 1} value={page}
                  onChange={e => goTo(Number(e.target.value))} aria-label="ページ" />
                <button type="button" onClick={() => goTo(page - 1)} disabled={page === 0}>前 ›</button>
                <span className="reader-pageno">{page + 1} / {pageCount}</span>
              </div>
              {atEnd && (
                <div className="reader-end">
                  <span>おわり</span>
                  <button type="button" onClick={() => goTo(0)}>最初から読む</button>
                  <button type="button" onClick={onClose}>本棚に戻る</button>
                </div>
              )}
            </>
          )}

          {tab === 'text' && (
            <article
              className={`reader-text${vertical ? ' is-vertical' : ''}`}
              style={{ fontSize: `${fontSize}px` }}
            >
              <h2>{book.title}</h2>
              {paragraphs.length > 0
                ? paragraphs.map((p, i) => <p key={i}>{p}</p>)
                : <p className="reader-muted">本文がありません。</p>}
            </article>
          )}

          <p className="reader-meta">
            {book.origin && `${book.origin} ・ `}
            {book.scene_count > 0 && `${book.scene_count}シーン ・ `}
            {book.char_count.toLocaleString()}字 ・ 作成 {formatDate(book.created_at)} ・ 更新 {formatDate(book.updated_at)}
          </p>
        </>
      )}
    </div>
  )
}
