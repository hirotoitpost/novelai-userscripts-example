import { useState, useCallback, KeyboardEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { apiFetch, GenerateRequest, GenerateResponse } from '../api'
import './ImageGenerate.css'

const MODELS = [
  { value: 'nai-diffusion-4-5-full',    label: 'NAI Diffusion V4.5 Full' },
  { value: 'nai-diffusion-4-5-curated', label: 'NAI Diffusion V4.5 Curated' },
  { value: 'nai-diffusion-4-full',      label: 'NAI Diffusion V4 Full' },
  { value: 'nai-diffusion-4-curated',   label: 'NAI Diffusion V4 Curated' },
  { value: 'nai-diffusion-3',           label: 'NAI Diffusion V3' },
] as const

const SIZES = [
  { value: 'portrait',       label: 'Portrait  (832×1216)' },
  { value: 'landscape',      label: 'Landscape (1216×832)' },
  { value: 'square',         label: 'Square    (1024×1024)' },
  { value: 'large_portrait', label: 'Portrait Large (1024×1536)' },
  { value: 'large_landscape',label: 'Landscape Large (1536×1024)' },
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

  const [prompt, setPrompt]       = useState('')
  const [negPrompt, setNegPrompt] = useState('')
  const [model, setModel]         = useState('nai-diffusion-4-5-full')
  const [size, setSize]           = useState('portrait')
  const [steps, setSteps]         = useState(28)
  const [scale, setScale]         = useState(6.0)
  const [seed, setSeed]           = useState<string>('')
  const [ucPreset, setUcPreset]   = useState('light')
  const [quality, setQuality]     = useState(true)

  const [loading, setLoading]     = useState(false)
  const [error, setError]         = useState<string | null>(null)
  const [result, setResult]       = useState<{ src: string; format: string } | null>(null)

  const generate = useCallback(async () => {
    if (!token || !prompt.trim()) return
    setLoading(true)
    setError(null)
    try {
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
      }
      const data = await apiFetch<GenerateResponse>(token, '/api/image/generate', body)
      setResult({ src: `data:image/${data.format};base64,${data.images[0]}`, format: data.format })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [token, prompt, negPrompt, model, size, steps, scale, seed, quality, ucPreset])

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
        <button className="ig-back" onClick={() => navigate('/')} title="ホームへ戻る">
          ← ホーム
        </button>
        <span className="ig-header-title">画像生成</span>
      </header>

      <div className="ig-body">
        {/* ===== Left Sidebar ===== */}
        <aside className="ig-sidebar">

          {/* Prompt */}
          <section className="ig-section">
            <label className="ig-label">プロンプト</label>
            <textarea
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
            <label className="ig-label">ネガティブプロンプト</label>
            <textarea
              className="ig-textarea ig-textarea--neg"
              value={negPrompt}
              onChange={e => setNegPrompt(e.target.value)}
              placeholder="lowres, bad anatomy, ..."
              rows={3}
            />
          </section>

          {/* Settings */}
          <section className="ig-section ig-settings">
            <label className="ig-label">設定</label>

            <div className="ig-field">
              <span className="ig-field-label">モデル</span>
              <select className="ig-select" value={model} onChange={e => setModel(e.target.value)}>
                {MODELS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
              </select>
            </div>

            <div className="ig-field">
              <span className="ig-field-label">サイズ</span>
              <select className="ig-select" value={size} onChange={e => setSize(e.target.value)}>
                {SIZES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>

            <div className="ig-field ig-field--row">
              <span className="ig-field-label">Steps</span>
              <input
                type="range" min={1} max={50} step={1}
                value={steps}
                onChange={e => setSteps(Number(e.target.value))}
                className="ig-range"
              />
              <span className="ig-field-value">{steps}</span>
            </div>

            <div className="ig-field ig-field--row">
              <span className="ig-field-label">Scale</span>
              <input
                type="range" min={0} max={10} step={0.1}
                value={scale}
                onChange={e => setScale(Number(e.target.value))}
                className="ig-range"
              />
              <span className="ig-field-value">{scale.toFixed(1)}</span>
            </div>

            <div className="ig-field">
              <span className="ig-field-label">Seed</span>
              <input
                type="number" min={0} max={999999999}
                value={seed}
                onChange={e => setSeed(e.target.value)}
                placeholder="ランダム"
                className="ig-input-number"
              />
            </div>

            <div className="ig-field">
              <span className="ig-field-label">UC プリセット</span>
              <select className="ig-select" value={ucPreset} onChange={e => setUcPreset(e.target.value)}>
                {UC_PRESETS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
              </select>
            </div>

            <div className="ig-field ig-field--checkbox">
              <label className="ig-checkbox-label">
                <input
                  type="checkbox"
                  checked={quality}
                  onChange={e => setQuality(e.target.checked)}
                />
                <span>品質タグを自動付与</span>
              </label>
            </div>
          </section>

          {/* Generate button */}
          <button
            className="ig-generate-btn"
            onClick={generate}
            disabled={loading || !prompt.trim()}
            title="Ctrl+Enter でも生成できます"
          >
            {loading ? <span className="ig-spinner" /> : '🎨 生成する'}
          </button>

          {error && <p className="ig-error">{error}</p>}
        </aside>

        {/* ===== Canvas Area ===== */}
        <main className="ig-canvas">
          {loading && (
            <div className="ig-canvas-placeholder">
              <span className="ig-canvas-spinner" />
              <p>生成中…</p>
            </div>
          )}

          {!loading && !result && (
            <div className="ig-canvas-placeholder">
              <span className="ig-canvas-icon">🖼️</span>
              <p>生成した画像がここに表示されます</p>
              <span className="ig-canvas-hint">Ctrl+Enter でも生成できます</span>
            </div>
          )}

          {!loading && result && (
            <div className="ig-result">
              <img
                className="ig-result-img"
                src={result.src}
                alt="Generated"
              />
              <div className="ig-result-actions">
                <button className="ig-action-btn" onClick={handleDownload}>
                  ↓ ダウンロード
                </button>
                <button className="ig-action-btn ig-action-btn--secondary" onClick={generate}>
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
