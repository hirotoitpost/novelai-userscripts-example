import { PointerEvent as ReactPointerEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { MangaV2Scene } from './MangaV2Studio'
import {
  Box, DEFAULT_GAPS, DividerLine, Gaps, Leaf, MAX_TILT, MIN_SHARE, PAGE_H, PAGE_W, Point, ShapeKind, Split, Tree, TrianglePoint,
  count, isSplit, layoutGeometry, leafOf, nodeAt, setAt, simplify, tidyLeaf,
} from './layoutTree'
import './PageLayoutEditor.css'

/**
 * 漫画v2のページごとのコマ割り: 確認(ページごとにどのシーンがどのコマに入るか)・変更(コマ数からパターンを選ぶ、
 * コマの境目をドラッグして大きさを変える・傾ける、コマの形(楕円・三角形・自由な多角形・手書き)と枠線、
 * ページを足す・消す)。保存すると合成・ダウンロード・コマの生成サイズが従う。
 */

interface ViewPage {
  source: 'layout' | 'template'
  layout: number | null
  count: number
  scene_ids: number[]
  tree: Tree | null
  gaps: Gaps
  spread: boolean
  panels: Point[][]
}

interface View { explicit: boolean; template: string; scenes: number; pages: ViewPage[] }

interface Pattern { id: string; label: string; tree: Tree }

/** 画面で直している1ページ。tree が無いのは取り込んだ作品から写したページ(斜めの枠など。形はそのまま) */
interface DraftPage { tree: Tree | null; layout: number | null; panels: Point[][]; gaps: Gaps }

interface Props {
  apiOrigin: string
  storyId: number
  template: string
  scenes: MangaV2Scene[]
  busy: boolean
  /** 保存したあと(コマの絵の一覧を読み直すなど) */
  onSaved?: () => void
}

const MAX_PANELS = 12
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

/** ページの上のコマの輪郭(ページに対する割合)。写したページはサーバーの輪郭のまま */
function outlinesOf(page: DraftPage): Point[][] {
  if (page.tree) return layoutGeometry(page.tree, page.gaps).panels.map(p => p.outline)
  return page.panels
}

const GAP_FIELDS: { key: keyof Gaps; label: string; max: number }[] = [
  { key: 'gutter_x', label: '横の間隔', max: 120 },
  { key: 'gutter_y', label: '縦の間隔', max: 120 },
  { key: 'margin', label: 'ページの余白', max: 200 },
]

function centroid(points: Point[]): Point {
  return [points.reduce((s, p) => s + p[0], 0) / points.length, points.reduce((s, p) => s + p[1], 0) / points.length]
}

const pathKey = (path: number[]) => path.join('.')

type Selection = { kind: 'panel'; path: number[] } | { kind: 'divider'; path: number[]; index: number } | null

function PageSvg({ page, labels, width }: { page: DraftPage; labels: string[]; width: number }) {
  const height = width / PAGE_ASPECT
  return (
    <svg className="ple-svg" viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img"
      aria-label={`${outlinesOf(page).length}コマのページ`}>
      <rect x={0} y={0} width={width} height={height} className="ple-paper" />
      {outlinesOf(page).map((points, i) => {
        const [cx, cy] = centroid(points)
        return (
          <g key={i}>
            <polygon points={points.map(([x, y]) => `${x * width},${y * height}`).join(' ')}
              className={labels[i] ? 'ple-panel' : 'ple-panel ple-panel--empty'} />
            <text x={cx * width} y={cy * height} className="ple-label" fontSize={Math.max(9, width / 14)}>{labels[i] ?? '空'}</text>
          </g>
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
  const [selection, setSelection] = useState<Selection>(null)
  // ドラッグ中のもの: 境目(位置)・頂点
  const [drag, setDrag] = useState<{ kind: 'divider'; line: DividerLine } | { kind: 'vertex'; path: number[]; vertex: number; box: Box } | null>(null)
  // 手書きで輪郭を描いている途中の点(本文領域の単位正方形)
  const [drawing, setDrawing] = useState<Point[] | null>(null)
  const [drawMode, setDrawMode] = useState(false)
  const svgRef = useRef<SVGSVGElement>(null)

  const load = useCallback(async () => {
    setError(null)
    const res = await fetch(`${apiOrigin}/api/manga-v2/${storyId}/page-layout?template=${encodeURIComponent(template)}`)
    if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`)
    const data: View = await res.json()
    setView(data)
    setPages(data.pages.map(p => ({ tree: p.tree, layout: p.source === 'layout' ? p.layout : null, panels: p.panels, gaps: p.gaps ?? DEFAULT_GAPS })))
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
      const n = page.tree ? count(page.tree) : page.panels.length
      out.push(scenes.slice(next, next + n))
      next += n
    }
    return { pages: out, rest: Math.max(0, scenes.length - next) }
  }, [pages, scenes])

  const current = pages[selected]
  const currentCount = current ? (current.tree ? count(current.tree) : current.panels.length) : 0
  const geometry = useMemo(() => (current?.tree ? layoutGeometry(current.tree, current.gaps) : null), [current])

  useEffect(() => {
    if (currentCount > 0) loadPatterns(currentCount).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentCount])

  useEffect(() => { setSelection(null); setDrawMode(false) }, [selected])

  function editPage(index: number, page: DraftPage) {
    setPages(prev => prev.map((p, i) => (i === index ? page : p)))
    setDirty(true)
  }

  function editTree(tree: Tree) {
    if (current) editPage(selected, { ...current, tree })
  }

  async function changeCount(n: number) {
    try {
      const list = await loadPatterns(n)
      if (list[0]) { editPage(selected, { tree: list[0].tree, layout: null, panels: [], gaps: current?.gaps ?? DEFAULT_GAPS }); setSelection(null) }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  function addPage() {
    const tree: Tree = { dir: 'rows', sizes: [0.5, 0.5], items: [{ dir: 'cols', sizes: [0.5, 0.5], items: ['panel', 'panel'] }, { dir: 'cols', sizes: [0.5, 0.5], items: ['panel', 'panel'] }] }
    setPages(prev => [...prev.slice(0, selected + 1), { tree, layout: null, panels: [], gaps: current?.gaps ?? DEFAULT_GAPS }, ...prev.slice(selected + 1)])
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
  const selectedPanel = selection?.kind === 'panel' && geometry
    ? geometry.panels.find(p => pathKey(p.path) === pathKey(selection.path)) ?? null
    : null
  const selectedSplit = selection?.kind === 'divider' && current?.tree ? nodeAt(current.tree, selection.path) : null

  function editLeaf(patch: Partial<Leaf>) {
    if (!current?.tree || !selectedPanel) return
    const leaf = { ...selectedPanel.leaf, ...patch }
    if (patch.shape === 'polygon' && !leaf.points?.length) {
      // 今の形の輪郭から始める(枠に対する割合)
      const [x0, y0, x1, y1] = selectedPanel.box
      const rel = (v: number, lo: number, hi: number) => Math.round(Math.min(Math.max((v - lo) / Math.max(hi - lo, 1e-6), 0), 1) * 1000) / 1000
      leaf.points = selectedPanel.outline.map(([x, y]) => [rel(x, x0, x1), rel(y, y0, y1)] as Point)
    }
    editTree(setAt(current.tree, selectedPanel.path, tidyLeaf(leaf)))
  }

  function setTilt(value: number) {
    if (!current?.tree || selection?.kind !== 'divider' || !selectedSplit || !isSplit(selectedSplit)) return
    const tilts = selectedSplit.tilts ?? Array(selectedSplit.items.length - 1).fill(0)
    const next: Split = { ...selectedSplit, tilts: tilts.map((t, i) => (i === selection.index ? value : t)) }
    if (next.tilts!.every(t => t === 0)) delete next.tilts
    editTree(setAt(current.tree, selection.path, next))
  }

  function addVertex() {
    if (!selectedPanel?.leaf.points) return
    const pts = selectedPanel.leaf.points
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
    editLeaf({ points: [...pts.slice(0, at + 1), mid, ...pts.slice(at + 1)] })
  }

  function removeVertex(vertex: number) {
    const pts = selectedPanel?.leaf.points
    if (!pts || pts.length <= 3) return
    editLeaf({ points: pts.filter((_, i) => i !== vertex) })
  }

  // ---- ポインター(ドラッグ・手書き) ----
  function unitPoint(e: ReactPointerEvent): Point | null {
    const svg = svgRef.current
    if (!svg) return null
    const r = svg.getBoundingClientRect()
    return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]
  }

  function startDivider(line: DividerLine, e: ReactPointerEvent<SVGElement>) {
    if (busy || saving || drawMode) return
    e.preventDefault()
    e.stopPropagation()
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
    setSelection({ kind: 'divider', path: line.path, index: line.index })
    setDrag({ kind: 'divider', line })
  }

  function startVertex(vertex: number, e: ReactPointerEvent<SVGElement>) {
    if (!selectedPanel) return
    e.preventDefault()
    e.stopPropagation()
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
    setDrag({ kind: 'vertex', path: selectedPanel.path, vertex, box: selectedPanel.box })
  }

  function onSvgPointerDown(e: ReactPointerEvent<SVGSVGElement>) {
    if (!drawMode || !selectedPanel) return
    const p = unitPoint(e)
    if (!p) return
    e.preventDefault()
    svgRef.current?.setPointerCapture?.(e.pointerId)
    setDrawing([p])
  }

  function onPointerMove(e: ReactPointerEvent) {
    const p = unitPoint(e)
    if (!p || !current?.tree) return
    if (drawing) {
      setDrawing(prev => (prev ? [...prev, p] : prev))
      return
    }
    if (!drag) return
    if (drag.kind === 'vertex') {
      const node = nodeAt(current.tree, drag.path)
      const leaf = leafOf(node)
      if (!leaf.points) return
      const [x0, y0, x1, y1] = drag.box
      const rel: Point = [
        Math.round(Math.min(Math.max((p[0] - x0) / (x1 - x0), 0), 1) * 1000) / 1000,
        Math.round(Math.min(Math.max((p[1] - y0) / (y1 - y0), 0), 1) * 1000) / 1000,
      ]
      editTree(setAt(current.tree, drag.path, tidyLeaf({ ...leaf, points: leaf.points.map((q, i) => (i === drag.vertex ? rel : q)) })))
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
    editTree(setAt(current.tree, line.path, { ...node, sizes: sizes.map(s => Math.round(s * 1000) / 1000) }))
  }

  function onPointerUp() {
    if (drawing && selectedPanel && current?.tree) {
      const [x0, y0, x1, y1] = selectedPanel.box
      const rel = drawing.map(([x, y]) => [
        Math.min(Math.max((x - x0) / (x1 - x0), 0), 1),
        Math.min(Math.max((y - y0) / (y1 - y0), 0), 1),
      ] as Point)
      const points = simplify(rel).slice(0, 400).map(([x, y]) => [Math.round(x * 1000) / 1000, Math.round(y * 1000) / 1000] as Point)
      if (points.length >= 3) {
        editTree(setAt(current.tree, selectedPanel.path, tidyLeaf({ ...selectedPanel.leaf, shape: 'polygon', points })))
      } else {
        setNote('線が短すぎます。コマの中をぐるっと囲むように描いてください。')
      }
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
        body: JSON.stringify({ template, pages: pages.map(p => (p.tree ? { tree: p.tree, gaps: p.gaps } : { layout: p.layout })) }),
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

  if (!view) return error ? <p className="ple-error" role="alert">{error}</p> : <p className="story-muted">読み込み中…</p>

  return (
    <div className="ple">
      <p className="story-muted">
        {view.explicit
          ? 'ページごとのコマ割りを決めてあります。'
          : `今はテンプレートで割り付けています(変えて保存すると、ページごとのコマ割りになります)。`}
        数字はコマに入るシーンの番号です(先頭から順にコマに入ります)。境目はドラッグで動かし、押して選ぶと傾けられます。
        コマを押すと、形・枠線を変えられます。
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
            {current.tree && geometry ? (
              <svg ref={svgRef} className={drawMode ? 'ple-svg ple-svg--draw' : 'ple-svg'} viewBox={`0 0 ${W} ${H}`} width={W} height={H}
                role="img" aria-label={`${selected + 1}ページ目のコマ割り`} onPointerDown={onSvgPointerDown}>
                <rect x={0} y={0} width={W} height={H} className="ple-paper" onClick={() => !drawMode && setSelection(null)} />
                {geometry.panels.map(p => {
                  const [cx, cy] = centroid(p.outline)
                  const on = selectedPanel && pathKey(selectedPanel.path) === pathKey(p.path)
                  const label = labels(selected)[p.index]
                  return (
                    <g key={pathKey(p.path)}>
                      <polygon points={p.outline.map(([x, y]) => `${x * W},${y * H}`).join(' ')}
                        className={`ple-panel${label ? '' : ' ple-panel--empty'}${on ? ' ple-panel--on' : ''}${p.leaf.border === false ? ' ple-panel--borderless' : ''}`}
                        onClick={() => !drawMode && setSelection({ kind: 'panel', path: p.path })} />
                      <text x={cx * W} y={cy * H} className="ple-label" fontSize={W / 14}>{label ?? '空'}</text>
                    </g>
                  )
                })}
                {!drawMode && geometry.dividers.map(d => {
                  const on = selection?.kind === 'divider' && pathKey(selection.path) === pathKey(d.path) && selection.index === d.index
                  const [[ax, ay], [bx, by]] = d.ends
                  return (
                    <line key={`${pathKey(d.path)}-${d.index}`} x1={ax * W} y1={ay * H} x2={bx * W} y2={by * H}
                      className={on ? 'ple-divider ple-divider--active' : 'ple-divider'}
                      style={{ cursor: d.dir === 'rows' ? 'ns-resize' : 'ew-resize' }}
                      onPointerDown={e => startDivider(d, e)} />
                  )
                })}
                {!drawMode && selectedPanel?.leaf.shape === 'polygon' && selectedPanel.leaf.points!.length <= 40 &&
                  selectedPanel.leaf.points!.map(([x, y], i) => {
                    const [x0, y0, x1, y1] = selectedPanel.box
                    return (
                      <circle key={i} cx={(x0 + (x1 - x0) * x) * W} cy={(y0 + (y1 - y0) * y) * H} r={6}
                        className="ple-vertex" onPointerDown={e => startVertex(i, e)}
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
                コマ数
                <select value={currentCount} disabled={off} onChange={e => void changeCount(Number(e.target.value))}>
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
              <div className="ple-box">
                <strong>コマの間隔・ページの余白</strong>
                {GAP_FIELDS.map(f => (
                  <label key={f.key} className="ple-range">
                    {f.label}
                    <input type="range" min={0} max={f.max} step={1} value={current.gaps[f.key]} disabled={off}
                      onChange={e => editPage(selected, { ...current, gaps: { ...current.gaps, [f.key]: Number(e.target.value) } })} />
                    {current.gaps[f.key] === 0 ? '0(接する)' : `${current.gaps[f.key]}px`}
                  </label>
                ))}
                <div className="ple-row">
                  <button type="button" disabled={off} onClick={() => editPage(selected, { ...current, gaps: { gutter_x: 0, gutter_y: 0, margin: current.gaps.margin } })}>
                    コマをぴったり接する
                  </button>
                  <button type="button" disabled={off} onClick={() => editPage(selected, { ...current, gaps: { ...current.gaps, margin: 0 } })}>
                    余白なし(裁ち落とし)
                  </button>
                  <button type="button" disabled={off} onClick={() => editPage(selected, { ...current, gaps: DEFAULT_GAPS })}>既定に戻す</button>
                </div>
                <p className="story-muted">0 にするとコマどうしが同じ線で接します。ページの端に接する辺には枠線を描きません。</p>
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

            {selectedPanel && (
              <div className="ple-box">
                <strong>コマ {selectedPanel.index + 1} の形</strong>
                <div className="ple-row">
                  <select value={selectedPanel.leaf.shape} disabled={off} aria-label="コマの形"
                    onChange={e => editLeaf({ shape: e.target.value as ShapeKind })}>
                    {SHAPES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
                  </select>
                  {selectedPanel.leaf.shape === 'triangle' && (
                    <select value={selectedPanel.leaf.point ?? 'up'} disabled={off} aria-label="三角形の向き"
                      onChange={e => editLeaf({ point: e.target.value as TrianglePoint })}>
                      {TRIANGLE_POINTS.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                    </select>
                  )}
                  <label className="ple-check">
                    <input type="checkbox" checked={selectedPanel.leaf.border !== false} disabled={off}
                      onChange={e => editLeaf({ border: e.target.checked ? undefined : false })} />
                    枠線
                  </label>
                </div>
                {selectedPanel.leaf.shape === 'polygon' && (
                  <div className="ple-row">
                    <button type="button" disabled={off || drawMode} onClick={addVertex}>頂点を足す</button>
                    <button type="button" disabled={off} className={drawMode ? 'ple-on' : ''}
                      onClick={() => setDrawMode(m => !m)}>
                      {drawMode ? '手書きをやめる' : '手書きで描く'}
                    </button>
                  </div>
                )}
                <p className="story-muted">
                  {selectedPanel.leaf.shape === 'polygon'
                    ? drawMode
                      ? '左の図に、コマの輪郭をぐるっと一筆で描いてください(枠の中に収めます)。'
                      : '頂点をドラッグで動かせます(ダブルクリックで消す)。「手書きで描く」で輪郭を描き直せます。'
                    : '形はコマの枠に収め、隣のコマにははみ出しません。吹き出しは形の内側に置きます。'}
                </p>
              </div>
            )}

            <div className="ple-patterns">
              {(patterns[currentCount] ?? []).map(p => (
                <button key={p.id} type="button" className="ple-pattern" disabled={off} title={p.label}
                  onClick={() => { editPage(selected, { tree: p.tree, layout: null, panels: [], gaps: current.gaps }); setSelection(null) }}>
                  <PageSvg page={{ tree: p.tree, layout: null, panels: [], gaps: current.gaps }} labels={[]} width={52} />
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
