import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { useLocalStorage } from '../hooks/useLocalStorage'
import FullscreenViewer from './FullscreenViewer'
import ShapeParts, { type ShapeInfo } from './ShapeParts'
import SmoothSpots, { DEFAULT_SMOOTH_RADIUS } from './SmoothSpots'
import './Model3D.css'

/**
 * 素体の 3D モデル(漫画のポーズの参考)。素体の正面・横・後ろの絵から、Colab の Hunyuan3D-2mv で形を作り、手元の Blender で
 * 整えて骨と人形の手を入れる(docs/model_3d_plan_jp.md)。ブラウザで回して見て動きを再生し、ポーズとカメラの向きを選んで
 * 参考の絵(灰色の陰影・線画風)を描き出す。形を作る仕事は数分かかるので、動いている間は状態を聞き直す。
 */

const Model3DViewer = lazy(() => import('./Model3DViewer'))

type CameraView = import('./Model3DViewer').CameraView
type Marker = import('./Model3DViewer').Marker

type JobState = 'queued' | 'running' | 'cleaning' | 'rigging' | 'posing' | 'previewing' | 'importing'

interface Job {
  status: 'running' | 'done' | 'error'
  remote: JobState | null
  error: string | null
  seconds: number
  /** 形の値の試しを作ったときの強さ */
  preview_weights?: Record<string, number> | null
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
    weights?: { nearest_bone: number; hand_cut: number; legs_split?: number; sculpted?: number }
  }
  /** Blender で直した所を取り込んだ結果 */
  blender_import?: { sculpt: number; keys: Record<string, number>; added: string[]; imported: number }
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
  /** 形状パーツ: 選べる形・保存した強さ・場面ごとの組み合わせ */
  shapes: ShapeInfo
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
  previewing: '形の値の試しを作っています(15 秒ほど)',
  importing: 'Blender で直した所を読んでいます',
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
  // 3D を画面の最前面に浮かせる(値を調整しながら見る)。位置は端末に残す
  const [floating, setFloating] = useState(false)
  const [floatPos, setFloatPos] = useLocalStorage('nai_model3d_float_pos', { right: 16, bottom: 16 })
  // 浮かせた窓で、形の値の試し(shape_preview.glb)を見ているか
  const [showPreview, setShowPreview] = useState(false)
  // 裸の素体のモデルで、乳首なしの写しを 3D に送るか(写しを描いている間は、作るボタンを止める)
  const [featureless, setFeatureless] = useState(true)
  const [drawingInputs, setDrawingInputs] = useState(false)
  // 3D のカメラの位置(読み込み直しても保つ。素体を替えたら初めから)
  const cameraView = useRef<CameraView | null>(null)
  const [animations, setAnimations] = useState<string[]>([])
  const [animation, setAnimation] = useState<string | null>(null)
  const [pose, setPose] = useState('stand')
  const [cameras, setCameras] = useState<string[]>(['front', 'three_quarter', 'side'])
  const [style, setStyle] = useState('shade')
  const [zoom, setZoom] = useState<number | null>(null)
  // 形状パーツの強さ(保存前の編集中。null なら保存した強さ)
  const [shapeDraft, setShapeDraft] = useState<Record<string, number> | null>(null)
  // ポーズの絵に使う形の組み合わせ(空ならいま保存している形)
  const [shapeSet, setShapeSet] = useState('')
  // ならす所(編集中。null なら保存したもの)と、3D の上のクリックで選んでいるか
  const [smoothDraft, setSmoothDraft] = useState<Marker[] | null>(null)
  const [picking, setPicking] = useState(false)

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
  useEffect(() => { cameraView.current = null }, [characterId, entry?.base_id])

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

  function toggleCamera(key: string) {
    setCameras(prev => (prev.includes(key) ? prev.filter(c => c !== key) : [...prev, key]))
  }

  const job = entry?.job
  const busy = disabled || job?.status === 'running'
  const meta = entry?.meta
  const modelSrc = entry?.files['model_rigged.glb'] ? fileUrl('model_rigged.glb') : entry?.files['model_clean.glb'] ? fileUrl('model_clean.glb') : null
  const previewReady = !!entry?.files['shape_preview.glb'] && !!entry?.job?.preview_weights && entry.job.status === 'done'
  const usingPreview = showPreview && previewReady
  const viewerSrc = usingPreview ? fileUrl('shape_preview.glb') : modelSrc
  const viewerShapes = usingPreview ? (entry?.job?.preview_weights ?? {}) : shapeWeights

  /** 浮かせた窓を、見出しをつかんで動かす(右下からの距離で持つ) */
  function dragFloat(e: React.PointerEvent<HTMLDivElement>) {
    const start = { x: e.clientX, y: e.clientY, ...floatPos }
    const move = (ev: PointerEvent) => setFloatPos({
      right: Math.max(0, start.right - (ev.clientX - start.x)),
      bottom: Math.max(0, start.bottom - (ev.clientY - start.y)),
    })
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  async function makeModel() {
    const drawing = featureless && entry?.kind === 'nude'
    setDrawingInputs(drawing)
    try {
      await post('', { seed, motion, featureless }, '作り始めました(数分かかります)')
    } finally {
      setDrawingInputs(false)
    }
  }

  useEffect(() => {
    setSmoothDraft(null)
    setPicking(false)
  }, [entry?.base_id])
  const smoothSpots = smoothDraft ?? entry?.shapes.smooth ?? []
  // 選んでいる間は、3D をクリックした所をならす所に足す
  const pickSpot = picking ? (at: Marker['at']) => setSmoothDraft([...smoothSpots, { at, radius: DEFAULT_SMOOTH_RADIUS }]) : undefined

  async function startSmoothPreview(spots: Marker[]) {
    setFloating(true)
    setViewing(true)
    setShowPreview(true)
    await post('/shape-preview', { weights: shapeWeights, smooth: spots }, 'ならす所の試しを作っています(15 秒ほど)')
  }

  async function startPreview(part: string, params: Record<string, number | string>, offsets?: Record<string, [number, number]>) {
    setFloating(true)
    setViewing(true)
    setShowPreview(true)
    await post('/shape-preview', { part, params, weights: shapeWeights, ...(offsets ? { offsets } : {}) }, '形の値の試しを作っています(15 秒ほど)')
  }
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
              {entry.kind === 'nude' && (
                <label className="fsh-label" title="乳首は部品で付けるので、正面・横の絵の乳首・乳輪だけを描き直した写しを 3D に送ります(乳首を描いた絵だと、陰影が胸の先の穴・平たさになります)。写しは元の絵が変わるまで使い回します">
                  <input type="checkbox" checked={featureless} disabled={busy || drawingInputs} onChange={e => setFeatureless(e.target.checked)} />
                  乳首なしの写しで作る(Anlas 2 枚ぶん)
                </label>
              )}
              <button type="button" className="lora-btn" disabled={busy || drawingInputs || !info.configured || !entry.views.front}
                onClick={() => void makeModel()}>
                {drawingInputs ? '乳首なしの写しを描いています…' : entry.files['model.glb'] ? '3D の形を作り直す' : '3D の形を作る'}
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
                  {viewing && (
                    <button type="button" className="lora-btn lora-btn--secondary" onClick={() => setFloating(f => !f)}
                      title="3D を画面の最前面に浮かせます。スクロールしても隠れないので、値を調整しながら見られます">
                      {floating ? 'ここに戻す' : '最前面に浮かせる'}
                    </button>
                  )}
                  {viewing && animations.length > 0 && (
                    <select className="lora-select" value={animation ?? ''} aria-label="再生する動き"
                      onChange={e => setAnimation(e.target.value || null)}>
                      <option value="">止める(立った姿勢)</option>
                      {animations.map(name => <option key={name} value={name}>{info.motions[name] ?? name}</option>)}
                    </select>
                  )}
                </div>
                {viewing && !floating && (
                  <Suspense fallback={<p className="lora-hint">読み込んでいます…</p>}>
                    <Model3DViewer key={viewerSrc} src={viewerSrc} animation={animation} onAnimations={setAnimations} shapes={viewerShapes} view={cameraView}
                      onPick={pickSpot} markers={usingPreview ? [] : smoothSpots} />
                  </Suspense>
                )}
                {viewing && floating && (
                  <div className="m3d-float" style={{ right: floatPos.right, bottom: floatPos.bottom }}>
                    <div className="m3d-float-head" onPointerDown={dragFloat} title="つかんで動かせます">
                      <span>{usingPreview ? '形の値の試し' : '3D'}</span>
                      {previewReady && (
                        <button type="button" className="lora-btn lora-btn--secondary" onPointerDown={e => e.stopPropagation()}
                          onClick={() => setShowPreview(p => !p)}>
                          {usingPreview ? 'モデル本体を見る' : '試しを見る'}
                        </button>
                      )}
                      <button type="button" className="lora-btn lora-btn--secondary" onPointerDown={e => e.stopPropagation()}
                        onClick={() => setFloating(false)} aria-label="浮かせた窓を閉じる">×</button>
                    </div>
                    {job?.status === 'running' && job.remote === 'previewing' && (
                      <p className="lora-hint">{JOB_LABELS.previewing}…({duration(job.seconds)})</p>
                    )}
                    <Suspense fallback={<p className="lora-hint">読み込んでいます…</p>}>
                      <Model3DViewer key={viewerSrc} src={viewerSrc} animation={animation} onAnimations={setAnimations} shapes={viewerShapes} view={cameraView}
                        onPick={usingPreview ? undefined : pickSpot} markers={usingPreview ? [] : smoothSpots} />
                    </Suspense>
                  </div>
                )}
              </div>
            )}

            {(stills.length > 0 || entry.files['preview.gif'] || entry.files['motion.gif'] || entry.files['input_front.png']) && (
              <div className="bdb-rig-views m3d-stills">
                {[...stills, ...(['input_front.png', 'input_left.png'] as const).filter(name => entry.files[name])].map(name => (
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
              <ShapeParts url={url} rootName={rootName} baseId={entry.base_id} shapes={entry.shapes} busy={busy}
                draft={shapeDraft} onDraft={setShapeDraft} onInfo={next => setInfo(next as Model3DInfo)}
                onRig={() => post('/rig', { motion }, '骨を入れ直しています(自分の形を入れます)')}
                onEditStart={() => { if (modelSrc) { setViewing(true); setFloating(true) } }}
                onPreview={(part, params, offsets) => void startPreview(part, params, offsets)} />
            )}

            {entry.files['model.blend'] && (
              <SmoothSpots url={url} rootName={rootName} baseId={entry.base_id} saved={entry.shapes.smooth ?? []}
                weights={entry.shapes.weights} busy={busy} draft={smoothDraft} onDraft={setSmoothDraft}
                picking={picking}
                onPicking={on => {
                  setPicking(on)
                  // 選ぶときは、モデル本体を最前面に浮かせて出す(動きは止める)
                  if (on && modelSrc) { setViewing(true); setFloating(true); setShowPreview(false); setAnimation(null) }
                }}
                onInfo={next => setInfo(next as Model3DInfo)}
                onRig={() => post('/rig', { motion }, '骨を入れ直しています(ならす所を入れます)')}
                onPreview={spots => { setPicking(false); void startSmoothPreview(spots) }} />
            )}

            {entry.files['model.blend'] && (
              <div className="m3d-blender">
                <h5>Blender で直す</h5>
                <p className="lora-hint">
                  Blender で開き、体を選んで、シェイプキーの「edit」を選んだまま彫って(スカルプト)直し、保存(Ctrl+S)してから
                  「Blender から取り込む」を押します。ほかのシェイプキー(胸の形など)を選んで彫ると、その形が直ります。
                  直した所は、骨を入れ直しても入ります(形を作り直すと消えます)。頂点を足したり減らしたりする直し方
                  (リメッシュ・ダイナミックトポロジー)は取り込めません。Blender で開いている間は、骨を入れ直さないでください
                  (開いているファイルが置き換わります)。
                </p>
                <div className="fsh-actions">
                  <button type="button" className="lora-btn lora-btn--secondary" disabled={busy}
                    onClick={() => void post('/open-blender', {}, 'Blender で開きました(Blender の窓を見てください)')}>Blender で開く</button>
                  <button type="button" className="lora-btn" disabled={busy}
                    onClick={() => void post('/import-blend', { motion }, 'Blender で直した所を取り込み、骨を入れ直しています')}>Blender から取り込む</button>
                </div>
                {meta?.blender_import && (
                  <p className="lora-hint">
                    取り込んだ所: 体 {meta.blender_import.sculpt} 頂点
                    {Object.keys(meta.blender_import.keys).length > 0 &&
                      `・シェイプキー ${Object.entries(meta.blender_import.keys).map(([k, n]) => `${k}(${n})`).join('・')}`}
                    {meta.blender_import.added.length > 0 &&
                      `(足したシェイプキー ${meta.blender_import.added.join('・')} は、まだ取り込めません)`}
                  </p>
                )}
              </div>
            )}

            {entry.files['model.blend'] && (
              <div className="m3d-poses">
                <h5>ポーズの参考の絵</h5>
                <div className="fsh-actions">
                  <select className="lora-select" value={pose} disabled={busy} aria-label="ポーズ" onChange={e => setPose(e.target.value)}>
                    {Object.entries(info.pose_options.poses).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                  </select>
                  {Object.keys(entry.shapes.sets).length > 0 && (
                    <select className="lora-select" value={shapeSet} disabled={busy} aria-label="形の組み合わせ" onChange={e => setShapeSet(e.target.value)}>
                      <option value="">形: いま保存している形</option>
                      {Object.keys(entry.shapes.sets).map(name => <option key={name} value={name}>形: {name}</option>)}
                    </select>
                  )}
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
                    onClick={() => void post('/pose', { pose, cameras, style, ...(shapeSet ? { shape_set: shapeSet } : {}) }, 'ポーズの絵を描いています')}>
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
