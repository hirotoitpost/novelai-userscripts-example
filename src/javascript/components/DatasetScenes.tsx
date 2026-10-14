import { useCallback, useEffect, useMemo, useState } from 'react'
import { apiFetch } from '../api'
import { AXES } from './VariationImportDialog'
import type { Axis } from './VariationImportDialog'
import './PublishDialog.css'
import './DatasetScenes.css'

/**
 * キャラ別データセットのシチュエーションと状態。
 * - シチュエーション: 場所・服装・ポーズなどをひとまとまりにしたもの(ジムで運動 など)。選ぶと、1枚ごとにどれか1つを使い、
 *   決めている軸はその中からだけ選ぶ(軸どうしがばらばらに組み合わさらない)。決めていない軸は、ふつうのバリエーションから
 * - 状態: キャラシートの容姿を一時的に変えるもの(食べ過ぎでお腹が出る など)。外すタグと足すタグを持つ
 * どちらも DB にあり(/api/lora-dataset/situations, /states)、ここで足したり直したり消したりできる。
 */

export interface Situation {
  id: number
  name: string
  framings: string[]
  poses: string[]
  outfits: string[]
  expressions: string[]
  locations: string[]
  extra_tags: string
  /** 成人キャラにだけ使えるタグ(当たったもの) */
  adult_only: string[]
}

export interface DatasetState {
  id: number
  name: string
  add_tags: string
  remove_tags: string
  /** ふだんの体の絵から描き直す部位(none はプロンプトだけで作る) */
  part: string
  adult_only: string[]
}

interface BodyPart {
  key: string
  label: string
}

interface Chunk {
  id: string
  label: string
  expansion: string
  is_category: boolean
  child_order: string[] | null
}

interface Extracted {
  axes: Record<Axis, string[]>
  sheet: { appearance: string[]; style: string[] }
  other: string[]
  adult: string[]
}

interface Props {
  token: string
  /** 選んでいるキャラ(成人キャラだけのものを選べるかに使う) */
  isAdult: boolean
  disabled: boolean
  situationIds: number[]
  onSituationIds: (ids: number[]) => void
  stateIds: number[]
  onStateIds: (ids: number[]) => void
  includePlain: boolean
  onIncludePlain: (value: boolean) => void
  /** 状態の作り方と、描き直しの強さ */
  stateMethod: 'prompt' | 'inpaint'
  onStateMethod: (value: 'prompt' | 'inpaint') => void
  inpaintStrength: number
  onInpaintStrength: (value: number) => void
  /** 一覧を読んだとき(ページで、選んだものの名前や数を出すため) */
  onLoaded?: (situations: Situation[], states: DatasetState[]) => void
}

type Editing = { kind: 'situation'; item: Situation | null } | { kind: 'state'; item: DatasetState | null }

async function send<T>(token: string, method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`)
  return (res.status === 204 ? null : await res.json()) as T
}

export default function DatasetScenes(props: Props) {
  const { token, isAdult, disabled, situationIds, stateIds } = props
  const [situations, setSituations] = useState<Situation[]>([])
  const [states, setStates] = useState<DatasetState[]>([])
  const [editing, setEditing] = useState<Editing | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [parts, setParts] = useState<BodyPart[]>([])

  useEffect(() => {
    apiFetch<BodyPart[]>(token, '/api/lora-dataset/body-parts').then(setParts).catch(() => {})
  }, [token])
  const partLabel = (key: string) => parts.find(p => p.key === key)?.label

  const load = useCallback(async () => {
    try {
      const [s, t] = await Promise.all([
        apiFetch<Situation[]>(token, '/api/lora-dataset/situations'),
        apiFetch<DatasetState[]>(token, '/api/lora-dataset/states'),
      ])
      setSituations(s)
      setStates(t)
      props.onLoaded?.(s, t)
      // 消されたものは、選択からも外す(見えないまま生成に使われないように)
      const sIds = situationIds.filter(id => s.some(x => x.id === id))
      const tIds = stateIds.filter(id => t.some(x => x.id === id))
      if (sIds.length !== situationIds.length) props.onSituationIds(sIds)
      if (tIds.length !== stateIds.length) props.onStateIds(tIds)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
    // 選択の並びが変わるたびに読み直さない(読み直すのは、開いたときと保存・削除のあと)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  useEffect(() => { void load() }, [load])

  const toggle = (ids: number[], id: number, set: (ids: number[]) => void) =>
    set(ids.includes(id) ? ids.filter(x => x !== id) : [...ids, id])

  const chip = (
    item: { id: number; name: string; adult_only: string[] },
    on: boolean,
    onToggle: () => void,
    onEdit: () => void,
    detail: string,
  ) => {
    const blocked = item.adult_only.length > 0 && !isAdult
    return (
      <span key={item.id} className={`ds-chip${on ? ' ds-chip--on' : ''}`}>
        <button type="button" className="ds-chip-main" onClick={onToggle} disabled={disabled || (blocked && !on)}
          aria-pressed={on} title={blocked ? '成人キャラ(キャラシートの成人フラグ)にだけ使えます' : detail}>
          {item.name}
          {item.adult_only.length > 0 && <span className="ds-adult">成人のみ</span>}
        </button>
        <button type="button" className="ds-chip-edit" onClick={onEdit} disabled={disabled} aria-label={`「${item.name}」を編集`}>✎</button>
      </span>
    )
  }

  const selectedStates = states.filter(s => stateIds.includes(s.id))

  return (
    <section className="lora-section ds-root">
      <span className="lora-section-label">シチュエーション</span>
      <p className="lora-hint">
        場所・服装・ポーズなどをひとまとまりにしたものです。選ぶと、1枚ごとにどれか1つを使い、決めている軸はその中からだけ選びます
        (選んだシチュエーションに均等に割り振ります)。決めていない軸は、下のバリエーションから選びます。
      </p>
      <div className="ds-chips">
        {situations.map(s => chip(
          s, situationIds.includes(s.id), () => toggle(situationIds, s.id, props.onSituationIds),
          () => setEditing({ kind: 'situation', item: s }),
          AXES.filter(a => s[a.key].length > 0).map(a => `${a.label}: ${s[a.key].join(' / ')}`).join('\n'),
        ))}
        <button type="button" className="ds-add" onClick={() => setEditing({ kind: 'situation', item: null })} disabled={disabled}>＋ 追加</button>
      </div>

      <span className="lora-section-label">状態</span>
      <p className="lora-hint">
        キャラシートの容姿を一時的に変えるものです(外すタグと足すタグ)。選んだ状態に均等に割り振ります。キャプションにも状態のタグが入るので、
        学習では体格の変化がキャラそのものの特徴として覚えられにくくなります。
      </p>
      <div className="ds-chips">
        {states.map(s => chip(
          s, stateIds.includes(s.id), () => toggle(stateIds, s.id, props.onStateIds),
          () => setEditing({ kind: 'state', item: s }),
          [
            s.remove_tags && `外す: ${s.remove_tags}`,
            s.add_tags && `足す: ${s.add_tags}`,
            partLabel(s.part) ? `描き直す部位: ${partLabel(s.part)}` : '',
          ].filter(Boolean).join('\n'),
        ))}
        <button type="button" className="ds-add" onClick={() => setEditing({ kind: 'state', item: null })} disabled={disabled}>＋ 追加</button>
      </div>
      {selectedStates.length > 0 && (
        <div className="ds-method">
          <span className="lora-label">状態の作り方</span>
          <label className="lora-checkbox-label">
            <input type="radio" name="ds-method" checked={props.stateMethod === 'prompt'} disabled={disabled}
              onChange={() => props.onStateMethod('prompt')} />
            状態のタグで生成する
          </label>
          <label className="lora-checkbox-label">
            <input type="radio" name="ds-method" checked={props.stateMethod === 'inpaint'} disabled={disabled}
              onChange={() => props.onStateMethod('inpaint')} />
            ふだんの体の絵から、部位だけを描き直す(インペイント)
          </label>
          {props.stateMethod === 'inpaint' ? (
            <>
              <p className="lora-hint">
                ふだんの体で1枚生成し、状態の部位(お腹など)だけを描き直した絵を足します。顔・服・背景が同じで体だけが違う組になります。
                生成枚数は「ふだんの体の絵」の枚数で、1枚ごとに状態の数だけ増えます。頭が見つからない絵(寝ている絵など)は、状態のタグで生成し直します。
                部位の無い状態は、タグで生成します。
              </p>
              <label className="lora-label">
                描き直しの強さ {props.inpaintStrength.toFixed(2)}
                <input type="range" min={0.3} max={1} step={0.05} value={props.inpaintStrength} disabled={disabled}
                  onChange={e => props.onInpaintStrength(Number(e.target.value))} />
              </label>
              {selectedStates.some(s => !partLabel(s.part)) && (
                <p className="lora-hint">部位の無い状態: {selectedStates.filter(s => !partLabel(s.part)).map(s => s.name).join('、')}(タグで生成します)</p>
              )}
            </>
          ) : (
            <label className="lora-checkbox-label">
              <input type="checkbox" checked={props.includePlain} onChange={e => props.onIncludePlain(e.target.checked)} disabled={disabled} />
              状態なし(ふだんの体)の枚も作る
            </label>
          )}
        </div>
      )}
      {selectedStates.some(s => s.adult_only.length > 0) && !isAdult && (
        <p className="lora-warn">成人キャラにだけ使える状態が選ばれています。このキャラでは生成できません(外してください)。</p>
      )}
      {error && <p className="lora-warn" role="alert">{error}</p>}

      {editing && (
        <SceneEditor
          token={token}
          editing={editing}
          parts={parts}
          onClose={() => setEditing(null)}
          onSaved={async removedId => {
            setEditing(null)
            if (removedId !== undefined) {
              if (editing.kind === 'situation') props.onSituationIds(situationIds.filter(id => id !== removedId))
              else props.onStateIds(stateIds.filter(id => id !== removedId))
            }
            await load()
          }}
        />
      )}
    </section>
  )
}

const NO_CATEGORY = '(分類なし)'
const lines = (values: string[]) => values.join('\n')
const fromLines = (text: string) => text.split('\n').map(l => l.trim()).filter(Boolean)

/** シチュエーション・状態の編集ダイアログ(新しく作る / 直す / 消す / プロンプトチャンクから読み込む) */
function SceneEditor({ token, editing, parts, onClose, onSaved }: {
  token: string
  editing: Editing
  parts: BodyPart[]
  onClose: () => void
  /** 保存・削除のあと。消したときは消したID */
  onSaved: (removedId?: number) => void | Promise<void>
}) {
  const situation = editing.kind === 'situation' ? editing.item : null
  const state = editing.kind === 'state' ? editing.item : null
  const [name, setName] = useState(editing.item?.name ?? '')
  const [axes, setAxes] = useState<Record<Axis, string>>(() => ({
    framings: lines(situation?.framings ?? []),
    poses: lines(situation?.poses ?? []),
    outfits: lines(situation?.outfits ?? []),
    expressions: lines(situation?.expressions ?? []),
    locations: lines(situation?.locations ?? []),
  }))
  const [extra, setExtra] = useState(situation?.extra_tags ?? '')
  const [addTags, setAddTags] = useState(state?.add_tags ?? '')
  const [removeTags, setRemoveTags] = useState(state?.remove_tags ?? '')
  const [part, setPart] = useState(state?.part ?? 'none')
  const [chunks, setChunks] = useState<Chunk[]>([])
  const [chunkQuery, setChunkQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  useEffect(() => {
    apiFetch<Chunk[]>(token, '/api/chunks/imported').then(setChunks).catch(() => {})
  }, [token])

  // チャンクに分類の名前を付ける。シチュエーションなら「シチュエーション」、状態なら「体型」の分類を先に出す
  const chunkList = useMemo(() => {
    const parent = new Map<string, Chunk>()
    for (const c of chunks.filter(c => c.is_category)) for (const child of c.child_order ?? []) parent.set(child, c)
    const prefer = editing.kind === 'situation' ? /シチュ|場面|situation/i : /体型|体格|状態|body/i
    return chunks
      .filter(c => !c.is_category && (c.expansion ?? '').trim())
      .map(c => {
        const owner = parent.get(c.id)
        return { ...c, category: owner && owner.id !== 'default' ? owner.label : NO_CATEGORY }
      })
      .sort((a, b) => Number(prefer.test(b.category)) - Number(prefer.test(a.category)))
  }, [chunks, editing.kind])
  const shownChunks = chunkList.filter(c => !chunkQuery.trim() || `${c.label} ${c.category} ${c.expansion}`.toLowerCase().includes(chunkQuery.trim().toLowerCase())).slice(0, 200)

  const run = async (work: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try {
      await work()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  /** チャンクのプロンプトを読み込む。シチュエーションは軸ごとに(1つの軸のタグは1つのまとまりとして)、状態は足すタグへ */
  const fromChunk = (id: string) => run(async () => {
    const chunk = chunkList.find(c => c.id === id)
    if (!chunk) return
    const result = await apiFetch<Extracted>(token, '/api/lora-dataset/variations/extract', { prompts: [chunk.expansion] })
    if (!name.trim()) setName(chunk.label.slice(0, 100))
    if (editing.kind === 'situation') {
      const next = { ...axes }
      for (const a of AXES) {
        const value = result.axes[a.key].join(', ')
        if (value) next[a.key] = [next[a.key], value].filter(Boolean).join('\n')
      }
      setAxes(next)
      const rest = [...result.other, ...result.adult]
      if (rest.length) setExtra([extra, rest.join(', ')].filter(Boolean).join(', '))
      setNote(`「${chunk.label}」を読み込みました。容姿・画風のタグ(${result.sheet.appearance.length + result.sheet.style.length}個)は入れていません。`)
    } else {
      const body = [...result.sheet.appearance, ...result.other, ...result.adult]
      setAddTags([addTags, body.join(', ')].filter(Boolean).join(', '))
      const skipped = AXES.reduce((n, a) => n + result.axes[a.key].length, 0)
      setNote(`「${chunk.label}」の体のタグを、足すタグに入れました。${skipped > 0 ? `場所・ポーズなどのタグ(${skipped}個)は入れていません(シチュエーションの側で持ちます)。` : ''}`)
    }
  })

  const save = () => run(async () => {
    if (editing.kind === 'situation') {
      const body = { name: name.trim(), ...Object.fromEntries(AXES.map(a => [a.key, fromLines(axes[a.key])])), extra_tags: extra }
      await send(token, situation ? 'PUT' : 'POST', `/api/lora-dataset/situations${situation ? `/${situation.id}` : ''}`, body)
    } else {
      const body = { name: name.trim(), add_tags: addTags, remove_tags: removeTags, part }
      await send(token, state ? 'PUT' : 'POST', `/api/lora-dataset/states${state ? `/${state.id}` : ''}`, body)
    }
    await onSaved()
  })

  const remove = () => {
    const item = editing.item
    if (!item || !window.confirm(`「${item.name}」を消します。よろしいですか?`)) return
    void run(async () => {
      await send(token, 'DELETE', `/api/lora-dataset/${editing.kind === 'situation' ? 'situations' : 'states'}/${item.id}`)
      await onSaved(item.id)
    })
  }

  const title = `${editing.kind === 'situation' ? 'シチュエーション' : '状態'}を${editing.item ? '直す' : '足す'}`
  return (
    <div className="publish-overlay" onClick={busy ? undefined : onClose}>
      <div className="publish-dialog ds-dialog" role="dialog" aria-modal="true" aria-labelledby="ds-title" onClick={e => e.stopPropagation()}>
        <h2 id="ds-title">{title}</h2>
        <label className="publish-field">
          <span>名前</span>
          <input value={name} maxLength={100} onChange={e => setName(e.target.value)}
            placeholder={editing.kind === 'situation' ? '例: ジムで運動' : '例: 食べ過ぎでお腹が出る'} />
        </label>

        <details className="ds-chunks">
          <summary>プロンプトチャンクから読み込む</summary>
          <input className="ds-search" value={chunkQuery} placeholder="名前・分類・中身で探す" aria-label="チャンクを探す"
            onChange={e => setChunkQuery(e.target.value)} />
          <select size={6} className="ds-chunk-select" aria-label="読み込むチャンク" disabled={busy}
            onChange={e => { if (e.target.value) void fromChunk(e.target.value) }}>
            {shownChunks.map(c => <option key={c.id} value={c.id}>{c.category} / {c.label}</option>)}
          </select>
          <p className="publish-hint">選ぶと、下の欄に足します(いまの内容は消しません)。</p>
        </details>
        {note && <p className="publish-hint" role="status">{note}</p>}

        {editing.kind === 'situation' ? (
          <>
            <p className="publish-hint">
              軸ごとに、このシチュエーションで使うものを1行に1つ書きます(1行の中はカンマ区切りで、まとめて使います)。
              空の軸は、ふつうのバリエーションから選びます。
            </p>
            <div className="ds-axes">
              {AXES.map(a => (
                <label key={a.key} className="publish-field">
                  <span>{a.label}</span>
                  <textarea rows={2} value={axes[a.key]} onChange={e => setAxes({ ...axes, [a.key]: e.target.value })}
                    placeholder={a.key === 'locations' ? '例: gym, indoors' : a.key === 'poses' ? '例: stretching\nlifting dumbbell' : ''} />
                </label>
              ))}
            </div>
            <label className="publish-field">
              <span>いつも足すタグ(時間帯・天気など。任意)</span>
              <input value={extra} maxLength={500} onChange={e => setExtra(e.target.value)} placeholder="例: daytime, sunlight" />
            </label>
          </>
        ) : (
          <>
            <label className="publish-field">
              <span>キャラシートの容姿から外すタグ(体型など。カンマ区切り)</span>
              <input value={removeTags} maxLength={500} onChange={e => setRemoveTags(e.target.value)} placeholder="例: slim, flat stomach" />
            </label>
            <label className="publish-field">
              <span>足すタグ(カンマ区切り)</span>
              <input value={addTags} maxLength={500} onChange={e => setAddTags(e.target.value)} placeholder="例: plump, round belly" />
            </label>
            <label className="publish-field">
              <span>描き直す部位(「部位だけを描き直す」で作るとき)</span>
              <select value={part} onChange={e => setPart(e.target.value)}>
                <option value="none">なし(タグで生成する)</option>
                {parts.map(p => <option key={p.key} value={p.key}>{p.label}</option>)}
              </select>
            </label>
            <p className="publish-hint">
              キャラシートに「標準の体型」があれば、足すタグはそれを置き換えます(外すタグは容姿の側から外します)。
              妊娠などのタグは、成人キャラにだけ使えます(コンテンツガードの「成人キャラだけに使うタグ」)。
            </p>
          </>
        )}

        {error && <p className="publish-error" role="alert">{error}</p>}
        <div className="publish-actions">
          {editing.item && <button type="button" className="ds-delete" onClick={remove} disabled={busy}>消す</button>}
          <button type="button" onClick={onClose} disabled={busy}>やめる</button>
          <button type="button" className="publish-primary" onClick={() => void save()} disabled={busy || !name.trim()}>保存</button>
        </div>
      </div>
    </div>
  )
}
