import { useState, useCallback, useEffect, useRef, KeyboardEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { useLocalStorage } from '../hooks/useLocalStorage'
import { apiFetch, GenerateRequest, AnlasEstimateRequest, AnlasEstimateResponse, I2iRequest } from '../api'
import './ImageGenerate.css'

const MODELS = [
  { value: 'nai-diffusion-4-5-full',    label: 'NAI Diffusion V4.5 Full' },
  { value: 'nai-diffusion-4-5-curated', label: 'NAI Diffusion V4.5 Curated' },
  { value: 'nai-diffusion-4-full',      label: 'NAI Diffusion V4 Full' },
  { value: 'nai-diffusion-4-curated',   label: 'NAI Diffusion V4 Curated' },
  { value: 'nai-diffusion-3',           label: 'NAI Diffusion V3' },
] as const

const SIZES = [
  { value: 'portrait',        label: 'Portrait  (832×1216)' },
  { value: 'landscape',       label: 'Landscape (1216×832)' },
  { value: 'square',          label: 'Square    (1024×1024)' },
  { value: 'large_portrait',  label: 'Portrait Large (1024×1536)' },
  { value: 'large_landscape', label: 'Landscape Large (1536×1024)' },
] as const

const UC_PRESETS = [
  { value: 'light',       label: 'ライト' },
  { value: 'strong',      label: 'ストロング' },
  { value: 'human_focus', label: '人物重視' },
  { value: 'furry_focus', label: 'ファーリー重視' },
] as const

export default function ImageGenerate() {
  const { token } = useAuth()
  const navigate = useNavigate()

  // localStorage に永続化する設定（seed は毎回ランダムが自然なので除外）
  const [prompt,    setPrompt]    = useLocalStorage('nai_gen_prompt',    '',                     300)
  const [negPrompt, setNegPrompt] = useLocalStorage('nai_gen_neg_prompt', '',                     300)
  const [model,     setModel]     = useLocalStorage('nai_gen_model',     'nai-diffusion-4-5-full')
  const [size,      setSize]      = useLocalStorage('nai_gen_size',      'portrait')
  const [steps,     setSteps]     = useLocalStorage('nai_gen_steps',     28)
  const [scale,     setScale]     = useLocalStorage('nai_gen_scale',     6.0)
  const [ucPreset,  setUcPreset]  = useLocalStorage('nai_gen_uc_preset', 'light')
  const [quality,   setQuality]   = useLocalStorage('nai_gen_quality',   true)

  // セッション中のみ保持
  const [seed,         setSeed]         = useState<string>('')
  const [loading,      setLoading]      = useState(false)
  const [error,        setError]        = useState<string | null>(null)
  const [result,       setResult]       = useState<{ src: string; format: string } | null>(null)
  const [preview,      setPreview]      = useState<string | null>(null)
  const [anlasEst,     setAnlasEst]     = useState<number | null>(null)
  const [anlasLoading, setAnlasLoading] = useState(false)

  // Image-to-Image
  const i2iFileInputRef               = useRef<HTMLInputElement>(null)
  const [i2iEnabled,  setI2iEnabled]  = useState(false)
  const [i2iImage,    setI2iImage]    = useState<string | null>(null)
  const [i2iDragOver, setI2iDragOver] = useState(false)
  const [i2iStrength, setI2iStrength] = useState(0.70)
  const [i2iNoise,    setI2iNoise]    = useState(0.00)

  const handleI2iFile = useCallback((file: File) => {
    if (!file.type.match(/^image\//)) return
    const reader = new FileReader()
    reader.onload = (e) => setI2iImage(e.target?.result as string)
    reader.readAsDataURL(file)
  }, [])

  useEffect(() => {
    if (!token || !prompt.trim()) {
      setAnlasEst(null)
      return
    }
    const timer = setTimeout(async () => {
      setAnlasLoading(true)
      try {
        const body: AnlasEstimateRequest = {
          params: {
            prompt:          prompt.trim(),
            negative_prompt: negPrompt.trim() || undefined,
            model,
            size,
            steps,
            scale,
            seed:      seed ? Number(seed) : undefined,
            quality,
            uc_preset: ucPreset,
            n_samples: 1,
          },
          is_opus: false,
        }
        const data = await apiFetch<AnlasEstimateResponse>(token, '/api/image/anlas', body)
        setAnlasEst(data.total_anlas)
      } catch {
        setAnlasEst(null)
      } finally {
        setAnlasLoading(false)
      }
    }, 500)
    return () => clearTimeout(timer)
  }, [token, prompt, negPrompt, model, size, steps, scale, seed, quality, ucPreset])

  const generate = useCallback(async () => {
    if (!token || !prompt.trim()) return
    setLoading(true)
    setError(null)
    setResult(null)
    setPreview(null)

    const i2i: I2iRequest | undefined =
      (i2iEnabled && i2iImage)
        ? { image: i2iImage, strength: i2iStrength, noise: i2iNoise }
        : undefined

    const body: GenerateRequest = {
      prompt:          prompt.trim(),
      negative_prompt: negPrompt.trim() || undefined,
      model,
      size,
      steps,
      scale,
      seed:      seed ? Number(seed) : undefined,
      quality,
      uc_preset: ucPreset,
      n_samples: 1,
      i2i,
    }

    try {
      const res = await fetch('/api/image/generate/stream', {
        method: 'POST',
        headers: {
          'Content-Type':  'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify(body),
      })

      if (!res.ok || !res.body) {
        const errData = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error((errData as { detail?: string }).detail ?? `HTTP ${res.status}`)
      }

      const reader  = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer    = ''
      let eventType = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (line === '') {
            eventType = ''
          } else if (line.startsWith('event: ')) {
            eventType = line.slice(7).trim()
          } else if (line.startsWith('data: ')) {
            let chunk: Record<string, unknown>
            try {
              chunk = JSON.parse(line.slice(6)) as Record<string, unknown>
            } catch {
              continue
            }
            if (eventType === 'error') {
              throw new Error((chunk.detail as string | undefined) ?? 'ストリーミングエラー')
            }
            const img = chunk.image as string | undefined
            if (!img) continue
            if (eventType === 'intermediate') {
              setPreview(`data:image/png;base64,${img}`)
            } else if (eventType === 'final') {
              setResult({ src: `data:image/png;base64,${img}`, format: 'png' })
            }
          }
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
      setPreview(null)
    }
  }, [token, prompt, negPrompt, model, size, steps, scale, seed, quality, ucPreset, i2iEnabled, i2iImage, i2iStrength, i2iNoise])

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault()
      generate()
    }
  }

  const handleDownload = () => {
    if (!result) return
    const a = document.createElement('a')
    a.href = result.src
    a.download = `nai_${Date.now()}.${result.format}`
    a.click()
  }

  return (
    <div className="ig-root">
      {/* Header */}
      <header className="ig-header">
        <button type="button" className="ig-back" onClick={() => navigate('/')} title="ホームへ戻る">
          ← ホーム
        </button>
        <span className="ig-header-title">画像生成</span>
      </header>

      <div className="ig-body">
        {/* ===== Left Sidebar ===== */}
        <aside className="ig-sidebar">

          {/* Prompt */}
          <section className="ig-section">
            <div className="ig-prompt-header">
              <label className="ig-label" htmlFor="ig-prompt">プロンプト</label>
              <button
                type="button"
                className="ig-enhance-btn"
                onClick={() => {
                  localStorage.setItem('nai_llm_init_prompt', prompt)
                  navigate('/llm')
                }}
                title="AI でプロンプトを強化する"
              >
                🤖 強化
              </button>
            </div>
            <textarea
              id="ig-prompt"
              className="ig-textarea ig-textarea--prompt"
              value={prompt}
              onChange={e => setPrompt(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="1girl, masterpiece, best quality, ..."
              rows={5}
            />
          </section>

          {/* Negative prompt */}
          <section className="ig-section">
            <label className="ig-label" htmlFor="ig-neg-prompt">ネガティブプロンプト</label>
            <textarea
              id="ig-neg-prompt"
              className="ig-textarea ig-textarea--neg"
              value={negPrompt}
              onChange={e => setNegPrompt(e.target.value)}
              placeholder="lowres, bad anatomy, ..."
              rows={3}
            />
          </section>

          {/* Settings */}
          <section className="ig-section ig-settings">
            <span className="ig-label">設定</span>

            <div className="ig-field">
              <label className="ig-field-label" htmlFor="ig-model">モデル</label>
              <select
                id="ig-model"
                className="ig-select"
                value={model}
                onChange={e => setModel(e.target.value)}
              >
                {MODELS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
              </select>
            </div>

            <div className="ig-field">
              <label className="ig-field-label" htmlFor="ig-size">サイズ</label>
              <select
                id="ig-size"
                className="ig-select"
                value={size}
                onChange={e => setSize(e.target.value)}
              >
                {SIZES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>

            <div className="ig-field ig-field--row">
              <label className="ig-field-label" htmlFor="ig-steps">Steps</label>
              <input
                id="ig-steps"
                type="range" min={1} max={50} step={1}
                value={steps}
                onChange={e => setSteps(Number(e.target.value))}
                className="ig-range"
              />
              <span className="ig-field-value" aria-live="polite">{steps}</span>
            </div>

            <div className="ig-field ig-field--row">
              <label className="ig-field-label" htmlFor="ig-scale">Scale</label>
              <input
                id="ig-scale"
                type="range" min={0} max={10} step={0.1}
                value={scale}
                onChange={e => setScale(Number(e.target.value))}
                className="ig-range"
              />
              <span className="ig-field-value" aria-live="polite">{scale.toFixed(1)}</span>
            </div>

            <div className="ig-field">
              <label className="ig-field-label" htmlFor="ig-seed">Seed</label>
              <input
                id="ig-seed"
                type="number" min={0} max={999999999}
                value={seed}
                onChange={e => setSeed(e.target.value)}
                placeholder="ランダム"
                className="ig-input-number"
              />
            </div>

            <div className="ig-field">
              <label className="ig-field-label" htmlFor="ig-uc-preset">UC プリセット</label>
              <select
                id="ig-uc-preset"
                className="ig-select"
                value={ucPreset}
                onChange={e => setUcPreset(e.target.value)}
              >
                {UC_PRESETS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
              </select>
            </div>

            <div className="ig-field ig-field--checkbox">
              <label className="ig-checkbox-label" htmlFor="ig-quality">
                <input
                  id="ig-quality"
                  type="checkbox"
                  checked={quality}
                  onChange={e => setQuality(e.target.checked)}
                />
                <span>品質タグを自動付与</span>
              </label>
            </div>
          </section>

          {/* Image-to-Image */}
          <section className="ig-section ig-section--i2i">
            <label className="ig-i2i-toggle">
              <input
                type="checkbox"
                checked={i2iEnabled}
                onChange={e => {
                  setI2iEnabled(e.target.checked)
                  if (!e.target.checked) setI2iImage(null)
                }}
              />
              <span className="ig-label ig-label--inline">Image-to-Image</span>
            </label>

            {i2iEnabled && (
              <div className="ig-i2i-controls">
                <div
                  className={[
                    'ig-i2i-drop',
                    i2iDragOver ? 'ig-i2i-drop--over'      : '',
                    i2iImage    ? 'ig-i2i-drop--has-image' : '',
                  ].join(' ')}
                  onDragOver={e => { e.preventDefault(); setI2iDragOver(true) }}
                  onDragLeave={() => setI2iDragOver(false)}
                  onDrop={e => {
                    e.preventDefault()
                    setI2iDragOver(false)
                    const f = e.dataTransfer.files[0]
                    if (f) handleI2iFile(f)
                  }}
                  onClick={() => i2iFileInputRef.current?.click()}
                  role="button"
                  tabIndex={0}
                  onKeyDown={e => e.key === 'Enter' && i2iFileInputRef.current?.click()}
                  aria-label="参照画像をドロップまたはクリックして選択"
                >
                  {i2iImage
                    ? <img className="ig-i2i-thumb" src={i2iImage} alt="参照画像" />
                    : <span>参照画像をドロップ / クリック</span>}
                </div>

                <input
                  ref={i2iFileInputRef}
                  type="file"
                  accept="image/*"
                  hidden
                  onChange={e => {
                    const f = e.target.files?.[0]
                    if (f) handleI2iFile(f)
                    e.target.value = ''
                  }}
                />

                {i2iImage && (
                  <button
                    type="button"
                    className="ig-i2i-clear"
                    onClick={() => setI2iImage(null)}
                  >
                    ✕ 画像をクリア
                  </button>
                )}

                <div className="ig-field ig-field--row">
                  <label className="ig-field-label" htmlFor="ig-i2i-strength">Strength</label>
                  <input
                    id="ig-i2i-strength"
                    type="range" min={0.01} max={0.99} step={0.01}
                    value={i2iStrength}
                    onChange={e => setI2iStrength(Number(e.target.value))}
                    className="ig-range"
                  />
                  <span className="ig-field-value" aria-live="polite">{i2iStrength.toFixed(2)}</span>
                </div>

                <div className="ig-field ig-field--row">
                  <label className="ig-field-label" htmlFor="ig-i2i-noise">Noise</label>
                  <input
                    id="ig-i2i-noise"
                    type="range" min={0} max={0.99} step={0.01}
                    value={i2iNoise}
                    onChange={e => setI2iNoise(Number(e.target.value))}
                    className="ig-range"
                  />
                  <span className="ig-field-value" aria-live="polite">{i2iNoise.toFixed(2)}</span>
                </div>
              </div>
            )}
          </section>

          {/* Generate button */}
          <button
            type="button"
            className="ig-generate-btn"
            onClick={generate}
            disabled={loading || !prompt.trim() || (i2iEnabled && !i2iImage)}
            title={
              i2iEnabled && !i2iImage
                ? '参照画像をアップロードしてください'
                : 'Ctrl+Enter でも生成できます'
            }
          >
            {loading ? <span className="ig-spinner" /> : '🎨 生成する'}
          </button>

          <div className="ig-anlas-row">
            {anlasLoading && (
              <span className="ig-anlas ig-anlas--loading">Anlas 計算中…</span>
            )}
            {!anlasLoading && anlasEst !== null && (
              <span className="ig-anlas">
                推定消費: <strong>{anlasEst}</strong> Anlas
              </span>
            )}
          </div>

          {error && <p className="ig-error" role="alert">{error}</p>}
        </aside>

        {/* ===== Canvas Area ===== */}
        <main className="ig-canvas">
          {loading && !preview && (
            <div className="ig-canvas-placeholder" aria-live="polite" aria-label="生成中">
              <span className="ig-canvas-spinner" />
              <p>生成中…</p>
            </div>
          )}

          {loading && preview && (
            <div className="ig-result ig-result--streaming">
              <img
                className="ig-result-img"
                src={preview}
                alt="生成中のプレビュー"
              />
              <div className="ig-streaming-badge">
                <span className="ig-streaming-dot" />
                生成中…
              </div>
            </div>
          )}

          {!loading && !result && (
            <div className="ig-canvas-placeholder">
              <span className="ig-canvas-icon" aria-hidden="true">🖼️</span>
              <p>生成した画像がここに表示されます</p>
              <span className="ig-canvas-hint">Ctrl+Enter でも生成できます</span>
            </div>
          )}

          {!loading && result && (
            <div className="ig-result">
              <img
                className="ig-result-img"
                src={result.src}
                alt="生成された画像"
              />
              <div className="ig-result-actions">
                <button type="button" className="ig-action-btn" onClick={handleDownload}>
                  ↓ ダウンロード
                </button>
                <button type="button" className="ig-action-btn ig-action-btn--secondary" onClick={generate}>
                  🔄 再生成
                </button>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  )
}
