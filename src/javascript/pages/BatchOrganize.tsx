import { useState, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import {
  apiFetch,
  streamBatchOrganize,
  BatchPreviewResponse,
  BatchGroupInfo,
  BatchProgressEvent,
  BatchCompleteEvent,
} from '../api'
import './BatchOrganize.css'

type Phase = 'idle' | 'previewing' | 'preview_done' | 'scanning' | 'organizing' | 'complete' | 'error'

interface LogEntry {
  file: string
  dest: string
  status: BatchProgressEvent['status']
  message?: string
}

export default function BatchOrganize() {
  const { token } = useAuth()
  const navigate = useNavigate()

  const [inputPath,   setInputPath]   = useState('')
  const [outputPath,  setOutputPath]  = useState('')
  const [threshold,   setThreshold]   = useState(0.75)
  const [operation,   setOperation]   = useState<'copy' | 'move'>('copy')
  const [saveJson,    setSaveJson]    = useState(true)

  const [phase,       setPhase]       = useState<Phase>('idle')
  const [preview,     setPreview]     = useState<BatchPreviewResponse | null>(null)
  const [scanProg,    setScanProg]    = useState({ current: 0, total: 0 })
  const [orgProg,     setOrgProg]     = useState({ current: 0, total: 0 })
  const [log,         setLog]         = useState<LogEntry[]>([])
  const [summary,     setSummary]     = useState<BatchCompleteEvent | null>(null)
  const [errorMsg,    setErrorMsg]    = useState<string | null>(null)

  const logEndRef = useRef<HTMLDivElement>(null)

  const scrollLog = useCallback(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  // ---- プレビュー ----
  const handlePreview = async () => {
    if (!inputPath.trim()) return
    setPhase('previewing')
    setPreview(null)
    setErrorMsg(null)
    try {
      const data = await apiFetch<BatchPreviewResponse>(
        token!,
        '/api/batch/preview',
        { input_path: inputPath.trim(), similarity_threshold: threshold },
      )
      setPreview(data)
      setPhase('preview_done')
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e))
      setPhase('error')
    }
  }

  // ---- 実行 ----
  const handleOrganize = async () => {
    if (!inputPath.trim() || !outputPath.trim()) return
    setPhase('scanning')
    setLog([])
    setSummary(null)
    setErrorMsg(null)
    setScanProg({ current: 0, total: 0 })
    setOrgProg({ current: 0, total: 0 })

    await streamBatchOrganize(
      token!,
      {
        input_path: inputPath.trim(),
        output_path: outputPath.trim(),
        operation,
        similarity_threshold: threshold,
        save_metadata_json: saveJson,
      },
      (e) => {
        setScanProg({ current: e.current, total: e.total })
        if (e.current === 1) setPhase('scanning')
      },
      (e) => {
        setPhase('organizing')
        setOrgProg({ current: e.current, total: e.total })
        setLog(prev => {
          const next = [...prev, {
            file: e.file,
            dest: e.dest,
            status: e.status,
            message: e.message,
          }]
          setTimeout(scrollLog, 30)
          return next
        })
      },
      (e) => {
        setSummary(e)
        setPhase('complete')
      },
      (msg) => {
        setErrorMsg(msg)
        setPhase('error')
      },
    )
  }

  const isRunning = phase === 'previewing' || phase === 'scanning' || phase === 'organizing'
  const canExecute = phase === 'preview_done' || phase === 'complete' || phase === 'error'

  const phaseTotalProgress = orgProg.total > 0
    ? Math.round((orgProg.current / orgProg.total) * 100)
    : 0

  return (
    <div className="batch-root">
      <header className="batch-header">
        <button type="button" className="batch-back" onClick={() => navigate('/')}>
          ← ホーム
        </button>
        <span className="batch-header-title">バッチ整理</span>
      </header>

      <div className="batch-body">
        {/* ===== 左: 設定パネル ===== */}
        <aside className="batch-sidebar">
          <section className="batch-form">
            <label className="batch-label" htmlFor="batch-input">入力フォルダ (サーバー側パス)</label>
            <input
              id="batch-input"
              className="batch-input"
              type="text"
              placeholder="/path/to/images"
              value={inputPath}
              onChange={e => setInputPath(e.target.value)}
              disabled={isRunning}
            />

            <label className="batch-label" htmlFor="batch-output">出力フォルダ (サーバー側パス)</label>
            <input
              id="batch-output"
              className="batch-input"
              type="text"
              placeholder="/path/to/output"
              value={outputPath}
              onChange={e => setOutputPath(e.target.value)}
              disabled={isRunning}
            />

            <label className="batch-label" htmlFor="batch-threshold">
              類似度しきい値: <strong>{Math.round(threshold * 100)}%</strong>
            </label>
            <input
              id="batch-threshold"
              className="batch-range"
              type="range"
              min={0.5} max={1.0} step={0.05}
              value={threshold}
              onChange={e => setThreshold(parseFloat(e.target.value))}
              disabled={isRunning}
            />
            <p className="batch-range-hint">
              高い値 → 完全に同じプロンプトのみ同グループ<br />
              低い値 → 似た題材をまとめてグループ化
            </p>

            <fieldset className="batch-fieldset" disabled={isRunning}>
              <legend className="batch-label">ファイル操作</legend>
              <label className="batch-radio-label">
                <input
                  type="radio" name="operation" value="copy"
                  checked={operation === 'copy'}
                  onChange={() => setOperation('copy')}
                />
                コピー (元ファイルを残す)
              </label>
              <label className="batch-radio-label">
                <input
                  type="radio" name="operation" value="move"
                  checked={operation === 'move'}
                  onChange={() => setOperation('move')}
                />
                移動 (元ファイルを削除)
              </label>
            </fieldset>

            <label className="batch-checkbox-label">
              <input
                type="checkbox"
                checked={saveJson}
                onChange={e => setSaveJson(e.target.checked)}
                disabled={isRunning}
              />
              メタデータを JSON で隣に保存
            </label>
          </section>

          <div className="batch-actions">
            <button
              type="button"
              className="batch-btn batch-btn--secondary"
              onClick={handlePreview}
              disabled={isRunning || !inputPath.trim()}
            >
              {phase === 'previewing'
                ? <><span className="batch-spinner batch-spinner--sm" />プレビュー中…</>
                : '🔍 プレビュー'}
            </button>
            <button
              type="button"
              className="batch-btn batch-btn--primary"
              onClick={handleOrganize}
              disabled={isRunning || !inputPath.trim() || !outputPath.trim()}
            >
              {isRunning && phase !== 'previewing'
                ? <><span className="batch-spinner batch-spinner--sm" />実行中…</>
                : '▶ 実行'}
            </button>
          </div>

          {operation === 'move' && canExecute && (
            <p className="batch-warn">
              移動モード: 元ファイルは削除されます。実行前にバックアップを確認してください。
            </p>
          )}
        </aside>

        {/* ===== 右: プレビュー / 進捗 ===== */}
        <main className="batch-main">

          {/* 初期状態 */}
          {phase === 'idle' && (
            <div className="batch-empty">
              <span className="batch-empty-icon" aria-hidden="true">📁</span>
              <p>フォルダパスを入力して「プレビュー」を押すと整理計画を確認できます</p>
            </div>
          )}

          {/* プレビュー中スピナー */}
          {phase === 'previewing' && (
            <div className="batch-empty">
              <span className="batch-spinner" />
              <p>スキャン中…</p>
            </div>
          )}

          {/* プレビュー結果 */}
          {(phase === 'preview_done' || (phase !== 'idle' && preview)) && preview && (
            <section className="batch-section">
              <span className="batch-section-label">整理プレビュー</span>
              <p className="batch-summary-line">
                合計 <strong>{preview.total_files}</strong> ファイル →
                <strong> {preview.groups.length}</strong> グループ
                {preview.no_metadata_files.length > 0 && (
                  <> + <strong>{preview.no_metadata_files.length}</strong> 件 (メタデータなし)</>
                )}
              </p>
              <div className="batch-groups">
                {preview.groups.map((g: BatchGroupInfo, i: number) => (
                  <div key={i} className="batch-group-card">
                    <div className="batch-group-header">
                      <span className="batch-group-name">{g.group_name}/</span>
                      <span className="batch-group-count">{g.file_count} 枚</span>
                    </div>
                    <p className="batch-group-prompt" title={g.representative_prompt}>
                      {g.representative_prompt.slice(0, 100) || '(プロンプトなし)'}
                      {g.representative_prompt.length > 100 && '…'}
                    </p>
                    <div className="batch-group-dates">
                      {Array.from(new Set(g.files.map(f => f.date))).sort().map(d => (
                        <span key={d} className="batch-date-chip">{d}</span>
                      ))}
                    </div>
                  </div>
                ))}
                {preview.no_metadata_files.length > 0 && (
                  <div className="batch-group-card batch-group-card--nometa">
                    <div className="batch-group-header">
                      <span className="batch-group-name">_no_metadata/</span>
                      <span className="batch-group-count">{preview.no_metadata_files.length} 枚</span>
                    </div>
                    <p className="batch-group-prompt">NAI メタデータが見つからなかったファイル</p>
                  </div>
                )}
              </div>
            </section>
          )}

          {/* スキャン進捗 */}
          {(phase === 'scanning' || phase === 'organizing' || phase === 'complete') && (
            <section className="batch-section">
              <span className="batch-section-label">
                {phase === 'scanning' ? 'スキャン中' : '整理進捗'}
              </span>

              {phase === 'scanning' && (
                <div className="batch-progress-wrap">
                  <div className="batch-progress-bar">
                    <div
                      className="batch-progress-fill"
                      style={{
                        width: scanProg.total > 0
                          ? `${Math.round((scanProg.current / scanProg.total) * 100)}%`
                          : '0%'
                      }}
                    />
                  </div>
                  <span className="batch-progress-text">
                    {scanProg.current} / {scanProg.total}
                  </span>
                </div>
              )}

              {(phase === 'organizing' || phase === 'complete') && (
                <>
                  <div className="batch-progress-wrap">
                    <div className="batch-progress-bar">
                      <div
                        className={`batch-progress-fill${phase === 'complete' ? ' batch-progress-fill--done' : ''}`}
                        style={{ width: `${phaseTotalProgress}%` }}
                      />
                    </div>
                    <span className="batch-progress-text">
                      {orgProg.current} / {orgProg.total} ({phaseTotalProgress}%)
                    </span>
                  </div>

                  {summary && (
                    <div className="batch-result-summary">
                      <span className="batch-result-item batch-result-item--ok">
                        ✔ 整理済み: {summary.organized}
                      </span>
                      <span className="batch-result-item batch-result-item--warn">
                        ⚠ メタデータなし: {summary.skipped_no_meta}
                      </span>
                      {summary.errors > 0 && (
                        <span className="batch-result-item batch-result-item--err">
                          ✖ エラー: {summary.errors}
                        </span>
                      )}
                    </div>
                  )}

                  <div className="batch-log">
                    {log.map((entry, i) => (
                      <div key={i} className={`batch-log-row batch-log-row--${entry.status}`}>
                        <span className="batch-log-icon">
                          {entry.status === 'ok' ? '✔' : entry.status === 'no_metadata' ? '~' : '✖'}
                        </span>
                        <span className="batch-log-file">{entry.file}</span>
                        {entry.dest && (
                          <span className="batch-log-dest" title={entry.dest}>→ {entry.dest}</span>
                        )}
                        {entry.message && (
                          <span className="batch-log-msg">{entry.message}</span>
                        )}
                      </div>
                    ))}
                    <div ref={logEndRef} />
                  </div>
                </>
              )}
            </section>
          )}

          {/* エラー */}
          {errorMsg && (
            <p className="batch-error" role="alert">{errorMsg}</p>
          )}
        </main>
      </div>
    </div>
  )
}
