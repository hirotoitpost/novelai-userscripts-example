import { useRef, useState } from 'react'
import { guardFetch } from '../guardFetch'
import type { Character } from './StoryCharacters'
import './CharacterTransfer.css'

/**
 * キャラシートの書き出し・取り込み(ZIP。/api/story/characters/export・import)。キャラ別データセットのページと、
 * 物語のページの登場人物の欄に置く。取り込むときは、キャラごとに 新しく / 別名で / 上書き / 入れない を選ぶ。
 * 未成年の定義を外す上書きは、コンテンツガードのパスワードを聞く(guardFetch)。
 */

type Action = 'new' | 'rename' | 'overwrite' | 'skip'

interface PlanItem {
  index: number
  name: string
  exists: boolean
  existing_minor: boolean
  is_minor: boolean
  is_adult: boolean
  minor_tags: string[]
  appearance_tags: string
  reference: boolean
  action: Action
  new_name: string
}

interface Props {
  apiOrigin: string
  characters: Character[]
  /** 取り込んだあと(キャラの一覧を読み直す) */
  onImported: () => void
}

async function detail(res: Response): Promise<string> {
  const body = await res.json().catch(() => null)
  const value = body?.detail
  return typeof value === 'string' ? value : value?.message ?? `HTTP ${res.status}`
}

export default function CharacterTransfer({ apiOrigin, characters, onImported }: Props) {
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [file, setFile] = useState<File | null>(null)
  const [plan, setPlan] = useState<PlanItem[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const toggle = (id: number, on: boolean) => setPicked(prev => {
    const next = new Set(prev)
    if (on) next.add(id)
    else next.delete(id)
    return next
  })

  async function exportPicked() {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch(`${apiOrigin}/api/story/characters/export`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids: [...picked] }),
      })
      if (!res.ok) throw new Error(await detail(res))
      const disposition = res.headers.get('Content-Disposition') ?? ''
      const name = decodeURIComponent(disposition.match(/filename\*=UTF-8''([^;]+)/)?.[1] ?? 'characters.zip')
      const link = document.createElement('a')
      link.href = URL.createObjectURL(await res.blob())
      link.download = name
      link.click()
      URL.revokeObjectURL(link.href)
      setNotice(`${picked.size}人を書き出しました。`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function preview(chosen: File) {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const form = new FormData()
      form.append('file', chosen)
      const res = await fetch(`${apiOrigin}/api/story/characters/import/preview`, { method: 'POST', body: form })
      if (!res.ok) throw new Error(await detail(res))
      setFile(chosen)
      setPlan((await res.json()).characters)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const update = (index: number, change: Partial<PlanItem>) =>
    setPlan(prev => prev?.map(item => (item.index === index ? { ...item, ...change } : item)) ?? null)

  async function apply() {
    if (!file || !plan) return
    setBusy(true)
    setError(null)
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('choices', JSON.stringify(plan.map(item => ({ index: item.index, action: item.action, name: item.new_name }))))
      const res = await guardFetch(`${apiOrigin}/api/story/characters/import`, { method: 'POST', body: form })
      if (!res.ok) throw new Error(await detail(res))
      const done: { imported: { name: string }[] } = await res.json()
      setNotice(`${done.imported.length}人を取り込みました: ${done.imported.map(c => c.name).join('、')}`)
      setPlan(null)
      setFile(null)
      onImported()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <details className="char-transfer">
      <summary>キャラシートの書き出し・取り込み</summary>
      <p className="char-transfer-hint">
        キャラシート(容姿・服装・画風・ネガティブ・トリガーワード・基準シード・メモ・年齢区分)と参照画像を ZIP にします。
        別の PC に移したり、人に渡したりできます。
      </p>
      <div className="char-transfer-list" role="group" aria-label="書き出すキャラ">
        {characters.map(c => (
          <label key={c.id}>
            <input type="checkbox" checked={picked.has(c.id)} onChange={e => toggle(c.id, e.target.checked)} /> {c.name}
          </label>
        ))}
      </div>
      <div className="char-transfer-row">
        <button type="button" onClick={() => setPicked(new Set(characters.map(c => c.id)))} disabled={busy}>全員を選ぶ</button>
        <button type="button" className="char-transfer-primary" disabled={busy || picked.size === 0} onClick={() => void exportPicked()}>
          選んだ{picked.size}人を書き出す
        </button>
        <button type="button" disabled={busy} onClick={() => fileRef.current?.click()}>ZIP を取り込む</button>
        <input ref={fileRef} type="file" accept=".zip,application/zip" hidden aria-label="取り込む ZIP"
          onChange={e => { const chosen = e.target.files?.[0]; e.target.value = ''; if (chosen) void preview(chosen) }} />
      </div>

      {plan && (
        <div className="char-transfer-plan">
          <p className="char-transfer-hint">{file?.name} の {plan.length}人。キャラごとに扱いを選んでください。</p>
          <ul>
            {plan.map(item => (
              <li key={item.index}>
                <div>
                  <strong>{item.name}</strong>
                  {item.is_minor && <span className="char-transfer-tag">未成年</span>}
                  {item.is_adult && !item.is_minor && <span className="char-transfer-tag">成人</span>}
                  {item.exists && <span className="char-transfer-tag char-transfer-tag--warn">同じ名前あり{item.existing_minor ? '(未成年)' : ''}</span>}
                  {item.reference && <span className="char-transfer-tag">参照画像</span>}
                </div>
                {item.minor_tags.length > 0 && item.is_adult && !item.is_minor && (
                  <div className="char-transfer-warn">未成年を示すタグ({item.minor_tags.join(', ')})があるので、成人としては入れられません</div>
                )}
                <div className="char-transfer-row">
                  <select value={item.action} aria-label={`${item.name}の扱い`} onChange={e => update(item.index, { action: e.target.value as Action })}>
                    {!item.exists && <option value="new">新しく入れる</option>}
                    <option value="rename">別名で入れる</option>
                    {item.exists && <option value="overwrite">上書きする</option>}
                    <option value="skip">入れない</option>
                  </select>
                  {item.action === 'rename' && (
                    <input value={item.new_name} aria-label={`${item.name}の新しい名前`} maxLength={100}
                      onChange={e => update(item.index, { new_name: e.target.value })} />
                  )}
                </div>
              </li>
            ))}
          </ul>
          <div className="char-transfer-row">
            <button type="button" className="char-transfer-primary" disabled={busy || plan.every(i => i.action === 'skip')} onClick={() => void apply()}>
              取り込む
            </button>
            <button type="button" disabled={busy} onClick={() => { setPlan(null); setFile(null) }}>やめる</button>
          </div>
        </div>
      )}
      {notice && <p className="char-transfer-hint" role="status">{notice}</p>}
      {error && <p className="char-transfer-error" role="alert">{error}</p>}
    </details>
  )
}
