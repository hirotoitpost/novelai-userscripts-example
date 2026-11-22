// X への投稿(/x-posts)へ、ギャラリー・本棚で選んだ画像を渡す。
// 画像の指定(ref): ギャラリーの画像のキー、本棚の本のページ book:{物語のID}:{番号}、本1冊 book:{物語のID}

const DRAFT_KEY = 'xpost.refs'

/** 選んだ画像を渡して、投稿のページを開く(navigate は react-router の useNavigate) */
export function openXPost(refs: string[], navigate: (to: string) => void): void {
  try {
    sessionStorage.setItem(DRAFT_KEY, JSON.stringify(refs))
  } catch {
    // 保存できなくても、ページは開く(画像は選び直してもらう)
  }
  navigate('/x-posts?new=1')
}

/** 渡された画像の指定を受け取る(1回だけ。受け取ったら消す) */
export function takeXPostDraft(): string[] {
  try {
    const raw = sessionStorage.getItem(DRAFT_KEY)
    sessionStorage.removeItem(DRAFT_KEY)
    const refs = raw ? JSON.parse(raw) : []
    return Array.isArray(refs) ? refs.filter((r): r is string => typeof r === 'string') : []
  } catch {
    return []
  }
}

/** X の文字数の数え方の近似(半角は 1、日本語などは 2、URL は 23。上限 280)。サーバーの x_api.weighted_length と同じ */
export function weightedLength(text: string): number {
  let total = 0
  const words = text.split(' ')
  words.forEach((word, i) => {
    if (/^https?:\/\//.test(word) && word.length > 8) {
      total += 23
    } else {
      for (const ch of word) {
        const code = ch.codePointAt(0) ?? 0
        const light = code <= 0x10ff || (code >= 0x2000 && code <= 0x200d) || (code >= 0x2010 && code <= 0x201f)
          || (code >= 0x2032 && code <= 0x2037)
        total += light ? 1 : 2
      }
    }
    if (i < words.length - 1) total += 1
  })
  return total
}

export const X_MAX_WEIGHTED = 280
