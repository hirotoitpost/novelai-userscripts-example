import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
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

/** サーバーのジョブの種類 → 表示名(src/python/notify.py の JOB_KIND_LABELS と揃える) */
export const JOB_KIND_LABELS: Record<string, string> = {
  split: 'シーン分割・タグ付け',
  illustrate: '挿絵の生成',
  characters: '登場人物の抽出',
  sfx: '効果音の提案',
  narration: 'ナレーションの作成',
  panels: 'コマの絵の生成',
  sfx_fonts: '描き文字の選択',
  manga: '漫画にする(コマの生成と合成)',
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

// ---- 処理状況の置き場所(アプリ全体で1つ) ----
// ページごとの useTaskStatus が自分のページ(パス)の欄を使う。ページを離れても処理(サーバーの
// ジョブの進捗の読み取りなど)は続くので、状態をここに置き、どのページからでも TaskTray で見られるようにする。

interface TaskEntry {
  status: TaskStatus | null
  minimized: boolean
  /** トレイから戻るときに開くページ(処理を始めたページ) */
  returnTo: string
  /** 再読み込みの後にサーバーのジョブの一覧から出し直したもの。ページが引き継ぐまでトレイが進捗を読む */
  restored?: boolean
}

const entries = new Map<string, TaskEntry>()
let snapshot: ReadonlyMap<string, TaskEntry> = new Map()
const listeners = new Set<() => void>()
const autoCloseTimers = new Map<string, number>()

function emit() {
  snapshot = new Map(entries)
  listeners.forEach(listener => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

function setEntry(scope: string, patch: Partial<TaskEntry>) {
  const current = entries.get(scope) ?? { status: null, minimized: false, returnTo: scope }
  const next = { ...current, ...patch }
  if (next.status === null) entries.delete(scope)
  else entries.set(scope, next)
  emit()
}

function clearAutoClose(scope: string) {
  const id = autoCloseTimers.get(scope)
  if (id !== undefined) window.clearTimeout(id)
  autoCloseTimers.delete(scope)
}

/** 全ページの処理状況(ページのパス → 状態) */
export function useTaskEntries(): ReadonlyMap<string, TaskEntry> {
  return useSyncExternalStore(subscribe, () => snapshot)
}

/** サーバーのジョブの通知 tag(nai-job-{物語ID})から物語のIDを読む */
function jobStoryId(tag: string): number | null {
  const match = /^nai-job-(\d+)$/.exec(tag)
  return match ? Number(match[1]) : null
}

// ---- 再読み込みをまたいで処理状況を出し直すための記録(このブラウザの localStorage) ----

/** このブラウザで見ているサーバーのジョブ(物語ID → 戻り先など)。閉じたら消す */
const WATCH_KEY = 'nai_task_watch'
/** ブラウザの中で動いていて、再読み込みで止まった処理(次に開いたときに知らせる) */
const INTERRUPTED_KEY = 'nai_task_interrupted'

interface WatchedJob {
  returnTo: string
  title: string
  startedAt: number
}

interface InterruptedTask extends WatchedJob {
  message: string
}

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

function writeJson(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // 保存できなくても、今の画面の表示は続けられる
  }
}

function watchJob(storyId: number, job: WatchedJob) {
  const all = readJson<Record<string, WatchedJob>>(WATCH_KEY, {})
  const current = all[storyId]
  if (current && current.returnTo === job.returnTo && current.title === job.title) return
  all[storyId] = job
  writeJson(WATCH_KEY, all)
}

/** 閉じた処理はもう出し直さない */
function unwatch(status: TaskStatus | null | undefined) {
  const storyId = status ? jobStoryId(status.notifyTag) : null
  if (storyId === null) return
  const all = readJson<Record<string, WatchedJob>>(WATCH_KEY, {})
  delete all[storyId]
  writeJson(WATCH_KEY, all)
}

function pathOf(url: string): string {
  return new URL(url, window.location.origin).pathname
}

/**
 * 長い処理の状態(タイトル・進捗・結果)を持ち、終了時にブラウザ通知を出す。
 * 表示は TaskStatusDialog に渡す。状態はページ(パス)ごとにアプリ全体で持つので、
 * 別のページへ移っても処理と表示は続き(TaskTray)、戻ってくるとダイアログに戻る。
 */
export function useTaskStatus() {
  const scope = useLocation().pathname
  const entry = useTaskEntries().get(scope)
  const status = entry?.status ?? null

  const start = useCallback((title: string, message = START_MESSAGE) => {
    clearAutoClose(scope)
    const current = entries.get(scope)?.status
    // 開き直したときの引き継ぎで同じ処理を始め直すときは、経過時間をそのまま続ける
    const startedAt = current?.state === 'running' && current.title === title ? current.startedAt : Date.now()
    setEntry(scope, {
      minimized: false,
      restored: false,
      returnTo: window.location.pathname + window.location.search,
      status: {
        state: 'running', title, message, progress: 0, total: 0,
        background: false, notifyTag: 'nai-task', startedAt, endedAt: null,
      },
    })
  }, [scope])

  const update = useCallback(
    /** jobTag を渡すと、サーバーのバックグラウンドジョブとして扱う */
    (message: string, progress?: number, total?: number, jobTag?: string) => {
      const current = entries.get(scope)
      if (!current?.status || current.status.state !== 'running') return
      // 物語のジョブなら、戻り先はその物語を開いた物語ページ(一覧から開いた物語は URL に出ないため)
      const storyId = jobTag ? jobStoryId(jobTag) : null
      const returnTo = storyId !== null && scope === '/story' ? `/story?story=${storyId}` : current.returnTo
      if (storyId !== null) {
        watchJob(storyId, { returnTo, title: current.status.title, startedAt: current.status.startedAt })
        // 開いた直後にサーバーの一覧から別のページの欄へ出し直したものがあれば、このページが引き継ぐ
        for (const [other, e] of entries) {
          if (other !== scope && e.restored && e.status?.notifyTag === jobTag) setEntry(other, { status: null })
        }
      }
      setEntry(scope, {
        returnTo,
        status: {
          ...current.status,
          message,
          progress: progress ?? current.status.progress,
          total: total ?? current.status.total,
          background: current.status.background || jobTag !== undefined,
          notifyTag: jobTag ?? current.status.notifyTag,
        },
      })
    },
    [scope],
  )

  const finish = useCallback((state: Exclude<TaskState, 'running'>, message?: string) => {
    const current = entries.get(scope)?.status
    if (!current) return
    const endedAt = Date.now()
    const elapsed = endedAt - current.startedAt
    // 途中経過を出さない処理は「開始しています...」のままなので、完了の言葉に置き換える
    const text = message ?? (current.message === START_MESSAGE ? `${STATE_LABELS[state]}しました` : current.message)
    setEntry(scope, { status: { ...current, state, message: text, endedAt } })

    if (state === 'done' && elapsed < QUICK_MS) {
      autoCloseTimers.set(scope, window.setTimeout(() => setEntry(scope, { status: null }), 1200))
      return
    }
    // 結果は閉じるまで出したままにする(スマホから戻ってきたときに見えるように)
    setEntry(scope, { minimized: false })
    if (elapsed >= NOTIFY_MS || document.hidden) {
      const head = state === 'done' ? '✅ 完了' : state === 'error' ? '⚠️ 失敗' : '⏹️ キャンセル'
      void showNotification(`${head}: ${current.title}`, `${text}\n(${formatDuration(elapsed)})`, current.notifyTag)
    }
  }, [scope])

  const dismiss = useCallback(() => {
    clearAutoClose(scope)
    unwatch(entries.get(scope)?.status)
    setEntry(scope, { status: null })
  }, [scope])

  const setMinimized = useCallback((minimized: boolean) => setEntry(scope, { minimized }), [scope])

  return {
    status,
    busy: status?.state === 'running',
    minimized: entry?.minimized ?? false,
    setMinimized,
    start,
    update,
    finish,
    dismiss,
  }
}

/**
 * 処理中は画面を消さない。スマホは画面が消えるとページが止まり、通信も切れる
 * (OCR で72枚を読ませたとき、17枚目で止まって結果ごと失われた)。
 */
function useWakeLock(running: boolean) {
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
}

const TITLE_PREFIX = /^(⏳|✅|⚠️|⏹️)[^|]*\| /

/** タブのタイトルに進捗を出す(スマホのタブ一覧や、別タブで作業中でも見える) */
function useTitleProgress(status: TaskStatus | null) {
  useEffect(() => {
    const base = document.title.replace(TITLE_PREFIX, '')
    if (!status) {
      document.title = base
      return
    }
    const prefix = status.state === 'running'
      ? `⏳ ${status.total > 0 ? `${status.progress}/${status.total} ` : ''}${status.title}`
      : `${STATE_ICONS[status.state]} ${STATE_LABELS[status.state]}`
    document.title = `${prefix} | ${base}`
  }, [status])
}

type ActiveEntry = TaskEntry & { status: TaskStatus }

interface ServerJob {
  story_id: number
  kind: string
  status: TaskState
  message: string
  progress: number
  total: number
  detail: string | null
  started_at: number | null
  ended_at: number | null
}

const RESTORE_POLL_MS = 3000

/**
 * 再読み込みの後(や別の端末で開いたとき)に、サーバーで動いているジョブの処理状況を出し直す。
 * 動いているジョブは全部、終わったジョブはこのブラウザで見ていて閉じていないものだけ。
 * ページが開いて自分で進捗を読み始める(start)までは、ここで進捗を読み続ける。
 * あわせて、前回ブラウザの中で動いていて再読み込みで止まった処理を「中断」として出す。
 */
function useRestoreServerJobs() {
  useEffect(() => {
    const interrupted = readJson<InterruptedTask[]>(INTERRUPTED_KEY, [])
    try {
      localStorage.removeItem(INTERRUPTED_KEY)
    } catch {
      // 消せなくても次に開いたときに同じ知らせが出るだけ
    }
    for (const task of interrupted) {
      const scope = pathOf(task.returnTo)
      if (entries.get(scope)?.status) continue
      setEntry(scope, {
        returnTo: task.returnTo,
        restored: true,
        minimized: true,
        status: {
          state: 'cancelled', title: task.title, message: task.message, progress: 0, total: 0,
          background: false, notifyTag: 'nai-task', startedAt: task.startedAt, endedAt: Date.now(),
        },
      })
    }

    let first = true
    let stopped = false
    const poll = async () => {
      const restoredRunning = [...entries.values()].some(e => e.restored && e.status?.state === 'running')
      if (!first && !restoredRunning) return
      first = false
      const res = await fetch('/api/story/jobs').catch(() => null)
      if (stopped || !res?.ok) return
      const jobs: ServerJob[] = await res.json()
      const watched = readJson<Record<string, WatchedJob>>(WATCH_KEY, {})
      const seen = new Set<string>()
      for (const job of jobs) {
        const tag = `nai-job-${job.story_id}`
        seen.add(tag)
        const existing = [...entries.entries()].find(([, e]) => e.status?.notifyTag === tag)
        // ページが自分で進捗を読んでいるものは触らない
        if (existing && !existing[1].restored) continue
        const watch = watched[job.story_id]
        if (job.status !== 'running' && !watch) continue
        const returnTo = existing?.[1].returnTo ?? watch?.returnTo ?? `/story?story=${job.story_id}`
        const scope = existing?.[0] ?? pathOf(returnTo)
        // そのページでは別の処理が出ている
        if (!existing && entries.get(scope)?.status) continue
        setEntry(scope, {
          returnTo,
          restored: true,
          minimized: existing?.[1].minimized ?? true,
          status: {
            state: job.status,
            title: watch?.title ?? JOB_KIND_LABELS[job.kind] ?? '処理',
            message: job.status === 'error' ? (job.detail ?? job.message) : job.message,
            progress: job.progress,
            total: job.total,
            background: true,
            notifyTag: tag,
            startedAt: job.started_at ? job.started_at * 1000 : Date.now(),
            endedAt: job.ended_at ? job.ended_at * 1000 : null,
          },
        })
      }
      // 一覧から消えた(バックエンドを再起動した)ジョブは、状況が分からないので止まった扱いにする
      for (const [scope, e] of entries) {
        if (e.restored && e.status?.state === 'running' && !seen.has(e.status.notifyTag)) {
          setEntry(scope, {
            status: { ...e.status, state: 'error', message: 'サーバーの処理状況が見つかりません(再起動した可能性があります)', endedAt: Date.now() },
          })
        }
      }
    }
    void poll()
    const id = window.setInterval(() => void poll(), RESTORE_POLL_MS)
    return () => {
      stopped = true
      window.clearInterval(id)
    }
  }, [])
}

/**
 * ブラウザの中で動いている処理(スクショの読み取りなど)は、再読み込みやタブを閉じると止まる。
 * その前に確認を出し、それでも離れたら、次に開いたときに「中断」と知らせるよう記録する。
 */
function useGuardBrowserTasks() {
  useEffect(() => {
    const inBrowser = () => [...entries.values()].filter(
      (entry): entry is ActiveEntry => entry.status?.state === 'running' && !entry.status.background,
    )
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (inBrowser().length === 0) return
      e.preventDefault()
      e.returnValue = ''
    }
    // 本当に離れたときだけ記録する(確認で「留まる」を選んだときは pagehide が来ない)
    const onPageHide = () => {
      const tasks = inBrowser()
      if (tasks.length === 0) return
      writeJson(INTERRUPTED_KEY, tasks.map(entry => ({
        returnTo: entry.returnTo,
        title: entry.status.title,
        startedAt: entry.status.startedAt,
        message: `ページを再読み込みしたか閉じたため、途中で止まりました(${entry.status.message})`,
      })))
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    window.addEventListener('pagehide', onPageHide)
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload)
      window.removeEventListener('pagehide', onPageHide)
    }
  }, [])
}

/**
 * どのページにいても、ほかのページで始めた処理の状況を画面の下に出す(押すとそのページへ戻り、
 * ダイアログを開く)。タブのタイトルの進捗と、処理中に画面を消さないのもここで行う。App に1つ置く。
 */
export function TaskTray() {
  const all = useTaskEntries()
  const here = useLocation().pathname
  const navigate = useNavigate()
  const [now, setNow] = useState(() => Date.now())

  const list = [...all.entries()].filter((item): item is [string, ActiveEntry] => item[1].status !== null)
  const running = list.some(([, e]) => e.status.state === 'running')
  // タイトルは処理中のものを優先し、無ければ終わったもの
  const primary = list.find(([, e]) => e.status.state === 'running') ?? list[0]
  useWakeLock(running)
  useTitleProgress(primary ? primary[1].status : null)
  useRestoreServerJobs()
  useGuardBrowserTasks()

  useEffect(() => {
    if (!running) return
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [running])

  // このページの処理は、ページ自身のダイアログ(または最小化の表示)が出す
  const others = list.filter(([scope]) => scope !== here)
  if (others.length === 0) return null

  return (
    <div className="task-tray" aria-label="ほかのページの処理">
      {others.map(([scope, { status, returnTo }]) => {
        const finished = status.state !== 'running'
        const elapsed = (status.endedAt ?? now) - status.startedAt
        const determinate = status.total > 0
        const percent = determinate ? Math.min(100, Math.round((status.progress / status.total) * 100)) : 0
        return (
          <div key={scope} className={`task-pill task-pill--tray task-pill--${status.state}`}>
            <button
              type="button"
              className="task-pill-open"
              onClick={() => {
                setEntry(scope, { minimized: false })
                navigate(returnTo)
              }}
              aria-label={`${status.title}の処理状況を開く`}
            >
              {finished
                ? <span className="task-pill-icon" aria-hidden>{STATE_ICONS[status.state as Exclude<TaskState, 'running'>]}</span>
                : <span className="task-spinner task-spinner--small" aria-hidden />}
              <span className="task-pill-text">
                <span className="task-pill-title">
                  {status.title}
                  {finished ? ` ・ ${STATE_LABELS[status.state]}` : determinate && ` ${status.progress}/${status.total}`}
                </span>
                <span className={`task-bar${determinate || finished ? '' : ' task-bar--indeterminate'}`}>
                  <span
                    className={`task-bar-fill task-bar-fill--${status.state}`}
                    style={determinate || finished ? { width: `${finished ? 100 : percent}%` } : undefined}
                  />
                </span>
              </span>
              <span className="task-pill-time">{finished ? '開く' : formatDuration(elapsed)}</span>
            </button>
            {finished && (
              <button
                type="button"
                className="task-pill-close"
                onClick={() => {
                  clearAutoClose(scope)
                  unwatch(status)
                  setEntry(scope, { status: null })
                }}
                aria-label="閉じる"
              >
                ×
              </button>
            )}
          </div>
        )
      })}
    </div>
  )
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
