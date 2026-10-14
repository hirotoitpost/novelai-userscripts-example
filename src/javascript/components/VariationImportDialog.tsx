import { useEffect, useMemo, useState } from 'react'
import type { DragEvent } from 'react'
import { apiFetch } from '../api'
import './PublishDialog.css'
import './VariationImportDialog.css'

/** キャラ別データセットのバリエーションの軸 */
export type Axis = 'framings' | 'poses' | 'outfits' | 'expressions' | 'locations'
export type Variations = Record<Axis, string[]>

export const AXES: { key: Axis; label: string }[] = [
  { key: 'framings', label: '構図' },
  { key: 'poses', label: 'ポーズ' },
  { key: 'outfits', label: '服装' },
  { key: 'expressions', label: '表情' },
  { key: 'locations', label: '場所' },
]

/** キャラシートの項目のうち、キーワードから足せるもの(容姿・普段の服装・画風) */
export type SheetField = 'appearance' | 'body' | 'outfit' | 'style'
export const SHEET_FIELDS: { key: SheetField; label: string; column: 'appearance_tags' | 'body_tags' | 'outfit_tags' | 'style_tags' }[] = [
  { key: 'appearance', label: '容姿(顔・髪・目)', column: 'appearance_tags' },
  { key: 'body', label: '標準の体型', column: 'body_tags' },
  { key: 'outfit', label: '普段の服装', column: 'outfit_tags' },
  { key: 'style', label: '画風', column: 'style_tags' },
]

/** キーワードを置く枠: バリエーションの軸、キャラシートの項目(sheet:…)、分類が分からないもの('') */
type Zone = Axis | `sheet:${SheetField}` | ''

/** キャラシートを変えるキャラ(キャラ別データセットで選んでいるキャラ) */
export interface SheetCharacter {
  id: number
  name: string
  appearance_tags: string
  body_tags?: string | null
  outfit_tags?: string | null
  style_tags?: string | null
}

/** POST /api/lora-dataset/variations/extract の結果 */
interface Extracted {
  axes: Variations
  /** キャラシートの側のキーワード(容姿、品質・画風) */
  sheet: { appearance: string[]; body: string[]; style: string[] }
  other: string[]
  adult: string[]
  skipped: string[]
  /** 軸が分からなかったキーワードの、チャンクの分類から付けた見当(軸か、キャラシートの項目) */
  suggested: Partial<Record<string, Axis | 'appearance' | 'body' | 'style'>>
}

/** 人数のタグ(1girl, 2boys, solo など)。キーワードとしては取り出さないが、キャラシートの容姿には入っている */
const COUNT_TAG = /^((\d\+?|multiple )?(girls?|boys?|others?)|solo)$/i

function splitTags(tags: string | null | undefined): string[] {
  return (tags ?? '').split(',').map(t => t.trim()).filter(Boolean)
}

interface HistoryEntry {
  id: number
  prompt: string
  created_at: string
  characters?: { prompt?: string }[]
}

interface ReverseResult {
  source: string
  positive: string
  characters?: { prompt: string }[]
}

/** GET /api/chunks/imported の1件(プロンプトチャンク。is_category なら分類で、child_order が中身) */
interface Chunk {
  id: string
  label: string
  expansion: string
  is_category: boolean
  child_order: string[] | null
}

/** 読み込むチャンク(分類の名前と、そこから見当を付けた軸も持つ) */
interface ChunkSource {
  id: string
  label: string
  category: string
  text: string
}

interface Props {
  token: string
  /** いまの候補(登録済みのものは足さない) */
  existing: Variations
  /** キャラシートを変えるキャラ(選んでいなければ null。キャラシートの枠は使えない) */
  character: SheetCharacter | null
  onClose: () => void
  /** 足したあとの候補の一覧と、候補に足した数、キャラシートに入れた数 */
  onAdded: (variations: Variations, added: number, sheetAdded: number) => void
}

/** 分類の名前から、軸の見当を付ける(サーバーの dataset_variations.hint_axis と同じ語) */
const HINT_WORDS: [Axis | 'appearance' | 'body' | 'style', string[]][] = [
  ['style', ['絵柄', '画風', 'アーティスト', '作家', 'artist', 'style']],
  ['body', ['体型', '体格', 'スタイル', 'body', 'figure']],
  ['appearance', ['髪', '容姿', '瞳', '肌', '顔立ち', 'hair', 'appearance']],
  ['expressions', ['表情', '顔', 'expression', 'face']],
  ['outfits', ['服', '衣装', '衣服', 'アクセ', 'コスチューム', 'outfit', 'cloth', 'costume']],
  ['framings', ['カメラ', '構図', '視点', 'アングル', 'camera', 'framing', 'angle']],
  ['locations', ['風景', '場所', '背景', 'ロケ', 'location', 'scene', 'background']],
  ['poses', ['ポーズ', '姿勢', '動作', '仕草', 'pose', 'action']],
]

function hintAxis(label: string): Axis | 'appearance' | 'body' | 'style' | null {
  const text = label.toLowerCase()
  return HINT_WORDS.find(([, words]) => words.some(w => text.includes(w)))?.[0] ?? null
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

const NO_CATEGORY = '(分類なし)'
const MAX_PROMPTS = 400
const DRAG_TYPE = 'text/x-variation-keyword'

/**
 * キーワードを読み込んで、バリエーションの候補に足すダイアログ。
 * 読み込み元: プロンプトの貼り付け / 最近の画像生成の履歴 / 画像(NovelAI の埋め込み。無ければ推定) / プロンプトチャンク。
 * 取り出したキーワードは軸ごとの枠に並ぶ。チェックを入れたものだけを足す。分類が違うもの・分からないものは、
 * 別の枠へドラッグするか、キーワードの横の欄で軸を選んで振り分ける。
 * 容姿・画風などキャラシートの側のキーワードは、キャラシートの枠(容姿・普段の服装・画風)に並び、チェックを入れると
 * 選んでいるキャラのキャラシートに入る(足すか、置き換えるかを選べる)。
 */
export default function VariationImportDialog({ token, existing, character, onClose, onAdded }: Props) {
  const [text, setText] = useState('')
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [chunks, setChunks] = useState<Chunk[]>([])
  const [chunkQuery, setChunkQuery] = useState('')
  const [chunkCategory, setChunkCategory] = useState('')
  const [chunkPicked, setChunkPicked] = useState<Set<string>>(new Set())
  const [extracted, setExtracted] = useState<Extracted | null>(null)
  // キーワードごとの、置いてある枠('' は「分類が分からないもの」)と、足すかどうか
  const [placed, setPlaced] = useState<Record<string, Zone>>({})
  const [checked, setChecked] = useState<Record<string, boolean>>({})
  const [dragOver, setDragOver] = useState<Zone | null>(null)
  // キャラシートへの入れ方: いまの内容に足す / チェックしたもので置き換える
  const [sheetMode, setSheetMode] = useState<'append' | 'replace'>('append')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiFetch<HistoryEntry[]>(token, '/api/chunks/history?limit=30')
      .then(entries => setHistory(entries.filter(e => (e.prompt ?? '').trim())))
      .catch(() => {})
    apiFetch<Chunk[]>(token, '/api/chunks/imported').then(setChunks).catch(() => {})
  }, [token])

  // チャンク(分類でないもの)に、分類の名前を付ける
  const chunkSources = useMemo<ChunkSource[]>(() => {
    const parent = new Map<string, Chunk>()
    for (const category of chunks.filter(c => c.is_category)) {
      for (const child of category.child_order ?? []) parent.set(child, category)
    }
    return chunks
      .filter(c => !c.is_category && (c.expansion ?? '').trim())
      .map(c => {
        const owner = parent.get(c.id)
        const category = owner && owner.id !== 'default' ? owner.label : NO_CATEGORY
        return { id: c.id, label: c.label, category, text: c.expansion }
      })
  }, [chunks])
  const categories = useMemo(() => [...new Set(chunkSources.map(c => c.category))].sort((a, b) => a.localeCompare(b, 'ja')), [chunkSources])
  const shownChunks = chunkSources.filter(c =>
    (!chunkCategory || c.category === chunkCategory)
    && (!chunkQuery.trim() || `${c.label} ${c.text}`.toLowerCase().includes(chunkQuery.trim().toLowerCase())))

  const isSheet = (zone: Zone): zone is `sheet:${SheetField}` => zone.startsWith('sheet:')
  const sheetColumn = (zone: `sheet:${SheetField}`) => SHEET_FIELDS.find(f => `sheet:${f.key}` === zone)!.column
  /** その枠に、すでに入っているキーワードか(候補に登録済み / キャラシートに入っている) */
  const known = (zone: Zone, tag: string) => {
    if (zone === '') return false
    const values = isSheet(zone) ? (character ? splitTags(character[sheetColumn(zone)]) : []) : existing[zone]
    return values.some(v => v.toLowerCase() === tag.toLowerCase())
  }
  /** その枠に置いたキーワードを、足すものとして選べるか(キャラシートの枠は、キャラを選んでいるときだけ) */
  const usable = (zone: Zone) => zone !== '' && (!isSheet(zone) || character !== null)
  /** 置き換えで入れる枠か(キャラシートの枠で、置き換えを選んでいるとき)。すでに入っているキーワードも、残すかどうかを選ぶ */
  const replacing = (zone: Zone) => isSheet(zone) && sheetMode === 'replace'
  /** その枠で、チェックを付け外しできるキーワードか */
  const tickable = (zone: Zone, tag: string) => usable(zone) && (replacing(zone) || !known(zone, tag))

  /** キャラシートへの入れ方を変える。置き換えにしたら、すでに入っているキーワードは残すものとして選ぶ */
  const changeSheetMode = (mode: 'append' | 'replace') => {
    setSheetMode(mode)
    const next = { ...checked }
    for (const tag of Object.keys(placed)) {
      const zone = placed[tag]
      if (isSheet(zone) && known(zone, tag)) next[tag] = mode === 'replace'
    }
    setChecked(next)
  }
  const append = (prompt: string) => setText(t => (t.trim() ? `${t.trim()}\n${prompt}` : prompt))

  const run = async (label: string, work: () => Promise<void>) => {
    setBusy(label)
    setError(null)
    try {
      await work()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const fromImage = (file: File) => run('画像を読み取っています…', async () => {
    const result = await apiFetch<ReverseResult>(token, '/api/llm/reverse-prompt/tags', { image: await readAsDataUrl(file) })
    const prompt = [result.positive, ...(result.characters ?? []).map(c => c.prompt)].filter(p => p.trim()).join(', ')
    if (!prompt) throw new Error('この画像からはプロンプトを読み取れませんでした。')
    append(prompt)
  })

  const pasted = text.split('\n').map(l => l.trim()).filter(Boolean)
  const pickedChunks = chunkSources.filter(c => chunkPicked.has(c.id))
  const sourceCount = pasted.length + pickedChunks.length

  const extract = () => run('キーワードを取り出しています…', async () => {
    const prompts = [...pasted, ...pickedChunks.map(c => c.text)].slice(0, MAX_PROMPTS)
    const hints = [...pasted.map(() => null), ...pickedChunks.map(c => hintAxis(c.category))].slice(0, MAX_PROMPTS)
    const result = await apiFetch<Extracted>(token, '/api/lora-dataset/variations/extract', { prompts, hints })
    const nextPlaced: Record<string, Zone> = {}
    const nextChecked: Record<string, boolean> = {}
    const zoneOf = (hint: Axis | 'appearance' | 'body' | 'style' | undefined): Zone =>
      hint === undefined ? '' : hint === 'appearance' || hint === 'body' || hint === 'style' ? `sheet:${hint}` : hint
    // 軸が分かったものは、登録済みでなければ足す。分からないものは、見当があればその枠に置く(成人向けは選ぶまで足さない)
    for (const { key } of AXES) {
      for (const tag of result.axes[key]) {
        nextPlaced[tag] = key
        nextChecked[tag] = !known(key, tag)
      }
    }
    // キャラシートの側のものは、キャラシートの枠に置く(キャラシートを変えるので、選ぶまで入れない)
    for (const field of ['appearance', 'body', 'style'] as const) {
      for (const tag of result.sheet[field]) {
        nextPlaced[tag] = `sheet:${field}`
        nextChecked[tag] = false
      }
    }
    for (const tag of result.other) {
      const zone = zoneOf(result.suggested[tag])
      nextPlaced[tag] = zone
      nextChecked[tag] = zone !== '' && !isSheet(zone) && !known(zone, tag)
    }
    for (const tag of result.adult) {
      nextPlaced[tag] = zoneOf(result.suggested[tag])
      nextChecked[tag] = false
    }
    setExtracted(result)
    setPlaced(nextPlaced)
    setChecked(nextChecked)
  })

  /** キーワードを別の枠へ移す。自分で移したものは、足すものとして選ぶ(すでに入っているもの・使えない枠は選ばない) */
  const move = (tag: string, to: Zone) => {
    if (placed[tag] === to) return
    setPlaced({ ...placed, [tag]: to })
    setChecked({ ...checked, [tag]: tickable(to, tag) })
  }

  const all = Object.keys(placed)
  const adult = new Set(extracted?.adult ?? [])
  const tagsIn = (zone: Zone) => all.filter(tag => placed[tag] === zone)
  const picked = (zone: Zone) => tagsIn(zone).filter(tag => checked[tag] && tickable(zone, tag))
  const chosen = AXES.map(({ key }) => ({ axis: key, values: picked(key) })).filter(group => group.values.length > 0)
  const total = chosen.reduce((sum, group) => sum + group.values.length, 0)
  // キャラシートに入れるもの(項目ごと)
  // 置き換えのときは、キーワードの出ている項目をすべて書き換える(すべて外せば、人数のタグだけが残る)
  const sheetChosen = SHEET_FIELDS.map(f => ({ ...f, zone: `sheet:${f.key}` as Zone, values: picked(`sheet:${f.key}`) }))
    .filter(f => (sheetMode === 'replace' ? tagsIn(f.zone).length > 0 && character !== null : f.values.length > 0))
  /** その項目に入れる内容。足す: いまの内容の後ろに足す。置き換える: 人数のタグと、チェックしたものだけにする */
  const sheetValue = (column: 'appearance_tags' | 'body_tags' | 'outfit_tags' | 'style_tags', values: string[]) => {
    const current = splitTags(character?.[column])
    const kept = sheetMode === 'replace' ? current.filter(tag => COUNT_TAG.test(tag)) : current
    return [...new Set([...kept, ...values])].join(', ')
  }
  // 置き換えで、いまの内容から変わる項目があるか(変わらなければ、何もしない)
  const sheetChanges = sheetChosen.filter(f => sheetValue(f.column, f.values) !== splitTags(character?.[f.column]).join(', '))
  // 変わる項目に入るキーワードの数(足すときは足す数、置き換えるときは置き換えたあとに残る数)
  const sheetTotal = sheetChanges.reduce((sum, f) => sum + f.values.length, 0)

  const add = () => run('足しています…', async () => {
    let variations = existing
    for (const group of chosen) {
      variations = await apiFetch<Variations>(token, '/api/lora-dataset/variations', group)
    }
    if (character && sheetChanges.length > 0) {
      const body = Object.fromEntries(sheetChanges.map(f => [f.column, sheetValue(f.column, f.values)]))
      const res = await fetch(`/api/story/characters/${character.id}/sheet`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const detail = (await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`
        throw new Error(`キャラシートを変えられませんでした: ${detail}${total > 0 ? '(候補には足してあります)' : ''}`)
      }
    }
    onAdded(variations, total, character && sheetChanges.length > 0 ? sheetTotal : 0)
  })

  const onDrop = (e: DragEvent, zone: Zone) => {
    e.preventDefault()
    setDragOver(null)
    const tag = e.dataTransfer.getData(DRAG_TYPE) || e.dataTransfer.getData('text/plain')
    if (tag in placed) move(tag, zone)
  }

  const zone = (key: Zone, label: string) => {
    const tags = tagsIn(key)
    const selectable = tags.filter(tag => tickable(key, tag))
    const allChecked = selectable.length > 0 && selectable.every(tag => checked[tag])
    return (
      <section key={key || 'unknown'}
        className={`vi-zone${dragOver === key ? ' vi-zone--over' : ''}${key === '' ? ' vi-zone--unknown' : ''}${isSheet(key) ? ' vi-zone--sheet' : ''}`}
        aria-label={label}
        onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; setDragOver(key) }}
        onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragOver(null) }}
        onDrop={e => onDrop(e, key)}>
        <h3>
          {label}
          <small>{tags.length}個{usable(key) && `(${isSheet(key) ? '入れる' : '足す'} ${picked(key).length}個)`}</small>
          {selectable.length > 0 && (
            <button type="button" className="vi-all"
              onClick={() => setChecked({ ...checked, ...Object.fromEntries(selectable.map(tag => [tag, !allChecked])) })}>
              {allChecked ? 'すべて外す' : 'すべて選ぶ'}
            </button>
          )}
        </h3>
        {replacing(key) && isSheet(key) && tags.length > 0 && (() => {
          // 置き換えると消える、いまのキャラシートのキーワード(人数のタグは残る)
          const keep = new Set(picked(key).map(t => t.toLowerCase()))
          const removed = splitTags(character?.[sheetColumn(key)]).filter(t => !COUNT_TAG.test(t) && !keep.has(t.toLowerCase()))
          return removed.length > 0 && <p className="vi-removed">消えるもの: {removed.join(', ')}</p>
        })()}
        {tags.length === 0
          ? <p className="vi-empty">{key === '' ? 'ありません' : 'ここへドラッグすると、この分類になります'}</p>
          : (
            <ul>
              {tags.map(tag => {
                const registered = known(key, tag)
                return (
                  <li key={tag} draggable className={`vi-row${checked[tag] ? ' vi-row--on' : ''}`}
                    onDragStart={e => {
                      e.dataTransfer.setData(DRAG_TYPE, tag)
                      e.dataTransfer.setData('text/plain', tag)
                      e.dataTransfer.effectAllowed = 'move'
                    }}
                    onDragEnd={() => setDragOver(null)}>
                    <label className="vi-pick">
                      <input type="checkbox" checked={!!checked[tag] && usable(key)} disabled={!tickable(key, tag)}
                        aria-label={`「${tag}」を足す`}
                        onChange={e => setChecked({ ...checked, [tag]: e.target.checked })} />
                      <span className="vi-tag">{tag}</span>
                    </label>
                    {adult.has(tag) && <span className="vi-r18" title="成人向け(R18 のデータセット用)">R18</span>}
                    {registered && <span className="vi-known">{isSheet(key) ? '入っています' : '登録済み'}</span>}
                    <select value={key} aria-label={`「${tag}」の分類`} title="分類を変える(ドラッグでも移せます)"
                      onChange={e => move(tag, e.target.value as Zone)}>
                      <option value="">分からない</option>
                      <optgroup label="バリエーション">
                        {AXES.map(a => <option key={a.key} value={a.key}>{a.label}</option>)}
                      </optgroup>
                      <optgroup label="キャラシート">
                        {SHEET_FIELDS.map(f => <option key={f.key} value={`sheet:${f.key}`}>{f.label}</option>)}
                      </optgroup>
                    </select>
                  </li>
                )
              })}
            </ul>
          )}
      </section>
    )
  }

  return (
    <div className="publish-overlay" onClick={busy ? undefined : onClose}>
      <div className="publish-dialog vi-dialog" role="dialog" aria-modal="true" aria-labelledby="vi-title" onClick={e => e.stopPropagation()}>
        <h2 id="vi-title">キーワードを読み込む</h2>
        <p className="publish-hint">
          プロンプトやプロンプトチャンクから、構図・ポーズ・服装・表情・場所のキーワードを取り出して、バリエーションの候補に足します。
          髪・目・体などの容姿と、品質・画風のキーワードは、キャラシートの枠に並びます(選ぶと、選んでいるキャラのキャラシートに入ります)。
        </p>
        <label className="publish-field">
          <span>プロンプト(貼り付け。1行に1つ。下の2つからも入れられます)</span>
          <textarea className="vi-text" rows={3} value={text} onChange={e => setText(e.target.value)}
            placeholder="例: 1girl, smile, school uniform, classroom, sitting, cowboy shot" />
        </label>
        <div className="vi-sources">
          <label className="publish-field">
            <span>最近の画像生成から</span>
            <select value="" disabled={history.length === 0} aria-label="最近の画像生成のプロンプトを入れる"
              onChange={e => {
                const entry = history.find(h => String(h.id) === e.target.value)
                if (entry) append([entry.prompt, ...(entry.characters ?? []).map(c => c.prompt ?? '')].filter(p => p.trim()).join(', '))
              }}>
              <option value="">{history.length === 0 ? '(履歴がありません)' : '選ぶと、上の欄に入ります'}</option>
              {history.map(h => (
                <option key={h.id} value={h.id}>{h.created_at.slice(5, 16).replace('T', ' ')} {h.prompt.slice(0, 60)}</option>
              ))}
            </select>
          </label>
          <label className="publish-field">
            <span>画像から(NovelAI の埋め込み。無ければ絵から推定)</span>
            <input type="file" accept="image/*" disabled={busy !== null}
              onChange={e => {
                const file = e.target.files?.[0]
                e.target.value = ''
                if (file) void fromImage(file)
              }} />
          </label>
        </div>

        <details className="vi-chunks">
          <summary>プロンプトチャンクから{chunkPicked.size > 0 && `(${chunkPicked.size}個選択中)`}</summary>
          {chunkSources.length === 0
            ? <p className="publish-hint">読み込めるチャンクがありません(プロンプトチャンクのページで、公式のチャンクを取り込んでください)。</p>
            : (
              <>
                <div className="vi-chunk-filters">
                  <select value={chunkCategory} aria-label="チャンクの分類で絞り込む" onChange={e => setChunkCategory(e.target.value)}>
                    <option value="">すべての分類({chunkSources.length})</option>
                    {categories.map(c => (
                      <option key={c} value={c}>{c}({chunkSources.filter(s => s.category === c).length})</option>
                    ))}
                  </select>
                  <input value={chunkQuery} placeholder="名前・中身で探す" aria-label="チャンクを探す" onChange={e => setChunkQuery(e.target.value)} />
                  <button type="button" disabled={shownChunks.length === 0}
                    onClick={() => {
                      const next = new Set(chunkPicked)
                      const allShown = shownChunks.every(c => next.has(c.id))
                      for (const c of shownChunks) allShown ? next.delete(c.id) : next.add(c.id)
                      setChunkPicked(next)
                    }}>
                    {shownChunks.length > 0 && shownChunks.every(c => chunkPicked.has(c.id)) ? '表示中をすべて外す' : `表示中の${shownChunks.length}個をすべて選ぶ`}
                  </button>
                </div>
                <ul className="vi-chunk-list">
                  {shownChunks.map(c => (
                    <li key={c.id}>
                      <label>
                        <input type="checkbox" checked={chunkPicked.has(c.id)}
                          onChange={e => {
                            const next = new Set(chunkPicked)
                            e.target.checked ? next.add(c.id) : next.delete(c.id)
                            setChunkPicked(next)
                          }} />
                        <span className="vi-chunk-name">{c.label}</span>
                        <span className="vi-chunk-cat">{c.category}</span>
                        <span className="vi-chunk-text">{c.text}</span>
                      </label>
                    </li>
                  ))}
                  {shownChunks.length === 0 && <li className="vi-empty">当てはまるチャンクがありません</li>}
                </ul>
              </>
            )}
        </details>

        <div className="publish-actions">
          <button type="button" onClick={() => void extract()} disabled={busy !== null || sourceCount === 0}>
            キーワードを取り出す{sourceCount > 0 && `(${sourceCount}件から)`}
          </button>
        </div>
        {sourceCount > MAX_PROMPTS && <p className="publish-error">一度に読み込めるのは {MAX_PROMPTS} 件までです(先頭の {MAX_PROMPTS} 件を使います)。</p>}
        {busy && <p className="publish-hint" role="status">{busy}</p>}
        {error && <p className="publish-error" role="alert">{error}</p>}

        {extracted && (
          <div className="vi-result">
            {all.length === 0
              ? <p className="publish-hint">足せるキーワードは見つかりませんでした。</p>
              : (
                <p className="publish-hint">
                  チェックを入れたものだけを足します。分類が違うものは、別の枠へドラッグするか、横の欄で選び直してください。
                </p>
              )}
            {all.length > 0 && <h3 className="vi-group">バリエーションの候補</h3>}
            {all.length > 0 && AXES.map(a => zone(a.key, a.label))}
            {all.length > 0 && (
              <>
                <h3 className="vi-group">
                  キャラシート{character ? `(${character.name})` : ''}
                  {character && (
                    <select className="vi-sheet-mode" value={sheetMode} aria-label="キャラシートへの入れ方"
                      onChange={e => changeSheetMode(e.target.value as 'append' | 'replace')}>
                      <option value="append">いまの内容に足す</option>
                      <option value="replace">選んだもので置き換える</option>
                    </select>
                  )}
                </h3>
                <p className="publish-hint">
                  {character
                    ? (sheetMode === 'replace'
                      ? 'キーワードの出ている項目を、チェックしたキーワードだけにします。すでに入っているキーワードは、チェックを外すと消えます(人数のタグは残します。キーワードの出ていない項目は変えません)。'
                      : 'チェックを入れたキーワードを、キャラシートのいまの内容の後ろに足します。はじめは何も選ばれていません。')
                    : 'キャラを選んでいないので、キャラシートには入れられません(閉じて、キャラを選んでから読み込んでください)。'}
                </p>
                {SHEET_FIELDS.map(f => zone(`sheet:${f.key}`, f.label))}
              </>
            )}
            {all.length > 0 && zone('', '分類が分からないもの(足すなら、上の枠へ移してください)')}
            {extracted.skipped.length > 0 && (
              <details className="vi-skipped">
                <summary>取り出さなかったタグ {extracted.skipped.length}個(人数)</summary>
                <p>{extracted.skipped.join(', ')}</p>
              </details>
            )}
          </div>
        )}

        <div className="publish-actions vi-footer">
          <button type="button" onClick={onClose} disabled={busy !== null}>閉じる</button>
          <button type="button" className="publish-primary" onClick={() => void add()}
            disabled={busy !== null || total + sheetChanges.length === 0}>
            {total + sheetChanges.length === 0
              ? '足す'
              : [
                total > 0 ? `候補に${total}個を足す` : '',
                sheetChanges.length === 0 ? '' : sheetMode === 'replace'
                  ? `キャラシートを${sheetTotal}個に置き換える`
                  : `キャラシートに${sheetTotal}個を足す`,
              ].filter(Boolean).join('・')}
          </button>
        </div>
      </div>
    </div>
  )
}
