import { useState } from 'react'
import { apiFetch } from '../api'
import { useLocalStorage } from '../hooks/useLocalStorage'
import type { Region } from './FullscreenViewer'
import './RegionRedrawTool.css'

/**
 * 全画面の拡大表示で見ている範囲を、高解像度にする(/api/image/region-redraw)。
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
  /** できた画像を開く */
  onOpen: (key: string) => void
}

export default function RegionRedrawTool({ token, imageKey, region, zoomed, onOpen }: Props) {
  const [mode, setMode] = useLocalStorage<Mode>('nai_region_redraw_mode', 'normal')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<Result | null>(null)

  if (!zoomed && !busy && !result && !error) return null

  const run = async () => {
    const box = region()
    if (!box) return
    setBusy(true)
    setError(null)
    setResult(null)
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

  return (
    <div className="region-tool">
      {zoomed && !busy && (
        <>
          <div className="region-tool-modes" role="group" aria-label="高解像度にする方法">
            {MODES.map(m => (
              <button key={m.value} type="button" title={m.title} aria-pressed={mode === m.value}
                className={mode === m.value ? 'is-on' : undefined} onClick={() => setMode(m.value)}>
                {m.label}
              </button>
            ))}
          </div>
          <button type="button" className="region-tool-run" onClick={() => void run()}
            title="いま見えている範囲を切り出して高解像度にします。結果はギャラリーに新しい画像として残ります">
            この範囲を高解像度に
          </button>
        </>
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
