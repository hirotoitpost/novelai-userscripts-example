import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import ImagePreview from '../components/ImagePreview'
import { API_ORIGIN, fileUrl, formatDate, getJson, readErrorDetail, thumbUrl } from '../library'
import './Publications.css'
import './CharacterImages.css'

/**
 * キャラごとの生成した画像(参照画像の候補・参照画像・漫画のコマ・描き直す前のコマ・データセット・顔と表情のシート・素体・
 * 生成履歴)を一覧で見て、参照画像に選び直す。下の「古い画像の整理」で、候補・使われていない参照画像・描き直す前のコマ・
 * 生成履歴をごみ箱へ移す(30日のあいだは元に戻せる)。API は /api/character-images(character_images.py)。
 */

interface Kind {
  key: string
  label: string
}

interface Item {
  kind: string
  path: string
  bytes: number
  created_at: string
  seed: number | null
  in_use: boolean
  note: string
}

interface Character {
  id: number
  name: string
  reference_image_path?: string | null
}

interface ImagesResponse {
  kinds: Kind[]
  cleanable: string[]
  reference_image_path: string | null
  items: Item[]
}

/** 一覧に一度に出す枚数(多いキャラは「もっと見る」で足す) */
const PAGE = 120
const DAY_CHOICES = [0, 7, 14, 30, 90]

function bytes(n: number): string {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(2)} GB`
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${Math.max(1, Math.round(n / 1024))} KB`
}

async function postJson<T>(path: string, method: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_ORIGIN}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(await readErrorDetail(res))
  return res.json()
}

export default function CharacterImages() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const characterId = Number(params.get('character')) || null
  const [characters, setCharacters] = useState<Character[]>([])
  const [data, setData] = useState<ImagesResponse | null>(null)
  const [kindFilter, setKindFilter] = useState<string>('all')
  const [shown, setShown] = useState(PAGE)
  const [preview, setPreview] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // 整理
  const [cleanKinds, setCleanKinds] = useState<string[]>(['candidate'])
  const [days, setDays] = useState(30)
  const [scope, setScope] = useState<'character' | 'all'>('character')
  const [targets, setTargets] = useState<Item[] | null>(null)
  const [chosen, setChosen] = useState<Set<string>>(new Set())

  useEffect(() => {
    getJson<Character[]>('/api/story/characters')
      .then(list => setCharacters(list))
      .catch(e => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  const load = useCallback(async () => {
    if (characterId === null) {
      setData(null)
      return
    }
    try {
      setData(await getJson<ImagesResponse>(`/api/character-images/${characterId}`))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [characterId])

  useEffect(() => {
    setKindFilter('all')
    setShown(PAGE)
    setTargets(null)
    setNotice(null)
    void load()
  }, [load])

  useEffect(() => {
    // キャラを選んでいなければ、整理は全体を対象にする
    if (characterId === null) setScope('all')
  }, [characterId])

  const kindLabel = useMemo(
    () => Object.fromEntries((data?.kinds ?? []).map(k => [k.key, k.label])) as Record<string, string>,
    [data],
  )
  const counts = useMemo(() => {
    const result: Record<string, number> = {}
    for (const item of data?.items ?? []) result[item.kind] = (result[item.kind] ?? 0) + 1
    return result
  }, [data])
  const items = useMemo(
    () => (data?.items ?? []).filter(i => kindFilter === 'all' || i.kind === kindFilter),
    [data, kindFilter],
  )
  const visible = items.slice(0, shown)

  async function chooseReference(item: Item) {
    if (characterId === null) return
    setBusy(true)
    setError(null)
    try {
      await postJson(`/api/character-images/${characterId}/reference`, 'PUT', { path: item.path })
      setNotice(item.kind === 'candidate'
        ? 'この絵を参照画像にしました(候補のシードを基準シードにもしました)。'
        : 'この絵を参照画像にしました。')
      setPreview(null)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  function cleanupBody(paths?: string[]) {
    return {
      kinds: cleanKinds,
      older_than_days: days,
      character_id: scope === 'character' ? characterId : null,
      ...(paths ? { paths } : {}),
    }
  }

  async function findTargets() {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const result = await postJson<{ items: Item[] }>('/api/character-images/cleanup/preview', 'POST', cleanupBody())
      setTargets(result.items)
      setChosen(new Set(result.items.map(i => i.path)))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function runCleanup() {
    const paths = (targets ?? []).filter(t => chosen.has(t.path)).map(t => t.path)
    if (paths.length === 0) return
    if (!window.confirm(`選んだ ${paths.length} 枚をごみ箱へ移します(30日のあいだは、ごみ箱から元に戻せます)。よろしいですか?`)) return
    setBusy(true)
    setError(null)
    try {
      const result = await postJson<{ deleted: number; bytes: number }>('/api/character-images/cleanup', 'POST', cleanupBody(paths))
      setNotice(`${result.deleted} 枚(${bytes(result.bytes)})をごみ箱へ移しました。`)
      setTargets(null)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const chosenBytes = (targets ?? []).filter(t => chosen.has(t.path)).reduce((sum, t) => sum + t.bytes, 0)
  const cleanable = data?.cleanable ?? ['candidate', 'reference', 'old_panel', 'history']
  const character = characters.find(c => c.id === characterId)

  return (
    <div className="pub-root">
      <header className="pub-header">
        <button type="button" className="pub-link" onClick={() => navigate('/characters')}>← キャラ一覧</button>
        <h1>生成した画像</h1>
        <select
          className="cimg-select"
          value={characterId ?? ''}
          onChange={e => setParams(e.target.value ? { character: e.target.value } : {})}
        >
          <option value="">キャラを選ぶ</option>
          {characters.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        {data && <span className="pub-count">{data.items.length} 枚</span>}
        <button type="button" className="pub-link" onClick={() => navigate('/trash')}>ごみ箱へ</button>
      </header>

      <main className="pub-main">
        {error && <p className="pub-error" role="alert">{error}</p>}
        {notice && <p className="pub-hint" role="status">{notice}</p>}

        {data && (
          <section className="cimg-section">
            <div className="cimg-current">
              {data.reference_image_path
                ? <img src={thumbUrl(data.reference_image_path, 240)} alt={`${character?.name ?? ''}のいまの参照画像`} />
                : <span className="cimg-noref">参照画像なし</span>}
              <p className="pub-hint">
                押すと拡大します。拡大した画面の「参照画像にする」で、その絵を参照画像に選び直せます(候補は、そのシードを基準シードにもします)。
                「使用中」はいまの参照画像・いまのコマなどで、整理では片付けません。
              </p>
            </div>
            <div className="cimg-chips" role="group" aria-label="種類で絞り込む">
              <button type="button" className={kindFilter === 'all' ? 'cimg-chip active' : 'cimg-chip'} onClick={() => { setKindFilter('all'); setShown(PAGE) }}>
                すべて {data.items.length}
              </button>
              {data.kinds.filter(k => counts[k.key]).map(k => (
                <button type="button" key={k.key} className={kindFilter === k.key ? 'cimg-chip active' : 'cimg-chip'}
                  onClick={() => { setKindFilter(k.key); setShown(PAGE) }}>
                  {k.label} {counts[k.key]}
                </button>
              ))}
            </div>
            {items.length === 0 && <p className="pub-hint">このキャラの画像はまだありません。</p>}
            <div className="cimg-grid">
              {visible.map((item, index) => (
                <button type="button" key={item.path} className="cimg-tile" onClick={() => setPreview(index)}
                  title={`${kindLabel[item.kind] ?? item.kind} ・ ${formatDate(item.created_at)}${item.note ? ` ・ ${item.note}` : ''}`}>
                  <img src={thumbUrl(item.path, 240)} alt="" loading="lazy" />
                  <span className="cimg-badge">{kindLabel[item.kind] ?? item.kind}</span>
                  {(item.in_use || item.path === data.reference_image_path) && <span className="cimg-badge cimg-badge--use">使用中</span>}
                </button>
              ))}
            </div>
            {items.length > shown && (
              <div className="pub-actions">
                <button type="button" className="pub-link" onClick={() => setShown(n => n + PAGE)}>
                  もっと見る(残り {items.length - shown} 枚)
                </button>
              </div>
            )}
          </section>
        )}
        {characterId === null && <p className="pub-hint">上でキャラを選ぶと、そのキャラの画像が並びます。下の整理は、キャラを選ばなくても全体に使えます。</p>}

        <section className="cimg-section cimg-cleanup">
          <h2>古い画像の整理</h2>
          <p className="pub-hint">
            選んだ種類のうち、指定した日数より前の画像を探して、ごみ箱へ移します(30日のあいだは元に戻せます。ディスクが空くのは、ごみ箱から消したときです)。
            いまの参照画像・いまのコマ・ブックマークした生成履歴は片付けません。
          </p>
          <div className="cimg-chips">
            {cleanable.map(key => (
              <label key={key} className="cimg-check">
                <input
                  type="checkbox"
                  checked={cleanKinds.includes(key)}
                  onChange={e => {
                    setTargets(null)
                    setCleanKinds(prev => e.target.checked ? [...prev, key] : prev.filter(k => k !== key))
                  }}
                />
                {kindLabel[key] ?? key}
              </label>
            ))}
          </div>
          <div className="cimg-chips">
            <label className="cimg-check">
              <select value={days} onChange={e => { setTargets(null); setDays(Number(e.target.value)) }}>
                {DAY_CHOICES.map(d => <option key={d} value={d}>{d === 0 ? '日数を問わない' : `${d}日より前`}</option>)}
              </select>
            </label>
            <label className="cimg-check">
              <input type="radio" name="cimg-scope" checked={scope === 'character'} disabled={characterId === null}
                onChange={() => { setTargets(null); setScope('character') }} />
              このキャラの画像だけ
            </label>
            <label className="cimg-check">
              <input type="radio" name="cimg-scope" checked={scope === 'all'} onChange={() => { setTargets(null); setScope('all') }} />
              全キャラ・すべての生成履歴
            </label>
            <button type="button" className="pub-primary" disabled={busy || cleanKinds.length === 0} onClick={() => void findTargets()}>
              整理できる画像を探す
            </button>
          </div>
          {targets !== null && (
            <>
              <p className="pub-hint">
                {targets.length === 0
                  ? '整理できる画像はありません。'
                  : `${targets.length} 枚見つかりました。残したいものは押して選択を外してください(${chosen.size} 枚 ・ ${bytes(chosenBytes)} を選択中)。`}
              </p>
              {targets.length > 0 && (
                <div className="pub-actions">
                  <button type="button" className="pub-link" onClick={() => setChosen(new Set(targets.map(t => t.path)))}>すべて選ぶ</button>
                  <button type="button" className="pub-link" onClick={() => setChosen(new Set())}>選択を外す</button>
                  <button type="button" className="pub-danger" disabled={busy || chosen.size === 0} onClick={() => void runCleanup()}>
                    選んだ {chosen.size} 枚をごみ箱へ
                  </button>
                </div>
              )}
              <div className="cimg-grid">
                {targets.slice(0, 600).map(t => (
                  <button type="button" key={t.path}
                    className={chosen.has(t.path) ? 'cimg-tile cimg-tile--chosen' : 'cimg-tile cimg-tile--kept'}
                    onClick={() => setChosen(prev => {
                      const next = new Set(prev)
                      if (next.has(t.path)) next.delete(t.path)
                      else next.add(t.path)
                      return next
                    })}
                    title={`${kindLabel[t.kind] ?? t.kind} ・ ${formatDate(t.created_at)}${t.note ? ` ・ ${t.note}` : ''}`}>
                    <img src={thumbUrl(t.path, 240)} alt="" loading="lazy" />
                    <span className="cimg-badge">{kindLabel[t.kind] ?? t.kind}</span>
                    <span className="cimg-badge cimg-badge--date">{formatDate(t.created_at).split(' ')[0]}</span>
                  </button>
                ))}
              </div>
              {targets.length > 600 && <p className="pub-hint">見本は古いほうから 600 枚だけ並べています(選択はすべてに効きます)。</p>}
            </>
          )}
        </section>
      </main>

      <ImagePreview
        images={visible.map(i => ({
          src: fileUrl(i.path),
          alt: kindLabel[i.kind] ?? i.kind,
          caption: [kindLabel[i.kind] ?? i.kind, formatDate(i.created_at), i.seed !== null ? `シード ${i.seed}` : '', i.note]
            .filter(Boolean).join(' ・ '),
        }))}
        index={preview}
        onIndexChange={setPreview}
        actions={index => (
          <button
            type="button"
            className="ipv-btn ipv-btn--primary"
            disabled={busy || visible[index]?.path === data?.reference_image_path}
            onClick={() => void chooseReference(visible[index])}
          >
            {visible[index]?.path === data?.reference_image_path ? 'いまの参照画像' : '参照画像にする'}
          </button>
        )}
      />
    </div>
  )
}
