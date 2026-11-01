import { useState } from 'react'
import type { Marker } from './Model3DViewer'

/**
 * 素体の 3D モデルの「ならす所」。3D の体の上をクリックして荒れた所を選び、半径を決めると、骨を入れるときに、そこを
 * まわりの面から当てはめたなめらかな面に置き換える(体の形に焼き込む)。選んだ所はモデルごとに残り、形を作り直しても
 * 骨を入れ直しても使う。保存する前に、試し(15 秒ほど。モデル本体は変わらない)で確かめられる。
 */

interface Props {
  url: string
  rootName: string
  baseId: number
  /** 保存したならす所 */
  saved: Marker[]
  /** 保存した形状パーツの強さ(保存するときに、そのまま送る) */
  weights: Record<string, number>
  busy: boolean
  /** 編集中のならす所(null なら保存したもの) */
  draft: Marker[] | null
  onDraft: (draft: Marker[] | null) => void
  /** 3D の上のクリックで選んでいるか */
  picking: boolean
  onPicking: (picking: boolean) => void
  /** サーバーが返した 3D の状態で、画面を新しくする */
  onInfo: (info: unknown) => void
  /** 骨を入れ直す */
  onRig: () => Promise<void>
  /** いまのならす所で試す */
  onPreview: (spots: Marker[]) => void
}

/** 選んだ所の初めの半径(mm) */
export const DEFAULT_SMOOTH_RADIUS = 12

async function detail(res: Response): Promise<string> {
  const data = await res.json().catch(() => null)
  return typeof data?.detail === 'string' ? data.detail : `HTTP ${res.status}`
}

export default function SmoothSpots({ url, rootName, baseId, saved, weights, busy, draft, onDraft, picking, onPicking, onInfo, onRig, onPreview }: Props) {
  const [status, setStatus] = useState<string | null>(null)
  const spots = draft ?? saved

  function setRadius(index: number, radius: number) {
    onDraft(spots.map((spot, i) => (i === index ? { ...spot, radius } : spot)))
  }

  async function saveAndRig() {
    setStatus(null)
    try {
      const res = await fetch(`${url}/shapes`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ root_name: rootName, base_id: baseId, weights, smooth: spots }),
      })
      if (!res.ok) {
        setStatus(`保存できませんでした: ${await detail(res)}`)
        return
      }
      onInfo(await res.json())
    } catch (e) {
      setStatus(e instanceof Error ? e.message : String(e))
      return
    }
    onDraft(null)
    onPicking(false)
    await onRig()
  }

  return (
    <div className="m3d-smooth">
      <h5>ならす所</h5>
      <p className="lora-hint">
        荒れた所(しわ・くぼみ・小さな穴)を 3D の上で選ぶと、骨を入れるときに、そこをまわりの面に合わせたなめらかな面に
        置き換えます。体の丸みは残ります。選んだ所はこのモデルに残り、形を作り直しても使います。
      </p>
      <div className="fsh-actions">
        <button type="button" className={`lora-btn${picking ? '' : ' lora-btn--secondary'}`} disabled={busy}
          onClick={() => onPicking(!picking)}>
          {picking ? '選ぶのをやめる' : '3D の上で選ぶ'}
        </button>
      </div>
      {picking && <p className="lora-hint">3D の体の上をクリックすると、ならす所を足します(ドラッグで回せます。動きは止めると選びやすいです)。</p>}
      {spots.length > 0 && (
        <ol className="m3d-smooth-list">
          {spots.map((spot, i) => (
            <li key={i}>
              <label className="fsh-label">半径 {spot.radius.toFixed(0)}mm
                <input type="range" min={4} max={40} step={1} value={spot.radius} disabled={busy}
                  onChange={e => setRadius(i, Number(e.target.value))} />
              </label>
              <button type="button" className="lora-btn lora-btn--secondary" disabled={busy}
                onClick={() => onDraft(spots.filter((_, k) => k !== i))}>消す</button>
            </li>
          ))}
        </ol>
      )}
      <div className="fsh-actions">
        <button type="button" className="lora-btn lora-btn--secondary" disabled={busy || spots.length === 0}
          title="いまのならす所で 3D を作って、浮かせた窓に出します(15 秒ほど。モデル本体は変わりません)"
          onClick={() => onPreview(spots)}>この所で試す</button>
        <button type="button" className="lora-btn" disabled={busy || !draft} onClick={() => void saveAndRig()}>保存して骨を入れ直す</button>
        {draft && <button type="button" className="lora-btn lora-btn--secondary" onClick={() => onDraft(null)}>元に戻す</button>}
      </div>
      {status && <p className="lora-error">{status}</p>}
    </div>
  )
}
