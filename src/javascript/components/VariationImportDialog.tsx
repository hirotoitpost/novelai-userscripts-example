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

/** POST /api/lora-dataset/variations/extract の結果 */
interface Extracted {
  axes: Variations
  other: string[]
  adult: string[]
  skipped: string[]
  /** 軸が分からなかったキーワードの、チャンクの分類から付けた軸の見当 */
  suggested: Partial<Record<string, Axis>>
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
  onClose: () => void
  /** 足したあとの候補の一覧と、足した数 */
  onAdded: (variations: Variations, added: number) => void
}

/** 分類の名前から、軸の見当を付ける(サーバーの dataset_variations.hint_axis と同じ語) */
const HINT_WORDS: [Axis, string[]][] = [
  ['expressions', ['表情', '顔', 'expression', 'face']],
  ['outfits', ['服', '衣装', '衣服', 'アクセ', 'コスチューム', 'outfit', 'cloth', 'costume']],
  ['framings', ['カメラ', '構図', '視点', 'アングル', 'camera', 'framing', 'angle']],
  ['locations', ['風景', '場所', '背景', 'ロケ', 'location', 'scene', 'background']],
  ['poses', ['ポーズ', '姿勢', '動作', '仕草', 'pose', 'action']],
]

function hintAxis(label: string): Axis | null {
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
 */
export default function VariationImportDialog({ token, existing, onClose, onAdded }: Props) {
  const [text, setText] = useState('')
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [chunks, setChunks] = useState<Chunk[]>([])
  const [chunkQuery, setChunkQuery] = useState('')
  const [chunkCategory, setChunkCategory] = useState('')
  const [chunkPicked, setChunkPicked] = useState<Set<string>>(new Set())
  const [extracted, setExtracted] = useState<Extracted | null>(null)
  // キーワードごとの、置いてある枠('' は「分類が分からないもの」)と、足すかどうか
  const [placed, setPlaced] = useState<Record<string, Axis | ''>>({})
  const [checked, setChecked] = useState<Record<string, boolean>>({})
  const [dragOver, setDragOver] = useState<Axis | '' | null>(null)
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

  const known = (axis: Axis, tag: string) => existing[axis].some(v => v.toLowerCase() === tag.toLowerCase())
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
    const nextPlaced: Record<string, Axis | ''> = {}
    const nextChecked: Record<string, boolean> = {}
    // 軸が分かったものは、登録済みでなければ足す。分からないものは、見当があればその枠に置く(成人向けは選ぶまで足さない)
    for (const { key } of AXES) {
      for (const tag of result.axes[key]) {
        nextPlaced[tag] = key
        nextChecked[tag] = !known(key, tag)
      }
    }
    for (const tag of result.other) {
      const axis: Axis | '' = result.suggested[tag] ?? ''
      nextPlaced[tag] = axis
      nextChecked[tag] = axis !== '' && !known(axis, tag)
    }
    for (const tag of result.adult) {
      nextPlaced[tag] = result.suggested[tag] ?? ''
      nextChecked[tag] = false
    }
    setExtracted(result)
    setPlaced(nextPlaced)
    setChecked(nextChecked)
  })

  /** キーワードを別の枠へ移す。分類が分からない枠から軸へ移したら、足すものとして選ぶ */
  const move = (tag: string, to: Axis | '') => {
    if (placed[tag] === to) return
    setPlaced({ ...placed, [tag]: to })
    setChecked({ ...checked, [tag]: to !== '' && !known(to, tag) && (checked[tag] || placed[tag] === '') })
  }

  const all = Object.keys(placed)
  const adult = new Set(extracted?.adult ?? [])
  const tagsIn = (zone: Axis | '') => all.filter(tag => placed[tag] === zone)
  const chosen = AXES.map(({ key }) => ({ axis: key, values: tagsIn(key).filter(tag => checked[tag] && !known(key, tag)) }))
    .filter(group => group.values.length > 0)
  const total = chosen.reduce((sum, group) => sum + group.values.length, 0)

  const add = () => run('足しています…', async () => {
    let variations = existing
    for (const group of chosen) {
      variations = await apiFetch<Variations>(token, '/api/lora-dataset/variations', group)
    }
    onAdded(variations, total)
  })

  const onDrop = (e: DragEvent, zone: Axis | '') => {
    e.preventDefault()
    setDragOver(null)
    const tag = e.dataTransfer.getData(DRAG_TYPE) || e.dataTransfer.getData('text/plain')
    if (tag in placed) move(tag, zone)
  }

  const zone = (key: Axis | '', label: string) => {
    const tags = tagsIn(key)
    const selectable = key === '' ? [] : tags.filter(tag => !known(key, tag))
    const allChecked = selectable.length > 0 && selectable.every(tag => checked[tag])
    return (
      <section key={key || 'unknown'} className={`vi-zone${dragOver === key ? ' vi-zone--over' : ''}${key === '' ? ' vi-zone--unknown' : ''}`}
        aria-label={label}
        onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; setDragOver(key) }}
        onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragOver(null) }}
        onDrop={e => onDrop(e, key)}>
        <h3>
          {label}
          <small>{tags.length}個{key !== '' && `(足す ${tags.filter(tag => checked[tag] && !known(key, tag)).length}個)`}</small>
          {selectable.length > 0 && (
            <button type="button" className="vi-all"
              onClick={() => setChecked({ ...checked, ...Object.fromEntries(selectable.map(tag => [tag, !allChecked])) })}>
              {allChecked ? 'すべて外す' : 'すべて選ぶ'}
            </button>
          )}
        </h3>
        {tags.length === 0
          ? <p className="vi-empty">{key === '' ? 'ありません' : 'ここへドラッグすると、この分類になります'}</p>
          : (
            <ul>
              {tags.map(tag => {
                const registered = key !== '' && known(key, tag)
                return (
                  <li key={tag} draggable className={`vi-row${checked[tag] ? ' vi-row--on' : ''}`}
                    onDragStart={e => {
                      e.dataTransfer.setData(DRAG_TYPE, tag)
                      e.dataTransfer.setData('text/plain', tag)
                      e.dataTransfer.effectAllowed = 'move'
                    }}
                    onDragEnd={() => setDragOver(null)}>
                    <label className="vi-pick">
                      <input type="checkbox" checked={!!checked[tag]} disabled={key === '' || registered}
                        aria-label={`「${tag}」を足す`}
                        onChange={e => setChecked({ ...checked, [tag]: e.target.checked })} />
                      <span className="vi-tag">{tag}</span>
                    </label>
                    {adult.has(tag) && <span className="vi-r18" title="成人向け(R18 のデータセット用)">R18</span>}
                    {registered && <span className="vi-known">登録済み</span>}
                    <select value={key} aria-label={`「${tag}」の分類`} title="分類を変える(ドラッグでも移せます)"
                      onChange={e => move(tag, e.target.value as Axis | '')}>
                      <option value="">分からない</option>
                      {AXES.map(a => <option key={a.key} value={a.key}>{a.label}</option>)}
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
          髪・目・体などの容姿と、品質・画風のタグは取り出しません(キャラシートの側で持つため)。
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
            {all.length > 0 && AXES.map(a => zone(a.key, a.label))}
            {all.length > 0 && zone('', '分類が分からないもの(足すなら、上の枠へ移してください)')}
            {extracted.skipped.length > 0 && (
              <details className="vi-skipped">
                <summary>取り出さなかったタグ {extracted.skipped.length}個(容姿・人数・品質・画風)</summary>
                <p>{extracted.skipped.join(', ')}</p>
              </details>
            )}
          </div>
        )}

        <div className="publish-actions vi-footer">
          <button type="button" onClick={onClose} disabled={busy !== null}>閉じる</button>
          <button type="button" className="publish-primary" onClick={() => void add()} disabled={busy !== null || total === 0}>
            {total > 0 ? `${total}個を候補に足す` : '候補に足す'}
          </button>
        </div>
      </div>
    </div>
  )
}
