import { useEffect, useState } from 'react'

interface Rule {
  key: string
  group: string
  label: string
  description: string
  kind: 'patterns' | 'words' | 'text' | 'int'
  match: 'word' | 'tag' | 'substring' | 'exact' | null
  value: string[] | string | number
  default: string[] | string | number
  modified: boolean
  minimum: number | null
  maximum: number | null
  updated_at: string | null
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
 * 値は DB にあり、保存すると次の処理から効く。
 */
export default function ContentGuardRules() {
  const [rules, setRules] = useState<Rule[]>([])
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [checkTags, setCheckTags] = useState('')
  const [checked, setChecked] = useState<CheckResult | null>(null)

  useEffect(() => {
    fetch(`${API}/rules`)
      .then(async r => (r.ok ? setRules((await r.json()).rules) : setError(await errorOf(r))))
      .catch(e => setError(String(e)))
  }, [])

  function replace(rule: Rule) {
    setRules(prev => prev.map(r => (r.key === rule.key ? rule : r)))
    setDrafts(({ [rule.key]: _done, ...rest }) => rest)
  }

  async function save(rule: Rule) {
    setError(null)
    const draft = drafts[rule.key]
    const value = rule.kind === 'text' ? draft : rule.kind === 'int' ? Number(draft) : draft.split('\n')
    const res = await fetch(`${API}/rules/${rule.key}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value }),
    })
    if (!res.ok) return setError(`${rule.label}: ${await errorOf(res)}`)
    replace(await res.json())
  }

  async function reset(rule: Rule) {
    if (!window.confirm(`「${rule.label}」をはじめの値に戻します。よろしいですか?`)) return
    setError(null)
    const res = await fetch(`${API}/rules/${rule.key}`, { method: 'DELETE' })
    if (!res.ok) return setError(`${rule.label}: ${await errorOf(res)}`)
    replace(await res.json())
  }

  async function resetAll() {
    if (!window.confirm('コンテンツガードのすべての項目を、はじめの値に戻します。よろしいですか?')) return
    setError(null)
    const res = await fetch(`${API}/reset`, { method: 'POST' })
    if (!res.ok) return setError(await errorOf(res))
    setRules((await res.json()).rules)
    setDrafts({})
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
  return (
    <>
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
                  {' '}<span className="adm-hint">{list ? `${(rule.value as string[]).length}件` : toText(rule.value)}</span>
                </summary>
                <p className="adm-hint">{rule.description}</p>
                <p className="adm-hint">
                  <code>{rule.key}</code>
                  {list && ` ・ 1行に1つ(${count}件)`}
                  {rule.kind === 'patterns' && ' ・ 正規表現(大文字・小文字を区別しない)'}
                  {rule.match && ` ・ ${MATCH_LABELS[rule.match]}`}
                </p>
                {rule.kind === 'int'
                  ? <input type="number" step={1} min={rule.minimum ?? undefined} max={rule.maximum ?? undefined}
                      value={draft} onChange={e => setDrafts({ ...drafts, [rule.key]: e.target.value })} />
                  : <textarea rows={list ? Math.min(16, Math.max(4, count + 1)) : 3} spellCheck={false} value={draft}
                      onChange={e => setDrafts({ ...drafts, [rule.key]: e.target.value })} />}
                <div className="adm-row">
                  <button type="button" className="adm-primary" disabled={!dirty} onClick={() => void save(rule)}>保存</button>
                  <button type="button" disabled={!rule.modified} onClick={() => void reset(rule)}>はじめの値に戻す</button>
                  {rule.modified && <span className="adm-hint">はじめの値: {toText(rule.default).replace(/\n/g, ', ')}</span>}
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
        <button type="button" className="adm-danger" onClick={() => void resetAll()}>すべてはじめの値に戻す</button>
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
