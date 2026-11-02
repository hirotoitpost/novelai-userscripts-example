import { useEffect, useState } from 'react'

/**
 * 素体の 3D に服を着せる欄(Model3D.tsx の中)。型紙の型を選び、数値を変えて「着せる」と、手元の Blender で布として着せて
 * 骨に付ける(scripts/blender/garment_skirt.py・dress_model.py。1 着 1〜2 分)。結果は着せた姿の静止画・動きの GIF と
 * model_dressed.glb(3D の欄で「服を着た姿」を選ぶと回して見られる)。今はスカートだけ。
 */

type Value = number | boolean | null

export interface GarmentField {
  label: string
  kind: 'float' | 'int' | 'bool'
  default: Value
  min: number
  max: number
  step: number
  unit: string
  optional: boolean
  hint: string
}

export interface GarmentKind {
  label: string
  presets: Record<string, { label: string; config: Record<string, Value> }>
  fields: Record<string, GarmentField>
}

export interface DressedItem {
  name: string
  kind: string
  preset?: string | null
  config: Record<string, Value>
  hip_cm?: number
  pattern_waist_cm?: number
  pattern_hem_cm?: number
  inside_body?: number
  seam_gap_mm?: number
  bones?: Record<string, number>
}

export interface DressedMeta { motion: string; items: DressedItem[]; dressed: number }

interface Props {
  options: Record<string, GarmentKind>
  motions: Record<string, string>
  dressed: DressedMeta | null
  busy: boolean
  files: Record<string, { label: string; updated: number }>
  fileUrl: (name: string) => string
  onDress: (body: Record<string, unknown>) => void
}

// 数値をまとめて出す(どの値がどこに効くか分かるように)
const GROUPS: { label: string; keys: string[] }[] = [
  { label: '形', keys: ['length', 'flare', 'ease'] },
  { label: 'ひだ', keys: ['pleats', 'pleat_depth', 'pleat_stitch', 'knife'] },
  { label: 'タック', keys: ['tucks', 'tuck_depth', 'tuck_stitch'] },
  { label: 'マチ', keys: ['gussets', 'gusset_width', 'gusset_height'] },
]

const KIND = 'skirt'

function format(value: Value, field: GarmentField): string {
  if (value === null) return field.hint || '—'
  if (typeof value === 'boolean') return value ? 'する' : 'しない'
  return `${field.kind === 'float' && field.step < 1 ? value.toFixed(2) : value}${field.unit}`
}

export default function Garments({ options, motions, dressed, busy, files, fileUrl, onDress }: Props) {
  const kind = options[KIND]
  const presetKeys = Object.keys(kind?.presets ?? {})
  const [preset, setPreset] = useState(presetKeys[0] ?? '')
  const [config, setConfig] = useState<Record<string, Value>>({})
  const [motion, setMotion] = useState('walk')

  function choose(key: string) {
    setPreset(key)
    if (!kind) return
    const defaults = Object.fromEntries(Object.entries(kind.fields).map(([k, f]) => [k, f.default]))
    setConfig({ ...defaults, ...kind.presets[key]?.config })
  }
  // 初めは、最後に着せた型と数値(無ければ最初の型)
  useEffect(() => {
    const last = dressed?.items.find(item => item.kind === KIND)
    if (last && kind) {
      setPreset(last.preset ?? '')
      setConfig(Object.fromEntries(Object.keys(kind.fields).map(k => [k, last.config[k] ?? kind.fields[k].default])))
    } else if (presetKeys[0]) {
      choose(presetKeys[0])
    }
  }, [!!kind, dressed?.dressed])

  if (!kind) return null
  const edited = !!preset && Object.entries(kind.presets[preset]?.config ?? {}).some(([k, v]) => config[k] !== v)
    || !!preset && Object.entries(kind.fields).some(([k, f]) => !(k in (kind.presets[preset]?.config ?? {})) && config[k] !== f.default)
  const knifeWithoutPleats = config.knife === true && !config.pleats
  const stills = (['dressed_front.png', 'dressed_side.png', 'dressed_back.png'] as const).filter(name => files[name])
  const item = dressed?.items[0]

  function set(key: string, value: Value) {
    setConfig(prev => ({ ...prev, [key]: value }))
  }

  return (
    <div className="m3d-dress">
      <h5>服を着せる(型紙から)</h5>
      <p className="lora-hint">
        型紙の型を選び(数値は変えられます)、「着せる」を押すと、手元の Blender で体の寸法を測って型紙を作り、布として着せて
        骨に付けます(1 着 1〜2 分。GPU・Anlas は使いません)。今はスカートだけです。骨を入れ直すと、着せた結果は消えます。
      </p>
      <div className="fsh-actions">
        <select className="lora-select" value={preset} disabled={busy} aria-label="服の型" onChange={e => choose(e.target.value)}>
          {presetKeys.map(key => <option key={key} value={key}>{kind.label}: {kind.presets[key].label}</option>)}
        </select>
        <select className="lora-select" value={motion} disabled={busy} aria-label="着せたあとの確かめの動き"
          onChange={e => setMotion(e.target.value)}>
          {Object.entries(motions).map(([key, label]) => <option key={key} value={key}>確かめ: {label}</option>)}
        </select>
        <button type="button" className="lora-btn" disabled={busy || knifeWithoutPleats}
          onClick={() => onDress({ motion, garments: [{ kind: KIND, preset: preset || null, config }] })}>
          {files['model_dressed.glb'] ? '着せ直す' : '着せる'}
        </button>
        {edited && <span className="lora-hint">(数値を変えています)</span>}
      </div>
      <details className="m3d-dress-fields">
        <summary>数値を変える</summary>
        {GROUPS.map(group => (
          <fieldset key={group.label} className="m3d-dress-group">
            <legend>{group.label}</legend>
            {group.keys.filter(key => kind.fields[key]).map(key => {
              const field = kind.fields[key]
              const value = config[key] ?? null
              return (
                <label key={key} className="m3d-dress-row" title={field.hint}>
                  <span className="m3d-shape-label">{field.label}</span>
                  {field.kind === 'bool' ? (
                    <input type="checkbox" checked={value === true} disabled={busy} onChange={e => set(key, e.target.checked)} />
                  ) : (
                    <>
                      {field.optional && (
                        <input type="checkbox" checked={value !== null} disabled={busy} aria-label={`${field.label}を決める`}
                          onChange={e => set(key, e.target.checked ? field.min : null)} />
                      )}
                      <input type="range" min={field.min} max={field.max} step={field.step} disabled={busy || value === null}
                        value={typeof value === 'number' ? value : field.min} onChange={e => set(key, Number(e.target.value))} />
                    </>
                  )}
                  <span className="lora-hint">{format(value, field)}{field.hint && value !== null ? `(${field.hint})` : ''}</span>
                </label>
              )
            })}
          </fieldset>
        ))}
        {knifeWithoutPleats && <p className="lora-error">車ひだには、ひだの数が要ります。</p>}
      </details>

      {item && (
        <ul className="bdb-quality">
          <li className={item.inside_body ? 'bdb-auto--ng' : 'bdb-auto--ok'}>
            着せたもの: {item.preset && kind.presets[item.preset] ? kind.presets[item.preset].label : kind.label}
            (確かめ: {motions[dressed!.motion] ?? dressed!.motion})
            {item.inside_body ? ` ⚠ 体に食い込んだ点 ${item.inside_body}` : ' ✓ 体への食い込みなし'}
          </li>
          {item.hip_cm != null && (
            <li className="lora-hint">
              腰回り {item.hip_cm}cm・型紙のウエスト {item.pattern_waist_cm}cm・裾 {item.pattern_hem_cm}cm
              {item.seam_gap_mm != null && `・縫い目のすき間 ${item.seam_gap_mm}mm`}
            </li>
          )}
        </ul>
      )}
      {(stills.length > 0 || files['dressed_motion.gif']) && (
        <div className="bdb-rig-views m3d-stills">
          {stills.map(name => (
            <figure key={name}><img src={fileUrl(name)} alt={files[name].label} /><figcaption>{files[name].label}</figcaption></figure>
          ))}
          {files['dressed_motion.gif'] && (
            <figure><img src={fileUrl('dressed_motion.gif')} alt="着せた動き" /><figcaption>{files['dressed_motion.gif'].label}</figcaption></figure>
          )}
        </div>
      )}
      {files['model_dressed.glb'] && (
        <div className="fsh-actions">
          <a className="lora-btn lora-btn--secondary" href={fileUrl('model_dressed.glb')} download
            title="服を着た、骨・動きの入った GLB">服を着た GLB</a>
          {files['model_dressed.blend'] && (
            <a className="lora-btn lora-btn--secondary" href={fileUrl('model_dressed.blend')} download
              title="服を着た Blender のファイル">服を着た .blend</a>
          )}
        </div>
      )}
    </div>
  )
}
