import { PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from 'react'
import './OverlayEditor.css'

/**
 * コマの絵に重ねるもの(重ね)の編集。文字を単体で作って、または別のコマの絵を写真にして、四隅をドラッグした位置に
 * 合わせて貼る(拡大・縮小・回転・遠近の変形)。四隅はコマの絵に対する割合で保存し、合成のたびに貼る。
 */

type Quad = [number, number][]

export interface Overlay {
  kind: 'text' | 'panel'
  quad: Quad
  text?: string
  font?: string
  color?: string
  vertical?: boolean
  stroke_color?: string
  stroke_width?: number
  keep_aspect?: boolean
  scene_id?: number
  frame?: number
  opacity?: number
}

interface SceneOption {
  id: number
  label: string
}

interface Props {
  apiOrigin: string
  sceneId: number
  sceneLabel: string
  /** このコマの絵(生成した元の絵)の URL */
  imageUrl: string
  /** 写真にできる、絵のあるほかのシーン */
  photoScenes: SceneOption[]
  fonts: { id: string; label: string }[]
  onClose: () => void
}

const DEFAULT_QUAD: Quad = [[0.3, 0.3], [0.7, 0.3], [0.7, 0.55], [0.3, 0.55]]

function clamp(v: number): number {
  return Math.min(1.2, Math.max(-0.2, v))
}

export default function OverlayEditor({ apiOrigin, sceneId, sceneLabel, imageUrl, photoScenes, fonts, onClose }: Props) {
  const [overlays, setOverlays] = useState<Overlay[]>([])
  const [selected, setSelected] = useState<number | null>(null)
  const [previewKey, setPreviewKey] = useState(0)
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const stage = useRef<HTMLDivElement>(null)
  const drag = useRef<{ index: number; corner: number | null; start: [number, number]; quad: Quad } | null>(null)

  useEffect(() => {
    fetch(`${apiOrigin}/api/manga-v2/scenes/${sceneId}/overlays`)
      .then(r => (r.ok ? r.json() : []))
      .then((list: Overlay[]) => {
        setOverlays(list)
        setSelected(list.length ? 0 : null)
      })
      .catch(() => setOverlays([]))
  }, [apiOrigin, sceneId])

  function change(index: number, patch: Partial<Overlay>) {
    setOverlays(list => list.map((o, i) => (i === index ? { ...o, ...patch } : o)))
    setDirty(true)
  }

  function add(overlay: Overlay) {
    setOverlays(list => [...list, overlay])
    setSelected(overlays.length)
    setDirty(true)
  }

  function remove(index: number) {
    setOverlays(list => list.filter((_, i) => i !== index))
    setSelected(null)
    setDirty(true)
  }

  function point(e: ReactPointerEvent): [number, number] {
    const box = stage.current!.getBoundingClientRect()
    return [(e.clientX - box.left) / box.width, (e.clientY - box.top) / box.height]
  }

  function startDrag(index: number, corner: number | null, e: ReactPointerEvent<SVGElement>) {
    e.preventDefault()
    e.stopPropagation()
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
    setSelected(index)
    drag.current = { index, corner, start: point(e), quad: overlays[index].quad }
  }

  function moveDrag(e: ReactPointerEvent) {
    const d = drag.current
    if (!d) return
    const [x, y] = point(e)
    const dx = x - d.start[0]
    const dy = y - d.start[1]
    const quad: Quad = d.corner === null
      ? d.quad.map(([qx, qy]) => [clamp(qx + dx), clamp(qy + dy)])
      : d.quad.map((p, i) => (i === d.corner ? [clamp(x), clamp(y)] : p))
    change(d.index, { quad })
  }

  /** 選んだ重ねを、コマの絵の白い空白(写真立ての枠の中など)に合わせる。押すたびに、今の位置に近い順で次の空白へ */
  async function fitBlank(index: number) {
    setError(null)
    const res = await fetch(`${apiOrigin}/api/manga-v2/scenes/${sceneId}/blank-regions`)
    const regions: { quad: Quad; center: [number, number] }[] = res.ok ? await res.json() : []
    if (regions.length === 0) {
      setError('白い空白(枠の中)が見つかりませんでした。四隅をドラッグして合わせてください。')
      return
    }
    const quad = overlays[index].quad
    const cx = quad.reduce((a, [x]) => a + x, 0) / 4
    const cy = quad.reduce((a, [, y]) => a + y, 0) / 4
    const ranked = [...regions].sort((a, b) => Math.hypot(a.center[0] - cx, a.center[1] - cy) - Math.hypot(b.center[0] - cx, b.center[1] - cy))
    const same = (q: Quad) => q.every(([x, y], i) => Math.abs(x - quad[i][0]) < 0.005 && Math.abs(y - quad[i][1]) < 0.005)
    const at = ranked.findIndex(r => same(r.quad))
    change(index, { quad: ranked[at >= 0 ? (at + 1) % ranked.length : 0].quad })
  }

  async function save() {
    setError(null)
    const res = await fetch(`${apiOrigin}/api/manga-v2/scenes/${sceneId}/overlays`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ overlays }),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => null)
      setError(typeof data?.detail === 'string' ? data.detail : `保存できませんでした (HTTP ${res.status})`)
      return
    }
    setDirty(false)
    setPreviewKey(k => k + 1)
  }

  const current = selected !== null ? overlays[selected] : null

  return (
    <div className="ove-overlay" role="dialog" aria-modal="true" aria-label={`${sceneLabel}の重ね`}>
      <div className="ove-panel">
        <div className="ove-head">
          <h3>{sceneLabel}の重ね(文字・写真)</h3>
          <button type="button" onClick={onClose}>閉じる</button>
        </div>
        <p className="ove-note">
          四隅の丸をドラッグして、貼る場所に合わせます(写真立ての枠の四隅に合わせると、斜めでも嵌まります)。
          内側をドラッグすると全体が動きます。保存すると右に貼った結果が出て、次の合成から貼られます。
        </p>
        <div className="ove-body">
          <div className="ove-stage" ref={stage} onPointerMove={moveDrag} onPointerUp={() => { drag.current = null }}>
            <img src={imageUrl} alt={`${sceneLabel}の絵`} draggable={false} />
            <svg viewBox="0 0 1 1" preserveAspectRatio="none">
              {overlays.map((o, i) => (
                <g key={i}>
                  <polygon
                    points={o.quad.map(([x, y]) => `${x},${y}`).join(' ')}
                    className={i === selected ? 'ove-quad ove-quad--on' : 'ove-quad'}
                    onPointerDown={e => startDrag(i, null, e)}
                  />
                </g>
              ))}
            </svg>
            {current && current.quad.map(([x, y], corner) => (
              <svg key={corner} className="ove-handle" style={{ left: `${x * 100}%`, top: `${y * 100}%` }}
                viewBox="0 0 10 10" onPointerDown={e => startDrag(selected!, corner, e)}>
                <circle cx="5" cy="5" r="4" />
              </svg>
            ))}
          </div>
          <div className="ove-side">
            <img key={previewKey} className="ove-preview"
              src={`${apiOrigin}/api/manga-v2/scenes/${sceneId}/overlay-preview?max_size=600&v=${previewKey}`}
              alt="貼った結果" onError={e => { (e.target as HTMLImageElement).style.visibility = 'hidden' }} />
            <div className="ove-row">
              <button type="button" onClick={() => add({ kind: 'text', quad: DEFAULT_QUAD, text: '文字', vertical: false, keep_aspect: true })}>
                ＋ 文字
              </button>
              <button type="button" disabled={photoScenes.length === 0}
                onClick={() => add({ kind: 'panel', quad: [[0.35, 0.3], [0.65, 0.3], [0.65, 0.6], [0.35, 0.6]], scene_id: photoScenes[0]?.id, frame: 0.05 })}>
                ＋ 写真(別のコマの絵)
              </button>
            </div>
            <ul className="ove-list">
              {overlays.map((o, i) => (
                <li key={i}>
                  <button type="button" className={i === selected ? 'ove-chip ove-chip--on' : 'ove-chip'} onClick={() => setSelected(i)}>
                    {o.kind === 'text' ? `文字「${(o.text ?? '').slice(0, 8)}」` : `写真(${photoScenes.find(s => s.id === o.scene_id)?.label ?? '?'})`}
                  </button>
                </li>
              ))}
            </ul>
            {current && selected !== null && (
              <div className="ove-fields">
                {current.kind === 'text' ? (
                  <>
                    <label>文字(改行で列・行を分けます)
                      <textarea rows={2} value={current.text ?? ''} onChange={e => change(selected, { text: e.target.value })} />
                    </label>
                    <label>フォント
                      <select value={current.font ?? ''} onChange={e => change(selected, { font: e.target.value || undefined })}>
                        <option value="">セリフと同じ既定のフォント</option>
                        {fonts.map(f => <option key={f.id} value={f.id}>{f.label}</option>)}
                      </select>
                    </label>
                    <div className="ove-row">
                      <label>色 <input type="color" value={current.color ?? '#000000'} onChange={e => change(selected, { color: e.target.value })} /></label>
                      <label><input type="checkbox" checked={!!current.vertical} onChange={e => change(selected, { vertical: e.target.checked })} /> 縦書き</label>
                      <label><input type="checkbox" checked={current.keep_aspect !== false} onChange={e => change(selected, { keep_aspect: e.target.checked })} /> 文字の形を保つ</label>
                    </div>
                    <div className="ove-row">
                      <label>縁取り {Math.round((current.stroke_width ?? 0) * 100)}%
                        <input type="range" min={0} max={20} value={Math.round((current.stroke_width ?? 0) * 100)}
                          onChange={e => change(selected, { stroke_width: Number(e.target.value) / 100 })} />
                      </label>
                      <label>縁の色 <input type="color" value={current.stroke_color ?? '#ffffff'} onChange={e => change(selected, { stroke_color: e.target.value })} /></label>
                    </div>
                  </>
                ) : (
                  <>
                    <label>写真にするコマ
                      <select value={current.scene_id ?? ''} onChange={e => change(selected, { scene_id: Number(e.target.value) })}>
                        {photoScenes.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
                      </select>
                    </label>
                    <label>白い縁 {Math.round((current.frame ?? 0) * 100)}%
                      <input type="range" min={0} max={20} value={Math.round((current.frame ?? 0) * 100)}
                        onChange={e => change(selected, { frame: Number(e.target.value) / 100 })} />
                    </label>
                  </>
                )}
                <label>不透明度 {Math.round((current.opacity ?? 1) * 100)}%
                  <input type="range" min={10} max={100} value={Math.round((current.opacity ?? 1) * 100)}
                    onChange={e => change(selected, { opacity: Number(e.target.value) / 100 })} />
                </label>
                <div className="ove-row">
                  <button type="button" onClick={() => void fitBlank(selected)}
                    title="絵の中の白い空白(写真立て・額・紙の枠の中)を探して四隅を合わせます。もう一度押すと次の空白へ">
                    白い枠に合わせる
                  </button>
                  <button type="button" onClick={() => change(selected, { quad: DEFAULT_QUAD })}>位置を戻す</button>
                  <button type="button" className="ove-danger" onClick={() => remove(selected)}>この重ねを消す</button>
                </div>
              </div>
            )}
            <div className="ove-row">
              <button type="button" className="ove-primary" disabled={!dirty} onClick={() => void save()}>保存して結果を見る</button>
            </div>
            {error && <p className="ove-error" role="alert">{error}</p>}
          </div>
        </div>
      </div>
    </div>
  )
}
