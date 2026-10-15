import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { guardFetch } from '../guardFetch'
import { API_ORIGIN } from '../library'
import './Publications.css'
import './GuardInspect.css'

/**
 * 今あるデータの点検(/api/content-guard/inspect): 今のコンテンツガードの設定で止まるはずのものが、前に作ったデータに
 * 残っていないかを見る。点検はデータを変えない。画像は選んでごみ箱へ移せる(30日のあいだは元に戻せる)。
 * 見本の画像は、性的で未成年を思わせるものなので、いつもぼかして出す(中身はギャラリーで確かめる)。
 */

interface SceneImage { key: string; path: string | null }
interface Scene { scene_index: number; title: string; action: 'block' | 'all_ages'; kind: 'minor' | 'undefined'; reasons: string[]; images: SceneImage[] }
interface StoryRow { id: number; title: string; scenes: Scene[]; minor_words: string[]; sexual_scenes: number; adult_tagging_with_minor_words: boolean }
interface CharacterRow { id: number; name: string; defined: 'minor' | 'adult' | 'undefined'; state: string; problem: string | null; cues: string[] }
interface ImageRow { key: string; source: string; path: string; label: string; story_id: number | null; prompt: string; reasons: string[] }
interface Report {
  settings: { undefined_age: string }
  summary: Record<string, number>
  characters: CharacterRow[]
  stories: StoryRow[]
  images: ImageRow[]
}

const DEFINED = { minor: '未成年', adult: '成人', undefined: '未定義' } as const

function thumb(path: string): string {
  return `${API_ORIGIN}/api/library/thumb?path=${encodeURIComponent(path)}&w=160`
}

export default function GuardInspect() {
  const navigate = useNavigate()
  const [report, setReport] = useState<Report | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [onlyMinor, setOnlyMinor] = useState(true)
  const [checkedAt, setCheckedAt] = useState<Date | null>(null)

  const run = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API_ORIGIN}/api/content-guard/inspect`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setReport(await res.json())
      setPicked(new Set())
      setCheckedAt(new Date())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => { void run() }, [run])

  const toggle = (key: string, on: boolean) => setPicked(prev => {
    const next = new Set(prev)
    if (on) next.add(key)
    else next.delete(key)
    return next
  })

  async function trashPicked() {
    if (!window.confirm(`選んだ画像 ${picked.size} 件をごみ箱へ移します(30日のあいだは元に戻せます)。よろしいですか?`)) return
    setError(null)
    try {
      const res = await fetch(`${API_ORIGIN}/api/library/images/delete`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ keys: [...picked] }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const done = await res.json()
      setNotice(`${done.deleted} 件をごみ箱へ移しました。`)
      await run()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function makeMinor(character: CharacterRow) {
    setError(null)
    try {
      const res = await guardFetch(`${API_ORIGIN}/api/story/characters/${character.id}/sheet`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ is_minor: true }),
      })
      if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`)
      setNotice(`${character.name} を「未成年」と定義しました。`)
      await run()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const summary = report?.summary
  const stories = (report?.stories ?? [])
    .map(s => ({ ...s, scenes: onlyMinor ? s.scenes.filter(sc => sc.kind === 'minor') : s.scenes }))
    .filter(s => s.scenes.length > 0 || s.adult_tagging_with_minor_words)
    .sort((a, b) => b.scenes.filter(s => s.kind === 'minor').length - a.scenes.filter(s => s.kind === 'minor').length)
  return (
    <div className="pub-root">
      <header className="pub-header">
        <button type="button" className="pub-link" onClick={() => navigate('/admin')}>← 開発管理者</button>
        <h1>データの点検</h1>
        <span className="pub-count">{checkedAt ? `${checkedAt.toLocaleTimeString('ja-JP')} に点検` : ''}</span>
        <button type="button" className="pub-link" disabled={loading} onClick={() => void run()}>{loading ? '点検中…' : '今の設定で点検し直す'}</button>
      </header>
      <main className="pub-main gi-main">
        <p className="pub-hint">
          今のコンテンツガードの設定(未成年キャラの安全対策)で、前に作ったデータに止まるはずのものが残っていないかを見ます。
          点検ではデータを変えません。画像は選んでごみ箱へ移せます(30日のあいだは元に戻せます)。
          判定モデル(画像そのもの)は使わず、プロンプト・キャプション・キャラシート・本文で見ています。
        </p>
        {error && <p className="pub-error" role="alert">{error}</p>}
        {notice && <p className="pub-hint" role="status">{notice}</p>}

        {summary && report && (
          <ul className="gi-summary">
            <li><strong>{summary.minor_scenes}</strong>未成年がいる性的な場面</li>
            <li><strong>{summary.minor_scene_images}</strong>その場面に残っている画像</li>
            <li><strong>{summary.characters}</strong>年齢区分を見直したいキャラ</li>
            <li><strong>{summary.images}</strong>気になる生成画像・データセット</li>
            <li><strong>{summary.adult_tagging_with_minor_words}</strong>未成年を示す語がある成人向けの物語</li>
            <li><strong>{summary.undefined_scenes}</strong>年齢区分が未定義のキャラの性的な場面(今の扱い: {report.settings.undefined_age === 'block' ? '止める' : '全年齢で描く'})</li>
          </ul>
        )}

        {picked.size > 0 && (
          <div className="gi-bar">
            <span>{picked.size} 件を選んでいます</span>
            <button type="button" className="gi-danger" onClick={() => void trashPicked()}>選んだ画像をごみ箱へ</button>
            <button type="button" onClick={() => setPicked(new Set())}>選択を外す</button>
          </div>
        )}

        {report && (
          <>
            <h2>キャラ</h2>
            {report.characters.length === 0 && <p className="pub-hint">見直したいキャラはいません。</p>}
            <ul className="gi-list">
              {report.characters.map(c => (
                <li key={c.id}>
                  <div><strong>{c.name}</strong> <span className="gi-tag">定義: {DEFINED[c.defined]}</span></div>
                  {c.problem && <div className="gi-warn">{c.problem}</div>}
                  {c.cues.length > 0 && <div className="pub-meta">{c.cues.join(' / ')}</div>}
                  {c.defined !== 'minor' && c.cues.length > 0 && (
                    <button type="button" onClick={() => void makeMinor(c)}
                      title="キャラシートで未成年と定義します(成人から変えるときは、パスワードを聞くことがあります)">未成年と定義する</button>
                  )}
                </li>
              ))}
            </ul>

            <h2>物語</h2>
            <label className="gi-filter">
              <input type="checkbox" checked={onlyMinor} onChange={e => setOnlyMinor(e.target.checked)} /> 未成年がいる場面だけを出す
            </label>
            {stories.length === 0 && <p className="pub-hint">該当する物語はありません。</p>}
            <ul className="gi-list">
              {stories.map(story => (
                <li key={story.id}>
                  <div>
                    <strong>{story.title}</strong> <span className="gi-tag">物語{story.id}</span>{' '}
                    <button type="button" className="gi-link" onClick={() => navigate(`/bookshelf?book=${story.id}`)}>本棚で開く</button>
                  </div>
                  {story.adult_tagging_with_minor_words && (
                    <div className="gi-warn">本文に未成年を示す語({story.minor_words.join('、')})があり、性的なタグの場面が {story.sexual_scenes} あります</div>
                  )}
                  {story.scenes.map(scene => (
                    <div key={scene.scene_index} className="gi-scene">
                      <div>
                        シーン{scene.scene_index + 1}{scene.title ? `: ${scene.title}` : ''}{' '}
                        <span className={`gi-tag${scene.kind === 'minor' ? ' gi-tag--minor' : ''}`}>
                          {scene.kind === 'minor' ? '未成年' : '未定義'} ・ {scene.action === 'block' ? '止める' : '全年齢'}
                        </span>
                      </div>
                      <div className="pub-meta">{scene.reasons.join(' / ')}</div>
                      <div className="gi-thumbs">
                        {scene.images.map(image => (
                          <label key={image.key} className={picked.has(image.key) ? 'is-picked' : undefined}>
                            {image.path && <img src={thumb(image.path)} alt="" loading="lazy" />}
                            <span><input type="checkbox" checked={picked.has(image.key)} onChange={e => toggle(image.key, e.target.checked)} /> {image.key.startsWith('panel') ? 'コマ' : '挿絵'}</span>
                          </label>
                        ))}
                      </div>
                    </div>
                  ))}
                </li>
              ))}
            </ul>

            <h2>生成画像・データセット</h2>
            {report.images.length === 0 && <p className="pub-hint">該当する画像はありません。</p>}
            <div className="gi-actions">
              {report.images.length > 0 && (
                <button type="button" onClick={() => setPicked(prev => new Set([...prev, ...report.images.map(i => i.key)]))}>ここの画像を全部選ぶ</button>
              )}
            </div>
            <ul className="gi-images">
              {report.images.map(image => (
                <li key={image.key} className={picked.has(image.key) ? 'is-picked' : undefined}>
                  <label>
                    <img src={thumb(image.path)} alt="" loading="lazy" />
                    <span><input type="checkbox" checked={picked.has(image.key)} onChange={e => toggle(image.key, e.target.checked)} /> {image.source === 'dataset' ? 'データセット' : '生成画像'}{image.label ? ` ・ ${image.label}` : ''}</span>
                    <span className="pub-meta">{image.reasons.join(' / ')}</span>
                  </label>
                </li>
              ))}
            </ul>
          </>
        )}
      </main>
    </div>
  )
}
