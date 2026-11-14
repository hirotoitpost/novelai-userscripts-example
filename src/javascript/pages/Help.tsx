import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import HelpChat from '../components/HelpChat'
import HelpMarkdown from '../components/HelpMarkdown'
import { HELP_ARTICLES, findArticle, groupByFeature } from '../help'
import './Help.css'

/**
 * ヘルプ(/help と /help/<slug>)。記事は src/help/*.md。
 * 一覧は機能ごとにまとめ、上の欄で題名・要約・本文から探せる。
 */
export default function Help() {
  const { slug } = useParams<{ slug?: string }>()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const article = slug ? findArticle(slug) : undefined

  const filtered = useMemo(() => {
    const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean)
    if (!words.length) return HELP_ARTICLES
    return HELP_ARTICLES.filter(a => {
      const text = `${a.title}\n${a.summary}\n${a.feature}\n${a.body}`.toLowerCase()
      return words.every(w => text.includes(w))
    })
  }, [query])

  const related = article ? HELP_ARTICLES.filter(a => a.feature === article.feature && a.slug !== article.slug) : []

  return (
    <div className="help-root">
      <header className="help-header">
        <button type="button" className="help-back" onClick={() => (article ? navigate('/help') : navigate('/'))}>
          ← {article ? 'ヘルプの一覧' : 'ホーム'}
        </button>
        <h1>{article ? article.title : 'ヘルプ'}</h1>
      </header>

      {slug && !article && (
        <p className="help-missing">その記事はありません。<Link to="/help">一覧へ</Link></p>
      )}

      {article && (
        <article className="help-article">
          <p className="help-feature">{article.feature}</p>
          <HelpMarkdown source={article.body} />
          {related.length > 0 && (
            <nav className="help-related" aria-label="関連する記事">
              <h3>{article.feature} のほかの記事</h3>
              <ul>
                {related.map(a => <li key={a.slug}><Link to={`/help/${a.slug}`}>{a.title}</Link></li>)}
              </ul>
            </nav>
          )}
        </article>
      )}

      {!slug && (
        <>
          <input className="help-search" type="search" value={query} placeholder="探す(例: 写し先、空き、戻す)"
            aria-label="ヘルプを探す" onChange={e => setQuery(e.target.value)} />
          {filtered.length === 0 && <p className="help-missing">見つかりませんでした。</p>}
          {groupByFeature(filtered).map(([feature, articles]) => (
            <section key={feature} className="help-group">
              <h2>{feature}</h2>
              <ul>
                {articles.map(a => (
                  <li key={a.slug}>
                    <Link to={`/help/${a.slug}`}>{a.title}</Link>
                    {a.summary && <p>{a.summary}</p>}
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </>
      )}

      {(article || !slug) && <HelpChat current={article?.slug} />}
    </div>
  )
}
