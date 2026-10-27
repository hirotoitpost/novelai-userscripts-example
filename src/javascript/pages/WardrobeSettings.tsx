import { useEffect, useState } from 'react'
import BackButton from '../components/BackButton'
import './LoraDataset.css'
import './BodyBaseSettings.css'

/**
 * 衣装の設定のページ。キャラの衣装(着せ替え)で選ぶ部品を編集する: 部位(上着・トップス・水着 など)ごとの服の種類と、
 * 色・柄。画面の名前・プロンプトに入れるタグ・色や柄を付けられるか・並べる順。足す・消すもここ。
 * 値(衣装が持つキー)は足したときに決まり、あとから変えない。使っている衣装がある部品は消せない。
 */

interface Part {
  id: number
  slot: string
  value: string
  label: string
  tags: string
  colorable: number
}

interface Draft {
  label: string
  tags: string
  colorable: boolean
}

const NEW_DRAFT: Draft = { label: '', tags: '', colorable: true }
// 色・柄そのものには、色を付けられるかの欄を出さない
const MODIFIERS = new Set(['color', 'pattern'])

function toDraft(p: Part): Draft {
  return { label: p.label, tags: p.tags, colorable: !!p.colorable }
}

async function detail(res: Response): Promise<string> {
  const data = await res.json().catch(() => null)
  return typeof data?.detail === 'string' ? data.detail : `HTTP ${res.status}`
}

export default function WardrobeSettings() {
  const [kinds, setKinds] = useState<{ key: string; label: string }[]>([])
  const [parts, setParts] = useState<Record<string, Part[]>>({})
  // 書きかけの内容(部品の id → 下書き)と、部位ごとの新しい部品の下書き
  const [drafts, setDrafts] = useState<Record<number, Draft>>({})
  const [newDrafts, setNewDrafts] = useState<Record<string, Draft>>({})
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null)
  const [busy, setBusy] = useState(false)

  function load() {
    fetch('/api/wardrobe/parts')
      .then(r => (r.ok ? r.json() : null))
      .then(data => {
        if (!data) return
        setKinds([...data.slots, ...data.modifiers])
        setParts(data.parts)
        setDrafts({})
      })
      .catch(() => setMessage({ text: '部品を読み込めませんでした', error: true }))
  }
  useEffect(load, [])

  async function run(action: () => Promise<Response>, done: string) {
    setBusy(true)
    setMessage(null)
    try {
      const res = await action()
      if (!res.ok) {
        setMessage({ text: await detail(res), error: true })
        return false
      }
      setMessage({ text: done, error: false })
      load()
      return true
    } finally {
      setBusy(false)
    }
  }

  function body(slot: string, d: Draft) {
    return JSON.stringify({ slot, label: d.label, tags: d.tags, colorable: MODIFIERS.has(slot) ? false : d.colorable })
  }

  async function save(p: Part) {
    const d = drafts[p.id]
    if (!d) return
    await run(
      () => fetch(`/api/wardrobe/parts/${p.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: body(p.slot, d) }),
      `「${d.label}」を保存しました`,
    )
  }

  async function add(slot: string) {
    const d = newDrafts[slot] ?? NEW_DRAFT
    const ok = await run(
      () => fetch('/api/wardrobe/parts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body(slot, d) }),
      `「${d.label}」を足しました`,
    )
    if (ok) setNewDrafts({ ...newDrafts, [slot]: NEW_DRAFT })
  }

  async function remove(p: Part) {
    if (!window.confirm(`部品「${p.label}」を消しますか?`)) return
    await run(() => fetch(`/api/wardrobe/parts/${p.id}`, { method: 'DELETE' }), `「${p.label}」を消しました`)
  }

  async function move(slot: string, index: number, delta: number) {
    const list = [...(parts[slot] ?? [])]
    const target = index + delta
    if (target < 0 || target >= list.length) return
    ;[list[index], list[target]] = [list[target], list[index]]
    setParts({ ...parts, [slot]: list })
    await run(
      () => fetch('/api/wardrobe/parts/order', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slot, ids: list.map(p => p.id) }),
      }),
      '並べ替えました',
    )
  }

  function editor(d: Draft, onChange: (d: Draft) => void, slot: string) {
    return (
      <>
        <input className="lora-input" placeholder="画面の名前" value={d.label} disabled={busy}
          onChange={e => onChange({ ...d, label: e.target.value })} />
        <input className="lora-input" placeholder="タグ" value={d.tags} disabled={busy}
          onChange={e => onChange({ ...d, tags: e.target.value })} />
        {MODIFIERS.has(slot) ? <span /> : (
          <label className="bbs-rear" title="色・柄を選べるか(はだし・体操服 など、色を付けない部品は外す)">
            <input type="checkbox" checked={d.colorable} disabled={busy} onChange={e => onChange({ ...d, colorable: e.target.checked })} />
            色・柄を付ける
          </label>
        )}
        <span />
      </>
    )
  }

  return (
    <div className="lora-root">
      <header className="lora-header">
        <BackButton className="lora-back" />
        <span className="lora-header-title">衣装の設定</span>
      </header>
      <main className="bbs-main">
        <p className="lora-hint">
          キャラの衣装(着せ替え)で選ぶ部品を編集します。色・柄は、部品の最初のタグの前に付きます(紺+チェック+プリーツスカート →
          navy blue plaid pleated skirt)。部品はどのキャラの衣装にも入るので、全年齢で止めるタグ・性的なタグ・未成年を示すタグは入れられません。
        </p>
        {message && <p className={message.error ? 'lora-error' : 'lora-hint'} role={message.error ? 'alert' : undefined}>{message.text}</p>}
        {kinds.map(k => (
          <section key={k.key} className="bbs-field">
            <h3>{k.label} <small>{k.key}</small></h3>
            <ul>
              {(parts[k.key] ?? []).map((p, i, list) => {
                const d = drafts[p.id] ?? toDraft(p)
                const dirty = drafts[p.id] !== undefined
                return (
                  <li key={p.id} className="bbs-row">
                    <span className="bbs-value" title="値(衣装が持つキー。変えられません)">{p.value}</span>
                    {editor(d, next => setDrafts({ ...drafts, [p.id]: next }), k.key)}
                    <div className="bbs-actions">
                      <button type="button" className="lora-btn lora-btn--secondary" disabled={busy || i === 0}
                        onClick={() => void move(k.key, i, -1)} aria-label="上へ">↑</button>
                      <button type="button" className="lora-btn lora-btn--secondary" disabled={busy || i === list.length - 1}
                        onClick={() => void move(k.key, i, 1)} aria-label="下へ">↓</button>
                      <button type="button" className="lora-btn" disabled={busy || !dirty || !d.label.trim()} onClick={() => void save(p)}>保存</button>
                      <button type="button" className="lora-btn lora-btn--danger" disabled={busy} onClick={() => void remove(p)}>削除</button>
                    </div>
                  </li>
                )
              })}
              <li className="bbs-row bbs-row--new">
                <span className="bbs-value">新しい部品</span>
                {editor(newDrafts[k.key] ?? NEW_DRAFT, next => setNewDrafts({ ...newDrafts, [k.key]: next }), k.key)}
                <div className="bbs-actions">
                  <button type="button" className="lora-btn" disabled={busy || !(newDrafts[k.key]?.label ?? '').trim()}
                    onClick={() => void add(k.key)}>足す</button>
                </div>
              </li>
            </ul>
          </section>
        ))}
      </main>
    </div>
  )
}
