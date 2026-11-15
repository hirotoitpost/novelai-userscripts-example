import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import type { StyleSet } from '../api'
import { API_ORIGIN, readErrorDetail, thumbUrl } from '../library'
import './StyleSetPicker.css'

/** 絵柄セットの一覧を読む(画面を開いたときに1回)。reload で読み直す。 */
export function useStyleSets(): { sets: StyleSet[]; error: string | null; reload: () => void } {
  const [sets, setSets] = useState<StyleSet[]>([])
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    fetch(`${API_ORIGIN}/api/style-sets`)
      .then(async r => {
        if (!r.ok) throw new Error(await readErrorDetail(r))
        return r.json() as Promise<StyleSet[]>
      })
      .then(data => { setSets(data); setError(null) })
      .catch(e => setError(e instanceof Error ? e.message : String(e)))
  }, [tick])
  return { sets, error, reload: () => setTick(t => t + 1) }
}

/** 絵柄セットの見本: セットの参照画像と、そのセットのキャラの参照画像(最大 samples 枚)。 */
export function StyleSetPreview({ set, samples = 3, excludeId }: { set: StyleSet; samples?: number; excludeId?: number }) {
  const members = set.members.filter(m => m.reference_image_path && m.id !== excludeId).slice(0, samples)
  return (
    <div className="ssp-preview">
      <img className="ssp-main" src={thumbUrl(set.image_path, 240)} alt={`${set.name}の絵柄の参照画像`} loading="lazy" />
      {members.map(m => (
        <img key={m.id} className="ssp-sample" src={thumbUrl(m.reference_image_path!, 240)} alt={`${m.name}(この絵柄のキャラ)`}
          title={m.name} loading="lazy" />
      ))}
    </div>
  )
}

interface Props {
  value: number | null | undefined
  onChange: (id: number | null) => void
  disabled?: boolean
  /** 見本から外すキャラ(編集中のキャラ自身) */
  excludeCharacterId?: number
}

/**
 * 絵柄セットを選ぶ。どんな絵柄かイメージしやすいよう、セットごとに参照画像と、そのセットのキャラの参照画像を見本に並べる。
 */
export default function StyleSetPicker({ value, onChange, disabled, excludeCharacterId }: Props) {
  const { sets, error } = useStyleSets()
  return (
    <div className="ssp">
      {error && <p className="ssp-error">絵柄セットを読み込めませんでした: {error}</p>}
      <div className="ssp-list" role="radiogroup" aria-label="絵柄セット">
        <button type="button" role="radio" aria-checked={value == null} disabled={disabled}
          className={`ssp-card ssp-card--none${value == null ? ' ssp-card--on' : ''}`} onClick={() => onChange(null)}>
          <span className="ssp-name">なし</span>
          <span className="ssp-note">キャラシートの画風だけで描く</span>
        </button>
        {sets.map(set => (
          <button key={set.id} type="button" role="radio" aria-checked={value === set.id} disabled={disabled}
            className={`ssp-card${value === set.id ? ' ssp-card--on' : ''}`} onClick={() => onChange(set.id)}
            title={[set.style_tags, set.notes].filter(Boolean).join('\n')}>
            <StyleSetPreview set={set} excludeId={excludeCharacterId} />
            <span className="ssp-name">{set.name}</span>
            <span className="ssp-note">
              強さ {set.strength.toFixed(2)}・キャラ {set.members.length}人
            </span>
          </button>
        ))}
      </div>
      <Link className="ssp-manage" to="/style-sets">絵柄セットを作る・編集する</Link>
    </div>
  )
}
