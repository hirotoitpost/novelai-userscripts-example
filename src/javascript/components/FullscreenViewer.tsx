import { useCallback, useEffect, useRef, useState } from 'react'
import './FullscreenViewer.css'

interface Props {
  src: string
  alt: string
  /** 画面の隅に出す位置(「3 / 12」など) */
  counter?: string
  /** 前へ・次へ。進めないときは undefined */
  onPrev?: () => void
  onNext?: () => void
  /** 右から左へ読む(漫画)。左端・右へのスワイプ・← が「次」になる */
  rtl?: boolean
  onClose: () => void
}

/** これだけ操作が無ければ、閉じるボタンなどを隠す */
const HIDE_CONTROLS_MS = 2500
/** ズームしていないとき、これ以上なぞればページを切り替える */
const SWIPE_THRESHOLD = 50
/** ズーム中は、絵の端からさらに押し出した量が画面の幅のこの割合を超えたらページを切り替える */
const ZOOMED_TURN_RATIO = 0.25
/** これ以下の動きならタップとみなす */
const TAP_SLOP = 10
/** 2回のタップがこの時間内ならダブルタップ(ズームの切り替え) */
const DOUBLE_TAP_MS = 260
const MIN_SCALE = 1
const MAX_SCALE = 5
const DOUBLE_TAP_SCALE = 2.5

interface View {
  scale: number
  x: number
  y: number
}

const FIT: View = { scale: 1, x: 0, y: 0 }

/**
 * 画像1枚を画面いっぱいに出す。ブラウザの全画面(Fullscreen API)が使えればそれを使い、
 * 使えない環境(iPhone の Safari は要素の全画面に未対応)ではページの上に画面いっぱいで重ねる。
 *
 * - 前後: 左右の端をタップ・スワイプ・矢印キー。真ん中のタップで操作の表示を出し入れ、Esc で閉じる
 * - ズーム: ピンチ・ダブルタップ(ダブルクリック)・ホイール・+/-/0 キー。ズーム中のドラッグは絵の中の移動で、
 *   絵の端からさらに画面の幅の 1/4 以上押し出したときだけページを切り替える(少しのスワイプでは変わらない)
 */
export default function FullscreenViewer({ src, alt, counter, onPrev, onNext, rtl = false, onClose }: Props) {
  const rootRef = useRef<HTMLDivElement>(null)
  const imageRef = useRef<HTMLImageElement>(null)
  const [controls, setControls] = useState(true)
  const [view, setView] = useState<View>(FIT)
  const [dragging, setDragging] = useState(false)
  // ズーム中に絵の端から押し出している量(ページ切り替えの手前を見せる)
  const [pull, setPull] = useState(0)
  const viewRef = useRef<View>(FIT)
  const hideTimer = useRef<number | null>(null)
  const tapTimer = useRef<number | null>(null)
  const lastTap = useRef<{ time: number; x: number; y: number } | null>(null)
  // 押している指(ポインター)の位置
  const pointers = useRef(new Map<number, { x: number; y: number }>())
  const gesture = useRef<{
    startX: number
    startY: number
    startView: View
    moved: boolean
    pinchDistance: number | null
    pinchCenter: { x: number; y: number } | null
    overflow: number
  } | null>(null)
  // 左右の操作を、読む向きに合わせて前後にする
  const left = rtl ? onNext : onPrev
  const right = rtl ? onPrev : onNext

  const applyView = useCallback((next: View) => {
    viewRef.current = next
    setView(next)
  }, [])

  const showControls = useCallback(() => {
    setControls(true)
    if (hideTimer.current !== null) window.clearTimeout(hideTimer.current)
    hideTimer.current = window.setTimeout(() => setControls(false), HIDE_CONTROLS_MS)
  }, [])

  // 別の絵に変わったら、ズームを戻す
  useEffect(() => {
    applyView(FIT)
    setPull(0)
  }, [src, applyView])

  // 開いたらブラウザの全画面にする。Esc などでブラウザの全画面が終わったら、こちらも閉じる
  useEffect(() => {
    const element = rootRef.current
    if (element && document.fullscreenEnabled && !document.fullscreenElement) {
      element.requestFullscreen().catch(() => {/* 断られたら、重ねて出すだけにする */})
    }
    const onChange = () => {
      if (!document.fullscreenElement) onClose()
    }
    document.addEventListener('fullscreenchange', onChange)
    showControls()
    return () => {
      document.removeEventListener('fullscreenchange', onChange)
      if (hideTimer.current !== null) window.clearTimeout(hideTimer.current)
      if (tapTimer.current !== null) window.clearTimeout(tapTimer.current)
      if (document.fullscreenElement) void document.exitFullscreen().catch(() => {})
    }
    // 開いたときに1回だけ
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /** 拡大した絵が画面からはみ出す量(移動できる範囲)。 */
  const limits = useCallback((scale: number) => {
    const image = imageRef.current
    const root = rootRef.current
    if (!image || !root) return { x: 0, y: 0 }
    return {
      x: Math.max(0, (image.offsetWidth * scale - root.clientWidth) / 2),
      y: Math.max(0, (image.offsetHeight * scale - root.clientHeight) / 2),
    }
  }, [])

  const clamp = useCallback((next: View): View => {
    const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, next.scale))
    if (scale <= MIN_SCALE) return FIT
    const { x, y } = limits(scale)
    return { scale, x: Math.min(x, Math.max(-x, next.x)), y: Math.min(y, Math.max(-y, next.y)) }
  }, [limits])

  /** 画面の点 (px, py) を動かさずに、倍率を scale にする。 */
  const zoomAt = useCallback((scale: number, px: number, py: number, from: View = viewRef.current) => {
    const root = rootRef.current
    if (!root) return
    const rect = root.getBoundingClientRect()
    // 画面の中心からの位置
    const cx = px - rect.left - rect.width / 2
    const cy = py - rect.top - rect.height / 2
    const ratio = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale)) / from.scale
    applyView(clamp({ scale: from.scale * ratio, x: cx - (cx - from.x) * ratio, y: cy - (cy - from.y) * ratio }))
  }, [applyView, clamp])

  const toggleZoom = useCallback((px: number, py: number) => {
    if (viewRef.current.scale > MIN_SCALE) applyView(FIT)
    else zoomAt(DOUBLE_TAP_SCALE, px, py)
  }, [applyView, zoomAt])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const root = rootRef.current?.getBoundingClientRect()
      const cx = root ? root.left + root.width / 2 : 0
      const cy = root ? root.top + root.height / 2 : 0
      if (e.key === 'Escape') {
        // ズーム中はまずズームを戻す
        if (viewRef.current.scale > MIN_SCALE) applyView(FIT)
        else onClose()
      } else if (e.key === 'ArrowLeft') left?.()
      else if (e.key === 'ArrowRight') right?.()
      else if (e.key === '+' || e.key === '=') zoomAt(viewRef.current.scale * 1.5, cx, cy)
      else if (e.key === '-') zoomAt(viewRef.current.scale / 1.5, cx, cy)
      else if (e.key === '0') applyView(FIT)
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [left, right, onClose, zoomAt, applyView])

  // ホイールでズーム(ページのスクロールにしないよう passive: false で受ける)
  useEffect(() => {
    const root = rootRef.current
    if (!root) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      zoomAt(viewRef.current.scale * Math.exp(-e.deltaY * 0.0015), e.clientX, e.clientY)
      showControls()
    }
    root.addEventListener('wheel', onWheel, { passive: false })
    return () => root.removeEventListener('wheel', onWheel)
  }, [zoomAt, showControls])

  /** タップ: ズームしていなければ左右の端で前後、真ん中で操作の表示。ズーム中はどこでも操作の表示 */
  const tap = useCallback((x: number) => {
    const root = rootRef.current
    if (!root) return
    const rect = root.getBoundingClientRect()
    const at = (x - rect.left) / rect.width
    if (viewRef.current.scale <= MIN_SCALE && at < 0.3) left?.()
    else if (viewRef.current.scale <= MIN_SCALE && at > 0.7) right?.()
    else if (controls) setControls(false)
    else showControls()
  }, [left, right, controls, showControls])

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest('.fsv-bar')) return
    try {
      e.currentTarget.setPointerCapture(e.pointerId)
    } catch {
      // 捕まえられないポインター(合成したイベントなど)でも、動きは受け取れる
    }
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    const points = [...pointers.current.values()]
    if (points.length === 2) {
      const [a, b] = points
      gesture.current = {
        startX: (a.x + b.x) / 2,
        startY: (a.y + b.y) / 2,
        startView: viewRef.current,
        moved: true,
        pinchDistance: Math.hypot(a.x - b.x, a.y - b.y),
        pinchCenter: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 },
        overflow: 0,
      }
    } else if (points.length === 1) {
      gesture.current = {
        startX: e.clientX,
        startY: e.clientY,
        startView: viewRef.current,
        moved: false,
        pinchDistance: null,
        pinchCenter: null,
        overflow: 0,
      }
    }
    setDragging(true)
  }

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!pointers.current.has(e.pointerId) || !gesture.current) return
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY })
    const g = gesture.current
    const points = [...pointers.current.values()]
    if (points.length >= 2 && g.pinchDistance && g.pinchCenter) {
      // ピンチ: 2本の指の中点を中心に拡大・縮小し、中点の動きで移動する
      const [a, b] = points
      const distance = Math.hypot(a.x - b.x, a.y - b.y)
      const center = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
      const scaled = { ...g.startView, x: g.startView.x + center.x - g.pinchCenter.x, y: g.startView.y + center.y - g.pinchCenter.y }
      zoomAt(g.startView.scale * (distance / g.pinchDistance), center.x, center.y, scaled)
      return
    }
    const dx = e.clientX - g.startX
    const dy = e.clientY - g.startY
    if (Math.hypot(dx, dy) > TAP_SLOP) g.moved = true
    if (g.startView.scale > MIN_SCALE) {
      // ズーム中: 絵の中を動かす。端で止まった分は「押し出した量」として数える
      const wanted = { ...g.startView, x: g.startView.x + dx, y: g.startView.y + dy }
      const placed = clamp(wanted)
      applyView(placed)
      g.overflow = wanted.x - placed.x
      setPull(g.overflow)
    } else {
      setPull(dx)
    }
  }

  const finishGesture = (e: React.PointerEvent<HTMLDivElement>) => {
    pointers.current.delete(e.pointerId)
    const g = gesture.current
    if (pointers.current.size > 0) {
      // ピンチの片方の指が離れたら、残った指でのドラッグとして続ける
      const [rest] = [...pointers.current.values()]
      gesture.current = g && {
        ...g,
        startX: rest.x,
        startY: rest.y,
        startView: viewRef.current,
        pinchDistance: null,
        pinchCenter: null,
        overflow: 0,
      }
      return
    }
    gesture.current = null
    setDragging(false)
    setPull(0)
    if (!g) return
    if (!g.moved) {
      // タップ。ダブルタップならズームを切り替え、1回なら少し待ってからタップの操作
      const now = Date.now()
      const previous = lastTap.current
      if (previous && now - previous.time < DOUBLE_TAP_MS && Math.hypot(e.clientX - previous.x, e.clientY - previous.y) < 40) {
        if (tapTimer.current !== null) window.clearTimeout(tapTimer.current)
        lastTap.current = null
        toggleZoom(e.clientX, e.clientY)
        return
      }
      lastTap.current = { time: now, x: e.clientX, y: e.clientY }
      const x = e.clientX
      tapTimer.current = window.setTimeout(() => tap(x), DOUBLE_TAP_MS)
      return
    }
    if (g.pinchDistance !== null) return
    const width = rootRef.current?.clientWidth ?? window.innerWidth
    const dx = e.clientX - g.startX
    if (g.startView.scale > MIN_SCALE) {
      // ズーム中: 端からの押し出しが大きいときだけページを切り替える(指を左へ = 右側の絵へ)
      if (Math.abs(g.overflow) > width * ZOOMED_TURN_RATIO) (g.overflow < 0 ? right : left)?.()
    } else if (Math.abs(dx) > SWIPE_THRESHOLD && Math.abs(dx) > Math.abs(e.clientY - g.startY)) {
      ;(dx < 0 ? right : left)?.()
    }
  }

  const zoomed = view.scale > MIN_SCALE
  const width = rootRef.current?.clientWidth ?? window.innerWidth
  // ページ切り替えの手前まで押し出しているか(ズーム中に端で押し出したときの目印)
  const pullRatio = zoomed ? Math.min(1, Math.abs(pull) / (width * ZOOMED_TURN_RATIO)) : 0

  return (
    <div
      ref={rootRef}
      className={`fsv${controls ? '' : ' fsv--hidden'}${zoomed ? ' fsv--zoomed' : ''}`}
      role="dialog"
      aria-modal="true"
      aria-label={`${alt}(全画面)`}
      onMouseMove={showControls}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={finishGesture}
      onPointerCancel={finishGesture}
      onDoubleClick={e => {
        // マウスのダブルクリック(タッチはダブルタップで扱う)
        if ((e.target as HTMLElement).closest('.fsv-bar')) return
        e.preventDefault()
      }}
    >
      <img
        ref={imageRef}
        className={`fsv-image${dragging ? ' fsv-image--dragging' : ''}`}
        src={src}
        alt={alt}
        draggable={false}
        style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})` }}
      />
      {pullRatio > 0 && (
        <div
          className={`fsv-pull fsv-pull--${pull < 0 ? 'right' : 'left'}${pullRatio >= 1 ? ' fsv-pull--ready' : ''}`}
          style={{ opacity: pullRatio }}
          aria-hidden
        >
          {pull < 0 ? (rtl ? '前へ' : '次へ') : (rtl ? '次へ' : '前へ')}
        </div>
      )}
      <div className="fsv-bar">
        {counter && <span className="fsv-counter">{counter}</span>}
        {zoomed && (
          <button type="button" className="fsv-close" onClick={() => applyView(FIT)}>
            {Math.round(view.scale * 100)}% ・ 元の大きさ
          </button>
        )}
        <button type="button" className="fsv-close" onClick={onClose}>全画面を終わる ✕</button>
      </div>
      {/* 画面読み上げ・キーボード向けの前後のボタン(見た目には出さない) */}
      <div className="fsv-sr">
        <button type="button" onClick={() => onPrev?.()} disabled={!onPrev}>前へ</button>
        <button type="button" onClick={() => onNext?.()} disabled={!onNext}>次へ</button>
      </div>
    </div>
  )
}
