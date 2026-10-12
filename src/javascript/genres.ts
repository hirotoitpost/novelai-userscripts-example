import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from './api'

/**
 * 漫画のジャンル(漫画ドラフトのジャンル欄と、作品の取り込みの割り当てで共通)。候補は DB にあり
 * (/api/manga-draft/genres)、画面から足したり消したりできる。どちらの欄も自由に書けるので、候補に無い
 * ジャンルも使える。
 */

/** 何も選んでいないときのジャンル(漫画ドラフトの初期値) */
export const DEFAULT_GENRE = '日常コメディ'

/** 候補に、すでに使われているジャンル(取り込みに割り当てたものなど)を足して、重なりを除く。 */
export function genreOptions(genres: string[], used: (string | null | undefined)[]): string[] {
  return [...new Set([...genres, ...used.map(g => (g ?? '').trim()).filter(Boolean)])]
}

export interface Genres {
  genres: string[]
  /** 候補に足す(すでにあれば何もしない)。失敗したら例外 */
  add: (name: string) => Promise<void>
  /** 候補から消す(取り込みへの割り当ては残る)。失敗したら例外 */
  remove: (name: string) => Promise<void>
  reload: () => Promise<void>
}

export function useGenres(token: string | null): Genres {
  const [genres, setGenres] = useState<string[]>([])

  const reload = useCallback(async () => {
    if (!token) return
    try {
      setGenres(await apiFetch<string[]>(token, '/api/manga-draft/genres'))
    } catch {
      // 候補が取れなくても、ジャンルは自由に書ける
    }
  }, [token])

  useEffect(() => { void reload() }, [reload])

  const add = useCallback(async (name: string) => {
    if (!token || !name.trim()) return
    setGenres(await apiFetch<string[]>(token, '/api/manga-draft/genres', { name: name.trim() }))
  }, [token])

  const remove = useCallback(async (name: string) => {
    if (!token) return
    const res = await fetch(`/api/manga-draft/genres?name=${encodeURIComponent(name)}`, {
      method: 'DELETE', headers: { Authorization: `Bearer ${token}` },
    })
    if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`)
    setGenres(await res.json())
  }, [token])

  return { genres, add, remove, reload }
}
