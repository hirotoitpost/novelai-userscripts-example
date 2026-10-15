import { useCallback, useEffect, useState } from 'react'

/**
 * 画像・作品データのバックアップ(管理ページの「操作とデータ」。/api/admin/data-backup)。
 * 写し先のフォルダ(外付けドライブなど)を指定すると、DB と画像を写す。2回目からは増えた・変わったファイルだけ。
 * 1日1回の自動バックアップと、7回分を残すのはバックエンドが行う。戻すときは、先に今の状態を1回バックアップする。
 */

interface Snapshot {
  name: string
  created_at: string
  files: number
  bytes: number
  added: number
  added_bytes: number
  include_datasets: boolean
  reason: string
}

interface State {
  settings: { destination: string; include_datasets: boolean; auto: boolean }
  keep: number
  snapshots: Snapshot[]
  destination_error: string | null
  job: { kind: string; status: 'idle' | 'running' | 'done' | 'error'; message: string; done: number; total: number; result: Record<string, unknown> | null; finished_at: string | null }
}

const BACKEND = `${window.location.protocol}//${window.location.hostname}:8000`

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BACKEND}/api/admin${path}`, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
  })
  const data = await res.json().catch(() => ({ detail: res.statusText }))
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${res.status}`)
  return data as T
}

function bytes(n: number): string {
  if (n === 0) return '0 KB'
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(2)} GB`
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${Math.max(1, Math.round(n / 1024))} KB`
}

export default function DataBackup() {
  const [state, setState] = useState<State | null>(null)
  const [destination, setDestination] = useState('')
  const [includeDatasets, setIncludeDatasets] = useState(false)
  const [auto, setAuto] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  const apply = useCallback((next: State) => {
    setState(next)
    setDestination(next.settings.destination)
    setIncludeDatasets(next.settings.include_datasets)
    setAuto(next.settings.auto)
  }, [])

  const load = useCallback(() => call<State>('/data-backup').then(setState).catch(e => setError(String(e))), [])
  useEffect(() => {
    call<State>('/data-backup').then(apply).catch(e => setError(e instanceof Error ? e.message : String(e)))
  }, [apply])
  // 動いているあいだは進み具合を見る
  const running = state?.job.status === 'running'
  useEffect(() => {
    if (!running) return
    const id = window.setInterval(() => void load(), 2000)
    return () => window.clearInterval(id)
  }, [running, load])

  const run = async (work: () => Promise<State>, done?: string) => {
    setError(null)
    setMessage(null)
    try {
      setState(await work())
      if (done) setMessage(done)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const save = () => run(async () => {
    const next = await call<State>('/data-backup/settings', {
      method: 'PUT', body: JSON.stringify({ destination, include_datasets: includeDatasets, auto }),
    })
    apply(next)
    return next
  }, '設定を保存しました。')

  const restore = (snap: Snapshot) => {
    if (!window.confirm(
      `${new Date(snap.created_at).toLocaleString('ja-JP')} のバックアップに戻します。\n` +
      '先に今の状態を1回バックアップしてから、DB と、その時にあったファイルを書き戻します(その後にできたファイルは残ります)。\n' +
      '戻しているあいだは、ほかの操作をしないでください。よろしいですか?',
    )) return
    void run(() => call<State>(`/data-backup/restore/${snap.name}`, { method: 'POST' }))
  }

  const dirty = !!state && (
    destination !== state.settings.destination || includeDatasets !== state.settings.include_datasets || auto !== state.settings.auto
  )
  const job = state?.job
  return (
    <section className="adm-section">
      <h2>画像・作品データのバックアップ</h2>
      <p className="adm-hint">
        DB(物語・キャラ・設定・履歴)と、生成画像・漫画のコマとページ・取り込んだ作品・素材・出版物・ごみ箱を、指定したフォルダ
        (外付けドライブなど)に写します。2回目からは、増えた・変わったファイルだけを写します。モデル・フォント・評価データ
        (取り直せるもの)と <code>.env</code>(トークン)は入れません。{state?.keep ?? 7} 回分を残し、古いものは自動で消します。
      </p>
      {error && <p className="adm-error" role="alert">{error}</p>}
      {message && <p className="adm-hint" role="status">{message}</p>}

      <div className="adm-row">
        <label style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem', flex: '1 1 100%' }}>
          写し先のフォルダ
          <input value={destination} placeholder="例: E:\backup" aria-label="写し先のフォルダ" spellCheck={false}
            onChange={e => setDestination(e.target.value)} />
        </label>
      </div>
      <div className="adm-row">
        <label><input type="checkbox" checked={includeDatasets} onChange={e => setIncludeDatasets(e.target.checked)} /> キャラ別データセットも入れる</label>
        <label><input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} /> 1日1回、自動で取る(バックエンドが動いているあいだ)</label>
        <button type="button" className="adm-primary" disabled={!dirty} onClick={() => void save()}>設定を保存</button>
      </div>
      {state?.destination_error && <p className="adm-error">{state.destination_error}</p>}

      <div className="adm-row">
        <button type="button" className="adm-primary" disabled={!state?.settings.destination || running || dirty}
          title={dirty ? '先に設定を保存してください' : undefined}
          onClick={() => void run(() => call<State>('/data-backup/run', { method: 'POST' }))}>
          今すぐバックアップ
        </button>
        {job && job.status !== 'idle' && (
          <span className="adm-hint" role="status">
            {job.kind === 'restore' ? '戻す' : 'バックアップ'}: {job.message}
            {job.status === 'running' && job.total > 0 && `(${job.done}/${job.total})`}
          </span>
        )}
      </div>
      {job?.status === 'running' && job.total > 0 && <progress max={job.total} value={job.done} style={{ width: '100%' }} />}

      {state && state.snapshots.length === 0 && state.settings.destination && <p className="adm-hint">まだありません。</p>}
      {state && state.snapshots.length > 0 && (
        <table className="adm-table adm-table--rows">
          <tbody>
            {state.snapshots.map(snap => (
              <tr key={snap.name}>
                <th>
                  {new Date(snap.created_at).toLocaleString('ja-JP')}
                  <div className="adm-hint">{snap.reason}{snap.include_datasets ? ' ・ データセット込み' : ''}</div>
                </th>
                <td className="adm-num">
                  {snap.files.toLocaleString()} ファイル ・ {bytes(snap.bytes)}
                  <div className="adm-hint">この回に写した分: {snap.added.toLocaleString()} ファイル ・ {bytes(snap.added_bytes)}</div>
                </td>
                <td className="adm-actions">
                  <button type="button" disabled={running} onClick={() => restore(snap)}>この時点に戻す</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
