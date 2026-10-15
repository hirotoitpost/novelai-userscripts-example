import { useEffect, useRef, useState } from 'react'
import { apiFetch } from '../api'
import { useLocalStorage } from '../hooks/useLocalStorage'
import './WholeEnhance.css'

/**
 * ギャラリーの画像全体の画質を上げる(/api/image/enhance)。拡大表示の範囲を高解像度にする RegionRedrawTool とは別に、
 * 画像の画面に置く。
 * - AI拡大(全体): この PC で2倍か4倍にする。絵の中身は変わらない(NovelAI を使わない)
 * - 描き直し(全体): 1MP 以内で NovelAI の img2img で弱めに描き直してから、AI 拡大する(Opus なら Anlas 0)
 * 時間がかかるので、処理は切り離して進み具合を見る。結果はギャラリーに新しい画像として残る(元の画像は変えない)。
 */

type Mode = 'upscale' | 'redraw'

interface Plan {
  width: number
  height: number
  /** 倍率 → 作れる大きさ [幅, 高さ]、作れなければその理由 */
  upscale: Record<string, [number, number] | string>
  redraw_size: [number, number]
  suggestion: string
}

interface Job {
  id: string
  status: 'running' | 'done' | 'error'
  message: string
  progress: number
  total: number
  result: { key: string; width: number; height: number } | null
}

const STRENGTHS = [
  { value: 0.25, label: '控えめ' },
  { value: 0.35, label: 'ふつう' },
  { value: 0.5, label: '強め' },
]

interface Props {
  token: string
  imageKey: string
  /** できた画像を開く */
  onOpen: (key: string) => void
}

export default function WholeEnhance({ token, imageKey, onOpen }: Props) {
  const [open, setOpen] = useState(false)
  const [plan, setPlan] = useState<Plan | null>(null)
  const [mode, setMode] = useLocalStorage<Mode>('nai_whole_enhance_mode', 'upscale')
  const [scale, setScale] = useLocalStorage<2 | 4>('nai_whole_enhance_scale', 2)
  const [strength, setStrength] = useLocalStorage('nai_whole_enhance_strength', 0.35)
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | null>(null)

  useEffect(() => {
    if (!open || plan) return
    apiFetch<Plan>(token, `/api/image/enhance/plan?key=${encodeURIComponent(imageKey)}`)
      .then(setPlan)
      .catch(e => setError(e instanceof Error ? e.message : String(e)))
  }, [open, plan, token, imageKey])

  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current) }, [])

  const follow = (id: string) => {
    apiFetch<Job>(token, `/api/image/enhance/jobs/${id}`)
      .then(next => {
        setJob(next)
        if (next.status === 'running') timer.current = window.setTimeout(() => follow(id), 1500)
      })
      .catch(e => setError(e instanceof Error ? e.message : String(e)))
  }

  const start = async () => {
    setError(null)
    try {
      const started = await apiFetch<Job>(token, '/api/image/enhance', { key: imageKey, mode, scale, strength })
      setJob(started)
      follow(started.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const target = plan?.upscale[String(scale)]
  const blocked = mode === 'upscale' && typeof target === 'string'
  const running = job?.status === 'running'
  const minutes = plan ? Math.max(1, Math.round((plan.width * plan.height) / (1024 * 1024) * 2)) : null
  return (
    <details className="whole-enhance" open={open} onToggle={e => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>画質を上げる(画像全体)</summary>
      {plan && (
        <p className="whole-enhance-hint">
          この画像: {plan.width}×{plan.height}。{plan.suggestion}
        </p>
      )}
      <div className="whole-enhance-row" role="radiogroup" aria-label="画質を上げる方法">
        <label>
          <input type="radio" name={`whole-mode-${imageKey}`} checked={mode === 'upscale'} onChange={() => setMode('upscale')} disabled={running} />
          {' '}AI拡大(絵は変わらない・Anlas なし)
        </label>
        <label>
          <input type="radio" name={`whole-mode-${imageKey}`} checked={mode === 'redraw'} onChange={() => setMode('redraw')} disabled={running} />
          {' '}描き直し(線の崩れ・ノイズを直す・Opus なら Anlas 0)
        </label>
      </div>
      <div className="whole-enhance-row">
        <label>
          倍率{' '}
          <select value={scale} disabled={running} onChange={e => setScale(Number(e.target.value) as 2 | 4)}>
            <option value={2}>2倍</option>
            <option value={4}>4倍</option>
          </select>
        </label>
        {mode === 'redraw' && (
          <label>
            描き直しの強さ{' '}
            <select value={strength} disabled={running} onChange={e => setStrength(Number(e.target.value))}>
              {STRENGTHS.map(s => <option key={s.value} value={s.value}>{s.label}({s.value})</option>)}
            </select>
          </label>
        )}
      </div>
      {plan && (
        <p className="whole-enhance-hint">
          {mode === 'upscale'
            ? typeof target === 'string' ? target : `できる大きさ: ${target?.[0]}×${target?.[1]}(長い辺は 4096px まで)。この PC で ${minutes}〜${minutes! * 2}分ほど`
            : `${plan.redraw_size[0]}×${plan.redraw_size[1]} で描き直してから、${typeof target === 'string' ? '大きくはしません(十分大きい画像)' : `${target?.[0]}×${target?.[1]} まで AI 拡大`}します`}
        </p>
      )}
      <button type="button" className="whole-enhance-run" disabled={!plan || running || blocked} onClick={() => void start()}>
        {running ? '処理中…' : '画質を上げる'}
      </button>
      {job && (
        <div className="whole-enhance-status" role="status">
          {running && <progress max={job.total} value={job.progress} />}
          <span>{job.message}</span>
          {job.status === 'done' && job.result && (
            <button type="button" onClick={() => onOpen(job.result!.key)}>できた画像を開く</button>
          )}
        </div>
      )}
      {error && <p className="whole-enhance-error" role="alert">{error}</p>}
    </details>
  )
}
