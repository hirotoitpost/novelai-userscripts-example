import { useEffect, useState } from 'react'

/**
 * 素体の 3D に服を着せる欄(Model3D.tsx の中)。服の種類(スカート・上の服)と型を選び、数値を変えて「着せる」と、手元の
 * Blender で布として着せて骨に付ける(scripts/blender/garment_skirt.py・garment_top.py・dress_model.py。1 着 1〜2 分)。
 * 結果は着せた姿の静止画・動きの GIF と model_dressed.glb(3D の欄で「服を着た姿」を選ぶと回して見られる)。1 度に 1 着。
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
  seam_joined?: number
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

// 数値をまとめて出す(どの値がどこに効くか分かるように。その服に無い値の組は出さない)
const GROUPS: { label: string; keys: string[] }[] = [
  { label: '形', keys: ['length', 'flare', 'ease', 'bridge', 'bending'] },
  { label: 'ひだ', keys: ['pleats', 'pleat_depth', 'pleat_stitch', 'knife'] },
  { label: 'タック', keys: ['tucks', 'tuck_depth', 'tuck_stitch'] },
  { label: 'マチ', keys: ['gussets', 'gusset_width', 'gusset_height'] },
  { label: '袖', keys: ['sleeve', 'sleeve_ease', 'sleeve_flare'] },
  { label: '襟ぐり', keys: ['neck_width', 'neck_front', 'neck_back'] },
]

function format(value: Value, field: GarmentField): string {
  if (value === null) return field.hint || '—'
  if (typeof value === 'boolean') return value ? 'する' : 'しない'
  return `${field.kind === 'float' && field.step < 1 ? value.toFixed(2) : value}${field.unit}`
}

export default function Garments({ options, motions, dressed, busy, files, fileUrl, onDress }: Props) {
  const kindKeys = Object.keys(options)
  const [kindKey, setKindKey] = useState(kindKeys[0] ?? '')
  const kind = options[kindKey]
  const [preset, setPreset] = useState('')
  const [config, setConfig] = useState<Record<string, Value>>({})
  const [motion, setMotion] = useState('walk')

  function choose(nextKind: string, key: string) {
    const target = options[nextKind]
    if (!target) return
    setKindKey(nextKind)
    setPreset(key)
    const defaults = Object.fromEntries(Object.entries(target.fields).map(([k, f]) => [k, f.default]))
    setConfig({ ...defaults, ...target.presets[key]?.config })
  }
  // 初めは、最後に着せた服の型と数値(無ければ最初の服の最初の型)
  useEffect(() => {
    const last = dressed?.items[0]
    const target = last && options[last.kind]
    if (last && target) {
      setKindKey(last.kind)
      setPreset(last.preset ?? '')
      setConfig(Object.fromEntries(Object.keys(target.fields).map(k => [k, last.config[k] ?? target.fields[k].default])))
    } else if (kindKeys[0]) {
      choose(kindKeys[0], Object.keys(options[kindKeys[0]].presets)[0] ?? '')
    }
  }, [kindKeys.length, dressed?.dressed])

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
        服の型を選び(数値は変えられます)、「着せる」を押すと、手元の Blender で体の寸法を測って型紙を作り、布として着せて
        骨に付けます(1 着 1〜2 分。GPU・Anlas は使いません)。上の服は、腕を上げて着せてから下ろします。1 度に 1 着です
        (着せ直すと、前の服は脱ぎます)。骨を入れ直すと、着せた結果は消えます。
      </p>
      <div className="fsh-actions">
        <select className="lora-select" value={`${kindKey}/${preset}`} disabled={busy} aria-label="服の型"
          onChange={e => choose(...(e.target.value.split('/') as [string, string]))}>
          {!preset && <option value={`${kindKey}/`}>{kind.label}: (数値を決めて着せたもの)</option>}
          {kindKeys.map(k => (
            <optgroup key={k} label={options[k].label}>
              {Object.entries(options[k].presets).map(([key, p]) => (
                <option key={key} value={`${k}/${key}`}>{options[k].label}: {p.label}</option>
              ))}
            </optgroup>
          ))}
        </select>
        <select className="lora-select" value={motion} disabled={busy} aria-label="着せたあとの確かめの動き"
          onChange={e => setMotion(e.target.value)}>
          {Object.entries(motions).map(([key, label]) => <option key={key} value={key}>確かめ: {label}</option>)}
        </select>
        <button type="button" className="lora-btn" disabled={busy || knifeWithoutPleats}
          onClick={() => onDress({ motion, garments: [{ kind: kindKey, preset: preset || null, config }] })}>
          {files['model_dressed.glb'] ? '着せ直す' : '着せる'}
        </button>
        {edited && <span className="lora-hint">(数値を変えています)</span>}
      </div>
      <details className="m3d-dress-fields">
        <summary>数値を変える</summary>
        {GROUPS.filter(group => group.keys.some(key => kind.fields[key])).map(group => (
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
            着せたもの: {options[item.kind]?.label ?? item.kind}
            {item.preset && options[item.kind]?.presets[item.preset] ? `・${options[item.kind].presets[item.preset].label}` : ''}
            (確かめ: {motions[dressed!.motion] ?? dressed!.motion})
            {item.inside_body ? ` ⚠ 体に食い込んだ点 ${item.inside_body}` : ' ✓ 体への食い込みなし'}
          </li>
          {item.hip_cm != null && (
            <li className="lora-hint">
              腰回り {item.hip_cm}cm・型紙のウエスト {item.pattern_waist_cm}cm・裾 {item.pattern_hem_cm}cm
              {item.seam_gap_mm != null && `・縫い目のすき間 ${item.seam_gap_mm}mm`}
            </li>
          )}
          {item.seam_joined != null && item.seam_joined > 0 && (
            <li className="lora-hint">袖ぐりで身頃と袖をつないだ点 {item.seam_joined}</li>
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
