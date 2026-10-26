import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fileUrl } from '../library'
import FullscreenViewer from './FullscreenViewer'
import './FaceSheet.css'
import './BodyBase.css'

/**
 * キャラシートの素体。服を着せる前の体(モデリングでいう素体)を、キャラの顔・髪・目のまま部位ごとに描いて見る。
 * 素体(肌の色・体格・身長・プロポーション など)はキャラとは別に一覧で持ち、ここで作る・直す。キャラにはどれを当てるか選ぶ。
 * 素体スーツ(全年齢)はどのキャラにも、裸の素体は成人キャラだけに当てられる(サーバーが判定する)。
 */

type Field = 'sex' | 'skin_tone' | 'build' | 'height' | 'proportion' | 'chest' | 'muscle'

interface Choice { value: string; label: string; tags: string }

interface Options {
  /** 設定の項目(プルダウン)。選択肢は DB にあり、素体の設定のページで編集する */
  fields: { key: Field; label: string }[]
  options: Record<Field, Choice[]>
  kinds: { value: 'suit' | 'nude'; label: string }[]
  groups: { id: string; label: string }[]
}

interface Base extends Record<Field, string> {
  id: number
  name: string
  kind: 'suit' | 'nude'
  extra_tags: string
  negative_tags: string
  notes: string
  used_by?: number
}

interface Part {
  key: string
  label: string
  group: string
  check: string
  available: boolean
  path: string | null
  updated: number | null
  /** 自動チェックの結果(チェックせずに描いた絵は null) */
  auto_check: { passed: boolean; score: number; reasons: string[]; attempts: number } | null
}

interface CharacterBody {
  base: Base | null
  body_tags: string
  nude_allowed: boolean
  nude_reason: string | null
  /** 未成年の手がかりがあるキャラ。素体スーツを控えめに描く(胸・曲線の設定を使わない) */
  modest: boolean
  parts: Part[]
}

interface RigInfo {
  base_id: number | null
  files: Record<string, { label: string; updated: number }>
  motion: string | null
  motions: Record<string, string>
  /** 作った2Dモデルに服の層を重ねたか */
  with_outfit?: boolean
  /** 服を着せた Aポーズの絵(服の層に使う)。drift は素体の絵からの関節のずれ(肩幅に対する割合) */
  outfit: { path: string; updated: number; outfit_tags: string; drift: number | null; drift_warn: boolean; attempts?: number } | null
  default_outfit_tags?: string
  /** 作った2Dモデルの揺れもの(髪の鎖・胸・風の強さ) */
  physics?: { hair: string[]; breasts: boolean; wind: number } | null
  /** 胸の揺れを付けられるキャラか(成人キャラで、控えめに描くキャラでない) */
  breast_allowed?: boolean
  /** 髪の下を NovelAI で描き直した Aポーズの絵(2Dモデルの体の絵に使う) */
  behind_hair?: { path: string; updated: number; region: string | null; points: unknown } | null
  /** 体の部位分けのモデル(see-through)を置いてあるか */
  parsing_model?: boolean
  /** 今のモデルを see-through の層から作ったか */
  from_layers?: boolean
  /** see-through(リモートの GPU)で分けた層。a_pose は素体の絵、outfit は服を着せた絵 */
  layers?: {
    configured: boolean
    a_pose: LayerState
    outfit: LayerState
  }
  /** 作ったときの品質の確かめ(Aポーズの骨格・服の層のずれ・描き出したコマのかけら/細い穴) */
  quality?: {
    a_pose_problems: string[]
    outfit_drift: number | null
    outfit_drift_warn: boolean
    frames: { ok: boolean; frames: number[]; gap_frames: number[]; worst_fragment: number }
  } | null
}

interface LayerState {
  updated: number | null
  job: { status: 'running' | 'done' | 'error'; error: string | null; seconds: number } | null
}

interface Props {
  characterId: number
  characterName: string
  token: string | null
  rootName: string
  disabled?: boolean
}

const EMPTY: Omit<Base, 'id'> = {
  name: '', kind: 'suit', sex: 'female', skin_tone: '', build: '', height: '', proportion: '', chest: '', muscle: '',
  extra_tags: '', negative_tags: '', notes: '',
}

async function detail(res: Response): Promise<string> {
  const data = await res.json().catch(() => null)
  return typeof data?.detail === 'string' ? data.detail : `HTTP ${res.status}`
}

export default function BodyBase({ characterId, characterName, token, rootName, disabled }: Props) {
  const navigate = useNavigate()
  const [options, setOptions] = useState<Options | null>(null)
  const [bases, setBases] = useState<Base[]>([])
  const [body, setBody] = useState<CharacterBody | null>(null)
  // 編集中の素体(id が無ければ新しく作る)
  const [editing, setEditing] = useState<(Omit<Base, 'id'> & { id?: number }) | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [useReference, setUseReference] = useState(false)
  // 自動チェック: 描いた絵を調べ、合わなければシードを変えて描き直す(回数は開発管理者の設定)
  const [autoCheck, setAutoCheck] = useState(true)
  const [running, setRunning] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [zoomKey, setZoomKey] = useState<string | null>(null)
  // 2Dモデル(Blender で骨を入れたもの)
  const [rig, setRig] = useState<RigInfo | null>(null)
  const [rigMotion, setRigMotion] = useState('wave')
  const [rigRunning, setRigRunning] = useState(false)
  const [rigStatus, setRigStatus] = useState<string | null>(null)
  // 服の層: 着せる服のタグ(空ならキャラシートの普段の服装)・描き直しの強さ・2Dモデルに重ねるか
  const [outfitTags, setOutfitTags] = useState('')
  const [outfitStrength, setOutfitStrength] = useState(0.85)
  const [withOutfit, setWithOutfit] = useState(true)
  // 揺れもの: 髪・胸(成人キャラだけ)・風の強さ
  const [hairPhysics, setHairPhysics] = useState(true)
  const [breastPhysics, setBreastPhysics] = useState(true)
  const [wind, setWind] = useState(0.3)
  const [outfitRunning, setOutfitRunning] = useState(false)
  // see-through の層からモデルを作るか(層があれば既定で使う)
  const [useLayers, setUseLayers] = useState(true)

  function loadBases() {
    fetch('/api/lora-dataset/body-bases').then(r => (r.ok ? r.json() : [])).then(setBases).catch(() => setBases([]))
  }
  function loadBody() {
    fetch(`/api/lora-dataset/character/${characterId}/body-base?root_name=${encodeURIComponent(rootName)}`)
      .then(r => (r.ok ? r.json() : null))
      .then(setBody)
      .catch(() => setBody(null))
  }
  useEffect(() => {
    fetch('/api/lora-dataset/body-bases/options').then(r => (r.ok ? r.json() : null)).then(setOptions).catch(() => {})
    loadBases()
  }, [])
  useEffect(loadBody, [characterId, rootName])

  function loadRig() {
    fetch(`/api/lora-dataset/character/${characterId}/body-rig?root_name=${encodeURIComponent(rootName)}`)
      .then(r => (r.ok ? r.json() : null))
      .then(setRig)
      .catch(() => setRig(null))
  }
  useEffect(loadRig, [characterId, rootName, body?.base?.id])
  // see-through の仕事が動いている間は、状態を聞き直す(1枚に数分かかる)
  const layersRunning = rig?.layers?.a_pose.job?.status === 'running' || rig?.layers?.outfit.job?.status === 'running'
  useEffect(() => {
    if (!layersRunning) return
    const timer = window.setInterval(loadRig, 5000)
    return () => window.clearInterval(timer)
  }, [layersRunning, characterId, rootName])
  // モデルに使う絵(服の層を重ねるなら服を着せた絵)の層
  const currentLayers = withOutfit && rig?.outfit ? rig?.layers?.outfit : rig?.layers?.a_pose
  const layersReady = useLayers && !!currentLayers?.updated
  // キャラを替えたら、服のタグは前に着せた服(無ければキャラシートの普段の服装)にする
  useEffect(() => {
    setOutfitTags(rig?.outfit?.outfit_tags || rig?.default_outfit_tags || '')
  }, [rig?.base_id, rig?.outfit?.outfit_tags, rig?.default_outfit_tags])

  // 髪の下の体の描き直し: 強さと、点の指定(任意。JSON)
  const [underStrength, setUnderStrength] = useState(0.55)
  const [underPoints, setUnderPoints] = useState('')
  const [underRunning, setUnderRunning] = useState(false)

  async function drawBehindHair() {
    let points: unknown = null
    if (underPoints.trim()) {
      try {
        points = JSON.parse(underPoints)
      } catch {
        setRigStatus('点の指定が JSON として読めません')
        return
      }
    }
    setUnderRunning(true)
    setRigStatus('垂れた髪を見つけて、髪の下の体を描き直しています…')
    try {
      const res = await fetch(`/api/lora-dataset/character/${characterId}/body-hair-under`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ strength: underStrength, root_name: rootName, ...(points ? { points } : {}) }),
      })
      if (!res.ok) {
        setRigStatus(`描き直せませんでした: ${await detail(res)}`)
        return
      }
      setRig(await res.json())
      setRigStatus('描き直しました。「2Dモデルを作る」で、この絵を体に使います')
    } catch (e) {
      setRigStatus(e instanceof Error ? e.message : String(e))
    } finally {
      setUnderRunning(false)
    }
  }

  async function drawOutfit() {
    setOutfitRunning(true)
    setRigStatus('素体の Aポーズに服を着せています…')
    try {
      const res = await fetch(`/api/lora-dataset/character/${characterId}/body-outfit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ outfit_tags: outfitTags, strength: outfitStrength, root_name: rootName }),
      })
      if (!res.ok) {
        setRigStatus(`描けませんでした: ${await detail(res)}`)
        return
      }
      const next: RigInfo = await res.json()
      setRig(next)
      setRigStatus(next.outfit?.drift_warn
        ? '描けました。ただし姿勢が素体の絵からずれています(強さを下げて描き直すと、ずれが減ります)'
        : '描けました。「2Dモデルを作る」で服の層を重ねます')
    } catch (e) {
      setRigStatus(e instanceof Error ? e.message : String(e))
    } finally {
      setOutfitRunning(false)
    }
  }

  async function splitLayers(outfit: boolean) {
    setRigStatus(null)
    try {
      const res = await fetch(`/api/lora-dataset/character/${characterId}/body-layers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ root_name: rootName, with_outfit: outfit }),
      })
      if (!res.ok) {
        setRigStatus(`層に分けられませんでした: ${await detail(res)}`)
        return
      }
      setRig(await res.json())
    } catch (e) {
      setRigStatus(e instanceof Error ? e.message : String(e))
    }
  }

  function rigUrl(name: string) {
    const updated = rig?.files[name]?.updated ?? 0
    return `/api/lora-dataset/character/${characterId}/body-rig/file?name=${name}&root_name=${encodeURIComponent(rootName)}&v=${updated}`
  }

  async function buildRig() {
    setRigRunning(true)
    setRigStatus('部位に分けて、Blender で骨を入れています(1分ほどかかります)…')
    try {
      const res = await fetch(`/api/lora-dataset/character/${characterId}/body-rig`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          motion: rigMotion, root_name: rootName, with_outfit: withOutfit,
          hair_physics: hairPhysics, breast_physics: breastPhysics && !!rig?.breast_allowed, wind,
          use_layers: layersReady,
        }),
      })
      if (!res.ok) {
        setRigStatus(`作れませんでした: ${await detail(res)}`)
        return
      }
      setRig(await res.json())
      setRigStatus('できました')
    } catch (e) {
      setRigStatus(e instanceof Error ? e.message : String(e))
    } finally {
      setRigRunning(false)
    }
  }
  useEffect(() => {
    setZoomKey(null)
    setPicked(new Set())
    setStatus(null)
    setError(null)
  }, [characterId])

  async function assign(baseId: number | null) {
    setError(null)
    const res = await fetch(`/api/lora-dataset/character/${characterId}/body-base?root_name=${encodeURIComponent(rootName)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body_base_id: baseId }),
    })
    if (!res.ok) {
      setError(await detail(res))
      return
    }
    setBody(await res.json())
    setPicked(new Set())
    loadBases()
  }

  async function save() {
    if (!editing) return
    setError(null)
    const { id, used_by: _usedBy, ...fields } = editing as Base
    const res = await fetch(id ? `/api/lora-dataset/body-bases/${id}` : '/api/lora-dataset/body-bases', {
      method: id ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fields),
    })
    if (!res.ok) {
      setError(await detail(res))
      return
    }
    const saved: Base = await res.json()
    setEditing(null)
    loadBases()
    // 作った素体は、まだ素体の無いキャラにそのまま当てる
    if (!id && !body?.base && (saved.kind === 'suit' || body?.nude_allowed)) await assign(saved.id)
    else loadBody()
  }

  async function remove(base: Base) {
    const note = base.used_by ? `(${base.used_by} 人のキャラが使っています。素体なしに戻ります)` : ''
    if (!window.confirm(`素体「${base.name}」を消しますか?${note} 描いた絵は残ります。`)) return
    const res = await fetch(`/api/lora-dataset/body-bases/${base.id}`, { method: 'DELETE' })
    if (!res.ok) setError(await detail(res))
    loadBases()
    loadBody()
  }

  function toggle(key: string) {
    const next = new Set(picked)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    setPicked(next)
  }

  function pickGroup(group: string, onlyMissing: boolean) {
    const next = new Set(picked)
    for (const p of body?.parts ?? []) if (p.group === group && p.available && (!onlyMissing || !p.path)) next.add(p.key)
    setPicked(next)
  }

  async function generate() {
    if (!picked.size) return
    setRunning(true)
    setError(null)
    setStatus(`${picked.size} 枚を描いています…`)
    try {
      const res = await fetch(`/api/lora-dataset/character/${characterId}/body-base`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ keys: [...picked], root_name: rootName, use_reference: useReference, auto_check: autoCheck }),
      })
      if (!res.ok || !res.body) {
        setStatus(`描けませんでした: ${await detail(res)}`)
        return
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let failed = 0
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const event = JSON.parse(line.slice(6)) as {
            current?: number; total?: number; label?: string; status?: string; attempt?: number; reasons?: string[]
          }
          if (event.status === 'error') failed++
          if (event.status === 'retry') {
            setStatus(`${event.current}/${event.total}: ${event.label} を描き直しています(${event.attempt}回目: ${event.reasons?.join('・')})`)
            continue
          }
          if (event.current && event.total) {
            setStatus(`${event.current}/${event.total}: ${event.label}${event.status === 'error' ? '(失敗)' : ''}`)
            if (event.status === 'ok') loadBody()
          }
        }
      }
      setStatus(failed ? `描き終わりました(失敗 ${failed} 枚)` : '描き終わりました')
      setPicked(new Set())
    } catch (e) {
      setStatus(e instanceof Error ? e.message : String(e))
    } finally {
      setRunning(false)
      loadBody()
    }
  }

  const busy = disabled || running
  const kindLabel = (kind: string) => options?.kinds.find(k => k.value === kind)?.label ?? kind
  const groups = options?.groups ?? []
  const drawn = groups.flatMap(g => (body?.parts ?? []).filter(p => p.group === g.id && p.path))
  const zoomIndex = drawn.findIndex(p => p.key === zoomKey)
  const zoom = zoomIndex >= 0 ? drawn[zoomIndex] : null

  return (
    <section className="fsh bdb">
      <h3>{characterName}の素体</h3>
      <p className="lora-hint">
        服を着せる前の体を、キャラの顔・髪・目のまま部位ごとに描きます。体型と肌の色は、キャラシートの容姿の代わりに素体の設定を使います。
        素体スーツはどのキャラにも、裸の素体(デッサン人形の程度)は成人キャラにだけ当てられます。
      </p>

      <div className="bdb-assign">
        <label className="lora-label" htmlFor="bdb-assign">このキャラに当てる素体</label>
        <select
          id="bdb-assign"
          className="lora-select"
          value={body?.base?.id ?? ''}
          disabled={busy}
          onChange={e => void assign(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">素体なし</option>
          {bases.map(b => (
            <option key={b.id} value={b.id} disabled={b.kind === 'nude' && !body?.nude_allowed}>
              {b.name}({kindLabel(b.kind)})
            </option>
          ))}
        </select>
        {body?.base && <p className="lora-hint">体のタグ: {body.body_tags || '(指定なし)'}</p>}
        {body && !body.nude_allowed && <p className="lora-hint">裸の素体は当てられません: {body.nude_reason}</p>}
        {body?.modest && (
          <p className="lora-hint">未成年の手がかりがあるキャラなので、素体スーツは胸・体の曲線の設定を使わず、控えめに描きます。</p>
        )}
      </div>

      <div className="bdb-list">
        <div className="fsh-group-head">
          <h4>素体の一覧</h4>
          <button type="button" className="lora-btn lora-btn--secondary" disabled={busy} onClick={() => setEditing({ ...EMPTY })}>
            新しい素体
          </button>
          <button type="button" className="lora-btn lora-btn--secondary" onClick={() => navigate('/body-base-settings')}
            title="性別・肌の色・体格 などのプルダウンの選択肢と、そのタグを編集します">
            選択肢を編集
          </button>
        </div>
        {bases.length === 0 && <p className="lora-hint">まだ素体がありません。</p>}
        <ul>
          {bases.map(b => (
            <li key={b.id} className={b.id === body?.base?.id ? 'bdb-item bdb-item--on' : 'bdb-item'}>
              <span className="bdb-item-name">{b.name}</span>
              <span className={b.kind === 'nude' ? 'bdb-kind bdb-kind--nude' : 'bdb-kind'}>{kindLabel(b.kind)}</span>
              <span className="lora-hint">{b.used_by ?? 0} 人</span>
              <button type="button" className="lora-btn lora-btn--secondary" disabled={busy} onClick={() => setEditing({ ...b })}>編集</button>
              <button type="button" className="lora-btn lora-btn--danger" disabled={busy} onClick={() => void remove(b)}>削除</button>
            </li>
          ))}
        </ul>
      </div>

      {editing && options && (
        <div className="bdb-editor">
          <h4>{editing.id ? `「${editing.name}」を編集` : '新しい素体'}</h4>
          <label className="lora-label">名前
            <input className="lora-input" value={editing.name} onChange={e => setEditing({ ...editing, name: e.target.value })} />
          </label>
          <div className="bdb-kinds">
            {options.kinds.map(k => (
              <label key={k.value} className="fsh-label">
                <input type="radio" name="bdb-kind" checked={editing.kind === k.value}
                  onChange={() => setEditing({ ...editing, kind: k.value })} />
                {k.label}
              </label>
            ))}
          </div>
          <div className="bdb-fields">
            {options.fields.map(f => (
              <label key={f.key} className="lora-label">{f.label}
                <select className="lora-select" value={editing[f.key]} onChange={e => setEditing({ ...editing, [f.key]: e.target.value })}>
                  {!options.options[f.key].some(c => c.value === '') && <option value="">指定しない</option>}
                  {options.options[f.key].map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
              </label>
            ))}
          </div>
          <label className="lora-label">足すタグ(ほくろ・傷あと・日焼けあと など。性的なタグは入れられません)
            <textarea className="lora-textarea" rows={2} value={editing.extra_tags}
              onChange={e => setEditing({ ...editing, extra_tags: e.target.value })} />
          </label>
          <label className="lora-label">ネガティブ
            <textarea className="lora-textarea" rows={2} value={editing.negative_tags}
              onChange={e => setEditing({ ...editing, negative_tags: e.target.value })} />
          </label>
          <label className="lora-label">メモ
            <textarea className="lora-textarea" rows={2} value={editing.notes} onChange={e => setEditing({ ...editing, notes: e.target.value })} />
          </label>
          <div className="fsh-actions">
            <button type="button" className="lora-btn" disabled={busy || !editing.name.trim()} onClick={() => void save()}>保存</button>
            <button type="button" className="lora-btn lora-btn--secondary" onClick={() => setEditing(null)}>やめる</button>
          </div>
        </div>
      )}

      {error && <p className="lora-error" role="alert">{error}</p>}

      {body?.base && groups.map(group => (
        <div key={group.id}>
          <div className="fsh-group-head">
            <h4>{group.label}</h4>
            <button type="button" className="lora-btn lora-btn--secondary" disabled={busy} onClick={() => pickGroup(group.id, false)}>全部選ぶ</button>
            <button type="button" className="lora-btn lora-btn--secondary" disabled={busy} onClick={() => pickGroup(group.id, true)}>まだの部位を選ぶ</button>
          </div>
          <div className="fsh-grid">
            {body.parts.filter(p => p.group === group.id).map(p => (
              <div key={p.key} className={picked.has(p.key) ? 'fsh-cell fsh-cell--on' : 'fsh-cell'} title={p.check}>
                {p.path ? (
                  <button type="button" className="fsh-thumb" onClick={() => setZoomKey(p.key)} title="拡大">
                    <img src={`${fileUrl(p.path)}&v=${p.updated ?? 0}`} alt={p.label} loading="lazy" />
                  </button>
                ) : (
                  <div className="fsh-thumb fsh-thumb--none">{p.available ? '未' : '全身図で確認'}</div>
                )}
                <label className="fsh-label">
                  <input type="checkbox" checked={picked.has(p.key)} disabled={busy || !p.available} onChange={() => toggle(p.key)} />
                  {p.label}
                </label>
                {p.path && p.auto_check && (
                  <span className={p.auto_check.passed ? 'bdb-auto bdb-auto--ok' : 'bdb-auto bdb-auto--ng'}
                    title={p.auto_check.passed ? '自動チェックに合格' : `合格する絵が出なかったので、いちばん良い絵を残しました: ${p.auto_check.reasons.join('・')}`}>
                    {p.auto_check.passed ? '✓ チェック済み' : `⚠ ${p.auto_check.reasons.join('・')}`}
                    {p.auto_check.attempts > 1 && `(${p.auto_check.attempts}回)`}
                  </span>
                )}
                <details className="bdb-check">
                  <summary>確かめる点</summary>
                  <p>{p.check}</p>
                </details>
              </div>
            ))}
          </div>
        </div>
      ))}

      {body?.base && (
        <div className="fsh-actions">
          <label className="fsh-label" title="振り返り・2人並び・手の突き出し・足裏の構図・Aポーズの崩れを調べ、合わなければシードを変えて描き直します(回数は開発管理者の設定。描くたびに Anlas を使います)">
            <input type="checkbox" checked={autoCheck} disabled={busy} onChange={e => setAutoCheck(e.target.checked)} />
            自動チェック(合わなければ描き直す)
          </label>
          <label className="fsh-label">
            <input type="checkbox" checked={useReference} disabled={busy} onChange={e => setUseReference(e.target.checked)} />
            参照画像も使う(V4.5・顔を寄せる。体型も寄るので、素体の体型を確かめるときは外す)
          </label>
          <button type="button" className="lora-btn" disabled={busy || picked.size === 0} onClick={() => void generate()}>
            {running ? '描いています…' : `選んだ ${picked.size} 枚を描く`}
          </button>
          {status && <span className="lora-hint">{status}</span>}
        </div>
      )}

      {body?.base && rig && (
        <div className="bdb-rig">
          <h4>2Dモデル(Blender)</h4>
          <p className="lora-hint">
            「Aポーズ(関節の確認)」の絵の形に合わせた平らなメッシュ(2Dの素体)を作り、絵をテクスチャとして貼って、関節に骨を入れた
            Blender のファイルを作ります。メッシュは骨の重みで皮膚のように曲がります(体で1枚、左右の腕で1枚ずつ)。確かめ用に動きを
            付けて描き出します。zip にはテクスチャと rig.json(骨・メッシュの頂点・重み)も入ります。Spine(4.1 の JSON・重み付きメッシュ・
            動き全部)と、Live2D の Cubism Editor に読み込む PSD(部位ごとのレイヤー)も書き出します。
          </p>
          <div className="bdb-outfit">
            <h5>服の層</h5>
            <p className="lora-hint">
              素体の Aポーズの首から下を、服のタグで描き直した絵を作ります(1枚ぶんの Anlas)。素体の絵と違う所を服の層として、
              同じ骨で動くメッシュにして体の手前に重ねます。Blender では「outfit」のコレクションで表示を切り替えられます。
            </p>
            <div className="bdb-outfit-row">
              {rig.outfit && (
                <img src={`${fileUrl(rig.outfit.path)}&v=${rig.outfit.updated}`} alt="服を着せた Aポーズ" className="bdb-outfit-thumb" />
              )}
              <div className="bdb-outfit-form">
                <label className="lora-label">着せる服のタグ
                  <textarea className="lora-textarea" rows={2} value={outfitTags} disabled={busy || outfitRunning}
                    placeholder="white long-sleeved blouse, navy skirt など(空ならキャラシートの普段の服装)"
                    onChange={e => setOutfitTags(e.target.value)} />
                </label>
                <label className="lora-label">描き直しの強さ {outfitStrength.toFixed(2)}(低いほど素体の姿勢に近く、高いほど服がはっきり変わる)
                  <input type="range" min={0.5} max={1} step={0.05} value={outfitStrength} disabled={busy || outfitRunning}
                    onChange={e => setOutfitStrength(Number(e.target.value))} />
                </label>
                <div className="fsh-actions">
                  <button type="button" className="lora-btn lora-btn--secondary" onClick={() => void drawOutfit()}
                    disabled={busy || outfitRunning || rigRunning || !body.parts.find(p => p.key === 'a_pose')?.path}>
                    {outfitRunning ? '描いています…' : rig.outfit ? '服を着せ直す' : '服を着せた絵を描く'}
                  </button>
                  {rig.outfit?.drift != null && (
                    <span className={rig.outfit.drift_warn ? 'lora-error' : 'lora-hint'}>
                      関節のずれ {(rig.outfit.drift * 100).toFixed(1)}%{rig.outfit.drift_warn ? '(大きめ)' : ''}
                      {(rig.outfit.attempts ?? 1) > 1 && `・${rig.outfit.attempts}回描いていちばんずれの小さい絵`}
                    </span>
                  )}
                </div>
              </div>
            </div>
          </div>
          <div className="bdb-outfit">
            <h5>髪の層(長い髪)</h5>
            <p className="lora-hint">
              垂れた髪は髪の層に分けて揺らします。{rig.parsing_model
                ? '髪は体の部位分けのモデル(see-through)で切り出します。'
                : '体の部位分けのモデルが無いので、髪は色と形で切り出します(scripts/export_body_parsing.py で入れると正確になります)。'}
              髪の下に隠れていた体は、ふだんはまわりから埋めます。下のボタンで NovelAI に描き直させると、きれいになります(1枚ぶんの Anlas)。
            </p>
            <div className="bdb-outfit-row">
              {rig.behind_hair && (
                <img src={`${fileUrl(rig.behind_hair.path)}&v=${rig.behind_hair.updated}`} alt="髪の下を描き直した Aポーズ" className="bdb-outfit-thumb" />
              )}
              <div className="bdb-outfit-form">
                <label className="lora-label">描き直しの強さ {underStrength.toFixed(2)}(強すぎると長い髪をまた描きます)
                  <input type="range" min={0.3} max={0.8} step={0.05} value={underStrength} disabled={busy || underRunning}
                    onChange={e => setUnderStrength(Number(e.target.value))} />
                </label>
                <details>
                  <summary className="lora-hint">点で髪を指す(任意)</summary>
                  <textarea className="lora-textarea" rows={3} value={underPoints} disabled={busy || underRunning}
                    placeholder='[{"pos": [[265,300],[250,400]], "neg": [[410,330]]}, {"pos": [[545,300]], "neg": [[410,330]]}]'
                    onChange={e => setUnderPoints(e.target.value)} />
                </details>
                <div className="fsh-actions">
                  <button type="button" className="lora-btn lora-btn--secondary" onClick={() => void drawBehindHair()}
                    disabled={busy || underRunning || rigRunning || !body.parts.find(p => p.key === 'a_pose')?.path}>
                    {underRunning ? '描き直しています…' : rig.behind_hair ? '髪の下の体を描き直す(やり直し)' : '髪の下の体を描き直す(NovelAI)'}
                  </button>
                </div>
              </div>
            </div>
          </div>
          {rig.layers && (
            <div className="bdb-outfit">
              <h5>see-through で層に分ける(リモートの GPU)</h5>
              <p className="lora-hint">
                絵を、前髪・後ろ髪・顔・目・口・服・腕・脚などの層に分けます(隠れていた所まで描き足します)。層ごとにメッシュを作るので、
                髪や腕の重なりがきれいに動きます。Colab で notebooks/see_through_colab.ipynb を動かしている間だけ使えます(1枚 8分ほど)。
              </p>
              {!rig.layers.configured ? (
                <p className="lora-hint">see-through のサーバーが設定されていません(.env の SEE_THROUGH_BASE_URL・SEE_THROUGH_TOKEN)。</p>
              ) : (
                <div className="fsh-actions">
                  {([['a_pose', false, '素体の絵'], ['outfit', true, '服を着せた絵']] as const).map(([key, outfit, label]) => {
                    const state = rig.layers![key]
                    const job = state.job
                    const hasSource = outfit ? !!rig.outfit : !!body.parts.find(p => p.key === 'a_pose')?.path
                    if (!hasSource) return null
                    return (
                      <span key={key} className="bdb-layers-item">
                        <button type="button" className="lora-btn lora-btn--secondary" onClick={() => void splitLayers(outfit)}
                          disabled={busy || rigRunning || job?.status === 'running'}>
                          {job?.status === 'running'
                            ? `${label}を分けています…(${Math.floor(job.seconds / 60)}分${job.seconds % 60}秒)`
                            : state.updated ? `${label}を分け直す` : `${label}を層に分ける`}
                        </button>
                        {job?.status === 'error' && <span className="lora-error">{job.error}</span>}
                        {state.updated && job?.status !== 'running' && <span className="lora-hint">✓ 分けた層あり</span>}
                      </span>
                    )
                  })}
                </div>
              )}
            </div>
          )}
          <div className="fsh-actions bdb-physics">
            <span className="lora-hint">揺れもの:</span>
            <label className="fsh-label" title="目の高さより下に垂れた髪を髪の層に分け、ばねでつないだ骨で、動きや重力・風に遅れて揺らします">
              <input type="checkbox" checked={hairPhysics} disabled={busy || rigRunning} onChange={e => setHairPhysics(e.target.checked)} />
              髪の揺れ
            </label>
            <label className="fsh-label"
              title={rig.breast_allowed ? '胸を、体の動きに遅れて揺らします' : '胸の揺れは、成人キャラ(未成年の手がかりの無いキャラ)だけに付けられます'}>
              <input type="checkbox" checked={breastPhysics && !!rig.breast_allowed} disabled={busy || rigRunning || !rig.breast_allowed}
                onChange={e => setBreastPhysics(e.target.checked)} />
              胸の揺れ{!rig.breast_allowed && '(このキャラには付けられません)'}
            </label>
            <label className="fsh-label">風 {wind.toFixed(2)}
              <input type="range" min={0} max={1} step={0.05} value={wind} disabled={busy || rigRunning || !hairPhysics}
                onChange={e => setWind(Number(e.target.value))} />
            </label>
            {rig.physics && (
              <span className="lora-hint">
                (今のモデル: 髪 {rig.physics.hair.length ? rig.physics.hair.length + '本の鎖' : 'なし'}・胸 {rig.physics.breasts ? 'あり' : 'なし'}・風 {rig.physics.wind})
              </span>
            )}
          </div>
          <div className="fsh-actions">
            {rig.outfit && (
              <label className="fsh-label">
                <input type="checkbox" checked={withOutfit} disabled={busy || rigRunning} onChange={e => setWithOutfit(e.target.checked)} />
                服の層を重ねる
              </label>
            )}
            {currentLayers?.updated && (
              <label className="fsh-label" title="see-through で分けた層からモデルを作ります(外すと、絵を骨の範囲で切り分けます)">
                <input type="checkbox" checked={useLayers} disabled={busy || rigRunning} onChange={e => setUseLayers(e.target.checked)} />
                分けた層を使う
              </label>
            )}
            <select className="lora-select bdb-rig-motion" value={rigMotion} disabled={busy || rigRunning}
              onChange={e => setRigMotion(e.target.value)} aria-label="付ける動き">
              {Object.entries(rig.motions).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
            <button type="button" className="lora-btn" disabled={busy || rigRunning || !body.parts.find(p => p.key === 'a_pose')?.path}
              onClick={() => void buildRig()}>
              {rigRunning ? '作っています…' : rig.files['rig.blend'] ? '2Dモデルを作り直す' : '2Dモデルを作る'}
            </button>
            {!body.parts.find(p => p.key === 'a_pose')?.path && <span className="lora-hint">先に「Aポーズ(関節の確認)」を描いてください。</span>}
            {rigStatus && <span className="lora-hint">{rigStatus}</span>}
          </div>
          {rig.quality && (
            <ul className="bdb-quality">
              <li className={rig.quality.a_pose_problems.length ? 'bdb-auto--ng' : 'bdb-auto--ok'}>
                Aポーズ: {rig.quality.a_pose_problems.length ? `⚠ ${rig.quality.a_pose_problems.join('・')}` : '✓ 腕が体から離れ、足首まで写っています'}
              </li>
              {rig.quality.outfit_drift != null && (
                <li className={rig.quality.outfit_drift_warn ? 'bdb-auto--ng' : 'bdb-auto--ok'}>
                  服の層の関節のずれ: {(rig.quality.outfit_drift * 100).toFixed(1)}%{rig.quality.outfit_drift_warn ? ' ⚠ 大きめ(服を着せ直すと減ります)' : ' ✓'}
                </li>
              )}
              <li className={rig.quality.frames.ok ? 'bdb-auto--ok' : 'bdb-auto--ng'}>
                動き: {rig.quality.frames.ok ? '✓ はみ出したかけらはありません' : `⚠ 本体から離れたかけらが出たコマ ${rig.quality.frames.frames.join(', ')}`}
              </li>
              {rig.quality.frames.gap_frames.length > 0 && (
                <li className="lora-hint">
                  細いすき間が見えるコマ {rig.quality.frames.gap_frames.join(', ')}(腕と胴の間が閉じただけのこともあります。GIF で確かめてください)
                </li>
              )}
            </ul>
          )}
          {(rig.files['overlay.png'] || rig.files['preview.gif']) && (
            <div className="bdb-rig-views">
              {rig.files['overlay.png'] && (
                <figure><img src={rigUrl('overlay.png')} alt="メッシュと骨" /><figcaption>メッシュと骨(三角形の色がいちばん重い骨、白い線が骨、赤い点が回す軸)</figcaption></figure>
              )}
              {rig.files['preview.gif'] && (
                <figure>
                  <img src={rigUrl('preview.gif')} alt="動きの確かめ" />
                  <figcaption>動きの確かめ{rig.motion ? `(${rig.motions[rig.motion] ?? rig.motion}${rig.with_outfit ? '・服の層あり' : ''}${rig.from_layers ? '・see-through の層' : ''})` : ''}</figcaption>
                </figure>
              )}
            </div>
          )}
          <div className="fsh-actions">
            {rig.files['rig.blend'] && <a className="lora-btn lora-btn--secondary" href={rigUrl('rig.blend')} download>Blender のファイル(.blend)</a>}
            {rig.files['rig.zip'] && <a className="lora-btn lora-btn--secondary" href={rigUrl('rig.zip')} download>テクスチャ+rig.json(.zip)</a>}
            {rig.files['spine.zip'] && (
              <a className="lora-btn lora-btn--secondary" href={rigUrl('spine.zip')} download
                title="Spine 4.1 の skeleton.json・アトラス・テクスチャ。重み付きメッシュと、動き全部がアニメーションとして入ります">
                Spine 用(.zip)
              </a>
            )}
            {rig.files['model.psd'] && (
              <a className="lora-btn lora-btn--secondary" href={rigUrl('model.psd')} download
                title="Live2D Cubism Editor に読み込む PSD。部位ごとのレイヤー(体と服の層はフォルダ分け)">
                Live2D 用 PSD
              </a>
            )}
          </div>
        </div>
      )}

      {zoom?.path && (
        <FullscreenViewer
          src={`${fileUrl(zoom.path)}&v=${zoom.updated ?? 0}`}
          alt={zoom.label}
          counter={`${zoomIndex + 1} / ${drawn.length} ・ ${zoom.label}`}
          onPrev={zoomIndex > 0 ? () => setZoomKey(drawn[zoomIndex - 1].key) : undefined}
          onNext={zoomIndex < drawn.length - 1 ? () => setZoomKey(drawn[zoomIndex + 1].key) : undefined}
          onClose={() => setZoomKey(null)}
        />
      )}
    </section>
  )
}
