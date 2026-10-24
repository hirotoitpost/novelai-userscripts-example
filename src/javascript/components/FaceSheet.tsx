import { useEffect, useState } from 'react'
import { fileUrl } from '../library'
import FullscreenViewer from './FullscreenViewer'
import './FaceSheet.css'

/**
 * キャラシートの顔・表情シート。顔の向き(正面・側面・背面・見返り・逆立ち など)と表情(喜怒哀楽・羞恥・テヘペロ・
 * ウインク など)を選んで1枚ずつ描き、一覧で見る。キャラシートの見た目と基準シードで描くので、項目ごとの違いは
 * 向き・表情だけになりやすい。描いた絵はキャプション付きでデータセットのフォルダ(face_sheet/)に置く。
 */

interface Entry {
  key: string
  label: string
  group: 'angle' | 'expression'
  path: string | null
  updated: number | null
}

interface Props {
  characterId: number
  characterName: string
  token: string | null
  rootName: string
  disabled?: boolean
}

const GROUPS: { id: Entry['group']; label: string }[] = [
  { id: 'angle', label: '顔の向き・姿勢' },
  { id: 'expression', label: '表情' },
]

export default function FaceSheet({ characterId, characterName, token, rootName, disabled }: Props) {
  const [entries, setEntries] = useState<Entry[]>([])
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [useReference, setUseReference] = useState(false)
  const [running, setRunning] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  // 拡大して見ている項目のキー。左右で、描いてある項目を画面の並び順に前後する
  const [zoomKey, setZoomKey] = useState<string | null>(null)

  function load() {
    fetch(`/api/lora-dataset/character/${characterId}/face-sheet?root_name=${encodeURIComponent(rootName)}`)
      .then(r => (r.ok ? r.json() : []))
      .then(setEntries)
      .catch(() => setEntries([]))
  }
  useEffect(load, [characterId, rootName])
  useEffect(() => setZoomKey(null), [characterId])

  function toggle(key: string) {
    const next = new Set(picked)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    setPicked(next)
  }

  function pickGroup(group: Entry['group'], onlyMissing: boolean) {
    const next = new Set(picked)
    for (const e of entries) if (e.group === group && (!onlyMissing || !e.path)) next.add(e.key)
    setPicked(next)
  }

  async function generate() {
    if (!picked.size) return
    setRunning(true)
    setStatus(`${picked.size} 枚を描いています…`)
    try {
      const res = await fetch(`/api/lora-dataset/character/${characterId}/face-sheet`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ keys: [...picked], root_name: rootName, use_reference: useReference }),
      })
      if (!res.ok || !res.body) {
        const data = await res.json().catch(() => null)
        setStatus(typeof data?.detail === 'string' ? data.detail : `描けませんでした (HTTP ${res.status})`)
        return
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let failed = 0
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const event = JSON.parse(line.slice(6)) as { current?: number; total?: number; label?: string; status?: string }
          if (event.status === 'error') failed++
          if (event.current && event.total) {
            setStatus(`${event.current}/${event.total}: ${event.label}${event.status === 'error' ? '(失敗)' : ''}`)
            if (event.status === 'ok') load()
          }
        }
      }
      setStatus(failed ? `描き終わりました(失敗 ${failed} 枚)` : '描き終わりました')
      setPicked(new Set())
    } catch (e) {
      setStatus(e instanceof Error ? e.message : String(e))
    } finally {
      setRunning(false)
      load()
    }
  }

  const busy = disabled || running
  const drawn = GROUPS.flatMap(group => entries.filter(e => e.group === group.id && e.path))
  const zoomIndex = drawn.findIndex(e => e.key === zoomKey)
  const zoom = zoomIndex >= 0 ? drawn[zoomIndex] : null

  return (
    <section className="fsh">
      <h3>{characterName}の顔・表情シート</h3>
      <p className="lora-hint">
        描きたい項目を選んで「描く」を押すと、キャラシートの見た目と基準シードで1枚ずつ描きます(1枚ごとに Anlas を使います)。
        描いてある項目は描き直します。絵はキャプション付きで、データセットのフォルダの face_sheet/ に置きます。
      </p>
      {GROUPS.map(group => (
        <div key={group.id}>
          <div className="fsh-group-head">
            <h4>{group.label}</h4>
            <button type="button" className="lora-btn lora-btn--secondary" disabled={busy} onClick={() => pickGroup(group.id, false)}>全部選ぶ</button>
            <button type="button" className="lora-btn lora-btn--secondary" disabled={busy} onClick={() => pickGroup(group.id, true)}>まだの項目を選ぶ</button>
          </div>
          <div className="fsh-grid">
            {entries.filter(e => e.group === group.id).map(e => (
              <div key={e.key} className={picked.has(e.key) ? 'fsh-cell fsh-cell--on' : 'fsh-cell'}>
                {e.path ? (
                  <button type="button" className="fsh-thumb" onClick={() => setZoomKey(e.key)} title="拡大">
                    <img src={`${fileUrl(e.path)}&v=${e.updated ?? 0}`} alt={e.label} loading="lazy" />
                  </button>
                ) : (
                  <div className="fsh-thumb fsh-thumb--none">未</div>
                )}
                <label className="fsh-label">
                  <input type="checkbox" checked={picked.has(e.key)} disabled={busy} onChange={() => toggle(e.key)} />
                  {e.label}
                </label>
              </div>
            ))}
          </div>
        </div>
      ))}
      <div className="fsh-actions">
        <label className="fsh-label">
          <input type="checkbox" checked={useReference} disabled={busy} onChange={e => setUseReference(e.target.checked)} />
          参照画像も使う(V4.5・顔を寄せる)
        </label>
        <button type="button" className="lora-btn" disabled={busy || picked.size === 0} onClick={() => void generate()}>
          {running ? '描いています…' : `選んだ ${picked.size} 枚を描く`}
        </button>
        {status && <span className="lora-hint">{status}</span>}
      </div>
      {zoom?.path && (
        <FullscreenViewer
          src={`${fileUrl(zoom.path)}&v=${zoom.updated ?? 0}`}
          alt={zoom.label}
          counter={`${zoomIndex + 1} / ${drawn.length} ・ ${zoom.label}`}
          onPrev={zoomIndex > 0 ? () => setZoomKey(drawn[zoomIndex - 1].key) : undefined}
          onNext={zoomIndex < drawn.length - 1 ? () => setZoomKey(drawn[zoomIndex + 1].key) : undefined}
          onClose={() => setZoomKey(null)}
        />
      )}
    </section>
  )
}
