import { PointerEvent as ReactPointerEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { MangaV2Scene } from './MangaV2Studio'
import './PageLayoutEditor.css'

/**
 * 漫画v2のページごとのコマ割り: 確認(ページごとにどのシーンがどのコマに入るか)・変更(コマ数からパターンを選ぶ、
 * コマの境目をドラッグして大きさを変える、ページを足す・消す)。保存すると合成・ダウンロード・コマの生成サイズが従う。
 * コマ割りは「段(rows)か列(cols)に分ける」の入れ子(分割の木)で、列は右から左へ読む。
 */

type Tree = 'panel' | { dir: 'rows' | 'cols'; sizes: number[]; items: Tree[] }
type Point = [number, number]
type Box = [number, number, number, number]

interface ViewPage {
  source: 'layout' | 'template'
  layout: number | null
  count: number
  scene_ids: number[]
  tree: Tree | null
  spread: boolean
  panels: Point[][]
}

interface View { explicit: boolean; template: string; scenes: number; pages: ViewPage[] }

interface Pattern { id: string; label: string; tree: Tree }

/** 画面で直している1ページ。tree が無いのは取り込んだ作品から写したページ(斜めの枠など。形はそのまま) */
interface DraftPage { tree: Tree | null; layout: number | null; panels: Point[][] }

interface Props {
  apiOrigin: string
  storyId: number
  template: string
  scenes: MangaV2Scene[]
  busy: boolean
  /** 保存したあと(コマの絵の一覧を読み直すなど) */
  onSaved?: () => void
}

const MIN_SHARE = 0.08
const MAX_PANELS = 12
// ページ(本文領域)の縦横比。1200×1700 から余白を除いた形に近い
const PAGE_ASPECT = 1100 / 1600

function count(tree: Tree): number {
  return tree === 'panel' ? 1 : tree.items.reduce((n, item) => n + count(item), 0)
}

/** 木のコマを読む順に、本文領域を単位正方形とした [x0, y0, x1, y1] で */
function rects(tree: Tree, box: Box = [0, 0, 1, 1]): Box[] {
  if (tree === 'panel') return [box]
  const [x0, y0, x1, y1] = box
  const out: Box[] = []
  let start = 0
  tree.items.forEach((item, i) => {
    const end = i === tree.items.length - 1 ? 1 : start + tree.sizes[i]
    const child: Box = tree.dir === 'rows'
      ? [x0, y0 + (y1 - y0) * start, x1, y0 + (y1 - y0) * end]
      : [x1 - (x1 - x0) * end, y0, x1 - (x1 - x0) * start, y1]
    out.push(...rects(item, child))
    start = end
  })
  return out
}

/** ドラッグできる境目: どの分割(path)の、何番目と次の間か、と画面上の位置 */
interface Divider { path: number[]; index: number; dir: 'rows' | 'cols'; box: Box; at: number }

function dividers(tree: Tree, box: Box = [0, 0, 1, 1], path: number[] = []): Divider[] {
  if (tree === 'panel') return []
  const [x0, y0, x1, y1] = box
  const out: Divider[] = []
  let start = 0
  tree.items.forEach((item, i) => {
    const end = i === tree.items.length - 1 ? 1 : start + tree.sizes[i]
    const child: Box = tree.dir === 'rows'
      ? [x0, y0 + (y1 - y0) * start, x1, y0 + (y1 - y0) * end]
      : [x1 - (x1 - x0) * end, y0, x1 - (x1 - x0) * start, y1]
    if (i < tree.items.length - 1) {
      out.push({ path, index: i, dir: tree.dir, box, at: tree.dir === 'rows' ? child[3] : child[0] })
    }
    out.push(...dividers(item, child, [...path, i]))
    start = end
  })
  return out
}

function setSizes(tree: Tree, path: number[], sizes: number[]): Tree {
  if (tree === 'panel') return tree
  if (path.length === 0) return { ...tree, sizes }
  const [head, ...rest] = path
  return { ...tree, items: tree.items.map((item, i) => (i === head ? setSizes(item, rest, sizes) : item)) }
}

function nodeAt(tree: Tree, path: number[]): Tree {
  return path.reduce((node, i) => (node === 'panel' ? node : node.items[i]), tree)
}

/** ページの輪郭(サーバーの形: ページに対する割合)を、本文領域の単位正方形に寄せる(余白 50/1200・50/1700 を除く) */
function toContent([x, y]: Point): Point {
  return [(x * 1200 - 50) / 1100, (y * 1700 - 50) / 1600]
}

function shapesOf(page: DraftPage): Point[][] {
  if (page.tree) return rects(page.tree).map(([x0, y0, x1, y1]) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
  return page.panels.map(points => points.map(toContent))
}

function PageSvg({ page, labels, width, onDivider, active }: {
  page: DraftPage
  labels: string[]
  width: number
  onDivider?: (d: Divider, e: ReactPointerEvent<SVGRectElement>) => void
  active?: Divider | null
}) {
  const height = width / PAGE_ASPECT
  const shapes = shapesOf(page)
  const lines = page.tree && onDivider ? dividers(page.tree) : []
  return (
    <svg className="ple-svg" viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img"
      aria-label={`${shapes.length}コマのページ`}>
      <rect x={0} y={0} width={width} height={height} className="ple-paper" />
      {shapes.map((points, i) => {
        const pts = points.map(([x, y]) => `${x * width},${y * height}`).join(' ')
        const cx = points.reduce((s, [x]) => s + x, 0) / points.length * width
        const cy = points.reduce((s, [, y]) => s + y, 0) / points.length * height
        return (
          <g key={i}>
            <polygon points={pts} className={labels[i] ? 'ple-panel' : 'ple-panel ple-panel--empty'} />
            <text x={cx} y={cy} className="ple-label" fontSize={Math.max(9, width / 14)}>{labels[i] ?? '空'}</text>
          </g>
        )
      })}
      {lines.map((d, i) => {
        const [x0, y0, x1, y1] = d.box
        const hit = 12
        const r = d.dir === 'rows'
          ? { x: x0 * width, y: d.at * height - hit / 2, width: (x1 - x0) * width, height: hit }
          : { x: d.at * width - hit / 2, y: y0 * height, width: hit, height: (y1 - y0) * height }
        const on = active && active.path.join() === d.path.join() && active.index === d.index
        return (
          <rect key={i} {...r} className={on ? 'ple-divider ple-divider--active' : 'ple-divider'}
            style={{ cursor: d.dir === 'rows' ? 'ns-resize' : 'ew-resize' }}
            onPointerDown={e => onDivider?.(d, e)} />
        )
      })}
    </svg>
  )
}

export default function PageLayoutEditor({ apiOrigin, storyId, template, scenes, busy, onSaved }: Props) {
  const [view, setView] = useState<View | null>(null)
  const [pages, setPages] = useState<DraftPage[]>([])
  const [selected, setSelected] = useState(0)
  const [patterns, setPatterns] = useState<Record<number, Pattern[]>>({})
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [drag, setDrag] = useState<Divider | null>(null)
  const svgRef = useRef<HTMLDivElement>(null)

  const load = useCallback(async () => {
    setError(null)
    const res = await fetch(`${apiOrigin}/api/manga-v2/${storyId}/page-layout?template=${encodeURIComponent(template)}`)
    if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`)
    const data: View = await res.json()
    setView(data)
    setPages(data.pages.map(p => ({ tree: p.tree, layout: p.source === 'layout' ? p.layout : null, panels: p.panels })))
    setSelected(s => Math.min(s, Math.max(0, data.pages.length - 1)))
    setDirty(false)
  }, [apiOrigin, storyId, template])

  useEffect(() => {
    load().catch(e => setError(e instanceof Error ? e.message : String(e)))
  }, [load])

  async function loadPatterns(n: number): Promise<Pattern[]> {
    if (patterns[n]) return patterns[n]
    const res = await fetch(`${apiOrigin}/api/manga-v2/layout-patterns?count=${n}`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const list: Pattern[] = await res.json()
    setPatterns(prev => ({ ...prev, [n]: list }))
    return list
  }

  // ページのコマ数から、シーンを先頭から順に割り当てる(合成と同じ)
  const assigned = useMemo(() => {
    const out: MangaV2Scene[][] = []
    let next = 0
    for (const page of pages) {
      const n = page.tree ? count(page.tree) : page.panels.length
      out.push(scenes.slice(next, next + n))
      next += n
    }
    return { pages: out, rest: Math.max(0, scenes.length - next) }
  }, [pages, scenes])

  const current = pages[selected]
  const currentCount = current ? (current.tree ? count(current.tree) : current.panels.length) : 0

  useEffect(() => {
    if (currentCount > 0) loadPatterns(currentCount).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentCount])

  function editPage(index: number, page: DraftPage) {
    setPages(prev => prev.map((p, i) => (i === index ? page : p)))
    setDirty(true)
  }

  async function changeCount(n: number) {
    try {
      const list = await loadPatterns(n)
      if (list[0]) editPage(selected, { tree: list[0].tree, layout: null, panels: [] })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  function addPage() {
    const tree: Tree = { dir: 'rows', sizes: [0.5, 0.5], items: [{ dir: 'cols', sizes: [0.5, 0.5], items: ['panel', 'panel'] }, { dir: 'cols', sizes: [0.5, 0.5], items: ['panel', 'panel'] }] }
    setPages(prev => [...prev.slice(0, selected + 1), { tree, layout: null, panels: [] }, ...prev.slice(selected + 1)])
    setSelected(selected + 1)
    setDirty(true)
  }

  function removePage() {
    if (pages.length <= 1) return
    setPages(prev => prev.filter((_, i) => i !== selected))
    setSelected(s => Math.max(0, s - 1))
    setDirty(true)
  }

  // ---- 境目のドラッグ ----
  function startDrag(d: Divider, e: ReactPointerEvent<SVGRectElement>) {
    if (busy || saving) return
    e.preventDefault()
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
    setDrag(d)
  }

  function moveDrag(e: ReactPointerEvent<HTMLDivElement>) {
    if (!drag || !current?.tree) return
    const svg = svgRef.current?.querySelector('svg')
    if (!svg) return
    const bounds = svg.getBoundingClientRect()
    const px = (e.clientX - bounds.left) / bounds.width
    const py = (e.clientY - bounds.top) / bounds.height
    const node = nodeAt(current.tree, drag.path)
    if (node === 'panel') return
    const [x0, y0, x1, y1] = drag.box
    // 分割の中での位置(rows は上から、cols は右から)
    const f = drag.dir === 'rows' ? (py - y0) / (y1 - y0) : (x1 - px) / (x1 - x0)
    const before = node.sizes.slice(0, drag.index).reduce((a, b) => a + b, 0)
    const pair = node.sizes[drag.index] + node.sizes[drag.index + 1]
    const first = Math.min(Math.max(f - before, MIN_SHARE), pair - MIN_SHARE)
    const sizes = node.sizes.map((s, i) => (i === drag.index ? first : i === drag.index + 1 ? pair - first : s))
    editPage(selected, { ...current, tree: setSizes(current.tree, drag.path, sizes.map(s => Math.round(s * 1000) / 1000)) })
  }

  async function save() {
    setSaving(true)
    setError(null)
    setNote(null)
    try {
      const res = await fetch(`${apiOrigin}/api/manga-v2/${storyId}/page-layout`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ template, pages: pages.map(p => (p.tree ? { tree: p.tree } : { layout: p.layout })) }),
      })
      if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`)
      const result = await res.json()
      await load()
      onSaved?.()
      setNote(`保存しました${result.stale_panels ? `。形が変わったコマ ${result.stale_panels} 枚を描き直し待ちにしました` : ''}。次の合成から使います。`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  async function reset() {
    if (!window.confirm('ページごとのコマ割りをやめて、テンプレートの割り付けに戻しますか？')) return
    setSaving(true)
    setError(null)
    try {
      const res = await fetch(`${apiOrigin}/api/manga-v2/${storyId}/page-layout?template=${encodeURIComponent(template)}`, { method: 'DELETE' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      await load()
      onSaved?.()
      setNote('テンプレートの割り付けに戻しました。')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const off = busy || saving
  const labels = (i: number) => (assigned.pages[i] ?? []).map(s => String(s.scene_index + 1))

  if (!view) return error ? <p className="ple-error" role="alert">{error}</p> : <p className="story-muted">読み込み中…</p>

  return (
    <div className="ple">
      <p className="story-muted">
        {view.explicit
          ? 'ページごとのコマ割りを決めてあります。'
          : `今はテンプレートで割り付けています(変えて保存すると、ページごとのコマ割りになります)。`}
        数字はコマに入るシーンの番号です。シーンは先頭から順にコマに入ります。境目をドラッグすると大きさを変えられます。
      </p>

      <div className="ple-strip" role="list">
        {pages.map((page, i) => (
          <button key={i} type="button" role="listitem"
            className={i === selected ? 'ple-thumb ple-thumb--on' : 'ple-thumb'}
            onClick={() => setSelected(i)} aria-label={`${i + 1}ページ目`}>
            <PageSvg page={page} labels={labels(i)} width={56} />
            <span>{i + 1}</span>
          </button>
        ))}
      </div>
      {assigned.rest > 0 && (
        <p className="story-muted">残り {assigned.rest} シーンは、テンプレートのコマ割りで続きます。</p>
      )}

      {current && (
        <div className="ple-edit">
          <div ref={svgRef} className="ple-large" onPointerMove={moveDrag} onPointerUp={() => setDrag(null)}
            onPointerLeave={() => setDrag(null)}>
            <PageSvg page={current} labels={labels(selected)} width={260} onDivider={startDrag} active={drag} />
          </div>
          <div className="ple-side">
            <div className="ple-row">
              <strong>{selected + 1}ページ目</strong>
              <label>
                コマ数
                <select value={currentCount} disabled={off} onChange={e => void changeCount(Number(e.target.value))}>
                  {Array.from({ length: MAX_PANELS }, (_, i) => i + 1).map(n => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
              <button type="button" disabled={off} onClick={addPage}>後ろにページを足す</button>
              <button type="button" disabled={off || pages.length <= 1} onClick={removePage}>このページを消す</button>
            </div>
            {!current.tree && (
              <p className="story-muted">取り込んだ作品から写したページです(形はそのまま)。パターンを選ぶと置き換わります。</p>
            )}
            <div className="ple-patterns">
              {(patterns[currentCount] ?? []).map(p => (
                <button key={p.id} type="button" className="ple-pattern" disabled={off} title={p.label}
                  onClick={() => editPage(selected, { tree: p.tree, layout: null, panels: [] })}>
                  <PageSvg page={{ tree: p.tree, layout: null, panels: [] }} labels={[]} width={52} />
                  <span>{p.label}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      <div className="ple-row">
        <button type="button" disabled={off || !dirty} onClick={() => void save()}>このコマ割りを保存</button>
        <button type="button" disabled={off || !dirty} onClick={() => void load()}>変更を取り消す</button>
        {view.explicit && <button type="button" disabled={off} onClick={() => void reset()}>テンプレートに戻す</button>}
      </div>
      {note && <p className="ple-note" role="status">{note}</p>}
      {error && <p className="ple-error" role="alert">{error}</p>}
    </div>
  )
}
