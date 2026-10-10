// ギャラリーと本棚で共用する API 呼び出し。他の物語系ページと同じく、バックエンドへ直接アクセスする。
export const API_ORIGIN = `${window.location.protocol}//${window.location.hostname}:8000`

export function fileUrl(path: string, download = false): string {
  return `${API_ORIGIN}/api/library/file?path=${encodeURIComponent(path)}${download ? '&download=true' : ''}`
}

/** 一覧用の縮小画像(WebP)。width はサーバーで 240/360/480/720 のいずれかに丸める。 */
export function thumbUrl(path: string, width = 360): string {
  return `${API_ORIGIN}/api/library/thumb?path=${encodeURIComponent(path)}&w=${width}`
}

export async function readErrorDetail(res: Response): Promise<string> {
  const text = await res.text()
  try {
    const data = JSON.parse(text)
    return typeof data.detail === 'string' ? data.detail : text
  } catch {
    return text || res.statusText
  }
}

export async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_ORIGIN}${path}`)
  if (!res.ok) throw new Error(await readErrorDetail(res))
  return res.json()
}

export async function setBookmark(kind: 'image' | 'book', key: string, bookmarked: boolean): Promise<void> {
  const res = await fetch(`${API_ORIGIN}/api/library/bookmarks`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind, key, bookmarked }),
  })
  if (!res.ok) throw new Error(await readErrorDetail(res))
}

/** Content-Disposition の filename*(日本語名)を優先して取り出す。 */
function filenameFrom(res: Response, fallback: string): string {
  const header = res.headers.get('Content-Disposition') ?? ''
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header)
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1])
    } catch {
      // 壊れた名前なら下の filename を使う
    }
  }
  return /filename="([^"]+)"/i.exec(header)?.[1] ?? fallback
}

/**
 * ファイルを取得してブラウザに保存させる。別オリジン(:8000)だと <a download> の名前指定が効かないので、
 * いったん Blob にしてから保存する。
 */
export async function downloadFile(path: string, fallback: string, init?: RequestInit): Promise<void> {
  const res = await fetch(`${API_ORIGIN}${path}`, init)
  if (!res.ok) throw new Error(await readErrorDetail(res))
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filenameFrom(res, fallback)
  document.body.appendChild(link)
  link.click()
  link.remove()
  // クリック直後に解放するとダウンロードが始まらないブラウザがあるので少し待つ
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

export function formatDate(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

/** 成人向けの手動指定。null で手動指定を外し、自動判定に戻す。 */
export async function setAdult(kind: 'image' | 'book', key: string, adult: boolean | null): Promise<void> {
  const res = await fetch(`${API_ORIGIN}/api/library/adult`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind, key, adult }),
  })
  if (!res.ok) throw new Error(await readErrorDetail(res))
}

export async function deleteImages(keys: string[]): Promise<number> {
  const res = await fetch(`${API_ORIGIN}/api/library/images/delete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keys }),
  })
  if (!res.ok) throw new Error(await readErrorDetail(res))
  return (await res.json()).deleted
}

export async function deleteBook(id: number): Promise<void> {
  const res = await fetch(`${API_ORIGIN}/api/library/books/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(await readErrorDetail(res))
}

export type Rating = 'all' | 'general' | 'adult'

export const RATINGS: { id: Rating; label: string }[] = [
  { id: 'all', label: 'すべて' },
  { id: 'general', label: '一般のみ' },
  { id: 'adult', label: '成人向けのみ' },
]
