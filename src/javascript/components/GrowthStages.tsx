import { useEffect, useState } from 'react'
import { guardFetch } from '../guardFetch'
import type { SheetEditorClasses } from './CharacterSheetEditor'
import type { Character } from './StoryCharacters'

/**
 * キャラシートの「成長段階」: 同じ人物の年齢ごとのキャラシート(新生児・1歳 など)を、元のキャラにつなぐ。
 * 元のキャラでは段階の一覧と「成長段階を追加」(今のシートを写して段階を作る)、段階では元のキャラ・段階の名前・順番を出す。
 * 年齢区分はバックエンドで決める: 年齢の入った段階は年齢で(18 歳以上は成人)、年齢のない段階は元のキャラが未成年なら未成年。
 */

interface Props {
  apiOrigin: string
  character: Character
  disabled?: boolean
  classes: SheetEditorClasses
  /** つなぎ方を変えたあとに呼ぶ(呼び出し側でキャラを読み直す) */
  onChanged: () => void
  /** 段階を作れたら渡す。未指定なら「成長段階を追加」を出さない */
  onCreated?: (created: Character) => void
  /** キャラシートの上書き保存(送った項目だけ) */
  save: (body: Record<string, string | number | boolean | null>) => Promise<boolean>
}

/** 幼い順: 年齢、同じ年齢(か年齢なし)なら順番、最後に名前 */
export function byGrowth(a: Character, b: Character): number {
  const big = Number.MAX_SAFE_INTEGER
  return (a.age ?? big) - (b.age ?? big) || (a.growth_order ?? big) - (b.growth_order ?? big) || a.name.localeCompare(b.name)
}

export default function GrowthStages({ apiOrigin, character, disabled, classes, onChanged, onCreated, save }: Props) {
  const [all, setAll] = useState<Character[]>([])
  const [label, setLabel] = useState('')
  const [order, setOrder] = useState('')
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    fetch(`${apiOrigin}/api/story/characters`)
      .then(r => (r.ok ? r.json() : []))
      .then(setAll)
      .catch(() => setAll([]))
  }
  useEffect(load, [apiOrigin, character.id, character.growth_of])
  useEffect(() => {
    setLabel(character.growth_label ?? '')
    setOrder(character.growth_order == null ? '' : String(character.growth_order))
    setError(null)
  }, [character.id, character.growth_label, character.growth_order])

  const baseId = character.growth_of ?? null
  const base = baseId === null ? null : all.find(c => c.id === baseId) ?? null
  const personId = baseId ?? character.id
  const stages = all.filter(c => c.growth_of === personId).sort(byGrowth)
  const candidates = all.filter(c => c.id !== character.id && c.growth_of == null)

  async function change(body: Record<string, string | number | boolean | null>) {
    if (await save(body)) {
      load()
      onChanged()
    }
  }

  function saveLabel() {
    const trimmed = order.trim()
    if (trimmed && !/^\d+$/.test(trimmed)) {
      setError('順番は数字で入力してください')
      return
    }
    setError(null)
    void change({ growth_label: label.trim() || null, growth_order: trimmed ? Number(trimmed) : null })
  }

  /** 今のシートを写して、この人物の新しい段階を作る */
  async function addStage() {
    if (!onCreated) return
    const stageLabel = window.prompt('段階の名前(新生児・6か月・1歳 など)')?.trim()
    if (!stageLabel) return
    const person = base ?? character
    const name = window.prompt('新しい段階のキャラ名', `${person.name}(${stageLabel})`)?.trim()
    if (!name) return
    const ageText = window.prompt('年齢(歳。18 歳未満は未成年、18 歳以上は成人になります。空欄なら入れない)', '')
    if (ageText === null) return
    if (ageText.trim() && !/^\d+$/.test(ageText.trim())) {
      setError('年齢は数字で入力してください')
      return
    }
    const nextOrder = Math.max(-1, ...stages.map(s => s.growth_order ?? -1)) + 1
    setError(null)
    try {
      const res = await guardFetch(`${apiOrigin}/api/story/characters/${character.id}/duplicate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // トリガーワードは他のキャラと重ねられないので写さない
        body: JSON.stringify({
          name,
          sheet: {
            growth_of: personId, growth_label: stageLabel, growth_order: nextOrder, trigger_word: '',
            ...(ageText.trim() ? { age: Number(ageText.trim()) } : {}),
          },
        }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => null)
        throw new Error((data && typeof data.detail === 'string' ? data.detail : null) ?? `作れませんでした (HTTP ${res.status})`)
      }
      load()
      onCreated(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className={classes.field}>
      <span className={classes.label}>
        成長段階(同じ人物の年齢ごとのキャラシート)。年齢の入った段階は年齢で年齢区分が決まり、年齢のない段階は元のキャラが
        未成年なら未成年になります。未成年から成人の段階を作るときは、コンテンツガードのパスワードを聞かれます。
      </span>
      {baseId !== null ? (
        <>
          <span>「{base?.name ?? `ID ${baseId}`}」の成長段階</span>
          <div className={classes.actions}>
            <input className={classes.input} value={label} placeholder="段階の名前(1歳 など)" disabled={disabled}
              onChange={e => setLabel(e.target.value)} aria-label="段階の名前" />
            <input className={classes.input} value={order} placeholder="順番(幼い順)" disabled={disabled} inputMode="numeric"
              onChange={e => setOrder(e.target.value)} aria-label="段階の順番" style={{ maxWidth: '8rem' }} />
            <button type="button" className={classes.button} disabled={disabled} onClick={saveLabel}>名前と順番を保存</button>
            <button type="button" className={classes.button} disabled={disabled}
              onClick={() => window.confirm('元のキャラとのつながりを外しますか？(キャラシートは残ります)') && void change({ growth_of: null })}>
              段階から外す
            </button>
          </div>
        </>
      ) : stages.length === 0 && candidates.length > 0 && (
        <select value="" disabled={disabled} aria-label="他のキャラの成長段階にする"
          onChange={e => {
            const target = all.find(c => c.id === Number(e.target.value))
            if (target && window.confirm(`「${character.name}」を「${target.name}」の成長段階にしますか？`)) {
              void change({ growth_of: target.id })
            }
          }}>
          <option value="">他のキャラの成長段階にする…</option>
          {candidates.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      )}
      {stages.length > 0 && (
        <ul>
          {stages.map(s => (
            <li key={s.id}>
              {s.growth_label || '(名前なし)'}{s.age != null && `(${s.age}歳)`}: {s.name}{s.id === character.id && '(このキャラ)'}
            </li>
          ))}
        </ul>
      )}
      {onCreated && (
        <div className={classes.actions}>
          <button type="button" className={classes.button} disabled={disabled} onClick={() => void addStage()}
            title="今のキャラシートを写して、この人物の新しい成長段階を作ります">
            成長段階を追加
          </button>
        </div>
      )}
      {error && <div className={classes.error} role="alert">{error}</div>}
    </div>
  )
}
