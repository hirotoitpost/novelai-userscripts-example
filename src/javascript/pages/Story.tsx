import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import './Story.css'

interface StoryScene {
  id: number
  scene_index: number
  page_index: number
  draft_title: string | null
  draft_text: string
  draft_prompt_tags: string
  seed_cue: string | null
  novelai_text: string | null
}

interface StoryData {
  id: number
  premise: string
  n_scenes: number
  panels_per_page: number
  status: string
  final_image_path: string | null
  scenes: StoryScene[]
}

interface StorySummary {
  id: number
  premise: string
  title: string | null
  status: string
  created_at: string
  final_image_path: string | null
}

interface MangaPage {
  id: number
  page_index: number
  image_path: string
}

function shortId(): string {
  return Math.random().toString(16).slice(2, 10)
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

// ViteのdevサーバープロキシはSSE(text/event-stream)を丸ごとバッファしてしまい、
// 完了するまで1バイトも中継しない(実機で確認済み: バックエンド直では文字単位で
// 届くのに、Vite経由だと30秒待っても0バイト)。backendのヘッダー
// (transfer-encoding: chunked / x-accel-buffering: no)は既に正しいのでこれは
// Vite側の制限。念のためこのページの/api/story/*呼び出しは全てプロキシを迂回して
// バックエンド(ポート8000)に直接アクセスする。CORSはバックエンド側で allow_origins=["*"]
// になっているため問題ない。
const API_ORIGIN = `${window.location.protocol}//${window.location.hostname}:8000`

/**
 * サーバーが event:progress / event:done / event:error の3種類だけ送ってくる
 * 前提のSSE専用クライアント。progressのたびに onProgress を呼び、done の
 * ペイロードで解決する。ユーザーによる中断は AbortSignal で行い、
 * DOMException("AbortError") をそのまま呼び出し元に伝播させる。
 */
async function postSSE<T>(
  path: string,
  signal: AbortSignal,
  onProgress: (message: string) => void,
  body?: unknown,
): Promise<T> {
  const res = await fetch(`${API_ORIGIN}${path}`, {
    method: 'POST',
    ...(body !== undefined
      ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
      : {}),
    signal,
  })
  if (!res.ok || !res.body) {
    throw new Error(await readErrorDetail(res))
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let sepIndex: number
    while ((sepIndex = buffer.indexOf('\n\n')) !== -1) {
      const rawEvent = buffer.slice(0, sepIndex)
      buffer = buffer.slice(sepIndex + 2)

      let eventName = 'message'
      let dataLine = ''
      for (const line of rawEvent.split('\n')) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLine = line.slice(5).trim()
      }
      if (!dataLine) continue
      const data = JSON.parse(dataLine)

      if (eventName === 'progress') {
        onProgress(data.message)
      } else if (eventName === 'done') {
        return data as T
      } else if (eventName === 'error') {
        throw new Error(data.detail ?? '不明なエラー')
      }
    }
  }

  throw new Error('サーバーからの応答が途中で終了しました')
}

function mangaFileUrl(path: string): string {
  return `${API_ORIGIN}/api/story/manga-file?path=${encodeURIComponent(path)}`
}

function DownloadableImage({ path, className, alt }: { path: string; className: string; alt: string }) {
  const [url, setUrl] = useState<string | null>(null)

  useEffect(() => {
    let objectUrl: string | null = null
    fetch(mangaFileUrl(path))
      .then(r => r.blob())
      .then(blob => {
        objectUrl = URL.createObjectURL(blob)
        setUrl(objectUrl)
      })
    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [path])

  const filename = useMemo(() => `manga_${shortId()}.png`, [path])

  if (!url) return <p className="story-muted">画像を読み込み中...</p>
  return (
    <a href={url} download={filename}>
      <img className={className} src={url} alt={alt} />
    </a>
  )
}

export default function Story() {
  const navigate = useNavigate()

  const [premise, setPremise] = useState('')
  const [nScenes, setNScenes] = useState(4)
  const [panelsPerPage, setPanelsPerPage] = useState(4)

  const [history, setHistory] = useState<StorySummary[]>([])
  const [story, setStory] = useState<StoryData | null>(null)
  const [mangaPages, setMangaPages] = useState<MangaPage[]>([])

  const [stepLabel, setStepLabel] = useState('')
  const [error, setError] = useState<string | null>(null)

  const abortRef = useRef<AbortController | null>(null)
  const busy = stepLabel !== ''

  const isWritten = (story?.scenes.length ?? 0) > 0 && story!.scenes.every(s => s.novelai_text)

  function loadHistory() {
    fetch(`${API_ORIGIN}/api/story?limit=20`)
      .then(r => r.json())
      .then(setHistory)
      .catch(() => {/* サイレント失敗 */})
  }

  useEffect(() => {
    loadHistory()
  }, [])

  function cancel() {
    abortRef.current?.abort()
  }

  async function loadStory(id: number) {
    setError(null)
    try {
      const [storyRes, pagesRes] = await Promise.all([
        fetch(`${API_ORIGIN}/api/story/${id}`),
        fetch(`${API_ORIGIN}/api/story/${id}/pages`),
      ])
      if (!storyRes.ok) throw new Error(await readErrorDetail(storyRes))
      const data: StoryData = await storyRes.json()
      const pages: MangaPage[] = pagesRes.ok ? await pagesRes.json() : []
      setStory(data)
      setMangaPages(pages)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function createDraft() {
    setError(null)
    const controller = new AbortController()
    abortRef.current = controller
    setStepLabel('Ollamaでシーン分割ドラフトを作成中...')
    try {
      const data = await postSSE<StoryData>(
        '/api/story/draft',
        controller.signal,
        setStepLabel,
        { premise, n_scenes: nScenes, panels_per_page: panelsPerPage },
      )
      setStory(data)
      setMangaPages([])
      loadHistory()
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') {
        setError('キャンセルしました。')
      } else {
        setError(e instanceof Error ? e.message : String(e))
      }
    } finally {
      setStepLabel('')
      abortRef.current = null
    }
  }

  async function runWrite() {
    if (!story) return
    setError(null)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      setStepLabel('NovelAI公式(Kayra)で本文を執筆中...')
      const written = await postSSE<StoryData>(`/api/story/${story.id}/write`, controller.signal, setStepLabel)
      setStory(written)
      loadHistory()
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') {
        setError('キャンセルしました。途中まで進んだシーンはそのまま残っています。')
      } else {
        setError(e instanceof Error ? e.message : String(e))
      }
    } finally {
      setStepLabel('')
      abortRef.current = null
    }
  }

  async function runIllustrateAndExport() {
    if (!story) return
    setError(null)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      setStepLabel('NovelAI Diffusion V5でコマ割り済み挿絵を生成中...')
      const pages = await postSSE<MangaPage[]>(`/api/story/${story.id}/illustrate`, controller.signal, setStepLabel)
      setMangaPages(pages)

      setStepLabel('全ページを1枚の漫画に合成中...')
      const res = await fetch(`${API_ORIGIN}/api/story/${story.id}/export-manga`, {
        method: 'POST',
        signal: controller.signal,
      })
      if (!res.ok) throw new Error(await readErrorDetail(res))
      const { image_path } = await res.json()
      setStory({ ...story, final_image_path: image_path })
      loadHistory()
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') {
        setError('キャンセルしました。')
      } else {
        setError(e instanceof Error ? e.message : String(e))
      }
    } finally {
      setStepLabel('')
      abortRef.current = null
    }
  }

  function reset() {
    setStory(null)
    setMangaPages([])
    setError(null)
    loadHistory()
  }

  return (
    <div className="story-root">
      <div className="story-inner">
        <button type="button" className="story-back" onClick={() => navigate('/')}>
          ← ホームへ
        </button>
        <h1>物語 → 挿絵 → 漫画</h1>
        <p className="story-intro">
          前提からOllamaでシーン分割ドラフトを作り、各シーンをNovelAI公式(Kayra)に書き継がせ、
          NovelAI Diffusion V5でページごとにコマ割り済みの挿絵を生成し、最後に1枚の漫画へまとめます。
        </p>

        {error && <div className="story-error">{error}</div>}

        {!story && (
          <>
            <section className="story-section">
              <h2>1. 前提を入力</h2>
              <textarea
                className="story-premise"
                placeholder="例: 田舎町に引っ越してきた少女が、廃校になった小学校で不思議な猫と出会う話。"
                value={premise}
                onChange={e => setPremise(e.target.value)}
                rows={4}
              />
              <div className="story-row">
                <label>
                  シーン数
                  <input
                    type="number"
                    min={1}
                    max={20}
                    value={nScenes}
                    onChange={e => setNScenes(Number(e.target.value))}
                  />
                </label>
                <label>
                  1ページあたりのコマ数
                  <input
                    type="number"
                    min={1}
                    max={8}
                    value={panelsPerPage}
                    onChange={e => setPanelsPerPage(Number(e.target.value))}
                  />
                </label>
              </div>
              <div className="story-actions">
                <button type="button" onClick={createDraft} disabled={!premise.trim() || busy}>
                  ドラフト生成
                </button>
                {busy && (
                  <button type="button" className="story-cancel" onClick={cancel}>
                    キャンセル
                  </button>
                )}
              </div>
              {stepLabel && <p className="story-step">{stepLabel}</p>}
            </section>

            {history.length > 0 && (
              <section className="story-section">
                <h2>過去の物語(ブラウザ再読み込みしても続きから再開できます)</h2>
                <ul className="story-history-list">
                  {history.map(h => (
                    <li key={h.id} className="story-history-item" onClick={() => loadStory(h.id)}>
                      <span className="story-history-premise">{h.premise}</span>
                      <span className="story-history-meta">
                        {h.status} ・ {new Date(h.created_at).toLocaleString()}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </>
        )}

        {story && (
          <section className="story-section">
            <h2>シーン一覧</h2>
            <ol className="story-scene-list">
              {story.scenes.map(scene => (
                <li key={scene.id} className="story-scene">
                  <div className="story-scene-title">
                    シーン{scene.scene_index + 1}{scene.draft_title ? `: ${scene.draft_title}` : ''}
                  </div>
                  <p className="story-scene-text">
                    {scene.novelai_text ? `${scene.seed_cue ?? ''}${scene.novelai_text}` : scene.draft_text}
                  </p>
                  {scene.draft_prompt_tags && (
                    <p className="story-scene-tags">タグ: {scene.draft_prompt_tags}</p>
                  )}
                </li>
              ))}
            </ol>

            <div className="story-actions">
              {!isWritten && (
                <button type="button" onClick={runWrite} disabled={busy}>
                  本編執筆(NovelAI公式)
                </button>
              )}
              {isWritten && (
                <button type="button" onClick={runIllustrateAndExport} disabled={busy}>
                  {mangaPages.length > 0 ? '挿絵を再生成 → 漫画化' : '挿絵を生成 → 漫画化'}
                </button>
              )}
              {busy && (
                <button type="button" className="story-cancel" onClick={cancel}>
                  キャンセル
                </button>
              )}
              <button type="button" className="story-secondary" onClick={reset}>
                最初からやり直す
              </button>
            </div>
            {stepLabel && <p className="story-step">{stepLabel}</p>}
          </section>
        )}

        {story && mangaPages.length > 0 && (
          <section className="story-section">
            <h2>ページごとの挿絵</h2>
            <div className="story-pages-grid">
              {mangaPages.map(page => (
                <img
                  key={page.id}
                  className="story-page-thumb"
                  loading="lazy"
                  src={mangaFileUrl(page.image_path)}
                  alt={`ページ${page.page_index + 1}`}
                />
              ))}
            </div>
          </section>
        )}

        {story?.final_image_path && (
          <section className="story-section">
            <h2>完成した漫画</h2>
            <DownloadableImage path={story.final_image_path} className="story-final-image" alt="完成した漫画" />
          </section>
        )}
      </div>
    </div>
  )
}
