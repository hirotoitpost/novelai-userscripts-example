import { BACKEND_ORIGIN } from '../backendOrigin'
import { useCallback, useEffect, useState } from 'react'

/**
 * 画像・作品データのバックアップ(管理ページの「操作とデータ」。/api/admin/data-backup)。
 * 写し先(外付けドライブなど)をいくつでも登録できる。一覧と DB は全部の写し先に、画像などの中身は「置く数」か所に振り分ける。
 * 空きが閾値を下回った写し先には新しい中身を置かない。抜けている写し先は飛ばし、戻ったら次の回でそろえる。
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

interface DiskSetting {
  path: string
  id: string
  reserve_gb: number
  reserve_percent: number
  retiring: boolean
}

interface DiskState extends DiskSetting {
  available: boolean
  error: string | null
  total?: number
  free?: number
  reserve_bytes?: number
  accepting?: boolean
  objects?: number | null
  bytes?: number | null
  pending?: number | null
  checked_at?: string | null
  same_drive?: boolean
}

interface Settings {
  disks: DiskSetting[]
  copies: number
  include_datasets: boolean
  auto: boolean
}

interface State {
  settings: Settings
  keep: number
  disks: DiskState[]
  health: { checked_at: string; copies: number; short: number; missing: number } | null
  snapshots: Snapshot[]
  job: { kind: string; status: 'idle' | 'running' | 'done' | 'error'; message: string; done: number; total: number; result: Record<string, unknown> | null; finished_at: string | null }
}

const BACKEND = BACKEND_ORIGIN
const NEW_DISK: DiskSetting = { path: '', id: '', reserve_gb: 50, reserve_percent: 10, retiring: false }

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
  if (n >= 1024 ** 4) return `${(n / 1024 ** 4).toFixed(2)} TB`
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(n >= 100 * 1024 ** 3 ? 0 : 1)} GB`
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${Math.max(1, Math.round(n / 1024))} KB`
}

function DiskStatus({ disk }: { disk: DiskState | undefined }) {
  if (!disk) return <span className="adm-hint">保存すると確かめます</span>
  if (!disk.available) return <span className="adm-error">{disk.error ?? 'つながっていません'}</span>
  const used = (disk.total ?? 0) - (disk.free ?? 0)
  return (
    <>
      <div>
        空き {bytes(disk.free ?? 0)} / {bytes(disk.total ?? 0)}(残す空き {bytes(disk.reserve_bytes ?? 0)})
        {' ・ '}
        {disk.retiring ? <strong>外す準備中</strong> : disk.accepting ? '置く先として使っています' : <strong>空きが閾値より少ないので、新しい中身は置きません</strong>}
      </div>
      <progress max={disk.total || 1} value={used} style={{ width: '100%' }} />
      {disk.objects != null && (
        <div className="adm-hint">前の回のとき: 中身 {disk.objects.toLocaleString()} 個 ・ {bytes(disk.bytes ?? 0)}</div>
      )}
      {disk.retiring && disk.pending != null && (
        <div className={disk.pending ? 'adm-error' : 'adm-hint'}>
          {disk.pending
            ? `ここにしか無い中身が ${disk.pending.toLocaleString()} 個あります。バックアップを取ると、ほかの写し先へ写します。`
            : 'ほかの写し先にそろいました。この写し先は外せます。'}
        </div>
      )}
      {disk.same_drive && <div className="adm-error">ほかの写し先と同じドライブにあります。ドライブが壊れると両方なくなります。</div>}
    </>
  )
}

export default function DataBackup() {
  const [state, setState] = useState<State | null>(null)
  const [disks, setDisks] = useState<DiskSetting[]>([])
  const [copies, setCopies] = useState(2)
  const [includeDatasets, setIncludeDatasets] = useState(false)
  const [auto, setAuto] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  const apply = useCallback((next: State) => {
    setState(next)
    setDisks(next.settings.disks.length ? next.settings.disks : [{ ...NEW_DISK }])
    setCopies(next.settings.copies)
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

  const filled = disks.filter(d => d.path.trim())
  const save = () => run(async () => {
    const next = await call<State>('/data-backup/settings', {
      method: 'PUT', body: JSON.stringify({ disks: filled, copies, include_datasets: includeDatasets, auto }),
    })
    apply(next)
    return next
  }, '設定を保存しました。')

  const updateDisk = (index: number, patch: Partial<DiskSetting>) =>
    setDisks(list => list.map((d, i) => (i === index ? { ...d, ...patch } : d)))

  const removeDisk = (index: number) => {
    const disk = disks[index]
    const live = state?.disks.find(d => d.id && d.id === disk.id)
    if (disk.path.trim() && !(live?.retiring && live.pending === 0)) {
      if (!window.confirm(
        `${disk.path} を写し先から外します。\n` +
        'ここにしか無い中身があると、その分は戻せなくなります。先に「外す準備」にしてバックアップを取り、' +
        '「この写し先は外せます」と出てから外すのが安全です。それでも外しますか?',
      )) return
    }
    setDisks(list => list.filter((_, i) => i !== index))
  }

  const restore = (snap: Snapshot) => {
    const offline = state?.disks.filter(d => !d.available).length ?? 0
    if (!window.confirm(
      `${new Date(snap.created_at).toLocaleString('ja-JP')} のバックアップに戻します。\n` +
      '先に今の状態を1回バックアップしてから、DB と、その時にあったファイルを書き戻します(その後にできたファイルは残ります)。\n' +
      (offline ? `つながっていない写し先が ${offline} つあります。そこにしか無いファイルは戻せず、あとで一覧に出します。\n` : '') +
      '戻しているあいだは、ほかの操作をしないでください。よろしいですか?',
    )) return
    void run(() => call<State>(`/data-backup/restore/${snap.name}`, { method: 'POST' }))
  }

  const saved = state?.settings
  const dirty = !!saved && (
    JSON.stringify(filled) !== JSON.stringify(saved.disks) || copies !== saved.copies
    || includeDatasets !== saved.include_datasets || auto !== saved.auto
  )
  const job = state?.job
  const result = job?.status === 'done' ? job.result : null
  const health = state?.health
  const active = state?.disks.filter(d => d.available && !d.retiring).length ?? 0
  return (
    <section className="adm-section" id="data-backup">
      <h2>画像・作品データのバックアップ</h2>
      <p className="adm-hint">
        DB(物語・キャラ・設定・履歴)と、生成画像・漫画のコマとページ・取り込んだ作品・素材・出版物・ごみ箱を、指定したフォルダ
        (外付けドライブなど)に写します。2回目からは、増えた・変わったファイルだけを写します。モデル・フォント・評価データ
        (取り直せるもの)と <code>.env</code>(トークン)は入れません。{state?.keep ?? 7} 回分を残し、古いものは自動で消します。
      </p>
      <p className="adm-hint">
        写し先はいくつでも足せます。DB と一覧は全部の写し先に、画像などは「置く数」か所に、空きの多い写し先から振り分けます。
        置く数が 2 なら、どの写し先が1つ抜けても全部戻せます。空きが「残す空き」(GB と % の大きい方)を下回った写し先には、新しい中身を置きません。
      </p>
      {error && <p className="adm-error" role="alert">{error}</p>}
      {message && <p className="adm-hint" role="status">{message}</p>}

      <table className="adm-table adm-table--rows">
        <tbody>
          {disks.map((disk, index) => (
            <tr key={disk.id || `new-${index}`}>
              <th style={{ minWidth: '16rem' }}>
                <input value={disk.path} placeholder="例: E:\backup" aria-label={`写し先 ${index + 1} のフォルダ`} spellCheck={false}
                  style={{ width: '100%' }} onChange={e => updateDisk(index, { path: e.target.value })} />
                <div className="adm-row" style={{ marginTop: '0.3rem' }}>
                  <label>残す空き <input type="number" min={0} step={10} value={disk.reserve_gb} style={{ width: '5rem' }}
                    onChange={e => updateDisk(index, { reserve_gb: Number(e.target.value) })} /> GB</label>
                  <label>または <input type="number" min={0} max={90} value={disk.reserve_percent} style={{ width: '4rem' }}
                    onChange={e => updateDisk(index, { reserve_percent: Number(e.target.value) })} /> %</label>
                  <label title="新しい中身を置かず、ここにしか無い中身をほかの写し先へ写します">
                    <input type="checkbox" checked={disk.retiring} onChange={e => updateDisk(index, { retiring: e.target.checked })} /> 外す準備
                  </label>
                </div>
              </th>
              <td>
                <DiskStatus disk={disk.id ? state?.disks.find(d => d.id === disk.id) : undefined} />
              </td>
              <td className="adm-actions">
                <button type="button" onClick={() => removeDisk(index)}>外す</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="adm-row">
        <button type="button" onClick={() => setDisks(list => [...list, { ...NEW_DISK }])}>写し先を足す</button>
        <label>置く数
          <select value={copies} onChange={e => setCopies(Number(e.target.value))} style={{ marginLeft: '0.3rem' }}>
            <option value={1}>1 か所(容量優先)</option>
            <option value={2}>2 か所(1つ抜けても戻せる)</option>
            <option value={3}>3 か所(2つ抜けても戻せる)</option>
          </select>
        </label>
      </div>
      <div className="adm-row">
        <label><input type="checkbox" checked={includeDatasets} onChange={e => setIncludeDatasets(e.target.checked)} /> キャラ別データセットも入れる</label>
        <label><input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} /> 1日1回、自動で取る(バックエンドが動いているあいだ)</label>
        <button type="button" className="adm-primary" disabled={!dirty} onClick={() => void save()}>設定を保存</button>
      </div>
      {saved && saved.disks.length > 0 && active < saved.copies && (
        <p className="adm-error">
          置く先として使える写し先が {active} つしかないので、{saved.copies} か所にはそろいません(写し先を足すか、置く数を減らしてください)。
        </p>
      )}
      {health && (health.short > 0 || health.missing > 0) && (
        <p className="adm-error">
          前の回({new Date(health.checked_at).toLocaleString('ja-JP')})のとき:
          {health.short > 0 && ` ${health.copies} か所にそろっていない中身が ${health.short.toLocaleString()} 個あります(空きができた回にそろえます)。`}
          {health.missing > 0 && ` つながっている写し先のどこにも無い中身が ${health.missing.toLocaleString()} 個あります(抜けている写し先にあるかもしれません)。`}
        </p>
      )}

      <div className="adm-row">
        <button type="button" className="adm-primary" disabled={!saved?.disks.length || running || dirty}
          title={dirty ? '先に設定を保存してください' : undefined}
          onClick={() => void run(() => call<State>('/data-backup/run', { method: 'POST' }))}>
          今すぐバックアップ
        </button>
        {job && job.status !== 'idle' && (
          <span className={job.status === 'error' ? 'adm-error' : 'adm-hint'} role="status">
            {job.kind === 'restore' ? '戻す' : 'バックアップ'}: {job.message}
            {job.status === 'running' && job.total > 0 && `(${job.done}/${job.total})`}
          </span>
        )}
      </div>
      {job?.status === 'running' && job.total > 0 && <progress max={job.total} value={job.done} style={{ width: '100%' }} />}
      {job?.kind === 'restore' && result && Number(result.missing) > 0 && (
        <p className="adm-error">
          {Number(result.missing).toLocaleString()} 個のファイルは、つながっている写し先に無かったので戻せませんでした
          (例: {(result.missing_examples as string[]).join('、')})。その写し先をつないで、もう一度戻してください。
        </p>
      )}

      {state && state.snapshots.length === 0 && saved?.disks.length ? <p className="adm-hint">まだありません。</p> : null}
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
                  <div className="adm-hint">この回に写した分: {snap.added.toLocaleString()} 個 ・ {bytes(snap.added_bytes)}</div>
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
