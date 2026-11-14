import { Link } from 'react-router-dom'
import { findArticle } from '../help'
import './HelpLink.css'

/** 欄の見出しの横に置く「?」。ヘルプの記事(/help/<slug>)へ。記事が無ければ出さない。 */
export default function HelpLink({ slug, label }: { slug: string; label?: string }) {
  const article = findArticle(slug)
  if (!article) return null
  return (
    <Link to={`/help/${slug}`} className="help-link" title={`ヘルプ: ${article.title}`} aria-label={`ヘルプ: ${article.title}`}>
      {label ?? '?'}
    </Link>
  )
}
