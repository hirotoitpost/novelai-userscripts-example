import { useEffect, useState } from 'react'

/**
 * 素体の 3D モデルの形状パーツ(docs/model_3d_shapes_plan_jp.md)。部位ごとに形を選び、強さをスライダーで変える(3D を
 * 回して見ながら、保存する前に確かめられる)。形の値(広さ・高さ・陥没の唇の幅 など)を変えた「自分の形」を作り、
 * 共有の一覧(どのモデルにも当てられる)に保存する。自分の形は、骨を入れ直すとモデルに入る。形と強さの組み合わせに
 * 名前を付けて、場面ごとに残す(ポーズの絵で選ぶ)。
 */

export interface ShapeInfo {
  /** 選べる形(部位 → 名前と形) */
  options: Record<string, { label: string; presets: Record<string, string> }>
  /** 保存した強さ(形の名前 → 0〜2) */
  weights: Record<string, number>
  /** 場面ごとの組み合わせ(名前 → 強さ) */
  sets: Record<string, Record<string, number>>
}

interface Catalog {
  presets: { key: string; part: string; label: string; tags: string[]; params: Record<string, number | string>; builtin: boolean; id: number | null }[]
  params: Record<string, { label: string; min: number; max: number; step: number }>
  op_params: Record<string, string[]>
}

interface Props {
  url: string
  rootName: string
  baseId: number
  shapes: ShapeInfo
  busy: boolean
  /** 編集中の強さ(null なら保存した強さ)。3D の表示にも使う */
  draft: Record<string, number> | null
  onDraft: (draft: Record<string, number> | null) => void
  /** サーバーが返した 3D の状態で、画面を新しくする */
  onInfo: (info: unknown) => void
  /** 骨を入れ直す(自分の形をモデルに入れる) */
  onRig: () => Promise<void>
}

async function detail(res: Response): Promise<string> {
  const data = await res.json().catch(() => null)
  return typeof data?.detail === 'string' ? data.detail : `HTTP ${res.status}`
}

const PRESET_URL = '/api/lora-dataset/body-shape-presets'

export default function ShapeParts({ url, rootName, baseId, shapes, busy, draft, onDraft, onInfo, onRig }: Props) {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [editing, setEditing] = useState<{ part: string; base: string; params: Record<string, number | string>; label: string; tags: string } | null>(null)
  const [setName, setSetName] = useState('')
  const [status, setStatus] = useState<string | null>(null)

  function loadCatalog() {
    fetch(PRESET_URL).then(r => (r.ok ? r.json() : null)).then(setCatalog).catch(() => setCatalog(null))
  }
  useEffect(loadCatalog, [])

  const weights = draft ?? shapes.weights
  // 選べない形(消した自分の形など)の強さは送らない
  const known = new Set(Object.values(shapes.options).flatMap(spec => Object.keys(spec.presets)))
  const clean = (w: Record<string, number>) => Object.fromEntries(Object.entries(w).filter(([key]) => known.has(key)))

  /** 部位の形を選ぶ(部位の中では 1 つだけ。空なら使わない)。強さは前の形の強さを引き継ぐ */
  function pick(part: string, preset: string) {
    const keys = Object.keys(shapes.options[part]?.presets ?? {})
    const before = keys.map(key => weights[key] ?? 0).find(w => w > 0) ?? 1
    const next = Object.fromEntries(Object.entries(weights).filter(([key]) => !keys.includes(key)))
    if (preset) next[preset] = before
    onDraft(next)
    setEditing(null)
  }

  function strength(key: string, value: number) {
    onDraft({ ...weights, [key]: value })
  }

  async function send(path: string, init: RequestInit, done: string): Promise<boolean> {
    setStatus(null)
    const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...init })
    if (!res.ok) {
      setStatus(`できませんでした: ${await detail(res)}`)
      return false
    }
    setStatus(done)
    return true
  }

  async function saveWeights(name?: string) {
    const body = { root_name: rootName, base_id: baseId, weights: clean(weights), ...(name ? { set_name: name } : {}) }
    const res = await fetch(`${url}/shapes`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    if (!res.ok) {
      setStatus(`保存できませんでした: ${await detail(res)}`)
      return false
    }
    onInfo(await res.json())
    if (!name) onDraft(null)
    setStatus(name ? `組み合わせ「${name}」を残しました(ポーズの絵で選べます)` : '形状パーツを保存しました(ポーズの絵に使います)')
    return true
  }

  async function deleteSet(name: string) {
    const res = await fetch(`${url}/shapes/sets/${encodeURIComponent(name)}?base_id=${baseId}&root_name=${encodeURIComponent(rootName)}`, { method: 'DELETE' })
    if (res.ok) onInfo(await res.json())
  }

  function startEdit(part: string) {
    const key = Object.keys(shapes.options[part]?.presets ?? {}).find(k => (weights[k] ?? 0) > 0)
      ?? Object.keys(shapes.options[part]?.presets ?? {})[0]
    const preset = catalog?.presets.find(p => p.key === key)
    if (!preset) return
    setEditing({
      part, base: preset.key, params: { ...preset.params },
      label: preset.builtin ? `${preset.label}(自分の形)` : preset.label, tags: preset.builtin ? '' : preset.tags.join(', '),
    })
  }

  async function savePreset(overwrite: boolean) {
    if (!editing) return
    const custom = catalog?.presets.find(p => p.key === editing.base && !p.builtin)
    const body = JSON.stringify({ part: editing.part, label: editing.label, params: editing.params, tags: editing.tags })
    const res = await fetch(overwrite && custom ? `${PRESET_URL}/${custom.id}` : PRESET_URL, {
      method: overwrite && custom ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body,
    })
    if (!res.ok) {
      setStatus(`保存できませんでした: ${await detail(res)}`)
      return
    }
    const saved = await res.json()
    setCatalog(saved)
    // 選べる形の一覧(サーバーが返すモデルの状態)を読み直す
    const info = await fetch(`${url}?root_name=${encodeURIComponent(rootName)}`).then(r => (r.ok ? r.json() : null)).catch(() => null)
    if (info) onInfo(info)
    // 新しい形を、この部位の形として選ぶ(モデルに入れるには骨を入れ直す)
    const keys = Object.keys(shapes.options[editing.part]?.presets ?? {})
    const next = Object.fromEntries(Object.entries(weights).filter(([key]) => !keys.includes(key)))
    next[saved.key] = 1
    onDraft(next)
    setEditing(null)
    setStatus('自分の形を保存しました。「保存して骨を入れ直す」で、このモデルに入ります(20 秒ほど)')
  }

  async function deletePreset(id: number) {
    if (await send(`${PRESET_URL}/${id}`, { method: 'DELETE' }, '自分の形を消しました(骨を入れ直すと、モデルから外れます)')) {
      loadCatalog()
      setEditing(null)
    }
  }

  async function saveAndRig() {
    // 保存した強さは骨を入れ直すときにも使うので、自分の形(まだモデルに無い)も強さに入れて送る
    const body = { root_name: rootName, base_id: baseId, weights: draft ?? shapes.weights }
    const res = await fetch(`${url}/shapes`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    if (!res.ok) {
      setStatus(`保存できませんでした: ${await detail(res)}`)
      return
    }
    onDraft(null)
    await onRig()
  }

  const opParams = (op: string) => (catalog?.op_params[op] ?? []).filter(name => {
    // 陥没のへこみ(socket_*)と横:縦は、もとの形にあるときだけ出す
    if (!editing) return false
    return !(name.startsWith('socket_') || name === 'aspect') || name in editing.params
  })

  return (
    <div className="m3d-poses">
      <h5>形状パーツ</h5>
      <p className="lora-hint">
        部位ごとに形を選び、強さを変えます(素体・キャラシートのタグから選んだものが初めの形)。乳首・乳輪は体の面につなぎ止めた部品で、
        胸の形やポーズに合わせて一緒に動きます。「3D を回して見る」で、保存する前に確かめられます。「形の値」で、広さ・高さ・唇の幅などを
        変えた自分の形を作れます(どのモデルにも使えます。モデルに入れるには骨を入れ直します)。
      </p>
      <div className="m3d-shapes">
        {Object.entries(shapes.options).map(([part, spec]) => {
          const keys = Object.keys(spec.presets)
          const current = keys.find(key => (weights[key] ?? 0) > 0) ?? ''
          return (
            <div key={part} className="m3d-shape-row">
              <span className="m3d-shape-label">{spec.label}</span>
              {part === 'repair' ? (
                <label className="fsh-label">
                  <input type="checkbox" checked={!!current} disabled={busy} onChange={e => pick(part, e.target.checked ? 'repair' : '')} />
                  入れる
                </label>
              ) : (
                <select className="lora-select" value={current} disabled={busy} onChange={e => pick(part, e.target.value)}>
                  <option value="">使わない</option>
                  {keys.map(key => <option key={key} value={key}>{key.startsWith('custom_') ? `★ ${spec.presets[key]}` : spec.presets[key]}</option>)}
                </select>
              )}
              {current && (
                <label className="fsh-label m3d-shape-strength">強さ {(weights[current] ?? 0).toFixed(2)}
                  <input type="range" min={0.05} max={2} step={0.05} value={weights[current] ?? 0} disabled={busy}
                    onChange={e => strength(current, Number(e.target.value))} />
                </label>
              )}
              {part !== 'repair' && catalog && (
                <button type="button" className="lora-btn lora-btn--secondary" disabled={busy} onClick={() => startEdit(part)}>形の値</button>
              )}
              {editing?.part === part && catalog && (
                <div className="m3d-shape-editor">
                  <label className="lora-label">名前
                    <input className="lora-input" value={editing.label} maxLength={40} onChange={e => setEditing({ ...editing, label: e.target.value })} />
                  </label>
                  {opParams(String(editing.params.op)).map(name => {
                    const spec = catalog.params[name]
                    const value = Number(editing.params[name] ?? spec.min)
                    return (
                      <label key={name} className="lora-label">{spec.label} {value.toFixed(3)}
                        <input type="range" min={spec.min} max={spec.max} step={spec.step} value={value}
                          onChange={e => setEditing({ ...editing, params: { ...editing.params, [name]: Number(e.target.value) } })} />
                      </label>
                    )
                  })}
                  <label className="lora-label">この形を表すタグ(任意。素体・キャラシートにあれば、初めの形にします)
                    <input className="lora-input" value={editing.tags} placeholder="puffy areolae など" onChange={e => setEditing({ ...editing, tags: e.target.value })} />
                  </label>
                  <div className="fsh-actions">
                    <button type="button" className="lora-btn" disabled={!editing.label.trim()} onClick={() => void savePreset(false)}>新しい形として保存</button>
                    {catalog.presets.find(p => p.key === editing.base && !p.builtin) && (
                      <>
                        <button type="button" className="lora-btn lora-btn--secondary" onClick={() => void savePreset(true)}>この形を上書き</button>
                        <button type="button" className="lora-btn lora-btn--secondary"
                          onClick={() => void deletePreset(catalog.presets.find(p => p.key === editing.base)!.id!)}>この形を消す</button>
                      </>
                    )}
                    <button type="button" className="lora-btn lora-btn--secondary" onClick={() => setEditing(null)}>やめる</button>
                  </div>
                  <span className="lora-hint">
                    値を変えた形は、骨を入れ直すとモデルに入ります(画面の 3D には、そのあとに出ます)。陥没の唇を広く・高くしすぎると、
                    乳輪の外まで盛り上がり、乳輪のふちが線で見えます(乳輪を広い形にすると目立ちません)。
                  </span>
                </div>
              )}
            </div>
          )
        })}
      </div>
      <div className="fsh-actions">
        <button type="button" className="lora-btn" disabled={busy || !draft} onClick={() => void saveWeights()}>保存する</button>
        <button type="button" className="lora-btn lora-btn--secondary" disabled={busy}
          title="いまの形と強さを保存し、骨を入れ直して、自分の形をモデルに入れます(20 秒ほど)"
          onClick={() => void saveAndRig()}>保存して骨を入れ直す</button>
        {draft && <button type="button" className="lora-btn lora-btn--secondary" onClick={() => onDraft(null)}>元に戻す</button>}
      </div>
      <div className="fsh-actions">
        <span className="lora-hint">場面ごとの組み合わせ:</span>
        {Object.keys(shapes.sets).map(name => (
          <span key={name} className="m3d-shape-set">
            <button type="button" className="lora-btn lora-btn--secondary" disabled={busy} title="この組み合わせを読み込む(保存すると、いまの形になります)"
              onClick={() => onDraft({ ...shapes.sets[name] })}>{name}</button>
            <button type="button" className="lora-btn lora-btn--secondary" disabled={busy} aria-label={`${name}を消す`}
              onClick={() => void deleteSet(name)}>×</button>
          </span>
        ))}
        <input className="lora-input m3d-set-name" value={setName} maxLength={40} placeholder="名前(例: 普段・陥没)" onChange={e => setSetName(e.target.value)} />
        <button type="button" className="lora-btn lora-btn--secondary" disabled={busy || !setName.trim()}
          onClick={() => void saveWeights(setName.trim()).then(ok => { if (ok) setSetName('') })}>いまの形を組み合わせとして残す</button>
      </div>
      {status && <p className="lora-hint">{status}</p>}
    </div>
  )
}
