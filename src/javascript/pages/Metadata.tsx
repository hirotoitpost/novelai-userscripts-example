import { useState, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import {
  apiFetch,
  MetadataExtractResponse,
  MetadataEraseResponse,
} from '../api'
import './Metadata.css'

function expandMetadata(meta: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(meta)) {
    if (typeof v === 'string' && (v.startsWith('{') || v.startsWith('['))) {
      try { result[k] = JSON.parse(v) } catch { result[k] = v }
    } else {
      result[k] = v
    }
  }
  return result
}

export default function Metadata() {
  const { token } = useAuth()
  const navigate = useNavigate()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const [fileName,   setFileName]   = useState<string>('')
  const [imageB64,   setImageB64]   = useState<string | null>(null)
  const [metadata,   setMetadata]   = useState<Record<string, unknown> | null>(null)
  const [extracting, setExtracting] = useState(false)
  const [eraseTarget, setEraseTarget] = useState<'alpha' | 'png_info' | 'both'>('both')
  const [erasing,    setErasing]    = useState(false)
  const [cleanedB64, setCleanedB64] = useState<string | null>(null)
  const [error,      setError]      = useState<string | null>(null)
  const [dragOver,   setDragOver]   = useState(false)

  const processFile = useCallback(async (file: File) => {
    if (!file.type.match(/^image\//)) {
      setError('画像ファイルを選択してください')
      return
    }
    setFileName(file.name)
    setCleanedB64(null)
    setMetadata(null)
    setError(null)

    const reader = new FileReader()
    reader.onload = async (e) => {
      const dataUrl = e.target?.result as string
      setImageB64(dataUrl)
      setExtracting(true)
      try {
        const data = await apiFetch<MetadataExtractResponse>(
          token!,
          '/api/metadata/extract',
          { image: dataUrl },
        )
        setMetadata(data.metadata)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setExtracting(false)
      }
    }
    reader.readAsDataURL(file)
  }, [token])

  const handleErase = async () => {
    if (!imageB64) return
    setErasing(true)
    setError(null)
    setCleanedB64(null)
    try {
      const data = await apiFetch<MetadataEraseResponse>(
        token!,
        '/api/metadata/erase',
        { image: imageB64, target: eraseTarget },
      )
      setCleanedB64(data.image)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setErasing(false)
    }
  }

  const handleDownloadCleaned = () => {
    if (!cleanedB64) return
    const a = document.createElement('a')
    a.href = `data:image/png;base64,${cleanedB64}`
    const base = fileName.replace(/\.[^.]+$/, '')
    a.download = `${base}_clean.png`
    a.click()
  }

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files[0]
    if (file) processFile(file)
  }, [processFile])

  const metaJson = metadata
    ? JSON.stringify(expandMetadata(metadata), null, 2)
    : null
  const hasMetadata = metadata !== null && Object.keys(metadata).length > 0

  return (
    <div className="meta-root">
      <header className="meta-header">
        <button type="button" className="meta-back" onClick={() => navigate('/')}>
          ← ホーム
        </button>
        <span className="meta-header-title">メタデータ管理</span>
      </header>

      <div className="meta-body">
        {/* ===== Left: upload / preview ===== */}
        <aside className="meta-sidebar">
          <div
            className={[
              'meta-drop-zone',
              dragOver  ? 'meta-drop-zone--over'      : '',
              imageB64  ? 'meta-drop-zone--has-image' : '',
            ].join(' ')}
            onDragOver={e => { e.preventDefault(); setDragOver(true) }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            role="button"
            tabIndex={0}
            onKeyDown={e => e.key === 'Enter' && fileInputRef.current?.click()}
            aria-label="画像をドロップまたはクリックして選択"
          >
            {imageB64 ? (
              <img className="meta-preview-img" src={imageB64} alt="アップロード画像のプレビュー" />
            ) : (
              <>
                <span className="meta-drop-icon" aria-hidden="true">🖼️</span>
                <p className="meta-drop-label">PNG / WebP をドロップ</p>
                <p className="meta-drop-hint">またはクリックして選択</p>
              </>
            )}
          </div>

          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/webp"
            hidden
            onChange={e => {
              const file = e.target.files?.[0]
              if (file) processFile(file)
              e.target.value = ''
            }}
          />

          {fileName && (
            <p className="meta-filename" title={fileName}>{fileName}</p>
          )}
        </aside>

        {/* ===== Right: metadata + erase ===== */}
        <main className="meta-main">
          {!imageB64 && !error && (
            <div className="meta-empty">
              <span className="meta-empty-icon" aria-hidden="true">📋</span>
              <p>画像をアップロードするとメタデータが表示されます</p>
            </div>
          )}

          {extracting && (
            <div className="meta-empty">
              <span className="meta-spinner" aria-label="抽出中" />
              <p>メタデータを抽出中…</p>
            </div>
          )}

          {!extracting && imageB64 && (
            <>
              <section className="meta-section">
                <span className="meta-label">メタデータ</span>
                {hasMetadata ? (
                  <pre className="meta-json">{metaJson}</pre>
                ) : (
                  <p className="meta-no-meta">メタデータが見つかりませんでした</p>
                )}
              </section>

              <section className="meta-section meta-erase-section">
                <span className="meta-label">メタデータ消去</span>
                <div className="meta-erase-controls">
                  <label className="meta-field-label" htmlFor="meta-erase-target">消去対象</label>
                  <select
                    id="meta-erase-target"
                    className="meta-select"
                    value={eraseTarget}
                    onChange={e => {
                      setEraseTarget(e.target.value as typeof eraseTarget)
                      setCleanedB64(null)
                    }}
                  >
                    <option value="png_info">PNG メタデータのみ</option>
                    <option value="alpha">アルファチャンネルのみ</option>
                    <option value="both">両方</option>
                  </select>
                  <button
                    type="button"
                    className="meta-erase-btn"
                    onClick={handleErase}
                    disabled={erasing}
                  >
                    {erasing
                      ? <span className="meta-spinner meta-spinner--sm" aria-label="処理中" />
                      : '🗑️ 消去する'}
                  </button>
                </div>

                {cleanedB64 && (
                  <button
                    type="button"
                    className="meta-download-btn"
                    onClick={handleDownloadCleaned}
                  >
                    ↓ クリーン画像をダウンロード
                  </button>
                )}
              </section>
            </>
          )}

          {error && <p className="meta-error" role="alert">{error}</p>}
        </main>
      </div>
    </div>
  )
}
