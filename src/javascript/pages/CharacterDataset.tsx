import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import { useLocalStorage } from '../hooks/useLocalStorage'
import {
  streamCharacterDataset,
  CharacterDatasetRequest,
  LoraDatasetCompleteEvent,
  LoraDatasetProgressEvent,
  LoraDatasetRetryEvent,
} from '../api'
import type { Character } from '../components/StoryCharacters'
import CharacterSheetEditor, { type SheetEditorClasses } from '../components/CharacterSheetEditor'
import GuardProfiles from '../components/GuardProfiles'
import './LoraDataset.css'
import './CharacterDataset.css'

type Axis = 'framings' | 'poses' | 'outfits' | 'expressions' | 'locations'
type Variations = Record<Axis, string[]>

const AXES: { key: Axis; label: string }[] = [
  { key: 'framings', label: '構図' },
  { key: 'poses', label: 'ポーズ' },
  { key: 'outfits', label: '服装' },
  { key: 'expressions', label: '表情' },
  { key: 'locations', label: '場所' },
]

type Phase = 'idle' | 'running' | 'complete' | 'error' | 'cancelled'

function readFileAsDataURL(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

function fileUrl(path: string): string {
  return `/api/story/manga-file?path=${encodeURIComponent(path)}`
}

/**
 * キャラシート(物語の登場人物に紐づく安定生成設定)を元に、ポーズ/服装/表情/場所を
 * 差し替えたデータセットを1枚ずつ生成する。手元の画像の取り込みもここで行う。
 */
const SHEET_CLASSES: SheetEditorClasses = {
  field: 'chards-sheet-field',
  label: 'lora-label',
  input: 'lora-textarea',
  actions: 'lora-actions',
  button: 'lora-btn lora-btn--secondary',
  primary: 'lora-btn lora-btn--primary',
  danger: 'lora-btn lora-btn--danger',
  error: 'lora-error',
}

export default function CharacterDataset() {
  const { token } = useAuth()
  const navigate = useNavigate()

  const [characters, setCharacters] = useState<Character[]>([])
  const [characterId, setCharacterId] = useLocalStorage<number | null>('nai_chards_character', null)
  const [defaults, setDefaults] = useState<Variations>({ framings: [], poses: [], outfits: [], expressions: [], locations: [] })
  const [selected, setSelected] = useLocalStorage<Variations>('nai_chards_selected', {
    framings: [], poses: [], outfits: [], expressions: [], locations: [],
  })
  const [custom, setCustom] = useState<Record<Axis, string>>({ framings: '', poses: '', outfits: '', expressions: '', locations: '' })

  const [rootName, setRootName] = useLocalStorage('nai_chards_root', 'training_data')
  const [count, setCount] = useLocalStorage('nai_chards_count', 10)
  const [width, setWidth] = useLocalStorage('nai_chards_width', 832)
  const [height, setHeight] = useLocalStorage('nai_chards_height', 1216)
  const [steps, setSteps] = useLocalStorage('nai_chards_steps', 23)
  const [scale, setScale] = useLocalStorage('nai_chards_scale', 5.0)
  const [useReference, setUseReference] = useLocalStorage('nai_chards_use_ref', false)
  const [refFidelity, setRefFidelity] = useLocalStorage('nai_chards_ref_fidelity', 1.0)
  const [refStrength, setRefStrength] = useLocalStorage('nai_chards_ref_strength', 0.7)
  const [refType, setRefType] = useLocalStorage<'character' | 'character&style'>('nai_chards_ref_type', 'character')
  const [maxAttempts, setMaxAttempts] = useLocalStorage('nai_chards_attempts', 1)
  const [threshold, setThreshold] = useLocalStorage('nai_chards_threshold', 0.7)
  const [scorer, setScorer] = useLocalStorage<'color' | 'vlm'>('nai_chards_scorer', 'color')
  const [guardId, setGuardId] = useLocalStorage<number | null>('nai_chards_guard', null)
  const [r18Selected, setR18Selected] = useState(false)
  const [editingSheet, setEditingSheet] = useState(false)
  const [newCharName, setNewCharName] = useState('')
  const [charError, setCharError] = useState<string | null>(null)

  const [phase, setPhase] = useState<Phase>('idle')
  const [progress, setProgress] = useState({ current: 0, total: 0 })
  const [retryNote, setRetryNote] = useState<string | null>(null)
  const [results, setResults] = useState<LoraDatasetProgressEvent[]>([])
  const [summary, setSummary] = useState<LoraDatasetCompleteEvent | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  // 手動取り込み
  const [importImages, setImportImages] = useState<{ name: string; data: string }[]>([])
  const [importCaption, setImportCaption] = useState('')
  const [importDragOver, setImportDragOver] = useState(false)
  const [importMsg, setImportMsg] = useState<string | null>(null)
  const importFileRef = useRef<HTMLInputElement>(null)

  function loadCharacters() {
    fetch('/api/story/characters').then(r => (r.ok ? r.json() : [])).then(setCharacters).catch(() => {})
  }

  // 物語を書かなくてもキャラ別データセットを始められるよう、ここでもキャラを作れるようにする
  async function createCharacter() {
    const name = newCharName.trim()
    if (!name) return
    setCharError(null)
    try {
      const res = await fetch('/api/story/characters', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, appearance_tags: '' }),
      })
      if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? '追加に失敗しました')
      const created: Character = await res.json()
      setNewCharName('')
      loadCharacters()
      setCharacterId(created.id)
      setEditingSheet(true)
    } catch (e) {
      setCharError(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => {
    loadCharacters()
    fetch('/api/lora-dataset/variations').then(r => (r.ok ? r.json() : null)).then(v => v && setDefaults(v)).catch(() => {})
  }, [])

  const character = characters.find(c => c.id === characterId) ?? null
  const hasReference = !!character?.reference_image_path
  // 成人キャラ以外では選べない(キャラを切り替えたら自動で全年齢に戻る)
  const r18 = r18Selected && !!character?.is_adult
  const rating = r18 ? 'r18' : 'general'
  const isRunning = phase === 'running'
  const combos = AXES.reduce((n, a) => n * Math.max(1, selected[a.key].length), 1)
  // 組み合わせより多い枚数も出せる(一巡するごとにシードをずらして繰り返す)
  const planned = count
  const rounds = Math.ceil(planned / combos)
  const canRun = !!token && !!character && !isRunning && (maxAttempts === 1 || hasReference)

  function toggle(axis: Axis, value: string) {
    const list = selected[axis]
    setSelected({ ...selected, [axis]: list.includes(value) ? list.filter(v => v !== value) : [...list, value] })
  }

  function addCustom(axis: Axis) {
    const value = custom[axis].trim()
    if (!value) return
    if (!defaults[axis].includes(value)) setDefaults({ ...defaults, [axis]: [...defaults[axis], value] })
    if (!selected[axis].includes(value)) setSelected({ ...selected, [axis]: [...selected[axis], value] })
    setCustom({ ...custom, [axis]: '' })
  }

  async function handleGenerate() {
    if (!canRun || !token || !character) return
    const controller = new AbortController()
    abortRef.current = controller
    setPhase('running')
    setResults([])
    setSummary(null)
    setErrorMsg(null)
    setRetryNote(null)
    setProgress({ current: 0, total: planned })

    const body: CharacterDatasetRequest = {
      root_name: rootName.trim() || 'training_data',
      ...selected,
      count,
      model: 'nai-diffusion-4-5-full',
      width,
      height,
      steps,
      scale,
      sampler: 'k_euler_ancestral',
      noise_schedule: 'karras',
      cfg_rescale: 0,
      use_reference: useReference,
      reference_fidelity: refFidelity,
      reference_strength: refStrength,
      reference_type: refType,
      max_attempts: maxAttempts,
      similarity_threshold: threshold,
      scorer,
      guard_profile_id: guardId,
      rating,
    }
    await streamCharacterDataset(
      token,
      character.id,
      body,
      e => {
        setRetryNote(null)
        setProgress({ current: e.current, total: e.total })
        setResults(prev => [e, ...prev])
      },
      (e: LoraDatasetRetryEvent) => {
        setRetryNote(`${e.file}: 類似度 ${e.score.toFixed(2)} が基準未満のため引き直し中 (${e.attempt}/${maxAttempts})`)
      },
      e => { setSummary(e); setPhase('complete') },
      msg => { setErrorMsg(msg); setPhase('error') },
      controller.signal,
    )
  }

  function handleCancel() {
    abortRef.current?.abort()
    setPhase('cancelled')
  }

  async function addImportFiles(files: FileList | File[]) {
    const images = await Promise.all(
      Array.from(files)
        .filter(f => f.type.startsWith('image/'))
        .map(async f => ({ name: f.name, data: await readFileAsDataURL(f) })),
    )
    setImportImages(prev => [...prev, ...images])
  }

  async function handleImport() {
    if (!character) return
    setImportMsg(null)
    let ok = 0
    for (const image of importImages) {
      const res = await fetch(`/api/lora-dataset/character/${character.id}/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          root_name: rootName.trim() || 'training_data',
          image: image.data,
          filename: image.name,
          caption: importCaption.trim() || null,
          guard_profile_id: guardId,
          rating,
        }),
      })
      if (!res.ok) {
        const detail = (await res.json().catch(() => ({}))).detail ?? `HTTP ${res.status}`
        setImportMsg(`${image.name}: ${detail}`)
        break
      }
      ok += 1
    }
    setImportImages(prev => prev.slice(ok))
    if (ok > 0) setImportMsg(prev => `${ok}枚を ${r18 ? 'manual_r18' : 'manual'}/ に取り込みました。${prev ?? ''}`)
  }

  const percent = progress.total > 0 ? Math.round((progress.current / progress.total) * 100) : 0

  return (
    <div className="lora-root">
      <header className="lora-header">
        <button type="button" className="lora-back" onClick={() => navigate('/')}>← ホーム</button>
        <span className="lora-header-title">キャラ別データセット</span>
      </header>

      <div className="lora-body">
        <aside className="lora-sidebar">
          <section className="lora-form">
            <label className="lora-label" htmlFor="chards-character">キャラクター</label>
            <select
              id="chards-character"
              className="lora-select"
              value={characterId ?? ''}
              onChange={e => setCharacterId(e.target.value ? Number(e.target.value) : null)}
              disabled={isRunning}
            >
              <option value="">選択してください</option>
              {characters.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <div className="chards-new-char">
              <input
                className="lora-input"
                placeholder="新しいキャラ名"
                value={newCharName}
                onChange={e => setNewCharName(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && void createCharacter()}
                disabled={isRunning}
              />
              <button
                type="button"
                className="lora-btn lora-btn--secondary"
                onClick={() => void createCharacter()}
                disabled={isRunning || !newCharName.trim()}
              >
                追加
              </button>
            </div>
            {charError && <p className="lora-error" role="alert">{charError}</p>}

            {character && editingSheet && (
              <div className="chards-sheet chards-sheet--edit">
                <CharacterSheetEditor
                  apiOrigin=""
                  character={character}
                  onSaved={loadCharacters}
                  disabled={isRunning}
                  classes={SHEET_CLASSES}
                  showReference
                  fileUrl={fileUrl}
                  onDuplicated={created => {
                    loadCharacters()
                    setCharacterId(created.id)
                  }}
                  onDeleted={() => {
                    loadCharacters()
                    setCharacterId(null)
                    setEditingSheet(false)
                  }}
                />
              </div>
            )}

            {character && !editingSheet && (
              <div className="chards-sheet">
                {hasReference && (
                  <img className="chards-ref" src={fileUrl(character.reference_image_path!)} alt={`${character.name}の参照画像`} />
                )}
                <dl>
                  <dt>トリガー</dt><dd>{character.trigger_word || '(なし・名前をフォルダ名に使用)'}</dd>
                  <dt>容姿</dt><dd>{character.appearance_tags || '(未設定)'}</dd>
                  <dt>普段の服装</dt><dd>{character.outfit_tags || '(未設定)'}</dd>
                  <dt>基準シード</dt><dd>{character.seed ?? '(ランダム)'}</dd>
                  <dt>参照画像</dt><dd>{hasReference ? 'あり' : 'なし'}</dd>
                </dl>
              </div>
            )}
            {character && (
              <button
                type="button"
                className="lora-btn lora-btn--secondary"
                onClick={() => setEditingSheet(!editingSheet)}
                disabled={isRunning}
              >
                {editingSheet ? 'キャラシートの編集を閉じる' : 'キャラシートを編集'}
              </button>
            )}

            <label className="lora-label" htmlFor="chards-root">出力ルート(outputs/ 配下)</label>
            <input id="chards-root" className="lora-input" value={rootName} onChange={e => setRootName(e.target.value)} disabled={isRunning} />

            <label className="lora-label" htmlFor="chards-count">生成枚数</label>
            <input
              id="chards-count" className="lora-input" type="number" min={1} max={200}
              value={count} onChange={e => setCount(Math.max(1, Number(e.target.value) || 1))} disabled={isRunning}
            />
            <p className="lora-hint">
              組み合わせ {combos} 通りから {planned} 枚。
              {rounds > 1 && ` 組み合わせが足りない分は、シードをずらして繰り返します(${rounds}周)。`}
              {' '}内部で1枚ずつ順番にリクエストします。
            </p>

            <div className="chards-row">
              <label className="lora-label">幅<input className="lora-input" type="number" step={64} value={width} onChange={e => setWidth(Number(e.target.value))} disabled={isRunning} /></label>
              <label className="lora-label">高さ<input className="lora-input" type="number" step={64} value={height} onChange={e => setHeight(Number(e.target.value))} disabled={isRunning} /></label>
            </div>
            <div className="chards-row">
              <label className="lora-label">Steps<input className="lora-input" type="number" min={1} max={50} value={steps} onChange={e => setSteps(Number(e.target.value))} disabled={isRunning} /></label>
              <label className="lora-label">Scale<input className="lora-input" type="number" step={0.1} value={scale} onChange={e => setScale(Number(e.target.value))} disabled={isRunning} /></label>
            </div>

            <fieldset className="lora-fieldset">
              <legend>キャラ参照(V4.5)</legend>
              <label className="lora-checkbox-label">
                <input type="checkbox" checked={useReference} onChange={e => setUseReference(e.target.checked)} disabled={isRunning || !hasReference} />
                参照画像を Character Reference に使う
              </label>
              <label className="lora-label">参照する範囲
                <select className="lora-select" value={refType} onChange={e => setRefType(e.target.value as 'character' | 'character&style')} disabled={isRunning}>
                  <option value="character">キャラのみ(服装・構図を変えやすい)</option>
                  <option value="character&style">キャラ＋画風(元画像に寄る)</option>
                </select>
              </label>
              <label className="lora-label">Fidelity {refFidelity.toFixed(2)}
                <input className="lora-range" type="range" min={0} max={1} step={0.05} value={refFidelity} onChange={e => setRefFidelity(Number(e.target.value))} disabled={isRunning} />
              </label>
              <label className="lora-label">Strength {refStrength.toFixed(2)}
                <input className="lora-range" type="range" min={0} max={1} step={0.05} value={refStrength} onChange={e => setRefStrength(Number(e.target.value))} disabled={isRunning} />
              </label>
            </fieldset>

            <fieldset className="lora-fieldset">
              <legend>引き直し(ガチャ対策)</legend>
              <label className="lora-label">1枚あたりの最大試行回数
                <select className="lora-select" value={maxAttempts} onChange={e => setMaxAttempts(Number(e.target.value))} disabled={isRunning}>
                  {[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n === 1 ? '1(引き直さない)' : n}</option>)}
                </select>
              </label>
              <label className="lora-label">合格ライン(類似度) {threshold.toFixed(2)}
                <input className="lora-range" type="range" min={0} max={1} step={0.05} value={threshold} onChange={e => setThreshold(Number(e.target.value))} disabled={isRunning} />
              </label>
              <label className="lora-label">判定方法
                <select className="lora-select" value={scorer} onChange={e => setScorer(e.target.value as 'color' | 'vlm')} disabled={isRunning}>
                  <option value="color">色の近さ(ローカル・高速)</option>
                  <option value="vlm">画像AIで採点(髪型・髪飾りまで見る・低速)</option>
                </select>
              </label>
              <p className="lora-hint">
                参照画像との類似度が合格ライン未満なら、シードをずらして引き直し、最も似た1枚を採用します。
                引き直しも1回ごとにAnlasを使います(最大 {planned * maxAttempts} リクエスト)。外れた画像は rejected/ に残ります。
                検証では採点が当たり外れを見分けられなかったため、通常は「1(引き直さない)」を推奨します。
              </p>
              {maxAttempts > 1 && !hasReference && <p className="lora-warn">引き直しには参照画像が必要です。</p>}
            </fieldset>

            <fieldset className="lora-fieldset">
              <legend>レーティング</legend>
              <label className="lora-checkbox-label">
                <input type="radio" name="chards-rating" checked={!r18} onChange={() => setR18Selected(false)} disabled={isRunning} />
                全年齢
              </label>
              <label className="lora-checkbox-label">
                <input
                  type="radio" name="chards-rating" checked={r18}
                  onChange={() => setR18Selected(true)}
                  disabled={isRunning || !character?.is_adult}
                />
                R18(成人キャラのみ)
              </label>
              {character && !character.is_adult && (
                <p className="lora-hint">R18はキャラシートで「成人キャラ」にしたキャラだけで使えます。</p>
              )}
              {r18 && (
                <p className="lora-hint">
                  R18では未成年を示すタグ(school uniform, student など)は使えません。
                  プロンプトに adult, mature female を加え、ネガティブで幼い見た目を避けます。
                  保存先は generated_r18/・manual_r18/ です。バリエーションは「追加…」から入力してください。
                </p>
              )}
            </fieldset>

            <GuardProfiles selectedId={guardId} onSelect={setGuardId} disabled={isRunning} />

            <div className="lora-actions">
              {isRunning
                ? <button type="button" className="lora-btn lora-btn--danger" onClick={handleCancel}>中断</button>
                : (
                  <>
                    <input
                      className="lora-input chards-count-inline"
                      type="number"
                      min={1}
                      max={200}
                      aria-label="生成枚数"
                      value={count}
                      onChange={e => setCount(Math.min(200, Math.max(1, Number(e.target.value) || 1)))}
                    />
                    <button type="button" className="lora-btn lora-btn--primary" onClick={() => void handleGenerate()} disabled={!canRun}>
                      生成({planned}枚)
                    </button>
                  </>
                )}
            </div>
          </section>
        </aside>

        <main className="lora-main">
          <section className="lora-section">
            <span className="lora-section-label">バリエーション(全年齢向け)</span>
            {AXES.map(axis => (
              <div key={axis.key} className="chards-axis">
                <span className="chards-axis-label">
                  {axis.label}
                  {axis.key === 'outfits' && selected.outfits.length === 0 && <small>(未選択ならキャラシートの普段の服装)</small>}
                </span>
                <div className="chards-chips">
                  {defaults[axis.key].map(value => (
                    <button
                      key={value}
                      type="button"
                      className={`chards-chip${selected[axis.key].includes(value) ? ' chards-chip--on' : ''}`}
                      onClick={() => toggle(axis.key, value)}
                      disabled={isRunning}
                    >
                      {value}
                    </button>
                  ))}
                  <input
                    className="chards-custom"
                    placeholder="追加…"
                    value={custom[axis.key]}
                    onChange={e => setCustom({ ...custom, [axis.key]: e.target.value })}
                    onKeyDown={e => e.key === 'Enter' && addCustom(axis.key)}
                    disabled={isRunning}
                  />
                </div>
              </div>
            ))}
          </section>

          {phase !== 'idle' && (
            <section className="lora-section">
              <span className="lora-section-label">生成結果</span>
              <div className="lora-progress-wrap">
                <progress className={`lora-progress-bar${phase === 'complete' ? ' lora-progress-bar--done' : ''}`} value={progress.current} max={progress.total || 1} />
                <span className="lora-progress-text">{progress.current} / {progress.total} ({percent}%)</span>
              </div>
              {retryNote && <p className="lora-hint">{retryNote}</p>}
              {phase === 'cancelled' && <p className="lora-warn">⏹ 中断しました。生成済みの画像は保存されています。</p>}
              {summary && (
                <div className="lora-result-summary">
                  <span className="lora-result-item lora-result-item--ok">✔ 成功: {summary.succeeded}</span>
                  {summary.failed > 0 && <span className="lora-result-item lora-result-item--err">✖ 失敗: {summary.failed}</span>}
                  <span className="lora-result-item">📁 {summary.output_path}</span>
                </div>
              )}
              <div className="lora-preview-grid">
                {results.map(e => (
                  <div key={e.file} className="lora-preview-card" title={e.prompt}>
                    <span className="lora-preview-label">
                      {e.file}
                      {e.score != null && ` · 類似度 ${e.score.toFixed(2)}`}
                      {e.attempts != null && e.attempts > 1 && ` · ${e.attempts}回目`}
                    </span>
                    {e.status === 'ok' && e.image_b64
                      ? <img className="lora-preview-img" src={`data:image/png;base64,${e.image_b64}`} alt={e.file} />
                      : <span className="lora-preview-failed">✖ 失敗{e.message ? `: ${e.message}` : ''}</span>}
                  </div>
                ))}
              </div>
            </section>
          )}
          {errorMsg && <p className="lora-error" role="alert">{errorMsg}</p>}

          <section className="lora-section">
            <span className="lora-section-label">手持ちの画像を取り込む</span>
            <div
              className={`lora-dropzone${importDragOver ? ' lora-dropzone--over' : ''}`}
              onClick={() => importFileRef.current?.click()}
              onDragOver={e => { e.preventDefault(); setImportDragOver(true) }}
              onDragLeave={() => setImportDragOver(false)}
              onDrop={e => { e.preventDefault(); setImportDragOver(false); void addImportFiles(e.dataTransfer.files) }}
              role="button"
              tabIndex={0}
            >
              {importImages.length === 0
                ? 'ここに画像をドロップ(またはクリックして選択)'
                : <div className="chards-import-thumbs">{importImages.map((img, i) => <img key={i} src={img.data} alt={img.name} />)}</div>}
            </div>
            <input
              ref={importFileRef} type="file" accept="image/*" multiple hidden
              onChange={e => { if (e.target.files) void addImportFiles(e.target.files); e.target.value = '' }}
            />
            <label className="lora-label" htmlFor="chards-caption">キャプション(空欄ならトリガーワード+容姿タグ)</label>
            <textarea
              id="chards-caption" className="lora-textarea" rows={2}
              placeholder={character ? [character.trigger_word, character.appearance_tags].filter(Boolean).join(', ') : ''}
              value={importCaption}
              onChange={e => setImportCaption(e.target.value)}
            />
            <div className="lora-actions">
              <button type="button" className="lora-btn lora-btn--secondary" onClick={() => setImportImages([])} disabled={importImages.length === 0}>クリア</button>
              <button type="button" className="lora-btn lora-btn--primary" onClick={() => void handleImport()} disabled={!character || importImages.length === 0}>
                {importImages.length}枚を取り込む
              </button>
            </div>
            {importMsg && <p className="lora-hint">{importMsg}</p>}
          </section>
        </main>
      </div>
    </div>
  )
}
