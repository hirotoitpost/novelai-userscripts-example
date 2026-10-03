import { PointerEvent as ReactPointerEvent, useRef, useState } from 'react'

/** 合成したページ上の吹き出し/描き文字(座標はページのピクセル)。 */
export interface MangaV2Element {
  key: string
  kind: 'bubble' | 'sfx' | 'narration'
  text: string
  box: [number, number, number, number]
  panel: [number, number, number, number]
  moved: boolean
}

interface Props {
  imageUrl: string
  elements: MangaV2Element[]
  pageWidth: number
  pageHeight: number
  busy: boolean
  /** 動かした先(コマ内の左上位置。コマの幅・高さに対する割合) */
  onMove: (element: MangaV2Element, x: number, y: number) => void
  onReset: (element: MangaV2Element) => void
}

interface Drag {
  key: string
  startX: number
  startY: number
  dx: number
  dy: number
}

/**
 * 合成済みのページに、吹き出し・描き文字の枠を重ねてドラッグで動かせるようにする。
 * 離した位置を保存して合成し直すのは呼び出し側(onMove)の役目。
 * タッチでも動かせるよう Pointer Events を使い、枠では touch-action: none でスクロールを止める。
 */
export default function MangaV2PageEditor({ imageUrl, elements, pageWidth, pageHeight, busy, onMove, onReset }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [drag, setDrag] = useState<Drag | null>(null)

  const pct = (value: number, total: number) => `${(value / total) * 100}%`

  function onPointerDown(e: ReactPointerEvent<HTMLDivElement>, element: MangaV2Element) {
    if (busy) return
    e.currentTarget.setPointerCapture(e.pointerId)
    setDrag({ key: element.key, startX: e.clientX, startY: e.clientY, dx: 0, dy: 0 })
  }

  function onPointerMove(e: ReactPointerEvent<HTMLDivElement>) {
    if (!drag) return
    setDrag({ ...drag, dx: e.clientX - drag.startX, dy: e.clientY - drag.startY })
  }

  function onPointerUp(element: MangaV2Element) {
    const container = containerRef.current
    const current = drag
    setDrag(null)
    if (!container || !current || current.key !== element.key) return
    // 少し触れただけ(タップ)なら動かさない
    if (Math.abs(current.dx) < 4 && Math.abs(current.dy) < 4) return
    const scale = pageWidth / container.clientWidth
    const [px0, py0, px1, py1] = element.panel
    const left = element.box[0] + current.dx * scale
    const top = element.box[1] + current.dy * scale
    const clamp = (v: number) => Math.min(Math.max(v, 0), 1)
    onMove(element, clamp((left - px0) / (px1 - px0)), clamp((top - py0) / (py1 - py0)))
  }

  return (
    <div className="mv2-editor" ref={containerRef}>
      <img className="mv2-editor-page" src={imageUrl} alt="合成したページ" draggable={false} />
      {elements.map(element => {
        const [x0, y0, x1, y1] = element.box
        const dragging = drag?.key === element.key
        return (
          <div
            key={element.key}
            className={`mv2-handle mv2-handle--${element.kind}${element.moved ? ' mv2-handle--moved' : ''}${dragging ? ' mv2-handle--dragging' : ''}`}
            style={{
              left: pct(x0, pageWidth),
              top: pct(y0, pageHeight),
              width: pct(x1 - x0, pageWidth),
              height: pct(y1 - y0, pageHeight),
              transform: dragging ? `translate(${drag.dx}px, ${drag.dy}px)` : undefined,
            }}
            title={`${element.text}(ドラッグで移動・ダブルクリックで自動配置に戻す)`}
            onPointerDown={e => onPointerDown(e, element)}
            onPointerMove={onPointerMove}
            onPointerUp={() => onPointerUp(element)}
            onPointerCancel={() => setDrag(null)}
            onDoubleClick={() => { if (element.moved && !busy) onReset(element) }}
          />
        )
      })}
    </div>
  )
}
