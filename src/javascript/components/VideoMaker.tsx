import { useEffect, useState } from 'react'
import { API_ORIGIN, readErrorDetail } from '../library'
import './VideoMaker.css'

/**
 * ギャラリーの絵から Grok で短い動画を作る(/api/videos。裏で数分)。
 * できた動画はギャラリーの「動画」に並び、そのまま X に投稿できる(投稿するときに見えない透かしが入る)。
 */

interface ModelOption {
  id: string
  usd_per_second: number
}

interface Config {
  configured: boolean
  models: ModelOption[]
  default_model: string
  resolutions: string[]
  min_seconds: number
  max_seconds: number
  loop_models: string[]
  loop_resolutions: string[]
  default_fade: number
}

interface Job {
  id: string
  status: 'starting' | 'generating' | 'done' | 'failed'
  progress: number
  message: string
  video_key: string | null
  cost_usd: number | null
  estimate_usd?: number
}

const MODEL_LABEL: Record<string, string> = {
  'grok-imagine-video-1.5': '高画質(1.5)',
  'grok-imagine-video-1.5-lite': '安い(1.5 lite)',
  'grok-imagine-video': '前のモデル',
}

export default function VideoMaker({ imageKey, onOpen }: { imageKey: string; onOpen: (key: string) => void }) {
  const [config, setConfig] = useState<Config | null>(null)
  const [prompt, setPrompt] = useState('')
  const [keepStyle, setKeepStyle] = useState(true)
  const [model, setModel] = useState('')
  const [seconds, setSeconds] = useState(6)
  const [resolution, setResolution] = useState('720p')
  const [audio, setAudio] = useState(true)
  const [loop, setLoop] = useState(false)
  const [fade, setFade] = useState(1.5)
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    void (async () => {
      try {
        const res = await fetch(`${API_ORIGIN}/api/videos/config`)
        if (!res.ok) throw new Error(await readErrorDetail(res))
        const data: Config = await res.json()
        setConfig(data)
        setModel(data.default_model)
        setFade(data.default_fade)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    })()
  }, [])

  // 作っている間は 5 秒ごとに進み具合を見る
  useEffect(() => {
    if (!job || job.status === 'done' || job.status === 'failed') return
    const timer = window.setInterval(async () => {
      try {
        const res = await fetch(`${API_ORIGIN}/api/videos/jobs/${job.id}`)
        if (!res.ok) throw new Error(await readErrorDetail(res))
        const next: Job = await res.json()
        setJob(prev => ({ ...next, estimate_usd: prev?.estimate_usd }))
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    }, 5000)
    return () => window.clearInterval(timer)
  }, [job])

  // ループは最後のコマを指定できるモデル・解像度だけ
  function changeLoop(next: boolean) {
    setLoop(next)
    if (!next || !config) return
    if (!config.loop_models.includes(model)) setModel(config.loop_models[0])
    if (!config.loop_resolutions.includes(resolution)) setResolution(config.loop_resolutions[config.loop_resolutions.length - 1])
  }

  const price = config?.models.find(m => m.id === model)?.usd_per_second ?? 0
  const estimate = Math.round(price * seconds * 100) / 100
  const running = job !== null && (job.status === 'starting' || job.status === 'generating')

  async function start() {
    if (!prompt.trim()) {
      setError('どう動かすかを書いてください。')
      return
    }
    if (!window.confirm(`Grok で ${seconds} 秒の動画を作ります。料金の目安は約 $${estimate}${audio ? '(音声付きは少し高くなります)' : ''}。よろしいですか?`)) return
    setError(null)
    try {
      const res = await fetch(`${API_ORIGIN}/api/videos/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: imageKey, prompt, keep_style: keepStyle, model, seconds, resolution, audio, loop, audio_fade: fade }),
      })
      if (!res.ok) throw new Error(await readErrorDetail(res))
      setJob(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  if (config && !config.configured) {
    return <p className="vm-note">xAI のキー(XAI_API_KEY)が .env にありません。</p>
  }

  return (
    <div className="vm-root">
      <label className="vm-field">
        どう動かすか(英語のほうが伝わりやすい)
        <textarea
          rows={3}
          value={prompt}
          onChange={e => setPrompt(e.target.value)}
          placeholder="例: She walks slowly toward the camera holding her umbrella. Ripples spread in the puddles. Static camera."
          disabled={running}
        />
      </label>
      <p className="vm-note">
        髪型など、動かしたくない所は「〜はそのまま」と書いてください(例: her side ponytail stays over her shoulder)。
      </p>
      <label className="vm-check">
        <input type="checkbox" checked={keepStyle} onChange={e => setKeepStyle(e.target.checked)} disabled={running} />
        絵柄・色・顔・髪型を崩さないよう一言足す
      </label>
      <label className="vm-check">
        <input type="checkbox" checked={loop} onChange={e => changeLoop(e.target.checked)} disabled={running} />
        ループにする(最後のコマも同じ絵にして、終わりが始まりにつながる)
      </label>
      {loop && (
        <p className="vm-note">
          高画質(1.5)・720p までになります。どう動かすかには「最後は最初と同じ姿勢に戻る」(returns to exactly the same pose as the first frame)と書くと自然です。
        </p>
      )}
      <div className="vm-row">
        <label>
          モデル
          <select value={model} onChange={e => setModel(e.target.value)} disabled={running}>
            {config?.models.filter(m => !loop || config.loop_models.includes(m.id)).map(m => (
              <option key={m.id} value={m.id}>{MODEL_LABEL[m.id] ?? m.id}(1秒 ${m.usd_per_second})</option>
            ))}
          </select>
        </label>
        <label>
          長さ
          <input type="number" min={config?.min_seconds ?? 1} max={config?.max_seconds ?? 15} value={seconds}
            onChange={e => setSeconds(Math.max(1, Math.min(15, Number(e.target.value) || 1)))} disabled={running} /> 秒
        </label>
        <label>
          解像度
          <select value={resolution} onChange={e => setResolution(e.target.value)} disabled={running}>
            {config?.resolutions.filter(r => !loop || config.loop_resolutions.includes(r)).map(r => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
        <label className="vm-check">
          <input type="checkbox" checked={audio} onChange={e => setAudio(e.target.checked)} disabled={running} />
          音声
        </label>
        {loop && audio && (
          <label title="音のつなぎ目をなじませる長さ。できたあとでも、動画の画面で変えられます">
            音のフェード
            <input type="number" min={0} max={3} step={0.1} value={fade}
              onChange={e => setFade(Math.max(0, Math.min(3, Number(e.target.value) || 0)))} disabled={running} /> 秒
          </label>
        )}
      </div>
      <div className="vm-row">
        <button type="button" className="vm-primary" onClick={() => void start()} disabled={!config || running}>
          {running ? '作っています…' : `動画を作る(約 $${estimate})`}
        </button>
        {running && <span className="vm-note">数分かかります。{job?.progress ? `${job.progress}%` : ''}この画面を閉じても続きます。</span>}
      </div>
      {job?.status === 'done' && job.video_key && (
        <p className="vm-done">
          できました{job.cost_usd !== null ? `(費用 $${job.cost_usd})` : ''}。{' '}
          <button type="button" onClick={() => onOpen(job.video_key!)}>できた動画を開く</button>
        </p>
      )}
      {job?.status === 'done' && job.message && <p className="vm-note">{job.message}</p>}
      {job?.status === 'failed' && <p className="vm-error">{job.message}</p>}
      {error && <p className="vm-error">{error}</p>}
    </div>
  )
}
