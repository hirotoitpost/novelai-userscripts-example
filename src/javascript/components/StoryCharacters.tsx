import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import CharacterSheetEditor from './CharacterSheetEditor'

export interface Character {
  id: number
  name: string
  appearance_tags: string
  notes: string | null
  created_at: string
  reference_image_path?: string | null
  trigger_word?: string | null
  outfit_tags?: string | null
  style_tags?: string | null
  negative_tags?: string | null
  seed?: number | null
  /** 成人キャラ。データセットのR18生成はこれが true のキャラだけ */
  is_adult?: boolean
}

export interface SceneCharacter {
  id: number
  name: string
  appearance_tags: string
  /** 漫画v2のキャラ参照に使う画像 */
  reference_image_path?: string | null
}

interface Props {
  apiOrigin: string
  /** 変更をStory側に反映させる(シーン一覧の再読み込み)。 */
  onChanged: () => void
  disabled?: boolean
  /** 開いている物語。指定すると、この物語の中でキャラを付け替える欄を出す。 */
  storyId?: number
}

interface StoryCharacterUsage {
  id: number
  name: string
  scenes: number
}

/**
 * 登場人物の一覧と編集。抽出は当たり外れがあるので、ここで手直しできることが前提。
 * キャラは物語をまたいで共有されるので、他の物語で作った設定もそのまま選べる。
 */
export default function StoryCharacters({ apiOrigin, onChanged, disabled, storyId }: Props) {
  const navigate = useNavigate()
  const [characters, setCharacters] = useState<Character[]>([])
  const [editing, setEditing] = useState<Record<number, string>>({})
  const [open, setOpen] = useState<Record<number, boolean>>({})
  const [newName, setNewName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [inStory, setInStory] = useState<StoryCharacterUsage[]>([])
  const [replaceFrom, setReplaceFrom] = useState<number | null>(null)
  const [replaceTo, setReplaceTo] = useState<number | null>(null)
  const [replaceMsg, setReplaceMsg] = useState<string | null>(null)

  function load() {
    fetch(`${apiOrigin}/api/story/characters`)
      .then(r => (r.ok ? r.json() : []))
      .then(setCharacters)
      .catch(() => {/* サイレント失敗 */})
    if (storyId != null) {
      fetch(`${apiOrigin}/api/story/${storyId}/characters`)
        .then(r => (r.ok ? r.json() : []))
        .then(setInStory)
        .catch(() => {/* サイレント失敗 */})
    }
  }

  useEffect(load, [apiOrigin, storyId])

  /** この物語の中だけで、from のキャラが出ているシーンを to のキャラに付け替える。 */
  async function replace() {
    if (storyId == null || replaceFrom == null || replaceTo == null) return
    const from = inStory.find(c => c.id === replaceFrom)
    const to = characters.find(c => c.id === replaceTo)
    if (!from || !to) return
    if (!window.confirm(
      `この物語の${from.scenes}シーンで「${from.name}」を「${to.name}」に付け替えますか？\n` +
      '他の物語の割り当てと、キャラ自体(キャラシート)は変わりません。',
    )) return
    setError(null)
    setReplaceMsg(null)
    try {
      const res = await fetch(`${apiOrigin}/api/story/${storyId}/characters/replace`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ from_character_id: replaceFrom, to_character_id: replaceTo }),
      })
      if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? '付け替えに失敗しました')
      const { scenes } = await res.json()
      setReplaceMsg(`${scenes}シーンを「${from.name}」から「${to.name}」に付け替えました。`)
      setReplaceFrom(null)
      setReplaceTo(null)
      load()
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

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
        登場人物の容姿タグと普段の服装は、そのキャラが出るシーンの画像生成に渡されます。
        「キャラシート」で基準シードやネガティブも決めておくと、同じ見た目で安定して出やすくなります。
        抽出結果は粗いので、不要なものは削除し、タグは英語で整えてください。
      </p>

      {error && <div className="story-error">{error}</div>}

      {storyId != null && (
        <div className="story-replace">
          <span className="story-muted">この物語でキャラを付け替える(抽出された仮のキャラを、作り込んだキャラシートのキャラに替える、など)</span>
          {inStory.length === 0 ? (
            <span className="story-muted">この物語のシーンにはまだキャラが割り当てられていません。</span>
          ) : (
            <div className="story-row">
              <select
                value={replaceFrom ?? ''}
                onChange={e => setReplaceFrom(e.target.value ? Number(e.target.value) : null)}
                disabled={disabled}
              >
                <option value="">付け替え元</option>
                {inStory.map(c => <option key={c.id} value={c.id}>{c.name}({c.scenes}シーン)</option>)}
              </select>
              <span>→</span>
              <select
                value={replaceTo ?? ''}
                onChange={e => setReplaceTo(e.target.value ? Number(e.target.value) : null)}
                disabled={disabled}
              >
                <option value="">付け替え先</option>
                {characters.filter(c => c.id !== replaceFrom).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
              <button
                type="button"
                onClick={() => void replace()}
                disabled={disabled || replaceFrom == null || replaceTo == null}
              >
                付け替え
              </button>
            </div>
          )}
          {replaceMsg && <span className="story-muted">{replaceMsg}</span>}
        </div>
      )}

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
            {open[character.id] ? (
              <CharacterSheetEditor
                apiOrigin={apiOrigin}
                character={character}
                disabled={disabled}
                onSaved={() => {
                  load()
                  onChanged()
                }}
                onDuplicated={() => load()}
              />
            ) : (
              <>
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
                    onClick={() => void save(character.name, editing[character.id] ?? '')}
                    disabled={disabled}
                  >
                    容姿タグを保存
                  </button>
                )}
              </>
            )}
            <div className="story-actions">
              <button type="button" onClick={() => setOpen({ ...open, [character.id]: !open[character.id] })}>
                {open[character.id] ? 'キャラシートを閉じる' : 'キャラシートを開く'}
              </button>
              <button type="button" onClick={() => navigate(`/character-dataset?character=${character.id}`)}>
                キャラ別データセットで開く
              </button>
            </div>
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
