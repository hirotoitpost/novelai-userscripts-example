import { useEffect, useRef, useState } from 'react'
import './BodyBaseBatch.css'

/**
 * キャラ一覧で選んだキャラの素体を、1人ずつ順に描く(一括)。1人分の描き方はキャラシートの素体のタブ(BodyBase.tsx)と同じ API:
 * 素体を当てる PUT /api/lora-dataset/character/{id}/body-base → 部位を描く POST(SSE)。
 * 素体を選べば、そのキャラに当ててから描く(当てている素体が替わる)。「各キャラに当てている素体」なら当て直さない。
 * 裸の素体を当てられないキャラ・素体の無いキャラ・描く部位の無いキャラは飛ばして、理由を結果に出す(判定はサーバー)。
 */

type BaseKind = 'suit' | 'swimsuit' | 'nude'

interface Base { id: number; name: string; kind: BaseKind }

interface Options {
  kinds: { value: BaseKind; label: string }[]
  groups: { id: string; label: string }[]
}

interface Part { key: string; group: string; available: boolean; path: string | null }

interface CharacterBody {
  base: Base | null
  modest: boolean
  parts: Part[]
}

interface Target { id: number; name: string }

interface Result {
  id: number
  name: string
  state: 'waiting' | 'running' | 'done' | 'skipped' | 'error'
  text: string
}

interface Props {
  characters: Target[]
  token: string | null
}

const ASSIGNED = 'assigned'

async function detail(res: Response): Promise<string> {
  const data = await res.json().catch(() => null)
  return typeof data?.detail === 'string' ? data.detail : `HTTP ${res.status}`
}

export default function BodyBaseBatch({ characters, token }: Props) {
  const [bases, setBases] = useState<Base[]>([])
  const [options, setOptions] = useState<Options | null>(null)
  const [baseChoice, setBaseChoice] = useState<string>(ASSIGNED)
  const [groups, setGroups] = useState<Set<string>>(new Set(['whole']))
  const [onlyMissing, setOnlyMissing] = useState(true)
  const [useReference, setUseReference] = useState(false)
  const [autoCheck, setAutoCheck] = useState(true)
  const [running, setRunning] = useState(false)
  const [results, setResults] = useState<Result[]>([])
  const [error, setError] = useState<string | null>(null)
  const abort = useRef<AbortController | null>(null)

  useEffect(() => {
    fetch('/api/lora-dataset/body-bases').then(r => (r.ok ? r.json() : [])).then(setBases).catch(() => setBases([]))
    fetch('/api/lora-dataset/body-bases/options').then(r => (r.ok ? r.json() : null)).then(setOptions).catch(() => {})
  }, [])

  // 止めずに閉じた(ページを離れた)ら、描いている途中の呼び出しも切る
  useEffect(() => () => abort.current?.abort(), [])

  const kindLabel = (kind: string) => options?.kinds.find(k => k.value === kind)?.label ?? kind
  const chosenBase = baseChoice === ASSIGNED ? null : bases.find(b => String(b.id) === baseChoice) ?? null

  function update(id: number, state: Result['state'], text: string) {
    setResults(list => list.map(r => (r.id === id ? { ...r, state, text } : r)))
  }

  /** 1人分: 素体を当てる(選んだとき)→ 描く部位を決める → 描く。描いた枚数・失敗の枚数を結果に書く。 */
  async function drawOne(target: Target, signal: AbortSignal) {
    const headers = { 'Content-Type': 'application/json' }
    let body: CharacterBody
    if (chosenBase) {
      const res = await fetch(`/api/lora-dataset/character/${target.id}/body-base`, {
        method: 'PUT', headers, body: JSON.stringify({ body_base_id: chosenBase.id }), signal,
      })
      if (!res.ok) {
        update(target.id, 'skipped', await detail(res))
        return
      }
      body = await res.json()
    } else {
      const res = await fetch(`/api/lora-dataset/character/${target.id}/body-base`, { signal })
      if (!res.ok) {
        update(target.id, 'error', await detail(res))
        return
      }
      body = await res.json()
      if (!body.base) {
        update(target.id, 'skipped', 'このキャラには素体を当てていません。')
        return
      }
    }
    const keys = body.parts
      .filter(p => groups.has(p.group) && p.available && (!onlyMissing || !p.path))
      .map(p => p.key)
    if (keys.length === 0) {
      update(target.id, 'skipped', onlyMissing ? '選んだ部位は描き終わっています。' : '選んだ部位のうち、この素体で描ける部位がありません。')
      return
    }
    const label = `${body.base?.name ?? ''}${body.modest ? '・控えめ' : ''}`
    update(target.id, 'running', `${label}: ${keys.length} 枚を描いています…`)
    const res = await fetch(`/api/lora-dataset/character/${target.id}/body-base`, {
      method: 'POST',
      headers: { ...headers, ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify({ keys, use_reference: useReference, auto_check: autoCheck }),
      signal,
    })
    if (!res.ok || !res.body) {
      update(target.id, 'error', await detail(res))
      return
    }
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let drawn = 0
    let failed = 0
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const event = JSON.parse(line.slice(6)) as {
          current?: number; total?: number; label?: string; status?: string; attempt?: number; reasons?: string[]
        }
        if (event.status === 'retry') {
          update(target.id, 'running', `${label}: ${event.current}/${event.total} ${event.label} を描き直しています(${event.attempt}回目)`)
          continue
        }
        if (event.status === 'ok') drawn++
        if (event.status === 'error') failed++
        if (event.current && event.total) update(target.id, 'running', `${label}: ${event.current}/${event.total} ${event.label}`)
      }
    }
    update(target.id, failed ? 'error' : 'done', `${label}: ${drawn} 枚描きました${failed ? `(失敗 ${failed} 枚)` : ''}`)
  }

  async function run() {
    if (characters.length === 0 || groups.size === 0) return
    const what = chosenBase ? `素体「${chosenBase.name}」を当てて` : '各キャラに当てている素体で'
    if (!window.confirm(`${characters.length} 人のキャラに、${what}素体を描きます。よろしいですか?`)) return
    const controller = new AbortController()
    abort.current = controller
    setRunning(true)
    setError(null)
    setResults(characters.map(c => ({ id: c.id, name: c.name, state: 'waiting', text: '待っています' })))
    try {
      for (const target of characters) {
        if (controller.signal.aborted) break
        try {
          await drawOne(target, controller.signal)
        } catch (e) {
          if (controller.signal.aborted) {
            update(target.id, 'error', '止めました')
            break
          }
          update(target.id, 'error', e instanceof Error ? e.message : String(e))
        }
      }
    } finally {
      setResults(list => list.map(r => (r.state === 'waiting' ? { ...r, state: 'skipped', text: '止めたので描いていません' } : r)))
      abort.current = null
      setRunning(false)
    }
  }

  function toggleGroup(id: string) {
    const next = new Set(groups)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setGroups(next)
  }

  const counts = {
    done: results.filter(r => r.state === 'done').length,
    skipped: results.filter(r => r.state === 'skipped').length,
    error: results.filter(r => r.state === 'error').length,
  }

  return (
    <section className="bbb" aria-label="素体を一括で描く">
      <h2>素体を一括で描く</h2>
      <p className="bbb-hint">
        下のカードで選んだキャラを、1人ずつ順に描きます。素体を選ぶと、そのキャラに当ててから描きます(当てている素体が替わります)。
        裸の素体を当てられないキャラ(成人フラグ・年齢・未成年を示すタグで判定)と、素体の無いキャラは飛ばします。
      </p>
      {error && <p className="bbb-error" role="alert">{error}</p>}
      <div className="bbb-row">
        <label className="bbb-field">
          素体
          <select value={baseChoice} disabled={running} onChange={e => setBaseChoice(e.target.value)}>
            <option value={ASSIGNED}>各キャラに当てている素体</option>
            {bases.map(b => <option key={b.id} value={b.id}>{b.name}({kindLabel(b.kind)})</option>)}
          </select>
        </label>
      </div>
      <div className="bbb-row" role="group" aria-label="描く部位">
        <span className="bbb-label">部位</span>
        {(options?.groups ?? []).map(g => (
          <label key={g.id} className="bbb-check">
            <input type="checkbox" checked={groups.has(g.id)} disabled={running} onChange={() => toggleGroup(g.id)} />
            {g.label}
          </label>
        ))}
      </div>
      <div className="bbb-row">
        <label className="bbb-check">
          <input type="checkbox" checked={onlyMissing} disabled={running} onChange={e => setOnlyMissing(e.target.checked)} />
          描いていない部位だけ
        </label>
        <label className="bbb-check" title="キャラの参照画像を、顔の参照として使います。参照画像がそのキャラの年齢に合っているか、先に確かめてください">
          <input type="checkbox" checked={useReference} disabled={running} onChange={e => setUseReference(e.target.checked)} />
          参照画像を使う
        </label>
        <label className="bbb-check">
          <input type="checkbox" checked={autoCheck} disabled={running} onChange={e => setAutoCheck(e.target.checked)} />
          自動チェック(合わなければ描き直す)
        </label>
      </div>
      <div className="bbb-row">
        {running ? (
          <button type="button" className="bbb-stop" onClick={() => abort.current?.abort()}>止める</button>
        ) : (
          <button type="button" className="bbb-run" disabled={!token || characters.length === 0 || groups.size === 0} onClick={() => void run()}>
            {characters.length} 人を描く
          </button>
        )}
        {!token && <span className="bbb-hint">描くには NovelAI にログインしてください。</span>}
        {characters.length === 0 && <span className="bbb-hint">下のカードの「選ぶ」でキャラを選んでください。</span>}
      </div>
      {results.length > 0 && (
        <>
          <p className="bbb-hint" role="status">
            {running ? '描いています…' : '終わりました'}(描いた {counts.done} 人・飛ばした {counts.skipped} 人・失敗 {counts.error} 人)
          </p>
          <ul className="bbb-results">
            {results.map(r => (
              <li key={r.id} className={`bbb-result bbb-result--${r.state}`}>
                <span className="bbb-name">{r.name}</span>
                <span>{r.text}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
