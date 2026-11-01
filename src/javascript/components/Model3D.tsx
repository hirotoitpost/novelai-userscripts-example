import { lazy, Suspense, useEffect, useState } from 'react'
import { useLocalStorage } from '../hooks/useLocalStorage'
import FullscreenViewer from './FullscreenViewer'
import './Model3D.css'

/**
 * 素体の 3D モデル(漫画のポーズの参考)。素体の正面・横・後ろの絵から、Colab の Hunyuan3D-2mv で形を作り、手元の Blender で
 * 整えて骨と人形の手を入れる(docs/model_3d_plan_jp.md)。ブラウザで回して見て動きを再生し、ポーズとカメラの向きを選んで
 * 参考の絵(灰色の陰影・線画風)を描き出す。形を作る仕事は数分かかるので、動いている間は状態を聞き直す。
 */

const Model3DViewer = lazy(() => import('./Model3DViewer'))

type JobState = 'queued' | 'running' | 'cleaning' | 'rigging' | 'posing'

interface Job {
  status: 'running' | 'done' | 'error'
  remote: JobState | null
  error: string | null
  seconds: number
}

interface PoseImage { name: string; pose: string; camera: string; style: string; updated: number }

interface Check { views: Record<string, number>; score: number; passed: boolean; reasons: string[]; attempts?: number }

interface Meta {
  seed?: number
  side_flipped?: boolean
  seconds?: number
  clean?: { faces_in: number; faces: number; fragments: number; height: number }
  check?: Check
  rig?: {
    motion: string
    bones?: number
    hand?: { ratios: Record<string, number>; source: string | null; score: number | null }
    weights?: { nearest_bone: number; hand_cut: number; legs_split?: number }
  }
}

interface BaseEntry {
  base_id: number
  name: string
  kind: 'suit' | 'swimsuit' | 'nude'
  assigned: boolean
  views: Record<'front' | 'left' | 'back', boolean>
  files: Record<string, { label: string; updated: number }>
  meta: Meta | null
  job: Job | null
  poses: PoseImage[]
  /** 形状パーツ: 選べる形(部位 → 名前と形)と、保存した強さ */
  shapes: { options: Record<string, { label: string; presets: Record<string, string> }>; weights: Record<string, number> }
}

interface Model3DInfo {
  configured: boolean
  bases: BaseEntry[]
  motions: Record<string, string>
  pose_options: { poses: Record<string, string>; cameras: Record<string, string>; styles: Record<string, string> }
}

interface Props {
  characterId: number
  rootName: string
  /** キャラに当てた素体(変わったら読み直す) */
  baseId: number | null
  disabled?: boolean
}

const KIND_LABELS: Record<BaseEntry['kind'], string> = { suit: 'スーツ', swimsuit: '水着', nude: '裸' }
const VIEW_LABELS: Record<string, string> = { front: '正面', left: '横', back: '後ろ' }
const JOB_LABELS: Record<JobState, string> = {
  queued: 'Colab の順番を待っています',
  running: 'Colab で形を作っています',
  cleaning: 'Blender で整えています',
  rigging: 'Blender で骨と手を入れています',
  posing: 'ポーズの絵を描いています',
}

async function detail(res: Response): Promise<string> {
  const data = await res.json().catch(() => null)
  return typeof data?.detail === 'string' ? data.detail : `HTTP ${res.status}`
}

function duration(seconds: number): string {
  return seconds >= 60 ? `${Math.floor(seconds / 60)}分${seconds % 60}秒` : `${seconds}秒`
}

export default function Model3D({ characterId, rootName, baseId, disabled }: Props) {
  const [open, setOpen] = useLocalStorage('nai_body_base_model3d_open', false)
  const [info, setInfo] = useState<Model3DInfo | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const [seed, setSeed] = useState(12345)
  const [motion, setMotion] = useState('wave')
  const [status, setStatus] = useState<string | null>(null)
  const [viewing, setViewing] = useState(false)
  const [animations, setAnimations] = useState<string[]>([])
  const [animation, setAnimation] = useState<string | null>(null)
  const [pose, setPose] = useState('stand')
  const [cameras, setCameras] = useState<string[]>(['front', 'three_quarter', 'side'])
  const [style, setStyle] = useState('shade')
  const [zoom, setZoom] = useState<number | null>(null)
  // 形状パーツの強さ(保存前の編集中。null なら保存した強さ)
  const [shapeDraft, setShapeDraft] = useState<Record<string, number> | null>(null)

  const url = `/api/lora-dataset/character/${characterId}/model-3d`

  function load() {
    fetch(`${url}?root_name=${encodeURIComponent(rootName)}`)
      .then(r => (r.ok ? r.json() : null))
      .then(setInfo)
      .catch(() => setInfo(null))
  }
  useEffect(() => {
    if (open) load()
  }, [characterId, rootName, baseId, open])

  const entry = info?.bases.find(b => b.base_id === selected) ?? info?.bases.find(b => b.assigned) ?? info?.bases[0] ?? null
  useEffect(() => {
    setSelected(null)
    setViewing(false)
    setZoom(null)
  }, [characterId])
  useEffect(() => setShapeDraft(null), [entry?.base_id, entry?.files['model.blend']?.updated])

  const running = !!info?.bases.some(b => b.job?.status === 'running')
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(load, 5000)
    return () => window.clearInterval(timer)
  }, [running, characterId, rootName])

  function fileUrl(name: string, updated?: number) {
    const v = updated ?? entry?.files[name]?.updated ?? 0
    return `${url}/file?base_id=${entry?.base_id}&name=${encodeURIComponent(name)}&root_name=${encodeURIComponent(rootName)}&v=${v}`
  }

  async function post(path: string, body: Record<string, unknown>, started: string) {
    if (!entry) return
    setStatus(null)
    try {
      const res = await fetch(`${url}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ root_name: rootName, base_id: entry.base_id, ...body }),
      })
      if (!res.ok) {
        setStatus(`始められませんでした: ${await detail(res)}`)
        return
      }
      setInfo(await res.json())
      setStatus(started)
    } catch (e) {
      setStatus(e instanceof Error ? e.message : String(e))
    }
  }

  const shapeWeights = shapeDraft ?? entry?.shapes.weights ?? {}

  /** 部位の形を選ぶ(部位の中では 1 つだけ。空なら使わない) */
  function pickShape(part: string, preset: string) {
    const presets = Object.keys(entry?.shapes.options[part]?.presets ?? {})
    const next = Object.fromEntries(Object.entries(shapeWeights).filter(([key]) => !presets.includes(key)))
    if (preset) next[preset] = 1
    setShapeDraft(next)
  }

  async function saveShapes() {
    if (!entry || !shapeDraft) return
    setStatus(null)
    const res = await fetch(`${url}/shapes`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ root_name: rootName, base_id: entry.base_id, weights: shapeDraft }),
    })
    if (!res.ok) {
      setStatus(`保存できませんでした: ${await detail(res)}`)
      return
    }
    setInfo(await res.json())
    setShapeDraft(null)
    setStatus('形状パーツを保存しました(ポーズの絵に使います)')
  }

  function toggleCamera(key: string) {
    setCameras(prev => (prev.includes(key) ? prev.filter(c => c !== key) : [...prev, key]))
  }

  const job = entry?.job
  const busy = disabled || job?.status === 'running'
  const meta = entry?.meta
  const viewerSrc = entry?.files['model_rigged.glb'] ? fileUrl('model_rigged.glb') : entry?.files['model_clean.glb'] ? fileUrl('model_clean.glb') : null
  const stills = (['front.png', 'side.png', 'back.png'] as const).filter(name => entry?.files[name])
  const poses = entry?.poses ?? []
  const zoomed = zoom != null ? poses[zoom] : null
  const poseLabel = (p: PoseImage) =>
    `${info?.pose_options.poses[p.pose] ?? p.pose}・${info?.pose_options.cameras[p.camera] ?? p.camera}・${info?.pose_options.styles[p.style] ?? p.style}`

  return (
    <details className="bdb-fold" open={open} onToggle={e => { if (e.currentTarget.open !== open) setOpen(e.currentTarget.open) }}>
      <summary className="bdb-fold-head"><h4>3Dモデル(漫画のポーズの参考)</h4></summary>
      <div className="m3d">
        <p className="lora-hint">
          素体の「正面」「側面」「背面」の絵から、Colab の GPU(Hunyuan3D-2mv)で 3D の形を作り、手元の Blender で整えて、骨と
          人形の手(指の骨入り。形は「手(手掌)」の絵に合わせる)を入れます。1体に 3〜6 分かかります。できたら、ブラウザで回して見て、
          ポーズとカメラの向きを選んで参考の絵を描き出せます(GPU は使いません)。
        </p>
        {!info ? (
          <p className="lora-hint">読み込んでいます…</p>
        ) : !info.bases.length ? (
          <p className="lora-hint">3D を作れる素体がありません。先に素体を当てて「正面」を描いてください。</p>
        ) : entry && (
          <>
            <div className="fsh-actions">
              {info.bases.length > 1 && (
                <select className="lora-select" value={entry.base_id} disabled={busy} aria-label="使う素体"
                  onChange={e => { setSelected(Number(e.target.value)); setViewing(false) }}>
                  {info.bases.map(b => (
                    <option key={b.base_id} value={b.base_id}>
                      {b.name}({KIND_LABELS[b.kind]}){b.assigned ? '・当てた素体' : ''}
                    </option>
                  ))}
                </select>
              )}
              <span className="lora-hint">
                送る絵: {Object.entries(entry.views).map(([key, ok]) => `${VIEW_LABELS[key]} ${ok ? '✓' : '✗'}`).join('・')}
              </span>
            </div>
            {!entry.views.front && <p className="lora-hint">先に素体の「正面」を描いてください(横・後ろもあると、前後の厚みが合います)。</p>}
            {!info.configured && (
              <p className="lora-hint">
                3D のサーバーが設定されていません。Colab で notebooks/model3d_colab.ipynb を動かし、.env に MODEL3D_BASE_URL と
                MODEL3D_TOKEN を書いて、バックエンドを起動し直してください(整え直す・骨を入れ直す・ポーズの絵は、サーバーなしで使えます)。
              </p>
            )}
            <div className="fsh-actions">
              <label className="fsh-label">シード
                <input type="number" className="lora-input m3d-seed" value={seed} min={0} disabled={busy}
                  onChange={e => setSeed(Number(e.target.value))} />
              </label>
              <button type="button" className="lora-btn" disabled={busy || !info.configured || !entry.views.front}
                onClick={() => void post('', { seed, motion }, '作り始めました(数分かかります)')}>
                {entry.files['model.glb'] ? '3D の形を作り直す' : '3D の形を作る'}
              </button>
              <button type="button" className="lora-btn lora-btn--secondary" disabled={busy || !entry.files['model.glb']}
                title="作った形を Blender で整え直し、素体の絵と比べ直して、骨を入れ直します(GPU は使いません)"
                onClick={() => void post('/clean', { motion }, '整え直しています')}>
                整え直す
              </button>
              <select className="lora-select" value={motion} disabled={busy} aria-label="確かめの動き"
                onChange={e => setMotion(e.target.value)}>
                {Object.entries(info.motions).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
              </select>
              <button type="button" className="lora-btn lora-btn--secondary" disabled={busy || !entry.files['model_clean.glb']}
                title="骨と人形の手を入れ直し、選んだ動きで確かめの GIF を描き直します"
                onClick={() => void post('/rig', { motion }, '骨を入れ直しています')}>
                骨を入れ直す
              </button>
            </div>
            {job?.status === 'running' && (
              <p className="lora-hint">{job.remote ? JOB_LABELS[job.remote] : '始めています'}…({duration(job.seconds)})</p>
            )}
            {job?.status === 'error' && <p className="lora-error">{job.error}</p>}
            {status && job?.status !== 'running' && <p className="lora-hint">{status}</p>}

            {meta?.check && (
              <ul className="bdb-quality">
                <li className={meta.check.passed ? 'bdb-auto--ok' : 'bdb-auto--ng'}>
                  素体の絵との重なり: {Object.entries(meta.check.views).map(([k, v]) => `${VIEW_LABELS[k] ?? k} ${v.toFixed(2)}`).join('・')}
                  {meta.check.passed ? ' ✓' : ` ⚠ ${meta.check.reasons.join('・')}`}
                  {(meta.check.attempts ?? 1) > 1 && `(${meta.check.attempts}回作って、いちばん合う形)`}
                </li>
                {meta.clean && (
                  <li className="lora-hint">
                    面 {meta.clean.faces.toLocaleString()}(元 {meta.clean.faces_in.toLocaleString()})・身長 {Math.round(meta.clean.height * 100)}cm
                    {meta.side_flipped ? '・横の絵は左右を反転して送りました' : ''}
                  </li>
                )}
                {meta.rig?.hand && (
                  <li className="lora-hint">
                    手の形: {meta.rig.hand.source ? `素体の絵(${meta.rig.hand.source})から測った割合` : '標準の割合(「手(手掌)」の絵を描くと、その形に合わせます)'}
                  </li>
                )}
              </ul>
            )}

            {viewerSrc && (
              <div className="m3d-view">
                <div className="fsh-actions">
                  <button type="button" className="lora-btn lora-btn--secondary" onClick={() => setViewing(v => !v)}>
                    {viewing ? '3D を閉じる' : '3D を回して見る'}
                  </button>
                  {viewing && animations.length > 0 && (
                    <select className="lora-select" value={animation ?? ''} aria-label="再生する動き"
                      onChange={e => setAnimation(e.target.value || null)}>
                      <option value="">止める(立った姿勢)</option>
                      {animations.map(name => <option key={name} value={name}>{info.motions[name] ?? name}</option>)}
                    </select>
                  )}
                </div>
                {viewing && (
                  <Suspense fallback={<p className="lora-hint">読み込んでいます…</p>}>
                    <Model3DViewer key={viewerSrc} src={viewerSrc} animation={animation} onAnimations={setAnimations} shapes={shapeWeights} />
                  </Suspense>
                )}
              </div>
            )}

            {(stills.length > 0 || entry.files['preview.gif'] || entry.files['motion.gif']) && (
              <div className="bdb-rig-views m3d-stills">
                {stills.map(name => (
                  <figure key={name}><img src={fileUrl(name)} alt={entry.files[name].label} /><figcaption>{entry.files[name].label}</figcaption></figure>
                ))}
                {entry.files['preview.gif'] && (
                  <figure><img src={fileUrl('preview.gif')} alt="1周" /><figcaption>1周</figcaption></figure>
                )}
                {entry.files['motion.gif'] && (
                  <figure>
                    <img src={fileUrl('motion.gif')} alt="動き" />
                    <figcaption>動き{meta?.rig ? `(${info.motions[meta.rig.motion] ?? meta.rig.motion})` : ''}</figcaption>
                  </figure>
                )}
              </div>
            )}

            {entry.files['model.blend'] && Object.keys(entry.shapes.options).length > 0 && (
              <div className="m3d-poses">
                <h5>形状パーツ</h5>
                <p className="lora-hint">
                  部位ごとに形を選びます(素体・キャラシートのタグから選んだものが初めの形)。乳首・乳輪は体の面につなぎ止めた部品で、
                  胸の形やポーズに合わせて一緒に動きます。「3D を回して見る」で、保存する前に確かめられます。保存すると、ポーズの絵に使います。
                </p>
                <div className="fsh-actions">
                  {Object.entries(entry.shapes.options).map(([part, spec]) => {
                    const keys = Object.keys(spec.presets)
                    if (part === 'repair') {
                      return (
                        <label key={part} className="fsh-label" title="AI の形にできた小さなへこみ・胸の先の崩れをならします">
                          <input type="checkbox" checked={(shapeWeights.repair ?? 0) > 0} disabled={busy}
                            onChange={e => pickShape(part, e.target.checked ? 'repair' : '')} />
                          {spec.label}
                        </label>
                      )
                    }
                    const current = keys.find(key => (shapeWeights[key] ?? 0) > 0) ?? ''
                    return (
                      <label key={part} className="fsh-label">{spec.label}
                        <select className="lora-select" value={current} disabled={busy} onChange={e => pickShape(part, e.target.value)}>
                          <option value="">使わない</option>
                          {keys.map(key => <option key={key} value={key}>{spec.presets[key]}</option>)}
                        </select>
                      </label>
                    )
                  })}
                  <button type="button" className="lora-btn" disabled={busy || !shapeDraft} onClick={() => void saveShapes()}>保存する</button>
                  {shapeDraft && (
                    <button type="button" className="lora-btn lora-btn--secondary" onClick={() => setShapeDraft(null)}>元に戻す</button>
                  )}
                </div>
              </div>
            )}

            {entry.files['model.blend'] && (
              <div className="m3d-poses">
                <h5>ポーズの参考の絵</h5>
                <div className="fsh-actions">
                  <select className="lora-select" value={pose} disabled={busy} aria-label="ポーズ" onChange={e => setPose(e.target.value)}>
                    {Object.entries(info.pose_options.poses).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                  </select>
                  <select className="lora-select" value={style} disabled={busy} aria-label="絵の種類" onChange={e => setStyle(e.target.value)}>
                    {Object.entries(info.pose_options.styles).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                  </select>
                </div>
                <div className="fsh-actions">
                  <span className="lora-hint">カメラ:</span>
                  {Object.entries(info.pose_options.cameras).map(([key, label]) => (
                    <label key={key} className="fsh-label">
                      <input type="checkbox" checked={cameras.includes(key)} disabled={busy} onChange={() => toggleCamera(key)} />
                      {label}
                    </label>
                  ))}
                  <button type="button" className="lora-btn" disabled={busy || !cameras.length}
                    onClick={() => void post('/pose', { pose, cameras, style }, 'ポーズの絵を描いています')}>
                    ポーズの絵を描く
                  </button>
                </div>
                {poses.length > 0 && (
                  <div className="m3d-pose-grid">
                    {poses.map((p, i) => (
                      <figure key={p.name}>
                        <button type="button" className="m3d-pose-thumb" onClick={() => setZoom(i)} title="大きく見る">
                          <img src={fileUrl(p.name, p.updated)} alt={poseLabel(p)} loading="lazy" />
                        </button>
                        <figcaption>{poseLabel(p)} <a href={fileUrl(p.name, p.updated)} download>保存</a></figcaption>
                      </figure>
                    ))}
                  </div>
                )}
              </div>
            )}

            <div className="fsh-actions">
              {entry.files['model_rigged.glb'] && (
                <a className="lora-btn lora-btn--secondary" href={fileUrl('model_rigged.glb')} download
                  title="骨・人形の手・動きの入った GLB">骨入りの GLB</a>
              )}
              {entry.files['model.blend'] && (
                <a className="lora-btn lora-btn--secondary" href={fileUrl('model.blend')} download
                  title="Blender で手でポーズを付ける用">Blender のファイル(.blend)</a>
              )}
              {entry.files['model_clean.glb'] && (
                <a className="lora-btn lora-btn--secondary" href={fileUrl('model_clean.glb')} download
                  title="整えた形だけ(骨なし)">形だけの GLB</a>
              )}
            </div>
          </>
        )}
      </div>
      {zoomed && zoom != null && (
        <FullscreenViewer
          src={fileUrl(zoomed.name, zoomed.updated)}
          alt={poseLabel(zoomed)}
          counter={`${zoom + 1} / ${poses.length} ・ ${poseLabel(zoomed)}`}
          onPrev={zoom > 0 ? () => setZoom(zoom - 1) : undefined}
          onNext={zoom < poses.length - 1 ? () => setZoom(zoom + 1) : undefined}
          onClose={() => setZoom(null)}
        />
      )}
    </details>
  )
}
