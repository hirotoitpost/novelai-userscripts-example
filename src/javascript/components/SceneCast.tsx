import { useState } from 'react'
import type { Character, SceneCharacter } from './StoryCharacters'
import './SceneCast.css'

/** 漫画のコマに描けるキャラの数(キャラごとのプロンプトの上限) */
const MAX_IN_PANEL = 4

interface Props {
  apiOrigin: string
  sceneId: number
  /** このシーンにいま割り当てているキャラ */
  current: SceneCharacter[]
  /** 登録済みのキャラ全員 */
  characters: Character[]
  /** この物語のほかのシーンに出ているキャラ(選ぶときに先に並べる) */
  inStory: number[]
  /** 割り当てを変えた・キャラを作ったあと(物語とキャラの一覧を読み直す) */
  onChanged: () => void
  disabled?: boolean
}

/**
 * シーンごとの登場人物: 外す・登録済みのキャラから足す・新しく作って足す。抽出の当たり外れを、シーンごとに直せる。
 * 足したり外したりしても、残るキャラの動作(表情・しぐさのタグ)はそのまま残る。
 */
export default function SceneCast({ apiOrigin, sceneId, current, characters, inStory, onChanged, disabled }: Props) {
  const [adding, setAdding] = useState('')
  const [newName, setNewName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const ids = current.map(c => c.id)
  const choices = characters
    .filter(c => !ids.includes(c.id))
    .sort((a, b) => Number(inStory.includes(b.id)) - Number(inStory.includes(a.id)) || a.name.localeCompare(b.name, 'ja'))
  const typed = newName.trim()

  async function save(next: number[]) {
    const res = await fetch(`${apiOrigin}/api/story/scenes/${sceneId}/characters`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ character_ids: next }),
    })
    if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`)
  }

  async function run(work: () => Promise<void>) {
    setBusy(true)
    setError(null)
    try {
      await work()
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const add = (id: number) => run(async () => { await save([...ids, id]); setAdding('') })

  const create = () => run(async () => {
    const res = await fetch(`${apiOrigin}/api/story/characters/new`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: typed }),
    })
    if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`)
    const created: Character = await res.json()
    await save([...ids, created.id])
    setNewName('')
  })

  const off = busy || disabled
  const existing = characters.find(c => c.name === typed)
  return (
    <div className="scene-cast">
      <span className="scene-cast-label">登場:</span>
      {current.map(c => (
        <span key={c.id} className="scene-cast-chip">
          {c.name}
          <button type="button" disabled={off} aria-label={`シーンから「${c.name}」を外す`} title="このシーンから外す(キャラは消えません)"
            onClick={() => void run(() => save(ids.filter(id => id !== c.id)))}>✕</button>
        </span>
      ))}
      {current.length === 0 && <span className="scene-cast-none">なし</span>}
      <select className="scene-cast-select" value={adding} disabled={off || choices.length === 0} aria-label="登録済みのキャラを足す"
        onChange={e => { const id = Number(e.target.value); if (id) void add(id) }}>
        <option value="">＋ 登録済みのキャラを足す</option>
        {choices.map(c => (
          <option key={c.id} value={c.id}>{c.name}{inStory.includes(c.id) ? '(この物語)' : ''}</option>
        ))}
      </select>
      <span className="scene-cast-new">
        <input value={newName} maxLength={100} placeholder="新しいキャラの名前" aria-label="新しく作るキャラの名前" disabled={off}
          onChange={e => setNewName(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && typed && !existing) { e.preventDefault(); void create() } }} />
        <button type="button" disabled={off || !typed || !!existing} onClick={() => void create()}
          title="名前だけでキャラを作り、このシーンに足します(容姿は「登場人物(容姿の指定)」で入れられます)">
          {existing ? 'その名前は登録済み' : '作って足す'}
        </button>
      </span>
      {current.length > MAX_IN_PANEL && (
        <span className="scene-cast-warn">漫画のコマに描けるのは{MAX_IN_PANEL}人までです(名前順の先の{MAX_IN_PANEL}人)</span>
      )}
      {error && <span className="scene-cast-error" role="alert">{error}</span>}
    </div>
  )
}
