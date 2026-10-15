import { useRef, useState } from 'react'
import { guardFetch } from '../guardFetch'
import { API_ORIGIN } from '../library'
import './CharacterTransfer.css'

/**
 * 作品(本棚の物語1つ)の書き出し・取り込み(ZIP。/api/library/books/{id}/export・import)。
 * - WorkExportButton: 本を開いた画面のボタン。性的な場面に成人でないキャラがいるコマ・ページの画像は入らない
 * - WorkImport: 本棚の見出しのボタンと、取り込む前の確認(登場キャラごとに、同じ名前のキャラを使うか新しく入れるか)
 * 見た目はキャラシートの書き出し・取り込み(CharacterTransfer.css)と同じ。
 */

async function detail(res: Response): Promise<string> {
  const body = await res.json().catch(() => null)
  const value = body?.detail
  return typeof value === 'string' ? value : value?.message ?? `HTTP ${res.status}`
}

export function WorkExportButton({ storyId }: { storyId: number }) {
  const [busy, setBusy] = useState(false)

  async function run() {
    setBusy(true)
    try {
      const res = await fetch(`${API_ORIGIN}/api/library/books/${storyId}/export`)
      if (!res.ok) throw new Error(await detail(res))
      const disposition = res.headers.get('Content-Disposition') ?? ''
      const name = decodeURIComponent(disposition.match(/filename\*=UTF-8''([^;]+)/)?.[1] ?? `story${storyId}.zip`)
      const link = document.createElement('a')
      link.href = URL.createObjectURL(await res.blob())
      link.download = name
      link.click()
      URL.revokeObjectURL(link.href)
      const skipped = Number(res.headers.get('X-Skipped-Images') ?? 0)
      if (skipped > 0) {
        window.alert(`性的な場面に成人でないキャラがいるため、${skipped}件のコマ・ページの画像は入れませんでした(本文とタグは入っています)。`)
      }
    } catch (e) {
      window.alert(`書き出せませんでした: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <button type="button" disabled={busy} onClick={() => void run()}
      title="この作品(本文・タグ・コマ・ページ・登場キャラのキャラシート)を ZIP にします。別の PC の本棚に取り込めます">
      {busy ? '作成中…' : '作品を書き出す(ZIP)'}
    </button>
  )
}

type Action = 'existing' | 'new' | 'rename'

interface Plan {
  title: string
  scenes: number
  panels: number
  pages: number
  characters: { index: number; name: string; exists: boolean; is_minor: boolean; action: Action; new_name: string }[]
}

export function WorkImport({ onImported, buttonClassName }: { onImported: (storyId: number) => void; buttonClassName?: string }) {
  const [file, setFile] = useState<File | null>(null)
  const [plan, setPlan] = useState<Plan | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  async function preview(chosen: File) {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const form = new FormData()
      form.append('file', chosen)
      const res = await fetch(`${API_ORIGIN}/api/library/books/import/preview`, { method: 'POST', body: form })
      if (!res.ok) throw new Error(await detail(res))
      setFile(chosen)
      setPlan(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const update = (index: number, change: Partial<Plan['characters'][number]>) =>
    setPlan(prev => prev && { ...prev, characters: prev.characters.map(c => (c.index === index ? { ...c, ...change } : c)) })

  async function apply() {
    if (!file || !plan) return
    setBusy(true)
    setError(null)
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('choices', JSON.stringify(plan.characters.map(c => ({ index: c.index, action: c.action, name: c.new_name }))))
      const res = await guardFetch(`${API_ORIGIN}/api/library/books/import`, { method: 'POST', body: form })
      if (!res.ok) throw new Error(await detail(res))
      const done: { story_id: number; title: string; skipped: number } = await res.json()
      setNotice(`「${done.title || plan.title}」を取り込みました` +
        (done.skipped ? `(性的な場面に成人でないキャラがいるため、${done.skipped}件の画像は入れませんでした)` : ''))
      setPlan(null)
      setFile(null)
      onImported(done.story_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <button type="button" className={buttonClassName} disabled={busy} onClick={() => fileRef.current?.click()}
        title="書き出した作品の ZIP を、新しい物語として本棚に取り込みます">
        作品を取り込む
      </button>
      <input ref={fileRef} type="file" accept=".zip,application/zip" hidden aria-label="取り込む作品の ZIP"
        onChange={e => { const chosen = e.target.files?.[0]; e.target.value = ''; if (chosen) void preview(chosen) }} />
      {(plan || error || notice) && (
        <div className="char-transfer work-import" role="region" aria-label="作品の取り込み">
          {plan && (
            <div className="char-transfer-plan">
              <p className="char-transfer-hint">
                「{plan.title || file?.name}」: {plan.scenes}シーン・コマ{plan.panels}枚・ページ{plan.pages}枚。新しい物語として本棚に入ります。
                登場キャラごとに、同じ名前のキャラを使うか、新しく入れるかを選んでください。
              </p>
              <ul>
                {plan.characters.map(c => (
                  <li key={c.index}>
                    <div>
                      <strong>{c.name}</strong>
                      {c.is_minor && <span className="char-transfer-tag">未成年</span>}
                      {c.exists && <span className="char-transfer-tag char-transfer-tag--warn">同じ名前あり</span>}
                    </div>
                    <div className="char-transfer-row">
                      <select value={c.action} aria-label={`${c.name}の扱い`} onChange={e => update(c.index, { action: e.target.value as Action })}>
                        {c.exists && <option value="existing">同じ名前のキャラを使う</option>}
                        {!c.exists && <option value="new">新しく入れる</option>}
                        <option value="rename">別名で新しく入れる</option>
                      </select>
                      {c.action === 'rename' && (
                        <input value={c.new_name} maxLength={100} aria-label={`${c.name}の新しい名前`}
                          onChange={e => update(c.index, { new_name: e.target.value })} />
                      )}
                    </div>
                  </li>
                ))}
              </ul>
              <div className="char-transfer-row">
                <button type="button" className="char-transfer-primary" disabled={busy} onClick={() => void apply()}>
                  {busy ? '取り込み中…' : '本棚に取り込む'}
                </button>
                <button type="button" disabled={busy} onClick={() => { setPlan(null); setFile(null) }}>やめる</button>
              </div>
            </div>
          )}
          {notice && <p className="char-transfer-hint" role="status">{notice}</p>}
          {error && <p className="char-transfer-error" role="alert">{error}</p>}
        </div>
      )}
    </>
  )
}
