import { useEffect, useState } from 'react'

interface CatalogFont {
  id: string
  label: string
  mood: string
  installed: boolean
  license: string
  source_url: string
}

interface FontOption {
  id: string
  label: string
}

interface Props {
  apiOrigin: string
  storyId: number
  /** 対象範囲のシーンに設定されている効果音(本文中の《》等は合成時に足される) */
  words: string[]
  fonts: FontOption[]
  busy: boolean
  runTask: (label: string, task: (signal: AbortSignal) => Promise<void>) => Promise<void>
  /** AIにフォントを選ばせるジョブを走らせる(範囲・上書き設定は呼び出し側が持つ) */
  onSuggest: () => Promise<void>
  /** フォントをダウンロードしたので、フォント一覧を読み直す */
  onFontsChanged: () => void
  /** 変更通知用。値が変わるとフォントの割り当てを読み直す */
  refreshKey: number
}

async function readErrorDetail(res: Response): Promise<string> {
  const text = await res.text()
  try {
    return JSON.parse(text).detail ?? text
  } catch {
    return text || res.statusText
  }
}

/**
 * 描き文字(効果音)向けフォントのダウンロードと、効果音ごとのフォントの割り当て。
 * フォントは Google Fonts の SIL OFL 書体だけを載せた一覧から選ぶ(fonts.py)。
 */
export default function MangaV2SfxFonts({
  apiOrigin, storyId, words, fonts, busy, runTask, onSuggest, onFontsChanged, refreshKey,
}: Props) {
  const [catalog, setCatalog] = useState<CatalogFont[]>([])
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [previewText, setPreviewText] = useState('ドドド')

  function loadCatalog() {
    fetch(`${apiOrigin}/api/manga-v2/font-catalog`).then(r => r.json()).then(setCatalog).catch(() => {})
  }

  function loadMapping() {
    fetch(`${apiOrigin}/api/manga-v2/${storyId}/sfx-fonts`)
      .then(r => (r.ok ? r.json() : {}))
      .then(setMapping)
      .catch(() => {})
  }

  useEffect(loadCatalog, [apiOrigin])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(loadMapping, [apiOrigin, storyId, refreshKey])

  function download(font: CatalogFont) {
    return runTask(`${font.label} をダウンロードしています...`, async signal => {
      const res = await fetch(`${apiOrigin}/api/manga-v2/font-catalog/${font.id}/download`, { method: 'POST', signal })
      if (!res.ok) throw new Error(await readErrorDetail(res))
      loadCatalog()
      onFontsChanged()
    })
  }

  function downloadAll() {
    const missing = catalog.filter(f => !f.installed)
    return runTask(`描き文字フォントを${missing.length}個ダウンロードしています...`, async signal => {
      for (const font of missing) {
        const res = await fetch(`${apiOrigin}/api/manga-v2/font-catalog/${font.id}/download`, { method: 'POST', signal })
        if (!res.ok) throw new Error(`${font.label}: ${await readErrorDetail(res)}`)
      }
      loadCatalog()
      onFontsChanged()
    })
  }

  async function assign(word: string, font: string | null) {
    const res = await fetch(`${apiOrigin}/api/manga-v2/${storyId}/sfx-fonts`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ word, font }),
    })
    if (res.ok) loadMapping()
  }

  const preview = (fontId: string, text: string) =>
    `${apiOrigin}/api/manga-v2/font-preview?font=${encodeURIComponent(fontId)}&text=${encodeURIComponent(text)}`
  const allWords = [...new Set([...words, ...Object.keys(mapping)])]
  const installedCount = catalog.filter(f => f.installed).length

  return (
    <details className="story-characters-block">
      <summary>描き文字フォント(効果音ごとに書体を変える)</summary>
      <p className="story-muted">
        Google Fonts の日本語書体のうち、効果音に向くものを集めた一覧です。いずれも {catalog[0]?.license ?? 'SIL Open Font License'}
        (商用利用・画像への使用可)で、ライセンス文と一緒に data/fonts/ に保存します。
      </p>
      <div className="story-row">
        <label>
          見本の文字
          <input type="text" value={previewText} maxLength={12} onChange={e => setPreviewText(e.target.value)} />
        </label>
      </div>
      <ul className="mv2-fonts">
        {catalog.map(font => (
          <li key={font.id} className="mv2-font">
            {font.installed ? (
              <img className="mv2-font-preview" src={preview(font.id, previewText || 'ドドド')} alt={`${font.label}の見本`} />
            ) : (
              <div className="mv2-font-preview mv2-thumb--empty">未ダウンロード</div>
            )}
            <div className="mv2-card-body">
              <div className="mv2-card-title">
                <a href={font.source_url} target="_blank" rel="noreferrer">{font.label}</a>
              </div>
              <div className="story-muted">{font.mood}</div>
              {!font.installed && (
                <button type="button" disabled={busy} onClick={() => download(font)}>ダウンロード</button>
              )}
            </div>
          </li>
        ))}
      </ul>
      <div className="story-actions">
        {catalog.some(f => !f.installed) && (
          <button type="button" disabled={busy} onClick={downloadAll}>まとめてダウンロード</button>
        )}
        <button type="button" disabled={busy || installedCount === 0} onClick={() => void onSuggest()}>
          効果音のフォントをAIに選ばせる
        </button>
      </div>

      {allWords.length > 0 && (
        <ul className="mv2-sfx-fonts">
          {allWords.map(word => (
            <li key={word}>
              <span className="mv2-sfx-word">{word}</span>
              <select value={mapping[word] ?? ''} disabled={busy} onChange={e => void assign(word, e.target.value || null)}>
                <option value="">既定の効果音フォント</option>
                {fonts.map(f => (
                  <option key={f.id} value={f.id}>{f.label}</option>
                ))}
              </select>
              {mapping[word] && (
                <img className="mv2-sfx-word-preview" src={preview(mapping[word], word)} alt={`${word}の見本`} />
              )}
            </li>
          ))}
        </ul>
      )}
      <p className="story-muted">
        AIが選ぶのはダウンロード済みの書体からです。選んだ結果はこの物語に保存され、合成し直すと反映されます。
      </p>
    </details>
  )
}
