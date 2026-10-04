/**
 * チャットのスクリーンショットの OCR(サーバーの /api/ocr/*)を、スマホでも途切れにくく呼ぶ。
 *
 * 72枚(2〜3分)を読ませたとき、17枚目でスマホ側が止まり(画面が消えた・アプリを切り替えた等)、
 * それまでの結果ごと失われた。そこで:
 * - 1枚ごとの結果を localStorage に覚え、同じ画像を選び直せば読み取り済みの分は飛ばす
 * - 通信が切れたら少し待ってやり直す
 * - アップロードと読み取りを重ねるため、2枚ずつ並行して送る
 */

export interface OcrBlock {
  kind: 'character' | 'narration' | 'user_action' | 'user_speech'
  text: string
  cut_top: boolean
  cut_bottom: boolean
}

const CACHE_KEY = 'nai_chat_ocr_cache_v1'
// 1枚あたり数百バイト〜数KB。古いものから捨てる
const CACHE_LIMIT = 400
const PARALLEL = 2
const RETRIES = 4

type Cache = Record<string, { blocks: OcrBlock[]; at: number }>

function loadCache(): Cache {
  try {
    return JSON.parse(localStorage.getItem(CACHE_KEY) ?? '{}')
  } catch {
    return {}
  }
}

function saveCache(cache: Cache) {
  const entries = Object.entries(cache).sort((a, b) => b[1].at - a[1].at).slice(0, CACHE_LIMIT)
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(Object.fromEntries(entries)))
  } catch {
    // 容量オーバーなど。覚えられないだけで読み取りは続ける
  }
}

/** 同じ画像かどうかの目印(中身のハッシュは重いので、名前・大きさ・日時で見る) */
function fileKey(file: File): string {
  return `${file.name}|${file.size}|${file.lastModified}`
}

/** 撮った順に並べる(更新日時が同じなら、日時入りのファイル名の順) */
export function sortScreenshots(files: File[]): File[] {
  return [...files].sort(
    (a, b) => a.lastModified - b.lastModified || a.name.localeCompare(b.name, undefined, { numeric: true }),
  )
}

async function readError(res: Response): Promise<string> {
  const text = await res.text()
  try {
    return JSON.parse(text).detail ?? text
  } catch {
    return text || res.statusText
  }
}

/** 通信の失敗(画面が消えて止まった等)とサーバーの一時的なエラーは、待ってやり直す */
async function postWithRetry(url: string, init: () => RequestInit, signal: AbortSignal, label: string): Promise<Response> {
  for (let attempt = 0; ; attempt++) {
    try {
      const res = await fetch(url, { ...init(), signal })
      if (res.ok || res.status < 500 || attempt >= RETRIES) {
        if (!res.ok) throw new Error(`${label}: ${await readError(res)}`)
        return res
      }
    } catch (e) {
      if (signal.aborted || (e instanceof DOMException && e.name === 'AbortError')) throw e
      if (attempt >= RETRIES || !(e instanceof TypeError)) {
        throw e instanceof TypeError ? new Error(`${label}: サーバーに接続できません(${e.message})`) : e
      }
    }
    await new Promise(resolve => setTimeout(resolve, 1500 * (attempt + 1)))
  }
}

/**
 * スクショを順に読み、1枚ごとの発言の塊を返す(並びは files の順)。
 * onProgress(読み終えた枚数, 読み取り済みで飛ばした枚数)
 */
export async function readScreenshots(
  apiOrigin: string,
  files: File[],
  signal: AbortSignal,
  onProgress: (done: number, cached: number, current: string) => void,
): Promise<OcrBlock[][]> {
  const cache = loadCache()
  const pages: (OcrBlock[] | null)[] = files.map(f => cache[fileKey(f)]?.blocks ?? null)
  const cachedCount = pages.filter(Boolean).length
  let done = cachedCount
  onProgress(done, cachedCount, '')

  const queue = files.map((_, i) => i).filter(i => pages[i] === null)
  async function worker() {
    while (queue.length > 0) {
      const i = queue.shift()!
      const file = files[i]
      onProgress(done, cachedCount, file.name)
      const res = await postWithRetry(
        `${apiOrigin}/api/ocr/chat-screenshot`,
        () => {
          const form = new FormData()
          form.append('file', file)
          return { method: 'POST', body: form }
        },
        signal,
        `${i + 1}枚目(${file.name})`,
      )
      const blocks: OcrBlock[] = (await res.json()).blocks
      pages[i] = blocks
      // 1枚ずつ覚えておく(途中で止まっても、選び直せばここから続く)
      cache[fileKey(file)] = { blocks, at: Date.now() }
      saveCache(cache)
      done++
      onProgress(done, cachedCount, file.name)
    }
  }
  await Promise.all(Array.from({ length: Math.min(PARALLEL, queue.length) }, worker))
  return pages as OcrBlock[][]
}

export async function mergeScreenshots(
  apiOrigin: string,
  pages: OcrBlock[][],
  signal: AbortSignal,
): Promise<{ blocks: OcrBlock[]; text: string }> {
  const res = await postWithRetry(
    `${apiOrigin}/api/ocr/chat-merge`,
    () => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pages }) }),
    signal,
    'つなぎ合わせ',
  )
  return res.json()
}
