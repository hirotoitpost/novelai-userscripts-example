import { useCallback, useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { apiFetch } from '../api'
import { useCurrentAssistantContext } from '../assistantContext'
import { useAuth } from '../context/AuthContext'
import { useLocalStorage } from '../hooks/useLocalStorage'
import AssistantChat from './AssistantChat'
import HelpChat from './HelpChat'
import HelpLink from './HelpLink'
import './AssistantPanel.css'

/**
 * 作業アシスタント(どのページでも右端から開く。広い画面では横に並べ、狭い画面では下から出す)。
 * 「次の操作」: 今のページと作業の状態から、やることと次の一手を出す(/api/assistant/guide。読むだけ)。
 * 「チャット」: 今のページの状況とヘルプの記事を渡して、文章モデルと話す(AssistantChat。読むだけ)。
 * 「ヘルプ」: ヘルプのチャット(今のページに合う記事を優先)。
 * 押すとその場所へ移るだけで、操作(生成・設定の変更)はしない。押すのは利用者。
 */

interface Step {
  key: string
  label: string
  status: 'done' | 'todo' | 'warn' | 'info'
  detail: string
  action: string
  action_label: string
  help: string
}

interface Guide {
  title: string
  summary: string
  steps: Step[]
  help: string
  next: string | null
}

type Tab = 'guide' | 'chat' | 'help'

const POLL_MS = 20000
const DOCK_QUERY = '(min-width: 1100px)'
const ICON: Record<Step['status'], string> = { done: '✓', todo: '▶', warn: '!', info: 'i' }
const STATUS_LABEL: Record<Step['status'], string> = { done: '済み', todo: 'これから', warn: '注意', info: '参考' }

/** アドレスからページの名前(案内を選ぶ) */
function pageOf(pathname: string): string {
  const first = pathname.split('/').filter(Boolean)[0] ?? ''
  return first === '' ? 'home' : first
}

/** その欄までスクロールする(移った直後は、欄が出るまで少し待つ) */
function scrollToAnchor(anchor: string) {
  if (!anchor) return
  let tries = 0
  const tick = () => {
    const el = document.getElementById(anchor)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
      return
    }
    if (tries++ < 20) window.setTimeout(tick, 200)
  }
  tick()
}

function useDocked(): boolean {
  const [docked, setDocked] = useState(() => window.matchMedia(DOCK_QUERY).matches)
  useEffect(() => {
    const media = window.matchMedia(DOCK_QUERY)
    const onChange = () => setDocked(media.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])
  return docked
}

export default function AssistantPanel() {
  const { token, isAuthenticated } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const { context, version } = useCurrentAssistantContext()
  const [open, setOpen] = useLocalStorage('nai_assistant_open', false)
  const [tab, setTab] = useLocalStorage<Tab>('nai_assistant_tab', 'guide')
  const [guide, setGuide] = useState<Guide | null>(null)
  const [error, setError] = useState<string | null>(null)
  const docked = useDocked()
  const page = pageOf(location.pathname)
  const storyId = context.storyId ?? 0
  const visible = isAuthenticated && location.pathname !== '/login'

  // 広い画面では、ページを左に寄せて横に並べる
  useEffect(() => {
    const root = document.documentElement
    root.classList.toggle('assistant-docked', visible && open && docked)
    return () => root.classList.remove('assistant-docked')
  }, [visible, open, docked])

  const load = useCallback(async () => {
    if (!token) return
    try {
      setGuide(await apiFetch<Guide>(token, `/api/assistant/guide?page=${encodeURIComponent(page)}&story_id=${storyId}`))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [token, page, storyId])

  useEffect(() => {
    if (!visible || !open || tab !== 'guide') return
    void load()
    const id = window.setInterval(() => void load(), POLL_MS)
    return () => window.clearInterval(id)
  }, [visible, open, tab, load, version])

  if (!visible) return null

  const go = (link: string) => {
    const url = new URL(link, window.location.origin)
    const samePage = url.pathname === location.pathname
    const sameStory = !url.searchParams.get('story') || Number(url.searchParams.get('story')) === storyId
    if (samePage && sameStory) {
      scrollToAnchor(url.hash.slice(1))
    } else {
      navigate(`${url.pathname}${url.search}${url.hash}`)
      scrollToAnchor(url.hash.slice(1))
    }
    if (!docked) setOpen(false)
  }

  if (!open) {
    return (
      <button type="button" className="as-toggle" onClick={() => setOpen(true)} aria-label="作業アシスタントを開く">
        <span aria-hidden="true">🧭</span> アシスタント
      </button>
    )
  }

  return (
    <aside className={docked ? 'as-panel as-panel--docked' : 'as-panel as-panel--sheet'} aria-label="作業アシスタント">
      <div className="as-head">
        <strong>作業アシスタント</strong>
        <div className="as-tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === 'guide'} className={tab === 'guide' ? 'as-tab as-tab--on' : 'as-tab'}
            onClick={() => setTab('guide')}>次の操作</button>
          <button type="button" role="tab" aria-selected={tab === 'chat'} className={tab === 'chat' ? 'as-tab as-tab--on' : 'as-tab'}
            onClick={() => setTab('chat')}>チャット</button>
          <button type="button" role="tab" aria-selected={tab === 'help'} className={tab === 'help' ? 'as-tab as-tab--on' : 'as-tab'}
            onClick={() => setTab('help')}>ヘルプ</button>
        </div>
        <button type="button" className="as-close" onClick={() => setOpen(false)} aria-label="閉じる">×</button>
      </div>

      <div className="as-body">
        {tab === 'guide' && (
          <>
            {error && <p className="as-error">{error}</p>}
            {!guide && !error && <p className="as-muted">読み込んでいます…</p>}
            {guide && (
              <>
                <div className="as-title">
                  {guide.title}
                  {guide.help && <HelpLink slug={guide.help} />}
                </div>
                {guide.summary && <p className="as-summary">{guide.summary}</p>}
                <ol className="as-steps">
                  {guide.steps.map(step => (
                    <li key={step.key} className={`as-step as-step--${step.status}${guide.next === step.key ? ' as-step--next' : ''}`}>
                      <span className={`as-icon as-icon--${step.status}`} title={STATUS_LABEL[step.status]} aria-label={STATUS_LABEL[step.status]}>
                        {ICON[step.status]}
                      </span>
                      <div className="as-step-main">
                        <div className="as-step-label">
                          {step.label}
                          {guide.next === step.key && <span className="as-next">次はこれ</span>}
                        </div>
                        {step.detail && <p className="as-step-detail">{step.detail}</p>}
                        <div className="as-step-actions">
                          {step.action && step.action_label && (
                            <button type="button" className={guide.next === step.key ? 'as-go as-go--primary' : 'as-go'} onClick={() => go(step.action)}>
                              {step.action_label}
                            </button>
                          )}
                          {step.help && <HelpLink slug={step.help} label="手順" />}
                        </div>
                      </div>
                    </li>
                  ))}
                </ol>
                <button type="button" className="as-refresh" onClick={() => void load()}>読み直す</button>
              </>
            )}
          </>
        )}
        {tab === 'chat' && <AssistantChat page={page} storyId={storyId} onNavigate={go} />}
        {tab === 'help' && <HelpChat current={guide?.help || undefined} />}
      </div>
    </aside>
  )
}
