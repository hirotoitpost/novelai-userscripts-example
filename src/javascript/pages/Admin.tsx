import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useLocalStorage } from '../hooks/useLocalStorage'
import ContentGuardRules from '../components/ContentGuardRules'
import GuardProfiles from '../components/GuardProfiles'
import { formatDuration } from '../components/TaskStatus'
import type { ImagePreset } from '../api'
import './LoraDataset.css'
import './Admin.css'

/**
 * 開発管理者のページ。サーバーの状態とログ、動いている処理と外部サービス、設定(.env)、サーバーの操作とデータの
 * 管理、アプリの既定値・プリセット・コンテンツガードを扱う。
 *
 * LAN の中から使える(この PC のほか、スマホや LAN のほかの端末からも)。インターネット側と、ほかのサイトのページからは
 * 使えない(バックエンドが断る)。管理の API(/api/admin)は、バックエンド(:8000)へ直接つなぐ。
 *
 * 設定を変えたときは、反映に何が要るかをダイアログで知らせる(ApplyDialog):
 *  - pc: PC の再起動が要る
 *  - server: サーバーの再起動が要る(ダイアログから起動し直せる)
 *  - immediate: すぐに反映される
 */

const HOST = window.location.hostname
const BACKEND = `${window.location.protocol}//${HOST}:8000`

async function admin<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BACKEND}/api/admin${path}`, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
  })
  const data = await res.json().catch(() => ({ detail: res.statusText }))
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${res.status}`)
  return data as T
}

function bytes(n: number): string {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(2)} GB`
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MB`
  if (n >= 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${n} B`
}

function when(seconds: number | null | undefined): string {
  return seconds ? new Date(seconds * 1000).toLocaleString('ja-JP') : '-'
}

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

type Tab = 'status' | 'jobs' | 'env' | 'ops' | 'defaults'
const TABS: { id: Tab; label: string }[] = [
  { id: 'status', label: '状態とログ' },
  { id: 'jobs', label: '処理と外部サービス' },
  { id: 'env', label: '設定(.env)' },
  { id: 'ops', label: '操作とデータ' },
  { id: 'defaults', label: '既定値・プリセット・ガード' },
]

type RestartTarget = 'all' | 'backend' | 'frontend'
const TARGET_LABEL: Record<RestartTarget, string> = { all: 'バックエンドとフロント', backend: 'バックエンド', frontend: 'フロント' }

/** サーバーを起動し直してもらう。http は true で http、false で https、null で今のまま。戻り値は画面に出す案内。 */
async function restartServers(target: RestartTarget, http: boolean | null, scheme: string): Promise<string> {
  const next = http === null ? scheme : http ? 'http' : 'https'
  await admin('/restart', { method: 'POST', body: JSON.stringify({ target, http }) })
  if (next !== window.location.protocol.replace(':', '')) {
    // 方式が変わると、このページの URL も変わる
    const url = `${next}://${window.location.host}/admin`
    window.setTimeout(() => { window.location.href = url }, 12000)
    return `${next} で起動し直しています。10秒ほどしたら ${url} を開きます。`
  }
  window.setTimeout(() => window.location.reload(), 10000)
  return '起動し直しています。10秒ほどで戻ります…'
}

/** 変更の反映に何が要るか。 */
interface Applied {
  level: 'pc' | 'server' | 'immediate'
  /** 何を変えたか(例: 「VLLM_MODEL」「全年齢のネガティブ」) */
  what: string
  /** server のとき、どれを起動し直すか */
  target?: RestartTarget
}

const AppliedContext = createContext<(applied: Applied) => void>(() => {})
/** 変更を保存したあとに呼ぶ。反映に何が要るかのダイアログを出す。 */
const useApplied = () => useContext(AppliedContext)

function ApplyDialog({ applied, onClose }: { applied: Applied; onClose: () => void }) {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const target = applied.target ?? 'backend'

  const restart = async () => {
    setBusy(true)
    try {
      const scheme = window.location.protocol.replace(':', '')
      setMessage(await restartServers(target, null, scheme))
    } catch (e) {
      setMessage(errorText(e))
      setBusy(false)
    }
  }

  return (
    <div className="adm-overlay" onClick={busy ? undefined : onClose}>
      <div className={`adm-dialog adm-dialog--${applied.level}`} role="alertdialog" aria-modal="true"
        aria-labelledby="adm-dialog-title" onClick={e => e.stopPropagation()}>
        {applied.level === 'pc' && (
          <>
            <h2 id="adm-dialog-title">PC の再起動が必要です</h2>
            <p>「{applied.what}」を保存しました。</p>
            <p>この項目は、<strong>PC を再起動したあと</strong>に反映されます(開いているターミナルや VS Code、そこから起動した
              プログラムには、起動したときの値が残っているためです)。サーバーの再起動だけでは反映されません。</p>
          </>
        )}
        {applied.level === 'server' && (
          <>
            <h2 id="adm-dialog-title">サーバーの再起動が必要です</h2>
            <p>「{applied.what}」を保存しました。</p>
            <p>この項目は、<strong>{TARGET_LABEL[target]}を起動し直したあと</strong>に反映されます。
              起動し直すと、動いている処理は止まります(途中までの結果は残ります)。</p>
            {target === 'backend' && <p className="adm-hint">MCP のツールも同じ値を使う場合は、Claude Code / VS Code 側の MCP サーバーも起動し直してください。</p>}
          </>
        )}
        {applied.level === 'immediate' && (
          <>
            <h2 id="adm-dialog-title">保存しました</h2>
            <p>「{applied.what}」の変更は、<strong>すぐに反映されます</strong>(次の処理から効きます。再起動は要りません)。</p>
          </>
        )}
        {message && <p className="adm-warn">{message}</p>}
        <div className="adm-row adm-dialog-actions">
          {applied.level === 'server' && (
            <button type="button" className="adm-primary" disabled={busy} onClick={() => void restart()}>
              {TARGET_LABEL[target]}を今すぐ起動し直す
            </button>
          )}
          <button type="button" disabled={busy} autoFocus onClick={onClose}>
            {applied.level === 'server' ? 'あとで起動し直す' : '閉じる'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function Admin() {
  const navigate = useNavigate()
  const [tab, setTab] = useLocalStorage<Tab>('nai_admin_tab', 'status')
  const [applied, setApplied] = useState<Applied | null>(null)

  return (
    <AppliedContext.Provider value={setApplied}>
      <div className="adm-root">
        <header className="adm-header">
          <button type="button" className="adm-link" onClick={() => navigate('/')}>← ホーム</button>
          <h1>開発管理者</h1>
        </header>
        <nav className="adm-tabs" role="tablist">
          {TABS.map(t => (
            <button key={t.id} type="button" role="tab" aria-selected={tab === t.id}
              className={tab === t.id ? 'adm-tab adm-tab--on' : 'adm-tab'} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </nav>
        <main className="adm-main">
          {tab === 'status' && <StatusTab />}
          {tab === 'jobs' && <JobsTab />}
          {tab === 'env' && <EnvTab />}
          {tab === 'ops' && <OpsTab />}
          {tab === 'defaults' && <DefaultsTab />}
        </main>
        {applied && <ApplyDialog applied={applied} onClose={() => setApplied(null)} />}
      </div>
    </AppliedContext.Provider>
  )
}

/** 一定の間隔で読み直す。失敗はメッセージにして返す。 */
function usePolling<T>(load: () => Promise<T>, intervalMs: number | null) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const loadRef = useRef(load)
  loadRef.current = load
  const refresh = useCallback(async () => {
    try {
      setData(await loadRef.current())
      setError(null)
    } catch (e) {
      setError(errorText(e))
    }
  }, [])
  useEffect(() => {
    void refresh()
    if (!intervalMs) return
    const id = window.setInterval(() => void refresh(), intervalMs)
    return () => window.clearInterval(id)
  }, [refresh, intervalMs])
  return { data, error, refresh, setData }
}

// ════════ 状態とログ ════════

interface Status {
  backend: { pid: number; scheme: string; started_at: number; uptime_seconds: number; python: string }
  frontend: { pid: string | null; scheme: string; alive: boolean }
  git: { commit: string; branch: string; subject: string; date: string; dirty: boolean }
  database: { path: string; bytes: number }
}

const LOG_NAMES: [string, string][] = [
  ['backend.err', 'バックエンド(起動・リクエスト・エラー)'],
  ['backend', 'バックエンド(標準出力)'],
  ['frontend', 'フロント(Vite)'],
  ['frontend.err', 'フロント(エラー)'],
]

function StatusTab() {
  const status = usePolling(() => admin<Status>('/status'), 5000)
  const [logName, setLogName] = useLocalStorage('nai_admin_log', 'backend.err')
  const [lines, setLines] = useLocalStorage('nai_admin_log_lines', 200)
  const [follow, setFollow] = useState(true)
  const log = usePolling(
    () => admin<{ lines: string[]; bytes: number; updated_at: number | null }>(`/logs?name=${logName}&lines=${lines}`),
    follow ? 3000 : null,
  )
  const boxRef = useRef<HTMLPreElement>(null)
  useEffect(() => {
    void log.refresh()
    // ログの種類・行数を変えたら読み直す
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [logName, lines])
  useEffect(() => {
    // 追いかけている間は、いつも末尾を見せる
    if (follow && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight
  }, [log.data, follow])

  const s = status.data
  return (
    <>
      <section className="adm-section">
        <h2>サーバー</h2>
        {status.error && <p className="adm-error">バックエンドにつながりません: {status.error}</p>}
        {s && (
          <table className="adm-table">
            <tbody>
              <tr>
                <th>バックエンド</th>
                <td><span className="adm-ok">● 起動中</span> {s.backend.scheme}://{HOST}:8000 ・ PID {s.backend.pid} ・
                  稼働 {formatDuration(s.backend.uptime_seconds * 1000)}(起動 {when(s.backend.started_at)})・ Python {s.backend.python}</td>
              </tr>
              <tr>
                <th>フロント(Vite)</th>
                <td>{s.frontend.alive ? <span className="adm-ok">● 起動中</span> : <span className="adm-bad">● 応答なし</span>}{' '}
                  {s.frontend.scheme}://{window.location.host}{s.frontend.pid && ` ・ PID ${s.frontend.pid}`}
                  {s.frontend.scheme !== s.backend.scheme && <span className="adm-bad"> ・ バックエンドと方式が違います(両方を起動し直してください)</span>}</td>
              </tr>
              <tr>
                <th>バージョン</th>
                <td><code>{s.git.commit || '?'}</code> {s.git.branch && `(${s.git.branch})`} {s.git.subject}
                  {s.git.date && ` ・ ${new Date(s.git.date).toLocaleString('ja-JP')}`}
                  {s.git.dirty && <span className="adm-warn"> ・ コミットしていない変更あり</span>}</td>
              </tr>
              <tr>
                <th>DB</th>
                <td><code>{s.database.path}</code> ・ {bytes(s.database.bytes)}</td>
              </tr>
            </tbody>
          </table>
        )}
      </section>

      <section className="adm-section">
        <h2>ログ</h2>
        <div className="adm-row">
          <select value={logName} onChange={e => setLogName(e.target.value)} aria-label="ログの種類">
            {LOG_NAMES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <label>末尾
            <select value={lines} onChange={e => setLines(Number(e.target.value))}>
              {[100, 200, 500, 1000, 2000].map(n => <option key={n} value={n}>{n}行</option>)}
            </select>
          </label>
          <label className="adm-check">
            <input type="checkbox" checked={follow} onChange={e => setFollow(e.target.checked)} /> 3秒ごとに読み直す
          </label>
          <button type="button" onClick={() => void log.refresh()}>読み直す</button>
          {log.data && <span className="adm-hint">{bytes(log.data.bytes)} ・ 更新 {when(log.data.updated_at)}</span>}
        </div>
        {log.error && <p className="adm-error">{log.error}</p>}
        <pre ref={boxRef} className="adm-log">{log.data?.lines.join('\n') || '(まだ何も出ていません)'}</pre>
      </section>
    </>
  )
}

// ════════ 処理と外部サービス ════════

interface StoryJob {
  story_id: number; title: string; kind: string; status: string; message: string
  progress: number; total: number; detail: string | null; started_at: number | null; ended_at: number | null
}
interface ImportJob {
  import_id: number; title: string; status: string; message: string
  progress: number; total: number; detail: string | null; story_id: number | null
}
interface Services {
  novelai: { configured: boolean; ok: boolean; detail?: string; tier?: number; active?: boolean; expires_at?: number; anlas?: number }
  ollama: { ok: boolean; detail?: string; text_url: string; text_model: string; vision_url: string; vision_model: string; models: string[] }
  models: { name: string; bytes: number; files: number }[]
  certificate: { exists: boolean; expires_at?: string; days_left?: number; names?: string[] }
}

const STATUS_LABEL: Record<string, string> = { running: '処理中', done: '完了', error: 'エラー', cancelled: 'キャンセル' }
const TIERS: Record<number, string> = { 0: 'Paper', 1: 'Tablet', 2: 'Scroll', 3: 'Opus' }

function JobsTab() {
  const navigate = useNavigate()
  const jobs = usePolling(() => admin<{ stories: StoryJob[]; imports: ImportJob[] }>('/jobs'), 3000)
  const services = usePolling(() => admin<Services>('/services'), null)
  const [message, setMessage] = useState<string | null>(null)

  const cancel = async (path: string, title: string) => {
    if (!window.confirm(`「${title}」の処理を止めます。途中までの結果は残ります。よろしいですか?`)) return
    try {
      await admin(path, { method: 'POST' })
      setMessage('止めました')
      void jobs.refresh()
    } catch (e) {
      setMessage(errorText(e))
    }
  }

  const sv = services.data
  return (
    <>
      <section className="adm-section">
        <h2>動いている処理・最近の処理</h2>
        {jobs.error && <p className="adm-error">{jobs.error}</p>}
        {message && <p className="adm-hint">{message}</p>}
        {jobs.data && jobs.data.stories.length + jobs.data.imports.length === 0 && (
          <p className="adm-hint">ありません(バックエンドを起動してからの処理が出ます)。</p>
        )}
        {jobs.data && (
          <table className="adm-table adm-table--rows">
            <tbody>
              {jobs.data.stories.map(j => (
                <tr key={`s${j.story_id}`}>
                  <th><span className={`adm-state adm-state--${j.status}`}>{STATUS_LABEL[j.status] ?? j.status}</span></th>
                  <td>
                    <strong>{j.title}</strong> <span className="adm-hint">({j.kind})</span><br />
                    {j.status === 'error' ? j.detail ?? j.message : j.message}
                    {j.total > 0 && ` ・ ${j.progress}/${j.total}`}
                    <span className="adm-hint"> ・ 開始 {when(j.started_at)}{j.ended_at && ` ・ 終了 ${when(j.ended_at)}`}</span>
                  </td>
                  <td className="adm-actions">
                    <button type="button" onClick={() => navigate(`/story?story=${j.story_id}`)}>開く</button>
                    {j.status === 'running' && (
                      <button type="button" className="adm-danger" onClick={() => void cancel(`/jobs/story/${j.story_id}/cancel`, j.title)}>止める</button>
                    )}
                  </td>
                </tr>
              ))}
              {jobs.data.imports.map(j => (
                <tr key={`i${j.import_id}`}>
                  <th><span className={`adm-state adm-state--${j.status}`}>{STATUS_LABEL[j.status] ?? j.status}</span></th>
                  <td>
                    <strong>{j.title}</strong> <span className="adm-hint">(漫画の取り込み)</span><br />
                    {j.status === 'error' ? j.detail ?? j.message : j.message}{j.total > 0 && ` ・ ${j.progress}/${j.total}`}
                  </td>
                  <td className="adm-actions">
                    <button type="button" onClick={() => navigate('/manga-import')}>開く</button>
                    {j.status === 'running' && (
                      <button type="button" className="adm-danger" onClick={() => void cancel(`/jobs/import/${j.import_id}/cancel`, j.title)}>止める</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="adm-section">
        <div className="adm-row">
          <h2>外部サービス</h2>
          <button type="button" onClick={() => void services.refresh()}>確かめ直す</button>
        </div>
        {services.error && <p className="adm-error">{services.error}</p>}
        {!sv && !services.error && <p className="adm-hint">確かめています…</p>}
        {sv && (
          <table className="adm-table">
            <tbody>
              <tr>
                <th>NovelAI(.env のトークン)</th>
                <td>
                  {sv.novelai.ok
                    ? <><span className="adm-ok">● 使えます</span> {TIERS[sv.novelai.tier ?? -1] ?? `tier ${sv.novelai.tier}`}
                      {sv.novelai.active === false && <span className="adm-bad"> ・ 契約が切れています</span>}
                      {' '}・ Anlas {sv.novelai.anlas?.toLocaleString()} ・ 期限 {when(sv.novelai.expires_at)}</>
                    : <><span className={sv.novelai.configured ? 'adm-bad' : 'adm-warn'}>● {sv.novelai.configured ? '使えません' : '未設定'}</span> {sv.novelai.detail}</>}
                </td>
              </tr>
              <tr>
                <th>ローカルの LLM(Ollama)</th>
                <td>
                  {sv.ollama.ok ? <span className="adm-ok">● つながります</span> : <span className="adm-bad">● つながりません</span>}
                  {' '}<code>{sv.ollama.text_url}</code> ・ 文章 <code>{sv.ollama.text_model || '-'}</code> ・ 画像 <code>{sv.ollama.vision_model || '-'}</code>
                  {sv.ollama.detail && <span className="adm-warn"> ・ {sv.ollama.detail}</span>}
                  {sv.ollama.models.length > 0 && <><br /><span className="adm-hint">入っているモデル: {sv.ollama.models.join('、')}</span></>}
                </td>
              </tr>
              <tr>
                <th>判定モデル(data/models)</th>
                <td>
                  {sv.models.length === 0 && <span className="adm-hint">まだダウンロードしていません(使うときに自動で取得します)</span>}
                  {sv.models.map(m => <div key={m.name}><code>{m.name}</code> ・ {bytes(m.bytes)}</div>)}
                </td>
              </tr>
              <tr>
                <th>LAN の証明書</th>
                <td>
                  {sv.certificate.exists
                    ? <><span className={(sv.certificate.days_left ?? 0) > 30 ? 'adm-ok' : 'adm-bad'}>● あと {sv.certificate.days_left} 日</span>
                      {' '}(期限 {sv.certificate.expires_at && new Date(sv.certificate.expires_at).toLocaleDateString('ja-JP')})
                      <br /><span className="adm-hint">{sv.certificate.names?.join('、')}</span></>
                    : <span className="adm-hint">ありません(https にするなら scripts/make_lan_cert.py)</span>}
                </td>
              </tr>
            </tbody>
          </table>
        )}
      </section>
    </>
  )
}

// ════════ 設定(.env) ════════

interface EnvEntry {
  key: string; set: boolean; secret: boolean; value: string | null; description: string
  restart: 'pc' | 'server'; restart_target: RestartTarget | ''
}
interface EnvSaved { entries: EnvEntry[]; restart: 'pc' | 'server'; restart_target: RestartTarget | '' }

function EnvTab() {
  const [entries, setEntries] = useState<EnvEntry[]>([])
  const [path, setPath] = useState('')
  const [editing, setEditing] = useState<Record<string, string>>({})
  const [newKey, setNewKey] = useState('')
  const [newValue, setNewValue] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [changed, setChanged] = useState(false)
  const applied = useApplied()

  useEffect(() => {
    admin<{ path: string; entries: EnvEntry[] }>('/env')
      .then(d => { setEntries(d.entries); setPath(d.path) })
      .catch(e => setError(errorText(e)))
  }, [])

  const save = async (key: string, value: string | null) => {
    setError(null)
    try {
      const d = await admin<EnvSaved>('/env', { method: 'PUT', body: JSON.stringify({ key, value }) })
      setEntries(d.entries)
      setEditing(({ [key]: _done, ...rest }) => rest)
      setChanged(true)
      applied({ level: d.restart, what: key, target: d.restart_target || undefined })
      return true
    } catch (e) {
      setError(errorText(e))
      return false
    }
  }

  return (
    <section className="adm-section">
      <h2>設定(.env)</h2>
      <p className="adm-hint">
        <code>{path}</code> の項目です。トークンなどの秘密は末尾の数文字だけ出します(全体は画面に出しません。変えるときは新しい値を入れます)。
        書き換える前の内容は <code>.env.bak</code> に1つだけ残ります。<strong>反映にはサーバーの再起動(項目によっては PC の再起動)が要ります</strong>。保存すると、何が要るかを表示します。
      </p>
      {changed && <p className="adm-warn">変更を保存しました。再起動するまでは、前の値で動いています。</p>}
      {error && <p className="adm-error">{error}</p>}
      <table className="adm-table adm-table--rows">
        <tbody>
          {entries.map(e => {
            const draft = editing[e.key]
            return (
              <tr key={e.key} className={e.set ? '' : 'adm-unset'}>
                <th><code>{e.key}</code>{e.secret && <span className="adm-tag">秘密</span>}
                  <span className="adm-tag">{e.restart === 'pc' ? 'PC の再起動' : e.restart_target === 'frontend' ? 'フロントの再起動' : 'バックエンドの再起動'}</span></th>
                <td>
                  {draft === undefined
                    ? (e.set ? <code>{e.value || '(空)'}</code> : <span className="adm-hint">(未設定)</span>)
                    : <input type={e.secret ? 'password' : 'text'} value={draft} autoFocus
                        placeholder={e.secret ? '新しい値' : ''} autoComplete="off"
                        onChange={ev => setEditing({ ...editing, [e.key]: ev.target.value })} />}
                  {e.description && <div className="adm-hint">{e.description}</div>}
                </td>
                <td className="adm-actions">
                  {draft === undefined ? (
                    <>
                      <button type="button" onClick={() => setEditing({ ...editing, [e.key]: e.secret ? '' : e.value ?? '' })}>
                        {e.set ? '変える' : '設定する'}
                      </button>
                      {e.set && (
                        <button type="button" className="adm-danger" onClick={() => {
                          if (window.confirm(`${e.key} を消します(.env ではコメントにします)。よろしいですか?`)) void save(e.key, null)
                        }}>消す</button>
                      )}
                    </>
                  ) : (
                    <>
                      <button type="button" className="adm-primary" onClick={() => void save(e.key, draft)}>保存</button>
                      <button type="button" onClick={() => setEditing(({ [e.key]: _cancel, ...rest }) => rest)}>やめる</button>
                    </>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <div className="adm-row">
        <input value={newKey} placeholder="新しい項目名(例: MY_SETTING)" onChange={e => setNewKey(e.target.value.toUpperCase())} />
        <input value={newValue} placeholder="値" onChange={e => setNewValue(e.target.value)} />
        <button type="button" disabled={!newKey.trim()} onClick={async () => {
          if (await save(newKey.trim(), newValue)) { setNewKey(''); setNewValue('') }
        }}>足す</button>
      </div>
    </section>
  )
}

// ════════ 操作とデータ ════════

interface Storage {
  usage: { label: string; path: string; bytes: number; files: number }[]
  disk: { free: number; total: number }
  cleanup: { category: string; label: string; files: number; bytes: number }[]
}
interface Backup { name: string; bytes: number; created_at: number }

function OpsTab() {
  const status = usePolling(() => admin<Status>('/status'), 5000)
  const services = usePolling(() => admin<Services>('/services'), null)
  const storage = usePolling(() => admin<Storage>('/storage'), null)
  const backups = usePolling(() => admin<{ dir: string; backups: Backup[] }>('/backups'), null)
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const scheme = status.data?.backend.scheme ?? window.location.protocol.replace(':', '')
  const hasCert = services.data?.certificate.exists ?? false

  const restart = async (target: RestartTarget, http: boolean | null) => {
    const next = http === null ? scheme : http ? 'http' : 'https'
    if (!window.confirm(`${TARGET_LABEL[target]}を起動し直します(${next})。動いている処理は止まります(途中までの結果は残ります)。よろしいですか?`)) return
    setBusy(true)
    setMessage(null)
    try {
      setMessage(await restartServers(target, http, scheme))
    } catch (e) {
      setMessage(errorText(e))
      setBusy(false)
    }
  }

  const cleanup = async (category: string, label: string, files: number) => {
    if (!window.confirm(`${label} ${files}個を消します。元に戻せません。よろしいですか?`)) return
    try {
      const r = await admin<{ deleted: number; bytes: number }>(`/cleanup/${category}`, { method: 'POST' })
      setMessage(`${r.deleted}個(${bytes(r.bytes)})を消しました`)
      void storage.refresh()
    } catch (e) {
      setMessage(errorText(e))
    }
  }

  return (
    <>
      <section className="adm-section">
        <h2>サーバーの操作</h2>
        {message && <p className="adm-warn">{message}</p>}
        <p className="adm-hint">今は <strong>{scheme}</strong> で動いています。起動し直すと、動いている処理は止まります(コマの生成などは途中まで残ります)。</p>
        <div className="adm-row">
          <button type="button" disabled={busy} onClick={() => void restart('all', null)}>両方を起動し直す</button>
          <button type="button" disabled={busy} onClick={() => void restart('backend', null)}>バックエンドだけ</button>
          <button type="button" disabled={busy} onClick={() => void restart('frontend', null)}>フロントだけ</button>
        </div>
        <div className="adm-row">
          {scheme === 'https'
            ? <button type="button" disabled={busy} onClick={() => void restart('all', true)}>http に切り替える(デバッグ用)</button>
            : <button type="button" disabled={busy || !hasCert} onClick={() => void restart('all', false)}
                title={hasCert ? undefined : '証明書がありません(scripts/make_lan_cert.py)'}>https に切り替える</button>}
          <span className="adm-hint">http の間は、スマホにインストールしたアプリと通知は使えません。</span>
        </div>
      </section>

      <section className="adm-section">
        <div className="adm-row">
          <h2>保存領域</h2>
          <button type="button" onClick={() => void storage.refresh()}>測り直す</button>
        </div>
        {storage.error && <p className="adm-error">{storage.error}</p>}
        {storage.data && (
          <>
            <p className="adm-hint">ディスクの空き {bytes(storage.data.disk.free)} / {bytes(storage.data.disk.total)}</p>
            <table className="adm-table adm-table--rows">
              <tbody>
                {storage.data.usage.map(u => (
                  <tr key={u.path}>
                    <th>{u.label}</th>
                    <td><code>{u.path}</code></td>
                    <td className="adm-num">{bytes(u.bytes)} ・ {u.files.toLocaleString()}個</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <h3>使われていないファイルの掃除</h3>
            <table className="adm-table adm-table--rows">
              <tbody>
                {storage.data.cleanup.map(c => (
                  <tr key={c.category}>
                    <th>{c.label}</th>
                    <td className="adm-num">{c.files.toLocaleString()}個 ・ {bytes(c.bytes)}</td>
                    <td className="adm-actions">
                      <button type="button" className="adm-danger" disabled={c.files === 0}
                        onClick={() => void cleanup(c.category, c.label, c.files)}>消す</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>

      <section className="adm-section">
        <div className="adm-row">
          <h2>DB のバックアップ</h2>
          <button type="button" className="adm-primary" onClick={async () => {
            try {
              const r = await admin<{ created: string; backups: Backup[] }>('/backups', { method: 'POST' })
              backups.setData({ dir: backups.data?.dir ?? '', backups: r.backups })
              setMessage(`バックアップを作りました: ${r.created}`)
              void storage.refresh()
            } catch (e) {
              setMessage(errorText(e))
            }
          }}>今のバックアップを作る</button>
        </div>
        <p className="adm-hint">
          物語・キャラ・プリセットなどの DB(<code>data/app.db</code>)を <code>{backups.data?.dir}</code> に写します(画像は含みません)。
          戻すときは、サーバーを止めてからそのファイルを <code>data/app.db</code> に上書きします。
        </p>
        {backups.data && backups.data.backups.length === 0 && <p className="adm-hint">まだありません。</p>}
        <table className="adm-table adm-table--rows">
          <tbody>
            {backups.data?.backups.map(b => (
              <tr key={b.name}>
                <th><code>{b.name}</code></th>
                <td className="adm-num">{bytes(b.bytes)} ・ {when(b.created_at)}</td>
                <td className="adm-actions">
                  <button type="button" className="adm-danger" onClick={async () => {
                    if (!window.confirm(`${b.name} を消します。よろしいですか?`)) return
                    const r = await admin<{ backups: Backup[] }>(`/backups/${b.name}`, { method: 'DELETE' })
                    backups.setData({ dir: backups.data?.dir ?? '', backups: r.backups })
                  }}>消す</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  )
}

// ════════ 既定値・プリセット・ガード ════════

interface Setting {
  key: string; group: string; label: string; description: string; kind: 'text' | 'int' | 'float'
  default: string | number; value: string | number; overridden: boolean; minimum: number | null; maximum: number | null
}

function DefaultsTab() {
  const [settings, setSettings] = useState<Setting[]>([])
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [presets, setPresets] = useState<ImagePreset[]>([])
  const [presetDrafts, setPresetDrafts] = useState<Record<number, string>>({})
  const [guardId, setGuardId] = useState<number | null>(null)
  const applied = useApplied()

  const loadPresets = useCallback(() => {
    fetch(`${BACKEND}/api/image/presets`).then(r => r.json()).then(setPresets).catch(() => {})
  }, [])

  useEffect(() => {
    admin<{ settings: Setting[] }>('/settings').then(d => setSettings(d.settings)).catch(e => setError(errorText(e)))
    loadPresets()
  }, [loadPresets])

  const saveSetting = async (key: string, value: string | number | null) => {
    setError(null)
    try {
      const d = await admin<{ settings: Setting[] }>(`/settings/${key}`, { method: 'PUT', body: JSON.stringify({ value }) })
      setSettings(d.settings)
      setDrafts(({ [key]: _done, ...rest }) => rest)
      applied({ level: 'immediate', what: d.settings.find(s => s.key === key)?.label ?? key })
    } catch (e) {
      setError(errorText(e))
    }
  }

  const savePreset = async (preset: ImagePreset) => {
    setError(null)
    try {
      const parsed = JSON.parse(presetDrafts[preset.id])
      const res = await fetch(`${BACKEND}/api/image/presets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: preset.name, settings: parsed }),
      })
      if (!res.ok) throw new Error(JSON.stringify((await res.json()).detail))
      setPresetDrafts(({ [preset.id]: _done, ...rest }) => rest)
      loadPresets()
      applied({ level: 'immediate', what: `プリセット「${preset.name}」` })
    } catch (e) {
      setError(`プリセット「${preset.name}」: ${errorText(e)}`)
    }
  }

  const groups = [...new Set(settings.map(s => s.group))]
  return (
    <>
      <section className="adm-section">
        <h2>既定値</h2>
        <p className="adm-hint">変えた値は DB に保存され、次の処理から効きます(再起動は要りません)。「既定に戻す」でコードの値に戻ります。</p>
        {error && <p className="adm-error">{error}</p>}
        {groups.map(group => (
          <div key={group}>
            <h3>{group}</h3>
            {settings.filter(s => s.group === group).map(s => {
              const draft = drafts[s.key] ?? String(s.value)
              const dirty = draft !== String(s.value)
              return (
                <div key={s.key} className="adm-setting">
                  <label>
                    <strong>{s.label}</strong>
                    {s.overridden && <span className="adm-tag">変更あり</span>}
                    <span className="adm-hint">{s.description}</span>
                    {s.kind === 'text'
                      ? <textarea rows={2} value={draft} onChange={e => setDrafts({ ...drafts, [s.key]: e.target.value })} />
                      : <input type="number" value={draft} step={s.kind === 'int' ? 1 : 0.05}
                          min={s.minimum ?? undefined} max={s.maximum ?? undefined}
                          onChange={e => setDrafts({ ...drafts, [s.key]: e.target.value })} />}
                  </label>
                  <div className="adm-row">
                    <button type="button" className="adm-primary" disabled={!dirty}
                      onClick={() => void saveSetting(s.key, s.kind === 'text' ? draft : Number(draft))}>保存</button>
                    <button type="button" disabled={!s.overridden} onClick={() => void saveSetting(s.key, null)}>既定に戻す</button>
                    {s.overridden && <span className="adm-hint">既定: {String(s.default)}</span>}
                  </div>
                </div>
              )
            })}
          </div>
        ))}
      </section>

      <section className="adm-section">
        <h2>画像のプリセット</h2>
        <p className="adm-hint">物語の挿絵・漫画・漫画ドラフトで選ぶ生成設定です。新しく作るのは各ページの「プリセットを保存」から。ここでは中身の確認・修正・削除ができます。</p>
        {presets.length === 0 && <p className="adm-hint">まだありません。</p>}
        {presets.map(p => {
          const text = presetDrafts[p.id] ?? JSON.stringify(p.settings, null, 2)
          return (
            <details key={p.id} className="adm-preset">
              <summary><strong>{p.name}</strong> <span className="adm-hint">{p.settings.model} ・ {p.settings.width}×{p.settings.height} ・ steps {p.settings.steps} ・ scale {p.settings.scale}</span></summary>
              <textarea rows={12} spellCheck={false} value={text} onChange={e => setPresetDrafts({ ...presetDrafts, [p.id]: e.target.value })} />
              <div className="adm-row">
                <button type="button" className="adm-primary" disabled={presetDrafts[p.id] === undefined} onClick={() => void savePreset(p)}>保存</button>
                <button type="button" className="adm-danger" onClick={async () => {
                  if (!window.confirm(`プリセット「${p.name}」を消します。よろしいですか?`)) return
                  await fetch(`${BACKEND}/api/image/presets/${p.id}`, { method: 'DELETE' })
                  loadPresets()
                  applied({ level: 'immediate', what: `プリセット「${p.name}」の削除` })
                }}>削除</button>
              </div>
            </details>
          )
        })}
      </section>

      <section className="adm-section">
        <h2>コンテンツガード</h2>
        <p className="adm-hint">
          性的な内容と、未成年に見える内容を扱う定義です(止めるタグ・取り除くタグ・足すネガティブ・LLM への指示・成人向けの判定の語)。
          値は DB にあり、保存すると次の処理から効きます(再起動は要りません)。API は <code>/api/content-guard</code> です。
        </p>
        <ContentGuardRules onSaved={what => applied({ level: 'immediate', what })} />
        <h3>ガードプロファイル(キャラ別データセット)</h3>
        <p className="adm-hint">「全年齢で止めるタグ」に、データセットごとに足すタグとネガティブです。</p>
        <div className="adm-guards">
          <GuardProfiles selectedId={guardId} onSelect={setGuardId} onSaved={what => applied({ level: 'immediate', what })} />
        </div>
      </section>
    </>
  )
}
