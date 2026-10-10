import { useEffect, type ReactNode } from 'react'
import './ImagePreview.css'

export interface PreviewImage {
  src: string
  alt: string
  /** 画像の下に出す説明(シード・ファイル名など) */
  caption?: string
}

interface Props {
  images: PreviewImage[]
  /** 開いている画像の番号。null なら閉じている */
  index: number | null
  onIndexChange: (index: number | null) => void
  /** 画像の下に並べるボタン(「この絵にする」など)。開いている画像の番号を受け取る */
  actions?: (index: number) => ReactNode
}

/**
 * 画像の拡大表示。←→(またはボタン)で前後の画像へ、Esc か外側を押すと閉じる。
 * キャラの参照画像の候補や、データセットの生成結果を見比べるのに使う。
 */
export default function ImagePreview({ images, index, onIndexChange, actions }: Props) {
  const open = index !== null && index >= 0 && index < images.length

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onIndexChange(null)
      else if (e.key === 'ArrowLeft') onIndexChange((index + images.length - 1) % images.length)
      else if (e.key === 'ArrowRight') onIndexChange((index + 1) % images.length)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, index, images.length, onIndexChange])

  if (!open) return null
  const image = images[index]
  const many = images.length > 1

  return (
    <div className="ipv-overlay" onClick={() => onIndexChange(null)}>
      <div className="ipv-dialog" role="dialog" aria-modal="true" aria-label={image.alt} onClick={e => e.stopPropagation()}>
        <div className="ipv-stage">
          {many && (
            <button
              type="button"
              className="ipv-nav ipv-nav--prev"
              onClick={() => onIndexChange((index + images.length - 1) % images.length)}
              aria-label="前の画像"
            >
              ‹
            </button>
          )}
          <img className="ipv-image" src={image.src} alt={image.alt} />
          {many && (
            <button
              type="button"
              className="ipv-nav ipv-nav--next"
              onClick={() => onIndexChange((index + 1) % images.length)}
              aria-label="次の画像"
            >
              ›
            </button>
          )}
        </div>
        <div className="ipv-footer">
          <span className="ipv-caption">
            {many && `${index + 1} / ${images.length}`}
            {image.caption && `${many ? ' ・ ' : ''}${image.caption}`}
          </span>
          <div className="ipv-actions">
            {actions?.(index)}
            <button type="button" className="ipv-btn" onClick={() => onIndexChange(null)}>閉じる</button>
          </div>
        </div>
      </div>
    </div>
  )
}
