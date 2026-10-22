import { useEffect, useRef, useState } from 'react'
import { guardFetch } from '../guardFetch'
import GrowthStages from './GrowthStages'
import ReferenceCandidates from './ReferenceCandidates'
import type { Character } from './StoryCharacters'

/** キャラシートで編集する項目。容姿は変えない部分、服装は場面で差し替える部分。 */
export type SheetField =
  | 'appearance_tags' | 'body_tags' | 'outfit_tags' | 'style_tags' | 'negative_tags' | 'trigger_word' | 'seed' | 'age'

/** 数字で送る項目(空欄は null) */
const NUMBER_FIELDS: Partial<Record<SheetField, string>> = { seed: '基準シード', age: '年齢' }

export const SHEET_FIELDS: { key: SheetField; label: string; placeholder: string; rows: number }[] = [
  { key: 'appearance_tags', label: '容姿(顔・髪・目。固定)', placeholder: '1girl, brown hair, long hair, two side up, pink scrunchie, yellow eyes', rows: 2 },
  { key: 'body_tags', label: '標準の体型(データセットの状態はここを置き換える)', placeholder: 'slim, medium breasts', rows: 1 },
  { key: 'outfit_tags', label: '普段の服装', placeholder: 'school uniform, cardigan, pleated skirt', rows: 1 },
  { key: 'style_tags', label: '画風(データセット用)', placeholder: 'best quality, masterpiece, flat color', rows: 1 },
  { key: 'negative_tags', label: 'ネガティブ', placeholder: 'short hair, blue eyes', rows: 1 },
  { key: 'trigger_word', label: 'トリガーワード', placeholder: 'kujo_yura', rows: 1 },
  { key: 'seed', label: '基準シード', placeholder: '3257661879', rows: 1 },
  { key: 'age', label: '年齢(歳。入れると年齢区分は自動で決まります: 18 歳未満は未成年、18 歳以上は成人)', placeholder: '1', rows: 1 },
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
  /** 参照画像の候補の画風の初期値(既定は漫画。キャラ別データセットではイラスト) */
  candidateStyle?: 'illustration' | 'manga'
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
  apiOrigin, character, onSaved, disabled, classes = STORY_CLASSES, showReference, fileUrl, candidateStyle, onDuplicated, onDeleted,
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
      // 未成年の定義を外すのは守りを緩める変更なので、パスワードを聞かれることがある
      const res = await guardFetch(`${apiOrigin}/api/story/characters/${character.id}/sheet`, {
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
      const numberLabel = NUMBER_FIELDS[key]
      if (numberLabel) {
        const trimmed = raw.trim()
        if (trimmed && !/^\d+$/.test(trimmed)) {
          setError(`${numberLabel}は数字で入力してください`)
          return null
        }
        body[key] = trimmed ? Number(trimmed) : null
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

  /** 容姿のうち体型のタグ(体つき・胸・腰など)を、標準の体型の欄へ移す(書きかけにするだけ。保存は「保存」で)。 */
  async function moveBodyTags() {
    setError(null)
    try {
      const res = await fetch(`${apiOrigin}/api/story/characters/split-body`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: value('appearance_tags') }),
      })
      if (!res.ok) throw new Error(await errorDetail(res))
      const { face, body } = await res.json() as { face: string; body: string }
      if (!body) {
        setError('容姿に体型のタグは見つかりませんでした。')
        return
      }
      const current = value('body_tags').trim()
      setDraft({ ...draft, appearance_tags: face, body_tags: [current, body].filter(Boolean).join(', ') })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
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
      const res = await guardFetch(`${apiOrigin}/api/story/characters/${character.id}/duplicate`, {
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
    if (!window.confirm(`「${character.name}」を削除しますか？(ごみ箱に入り、30日のあいだは元に戻せます)${where}`)) return
    try {
      const res = await guardFetch(`${apiOrigin}/api/story/characters/${character.id}`, { method: 'DELETE' })
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
          {field.key === 'body_tags' && (
            <button type="button" className={classes.button} disabled={busy || !value('appearance_tags').trim()}
              onClick={() => void moveBodyTags()}
              title="容姿のうち体つき・胸・腰などのタグを、この欄へ移します(保存するまでは書きかけです)">
              容姿から体型のタグを移す
            </button>
          )}
        </label>
      ))}

      <label className={classes.field}>
        <span className={classes.label}>年齢区分</span>
        <select
          value={character.is_minor ? 'minor' : character.is_adult ? 'adult' : 'undefined'}
          disabled={busy || character.age != null}
          title={character.age != null ? '年齢が入っているので、年齢区分は年齢で決まります' : undefined}
          onChange={e => void put(
            e.target.value === 'minor' ? { is_minor: true }
              : e.target.value === 'adult' ? { is_adult: true }
              : { is_adult: false, is_minor: false },
          )}
        >
          <option value="undefined">未定義</option>
          <option value="minor">未成年</option>
          <option value="adult">成人</option>
        </select>
        <span className={classes.label}>
          未成年: 性的な場面には描きません(外すときはコンテンツガードのパスワードが要ります)。
          成人: データセットのR18生成も使えます(未成年を示すタグがあると選べません)。
          未定義のキャラが性的な場面にいるときの扱いは、管理ページのコンテンツガードで決めます。
        </span>
      </label>

      <GrowthStages
        apiOrigin={apiOrigin}
        character={character}
        disabled={busy}
        classes={classes}
        onChanged={onSaved}
        onCreated={onDuplicated}
        save={put}
      />

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
          {fileUrl && (
            <ReferenceCandidates
              apiOrigin={apiOrigin}
              characterId={character.id}
              fileUrl={fileUrl}
              disabled={busy}
              defaultStyle={candidateStyle}
              buttonClass={classes.button}
              labelClass={classes.label}
              errorClass={classes.error}
              onChosen={() => {
                // 基準シードも変わるので、書きかけのシードは捨てる
                setDraft(({ seed: _seed, ...rest }) => rest)
                onSaved()
              }}
            />
          )}
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
