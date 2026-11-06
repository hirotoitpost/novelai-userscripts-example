import { useEffect, useState } from 'react'

interface Candidate {
  id: number
  number: number
  image_path: string
  seed: number | null
}

interface Props {
  apiOrigin: string
  sceneId: number
  index: number
  busy: boolean
  fileUrl: (path: string) => string
  /** 候補をコマの絵にしたあと(新しい絵のパスと、残った候補の枚数) */
  onAdopted: (imagePath: string, remaining: number) => void
  /** 候補を消したあと(残った候補の枚数) */
  onDiscarded: (remaining: number) => void
  onClose: () => void
}

/**
 * コマの候補の絵(漫画v2で1コマに何枚か描いたもの)を並べ、選んだ1枚をコマの絵にする。
 * それまでの絵は候補に戻るので、選び直せる。
 */
export function PanelCandidates({ apiOrigin, sceneId, index, busy, fileUrl, onAdopted, onDiscarded, onClose }: Props) {
  const [items, setItems] = useState<Candidate[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [working, setWorking] = useState(false)

  async function load() {
    const res = await fetch(`${apiOrigin}/api/manga-v2/scenes/${sceneId}/candidates`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    setItems(await res.json())
  }

  useEffect(() => {
    load().catch(e => setError(e instanceof Error ? e.message : String(e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sceneId])

  async function act(work: () => Promise<void>) {
    setWorking(true)
    setError(null)
    try {
      await work()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setWorking(false)
    }
  }

  const adopt = (c: Candidate) => act(async () => {
    const res = await fetch(`${apiOrigin}/api/manga-v2/scenes/${sceneId}/candidates/${c.id}/adopt`, { method: 'POST' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const result: { image_path: string; remaining: number } = await res.json()
    await load()
    onAdopted(result.image_path, result.remaining)
  })

  const discard = (c?: Candidate) => act(async () => {
    const what = c ? `候補${c.number}` : 'すべての候補'
    if (!window.confirm(`コマ${index + 1}の${what}を消しますか？(コマの絵はそのままです)`)) return
    const query = c ? `?candidate_id=${c.id}` : ''
    const res = await fetch(`${apiOrigin}/api/manga-v2/scenes/${sceneId}/candidates${query}`, { method: 'DELETE' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    await load()
    const remaining = c ? (items?.length ?? 1) - 1 : 0
    onDiscarded(Math.max(0, remaining))
  })

  const disabled = busy || working
  return (
    <div className="sed-candidates" role="group" aria-label={`コマ${index + 1}の候補`}>
      <div className="sed-candidates-head">
        <strong>候補{items ? `(${items.length}枚)` : ''}</strong>
        <span>
          {items && items.length > 0 && (
            <button type="button" className="sed-danger" disabled={disabled} onClick={() => void discard()}>すべて消す</button>
          )}
          <button type="button" onClick={onClose} aria-label="候補を閉じる">✕</button>
        </span>
      </div>
      {error && <p className="sed-error" role="alert">{error}</p>}
      {items === null && !error && <p>読み込み中…</p>}
      {items && items.length === 0 && <p className="sed-hint">候補はありません。</p>}
      <ul className="sed-candidate-list">
        {items?.map(c => (
          <li key={c.id}>
            <img src={fileUrl(c.image_path)} alt={`コマ${index + 1}の候補${c.number}`} />
            <span className="sed-candidate-actions">
              <button type="button" disabled={disabled} onClick={() => void adopt(c)}
                title="この候補をコマの絵にします(今の絵は候補に戻ります)">{c.number} を使う</button>
              <button type="button" disabled={disabled} onClick={() => void discard(c)} aria-label={`候補${c.number}を消す`}>✕</button>
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
