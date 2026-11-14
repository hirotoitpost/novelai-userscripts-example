import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  SHORTCUT_GROUPS, clickShortcut, focusSearch, fullscreenOpen, isShortcutKey, keyIs,
} from '../shortcuts'
import './ShortcutHelp.css'

/**
 * どのページでも効くキーボードショートカット(? で一覧、/ で検索欄、F で全画面)と、その一覧の画面。
 * 画像の前後(J / L)などは、それぞれの表示が受ける。
 */
export default function ShortcutHelp() {
  const [open, setOpen] = useState(false)
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!isShortcutKey(e)) return
      if (e.key === '?') {
        setOpen(v => !v)
      } else if (open && e.key === 'Escape') {
        setOpen(false)
      } else if (open) {
        return
      } else if (e.key === '/') {
        if (!focusSearch()) return
      } else if (keyIs(e, 'f') && !e.shiftKey) {
        // 全画面のときは、全画面の表示が F を受けて終える
        if (fullscreenOpen() || !clickShortcut('fullscreen')) return
      } else {
        return
      }
      e.preventDefault()
      // 拡大表示などの Esc ・矢印キーまで届かないように
      e.stopImmediatePropagation()
    }
    // 拡大表示などより先に受ける(一覧を開いているときの Esc で、後ろの拡大表示まで閉じないように)
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [open])

  useEffect(() => {
    if (open) closeRef.current?.focus()
  }, [open])

  if (!open) return null
  return (
    <div className="ksh-overlay" onClick={() => setOpen(false)}>
      <div className="ksh-dialog" role="dialog" aria-modal="true" aria-label="キーボードショートカット"
        onClick={e => e.stopPropagation()}>
        <div className="ksh-head">
          <h2>キーボードショートカット</h2>
          <button ref={closeRef} type="button" className="ksh-close" onClick={() => setOpen(false)} aria-label="閉じる">×</button>
        </div>
        {SHORTCUT_GROUPS.map(group => (
          <section key={group.title}>
            <h3>{group.title}</h3>
            <dl>
              {group.items.map(item => (
                <div key={item.label} className="ksh-row">
                  <dt>{item.keys.map((k, i) => <span key={k}>{i > 0 && ' / '}<kbd>{k}</kbd></span>)}</dt>
                  <dd>{item.label}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
        <p className="ksh-note">
          文字を入力しているときは効きません。<Link to="/help/shortcuts" onClick={() => setOpen(false)}>ヘルプ</Link>
        </p>
      </div>
    </div>
  )
}
