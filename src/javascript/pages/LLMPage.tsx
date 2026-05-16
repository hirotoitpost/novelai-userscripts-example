import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import {
  streamLLM,
  PromptFormatRequest,
  CharGenRequest,
  StoryDraftRequest,
  AuxTextRequest,
  MetadataGenRequest,
  ReversePromptRequest,
} from '../api'
import './LLMPage.css'

type LLMTab = 'prompt_format' | 'char_gen' | 'story_draft' | 'aux_text' | 'metadata_gen' | 'reverse_prompt'

const TABS: { id: LLMTab; label: string }[] = [
  { id: 'prompt_format',  label: 'プロンプト整形' },
  { id: 'char_gen',       label: 'キャラ設定生成' },
  { id: 'story_draft',    label: '物語ドラフト' },
  { id: 'aux_text',       label: '補助テキスト' },
  { id: 'metadata_gen',   label: 'メタデータ生成' },
  { id: 'reverse_prompt', label: 'リバースプロンプト' },
]

const MODELS = [
  { value: 'nai-diffusion-4-5-full',    label: 'V4.5 Full' },
  { value: 'nai-diffusion-4-5-curated', label: 'V4.5 Curated' },
  { value: 'nai-diffusion-4-full',      label: 'V4 Full' },
  { value: 'nai-diffusion-4-curated',   label: 'V4 Curated' },
  { value: 'nai-diffusion-3',           label: 'V3' },
]

const SIZES = [
  { value: 'portrait',        label: 'Portrait' },
  { value: 'landscape',       label: 'Landscape' },
  { value: 'square',          label: 'Square' },
  { value: 'large_portrait',  label: 'Large Portrait' },
  { value: 'large_landscape', label: 'Large Landscape' },
]

// ── shared hook for streaming text output ──
function useLLMStream() {
  const [output, setOutput]     = useState('')
  const [streaming, setStreaming] = useState(false)
  const [error, setError]       = useState<string | null>(null)
  const [copied, setCopied]     = useState(false)
  const abortRef                = useRef(false)

  const reset = useCallback(() => {
    setOutput('')
    setError(null)
    setCopied(false)
  }, [])

  const run = useCallback(
    async (token: string, path: string, body: unknown) => {
      reset()
      setStreaming(true)
      abortRef.current = false
      await streamLLM(
        token,
        path,
        body,
        (delta) => { if (!abortRef.current) setOutput(prev => prev + delta) },
        (_full) => { setStreaming(false) },
        (msg)   => { setError(msg); setStreaming(false) },
      )
    },
    [reset],
  )

  const copy = useCallback((text: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }, [])

  return { output, streaming, error, copied, run, copy, reset }
}

// ── helper: try to parse JSON, return null on failure ──
function tryParseJSON<T>(text: string): T | null {
  try { return JSON.parse(text) as T } catch { return null }
}

// ════════════════════════════════════════
// Panel: プロンプト整形
// ════════════════════════════════════════
function PromptFormatPanel() {
  const { token } = useAuth()
  const navigate  = useNavigate()
  const { output, streaming, error, copied, run, copy } = useLLMStream()

  const [roughPrompt, setRoughPrompt] = useState('')
  const [targetModel, setTargetModel] = useState('nai-diffusion-4-5-full')

  useEffect(() => {
    const init = localStorage.getItem('nai_llm_init_prompt')
    if (init) {
      setRoughPrompt(JSON.parse(init) as string)
      localStorage.removeItem('nai_llm_init_prompt')
    }
  }, [])

  const handleRun = () => {
    if (!token || !roughPrompt.trim()) return
    const req: PromptFormatRequest = { rough_prompt: roughPrompt.trim(), target_model: targetModel }
    run(token, '/api/llm/prompt-format', req)
  }

  const handleUse = () => {
    localStorage.setItem('nai_gen_prompt', JSON.stringify(output))
    navigate('/generate')
  }

  return (
    <div className="llm-panel">
      <p className="llm-panel-desc">曖昧な説明 → NovelAI コンマ区切りタグリストに整形します。</p>
      <div className="llm-field">
        <label className="llm-label">入力（日本語・英語どちらでも可）</label>
        <textarea
          className="llm-textarea"
          rows={4}
          value={roughPrompt}
          onChange={e => setRoughPrompt(e.target.value)}
          placeholder="例: 白髪で赤い目の女の子、青い着物、桜の木の下"
        />
      </div>
      <div className="llm-field llm-field--row">
        <label className="llm-label">対象モデル</label>
        <select className="llm-select" value={targetModel} onChange={e => setTargetModel(e.target.value)}>
          {MODELS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
        </select>
      </div>
      <button className="llm-run-btn" onClick={handleRun} disabled={streaming || !roughPrompt.trim()}>
        {streaming ? <span className="llm-spinner" /> : '生成'}
      </button>
      {error && <p className="llm-error">{error}</p>}
      {(output || streaming) && (
        <div className="llm-output-box">
          <div className="llm-output-actions">
            <button className="llm-copy-btn" onClick={() => copy(output)}>{copied ? '✓ コピー済み' : 'コピー'}</button>
            {!streaming && output && (
              <button className="llm-use-btn" onClick={handleUse}>画像生成に使用 →</button>
            )}
          </div>
          <pre className="llm-output">{output}{streaming && <span className="llm-cursor">|</span>}</pre>
        </div>
      )}
    </div>
  )
}

// ════════════════════════════════════════
// Panel: キャラ設定生成
// ════════════════════════════════════════
interface CharGenResult { name: string; positive_tags: string; negative_tags: string; character_notes: string }

function CharGenPanel() {
  const { token } = useAuth()
  const navigate  = useNavigate()
  const { output, streaming, error, copied, run, copy } = useLLMStream()
  const [concept, setConcept] = useState('')
  const [style,   setStyle]   = useState('anime')

  const parsed = tryParseJSON<CharGenResult>(output)

  const handleRun = () => {
    if (!token || !concept.trim()) return
    const req: CharGenRequest = { concept: concept.trim(), style }
    run(token, '/api/llm/char-gen', req)
  }

  const handleUse = () => {
    if (!parsed) return
    localStorage.setItem('nai_gen_prompt',     JSON.stringify(parsed.positive_tags))
    localStorage.setItem('nai_gen_neg_prompt', JSON.stringify(parsed.negative_tags))
    navigate('/generate')
  }

  return (
    <div className="llm-panel">
      <p className="llm-panel-desc">キャラクター概念 → NovelAI ビジュアルタグ JSON を生成します。</p>
      <div className="llm-field">
        <label className="llm-label">キャラクター概念</label>
        <textarea
          className="llm-textarea"
          rows={3}
          value={concept}
          onChange={e => setConcept(e.target.value)}
          placeholder="例: 活発な少女、元気いっぱい、赤いリボン、魔法使い見習い"
        />
      </div>
      <div className="llm-field llm-field--row">
        <label className="llm-label">スタイル</label>
        <input className="llm-input" value={style} onChange={e => setStyle(e.target.value)} placeholder="anime" />
      </div>
      <button className="llm-run-btn" onClick={handleRun} disabled={streaming || !concept.trim()}>
        {streaming ? <span className="llm-spinner" /> : '生成'}
      </button>
      {error && <p className="llm-error">{error}</p>}
      {(output || streaming) && (
        <div className="llm-output-box">
          {parsed ? (
            <>
              <div className="llm-char-card">
                <div className="llm-char-name">{parsed.name}</div>
                <div className="llm-char-row">
                  <span className="llm-char-label">Positive</span>
                  <span className="llm-char-tags">{parsed.positive_tags}</span>
                  <button className="llm-copy-btn llm-copy-btn--sm" onClick={() => copy(parsed.positive_tags)}>コピー</button>
                </div>
                <div className="llm-char-row">
                  <span className="llm-char-label">Negative</span>
                  <span className="llm-char-tags llm-char-tags--neg">{parsed.negative_tags}</span>
                  <button className="llm-copy-btn llm-copy-btn--sm" onClick={() => copy(parsed.negative_tags)}>コピー</button>
                </div>
                {parsed.character_notes && (
                  <div className="llm-char-notes">{parsed.character_notes}</div>
                )}
              </div>
              <div className="llm-output-actions">
                <button className="llm-copy-btn" onClick={() => copy(output)}>{copied ? '✓ JSON コピー済み' : 'JSON コピー'}</button>
                <button className="llm-use-btn" onClick={handleUse}>画像生成に使用 →</button>
              </div>
            </>
          ) : (
            <div className="llm-output-box">
              <div className="llm-output-actions">
                <button className="llm-copy-btn" onClick={() => copy(output)}>{copied ? '✓ コピー済み' : 'コピー'}</button>
              </div>
              <pre className="llm-output">{output}{streaming && <span className="llm-cursor">|</span>}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ════════════════════════════════════════
// Panel: 物語ドラフト
// ════════════════════════════════════════
function StoryDraftPanel() {
  const { token } = useAuth()
  const { output, streaming, error, copied, run, copy } = useLLMStream()
  const [premise,  setPremise]  = useState('')
  const [nScenes,  setNScenes]  = useState(3)

  // Extract individual prompt candidates from output
  const prompts = [...output.matchAll(/\[生成プロンプト候補\]:\s*(.+)/g)].map(m => m[1].trim())

  const handleRun = () => {
    if (!token || !premise.trim()) return
    const req: StoryDraftRequest = { premise: premise.trim(), n_scenes: nScenes }
    run(token, '/api/llm/story-draft', req)
  }

  return (
    <div className="llm-panel">
      <p className="llm-panel-desc">前提設定 → シーン付き物語文 + 各シーンの生成プロンプト候補を出力します。</p>
      <div className="llm-field">
        <label className="llm-label">物語の前提</label>
        <textarea
          className="llm-textarea"
          rows={3}
          value={premise}
          onChange={e => setPremise(e.target.value)}
          placeholder="例: 魔法学校に転入してきた少女が、図書館で謎の古書を発見する"
        />
      </div>
      <div className="llm-field llm-field--row">
        <label className="llm-label">シーン数</label>
        <input
          type="range" min={1} max={6} step={1}
          value={nScenes}
          onChange={e => setNScenes(Number(e.target.value))}
          className="llm-range"
        />
        <span className="llm-range-val">{nScenes}</span>
      </div>
      <button className="llm-run-btn" onClick={handleRun} disabled={streaming || !premise.trim()}>
        {streaming ? <span className="llm-spinner" /> : '生成'}
      </button>
      {error && <p className="llm-error">{error}</p>}
      {(output || streaming) && (
        <div className="llm-output-box">
          <div className="llm-output-actions">
            <button className="llm-copy-btn" onClick={() => copy(output)}>{copied ? '✓ コピー済み' : '全文コピー'}</button>
          </div>
          <pre className="llm-output">{output}{streaming && <span className="llm-cursor">|</span>}</pre>
          {!streaming && prompts.length > 0 && (
            <div className="llm-story-prompts">
              <div className="llm-story-prompts-title">プロンプト候補一覧</div>
              {prompts.map((p, i) => (
                <div key={i} className="llm-story-prompt-row">
                  <span className="llm-story-prompt-text">{p}</span>
                  <button className="llm-copy-btn llm-copy-btn--sm" onClick={() => copy(p)}>コピー</button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ════════════════════════════════════════
// Panel: 補助テキスト生成
// ════════════════════════════════════════
interface AuxTextResult { positive: string; negative: string; explanation: string }

function AuxTextPanel() {
  const { token } = useAuth()
  const navigate  = useNavigate()
  const { output, streaming, error, copied, run, copy } = useLLMStream()
  const [concept,     setConcept]     = useState('')
  const [targetModel, setTargetModel] = useState('nai-diffusion-4-5-full')

  const parsed = tryParseJSON<AuxTextResult>(output)

  const handleRun = () => {
    if (!token || !concept.trim()) return
    const req: AuxTextRequest = { concept: concept.trim(), target_model: targetModel }
    run(token, '/api/llm/aux-text', req)
  }

  const handleUse = () => {
    if (!parsed) return
    localStorage.setItem('nai_gen_prompt',     JSON.stringify(parsed.positive))
    localStorage.setItem('nai_gen_neg_prompt', JSON.stringify(parsed.negative))
    navigate('/generate')
  }

  return (
    <div className="llm-panel">
      <p className="llm-panel-desc">コンセプト → ポジティブ / ネガティブプロンプトの最適ペアを生成します。</p>
      <div className="llm-field">
        <label className="llm-label">コンセプト</label>
        <textarea
          className="llm-textarea"
          rows={3}
          value={concept}
          onChange={e => setConcept(e.target.value)}
          placeholder="例: 夕暮れの海岸で波に濡れながら振り向く少女"
        />
      </div>
      <div className="llm-field llm-field--row">
        <label className="llm-label">対象モデル</label>
        <select className="llm-select" value={targetModel} onChange={e => setTargetModel(e.target.value)}>
          {MODELS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
        </select>
      </div>
      <button className="llm-run-btn" onClick={handleRun} disabled={streaming || !concept.trim()}>
        {streaming ? <span className="llm-spinner" /> : '生成'}
      </button>
      {error && <p className="llm-error">{error}</p>}
      {(output || streaming) && (
        <div className="llm-output-box">
          {parsed ? (
            <>
              <div className="llm-aux-row">
                <span className="llm-aux-label">Positive</span>
                <pre className="llm-aux-text">{parsed.positive}</pre>
                <button className="llm-copy-btn llm-copy-btn--sm" onClick={() => copy(parsed.positive)}>コピー</button>
              </div>
              <div className="llm-aux-row">
                <span className="llm-aux-label llm-aux-label--neg">Negative</span>
                <pre className="llm-aux-text llm-aux-text--neg">{parsed.negative}</pre>
                <button className="llm-copy-btn llm-copy-btn--sm" onClick={() => copy(parsed.negative)}>コピー</button>
              </div>
              {parsed.explanation && (
                <p className="llm-aux-explanation">{parsed.explanation}</p>
              )}
              <div className="llm-output-actions">
                <button className="llm-copy-btn" onClick={() => copy(output)}>{copied ? '✓ JSON コピー済み' : 'JSON コピー'}</button>
                <button className="llm-use-btn" onClick={handleUse}>画像生成に使用 →</button>
              </div>
            </>
          ) : (
            <>
              <div className="llm-output-actions">
                <button className="llm-copy-btn" onClick={() => copy(output)}>{copied ? '✓ コピー済み' : 'コピー'}</button>
              </div>
              <pre className="llm-output">{output}{streaming && <span className="llm-cursor">|</span>}</pre>
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ════════════════════════════════════════
// Panel: メタデータ生成
// ════════════════════════════════════════
interface MetadataGenResult {
  prompt: string; negative_prompt: string; model: string; size: string
  steps: number; scale: number; sampler: string; noise_schedule: string
  quality: boolean; uc_preset: string; cfg_rescale: number; variety_boost: boolean
  reasoning?: string
}

function MetadataGenPanel() {
  const { token } = useAuth()
  const navigate  = useNavigate()
  const { output, streaming, error, copied, run, copy } = useLLMStream()
  const [concept,     setConcept]     = useState('')
  const [targetModel, setTargetModel] = useState('nai-diffusion-4-5-full')
  const [size,        setSize]        = useState('portrait')

  const parsed = tryParseJSON<MetadataGenResult>(output)

  const handleRun = () => {
    if (!token || !concept.trim()) return
    const req: MetadataGenRequest = { concept: concept.trim(), target_model: targetModel, size }
    run(token, '/api/llm/metadata-gen', req)
  }

  const handleUse = () => {
    if (!parsed) return
    localStorage.setItem('nai_gen_prompt',     JSON.stringify(parsed.prompt))
    localStorage.setItem('nai_gen_neg_prompt', JSON.stringify(parsed.negative_prompt))
    localStorage.setItem('nai_gen_model',      JSON.stringify(parsed.model))
    localStorage.setItem('nai_gen_size',       JSON.stringify(parsed.size))
    localStorage.setItem('nai_gen_steps',      JSON.stringify(parsed.steps))
    localStorage.setItem('nai_gen_scale',      JSON.stringify(parsed.scale))
    localStorage.setItem('nai_gen_uc_preset',  JSON.stringify(parsed.uc_preset))
    localStorage.setItem('nai_gen_quality',    JSON.stringify(parsed.quality))
    navigate('/generate')
  }

  return (
    <div className="llm-panel">
      <p className="llm-panel-desc">コンセプト → 全生成パラメータ（プロンプト・Steps・Scale 等）を一括提案します。</p>
      <div className="llm-field">
        <label className="llm-label">コンセプト</label>
        <textarea
          className="llm-textarea"
          rows={3}
          value={concept}
          onChange={e => setConcept(e.target.value)}
          placeholder="例: 幻想的な森の中で光の粒子に包まれた妖精"
        />
      </div>
      <div className="llm-row">
        <div className="llm-field llm-field--row">
          <label className="llm-label">モデル</label>
          <select className="llm-select" value={targetModel} onChange={e => setTargetModel(e.target.value)}>
            {MODELS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
        </div>
        <div className="llm-field llm-field--row">
          <label className="llm-label">サイズ</label>
          <select className="llm-select" value={size} onChange={e => setSize(e.target.value)}>
            {SIZES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
        </div>
      </div>
      <button className="llm-run-btn" onClick={handleRun} disabled={streaming || !concept.trim()}>
        {streaming ? <span className="llm-spinner" /> : '生成'}
      </button>
      {error && <p className="llm-error">{error}</p>}
      {(output || streaming) && (
        <div className="llm-output-box">
          {parsed ? (
            <>
              <table className="llm-meta-table">
                <tbody>
                  {[
                    ['モデル',    parsed.model],
                    ['サイズ',    parsed.size],
                    ['Steps',     String(parsed.steps)],
                    ['Scale',     String(parsed.scale)],
                    ['Sampler',   parsed.sampler],
                    ['UC Preset', parsed.uc_preset],
                  ].map(([k, v]) => (
                    <tr key={k}>
                      <td className="llm-meta-key">{k}</td>
                      <td className="llm-meta-val">{v}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="llm-aux-row">
                <span className="llm-aux-label">Positive</span>
                <pre className="llm-aux-text">{parsed.prompt}</pre>
              </div>
              <div className="llm-aux-row">
                <span className="llm-aux-label llm-aux-label--neg">Negative</span>
                <pre className="llm-aux-text llm-aux-text--neg">{parsed.negative_prompt}</pre>
              </div>
              {parsed.reasoning && (
                <p className="llm-aux-explanation">{parsed.reasoning}</p>
              )}
              <div className="llm-output-actions">
                <button className="llm-copy-btn" onClick={() => copy(output)}>{copied ? '✓ JSON コピー済み' : 'JSON コピー'}</button>
                <button className="llm-use-btn" onClick={handleUse}>全パラメータを画像生成に適用 →</button>
              </div>
            </>
          ) : (
            <>
              <div className="llm-output-actions">
                <button className="llm-copy-btn" onClick={() => copy(output)}>{copied ? '✓ コピー済み' : 'コピー'}</button>
              </div>
              <pre className="llm-output">{output}{streaming && <span className="llm-cursor">|</span>}</pre>
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ════════════════════════════════════════
// Panel: リバースプロンプト
// ════════════════════════════════════════
interface ReversePromptResult { positive: string; negative: string; analysis: string; confidence: string }

function ReversePromptPanel() {
  const { token } = useAuth()
  const navigate  = useNavigate()
  const { output, streaming, error, copied, run, copy } = useLLMStream()
  const [image,    setImage]    = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef            = useRef<HTMLInputElement>(null)

  const parsed = tryParseJSON<ReversePromptResult>(output)

  const handleFile = useCallback((file: File) => {
    if (!file.type.match(/^image\//)) return
    const reader = new FileReader()
    reader.onload = e => setImage(e.target?.result as string)
    reader.readAsDataURL(file)
  }, [])

  const handleRun = () => {
    if (!token || !image) return
    const req: ReversePromptRequest = { image }
    run(token, '/api/llm/reverse-prompt', req)
  }

  const handleUse = () => {
    if (!parsed) return
    localStorage.setItem('nai_gen_prompt',     JSON.stringify(parsed.positive))
    localStorage.setItem('nai_gen_neg_prompt', JSON.stringify(parsed.negative))
    navigate('/generate')
  }

  return (
    <div className="llm-panel">
      <p className="llm-panel-desc">アニメ・イラスト画像をアップロード → 再現用 NovelAI プロンプトを逆算します。</p>
      <div
        className={['llm-drop', dragOver ? 'llm-drop--over' : '', image ? 'llm-drop--has-image' : ''].join(' ')}
        onDragOver={e => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={e => { e.preventDefault(); setDragOver(false); const f = e.dataTransfer.files[0]; if (f) handleFile(f) }}
        onClick={() => fileInputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={e => e.key === 'Enter' && fileInputRef.current?.click()}
        aria-label="画像をドロップまたはクリックして選択"
      >
        {image
          ? <img className="llm-drop-thumb" src={image} alt="解析対象画像" />
          : <span>画像をドロップ / クリックして選択</span>}
      </div>
      <input ref={fileInputRef} type="file" accept="image/*" hidden onChange={e => {
        const f = e.target.files?.[0]; if (f) handleFile(f); e.target.value = ''
      }} />
      {image && (
        <button type="button" className="llm-clear-btn" onClick={() => setImage(null)}>✕ 画像をクリア</button>
      )}
      <button className="llm-run-btn" onClick={handleRun} disabled={streaming || !image}>
        {streaming ? <span className="llm-spinner" /> : '解析してプロンプト生成'}
      </button>
      {error && <p className="llm-error">{error}</p>}
      {(output || streaming) && (
        <div className="llm-output-box">
          {parsed ? (
            <>
              {parsed.analysis && <p className="llm-aux-explanation">{parsed.analysis}</p>}
              {parsed.confidence && (
                <p className="llm-confidence">信頼度: <strong>{parsed.confidence}</strong></p>
              )}
              <div className="llm-aux-row">
                <span className="llm-aux-label">Positive</span>
                <pre className="llm-aux-text">{parsed.positive}</pre>
                <button className="llm-copy-btn llm-copy-btn--sm" onClick={() => copy(parsed.positive)}>コピー</button>
              </div>
              <div className="llm-aux-row">
                <span className="llm-aux-label llm-aux-label--neg">Negative</span>
                <pre className="llm-aux-text llm-aux-text--neg">{parsed.negative}</pre>
                <button className="llm-copy-btn llm-copy-btn--sm" onClick={() => copy(parsed.negative)}>コピー</button>
              </div>
              <div className="llm-output-actions">
                <button className="llm-copy-btn" onClick={() => copy(output)}>{copied ? '✓ JSON コピー済み' : 'JSON コピー'}</button>
                <button className="llm-use-btn" onClick={handleUse}>画像生成に使用 →</button>
              </div>
            </>
          ) : (
            <>
              <div className="llm-output-actions">
                <button className="llm-copy-btn" onClick={() => copy(output)}>{copied ? '✓ コピー済み' : 'コピー'}</button>
              </div>
              <pre className="llm-output">{output}{streaming && <span className="llm-cursor">|</span>}</pre>
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ════════════════════════════════════════
// LLMPage root
// ════════════════════════════════════════
export default function LLMPage() {
  const navigate   = useNavigate()
  const [activeTab, setActiveTab] = useState<LLMTab>('prompt_format')

  useEffect(() => {
    // If arriving from /generate with an init prompt, switch to the prompt format tab
    const init = localStorage.getItem('nai_llm_init_prompt')
    if (init) setActiveTab('prompt_format')
  }, [])

  const renderPanel = () => {
    switch (activeTab) {
      case 'prompt_format':  return <PromptFormatPanel />
      case 'char_gen':       return <CharGenPanel />
      case 'story_draft':    return <StoryDraftPanel />
      case 'aux_text':       return <AuxTextPanel />
      case 'metadata_gen':   return <MetadataGenPanel />
      case 'reverse_prompt': return <ReversePromptPanel />
    }
  }

  return (
    <div className="llm-root">
      <header className="llm-header">
        <button type="button" className="llm-back" onClick={() => navigate('/')} title="ホームへ戻る">
          ← ホーム
        </button>
        <span className="llm-header-title">AI アシスタント</span>
      </header>

      <div className="llm-tabs" role="tablist">
        {TABS.map(tab => (
          <button
            key={tab.id}
            role="tab"
            aria-selected={activeTab === tab.id}
            className={['llm-tab', activeTab === tab.id ? 'llm-tab--active' : ''].join(' ')}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <main className="llm-main">
        {renderPanel()}
      </main>
    </div>
  )
}
