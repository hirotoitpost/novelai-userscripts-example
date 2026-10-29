import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../api'
import type { Character } from './StoryCharacters'

/**
 * 画像からキャラを自動で作るボタン(画像生成ページの生成画像・リバースプロンプトの画像)。
 * NovelAI で生成した画像は埋め込まれたプロンプトを、それ以外は判定モデルのタグを使い、名前・人物像は文章モデルが
 * 考える(キャラ名のタグがあれば、そのキャラの名前)。1人の画像は参照画像にもなる(サーバーの character_from_image.py)。
 */
export default function CharacterFromImage({ token, image, className }: {
  token: string
  /** 送る画像(data URL)を用意する */
  image: () => Promise<string>
  className?: string
}) {
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ text: string; isError: boolean } | null>(null)

  const run = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const data = await apiFetch<{ source: string; characters: Character[] }>(
        token, '/api/story/characters/from-image', { image: await image() },
      )
      const names = data.characters.map(c => `「${c.name}」`).join('')
      setMessage({
        text: `${names}をキャラ一覧に作りました(${data.source === 'metadata' ? '埋め込まれたプロンプト' : '判定モデルのタグ'}から)。`
          + (data.characters.length === 1 ? 'この画像を参照画像にしました。' : '二人以上の画像なので、参照画像はキャラ一覧で選んでください。'),
        isError: false,
      })
    } catch (e) {
      setMessage({ text: e instanceof Error ? e.message : String(e), isError: true })
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <button type="button" className={className} onClick={() => void run()} disabled={busy}
        title="画像の人物を、名前・人物像つきでキャラ一覧に作ります(名前は文章モデルが考えます。キャラ名のタグがあればそのキャラの名前)">
        {busy ? 'キャラを作っています…' : '🧑 この画像からキャラを自動作成'}
      </button>
      {message && (
        <p className={message.isError ? 'ig-error' : 'ig-preset-msg'} role="status">
          {message.text}
          {!message.isError && (
            <button type="button" className="ig-preset-btn" onClick={() => navigate('/characters')}>キャラ一覧で見る</button>
          )}
        </p>
      )}
    </>
  )
}
