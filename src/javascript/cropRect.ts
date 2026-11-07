/** 全画面表示で範囲を選ぶ枠の計算(RegionRedrawTool)。画面の px で扱う。 */

export interface ScreenRect {
  left: number
  top: number
  width: number
  height: number
}

/** 範囲の縦横比(幅/高さ)。free は自由 */
export const ASPECTS: { value: string; label: string; ratio: number | null }[] = [
  { value: 'free', label: '自由', ratio: null },
  { value: '1:1', label: '1:1', ratio: 1 },
  { value: '2:3', label: '2:3', ratio: 2 / 3 },
  { value: '3:2', label: '3:2', ratio: 3 / 2 },
  { value: '3:4', label: '3:4', ratio: 3 / 4 },
  { value: '4:3', label: '4:3', ratio: 4 / 3 },
  { value: '9:16', label: '9:16', ratio: 9 / 16 },
  { value: '16:9', label: '16:9', ratio: 16 / 9 },
]

/** 枠の最小の大きさ(画面の px) */
const MIN_SIZE = 40

export type Handle = 'move' | 'nw' | 'ne' | 'sw' | 'se'

/** bounds(絵の見えている範囲)の中に収め、縦横比 ratio(null なら自由)を保つ。 */
export function fitRect(rect: ScreenRect, bounds: ScreenRect, ratio: number | null): ScreenRect {
  let { width, height } = rect
  if (ratio) {
    // 面積を大きく変えないよう、今の幅と高さの平均的な大きさで比を合わせる
    const size = Math.sqrt(width * height)
    width = size * Math.sqrt(ratio)
    height = size / Math.sqrt(ratio)
  }
  // 絵からはみ出すなら、比を保ったまま縮める
  const shrink = Math.min(1, bounds.width / width, bounds.height / height)
  width = Math.max(MIN_SIZE, width * shrink)
  height = Math.max(MIN_SIZE, height * shrink)
  const left = Math.min(Math.max(rect.left, bounds.left), bounds.left + bounds.width - width)
  const top = Math.min(Math.max(rect.top, bounds.top), bounds.top + bounds.height - height)
  return { left, top, width, height }
}

/** 角をドラッグして大きさを変える(反対の角は動かさない)。縦横比が決まっていれば、それを保つ。 */
export function resizeRect(start: ScreenRect, handle: Exclude<Handle, 'move'>, dx: number, dy: number,
  bounds: ScreenRect, ratio: number | null): ScreenRect {
  const right = start.left + start.width
  const bottom = start.top + start.height
  // 動かさない角
  const ax = handle === 'nw' || handle === 'sw' ? right : start.left
  const ay = handle === 'nw' || handle === 'ne' ? bottom : start.top
  // 動かす角の新しい位置(絵の範囲の中)
  let mx = Math.min(Math.max((handle === 'nw' || handle === 'sw' ? start.left : right) + dx, bounds.left), bounds.left + bounds.width)
  let my = Math.min(Math.max((handle === 'nw' || handle === 'ne' ? start.top : bottom) + dy, bounds.top), bounds.top + bounds.height)
  let width = Math.max(MIN_SIZE, Math.abs(mx - ax))
  let height = Math.max(MIN_SIZE, Math.abs(my - ay))
  if (ratio) {
    // 大きく動かした向きに合わせて、もう片方を決める
    if (width / height > ratio) height = width / ratio
    else width = height * ratio
    // 絵からはみ出すなら縮める
    const room = {
      w: handle === 'nw' || handle === 'sw' ? ax - bounds.left : bounds.left + bounds.width - ax,
      h: handle === 'nw' || handle === 'ne' ? ay - bounds.top : bounds.top + bounds.height - ay,
    }
    const shrink = Math.min(1, room.w / width, room.h / height)
    width *= shrink
    height *= shrink
  }
  mx = handle === 'nw' || handle === 'sw' ? ax - width : ax + width
  my = handle === 'nw' || handle === 'ne' ? ay - height : ay + height
  return { left: Math.min(ax, mx), top: Math.min(ay, my), width, height }
}

