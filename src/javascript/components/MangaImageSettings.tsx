import { useEffect, useState } from 'react'

export interface MangaImageSettingsValue {
  model: string
  width: number
  height: number
  steps: number
  scale: number
  sampler: string
  noise_schedule: string
  cfg_rescale: number
  negative_prompt: string | null
  /** null ならページごとに別のシード(従来動作)、数値なら全ページ固定。 */
  seed: number | null
  /** V5 の complexity タグ。公式は通常の画像に high を推奨。 */
  complexity: Complexity
}

type Complexity = 'low' | 'medium' | 'high' | 'ultra'

interface ImagePreset {
  id: number
  name: string
  settings: MangaImageSettingsValue
  created_at: string
}

export const DEFAULT_MANGA_IMAGE_SETTINGS: MangaImageSettingsValue = {
  model: 'nai-diffusion-5-full',
  width: 1216,
  height: 1728,
  steps: 28,
  scale: 7.0,
  sampler: 'k_euler_ancestral',
  noise_schedule: 'karras',
  cfg_rescale: 0.0,
  negative_prompt: null,
  seed: null,
  complexity: 'high',
}

const COMPLEXITIES: Complexity[] = ['low', 'medium', 'high', 'ultra']

const MODELS = ['nai-diffusion-5-full', 'nai-diffusion-4-5-full', 'nai-diffusion-4-5-curated']
const SAMPLERS = [
  'k_euler',
  'k_euler_ancestral',
  'k_dpm_2',
  'k_dpm_2_ancestral',
  'k_dpmpp_2m',
  'k_dpmpp_2s_ancestral',
  'k_dpmpp_sde',
  'ddim',
]
const NOISE_SCHEDULES = ['karras', 'exponential', 'polyexponential']

interface Props {
  apiOrigin: string
  value: MangaImageSettingsValue
  onChange: (value: MangaImageSettingsValue) => void
  disabled?: boolean
}

export default function MangaImageSettings({ apiOrigin, value, onChange, disabled }: Props) {
  const [presets, setPresets] = useState<ImagePreset[]>([])
  const [presetName, setPresetName] = useState('')
  const [error, setError] = useState<string | null>(null)

  function loadPresets() {
    fetch(`${apiOrigin}/api/image/presets`)
      .then(r => (r.ok ? r.json() : []))
      .then(setPresets)
      .catch(() => {/* サイレント失敗 */})
  }

  useEffect(loadPresets, [apiOrigin])

  function set<K extends keyof MangaImageSettingsValue>(key: K, v: MangaImageSettingsValue[K]) {
    onChange({ ...value, [key]: v })
  }

  async function savePreset() {
    const name = presetName.trim()
    if (!name) return
    setError(null)
    try {
      const res = await fetch(`${apiOrigin}/api/image/presets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, settings: value }),
      })
      if (!res.ok) throw new Error((await res.json()).detail ?? '保存に失敗しました')
      setPresetName('')
      loadPresets()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function removePreset(id: number) {
    setError(null)
    try {
      await fetch(`${apiOrigin}/api/image/presets/${id}`, { method: 'DELETE' })
      loadPresets()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="story-settings">
      <div className="story-row">
        <label>
          プリセット
          <select
            value=""
            disabled={disabled}
            onChange={e => {
              const preset = presets.find(p => String(p.id) === e.target.value)
              if (preset) {
                // complexity 追加前に保存したプリセットには欠けているので既定値で補う。
                onChange({ ...DEFAULT_MANGA_IMAGE_SETTINGS, ...preset.settings })
                setPresetName(preset.name)
              }
            }}
          >
            <option value="">選択して読み込み...</option>
            {presets.map(p => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </label>
        <label>
          プリセット名で保存(同名は上書き)
          <input
            type="text"
            placeholder="例: 縦長モノクロ"
            value={presetName}
            disabled={disabled}
            onChange={e => setPresetName(e.target.value)}
          />
        </label>
      </div>
      <div className="story-actions">
        <button type="button" onClick={savePreset} disabled={disabled || !presetName.trim()}>
          プリセットを保存
        </button>
        <button
          type="button"
          className="story-secondary"
          onClick={() => onChange(DEFAULT_MANGA_IMAGE_SETTINGS)}
          disabled={disabled}
        >
          既定値に戻す
        </button>
      </div>
      {presets.length > 0 && (
        <div className="story-preset-chips">
          {presets.map(p => (
            <span key={p.id} className="story-preset-chip">
              {p.name}
              <button type="button" onClick={() => removePreset(p.id)} disabled={disabled}>×</button>
            </span>
          ))}
        </div>
      )}
      {error && <div className="story-error">{error}</div>}

      <div className="story-row">
        <label>
          モデル
          <select value={value.model} disabled={disabled} onChange={e => set('model', e.target.value)}>
            {MODELS.map(m => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </label>
        <label>
          サンプラー
          <select value={value.sampler} disabled={disabled} onChange={e => set('sampler', e.target.value)}>
            {SAMPLERS.map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
        <label>
          ノイズスケジュール
          <select
            value={value.noise_schedule}
            disabled={disabled}
            onChange={e => set('noise_schedule', e.target.value)}
          >
            {NOISE_SCHEDULES.map(n => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </label>
        <label>
          複雑さ(V5)
          <select
            value={value.complexity}
            disabled={disabled}
            onChange={e => set('complexity', e.target.value as Complexity)}
          >
            {COMPLEXITIES.map(c => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </label>
      </div>

      <div className="story-row">
        <label>
          幅
          <input
            type="number"
            min={512}
            max={2048}
            step={64}
            value={value.width}
            disabled={disabled}
            onChange={e => set('width', Number(e.target.value))}
          />
        </label>
        <label>
          高さ
          <input
            type="number"
            min={512}
            max={2048}
            step={64}
            value={value.height}
            disabled={disabled}
            onChange={e => set('height', Number(e.target.value))}
          />
        </label>
        <label>
          ステップ
          <input
            type="number"
            min={1}
            max={50}
            value={value.steps}
            disabled={disabled}
            onChange={e => set('steps', Number(e.target.value))}
          />
        </label>
      </div>

      <div className="story-row">
        <label>
          スケール(CFG)
          <input
            type="number"
            min={0}
            max={10}
            step={0.1}
            value={value.scale}
            disabled={disabled}
            onChange={e => set('scale', Number(e.target.value))}
          />
        </label>
        <label>
          CFGリスケール
          <input
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={value.cfg_rescale}
            disabled={disabled}
            onChange={e => set('cfg_rescale', Number(e.target.value))}
          />
        </label>
        <label>
          シード(空欄ならページごとに変える)
          <input
            type="number"
            min={0}
            max={4294967295}
            placeholder="自動"
            value={value.seed ?? ''}
            disabled={disabled}
            onChange={e => set('seed', e.target.value === '' ? null : Number(e.target.value))}
          />
        </label>
      </div>

      <label className="story-settings-negative">
        ネガティブプロンプト(空欄なら既定値)
        <textarea
          className="story-premise"
          rows={2}
          placeholder="lowres, bad quality, ..."
          value={value.negative_prompt ?? ''}
          disabled={disabled}
          onChange={e => set('negative_prompt', e.target.value === '' ? null : e.target.value)}
        />
      </label>
    </div>
  )
}
