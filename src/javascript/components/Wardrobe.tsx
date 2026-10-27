import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fileUrl } from '../library'
import FullscreenViewer from './FullscreenViewer'
import './FaceSheet.css'
import './Wardrobe.css'

/**
 * キャラシートの衣装(着せ替え)。キャラに服を何着も登録し、1着を普段の衣装にする(キャラシートの普段の服装に入る)。
 * 服は部位ごとに部品・色・柄を選び、自由入力のタグを足して組み立てる。部品は衣装の設定のページで編集する。
 * 衣装は素体の Aポーズに着せて絵で確かめられる(素体を当てているキャラだけ。その絵は2Dモデルの服の層にも使える)。
 */

interface Part { id: number; slot: string; value: string; label: string; tags: string; colorable: number }
interface Kind { key: string; label: string }
interface OutfitPart { slot: string; value: string; color: string; pattern: string }
interface Outfit {
  id: number
  name: string
  parts: OutfitPart[]
  extra_tags: string
  tags: string
  dressed: { path: string; updated: number } | null
}
interface Outfits { outfit_id: number | null; outfit_tags: string; outfits: Outfit[] }
interface Draft { id: number | null; name: string; parts: OutfitPart[]; extra_tags: string }

interface Props {
  characterId: number
  token: string | null
  rootName: string
  disabled?: boolean
  /** 普段の衣装を変えたとき(キャラシートの普段の服装が変わる)に呼ぶ */
  onChanged?: () => void
}

const EMPTY_PART: OutfitPart = { slot: 'top', value: '', color: '', pattern: '' }

async function detail(res: Response): Promise<string> {
  const data = await res.json().catch(() => null)
  return typeof data?.detail === 'string' ? data.detail : `HTTP ${res.status}`
}

/** 衣装のタグ(サーバーの wardrobe.compose と同じ組み立て。保存前の見本に使う) */
function compose(parts: OutfitPart[], extra: string, catalog: Record<string, Part[]>, slots: Kind[]): string {
  const order = slots.map(s => s.key)
  const find = (slot: string, value: string) => (catalog[slot] ?? []).find(p => p.value === value)
  const tags: string[] = []
  for (const part of [...parts].filter(p => order.includes(p.slot)).sort((a, b) => order.indexOf(a.slot) - order.indexOf(b.slot))) {
    const item = find(part.slot, part.value)
    if (!item) continue
    const list = item.tags.split(',').map(t => t.trim()).filter(Boolean)
    if (!list.length) continue
    if (item.colorable) {
      const prefix = [find('color', part.color)?.tags, find('pattern', part.pattern)?.tags].map(t => t?.trim()).filter(Boolean).join(' ')
      if (prefix) list[0] = `${prefix} ${list[0]}`
    }
    tags.push(...list)
  }
  tags.push(...extra.split(',').map(t => t.trim()).filter(Boolean))
  const seen = new Set<string>()
  return tags.filter(t => !seen.has(t.toLowerCase()) && seen.add(t.toLowerCase())).join(', ')
}

export default function Wardrobe({ characterId, token, rootName, disabled, onChanged }: Props) {
  const navigate = useNavigate()
  const [slots, setSlots] = useState<Kind[]>([])
  const [catalog, setCatalog] = useState<Record<string, Part[]>>({})
  const [data, setData] = useState<Outfits | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [status, setStatus] = useState<{ text: string; error: boolean } | null>(null)
  const [busy, setBusy] = useState(false)
  const [dressing, setDressing] = useState<number | null>(null)
  const [zoom, setZoom] = useState<Outfit | null>(null)

  const headers = { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) }
  const base = `/api/wardrobe/characters/${characterId}`
  const query = `?root_name=${encodeURIComponent(rootName)}`

  useEffect(() => {
    fetch('/api/wardrobe/parts')
      .then(r => (r.ok ? r.json() : null))
      .then(d => {
        if (!d) return
        setSlots(d.slots)
        setCatalog(d.parts)
      })
      .catch(() => setStatus({ text: '部品を読み込めませんでした', error: true }))
  }, [])

  useEffect(() => {
    setData(null)
    setDraft(null)
    setStatus(null)
    fetch(`${base}/outfits${query}`)
      .then(r => (r.ok ? r.json() : null))
      .then(d => d && setData(d))
      .catch(() => setStatus({ text: '衣装を読み込めませんでした', error: true }))
  }, [base, query])

  async function call(url: string, init: RequestInit, done: string, changed = false): Promise<boolean> {
    setBusy(true)
    setStatus(null)
    try {
      const res = await fetch(url, { headers, ...init })
      if (!res.ok) {
        setStatus({ text: await detail(res), error: true })
        return false
      }
      setData(await res.json())
      setStatus({ text: done, error: false })
      if (changed) onChanged?.()
      return true
    } catch (e) {
      setStatus({ text: e instanceof Error ? e.message : String(e), error: true })
      return false
    } finally {
      setBusy(false)
    }
  }

  async function save() {
    if (!draft) return
    const body = JSON.stringify({ name: draft.name.trim(), parts: draft.parts.filter(p => p.value), extra_tags: draft.extra_tags })
    const ok = draft.id === null
      ? await call(`${base}/outfits${query}`, { method: 'POST', body }, `「${draft.name}」を登録しました`)
      : await call(`${base}/outfits/${draft.id}${query}`, { method: 'PUT', body }, `「${draft.name}」を保存しました`,
        draft.id === data?.outfit_id)
    if (ok) setDraft(null)
  }

  async function remove(o: Outfit) {
    if (!window.confirm(`衣装「${o.name}」を消しますか?(着せた絵は残ります)`)) return
    await call(`${base}/outfits/${o.id}${query}`, { method: 'DELETE' }, `「${o.name}」を消しました`, o.id === data?.outfit_id)
  }

  async function wear(o: Outfit) {
    await call(`${base}/outfit${query}`, { method: 'PUT', body: JSON.stringify({ outfit_id: o.id }) },
      `「${o.name}」に着せ替えました(キャラシートの普段の服装が変わりました)`, true)
  }

  async function dress(o: Outfit) {
    setDressing(o.id)
    setStatus({ text: `「${o.name}」を素体の Aポーズに着せています…`, error: false })
    try {
      const res = await fetch(`/api/lora-dataset/character/${characterId}/body-outfit`, {
        method: 'POST', headers, body: JSON.stringify({ outfit_id: o.id, root_name: rootName }),
      })
      if (!res.ok) {
        setStatus({ text: `着せられませんでした: ${await detail(res)}`, error: true })
        return
      }
      const reload = await fetch(`${base}/outfits${query}`)
      if (reload.ok) setData(await reload.json())
      setStatus({ text: `「${o.name}」を着せました。素体のタブの2Dモデルの服の層にも、この絵が使われます`, error: false })
    } finally {
      setDressing(null)
    }
  }

  async function useForModel(o: Outfit) {
    setBusy(true)
    try {
      const res = await fetch(`/api/lora-dataset/character/${characterId}/body-outfit/${o.id}/use${query}`, { method: 'POST', headers })
      setStatus(res.ok
        ? { text: `「${o.name}」の絵を、2Dモデルの服の層に使います(層に分け直すときは素体のタブで)`, error: false }
        : { text: await detail(res), error: true })
    } finally {
      setBusy(false)
    }
  }

  function editPart(index: number, change: Partial<OutfitPart>) {
    if (!draft) return
    setDraft({ ...draft, parts: draft.parts.map((p, i) => (i === index ? { ...p, ...change } : p)) })
  }

  const off = disabled || busy || dressing !== null
  const options = (slot: string) => catalog[slot] ?? []
  const item = (p: OutfitPart) => options(p.slot).find(o => o.value === p.value)

  return (
    <section className="wdr">
      <div className="wdr-head">
        <h3>衣装(着せ替え)</h3>
        <div className="fsh-actions">
          <button type="button" className="lora-btn lora-btn--primary" disabled={off || draft !== null}
            onClick={() => setDraft({ id: null, name: '', parts: [{ ...EMPTY_PART }], extra_tags: '' })}>衣装を足す</button>
          <button type="button" className="lora-btn lora-btn--secondary" onClick={() => navigate('/wardrobe-settings')}>
            部品の設定
          </button>
        </div>
      </div>
      <p className="lora-hint">
        服を部位ごとに選んで(色・柄も)、自由入力のタグを足して衣装にします。「着る」を押した衣装が普段の服装になり、データセット・漫画の
        場面で使われます。素体を当てていれば「素体に着せる」で Aポーズに着せた絵を描けます(1枚ぶんの Anlas)。
      </p>
      {status && <p className={status.error ? 'lora-error' : 'lora-hint'} role={status.error ? 'alert' : undefined}>{status.text}</p>}

      {draft && (
        <div className="wdr-editor">
          <label className="lora-label">衣装の名前
            <input className="lora-input" value={draft.name} maxLength={40} placeholder="夏服・水着・パジャマ など" disabled={off}
              onChange={e => setDraft({ ...draft, name: e.target.value })} />
          </label>
          <ul className="wdr-parts">
            {draft.parts.map((p, i) => (
              <li key={i} className="wdr-part">
                <select className="lora-select" value={p.slot} disabled={off} aria-label="部位"
                  onChange={e => editPart(i, { slot: e.target.value, value: '' })}>
                  {slots.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
                </select>
                <select className="lora-select" value={p.value} disabled={off} aria-label="種類"
                  onChange={e => editPart(i, { value: e.target.value })}>
                  <option value="">選んでください</option>
                  {options(p.slot).map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
                <select className="lora-select" value={p.color} disabled={off || !item(p)?.colorable} aria-label="色"
                  onChange={e => editPart(i, { color: e.target.value })}>
                  <option value="">色なし</option>
                  {options('color').map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
                <select className="lora-select" value={p.pattern} disabled={off || !item(p)?.colorable} aria-label="柄"
                  onChange={e => editPart(i, { pattern: e.target.value })}>
                  <option value="">柄なし</option>
                  {options('pattern').map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
                <button type="button" className="lora-btn lora-btn--secondary" disabled={off} aria-label="この部品を外す"
                  onClick={() => setDraft({ ...draft, parts: draft.parts.filter((_, j) => j !== i) })}>×</button>
              </li>
            ))}
          </ul>
          <button type="button" className="lora-btn lora-btn--secondary" disabled={off}
            onClick={() => setDraft({ ...draft, parts: [...draft.parts, { ...EMPTY_PART }] })}>部品を足す</button>
          <label className="lora-label">自由入力のタグ(部品に無い服・飾り)
            <textarea className="lora-textarea" rows={2} value={draft.extra_tags} disabled={off} placeholder="frilled collar, red neck ribbon"
              onChange={e => setDraft({ ...draft, extra_tags: e.target.value })} />
          </label>
          <p className="wdr-tags">{compose(draft.parts, draft.extra_tags, catalog, slots) || '(タグなし)'}</p>
          <div className="fsh-actions">
            <button type="button" className="lora-btn lora-btn--primary" disabled={off || !draft.name.trim()} onClick={() => void save()}>
              {draft.id === null ? '登録' : '保存'}
            </button>
            <button type="button" className="lora-btn lora-btn--secondary" disabled={busy} onClick={() => setDraft(null)}>やめる</button>
          </div>
        </div>
      )}

      {data && data.outfits.length === 0 && !draft && <p className="lora-hint">まだ衣装がありません。「衣装を足す」で登録します。</p>}
      <ul className="wdr-list">
        {data?.outfits.map(o => {
          const worn = o.id === data.outfit_id
          return (
            <li key={o.id} className={worn ? 'wdr-card wdr-card--worn' : 'wdr-card'}>
              {o.dressed ? (
                <button type="button" className="wdr-thumb" onClick={() => setZoom(o)} title="拡大">
                  <img src={`${fileUrl(o.dressed.path)}&v=${o.dressed.updated}`} alt={`${o.name}を着せた素体`} loading="lazy" />
                </button>
              ) : <div className="wdr-thumb wdr-thumb--none">まだ着せていません</div>}
              <div className="wdr-card-body">
                <h4>{o.name}{worn && <span className="wdr-badge">普段</span>}</h4>
                <p className="wdr-tags">{o.tags || '(タグなし)'}</p>
                <div className="fsh-actions">
                  <button type="button" className="lora-btn lora-btn--primary" disabled={off || worn} onClick={() => void wear(o)}>
                    {worn ? '着ています' : '着る'}
                  </button>
                  <button type="button" className="lora-btn lora-btn--secondary" disabled={off || draft !== null}
                    onClick={() => setDraft({ id: o.id, name: o.name, parts: o.parts.map(p => ({ ...EMPTY_PART, ...p })), extra_tags: o.extra_tags })}>
                    編集
                  </button>
                  <button type="button" className="lora-btn lora-btn--secondary" disabled={off || !o.tags} onClick={() => void dress(o)}>
                    {dressing === o.id ? '着せています…' : o.dressed ? '素体に着せ直す' : '素体に着せる'}
                  </button>
                  {o.dressed && (
                    <button type="button" className="lora-btn lora-btn--secondary" disabled={off} onClick={() => void useForModel(o)}
                      title="この絵を、素体のタブの2Dモデルの服の層に使います">2Dモデルに使う</button>
                  )}
                  <button type="button" className="lora-btn lora-btn--danger" disabled={off} onClick={() => void remove(o)}>削除</button>
                </div>
              </div>
            </li>
          )
        })}
      </ul>
      {zoom?.dressed && (
        <FullscreenViewer src={`${fileUrl(zoom.dressed.path)}&v=${zoom.dressed.updated}`} alt={`${zoom.name}を着せた素体`}
          onClose={() => setZoom(null)} />
      )}
    </section>
  )
}
