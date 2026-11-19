import { useEffect, useState } from 'react'
import { API_ORIGIN } from '../library'

/** /api/title-styles の1件(作品風テンプレート)。 */
export interface TitleStyle {
  key: string
  label: string
  kind: 'game' | 'anime'
  title_tag: string
  look: string
  outfit: string
  negative: string
  notes: string
  framing: string
  style_tags: string
  prompt_tags: string
}

/** タグの列の末尾に足す(重複は足さない)。 */
export function appendTags(current: string, add: string): string {
  const have = new Set(current.split(',').map(t => t.trim().toLowerCase()).filter(Boolean))
  const extra = add.split(',').map(t => t.trim()).filter(t => t && !have.has(t.toLowerCase()))
  if (extra.length === 0) return current
  const base = current.replace(/[,\s]+$/, '')
  return base ? `${base}, ${extra.join(', ')}` : extra.join(', ')
}

interface Props {
  /** 選んだテンプレートのプロンプト用タグとネガティブを渡す */
  onApply: (style: TitleStyle) => void
}

/** 作品風テンプレート(ブルアカ風・NIKKE風など)を選んでプロンプトに足す。 */
export default function TitleStylePicker({ onApply }: Props) {
  const [styles, setStyles] = useState<TitleStyle[]>([])
  useEffect(() => {
    fetch(`${API_ORIGIN}/api/title-styles`)
      .then(r => (r.ok ? (r.json() as Promise<TitleStyle[]>) : []))
      .then(setStyles)
      .catch(() => setStyles([]))
  }, [])
  if (styles.length === 0) return null
  const groups: [string, TitleStyle[]][] = [
    ['ゲーム(立ち絵)', styles.filter(s => s.kind === 'game')],
    ['アニメ(放送画面)', styles.filter(s => s.kind === 'anime')],
  ]
  return (
    <select className="ig-title-style" value="" aria-label="作品風を足す"
      title="作品タグ・立ち絵の雰囲気・衣装の例をプロンプトに、混ざりやすい物をネガティブに足します(Anlas は増えません)。作品のキャラの特徴が混ざることがあるので、髪色・髪型・目のタグは強めに"
      onChange={e => {
        const style = styles.find(s => s.key === e.target.value)
        if (style) onApply(style)
      }}>
      <option value="">+ 作品風を足す</option>
      {groups.map(([label, list]) => (
        <optgroup key={label} label={label}>
          {list.map(s => <option key={s.key} value={s.key} title={s.notes}>{s.label}</option>)}
        </optgroup>
      ))}
    </select>
  )
}
