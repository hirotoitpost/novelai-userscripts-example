import { Fragment, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

/**
 * ヘルプの記事に使う、小さな Markdown の表示(HTML は通さない。React の要素だけで組む)。
 * 使えるもの: # 見出し(1〜3)、段落、- 箇条書き、1. 番号付き(下に字下げした - を入れられる)、> 注意書き、
 * ![説明](画像・GIF・動画の場所)、**太字**、`コード`、[文字](リンク)。/ で始まるリンクはアプリの中を移る。
 */

function inline(text: string, keyBase: string): ReactNode[] {
  const nodes: ReactNode[] = []
  const pattern = /(\*\*([^*]+)\*\*)|(`([^`]+)`)|(\[([^\]]+)\]\(([^)\s]+)\))/g
  let last = 0
  let match: RegExpExecArray | null
  let index = 0
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index))
    const key = `${keyBase}-${index++}`
    if (match[2] !== undefined) {
      nodes.push(<strong key={key}>{inline(match[2], key)}</strong>)
    } else if (match[4] !== undefined) {
      nodes.push(<code key={key}>{match[4]}</code>)
    } else {
      const label = match[6]
      const href = match[7]
      nodes.push(href.startsWith('/')
        ? <Link key={key} to={href}>{label}</Link>
        : /^https?:\/\//.test(href)
          ? <a key={key} href={href} target="_blank" rel="noreferrer">{label}</a>
          : <Fragment key={key}>{label}</Fragment>)
    }
    last = match.index + match[0].length
  }
  if (last < text.length) nodes.push(text.slice(last))
  return nodes
}

function media(alt: string, src: string, key: string): ReactNode {
  // 画像・GIF・動画は、記事と同じ配信元に置いたもの(/help-media/...)だけ
  if (!src.startsWith('/')) return null
  if (/\.(mp4|webm)$/i.test(src)) {
    return (
      <figure key={key} className="help-media">
        {/* GIF のように、音なしで繰り返し流す(止めたいときは操作欄で) */}
        <video src={src} controls muted loop autoPlay playsInline preload="metadata" aria-label={alt} />
        {alt && <figcaption>{alt}</figcaption>}
      </figure>
    )
  }
  return (
    <figure key={key} className="help-media">
      <img src={src} alt={alt} loading="lazy" />
      {alt && <figcaption>{alt}</figcaption>}
    </figure>
  )
}

interface ListItem {
  text: string
  children: string[]
}

export default function HelpMarkdown({ source }: { source: string }) {
  const lines = source.replace(/\r\n/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let i = 0
  let n = 0
  while (i < lines.length) {
    const line = lines[i]
    const key = `b${n++}`
    if (!line.trim()) {
      i++
      continue
    }
    const heading = /^(#{1,3})\s+(.*)$/.exec(line)
    if (heading) {
      const level = heading[1].length
      const content = inline(heading[2], key)
      blocks.push(level === 1 ? <h2 key={key}>{content}</h2> : level === 2 ? <h3 key={key}>{content}</h3> : <h4 key={key}>{content}</h4>)
      i++
      continue
    }
    const image = /^!\[([^\]]*)\]\(([^)\s]+)\)\s*$/.exec(line)
    if (image) {
      blocks.push(media(image[1], image[2], key))
      i++
      continue
    }
    if (line.startsWith('>')) {
      const quoted: string[] = []
      while (i < lines.length && lines[i].startsWith('>')) quoted.push(lines[i++].replace(/^>\s?/, ''))
      blocks.push(<aside key={key} className="help-note">{inline(quoted.join(' '), key)}</aside>)
      continue
    }
    const ordered = /^\d+\.\s+/.test(line)
    if (ordered || /^-\s+/.test(line)) {
      const items: ListItem[] = []
      while (i < lines.length) {
        const current = lines[i]
        if (ordered ? /^\d+\.\s+/.test(current) : /^-\s+/.test(current)) {
          items.push({ text: current.replace(/^(\d+\.|-)\s+/, ''), children: [] })
        } else if (/^\s{2,}-\s+/.test(current) && items.length) {
          items[items.length - 1].children.push(current.replace(/^\s+-\s+/, ''))
        } else {
          break
        }
        i++
      }
      const content = items.map((item, index) => (
        <li key={`${key}-${index}`}>
          {inline(item.text, `${key}-${index}`)}
          {item.children.length > 0 && (
            <ul>{item.children.map((child, c) => <li key={c}>{inline(child, `${key}-${index}-${c}`)}</li>)}</ul>
          )}
        </li>
      ))
      blocks.push(ordered ? <ol key={key}>{content}</ol> : <ul key={key}>{content}</ul>)
      continue
    }
    const paragraph: string[] = []
    while (
      i < lines.length && lines[i].trim() && !/^(#{1,3}\s|>|-\s|\d+\.\s|!\[)/.test(lines[i])
    ) paragraph.push(lines[i++])
    blocks.push(<p key={key}>{inline(paragraph.join(' '), key)}</p>)
  }
  return <>{blocks}</>
}
