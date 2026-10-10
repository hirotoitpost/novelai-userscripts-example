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
  apiFetch,
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
interface ReverseTag { tag: string; probability: number; category: 'general' | 'character' }
interface ReverseResult {
  source: 'metadata' | 'tagger'
  positive: string
  negative: string
  characters: { prompt: string; negative: string }[]
  settings: { seed: number | null; steps: number | null; scale: number | null; sampler: string | null; width: number | null; height: number | null } | null
  software: string | null
  tags: ReverseTag[]
  rating: string | null
  general_threshold: number
  character_threshold: number
  style_threshold: number
  style_tags: string[]
  model: string
}

// プロンプトの先頭に置く人数のタグ(src/python/image_tagger.py の _COUNT_TAGS と揃える)
const COUNT_TAGS = [
  '1girl', '2girls', '3girls', '4girls', '5girls', '6+girls', 'multiple girls',
  '1boy', '2boys', '3boys', '4boys', '5boys', '6+boys', 'multiple boys',
  '1other', '2others', '3others', 'multiple others', 'no humans',
]

const RATING_LABELS: Record<string, string> = {
  general: '全年齢', sensitive: 'センシティブ', questionable: 'きわどい', explicit: '成人向け',
}

const PREPOSITIONS = new Set(['under', 'on', 'in', 'between', 'over', 'behind', 'from', 'of', 'around', 'with'])

/** より詳しいタグに含まれる広いタグか(src/python/image_tagger.py の _implied と同じ) */
function isImplied(tag: string, others: string[]): boolean {
  const words = tag.split(' ')
  return others.some(other => {
    const o = other.split(' ')
    if (other === tag || o.length <= words.length) return false
    if (o.slice(-words.length).join(' ') === tag) return true
    return o.slice(0, words.length).join(' ') === tag && PREPOSITIONS.has(o[words.length])
  })
}

/**
 * しきい値と手での選び直しから、NovelAI の並び(人数 → キャラ名 → 絵柄 → そのほか確率の高い順)のプロンプトを
 * 作る。手で入れたタグは、詳しいタグに含まれていても残す。
 */
function tagsToPrompt(tags: ReverseTag[], picked: Set<string>, manual: Record<string, boolean>, styles: string[]): string {
  const chosen = tags.filter(t => picked.has(t.tag))
  const names = chosen.map(t => t.tag)
  const kept = chosen.filter(t => COUNT_TAGS.includes(t.tag) || manual[t.tag] || !isImplied(t.tag, names))
  const counts = kept.filter(t => COUNT_TAGS.includes(t.tag)).sort((a, b) => COUNT_TAGS.indexOf(a.tag) - COUNT_TAGS.indexOf(b.tag))
  const characters = kept.filter(t => t.category === 'character')
  const styleTags = kept.filter(t => styles.includes(t.tag))
  const others = kept.filter(t => t.category === 'general' && !COUNT_TAGS.includes(t.tag) && !styles.includes(t.tag))
  return [...counts, ...characters, ...styleTags, ...others].map(t => t.tag).join(', ')
}

function ReversePromptPanel() {
  const { token } = useAuth()
  const navigate  = useNavigate()
  const [image,    setImage]    = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [running,  setRunning]  = useState(false)
  const [error,    setError]    = useState<string | null>(null)
  const [result,   setResult]   = useState<ReverseResult | null>(null)
  const [threshold, setThreshold] = useState(0.5)
  // しきい値で選んだ後に、手で外した/足したタグ
  const [toggled,  setToggled]  = useState<Record<string, boolean>>({})
  const [copied,   setCopied]   = useState<string | null>(null)
  const fileInputRef            = useRef<HTMLInputElement>(null)

  const handleFile = useCallback((file: File) => {
    if (!file.type.match(/^image\//)) return
    const reader = new FileReader()
    reader.onload = e => { setImage(e.target?.result as string); setResult(null); setError(null) }
    reader.readAsDataURL(file)
  }, [])

  const handleRun = async () => {
    if (!token || !image) return
    setRunning(true)
    setError(null)
    setResult(null)
    setToggled({})
    try {
      const req: ReversePromptRequest = { image }
      const data = await apiFetch<ReverseResult>(token, '/api/llm/reverse-prompt/tags', req)
      setResult(data)
      setThreshold(data.general_threshold)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setRunning(false)
    }
  }

  // 絵柄・色のタグは低いしきい値で拾う(一般タグのしきい値を下げたときはそちらに合わせる)
  const tagThreshold = (t: ReverseTag) => {
    if (t.category === 'character') return result!.character_threshold
    if (result!.style_tags.includes(t.tag)) return Math.min(result!.style_threshold, threshold)
    return threshold
  }
  const picked = new Set(
    (result?.tags ?? [])
      .filter(t => toggled[t.tag] ?? (t.probability >= tagThreshold(t)))
      .map(t => t.tag),
  )
  const positive = result?.source === 'tagger'
    ? tagsToPrompt(result.tags, picked, toggled, result.style_tags)
    : result?.positive ?? ''
  // 画像にあるタグ(手で入れたものも)はネガティブから外す(両方にあると打ち消し合う)
  const positiveTags = new Set(positive.split(',').map(t => t.trim()))
  const negative = result?.source === 'tagger'
    ? result.negative.split(',').map(t => t.trim()).filter(t => t && !positiveTags.has(t)).join(', ')
    : result?.negative ?? ''
  // 画像生成ページはキャラごとの欄が無いので、キャラのプロンプトは本体の後ろにつなげる
  const merged = [positive, ...(result?.characters ?? []).map(c => c.prompt)].filter(Boolean).join(', ')

  const copy = async (text: string, key: string) => {
    await navigator.clipboard.writeText(text).catch(() => {})
    setCopied(key)
    window.setTimeout(() => setCopied(null), 1500)
  }

  const handleUse = () => {
    if (!result) return
    localStorage.setItem('nai_gen_prompt',     JSON.stringify(merged))
    localStorage.setItem('nai_gen_neg_prompt', JSON.stringify(negative))
    if (result.settings?.steps) localStorage.setItem('nai_gen_steps', JSON.stringify(result.settings.steps))
    if (result.settings?.scale) localStorage.setItem('nai_gen_scale', JSON.stringify(result.settings.scale))
    navigate('/generate')
  }

  return (
    <div className="llm-panel">
      <p className="llm-panel-desc">
        画像から、似た画像を生成するための NovelAI プロンプトを逆引きします。NovelAI で生成した画像なら、画像に埋め込まれた
        プロンプトと設定をそのまま取り出します。それ以外は danbooru タグの判定モデル(WD Tagger)で推定します(初回はモデルの
        ダウンロードに数分かかります)。
      </p>
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
        <button type="button" className="llm-clear-btn" onClick={() => { setImage(null); setResult(null) }}>✕ 画像をクリア</button>
      )}
      <button className="llm-run-btn" onClick={() => void handleRun()} disabled={running || !image}>
        {running ? <span className="llm-spinner" /> : 'プロンプトを逆引き'}
      </button>
      {error && <p className="llm-error">{error}</p>}

      {result && (
        <div className="llm-output-box">
          {result.source === 'metadata' ? (
            <p className="llm-confidence">
              ✅ <strong>画像に埋め込まれた生成時のプロンプト</strong>です(同じ設定・シードなら同じ画像になります)
              {result.software && ` ・ ${result.software}`}
            </p>
          ) : (
            <>
              <p className="llm-confidence">
                🔎 <strong>WD Tagger で推定</strong>しました
                {result.rating && ` ・ 判定: ${RATING_LABELS[result.rating] ?? result.rating}`}
                。タグを押すと入れる/外すを切り替えられます。
              </p>
              <label className="llm-rev-threshold">
                <span>しきい値 {threshold.toFixed(2)}(下げるとタグが増え、上げると確かなものだけになります)</span>
                <input
                  type="range" min={0.2} max={0.9} step={0.05} value={threshold}
                  onChange={e => { setThreshold(Number(e.target.value)); setToggled({}) }}
                />
              </label>
              <div className="llm-rev-tags">
                {result.tags.map(t => (
                  <button
                    key={t.tag}
                    type="button"
                    className={[
                      'llm-rev-tag',
                      picked.has(t.tag) ? 'llm-rev-tag--on' : '',
                      t.category === 'character' ? 'llm-rev-tag--character' : '',
                    ].join(' ')}
                    onClick={() => setToggled({ ...toggled, [t.tag]: !picked.has(t.tag) })}
                    title={`確率 ${(t.probability * 100).toFixed(0)}%${t.category === 'character' ? '(キャラ名)' : ''}`}
                  >
                    {t.tag} <small>{Math.round(t.probability * 100)}</small>
                  </button>
                ))}
              </div>
            </>
          )}
          <div className="llm-aux-row">
            <span className="llm-aux-label">Positive</span>
            <pre className="llm-aux-text">{positive}</pre>
            <button className="llm-copy-btn llm-copy-btn--sm" onClick={() => void copy(positive, 'pos')}>{copied === 'pos' ? '✓' : 'コピー'}</button>
          </div>
          {result.characters.map((c, i) => (
            <div key={i} className="llm-aux-row">
              <span className="llm-aux-label">キャラ{i + 1}</span>
              <pre className="llm-aux-text">{c.prompt}{c.negative && `\n(ネガティブ: ${c.negative})`}</pre>
              <button className="llm-copy-btn llm-copy-btn--sm" onClick={() => void copy(c.prompt, `c${i}`)}>{copied === `c${i}` ? '✓' : 'コピー'}</button>
            </div>
          ))}
          <div className="llm-aux-row">
            <span className="llm-aux-label llm-aux-label--neg">Negative</span>
            <pre className="llm-aux-text llm-aux-text--neg">{negative}</pre>
            <button className="llm-copy-btn llm-copy-btn--sm" onClick={() => void copy(negative, 'neg')}>{copied === 'neg' ? '✓' : 'コピー'}</button>
          </div>
          {result.settings && (
            <p className="llm-aux-explanation">
              設定: {[
                result.settings.width && result.settings.height && `${result.settings.width}×${result.settings.height}`,
                result.settings.steps && `steps ${result.settings.steps}`,
                result.settings.scale && `scale ${result.settings.scale}`,
                result.settings.sampler,
                result.settings.seed != null && `シード ${result.settings.seed}`,
              ].filter(Boolean).join(' ・ ')}
            </p>
          )}
          <div className="llm-output-actions">
            <button className="llm-use-btn" onClick={handleUse} disabled={!merged}>画像生成に使用 →</button>
          </div>
          {result.characters.length > 0 && (
            <p className="llm-aux-explanation">画像生成ページにはキャラごとの欄が無いため、キャラのプロンプトは Positive の後ろにつなげて渡します。</p>
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
