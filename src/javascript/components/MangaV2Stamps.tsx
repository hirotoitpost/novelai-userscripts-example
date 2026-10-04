import { useEffect, useState } from 'react'

interface StampSource {
  key: string
  title: string
  author: string
  url: string
  count: number
}

interface Stamp {
  id: number
  source_key: string
  sheet: number
  idx: number
  label: string
}

interface Props {
  apiOrigin: string
  storyId: number
  /** 対象範囲のシーンに設定されている効果音 */
  words: string[]
  busy: boolean
  runTask: (label: string, task: (signal: AbortSignal) => Promise<void>) => Promise<void>
  /** 値が変わると割り当てを読み直す(AIが描き文字を選んだ後など) */
  refreshKey: number
}

/** 読みの比較用(バックエンドの normalize_reading と同じ規則) */
function normalizeReading(text: string): string {
  return text
    .normalize('NFKC')
    .replace(/[ァ-ヶ]/g, c => String.fromCharCode(c.charCodeAt(0) - 0x60))
    .replace(/[っーｰ〜~…・.!！?？♥♡\s]/g, '')
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
 * 描き文字スタンプ(素材集から切り出した手描きの擬音)の取り込みと、効果音ごとの割り当て。
 * 手描きの崩し文字は自動では読めないので、効果音ごとに一覧から選んでもらう。
 */
export default function MangaV2Stamps({ apiOrigin, storyId, words, busy, runTask, refreshKey }: Props) {
  const [sources, setSources] = useState<StampSource[]>([])
  const [stamps, setStamps] = useState<Stamp[]>([])
  const [mapping, setMapping] = useState<Record<string, number>>({})
  const [importUrl, setImportUrl] = useState('')
  const [upload, setUpload] = useState({ title: '', author: '', url: '' })
  // 選択中の効果音(スタンプ一覧を開いている語)と、一覧の絞り込み
  const [picking, setPicking] = useState<string | null>(null)
  const [source, setSource] = useState('')
  const [sheet, setSheet] = useState(0)
  const [deleteMode, setDeleteMode] = useState(false)
  const [labelMode, setLabelMode] = useState(false)
  const [search, setSearch] = useState('')
  const [labelDrafts, setLabelDrafts] = useState<Record<number, string>>({})

  const stampUrl = (id: number) => `${apiOrigin}/api/manga-v2/stamps/${id}/image`

  function loadSources() {
    fetch(`${apiOrigin}/api/manga-v2/stamp-sources`).then(r => r.json()).then((list: StampSource[]) => {
      setSources(list)
      setSource(current => current || list[0]?.key || '')
    }).catch(() => {})
  }

  function loadStamps() {
    fetch(`${apiOrigin}/api/manga-v2/stamps`).then(r => r.json()).then(setStamps).catch(() => {})
  }

  function loadMapping() {
    fetch(`${apiOrigin}/api/manga-v2/${storyId}/sfx-stamps`).then(r => (r.ok ? r.json() : {})).then(setMapping).catch(() => {})
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { loadSources(); loadStamps() }, [apiOrigin])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(loadMapping, [apiOrigin, storyId, refreshKey])

  function importFromPixiv() {
    return runTask('素材をpixivから取り込んで切り分けています...', async signal => {
      const res = await fetch(`${apiOrigin}/api/manga-v2/stamps/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: importUrl }),
        signal,
      })
      if (!res.ok) throw new Error(await readErrorDetail(res))
      const added: StampSource = await res.json()
      setSource(added.key)
      setSheet(0)
      setImportUrl('')
      loadSources()
      loadStamps()
    })
  }

  function uploadSheet(file: File) {
    const reader = new FileReader()
    reader.onload = () => void runTask('素材シートを切り分けています...', async signal => {
      const res = await fetch(`${apiOrigin}/api/manga-v2/stamps/upload`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image: reader.result, ...upload }),
        signal,
      })
      if (!res.ok) throw new Error(await readErrorDetail(res))
      const added: StampSource = await res.json()
      setSource(added.key)
      setSheet(0)
      loadSources()
      loadStamps()
    })
    reader.readAsDataURL(file)
  }

  function uploadZip(file: File) {
    const reader = new FileReader()
    reader.onload = () => void runTask('素材集(ZIP)を取り込んでいます...', async signal => {
      const res = await fetch(`${apiOrigin}/api/manga-v2/stamps/upload-zip`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ zip: reader.result, ...upload }),
        signal,
      })
      if (!res.ok) throw new Error(await readErrorDetail(res))
      const added: StampSource = await res.json()
      setSource(added.key)
      setSheet(0)
      loadSources()
      loadStamps()
    })
    reader.readAsDataURL(file)
  }

  async function removeSource(key: string) {
    if (!window.confirm('この取り込み元のスタンプをすべて削除しますか?')) return
    await fetch(`${apiOrigin}/api/manga-v2/stamp-sources/${encodeURIComponent(key)}`, { method: 'DELETE' })
    setSource('')
    loadSources()
    loadStamps()
  }

  async function removeStamp(id: number) {
    await fetch(`${apiOrigin}/api/manga-v2/stamps/${id}`, { method: 'DELETE' })
    setStamps(list => list.filter(s => s.id !== id))
  }

  async function saveLabel(stamp: Stamp) {
    const label = (labelDrafts[stamp.id] ?? stamp.label).trim()
    if (label === stamp.label) return
    const res = await fetch(`${apiOrigin}/api/manga-v2/stamps/${stamp.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label }),
    })
    if (res.ok) setStamps(list => list.map(s => (s.id === stamp.id ? { ...s, label } : s)))
  }

  function openPicker(word: string | null) {
    setPicking(word)
    // 選ぶ語の読みで絞り込んでおく(一致が無ければ全部見せる)
    setSearch(word && stamps.some(s => normalizeReading(s.label).includes(normalizeReading(word))) ? word : '')
  }

  async function assign(word: string, stampId: number | null) {
    const res = await fetch(`${apiOrigin}/api/manga-v2/${storyId}/sfx-stamps`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ word, stamp_id: stampId }),
    })
    if (res.ok) loadMapping()
    setPicking(null)
  }

  const sourceStamps = stamps.filter(s => s.source_key === source)
  const sheets = [...new Set(sourceStamps.map(s => s.sheet))]
  const query = normalizeReading(search)
  // 読みで検索しているときはシートをまたいで探す
  const shown = query
    ? sourceStamps.filter(s => normalizeReading(s.label).includes(query))
    : sourceStamps.filter(s => s.sheet === sheet)
  const labeledCount = sourceStamps.filter(s => s.label).length
  const allWords = [...new Set([...words, ...Object.keys(mapping)])]
  const currentSource = sources.find(s => s.key === source)

  return (
    <details className="story-characters-block">
      <summary>描き文字スタンプ(手描きの擬音素材を重ねる)</summary>
      <p className="story-muted">
        素材集のシート(背景が透過したPNG)を1語ずつのスタンプに切り分けて保存します(data/stamps/)。
        素材の利用条件は作者のページで確認してください。
      </p>

      <div className="story-row">
        <label>
          pixiv の作品URL
          <input type="url" value={importUrl} placeholder="https://www.pixiv.net/artworks/..." disabled={busy}
            onChange={e => setImportUrl(e.target.value)} />
        </label>
      </div>
      <div className="story-actions">
        <button type="button" disabled={busy || !importUrl.trim()} onClick={importFromPixiv}>取り込む</button>
      </div>

      <details className="mv2-upload-block">
        <summary>手元の素材を取り込む(透過PNGのシート / 1語1ファイルのZIP)</summary>
        <p className="story-muted">
          ZIPはファイル名の数字より前を読みにします(例: くちゅ1_0007.png →「くちゅ」)。素材名を空にするとZIP内のフォルダ名を使います。
        </p>
        <div className="story-row">
          <label>素材名<input type="text" value={upload.title} onChange={e => setUpload({ ...upload, title: e.target.value })} /></label>
          <label>作者<input type="text" value={upload.author} onChange={e => setUpload({ ...upload, author: e.target.value })} /></label>
          <label>入手先URL<input type="url" value={upload.url} onChange={e => setUpload({ ...upload, url: e.target.value })} /></label>
        </div>
        <label className="mv2-upload">
          シート画像を選ぶ(素材名が必要)
          <input type="file" accept="image/png" disabled={busy || !upload.title.trim()}
            onChange={e => { const f = e.target.files?.[0]; if (f) uploadSheet(f); e.target.value = '' }} />
        </label>
        <label className="mv2-upload">
          素材集のZIPを選ぶ
          <input type="file" accept=".zip,application/zip" disabled={busy}
            onChange={e => { const f = e.target.files?.[0]; if (f) uploadZip(f); e.target.value = '' }} />
        </label>
      </details>

      {sources.length > 0 && (
        <ul className="mv2-stamp-sources">
          {sources.map(s => (
            <li key={s.key}>
              <a href={s.url || undefined} target="_blank" rel="noreferrer">{s.title}</a>
              {s.author && ` / ${s.author}`}(スタンプ{s.count}個)
              <button type="button" className="story-secondary" disabled={busy} onClick={() => void removeSource(s.key)}>削除</button>
            </li>
          ))}
        </ul>
      )}

      {allWords.length > 0 && sources.length > 0 && (
        <ul className="mv2-sfx-fonts">
          {allWords.map(word => (
            <li key={word}>
              <span className="mv2-sfx-word">{word}</span>
              {mapping[word] ? (
                <img className="mv2-stamp-assigned" src={stampUrl(mapping[word])} alt={`${word}のスタンプ`} />
              ) : (
                <span className="story-muted">フォントで描く</span>
              )}
              <button type="button" disabled={busy} onClick={() => openPicker(picking === word ? null : word)}>
                {picking === word ? '閉じる' : 'スタンプを選ぶ'}
              </button>
              {mapping[word] && (
                <button type="button" className="story-secondary" disabled={busy} onClick={() => void assign(word, null)}>外す</button>
              )}
            </li>
          ))}
        </ul>
      )}

      {(picking || deleteMode || labelMode) && sources.length > 0 && (
        <div className="mv2-stamp-picker">
          <p className="story-muted">
            {deleteMode
              ? '削除モード: タップしたスタンプを一覧から消します(2語がくっついた塊など)。'
              : labelMode
                ? '読みの編集: 各スタンプの下に読みを入力します(フォーカスを外すと保存)。読みがあるとAIが自動で割り当てられます。'
                : `「${picking}」に使うスタンプをタップしてください。`}
            {` 読みあり ${labeledCount}/${sourceStamps.length}個。`}
            {currentSource && ` 出典: ${currentSource.title}${currentSource.author ? ` / ${currentSource.author}` : ''}`}
          </p>
          <div className="story-row">
            <label>
              素材
              <select value={source} onChange={e => { setSource(e.target.value); setSheet(0) }}>
                {sources.map(s => <option key={s.key} value={s.key}>{s.title}</option>)}
              </select>
            </label>
            <label>
              読みで検索(シートをまたいで探す)
              <input type="search" value={search} placeholder="例: ビクッ" onChange={e => setSearch(e.target.value)} />
            </label>
          </div>
          <div className="mv2-page-tabs" hidden={!!query}>
            {sheets.map(n => (
              <button key={n} type="button" className={n === sheet ? 'mv2-mode-active' : 'story-secondary'} onClick={() => setSheet(n)}>
                シート{n + 1}
              </button>
            ))}
          </div>
          <ul className="mv2-stamp-grid">
            {shown.map(s => (
              <li key={s.id}>
                <button
                  type="button"
                  className={deleteMode ? 'mv2-stamp mv2-stamp--delete' : 'mv2-stamp'}
                  disabled={busy || (labelMode && !deleteMode)}
                  onClick={() => (deleteMode ? void removeStamp(s.id) : picking && void assign(picking, s.id))}
                >
                  <img src={stampUrl(s.id)} alt={s.label || `スタンプ${s.id}`} loading="lazy" />
                </button>
                {labelMode ? (
                  <input
                    className="mv2-stamp-label-input"
                    type="text"
                    maxLength={30}
                    value={labelDrafts[s.id] ?? s.label}
                    placeholder="読み"
                    onChange={e => setLabelDrafts({ ...labelDrafts, [s.id]: e.target.value })}
                    onBlur={() => void saveLabel(s)}
                  />
                ) : (
                  <div className="mv2-stamp-label">{s.label || '—'}</div>
                )}
              </li>
            ))}
            {shown.length === 0 && <li className="story-muted">該当するスタンプがありません。</li>}
          </ul>
        </div>
      )}
      {sources.length > 0 && (
        <div className="story-row">
          <label className="mv2-check">
            <input type="checkbox" checked={labelMode} onChange={e => { setLabelMode(e.target.checked); setDeleteMode(false) }} />
            スタンプの読みを編集する
          </label>
          <label className="mv2-check">
            <input type="checkbox" checked={deleteMode} onChange={e => { setDeleteMode(e.target.checked); setLabelMode(false) }} />
            不要なスタンプを削除するモード
          </label>
        </div>
      )}
    </details>
  )
}
