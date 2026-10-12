import { useState } from 'react'
import type { Genres } from '../genres'
import './GenreManager.css'

/**
 * 漫画のジャンルの管理: 候補の一覧と、追加・削除。漫画ドラフトと作品の取り込みで同じ候補を使う。
 * 消しても、取り込みに割り当ててあるジャンルはそのまま残る(候補に出なくなるだけ)。
 */
export default function GenreManager({ genres, onError }: { genres: Genres; onError: (message: string | null) => void }) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const typed = name.trim()
  const exists = genres.genres.includes(typed)

  const run = async (work: () => Promise<void>) => {
    setBusy(true)
    onError(null)
    try {
      await work()
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const add = () => {
    if (!typed || exists) return
    void run(async () => {
      await genres.add(typed)
      setName('')
    })
  }

  return (
    <details className="genre-manager">
      <summary>ジャンルの管理(追加・削除)</summary>
      <ul className="genre-manager-list">
        {genres.genres.map(g => (
          <li key={g}>
            <span>{g}</span>
            <button type="button" disabled={busy} aria-label={`ジャンル「${g}」を候補から消す`} title="候補から消す"
              onClick={() => {
                if (window.confirm(`ジャンル「${g}」を候補から消します。取り込みへの割り当ては残ります。よろしいですか?`)) {
                  void run(() => genres.remove(g))
                }
              }}>✕</button>
          </li>
        ))}
        {genres.genres.length === 0 && <li className="genre-manager-empty">候補がありません</li>}
      </ul>
      <div className="genre-manager-add">
        <input value={name} maxLength={100} placeholder="新しいジャンル(例: バトル)" aria-label="新しいジャンルの名前"
          onChange={e => setName(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); add() } }} />
        <button type="button" disabled={busy || !typed || exists} onClick={add}>
          {exists ? 'すでにあります' : 'ジャンルを追加'}
        </button>
      </div>
    </details>
  )
}
