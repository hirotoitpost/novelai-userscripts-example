import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../api'

/**
 * キャストのセット: よく使うキャラの組み合わせに名前を付けて保存し、押すだけで選べるようにする
 * (/api/story/cast-presets。DB に残るので、ほかの端末からも使える)。漫画ドラフトとおまかせ漫画で使う。
 */

export interface CastPreset {
  id: number
  name: string
  character_ids: number[]
}

interface Props {
  token: string
  /** いま選んでいるキャラ */
  selected: number[]
  /** セットを押したとき(消されたキャラは除いて渡す) */
  onSelect: (ids: number[]) => void
  /** いま登録されているキャラのID(消されたキャラをセットから外して見せる) */
  existing: number[]
  className?: string
}

async function send<T>(token: string, method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`)
  return (res.status === 204 ? null : await res.json()) as T
}

export default function CastPresets({ token, selected, onSelect, existing, className }: Props) {
  const [presets, setPresets] = useState<CastPreset[]>([])
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    apiFetch<CastPreset[]>(token, '/api/story/cast-presets').then(setPresets).catch(() => {})
  }, [token])
  useEffect(() => { load() }, [load])

  const save = async () => {
    const name = window.prompt('このキャストのセットの名前(同じ名前なら上書きします)', '')?.trim()
    if (!name) return
    setError(null)
    try {
      await send(token, 'POST', '/api/story/cast-presets', { name, character_ids: selected })
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const remove = async (preset: CastPreset) => {
    if (!window.confirm(`キャストのセット「${preset.name}」を消します(キャラは消えません)。よろしいですか?`)) return
    try {
      await send(token, 'DELETE', `/api/story/cast-presets/${preset.id}`)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className={className ?? 'cast-presets'}>
      <span className="md-hint">キャストのセット:</span>
      {presets.map(p => {
        const ids = p.character_ids.filter(id => existing.includes(id))
        const on = ids.length > 0 && ids.length === selected.length && ids.every(id => selected.includes(id))
        return (
          <span key={p.id} className={`md-chip cast-preset${on ? ' md-chip--on' : ''}`}>
            <button type="button" className="cast-preset-main" onClick={() => onSelect(ids)} disabled={ids.length === 0}
              title={ids.length < p.character_ids.length ? '消されたキャラは除いて選びます' : undefined}>
              {p.name}({ids.length}人)
            </button>
            <button type="button" className="cast-preset-remove" onClick={() => void remove(p)} aria-label={`キャストのセット「${p.name}」を消す`}>✕</button>
          </span>
        )
      })}
      <button type="button" className="md-small" onClick={() => void save()} disabled={selected.length === 0}
        title="いま選んでいるキャラの組み合わせに名前を付けて保存します">
        いまの組み合わせを保存
      </button>
      {error && <span className="md-error">{error}</span>}
    </div>
  )
}
