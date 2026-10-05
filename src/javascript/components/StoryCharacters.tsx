import { useEffect, useState } from 'react'

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

/** キャラシートで編集する項目。容姿は変えない部分、服装は場面で差し替える部分。 */
type SheetField = 'appearance_tags' | 'outfit_tags' | 'style_tags' | 'negative_tags' | 'trigger_word' | 'seed'

const SHEET_FIELDS: { key: SheetField; label: string; placeholder: string; rows: number }[] = [
  { key: 'appearance_tags', label: '容姿(固定)', placeholder: '1girl, brown hair, long hair, two side up, pink scrunchie, yellow eyes', rows: 2 },
  { key: 'outfit_tags', label: '普段の服装', placeholder: 'school uniform, cardigan, pleated skirt', rows: 1 },
  { key: 'style_tags', label: '画風(データセット用)', placeholder: 'best quality, masterpiece, flat color', rows: 1 },
  { key: 'negative_tags', label: 'ネガティブ', placeholder: 'short hair, blue eyes', rows: 1 },
  { key: 'trigger_word', label: 'トリガーワード', placeholder: 'kujo_yura', rows: 1 },
  { key: 'seed', label: '基準シード', placeholder: '3257661879', rows: 1 },
]

function sheetValue(character: Character, key: SheetField): string {
  const value = character[key]
  return value == null ? '' : String(value)
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
}

/**
 * 登場人物の一覧と編集。抽出は当たり外れがあるので、ここで手直しできることが前提。
 * キャラは物語をまたいで共有されるので、他の物語で作った設定もそのまま選べる。
 */
export default function StoryCharacters({ apiOrigin, onChanged, disabled }: Props) {
  const [characters, setCharacters] = useState<Character[]>([])
  const [editing, setEditing] = useState<Record<number, Partial<Record<SheetField, string>>>>({})
  const [open, setOpen] = useState<Record<number, boolean>>({})
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

  async function saveSheet(character: Character) {
    setError(null)
    const changes = editing[character.id] ?? {}
    const body: Record<string, string | number | null> = {}
    for (const [key, value] of Object.entries(changes) as [SheetField, string][]) {
      if (key === 'seed') {
        const trimmed = value.trim()
        if (trimmed && !/^\d+$/.test(trimmed)) {
          setError('基準シードは数字で入力してください')
          return
        }
        body.seed = trimmed ? Number(trimmed) : null
      } else {
        body[key] = value.trim()
      }
    }
    try {
      const res = await fetch(`${apiOrigin}/api/story/characters/${character.id}/sheet`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error((await res.json()).detail ?? '保存に失敗しました')
      setEditing(prev => {
        const next = { ...prev }
        delete next[character.id]
        return next
      })
      load()
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function setAdult(character: Character, isAdult: boolean) {
    setError(null)
    try {
      const res = await fetch(`${apiOrigin}/api/story/characters/${character.id}/sheet`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_adult: isAdult }),
      })
      if (!res.ok) throw new Error((await res.json()).detail ?? '保存に失敗しました')
      load()
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
            {(() => {
              const draft = editing[character.id] ?? {}
              const value = (key: SheetField) => draft[key] ?? sheetValue(character, key)
              const dirty = Object.entries(draft).some(([k, v]) => v !== sheetValue(character, k as SheetField))
              const fields = open[character.id] ? SHEET_FIELDS : SHEET_FIELDS.slice(0, 1)
              return (
                <>
                  {fields.map(field => (
                    <label key={field.key} className="story-sheet-field">
                      {open[character.id] && <span className="story-muted">{field.label}</span>}
                      <textarea
                        className="story-premise"
                        rows={field.rows}
                        placeholder={field.placeholder}
                        value={value(field.key)}
                        disabled={disabled}
                        onChange={e =>
                          setEditing({ ...editing, [character.id]: { ...draft, [field.key]: e.target.value } })
                        }
                      />
                    </label>
                  ))}
                  {open[character.id] && (
                    <label className="story-sheet-field">
                      <input
                        type="checkbox"
                        checked={!!character.is_adult}
                        disabled={disabled}
                        onChange={e => void setAdult(character, e.target.checked)}
                      />
                      成人キャラ(データセットのR18生成を許可。未成年を示すタグがあると付けられません)
                    </label>
                  )}
                  <div className="story-actions">
                    <button
                      type="button"
                      onClick={() => setOpen({ ...open, [character.id]: !open[character.id] })}
                    >
                      {open[character.id] ? 'キャラシートを閉じる' : 'キャラシートを開く'}
                    </button>
                    {dirty && (
                      <button type="button" onClick={() => void saveSheet(character)} disabled={disabled}>
                        保存
                      </button>
                    )}
                  </div>
                </>
              )
            })()}
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
