import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
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

interface CharacterReferenceEntry {
  image: string | null
  type: 'character' | 'style' | 'character&style'
  fidelity: number
  strength: number
}

interface CharacterPromptEntry {
  prompt: string
  negativePrompt: string
  x: number
  y: number
  enabled: boolean
}

interface HistoryCharacterReference {
  image: string | null
  type: string
  fidelity: number
  strength: number
}

interface HistoryCharacterPrompt {
  prompt: string
  negative_prompt: string
  position: [number, number] | string
  enabled: boolean
}

interface HistoryEntry {
  id: number
  prompt: string
  model: string
  size: string
  seed: number | null
  chunk_ids: string[]
  images: string[]
  i2i_image: string | null
  i2i_strength: number | null
  i2i_noise: number | null
  character_references: HistoryCharacterReference[]
  characters: HistoryCharacterPrompt[]
  created_at: string
}

type Mode = 'scenario' | 'random' | 'similar' | 'preset'

const CHARACTER_REFERENCE_TYPES = [
  { value: 'character&style', label: 'キャラ+スタイル' },
  { value: 'character',       label: 'キャラのみ' },
  { value: 'style',           label: 'スタイルのみ' },
] as const

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

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

// 同じ巨大な base64 文字列を <img src> と <a href> の両方に置くと、
// 長押しのコンテキストメニュー表示時にブラウザが二重に処理して固まることがあるため、
// 表示・ダウンロードとも軽量な Blob URL 経由の参照にまとめる。

// crypto.randomUUID() は secure context (https/localhost) 限定で、
// LAN上のスマホからは http://<IP>:5173 でアクセスするため使えない。
// 衝突しても実害が無いファイル名生成なので Math.random ベースの短縮IDで十分。
function shortId(): string {
  return Math.random().toString(16).slice(2, 10).padEnd(8, '0')
}

function formatForFilename(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}` +
    `-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`
  )
}

function DownloadableImage({ base64, alt, date }: { base64: string; alt: string; date?: Date }) {
  const [url, setUrl] = useState<string | null>(null)

  useEffect(() => {
    const bytes = Uint8Array.from(atob(base64), c => c.charCodeAt(0))
    const objectUrl = URL.createObjectURL(new Blob([bytes], { type: 'image/png' }))
    setUrl(objectUrl)
    return () => URL.revokeObjectURL(objectUrl)
  }, [base64])

  // 画像内容(base64)ごとに一度だけ生成し、再レンダーのたびに名前が変わらないようにする
  const filename = useMemo(() => `nai_${formatForFilename(date ?? new Date())}_${shortId()}.png`, [base64, date])

  if (!url) return null
  return (
    <a href={url} download={filename}>
      <img src={url} alt={alt} />
    </a>
  )
}

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

  const [negPrompt, setNegPrompt] = useState('')
  const [model, setModel] = useState<string>(MODELS[0].value)
  const [size, setSize] = useState<string>(SIZES[0].value)
  const [steps, setSteps] = useState(23)
  const [scale, setScale] = useState(5.0)
  const [seed, setSeed] = useState('')
  const [ucPreset, setUcPreset] = useState<string>(UC_PRESETS[0].value)
  const [quality, setQuality] = useState(true)

  const [i2iEnabled, setI2iEnabled] = useState(false)
  const [i2iImage, setI2iImage] = useState<string | null>(null)
  const [i2iStrength, setI2iStrength] = useState(0.7)
  const [i2iNoise, setI2iNoise] = useState(0.0)

  const [characterRefs, setCharacterRefs] = useState<CharacterReferenceEntry[]>([])
  const [characters, setCharacters] = useState<CharacterPromptEntry[]>([])

  const [generatedImages, setGeneratedImages] = useState<string[]>([])
  const [generating, setGenerating] = useState(false)
  const [generateError, setGenerateError] = useState<string | null>(null)

  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [showHistory, setShowHistory] = useState(false)

  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const loadHistory = () => {
    fetch('/api/chunks/history').then(r => r.json()).then(setHistory)
  }

  useEffect(() => {
    fetch('/api/chunks/situations').then(r => r.json()).then(setSituations)
    fetch('/api/chunks/presets').then(r => r.json()).then(setPresets)
    fetch('/api/chunks/imported').then(r => r.json()).then(setAllChunks)
    loadHistory()
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

  const handleI2iFile = async (file: File) => {
    if (!file.type.match(/^image\//)) return
    setI2iImage(await readFileAsDataUrl(file))
  }

  const addCharacterRef = () => {
    setCharacterRefs(prev => [...prev, { image: null, type: 'character&style', fidelity: 1.0, strength: 1.0 }])
  }

  const updateCharacterRef = (index: number, patch: Partial<CharacterReferenceEntry>) => {
    setCharacterRefs(prev => prev.map((c, i) => (i === index ? { ...c, ...patch } : c)))
  }

  const removeCharacterRef = (index: number) => {
    setCharacterRefs(prev => prev.filter((_, i) => i !== index))
  }

  const addCharacter = () => {
    setCharacters(prev => [...prev, { prompt: '', negativePrompt: '', x: 0.5, y: 0.5, enabled: true }])
  }

  const updateCharacter = (index: number, patch: Partial<CharacterPromptEntry>) => {
    setCharacters(prev => prev.map((c, i) => (i === index ? { ...c, ...patch } : c)))
  }

  const removeCharacter = (index: number) => {
    setCharacters(prev => prev.filter((_, i) => i !== index))
  }

  const generateImage = async () => {
    if (!token || !assembledText) return
    setGenerateError(null)
    setGenerating(true)
    setGeneratedImages([])
    try {
      const usedChunkIds = results.filter(c => checkedIds.has(c.id)).map(c => c.id)

      const i2i = i2iEnabled && i2iImage
        ? { image: i2iImage, strength: i2iStrength, noise: i2iNoise }
        : undefined

      const validCharacterRefs = characterRefs.filter(c => c.image)
      const character_references = validCharacterRefs.length > 0
        ? validCharacterRefs.map(c => ({ image: c.image, type: c.type, fidelity: c.fidelity, strength: c.strength }))
        : undefined

      const validCharacters = characters.filter(c => c.prompt.trim())
      const charactersPayload = validCharacters.length > 0
        ? validCharacters.map(c => ({
            prompt: c.prompt,
            negative_prompt: c.negativePrompt,
            position: [c.x, c.y],
            enabled: c.enabled,
          }))
        : undefined

      const res = await fetch('/api/chunks/select/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          chunk_ids: usedChunkIds,
          generation: {
            prompt: assembledText,
            negative_prompt: negPrompt.trim() || undefined,
            model,
            size,
            steps,
            scale,
            seed: seed ? Number(seed) : undefined,
            quality,
            uc_preset: ucPreset,
            n_samples: 1,
            i2i,
            character_references,
            characters: charactersPayload,
          },
        }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail ?? '生成に失敗しました')
      setGeneratedImages(data.images)
      loadHistory()
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

        <section className="selection-section">
          <h2>生成設定</h2>
          <div className="selection-settings-grid">
            <label>
              モデル
              <select value={model} onChange={e => setModel(e.target.value)}>
                {MODELS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
              </select>
            </label>
            <label>
              サイズ
              <select value={size} onChange={e => setSize(e.target.value)}>
                {SIZES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </label>
            <label>
              UC プリセット
              <select value={ucPreset} onChange={e => setUcPreset(e.target.value)}>
                {UC_PRESETS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
              </select>
            </label>
            <label>
              Seed
              <input
                type="number" min={0} max={4294967295}
                value={seed}
                onChange={e => setSeed(e.target.value)}
                placeholder="ランダム"
              />
            </label>
            <label className="selection-settings-row">
              Steps
              <input
                type="range" min={1} max={50} step={1}
                value={steps}
                onChange={e => setSteps(Number(e.target.value))}
              />
              <span>{steps}</span>
            </label>
            <label className="selection-settings-row">
              Scale
              <input
                type="range" min={0} max={10} step={0.1}
                value={scale}
                onChange={e => setScale(Number(e.target.value))}
              />
              <span>{scale.toFixed(1)}</span>
            </label>
            <label className="selection-settings-checkbox">
              <input
                type="checkbox"
                checked={quality}
                onChange={e => setQuality(e.target.checked)}
              />
              品質タグを自動付与
            </label>
          </div>
          <label className="selection-negprompt-label">
            ネガティブプロンプト
            <textarea
              value={negPrompt}
              onChange={e => setNegPrompt(e.target.value)}
              placeholder="lowres, bad anatomy, ..."
              rows={2}
            />
          </label>
        </section>

        <section className="selection-section">
          <h2>Image-to-Image</h2>
          <label className="selection-i2i-toggle">
            <input
              type="checkbox"
              checked={i2iEnabled}
              onChange={e => {
                setI2iEnabled(e.target.checked)
                if (!e.target.checked) setI2iImage(null)
              }}
            />
            参照画像から変換する
          </label>
          {i2iEnabled && (
            <div className="selection-i2i-controls">
              <input
                type="file"
                accept="image/*"
                onChange={e => {
                  const f = e.target.files?.[0]
                  if (f) void handleI2iFile(f)
                  e.target.value = ''
                }}
              />
              {i2iImage && <img className="selection-i2i-thumb" src={i2iImage} alt="i2i参照画像" />}
              <label className="selection-settings-row">
                Strength
                <input
                  type="range" min={0.01} max={0.99} step={0.01}
                  value={i2iStrength}
                  onChange={e => setI2iStrength(Number(e.target.value))}
                />
                <span>{i2iStrength.toFixed(2)}</span>
              </label>
              <label className="selection-settings-row">
                Noise
                <input
                  type="range" min={0} max={0.99} step={0.01}
                  value={i2iNoise}
                  onChange={e => setI2iNoise(Number(e.target.value))}
                />
                <span>{i2iNoise.toFixed(2)}</span>
              </label>
            </div>
          )}
        </section>

        <section className="selection-section">
          <h2>キャラクター参照画像(Character Reference)</h2>
          <p className="selection-section-note">同じキャラを一貫して生成したい時に、人物画像を参照として渡します。</p>
          {characterRefs.map((c, i) => (
            <div key={i} className="selection-charref-item">
              <input
                type="file"
                accept="image/*"
                onChange={async e => {
                  const f = e.target.files?.[0]
                  if (f) updateCharacterRef(i, { image: await readFileAsDataUrl(f) })
                  e.target.value = ''
                }}
              />
              {c.image && <img className="selection-i2i-thumb" src={c.image} alt={`キャラ参照 ${i + 1}`} />}
              <select value={c.type} onChange={e => updateCharacterRef(i, { type: e.target.value as CharacterReferenceEntry['type'] })}>
                {CHARACTER_REFERENCE_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
              <label className="selection-settings-row">
                Fidelity
                <input
                  type="range" min={0} max={1} step={0.01}
                  value={c.fidelity}
                  onChange={e => updateCharacterRef(i, { fidelity: Number(e.target.value) })}
                />
                <span>{c.fidelity.toFixed(2)}</span>
              </label>
              <label className="selection-settings-row">
                Strength
                <input
                  type="range" min={0} max={1} step={0.01}
                  value={c.strength}
                  onChange={e => updateCharacterRef(i, { strength: Number(e.target.value) })}
                />
                <span>{c.strength.toFixed(2)}</span>
              </label>
              <button type="button" onClick={() => removeCharacterRef(i)}>削除</button>
            </div>
          ))}
          <button type="button" onClick={addCharacterRef}>+ 参照画像を追加</button>
        </section>

        <section className="selection-section">
          <h2>複数キャラのテキストプロンプト</h2>
          <p className="selection-section-note">画像なしで、キャラごとにプロンプトと配置位置(x, y: 0〜1)を指定して同時に生成します。</p>
          {characters.map((c, i) => (
            <div key={i} className="selection-character-item">
              <label className="selection-settings-checkbox">
                <input
                  type="checkbox"
                  checked={c.enabled}
                  onChange={e => updateCharacter(i, { enabled: e.target.checked })}
                />
                有効
              </label>
              <textarea
                placeholder="キャラのプロンプト"
                value={c.prompt}
                onChange={e => updateCharacter(i, { prompt: e.target.value })}
                rows={2}
              />
              <textarea
                placeholder="キャラのネガティブプロンプト"
                value={c.negativePrompt}
                onChange={e => updateCharacter(i, { negativePrompt: e.target.value })}
                rows={1}
              />
              <div className="selection-settings-grid">
                <label>
                  X (0〜1)
                  <input
                    type="number" min={0} max={1} step={0.01}
                    value={c.x}
                    onChange={e => updateCharacter(i, { x: Number(e.target.value) })}
                  />
                </label>
                <label>
                  Y (0〜1)
                  <input
                    type="number" min={0} max={1} step={0.01}
                    value={c.y}
                    onChange={e => updateCharacter(i, { y: Number(e.target.value) })}
                  />
                </label>
              </div>
              <button type="button" onClick={() => removeCharacter(i)}>削除</button>
            </div>
          ))}
          <button type="button" onClick={addCharacter}>+ キャラを追加</button>
        </section>

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
                    <DownloadableImage key={i} base64={b64} alt={`生成結果 ${i + 1}`} />
                  ))}
                </div>
              )}
            </section>
          </>
        )}

        <section className="selection-section">
          <h2 onClick={() => setShowHistory(v => !v)} className="selection-history-toggle">
            生成履歴({history.length}件) {showHistory ? '▲' : '▼'}
          </h2>
          {showHistory && (
            <ul className="selection-history-list">
              {history.map(h => (
                <li key={h.id} className="selection-history-item">
                  <div className="selection-history-images">
                    {h.images.map((b64, i) => (
                      <DownloadableImage
                        key={i}
                        base64={b64}
                        alt={`履歴 ${h.id}-${i + 1}`}
                        date={new Date(h.created_at)}
                      />
                    ))}
                  </div>
                  <div className="selection-history-meta">
                    <p className="selection-history-prompt">{h.prompt}</p>
                    <p className="selection-history-info">
                      {h.model} / {h.size} / seed={h.seed ?? 'random'} / チャンク{h.chunk_ids.length}件 /{' '}
                      {new Date(h.created_at).toLocaleString('ja-JP')}
                    </p>
                    {h.i2i_image && (
                      <p className="selection-history-info">
                        i2i: strength={h.i2i_strength?.toFixed(2)} noise={h.i2i_noise?.toFixed(2)}
                        <img
                          className="selection-history-ref-thumb"
                          src={`data:image/png;base64,${h.i2i_image}`}
                          alt="i2i参照画像"
                        />
                      </p>
                    )}
                    {h.character_references.length > 0 && (
                      <p className="selection-history-info">
                        キャラ参照{h.character_references.length}件
                        {h.character_references.map((cr, i) => cr.image && (
                          <img
                            key={i}
                            className="selection-history-ref-thumb"
                            src={`data:image/png;base64,${cr.image}`}
                            alt={`キャラ参照 ${i + 1}`}
                          />
                        ))}
                      </p>
                    )}
                    {h.characters.length > 0 && (
                      <p className="selection-history-info">
                        複数キャラ: {h.characters.map(c => c.prompt).join(' / ')}
                      </p>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  )
}
