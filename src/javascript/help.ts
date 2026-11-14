/**
 * ヘルプの記事(src/help/*.md)。先頭の --- で囲んだところに title / feature / order / summary を書く。
 * 記事の名前(slug)はファイル名。/help/<slug> で開く。ヘルプのチャット(あとで)も同じファイルを読む。
 */

export interface HelpArticle {
  slug: string
  title: string
  feature: string
  order: number
  summary: string
  body: string
}

const files = import.meta.glob('../help/*.md', { query: '?raw', import: 'default', eager: true }) as Record<string, string>

export function parseArticle(slug: string, text: string): HelpArticle {
  const normalized = text.replace(/\r\n/g, '\n')
  const match = /^---\n([\s\S]*?)\n---\n?/.exec(normalized)
  const meta: Record<string, string> = {}
  if (match) {
    for (const line of match[1].split('\n')) {
      const at = line.indexOf(':')
      if (at > 0) meta[line.slice(0, at).trim()] = line.slice(at + 1).trim()
    }
  }
  return {
    slug,
    title: meta.title || slug,
    feature: meta.feature || 'そのほか',
    order: Number(meta.order) || 99,
    summary: meta.summary || '',
    body: match ? normalized.slice(match[0].length) : normalized,
  }
}

export const HELP_ARTICLES: HelpArticle[] = Object.entries(files)
  .map(([path, text]) => parseArticle(path.split('/').pop()!.replace(/\.md$/, ''), text))
  .sort((a, b) => a.feature.localeCompare(b.feature, 'ja') || a.order - b.order)

export function findArticle(slug: string): HelpArticle | undefined {
  return HELP_ARTICLES.find(a => a.slug === slug)
}

/** 機能ごとにまとめる(機能の並びは、記事の多い順ではなく名前順) */
export function groupByFeature(articles: HelpArticle[]): [string, HelpArticle[]][] {
  const groups = new Map<string, HelpArticle[]>()
  for (const article of articles) {
    groups.set(article.feature, [...(groups.get(article.feature) ?? []), article])
  }
  return [...groups.entries()]
}
