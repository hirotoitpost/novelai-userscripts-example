import { useEffect, useState } from 'react'
import BackButton from '../components/BackButton'
import FullscreenViewer from '../components/FullscreenViewer'
import { fileUrl } from '../library'
import './LoraDataset.css'
import './AdultPatterns.css'

/**
 * R18 のパターンのページ。成人向けの作品で使うプレイ(体位・行為・構図のタグ)と、シナリオの型(導入から展開の流れ)を
 * 足す・直す・消す・並べ替える。表現(描き文字・セリフの言い回し)は漫画ドラフトの台本と、取り込んだ R18 の作品の
 * 描き文字の読みの候補に使う。プレイには参照画像を何枚でも付けられる(判定モデルで構図・行為のタグを読み取り、
 * 使うたびに1枚ずつ順に足す)。使えるのは、年齢(18 歳以上)を入れた成人キャラだけの場面・データセット・漫画。
 */

interface PatternImage { id: number; path: string; tags: string }
interface Pattern {
  id: number
  kind: string
  name: string
  tags: string
  description: string
  sfx: string
  lines: string
  images: PatternImage[]
}
type Draft = Pick<Pattern, 'name' | 'tags' | 'description' | 'sfx' | 'lines'>

const EMPTY: Draft = { name: '', tags: '', description: '', sfx: '', lines: '' }

function toDraft(p: Pattern): Draft {
  return { name: p.name, tags: p.tags, description: p.description, sfx: p.sfx, lines: p.lines }
}

async function detail(res: Response): Promise<string> {
  const data = await res.json().catch(() => null)
  return typeof data?.detail === 'string' ? data.detail : `HTTP ${res.status}`
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

export default function AdultPatterns() {
  const [kinds, setKinds] = useState<{ key: string; label: string }[]>([])
  const [patterns, setPatterns] = useState<Record<string, Pattern[]>>({})
  const [drafts, setDrafts] = useState<Record<number, Draft>>({})
  const [newDrafts, setNewDrafts] = useState<Record<string, Draft>>({})
  const [imageTags, setImageTags] = useState<Record<number, string>>({})
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null)
  const [busy, setBusy] = useState(false)
  const [zoom, setZoom] = useState<PatternImage | null>(null)

  function load() {
    fetch('/api/adult-patterns')
      .then(r => (r.ok ? r.json() : null))
      .then(data => {
        if (!data) return
        setKinds(data.kinds)
        setPatterns(data.patterns)
        setDrafts({})
        setImageTags({})
      })
      .catch(() => setMessage({ text: 'パターンを読み込めませんでした', error: true }))
  }
  useEffect(load, [])

  async function run(action: () => Promise<Response>, done: string): Promise<boolean> {
    setBusy(true)
    setMessage(null)
    try {
      const res = await action()
      if (!res.ok) {
        setMessage({ text: await detail(res), error: true })
        return false
      }
      setMessage({ text: done, error: false })
      load()
      return true
    } finally {
      setBusy(false)
    }
  }

  const json = (method: string, body: unknown): RequestInit => ({
    method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })

  async function save(p: Pattern) {
    const d = drafts[p.id]
    if (!d) return
    await run(() => fetch(`/api/adult-patterns/${p.id}`, json('PUT', { kind: p.kind, ...d })), `「${d.name}」を保存しました`)
  }

  async function add(kind: string) {
    const d = newDrafts[kind] ?? EMPTY
    if (await run(() => fetch('/api/adult-patterns', json('POST', { kind, ...d })), `「${d.name}」を足しました`)) {
      setNewDrafts({ ...newDrafts, [kind]: EMPTY })
    }
  }

  async function remove(p: Pattern) {
    if (!window.confirm(`「${p.name}」を消しますか?(参照画像も消えます)`)) return
    await run(() => fetch(`/api/adult-patterns/${p.id}`, { method: 'DELETE' }), `「${p.name}」を消しました`)
  }

  async function move(kind: string, index: number, delta: number) {
    const list = [...(patterns[kind] ?? [])]
    const target = index + delta
    if (target < 0 || target >= list.length) return
    ;[list[index], list[target]] = [list[target], list[index]]
    setPatterns({ ...patterns, [kind]: list })
    await run(() => fetch('/api/adult-patterns/order', json('PUT', { kind, ids: list.map(p => p.id) })), '並べ替えました')
  }

  async function upload(p: Pattern, files: FileList | null) {
    if (!files?.length) return
    for (const [index, file] of Array.from(files).entries()) {
      setMessage({ text: `参照画像を判定しています ${index + 1}/${files.length}: ${file.name}`, error: false })
      const data = await readAsDataUrl(file)
      const ok = await run(() => fetch(`/api/adult-patterns/${p.id}/images`, json('POST', { data })), `${file.name} を足しました`)
      if (!ok) return
    }
  }

  async function saveImageTags(p: Pattern, image: PatternImage) {
    const tags = imageTags[image.id]
    if (tags === undefined) return
    await run(() => fetch(`/api/adult-patterns/${p.id}/images/${image.id}`, json('PUT', { tags })), 'タグを保存しました')
  }

  async function removeImage(p: Pattern, image: PatternImage) {
    if (!window.confirm('この参照画像を消しますか?')) return
    await run(() => fetch(`/api/adult-patterns/${p.id}/images/${image.id}`, { method: 'DELETE' }), '参照画像を消しました')
  }

  function editor(kind: string, d: Draft, onChange: (d: Draft) => void) {
    const set = (key: keyof Draft) => (e: { target: { value: string } }) => onChange({ ...d, [key]: e.target.value })
    return (
      <div className="adp-fields">
        <label className="lora-label">名前<input className="lora-input" value={d.name} maxLength={60} disabled={busy} onChange={set('name')} /></label>
        <label className="lora-label">{kind === 'play' ? 'タグ(体位・行為・構図)' : 'タグ(場所・時間など。無くてよい)'}
          <input className="lora-input" value={d.tags} disabled={busy} onChange={set('tags')} placeholder="sex, cowgirl position, sweat" />
        </label>
        {kind === 'scenario' && (
          <label className="lora-label">流れ(導入 → 展開 → … 。文章モデルに渡します)
            <textarea className="lora-textarea" rows={2} value={d.description} disabled={busy} onChange={set('description')} />
          </label>
        )}
        <label className="lora-label">描き文字(1行に1つ)
          <textarea className="lora-textarea" rows={2} value={d.sfx} disabled={busy} onChange={set('sfx')} placeholder={'ぱんっ\nずちゅ'} />
        </label>
        <label className="lora-label">セリフの言い回しの例(1行に1つ)
          <textarea className="lora-textarea" rows={2} value={d.lines} disabled={busy} onChange={set('lines')} />
        </label>
      </div>
    )
  }

  return (
    <div className="lora-root">
      <header className="lora-header">
        <BackButton className="lora-back" />
        <span className="lora-header-title">R18 のパターン</span>
      </header>
      <main className="adp-main">
        <p className="lora-hint">
          成人向けの作品で使うプレイとシナリオの型です。漫画ドラフトの筋書き・漫画の場面・キャラ別データセット(R18)で選べます。
          使えるのは、登場するキャラが全員、年齢(18 歳以上)を入れた成人のときだけです。未成年を思わせる語は入れられません。
          描き文字とセリフの言い回しは、台本づくりの例になり、取り込んだ R18 の作品の描き文字の読みの候補にもなります。
          プレイの参照画像は、判定モデルで構図・行為のタグを読み取り、プレイを使うたびに1枚ずつ順にそのタグを足します
          (未成年を思わせる絵は受け付けません)。
        </p>
        {message && <p className={message.error ? 'lora-error' : 'lora-hint'} role={message.error ? 'alert' : undefined}>{message.text}</p>}
        {kinds.map(k => (
          <section key={k.key} className="adp-kind">
            <h3>{k.label}</h3>
            <ul>
              {(patterns[k.key] ?? []).map((p, i, list) => {
                const d = drafts[p.id] ?? toDraft(p)
                const dirty = drafts[p.id] !== undefined
                return (
                  <li key={p.id} className="adp-item">
                    {editor(k.key, d, next => setDrafts({ ...drafts, [p.id]: next }))}
                    {k.key === 'play' && (
                      <div className="adp-images">
                        {p.images.map(image => (
                          <div key={image.id} className="adp-image">
                            <button type="button" className="adp-thumb" onClick={() => setZoom(image)} title="拡大">
                              <img src={fileUrl(image.path)} alt="参照画像" loading="lazy" />
                            </button>
                            <input className="lora-input" value={imageTags[image.id] ?? image.tags} disabled={busy}
                              aria-label="参照画像から読み取ったタグ"
                              onChange={e => setImageTags({ ...imageTags, [image.id]: e.target.value })} />
                            <div className="adp-actions">
                              <button type="button" className="lora-btn lora-btn--secondary" disabled={busy || imageTags[image.id] === undefined}
                                onClick={() => void saveImageTags(p, image)}>タグを保存</button>
                              <button type="button" className="lora-btn lora-btn--danger" disabled={busy}
                                onClick={() => void removeImage(p, image)}>削除</button>
                            </div>
                          </div>
                        ))}
                        <label className="adp-upload">
                          参照画像を足す
                          <input type="file" accept="image/*" multiple disabled={busy}
                            onChange={e => { void upload(p, e.target.files); e.target.value = '' }} />
                        </label>
                      </div>
                    )}
                    <div className="adp-actions">
                      <button type="button" className="lora-btn lora-btn--secondary" disabled={busy || i === 0}
                        onClick={() => void move(k.key, i, -1)} aria-label="上へ">↑</button>
                      <button type="button" className="lora-btn lora-btn--secondary" disabled={busy || i === list.length - 1}
                        onClick={() => void move(k.key, i, 1)} aria-label="下へ">↓</button>
                      <button type="button" className="lora-btn" disabled={busy || !dirty || !d.name.trim()} onClick={() => void save(p)}>保存</button>
                      <button type="button" className="lora-btn lora-btn--danger" disabled={busy} onClick={() => void remove(p)}>削除</button>
                    </div>
                  </li>
                )
              })}
              <li className="adp-item adp-item--new">
                <h4>新しい{k.label}</h4>
                {editor(k.key, newDrafts[k.key] ?? EMPTY, next => setNewDrafts({ ...newDrafts, [k.key]: next }))}
                <div className="adp-actions">
                  <button type="button" className="lora-btn" disabled={busy || !(newDrafts[k.key]?.name ?? '').trim()}
                    onClick={() => void add(k.key)}>足す</button>
                </div>
              </li>
            </ul>
          </section>
        ))}
      </main>
      {zoom && <FullscreenViewer src={fileUrl(zoom.path)} alt="参照画像" onClose={() => setZoom(null)} />}
    </div>
  )
}
