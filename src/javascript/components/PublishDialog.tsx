import { useState } from 'react'
import type { FormEvent } from 'react'
import { useLocalStorage } from '../hooks/useLocalStorage'
import { downloadFile, publish, Publication } from '../library'
import './PublishDialog.css'

/** 出版するもの。imageKeys はギャラリーのキー、bookIds は物語のID。summary は画面に出す中身の説明 */
export interface PublishTarget {
  imageKeys: string[]
  bookIds: number[]
  /** 名前の欄のはじめの値 */
  suggestion: string
  summary: string
}

interface Props {
  target: PublishTarget
  onClose: () => void
  /** 出版物ができた。downloaded は、続けて ZIP をダウンロードしたか */
  onDone: (publication: Publication, downloaded: boolean) => void
}

/**
 * 出版のダイアログ: 名前を決めて出版する。選んだものから NovelAI の生成情報(メタデータ)を取り除いたものが、
 * 出版物として別に保存され、ZIP にまとまる。「作成後に ZIP をダウンロードする」を選ぶと、できた ZIP をそのまま
 * 受け取る(選んだかどうかは端末ごとに覚える)。
 */
export default function PublishDialog({ target, onClose, onDone }: Props) {
  const [title, setTitle] = useState(target.suggestion)
  const [download, setDownload] = useLocalStorage('nai_publish_download', false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: FormEvent) {
    e.preventDefault()
    const name = title.trim().slice(0, 120)
    if (!name || busy) return
    setBusy(true)
    setError(null)
    let publication: Publication
    try {
      publication = await publish(name, target.imageKeys, target.bookIds)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
      return
    }
    let downloaded = false
    if (download) {
      try {
        await downloadFile(`/api/publications/${publication.id}/download`, `${publication.title}.zip`)
        downloaded = true
      } catch {
        // 出版物はできているので、ダウンロードだけやり直せるようにする(呼び出し側がボタンを出す)
      }
    }
    onDone(publication, downloaded)
  }

  return (
    <div className="publish-overlay" onClick={busy ? undefined : onClose}>
      <form className="publish-dialog" role="dialog" aria-modal="true" aria-labelledby="publish-dialog-title"
        onClick={e => e.stopPropagation()} onSubmit={e => void submit(e)}>
        <h2 id="publish-dialog-title">出版する</h2>
        <p className="publish-summary">{target.summary}</p>
        <p className="publish-hint">
          NovelAI の生成情報(プロンプト・シード値などのメタデータ)を取り除いたものを、出版物として別に保存し、ZIP にまとめます。
          元の画像・本は変わりません。
        </p>
        <label className="publish-field">
          <span>出版物の名前(ZIP の名前になります)</span>
          <input value={title} maxLength={120} autoFocus onChange={e => setTitle(e.target.value)} />
        </label>
        <label className="publish-check">
          <input type="checkbox" checked={download} onChange={e => setDownload(e.target.checked)} />
          作成後に ZIP をダウンロードする
        </label>
        {error && <p className="publish-error" role="alert">{error}</p>}
        <div className="publish-actions">
          <button type="button" onClick={onClose} disabled={busy}>やめる</button>
          <button type="submit" className="publish-primary" disabled={busy || !title.trim()}>
            {busy ? '出版中…' : download ? '出版してダウンロード' : '出版する'}
          </button>
        </div>
      </form>
    </div>
  )
}

/** 出版できたあとの知らせ(ダウンロード済みかどうかで文を変える)。 */
export function publishedMessage(publication: Publication, downloaded: boolean): string {
  return `出版物「${publication.title}」を作りました(${publication.file_count}ファイル。生成情報は取り除いてあります)。`
    + (downloaded ? ' ZIP をダウンロードしました。' : '')
}
