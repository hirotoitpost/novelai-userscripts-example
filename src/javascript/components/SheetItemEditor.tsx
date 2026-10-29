import { useState } from 'react'
import type { FormEvent } from 'react'
import './SheetItemEditor.css'

/**
 * 顔・表情シート(face)・素体(body)の項目を足す・書き換えるダイアログ(全キャラ共通)。
 * コードの項目は書き換えると上書きになり、「既定に戻す」で元に戻る。足した項目は消せる(描いた絵のファイルは残る)。
 * 足した素体の部位は、裸の素体では描かない。
 */

export interface SheetItem {
  key: string
  label: string
  group: string
  tags: string
  negative: string
  size: 'square' | 'portrait'
  /** 素体だけ: 人体の構造として確かめる点 */
  check?: string
  /** コードの項目か(false なら足した項目) */
  builtin: boolean
  /** DB で上書き・足したものか */
  edited: boolean
}

interface Props {
  sheet: 'face' | 'body'
  groups: { id: string; label: string }[]
  /** 書き換える項目。null なら足す(group は足すまとまり) */
  item: SheetItem | null
  group?: string
  token: string | null
  onClose: () => void
  /** 保存・既定に戻す・消すのあと(一覧を読み直す) */
  onChanged: () => void
}

export default function SheetItemEditor({ sheet, groups, item, group, token, onClose, onChanged }: Props) {
  const [label, setLabel] = useState(item?.label ?? '')
  const [grp, setGrp] = useState(item?.group ?? group ?? groups[0]?.id ?? '')
  const [tags, setTags] = useState(item?.tags ?? '')
  const [negative, setNegative] = useState(item?.negative ?? '')
  const [size, setSize] = useState<'square' | 'portrait'>(item?.size ?? 'square')
  const [check, setCheck] = useState(item?.check ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function call(method: string, path: string, body?: unknown) {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch(`/api/lora-dataset/sheet-items/${sheet}${path}`, {
        method,
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => null)
        throw new Error(typeof data?.detail === 'string' ? data.detail : `保存できませんでした (HTTP ${res.status})`)
      }
      onChanged()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    const body = { label: label.trim(), group: grp, tags: tags.trim(), negative: negative.trim(), size, check: check.trim() }
    void call(item ? 'PUT' : 'POST', item ? `/${encodeURIComponent(item.key)}` : '', body)
  }

  function remove() {
    if (!item) return
    const message = item.builtin
      ? `「${item.label}」の名前・プロンプトを既定に戻します。よろしいですか?`
      : `「${item.label}」を消します(全キャラから消えます。描いた絵のファイルは残ります)。よろしいですか?`
    if (window.confirm(message)) void call('DELETE', `/${encodeURIComponent(item.key)}`)
  }

  const what = sheet === 'face' ? '顔・表情シートの項目' : '素体の部位'
  return (
    <div className="sie-backdrop" role="presentation" onClick={onClose}>
      <form className="sie" role="dialog" aria-label={item ? `${what}を編集` : `${what}を足す`}
        onClick={e => e.stopPropagation()} onSubmit={submit}>
        <h4>{item ? `${what}を編集: ${item.label}` : `${what}を足す`}</h4>
        <p className="lora-hint">
          全キャラ共通の{what}です。プロンプトには、キャラシートの見た目と{sheet === 'face' ? '無地の背景' : '素体の体・無地の背景'}に足すタグ(構図を含む)を書きます。
          {sheet === 'body' && !item?.builtin && ' 足した部位は、裸の素体では描きません。'}
        </p>
        <label className="sie-field">
          <span>名前</span>
          <input className="lora-input" value={label} maxLength={40} required onChange={e => setLabel(e.target.value)} />
        </label>
        <label className="sie-field">
          <span>まとまり</span>
          <select className="lora-select" value={grp} onChange={e => setGrp(e.target.value)}>
            {groups.map(g => <option key={g.id} value={g.id}>{g.label}</option>)}
          </select>
        </label>
        <label className="sie-field">
          <span>プロンプト(足すタグ)</span>
          <textarea className="lora-input" rows={3} value={tags} maxLength={1000} required onChange={e => setTags(e.target.value)}
            placeholder={sheet === 'face' ? 'portrait, close-up, looking at viewer, smile' : 'full body, standing, from side'} />
        </label>
        <label className="sie-field">
          <span>ネガティブ(この項目だけに足す)</span>
          <textarea className="lora-input" rows={2} value={negative} maxLength={1000} onChange={e => setNegative(e.target.value)} />
        </label>
        <label className="sie-field">
          <span>サイズ</span>
          <select className="lora-select" value={size} onChange={e => setSize(e.target.value as 'square' | 'portrait')}>
            <option value="square">正方形(1024×1024)</option>
            <option value="portrait">縦長(832×1216)</option>
          </select>
        </label>
        {sheet === 'body' && (
          <label className="sie-field">
            <span>確かめる点(画面に出すメモ)</span>
            <textarea className="lora-input" rows={2} value={check} maxLength={400} onChange={e => setCheck(e.target.value)} />
          </label>
        )}
        {error && <p className="lora-error" role="alert">{error}</p>}
        <div className="sie-actions">
          <button type="submit" className="lora-btn" disabled={busy || !label.trim() || !tags.trim()}>{item ? '保存' : '足す'}</button>
          {item && (item.edited || !item.builtin) && (
            <button type="button" className="lora-btn lora-btn--secondary" disabled={busy} onClick={remove}>
              {item.builtin ? '既定に戻す' : '消す'}
            </button>
          )}
          <button type="button" className="lora-btn lora-btn--secondary" disabled={busy} onClick={onClose}>やめる</button>
        </div>
      </form>
    </div>
  )
}
