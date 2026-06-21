import { useState, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { useLocalStorage } from '../hooks/useLocalStorage'
import { streamLoraDataset, streamLoraDatasetPreview, LoraDatasetProgressEvent, LoraDatasetCompleteEvent, LoraDatasetRequest } from '../api'
import './LoraDataset.css'

const MODELS = [
  { value: 'nai-diffusion-4-5-full',    label: 'NAI Diffusion V4.5 Full' },
  { value: 'nai-diffusion-4-5-curated', label: 'NAI Diffusion V4.5 Curated' },
  { value: 'nai-diffusion-4-full',      label: 'NAI Diffusion V4 Full' },
  { value: 'nai-diffusion-4-curated',   label: 'NAI Diffusion V4 Curated' },
  { value: 'nai-diffusion-3',           label: 'NAI Diffusion V3' },
  { value: 'nai-diffusion-3-furry',     label: 'NAI Diffusion V3 Furry' },
] as const

const SAMPLERS = [
  { value: 'k_euler_ancestral',   label: 'Euler Ancestral' },
  { value: 'k_euler',             label: 'Euler' },
  { value: 'k_dpm_2',             label: 'DPM2' },
  { value: 'k_dpm_2_ancestral',   label: 'DPM2 Ancestral' },
  { value: 'k_dpmpp_2m',          label: 'DPM++ 2M' },
  { value: 'k_dpmpp_2s_ancestral',label: 'DPM++ 2S Ancestral' },
  { value: 'k_dpmpp_sde',         label: 'DPM++ SDE' },
  { value: 'ddim',                label: 'DDIM' },
] as const

const NOISE_SCHEDULES = [
  { value: 'karras',          label: 'Karras' },
  { value: 'exponential',     label: 'Exponential' },
  { value: 'polyexponential', label: 'Polyexponential' },
] as const

const REFERENCE_TYPES = [
  { value: 'character&style', label: 'キャラクター＆画風' },
  { value: 'character',       label: 'キャラクターのみ' },
  { value: 'style',           label: '画風のみ' },
] as const

const V4_5_MODELS = new Set(['nai-diffusion-4-5-full', 'nai-diffusion-4-5-curated'])

function readFileAsDataURL(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

// 顔30枚 / 上半身40枚 / 全身30枚の固定テンプレート
const SHOT_PLAN = [
  { category: 'face',       label: '顔アップ',  count: 30, size: '512×512' },
  { category: 'upper_body', label: '上半身',    count: 40, size: '512×768' },
  { category: 'full_body',  label: '全身',      count: 30, size: '512×768' },
] as const

type Phase = 'idle' | 'running' | 'complete' | 'error' | 'cancelled'

export default function LoraDataset() {
  const { token } = useAuth()
  const navigate = useNavigate()

  const [characterId, setCharacterId] = useLocalStorage('nai_lora_character_id', 'cahrunna')
  const [triggerWord, setTriggerWord] = useLocalStorage('nai_lora_trigger_word', 'cahrunna_girl')
  const [baseTags,    setBaseTags]    = useLocalStorage('nai_lora_base_tags', '1girl, solo, brown hair, medium hair, brown eyes')
  const [extraTags,   setExtraTags]   = useState('')
  const [outfitTag,   setOutfitTag]   = useLocalStorage('nai_lora_outfit_tag', 'white dress')
  const [rootName,    setRootName]   = useState('training_data')

  const [model,        setModel]        = useLocalStorage('nai_lora_model', 'nai-diffusion-3')
  const [steps,         setSteps]         = useLocalStorage('nai_lora_steps', 23)
  const [scale,         setScale]         = useLocalStorage('nai_lora_scale', 5.0)
  const [sampler,       setSampler]       = useLocalStorage('nai_lora_sampler', 'k_euler_ancestral')
  const [noiseSchedule, setNoiseSchedule] = useLocalStorage('nai_lora_noise_schedule', 'karras')
  const [cfgRescale,    setCfgRescale]    = useLocalStorage('nai_lora_cfg_rescale', 0.0)
  const [negativePrompt, setNegativePrompt] = useLocalStorage(
    'nai_lora_negative_prompt',
    'worst quality, low quality, blurry, bad anatomy, extra limbs, missing fingers, ugly, duplicate',
  )
  const [seed, setSeed] = useState('')
  const [shuffleTags, setShuffleTags] = useLocalStorage('nai_lora_shuffle_tags', false)

  // 精密参照画像（Precise Character Reference）
  const charRefFileRef = useRef<HTMLInputElement>(null)
  const [charRefEnabled,  setCharRefEnabled]  = useState(false)
  const [charRefImage,    setCharRefImage]    = useState<string | null>(null)
  const [charRefDragOver, setCharRefDragOver] = useState(false)
  const [charRefType,     setCharRefType]     = useState<typeof REFERENCE_TYPES[number]['value']>('character&style')
  const [charRefFidelity, setCharRefFidelity] = useState(1.0)
  const [charRefStrength, setCharRefStrength] = useState(1.0)

  // Vibe Transfer
  const vibeFileRef = useRef<HTMLInputElement>(null)
  const [vibeEnabled,      setVibeEnabled]      = useState(false)
  const [vibeImage,        setVibeImage]        = useState<string | null>(null)
  const [vibeDragOver,     setVibeDragOver]     = useState(false)
  const [vibeInfoExtracted, setVibeInfoExtracted] = useState(0.7)
  const [vibeStrength,     setVibeStrength]     = useState(0.6)

  const [phase,    setPhase]    = useState<Phase>('idle')
  const [progress, setProgress] = useState({ current: 0, total: 0 })
  const [log,       setLog]     = useState<LoraDatasetProgressEvent[]>([])
  const [summary,   setSummary] = useState<LoraDatasetCompleteEvent | null>(null)
  const [errorMsg,  setErrorMsg] = useState<string | null>(null)

  const abortRef = useRef<AbortController | null>(null)
  const cancelledRef = useRef(false)

  const logEndRef = useRef<HTMLDivElement>(null)
  const scrollLog = useCallback(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  const [previewPhase,  setPreviewPhase]  = useState<Phase>('idle')
  const [previewImages, setPreviewImages] = useState<LoraDatasetProgressEvent[]>([])
  const [previewError,  setPreviewError]  = useState<string | null>(null)

  const totalCount = SHOT_PLAN.reduce((sum, s) => sum + s.count, 0)
  const isRunning = phase === 'running'
  const isPreviewRunning = previewPhase === 'running'
  const isV45 = V4_5_MODELS.has(model)
  const canRun = !isRunning && !isPreviewRunning && characterId.trim() !== '' && triggerWord.trim() !== ''
    && (!charRefEnabled || (!!charRefImage && isV45))

  const handleCharRefFile = useCallback((file: File) => {
    if (!file.type.match(/^image\//)) return
    readFileAsDataURL(file).then(setCharRefImage)
  }, [])

  const handleVibeFile = useCallback((file: File) => {
    if (!file.type.match(/^image\//)) return
    readFileAsDataURL(file).then(setVibeImage)
  }, [])

  const buildRequestBody = (): LoraDatasetRequest => ({
    character_id: characterId.trim(),
    trigger_word: triggerWord.trim(),
    base_tags: baseTags.trim(),
    extra_tags: extraTags.trim(),
    outfit_tag: outfitTag.trim(),
    root_name: rootName.trim() || 'training_data',
    model,
    steps,
    scale,
    sampler,
    noise_schedule: noiseSchedule,
    cfg_rescale: cfgRescale,
    negative_prompt: negativePrompt.trim(),
    seed: seed.trim() ? Number(seed) : undefined,
    shuffle_tags: shuffleTags,
    character_reference: (charRefEnabled && charRefImage) ? {
      image: charRefImage,
      type: charRefType,
      fidelity: charRefFidelity,
      strength: charRefStrength,
    } : undefined,
    vibe_transfer: (vibeEnabled && vibeImage) ? {
      images: [{
        image: vibeImage,
        info_extracted: vibeInfoExtracted,
        strength: vibeStrength,
        controlnet_model: model,
      }],
      strength: vibeStrength,
    } : undefined,
  })

  const handleGenerate = async () => {
    if (!token || !canRun) return
    cancelledRef.current = false
    const controller = new AbortController()
    abortRef.current = controller

    setPhase('running')
    setLog([])
    setSummary(null)
    setErrorMsg(null)
    setProgress({ current: 0, total: totalCount })

    await streamLoraDataset(
      token,
      buildRequestBody(),
      (e) => {
        setProgress({ current: e.current, total: e.total })
        setLog(prev => {
          const next = [...prev, e]
          setTimeout(scrollLog, 30)
          return next
        })
      },
      (e) => {
        if (!cancelledRef.current) { setSummary(e); setPhase('complete') }
      },
      (msg) => {
        if (!cancelledRef.current) { setErrorMsg(msg); setPhase('error') }
      },
      controller.signal,
    )
  }

  const handleCancel = () => {
    cancelledRef.current = true
    abortRef.current?.abort()
    setPhase('cancelled')
  }

  const handlePreview = async () => {
    if (!token || !canRun) return
    setPreviewPhase('running')
    setPreviewImages([])
    setPreviewError(null)

    await streamLoraDatasetPreview(
      token,
      buildRequestBody(),
      (e) => {
        setPreviewImages(prev => [...prev, e])
      },
      () => {
        setPreviewPhase('complete')
      },
      (msg) => {
        setPreviewError(msg)
        setPreviewPhase('error')
      },
    )
  }

  const percent = progress.total > 0 ? Math.round((progress.current / progress.total) * 100) : 0
  const CATEGORY_LABELS: Record<string, string> = { face: '顔アップ', upper_body: '上半身', full_body: '全身' }

  return (
    <div className="lora-root">
      <header className="lora-header">
        <button type="button" className="lora-back" onClick={() => navigate('/')}>
          ← ホーム
        </button>
        <span className="lora-header-title">LoRA学習データセット生成</span>
      </header>

      <div className="lora-body">
        {/* ===== 左: 設定パネル ===== */}
        <aside className="lora-sidebar">
          <section className="lora-form">
            <label className="lora-label" htmlFor="lora-character-id">キャラクターID（フォルダ名）</label>
            <input
              id="lora-character-id"
              className="lora-input"
              type="text"
              placeholder="cahrunna"
              value={characterId}
              onChange={e => setCharacterId(e.target.value)}
              disabled={isRunning}
            />

            <label className="lora-label" htmlFor="lora-trigger-word">トリガーワード</label>
            <input
              id="lora-trigger-word"
              className="lora-input"
              type="text"
              placeholder="cahrunna_girl"
              value={triggerWord}
              onChange={e => setTriggerWord(e.target.value)}
              disabled={isRunning}
            />
            <p className="lora-hint">全画像のプロンプト先頭に必ず付与され、LoRA学習時の識別キーになります。</p>

            <label className="lora-label" htmlFor="lora-base-tags">外見タグ（共通）</label>
            <textarea
              id="lora-base-tags"
              className="lora-textarea"
              rows={2}
              placeholder="1girl, solo, brown hair, medium hair, brown eyes"
              value={baseTags}
              onChange={e => setBaseTags(e.target.value)}
              disabled={isRunning}
            />

            <label className="lora-label" htmlFor="lora-extra-tags">追加タグ（任意）</label>
            <textarea
              id="lora-extra-tags"
              className="lora-textarea"
              rows={2}
              placeholder="school uniform, ponytail, scrunchie, ..."
              value={extraTags}
              onChange={e => setExtraTags(e.target.value)}
              disabled={isRunning}
            />

            <label className="lora-label" htmlFor="lora-outfit-tag">衣装タグ（上半身/全身用・空欄で無効化）</label>
            <input
              id="lora-outfit-tag"
              className="lora-input"
              type="text"
              placeholder="white dress"
              value={outfitTag}
              onChange={e => setOutfitTag(e.target.value)}
              disabled={isRunning}
            />

            <label className="lora-label" htmlFor="lora-root-name">出力ルートフォルダ名</label>
            <input
              id="lora-root-name"
              className="lora-input"
              type="text"
              placeholder="training_data"
              value={rootName}
              onChange={e => setRootName(e.target.value)}
              disabled={isRunning}
            />
            <p className="lora-hint">outputs/&lt;ルート名&gt;/&lt;キャラクターID&gt;/ に保存されます。パターンごとに分けてください。</p>

            <label className="lora-label" htmlFor="lora-negative">ネガティブプロンプト</label>
            <textarea
              id="lora-negative"
              className="lora-textarea"
              rows={2}
              value={negativePrompt}
              onChange={e => setNegativePrompt(e.target.value)}
              disabled={isRunning}
            />

            <fieldset className="lora-fieldset" disabled={isRunning}>
              <legend className="lora-label">AI設定</legend>

              <label className="lora-label" htmlFor="lora-model">モデル</label>
              <select id="lora-model" className="lora-select" value={model} onChange={e => setModel(e.target.value)}>
                {MODELS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
              </select>

              <label className="lora-label" htmlFor="lora-sampler">サンプラー</label>
              <select id="lora-sampler" className="lora-select" value={sampler} onChange={e => setSampler(e.target.value)}>
                {SAMPLERS.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>

              <label className="lora-label" htmlFor="lora-noise">ノイズ設定</label>
              <select id="lora-noise" className="lora-select" value={noiseSchedule} onChange={e => setNoiseSchedule(e.target.value)}>
                {NOISE_SCHEDULES.map(n => <option key={n.value} value={n.value}>{n.label}</option>)}
              </select>

              <div className="lora-grid-2">
                <div>
                  <label className="lora-label" htmlFor="lora-steps">ステップ</label>
                  <input
                    id="lora-steps" className="lora-input" type="number" min={1} max={50}
                    value={steps} onChange={e => setSteps(Number(e.target.value))}
                  />
                </div>
                <div>
                  <label className="lora-label" htmlFor="lora-scale">プロンプトガイダンス</label>
                  <input
                    id="lora-scale" className="lora-input" type="number" step={0.1} min={0} max={10}
                    value={scale} onChange={e => setScale(Number(e.target.value))}
                  />
                </div>
              </div>

              <label className="lora-label" htmlFor="lora-cfg-rescale">
                ガイダンス再調整: <strong>{cfgRescale.toFixed(2)}</strong>
              </label>
              <input
                id="lora-cfg-rescale"
                className="lora-range"
                type="range" min={0} max={1} step={0.01}
                value={cfgRescale}
                onChange={e => setCfgRescale(parseFloat(e.target.value))}
              />

              <label className="lora-label" htmlFor="lora-seed">ベースシード値（空欄でランダム）</label>
              <input
                id="lora-seed"
                className="lora-input"
                type="number" min={0} max={4294967295}
                placeholder="ランダム"
                value={seed}
                onChange={e => {
                  const v = e.target.value
                  if (v === '') { setSeed(''); return }
                  const n = Math.min(4294967295, Math.max(0, Math.floor(Number(v))))
                  setSeed(Number.isNaN(n) ? '' : String(n))
                }}
              />
              <p className="lora-hint">
                0〜4294967295の範囲で指定してください（NovelAIのシード値仕様・32bit整数）。
                画像ごとに連番オフセット（seed, seed+1, seed+2...）を適用し、再現性を保ちつつ構図に差を出します。
              </p>

              <label className="lora-checkbox-label">
                <input
                  type="checkbox"
                  checked={shuffleTags}
                  onChange={e => setShuffleTags(e.target.checked)}
                />
                タグの順序をシャッフル（トリガーワードは先頭固定）
              </label>
            </fieldset>

            {/* 精密参照画像 */}
            <fieldset className="lora-fieldset" disabled={isRunning}>
              <legend className="lora-label">精密参照画像（V4.5系モデル限定）</legend>
              <label className="lora-checkbox-label">
                <input
                  type="checkbox"
                  checked={charRefEnabled}
                  onChange={e => { setCharRefEnabled(e.target.checked); if (!e.target.checked) setCharRefImage(null) }}
                />
                有効化
              </label>

              {charRefEnabled && (
                <>
                  {!isV45 && (
                    <p className="lora-warn">精密参照画像はNAI Diffusion V4.5系モデルでのみ使用できます。上のモデル選択を変更してください。</p>
                  )}
                  <div
                    className={['lora-dropzone', charRefDragOver ? 'lora-dropzone--over' : '', charRefImage ? 'lora-dropzone--has-image' : ''].join(' ')}
                    onDragOver={e => { e.preventDefault(); setCharRefDragOver(true) }}
                    onDragLeave={() => setCharRefDragOver(false)}
                    onDrop={e => { e.preventDefault(); setCharRefDragOver(false); const f = e.dataTransfer.files[0]; if (f) handleCharRefFile(f) }}
                    onClick={() => charRefFileRef.current?.click()}
                    role="button" tabIndex={0}
                    onKeyDown={e => e.key === 'Enter' && charRefFileRef.current?.click()}
                  >
                    {charRefImage
                      ? <img className="lora-dropzone-thumb" src={charRefImage} alt="参照画像" />
                      : <span>参照画像をドロップ / クリック</span>}
                  </div>
                  <input ref={charRefFileRef} type="file" accept="image/*" hidden
                    onChange={e => { const f = e.target.files?.[0]; if (f) handleCharRefFile(f); e.target.value = '' }} />

                  <label className="lora-label" htmlFor="lora-charref-type">参照タイプ</label>
                  <select id="lora-charref-type" className="lora-select" value={charRefType} onChange={e => setCharRefType(e.target.value as typeof charRefType)}>
                    {REFERENCE_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                  </select>

                  <label className="lora-label">Fidelity: <strong>{charRefFidelity.toFixed(2)}</strong></label>
                  <input className="lora-range" type="range" min={0} max={1} step={0.01} aria-label="Fidelity"
                    value={charRefFidelity} onChange={e => setCharRefFidelity(parseFloat(e.target.value))} />

                  <label className="lora-label">強度: <strong>{charRefStrength.toFixed(2)}</strong></label>
                  <input className="lora-range" type="range" min={0} max={1} step={0.01} aria-label="精密参照画像の強度"
                    value={charRefStrength} onChange={e => setCharRefStrength(parseFloat(e.target.value))} />
                </>
              )}
            </fieldset>

            {/* Vibe Transfer */}
            <fieldset className="lora-fieldset" disabled={isRunning}>
              <legend className="lora-label">Vibe Transfer（雰囲気転送）</legend>
              <label className="lora-checkbox-label">
                <input
                  type="checkbox"
                  checked={vibeEnabled}
                  onChange={e => { setVibeEnabled(e.target.checked); if (!e.target.checked) setVibeImage(null) }}
                />
                有効化
              </label>

              {vibeEnabled && (
                <>
                  <div
                    className={['lora-dropzone', vibeDragOver ? 'lora-dropzone--over' : '', vibeImage ? 'lora-dropzone--has-image' : ''].join(' ')}
                    onDragOver={e => { e.preventDefault(); setVibeDragOver(true) }}
                    onDragLeave={() => setVibeDragOver(false)}
                    onDrop={e => { e.preventDefault(); setVibeDragOver(false); const f = e.dataTransfer.files[0]; if (f) handleVibeFile(f) }}
                    onClick={() => vibeFileRef.current?.click()}
                    role="button" tabIndex={0}
                    onKeyDown={e => e.key === 'Enter' && vibeFileRef.current?.click()}
                  >
                    {vibeImage
                      ? <img className="lora-dropzone-thumb" src={vibeImage} alt="雰囲気参照画像" />
                      : <span>雰囲気参照画像をドロップ / クリック</span>}
                  </div>
                  <input ref={vibeFileRef} type="file" accept="image/*" hidden
                    onChange={e => { const f = e.target.files?.[0]; if (f) handleVibeFile(f); e.target.value = '' }} />

                  <label className="lora-label">情報抽出量: <strong>{vibeInfoExtracted.toFixed(2)}</strong></label>
                  <input className="lora-range" type="range" min={0.01} max={1} step={0.01} aria-label="情報抽出量"
                    value={vibeInfoExtracted} onChange={e => setVibeInfoExtracted(parseFloat(e.target.value))} />

                  <label className="lora-label">強度: <strong>{vibeStrength.toFixed(2)}</strong></label>
                  <input className="lora-range" type="range" min={0.01} max={1} step={0.01} aria-label="Vibe Transferの強度"
                    value={vibeStrength} onChange={e => setVibeStrength(parseFloat(e.target.value))} />
                </>
              )}
            </fieldset>
          </section>

          <div className="lora-actions">
            <button
              type="button"
              className="lora-btn lora-btn--secondary"
              onClick={handlePreview}
              disabled={!canRun}
            >
              {isPreviewRunning
                ? <><span className="lora-spinner lora-spinner--sm" />生成中…</>
                : '🔍 1セットプレビュー'}
            </button>
            {isRunning ? (
              <button
                type="button"
                className="lora-btn lora-btn--danger"
                onClick={handleCancel}
              >
                ⏹ 中断
              </button>
            ) : (
              <button
                type="button"
                className="lora-btn lora-btn--primary"
                onClick={handleGenerate}
                disabled={!canRun}
              >
                {`▶ ${totalCount}枚を生成`}
              </button>
            )}
          </div>
        </aside>

        {/* ===== 右: ショット構成 / 進捗 ===== */}
        <main className="lora-main">
          <section className="lora-section">
            <span className="lora-section-label">ショット構成（固定テンプレート）</span>
            <div className="lora-plan">
              {SHOT_PLAN.map(s => (
                <div key={s.category} className="lora-plan-card">
                  <span className="lora-plan-label">{s.label}</span>
                  <span className="lora-plan-count">{s.count}枚</span>
                  <span className="lora-plan-size">{s.size}</span>
                </div>
              ))}
            </div>
          </section>

          {previewPhase !== 'idle' && (
            <section className="lora-section">
              <span className="lora-section-label">1セットプレビュー</span>
              <div className="lora-preview-grid">
                {previewImages.map((e, i) => (
                  <div key={i} className="lora-preview-card">
                    <span className="lora-preview-label">{CATEGORY_LABELS[e.category] ?? e.category}</span>
                    {e.status === 'ok' && e.image_b64
                      ? <img className="lora-preview-img" src={`data:image/png;base64,${e.image_b64}`} alt={e.category} />
                      : <span className="lora-preview-failed">✖ 失敗{e.message ? `: ${e.message}` : ''}</span>}
                  </div>
                ))}
                {isPreviewRunning && previewImages.length < 3 && (
                  <div className="lora-preview-card lora-preview-card--pending">
                    <span className="lora-spinner lora-spinner--sm" />
                  </div>
                )}
              </div>
              {previewError && <p className="lora-error" role="alert">{previewError}</p>}
            </section>
          )}

          {phase !== 'idle' && (
            <section className="lora-section">
              <span className="lora-section-label">生成進捗</span>
              <div className="lora-progress-wrap">
                <progress
                  className={`lora-progress-bar${phase === 'complete' ? ' lora-progress-bar--done' : ''}`}
                  value={progress.current}
                  max={progress.total || 1}
                />
                <span className="lora-progress-text">
                  {progress.current} / {progress.total} ({percent}%)
                </span>
              </div>

              {phase === 'cancelled' && (
                <p className="lora-warn">⏹ 中断しました（{progress.current} / {progress.total} 枚まで生成済み）。「生成」を押すと最初からやり直します。</p>
              )}

              {summary && (
                <div className="lora-result-summary">
                  <span className="lora-result-item lora-result-item--ok">✔ 成功: {summary.succeeded}</span>
                  {summary.failed > 0 && (
                    <span className="lora-result-item lora-result-item--err">✖ 失敗: {summary.failed}</span>
                  )}
                  <span className="lora-result-item">📁 {summary.output_path}</span>
                </div>
              )}

              <div className="lora-log">
                {log.map((entry, i) => (
                  <div key={i} className={`lora-log-row lora-log-row--${entry.status}`}>
                    <span className="lora-log-icon">{entry.status === 'ok' ? '✔' : '✖'}</span>
                    <span className="lora-log-category">{entry.category}/</span>
                    <span className="lora-log-file">{entry.file}</span>
                    {entry.message && <span className="lora-log-msg">{entry.message}</span>}
                  </div>
                ))}
                <div ref={logEndRef} />
              </div>
            </section>
          )}

          {errorMsg && <p className="lora-error" role="alert">{errorMsg}</p>}
        </main>
      </div>
    </div>
  )
}
