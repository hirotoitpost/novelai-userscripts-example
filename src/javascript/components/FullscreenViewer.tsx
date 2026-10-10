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
  /** 右から左へ読む(漫画)。左端・左へのスワイプ・← が「次」になる */
  rtl?: boolean
  onClose: () => void
}

/** これだけ操作が無ければ、閉じるボタンなどを隠す */
const HIDE_CONTROLS_MS = 2500
const SWIPE_THRESHOLD = 50

/**
 * 画像1枚を画面いっぱいに出す。ブラウザの全画面(Fullscreen API)が使えればそれを使い、
 * 使えない環境(iPhone の Safari は要素の全画面に未対応)ではページの上に画面いっぱいで重ねる。
 * 左右の端を押す・スワイプ・矢印キーで前後へ、真ん中を押すと操作の表示を出し入れ、Esc で閉じる。
 */
export default function FullscreenViewer({ src, alt, counter, onPrev, onNext, rtl = false, onClose }: Props) {
  const rootRef = useRef<HTMLDivElement>(null)
  const [controls, setControls] = useState(true)
  const hideTimer = useRef<number | null>(null)
  const touchStart = useRef<number | null>(null)
  // 左右の操作を、読む向きに合わせて前後にする
  const left = rtl ? onNext : onPrev
  const right = rtl ? onPrev : onNext

  const showControls = useCallback(() => {
    setControls(true)
    if (hideTimer.current !== null) window.clearTimeout(hideTimer.current)
    hideTimer.current = window.setTimeout(() => setControls(false), HIDE_CONTROLS_MS)
  }, [])

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
      if (document.fullscreenElement) void document.exitFullscreen().catch(() => {})
    }
    // 開いたときに1回だけ
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowLeft') left?.()
      else if (e.key === 'ArrowRight') right?.()
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [left, right, onClose])

  return (
    <div
      ref={rootRef}
      className={`fsv${controls ? '' : ' fsv--hidden'}`}
      role="dialog"
      aria-modal="true"
      aria-label={`${alt}(全画面)`}
      onMouseMove={showControls}
      onTouchStart={e => { touchStart.current = e.touches[0].clientX }}
      onTouchEnd={e => {
        if (touchStart.current === null) return
        const dx = e.changedTouches[0].clientX - touchStart.current
        touchStart.current = null
        // 指を左へなぞると、右側の次の絵が出てくる(漫画は右へなぞると次)
        if (Math.abs(dx) > SWIPE_THRESHOLD) (dx < 0 ? right : left)?.()
      }}
    >
      <img className="fsv-image" src={src} alt={alt} />
      <button type="button" className="fsv-zone fsv-zone--left" onClick={() => left?.()} disabled={!left}
        aria-label={rtl ? '次へ' : '前へ'} />
      <button type="button" className="fsv-zone fsv-zone--center" onClick={() => (controls ? setControls(false) : showControls())}
        aria-label="操作の表示を切り替える" />
      <button type="button" className="fsv-zone fsv-zone--right" onClick={() => right?.()} disabled={!right}
        aria-label={rtl ? '前へ' : '次へ'} />
      <div className="fsv-bar">
        {counter && <span className="fsv-counter">{counter}</span>}
        <button type="button" className="fsv-close" onClick={onClose}>全画面を終わる ✕</button>
      </div>
    </div>
  )
}
