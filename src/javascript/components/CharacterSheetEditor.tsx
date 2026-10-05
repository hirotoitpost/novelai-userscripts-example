import { useEffect, useRef, useState } from 'react'
import type { Character } from './StoryCharacters'

/** キャラシートで編集する項目。容姿は変えない部分、服装は場面で差し替える部分。 */
export type SheetField = 'appearance_tags' | 'outfit_tags' | 'style_tags' | 'negative_tags' | 'trigger_word' | 'seed'

export const SHEET_FIELDS: { key: SheetField; label: string; placeholder: string; rows: number }[] = [
  { key: 'appearance_tags', label: '容姿(固定)', placeholder: '1girl, brown hair, long hair, two side up, pink scrunchie, yellow eyes', rows: 2 },
  { key: 'outfit_tags', label: '普段の服装', placeholder: 'school uniform, cardigan, pleated skirt', rows: 1 },
  { key: 'style_tags', label: '画風(データセット用)', placeholder: 'best quality, masterpiece, flat color', rows: 1 },
  { key: 'negative_tags', label: 'ネガティブ', placeholder: 'short hair, blue eyes', rows: 1 },
  { key: 'trigger_word', label: 'トリガーワード', placeholder: 'kujo_yura', rows: 1 },
  { key: 'seed', label: '基準シード', placeholder: '3257661879', rows: 1 },
]

export function sheetValue(character: Character, key: SheetField): string {
  const value = character[key]
  return value == null ? '' : String(value)
}

/** 見た目は使う画面に合わせる(物語の画面と、キャラ別データセットの画面で配色が違う)。 */
export interface SheetEditorClasses {
  field: string
  label: string
  input: string
  actions: string
  button: string
  primary: string
  danger?: string
  error: string
}

const STORY_CLASSES: SheetEditorClasses = {
  field: 'story-sheet-field',
  label: 'story-muted',
  input: 'story-premise',
  actions: 'story-actions',
  button: '',
  primary: '',
  error: 'story-error',
}

interface Props {
  apiOrigin: string
  character: Character
  /** 保存後に呼ぶ。呼び出し側でキャラ一覧を読み直す。 */
  onSaved: () => void
  disabled?: boolean
  classes?: SheetEditorClasses
  /** 参照画像の登録/削除も出すか(物語の画面では漫画v2側に既にあるので出さない) */
  showReference?: boolean
  fileUrl?: (path: string) => string
  /** 別名保存できた新しいキャラを渡す(呼び出し側でそのキャラを選び直す)。未指定なら別名保存を出さない */
  onDuplicated?: (created: Character) => void
  /** 削除できたら呼ぶ。未指定なら削除を出さない */
  onDeleted?: () => void
}

function readFileAsDataURL(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

async function errorDetail(res: Response): Promise<string> {
  const data = await res.json().catch(() => null)
  return (data && typeof data.detail === 'string' ? data.detail : null) ?? `保存に失敗しました (HTTP ${res.status})`
}

/**
 * 1人分のキャラシートの編集。物語の「登場人物」と、キャラ別データセットの画面の両方で使う。
 * 変更した項目だけを PUT /api/story/characters/{id}/sheet に送る。
 */
export default function CharacterSheetEditor({
  apiOrigin, character, onSaved, disabled, classes = STORY_CLASSES, showReference, fileUrl, onDuplicated, onDeleted,
}: Props) {
  const [draft, setDraft] = useState<Partial<Record<SheetField, string>>>({})
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  // 別のキャラに切り替えたら、書きかけの内容は捨てる
  useEffect(() => {
    setDraft({})
    setError(null)
  }, [character.id])

  const value = (key: SheetField) => draft[key] ?? sheetValue(character, key)
  const dirty = Object.entries(draft).some(([k, v]) => v !== sheetValue(character, k as SheetField))

  async function put(body: Record<string, string | number | boolean | null>): Promise<boolean> {
    setError(null)
    setSaving(true)
    try {
      const res = await fetch(`${apiOrigin}/api/story/characters/${character.id}/sheet`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error(await errorDetail(res))
      onSaved()
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return false
    } finally {
      setSaving(false)
    }
  }

  /** 書きかけの変更を送信用の形にする。基準シードが数字でなければ null を返す。 */
  function changes(): Record<string, string | number | null> | null {
    const body: Record<string, string | number | null> = {}
    for (const [key, raw] of Object.entries(draft) as [SheetField, string][]) {
      if (raw === sheetValue(character, key)) continue
      if (key === 'seed') {
        const trimmed = raw.trim()
        if (trimmed && !/^\d+$/.test(trimmed)) {
          setError('基準シードは数字で入力してください')
          return null
        }
        body.seed = trimmed ? Number(trimmed) : null
      } else {
        body[key] = raw.trim()
      }
    }
    return body
  }

  async function save() {
    const body = changes()
    if (body && (await put(body))) setDraft({})
  }

  /** 別名保存: 今の内容(書きかけの変更を含む)を新しい名前のキャラとして保存する。元のキャラは変えない。 */
  async function saveAs() {
    const body = changes()
    if (!body || !onDuplicated) return
    const name = window.prompt('新しいキャラ名', `${character.name} (2)`)?.trim()
    if (!name) return
    // トリガーワードはデータセットの保存先フォルダにも使うので、元キャラと別にする
    const currentTrigger = value('trigger_word').trim()
    const trigger = window.prompt(
      'トリガーワード(他のキャラと重ならないもの。空欄ならなし)',
      currentTrigger ? `${currentTrigger}_2` : '',
    )
    if (trigger === null) return
    setError(null)
    setSaving(true)
    try {
      const res = await fetch(`${apiOrigin}/api/story/characters/${character.id}/duplicate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, sheet: { ...body, trigger_word: trigger.trim() } }),
      })
      if (!res.ok) throw new Error(await errorDetail(res))
      setDraft({})
      onDuplicated(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  /** 削除: キャラは物語をまたいで共有されているので、使われている物語を示してから確認する。 */
  async function remove() {
    if (!onDeleted) return
    setError(null)
    const usage = await fetch(`${apiOrigin}/api/story/characters/${character.id}/usage`)
      .then(r => (r.ok ? r.json() : null))
      .catch(() => null) as { stories: number; scenes: number; story_titles: string[] } | null
    const where = usage && usage.scenes > 0
      ? `\n\n${usage.stories}件の物語・${usage.scenes}シーンで使われています。削除すると割り当ても外れます。\n` +
        usage.story_titles.map(t => `・${t}`).join('\n')
      : '\n\nどの物語のシーンにも割り当てられていません。'
    if (!window.confirm(`「${character.name}」を削除しますか？元に戻せません。${where}`)) return
    try {
      const res = await fetch(`${apiOrigin}/api/story/characters/${character.id}`, { method: 'DELETE' })
      if (!res.ok) throw new Error(await errorDetail(res))
      onDeleted()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function uploadReference(file: File) {
    if (!file.type.startsWith('image/')) return
    setError(null)
    try {
      const res = await fetch(`${apiOrigin}/api/manga-v2/characters/${character.id}/reference`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image: await readFileAsDataURL(file) }),
      })
      if (!res.ok) throw new Error(await errorDetail(res))
      onSaved()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function removeReference() {
    if (!window.confirm(`${character.name}の参照画像を外しますか？(画像ファイルは残ります)`)) return
    await fetch(`${apiOrigin}/api/manga-v2/characters/${character.id}/reference`, { method: 'DELETE' }).catch(() => {})
    onSaved()
  }

  const busy = disabled || saving

  return (
    <>
      {SHEET_FIELDS.map(field => (
        <label key={field.key} className={classes.field}>
          <span className={classes.label}>{field.label}</span>
          <textarea
            className={classes.input}
            rows={field.rows}
            placeholder={field.placeholder}
            value={value(field.key)}
            disabled={busy}
            onChange={e => setDraft({ ...draft, [field.key]: e.target.value })}
          />
        </label>
      ))}

      <label className={classes.field}>
        <span>
          <input
            type="checkbox"
            checked={!!character.is_adult}
            disabled={busy}
            onChange={e => void put({ is_adult: e.target.checked })}
          />
          {' '}成人キャラ(データセットのR18生成を許可。未成年を示すタグがあると付けられません)
        </span>
      </label>

      {showReference && (
        <div className={classes.field}>
          <span className={classes.label}>参照画像</span>
          {character.reference_image_path && fileUrl && (
            <img className="chards-ref" src={fileUrl(character.reference_image_path)} alt={`${character.name}の参照画像`} />
          )}
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            hidden
            onChange={e => {
              const file = e.target.files?.[0]
              if (file) void uploadReference(file)
              e.target.value = ''
            }}
          />
          <div className={classes.actions}>
            <button type="button" className={classes.button} onClick={() => fileRef.current?.click()} disabled={busy}>
              {character.reference_image_path ? '参照画像を差し替え' : '参照画像を登録'}
            </button>
            {character.reference_image_path && (
              <button type="button" className={classes.button} onClick={() => void removeReference()} disabled={busy}>
                外す
              </button>
            )}
          </div>
        </div>
      )}

      <div className={classes.actions}>
        {dirty && (
          <>
            <button type="button" className={classes.button} onClick={() => setDraft({})} disabled={busy}>
              元に戻す
            </button>
            <button type="button" className={classes.primary} onClick={() => void save()} disabled={busy}>
              {saving ? '保存中…' : '上書き保存'}
            </button>
          </>
        )}
        {onDuplicated && (
          <button type="button" className={classes.button} onClick={() => void saveAs()} disabled={busy}>
            別名で保存
          </button>
        )}
        {onDeleted && (
          <button type="button" className={classes.danger ?? classes.button} onClick={() => void remove()} disabled={busy}>
            削除
          </button>
        )}
      </div>
      {error && <div className={classes.error} role="alert">{error}</div>}
    </>
  )
}
