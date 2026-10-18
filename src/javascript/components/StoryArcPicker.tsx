import { useEffect, useState } from 'react'

/**
 * 話の流れのセット(/api/story-arcs)。夏休み(星座観測 → 水着の買い物 → プール → 花火大会)のような、アニメでよくある
 * 巻ごとの流れを選び、段階(1巻分)をおまかせ漫画のテーマと補足に入れる。セットは足したり直したりできる。
 */

export interface StoryArc {
  id: number
  name: string
  description: string
  beats: string[]
}

interface Props {
  token: string
  /** 段階を選んだとき: テーマ(その段階)と補足(セットの説明・この巻の後の流れ) */
  onUse: (theme: string, notes: string) => void
}

// PUT・DELETE も送る(apiFetch は GET・POST だけ)。CastPresets と同じ
async function send<T>(token: string, method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`)
  return (res.status === 204 ? null : await res.json()) as T
}

/** 段階 index を作るときの補足。次の巻へつながるよう、この巻の後の流れも書く */
export function arcNotes(arc: StoryArc, index: number): string {
  const parts = [`話の流れのセット「${arc.name}」の第${index + 1}段階(全${arc.beats.length}段階)。`]
  if (arc.description.trim()) parts.push(arc.description.trim())
  const next = arc.beats[index + 1]
  parts.push(next ? `この巻の後の流れ(この巻では描かない): ${next}` : 'この巻でセットの流れは終わり。')
  return parts.join('\n')
}

export default function StoryArcPicker({ token, onUse }: Props) {
  const [arcs, setArcs] = useState<StoryArc[]>([])
  const [arcId, setArcId] = useState<number | null>(null)
  const [editing, setEditing] = useState<{ id: number | null; name: string; description: string; beats: string } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = async (select?: number) => {
    const list = await send<StoryArc[]>(token, 'GET', '/api/story-arcs')
    setArcs(list)
    setArcId(current => select ?? (list.some(a => a.id === current) ? current : list[0]?.id ?? null))
  }
  // 開いたときに一度読む
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load().catch(e => setError(String(e))) }, [token])

  const arc = arcs.find(a => a.id === arcId) ?? null

  const save = async () => {
    if (!editing) return
    setError(null)
    try {
      const body = { name: editing.name, description: editing.description, beats: editing.beats.split('\n') }
      const saved = editing.id === null
        ? await send<StoryArc>(token, 'POST', '/api/story-arcs', body)
        : await send<StoryArc>(token, 'PUT', `/api/story-arcs/${editing.id}`, body)
      setEditing(null)
      await load(saved.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const remove = async () => {
    if (!arc || !window.confirm(`セット「${arc.name}」を消しますか?`)) return
    try {
      await send(token, 'DELETE', `/api/story-arcs/${arc.id}`)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="md-field">
      <span>話の流れのセット(段階を選ぶと、テーマと補足に入ります。シリーズの続きにすると、巻を重ねて流れを進められます)</span>
      <div className="md-row">
        <select value={arcId ?? ''} onChange={e => setArcId(e.target.value ? Number(e.target.value) : null)}>
          {arcs.length === 0 && <option value="">(セットがありません)</option>}
          {arcs.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <button type="button" className="md-small" onClick={() => setEditing({ id: null, name: '', description: '', beats: '' })}>新しいセット</button>
        {arc && (
          <>
            <button type="button" className="md-small"
              onClick={() => setEditing({ id: arc.id, name: arc.name, description: arc.description, beats: arc.beats.join('\n') })}>直す</button>
            <button type="button" className="md-small" onClick={() => void remove()}>消す</button>
          </>
        )}
      </div>
      {arc && !editing && (
        <>
          {arc.description && <p className="md-hint">{arc.description}</p>}
          <ol className="md-arc-beats">
            {arc.beats.map((beat, index) => (
              <li key={index}>
                {beat}{' '}
                <button type="button" className="md-small" onClick={() => onUse(beat, arcNotes(arc, index))}>この巻を作る</button>
              </li>
            ))}
          </ol>
        </>
      )}
      {editing && (
        <div className="md-arc-editor">
          <label className="md-field"><span>名前</span>
            <input value={editing.name} maxLength={100} onChange={e => setEditing({ ...editing, name: e.target.value })} />
          </label>
          <label className="md-field"><span>説明(セットの後に続く流れなども)</span>
            <input value={editing.description} maxLength={1000} onChange={e => setEditing({ ...editing, description: e.target.value })} />
          </label>
          <label className="md-field"><span>流れ(1行が1巻)</span>
            <textarea rows={5} value={editing.beats} onChange={e => setEditing({ ...editing, beats: e.target.value })} />
          </label>
          <div className="md-row">
            <button type="button" className="md-small" onClick={() => void save()} disabled={!editing.name.trim() || !editing.beats.trim()}>保存</button>
            <button type="button" className="md-small" onClick={() => setEditing(null)}>やめる</button>
          </div>
        </div>
      )}
      {error && <p className="md-error" role="alert">{error}</p>}
    </div>
  )
}
