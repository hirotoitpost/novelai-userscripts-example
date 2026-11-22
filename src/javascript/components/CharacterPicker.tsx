import { useEffect, useMemo, useState } from 'react'
import { getJson, thumbUrl } from '../library'
import './CharacterPicker.css'

/**
 * キャラを絵で選ぶ画面(全画面の上に出す)。キャラシートの参照画像のカードを、作品(シリーズ)ごとの横の列に並べる。
 * カードを押すとプロフィール(大きい絵・年齢・普段の服・出ている作品・メモ)を出し、「このキャラにする」で決める。
 * 上の検索欄で名前・メモから絞れる(絞っているあいだは列に分けず、まとめて並べる)。
 * 「全キャラ一覧」で、列に分けずに全員を名前順に並べる(もう一度押すと作品ごとに戻る)。
 * 使う側は open / onClose / onPick を渡すだけ(一覧は開いたときに読む)。
 */

export interface PickerCharacter {
  id: number
  name: string
  notes: string | null
  appearance_tags: string
  outfit_tags: string | null
  reference_image_path: string | null
  age: number | null
  is_adult: boolean
  is_minor: boolean
  growth_of: number | null
  growth_label: string | null
  created_at: string
}

interface Work {
  story_id: number
  title: string
  scenes: number
  created_at: string
  volume_no: number | null
  series_id: number | null
  series_title: string | null
}

interface Group {
  key: string
  title: string
  items: PickerCharacter[]
}

const RECENT = 12

function ageLabel(c: PickerCharacter): string {
  if (c.age !== null) return `${c.age}歳`
  if (c.is_minor) return '未成年'
  if (c.is_adult) return '成人'
  return '年齢未設定'
}

function blurb(c: PickerCharacter, length = 60): string {
  const text = (c.notes ?? '').replace(/\s+/g, ' ').trim()
  return text.length > length ? `${text.slice(0, length)}…` : text
}

function groupsOf(list: PickerCharacter[], works: Record<string, Work[]>): Group[] {
  const recent = [...list].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, RECENT)
  const series = new Map<number, { title: string; latest: string; items: PickerCharacter[] }>()
  const loose: PickerCharacter[] = []
  for (const c of list) {
    const own = works[String(c.id)] ?? []
    const ids = new Set<number>()
    for (const w of own) {
      if (w.series_id === null || ids.has(w.series_id)) continue
      ids.add(w.series_id)
      const g = series.get(w.series_id) ?? { title: w.series_title ?? `シリーズ ${w.series_id}`, latest: '', items: [] }
      g.items.push(c)
      if (w.created_at > g.latest) g.latest = w.created_at
      series.set(w.series_id, g)
    }
    if (ids.size === 0) loose.push(c)
  }
  const groups: Group[] = [{ key: 'recent', title: '最近作ったキャラ', items: recent }]
  for (const [id, g] of [...series.entries()].sort((a, b) => b[1].latest.localeCompare(a[1].latest))) {
    groups.push({ key: `series-${id}`, title: g.title, items: g.items })
  }
  if (loose.length) groups.push({ key: 'loose', title: 'シリーズに出ていないキャラ', items: loose })
  return groups.filter(g => g.items.length)
}

/** 名前に段階の名前が入っていれば重ねて出さない(「九条ゆら(22歳)」に「(22歳)」を足さない) */
function stageLabel(c: PickerCharacter): string | null {
  return c.growth_label && !c.name.includes(c.growth_label) ? c.growth_label : null
}

function Card({ c, current, onOpen }: { c: PickerCharacter; current: boolean; onOpen: () => void }) {
  return (
    <button type="button" className={`cpk-card${current ? ' is-current' : ''}`} onClick={onOpen} title={c.name}>
      <div className="cpk-thumb">
        {c.reference_image_path
          ? <img src={thumbUrl(c.reference_image_path, 360)} alt="" loading="lazy" />
          : <span className="cpk-noimg">参照画像なし</span>}
      </div>
      <div className="cpk-name">{c.name}{stageLabel(c) ? <small>({stageLabel(c)})</small> : null}</div>
      <div className="cpk-blurb">{blurb(c) || ageLabel(c)}</div>
    </button>
  )
}

export default function CharacterPicker({
  open, currentId, onClose, onPick, allowNone = true,
}: {
  open: boolean
  currentId: number | null
  onClose: () => void
  onPick: (character: PickerCharacter | null) => void
  allowNone?: boolean
}) {
  const [list, setList] = useState<PickerCharacter[] | null>(null)
  const [works, setWorks] = useState<Record<string, Work[]>>({})
  const [query, setQuery] = useState('')
  const [detail, setDetail] = useState<PickerCharacter | null>(null)
  const [showAll, setShowAll] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setDetail(null)
    setQuery('')
    Promise.all([
      getJson<PickerCharacter[]>('/api/story/characters'),
      getJson<Record<string, Work[]>>('/api/story/character-works').catch(() => ({})),
    ])
      .then(([cs, ws]) => { setList(cs); setWorks(ws) })
      .catch(e => setError(`キャラを読めませんでした: ${e instanceof Error ? e.message : String(e)}`))
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      if (detail) setDetail(null)
      else onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, detail, onClose])

  const groups = useMemo(() => (list ? groupsOf(list, works) : []), [list, works])
  const matches = useMemo(() => {
    if (!list) return null
    const q = query.trim().toLowerCase()
    if (!q && !showAll) return null
    const found = q ? list.filter(c => c.name.toLowerCase().includes(q) || (c.notes ?? '').toLowerCase().includes(q)) : list
    return [...found].sort((a, b) => a.name.localeCompare(b.name, 'ja'))
  }, [list, query, showAll])

  if (!open) return null
  const detailWorks = detail ? (works[String(detail.id)] ?? []) : []

  return (
    <div className="cpk-backdrop" role="dialog" aria-modal="true" aria-label="キャラを選ぶ" onClick={onClose}>
      <div className="cpk-panel" onClick={e => e.stopPropagation()}>
        <header className="cpk-head">
          <h2>キャラを選ぶ</h2>
          {!detail && (
            <input
              className="cpk-search"
              type="search"
              placeholder="名前・メモで探す"
              value={query}
              onChange={e => setQuery(e.target.value)}
              autoFocus
            />
          )}
          {allowNone && !detail && (
            <button type="button" className="cpk-none" onClick={() => onPick(null)}>キャラなしで描く</button>
          )}
          {!detail && (
            <button type="button" className={`cpk-none${showAll ? ' is-on' : ''}`} onClick={() => setShowAll(v => !v)}
              title={showAll ? '作品(シリーズ)ごとの列に戻します' : '列に分けずに、全員を名前順に並べます'}>
              {showAll ? '作品ごとに戻る' : `全キャラ一覧${list ? `(${list.length}人)` : ''}`}
            </button>
          )}
          <button type="button" className="cpk-close" onClick={onClose} aria-label="閉じる">×</button>
        </header>

        {error && <p className="pub-error" role="alert">{error}</p>}
        {!list && !error && <p className="cpk-loading">読み込んでいます…</p>}

        {detail ? (
          <div className="cpk-detail">
            <div className="cpk-detail-image">
              {detail.reference_image_path
                ? <img src={thumbUrl(detail.reference_image_path, 720)} alt={detail.name} />
                : <span className="cpk-noimg">参照画像なし</span>}
            </div>
            <div className="cpk-profile">
              <h3>{detail.name}{stageLabel(detail) ? <small>({stageLabel(detail)})</small> : null}</h3>
              {detail.notes && <p className="cpk-notes">{blurb(detail, 240)}</p>}
              <dl>
                <dt>年齢</dt><dd>{ageLabel(detail)}</dd>
                <dt>普段の服</dt><dd>{detail.outfit_tags?.trim() || 'なし'}</dd>
                <dt>見た目</dt><dd className="cpk-tags">{detail.appearance_tags}</dd>
                <dt>出ている作品</dt>
                <dd>
                  {detailWorks.length
                    ? detailWorks.slice(0, 6).map(w => (
                      <span key={w.story_id} className="cpk-work">
                        {w.series_title ? `${w.series_title}${w.volume_no ? ` ${w.volume_no}巻` : ''}: ` : ''}{w.title}
                      </span>
                    ))
                    : 'まだありません'}
                  {detailWorks.length > 6 && <span className="cpk-work">ほか {detailWorks.length - 6} 作</span>}
                </dd>
              </dl>
              <div className="cpk-detail-actions">
                <button type="button" className="cpk-pick" onClick={() => onPick(detail)}>このキャラにする</button>
                <button type="button" className="cpk-back" onClick={() => setDetail(null)}>一覧に戻る</button>
              </div>
            </div>
          </div>
        ) : matches ? (
          <div className="cpk-body">
            {matches.length === 0 && <p className="cpk-loading">見つかりません</p>}
            <div className="cpk-grid">
              {matches.map(c => <Card key={c.id} c={c} current={c.id === currentId} onOpen={() => setDetail(c)} />)}
            </div>
          </div>
        ) : (
          <div className="cpk-body">
            {groups.map(g => (
              <section key={g.key} className="cpk-group">
                <h3>{g.title}<span className="cpk-count">{g.items.length}人</span></h3>
                <div className="cpk-row">
                  {g.items.map(c => <Card key={c.id} c={c} current={c.id === currentId} onOpen={() => setDetail(c)} />)}
                </div>
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
