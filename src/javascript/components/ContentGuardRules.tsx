import { useEffect, useRef, useState } from 'react'
import { guardFetch } from '../guardFetch'
import './GuardSafety.css'

interface Rule {
  key: string
  group: string
  label: string
  description: string
  kind: 'patterns' | 'words' | 'text' | 'int' | 'choice'
  match: 'word' | 'tag' | 'substring' | 'exact' | null
  value: string[] | string | number
  default: string[] | string | number
  modified: boolean
  minimum: number | null
  maximum: number | null
  /** choice の選択肢(先のものほど強い守り) */
  choices: { value: string; label: string }[]
  updated_at: string | null
}

type Direction = 'same' | 'stricter' | 'looser' | 'invalid'

interface PlanItem {
  key?: string
  name?: string
  label?: string
  direction: Direction
  added?: string[]
  removed?: string[]
  reason?: string
}

interface ImportPlan {
  exported_at: string | null
  rules: PlanItem[]
  profiles: PlanItem[]
}

const DIRECTION_LABELS: Record<Direction, string> = {
  same: '同じ',
  stricter: '強める',
  looser: '緩める(パスワードが要る)',
  invalid: '取り込めない',
}

interface CheckResult {
  dataset_blocked: string[]
  dataset_minor: string[]
  sexual: boolean
  library_adult: boolean
  scene_tags: string
  scene_tags_adult: string
}

const API = '/api/content-guard'

const MATCH_LABELS: Record<string, string> = {
  word: '単語として当たる',
  tag: 'タグ全体が一致したときだけ当たる',
  substring: '文中のどこにあっても当たる',
  exact: '完全一致(大文字・小文字を区別)',
}

/** 画面の入力欄に出す形(並びは1行に1つ)。 */
function toText(value: Rule['value']): string {
  return Array.isArray(value) ? value.join('\n') : String(value)
}

function shown(rule: Rule, value: Rule['value']): string {
  if (rule.kind === 'choice') return rule.choices.find(c => c.value === value)?.label ?? String(value)
  return toText(value)
}

async function errorOf(res: Response): Promise<string> {
  try {
    const detail = (await res.json()).detail
    return typeof detail === 'string' ? detail : JSON.stringify(detail)
  } catch {
    return `${res.status} ${res.statusText}`
  }
}

/**
 * コンテンツガードの定義(止めるタグ・取り除くタグ・足すネガティブ・LLM への指示・成人向けの判定の語)の編集。
 * 値は DB にあり、保存すると次の処理から効く。保存・戻したときは onSaved で知らせる。
 */
export default function ContentGuardRules({ onSaved }: { onSaved?: (what: string) => void }) {
  const [rules, setRules] = useState<Rule[]>([])
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [checkTags, setCheckTags] = useState('')
  const [checked, setChecked] = useState<CheckResult | null>(null)
  const [plan, setPlan] = useState<{ data: unknown; plan: ImportPlan } | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [notice, setNotice] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  function load() {
    fetch(`${API}/rules`)
      .then(async r => (r.ok ? setRules((await r.json()).rules) : setError(await errorOf(r))))
      .catch(e => setError(String(e)))
  }
  useEffect(load, [])

  function replace(rule: Rule) {
    setRules(prev => prev.map(r => (r.key === rule.key ? rule : r)))
    setDrafts(({ [rule.key]: _done, ...rest }) => rest)
  }

  async function save(rule: Rule) {
    setError(null)
    const draft = drafts[rule.key]
    const value = rule.kind === 'text' || rule.kind === 'choice' ? draft : rule.kind === 'int' ? Number(draft) : draft.split('\n')
    const res = await guardFetch(`${API}/rules/${rule.key}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value }),
    })
    if (!res.ok) return setError(`${rule.label}: ${await errorOf(res)}`)
    replace(await res.json())
    onSaved?.(rule.label)
  }

  async function reset(rule: Rule) {
    if (!window.confirm(`「${rule.label}」をはじめの値に戻します。よろしいですか?`)) return
    setError(null)
    const res = await fetch(`${API}/rules/${rule.key}`, { method: 'DELETE' })
    if (!res.ok) return setError(`${rule.label}: ${await errorOf(res)}`)
    replace(await res.json())
    onSaved?.(`${rule.label}(はじめの値に戻す)`)
  }

  async function resetAll() {
    if (!window.confirm('コンテンツガードのすべての項目を、はじめの値に戻します。よろしいですか?')) return
    setError(null)
    const res = await fetch(`${API}/reset`, { method: 'POST' })
    if (!res.ok) return setError(await errorOf(res))
    setRules((await res.json()).rules)
    setDrafts({})
    onSaved?.('コンテンツガードのすべての項目(はじめの値に戻す)')
  }

  async function exportRules() {
    setError(null)
    const res = await fetch(`${API}/export`)
    if (!res.ok) return setError(await errorOf(res))
    const data = await res.json()
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = `content-guard_${String(data.exported_at).slice(0, 10)}.json`
    link.click()
    URL.revokeObjectURL(link.href)
  }

  async function previewImport(file: File) {
    setError(null)
    setNotice(null)
    let data: unknown
    try {
      data = JSON.parse(await file.text())
    } catch {
      return setError('JSON として読めないファイルです。')
    }
    const res = await fetch(`${API}/import/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data }),
    })
    if (!res.ok) return setError(await errorOf(res))
    const next: ImportPlan = await res.json()
    setPlan({ data, plan: next })
    // はじめは、取り込める変更をすべて選んでおく
    setPicked(new Set([
      ...next.rules.filter(r => r.direction === 'stricter' || r.direction === 'looser').map(r => `rule:${r.key}`),
      ...next.profiles.filter(p => p.direction === 'stricter' || p.direction === 'looser').map(p => `profile:${p.name}`),
    ]))
  }

  async function applyImport() {
    if (!plan) return
    setError(null)
    const keys = [...picked].filter(k => k.startsWith('rule:')).map(k => k.slice(5))
    const profiles = [...picked].filter(k => k.startsWith('profile:')).map(k => k.slice(8))
    const res = await guardFetch(`${API}/import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data: plan.data, keys, profiles }),
    })
    if (!res.ok) return setError(await errorOf(res))
    const done = await res.json()
    setPlan(null)
    setNotice(`取り込みました(項目${done.rules}件・ガードプロファイル${done.profiles}件)`)
    load()
    onSaved?.('コンテンツガードの取り込み')
  }

  function toggle(id: string, on: boolean) {
    setPicked(prev => {
      const next = new Set(prev)
      if (on) next.add(id)
      else next.delete(id)
      return next
    })
  }

  async function check() {
    setError(null)
    const res = await fetch(`${API}/check`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tags: checkTags }),
    })
    if (!res.ok) return setError(await errorOf(res))
    setChecked(await res.json())
  }

  const groups = [...new Set(rules.map(r => r.group))]
  const modified = rules.filter(r => r.modified).length
  const planRows = plan ? [
    ...plan.plan.rules.map(r => ({ id: `rule:${r.key}`, title: r.label ?? r.key ?? '', item: r })),
    ...plan.plan.profiles.map(p => ({ id: `profile:${p.name}`, title: `ガードプロファイル「${p.name}」`, item: p })),
  ].filter(row => row.item.direction !== 'same') : []
  return (
    <>
      <div className="adm-row guard-top">
        <span className="adm-hint">はじめの値と違う項目: {modified}件</span>
        <button type="button" className="adm-danger" disabled={modified === 0} onClick={() => void resetAll()}
          title="すべての項目をはじめの値に戻します(パスワードは要りません)">すべてはじめの値に戻す</button>
        <button type="button" onClick={() => void exportRules()} title="すべての項目とガードプロファイルを JSON ファイルに書き出します">書き出す</button>
        <button type="button" onClick={() => fileRef.current?.click()} title="書き出したファイルを読み、違いを確かめてから取り込みます">取り込む</button>
        <input ref={fileRef} type="file" accept="application/json,.json" hidden aria-label="取り込むファイル"
          onChange={e => { const file = e.target.files?.[0]; e.target.value = ''; if (file) void previewImport(file) }} />
      </div>
      {notice && <p className="adm-hint" role="status">{notice}</p>}
      {plan && (
        <div className="guard-import">
          <h3>取り込む内容の確認</h3>
          <p className="adm-hint">
            {plan.plan.exported_at ? `${plan.plan.exported_at.slice(0, 16).replace('T', ' ')} に書き出したファイルです。` : ''}
            今の値と違う項目だけを出しています。「緩める」項目を取り込むときは、パスワードを聞きます。
          </p>
          {planRows.length === 0 && <p className="adm-hint">今の値と同じです。取り込むものはありません。</p>}
          <ul className="guard-import-list">
            {planRows.map(({ id, title, item }) => (
              <li key={id} className={`guard-import-${item.direction}`}>
                <label>
                  <input type="checkbox" disabled={item.direction === 'invalid'} checked={picked.has(id)}
                    onChange={e => toggle(id, e.target.checked)} />
                  {' '}<strong>{title}</strong> <span className="adm-tag">{DIRECTION_LABELS[item.direction]}</span>
                </label>
                {item.reason && <span className="adm-hint"> {item.reason}</span>}
                {!!item.added?.length && <div className="adm-hint">増える: {item.added.join(', ')}</div>}
                {!!item.removed?.length && <div className="adm-hint">減る: {item.removed.join(', ')}</div>}
              </li>
            ))}
          </ul>
          <div className="adm-row">
            <button type="button" className="adm-primary" disabled={picked.size === 0} onClick={() => void applyImport()}>選んだ項目を取り込む</button>
            <button type="button" onClick={() => setPlan(null)}>やめる</button>
          </div>
        </div>
      )}
      {error && <p className="adm-error" role="alert">{error}</p>}
      {groups.map(group => (
        <div key={group}>
          <h3>{group}</h3>
          {rules.filter(r => r.group === group).map(rule => {
            const draft = drafts[rule.key] ?? toText(rule.value)
            const dirty = draft !== toText(rule.value)
            const list = rule.kind === 'patterns' || rule.kind === 'words'
            const count = list ? draft.split('\n').filter(l => l.trim()).length : 0
            return (
              <details key={rule.key} className="adm-preset">
                <summary>
                  <strong>{rule.label}</strong>
                  {rule.modified && <span className="adm-tag">変更あり</span>}
                  {dirty && <span className="adm-tag">未保存</span>}
                  {' '}<span className="adm-hint">{list ? `${(rule.value as string[]).length}件` : shown(rule, rule.value)}</span>
                </summary>
                <p className="adm-hint">{rule.description}</p>
                <p className="adm-hint">
                  <code>{rule.key}</code>
                  {list && ` ・ 1行に1つ(${count}件)`}
                  {rule.kind === 'patterns' && ' ・ 正規表現(大文字・小文字を区別しない)'}
                  {rule.match && ` ・ ${MATCH_LABELS[rule.match]}`}
                </p>
                {rule.kind === 'choice'
                  ? <select value={draft} onChange={e => setDrafts({ ...drafts, [rule.key]: e.target.value })}>
                      {rule.choices.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
                    </select>
                  : rule.kind === 'int'
                  ? <input type="number" step={1} min={rule.minimum ?? undefined} max={rule.maximum ?? undefined}
                      value={draft} onChange={e => setDrafts({ ...drafts, [rule.key]: e.target.value })} />
                  : <textarea rows={list ? Math.min(16, Math.max(4, count + 1)) : 3} spellCheck={false} value={draft}
                      onChange={e => setDrafts({ ...drafts, [rule.key]: e.target.value })} />}
                <div className="adm-row">
                  <button type="button" className="adm-primary" disabled={!dirty} onClick={() => void save(rule)}>保存</button>
                  <button type="button" disabled={!rule.modified} onClick={() => void reset(rule)}>はじめの値に戻す</button>
                  {rule.modified && <span className="adm-hint">はじめの値: {shown(rule, rule.default).replace(/\n/g, ', ')}</span>}
                </div>
              </details>
            )
          })}
        </div>
      ))}

      <h3>タグで確かめる</h3>
      <p className="adm-hint">タグを入れると、保存済みの定義でどう扱われるかを表示します。</p>
      <textarea rows={2} spellCheck={false} placeholder="例: 1girl, nude, school uniform, bed" value={checkTags}
        onChange={e => setCheckTags(e.target.value)} />
      <div className="adm-row">
        <button type="button" disabled={!checkTags.trim()} onClick={() => void check()}>確かめる</button>
      </div>
      {checked && (
        <table className="adm-table adm-table--rows">
          <tbody>
            <tr><th>全年齢のデータセットで止まるタグ</th><td>{checked.dataset_blocked.join(', ') || '(なし)'}</td></tr>
            <tr><th>R18 のデータセットで止まるタグ</th><td>{checked.dataset_minor.join(', ') || '(なし)'}</td></tr>
            <tr><th>性的な場面とみなすか</th><td>{checked.sexual ? 'はい' : 'いいえ'}</td></tr>
            <tr><th>本棚・ギャラリーで成人向けか</th><td>{checked.library_adult ? 'はい' : 'いいえ'}</td></tr>
            <tr><th>漫画のコマに送るタグ</th><td>{checked.scene_tags || '(空)'}</td></tr>
            <tr><th>成人向けのタグ付けの結果</th><td>{checked.scene_tags_adult || '(空)'}</td></tr>
          </tbody>
        </table>
      )}
    </>
  )
}
