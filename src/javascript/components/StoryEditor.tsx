import { Fragment, useEffect, useMemo, useState } from 'react'
import type { Character } from './StoryCharacters'
import type { DatasetState } from './DatasetScenes'
import './StoryEditor.css'

/**
 * 物語の編集画面: コマ割り(シーンの並べ替え・追加・削除、ページの区切り、1ページのコマ数)・セリフ(1行ずつ・話し手つき)・
 * タグ・登場人物・ナレーション・効果音をまとめて直す。直した内容は下書きで、「上書き保存」で元の物語に、
 * 「別名保存」で新しい物語として保存するまで、元の物語は変わらない(閉じれば捨てる)。
 * コマの絵は、タグ・登場人物を変えたシーンだけ「描き直し待ち」になる(絵は消えない)。
 */

interface Line { text: string; speaker_id: number | null }

interface CastRow { id: number; name: string; action_tags: string; outfit_tags: string; state_id: number | null }

interface EditScene {
  /** 新しく足したシーンは null */
  id: number | null
  /** 画面の中だけで使う見分け用 */
  key: string
  title: string
  text: string
  prompt_tags: string
  narration: string | null
  sfx: string[] | null
  dialogue: Line[]
  /** 保存済みのセリフが編集画面で決めたものか(false なら本文の「」から取り出したもの) */
  dialogue_custom: boolean
  /** この画面でセリフを触ったか(触っていなければ、本文から取り出す仕組みのまま保存する) */
  dialogue_touched: boolean
  page_index: number
  characters: CastRow[]
  panel: { image_path: string; stale: boolean } | null
}

interface EditData {
  id: number
  title: string | null
  panels_per_page: number
  custom_pages: boolean
  scenes: EditScene[]
}

interface Props {
  apiOrigin: string
  storyId: number
  /** 登録済みのキャラ全員(登場人物を足すとき) */
  characters: Character[]
  states: DatasetState[]
  fileUrl: (path: string) => string
  onClose: () => void
  /** 保存したあと。上書きなら同じ物語、別名保存なら新しい物語のID */
  onSaved: (storyId: number) => void
}

let keySeq = 0
const nextKey = () => `new-${++keySeq}`

async function errorDetail(res: Response): Promise<string> {
  const data = await res.json().catch(() => null)
  const detail = data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((d: { msg?: string }) => d.msg ?? '').join(' / ')
  return `HTTP ${res.status}`
}

/** 絵に効く内容(タグ・登場人物とその動作・服装・状態)。保存したときの内容と比べて「描き直し待ち」を出す */
function imageKey(s: Pick<EditScene, 'prompt_tags' | 'characters'>): string {
  const cast = [...s.characters]
    .sort((a, b) => a.id - b.id)
    .map(c => [c.id, c.action_tags.trim(), c.outfit_tags.trim(), c.state_id])
  return JSON.stringify([s.prompt_tags.trim(), cast])
}

function move<T>(list: T[], from: number, to: number): T[] {
  if (to < 0 || to >= list.length) return list
  const next = [...list]
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}

export default function StoryEditor({ apiOrigin, storyId, characters, states, fileUrl, onClose, onSaved }: Props) {
  const [data, setData] = useState<EditData | null>(null)
  const [original, setOriginal] = useState<Record<number, string>>({})
  const [title, setTitle] = useState('')
  const [perPage, setPerPage] = useState(4)
  const [customPages, setCustomPages] = useState(false)
  const [scenes, setScenes] = useState<EditScene[]>([])
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [saveAsTitle, setSaveAsTitle] = useState<string | null>(null)

  async function load() {
    setError(null)
    const res = await fetch(`${apiOrigin}/api/story/${storyId}/edit`)
    if (!res.ok) throw new Error(await errorDetail(res))
    const loaded = (await res.json()) as Omit<EditData, 'scenes'> & { scenes: Omit<EditScene, 'key' | 'dialogue_touched'>[] }
    const list = loaded.scenes.map(s => ({ ...s, key: `s${s.id}`, title: s.title ?? '', dialogue_touched: false }))
    setData({ ...loaded, scenes: list })
    setOriginal(Object.fromEntries(list.map(s => [s.id!, imageKey(s)])))
    setTitle(loaded.title ?? '')
    setPerPage(loaded.panels_per_page)
    setCustomPages(loaded.custom_pages)
    setScenes(list)
    setDirty(false)
  }

  useEffect(() => {
    load().catch(e => setError(e instanceof Error ? e.message : String(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storyId])

  // 閉じる・再読み込みで下書きが消えるときは確かめる
  useEffect(() => {
    if (!dirty) return
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault() }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  /** 表示するページ番号(区切りを決めていなければ、コマ数ずつ) */
  const pages = useMemo(() => {
    if (!customPages) return scenes.map((_, i) => Math.floor(i / perPage))
    const out: number[] = []
    scenes.forEach((s, i) => out.push(i === 0 ? 0 : Math.max(s.page_index, out[i - 1])))
    const numbers = new Map([...new Set(out)].map((v, i) => [v, i]))
    return out.map(v => numbers.get(v)!)
  }, [scenes, customPages, perPage])

  function update(index: number, patch: Partial<EditScene>) {
    setScenes(prev => prev.map((s, i) => (i === index ? { ...s, ...patch } : s)))
    setDirty(true)
  }

  function edit(next: EditScene[]) {
    setScenes(next)
    setDirty(true)
  }

  /** ページ番号を、今の表示の並びで振り直す(区切りの付け外し・並べ替えのあと) */
  function withPages(list: EditScene[], breaks: boolean[]): EditScene[] {
    let page = 0
    return list.map((s, i) => {
      if (i > 0 && breaks[i]) page += 1
      return { ...s, page_index: page }
    })
  }

  function currentBreaks(): boolean[] {
    return scenes.map((_, i) => i > 0 && pages[i] !== pages[i - 1])
  }

  function toggleBreak(index: number) {
    const breaks = currentBreaks()
    breaks[index] = !breaks[index]
    setCustomPages(true)
    edit(withPages(scenes, breaks))
  }

  function enableCustomPages(on: boolean) {
    // 個別に区切るときは、今の見た目(コマ数ずつ)から始める
    if (on) edit(withPages(scenes, currentBreaks()))
    setCustomPages(on)
    setDirty(true)
  }

  function moveScene(index: number, to: number) {
    // 区切りはページの見た目ごと付いていくよう、移したシーンは移した先のページに入れる
    const breaks = currentBreaks()
    const moved = move(scenes, index, to)
    const movedBreaks = move(breaks, index, to)
    movedBreaks[0] = false
    edit(customPages ? withPages(moved, movedBreaks) : moved)
  }

  function addScene(after: number) {
    const base = scenes[after]
    const fresh: EditScene = {
      id: null, key: nextKey(), title: '', text: '', prompt_tags: '', narration: null, sfx: null,
      dialogue: [], dialogue_custom: false, dialogue_touched: true,
      page_index: base ? base.page_index : 0,
      characters: base ? base.characters.map(c => ({ ...c, action_tags: '', outfit_tags: c.outfit_tags, state_id: c.state_id })) : [],
      panel: null,
    }
    const next = [...scenes]
    next.splice(after + 1, 0, fresh)
    edit(next)
  }

  function removeScene(index: number) {
    const s = scenes[index]
    if (s.panel && !window.confirm(`シーン${index + 1}を消しますか？(コマの絵も、この物語から外れます)`)) return
    edit(scenes.filter((_, i) => i !== index))
  }

  function editLines(index: number, lines: Line[]) {
    update(index, { dialogue: lines, dialogue_touched: true })
  }

  function body() {
    return {
      title: title.trim() || null,
      panels_per_page: perPage,
      custom_pages: customPages,
      scenes: scenes.map((s, i) => ({
        id: s.id,
        title: s.title.trim() || null,
        text: s.text,
        prompt_tags: s.prompt_tags,
        narration: s.narration,
        sfx: s.sfx,
        // セリフを触っていなくて、本文から取り出していたものは、そのまま本文から取り出す
        dialogue: s.dialogue_touched || s.dialogue_custom ? s.dialogue : null,
        page_index: pages[i],
        characters: s.characters.map(c => ({ id: c.id, action_tags: c.action_tags, outfit_tags: c.outfit_tags, state_id: c.state_id })),
      })),
    }
  }

  async function run(work: () => Promise<void>) {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      await work()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const overwrite = () => run(async () => {
    const res = await fetch(`${apiOrigin}/api/story/${storyId}/edit`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body()),
    })
    if (!res.ok) throw new Error(await errorDetail(res))
    const result = await res.json()
    await load()
    onSaved(storyId)
    const parts = ['上書き保存しました']
    if (result.stale_panels) parts.push(`描き直し待ちのコマ ${result.stale_panels}`)
    if (result.dropped_pages) parts.push(`内容の変わったページの挿絵 ${result.dropped_pages} 枚を外しました`)
    setNote(parts.join('。'))
  })

  const saveAs = () => run(async () => {
    const name = (saveAsTitle ?? '').trim()
    if (!name) throw new Error('新しい物語の題名を入れてください。')
    const res = await fetch(`${apiOrigin}/api/story/${storyId}/save-as`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...body(), title: name }),
    })
    if (!res.ok) throw new Error(await errorDetail(res))
    const result = await res.json()
    setDirty(false)
    onSaved(result.story_id)
  })

  function close() {
    if (dirty && !window.confirm('保存していない変更があります。捨てて閉じますか？')) return
    onClose()
  }

  if (!data) {
    return (
      <div className="sed-overlay" role="dialog" aria-modal="true" aria-label="物語の編集">
        <div className="sed-panel">
          {error ? <p className="sed-error" role="alert">{error}</p> : <p>読み込み中…</p>}
          <button type="button" onClick={onClose}>閉じる</button>
        </div>
      </div>
    )
  }

  return (
    <div className="sed-overlay" role="dialog" aria-modal="true" aria-label="物語の編集">
      <div className="sed-panel">
        <header className="sed-header">
          <h2>物語の編集{dirty && <span className="sed-dirty">(未保存)</span>}</h2>
          <div className="sed-header-fields">
            <label>題名<input value={title} disabled={busy} onChange={e => { setTitle(e.target.value); setDirty(true) }} /></label>
            <label>1ページのコマ数
              <input type="number" min={1} max={12} value={perPage} disabled={busy || customPages}
                onChange={e => { setPerPage(Math.min(12, Math.max(1, Number(e.target.value) || 1))); setDirty(true) }} />
            </label>
            <label className="sed-check">
              <input type="checkbox" checked={customPages} disabled={busy} onChange={e => enableCustomPages(e.target.checked)} />
              ページの区切りを個別に決める
            </label>
          </div>
          <div className="sed-header-actions">
            <button type="button" className="sed-primary" disabled={busy || !dirty} onClick={() => void overwrite()}>上書き保存</button>
            {saveAsTitle === null ? (
              <button type="button" disabled={busy} onClick={() => setSaveAsTitle(`${title || '無題'}(コピー)`)}>別名保存…</button>
            ) : (
              <span className="sed-saveas">
                <input value={saveAsTitle} aria-label="新しい物語の題名" disabled={busy} onChange={e => setSaveAsTitle(e.target.value)} />
                <button type="button" className="sed-primary" disabled={busy} onClick={() => void saveAs()}>新しい物語として保存</button>
                <button type="button" disabled={busy} onClick={() => setSaveAsTitle(null)}>やめる</button>
              </span>
            )}
            <button type="button" disabled={busy} onClick={close}>閉じる</button>
          </div>
          {note && <p className="sed-note" role="status">{note}</p>}
          {error && <p className="sed-error" role="alert">{error}</p>}
          <p className="sed-hint">
            直した内容は、保存するまで元の物語に反映されません。タグ・登場人物を変えたコマは「描き直し待ち」になり、
            コマの生成で「生成済みを飛ばす」にしても描き直されます(それまでは今の絵を使います)。
          </p>
        </header>

        <ol className="sed-scenes">
          {scenes.map((s, i) => {
            const changed = s.id !== null && s.panel !== null && original[s.id] !== imageKey(s)
            const newPage = i === 0 || pages[i] !== pages[i - 1]
            return (
              <Fragment key={s.key}>
                {newPage && <li className="sed-page-label" aria-hidden>{pages[i] + 1}ページ</li>}
                {i > 0 && customPages && (
                  <li className="sed-break">
                    <button type="button" disabled={busy} onClick={() => toggleBreak(i)}>
                      {newPage ? '↑ 前のページとつなげる' : '✂ ここでページを区切る'}
                    </button>
                  </li>
                )}
                <li className="sed-scene">
                  <SceneCard
                    index={i}
                    total={scenes.length}
                    scene={s}
                    changed={changed}
                    busy={busy}
                    characters={characters}
                    states={states}
                    fileUrl={fileUrl}
                    onChange={patch => update(i, patch)}
                    onLines={lines => editLines(i, lines)}
                    onMove={to => moveScene(i, to)}
                    onAdd={() => addScene(i)}
                    onRemove={() => removeScene(i)}
                  />
                </li>
              </Fragment>
            )
          })}
        </ol>
        {scenes.length === 0 && (
          <button type="button" disabled={busy} onClick={() => addScene(-1)}>＋ シーンを足す</button>
        )}
      </div>
    </div>
  )
}

interface CardProps {
  index: number
  total: number
  scene: EditScene
  changed: boolean
  busy: boolean
  characters: Character[]
  states: DatasetState[]
  fileUrl: (path: string) => string
  onChange: (patch: Partial<EditScene>) => void
  onLines: (lines: Line[]) => void
  onMove: (to: number) => void
  onAdd: () => void
  onRemove: () => void
}

function SceneCard({
  index, total, scene, changed, busy, characters, states, fileUrl, onChange, onLines, onMove, onAdd, onRemove,
}: CardProps) {
  const castIds = scene.characters.map(c => c.id)
  const addable = characters.filter(c => !castIds.includes(c.id)).sort((a, b) => a.name.localeCompare(b.name, 'ja'))

  function setCast(next: CastRow[]) {
    // 外したキャラが話し手のセリフは、話し手なしにする
    const ids = new Set(next.map(c => c.id))
    const lines = scene.dialogue.map(l => (l.speaker_id !== null && !ids.has(l.speaker_id) ? { ...l, speaker_id: null } : l))
    onChange({ characters: next, ...(lines.some((l, i) => l !== scene.dialogue[i]) ? { dialogue: lines, dialogue_touched: true } : {}) })
  }

  function editCast(id: number, patch: Partial<CastRow>) {
    setCast(scene.characters.map(c => (c.id === id ? { ...c, ...patch } : c)))
  }

  function editLine(i: number, patch: Partial<Line>) {
    onLines(scene.dialogue.map((l, j) => (j === i ? { ...l, ...patch } : l)))
  }

  return (
    <div className="sed-card">
      <div className="sed-card-head">
        <span className="sed-num">コマ{index + 1}{scene.id === null && '(新)'}</span>
        <input className="sed-title" value={scene.title} placeholder="シーンの題(任意)" disabled={busy} aria-label={`コマ${index + 1}の題`}
          onChange={e => onChange({ title: e.target.value })} />
        <span className="sed-card-actions">
          <button type="button" disabled={busy || index === 0} onClick={() => onMove(index - 1)} aria-label="前へ">▲</button>
          <button type="button" disabled={busy || index === total - 1} onClick={() => onMove(index + 1)} aria-label="後ろへ">▼</button>
          <button type="button" disabled={busy} onClick={onAdd} title="このコマの後ろに新しいコマを足す">＋</button>
          <button type="button" disabled={busy} onClick={onRemove} className="sed-danger" aria-label={`コマ${index + 1}を消す`}>✕</button>
        </span>
      </div>

      <div className="sed-card-body">
        <div className="sed-thumb">
          {scene.panel ? <img src={fileUrl(scene.panel.image_path)} alt={`コマ${index + 1}の絵`} /> : <span>絵なし</span>}
          {scene.panel && (changed || scene.panel.stale) && <span className="sed-badge">描き直し待ち</span>}
        </div>

        <div className="sed-fields">
          <label className="sed-label">作画タグ
            <textarea rows={2} value={scene.prompt_tags} disabled={busy} onChange={e => onChange({ prompt_tags: e.target.value })} />
          </label>

          <div className="sed-label">登場人物
            <table className="sed-cast">
              <tbody>
                {scene.characters.map(c => (
                  <tr key={c.id}>
                    <td>{c.name}</td>
                    <td>
                      <select value={c.state_id ?? ''} disabled={busy} aria-label={`${c.name}の状態`}
                        onChange={e => editCast(c.id, { state_id: e.target.value ? Number(e.target.value) : null })}>
                        <option value="">ふだん</option>
                        {states.map(st => <option key={st.id} value={st.id}>{st.name}{st.adult_only.length ? '(成人のみ)' : ''}</option>)}
                      </select>
                    </td>
                    <td><input value={c.outfit_tags} placeholder="服装(空欄ならふだん)" disabled={busy} aria-label={`${c.name}の服装`}
                      onChange={e => editCast(c.id, { outfit_tags: e.target.value })} /></td>
                    <td><input value={c.action_tags} placeholder="動作・表情" disabled={busy} aria-label={`${c.name}の動作`}
                      onChange={e => editCast(c.id, { action_tags: e.target.value })} /></td>
                    <td><button type="button" disabled={busy} aria-label={`${c.name}を外す`}
                      onClick={() => setCast(scene.characters.filter(x => x.id !== c.id))}>✕</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <select value="" disabled={busy || addable.length === 0} aria-label="登場人物を足す"
              onChange={e => {
                const c = characters.find(x => x.id === Number(e.target.value))
                if (c) setCast([...scene.characters, { id: c.id, name: c.name, action_tags: '', outfit_tags: '', state_id: null }])
              }}>
              <option value="">＋ 登場人物を足す</option>
              {addable.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>

          <div className="sed-label">セリフ{!scene.dialogue_custom && !scene.dialogue_touched && <small>(本文の「」から。直すとこの並びで保存します)</small>}
            <ol className="sed-lines">
              {scene.dialogue.map((l, i) => (
                <li key={i}>
                  <select value={l.speaker_id ?? ''} disabled={busy} aria-label={`セリフ${i + 1}の話し手`}
                    onChange={e => editLine(i, { speaker_id: e.target.value ? Number(e.target.value) : null })}>
                    <option value="">話し手なし</option>
                    {scene.characters.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                  </select>
                  <input value={l.text} disabled={busy} aria-label={`セリフ${i + 1}`} placeholder="セリフ(（…）で囲むと心の声)"
                    onChange={e => editLine(i, { text: e.target.value })} />
                  <button type="button" disabled={busy || i === 0} aria-label="上へ" onClick={() => onLines(move(scene.dialogue, i, i - 1))}>▲</button>
                  <button type="button" disabled={busy || i === scene.dialogue.length - 1} aria-label="下へ" onClick={() => onLines(move(scene.dialogue, i, i + 1))}>▼</button>
                  <button type="button" disabled={busy} aria-label={`セリフ${i + 1}を消す`} onClick={() => onLines(scene.dialogue.filter((_, j) => j !== i))}>✕</button>
                </li>
              ))}
            </ol>
            <button type="button" disabled={busy} onClick={() => onLines([...scene.dialogue, { text: '', speaker_id: null }])}>＋ セリフを足す</button>
          </div>

          <div className="sed-row">
            <label className="sed-label">ナレーション
              <input value={scene.narration ?? ''} disabled={busy} placeholder="(なし)"
                onChange={e => onChange({ narration: e.target.value || null })} />
            </label>
            <label className="sed-label">効果音(、区切り)
              <input value={(scene.sfx ?? []).join('、')} disabled={busy} placeholder="ドキッ、ザワ…"
                onChange={e => onChange({ sfx: e.target.value.split(/[、,]/).map(w => w.trim()).filter(Boolean) })} />
            </label>
          </div>

          <details className="sed-text">
            <summary>本文</summary>
            <textarea rows={5} value={scene.text} disabled={busy} onChange={e => onChange({ text: e.target.value })} />
          </details>
        </div>
      </div>
    </div>
  )
}
