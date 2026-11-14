import { useCallback, useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import './NoticeBell.css'

/**
 * お知らせのベル(どのページでも右下に出す。/api/admin/notices。バックエンドの notices.py)。
 * 各機能の「作業が要る」こと(バックアップの写し先の空きが足りなくなりそう など)を知らせる。管理者だけ。
 * お知らせが無いときは出さない。「あとで」(1・7・30日)と「今後知らせない」(重さか題名が変わるまで)で隠せる。
 * 画面と同じ配信元へ送る(開発中は Vite の中継。スマホから :8000 へ直接の fetch は失敗することがある)。
 */

interface Notice {
  key: string
  level: 'info' | 'warning' | 'error'
  title: string
  body: string
  link: string
  link_label: string
  help: string
  feature: string
  fingerprint: string
  hidden?: 'snoozed' | 'dismissed'
  snoozed_until?: string
}

interface Listing {
  notices: Notice[]
  hidden: Notice[]
}

const POLL_MS = 10 * 60 * 1000
const LEVEL_LABEL: Record<Notice['level'], string> = { error: '要対応', warning: '注意', info: 'お知らせ' }

async function call(path: string, body?: unknown): Promise<Listing> {
  const res = await fetch(`/api/admin/notices${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return (await res.json()) as Listing
}

export default function NoticeBell() {
  const { isAuthenticated, isAdmin } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const [data, setData] = useState<Listing | null>(null)
  const [open, setOpen] = useState(false)
  const [showHidden, setShowHidden] = useState(false)
  const [busy, setBusy] = useState(false)
  const enabled = isAuthenticated && isAdmin

  const load = useCallback(async (refresh = false) => {
    try {
      setData(await call(refresh ? '?refresh=true' : ''))
    } catch {
      // 管理者でない・LAN の外・バックエンドが止まっている: ベルを出さない
      setData(null)
    }
  }, [])

  useEffect(() => {
    if (!enabled) {
      setData(null)
      return
    }
    void load()
    const id = window.setInterval(() => void load(), POLL_MS)
    const onVisible = () => { if (document.visibilityState === 'visible') void load() }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [enabled, load])

  const act = async (work: () => Promise<Listing>) => {
    setBusy(true)
    try {
      setData(await work())
    } catch {
      // 次の見回りで読み直す
    } finally {
      setBusy(false)
    }
  }

  if (!enabled || !data || location.pathname === '/login') return null
  const count = data.notices.length
  if (count === 0 && !open) return null
  const worst = data.notices[0]?.level ?? 'info'

  const go = (notice: Notice) => {
    setOpen(false)
    if (notice.link) navigate(notice.link)
  }

  return (
    <>
      <button type="button" className={`nb-bell nb-bell--${worst}`} aria-label={`お知らせ ${count} 件`}
        aria-expanded={open} onClick={() => setOpen(v => !v)}>
        <span aria-hidden="true">🔔</span>
        {count > 0 && <span className="nb-count">{count}</span>}
      </button>
      {open && (
        <div className="nb-panel" role="dialog" aria-label="お知らせ">
          <div className="nb-head">
            <strong>お知らせ</strong>
            <button type="button" className="nb-text" disabled={busy} onClick={() => void act(() => call('?refresh=true'))}>
              点検し直す
            </button>
            <button type="button" className="nb-close" aria-label="閉じる" onClick={() => setOpen(false)}>×</button>
          </div>
          {count === 0 && <p className="nb-empty">いま対応が要るものはありません。</p>}
          <ul className="nb-list">
            {data.notices.map(notice => (
              <li key={notice.key} className={`nb-item nb-item--${notice.level}`}>
                <div className="nb-meta">
                  <span className={`nb-level nb-level--${notice.level}`}>{LEVEL_LABEL[notice.level]}</span>
                  {notice.feature && <span className="nb-feature">{notice.feature}</span>}
                </div>
                <div className="nb-title">{notice.title}</div>
                <p className="nb-body">{notice.body}</p>
                <div className="nb-actions">
                  {notice.link && (
                    <button type="button" className="nb-primary" onClick={() => go(notice)}>{notice.link_label || '開く'}</button>
                  )}
                  <select aria-label="あとで知らせる" value="" disabled={busy}
                    onChange={e => {
                      const days = Number(e.target.value)
                      if (days) void act(() => call('/snooze', { key: notice.key, days }))
                    }}>
                    <option value="">あとで…</option>
                    <option value="1">明日また知らせる</option>
                    <option value="7">7日後に知らせる</option>
                    <option value="30">30日後に知らせる</option>
                  </select>
                  <button type="button" className="nb-text" disabled={busy}
                    title="重さか内容が変わるまで知らせません"
                    onClick={() => void act(() => call('/dismiss', { key: notice.key, fingerprint: notice.fingerprint }))}>
                    今後知らせない
                  </button>
                </div>
              </li>
            ))}
          </ul>
          {data.hidden.length > 0 && (
            <div className="nb-hidden">
              <button type="button" className="nb-text" onClick={() => setShowHidden(v => !v)}>
                隠しているお知らせ({data.hidden.length}){showHidden ? ' ▲' : ' ▼'}
              </button>
              {showHidden && (
                <>
                  <ul className="nb-list nb-list--hidden">
                    {data.hidden.map(notice => (
                      <li key={notice.key} className="nb-item">
                        <div className="nb-title">{notice.title}</div>
                        <div className="nb-feature">
                          {notice.hidden === 'snoozed' && notice.snoozed_until
                            ? `${new Date(notice.snoozed_until).toLocaleString('ja-JP')} まで あとで`
                            : '今後知らせない'}
                        </div>
                      </li>
                    ))}
                  </ul>
                  <button type="button" className="nb-text" disabled={busy} onClick={() => void act(() => call('/restore', {}))}>
                    全部また知らせる
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      )}
    </>
  )
}
