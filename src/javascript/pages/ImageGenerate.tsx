import { useState, useCallback, useEffect, useRef, KeyboardEvent, ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { useLocalStorage } from '../hooks/useLocalStorage'
import ChunkPicker from '../components/ChunkPicker'
import { naiImageFilename } from '../downloadFilename'
import type { Character } from '../components/StoryCharacters'
import {
  apiFetch, GenerateRequest, AnlasEstimateRequest, AnlasEstimateResponse, I2iRequest,
  ImagePreset, ImagePresetSettings, CharacterSheetPrompt, isAuthExpired, notifyAuthExpired,
} from '../api'
import './ImageGenerate.css'

const MODELS = [
  { value: 'nai-diffusion-4-5-full',    label: 'NAI Diffusion V4.5 Full' },
  { value: 'nai-diffusion-4-5-curated', label: 'NAI Diffusion V4.5 Curated' },
  { value: 'nai-diffusion-4-full',      label: 'NAI Diffusion V4 Full' },
  { value: 'nai-diffusion-4-curated',   label: 'NAI Diffusion V4 Curated' },
  { value: 'nai-diffusion-3',           label: 'NAI Diffusion V3' },
] as const

const SIZES = [
  { value: 'portrait',        label: 'Portrait  (832×1216)',        width: 832,  height: 1216 },
  { value: 'landscape',       label: 'Landscape (1216×832)',        width: 1216, height: 832 },
  { value: 'square',          label: 'Square    (1024×1024)',       width: 1024, height: 1024 },
  { value: 'large_portrait',  label: 'Portrait Large (1024×1536)',  width: 1024, height: 1536 },
  { value: 'large_landscape', label: 'Landscape Large (1536×1024)', width: 1536, height: 1024 },
] as const

const SAMPLERS = [
  { value: 'k_euler_ancestral',    label: 'Euler Ancestral' },
  { value: 'k_euler',              label: 'Euler' },
  { value: 'k_dpm_2',              label: 'DPM2' },
  { value: 'k_dpm_2_ancestral',    label: 'DPM2 Ancestral' },
  { value: 'k_dpmpp_2m',           label: 'DPM++ 2M' },
  { value: 'k_dpmpp_2s_ancestral', label: 'DPM++ 2S Ancestral' },
  { value: 'k_dpmpp_sde',          label: 'DPM++ SDE' },
  { value: 'ddim',                 label: 'DDIM' },
] as const

const NOISE_SCHEDULES = ['karras', 'exponential', 'polyexponential'] as const

// プリセットは挿絵パネルと共用しており、バックエンドが受け付けるモデルはこの3つだけ。
const PRESET_MODELS = new Set(['nai-diffusion-5-full', 'nai-diffusion-4-5-full', 'nai-diffusion-4-5-curated'])

const AI_DEFAULTS = {
  steps:         27,
  scale:         6.0,
  sampler:       'k_euler_ancestral',
  noiseSchedule: 'karras',
  cfgRescale:    0.0,
  varietyBoost:  false,
}

interface SliderFieldProps {
  id: string
  label: string
  value: number
  min: number
  max: number
  step: number
  onChange: (v: number) => void
  extra?: ReactNode
}

/** 数値ボックス + スライダー(NovelAI の AI設定パネルと同じ並び)。 */
function SliderField({ id, label, value, min, max, step, onChange, extra }: SliderFieldProps) {
  return (
    <div className="ig-ai-field">
      <div className="ig-ai-field-head">
        <label className="ig-ai-label" htmlFor={id}>{label}</label>
        {extra}
      </div>
      <div className="ig-ai-slider-row">
        <input
          id={id}
          type="number" min={min} max={max} step={step}
          value={value}
          onChange={e => {
            const v = Number(e.target.value)
            if (!Number.isNaN(v)) onChange(Math.min(max, Math.max(min, v)))
          }}
          className="ig-ai-num"
        />
        <input
          type="range" min={min} max={max} step={step}
          value={value}
          onChange={e => onChange(Number(e.target.value))}
          className="ig-range"
          aria-label={label}
        />
      </div>
    </div>
  )
}

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
  const [steps,     setSteps]     = useLocalStorage('nai_gen_steps',     AI_DEFAULTS.steps)
  const [scale,     setScale]     = useLocalStorage('nai_gen_scale',     AI_DEFAULTS.scale)
  const [ucPreset,  setUcPreset]  = useLocalStorage('nai_gen_uc_preset', 'light')
  const [quality,   setQuality]   = useLocalStorage('nai_gen_quality',   true)
  const [sampler,       setSampler]       = useLocalStorage('nai_gen_sampler',        AI_DEFAULTS.sampler)
  const [noiseSchedule, setNoiseSchedule] = useLocalStorage('nai_gen_noise_schedule', AI_DEFAULTS.noiseSchedule)
  const [cfgRescale,    setCfgRescale]    = useLocalStorage('nai_gen_cfg_rescale',    AI_DEFAULTS.cfgRescale)
  const [varietyBoost,  setVarietyBoost]  = useLocalStorage('nai_gen_variety_boost',  AI_DEFAULTS.varietyBoost)

  // プロンプトチャンク
  const promptRef                     = useRef<HTMLTextAreaElement>(null)
  const negPromptRef                  = useRef<HTMLTextAreaElement>(null)
  const [chunksOpen,  setChunksOpen]  = useState(false)
  // チャンクのボタンを押すと textarea のフォーカスが外れるので、最後のカーソル位置を覚えておく。
  const caretRef                      = useRef<{ field: 'prompt' | 'neg'; start: number; end: number } | null>(null)
  const [insertTarget, setInsertTarget] = useState<'prompt' | 'neg'>('prompt')

  const rememberCaret = (field: 'prompt' | 'neg', ta: HTMLTextAreaElement) => {
    caretRef.current = { field, start: ta.selectionStart, end: ta.selectionEnd }
    if (field !== insertTarget) setInsertTarget(field)
  }

  // AI設定パネルの開閉
  const [aiOpen,       setAiOpen]       = useState(true)
  const [advancedOpen, setAdvancedOpen] = useState(true)

  // プリセット(/api/image/presets、挿絵パネルと共用)
  const [presets,    setPresets]    = useState<ImagePreset[]>([])
  const [presetId,   setPresetId]   = useState<number | null>(null)
  const [presetName, setPresetName] = useState('')
  const [presetMsg,  setPresetMsg]  = useState<{ text: string; isError: boolean } | null>(null)

  // キャラシート(キャラ別データセットと共用)からプロンプト・ネガティブ・基準シードを読み込む
  const [characters,  setCharacters]  = useState<Character[]>([])
  const [characterId, setCharacterId] = useState<number | null>(null)
  const [sheetMsg,    setSheetMsg]    = useState<{ text: string; isError: boolean } | null>(null)

  // セッション中のみ保持
  const [seed,         setSeed]         = useState<string>('')
  const [loading,      setLoading]      = useState(false)
  const [error,        setError]        = useState<string | null>(null)
  const [result,       setResult]       = useState<{ src: string; filename: string } | null>(null)
  const [preview,      setPreview]      = useState<string | null>(null)
  const [anlasEst,     setAnlasEst]     = useState<number | null>(null)
  const [anlasLoading, setAnlasLoading] = useState(false)

  // 結果画像は Blob URL で保持する。data URL だとスマホの長押し保存で
  // ファイル名が「ダウンロード」固定になるため、<a download> で包んで名前を付ける
  // (Selection の DownloadableImage と同じ方式)。
  useEffect(() => {
    if (!result) return
    return () => URL.revokeObjectURL(result.src)
  }, [result])

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

  const loadPresets = useCallback(async () => {
    if (!token) return
    try {
      setPresets(await apiFetch<ImagePreset[]>(token, '/api/image/presets'))
    } catch {
      // 一覧が取れなくても生成はできるのでサイレント失敗
    }
  }, [token])

  useEffect(() => { loadPresets() }, [loadPresets])

  useEffect(() => {
    if (!token) return
    apiFetch<Character[]>(token, '/api/story/characters')
      .then(setCharacters)
      .catch(() => {})  // 一覧が取れなくても生成はできるのでサイレント失敗
  }, [token])

  /**
   * キャラシートのプロンプト・ネガティブプロンプト・基準シードで入力欄を置き換える。
   * 組み立てはキャラ別データセット(全年齢)からトリガーワードを除いたもので、データセットと同じ見た目を単発で試せる。
   */
  const importCharacterSheet = async () => {
    const target = characters.find(c => c.id === characterId)
    if (!token || !target) return
    if ((prompt.trim() || negPrompt.trim())
        && !window.confirm(`今のプロンプトとネガティブプロンプトを「${target.name}」のキャラシートで置き換えます。よろしいですか?`)) {
      return
    }
    try {
      const sheet = await apiFetch<CharacterSheetPrompt>(token, `/api/lora-dataset/character/${target.id}/sheet-prompt`)
      setPrompt(sheet.prompt)
      setNegPrompt(sheet.negative_prompt)
      setSeed(sheet.seed != null ? String(sheet.seed) : '')
      caretRef.current = null
      setSheetMsg({
        text: [
          `「${sheet.name}」のキャラシートを読み込みました`,
          sheet.seed == null ? '基準シードが無いため、シード値は空(毎回ランダム)にしました' : '',
          target.reference_image_path ? '参照画像はこのページでは使いません' : '',
        ].filter(Boolean).join('。'),
        isError: false,
      })
    } catch (e) {
      setSheetMsg({ text: e instanceof Error ? e.message : String(e), isError: true })
    }
  }

  /**
   * 最後にカーソルがあった欄(プロンプト/ネガティブ)のカーソル位置にチャンクを挿入する。
   * 範囲選択中なら選択部分を置き換え、まだどちらにも触れていなければプロンプト末尾に足す。
   * 前後のタグとはカンマで区切る。
   */
  const insertChunk = (text: string) => {
    const field   = caretRef.current?.field ?? 'prompt'
    const value   = field === 'prompt' ? prompt : negPrompt
    const setter  = field === 'prompt' ? setPrompt : setNegPrompt
    const ref     = field === 'prompt' ? promptRef : negPromptRef
    const start   = Math.min(caretRef.current?.start ?? value.length, value.length)
    const end     = Math.min(caretRef.current?.end   ?? value.length, value.length)
    const before  = value.slice(0, start)
    const after   = value.slice(end)
    // 既にあるカンマ・空白は活かし、足りない分だけ補って "a, X, b" の形にそろえる。
    const lead  = !before.trim() ? '' : /,\s*$/.test(before) ? (/\s$/.test(before) ? '' : ' ') : ', '
    const trail = !after.trim()  ? '' : /^\s*,/.test(after)  ? '' : (/^\s/.test(after) ? ',' : ', ')
    setter(before + lead + text + trail + after)

    // 次のチャンクも続けて同じ場所へ入るよう、挿入した直後にカーソルを進める。
    const caret = (before + lead + text).length
    caretRef.current = { field, start: caret, end: caret }
    requestAnimationFrame(() => {
      ref.current?.focus()
      ref.current?.setSelectionRange(caret, caret)
    })
  }

  const resetAiSettings = () => {
    setSteps(AI_DEFAULTS.steps)
    setScale(AI_DEFAULTS.scale)
    setSampler(AI_DEFAULTS.sampler)
    setNoiseSchedule(AI_DEFAULTS.noiseSchedule)
    setCfgRescale(AI_DEFAULTS.cfgRescale)
    setVarietyBoost(AI_DEFAULTS.varietyBoost)
    setSeed('')
  }

  const applyPreset = (preset: ImagePreset) => {
    const s = preset.settings
    const notes: string[] = []
    if (MODELS.some(m => m.value === s.model)) {
      setModel(s.model)
    } else {
      notes.push(`モデル ${s.model} はこのページで使えないため変更していません`)
    }
    const matchedSize = SIZES.find(z => z.width === s.width && z.height === s.height)
    if (matchedSize) {
      setSize(matchedSize.value)
    } else {
      notes.push(`サイズ ${s.width}×${s.height} は選択肢に無いため変更していません`)
    }
    setSteps(s.steps)
    setScale(s.scale)
    setSampler(s.sampler)
    setNoiseSchedule(s.noise_schedule)
    setCfgRescale(s.cfg_rescale)
    setVarietyBoost(s.variety_boost ?? false)
    setSeed(s.seed != null ? String(s.seed) : '')
    setPresetId(preset.id)
    setPresetName(preset.name)
    setPresetMsg({
      text: [`「${preset.name}」を読み込みました`, ...notes].join('。'),
      isError: false,
    })
  }

  const savePreset = async () => {
    const name = presetName.trim()
    if (!token || !name) return
    if (!PRESET_MODELS.has(model)) {
      setPresetMsg({ text: 'プリセットに保存できるのは V4.5 / V5 系のモデルだけです', isError: true })
      return
    }
    const dims = SIZES.find(z => z.value === size) ?? SIZES[0]
    // 同名プリセットを上書きするときは、このページに無い項目(ネガティブプロンプト・複雑さ)を残す。
    const existing = presets.find(p => p.name === name)
    const settings: ImagePresetSettings = {
      negative_prompt: null,
      complexity:      'high',
      ...existing?.settings,
      model,
      width:          dims.width,
      height:         dims.height,
      steps,
      scale,
      sampler,
      noise_schedule: noiseSchedule,
      cfg_rescale:    cfgRescale,
      variety_boost:  varietyBoost,
      seed:           seed ? Number(seed) : null,
    }
    try {
      const saved = await apiFetch<ImagePreset>(token, '/api/image/presets', { name, settings })
      setPresetId(saved.id)
      setPresetMsg({ text: `「${name}」を保存しました`, isError: false })
      await loadPresets()
    } catch (e) {
      setPresetMsg({ text: e instanceof Error ? e.message : String(e), isError: true })
    }
  }

  const deletePreset = async () => {
    const target = presets.find(p => p.id === presetId)
    if (!token || !target) return
    try {
      const res = await fetch(`/api/image/presets/${target.id}`, {
        method:  'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setPresetId(null)
      setPresetMsg({ text: `「${target.name}」を削除しました`, isError: false })
      await loadPresets()
    } catch (e) {
      setPresetMsg({ text: e instanceof Error ? e.message : String(e), isError: true })
    }
  }

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
            sampler,
            noise_schedule: noiseSchedule,
            cfg_rescale:    cfgRescale,
            variety_boost:  varietyBoost,
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
  }, [token, prompt, negPrompt, model, size, steps, scale, seed, quality, ucPreset,
      sampler, noiseSchedule, cfgRescale, varietyBoost])

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
      sampler,
      noise_schedule: noiseSchedule,
      cfg_rescale:    cfgRescale,
      variety_boost:  varietyBoost,
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
        const detail = (errData as { detail?: string }).detail
        if (isAuthExpired(res.status, detail)) notifyAuthExpired()
        throw new Error(detail ?? `HTTP ${res.status}`)
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
              const detail = chunk.detail as string | undefined
              if (isAuthExpired(0, detail)) notifyAuthExpired()
              throw new Error(detail ?? 'ストリーミングエラー')
            }
            const img = chunk.image as string | undefined
            if (!img) continue
            if (eventType === 'intermediate') {
              setPreview(`data:image/png;base64,${img}`)
            } else if (eventType === 'final') {
              const bytes = Uint8Array.from(atob(img), c => c.charCodeAt(0))
              setResult({
                src: URL.createObjectURL(new Blob([bytes], { type: 'image/png' })),
                filename: naiImageFilename(),
              })
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
  }, [token, prompt, negPrompt, model, size, steps, scale, seed, quality, ucPreset,
      sampler, noiseSchedule, cfgRescale, varietyBoost,
      i2iEnabled, i2iImage, i2iStrength, i2iNoise])

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
    a.download = result.filename
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
              ref={promptRef}
              id="ig-prompt"
              className="ig-textarea ig-textarea--prompt"
              value={prompt}
              onChange={e => { setPrompt(e.target.value); rememberCaret('prompt', e.target) }}
              onSelect={e => rememberCaret('prompt', e.currentTarget)}
              onKeyDown={handleKeyDown}
              placeholder="1girl, masterpiece, best quality, ..."
              rows={5}
            />
          </section>

          {/* Prompt chunks */}
          <section className="ig-section">
            <button
              type="button"
              className="ig-advanced-toggle"
              onClick={() => setChunksOpen(!chunksOpen)}
              aria-expanded={chunksOpen}
            >
              プロンプトチャンク {chunksOpen ? '▼' : '▶'}
            </button>
            {chunksOpen && (
              <>
                <p className="ig-chunk-note">
                  挿入先: <strong>{insertTarget === 'prompt' ? 'プロンプト' : 'ネガティブプロンプト'}</strong> のカーソル位置
                </p>
                <ChunkPicker onInsert={insertChunk} />
              </>
            )}
          </section>

          {/* Negative prompt */}
          <section className="ig-section">
            <label className="ig-label" htmlFor="ig-neg-prompt">ネガティブプロンプト</label>
            <textarea
              ref={negPromptRef}
              id="ig-neg-prompt"
              className="ig-textarea ig-textarea--neg"
              value={negPrompt}
              onChange={e => { setNegPrompt(e.target.value); rememberCaret('neg', e.target) }}
              onSelect={e => rememberCaret('neg', e.currentTarget)}
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

          {/* Presets */}
          <section className="ig-section ig-settings">
            <span className="ig-label">プリセット</span>
            <div className="ig-preset-row">
              <select
                className="ig-select"
                aria-label="プリセットを選択"
                value={presetId ?? ''}
                onChange={e => {
                  const p = presets.find(x => String(x.id) === e.target.value)
                  if (p) applyPreset(p)
                  else setPresetId(null)
                }}
              >
                <option value="">選択して読み込み...</option>
                {presets.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
              <button
                type="button"
                className="ig-preset-btn ig-preset-btn--danger"
                onClick={deletePreset}
                disabled={presetId === null}
                title="選択中のプリセットを削除"
              >
                削除
              </button>
            </div>
            <div className="ig-preset-row">
              <input
                type="text"
                className="ig-input-number"
                aria-label="プリセット名"
                placeholder="プリセット名(同名は上書き)"
                value={presetName}
                onChange={e => setPresetName(e.target.value)}
              />
              <button
                type="button"
                className="ig-preset-btn"
                onClick={savePreset}
                disabled={!presetName.trim()}
              >
                保存
              </button>
            </div>
            {presetMsg && (
              <p className={presetMsg.isError ? 'ig-preset-msg ig-preset-msg--error' : 'ig-preset-msg'}>
                {presetMsg.text}
              </p>
            )}
          </section>

          {/* Character sheet */}
          <section className="ig-section ig-settings">
            <span className="ig-label">キャラシート</span>
            <div className="ig-preset-row">
              <select
                className="ig-select"
                aria-label="キャラシートを選択"
                value={characterId ?? ''}
                onChange={e => {
                  setCharacterId(e.target.value ? Number(e.target.value) : null)
                  setSheetMsg(null)
                }}
              >
                <option value="">キャラを選択...</option>
                {characters.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
              <button
                type="button"
                className="ig-preset-btn"
                onClick={importCharacterSheet}
                disabled={characterId === null}
                title="プロンプト・ネガティブプロンプト・シード値をキャラシートの内容で置き換える"
              >
                読み込む
              </button>
            </div>
            {sheetMsg && (
              <p className={sheetMsg.isError ? 'ig-preset-msg ig-preset-msg--error' : 'ig-preset-msg'}>
                {sheetMsg.text}
              </p>
            )}
          </section>

          {/* AI settings (NovelAI の「AI設定」パネルと同じ項目名・並び) */}
          <section className="ig-section ig-ai-panel">
            <div className="ig-ai-header">
              <span className="ig-ai-title">AI設定</span>
              <div className="ig-ai-header-actions">
                <button type="button" className="ig-icon-btn" onClick={resetAiSettings} title="既定値に戻す">↺</button>
                <button
                  type="button"
                  className="ig-icon-btn"
                  onClick={() => setAiOpen(o => !o)}
                  title={aiOpen ? '折りたたむ' : '展開する'}
                  aria-expanded={aiOpen}
                >
                  {aiOpen ? '▼' : '▶'}
                </button>
              </div>
            </div>

            {aiOpen && (
              <div className="ig-ai-body">
                <SliderField
                  id="ig-steps" label="ステップ"
                  value={steps} min={1} max={50} step={1}
                  onChange={setSteps}
                />

                <SliderField
                  id="ig-scale" label="プロンプトガイダンス"
                  value={scale} min={0} max={10} step={0.1}
                  onChange={setScale}
                  extra={
                    <button
                      type="button"
                      className={varietyBoost ? 'ig-toggle-chip ig-toggle-chip--on' : 'ig-toggle-chip'}
                      onClick={() => setVarietyBoost(!varietyBoost)}
                      aria-pressed={varietyBoost}
                      title="Variety Boost"
                    >
                      {varietyBoost ? '✓' : '✕'} 多様性
                    </button>
                  }
                />

                <div className="ig-ai-pair">
                  <div className="ig-ai-field">
                    <label className="ig-ai-label" htmlFor="ig-seed">シード値</label>
                    <div className="ig-seed-row">
                      <input
                        id="ig-seed"
                        type="number" min={0} max={4294967295}
                        value={seed}
                        onChange={e => setSeed(e.target.value)}
                        placeholder="シード値を入力"
                        className="ig-input-number"
                      />
                      <button
                        type="button"
                        className="ig-icon-btn ig-icon-btn--boxed"
                        onClick={() => setSeed(String(Math.floor(Math.random() * 4294967296)))}
                        title="ランダムなシード値を入れる(空欄なら毎回ランダム)"
                      >
                        🌱
                      </button>
                    </div>
                  </div>
                  <div className="ig-ai-field">
                    <label className="ig-ai-label" htmlFor="ig-sampler">サンプラー</label>
                    <select
                      id="ig-sampler"
                      className="ig-select"
                      value={sampler}
                      onChange={e => setSampler(e.target.value)}
                    >
                      {SAMPLERS.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
                    </select>
                  </div>
                </div>

                <button
                  type="button"
                  className="ig-advanced-toggle"
                  onClick={() => setAdvancedOpen(o => !o)}
                  aria-expanded={advancedOpen}
                >
                  詳細設定 {advancedOpen ? '▼' : '▶'}
                </button>

                {advancedOpen && (
                  <>
                    <SliderField
                      id="ig-cfg-rescale" label="プロンプトガイダンスの再調整"
                      value={cfgRescale} min={0} max={1} step={0.01}
                      onChange={setCfgRescale}
                    />

                    <div className="ig-ai-field">
                      <label className="ig-ai-label" htmlFor="ig-noise-schedule">ノイズ設定</label>
                      <select
                        id="ig-noise-schedule"
                        className="ig-select"
                        value={noiseSchedule}
                        onChange={e => setNoiseSchedule(e.target.value)}
                      >
                        {NOISE_SCHEDULES.map(n => <option key={n} value={n}>{n}</option>)}
                      </select>
                    </div>
                  </>
                )}
              </div>
            )}
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
              <a href={result.src} download={result.filename}>
                <img
                  className="ig-result-img"
                  src={result.src}
                  alt="生成された画像"
                />
              </a>
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
