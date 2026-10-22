import { useEffect, useState } from 'react'

/**
 * コマを描くときのネガティブ(物語全体・ページごと)。入れてあれば細かい方(コマ > ページ > 物語 > 挿絵の設定)を使う。
 * コマごとのネガティブは物語の編集画面で入れる(ここでは入れてあるコマの数だけ示す)。
 */

interface Props {
  apiOrigin: string
  storyId: number
  template: string
  busy?: boolean
}

interface Negatives {
  story: string | null
  pages: Record<string, string>
  scenes: Record<string, string>
}

interface LayoutPage {
  scene_ids: number[]
}

export default function NegativeEditor({ apiOrigin, storyId, template, busy }: Props) {
  const [saved, setSaved] = useState<Negatives | null>(null)
  const [story, setStory] = useState('')
  const [pages, setPages] = useState<Record<string, string>>({})
  const [layout, setLayout] = useState<LayoutPage[]>([])
  const [sceneNumbers, setSceneNumbers] = useState<Record<number, number>>({})
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    setMessage(null)
    fetch(`${apiOrigin}/api/manga-v2/${storyId}/negatives`)
      .then(r => (r.ok ? r.json() : null))
      .then((data: Negatives | null) => {
        if (!data) return
        setSaved(data)
        setStory(data.story ?? '')
        setPages(data.pages)
      })
      .catch(() => {})
    fetch(`${apiOrigin}/api/manga-v2/${storyId}/page-layout?template=${encodeURIComponent(template)}`)
      .then(r => (r.ok ? r.json() : null))
      .then((data: { pages: LayoutPage[] } | null) => {
        const list = data?.pages ?? []
        setLayout(list)
        // シーンID → シーン番号(1始まり)。ページに入るシーンの範囲を出す
        const numbers: Record<number, number> = {}
        let n = 1
        for (const page of list) for (const id of page.scene_ids) numbers[id] = n++
        setSceneNumbers(numbers)
      })
      .catch(() => setLayout([]))
  }, [apiOrigin, storyId, template])

  const dirty = saved !== null && (
    story.trim() !== (saved.story ?? '').trim()
    || JSON.stringify(Object.fromEntries(Object.entries(pages).filter(([, v]) => v.trim())))
      !== JSON.stringify(saved.pages)
  )

  async function save() {
    setMessage(null)
    const res = await fetch(`${apiOrigin}/api/manga-v2/${storyId}/negatives`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        story: story.trim(),
        pages: Object.fromEntries(Object.entries(pages).filter(([, v]) => v.trim()).map(([k, v]) => [k, v.trim()])),
      }),
    })
    if (!res.ok) {
      setMessage(`保存できませんでした (HTTP ${res.status})`)
      return
    }
    const data: Negatives = await res.json()
    setSaved(data)
    setStory(data.story ?? '')
    setPages(data.pages)
    setMessage('保存しました。次にコマを描くときから使います')
  }

  const sceneCount = Object.keys(saved?.scenes ?? {}).length

  return (
    <div>
      <p className="story-muted">
        入れてあれば細かい方(コマ &gt; ページ &gt; 物語 &gt; 挿絵の設定)を使います。品質・文字を描かせない・モノクロ・
        未成年対策のネガティブは、どれを選んでも足されます。コマごとのネガティブは物語の編集画面で入れます
        {sceneCount > 0 && `(いま ${sceneCount} コマに入っています)`}。
      </p>
      <label className="story-sheet-field">
        <span className="story-muted">物語全体</span>
        <textarea className="story-premise" rows={2} value={story} disabled={busy} placeholder="例: nsfw, micro bikini, cleavage cutout"
          onChange={e => setStory(e.target.value)} />
      </label>
      {layout.map((page, index) => {
        const numbers = page.scene_ids.map(id => sceneNumbers[id]).filter(Boolean)
        const range = numbers.length ? `シーン${numbers[0]}〜${numbers[numbers.length - 1]}` : 'シーンなし'
        return (
          <label key={index} className="story-sheet-field">
            <span className="story-muted">P{index + 1}({range})</span>
            <textarea className="story-premise" rows={1} value={pages[String(index)] ?? ''} disabled={busy}
              placeholder="空なら物語全体のネガティブ"
              onChange={e => setPages({ ...pages, [String(index)]: e.target.value })} />
          </label>
        )
      })}
      <div className="story-actions">
        <button type="button" disabled={busy || !dirty} onClick={() => void save()}>ネガティブを保存</button>
        {message && <span className="story-muted">{message}</span>}
      </div>
    </div>
  )
}
