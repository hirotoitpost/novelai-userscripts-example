import { useLocation, useNavigate } from 'react-router-dom'

/**
 * ページの「← ホーム」ボタン。キャラ一覧などから移ってきたとき(navigate の state に backLabel)は、
 * 「← キャラ一覧」のように、移ってきたページへ戻るボタンになる(ブラウザの戻ると同じ動き)。
 */

/** 移った先に「戻る」を出すための state。navigate(path, { state: backState('キャラ一覧') }) のように使う */
export interface BackState {
  backLabel: string
}

export function backState(label: string): BackState {
  return { backLabel: label }
}

/** 移ってきたページの名前(無ければ null) */
export function useBackLabel(): string | null {
  const location = useLocation()
  const state = location.state as Partial<BackState> | null
  return typeof state?.backLabel === 'string' ? state.backLabel : null
}

interface Props {
  className: string
  /** ふだんの文言(既定は「← ホーム」) */
  label?: string
}

export default function BackButton({ className, label = '← ホーム' }: Props) {
  const navigate = useNavigate()
  const back = useBackLabel()
  return (
    <button type="button" className={className} onClick={() => (back ? navigate(-1) : navigate('/'))}>
      {back ? `← ${back}` : label}
    </button>
  )
}
