import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { apiFetch, GenerateRequest, GenerateResponse } from '../api'
import './Selection.css'

interface Situation {
  id: number
  name: string
}

interface ResultChunk {
  id: string
  label: string
  expansion: string
  color: string | null
  score?: number
}

interface PresetSummary {
  id: number
  name: string
  created_at: string
}

interface AllChunk {
  id: string
  label: string
  expansion: string
  is_category: boolean
}

type Mode = 'scenario' | 'random' | 'similar' | 'preset'

export default function Selection() {
  const navigate = useNavigate()
  const { token } = useAuth()

  const [mode, setMode] = useState<Mode>('scenario')
  const [situations, setSituations] = useState<Situation[]>([])
  const [presets, setPresets] = useState<PresetSummary[]>([])
  const [allChunks, setAllChunks] = useState<AllChunk[]>([])

  const [situationId, setSituationId] = useState<number | null>(null)
  const [randomCount, setRandomCount] = useState(5)
  const [referenceQuery, setReferenceQuery] = useState('')
  const [referenceId, setReferenceId] = useState<string | null>(null)
  const [presetId, setPresetId] = useState<number | null>(null)

  const [results, setResults] = useState<ResultChunk[]>([])
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set())
  const [presetName, setPresetName] = useState('')

  const [generatedImages, setGeneratedImages] = useState<string[]>([])
  const [generating, setGenerating] = useState(false)
  const [generateError, setGenerateError] = useState<string | null>(null)

  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    fetch('/api/chunks/situations').then(r => r.json()).then(setSituations)
    fetch('/api/chunks/presets').then(r => r.json()).then(setPresets)
    fetch('/api/chunks/imported').then(r => r.json()).then(setAllChunks)
  }, [])

  const referenceMatches = useMemo(() => {
    const q = referenceQuery.trim().toLowerCase()
    if (!q) return []
    return allChunks.filter(c => !c.is_category && c.label.toLowerCase().includes(q)).slice(0, 8)
  }, [referenceQuery, allChunks])

  const applyResults = (chunks: ResultChunk[]) => {
    setResults(chunks)
    setCheckedIds(new Set(chunks.map(c => c.id)))
  }

  const run = async () => {
    setError(null)
    setLoading(true)
    try {
      if (mode === 'scenario') {
        if (situationId === null) throw new Error('シチュエーションを選んでください')
        const res = await fetch('/api/chunks/select/scenario', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ situation_id: situationId }),
        })
        const data = await res.json()
        if (!res.ok) throw new Error(data.detail ?? '選択に失敗しました')
        applyResults(data)
      } else if (mode === 'random') {
        const res = await fetch('/api/chunks/select/random', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            situation_id: situationId ?? undefined,
            count: randomCount || undefined,
          }),
        })
        const data = await res.json()
        if (!res.ok) throw new Error(data.detail ?? '選択に失敗しました')
        applyResults(data)
      } else if (mode === 'similar') {
        if (!referenceId) throw new Error('基準にするチャンクを選んでください')
        const res = await fetch('/api/chunks/select/similar', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ chunk_id: referenceId, limit: 10 }),
        })
        const data = await res.json()
        if (!res.ok) throw new Error(data.detail ?? '選択に失敗しました')
        applyResults(data)
      } else if (mode === 'preset') {
        if (presetId === null) throw new Error('プリセットを選んでください')
        const res = await fetch(`/api/chunks/presets/${presetId}`)
        const data = await res.json()
        if (!res.ok) throw new Error(data.detail ?? '取得に失敗しました')
        applyResults(data.chunks)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  const toggleChecked = (id: string) => {
    setCheckedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const assembledText = results
    .filter(c => checkedIds.has(c.id))
    .map(c => c.expansion)
    .filter(Boolean)
    .join(', ')

  const copyAssembled = async () => {
    await navigator.clipboard.writeText(assembledText)
  }

  const saveAsPreset = async () => {
    const name = presetName.trim()
    if (!name) return
    const chunkIds = results.filter(c => checkedIds.has(c.id)).map(c => c.id)
    if (chunkIds.length === 0) return
    const res = await fetch('/api/chunks/presets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, chunk_ids: chunkIds }),
    })
    if (res.ok) {
      setPresetName('')
      fetch('/api/chunks/presets').then(r => r.json()).then(setPresets)
    }
  }

  const generateImage = async () => {
    if (!token || !assembledText) return
    setGenerateError(null)
    setGenerating(true)
    setGeneratedImages([])
    try {
      const body: GenerateRequest = {
        prompt: assembledText,
        model: 'nai-diffusion-4-5-full',
        size: 'portrait',
        steps: 23,
        scale: 5.0,
        quality: true,
        uc_preset: 'light',
        n_samples: 1,
      }
      const data = await apiFetch<GenerateResponse>(token, '/api/image/generate', body)
      setGeneratedImages(data.images)
    } catch (e) {
      setGenerateError(e instanceof Error ? e.message : String(e))
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div className="selection-root">
      <div className="selection-inner">
        <button type="button" className="selection-back" onClick={() => navigate('/')}>
          ← ホーム
        </button>
        <h1>ワード選択</h1>
        <p className="selection-intro">
          シナリオ・ランダム・類似・プリセットの4通りでチャンクを選び、結果をチェックボックスで調整してからプロンプトとして結合します。
        </p>

        <div className="selection-modes">
          {(['scenario', 'random', 'similar', 'preset'] as const).map(m => (
            <button
              key={m}
              type="button"
              className={m === mode ? 'selection-mode selection-mode--active' : 'selection-mode'}
              onClick={() => setMode(m)}
            >
              {{ scenario: 'シナリオベース', random: 'ランダム', similar: '類似', preset: 'プリセット' }[m]}
            </button>
          ))}
        </div>

        <section className="selection-section">
          {mode === 'scenario' && (
            <select value={situationId ?? ''} onChange={e => setSituationId(e.target.value ? Number(e.target.value) : null)}>
              <option value="">シチュエーションを選択</option>
              {situations.map(s => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          )}

          {mode === 'random' && (
            <div className="selection-random-controls">
              <select value={situationId ?? ''} onChange={e => setSituationId(e.target.value ? Number(e.target.value) : null)}>
                <option value="">シチュエーション指定なし</option>
                {situations.map(s => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
              <label>
                件数:
                <input
                  type="number"
                  min={1}
                  value={randomCount}
                  onChange={e => setRandomCount(Number(e.target.value))}
                />
              </label>
            </div>
          )}

          {mode === 'similar' && (
            <div className="selection-similar-controls">
              <input
                placeholder="基準にするチャンクをラベルで検索..."
                value={referenceQuery}
                onChange={e => {
                  setReferenceQuery(e.target.value)
                  setReferenceId(null)
                }}
              />
              {referenceMatches.length > 0 && !referenceId && (
                <ul className="selection-reference-matches">
                  {referenceMatches.map(c => (
                    <li key={c.id}>
                      <button
                        type="button"
                        onClick={() => {
                          setReferenceId(c.id)
                          setReferenceQuery(c.label)
                        }}
                      >
                        {c.label}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {referenceId && <p className="selection-reference-selected">✓ 基準チャンクを選択済み</p>}
            </div>
          )}

          {mode === 'preset' && (
            <select value={presetId ?? ''} onChange={e => setPresetId(e.target.value ? Number(e.target.value) : null)}>
              <option value="">プリセットを選択</option>
              {presets.map(p => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          )}

          <button type="button" onClick={run} disabled={loading}>
            {loading ? '実行中...' : '実行'}
          </button>
        </section>

        {error && <p className="selection-error">{error}</p>}

        {results.length > 0 && (
          <>
            <section className="selection-section">
              <h2>結果({checkedIds.size} / {results.length} 件選択中)</h2>
              <ul className="selection-results">
                {results.map(c => (
                  <li key={c.id}>
                    <label>
                      <input
                        type="checkbox"
                        checked={checkedIds.has(c.id)}
                        onChange={() => toggleChecked(c.id)}
                      />
                      <span className="selection-result-dot" style={{ background: c.color ?? '#888' }} />
                      {c.label}
                      {c.score !== undefined && (
                        <span className="selection-result-score">類似度 {c.score.toFixed(3)}</span>
                      )}
                    </label>
                  </li>
                ))}
              </ul>
            </section>

            <section className="selection-section">
              <h2>結合結果</h2>
              <textarea readOnly value={assembledText} rows={4} />
              <div className="selection-assembled-actions">
                <button type="button" onClick={copyAssembled} disabled={!assembledText}>
                  コピー
                </button>
                <input
                  placeholder="この選択をプリセットとして保存する名前"
                  value={presetName}
                  onChange={e => setPresetName(e.target.value)}
                />
                <button type="button" onClick={saveAsPreset} disabled={!presetName.trim() || checkedIds.size === 0}>
                  プリセットとして保存
                </button>
                <button type="button" onClick={generateImage} disabled={!assembledText || generating}>
                  {generating ? '生成中...' : '画像を生成'}
                </button>
              </div>

              {generateError && <p className="selection-error">{generateError}</p>}

              {generatedImages.length > 0 && (
                <div className="selection-generated">
                  {generatedImages.map((b64, i) => (
                    <img key={i} src={`data:image/png;base64,${b64}`} alt={`生成結果 ${i + 1}`} />
                  ))}
                </div>
              )}
            </section>
          </>
        )}
      </div>
    </div>
  )
}
