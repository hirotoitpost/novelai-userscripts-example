import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { API_ORIGIN, formatDate, getJson, thumbUrl } from '../library'
import { takeXPostDraft, weightedLength, X_MAX_WEIGHTED } from '../xpost'
import './XPosts.css'

/**
 * X への投稿。ギャラリー・本棚で選んだ画像を、1件(4枚まで)またはスレッド(4枚ずつ)にして、今すぐ・予約で投稿する。
 * 下の一覧で、予約中・投稿済み・失敗を確かめる(取り消し・やり直し・X から削除・反応の数)。
 * 投稿する画像からは NovelAI の生成情報を取り除き、見えない透かしを入れる(watermark_registry.py)。成人向けの画像があれば、センシティブ指定にする(外せない)。
 */

interface ImageRef {
  ref: string
  path: string
  label: string
  adult: boolean
}

interface Part {
  text: string
  images: ImageRef[]
}

type Status = 'scheduled' | 'posting' | 'posted' | 'failed' | 'cancelled' | 'missed' | 'deleted'

interface Metrics {
  like_count?: number
  retweet_count?: number
  reply_count?: number
  quote_count?: number
  bookmark_count?: number
  impression_count?: number
}

interface XPost {
  id: number
  status: Status
  title: string
  parts: Part[]
  sensitive: boolean
  scheduled_at: string | null
  created_at: string
  posted_at: string | null
  tweet_ids: string[]
  urls: string[]
  username: string | null
  error: string | null
  metrics: { total: Metrics; parts: (Metrics | null)[] } | null
  metrics_at: string | null
  warnings?: string[]
}

interface CheckResult {
  ok: boolean
  reasons: string[]
  adult: boolean
  warnings: string[]
}

const STATUS_LABEL: Record<Status, string> = {
  scheduled: '予約中',
  posting: '投稿中',
  posted: '投稿済み',
  failed: '失敗',
  cancelled: '取り消し',
  missed: '見送り',
  deleted: 'X から削除済み',
}

async function send<T>(path: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_ORIGIN}${path}`, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text()
    let message = text || res.statusText
    try {
      const data = JSON.parse(text)
      if (typeof data.detail === 'string') message = data.detail
      else if (data.detail?.reasons) message = [data.detail.message, ...data.detail.reasons].join('\n')
    } catch {
      // 本文が JSON でなければそのまま
    }
    throw new Error(message)
  }
  return res.status === 204 ? (undefined as T) : res.json()
}

function isVideoRef(image: ImageRef): boolean {
  return /\.(mp4|mov|webm)$/i.test(image.path)
}

/** 投稿ごとに分ける。動画は X の決まりで1件に1本だけなので、それだけで1件にする */
function groupPool(items: ImageRef[], size: number): ImageRef[][] {
  const groups: ImageRef[][] = []
  let current: ImageRef[] = []
  for (const item of items) {
    if (isVideoRef(item)) {
      if (current.length) groups.push(current)
      groups.push([item])
      current = []
      continue
    }
    current.push(item)
    if (current.length === size) {
      groups.push(current)
      current = []
    }
  }
  if (current.length) groups.push(current)
  return groups
}

/** datetime-local の初期値(今から1時間後、分は 0) */
function defaultWhen(): string {
  const d = new Date(Date.now() + 60 * 60 * 1000)
  d.setMinutes(0, 0, 0)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function toLocalInput(iso: string): string {
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function metricsText(m: Metrics): string {
  const items: [string, number | undefined][] = [
    ['表示', m.impression_count], ['いいね', m.like_count], ['リポスト', m.retweet_count],
    ['返信', m.reply_count], ['引用', m.quote_count], ['ブックマーク', m.bookmark_count],
  ]
  return items.filter(([, v]) => v !== undefined).map(([k, v]) => `${k} ${v}`).join(' ・ ')
}

export default function XPosts() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [posts, setPosts] = useState<XPost[]>([])
  const [configured, setConfigured] = useState<boolean | null>(null)
  const [account, setAccount] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<number | 'compose' | 'account' | null>(null)

  // 作る(書き換える)投稿
  const [composing, setComposing] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [title, setTitle] = useState('')
  const [pool, setPool] = useState<ImageRef[]>([])
  const [perPost, setPerPost] = useState(4)
  const [texts, setTexts] = useState<string[]>([''])
  const [sensitive, setSensitive] = useState(false)
  const [mode, setMode] = useState<'now' | 'schedule'>('now')
  const [when, setWhen] = useState(defaultWhen)
  const [check, setCheck] = useState<CheckResult | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setPosts(await getJson<XPost[]>('/api/x/posts'))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    void load()
    getJson<{ configured: boolean }>('/api/x/status')
      .then(s => setConfigured(s.configured))
      .catch(e => setError(e instanceof Error ? e.message : String(e)))
  }, [load])

  // 予約中・投稿中があれば、10 秒ごとに読み直す
  const active = posts.some(p => p.status === 'scheduled' || p.status === 'posting')
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => void load(), 10000)
    return () => window.clearInterval(timer)
  }, [active, load])

  // ギャラリー・本棚から渡された画像
  useEffect(() => {
    if (params.get('new') !== '1') return
    const refs = takeXPostDraft()
    setParams({}, { replace: true })
    resetComposer()
    setComposing(true)
    if (refs.length === 0) return
    send<ImageRef[]>('/api/x/resolve', 'POST', { refs })
      .then(found => {
        setPool(found)
        setSensitive(found.some(i => i.adult))
        // 漫画のページ(本)は1件に1ページずつを既定にする(4枚並ぶと小さくて読めないので)
        setPerPost(found.length > 0 && found.every(i => i.ref.startsWith('book:')) ? 1 : 4)
      })
      .catch(e => setError(e instanceof Error ? e.message : String(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params])

  const groups = useMemo(() => (pool.length ? groupPool(pool, perPost) : [[]]), [pool, perPost])
  const partTexts = groups.map((_, i) => texts[i] ?? '')
  const adult = pool.some(i => i.adult) || !!check?.adult

  function resetComposer() {
    setEditingId(null)
    setTitle('')
    setPool([])
    setPerPost(4)
    setTexts([''])
    setSensitive(false)
    setMode('now')
    setWhen(defaultWhen())
    setCheck(null)
  }

  function setText(index: number, value: string) {
    setCheck(null)
    setTexts(prev => {
      const next = [...prev]
      while (next.length <= index) next.push('')
      next[index] = value
      return next
    })
  }

  function addNumbers() {
    const total = groups.length
    setTexts(groups.map((_, i) => {
      const base = (texts[i] ?? '').replace(/\s*\(\d+\/\d+\)\s*$/, '')
      return `${base}${base ? ' ' : ''}(${i + 1}/${total})`
    }))
  }

  function move(index: number, delta: number) {
    setCheck(null)
    setPool(prev => {
      const next = [...prev]
      const target = index + delta
      if (target < 0 || target >= next.length) return prev
      ;[next[index], next[target]] = [next[target], next[index]]
      return next
    })
  }

  function removeImage(index: number) {
    setCheck(null)
    setPool(prev => prev.filter((_, i) => i !== index))
  }

  function body() {
    return {
      title,
      parts: groups.map((images, i) => ({ text: partTexts[i], refs: images.map(img => img.ref) })),
      sensitive: sensitive || adult,
      scheduled_at: mode === 'schedule' ? new Date(when).toISOString() : null,
    }
  }

  async function runCheck(): Promise<CheckResult | null> {
    setBusy('compose')
    setError(null)
    try {
      const result = await send<CheckResult>('/api/x/check', 'POST', body())
      setCheck(result)
      if (result.adult) setSensitive(true)
      return result
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return null
    } finally {
      setBusy(null)
    }
  }

  async function submit() {
    const result = await runCheck()
    if (!result?.ok) return
    const count = groups.length
    const whenText = mode === 'schedule' ? `${new Date(when).toLocaleString()} に予約` : '今すぐ投稿'
    const summary = `${count > 1 ? `${count} 件のスレッド` : '1 件の投稿'}(画像 ${pool.length} 枚${sensitive || result.adult ? '・センシティブ指定' : ''})を${whenText}します。よろしいですか?`
    if (!window.confirm(summary)) return
    setBusy('compose')
    setError(null)
    try {
      const saved = editingId === null
        ? await send<XPost>('/api/x/posts', 'POST', body())
        : await send<XPost>(`/api/x/posts/${editingId}`, 'PUT', body())
      setNotice(mode === 'schedule' ? '予約しました。時刻になるとバックエンドが投稿します(バックエンドを起動しておいてください)。' : '投稿を始めました。下の一覧で結果を確かめてください。')
      if (saved.warnings?.length) setNotice(n => `${n}\n注意: ${saved.warnings!.join(' / ')}`)
      resetComposer()
      setComposing(false)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  function edit(post: XPost) {
    const images = post.parts.flatMap(p => p.images)
    const size = Math.max(1, ...post.parts.map(p => p.images.length))
    setEditingId(post.id)
    setTitle(post.title)
    setPool(images)
    setPerPost(Math.min(4, size))
    setTexts(post.parts.map(p => p.text))
    setSensitive(post.sensitive)
    if (post.scheduled_at && new Date(post.scheduled_at).getTime() > Date.now()) {
      setMode('schedule')
      setWhen(toLocalInput(post.scheduled_at))
    } else {
      setMode('now')
    }
    setCheck(null)
    setComposing(true)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  async function act(post: XPost, action: 'cancel' | 'retry' | 'delete-remote' | 'remove' | 'metrics') {
    const confirmText: Partial<Record<typeof action, string>> = {
      'delete-remote': `X から投稿${post.tweet_ids.length > 1 ? `(スレッド ${post.tweet_ids.length} 件)` : ''}を消します。元に戻せません。よろしいですか?`,
      remove: 'この記録を消します(X の投稿は消えません)。よろしいですか?',
      retry: post.tweet_ids.length ? `続き(${post.tweet_ids.length + 1} 件目から)を今すぐ投稿します。よろしいですか?` : '今すぐ投稿し直します。よろしいですか?',
    }
    if (confirmText[action] && !window.confirm(confirmText[action])) return
    setBusy(post.id)
    setError(null)
    try {
      if (action === 'cancel') await send(`/api/x/posts/${post.id}/cancel`, 'POST')
      else if (action === 'retry') await send(`/api/x/posts/${post.id}/retry`, 'POST', {})
      else if (action === 'delete-remote') await send(`/api/x/posts/${post.id}/delete-remote`, 'POST')
      else if (action === 'remove') await send(`/api/x/posts/${post.id}`, 'DELETE')
      else await send('/api/x/metrics', 'POST', { ids: [post.id] })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function checkAccount() {
    setBusy('account')
    setError(null)
    try {
      const me = await send<{ name: string; username: string }>('/api/x/account', 'POST')
      setAccount(`${me.name}(@${me.username})`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const overLimit = partTexts.some(t => weightedLength(t) > X_MAX_WEIGHTED)

  return (
    <div className="xp-root">
      <header className="xp-header">
        <button type="button" className="xp-link" onClick={() => navigate('/')}>← ホーム</button>
        <h1>X 投稿</h1>
        <span className="xp-account">
          {account ?? (configured === false ? 'X のキーが .env にありません' : '')}
        </span>
        <button type="button" className="xp-link" onClick={() => void checkAccount()} disabled={!configured || busy === 'account'}
          title="つながっている X のアカウントを確かめます(X の読み取りを1回使います)">
          アカウントを確認
        </button>
        <button type="button" className="xp-link" onClick={() => navigate('/gallery')}>ギャラリーへ</button>
        <button type="button" className="xp-link" onClick={() => navigate('/bookshelf')}>本棚へ</button>
        {!composing && (
          <button type="button" className="xp-primary" onClick={() => { resetComposer(); setComposing(true) }} disabled={!configured}>
            ＋ 新しい投稿
          </button>
        )}
      </header>

      <main className="xp-main">
        {error && <p className="xp-error" role="alert">{error}</p>}
        {notice && <p className="xp-notice" role="status" onClick={() => setNotice(null)}>{notice}</p>}

        {composing && (
          <section className="xp-composer" aria-label="投稿を作る">
            <div className="xp-row">
              <h2>{editingId === null ? '新しい投稿' : '予約を書き換える'}</h2>
              <input className="xp-title" placeholder="メモ(一覧での名前。X には出ません)" value={title}
                onChange={e => setTitle(e.target.value)} maxLength={120} />
            </div>

            {pool.length === 0 ? (
              <p className="xp-hint">
                画像はギャラリー(選択 → 「X に投稿」)か、本棚の本(「X に投稿」)から選んでください。本文だけの投稿もできます。
              </p>
            ) : (
              <div className="xp-row">
                <label>
                  1件の画像
                  <select value={perPost} onChange={e => { setPerPost(Number(e.target.value)); setCheck(null) }}>
                    {[1, 2, 3, 4].map(n => <option key={n} value={n}>{n} 枚</option>)}
                  </select>
                </label>
                <span className="xp-hint">
                  {pool.some(isVideoRef) && '動画は1本で1件になります。'}
                  画像 {pool.length} 枚 → {groups.length > 1 ? `${groups.length} 件のスレッド(2件目からは前の投稿への返信)` : '1件'}
                </span>
                {groups.length > 1 && <button type="button" className="xp-link" onClick={addNumbers}>(1/N) の番号を付ける</button>}
              </div>
            )}

            <ol className="xp-parts">
              {groups.map((images, partIndex) => {
                const offset = groups.slice(0, partIndex).reduce((n, g) => n + g.length, 0)
                const length = weightedLength(partTexts[partIndex])
                return (
                  <li key={partIndex} className="xp-part">
                    <textarea
                      value={partTexts[partIndex]}
                      onChange={e => setText(partIndex, e.target.value)}
                      placeholder={partIndex === 0 ? '本文(ハッシュタグも入れられます)' : '続きの本文(空でもかまいません)'}
                      rows={3}
                    />
                    <span className={`xp-count${length > X_MAX_WEIGHTED ? ' is-over' : ''}`}>{length} / {X_MAX_WEIGHTED}</span>
                    {images.length > 0 && (
                      <div className="xp-images">
                        {images.map((image, i) => (
                          <figure key={image.ref} className="xp-image">
                            <img src={thumbUrl(image.path, 240)} alt={image.label} loading="lazy" />
                            <figcaption>
                              {isVideoRef(image) && <span className="xp-video">▶ 動画</span>}
                              {image.adult && <span className="xp-adult">R18</span>}
                              <button type="button" onClick={() => move(offset + i, -1)} disabled={offset + i === 0} aria-label="前へ">←</button>
                              <button type="button" onClick={() => move(offset + i, 1)} disabled={offset + i === pool.length - 1} aria-label="後ろへ">→</button>
                              <button type="button" onClick={() => removeImage(offset + i)} aria-label="外す">×</button>
                            </figcaption>
                          </figure>
                        ))}
                      </div>
                    )}
                  </li>
                )
              })}
            </ol>

            <div className="xp-row">
              <label className="xp-check">
                <input type="checkbox" checked={sensitive || adult} disabled={adult}
                  onChange={e => setSensitive(e.target.checked)} />
                センシティブな内容として投稿する
                {adult && <small>(成人向けの画像があるので外せません)</small>}
              </label>
            </div>
            <p className="xp-meta">画像には見えない透かし(番号)が自動で入り、「見えない透かし」の台帳に残ります。</p>
            <div className="xp-row">
              <label className="xp-check">
                <input type="radio" checked={mode === 'now'} onChange={() => setMode('now')} /> 今すぐ投稿
              </label>
              <label className="xp-check">
                <input type="radio" checked={mode === 'schedule'} onChange={() => setMode('schedule')} /> 予約
              </label>
              {mode === 'schedule' && (
                <input type="datetime-local" value={when} onChange={e => setWhen(e.target.value)} />
              )}
            </div>
            {mode === 'schedule' && (
              <p className="xp-hint">予約の時刻にバックエンドが起動している必要があります。6 時間以上遅れたものは投稿せずに「見送り」にします。</p>
            )}

            {check && !check.ok && (
              <div className="xp-error" role="alert">
                投稿できません:
                <ul>{check.reasons.map(r => <li key={r}>{r}</li>)}</ul>
              </div>
            )}
            {check?.ok && check.warnings.length > 0 && (
              <p className="xp-warn">注意: {check.warnings.join(' / ')}</p>
            )}

            <div className="xp-actions">
              <button type="button" className="xp-link" onClick={() => void runCheck()} disabled={busy === 'compose'}>確認</button>
              <button type="button" className="xp-primary" onClick={() => void submit()}
                disabled={busy === 'compose' || overLimit || !configured || (pool.length === 0 && !partTexts.some(t => t.trim()))}>
                {mode === 'schedule' ? '予約する' : '投稿する'}
              </button>
              <button type="button" className="xp-link" onClick={() => { resetComposer(); setComposing(false) }}>やめる</button>
            </div>
          </section>
        )}

        <h2 className="xp-list-title">投稿の記録({posts.length} 件)</h2>
        {posts.length === 0 && <p className="xp-hint">まだありません。</p>}
        <ul className="xp-list">
          {posts.map(post => {
            const images = post.parts.flatMap(p => p.images)
            return (
              <li key={post.id} className={`xp-item is-${post.status}`}>
                <div className="xp-item-head">
                  <span className={`xp-status is-${post.status}`}>{STATUS_LABEL[post.status]}</span>
                  <strong>{post.title || post.parts[0]?.text.slice(0, 40) || `投稿 ${post.id}`}</strong>
                  {post.sensitive && <span className="xp-adult">センシティブ</span>}
                  <span className="xp-meta">
                    {post.status === 'scheduled' && post.scheduled_at ? `予約 ${formatDate(post.scheduled_at)}` : null}
                    {post.posted_at ? `投稿 ${formatDate(post.posted_at)}` : null}
                    {!post.posted_at && post.status !== 'scheduled' ? `作成 ${formatDate(post.created_at)}` : null}
                    {post.parts.length > 1 ? ` ・ スレッド ${post.parts.length} 件` : ''}
                    {images.length ? ` ・ 画像 ${images.length} 枚` : ''}
                  </span>
                </div>
                {post.parts[0]?.text && <p className="xp-text">{post.parts[0].text}</p>}
                {images.length > 0 && (
                  <div className="xp-thumbs">
                    {images.slice(0, 8).map(image => (
                      <img key={image.ref} src={thumbUrl(image.path, 240)} alt={image.label} loading="lazy" />
                    ))}
                    {images.length > 8 && <span className="xp-meta">ほか {images.length - 8} 枚</span>}
                  </div>
                )}
                {post.error && <p className="xp-error">{post.error}</p>}
                {post.urls.length > 0 && post.status !== 'deleted' && (
                  <p className="xp-urls">
                    {post.urls.map((url, i) => (
                      <a key={url} href={url} target="_blank" rel="noreferrer">{post.urls.length > 1 ? `${i + 1}件目` : 'X で見る'}</a>
                    ))}
                  </p>
                )}
                {post.metrics && (
                  <p className="xp-meta">反応: {metricsText(post.metrics.total)}({post.metrics_at ? formatDate(post.metrics_at) : ''} 時点)</p>
                )}
                <div className="xp-actions">
                  {post.status === 'scheduled' && (
                    <>
                      <button type="button" onClick={() => edit(post)} disabled={busy === post.id}>書き換える</button>
                      <button type="button" onClick={() => void act(post, 'cancel')} disabled={busy === post.id}>予約を取り消す</button>
                    </>
                  )}
                  {(post.status === 'failed' || post.status === 'missed' || post.status === 'cancelled') && (
                    <>
                      <button type="button" className="xp-primary" onClick={() => void act(post, 'retry')} disabled={busy === post.id}>
                        {post.tweet_ids.length ? '続きを投稿' : '今すぐ投稿'}
                      </button>
                      {post.tweet_ids.length === 0 && (
                        <button type="button" onClick={() => edit(post)} disabled={busy === post.id}>書き換えて予約</button>
                      )}
                    </>
                  )}
                  {post.status === 'posted' && (
                    <button type="button" onClick={() => void act(post, 'metrics')} disabled={busy === post.id}
                      title="いいね・リポストなどの数を X から読みます(X の読み取りを使います)">
                      反応を見る
                    </button>
                  )}
                  {post.tweet_ids.length > 0 && post.status !== 'deleted' && post.status !== 'posting' && (
                    <button type="button" className="xp-danger" onClick={() => void act(post, 'delete-remote')} disabled={busy === post.id}>
                      X から削除
                    </button>
                  )}
                  {post.status !== 'posting' && post.status !== 'scheduled' && (
                    <button type="button" onClick={() => void act(post, 'remove')} disabled={busy === post.id}>記録を消す</button>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      </main>
    </div>
  )
}
