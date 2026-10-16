import { useCallback, useEffect, useRef, useState } from 'react'
import { API_ORIGIN, fileUrl } from '../library'

/** 部位ごとに作ったうごイラ(E)の、髪の範囲の状態(/api/ugoira/{id}/rig) */
interface RigState {
  source: string
  view: string
  version: number
  can_undo: boolean
  detail?: boolean
}

interface Props {
  projectId: number
  /** ほかの処理が動いているあいだは触れない */
  running: boolean
  /** 作り直しを始めたら、進み具合の追いかけを親に任せる */
  onRedo: (jobId: string) => void
}

async function post(path: string, body?: unknown): Promise<RigState & { id?: string }> {
  const res = await fetch(`${API_ORIGIN}/api/ugoira${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => null)
    throw new Error(typeof detail?.detail === 'string' ? detail.detail : `HTTP ${res.status}`)
  }
  return res.json()
}

/**
 * 半自動: 自動で取った髪の範囲(赤)を、タップで直す。タップした所のまわりを4倍に拡大して切り出し、元の絵の座標に戻して
 * 足す・引く。直したら「この範囲で作り直す」で、同じうごイラのコマを作り直す。
 */
export default function UgoiraRigEditor({ projectId, running, onRedo }: Props) {
  const [state, setState] = useState<RigState | null>(null)
  const [mode, setMode] = useState<'add' | 'remove'>('add')
  const [detail, setDetail] = useState<'auto' | 'on' | 'off'>('auto')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [edited, setEdited] = useState(false)
  const image = useRef<HTMLImageElement>(null)

  const load = useCallback(() => {
    fetch(`${API_ORIGIN}/api/ugoira/${projectId}/rig`)
      .then(r => (r.ok ? r.json() : null))
      .then(setState)
      .catch(() => setState(null))
  }, [projectId])
  useEffect(() => { load(); setEdited(false) }, [load])

  async function run(action: () => Promise<RigState>, changes = true) {
    setBusy(true)
    setError(null)
    try {
      setState(await action())
      if (changes) setEdited(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function tap(e: React.MouseEvent<HTMLImageElement>) {
    const img = image.current
    if (!img || busy || running) return
    const rect = img.getBoundingClientRect()
    const x = ((e.clientX - rect.left) / rect.width) * img.naturalWidth
    const y = ((e.clientY - rect.top) / rect.height) * img.naturalHeight
    void run(() => post(`/${projectId}/rig/edit`, {
      x, y, mode, ...(detail === 'auto' ? {} : { detail: detail === 'on' }),
    }))
  }

  async function redo() {
    setBusy(true)
    setError(null)
    try {
      const job = await post(`/${projectId}/rig/redo`)
      if (job.id) onRedo(job.id)
      setEdited(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (!state) return null
  const disabled = busy || running
  return (
    <div className="ugo-rig">
      <h3>髪の範囲を直す(半自動)</h3>
      <p className="pub-meta">
        赤い所が、動かす髪の範囲です(自動で取ったもの)。取りこぼした髪をタップすると足し、余分な所は「外す」にしてタップすると
        引きます。タップした所のまわりを拡大して切り出すので、細い毛筋も取れます。直したら「この範囲で作り直す」を押してください。
      </p>
      <div className="ugo-row" role="radiogroup" aria-label="タップしたとき">
        <label><input type="radio" name="ugo-rig-mode" checked={mode === 'add'} disabled={disabled} onChange={() => setMode('add')} /> 足す</label>
        <label><input type="radio" name="ugo-rig-mode" checked={mode === 'remove'} disabled={disabled} onChange={() => setMode('remove')} /> 外す</label>
        <label>拡大{' '}
          <select value={detail} disabled={disabled} onChange={e => setDetail(e.target.value as 'auto' | 'on' | 'off')}>
            <option value="auto">おまかせ(小さい絵は高精細)</option>
            <option value="off">ふつう(速い)</option>
            <option value="on">高精細(AI 拡大。1回10秒ほど)</option>
          </select>
        </label>
      </div>
      <div className={`ugo-rig-canvas${disabled ? ' is-busy' : ''}`}>
        <img ref={image} src={fileUrl(state.source)} alt="元の絵(タップして髪の範囲を直す)" onClick={tap} />
        <img className="ugo-rig-overlay" src={`${fileUrl(state.view)}&v=${state.version}`} alt="" aria-hidden="true" />
      </div>
      {busy && <p className="pub-meta" role="status">切り出しています…</p>}
      {error && <p className="pub-error" role="alert">{error}</p>}
      <div className="ugo-row">
        <button type="button" disabled={disabled || !state.can_undo} onClick={() => void run(() => post(`/${projectId}/rig/undo`))}>一つ前に戻す</button>
        <button type="button" disabled={disabled} onClick={() => void run(() => post(`/${projectId}/rig/reset`))}>自動の範囲に戻す</button>
        <button type="button" className="ugo-primary" disabled={disabled} onClick={() => void redo()}>
          この範囲で作り直す{edited ? '(直しあり)' : ''}
        </button>
      </div>
    </div>
  )
}
