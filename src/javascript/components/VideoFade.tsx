import { useState } from 'react'
import { API_ORIGIN, readErrorDetail } from '../library'
import './VideoMaker.css'

/**
 * 動画の音のフェード(始まりでだんだん大きく、終わりでだんだん小さく)をかけ直す(/api/videos/fade)。
 * ループ動画の音のつなぎ目をなじませる。元の音から作り直すので、何度変えてもよい(0 で元の音)。
 */
export default function VideoFade({
  videoKey, current, onDone,
}: { videoKey: string; current: number | null; onDone: (fade: number) => void }) {
  const [seconds, setSeconds] = useState(current ?? 1.5)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function apply() {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch(`${API_ORIGIN}/api/videos/fade`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: videoKey, seconds }),
      })
      if (!res.ok) throw new Error(await readErrorDetail(res))
      const data: { audio_fade: number } = await res.json()
      onDone(data.audio_fade)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="vm-root">
      <div className="vm-row">
        <label title="ループのつなぎ目で音がぶつ切りに聞こえるときに。長いほど、ふわっと引いて戻ります">
          音のフェード
          <input type="number" min={0} max={3} step={0.1} value={seconds}
            onChange={e => setSeconds(Math.max(0, Math.min(3, Number(e.target.value) || 0)))} disabled={busy} /> 秒
        </label>
        <button type="button" className="vm-primary" onClick={() => void apply()} disabled={busy}>
          {busy ? 'かけ直しています…' : 'かけ直す'}
        </button>
        <span className="vm-note">今: {current === null ? 'なし' : `${current} 秒`}(0 で元の音)</span>
      </div>
      {error && <p className="vm-error">{error}</p>}
    </div>
  )
}
