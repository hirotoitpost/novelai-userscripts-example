import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import type { KeyboardEvent, TextareaHTMLAttributes } from 'react'
import './TagTextarea.css'

/** GET /api/image/suggest-tags の1件。label は日本語で引いたときの日本語のタグ名 */
interface TagSuggestion {
  tag: string
  label: string | null
  count: number
}

interface Props extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'value' | 'onChange'> {
  value: string
  /** 入力・補完で値が変わったとき。caret は変更後のカーソル位置 */
  onValueChange: (value: string, caret: number) => void
  token: string | null
  /** 補完の候補を引くモデル(モデルごとにタグの語彙が違う) */
  model: string
}

/** 補完を引く最短の文字数(英数字 / 日本語) */
const MIN_ASCII = 2
const MIN_OTHER = 1
const DEBOUNCE_MS = 220
/** 強調の記号・重み(1.2::tag、{tag}、-tag など)は、タグの語の外として扱う */
const PREFIX = /^[\s{[(]*(?:-?\d+(?:\.\d+)?::)?[\s{[(]*/

/** カーソルの位置で入力中のタグ(直前の区切りからカーソルまで)。start は語の始まりの位置。 */
export function currentFragment(value: string, caret: number): { start: number; text: string } | null {
  const before = value.slice(0, caret)
  const cut = Math.max(before.lastIndexOf(','), before.lastIndexOf('\n'), before.lastIndexOf('|')) + 1
  const raw = before.slice(cut)
  const prefix = raw.match(PREFIX)?.[0].length ?? 0
  const text = raw.slice(prefix)
  // カーソルの直後がタグの続きなら(語の途中)、補完しない
  const next = value.slice(caret, caret + 1)
  if (!text.trim() || (next && !/[\s,|\n}\])]/.test(next))) return null
  return { start: cut + prefix, text }
}

/** 入力中のタグを、選んだタグに置き換える。次のタグを続けて打てるように、後ろにカンマを足す。 */
export function applySuggestion(value: string, caret: number, start: number, tag: string): { value: string; caret: number } {
  const after = value.slice(caret)
  const tail = /^\s*[,|\n}\])]/.test(after) ? '' : ', '
  const next = value.slice(0, start) + tag + tail + after.replace(/^[ \t]+(?=\S)/, tail ? '' : ' ')
  return { value: next, caret: start + tag.length + tail.length }
}

/**
 * タグの補完つきのプロンプト欄。打っている途中のタグ(カンマ区切りの最後の語)から NovelAI のタグ候補を出す。
 * 日本語で打つと、日本語のタグ名から英語のタグを引ける。↑↓ で選び、Enter か Tab で入れる。Esc で閉じる。
 */
const TagTextarea = forwardRef<HTMLTextAreaElement, Props>(function TagTextarea(
  { value, onValueChange, token, model, onKeyDown, onBlur, onSelect, ...rest }, ref,
) {
  const inner = useRef<HTMLTextAreaElement>(null)
  useImperativeHandle(ref, () => inner.current as HTMLTextAreaElement)
  const [items, setItems] = useState<TagSuggestion[]>([])
  const [active, setActive] = useState(0)
  const [fragment, setFragment] = useState<{ start: number; text: string } | null>(null)
  const requestId = useRef(0)
  // IME で変換している間は引かない(確定してから引く)
  const composing = useRef(false)

  const close = useCallback(() => {
    requestId.current += 1
    setItems([])
    setFragment(null)
  }, [])

  const look = useCallback((text: string, caret: number) => {
    const found = composing.current ? null : currentFragment(text, caret)
    const query = found?.text.trim() ?? ''
    // eslint-disable-next-line no-control-regex
    const ascii = /^[\x00-\x7f]*$/.test(query)
    if (!found || !token || query.length < (ascii ? MIN_ASCII : MIN_OTHER) || query.length > 60) {
      close()
      return
    }
    setFragment(found)
    const id = ++requestId.current
    window.setTimeout(async () => {
      if (id !== requestId.current) return
      try {
        const res = await fetch(`/api/image/suggest-tags?q=${encodeURIComponent(query)}&model=${encodeURIComponent(model)}`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (!res.ok || id !== requestId.current) return
        const data = (await res.json()) as { items: TagSuggestion[] }
        // 打ったものと同じタグしか無ければ、出さない
        const list = data.items.filter(i => i.tag.toLowerCase() !== query.toLowerCase() || i.label)
        setItems(list.slice(0, 10))
        setActive(0)
      } catch {
        // 候補が引けなくても、入力はそのまま続けられる
      }
    }, DEBOUNCE_MS)
  }, [token, model, close])

  // ほかの操作(読み込み・チャンクの挿入など)で値が変わったら、古い候補は閉じる
  const typed = useRef(value)
  useEffect(() => {
    if (value !== typed.current) close()
  }, [value, close])

  const accept = (item: TagSuggestion) => {
    const ta = inner.current
    if (!ta || !fragment) return
    const result = applySuggestion(value, ta.selectionStart, fragment.start, item.tag)
    typed.current = result.value
    onValueChange(result.value, result.caret)
    close()
    requestAnimationFrame(() => {
      ta.focus()
      ta.setSelectionRange(result.caret, result.caret)
    })
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (items.length > 0 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        setActive(a => (a + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length)
        return
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault()
        accept(items[active])
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        close()
        return
      }
    }
    onKeyDown?.(e)
  }

  return (
    <div className="tag-ta">
      <textarea
        {...rest}
        ref={inner}
        value={value}
        onChange={e => {
          typed.current = e.target.value
          onValueChange(e.target.value, e.target.selectionStart)
          look(e.target.value, e.target.selectionStart)
        }}
        onCompositionStart={() => { composing.current = true }}
        onCompositionEnd={e => {
          composing.current = false
          look(e.currentTarget.value, e.currentTarget.selectionStart)
        }}
        onSelect={onSelect}
        onKeyDown={handleKeyDown}
        onBlur={e => {
          // 候補をクリックしたときに先に閉じてしまわないよう、少し待つ
          window.setTimeout(close, 150)
          onBlur?.(e)
        }}
        aria-autocomplete="list"
        aria-expanded={items.length > 0}
      />
      {items.length > 0 && (
        <ul className="tag-ta-list" role="listbox" aria-label="タグの候補">
          {items.map((item, i) => (
            <li
              key={`${item.tag}-${item.label ?? ''}`}
              role="option"
              aria-selected={i === active}
              className={i === active ? 'tag-ta-item tag-ta-item--on' : 'tag-ta-item'}
              // フォーカスを欄に残したまま選ぶ
              onMouseDown={e => { e.preventDefault(); accept(item) }}
              onMouseEnter={() => setActive(i)}
            >
              <span className="tag-ta-tag">{item.tag}</span>
              {item.label && <span className="tag-ta-label">{item.label}</span>}
              <span className="tag-ta-count">{item.count >= 10000 ? '1万+' : item.count.toLocaleString()}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
})

export default TagTextarea
