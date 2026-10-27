import { useEffect, useState } from 'react'
import type { SceneCharacter } from './StoryCharacters'
import type { DatasetState, Situation } from './DatasetScenes'
import { AXES } from './VariationImportDialog'
import type { Axis } from './VariationImportDialog'
import './SceneDirection.css'

interface Props {
  apiOrigin: string
  sceneId: number
  /** このシーンにいま割り当てているキャラ */
  current: SceneCharacter[]
  /** キャラ別データセットのシチュエーションと状態(/api/lora-dataset/situations, /states) */
  situations: Situation[]
  states: DatasetState[]
  /** 書き込んだあと(物語を読み直す) */
  onChanged: () => void
  disabled?: boolean
}

interface Row { state: number | null; outfit: string; action: string }

/** キャラの衣装(衣装のタブで登録した服。/api/wardrobe/outfits) */
interface Outfit { id: number; character_id: number; name: string; tags: string }

function rowOf(c: SceneCharacter): Row {
  return { state: c.state_id ?? null, outfit: c.scene_outfit_tags ?? '', action: c.action_tags ?? '' }
}

async function errorDetail(res: Response): Promise<string> {
  const data = await res.json().catch(() => null)
  return (data && typeof data.detail === 'string' ? data.detail : null) ?? `HTTP ${res.status}`
}

/**
 * シーンの演出: キャラごとの状態・その場面の服装・動作と、シチュエーションの書き込み。
 * 状態はキャラ別データセットと同じく容姿を一時的に変え、コマ・挿絵の生成のたびに効く(キャラシートは変えない)。
 * シチュエーションは、選んだときにシーンのタグ・服装・動作へタグを書き込むだけ(書き込んだあとは手で直せる)。
 * その場面の服装は、キャラに登録した衣装から選んで入れることもできる(入れたあとは手で直せる)。
 */
export default function SceneDirection({ apiOrigin, sceneId, current, situations, states, onChanged, disabled }: Props) {
  const [rows, setRows] = useState<Record<number, Row>>({})
  const [situationId, setSituationId] = useState<number | ''>('')
  const [choices, setChoices] = useState<Partial<Record<Axis, string>>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [outfits, setOutfits] = useState<Outfit[]>([])

  useEffect(() => {
    fetch(`${apiOrigin}/api/wardrobe/outfits`)
      .then(r => (r.ok ? r.json() : []))
      .then(setOutfits)
      .catch(() => setOutfits([]))
  }, [apiOrigin])

  // 物語を読み直したら、保存済みの値に戻す
  useEffect(() => {
    setRows(Object.fromEntries(current.map(c => [c.id, rowOf(c)])))
  }, [current])

  const situation = situations.find(s => s.id === situationId) ?? null
  const dirty = current.some(c => {
    const row = rows[c.id]
    const saved = rowOf(c)
    return row && (row.state !== saved.state || row.outfit !== saved.outfit || row.action !== saved.action)
  })
  const off = busy || disabled

  async function run(work: () => Promise<string>) {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      setNote(await work())
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const save = () => run(async () => {
    const ids = current.map(c => c.id)
    const pick = <T,>(f: (r: Row) => T) => Object.fromEntries(ids.map(id => [id, f(rows[id] ?? rowOf(current.find(c => c.id === id)!))]))
    const res = await fetch(`${apiOrigin}/api/story/scenes/${sceneId}/characters`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        character_ids: ids,
        states: pick(r => r.state),
        outfits: pick(r => r.outfit),
        actions: pick(r => r.action),
      }),
    })
    if (!res.ok) throw new Error(await errorDetail(res))
    return '保存しました'
  })

  const applySituation = () => run(async () => {
    if (!situation) return ''
    const res = await fetch(`${apiOrigin}/api/story/scenes/${sceneId}/situation`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ situation_id: situation.id, choices }),
    })
    if (!res.ok) throw new Error(await errorDetail(res))
    setSituationId('')
    setChoices({})
    return `「${situation.name}」のタグを書き込みました(シーンのタグ・服装・動作。手で直せます)`
  })

  function edit(id: number, patch: Partial<Row>) {
    setRows(prev => ({ ...prev, [id]: { ...(prev[id] ?? rowOf(current.find(c => c.id === id)!)), ...patch } }))
  }

  if (current.length === 0) {
    return <p className="scene-dir-none">登場人物を足すと、状態・服装・動作を指定できます。</p>
  }
  return (
    <div className="scene-dir">
      <table className="scene-dir-table">
        <thead>
          <tr><th>キャラ</th><th>状態</th><th>この場面の服装</th><th>動作・表情</th></tr>
        </thead>
        <tbody>
          {current.map(c => {
            const row = rows[c.id] ?? rowOf(c)
            return (
              <tr key={c.id}>
                <td>{c.name}</td>
                <td>
                  <select value={row.state ?? ''} disabled={off} aria-label={`${c.name}の状態`}
                    onChange={e => edit(c.id, { state: e.target.value ? Number(e.target.value) : null })}>
                    <option value="">ふだん</option>
                    {states.map(s => (
                      <option key={s.id} value={s.id}>{s.name}{s.adult_only.length ? '(成人キャラのみ)' : ''}</option>
                    ))}
                  </select>
                </td>
                <td>
                  <input value={row.outfit} disabled={off} placeholder="空欄ならふだんの服装" aria-label={`${c.name}のこの場面の服装`}
                    onChange={e => edit(c.id, { outfit: e.target.value })} />
                  {outfits.some(o => o.character_id === c.id) && (
                    <select value="" disabled={off} aria-label={`${c.name}の衣装から選ぶ`} className="scene-dir-outfit"
                      onChange={e => {
                        const picked = outfits.find(o => o.id === Number(e.target.value))
                        if (picked) edit(c.id, { outfit: picked.tags })
                      }}>
                      <option value="">衣装から選ぶ…</option>
                      {outfits.filter(o => o.character_id === c.id).map(o => <option key={o.id} value={o.id}>{o.name}</option>)}
                    </select>
                  )}
                </td>
                <td>
                  <input value={row.action} disabled={off} placeholder="smile, waving など" aria-label={`${c.name}の動作・表情`}
                    onChange={e => edit(c.id, { action: e.target.value })} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="scene-dir-actions">
        <button type="button" disabled={off || !dirty} onClick={() => void save()}>保存</button>
        {dirty && (
          <button type="button" disabled={off} onClick={() => setRows(Object.fromEntries(current.map(c => [c.id, rowOf(c)])))}>
            元に戻す
          </button>
        )}
      </div>

      <div className="scene-dir-situation">
        <select value={situationId} disabled={off || dirty} aria-label="シチュエーションを選ぶ"
          onChange={e => { setSituationId(e.target.value ? Number(e.target.value) : ''); setChoices({}) }}>
          <option value="">シチュエーションを書き込む…</option>
          {situations.map(s => (
            <option key={s.id} value={s.id}>{s.name}{s.adult_only.length ? '(成人キャラのみ)' : ''}</option>
          ))}
        </select>
        {situation && AXES.filter(a => situation[a.key].length > 1).map(a => (
          <label key={a.key}>
            {a.label}
            <select value={choices[a.key] ?? situation[a.key][0]} disabled={off}
              onChange={e => setChoices(prev => ({ ...prev, [a.key]: e.target.value }))}>
              {situation[a.key].map(v => <option key={v} value={v}>{v}</option>)}
            </select>
          </label>
        ))}
        {situation && (
          <button type="button" disabled={off} onClick={() => void applySituation()}
            title="構図・場所・追加のタグはシーンのタグに、服装は全員のこの場面の服装に、ポーズ・表情は全員の動作に書き込みます">
            書き込む
          </button>
        )}
      </div>
      {dirty && <p className="scene-dir-hint">シチュエーションを書き込む前に、変更を保存するか元に戻してください。</p>}
      {note && <p className="scene-dir-note" role="status">{note}</p>}
      {error && <p className="scene-dir-error" role="alert">{error}</p>}
    </div>
  )
}
