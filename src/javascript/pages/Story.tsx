import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { decodeStoryDocument } from '../novelaiDocument'
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
  raw_text: string | null
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

/** GET /api/story/remote のレスポンス。document は base64+msgpack の生データ。 */
interface RemoteStoryResponse {
  remote_object_id: string | null
  title: string
  description: string
  text_preview: string
  document: string | null
}

/** document をプレーンテキストへ復元したもの(復元できなければ text は空)。 */
interface RemoteStory extends RemoteStoryResponse {
  text: string
}

// ChunksページのNovelAI公式同期機能と同じlocalStorageキーを使い、
// どちらかのページで一度計算した復号鍵をもう片方でも使い回せるようにする。
const ENCRYPTION_KEY_STORAGE = 'nai_encryption_key'

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
  const { token } = useAuth()

  const [premise, setPremise] = useState('')
  const [nScenes, setNScenes] = useState(4)
  const [panelsPerPage, setPanelsPerPage] = useState(4)

  // シーン分割の条件。シーン数はこの上限から結果として決まる。
  const [maxParagraphs, setMaxParagraphs] = useState(6)
  const [maxChars, setMaxChars] = useState(300)
  // 挿絵を生成するページ範囲(表示は1始まり)。
  const [pageFrom, setPageFrom] = useState(1)
  const [pageTo, setPageTo] = useState(5)
  const [importText, setImportText] = useState('')

  const [remoteEmail, setRemoteEmail] = useState('')
  const [remotePassword, setRemotePassword] = useState('')
  const [encryptionKey, setEncryptionKey] = useState<string | null>(() =>
    localStorage.getItem(ENCRYPTION_KEY_STORAGE)
  )
  const [remoteStories, setRemoteStories] = useState<RemoteStory[] | null>(null)
  const [remoteLoading, setRemoteLoading] = useState(false)
  const [remoteError, setRemoteError] = useState<string | null>(null)

  const [history, setHistory] = useState<StorySummary[]>([])
  const [story, setStory] = useState<StoryData | null>(null)
  const [mangaPages, setMangaPages] = useState<MangaPage[]>([])

  const [stepLabel, setStepLabel] = useState('')
  const [error, setError] = useState<string | null>(null)

  const abortRef = useRef<AbortController | null>(null)
  const busy = stepLabel !== ''

  const isWritten = (story?.scenes.length ?? 0) > 0 && story!.scenes.every(s => s.novelai_text)
  const isUnsplitImport = (story?.scenes.length ?? 0) === 0 && !!story?.raw_text
  const totalPages = story ? Math.max(...story.scenes.map(s => s.page_index + 1), 0) : 0

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

  async function importStory(text: string = importText) {
    // シーン分割・タグ付けは行わず、本文をそのままDBへ保存するだけなのでOllama呼び出しが
    // 無く即座に終わる。SSEにする必要はないため通常のfetchで十分。
    setError(null)
    setImportText(text)
    setStepLabel('物語を取り込み中...')
    try {
      const res = await fetch(`${API_ORIGIN}/api/story/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, n_scenes: nScenes, panels_per_page: panelsPerPage }),
      })
      if (!res.ok) throw new Error(await readErrorDetail(res))
      const data: StoryData = await res.json()
      setStory(data)
      setMangaPages([])
      setImportText('')
      loadHistory()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setStepLabel('')
    }
  }

  async function runSplit() {
    if (!story) return
    setError(null)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      setStepLabel('シーン分割・タグ付けを実行中...')
      const result = await postSSE<StoryData>(
        `/api/story/${story.id}/split`,
        controller.signal,
        setStepLabel,
        { max_paragraphs: maxParagraphs, max_chars: maxChars },
      )
      setStory(result)
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

  async function computeRemoteKey() {
    setRemoteError(null)
    setRemoteLoading(true)
    try {
      const res = await fetch(`${API_ORIGIN}/api/chunks/encryption-key`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: remoteEmail, password: remotePassword }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail ?? '鍵の計算に失敗しました')
      localStorage.setItem(ENCRYPTION_KEY_STORAGE, data.encryption_key)
      setEncryptionKey(data.encryption_key)
      setRemotePassword('')
    } catch (e) {
      setRemoteError(e instanceof Error ? e.message : String(e))
    } finally {
      setRemoteLoading(false)
    }
  }

  function forgetRemoteKey() {
    localStorage.removeItem(ENCRYPTION_KEY_STORAGE)
    setEncryptionKey(null)
    setRemoteStories(null)
  }

  async function fetchRemoteStories() {
    if (!token || !encryptionKey) return
    setRemoteError(null)
    setRemoteLoading(true)
    try {
      const res = await fetch(
        `${API_ORIGIN}/api/story/remote?encryption_key=${encodeURIComponent(encryptionKey)}`,
        { headers: { Authorization: `Bearer ${token}` } },
      )
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail ?? '物語の取得に失敗しました')
      const decoded: RemoteStory[] = (data as RemoteStoryResponse[]).map(r => ({
        ...r,
        text: r.document ? decodeStoryDocument(r.document) : '',
      }))
      setRemoteStories(decoded)
    } catch (e) {
      setRemoteError(e instanceof Error ? e.message : String(e))
    } finally {
      setRemoteLoading(false)
    }
  }

  function importRemoteStory(remote: RemoteStory) {
    void importStory(remote.text)
  }

  async function importAllRemoteStories() {
    const importable = (remoteStories ?? []).filter(r => r.text)
    if (importable.length === 0) return
    setError(null)
    const failed: string[] = []
    for (let i = 0; i < importable.length; i++) {
      const remote = importable[i]
      setStepLabel(`一括取り込み中(${i + 1}/${importable.length}): ${remote.title}`)
      try {
        const res = await fetch(`${API_ORIGIN}/api/story/import`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: remote.text, n_scenes: nScenes, panels_per_page: panelsPerPage }),
        })
        if (!res.ok) throw new Error(await readErrorDetail(res))
      } catch (e) {
        failed.push(`${remote.title}(${e instanceof Error ? e.message : String(e)})`)
      }
    }
    setStepLabel('')
    if (failed.length > 0) {
      setError(`${importable.length - failed.length}/${importable.length}件取り込みました。失敗: ${failed.join(', ')}`)
    }
    loadHistory()
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
      const from = Math.max(1, Math.min(pageFrom, totalPages))
      const to = Math.max(from, Math.min(pageTo, totalPages))
      setStepLabel(`NovelAI Diffusion V5でコマ割り済み挿絵を生成中(${from}〜${to}ページ)...`)
      const pages = await postSSE<MangaPage[]>(
        `/api/story/${story.id}/illustrate`,
        controller.signal,
        setStepLabel,
        { page_from: from - 1, page_to: to - 1 },
      )
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
        <button type="button" className="story-back" onClick={() => navigate('/story-library')}>
          📚 物語を読む
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

            <section className="story-section">
              <h2>1b. または、公式サイトで書いた物語をインポート</h2>
              <p className="story-intro">
                NovelAI公式サイトのストーリーエディタなどで既に書いた本文を取り込むと、
                本文を書き換えずに場面へ分割します(NovelAI公式Kayraによる自動執筆はスキップされ、
                そのまま挿絵生成に進めます)。
              </p>
              <p className="story-intro">
                <strong>推奨:</strong> 下の「公式サイトの物語一覧を取得」を押すと、アカウント内の
                物語をまとめて取得できます(本文はブラウザ側で復元します)。一覧から個別に、または
                「すべて取り込む」で一括インポートできます。
              </p>
              <p className="story-intro">
                うまく取得できない場合は、NovelAI公式の「Export Story → As Plaintext」で
                <code>.txt</code>をダウンロードして中身を下の欄に貼り付けるか、
                <code>scripts/export-story.naiscript</code> と <code>scripts/story-import.user.js</code>
                を使ったクリップボード経由の取り込みも利用できます。
              </p>

              <div className="story-remote">
                {encryptionKey ? (
                  <div className="story-row">
                    <span>✓ 復号鍵 設定済み</span>
                    <button type="button" onClick={forgetRemoteKey}>削除</button>
                    <button type="button" onClick={fetchRemoteStories} disabled={remoteLoading || !token}>
                      {remoteLoading ? '取得中...' : '公式サイトの物語一覧を取得'}
                    </button>
                  </div>
                ) : (
                  <div className="story-row">
                    <input
                      type="email"
                      placeholder="メールアドレス"
                      value={remoteEmail}
                      onChange={e => setRemoteEmail(e.target.value)}
                    />
                    <input
                      type="password"
                      placeholder="パスワード"
                      value={remotePassword}
                      onChange={e => setRemotePassword(e.target.value)}
                    />
                    <button
                      type="button"
                      onClick={computeRemoteKey}
                      disabled={remoteLoading || !remoteEmail || !remotePassword}
                    >
                      鍵を計算して保存
                    </button>
                  </div>
                )}
                {!token && (
                  <p className="story-muted">
                    直接取得にはログインが必要です(復号鍵の計算にはメール・パスワードのみ使用し、保存されません)。
                  </p>
                )}
                {remoteError && <div className="story-error">{remoteError}</div>}

                {remoteStories && remoteStories.some(r => r.text) && (
                  <div className="story-row">
                    <button type="button" onClick={importAllRemoteStories} disabled={busy}>
                      すべて取り込む({remoteStories.filter(r => r.text).length}件)
                    </button>
                  </div>
                )}

                {remoteStories && (
                  <ul className="story-history-list">
                    {remoteStories.length === 0 && <li className="story-muted">物語が見つかりませんでした。</li>}
                    {remoteStories.map(remote => (
                      <li key={remote.remote_object_id ?? remote.title} className="story-history-item">
                        <span className="story-history-premise">{remote.title}</span>
                        <span className="story-history-meta">
                          {remote.text ? `${remote.text.length}文字` : '本文を自動取得できませんでした(貼り付けをご利用ください)'}
                        </span>
                        {remote.text && (
                          <button type="button" onClick={() => importRemoteStory(remote)} disabled={busy}>
                            この物語を取り込む
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <textarea
                className="story-premise"
                placeholder="ここに物語本文を貼り付け..."
                value={importText}
                onChange={e => setImportText(e.target.value)}
                rows={8}
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
                <button type="button" onClick={() => importStory()} disabled={!importText.trim() || busy}>
                  インポート
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
            {isUnsplitImport ? (
              <div className="story-raw-preview">
                <p className="story-muted">
                  まだシーン分割・タグ付け前です。取り込んだ本文をそのままDBへ保存してあります。
                </p>
                <p className="story-scene-text">{story.raw_text}</p>
              </div>
            ) : (
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
            )}

            {isUnsplitImport && (
              <div className="story-row">
                <label>
                  1シーンの段落数上限
                  <input
                    type="number"
                    min={1}
                    max={100}
                    value={maxParagraphs}
                    onChange={e => setMaxParagraphs(Number(e.target.value))}
                  />
                </label>
                <label>
                  1シーンの文字数上限
                  <input
                    type="number"
                    min={50}
                    max={5000}
                    step={50}
                    value={maxChars}
                    onChange={e => setMaxChars(Number(e.target.value))}
                  />
                </label>
              </div>
            )}

            {isWritten && (
              <div className="story-row">
                <label>
                  挿絵の開始ページ
                  <input
                    type="number"
                    min={1}
                    max={totalPages}
                    value={pageFrom}
                    onChange={e => setPageFrom(Number(e.target.value))}
                  />
                </label>
                <label>
                  挿絵の終了ページ(全{totalPages}ページ)
                  <input
                    type="number"
                    min={1}
                    max={totalPages}
                    value={pageTo}
                    onChange={e => setPageTo(Number(e.target.value))}
                  />
                </label>
              </div>
            )}

            <div className="story-actions">
              {isUnsplitImport && (
                <button type="button" onClick={runSplit} disabled={busy}>
                  シーン分割・タグ付けを実行
                </button>
              )}
              {!isUnsplitImport && !isWritten && (
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
