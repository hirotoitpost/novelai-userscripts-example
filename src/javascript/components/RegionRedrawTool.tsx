import { useRef, useState } from 'react'
import { apiFetch } from '../api'
import { useLocalStorage } from '../hooks/useLocalStorage'
import type { ImageGeometry, Region, ScreenRect } from './FullscreenViewer'
import { ASPECTS, fitRect, resizeRect, type Handle } from '../cropRect'
import './RegionRedrawTool.css'

/**
 * 全画面の拡大表示で、絵の一部を高解像度にする(/api/image/region-redraw)。範囲は2通り。
 * - ズームして、いま見えている範囲
 * - 「範囲を選ぶ」で、枠をドラッグして決めた範囲(クロップ)。縦横比を 自由 / 1:1 / 2:3 などから選べる
 * 高解像度にする方法:
 * - 描き直し: この PC で AI 拡大してから、NovelAI の img2img で弱めに描き直す(Opus なら Anlas 0)
 * - AI 拡大だけ: NovelAI を使わない。絵の中身は変わらない
 * 結果はギャラリーに新しい画像として残る(元の画像は変えない)。
 */

type Mode = 'gentle' | 'normal' | 'strong' | 'upscale'

const MODES: { value: Mode; label: string; title: string }[] = [
  { value: 'gentle', label: '描き直し 控えめ', title: '強さ 0.25。元の絵にいちばん近いまま、線と細部を描き直します' },
  { value: 'normal', label: '描き直し ふつう', title: '強さ 0.35。線と塗りがきれいになります(おすすめ)' },
  { value: 'strong', label: '描き直し 強め', title: '強さ 0.5。細部が増えますが、髪や色が元と少し変わります' },
  { value: 'upscale', label: 'AI拡大だけ', title: 'NovelAI を使わず、この PC で4倍に拡大します。絵の中身は変わりません' },
]

const STRENGTH: Record<Exclude<Mode, 'upscale'>, number> = { gentle: 0.25, normal: 0.35, strong: 0.5 }

interface Result {
  key: string
  width: number
  height: number
}

interface Props {
  token: string
  imageKey: string
  region: () => Region | null
  zoomed: boolean
  /** 全画面の操作の表示が出ているか。隠れているときはメニューも隠す(範囲を選んでいる途中・作っている途中・結果・エラーの表示は残す) */
  visible: boolean
  /** 画面上の絵の位置(範囲を選ぶのに使う)。無ければ範囲は選べない */
  geometry?: ImageGeometry
  /** できた画像を開く */
  onOpen: (key: string) => void
}

export default function RegionRedrawTool({ token, imageKey, region, zoomed, visible, geometry, onOpen }: Props) {
  const [mode, setMode] = useLocalStorage<Mode>('nai_region_redraw_mode', 'normal')
  const [aspect, setAspect] = useLocalStorage<string>('nai_region_crop_aspect', 'free')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<Result | null>(null)
  // 範囲を選んでいる間の枠(画面の px)。null なら選んでいない
  const [crop, setCrop] = useState<ScreenRect | null>(null)
  const drag = useRef<{ handle: Handle; x: number; y: number; start: ScreenRect; bounds: ScreenRect } | null>(null)

  const ratio = ASPECTS.find(a => a.value === aspect)?.ratio ?? null
  const cropping = crop !== null
  const menu = !busy && (cropping || (visible && (zoomed || !!geometry)))
  if (!menu && !busy && !result && !error) return null

  const startCrop = () => {
    const bounds = geometry?.imageRect()
    if (!bounds) return
    // 画面に見えている絵の範囲の、真ん中あたり 6 割
    const view = {
      left: Math.max(bounds.left, 0),
      top: Math.max(bounds.top, 0),
      width: Math.min(bounds.left + bounds.width, window.innerWidth) - Math.max(bounds.left, 0),
      height: Math.min(bounds.top + bounds.height, window.innerHeight) - Math.max(bounds.top, 0),
    }
    const size = Math.min(view.width, view.height) * 0.6
    setCrop(fitRect(
      { left: view.left + (view.width - size) / 2, top: view.top + (view.height - size) / 2, width: size, height: size },
      view, ratio,
    ))
  }

  const chooseAspect = (value: string) => {
    setAspect(value)
    const bounds = geometry?.imageRect()
    if (crop && bounds) setCrop(fitRect(crop, bounds, ASPECTS.find(a => a.value === value)?.ratio ?? null))
  }

  const onPointerDown = (handle: Handle) => (e: React.PointerEvent) => {
    const bounds = geometry?.imageRect()
    if (!crop || !bounds) return
    e.stopPropagation()
    try {
      ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    } catch {
      // 捕まえられなくても動きは受け取れる
    }
    drag.current = { handle, x: e.clientX, y: e.clientY, start: crop, bounds }
  }

  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d) return
    const dx = e.clientX - d.x
    const dy = e.clientY - d.y
    if (d.handle === 'move') {
      setCrop(fitRect({ ...d.start, left: d.start.left + dx, top: d.start.top + dy }, d.bounds, null))
    } else {
      setCrop(resizeRect(d.start, d.handle, dx, dy, d.bounds, ratio))
    }
  }

  const onPointerUp = () => { drag.current = null }

  const run = async () => {
    const box = crop ? geometry?.toImageBox(crop) ?? null : region()
    if (!box) return
    setBusy(true)
    setError(null)
    setResult(null)
    setCrop(null)
    try {
      const body = mode === 'upscale'
        ? { key: imageKey, box, mode: 'upscale' }
        : { key: imageKey, box, mode: 'redraw', strength: STRENGTH[mode] }
      setResult(await apiFetch<Result>(token, '/api/image/region-redraw', body))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const pixels = crop ? geometry?.toImageBox(crop) : null

  return (
    <div className="region-tool">
      {crop && (
        <div className="region-crop-layer" onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}>
          <div
            className="region-crop-box"
            style={{ left: crop.left, top: crop.top, width: crop.width, height: crop.height }}
            onPointerDown={onPointerDown('move')}
          >
            {(['nw', 'ne', 'sw', 'se'] as const).map(h => (
              <span key={h} className={`region-crop-handle region-crop-handle--${h}`} onPointerDown={onPointerDown(h)} />
            ))}
            {pixels && <span className="region-crop-size">{pixels[2]}×{pixels[3]}</span>}
          </div>
        </div>
      )}
      {menu && (
        <div className="region-tool-menu">
          {(cropping || zoomed) && <div className="region-tool-modes" role="group" aria-label="高解像度にする方法">
            {MODES.map(m => (
              <button key={m.value} type="button" title={m.title} aria-pressed={mode === m.value}
                className={mode === m.value ? 'is-on' : undefined} onClick={() => setMode(m.value)}>
                {m.label}
              </button>
            ))}
          </div>}
          {cropping && (
            <div className="region-tool-modes" role="group" aria-label="範囲の縦横比">
              {ASPECTS.map(a => (
                <button key={a.value} type="button" aria-pressed={aspect === a.value}
                  className={aspect === a.value ? 'is-on' : undefined} onClick={() => chooseAspect(a.value)}>
                  {a.label}
                </button>
              ))}
            </div>
          )}
          <div className="region-tool-modes">
            {cropping ? (
              <>
                <button type="button" className="region-tool-run" onClick={() => void run()} disabled={!pixels}
                  title="枠で選んだ範囲を切り出して高解像度にします。結果はギャラリーに新しい画像として残ります">
                  選んだ範囲を高解像度に
                </button>
                <button type="button" onClick={() => setCrop(null)}>やめる</button>
              </>
            ) : (
              <>
                {geometry && (
                  <button type="button" onClick={startCrop}
                    title="枠をドラッグして範囲を決めます(角で大きさ、中で位置。縦横比は自由・1:1 などから選べます)">
                    範囲を選ぶ
                  </button>
                )}
                {zoomed && (
                  <button type="button" className="region-tool-run" onClick={() => void run()}
                    title="いま見えている範囲を切り出して高解像度にします。結果はギャラリーに新しい画像として残ります">
                    見えている範囲を高解像度に
                  </button>
                )}
              </>
            )}
          </div>
        </div>
      )}
      {busy && (
        <p className="region-tool-status" role="status">
          {mode === 'upscale' ? 'AI 拡大しています(30秒ほど)…' : 'AI 拡大してから描き直しています(1分ほど)…'}
        </p>
      )}
      {result && (
        <p className="region-tool-status" role="status">
          できました({result.width}×{result.height})
          <button type="button" onClick={() => onOpen(result.key)}>開く</button>
          <button type="button" onClick={() => setResult(null)} aria-label="閉じる">✕</button>
        </p>
      )}
      {error && (
        <p className="region-tool-status region-tool-error" role="alert">
          {error}
          <button type="button" onClick={() => setError(null)} aria-label="閉じる">✕</button>
        </p>
      )}
    </div>
  )
}
