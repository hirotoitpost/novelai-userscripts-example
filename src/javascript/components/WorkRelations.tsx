import { useEffect, useState } from 'react'
import { API_ORIGIN, getJson, readErrorDetail, thumbUrl } from '../library'

export interface WorkRef {
  kind: 'series' | 'story'
  id: number
}

export interface Author {
  id: number
  name: string
  platform: string
  url: string
}

interface Work extends WorkRef {
  title: string
  cover_path: string | null
  volume_count: number
  adult: boolean
  first_story_id: number
}

interface RelatedWork {
  relation_id: number
  type: 'spinoff' | 'crossover'
  role: 'spinoff_of' | 'has_spinoff' | 'crossover'
  work: Work
}

interface Relations {
  authors: Author[]
  related: RelatedWork[]
}

// 追加するときの関連の向き。spinoff_of: この作品が相手のスピンオフ / has_spinoff: 相手がこの作品のスピンオフ
type NewRole = 'spinoff_of' | 'has_spinoff' | 'crossover'

const ROLE_LABELS: Record<RelatedWork['role'], string> = {
  spinoff_of: 'この作品の元作品',
  has_spinoff: 'スピンオフ',
  crossover: 'クロスオーバー',
}

const NEW_ROLE_LABELS: Record<NewRole, string> = {
  spinoff_of: 'この作品は、次の作品のスピンオフ',
  has_spinoff: '次の作品は、この作品のスピンオフ',
  crossover: '次の作品とクロスオーバー',
}

async function send(method: string, path: string, body?: unknown): Promise<unknown> {
  const res = await fetch(`${API_ORIGIN}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) throw new Error(await readErrorDetail(res))
  return res.status === 204 ? null : res.json()
}

interface Props {
  work: WorkRef
  /** 関連作品を開く(物語ID) */
  onOpen: (storyId: number) => void
  /** 作者を付け外ししたので、本棚の作者表示・絞り込みを読み直す */
  onChanged: () => void
  /** 成人向けの表紙をぼかすか */
  blurAdult: boolean
}

/**
 * 本の画面の「作者・関連作品」。作者(出典・原作者)の付け外しと、スピンオフ・クロスオーバーの
 * 関連の追加・削除をする。関連は巻ではなく作品(シリーズ全体、または単巻の物語)に付く。
 */
export default function WorkRelations({ work, onOpen, onChanged, blurAdult }: Props) {
  const [relations, setRelations] = useState<Relations | null>(null)
  const [authors, setAuthors] = useState<(Author & { work_count: number })[]>([])
  const [works, setWorks] = useState<Work[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [authorToAdd, setAuthorToAdd] = useState('')
  const [newRole, setNewRole] = useState<NewRole>('crossover')
  const [workToAdd, setWorkToAdd] = useState('')

  function load() {
    getJson<Relations>(`/api/works/${work.kind}/${work.id}/relations`)
      .then(setRelations)
      .catch(e => setError(e instanceof Error ? e.message : String(e)))
    getJson<(Author & { work_count: number })[]>('/api/authors').then(setAuthors).catch(() => {})
    getJson<Work[]>('/api/works').then(setWorks).catch(() => {})
  }

  useEffect(load, [work.kind, work.id])

  async function run(task: () => Promise<unknown>, changed = false) {
    setBusy(true)
    setError(null)
    try {
      await task()
      load()
      if (changed) onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function addAuthor() {
    if (authorToAdd === 'new') {
      const name = window.prompt('作者(出典・原作者)の名前:')?.trim()
      if (!name) return
      const platform = window.prompt('出典(サイト・サービス名など。無ければ空のまま):', '')?.trim() ?? ''
      void run(async () => {
        const created = (await send('POST', '/api/authors', { name, platform })) as Author
        await send('POST', `/api/works/${work.kind}/${work.id}/authors`, { author_id: created.id })
      }, true)
    } else if (authorToAdd) {
      void run(() => send('POST', `/api/works/${work.kind}/${work.id}/authors`, { author_id: Number(authorToAdd) }), true)
    }
    setAuthorToAdd('')
  }

  function addRelation() {
    const [kind, id] = workToAdd.split(':')
    if (!kind || !id) return
    const other = { kind, id: Number(id) }
    const self = { kind: work.kind, id: work.id }
    const body = newRole === 'has_spinoff'
      ? { type: 'spinoff', source: other, target: self }
      : { type: newRole === 'spinoff_of' ? 'spinoff' : 'crossover', source: self, target: other }
    void run(() => send('POST', '/api/works/relations', body))
    setWorkToAdd('')
  }

  function removeRelation(rel: RelatedWork) {
    if (!window.confirm(`「${rel.work.title}」との関連(${ROLE_LABELS[rel.role]})を外します。作品は消えません。`)) return
    void run(() => send('DELETE', `/api/works/relations/${rel.relation_id}`))
  }

  const attached = new Set(relations?.authors.map(a => a.id) ?? [])
  const related = new Set(relations?.related.map(r => `${r.work.kind}:${r.work.id}`) ?? [])
  const candidates = works.filter(w => !(w.kind === work.kind && w.id === work.id) && !related.has(`${w.kind}:${w.id}`))

  return (
    <details className="work-relations">
      <summary>
        作者・関連作品
        {relations && (relations.authors.length > 0 || relations.related.length > 0) && (
          <span className="work-relations-count">
            {relations.authors.map(a => a.name).join('、')}
            {relations.related.length > 0 && ` ・ 関連${relations.related.length}作品`}
          </span>
        )}
      </summary>

      {error && <p className="work-relations-error">{error}</p>}

      <div className="work-relations-block">
        <h4>作者(出典・原作者)</h4>
        <div className="work-relations-chips">
          {relations?.authors.length === 0 && <span className="work-relations-muted">まだありません</span>}
          {relations?.authors.map(a => (
            <span key={a.id} className="work-relations-chip">
              {a.platform ? `${a.name}(${a.platform})` : a.name}
              <button
                type="button"
                aria-label={`${a.name}を外す`}
                disabled={busy}
                onClick={() => void run(() => send('DELETE', `/api/works/${work.kind}/${work.id}/authors/${a.id}`), true)}
              >
                ×
              </button>
            </span>
          ))}
        </div>
        <div className="work-relations-add">
          <select value={authorToAdd} onChange={e => setAuthorToAdd(e.target.value)} disabled={busy} aria-label="作者を選ぶ">
            <option value="">作者を追加…</option>
            {authors.filter(a => !attached.has(a.id)).map(a => (
              <option key={a.id} value={a.id}>
                {a.platform ? `${a.name}(${a.platform})` : a.name}
              </option>
            ))}
            <option value="new">＋ 新しい作者を登録</option>
          </select>
          <button type="button" disabled={busy || !authorToAdd} onClick={addAuthor}>追加</button>
        </div>
      </div>

      <div className="work-relations-block">
        <h4>関連作品</h4>
        {relations?.related.length === 0 && <p className="work-relations-muted">まだありません</p>}
        <ul className="work-relations-list">
          {relations?.related.map(rel => (
            <li key={rel.relation_id}>
              <button type="button" className="work-relations-open" onClick={() => onOpen(rel.work.first_story_id)}>
                {rel.work.cover_path ? (
                  <img
                    src={thumbUrl(rel.work.cover_path, 240)}
                    alt=""
                    className={rel.work.adult && blurAdult ? 'is-blurred' : undefined}
                  />
                ) : (
                  <span className="work-relations-nocover" />
                )}
                <span>
                  <small>{ROLE_LABELS[rel.role]}</small>
                  {rel.work.title}
                  {rel.work.kind === 'series' && <small>全{rel.work.volume_count}巻</small>}
                </span>
              </button>
              <button type="button" className="work-relations-remove" disabled={busy}
                aria-label="関連を外す" onClick={() => removeRelation(rel)}>×</button>
            </li>
          ))}
        </ul>
        <div className="work-relations-add">
          <select value={newRole} onChange={e => setNewRole(e.target.value as NewRole)} disabled={busy} aria-label="関連の種類">
            {(Object.keys(NEW_ROLE_LABELS) as NewRole[]).map(role => (
              <option key={role} value={role}>{NEW_ROLE_LABELS[role]}</option>
            ))}
          </select>
          <select value={workToAdd} onChange={e => setWorkToAdd(e.target.value)} disabled={busy} aria-label="関連付ける作品">
            <option value="">作品を選ぶ…</option>
            {candidates.map(w => (
              <option key={`${w.kind}:${w.id}`} value={`${w.kind}:${w.id}`}>
                {w.title}{w.kind === 'series' ? `(全${w.volume_count}巻)` : ''}
              </option>
            ))}
          </select>
          <button type="button" disabled={busy || !workToAdd} onClick={addRelation}>追加</button>
        </div>
      </div>
    </details>
  )
}
