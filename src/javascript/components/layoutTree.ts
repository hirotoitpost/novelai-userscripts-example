/**
 * ページのコマ割りの分割の木(src/python/manga_v2/layout.py と同じ形)と、画面に描くための輪郭の計算。
 * サーバーと同じく、ページ(1200×1700)の上で余白とコマの間隔を空けて計算し、ページに対する割合で返す。
 */

export const PAGE_W = 1200
export const PAGE_H = 1700

/** コマの間隔(横・縦)とページの余白(ピクセル)。0 でコマどうしが接する・裁ち落とし */
export interface Gaps { gutter_x: number; gutter_y: number; margin: number }
export const DEFAULT_GAPS: Gaps = { gutter_x: 16, gutter_y: 28, margin: 50 }

/** ページの見た目: 地の色(余白・コマの間隔)と、枠線の色・太さ(ピクセル。0 なら描かない) */
export interface PageStyle { background: string; border_color: string; border_width: number }
export const DEFAULT_STYLE: PageStyle = { background: '#ffffff', border_color: '#000000', border_width: 4 }

export type Point = [number, number]
export type Box = [number, number, number, number]
export type ShapeKind = 'rect' | 'ellipse' | 'triangle' | 'polygon'
export type TrianglePoint = 'up' | 'down' | 'left' | 'right'

/** 1コマ。"panel" はふつうのコマ(四角・枠線あり) */
export interface Leaf {
  shape: ShapeKind
  /** 三角形の尖る向き */
  point?: TrianglePoint
  /** 自由な多角形・手書きの輪郭の頂点(コマの枠に対する割合) */
  points?: Point[]
  /** false なら枠線を描かない */
  border?: boolean
  /** このコマだけの枠線の色・太さ(無ければページの設定) */
  border_color?: string
  border_width?: number
}

export interface Split {
  dir: 'rows' | 'cols'
  sizes: number[]
  /** 境目ごとの傾き(rows は左端が下がる向き、cols は上端が左へ寄る向きが正) */
  tilts?: number[]
  items: Tree[]
}

export type Tree = 'panel' | Leaf | Split

export const MIN_SHARE = 0.08
export const MAX_TILT = 0.25

export function isSplit(tree: Tree): tree is Split {
  return tree !== 'panel' && 'items' in tree
}

export function leafOf(tree: Tree): Leaf {
  return tree === 'panel' ? { shape: 'rect' } : (tree as Leaf)
}

export function count(tree: Tree): number {
  return isSplit(tree) ? tree.items.reduce((n, item) => n + count(item), 0) : 1
}

/** 子の木を置き換える(path は items の番号の並び) */
export function setAt(tree: Tree, path: number[], value: Tree): Tree {
  if (path.length === 0) return value
  if (!isSplit(tree)) return tree
  const [head, ...rest] = path
  return { ...tree, items: tree.items.map((item, i) => (i === head ? setAt(item, rest, value) : item)) }
}

export function nodeAt(tree: Tree, path: number[]): Tree {
  return path.reduce<Tree>((node, i) => (isSplit(node) ? node.items[i] : node), tree)
}

/** 葉を保存する形にする(何も決めていなければ "panel") */
export function tidyLeaf(leaf: Leaf): Tree {
  const out: Leaf = { shape: leaf.shape }
  if (leaf.shape === 'triangle') out.point = leaf.point ?? 'up'
  if (leaf.shape === 'polygon') out.points = leaf.points
  if (leaf.border === false) out.border = false
  if (leaf.border_color !== undefined) out.border_color = leaf.border_color
  if (leaf.border_width !== undefined) out.border_width = leaf.border_width
  return Object.keys(out).length === 1 && out.shape === 'rect' ? 'panel' : out
}

type Polygon = Point[]

function clip(polygon: Polygon, a: Point, b: Point, keepLeft: boolean): Polygon {
  const side = (p: Point) => (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
  const inside = (p: Point) => (keepLeft ? side(p) <= 0 : side(p) >= 0)
  const cross = (p: Point, q: Point): Point => {
    const sp = side(p)
    const t = sp / (sp - side(q))
    return [p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t]
  }
  const out: Polygon = []
  polygon.forEach((current, i) => {
    const previous = polygon[(i + polygon.length - 1) % polygon.length]
    if (inside(current)) {
      if (!inside(previous)) out.push(cross(previous, current))
      out.push(current)
    } else if (inside(previous)) {
      out.push(cross(previous, current))
    }
  })
  return out
}

function bounds(polygon: Polygon): Box {
  const xs = polygon.map(p => p[0])
  const ys = polygon.map(p => p[1])
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]
}

/** 境目の直線(読む順で index と index+1 の間)の2つの端 */
function dividerLine(split: Split, box: Box, at: number, tilt: number): [Point, Point] {
  const [x0, y0, x1, y1] = box
  if (split.dir === 'rows') return [[x0, y0 + (y1 - y0) * (at + tilt)], [x1, y0 + (y1 - y0) * (at - tilt)]]
  return [[x1 - (x1 - x0) * (at + tilt), y0], [x1 - (x1 - x0) * (at - tilt), y1]]
}

/** 葉の形の輪郭(枠 box に収めた形) */
function shapeOutline(leaf: Leaf, box: Box): Polygon {
  const [x0, y0, x1, y1] = box
  const w = x1 - x0
  const h = y1 - y0
  if (leaf.shape === 'ellipse') {
    return Array.from({ length: 48 }, (_, i) => {
      const a = (2 * Math.PI * i) / 48
      return [(x0 + x1) / 2 + (w / 2) * Math.cos(a), (y0 + y1) / 2 + (h / 2) * Math.sin(a)] as Point
    })
  }
  if (leaf.shape === 'triangle') {
    return {
      up: [[(x0 + x1) / 2, y0], [x1, y1], [x0, y1]],
      down: [[x0, y0], [x1, y0], [(x0 + x1) / 2, y1]],
      left: [[x0, (y0 + y1) / 2], [x1, y0], [x1, y1]],
      right: [[x0, y0], [x1, (y0 + y1) / 2], [x0, y1]],
    }[leaf.point ?? 'up'] as Polygon
  }
  return (leaf.points ?? []).map(([x, y]) => [x0 + w * x, y0 + h * y] as Point)
}

export interface PanelOutline {
  /** 読む順で何番目のコマか */
  index: number
  path: number[]
  leaf: Leaf
  /** 区切られた領域(形の前) */
  region: Polygon
  /** 形を収める枠(自由な多角形の頂点はこの枠に対する割合) */
  box: Box
  outline: Polygon
}

export interface DividerLine { path: number[]; index: number; dir: 'rows' | 'cols'; box: Box; ends: [Point, Point] }

/**
 * 木のコマの輪郭と、ドラッグできる境目(どれもページに対する割合)。境目の直線の両側に、コマの間隔の半分ずつを空ける。
 */
export function layoutGeometry(tree: Tree, gaps: Gaps = DEFAULT_GAPS): { panels: PanelOutline[]; dividers: DividerLine[] } {
  const panels: PanelOutline[] = []
  const dividers: DividerLine[] = []
  const halfX = Math.floor(gaps.gutter_x / 2)
  const halfY = Math.floor(gaps.gutter_y / 2)
  const norm = (p: Point): Point => [p[0] / PAGE_W, p[1] / PAGE_H]
  const normBox = (b: Box): Box => [b[0] / PAGE_W, b[1] / PAGE_H, b[2] / PAGE_W, b[3] / PAGE_H]
  const walk = (node: Tree, region: Polygon, box: Box, path: number[]) => {
    if (!isSplit(node)) {
      const leaf = leafOf(node)
      const [rx0, ry0, rx1, ry1] = bounds(region)
      const cell: Box = [Math.max(rx0, box[0]), Math.max(ry0, box[1]), Math.min(rx1, box[2]), Math.min(ry1, box[3])]
      let outline = region
      if (leaf.shape !== 'rect') {
        outline = shapeOutline(leaf, cell)
        // 区切られた領域(時計回り)の辺ごとに、内側だけを残す
        region.forEach((a, i) => {
          if (outline.length >= 3) outline = clip(outline, a, region[(i + 1) % region.length], false)
        })
        if (outline.length < 3) outline = region
      }
      panels.push({ index: panels.length, path, leaf, region: region.map(norm), box: normBox(cell), outline: outline.map(norm) })
      return
    }
    const n = node.items.length
    const tilts = node.tilts ?? Array(n - 1).fill(0)
    let start = 0
    node.items.forEach((item, i) => {
      const end = i === n - 1 ? 1 : start + node.sizes[i]
      let piece = region
      // 境目の直線を、間隔の半分だけ子の側へずらして切る(rows は下・上、cols は左・右へ)
      const shift = (line: [Point, Point], toward: 1 | -1): [Point, Point] => node.dir === 'rows'
        ? [[line[0][0], line[0][1] + toward * halfY], [line[1][0], line[1][1] + toward * halfY]]
        : [[line[0][0] - toward * halfX, line[0][1]], [line[1][0] - toward * halfX, line[1][1]]]
      if (i > 0) {
        const [a, b] = shift(dividerLine(node, box, start, tilts[i - 1]), 1)
        piece = clip(piece, a, b, false)
      }
      if (i < n - 1) {
        const line = dividerLine(node, box, end, tilts[i])
        const [a, b] = shift(line, -1)
        piece = clip(piece, a, b, true)
        dividers.push({ path, index: i, dir: node.dir, box: normBox(box), ends: [norm(line[0]), norm(line[1])] })
      }
      const [x0, y0, x1, y1] = box
      const child: Box = node.dir === 'rows'
        ? [x0, y0 + (y1 - y0) * start, x1, y0 + (y1 - y0) * end]
        : [x1 - (x1 - x0) * end, y0, x1 - (x1 - x0) * start, y1]
      walk(item, piece, child, [...path, i])
      start = end
    })
  }
  const m = gaps.margin
  walk(tree, [[m, m], [PAGE_W - m, m], [PAGE_W - m, PAGE_H - m], [m, PAGE_H - m]], [m, m, PAGE_W - m, PAGE_H - m], [])
  return { panels, dividers }
}

/** 手書きの線を間引く(Ramer–Douglas–Peucker)。epsilon は単位正方形での許容のずれ */
export function simplify(points: Point[], epsilon = 0.008): Point[] {
  if (points.length < 3) return points
  const [a, b] = [points[0], points[points.length - 1]]
  const dx = b[0] - a[0]
  const dy = b[1] - a[1]
  const length = Math.hypot(dx, dy) || 1e-9
  let worst = 0
  let at = 0
  points.forEach((p, i) => {
    const d = Math.abs(dy * p[0] - dx * p[1] + b[0] * a[1] - b[1] * a[0]) / length
    if (d > worst) { worst = d; at = i }
  })
  if (worst <= epsilon) return [a, b]
  return [...simplify(points.slice(0, at + 1), epsilon).slice(0, -1), ...simplify(points.slice(at), epsilon)]
}
