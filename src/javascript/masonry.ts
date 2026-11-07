import { useEffect, useState } from 'react'

/**
 * 画像を、読む順(左上から右へ、次の行へ)に列へ振り分ける。1枚ずつ、その時点でいちばん短い列(同じなら左)に置く。
 * ratios は各画像の 高さ/幅。前から順に決めるので、続きを足しても、すでに置いた画像の場所は変わらない。
 * 戻り値は列ごとの、画像の番号の並び。
 */
export function masonryColumns(ratios: number[], count: number): number[][] {
  const columns: number[][] = Array.from({ length: Math.max(1, count) }, () => [])
  const heights = columns.map(() => 0)
  ratios.forEach((ratio, index) => {
    let shortest = 0
    for (let c = 1; c < heights.length; c += 1) {
      // わずかな差(丸め)では左を優先して、横に順に並ぶようにする
      if (heights[c] < heights[shortest] - 0.01) shortest = c
    }
    columns[shortest].push(index)
    heights[shortest] += ratio
  })
  return columns
}

/** 画面の幅に応じた列の数(前の CSS の段組みと同じ区切り: 2 / 640px〜3 / 1000px〜4 / 1400px〜5)。 */
const BREAKPOINTS: [number, number][] = [[1400, 5], [1000, 4], [640, 3]]

function columnsFor(width: number): number {
  return BREAKPOINTS.find(([min]) => width >= min)?.[1] ?? 2
}

export function useColumnCount(): number {
  const [count, setCount] = useState(() => columnsFor(window.innerWidth))
  useEffect(() => {
    const update = () => setCount(columnsFor(window.innerWidth))
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [])
  return count
}
