import { useEffect, useRef, useState } from 'react'
import { useAuth } from '../context/AuthContext'
import ImagePreview from './ImagePreview'
import { formatDuration } from './TaskStatus'
import './ReferenceCandidates.css'

interface Candidate {
  path: string
  seed: number
}

interface Props {
  apiOrigin: string
  characterId: number
  fileUrl: (path: string) => string
  /** 1枚選んで参照画像と基準シードに登録できたら呼ぶ(呼び出し側でキャラ一覧を読み直す) */
  onChosen: () => void
  disabled?: boolean
  buttonClass?: string
  labelClass?: string
  errorClass?: string
}

/** 一度に作る候補の数 */
const COUNT = 4

async function errorDetail(res: Response): Promise<string> {
  const data = await res.json().catch(() => null)
  return (data && typeof data.detail === 'string' ? data.detail : null) ?? `失敗しました (HTTP ${res.status})`
}

/**
 * キャラシートから参照画像の候補を生成し、選んだ1枚を参照画像と基準シードに登録する。
 * キャラ別データセットのキャラシート編集と、漫画ドラフトの「漫画にする」で使う。
 * 候補は1枚ずつ頼んで、できたものから並べる(進み具合が分かるように)。押すと拡大して見比べられる。
 */
export default function ReferenceCandidates({
  apiOrigin, characterId, fileUrl, onChosen, disabled, buttonClass = '', labelClass = '', errorClass = '',
}: Props) {
  // 候補の生成は NovelAI を呼ぶので、ログイン中のトークンを渡す(無ければサーバーの永続トークン)
  const { token } = useAuth()
  const headers = { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) }
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [generating, setGenerating] = useState(false)
  const [startedAt, setStartedAt] = useState(0)
  const [now, setNow] = useState(() => Date.now())
  const [preview, setPreview] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  // 別のキャラに切り替えたら、生成中の残りは捨てる
  const runRef = useRef(0)

  useEffect(() => {
    runRef.current += 1
    setCandidates([])
    setGenerating(false)
    setPreview(null)
    setError(null)
  }, [characterId])

  useEffect(() => {
    if (!generating) return
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [generating])

  async function generate() {
    const run = ++runRef.current
    setError(null)
    setCandidates([])
    setPreview(null)
    setGenerating(true)
    setStartedAt(Date.now())
    setNow(Date.now())
    try {
      for (let i = 0; i < COUNT; i++) {
        const res = await fetch(`${apiOrigin}/api/manga-v2/characters/${characterId}/reference-candidates`, {
          method: 'POST',
          headers,
          body: JSON.stringify({ count: 1 }),
        })
        if (!res.ok) throw new Error(await errorDetail(res))
        const made: Candidate[] = await res.json()
        if (run !== runRef.current) return
        setCandidates(prev => [...prev, ...made])
      }
    } catch (e) {
      if (run === runRef.current) setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (run === runRef.current) setGenerating(false)
    }
  }

  async function choose(candidate: Candidate) {
    setError(null)
    try {
      const res = await fetch(`${apiOrigin}/api/manga-v2/characters/${characterId}/reference`, {
        method: 'PUT',
        headers,
        body: JSON.stringify({ candidate_path: candidate.path, seed: candidate.seed }),
      })
      if (!res.ok) throw new Error(await errorDetail(res))
      setCandidates([])
      setPreview(null)
      onChosen()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const busy = disabled || generating
  const done = candidates.length
  // 1枚目ができたら、1枚あたりの時間から残りを見積もる
  const elapsed = now - startedAt
  const remaining = done > 0 && done < COUNT ? (elapsed / done) * (COUNT - done) : null

  return (
    <>
      <button type="button" className={buttonClass} onClick={() => void generate()} disabled={busy}>
        {generating ? `候補を生成中… ${done}/${COUNT}` : candidates.length ? '候補を作り直す' : 'キャラシートから候補を生成'}
      </button>
      {generating && (
        <div className="ref-progress" role="status" aria-live="polite">
          <div
            className="ref-progress-bar"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={COUNT}
            aria-valuenow={done}
          >
            <div className="ref-progress-fill" style={{ width: `${(done / COUNT) * 100}%` }} />
          </div>
          <span className="ref-progress-text">
            {done + 1}枚目を生成中({done}/{COUNT})・ 経過 {formatDuration(elapsed)}
            {remaining !== null && ` ・ 残り約${formatDuration(remaining)}`}
          </span>
        </div>
      )}
      {(candidates.length > 0 || generating) && (
        <>
          {candidates.length > 0 && (
            <span className={labelClass}>
              押すと拡大します。気に入った1枚を「この絵にする」と、参照画像と基準シードに登録します(コマの見た目がその絵に寄ります)
            </span>
          )}
          <div className="ref-candidates">
            {candidates.map((candidate, index) => (
              <button
                key={candidate.path}
                type="button"
                className="ref-candidate"
                onClick={() => setPreview(index)}
                title={`シード ${candidate.seed}(押すと拡大)`}
              >
                <img src={fileUrl(candidate.path)} alt={`候補(シード ${candidate.seed})`} />
              </button>
            ))}
            {generating && Array.from({ length: COUNT - done }, (_, i) => (
              <div key={`pending-${i}`} className={`ref-candidate ref-candidate--pending${i === 0 ? ' ref-candidate--active' : ''}`}>
                <span>{i === 0 ? '生成中…' : '待機中'}</span>
              </div>
            ))}
          </div>
        </>
      )}
      {error && <div className={errorClass} role="alert">{error}</div>}
      <ImagePreview
        images={candidates.map(c => ({ src: fileUrl(c.path), alt: `候補(シード ${c.seed})`, caption: `シード ${c.seed}` }))}
        index={preview}
        onIndexChange={setPreview}
        actions={index => (
          <button
            type="button"
            className="ipv-btn ipv-btn--primary"
            onClick={() => void choose(candidates[index])}
            disabled={disabled}
          >
            この絵にする
          </button>
        )}
      />
    </>
  )
}
