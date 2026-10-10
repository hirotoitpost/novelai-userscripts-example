import { useCallback, useEffect, useRef, useState } from 'react'
import { enableNotifications, notifySupport, NotifySupport, showNotification } from '../notify'
import './TaskStatus.css'

export type TaskState = 'running' | 'done' | 'error' | 'cancelled'

export interface TaskStatus {
  state: TaskState
  title: string
  message: string
  progress: number
  total: number
  /** サーバーのバックグラウンドジョブ(画面を閉じても続く)かどうか */
  background: boolean
  /** 通知の tag。サーバーのジョブはプッシュ通知と同じ tag にして二重に出さない */
  notifyTag: string
  startedAt: number
  endedAt: number | null
}

/** これより早く成功した処理はダイアログを自動で閉じ、通知もしない(位置の保存など) */
const QUICK_MS = 4000
const START_MESSAGE = '開始しています...'
/** これより長くかかった処理は、画面を見ていても終了を通知する */
const NOTIFY_MS = 15000

const STATE_LABELS: Record<TaskState, string> = {
  running: '処理中',
  done: '完了',
  error: 'エラー',
  cancelled: 'キャンセル',
}

const STATE_ICONS: Record<Exclude<TaskState, 'running'>, string> = {
  done: '✅',
  error: '⚠️',
  cancelled: '⏹️',
}

export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) return `${h}時間${m}分`
  if (m > 0) return `${m}分${String(s).padStart(2, '0')}秒`
  return `${s}秒`
}

/**
 * 長い処理の状態(タイトル・進捗・結果)を持ち、終了時にブラウザ通知を出す。
 * 表示は TaskStatusDialog に渡す。
 */
export function useTaskStatus() {
  const [status, setStatus] = useState<TaskStatus | null>(null)
  const [minimized, setMinimized] = useState(false)
  const statusRef = useRef<TaskStatus | null>(null)
  const autoCloseRef = useRef<number | null>(null)

  const apply = useCallback((next: TaskStatus | null) => {
    statusRef.current = next
    setStatus(next)
  }, [])

  const clearAutoClose = () => {
    if (autoCloseRef.current !== null) window.clearTimeout(autoCloseRef.current)
    autoCloseRef.current = null
  }

  const start = useCallback((title: string, message = START_MESSAGE) => {
    clearAutoClose()
    setMinimized(false)
    apply({
      state: 'running', title, message, progress: 0, total: 0,
      background: false, notifyTag: 'nai-task', startedAt: Date.now(), endedAt: null,
    })
  }, [apply])

  const update = useCallback(
    /** jobTag を渡すと、サーバーのバックグラウンドジョブとして扱う */
    (message: string, progress?: number, total?: number, jobTag?: string) => {
      const current = statusRef.current
      if (!current || current.state !== 'running') return
      apply({
        ...current,
        message,
        progress: progress ?? current.progress,
        total: total ?? current.total,
        background: current.background || jobTag !== undefined,
        notifyTag: jobTag ?? current.notifyTag,
      })
    },
    [apply],
  )

  const finish = useCallback((state: Exclude<TaskState, 'running'>, message?: string) => {
    const current = statusRef.current
    if (!current) return
    const endedAt = Date.now()
    const elapsed = endedAt - current.startedAt
    // 途中経過を出さない処理は「開始しています...」のままなので、完了の言葉に置き換える
    const text = message ?? (current.message === START_MESSAGE ? `${STATE_LABELS[state]}しました` : current.message)
    apply({ ...current, state, message: text, endedAt })

    if (state === 'done' && elapsed < QUICK_MS) {
      autoCloseRef.current = window.setTimeout(() => apply(null), 1200)
      return
    }
    // 結果は閉じるまで出したままにする(スマホから戻ってきたときに見えるように)
    setMinimized(false)
    if (elapsed >= NOTIFY_MS || document.hidden) {
      const head = state === 'done' ? '✅ 完了' : state === 'error' ? '⚠️ 失敗' : '⏹️ キャンセル'
      void showNotification(`${head}: ${current.title}`, `${text}\n(${formatDuration(elapsed)})`, current.notifyTag)
    }
  }, [apply])

  const dismiss = useCallback(() => {
    clearAutoClose()
    apply(null)
  }, [apply])

  useEffect(() => clearAutoClose, [])

  // 処理中は画面を消さない。スマホは画面が消えるとページが止まり、通信も切れる
  // (OCR で72枚を読ませたとき、17枚目で止まって結果ごと失われた)。
  const running = status?.state === 'running'
  useEffect(() => {
    if (!running || !('wakeLock' in navigator)) return
    let lock: WakeLockSentinel | null = null
    let stopped = false
    const acquire = async () => {
      try {
        const next = await navigator.wakeLock.request('screen')
        if (stopped) void next.release()
        else lock = next
      } catch {
        // 電池残量が少ない・非対応などで断られたら、そのまま続ける
      }
    }
    // タブを切り替えると自動で解除されるので、戻ってきたら取り直す
    const onVisible = () => {
      if (document.visibilityState === 'visible' && (!lock || lock.released)) void acquire()
    }
    void acquire()
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      stopped = true
      document.removeEventListener('visibilitychange', onVisible)
      void lock?.release()
    }
  }, [running])

  // タブのタイトルに進捗を出す(スマホのタブ一覧や、別タブで作業中でも見える)
  useEffect(() => {
    const base = document.title.replace(/^(⏳|✅|⚠️|⏹️)[^|]*\| /, '')
    if (!status) {
      document.title = base
      return
    }
    const prefix = status.state === 'running'
      ? `⏳ ${status.total > 0 ? `${status.progress}/${status.total} ` : ''}${status.title}`
      : `${STATE_ICONS[status.state]} ${STATE_LABELS[status.state]}`
    document.title = `${prefix} | ${base}`
  }, [status])
  useEffect(() => () => {
    document.title = document.title.replace(/^(⏳|✅|⚠️|⏹️)[^|]*\| /, '')
  }, [])

  return {
    status,
    busy: status?.state === 'running',
    minimized,
    setMinimized,
    start,
    update,
    finish,
    dismiss,
  }
}

interface DialogProps {
  status: TaskStatus | null
  minimized: boolean
  onMinimize: (minimized: boolean) => void
  onCancel?: () => void
  onClose: () => void
}

export function TaskStatusDialog({ status, minimized, onMinimize, onCancel, onClose }: DialogProps) {
  const [now, setNow] = useState(() => Date.now())
  const [permission, setPermission] = useState<NotifySupport>(() => notifySupport())
  const closeRef = useRef<HTMLButtonElement | null>(null)

  const running = status?.state === 'running'
  useEffect(() => {
    if (!running) return
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [running])

  // 終わったら「閉じる」にフォーカスを移し、Esc でも閉じられるようにする
  const finished = !!status && !running
  useEffect(() => {
    if (!finished) return
    closeRef.current?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [finished, onClose])

  if (!status) return null

  const elapsed = (status.endedAt ?? now) - status.startedAt
  const determinate = status.total > 0
  const percent = determinate ? Math.min(100, Math.round((status.progress / status.total) * 100)) : 0
  const remaining = running && determinate && status.progress > 0 && status.progress < status.total
    ? (elapsed / status.progress) * (status.total - status.progress)
    : null

  const bar = (
    <div
      className={`task-bar${determinate || !running ? '' : ' task-bar--indeterminate'}`}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={determinate ? status.total : undefined}
      aria-valuenow={determinate ? status.progress : undefined}
    >
      <div
        className={`task-bar-fill task-bar-fill--${status.state}`}
        style={determinate || !running ? { width: `${running ? percent : 100}%` } : undefined}
      />
    </div>
  )

  if (minimized && running) {
    return (
      <button type="button" className="task-pill" onClick={() => onMinimize(false)} aria-label="処理状況を開く">
        <span className="task-spinner task-spinner--small" aria-hidden />
        <span className="task-pill-text">
          <span className="task-pill-title">
            {status.title}
            {determinate && ` ${status.progress}/${status.total}`}
          </span>
          {bar}
        </span>
        <span className="task-pill-time">{formatDuration(elapsed)}</span>
      </button>
    )
  }

  return (
    <div className="task-overlay" onClick={finished ? onClose : undefined}>
      <div
        className={`task-dialog task-dialog--${status.state}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="task-dialog-title"
        onClick={e => e.stopPropagation()}
      >
        <div className="task-head">
          {running
            ? <span className="task-spinner" aria-hidden />
            : <span className="task-icon" aria-hidden>{STATE_ICONS[status.state as Exclude<TaskState, 'running'>]}</span>}
          <div className="task-head-text">
            <span className={`task-chip task-chip--${status.state}`}>{STATE_LABELS[status.state]}</span>
            <h2 id="task-dialog-title" className="task-title">{status.title}</h2>
          </div>
        </div>

        <p className="task-message" aria-live="polite">{status.message}</p>

        {bar}
        <div className="task-meta">
          <span>
            {determinate ? `${status.progress} / ${status.total}(${percent}%)` : running ? '進捗を確認中' : ''}
          </span>
          <span>
            {running ? '経過' : '所要時間'} {formatDuration(elapsed)}
            {remaining !== null && ` ・ 残り約${formatDuration(remaining)}`}
          </span>
        </div>

        {running && status.background && (
          <p className="task-hint">
            サーバーで処理しているので、画面を閉じたりスマホをスリープさせても止まりません。
            このページを開き直すと進捗の表示に戻ります。
          </p>
        )}

        {running && permission === 'default' && (
          <button
            type="button"
            className="task-link"
            onClick={async () => setPermission(await enableNotifications())}
          >
            🔔 終わったら通知する(画面を消していても届きます)
          </button>
        )}
        {running && permission === 'denied' && (
          <p className="task-hint">ブラウザの通知はブロックされています(サイトの設定から許可できます)。</p>
        )}

        <div className="task-actions">
          {running ? (
            <>
              <button type="button" className="task-btn task-btn--ghost" onClick={() => onMinimize(true)}>
                最小化
              </button>
              {onCancel && (
                <button type="button" className="task-btn task-btn--danger" onClick={onCancel}>
                  キャンセル
                </button>
              )}
            </>
          ) : (
            <button ref={closeRef} type="button" className="task-btn" onClick={onClose}>
              閉じる
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
