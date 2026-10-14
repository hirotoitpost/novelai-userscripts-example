import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../api'
import './GenreManager.css'
import './RedrawStates.css'

/**
 * 描き直しで作る人物の状態(透明・幽霊・石化 など)の管理: 一覧と、追加・書き換え・削除(/api/manga-v2/redraw-states)。
 * 台本で人物の動作のタグが「反応するタグ」に当たると、コマを描いたあとに、その人物の範囲だけを「描き直しのタグ」で描き直す。
 */

export interface RedrawState {
  id: number
  name: string
  match_tags: string
  redraw_tags: string
  look: Look
}

type Look = 'none' | 'outfit' | 'full'

const LOOKS: { value: Look; label: string }[] = [
  { value: 'none', label: '入れない' },
  { value: 'outfit', label: '服装だけ' },
  { value: 'full', label: '容姿・服装' },
]

const EMPTY = { name: '', match_tags: '', redraw_tags: '', look: 'none' as Look }

async function send<T>(token: string, method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`)
  return (res.status === 204 ? null : await res.json()) as T
}

export default function RedrawStates({ token }: { token: string }) {
  const [states, setStates] = useState<RedrawState[]>([])
  const [form, setForm] = useState(EMPTY)
  const [editing, setEditing] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    apiFetch<RedrawState[]>(token, '/api/manga-v2/redraw-states').then(setStates).catch(() => {})
  }, [token])
  useEffect(() => { load() }, [load])

  const run = async (work: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try {
      await work()
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const ready = form.name.trim() && form.match_tags.trim() && form.redraw_tags.trim()
  const save = () => {
    if (!ready) return
    void run(async () => {
      if (editing === null) await send(token, 'POST', '/api/manga-v2/redraw-states', form)
      else await send(token, 'PUT', `/api/manga-v2/redraw-states/${editing}`, form)
      setForm(EMPTY)
      setEditing(null)
    })
  }

  const edit = (state: RedrawState) => {
    setEditing(state.id)
    setForm({ name: state.name, match_tags: state.match_tags, redraw_tags: state.redraw_tags, look: state.look })
  }

  return (
    <details className="genre-manager redraw-states">
      <summary>描き直しで作る状態(透明 など)の管理</summary>
      <p className="redraw-states-hint">
        台本で人物の動作のタグが「反応するタグ」に当たると、コマを描いたあとに、その人物の範囲だけを「描き直しのタグ」で描き直します
        (インペイント)。ほかの人物と背景はそのままです。
      </p>
      <ul className="redraw-states-list">
        {states.map(s => (
          <li key={s.id} className={editing === s.id ? 'redraw-states-editing' : undefined}>
            <strong>{s.name}</strong>
            <span className="redraw-states-tags">反応: {s.match_tags}</span>
            <span className="redraw-states-tags">描き直し: {s.redraw_tags}(見た目: {LOOKS.find(l => l.value === s.look)?.label})</span>
            <span className="redraw-states-actions">
              <button type="button" disabled={busy} onClick={() => edit(s)}>直す</button>
              <button type="button" disabled={busy} aria-label={`状態「${s.name}」を消す`}
                onClick={() => {
                  if (window.confirm(`状態「${s.name}」を消します。よろしいですか?`)) {
                    void run(() => send(token, 'DELETE', `/api/manga-v2/redraw-states/${s.id}`))
                  }
                }}>✕</button>
            </span>
          </li>
        ))}
        {states.length === 0 && <li className="genre-manager-empty">状態がありません</li>}
      </ul>
      <div className="redraw-states-form">
        <input value={form.name} maxLength={100} placeholder="名前(例: 透明)" aria-label="状態の名前"
          onChange={e => setForm({ ...form, name: e.target.value })} />
        <input value={form.match_tags} maxLength={500} placeholder="反応する動作のタグ(例: invisible)" aria-label="反応する動作のタグ"
          onChange={e => setForm({ ...form, match_tags: e.target.value })} />
        <input value={form.redraw_tags} maxLength={500} placeholder="描き直しのタグ(例: empty clothes, floating clothes)"
          aria-label="描き直しのタグ" onChange={e => setForm({ ...form, redraw_tags: e.target.value })} />
        <label>
          その人の見た目
          <select value={form.look} onChange={e => setForm({ ...form, look: e.target.value as Look })}>
            {LOOKS.map(l => <option key={l.value} value={l.value}>{l.label}</option>)}
          </select>
        </label>
        <button type="button" disabled={busy || !ready} onClick={save}>{editing === null ? '状態を追加' : '書き換える'}</button>
        {editing !== null && (
          <button type="button" disabled={busy} onClick={() => { setEditing(null); setForm(EMPTY) }}>やめる</button>
        )}
      </div>
      {error && <p className="md-error" role="alert">{error}</p>}
    </details>
  )
}
