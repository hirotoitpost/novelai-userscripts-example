import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { API_ORIGIN, deletePublication, downloadFile, formatDate, getJson, Publication } from '../library'
import './Publications.css'

/**
 * 出版物の一覧。ギャラリー・本棚で選んで「出版する」と、画像から NovelAI の生成情報(メタデータ)を取り除いたものが
 * 別に保存され、ZIP にまとまる。ここでは、その ZIP を受け取る・消すができる(消しても元の画像・本は残る)。
 */

function bytes(n: number): string {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(2)} GB`
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${Math.max(1, Math.round(n / 1024))} KB`
}

function summary(publication: Publication): string {
  return publication.items
    .map(item => (item.kind === 'book' ? `「${item.title}」${item.files}ページ` : `画像 ${item.files}枚`))
    .join('、')
}

export default function Publications() {
  const navigate = useNavigate()
  const [publications, setPublications] = useState<Publication[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      setPublications(await getJson<Publication[]>('/api/publications'))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  async function download(publication: Publication) {
    setBusy(publication.id)
    setError(null)
    try {
      await downloadFile(`/api/publications/${publication.id}/download`, `${publication.title}.zip`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function remove(publication: Publication) {
    if (!window.confirm(`出版物「${publication.title}」を消します(保存した画像と ZIP)。元の画像・本は消えません。よろしいですか?`)) return
    setBusy(publication.id)
    setError(null)
    try {
      await deletePublication(publication.id)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="pub-root">
      <header className="pub-header">
        <button type="button" className="pub-link" onClick={() => navigate('/')}>← ホーム</button>
        <h1>出版物</h1>
        <span className="pub-count">{publications.length} 件</span>
        <button type="button" className="pub-link" onClick={() => navigate('/gallery')}>ギャラリーへ</button>
        <button type="button" className="pub-link" onClick={() => navigate('/bookshelf')}>本棚へ</button>
      </header>

      <main className="pub-main">
        <p className="pub-hint">
          ギャラリーか本棚で「選択」して「出版する」と、選んだ画像・本から NovelAI の生成情報(プロンプト・シード値などの
          メタデータ)を取り除いたものが、ここに出版物として保存されます。元の画像・本はそのまま残ります。
        </p>
        {error && <p className="pub-error" role="alert">{error}</p>}
        {!loading && publications.length === 0 && !error && <p className="pub-hint">まだありません。</p>}
        <ul className="pub-list">
          {publications.map(publication => (
            <li key={publication.id} className="pub-item">
              <img className="pub-cover" src={`${API_ORIGIN}/api/publications/${publication.id}/cover`} alt="" loading="lazy"
                onError={e => { e.currentTarget.style.visibility = 'hidden' }} />
              <div className="pub-body">
                <strong>{publication.title}</strong>
                <span className="pub-meta">
                  {publication.file_count} ファイル ・ {bytes(publication.bytes)} ・ {formatDate(publication.created_at)}
                </span>
                <span className="pub-meta">{summary(publication)}</span>
                <span className="pub-meta"><code>{publication.zip_path}</code></span>
              </div>
              <div className="pub-actions">
                <button type="button" className="pub-primary" disabled={busy === publication.id} onClick={() => void download(publication)}>
                  ZIP をダウンロード
                </button>
                <button type="button" className="pub-danger" disabled={busy === publication.id} onClick={() => void remove(publication)}>
                  消す
                </button>
              </div>
            </li>
          ))}
        </ul>
      </main>
    </div>
  )
}
