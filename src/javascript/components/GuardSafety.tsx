import { useEffect, useState } from 'react'
import { forgetGuardToken, guardFetch, guardHeaders, unlockGuard } from '../guardFetch'
import './GuardSafety.css'

/**
 * 未成年キャラの安全対策の管理(管理ページのコンテンツガードの欄):
 * - パスワード: 守りを緩める変更に要る。はじめの設定・変更・いま開いているか
 * - キャラの年齢区分: 未成年 / 成人 / 未定義 と、未成年の手がかり(キャラシートのタグ・メモ)
 * - 記録: 性的な場面を止めた・全年齢で描いた場所と理由
 * 未定義のキャラの扱い(止める / 全年齢で描く)は、下の項目の一覧(未成年キャラ)で変える。
 */

interface PasswordState {
  set: boolean
  unlocked: boolean
  min_length: number
}

interface CharacterAge {
  id: number
  name: string
  state: 'minor' | 'adult' | 'undefined'
  defined: 'minor' | 'adult' | 'undefined'
  reasons: string[]
}

interface GuardEvent {
  id: number
  created_at: string
  action: 'block' | 'all_ages'
  story_id: number | null
  place: string
  detail: string
}

const API = '/api/content-guard'
const STATE_LABELS = { minor: '未成年', adult: '成人', undefined: '未定義' } as const

async function errorOf(res: Response): Promise<string> {
  const detail = (await res.json().catch(() => null))?.detail
  return typeof detail === 'string' ? detail : detail?.message ?? `HTTP ${res.status}`
}

export default function GuardSafety({ onSaved }: { onSaved?: (what: string) => void }) {
  const [state, setState] = useState<PasswordState | null>(null)
  const [characters, setCharacters] = useState<CharacterAge[]>([])
  const [events, setEvents] = useState<GuardEvent[]>([])
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<'all' | 'minor' | 'undefined'>('minor')

  function load() {
    fetch(`${API}/password`, { headers: guardHeaders() }).then(r => r.json()).then(setState).catch(() => {})
    fetch(`${API}/characters`).then(r => r.json()).then(setCharacters).catch(() => {})
    fetch(`${API}/events?limit=50`).then(r => r.json()).then(setEvents).catch(() => {})
  }
  useEffect(load, [])

  async function run(work: () => Promise<string>) {
    setError(null)
    setMessage(null)
    try {
      setMessage(await work())
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const mismatch = next !== again
  const short = !!state && next.length > 0 && next.length < state.min_length

  const setup = () => run(async () => {
    const res = await fetch(`${API}/password/setup`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: next }),
    })
    if (!res.ok) throw new Error(await errorOf(res))
    setNext('')
    setAgain('')
    onSaved?.('コンテンツガードのパスワードの設定')
    return 'パスワードを設定しました。守りを緩める変更のときに聞きます。'
  })

  const change = () => run(async () => {
    const res = await fetch(`${API}/password/change`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ current, new: next }),
    })
    if (!res.ok) throw new Error(await errorOf(res))
    forgetGuardToken()
    setCurrent('')
    setNext('')
    setAgain('')
    onSaved?.('コンテンツガードのパスワードの変更')
    return 'パスワードを変えました。'
  })

  const unlock = () => run(async () => {
    await unlockGuard(current)
    setCurrent('')
    return '開けました。10分のあいだ、守りを緩める変更ができます。'
  })

  const lock = () => run(async () => {
    forgetGuardToken()
    return '閉じました。'
  })

  const setAge = (character: CharacterAge, value: string) => run(async () => {
    const body = value === 'minor' ? { is_minor: true } : value === 'adult' ? { is_adult: true } : { is_adult: false, is_minor: false }
    const res = await guardFetch(`/api/story/characters/${character.id}/sheet`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    if (!res.ok) throw new Error(`${character.name}: ${await errorOf(res)}`)
    onSaved?.(`${character.name}の年齢区分`)
    return `${character.name}を「${STATE_LABELS[value as keyof typeof STATE_LABELS]}」にしました。`
  })

  const shown = characters.filter(c => filter === 'all' || c.state === filter)
  return (
    <div className="guard-safety">
      {error && <p className="adm-error" role="alert">{error}</p>}
      {message && <p className="adm-hint" role="status">{message}</p>}

      <h3>パスワード(守りを緩める変更に使う)</h3>
      <p className="adm-hint">
        止める語を減らす・未定義のキャラの扱いを変える・未成年の定義を外す・ガードプロファイルを消す、といった変更のときだけ聞きます。
        はじめの値に戻す・守りを強める変更には要りません。忘れたときは、この PC で <code>uv run python scripts/reset_guard_password.py</code> を実行します。
      </p>
      {state && !state.set && (
        <div className="adm-row guard-password">
          <span className="adm-tag">まだ設定していません(緩める変更はできません)</span>
          <input type="password" autoComplete="new-password" placeholder={`新しいパスワード(${state.min_length}文字以上)`}
            aria-label="新しいパスワード" value={next} onChange={e => setNext(e.target.value)} />
          <input type="password" autoComplete="new-password" placeholder="もう一度" aria-label="新しいパスワード(もう一度)"
            value={again} onChange={e => setAgain(e.target.value)} />
          <button type="button" className="adm-primary" disabled={!next || mismatch || short} onClick={() => void setup()}>設定する</button>
          {mismatch && again && <span className="adm-hint">2つが合っていません</span>}
        </div>
      )}
      {state?.set && (
        <>
          <div className="adm-row guard-password">
            <span className="adm-tag">{state.unlocked ? '開いています(10分まで)' : '閉じています'}</span>
            {state.unlocked
              ? <button type="button" onClick={() => void lock()}>閉じる</button>
              : (
                <>
                  <input type="password" autoComplete="current-password" placeholder="パスワード" aria-label="パスワード"
                    value={current} onChange={e => setCurrent(e.target.value)} />
                  <button type="button" disabled={!current} onClick={() => void unlock()}>開ける</button>
                </>
              )}
          </div>
          <details className="adm-preset">
            <summary><strong>パスワードの変更</strong></summary>
            <div className="adm-row guard-password">
              <input type="password" autoComplete="current-password" placeholder="今のパスワード" aria-label="今のパスワード"
                value={current} onChange={e => setCurrent(e.target.value)} />
              <input type="password" autoComplete="new-password" placeholder={`新しいパスワード(${state.min_length}文字以上)`}
                aria-label="新しいパスワード" value={next} onChange={e => setNext(e.target.value)} />
              <input type="password" autoComplete="new-password" placeholder="もう一度" aria-label="新しいパスワード(もう一度)"
                value={again} onChange={e => setAgain(e.target.value)} />
              <button type="button" className="adm-primary" disabled={!current || !next || mismatch || short} onClick={() => void change()}>変える</button>
              {mismatch && again && <span className="adm-hint">2つが合っていません</span>}
            </div>
          </details>
        </>
      )}

      <h3>キャラの年齢区分</h3>
      <p className="adm-hint">
        性的な場面に「未成年」のキャラがいれば止めます。「未定義」のキャラの扱いは、下の項目「年齢区分が未定義のキャラの扱い」で決めます。
        キャラシートのタグやメモに未成年の手がかりがあれば、成人と定義していても未成年として扱います(物語の本文の手がかりは、物語ごとに生成のときに見ます)。
      </p>
      <div className="adm-row">
        {(['minor', 'undefined', 'all'] as const).map(f => (
          <label key={f}>
            <input type="radio" name="guard-age-filter" checked={filter === f} onChange={() => setFilter(f)} />
            {' '}{f === 'all' ? `すべて(${characters.length})` : `${STATE_LABELS[f]}(${characters.filter(c => c.state === f).length})`}
          </label>
        ))}
      </div>
      <table className="adm-table guard-ages">
        <thead><tr><th>キャラ</th><th>扱い</th><th>定義</th><th>理由</th></tr></thead>
        <tbody>
          {shown.map(c => (
            <tr key={c.id}>
              <td>{c.name}</td>
              <td>{STATE_LABELS[c.state]}</td>
              <td>
                <select value={c.defined} aria-label={`${c.name}の年齢区分`} onChange={e => void setAge(c, e.target.value)}>
                  <option value="undefined">未定義</option>
                  <option value="minor">未成年</option>
                  <option value="adult">成人</option>
                </select>
              </td>
              <td className="adm-hint">{c.reasons.join(' / ')}</td>
            </tr>
          ))}
          {shown.length === 0 && <tr><td colSpan={4} className="adm-hint">該当するキャラはいません</td></tr>}
        </tbody>
      </table>

      <h3>止めた・全年齢で描いた記録</h3>
      {events.length === 0
        ? <p className="adm-hint">まだありません。</p>
        : (
          <table className="adm-table guard-events">
            <thead><tr><th>日時</th><th>扱い</th><th>場所</th><th>理由</th></tr></thead>
            <tbody>
              {events.map(e => (
                <tr key={e.id}>
                  <td>{new Date(e.created_at).toLocaleString('ja-JP')}</td>
                  <td>{e.action === 'block' ? '止めた' : '全年齢で描いた'}</td>
                  <td>{e.story_id !== null ? `物語${e.story_id} ・ ` : ''}{e.place}</td>
                  <td className="adm-hint">{e.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
    </div>
  )
}
