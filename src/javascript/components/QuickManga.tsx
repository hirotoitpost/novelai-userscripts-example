import { useState } from 'react'
import { Link } from 'react-router-dom'
import { apiFetch } from '../api'
import { DEFAULT_GENRE } from '../genres'
import { useLocalStorage } from '../hooks/useLocalStorage'
import type { Character } from './StoryCharacters'
import CastPresets from './CastPresets'

/**
 * おまかせ漫画: テーマ1行(とジャンル)から、登場人物 → 大枠シナリオ → 台本 → 漫画まで一気に作る(/api/quick-manga)。
 * 題材の候補も出せる(なろう・AniList の今の傾向を手がかりにする。特定の作品の筋は使わない)。
 * 進み具合は、ページの処理状況のダイアログに出す。
 */

interface QuickJob {
  id: string
  status: string
  message: string
  progress: number
  total: number
  detail: string | null
  story_id: number | null
  characters: { id: number; name: string }[]
  title: string | null
}

interface ThemeResult {
  themes: string[]
  trends: { narou: string[]; anilist: string[]; errors: string[] }
}

/** ページの処理状況(useTaskStatus)のうち、ここで使うもの */
interface Task {
  busy: boolean
  start: (title: string, message?: string) => void
  update: (message: string, progress?: number, total?: number) => void
  finish: (state: 'done' | 'error' | 'cancelled', message: string) => void
}

interface Props {
  token: string
  characters: Character[]
  genres: string[]
  task: Task
  /** キャラを新しく作ったとき(キャラの一覧を読み直す) */
  onCharactersChanged: () => void
}

export default function QuickManga({ token, characters, genres, task, onCharactersChanged }: Props) {
  const [theme, setTheme] = useState('')
  const [genre, setGenre] = useLocalStorage('nai_quick_genre', DEFAULT_GENRE)
  const [episodes, setEpisodes] = useLocalStorage('nai_quick_episodes', 2)
  const [castIds, setCastIds] = useState<number[]>([])
  const [castCount, setCastCount] = useLocalStorage('nai_quick_cast_count', 2)
  const [color, setColor] = useLocalStorage('nai_quick_color', true)
  const [scriptOnly, setScriptOnly] = useLocalStorage('nai_quick_script_only', false)
  const [useReference, setUseReference] = useLocalStorage('nai_quick_reference', false)
  const [useTrends, setUseTrends] = useLocalStorage('nai_quick_trends', true)
  const [review, setReview] = useLocalStorage('nai_quick_review', true)
  const [checkPanels, setCheckPanels] = useLocalStorage('nai_quick_check_panels', true)
  const [themes, setThemes] = useState<ThemeResult | null>(null)
  const [thinking, setThinking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [made, setMade] = useState<{ storyId: number; message: string; title: string | null } | null>(null)

  const suggest = async () => {
    setThinking(true)
    setError(null)
    try {
      setThemes(await apiFetch<ThemeResult>(token, `/api/quick-manga/themes?genre=${encodeURIComponent(genre)}&trends=${useTrends}`))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setThinking(false)
    }
  }

  const follow = async (job: QuickJob) => {
    task.start('おまかせ漫画')
    setMade(null)
    let charactersShown = false
    while (true) {
      await new Promise(r => setTimeout(r, 3000))
      const current = await apiFetch<QuickJob>(token, `/api/quick-manga/jobs/${job.id}`).catch(() => undefined)
      if (current === undefined) continue
      if (!charactersShown && current.characters.length > 0) {
        charactersShown = true
        onCharactersChanged()
      }
      task.update(current.message, current.progress, current.total)
      if (current.status === 'running') continue
      if (current.status === 'done' && current.story_id) {
        task.finish('done', current.message)
        setMade({ storyId: current.story_id, message: current.message, title: current.title })
      } else {
        task.finish('error', current.detail ?? current.message)
      }
      onCharactersChanged()
      return
    }
  }

  const start = async () => {
    if (!theme.trim()) return
    setError(null)
    try {
      const job = await apiFetch<QuickJob>(token, '/api/quick-manga', {
        theme: theme.trim(), genre: genre.trim() || DEFAULT_GENRE, episodes, character_ids: castIds, cast_count: castCount,
        color, stop_after_script: scriptOnly, use_character_reference: useReference,
        review_script: review, check_panels: checkPanels,
      })
      void follow(job)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const trendWords = themes ? [...themes.trends.narou, ...themes.trends.anilist] : []
  return (
    <section className="md-section md-quick">
      <h2>おまかせで作る</h2>
      <p className="md-hint">
        テーマを1行書くだけで、登場人物(選ばなければ新しく考えます)→ 大枠シナリオ → 台本 → コマの生成と合成まで、続けて作ります。
        途中の手直しをしたいときは、下の「1. テーマと登場人物」から順に進めてください。
      </p>
      <div className="md-row">
        <label className="md-field">
          <span>ジャンル</span>
          <input list="md-quick-genres" value={genre} onChange={e => setGenre(e.target.value)} />
          <datalist id="md-quick-genres">{genres.map(g => <option key={g} value={g} />)}</datalist>
        </label>
        <label className="md-field md-field--narrow">
          <span>話数(1話=4コマ)</span>
          <input type="number" min={1} max={4} value={episodes} onChange={e => setEpisodes(Math.min(4, Math.max(1, Number(e.target.value) || 1)))} />
        </label>
      </div>
      <label className="md-field">
        <span>テーマ</span>
        <input value={theme} maxLength={500} onChange={e => setTheme(e.target.value)} placeholder="例: カフェの新メニュー開発で、新人の奇抜な試作品に先輩が振り回される" />
      </label>
      <div className="md-quick-themes">
        <button type="button" className="md-small" onClick={() => void suggest()} disabled={thinking || task.busy}>
          {thinking ? '考えています…' : '題材の候補を出す'}
        </button>
        <label className="md-check">
          <input type="checkbox" checked={useTrends} onChange={e => setUseTrends(e.target.checked)} />
          今の人気の傾向(なろう・AniList)を手がかりにする
        </label>
      </div>
      {themes && (
        <div className="md-quick-candidates">
          {themes.themes.map(t => (
            <button type="button" key={t} className={theme === t ? 'md-chip md-chip--on' : 'md-chip'} onClick={() => setTheme(t)}>{t}</button>
          ))}
          {trendWords.length > 0 && <p className="md-hint">手がかり: {trendWords.join('、')}</p>}
          {themes.trends.errors.length > 0 && <p className="md-hint">問い合わせられなかったもの: {themes.trends.errors.map(e => e.split(':')[0]).join('、')}(ジャンルだけから考えました)</p>}
        </div>
      )}
      <div className="md-field">
        <span>登場人物(選ばなければ、テーマに合う人物を {castCount} 人考えてキャラシートを作ります)</span>
        <CastPresets token={token} selected={castIds} existing={characters.map(c => c.id)} className="md-chips"
          onSelect={ids => setCastIds(ids.slice(0, 4))} />
        <div className="md-chips">
          {characters.map(c => (
            <button type="button" key={c.id} className={castIds.includes(c.id) ? 'md-chip md-chip--on' : 'md-chip'}
              onClick={() => setCastIds(castIds.includes(c.id) ? castIds.filter(i => i !== c.id) : castIds.length < 4 ? [...castIds, c.id] : castIds)}>
              {c.name}
            </button>
          ))}
        </div>
        {castIds.length === 0 && (
          <label className="md-field--inline md-quick-count">
            新しく考える人数
            <input type="number" min={1} max={4} value={castCount} onChange={e => setCastCount(Math.min(4, Math.max(1, Number(e.target.value) || 1)))} />
          </label>
        )}
      </div>
      <div className="md-quick-options">
        <label className="md-check"><input type="checkbox" checked={color} onChange={e => setColor(e.target.checked)} />カラー</label>
        <label className="md-check"><input type="checkbox" checked={scriptOnly} onChange={e => setScriptOnly(e.target.checked)} />台本(物語)を作るところまでにする</label>
        <label className="md-check" title="キャラの参照画像で見た目をそろえます。1コマごとに +5 Anlas、V4.5 で生成します">
          <input type="checkbox" checked={useReference} onChange={e => setUseReference(e.target.checked)} />キャラ参照を使う(1コマ +5 Anlas)
        </label>
        <label className="md-check" title="セリフと筋から、絵に要る物・状態(食べ物・小物・透明など)を作画タグに足します">
          <input type="checkbox" checked={review} onChange={e => setReview(e.target.checked)} />台本を見直す(絵に要る物・状態を足す)
        </label>
        <label className="md-check" title="判定モデルで物や人数が描かれているかを確かめ、外れたコマはタグを強めて描き直します">
          <input type="checkbox" checked={checkPanels} onChange={e => setCheckPanels(e.target.checked)} disabled={scriptOnly} />絵を確かめて、ずれたコマを描き直す
        </label>
      </div>
      <button type="button" className="md-primary" onClick={() => void start()} disabled={!theme.trim() || task.busy}>おまかせで作る</button>
      {error && <p className="md-error" role="alert">{error}</p>}
      {made && (
        <p className="md-result">
          {made.message}
          {' '}<Link to={`/bookshelf?book=${made.storyId}`}>本棚で読む</Link>
          {' '}<Link to={`/story?story=${made.storyId}`}>物語のページで手直しする</Link>
        </p>
      )}
    </section>
  )
}
