import { useEffect, useState } from 'react'
import { useAuth } from '../context/AuthContext'
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

async function errorDetail(res: Response): Promise<string> {
  const data = await res.json().catch(() => null)
  return (data && typeof data.detail === 'string' ? data.detail : null) ?? `失敗しました (HTTP ${res.status})`
}

/**
 * キャラシートから参照画像の候補を生成し、選んだ1枚を参照画像と基準シードに登録する。
 * キャラ別データセットのキャラシート編集と、漫画ドラフトの「漫画にする」で使う。
 */
export default function ReferenceCandidates({
  apiOrigin, characterId, fileUrl, onChosen, disabled, buttonClass = '', labelClass = '', errorClass = '',
}: Props) {
  // 候補の生成は NovelAI を呼ぶので、ログイン中のトークンを渡す(無ければサーバーの永続トークン)
  const { token } = useAuth()
  const headers = { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) }
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setCandidates([])
    setError(null)
  }, [characterId])

  async function generate() {
    setError(null)
    setGenerating(true)
    try {
      const res = await fetch(`${apiOrigin}/api/manga-v2/characters/${characterId}/reference-candidates`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ count: 4 }),
      })
      if (!res.ok) throw new Error(await errorDetail(res))
      setCandidates(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setGenerating(false)
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
      onChosen()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const busy = disabled || generating

  return (
    <>
      <button type="button" className={buttonClass} onClick={() => void generate()} disabled={busy}>
        {generating ? '候補を生成中…(1分ほど)' : candidates.length ? '候補を作り直す' : 'キャラシートから候補を生成'}
      </button>
      {candidates.length > 0 && (
        <>
          <span className={labelClass}>
            気に入った1枚を選ぶと、参照画像と基準シードに登録します(コマの見た目がその絵に寄ります)
          </span>
          <div className="ref-candidates">
            {candidates.map(candidate => (
              <button
                key={candidate.path}
                type="button"
                className="ref-candidate"
                onClick={() => void choose(candidate)}
                disabled={busy}
                title={`シード ${candidate.seed}`}
              >
                <img src={fileUrl(candidate.path)} alt={`候補(シード ${candidate.seed})`} />
              </button>
            ))}
          </div>
        </>
      )}
      {error && <div className={errorClass} role="alert">{error}</div>}
    </>
  )
}
