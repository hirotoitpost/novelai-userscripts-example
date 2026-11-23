import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { API_ORIGIN, formatDate, readErrorDetail, thumbUrl } from '../library'
import './Watermarks.css'

/**
 * 見えない透かし。X への投稿と出版物(ZIP)の画像には、目に見えない透かし(番号)が自動で入り、台帳に残る。
 * ここでは、ネットで見かけた画像が自分の出したものかを照合し、台帳(何をいつどこへ出したか)を見る。
 */

interface Mark {
  id: number
  mark_id: number
  purpose: string
  purpose_label: string
  source_path: string
  ref: string | null
  label: string
  x_post_id: number | null
  x_post_url: string | null
  publication_id: number | null
  publication_title: string | null
  width: number
  height: number
  created_at: string
}

interface Verify {
  found: boolean
  method: 'exact' | 'watermark' | 'watermark_resized' | 'similar' | 'none'
  mark_id: number | null
  distance: number | null
  record: Mark | null
  foreign_mark?: number | null
}

const METHOD_TEXT: Record<Verify['method'], string> = {
  exact: '出したファイルとまったく同じです。',
  watermark: '画像の中の透かしを読めました。自分が出した画像です。',
  watermark_resized: '縮小された画像でしたが、元の大きさに戻して透かしを読めました。自分が出した画像です。',
  similar: '透かしは読めませんでしたが、見た目がほぼ同じ画像を出しています(切り抜き・加工されているかもしれません)。',
  none: '台帳に一致する画像はありません。',
}

function markText(id: number): string {
  return id.toString(16).toUpperCase().padStart(8, '0')
}

export default function Watermarks() {
  const navigate = useNavigate()
  const [marks, setMarks] = useState<Mark[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<string | null>(null)
  const [result, setResult] = useState<Verify | null>(null)
  const [dragging, setDragging] = useState(false)

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_ORIGIN}/api/watermarks`)
      if (!res.ok) throw new Error(await readErrorDetail(res))
      setMarks(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview)
  }, [preview])

  async function verify(file: File) {
    setError(null)
    setResult(null)
    setPreview(URL.createObjectURL(file))
    setBusy(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch(`${API_ORIGIN}/api/watermarks/verify`, { method: 'POST', body: form })
      if (!res.ok) throw new Error(await readErrorDetail(res))
      setResult(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function onFiles(files: FileList | null) {
    const file = files?.[0]
    if (file) void verify(file)
  }

  const record = result?.record ?? null

  return (
    <div className="wm-root">
      <header className="wm-header">
        <button type="button" className="wm-link" onClick={() => navigate('/')}>← ホーム</button>
        <h1>見えない透かし</h1>
      </header>

      <main className="wm-main">
        <p className="wm-lead">
          X への投稿と出版物(ZIP)の画像には、目に見えない透かし(番号)が自動で入り、下の台帳に残ります。
          ネットで見かけた画像を入れると、自分の出したものかを確かめられます。
          JPEG への再圧縮や軽い縮小のあとでも読めますが、切り抜き・回転・大きな縮小では読めないことがあります
          (そのときは見た目の指紋で照合します)。
        </p>
        {error && <p className="wm-error">{error}</p>}

        <section className="wm-section">
          <h2>照合する</h2>
          <label
            className={`wm-drop${dragging ? ' is-dragging' : ''}`}
            onDragOver={e => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={e => { e.preventDefault(); setDragging(false); onFiles(e.dataTransfer.files) }}
          >
            <input type="file" accept="image/*" onChange={e => { onFiles(e.target.files); e.target.value = '' }} />
            {busy ? '照合しています…' : '画像をここに置くか、押して選んでください'}
          </label>
          {(preview || result) && (
            <div className="wm-result">
              {preview && <img src={preview} alt="照合した画像" />}
              {result && (
                <div className={`wm-verdict${result.found ? ' is-found' : ''}`}>
                  <strong>{result.found ? (result.method === 'similar' ? 'よく似た画像を出しています' : '自分の画像です') : '見つかりません'}</strong>
                  <p>{METHOD_TEXT[result.method]}</p>
                  {result.mark_id !== null && <p>透かしの番号: <code>{markText(result.mark_id)}</code></p>}
                  {result.distance !== null && result.method !== 'exact' && (
                    <p className="wm-meta">見た目の指紋の差: {result.distance} / 64(6 以下ならほぼ同じ絵)</p>
                  )}
                  {result.foreign_mark != null && (
                    <p className="wm-meta">台帳に無い番号の透かし({markText(result.foreign_mark)})が読めました。別の鍵や別の環境で入れたものかもしれません。</p>
                  )}
                  {record && (
                    <div className="wm-record">
                      <img src={thumbUrl(record.source_path, 240)} alt={record.label} loading="lazy" />
                      <div>
                        <p>{result.found ? '出した記録' : 'いちばん近い記録'}: {record.purpose_label} ・ {formatDate(record.created_at)}</p>
                        {record.label && <p>{record.label}</p>}
                        {record.x_post_url && <a href={record.x_post_url} target="_blank" rel="noreferrer">X の投稿を開く</a>}
                        {record.publication_title && <p>出版物: {record.publication_title}</p>}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </section>

        <section className="wm-section">
          <h2>台帳 <span className="wm-meta">{marks.length} 件</span></h2>
          {marks.length === 0 ? (
            <p className="wm-meta">まだありません。X に投稿するか、出版物を作ると記録されます。</p>
          ) : (
            <ul className="wm-list">
              {marks.map(mark => (
                <li key={mark.id}>
                  <img src={thumbUrl(mark.source_path, 240)} alt={mark.label} loading="lazy" />
                  <div>
                    <p><code>{markText(mark.mark_id)}</code> ・ {mark.purpose_label}</p>
                    <p className="wm-meta">{formatDate(mark.created_at)} ・ {mark.width}×{mark.height}</p>
                    {mark.label && <p className="wm-label">{mark.label}</p>}
                    {mark.x_post_url && <a href={mark.x_post_url} target="_blank" rel="noreferrer">X で見る</a>}
                    {mark.publication_title && <p className="wm-meta">出版物: {mark.publication_title}</p>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>
    </div>
  )
}
