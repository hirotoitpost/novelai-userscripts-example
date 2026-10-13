import { useEffect, useState } from 'react'
import { apiFetch } from '../api'
import './PublishDialog.css'
import './VariationImportDialog.css'

/** キャラ別データセットのバリエーションの軸 */
export type Axis = 'framings' | 'poses' | 'outfits' | 'expressions' | 'locations'
export type Variations = Record<Axis, string[]>

export const AXES: { key: Axis; label: string }[] = [
  { key: 'framings', label: '構図' },
  { key: 'poses', label: 'ポーズ' },
  { key: 'outfits', label: '服装' },
  { key: 'expressions', label: '表情' },
  { key: 'locations', label: '場所' },
]

/** POST /api/lora-dataset/variations/extract の結果 */
interface Extracted {
  axes: Variations
  other: string[]
  adult: string[]
  skipped: string[]
}

interface HistoryEntry {
  id: number
  prompt: string
  created_at: string
  characters?: { prompt?: string }[]
}

interface ReverseResult {
  source: string
  positive: string
  characters?: { prompt: string }[]
}

interface Props {
  token: string
  /** いまの候補(登録済みのものは足さない) */
  existing: Variations
  onClose: () => void
  /** 足したあとの候補の一覧と、足した数 */
  onAdded: (variations: Variations, added: number) => void
}

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

/**
 * プロンプトからキーワードを取り出して、バリエーションの候補に足すダイアログ。プロンプトは、貼り付けるか、
 * 最近の画像生成の履歴から選ぶか、画像から読む(NovelAI の埋め込み。無ければリバースプロンプトの推定)。
 * 取り出したキーワードは軸ごとに分けて見せ、軸を直したり外したりしてから足す。
 */
export default function VariationImportDialog({ token, existing, onClose, onAdded }: Props) {
  const [text, setText] = useState('')
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [extracted, setExtracted] = useState<Extracted | null>(null)
  // キーワードごとの、足す軸('' は足さない)
  const [picks, setPicks] = useState<Record<string, Axis | ''>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiFetch<HistoryEntry[]>(token, '/api/chunks/history?limit=30')
      .then(entries => setHistory(entries.filter(e => (e.prompt ?? '').trim())))
      .catch(() => {})
  }, [token])

  const known = (axis: Axis, tag: string) => existing[axis].some(v => v.toLowerCase() === tag.toLowerCase())
  const append = (prompt: string) => setText(t => (t.trim() ? `${t.trim()}\n${prompt}` : prompt))

  const run = async (label: string, work: () => Promise<void>) => {
    setBusy(label)
    setError(null)
    try {
      await work()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const fromImage = (file: File) => run('画像を読み取っています…', async () => {
    const result = await apiFetch<ReverseResult>(token, '/api/llm/reverse-prompt/tags', { image: await readAsDataUrl(file) })
    const prompt = [result.positive, ...(result.characters ?? []).map(c => c.prompt)].filter(p => p.trim()).join(', ')
    if (!prompt) throw new Error('この画像からはプロンプトを読み取れませんでした。')
    append(prompt)
  })

  const extract = () => run('キーワードを取り出しています…', async () => {
    const prompts = text.split('\n').map(l => l.trim()).filter(Boolean).slice(0, 50)
    const result = await apiFetch<Extracted>(token, '/api/lora-dataset/variations/extract', { prompts })
    const next: Record<string, Axis | ''> = {}
    // 軸が分かったものは、登録済みでなければ足す。分からないもの・成人向けは、選ぶまで足さない
    for (const { key } of AXES) for (const tag of result.axes[key]) next[tag] = known(key, tag) ? '' : key
    for (const tag of [...result.other, ...result.adult]) next[tag] = ''
    setExtracted(result)
    setPicks(next)
  })

  const chosen = AXES.map(({ key }) => ({ axis: key, values: Object.keys(picks).filter(tag => picks[tag] === key && !known(key, tag)) }))
    .filter(group => group.values.length > 0)
  const total = chosen.reduce((sum, group) => sum + group.values.length, 0)

  const add = () => run('足しています…', async () => {
    let variations = existing
    for (const group of chosen) {
      variations = await apiFetch<Variations>(token, '/api/lora-dataset/variations', group)
    }
    onAdded(variations, total)
  })

  const row = (tag: string, guessed: Axis | null) => {
    const registered = guessed !== null && known(guessed, tag)
    return (
      <li key={tag} className={picks[tag] ? 'vi-row vi-row--on' : 'vi-row'}>
        <span className="vi-tag">{tag}</span>
        {registered && !picks[tag]
          ? <span className="vi-known">登録済み</span>
          : (
            <select value={picks[tag] ?? ''} aria-label={`「${tag}」を足す軸`}
              onChange={e => setPicks({ ...picks, [tag]: e.target.value as Axis | '' })}>
              <option value="">足さない</option>
              {AXES.map(a => <option key={a.key} value={a.key}>{a.label}</option>)}
            </select>
          )}
      </li>
    )
  }

  const empty = extracted && AXES.every(a => extracted.axes[a.key].length === 0) && extracted.other.length === 0 && extracted.adult.length === 0

  return (
    <div className="publish-overlay" onClick={busy ? undefined : onClose}>
      <div className="publish-dialog vi-dialog" role="dialog" aria-modal="true" aria-labelledby="vi-title" onClick={e => e.stopPropagation()}>
        <h2 id="vi-title">プロンプトからキーワードを読み込む</h2>
        <p className="publish-hint">
          画像生成で使ったプロンプトや、リバースプロンプトの結果から、構図・ポーズ・服装・表情・場所のキーワードを取り出して、
          バリエーションの候補に足します。髪・目・体などの容姿と、品質・画風のタグは取り出しません(キャラシートの側で持つため)。
        </p>
        <label className="publish-field">
          <span>プロンプト(貼り付け。1行に1つ。下の2つからも入れられます)</span>
          <textarea className="vi-text" rows={4} value={text} onChange={e => setText(e.target.value)}
            placeholder="例: 1girl, smile, school uniform, classroom, sitting, cowboy shot" />
        </label>
        <div className="vi-sources">
          <label className="publish-field">
            <span>最近の画像生成から</span>
            <select value="" disabled={history.length === 0} aria-label="最近の画像生成のプロンプトを入れる"
              onChange={e => {
                const entry = history.find(h => String(h.id) === e.target.value)
                if (entry) append([entry.prompt, ...(entry.characters ?? []).map(c => c.prompt ?? '')].filter(p => p.trim()).join(', '))
              }}>
              <option value="">{history.length === 0 ? '(履歴がありません)' : '選ぶと、上の欄に入ります'}</option>
              {history.map(h => (
                <option key={h.id} value={h.id}>{h.created_at.slice(5, 16).replace('T', ' ')} {h.prompt.slice(0, 60)}</option>
              ))}
            </select>
          </label>
          <label className="publish-field">
            <span>画像から(NovelAI の埋め込み。無ければ絵から推定)</span>
            <input type="file" accept="image/*" disabled={busy !== null}
              onChange={e => {
                const file = e.target.files?.[0]
                e.target.value = ''
                if (file) void fromImage(file)
              }} />
          </label>
        </div>
        <div className="publish-actions">
          <button type="button" onClick={() => void extract()} disabled={busy !== null || !text.trim()}>キーワードを取り出す</button>
        </div>
        {busy && <p className="publish-hint" role="status">{busy}</p>}
        {error && <p className="publish-error" role="alert">{error}</p>}

        {extracted && (
          <div className="vi-result">
            {empty && <p className="publish-hint">足せるキーワードは見つかりませんでした。</p>}
            {AXES.filter(a => extracted.axes[a.key].length > 0).map(a => (
              <section key={a.key}>
                <h3>{a.label}</h3>
                <ul>{extracted.axes[a.key].map(tag => row(tag, a.key))}</ul>
              </section>
            ))}
            {extracted.other.length > 0 && (
              <section>
                <h3>どの軸か分からなかったもの(足すなら軸を選んでください)</h3>
                <ul>{extracted.other.map(tag => row(tag, null))}</ul>
              </section>
            )}
            {extracted.adult.length > 0 && (
              <section>
                <h3>成人向け(R18 のデータセット用。足すなら軸を選んでください)</h3>
                <ul>{extracted.adult.map(tag => row(tag, null))}</ul>
              </section>
            )}
            {extracted.skipped.length > 0 && (
              <details className="vi-skipped">
                <summary>取り出さなかったタグ {extracted.skipped.length}個(容姿・人数・品質・画風)</summary>
                <p>{extracted.skipped.join(', ')}</p>
              </details>
            )}
          </div>
        )}

        <div className="publish-actions">
          <button type="button" onClick={onClose} disabled={busy !== null}>閉じる</button>
          <button type="button" className="publish-primary" onClick={() => void add()} disabled={busy !== null || total === 0}>
            {total > 0 ? `${total}個を候補に足す` : '候補に足す'}
          </button>
        </div>
      </div>
    </div>
  )
}
