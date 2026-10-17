import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { API_ORIGIN, fileUrl } from '../library'
import UgoiraRigEditor from '../components/UgoiraRigEditor'
import './Publications.css'
import './Ugoira.css'

/**
 * うごイラ(Pixiv のパラパラ漫画のような動く絵)を作る・直す・書き出す(/api/ugoira)。
 * A. 部分だけ描き直す: ギャラリーの画像の、目・口・髪など動かす所だけを描き直してコマにする(範囲の外は元のまま)。
 * コマの並び・表示時間を直し、プレビューで確かめて、Pixiv 用 ZIP・GIF・アニメ WebP に書き出す。
 */

interface Motion { id: number; name: string; region: string; frames: string[]; delays: number[] }
interface Frame { path: string; delay: number; tags: string }
interface Warp { kind: string; name: string; period: number }
interface Project { id: number; name: string; source_key: string | null; method: string; frames: Frame[]; created_at: string }
interface VideoStatus { configured: boolean; reachable: boolean; message: string; gpu?: string; vram_free_gb?: number | null }
interface Job { id: string; status: 'running' | 'done' | 'error'; message: string; progress: number; total: number; project_id: number | null }

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_ORIGIN}/api/ugoira${path}`, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
  })
  const body = await res.json().catch(() => null)
  if (!res.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `HTTP ${res.status}`)
  return body as T
}

function Preview({ frames }: { frames: Frame[] }) {
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(true)
  useEffect(() => {
    if (!playing || frames.length === 0) return
    const id = window.setTimeout(() => setIndex(i => (i + 1) % frames.length), frames[index % frames.length]?.delay ?? 100)
    return () => window.clearTimeout(id)
  }, [index, playing, frames])
  if (frames.length === 0) return null
  const current = frames[index % frames.length]
  return (
    <div className="ugo-preview">
      <img src={fileUrl(current.path)} alt={`コマ ${index % frames.length + 1}`} />
      <div className="ugo-row">
        <button type="button" onClick={() => setPlaying(!playing)}>{playing ? '止める' : '動かす'}</button>
        <span className="pub-meta">コマ {index % frames.length + 1}/{frames.length}・{current.delay}ms</span>
      </div>
    </div>
  )
}

export default function Ugoira() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const sourceKey = params.get('key')
  const [motions, setMotions] = useState<Motion[]>([])
  const [regions, setRegions] = useState<Record<string, string>>({})
  const [motionId, setMotionId] = useState<number | null>(null)
  // 描き直しの強さ。'auto' は方法ごとの既定(A: 1.0、B: 0.7)
  const [strength, setStrength] = useState<string>('auto')
  const [projects, setProjects] = useState<Project[]>([])
  const [project, setProject] = useState<Project | null>(null)
  const [frames, setFrames] = useState<Frame[]>([])
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [maxSide, setMaxSide] = useState('')
  const [interpCount, setInterpCount] = useState(1)
  const [interpLoop, setInterpLoop] = useState(true)
  // 作るときに足す中割りの枚数(0 は足さない)
  const [between, setBetween] = useState(1)
  const [warps, setWarps] = useState<Warp[]>([])
  const [warpKind, setWarpKind] = useState('breath')
  const [warpAmount, setWarpAmount] = useState(1)
  const [warpFrames, setWarpFrames] = useState(16)
  const [keepBackground, setKeepBackground] = useState(true)
  const [rigs, setRigs] = useState<Warp[]>([])
  const [rigStyles, setRigStyles] = useState<Record<string, string>>({})
  const [rigKind, setRigKind] = useState('hair')
  const [rigStyle, setRigStyle] = useState('anime')
  const [rigAmount, setRigAmount] = useState(1)
  // 髪の裏を NovelAI で描く(胴体だけを切り出して。髪が残ったら LaMa)
  const [rigNovelai, setRigNovelai] = useState(false)
  // F. 動画モデル(リモートの GPU の ComfyUI)
  const [videoStatus, setVideoStatus] = useState<VideoStatus | null>(null)
  const [videoMotion, setVideoMotion] = useState('she blinks slowly, her hair sways gently in the wind, subtle breathing')
  const [videoKind, setVideoKind] = useState<'loop' | 'i2v'>('loop')
  const [videoSeconds, setVideoSeconds] = useState(3)
  const [videoBetween, setVideoBetween] = useState(0)
  const [videoSmooth, setVideoSmooth] = useState(true)
  const [editing, setEditing] = useState<Motion | null>(null)
  const [draft, setDraft] = useState({ name: '', region: 'eyes', lines: '' })
  const timer = useRef<number | null>(null)
  const [sourcePath, setSourcePath] = useState<string | null>(null)
  useEffect(() => {
    if (!sourceKey) return
    fetch(`${API_ORIGIN}/api/library/images/item?key=${encodeURIComponent(sourceKey)}`)
      .then(r => (r.ok ? r.json() : null))
      .then(item => setSourcePath(item?.path ?? null))
      .catch(() => setSourcePath(null))
  }, [sourceKey])

  const loadMotions = useCallback(() => {
    call<{ motions: Motion[]; regions: Record<string, string>; warps?: Warp[]; rigs?: Warp[]; styles?: Record<string, string> }>('/motions')
      .then(data => {
        setMotions(data.motions)
        setRegions(data.regions)
        setWarps(data.warps ?? [])
        setRigs(data.rigs ?? [])
        setRigStyles(data.styles ?? {})
        setMotionId(id => id ?? data.motions[0]?.id ?? null)
      })
      .catch(e => setError(String(e)))
  }, [])
  const loadVideoStatus = useCallback(() => {
    setVideoStatus(null)
    call<VideoStatus>('/video/status').then(setVideoStatus).catch(e => setVideoStatus({ configured: false, reachable: false, message: String(e) }))
  }, [])
  useEffect(() => { if (sourceKey) loadVideoStatus() }, [sourceKey, loadVideoStatus])
  const loadProjects = useCallback(() => { call<Project[]>('').then(setProjects).catch(() => {}) }, [])
  useEffect(() => { loadMotions(); loadProjects() }, [loadMotions, loadProjects])
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current) }, [])

  const open = useCallback(async (id: number) => {
    const p = await call<Project>(`/${id}`)
    setProject(p)
    setFrames(p.frames)
    setParams(prev => { const next = new URLSearchParams(prev); next.set('project', String(id)); return next })
  }, [setParams])
  useEffect(() => {
    const id = Number(params.get('project'))
    if (id && project?.id !== id) void open(id).catch(e => setError(String(e)))
  }, [params, project, open])

  const follow = (id: string) => {
    call<Job>(`/jobs/${id}`).then(next => {
      setJob(next)
      if (next.status === 'running') timer.current = window.setTimeout(() => follow(id), 1500)
      else if (next.status === 'done' && next.project_id) { void open(next.project_id); loadProjects() }
    }).catch(e => setError(String(e)))
  }

  async function start() {
    if (!sourceKey || motionId === null) return
    setError(null)
    try {
      const started = await call<Job>('/partial', { method: 'POST', body: JSON.stringify({ key: sourceKey, motion_id: motionId, interpolate: between, ...(strength === 'auto' ? {} : { strength: Number(strength) }) }) })
      setJob(started)
      follow(started.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  /** D. 絵を変形して動かす(描き直さない。この PC で) */
  async function startWarp() {
    if (!sourceKey) return
    setError(null)
    try {
      const started = await call<Job>('/warp', {
        method: 'POST',
        body: JSON.stringify({ key: sourceKey, kind: warpKind, amount: warpAmount, frames: warpFrames, keep_background: keepBackground }),
      })
      setJob(started)
      follow(started.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  /** E. 部位ごとに分けて、骨格とばねで動かす(描き直さない。この PC で) */
  async function startRig() {
    if (!sourceKey) return
    setError(null)
    try {
      const started = await call<Job>('/rig', {
        method: 'POST',
        body: JSON.stringify({ key: sourceKey, kind: rigKind, style: rigStyle, amount: rigAmount, plate: rigNovelai ? 'novelai' : 'lama' }),
      })
      setJob(started)
      follow(started.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  /** F. 動画モデルで動かす(リモートの GPU の ComfyUI。Wan 2.2) */
  async function startVideo() {
    if (!sourceKey) return
    setError(null)
    try {
      const started = await call<Job>('/video', {
        method: 'POST',
        body: JSON.stringify({ key: sourceKey, motion: videoMotion, kind: videoKind, seconds: videoSeconds, interpolate: videoBetween, smooth: videoSmooth }),
      })
      setJob(started)
      follow(started.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const move = (index: number, step: number) => setFrames(prev => {
    const next = [...prev]
    const target = index + step
    if (target < 0 || target >= next.length) return prev
    ;[next[index], next[target]] = [next[target], next[index]]
    return next
  })

  /** C. 中割りを足したうごイラを新しく作る(この PC で。元のうごイラは残る) */
  async function interpolate() {
    if (!project) return
    setError(null)
    try {
      const started = await call<Job>(`/${project.id}/interpolate`, {
        method: 'POST', body: JSON.stringify({ count: interpCount, loop: interpLoop }),
      })
      setJob(started)
      follow(started.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function saveFrames() {
    if (!project) return
    setError(null)
    try {
      const saved = await call<Project>(`/${project.id}/frames`, { method: 'PUT', body: JSON.stringify({ frames }) })
      setProject(saved)
      setFrames(saved.frames)
      setNotice('コマの並び・表示時間を保存しました。')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function download(format: 'zip' | 'gif' | 'webp') {
    if (!project) return
    setError(null)
    try {
      const query = new URLSearchParams({ format, ...(maxSide ? { max_side: maxSide } : {}) })
      const res = await fetch(`${API_ORIGIN}/api/ugoira/${project.id}/export?${query}`)
      if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`)
      const link = document.createElement('a')
      link.href = URL.createObjectURL(await res.blob())
      link.download = `ugoira_${project.id}.${format}`
      link.click()
      URL.revokeObjectURL(link.href)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function remove() {
    if (!project || !window.confirm(`「${project.name}」をごみ箱へ移します(30日のあいだは元に戻せます)。よろしいですか?`)) return
    await fetch(`${API_ORIGIN}/api/ugoira/${project.id}`, { method: 'DELETE' })
    setProject(null)
    setFrames([])
    setParams(prev => { const next = new URLSearchParams(prev); next.delete('project'); return next })
    loadProjects()
  }

  function editMotion(motion: Motion | null) {
    setEditing(motion ?? { id: 0, name: '', region: 'eyes', frames: [], delays: [] })
    setDraft(motion
      ? { name: motion.name, region: motion.region, lines: motion.frames.map((f, i) => `${f} ; ${motion.delays[i]}`).join('\n') }
      : { name: '', region: 'eyes', lines: ' ; 1000\nclosed eyes ; 100' })
  }

  async function saveMotion() {
    if (!editing) return
    const rows = draft.lines.split('\n').filter(l => l.trim())
    const body = {
      name: draft.name, region: draft.region,
      frames: rows.map(l => l.split(';')[0].trim()),
      delays: rows.map(l => Number(l.split(';')[1]?.trim() || 100)),
    }
    setError(null)
    try {
      await call(editing.id ? `/motions/${editing.id}` : '/motions', { method: editing.id ? 'PUT' : 'POST', body: JSON.stringify(body) })
      setEditing(null)
      loadMotions()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function deleteMotion(motion: Motion) {
    if (!window.confirm(`動き「${motion.name}」を消します。よろしいですか?`)) return
    await fetch(`${API_ORIGIN}/api/ugoira/motions/${motion.id}`, { method: 'DELETE' })
    setMotionId(null)
    loadMotions()
  }

  const motion = motions.find(m => m.id === motionId)
  // 体ごと・絵全体の動きは B(全体を少しずつ描き直す)
  const whole = motion?.region === 'person' || motion?.region === 'all'
  const running = job?.status === 'running'
  const dirty = !!project && JSON.stringify(frames) !== JSON.stringify(project.frames)
  return (
    <div className="pub-root">
      <header className="pub-header">
        <button type="button" className="pub-link" onClick={() => navigate('/')}>← ホーム</button>
        <h1>うごイラ</h1>
        <span className="pub-count">{projects.length} 件</span>
        <button type="button" className="pub-link" onClick={() => navigate('/gallery')}>ギャラリーへ</button>
      </header>
      <main className="pub-main ugo-main">
        {error && <p className="pub-error" role="alert">{error}</p>}
        {notice && <p className="pub-hint" role="status">{notice}</p>}

        <section className="ugo-section">
          <h2>作る</h2>
          {!sourceKey && <p className="pub-hint">ギャラリーで画像を開き、「うごイラを作る」を押すと、ここに元の絵が入ります。</p>}
          {sourceKey && (
            <>
              <div className="ugo-source">
                {sourcePath && <img src={`${API_ORIGIN}/api/library/thumb?path=${encodeURIComponent(sourcePath)}&w=240`} alt="元の絵" />}
                <span className="pub-meta">元の絵: <code>{sourceKey}</code></span>
              </div>
              <div className="ugo-row">
                <label>動き{' '}
                  <select value={motionId ?? ''} disabled={running} onChange={e => setMotionId(Number(e.target.value))}>
                    {motions.map(m => <option key={m.id} value={m.id}>{m.name}({regions[m.region]}・{m.frames.length}コマ)</option>)}
                  </select>
                </label>
                <label>描き直しの強さ{' '}
                  <select value={strength} disabled={running} onChange={e => setStrength(e.target.value)}>
                    <option value="auto">おまかせ({whole ? '0.7' : '1.0'})</option>
                    {(whole ? ['0.8', '0.6', '0.5'] : ['0.8', '0.6']).map(v => (
                      <option key={v} value={v}>{v}{Number(v) < (whole ? 0.7 : 1.0) ? '(元の絵に近い)' : '(動きが大きい・変わりやすい)'}</option>
                    ))}
                  </select>
                </label>
                <label>中割り{' '}
                  <select value={between} disabled={running} onChange={e => setBetween(Number(e.target.value))}>
                    <option value={0}>足さない</option>
                    {[1, 2].map(n => <option key={n} value={n}>コマのあいだに{n}枚(なめらか)</option>)}
                  </select>
                </label>
              </div>
              {motion && <p className="pub-meta">コマ: {motion.frames.map((f, i) => `${f || (whole ? '(タグを足さずに描き直す)' : '(元のまま)')} ${motion.delays[i]}ms`).join(' → ')}</p>}
              <button type="button" className="ugo-primary" disabled={running || !motion} onClick={() => void start()}>
                {running ? '作っています…' : 'うごイラを作る'}
              </button>
              {job && (
                <div className="ugo-row" role="status">
                  {running && <progress max={job.total} value={job.progress} />}
                  <span className="pub-meta">{job.message}</span>
                </div>
              )}
              <p className="pub-hint">
                {whole
                  ? '体ごとの動き(B): 元の絵から毎回描き直します(img2img)。手を振るなどの大きい動きが出ますが、服や細部が少し変わることがあります。体ごとのときは、背景は元の絵のままです。'
                  : '部分の動き(A): 頭の検出から目・口・髪の範囲を決め、そこだけを描き直します(範囲の外は元の絵のまま)。'}
                1MP 以内なので、Opus なら Anlas はかかりません。描き直した所の明るさ・色は、元の絵に合わせてから重ねます。
                中割りは、作ったあとにこの PC で足します(RIFE)。
              </p>

              <h3>変形で動かす(描き直さない)</h3>
              <p className="pub-hint">
                元の絵をそのまま少しずつ動かします(D)。線・色・トーンがどのコマでも同じなので、ちらつきません。NovelAI は使わず、
                この PC で作ります。呼吸・髪がなびく・首をかしげる・うなずく のような、小さく繰り返す動きに向きます。
              </p>
              <div className="ugo-row">
                <label>動き{' '}
                  <select value={warpKind} disabled={running} onChange={e => setWarpKind(e.target.value)}>
                    {warps.map(w => <option key={w.kind} value={w.kind}>{w.name}(1周 {w.period / 1000}秒)</option>)}
                  </select>
                </label>
                <label>大きさ{' '}
                  <select value={warpAmount} disabled={running} onChange={e => setWarpAmount(Number(e.target.value))}>
                    <option value={0.6}>小さく</option>
                    <option value={1}>ふつう</option>
                    <option value={1.5}>大きく</option>
                  </select>
                </label>
                <label>コマ数{' '}
                  <select value={warpFrames} disabled={running} onChange={e => setWarpFrames(Number(e.target.value))}>
                    {[12, 16, 24].map(n => <option key={n} value={n}>{n}</option>)}
                  </select>
                </label>
                <label>
                  <input type="checkbox" checked={keepBackground} disabled={running} onChange={e => setKeepBackground(e.target.checked)} />
                  {' '}背景は動かさない(人物だけを動かす)
                </label>
              </div>
              <button type="button" className="ugo-primary" disabled={running || warps.length === 0} onClick={() => void startWarp()}>
                {running ? '作っています…' : '変形でうごイラを作る'}
              </button>

              <h3>部位ごとに動かす(骨格とばね)</h3>
              <p className="pub-hint">
                人物・髪・頭を分けて、髪だけ・頭だけを動かします(E)。髪はばねでつないで、遅れて付いてくるように揺らします。
                動いて見えるようになった裏(髪の下の服など)は、この PC のモデルで埋めます。構図(寝ている・横顔・後ろ姿 など)は、
                絵のプロンプトから選びます。はじめての時は、モデル(合わせて約480MB)を取ってきます。
              </p>
              <div className="ugo-row">
                <label>動き{' '}
                  <select value={rigKind} disabled={running} onChange={e => setRigKind(e.target.value)}>
                    {rigs.map(r => <option key={r.kind} value={r.kind}>{r.name}(1周 {r.period / 1000}秒)</option>)}
                  </select>
                </label>
                {Object.entries(rigStyles).map(([value, label]) => (
                  <label key={value}>
                    <input type="radio" name="ugo-rig-style" checked={rigStyle === value} disabled={running} onChange={() => setRigStyle(value)} />
                    {' '}{label}
                  </label>
                ))}
                <label>大きさ{' '}
                  <select value={rigAmount} disabled={running} onChange={e => setRigAmount(Number(e.target.value))}>
                    <option value={0.6}>小さく</option>
                    <option value={1}>ふつう</option>
                    <option value={1.5}>大きく</option>
                  </select>
                </label>
              </div>
              <div className="ugo-row">
                <label>
                  <input type="checkbox" checked={rigNovelai} disabled={running} onChange={e => setRigNovelai(e.target.checked)} />
                  {' '}髪の裏を NovelAI で描く(頭を除いた胴体だけを描き直す。Opus なら Anlas 0。髪が残ったら、この PC の埋め方に戻す)
                </label>
              </div>
              <p className="pub-meta">
                アニメ調: 髪の束ごとに大きく揺れ、2コマずつ止めてメリハリを付けます。リアル寄り: 細かい毛筋ごとに波が流れ、
                毎コマなめらかに動き、少しぶれます。
              </p>
              <button type="button" className="ugo-primary" disabled={running || rigs.length === 0} onClick={() => void startRig()}>
                {running ? '作っています…' : '部位ごとにうごイラを作る'}
              </button>

              <h3>動画モデルで動かす(リモートの GPU)</h3>
              <p className="pub-hint">
                Colab や GCP の GPU で動かす ComfyUI に、動画モデル(Wan 2.2)で元の絵から動画を作らせます(F)。体・髪・表情が
                いっしょに、なめらかに動きます。代わりに、細部は元の絵から少し変わることがあります。NovelAI は使いません。
                5秒で 10〜20 分ほどかかります(L4)。接続のしかたは docs/video_remote_jp.md にあります。
              </p>
              <div className="ugo-row" role="status">
                <span className="pub-meta">
                  接続先: {videoStatus ? videoStatus.message : '確かめています…'}
                  {videoStatus?.vram_free_gb != null && `・空きメモリ ${videoStatus.vram_free_gb}GB`}
                </span>
                <button type="button" disabled={running} onClick={loadVideoStatus}>確かめ直す</button>
              </div>
              <label className="ugo-block">動き(英語の文が効きます){' '}
                <textarea rows={2} value={videoMotion} disabled={running} maxLength={500} onChange={e => setVideoMotion(e.target.value)} />
              </label>
              <div className="ugo-row">
                <label>
                  <input type="radio" name="ugo-video-kind" checked={videoKind === 'loop'} disabled={running} onChange={() => setVideoKind('loop')} />
                  {' '}繰り返す(最後に元の絵へ戻る。うごイラ向き)
                </label>
                <label>
                  <input type="radio" name="ugo-video-kind" checked={videoKind === 'i2v'} disabled={running} onChange={() => setVideoKind('i2v')} />
                  {' '}自由に動かす(戻らない)
                </label>
                <label>長さ{' '}
                  <select value={videoSeconds} disabled={running} onChange={e => setVideoSeconds(Number(e.target.value))}>
                    {[2, 3, 5].map(n => <option key={n} value={n}>{n}秒({n * 16 + 1}コマ)</option>)}
                  </select>
                </label>
                <label>中割り{' '}
                  <select value={videoBetween} disabled={running} onChange={e => setVideoBetween(Number(e.target.value))}>
                    <option value={0}>足さない(16fps)</option>
                    <option value={1}>あいだに1枚(32fps・この PC で)</option>
                  </select>
                </label>
                <label title="明るさを最初のコマにそろえ、繰り返しの継ぎ目を中割りで閉じ、4コマごとのちらつきをならします(この PC で)">
                  <input type="checkbox" checked={videoSmooth} disabled={running} onChange={e => setVideoSmooth(e.target.checked)} />
                  {' '}コマを整える(継ぎ目・ちらつき)
                </label>
              </div>
              <button
                type="button"
                className="ugo-primary"
                disabled={running || !videoStatus?.reachable || !videoMotion.trim()}
                onClick={() => void startVideo()}
              >
                {running ? '作っています…' : '動画モデルでうごイラを作る'}
              </button>
            </>
          )}
        </section>

        {project && (
          <section className="ugo-section">
            <h2>{project.name}</h2>
            <div className="ugo-editor">
              <Preview frames={frames} />
              <div className="ugo-frames">
                {frames.map((frame, index) => (
                  <div key={frame.path} className="ugo-frame">
                    <img src={fileUrl(frame.path)} alt={`コマ ${index + 1}`} loading="lazy" />
                    <div className="ugo-row">
                      <span className="pub-meta">{index + 1}. {frame.tags || '(元のまま)'}</span>
                    </div>
                    <div className="ugo-row">
                      <input type="number" min={20} max={10000} step={10} value={frame.delay} aria-label={`コマ ${index + 1} の表示時間`}
                        onChange={e => setFrames(prev => prev.map((f, i) => (i === index ? { ...f, delay: Number(e.target.value) } : f)))} />ms
                      <button type="button" onClick={() => move(index, -1)} disabled={index === 0} aria-label="前へ">↑</button>
                      <button type="button" onClick={() => move(index, 1)} disabled={index === frames.length - 1} aria-label="後ろへ">↓</button>
                      <button type="button" onClick={() => setFrames(prev => [...prev.slice(0, index + 1), { ...frame }, ...prev.slice(index + 1)])}
                        title="同じコマを、すぐ後ろにもう1枚入れます">複製</button>
                      <button type="button" disabled={frames.length <= 1} onClick={() => setFrames(prev => prev.filter((_, i) => i !== index))}>外す</button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <div className="ugo-row">
              <button type="button" className="ugo-primary" disabled={!dirty} onClick={() => void saveFrames()}>並び・表示時間を保存</button>
              {dirty && <button type="button" onClick={() => setFrames(project.frames)}>元に戻す</button>}
            </div>
            {project.method === 'rig' && (
              <UgoiraRigEditor key={project.id} projectId={project.id} running={running}
                onRedo={id => { setJob({ id, status: 'running', message: '作り直しています', progress: 0, total: 4, project_id: project.id }); follow(id) }} />
            )}
            <h3>中割り(コマのあいだを補う)</h3>
            <p className="pub-meta">
              コマとコマのあいだの絵を、この PC で作って足します(フレーム補間のモデル RIFE。NovelAI は使いません)。
              動きがなめらかになります。元のうごイラは残し、中割りを足したうごイラを新しく作ります。
            </p>
            <div className="ugo-row">
              <label>あいだに{' '}
                <select value={interpCount} disabled={running} onChange={e => setInterpCount(Number(e.target.value))}>
                  {[1, 2, 3].map(n => <option key={n} value={n}>{n}枚</option>)}
                </select>
              </label>
              <label><input type="checkbox" checked={interpLoop} disabled={running} onChange={e => setInterpLoop(e.target.checked)} /> 最後から最初へ戻るところにも</label>
              <button type="button" disabled={running || dirty || frames.length < 2} onClick={() => void interpolate()}>
                {running ? '作っています…' : '中割りを足す'}
              </button>
              {dirty && <span className="pub-meta">先に保存してください</span>}
            </div>
            {job && project && (
              <div className="ugo-row" role="status">
                {running && <progress max={job.total} value={job.progress} />}
                <span className="pub-meta">{job.message}</span>
              </div>
            )}

            <h3>書き出す</h3>
            <div className="ugo-row">
              <label>長い辺{' '}
                <select value={maxSide} onChange={e => setMaxSide(e.target.value)}>
                  <option value="">元のまま</option>
                  <option value="1200">1200px</option>
                  <option value="800">800px</option>
                  <option value="600">600px</option>
                </select>
              </label>
              <button type="button" disabled={dirty} onClick={() => void download('zip')} title="Pixiv のうごイラの投稿に使う ZIP(コマの画像を順番どおりに。表示時間は投稿の画面で決めます)">Pixiv 用 ZIP</button>
              <button type="button" disabled={dirty} onClick={() => void download('gif')}>GIF</button>
              <button type="button" disabled={dirty} onClick={() => void download('webp')}>アニメ WebP</button>
              {dirty && <span className="pub-meta">先に保存してください</span>}
            </div>
            <button type="button" className="ugo-danger" onClick={() => void remove()}>このうごイラをごみ箱へ</button>
          </section>
        )}

        <section className="ugo-section">
          <h2>作ったうごイラ</h2>
          {projects.length === 0 && <p className="pub-hint">まだありません。</p>}
          <ul className="ugo-list">
            {projects.map(p => (
              <li key={p.id}>
                <button type="button" onClick={() => void open(p.id)} className={project?.id === p.id ? 'is-open' : undefined}>
                  {p.frames[0] && <img src={fileUrl(p.frames[0].path)} alt="" loading="lazy" />}
                  <span>{p.name}</span>
                  <span className="pub-meta">{p.frames.length}コマ</span>
                </button>
              </li>
            ))}
          </ul>
        </section>

        <details className="ugo-section">
          <summary>動きの管理(足す・直す・消す)</summary>
          <p className="pub-hint">コマは1行に1つ、「タグ ; 表示時間(ミリ秒)」の形です。タグが空の行は元の絵のままのコマになります。</p>
          <ul className="ugo-motions">
            {motions.map(m => (
              <li key={m.id}>
                <strong>{m.name}</strong> <span className="pub-meta">{regions[m.region]}・{m.frames.length}コマ</span>{' '}
                <button type="button" onClick={() => editMotion(m)}>直す</button>
                <button type="button" onClick={() => void deleteMotion(m)}>消す</button>
              </li>
            ))}
          </ul>
          <button type="button" onClick={() => editMotion(null)}>動きを足す</button>
          {editing && (
            <div className="ugo-motion-form">
              <input value={draft.name} placeholder="名前(例: ウインク)" aria-label="動きの名前" onChange={e => setDraft({ ...draft, name: e.target.value })} />
              <select value={draft.region} aria-label="描き直す範囲" onChange={e => setDraft({ ...draft, region: e.target.value })}>
                {Object.entries(regions).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
              <textarea rows={6} value={draft.lines} aria-label="コマ(タグ ; ミリ秒)" spellCheck={false}
                onChange={e => setDraft({ ...draft, lines: e.target.value })} />
              <div className="ugo-row">
                <button type="button" className="ugo-primary" onClick={() => void saveMotion()}>保存</button>
                <button type="button" onClick={() => setEditing(null)}>やめる</button>
              </div>
            </div>
          )}
        </details>
      </main>
    </div>
  )
}
