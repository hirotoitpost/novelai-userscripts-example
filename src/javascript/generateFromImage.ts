import { apiFetch, GENERATE_FROM_IMAGE_KEY } from './api'
import type { GalleryImageReferences, MetadataGenerationSettings } from './api'
import { fileUrl } from './library'

/** 似た画像を作る元の画像(ギャラリーの画像、キャラの生成した画像のページの画像) */
export interface SourceImage {
  /** ギャラリーのキー(ギャラリーに無い画像は file:<パス>) */
  key: string
  path: string
  prompt?: string
  model?: string | null
  width?: number | null
  height?: number | null
  seed?: number | null
}

/**
 * 似た画像を作るために、この画像の設定を画像生成ページへ渡す準備をする(sessionStorage に入れる。呼んだ側が /generate へ移る)。
 * 画像に埋め込まれた NovelAI の設定を読む。埋め込みが無い画像は、記録にあるプロンプトだけを渡す。
 * シード値も渡す(そのまま生成すると同じ画像になる。変えたいときは、画像生成ページでシード値を変える)。
 * 生成のときに使った参照画像(i2i の元画像・キャラ参照)も渡す(画像はパスだけ渡し、画像生成ページで読み込む)。
 * characterId を渡すと、そのキャラのキャラシートの参照画像もキャラ参照に入れ、生成した画像をそのキャラに関連付ける
 * (キャラの生成した画像のページに並ぶ)。
 */
export async function prepareGenerateFromImage(
  token: string, image: SourceImage, name: string, characterId?: number | null,
): Promise<void> {
  let settings: MetadataGenerationSettings
  try {
    // 表示用に読み込み済みの画像(別オリジンの <img>)はキャッシュから使えないので、読み直す
    const res = await fetch(fileUrl(image.path), { cache: 'no-store' })
    if (!res.ok) throw new Error(`画像を読み込めませんでした(HTTP ${res.status})`)
    const blob = await res.blob()
    const data = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result as string)
      reader.onerror = () => reject(reader.error)
      reader.readAsDataURL(blob)
    })
    settings = await apiFetch<MetadataGenerationSettings>(token, '/api/metadata/generation', { image: data })
  } catch (e) {
    if (!image.prompt?.trim()) throw e
    settings = {
      prompt: image.prompt, negative_prompt: '', characters: [], use_coords: false,
      model: image.model ?? null, width: image.width ?? null, height: image.height ?? null, size_preset: null,
      steps: null, scale: null, sampler: null, noise_schedule: null, cfg_rescale: null,
      variety_boost: false, seed: image.seed ?? null, source: 'ギャラリーの記録',
      notes: [
        'この画像には生成設定が埋め込まれていなかったので、ギャラリーの記録にあるプロンプト・モデル・サイズ・シード値だけを入れました',
        'ネガティブプロンプトは空です。ステップ数などは今の設定のままです',
      ],
    }
  }
  // 参照画像が読めなくても、設定だけは渡す
  const query = `key=${encodeURIComponent(image.key)}${characterId ? `&character_id=${characterId}` : ''}`
  const references = await apiFetch<GalleryImageReferences>(token, `/api/library/images/references?${query}`)
    .catch(() => null)
  sessionStorage.setItem(GENERATE_FROM_IMAGE_KEY, JSON.stringify({ settings, name, references }))
}
