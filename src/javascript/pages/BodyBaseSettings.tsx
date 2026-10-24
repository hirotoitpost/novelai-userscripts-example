import { useEffect, useState } from 'react'
import BackButton from '../components/BackButton'
import './LoraDataset.css'
import './BodyBaseSettings.css'

/**
 * 素体の設定のページ。キャラシートの「素体」タブのプルダウン(性別・肌の色・体格・身長・プロポーション・胸・筋肉の付き方)の
 * 選択肢を編集する: 画面の名前・プロンプトに入れるタグ・真後ろの部位で代わりに使うタグ・並べる順。足す・消すもここ。
 * 値(素体が持つキー)は足したときに決まり、あとから変えない。使っている素体がある選択肢は消せない。
 */

interface Option {
  id: number
  field: string
  value: string
  label: string
  tags: string
  rear_tags: string | null
}

interface Draft {
  label: string
  tags: string
  /** 真後ろの部位で別のタグを使うか(使わなければ rear_tags は null) */
  useRear: boolean
  rearTags: string
}

const NEW_DRAFT: Draft = { label: '', tags: '', useRear: false, rearTags: '' }

function toDraft(o: Option): Draft {
  return { label: o.label, tags: o.tags, useRear: o.rear_tags !== null, rearTags: o.rear_tags ?? '' }
}

async function detail(res: Response): Promise<string> {
  const data = await res.json().catch(() => null)
  return typeof data?.detail === 'string' ? data.detail : `HTTP ${res.status}`
}

export default function BodyBaseSettings() {
  const [fields, setFields] = useState<{ key: string; label: string }[]>([])
  const [options, setOptions] = useState<Record<string, Option[]>>({})
  // 書きかけの内容(選択肢の id → 下書き)と、項目ごとの新しい選択肢の下書き
  const [drafts, setDrafts] = useState<Record<number, Draft>>({})
  const [newDrafts, setNewDrafts] = useState<Record<string, Draft>>({})
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null)
  const [busy, setBusy] = useState(false)

  function load() {
    fetch('/api/lora-dataset/body-bases/options')
      .then(r => (r.ok ? r.json() : null))
      .then(data => {
        if (!data) return
        setFields(data.fields)
        setOptions(data.options)
        setDrafts({})
      })
      .catch(() => setMessage({ text: '選択肢を読み込めませんでした', error: true }))
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

  function body(field: string, d: Draft) {
    return JSON.stringify({ field, label: d.label, tags: d.tags, rear_tags: d.useRear ? d.rearTags : null })
  }

  async function save(o: Option) {
    const d = drafts[o.id]
    if (!d) return
    await run(
      () => fetch(`/api/lora-dataset/body-base-options/${o.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: body(o.field, d) }),
      `「${d.label}」を保存しました`,
    )
  }

  async function add(field: string) {
    const d = newDrafts[field] ?? NEW_DRAFT
    const ok = await run(
      () => fetch('/api/lora-dataset/body-base-options', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body(field, d) }),
      `「${d.label}」を足しました`,
    )
    if (ok) setNewDrafts({ ...newDrafts, [field]: NEW_DRAFT })
  }

  async function remove(o: Option) {
    if (!window.confirm(`選択肢「${o.label}」を消しますか?`)) return
    await run(() => fetch(`/api/lora-dataset/body-base-options/${o.id}`, { method: 'DELETE' }), `「${o.label}」を消しました`)
  }

  async function move(field: string, index: number, delta: number) {
    const list = [...(options[field] ?? [])]
    const target = index + delta
    if (target < 0 || target >= list.length) return
    ;[list[index], list[target]] = [list[target], list[index]]
    setOptions({ ...options, [field]: list })
    await run(
      () => fetch('/api/lora-dataset/body-base-options/order', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ field, ids: list.map(o => o.id) }),
      }),
      '並べ替えました',
    )
  }

  function editor(d: Draft, onChange: (d: Draft) => void, field: string) {
    return (
      <>
        <input className="lora-input" placeholder="画面の名前" value={d.label} disabled={busy}
          onChange={e => onChange({ ...d, label: e.target.value })} />
        <input className="lora-input" placeholder="タグ(空なら何も入れない)" value={d.tags} disabled={busy}
          onChange={e => onChange({ ...d, tags: e.target.value })} />
        <label className="bbs-rear" title="背面・後頭部・背部では、タグの代わりにこちらを使います(胸の large breasts は背面を正面向きに引き戻すため など)">
          <input type="checkbox" checked={d.useRear} disabled={busy} onChange={e => onChange({ ...d, useRear: e.target.checked })} />
          真後ろで別のタグ
        </label>
        {d.useRear && (
          <input className="lora-input" placeholder="真後ろの部位のタグ(空なら入れない)" value={d.rearTags} disabled={busy}
            onChange={e => onChange({ ...d, rearTags: e.target.value })} aria-label={`${field} 真後ろのタグ`} />
        )}
      </>
    )
  }

  return (
    <div className="lora-root">
      <header className="lora-header">
        <BackButton className="lora-back" />
        <span className="lora-header-title">素体の設定</span>
      </header>
      <main className="bbs-main">
        <p className="lora-hint">
          キャラシートの「素体」タブのプルダウンの選択肢を編集します。タグは素体スーツ(全年齢)にも入るので、
          全年齢で止めるタグ・性的なタグ・未成年を示すタグは入れられません。「真後ろで別のタグ」は、背面・後頭部・背部の絵で
          タグの代わりに使います。未成年の手がかりがあるキャラで使わない設定は、コンテンツガードの「控えめな素体スーツで使わない設定」で決めます。
        </p>
        {message && <p className={message.error ? 'lora-error' : 'lora-hint'} role={message.error ? 'alert' : undefined}>{message.text}</p>}
        {fields.map(f => (
          <section key={f.key} className="bbs-field">
            <h3>{f.label} <small>{f.key}</small></h3>
            <ul>
              {(options[f.key] ?? []).map((o, i, list) => {
                const d = drafts[o.id] ?? toDraft(o)
                const dirty = drafts[o.id] !== undefined
                return (
                  <li key={o.id} className="bbs-row">
                    <span className="bbs-value" title="値(素体が持つキー。変えられません)">{o.value || '(空)'}</span>
                    {editor(d, next => setDrafts({ ...drafts, [o.id]: next }), f.key)}
                    <div className="bbs-actions">
                      <button type="button" className="lora-btn lora-btn--secondary" disabled={busy || i === 0}
                        onClick={() => void move(f.key, i, -1)} aria-label="上へ">↑</button>
                      <button type="button" className="lora-btn lora-btn--secondary" disabled={busy || i === list.length - 1}
                        onClick={() => void move(f.key, i, 1)} aria-label="下へ">↓</button>
                      <button type="button" className="lora-btn" disabled={busy || !dirty || !d.label.trim()} onClick={() => void save(o)}>保存</button>
                      <button type="button" className="lora-btn lora-btn--danger" disabled={busy} onClick={() => void remove(o)}>削除</button>
                    </div>
                  </li>
                )
              })}
              <li className="bbs-row bbs-row--new">
                <span className="bbs-value">新しい選択肢</span>
                {editor(newDrafts[f.key] ?? NEW_DRAFT, next => setNewDrafts({ ...newDrafts, [f.key]: next }), f.key)}
                <div className="bbs-actions">
                  <button type="button" className="lora-btn" disabled={busy || !(newDrafts[f.key]?.label ?? '').trim()}
                    onClick={() => void add(f.key)}>足す</button>
                </div>
              </li>
            </ul>
          </section>
        ))}
      </main>
    </div>
  )
}
