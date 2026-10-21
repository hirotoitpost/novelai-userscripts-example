import { PointerEvent as ReactPointerEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { MangaV2Scene } from './MangaV2Studio'
import {
  Box, DEFAULT_CUTOUT, DEFAULT_GAPS, DEFAULT_STYLE, DividerLine, FloatPanel, Gaps, Leaf, MAX_TILT, MIN_SHARE, PAGE_H, PAGE_W, PageStyle, Point,
  ShapeKind, Split, Tree, TrianglePoint,
  count, floatOutline, isSplit, layoutGeometry, nodeAt, setAt, simplify, tidyLeaf,
} from './layoutTree'
import './PageLayoutEditor.css'

/**
 * 漫画v2のページごとのコマ割り: 確認(ページごとにどのシーンがどのコマに入るか)・変更(コマ数からパターンを選ぶ、
 * コマの境目をドラッグして大きさを変える・傾ける、コマの形(楕円・三角形・自由な形・手書き)と枠線、間隔・余白・色、
 * 重ねて置くコマと重なり順、ページを足す・消す)。保存すると合成・ダウンロード・コマの生成サイズが従う。
 */

interface ViewPage {
  source: 'layout' | 'template'
  layout: number | null
  count: number
  scene_ids: number[]
  tree: Tree | null
  gaps: Gaps
  style: PageStyle
  floats: FloatPanel[]
  spread: boolean
  panels: Point[][]
}

interface View { explicit: boolean; template: string; scenes: number; pages: ViewPage[] }

interface Pattern { id: string; label: string; tree: Tree }

/** 画面で直している1ページ。tree が無いのは取り込んだ作品から写したページ(斜めの枠など。形はそのまま) */
interface DraftPage { tree: Tree | null; layout: number | null; panels: Point[][]; gaps: Gaps; style: PageStyle; floats: FloatPanel[] }

interface Props {
  apiOrigin: string
  storyId: number
  template: string
  scenes: MangaV2Scene[]
  busy: boolean
  /** 保存したあと(コマの絵の一覧を読み直すなど) */
  onSaved?: () => void
  /** 出力フォルダのファイルを画面で見せる URL(切り抜きの確認) */
  fileUrl?: (path: string) => string
}

/** シーンのコマの絵の切り抜きの状態(/api/manga-v2/scenes/{id}/cutout) */
interface CutoutStatus { methods: string[]; preview_path: string | null; width: number; height: number; upscaled: boolean }

async function detail(res: Response): Promise<string> {
  return (await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`
}

/**
 * そのコマに入るシーンの絵の、背景を消した結果の確認と、NovelAI での作り直し(Anlas を使う)。
 * 合成ではこの PC で切り抜く(無ければ作る)。NovelAI で作ったものがあれば、そちらを使う。
 */
function CutoutTools({ apiOrigin, sceneId, fileUrl, disabled }: { apiOrigin: string; sceneId: number; fileUrl?: (p: string) => string; disabled: boolean }) {
  const [status, setStatus] = useState<CutoutStatus | null>(null)
  const [missing, setMissing] = useState(false)
  const [working, setWorking] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    const res = await fetch(`${apiOrigin}/api/manga-v2/scenes/${sceneId}/cutout`)
    if (res.status === 404) { setMissing(true); setStatus(null); return }
    if (!res.ok) throw new Error(await detail(res))
    setMissing(false)
    setStatus(await res.json())
  }, [apiOrigin, sceneId])

  useEffect(() => { refresh().catch(e => setMessage(e instanceof Error ? e.message : String(e))) }, [refresh])

  async function run(work: () => Promise<void>) {
    setWorking(true)
    setMessage(null)
    try {
      await work()
      await refresh()
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e))
    } finally {
      setWorking(false)
    }
  }

  const local = () => run(async () => {
    const res = await fetch(`${apiOrigin}/api/manga-v2/scenes/${sceneId}/cutout`, { method: 'POST' })
    if (!res.ok) throw new Error(await detail(res))
  })

  const novelai = () => run(async () => {
    if (!status) return
    const est = await fetch(`${apiOrigin}/api/image/tools/anlas?tool=bg-removal&width=${status.width}&height=${status.height}`)
    const anlas = est.ok ? (await est.json()).total_anlas : null
    if (!window.confirm(`NovelAI で背景を消します(${anlas !== null ? `約 ${anlas} Anlas` : 'Anlas を使います'}。Opus でも無料になりません)。続けますか？`)) return
    const res = await fetch(`${apiOrigin}/api/manga-v2/scenes/${sceneId}/cutout/novelai`, { method: 'POST' })
    if (!res.ok) throw new Error(await detail(res))
    setMessage('NovelAI で背景を消しました。次の合成から使います。')
  })

  const upscale = () => run(async () => {
    const res = await fetch(`${apiOrigin}/api/manga-v2/scenes/${sceneId}/upscale`, { method: 'POST' })
    if (!res.ok) throw new Error(await detail(res))
    setMessage('AI で2倍に拡大しました。次の合成から、大きく貼っても輪郭がくっきりします。')
  })

  const dropUpscale = () => run(async () => {
    const res = await fetch(`${apiOrigin}/api/manga-v2/scenes/${sceneId}/upscale`, { method: 'DELETE' })
    if (!res.ok) throw new Error(await detail(res))
  })

  const dropNovelai = () => run(async () => {
    const res = await fetch(`${apiOrigin}/api/manga-v2/scenes/${sceneId}/cutout`, { method: 'DELETE' })
    if (!res.ok) throw new Error(await detail(res))
  })

  if (missing) return <p className="story-muted">このコマに入るシーンには、まだ絵がありません(絵を描いてから確認できます)。</p>
  const off = disabled || working
  return (
    <div className="ple-cutout">
      <div className="ple-row">
        <button type="button" disabled={off || !status} onClick={() => void local()}>
          {working ? '処理中…' : '切り抜きを確認(この PC・無料)'}
        </button>
        <button type="button" disabled={off || !status} onClick={() => void novelai()}>NovelAI で背景を消す(Anlas)</button>
        {status?.methods.includes('novelai') && (
          <button type="button" disabled={off} onClick={() => void dropNovelai()}>NovelAI の切り抜きをやめる</button>
        )}
      </div>
      <div className="ple-row">
        {status?.upscaled
          ? <button type="button" disabled={off} onClick={() => void dropUpscale()}>拡大をやめる</button>
          : (
            <button type="button" disabled={off || !status} onClick={() => void upscale()}
              title="この PC のアニメ絵向けの拡大で、絵を2倍にします(無料・1枚40秒ほど)">
              {working ? '処理中…' : 'AI で拡大して画質を上げる(この PC・約40秒)'}
            </button>
          )}
        <span className="story-muted">{status?.upscaled ? '拡大した絵を使います' : ''}</span>
      </div>
      <p className="story-muted">
        合成で使う切り抜き: {status?.methods.includes('novelai') ? 'NovelAI で作ったもの' : 'この PC で作るもの(合成のときに作ります)'}
      </p>
      {status?.preview_path && fileUrl && (
        <img className="ple-cutout-preview" src={`${fileUrl(status.preview_path)}&t=${status.methods.join('-')}`} alt="背景を消した絵" />
      )}
      {message && <p className="story-muted" role="status">{message}</p>}
    </div>
  )
}

const MAX_PANELS = 12
const MAX_FLOATS = 8
const PAGE_ASPECT = PAGE_W / PAGE_H

const SHAPES: { value: ShapeKind; label: string }[] = [
  { value: 'rect', label: '四角(斜めの境目なら台形)' },
  { value: 'ellipse', label: '楕円・丸' },
  { value: 'triangle', label: '三角形' },
  { value: 'polygon', label: '自由な形(頂点・手書き)' },
]
const TRIANGLE_POINTS: { value: TrianglePoint; label: string }[] = [
  { value: 'up', label: '上に尖る' }, { value: 'down', label: '下に尖る' },
  { value: 'left', label: '左に尖る' }, { value: 'right', label: '右に尖る' },
]
const GAP_FIELDS: { key: keyof Gaps; label: string; max: number }[] = [
  { key: 'gutter_x', label: '横の間隔', max: 120 },
  { key: 'gutter_y', label: '縦の間隔', max: 120 },
  { key: 'margin', label: 'ページの余白', max: 200 },
]

/**
 * ページに描くコマ(読む順)。grid はマス目のコマ(path で木の葉を指す)、float は重ねて置くコマ(floats の番号)。
 * box は形を収める枠(自由な形の頂点はこの枠に対する割合)。
 */
type Item =
  | { kind: 'grid'; key: string; path: number[]; leaf: Leaf; box: Box; outline: Point[]; z: number }
  | { kind: 'float'; key: string; index: number; leaf: FloatPanel; box: Box; outline: Point[]; z: number }

/** 読む順のコマ。マス目のコマの間に、重ねて置くコマを after の位置で入れる(サーバーと同じ) */
function itemsOf(page: DraftPage): Item[] {
  if (!page.tree) return []
  const grid = layoutGeometry(page.tree, page.gaps).panels
  const out: Item[] = []
  for (let i = 0; i <= grid.length; i++) {
    page.floats.forEach((f, index) => {
      if (Math.min(f.after, grid.length) === i) {
        out.push({ kind: 'float', key: `f${index}`, index, leaf: f, box: f.rect, outline: floatOutline(f), z: f.z })
      }
    })
    if (i < grid.length) {
      const g = grid[i]
      out.push({ kind: 'grid', key: `g${g.path.join('.')}`, path: g.path, leaf: g.leaf, box: g.box, outline: g.outline, z: g.leaf.z ?? 0 })
    }
  }
  return out
}

/** 重なり順に並べる(z が小さい方から。同じなら読む順)。後ろほど前面 */
function byLayer(items: Item[]): Item[] {
  return items.map((item, reading) => ({ item, reading })).sort((a, b) => a.item.z - b.item.z || a.reading - b.reading).map(x => x.item)
}

function panelCount(page: DraftPage): number {
  return page.tree ? count(page.tree) + page.floats.length : page.panels.length
}

function centroid(points: Point[]): Point {
  return [points.reduce((s, p) => s + p[0], 0) / points.length, points.reduce((s, p) => s + p[1], 0) / points.length]
}

function strokeOf(leaf: Leaf, style: PageStyle): { stroke: string; width: number } {
  return { stroke: leaf.border_color ?? style.border_color, width: leaf.border === false ? 0 : (leaf.border_width ?? style.border_width) }
}

/** 小さな図(ページの一覧・パターン) */
function PageSvg({ page, labels, width }: { page: DraftPage; labels: string[]; width: number }) {
  const height = width / PAGE_ASPECT
  const items = page.tree ? itemsOf(page) : null
  const shapes = items
    ? byLayer(items).map(item => ({ points: item.outline, reading: items.indexOf(item), ...strokeOf(item.leaf, page.style) }))
    : page.panels.map((points, i) => ({ points, reading: i, stroke: page.style.border_color, width: page.style.border_width }))
  return (
    <svg className="ple-svg" viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img"
      aria-label={`${shapes.length}コマのページ`}>
      <rect x={0} y={0} width={width} height={height} className="ple-paper" style={{ fill: page.style.background }} />
      {shapes.map(({ points, reading, stroke, width: line }) => {
        const [cx, cy] = centroid(points)
        return (
          <g key={reading}>
            <polygon points={points.map(([x, y]) => `${x * width},${y * height}`).join(' ')}
              className={labels[reading] ? 'ple-panel' : 'ple-panel ple-panel--empty'}
              style={{ stroke: line ? stroke : 'none', strokeWidth: Math.max(0.6, (line * width) / PAGE_W) }} />
            <text x={cx * width} y={cy * height} className="ple-label" fontSize={Math.max(9, width / 14)}>{labels[reading] ?? '空'}</text>
          </g>
        )
      })}
    </svg>
  )
}

type Selection = { kind: 'item'; key: string } | { kind: 'divider'; path: number[]; index: number } | null

type Drag =
  | { kind: 'divider'; line: DividerLine }
  | { kind: 'vertex'; key: string; vertex: number }
  | { kind: 'move'; index: number; start: Point; rect: Box }
  | { kind: 'resize'; index: number; rect: Box }

export default function PageLayoutEditor({ apiOrigin, storyId, template, scenes, busy, onSaved, fileUrl }: Props) {
  const [view, setView] = useState<View | null>(null)
  const [pages, setPages] = useState<DraftPage[]>([])
  const [selected, setSelected] = useState(0)
  const [patterns, setPatterns] = useState<Record<number, Pattern[]>>({})
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [selection, setSelection] = useState<Selection>(null)
  const [drag, setDrag] = useState<Drag | null>(null)
  // 手書きで輪郭を描いている途中の点(ページに対する割合)
  const [drawing, setDrawing] = useState<Point[] | null>(null)
  const [drawMode, setDrawMode] = useState(false)
  const svgRef = useRef<SVGSVGElement>(null)

  const load = useCallback(async () => {
    setError(null)
    const res = await fetch(`${apiOrigin}/api/manga-v2/${storyId}/page-layout?template=${encodeURIComponent(template)}`)
    if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`)
    const data: View = await res.json()
    setView(data)
    setPages(data.pages.map(p => ({
      tree: p.tree, layout: p.source === 'layout' ? p.layout : null, panels: p.panels,
      gaps: p.gaps ?? DEFAULT_GAPS, style: p.style ?? DEFAULT_STYLE, floats: p.floats ?? [],
    })))
    setSelected(s => Math.min(s, Math.max(0, data.pages.length - 1)))
    setSelection(null)
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
      const n = panelCount(page)
      out.push(scenes.slice(next, next + n))
      next += n
    }
    return { pages: out, rest: Math.max(0, scenes.length - next) }
  }, [pages, scenes])

  const current = pages[selected]
  const gridCount = current?.tree ? count(current.tree) : 0
  const items = useMemo(() => (current ? itemsOf(current) : []), [current])
  const dividers = useMemo(() => (current?.tree ? layoutGeometry(current.tree, current.gaps).dividers : []), [current])

  useEffect(() => {
    if (gridCount > 0) loadPatterns(gridCount).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gridCount])

  useEffect(() => { setSelection(null); setDrawMode(false) }, [selected])

  function editPage(index: number, page: DraftPage) {
    setPages(prev => prev.map((p, i) => (i === index ? page : p)))
    setDirty(true)
  }

  function editCurrent(patch: Partial<DraftPage>) {
    if (current) editPage(selected, { ...current, ...patch })
  }

  async function changeCount(n: number) {
    try {
      const list = await loadPatterns(n)
      if (list[0] && current) {
        // マス目のコマ数が変わるので、重ねたコマの読む順は後ろに寄せる
        editCurrent({ tree: list[0].tree, layout: null, panels: [], floats: current.floats.map(f => ({ ...f, after: Math.min(f.after, n) })) })
        setSelection(null)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  function addPage() {
    const tree: Tree = { dir: 'rows', sizes: [0.5, 0.5], items: [{ dir: 'cols', sizes: [0.5, 0.5], items: ['panel', 'panel'] }, { dir: 'cols', sizes: [0.5, 0.5], items: ['panel', 'panel'] }] }
    const page: DraftPage = { tree, layout: null, panels: [], gaps: current?.gaps ?? DEFAULT_GAPS, style: current?.style ?? DEFAULT_STYLE, floats: [] }
    setPages(prev => [...prev.slice(0, selected + 1), page, ...prev.slice(selected + 1)])
    setSelected(selected + 1)
    setDirty(true)
  }

  function removePage() {
    if (pages.length <= 1) return
    setPages(prev => prev.filter((_, i) => i !== selected))
    setSelected(s => Math.max(0, s - 1))
    setDirty(true)
  }

  // ---- 選んだコマ・境目 ----
  const target = selection?.kind === 'item' ? items.find(item => item.key === selection.key) ?? null : null
  const selectedSplit = selection?.kind === 'divider' && current?.tree ? nodeAt(current.tree, selection.path) : null

  /** 選んだコマ(マス目・重ねたコマ)の形・枠線・重なり順を変える */
  function updateItem(item: Item, patch: Partial<Leaf>) {
    if (!current?.tree) return
    if (item.kind === 'grid') {
      editCurrent({ tree: setAt(current.tree, item.path, tidyLeaf({ ...item.leaf, ...patch })) })
    } else {
      const next = { ...item.leaf, ...patch }
      editCurrent({ floats: current.floats.map((f, i) => (i === item.index ? next : f)) })
    }
  }

  function editLeaf(patch: Partial<Leaf>) {
    if (!target) return
    if (patch.shape === 'polygon' && !target.leaf.points?.length) {
      // 今の形の輪郭から始める(枠に対する割合)
      const [x0, y0, x1, y1] = target.box
      const rel = (v: number, lo: number, hi: number) => Math.round(Math.min(Math.max((v - lo) / Math.max(hi - lo, 1e-6), 0), 1) * 1000) / 1000
      patch = { ...patch, points: target.outline.map(([x, y]) => [rel(x, x0, x1), rel(y, y0, y1)] as Point) }
    }
    updateItem(target, patch)
  }

  /** 重なり順: 一番前・一つ前・一つ後ろ・一番後ろ */
  function restack(how: 'front' | 'forward' | 'backward' | 'back') {
    if (!target) return
    const zs = items.map(item => item.z)
    const z = how === 'front' ? Math.max(...zs) + 1 : how === 'back' ? Math.min(...zs) - 1 : target.z + (how === 'forward' ? 1 : -1)
    updateItem(target, { z: Math.max(-99, Math.min(99, z)) })
  }

  function addFloat() {
    if (!current?.tree || current.floats.length >= MAX_FLOATS) return
    const after = target?.kind === 'grid' ? items.filter(i => i.kind === 'grid').indexOf(target) + 1 : gridCount
    const z = Math.max(0, ...items.map(i => i.z)) + 1
    const item: FloatPanel = { shape: 'rect', rect: [0.3, 0.35, 0.7, 0.6], after, z }
    editCurrent({ floats: [...current.floats, item] })
    setSelection({ kind: 'item', key: `f${current.floats.length}` })
  }

  function removeFloat(index: number) {
    if (!current) return
    editCurrent({ floats: current.floats.filter((_, i) => i !== index) })
    setSelection(null)
  }

  function setTilt(value: number) {
    if (!current?.tree || selection?.kind !== 'divider' || !selectedSplit || !isSplit(selectedSplit)) return
    const tilts = selectedSplit.tilts ?? Array(selectedSplit.items.length - 1).fill(0)
    const next: Split = { ...selectedSplit, tilts: tilts.map((t, i) => (i === selection.index ? value : t)) }
    if (next.tilts!.every(t => t === 0)) delete next.tilts
    editCurrent({ tree: setAt(current.tree, selection.path, next) })
  }

  function addVertex() {
    const pts = target?.leaf.points
    if (!target || !pts) return
    // 一番長い辺の真ん中に足す
    let at = 0
    let longest = -1
    pts.forEach((p, i) => {
      const q = pts[(i + 1) % pts.length]
      const d = Math.hypot(q[0] - p[0], q[1] - p[1])
      if (d > longest) { longest = d; at = i }
    })
    const q = pts[(at + 1) % pts.length]
    const mid: Point = [(pts[at][0] + q[0]) / 2, (pts[at][1] + q[1]) / 2]
    updateItem(target, { points: [...pts.slice(0, at + 1), mid, ...pts.slice(at + 1)] })
  }

  function removeVertex(vertex: number) {
    const pts = target?.leaf.points
    if (!target || !pts || pts.length <= 3) return
    updateItem(target, { points: pts.filter((_, i) => i !== vertex) })
  }

  // ---- ポインター(ドラッグ・手書き) ----
  function pagePoint(e: ReactPointerEvent): Point | null {
    const svg = svgRef.current
    if (!svg) return null
    const r = svg.getBoundingClientRect()
    return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]
  }

  function capture(e: ReactPointerEvent<SVGElement>) {
    e.preventDefault()
    e.stopPropagation()
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
  }

  function startDivider(line: DividerLine, e: ReactPointerEvent<SVGElement>) {
    if (busy || saving || drawMode) return
    capture(e)
    setSelection({ kind: 'divider', path: line.path, index: line.index })
    setDrag({ kind: 'divider', line })
  }

  function startItem(item: Item, e: ReactPointerEvent<SVGElement>) {
    if (busy || saving || drawMode) return
    setSelection({ kind: 'item', key: item.key })
    if (item.kind !== 'float') return
    const p = pagePoint(e)
    if (!p) return
    capture(e)
    setDrag({ kind: 'move', index: item.index, start: p, rect: item.leaf.rect })
  }

  function onSvgPointerDown(e: ReactPointerEvent<SVGSVGElement>) {
    if (!drawMode || !target) return
    const p = pagePoint(e)
    if (!p) return
    e.preventDefault()
    svgRef.current?.setPointerCapture?.(e.pointerId)
    setDrawing([p])
  }

  function onPointerMove(e: ReactPointerEvent) {
    const p = pagePoint(e)
    if (!p || !current?.tree) return
    if (drawing) {
      setDrawing(prev => (prev ? [...prev, p] : prev))
      return
    }
    if (!drag) return
    const round = (v: number) => Math.round(v * 1000) / 1000
    if (drag.kind === 'move' || drag.kind === 'resize') {
      const f = current.floats[drag.index]
      if (!f) return
      const [x0, y0, x1, y1] = drag.rect
      let rect: Box
      if (drag.kind === 'move') {
        const dx = Math.min(Math.max(p[0] - drag.start[0], -x0), 1 - x1)
        const dy = Math.min(Math.max(p[1] - drag.start[1], -y0), 1 - y1)
        rect = [x0 + dx, y0 + dy, x1 + dx, y1 + dy]
      } else {
        rect = [x0, y0, Math.min(Math.max(p[0], x0 + 0.05), 1), Math.min(Math.max(p[1], y0 + 0.05), 1)]
      }
      editCurrent({ floats: current.floats.map((x, i) => (i === drag.index ? { ...x, rect: rect.map(round) as Box } : x)) })
      return
    }
    if (drag.kind === 'vertex') {
      const item = items.find(i => i.key === drag.key)
      if (!item?.leaf.points) return
      const [x0, y0, x1, y1] = item.box
      const rel: Point = [
        round(Math.min(Math.max((p[0] - x0) / (x1 - x0), 0), 1)),
        round(Math.min(Math.max((p[1] - y0) / (y1 - y0), 0), 1)),
      ]
      updateItem(item, { points: item.leaf.points.map((q, i) => (i === drag.vertex ? rel : q)) })
      return
    }
    const { line } = drag
    const node = nodeAt(current.tree, line.path)
    if (!isSplit(node)) return
    const [x0, y0, x1, y1] = line.box
    // 分割の中での位置(rows は上から、cols は右から)
    const f = line.dir === 'rows' ? (p[1] - y0) / (y1 - y0) : (x1 - p[0]) / (x1 - x0)
    const before = node.sizes.slice(0, line.index).reduce((a, b) => a + b, 0)
    const pair = node.sizes[line.index] + node.sizes[line.index + 1]
    const first = Math.min(Math.max(f - before, MIN_SHARE), pair - MIN_SHARE)
    const sizes = node.sizes.map((s, i) => (i === line.index ? first : i === line.index + 1 ? pair - first : s))
    editCurrent({ tree: setAt(current.tree, line.path, { ...node, sizes: sizes.map(round) }) })
  }

  function onPointerUp() {
    if (drawing && target) {
      const [x0, y0, x1, y1] = target.box
      const rel = drawing.map(([x, y]) => [
        Math.min(Math.max((x - x0) / (x1 - x0), 0), 1),
        Math.min(Math.max((y - y0) / (y1 - y0), 0), 1),
      ] as Point)
      const points = simplify(rel).slice(0, 400).map(([x, y]) => [Math.round(x * 1000) / 1000, Math.round(y * 1000) / 1000] as Point)
      if (points.length >= 3) updateItem(target, { shape: 'polygon', points })
      else setNote('線が短すぎます。コマの中をぐるっと囲むように描いてください。')
      setDrawing(null)
      setDrawMode(false)
    }
    setDrag(null)
  }

  async function save() {
    setSaving(true)
    setError(null)
    setNote(null)
    try {
      const res = await fetch(`${apiOrigin}/api/manga-v2/${storyId}/page-layout`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          template,
          pages: pages.map(p => (p.tree ? { tree: p.tree, gaps: p.gaps, style: p.style, floats: p.floats } : { layout: p.layout })),
        }),
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
  const W = 300
  const H = W / PAGE_ASPECT
  const tiltValue = selection?.kind === 'divider' && selectedSplit && isSplit(selectedSplit)
    ? (selectedSplit.tilts?.[selection.index] ?? 0)
    : 0
  const readingOf = (item: Item) => items.indexOf(item)

  if (!view) return error ? <p className="ple-error" role="alert">{error}</p> : <p className="story-muted">読み込み中…</p>

  return (
    <div className="ple">
      <p className="story-muted">
        {view.explicit
          ? 'ページごとのコマ割りを決めてあります。'
          : `今はテンプレートで割り付けています(変えて保存すると、ページごとのコマ割りになります)。`}
        数字はコマに入るシーンの番号です(読む順にコマに入ります)。境目はドラッグで動かし、押して選ぶと傾けられます。
        コマを押すと、形・枠線・重なり順を変えられます。
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
          <div className="ple-large" onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerLeave={onPointerUp}>
            {current.tree ? (
              <svg ref={svgRef} className={drawMode ? 'ple-svg ple-svg--draw' : 'ple-svg'} viewBox={`0 0 ${W} ${H}`} width={W} height={H}
                role="img" aria-label={`${selected + 1}ページ目のコマ割り`} onPointerDown={onSvgPointerDown}>
                <rect x={0} y={0} width={W} height={H} className="ple-paper" style={{ fill: current.style.background }}
                  onClick={() => !drawMode && setSelection(null)} />
                {byLayer(items).map(item => {
                  const [cx, cy] = centroid(item.outline)
                  const on = target?.key === item.key
                  const label = labels(selected)[readingOf(item)]
                  const { stroke, width: line } = strokeOf(item.leaf, current.style)
                  return (
                    <g key={item.key}>
                      <polygon points={item.outline.map(([x, y]) => `${x * W},${y * H}`).join(' ')}
                        className={`ple-panel${label ? '' : ' ple-panel--empty'}${on ? ' ple-panel--on' : ''}${item.kind === 'float' ? ' ple-panel--float' : ''}`}
                        style={on ? undefined : { stroke: line ? stroke : '#9a9ab0', strokeWidth: line ? Math.max(0.8, (line * W) / PAGE_W) : 0.8, strokeDasharray: line ? undefined : '2 4' }}
                        onPointerDown={e => startItem(item, e)} />
                      <text x={cx * W} y={cy * H} className="ple-label" fontSize={W / 14}>{label ?? '空'}</text>
                    </g>
                  )
                })}
                {!drawMode && dividers.map(d => {
                  const on = selection?.kind === 'divider' && selection.path.join('.') === d.path.join('.') && selection.index === d.index
                  const [[ax, ay], [bx, by]] = d.ends
                  return (
                    <line key={`${d.path.join('.')}-${d.index}`} x1={ax * W} y1={ay * H} x2={bx * W} y2={by * H}
                      className={on ? 'ple-divider ple-divider--active' : 'ple-divider'}
                      style={{ cursor: d.dir === 'rows' ? 'ns-resize' : 'ew-resize' }}
                      onPointerDown={e => startDivider(d, e)} />
                  )
                })}
                {!drawMode && target?.kind === 'float' && (
                  <circle cx={target.leaf.rect[2] * W} cy={target.leaf.rect[3] * H} r={7} className="ple-vertex ple-resize"
                    onPointerDown={e => { capture(e); setDrag({ kind: 'resize', index: target.index, rect: target.leaf.rect }) }} />
                )}
                {!drawMode && target?.leaf.shape === 'polygon' && (target.leaf.points?.length ?? 0) <= 40 &&
                  target.leaf.points!.map(([x, y], i) => {
                    const [x0, y0, x1, y1] = target.box
                    return (
                      <circle key={i} cx={(x0 + (x1 - x0) * x) * W} cy={(y0 + (y1 - y0) * y) * H} r={6}
                        className="ple-vertex" onPointerDown={e => { capture(e); setDrag({ kind: 'vertex', key: target.key, vertex: i }) }}
                        onDoubleClick={() => removeVertex(i)} />
                    )
                  })}
                {drawing && (
                  <polyline points={drawing.map(([x, y]) => `${x * W},${y * H}`).join(' ')} className="ple-stroke" />
                )}
              </svg>
            ) : (
              <PageSvg page={current} labels={labels(selected)} width={W} />
            )}
          </div>

          <div className="ple-side">
            <div className="ple-row">
              <strong>{selected + 1}ページ目</strong>
              <label>
                マス目のコマ数
                <select value={gridCount || panelCount(current)} disabled={off} onChange={e => void changeCount(Number(e.target.value))}>
                  {Array.from({ length: MAX_PANELS }, (_, i) => i + 1).map(n => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
              <button type="button" disabled={off} onClick={addPage}>後ろにページを足す</button>
              <button type="button" disabled={off || pages.length <= 1} onClick={removePage}>このページを消す</button>
            </div>
            {!current.tree && (
              <p className="story-muted">取り込んだ作品から写したページです(形はそのまま)。パターンを選ぶと置き換わり、形も直せます。</p>
            )}
            {current.tree && (
              <div className="ple-row">
                <button type="button" disabled={off || current.floats.length >= MAX_FLOATS} onClick={addFloat}
                  title="マス目とは別に、ページの好きな位置にコマを重ねて置きます(ドラッグで移動、右下の丸で大きさ)">
                  コマを重ねて置く
                </button>
              </div>
            )}

            {target && (
              <div className="ple-box">
                <strong>
                  {target.kind === 'float' ? '重ねたコマ' : 'コマ'}(読む順 {readingOf(target) + 1} 番目)
                </strong>
                <div className="ple-row">
                  <span>重なり順</span>
                  <button type="button" disabled={off} onClick={() => restack('front')}>最前面へ</button>
                  <button type="button" disabled={off} onClick={() => restack('forward')}>前面へ</button>
                  <button type="button" disabled={off} onClick={() => restack('backward')}>背面へ</button>
                  <button type="button" disabled={off} onClick={() => restack('back')}>最背面へ</button>
                </div>
                {target.kind === 'float' && (
                  <div className="ple-row">
                    <label>
                      読む順
                      <select value={Math.min(target.leaf.after, gridCount)} disabled={off}
                        onChange={e => updateItem(target, { after: Number(e.target.value) } as Partial<FloatPanel>)}>
                        {Array.from({ length: gridCount + 1 }, (_, i) => i).map(i => (
                          <option key={i} value={i}>{i === 0 ? '先頭' : `マス目の ${i} コマ目の後ろ`}</option>
                        ))}
                      </select>
                    </label>
                    <button type="button" disabled={off} onClick={() => removeFloat(target.index)}>この重ねたコマを消す</button>
                  </div>
                )}
                <div className="ple-row">
                  <select value={target.leaf.shape} disabled={off} aria-label="コマの形"
                    onChange={e => editLeaf({ shape: e.target.value as ShapeKind })}>
                    {SHAPES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
                  </select>
                  {target.leaf.shape === 'triangle' && (
                    <select value={target.leaf.point ?? 'up'} disabled={off} aria-label="三角形の向き"
                      onChange={e => editLeaf({ point: e.target.value as TrianglePoint })}>
                      {TRIANGLE_POINTS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                    </select>
                  )}
                  <label className="ple-check">
                    <input type="checkbox" checked={target.leaf.border !== false} disabled={off}
                      onChange={e => editLeaf({ border: e.target.checked ? undefined : false })} />
                    枠線
                  </label>
                </div>
                {target.leaf.border !== false && (
                  <div className="ple-row">
                    <label className="ple-check">
                      <input type="checkbox" disabled={off}
                        checked={target.leaf.border_color !== undefined || target.leaf.border_width !== undefined}
                        onChange={e => editLeaf(e.target.checked
                          ? { border_color: current.style.border_color, border_width: current.style.border_width }
                          : { border_color: undefined, border_width: undefined })} />
                      このコマだけ線を変える
                    </label>
                    {(target.leaf.border_color !== undefined || target.leaf.border_width !== undefined) && (
                      <>
                        <input type="color" aria-label="このコマの枠線の色" disabled={off}
                          value={target.leaf.border_color ?? current.style.border_color}
                          onChange={e => editLeaf({ border_color: e.target.value })} />
                        <label className="ple-range">
                          <input type="range" min={0} max={20} step={1} disabled={off} aria-label="このコマの枠線の太さ"
                            value={target.leaf.border_width ?? current.style.border_width}
                            onChange={e => editLeaf({ border_width: Number(e.target.value) })} />
                          {target.leaf.border_width ?? current.style.border_width}px
                        </label>
                      </>
                    )}
                  </div>
                )}
                <div className="ple-row">
                  <label className="ple-check">
                    <input type="checkbox" checked={!!target.leaf.cutout} disabled={off}
                      onChange={e => editLeaf({ cutout: e.target.checked ? DEFAULT_CUTOUT : undefined })} />
                    背景を消す(人物だけを見せる)
                  </label>
                  {target.leaf.cutout && (
                    <>
                      <select value={target.leaf.cutout.fit ?? 'cover'} disabled={off} aria-label="人物の収め方"
                        onChange={e => editLeaf({ cutout: { ...target.leaf.cutout!, fit: e.target.value as 'cover' | 'contain' } })}>
                        <option value="cover">枠いっぱい(はみ出しは切る)</option>
                        <option value="contain">人物全体を収める(足元を下に)</option>
                      </select>
                      <label className="ple-check">
                        <input type="checkbox" checked={!!target.leaf.cutout.fade} disabled={off}
                          onChange={e => editLeaf({ cutout: { ...target.leaf.cutout!, fade: e.target.checked } })} />
                        下端をぼかす
                      </label>
                      <label className="ple-check">
                        <input type="checkbox" checked={target.leaf.cutout.outline} disabled={off}
                          onChange={e => editLeaf({ cutout: { ...target.leaf.cutout!, outline: e.target.checked } })} />
                        縁取り
                      </label>
                      {target.leaf.cutout.outline && (
                        <>
                          <input type="color" aria-label="縁取りの色" value={target.leaf.cutout.outline_color} disabled={off}
                            onChange={e => editLeaf({ cutout: { ...target.leaf.cutout!, outline_color: e.target.value } })} />
                          <label className="ple-range">
                            <input type="range" min={1} max={40} step={1} aria-label="縁取りの太さ" value={target.leaf.cutout.outline_width} disabled={off}
                              onChange={e => editLeaf({ cutout: { ...target.leaf.cutout!, outline_width: Number(e.target.value) } })} />
                            {target.leaf.cutout.outline_width}px
                          </label>
                        </>
                      )}
                    </>
                  )}
                </div>
                {target.leaf.cutout && (() => {
                  const scene = assigned.pages[selected]?.[readingOf(target)]
                  return scene
                    ? <CutoutTools key={scene.id} apiOrigin={apiOrigin} sceneId={scene.id} fileUrl={fileUrl} disabled={off} />
                    : <p className="story-muted">このコマに入るシーンがありません。</p>
                })()}
                {target.leaf.shape === 'polygon' && (
                  <div className="ple-row">
                    <button type="button" disabled={off || drawMode} onClick={addVertex}>頂点を足す</button>
                    <button type="button" disabled={off} className={drawMode ? 'ple-on' : ''} onClick={() => setDrawMode(m => !m)}>
                      {drawMode ? '手書きをやめる' : '手書きで描く'}
                    </button>
                  </div>
                )}
                <p className="story-muted">
                  {target.leaf.shape === 'polygon'
                    ? drawMode
                      ? '左の図に、コマの輪郭をぐるっと一筆で描いてください(枠の中に収めます)。'
                      : '頂点をドラッグで動かせます(ダブルクリックで消す)。「手書きで描く」で輪郭を描き直せます。'
                    : target.kind === 'float'
                      ? 'ドラッグで移動、右下の丸で大きさを変えます。前面のコマに隠れた所には、後ろのコマの吹き出しを置きません。'
                      : '形はコマの枠に収め、隣のコマにははみ出しません。吹き出しは形の内側に置きます。'}
                </p>
              </div>
            )}

            {selection?.kind === 'divider' && (
              <div className="ple-box">
                <strong>境目の傾き</strong>
                <label className="ple-range">
                  <input type="range" min={-MAX_TILT} max={MAX_TILT} step={0.01} value={tiltValue} disabled={off}
                    onChange={e => setTilt(Number(e.target.value))} />
                  {tiltValue === 0 ? 'まっすぐ' : tiltValue.toFixed(2)}
                </label>
                <button type="button" disabled={off || tiltValue === 0} onClick={() => setTilt(0)}>まっすぐに戻す</button>
                <p className="story-muted">傾けると、両側のコマが同じ角度の台形になります。</p>
              </div>
            )}

            {current.tree && (
              <div className="ple-box">
                <strong>コマの間隔・ページの余白</strong>
                {GAP_FIELDS.map(f => (
                  <label key={f.key} className="ple-range">
                    {f.label}
                    <input type="range" min={0} max={f.max} step={1} value={current.gaps[f.key]} disabled={off}
                      onChange={e => editCurrent({ gaps: { ...current.gaps, [f.key]: Number(e.target.value) } })} />
                    {current.gaps[f.key] === 0 ? '0(接する)' : `${current.gaps[f.key]}px`}
                  </label>
                ))}
                <div className="ple-row">
                  <button type="button" disabled={off} onClick={() => editCurrent({ gaps: { gutter_x: 0, gutter_y: 0, margin: current.gaps.margin } })}>
                    コマをぴったり接する
                  </button>
                  <button type="button" disabled={off} onClick={() => editCurrent({ gaps: { ...current.gaps, margin: 0 } })}>
                    余白なし(裁ち落とし)
                  </button>
                  <button type="button" disabled={off} onClick={() => editCurrent({ gaps: DEFAULT_GAPS })}>既定に戻す</button>
                </div>
                <p className="story-muted">0 にするとコマどうしが同じ線で接します。ページの端に接する辺には枠線を描きません。</p>
              </div>
            )}

            {current.tree && (
              <div className="ple-box">
                <strong>色と枠線</strong>
                <div className="ple-row">
                  <label className="ple-range">
                    地の色(余白・間隔)
                    <input type="color" value={current.style.background} disabled={off}
                      onChange={e => editCurrent({ style: { ...current.style, background: e.target.value } })} />
                  </label>
                  <label className="ple-range">
                    枠線の色
                    <input type="color" value={current.style.border_color} disabled={off}
                      onChange={e => editCurrent({ style: { ...current.style, border_color: e.target.value } })} />
                  </label>
                </div>
                <label className="ple-range">
                  枠線の太さ
                  <input type="range" min={0} max={20} step={1} value={current.style.border_width} disabled={off}
                    onChange={e => editCurrent({ style: { ...current.style, border_width: Number(e.target.value) } })} />
                  {current.style.border_width === 0 ? 'なし' : `${current.style.border_width}px`}
                </label>
                <div className="ple-row">
                  <button type="button" disabled={off}
                    onClick={() => editCurrent({ style: { background: '#000000', border_color: '#ffffff', border_width: current.style.border_width } })}>
                    黒地(回想など)
                  </button>
                  <button type="button" disabled={off} onClick={() => editCurrent({ style: DEFAULT_STYLE })}>既定に戻す</button>
                </div>
              </div>
            )}

            <div className="ple-patterns">
              {(patterns[gridCount] ?? []).map(p => (
                <button key={p.id} type="button" className="ple-pattern" disabled={off} title={p.label}
                  onClick={() => { editCurrent({ tree: p.tree, layout: null, panels: [] }); setSelection(null) }}>
                  <PageSvg page={{ ...current, tree: p.tree, layout: null, panels: [] }} labels={[]} width={52} />
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
