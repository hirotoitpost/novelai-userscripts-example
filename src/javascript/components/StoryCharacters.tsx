import { useEffect, useState } from 'react'

export interface Character {
  id: number
  name: string
  appearance_tags: string
  notes: string | null
  created_at: string
}

export interface SceneCharacter {
  id: number
  name: string
  appearance_tags: string
}

interface Props {
  apiOrigin: string
  /** 変更をStory側に反映させる(シーン一覧の再読み込み)。 */
  onChanged: () => void
  disabled?: boolean
}

/**
 * 登場人物の一覧と編集。抽出は当たり外れがあるので、ここで手直しできることが前提。
 * キャラは物語をまたいで共有されるので、他の物語で作った設定もそのまま選べる。
 */
export default function StoryCharacters({ apiOrigin, onChanged, disabled }: Props) {
  const [characters, setCharacters] = useState<Character[]>([])
  const [editing, setEditing] = useState<Record<number, string>>({})
  const [newName, setNewName] = useState('')
  const [error, setError] = useState<string | null>(null)

  function load() {
    fetch(`${apiOrigin}/api/story/characters`)
      .then(r => (r.ok ? r.json() : []))
      .then(setCharacters)
      .catch(() => {/* サイレント失敗 */})
  }

  useEffect(load, [apiOrigin])

  async function save(name: string, appearanceTags: string) {
    setError(null)
    try {
      const res = await fetch(`${apiOrigin}/api/story/characters`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, appearance_tags: appearanceTags }),
      })
      if (!res.ok) throw new Error((await res.json()).detail ?? '保存に失敗しました')
      load()
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function remove(id: number) {
    setError(null)
    try {
      await fetch(`${apiOrigin}/api/story/characters/${id}`, { method: 'DELETE' })
      load()
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="story-settings">
      <p className="story-muted">
        登場人物の容姿タグは、そのキャラが出るシーンを含むページの画像生成に渡されます。
        抽出結果は粗いので、不要なものは削除し、タグは英語で整えてください。
      </p>

      {error && <div className="story-error">{error}</div>}

      <ul className="story-character-list">
        {characters.length === 0 && <li className="story-muted">まだ登録がありません。</li>}
        {characters.map(character => (
          <li key={character.id} className="story-character">
            <div className="story-character-head">
              <span className="story-character-name">{character.name}</span>
              <button type="button" onClick={() => remove(character.id)} disabled={disabled}>
                削除
              </button>
            </div>
            <textarea
              className="story-premise"
              rows={2}
              placeholder="1girl, long black hair, blue eyes, school uniform"
              value={editing[character.id] ?? character.appearance_tags}
              disabled={disabled}
              onChange={e => setEditing({ ...editing, [character.id]: e.target.value })}
            />
            {(editing[character.id] ?? character.appearance_tags) !== character.appearance_tags && (
              <button
                type="button"
                onClick={() => save(character.name, editing[character.id] ?? '')}
                disabled={disabled}
              >
                容姿タグを保存
              </button>
            )}
          </li>
        ))}
      </ul>

      <div className="story-row">
        <label>
          キャラを手動で追加
          <input
            type="text"
            placeholder="キャラ名"
            value={newName}
            disabled={disabled}
            onChange={e => setNewName(e.target.value)}
          />
        </label>
      </div>
      <div className="story-actions">
        <button
          type="button"
          onClick={() => {
            void save(newName.trim(), '')
            setNewName('')
          }}
          disabled={disabled || !newName.trim()}
        >
          追加
        </button>
      </div>
    </div>
  )
}
