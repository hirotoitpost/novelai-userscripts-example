import { useState } from 'react'
import type { FormEvent } from 'react'
import { apiFetch } from '../api'
import { DEFAULT_GENRE, genreOptions, useGenres } from '../genres'
import { useLocalStorage } from '../hooks/useLocalStorage'
import './PublishDialog.css'

/** ギャラリーから漫画を作る処理(POST /api/gallery-manga と GET /api/gallery-manga/jobs/{id}) */
export interface GalleryMangaJob {
  id: string
  status: string
  message: string
  progress: number
  total: number
  detail: string | null
  story_id: number | null
}

/** 1回に漫画にできる画像の数(サーバーの gallery_manga.MAX_IMAGES と同じ) */
export const GALLERY_MANGA_MAX = 24

interface Props {
  token: string
  /** 選んだ画像のキー(選んだ順。1枚が1コマになる) */
  imageKeys: string[]
  onClose: () => void
  /** 処理が始まった(進み具合は呼び出し側で追う) */
  onStarted: (job: GalleryMangaJob) => void
}

/**
 * ギャラリーで選んだ画像から漫画を作るダイアログ。ジャンルを選ぶと、そのジャンルに合う話・セリフ・描き文字を
 * 文章モデルが考え、各画像のプロンプトから似た絵をコマとして生成し直す(選んだ画像そのものは使わない)。
 */
export default function GalleryMangaDialog({ token, imageKeys, onClose, onStarted }: Props) {
  const genres = useGenres(token)
  const [genre, setGenre] = useLocalStorage('nai_gallery_manga_genre', DEFAULT_GENRE)
  const [theme, setTheme] = useState('')
  const [title, setTitle] = useState('')
  const [color, setColor] = useLocalStorage('nai_gallery_manga_color', true)
  const [scriptOnly, setScriptOnly] = useLocalStorage('nai_gallery_manga_script_only', false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const tooMany = imageKeys.length > GALLERY_MANGA_MAX
  const pages = Math.ceil(imageKeys.length / 4)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (busy || tooMany) return
    setBusy(true)
    setError(null)
    try {
      onStarted(await apiFetch<GalleryMangaJob>(token, '/api/gallery-manga', {
        image_keys: imageKeys, genre: genre.trim(), theme: theme.trim(), title: title.trim(),
        color, make_images: !scriptOnly,
      }))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  return (
    <div className="publish-overlay" onClick={busy ? undefined : onClose}>
      <form className="publish-dialog" role="dialog" aria-modal="true" aria-labelledby="gallery-manga-title"
        onClick={e => e.stopPropagation()} onSubmit={e => void submit(e)}>
        <h2 id="gallery-manga-title">選んだ画像から漫画を作る</h2>
        <p className="publish-summary">画像 {imageKeys.length}枚 → {imageKeys.length}コマ・{pages}ページ(選んだ順。4コマで1ページ)</p>
        <p className="publish-hint">
          ジャンルに合う話・セリフ・描き文字を考え、各画像のプロンプトから似た絵をコマとして生成し直します
          (ジャンルに合わせて表情や動作のタグを少し変えます。選んだ画像そのものは使わず、元の画像も変わりません)。
          NovelAI の文章・画像生成を使います。
        </p>
        <label className="publish-field">
          <span>ジャンル</span>
          <input list="gallery-manga-genres" value={genre} maxLength={100} autoFocus onChange={e => setGenre(e.target.value)} />
          <datalist id="gallery-manga-genres">{genreOptions(genres.genres, []).map(g => <option key={g} value={g} />)}</datalist>
        </label>
        <label className="publish-field">
          <span>話の方向・補足(任意)</span>
          <input value={theme} maxLength={500} placeholder="例: 待ち合わせに遅れて慌てる話" onChange={e => setTheme(e.target.value)} />
        </label>
        <label className="publish-field">
          <span>タイトル(空なら自動で付けます)</span>
          <input value={title} maxLength={200} onChange={e => setTitle(e.target.value)} />
        </label>
        <label className="publish-check">
          <input type="checkbox" checked={color} onChange={e => setColor(e.target.checked)} />
          カラーで描く(オフなら白黒)
        </label>
        <label className="publish-check">
          <input type="checkbox" checked={scriptOnly} onChange={e => setScriptOnly(e.target.checked)} />
          台本(物語)を作るところまでにする(コマの絵は、物語のページで確かめてから生成)
        </label>
        {tooMany && <p className="publish-error" role="alert">一度に漫画にできるのは {GALLERY_MANGA_MAX} 枚までです(いま {imageKeys.length} 枚)。</p>}
        {error && <p className="publish-error" role="alert">{error}</p>}
        <div className="publish-actions">
          <button type="button" onClick={onClose} disabled={busy}>やめる</button>
          <button type="submit" className="publish-primary" disabled={busy || tooMany || imageKeys.length === 0}>
            {busy ? '始めています…' : scriptOnly ? '台本を作る' : '漫画を作る'}
          </button>
        </div>
      </form>
    </div>
  )
}
