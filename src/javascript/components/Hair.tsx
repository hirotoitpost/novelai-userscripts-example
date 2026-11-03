import { useEffect, useState } from 'react'
import type { GarmentField } from './Garments'

/**
 * スキンヘッドのモデルに、型から作った髪を付ける欄(Model3D.tsx の中)。型を選び、数値を変えて「髪を付ける」と、手元の
 * Blender で頭に沿って髪(頭を覆う髪・お団子・前髪・顔の横の房・後れ毛)を作り、房とお団子に鎖の骨を入れて動きごとに
 * 揺れを焼き込む(scripts/blender/hair_kit.py。15 秒ほど)。結果は頭のまわりの絵・揺れの GIF と bald/model_rigged.glb
 * (3D の欄で「スキンヘッド+髪」を選ぶと回して見られる)。下ろした髪は、重力で垂らして肩・背中に沿わせる。
 */

type Value = number

export interface HairOptions {
  presets: Record<string, { label: string; config: Record<string, Value> }>
  fields: Record<string, GarmentField>
}

export interface HairMeta {
  preset: string | null
  config: Record<string, Value>
  motion: string
  chains?: number
  sway_mm?: Record<string, number>
}

interface Props {
  options: HairOptions
  motions: Record<string, string>
  hair: HairMeta | null
  busy: boolean
  files: Record<string, { label: string; updated: number }>
  fileUrl: (name: string) => string
  onMake: (body: Record<string, unknown>) => void
}

// 数値をまとめて出す(どの値がどこに効くか分かるように)
const GROUPS: { label: string; keys: string[] }[] = [
  { label: '頭', keys: ['volume', 'hairline'] },
  { label: '前髪', keys: ['bangs', 'bangs_length', 'bangs_spread', 'bangs_part'] },
  { label: '顔の横・後れ毛', keys: ['side_locks', 'side_length', 'wisps', 'nape_wisps'] },
  { label: 'お団子', keys: ['buns', 'bun_size', 'bun_height', 'bun_wisps'] },
  { label: '下ろした後ろ髪', keys: ['back_length', 'back_locks'] },
]

// 揺れの大きさを出す部品の名前
const KIND_LABELS: Record<string, string> = {
  bang: '前髪', side: '顔の横の房', wisp: '耳の前の後れ毛', nape: 'うなじの後れ毛', bun: 'お団子', back: '後ろ髪',
}

const STILLS = ['bald/hair_front.png', 'bald/hair_side.png', 'bald/hair_back.png', 'bald/hair_motion.gif']

function fromPreset(options: HairOptions, preset: string): Record<string, Value> {
  const defaults = Object.fromEntries(Object.entries(options.fields).map(([k, f]) => [k, f.default as Value]))
  return { ...defaults, ...options.presets[preset]?.config }
}

function format(value: Value, field: GarmentField): string {
  return `${field.kind === 'float' && field.step < 1 ? value.toFixed(2) : value}${field.unit}`
}

export default function Hair({ options, motions, hair, busy, files, fileUrl, onMake }: Props) {
  const first = Object.keys(options.presets)[0] ?? ''
  const [preset, setPreset] = useState(first)
  const [config, setConfig] = useState<Record<string, Value>>(() => fromPreset(options, first))
  const [motion, setMotion] = useState('jump')

  // 初めは、最後に付けた髪の型と数値
  useEffect(() => {
    if (!hair) return
    setPreset(hair.preset ?? '')
    setConfig(Object.fromEntries(Object.entries(options.fields).map(([k, f]) => [k, hair.config[k] ?? (f.default as Value)])))
    setMotion(hair.motion)
  }, [hair?.preset, JSON.stringify(hair?.config)])

  const edited = !!preset && Object.entries(options.fields).some(([k, f]) => {
    const base = options.presets[preset]?.config ?? {}
    return config[k] !== (k in base ? base[k] : f.default)
  })
  const stills = STILLS.filter(name => files[name])

  return (
    <div className="m3d-dress">
      <h5>髪を付ける(型から)</h5>
      <p className="lora-hint">
        型を選び(数値は変えられます)、「髪を付ける」を押すと、手元の Blender でスキンヘッドの頭に沿って髪を作り、房と
        お団子に骨を入れて、動きに合わせて揺らします(15〜30 秒。GPU・Anlas は使いません)。下ろした髪は、重力で垂らして
        肩・背中に沿わせます。
      </p>
      <div className="fsh-actions">
        <select className="lora-select" value={preset || '*'} disabled={busy} aria-label="髪の型"
          onChange={e => { if (e.target.value !== '*') { setPreset(e.target.value); setConfig(fromPreset(options, e.target.value)) } }}>
          {!preset && <option value="*">(数値を決めて付けたもの)</option>}
          {Object.entries(options.presets).map(([key, p]) => <option key={key} value={key}>{p.label}</option>)}
        </select>
        {edited && <span className="lora-hint">(数値を変えています)</span>}
      </div>
      <details className="m3d-dress-fields">
        <summary>髪の数値を変える</summary>
        {GROUPS.map(group => (
          <fieldset key={group.label} className="m3d-dress-group">
            <legend>{group.label}</legend>
            {group.keys.filter(key => options.fields[key]).map(key => {
              const field = options.fields[key]
              const value = config[key] ?? (field.default as Value)
              return (
                <label key={key} className="m3d-dress-row" title={field.hint}>
                  <span className="m3d-shape-label">{field.label}</span>
                  <input type="range" min={field.min} max={field.max} step={field.step} disabled={busy} value={value}
                    onChange={e => setConfig(prev => ({ ...prev, [key]: Number(e.target.value) }))} />
                  <span className="lora-hint">{format(value, field)}{field.hint ? `(${field.hint})` : ''}</span>
                </label>
              )
            })}
          </fieldset>
        ))}
      </details>
      <div className="fsh-actions">
        <select className="lora-select" value={motion} disabled={busy} aria-label="髪を付けたあとの確かめの動き"
          onChange={e => setMotion(e.target.value)}>
          {Object.entries(motions).map(([key, label]) => <option key={key} value={key}>確かめ: {label}</option>)}
        </select>
        <button type="button" className="lora-btn" disabled={busy}
          onClick={() => onMake({ motion, preset: preset || null, config })}>
          {hair ? '髪を付け直す' : '髪を付ける'}
        </button>
      </div>
      {hair?.sway_mm && (
        <p className="lora-hint">
          揺れの大きさ(鎖の先が頭から離れたいちばん大きな距離):{' '}
          {Object.entries(hair.sway_mm).map(([k, v]) => `${KIND_LABELS[k] ?? k} ${v}mm`).join('・')}
        </p>
      )}
      {stills.length > 0 && (
        <div className="bdb-rig-views m3d-stills">
          {stills.map(name => (
            <figure key={name}><img src={fileUrl(name)} alt={files[name].label} /><figcaption>{files[name].label}</figcaption></figure>
          ))}
        </div>
      )}
    </div>
  )
}
