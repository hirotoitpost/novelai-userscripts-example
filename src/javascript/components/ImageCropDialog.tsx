import { PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from 'react'
import './ImageCropDialog.css'

/**
 * 画像の切り抜き。範囲をドラッグで動かし、四隅で大きさを変える。縦横比は自由・正方形・縦長/横長(NovelAI の生成サイズの比)から。
 * 切り抜いた結果は元の画素のまま(縮めない)PNG の data URL にして返す。
 */

interface Props {
  src: string
  onCancel: () => void
  onCrop: (dataUrl: string) => void
  /** ダイアログの見出し */
  title?: string
  /** 注意書き(切り抜くと埋め込まれた情報が失われる、など) */
  note?: string
}

interface Rect { x: number; y: number; w: number; h: number }
type Corner = 'nw' | 'ne' | 'sw' | 'se'

const ASPECTS: { id: string; label: string; ratio: number | null }[] = [
  { id: 'free', label: '自由', ratio: null },
  { id: 'square', label: '正方形 1:1', ratio: 1 },
  { id: 'portrait', label: '縦長 832×1216', ratio: 832 / 1216 },
  { id: 'landscape', label: '横長 1216×832', ratio: 1216 / 832 },
]
const MIN = 16

/** rect の中に収まる、ratio の一番大きな範囲(真ん中に) */
function fitAspect(rect: Rect, ratio: number): Rect {
  let w = rect.w
  let h = w / ratio
  if (h > rect.h) { h = rect.h; w = h * ratio }
  return { x: rect.x + (rect.w - w) / 2, y: rect.y + (rect.h - h) / 2, w, h }
}

export default function ImageCropDialog({ src, onCancel, onCrop, title = '画像を切り抜く', note }: Props) {
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null)
  const [rect, setRect] = useState<Rect | null>(null)
  const [aspect, setAspect] = useState('free')
  const imgRef = useRef<HTMLImageElement>(null)
  const drag = useRef<{ kind: 'move' | Corner; start: { x: number; y: number }; rect: Rect } | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])

  function onLoad() {
    const img = imgRef.current
    if (!img) return
    setNatural({ w: img.naturalWidth, h: img.naturalHeight })
    setRect({ x: 0, y: 0, w: img.naturalWidth, h: img.naturalHeight })
  }

  /** 表示の1ピクセルが、元の画像の何ピクセルか */
  function scale(): number {
    const img = imgRef.current
    return img && natural ? natural.w / img.clientWidth : 1
  }

  function pointer(e: ReactPointerEvent): { x: number; y: number } {
    const box = imgRef.current!.getBoundingClientRect()
    const s = scale()
    return { x: (e.clientX - box.left) * s, y: (e.clientY - box.top) * s }
  }

  function chooseAspect(id: string) {
    setAspect(id)
    const ratio = ASPECTS.find(a => a.id === id)?.ratio
    if (ratio && rect) setRect(fitAspect(rect, ratio))
  }

  function start(kind: 'move' | Corner, e: ReactPointerEvent<HTMLElement>) {
    if (!rect) return
    e.preventDefault()
    e.stopPropagation()
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
    drag.current = { kind, start: pointer(e), rect }
  }

  function move(e: ReactPointerEvent) {
    const d = drag.current
    if (!d || !natural) return
    const p = pointer(e)
    const dx = p.x - d.start.x
    const dy = p.y - d.start.y
    const r = d.rect
    if (d.kind === 'move') {
      setRect({ ...r, x: Math.min(Math.max(r.x + dx, 0), natural.w - r.w), y: Math.min(Math.max(r.y + dy, 0), natural.h - r.h) })
      return
    }
    // 動かす角の向かいの角を止めて、大きさを変える
    const fixedX = d.kind.includes('w') ? r.x + r.w : r.x
    const fixedY = d.kind.includes('n') ? r.y + r.h : r.y
    let px = Math.min(Math.max(p.x, 0), natural.w)
    let py = Math.min(Math.max(p.y, 0), natural.h)
    let w = Math.max(Math.abs(px - fixedX), MIN)
    let h = Math.max(Math.abs(py - fixedY), MIN)
    const ratio = ASPECTS.find(a => a.id === aspect)?.ratio
    if (ratio) {
      // 縦横比を保つ: 動かした量の大きい方に合わせ、画像の外に出る分は縮める
      if (w / h > ratio) h = w / ratio
      else w = h * ratio
      const maxW = d.kind.includes('w') ? fixedX : natural.w - fixedX
      const maxH = d.kind.includes('n') ? fixedY : natural.h - fixedY
      const shrink = Math.min(1, maxW / w, maxH / h)
      w *= shrink
      h *= shrink
    }
    px = d.kind.includes('w') ? fixedX - w : fixedX
    py = d.kind.includes('n') ? fixedY - h : fixedY
    setRect({ x: px, y: py, w, h })
  }

  function crop() {
    const img = imgRef.current
    if (!img || !rect) return
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(rect.w))
    canvas.height = Math.max(1, Math.round(rect.h))
    canvas.getContext('2d')!.drawImage(img, rect.x, rect.y, rect.w, rect.h, 0, 0, canvas.width, canvas.height)
    onCrop(canvas.toDataURL('image/png'))
  }

  const s = natural ? scale() : 1
  const view = rect ? { left: rect.x / s, top: rect.y / s, width: rect.w / s, height: rect.h / s } : null

  return (
    <div className="icd-overlay" role="dialog" aria-modal="true" aria-label={title}>
      <div className="icd-panel">
        <h3>{title}</h3>
        <div className="icd-row">
          {ASPECTS.map(a => (
            <button key={a.id} type="button" className={aspect === a.id ? 'icd-chip icd-chip--on' : 'icd-chip'}
              onClick={() => chooseAspect(a.id)}>{a.label}</button>
          ))}
          {natural && (
            <button type="button" className="icd-chip" onClick={() => { setAspect('free'); setRect({ x: 0, y: 0, w: natural.w, h: natural.h }) }}>
              全体に戻す
            </button>
          )}
        </div>
        <div className="icd-stage" onPointerMove={move} onPointerUp={() => { drag.current = null }} onPointerLeave={() => { drag.current = null }}>
          <img ref={imgRef} src={src} alt="切り抜く画像" onLoad={onLoad} draggable={false} />
          {view && (
            <div className="icd-rect" style={view} onPointerDown={e => start('move', e)}>
              {(['nw', 'ne', 'sw', 'se'] as Corner[]).map(c => (
                <span key={c} className={`icd-handle icd-handle--${c}`} onPointerDown={e => start(c, e)} />
              ))}
            </div>
          )}
        </div>
        {rect && <p className="icd-note">切り抜く大きさ: {Math.round(rect.w)}×{Math.round(rect.h)} px(ドラッグで移動、四隅で大きさ)</p>}
        {note && <p className="icd-note">{note}</p>}
        <div className="icd-row icd-actions">
          <button type="button" onClick={onCancel}>やめる</button>
          <button type="button" className="icd-primary" disabled={!rect} onClick={crop}>切り抜く</button>
        </div>
      </div>
    </div>
  )
}
