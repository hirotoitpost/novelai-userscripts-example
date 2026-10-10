import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import './StoryLibrary.css'

interface StorySummary {
  id: number
  premise: string
  title: string | null
  status: string
  created_at: string
  final_image_path: string | null
}

interface StoryScene {
  id: number
  scene_index: number
  novelai_text: string | null
  draft_text: string
  seed_cue: string | null
}

interface StoryDetail {
  id: number
  premise: string
  title: string | null
  status: string
  created_at: string
  raw_text: string | null
  scenes: StoryScene[]
}

// Story.tsx と同じ理由(Viteのdevプロキシ経由だとSSEが詰まる件とは無関係だが、
// このページも他の/api/story/*呼び出しと挙動を揃えるため)でバックエンドへ直接アクセスする。
const API_ORIGIN = `${window.location.protocol}//${window.location.hostname}:8000`

const STATUS_LABELS: Record<string, string> = {
  draft: 'ドラフト',
  imported: '取り込み済み(未分割)',
  written: '執筆済み',
  illustrated: '挿絵生成済み',
  completed: '漫画化完了',
}

async function readErrorDetail(res: Response): Promise<string> {
  const text = await res.text()
  try {
    const data = JSON.parse(text)
    return data.detail ?? text
  } catch {
    return text || res.statusText
  }
}

export default function StoryLibrary() {
  const navigate = useNavigate()

  const [stories, setStories] = useState<StorySummary[]>([])
  const [selected, setSelected] = useState<StoryDetail | null>(null)
  const [openingId, setOpeningId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  function loadStories() {
    setError(null)
    fetch(`${API_ORIGIN}/api/story?limit=100`)
      .then(async r => {
        if (!r.ok) throw new Error(await readErrorDetail(r))
        return r.json()
      })
      .then(setStories)
      .catch(e => setError(e instanceof Error ? e.message : String(e)))
  }

  useEffect(() => {
    loadStories()
  }, [])

  async function openStory(id: number) {
    setError(null)
    setOpeningId(id)
    try {
      const res = await fetch(`${API_ORIGIN}/api/story/${id}`)
      if (!res.ok) throw new Error(await readErrorDetail(res))
      setSelected(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setOpeningId(null)
    }
  }

  const bodyText = !selected
    ? ''
    : selected.scenes.length > 0
      ? selected.scenes
          .map(s => (s.novelai_text ? `${s.seed_cue ?? ''}${s.novelai_text}` : s.draft_text))
          .join('\n\n')
      : (selected.raw_text ?? '')

  return (
    <div className="library-root">
      <div className="library-inner">
        <button
          type="button"
          className="library-back"
          onClick={() => (selected ? setSelected(null) : navigate('/'))}
        >
          {selected ? '← 一覧へ' : '← ホームへ'}
        </button>

        {error && <div className="library-error">{error}</div>}

        {!selected && (
          <>
            <h1>物語を読む</h1>
            <p className="library-intro">取り込み・生成した物語を一覧から選んで読めます。</p>
            <ul className="library-list">
              {stories.length === 0 && <li className="library-muted">物語がまだありません。</li>}
              {stories.map(s => (
                <li
                  key={s.id}
                  className="library-item"
                  onClick={() => openStory(s.id)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={e => e.key === 'Enter' && openStory(s.id)}
                >
                  <span className="library-item-title">{s.title ?? s.premise}</span>
                  <span className="library-item-meta">
                    {STATUS_LABELS[s.status] ?? s.status} ・ {new Date(s.created_at).toLocaleString()}
                    {openingId === s.id && ' ・ 読み込み中...'}
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}

        {selected && (
          <article className="library-reader">
            <h1>{selected.title ?? selected.premise}</h1>
            <p className="library-reader-meta">
              {STATUS_LABELS[selected.status] ?? selected.status} ・ {new Date(selected.created_at).toLocaleString()}
            </p>
            {bodyText ? (
              <div className="library-reader-body">{bodyText}</div>
            ) : (
              <p className="library-muted">本文がありません。</p>
            )}
          </article>
        )}
      </div>
    </div>
  )
}
