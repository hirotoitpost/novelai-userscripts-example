import { useEffect, useRef, useState } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'
import BackButton from '../components/BackButton'
import { useAuth } from '../context/AuthContext'
import { useLocalStorage } from '../hooks/useLocalStorage'
import {
  apiFetch,
  ImagePreset,
  streamCharacterDataset,
  CharacterDatasetRequest,
  LoraDatasetCompleteEvent,
  LoraDatasetProgressEvent,
  LoraDatasetRetryEvent,
} from '../api'
import type { Character } from '../components/StoryCharacters'
import CharacterSheetEditor, { type SheetEditorClasses } from '../components/CharacterSheetEditor'
import CharacterTransfer from '../components/CharacterTransfer'
import GuardProfiles from '../components/GuardProfiles'
import ImagePreview from '../components/ImagePreview'
import { TaskStatusDialog, useTaskStatus } from '../components/TaskStatus'
import VariationImportDialog, { AXES } from '../components/VariationImportDialog'
import DatasetScenes from '../components/DatasetScenes'
import FaceSheet from '../components/FaceSheet'
import BodyBase from '../components/BodyBase'
import Wardrobe from '../components/Wardrobe'
import type { DatasetState, Situation } from '../components/DatasetScenes'
import type { Axis, Variations } from '../components/VariationImportDialog'
import { MODELS, SIZES, SAMPLERS, NOISE_SCHEDULES, UC_PRESETS } from '../imageOptions'
import './LoraDataset.css'
import './CharacterDataset.css'


type Phase = 'idle' | 'running' | 'complete' | 'error' | 'cancelled'

type TabId = 'dataset' | 'body' | 'wardrobe'
const TABS: { id: TabId; label: string }[] = [
  { id: 'dataset', label: 'データセット' },
  { id: 'body', label: '素体' },
  { id: 'wardrobe', label: '衣装' },
]

// キャラ参照(Character Reference)は NovelAI が V4.5 系でしか受け付けない
const REFERENCE_MODELS = new Set(['nai-diffusion-4-5-full', 'nai-diffusion-4-5-curated'])

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
  // キャラ一覧の「顔・表情」から開いたとき(#faces)は、顔・表情シートを開いておく
  const [faceSheetOpen, setFaceSheetOpen] = useState(() => window.location.hash === '#faces')
  // 右側のタブ。キャラ一覧の「素体」「衣装」から開いたとき(#body / #wardrobe)はそのタブ
  const [tab, setTab] = useState<TabId>(() => (
    window.location.hash === '#body' ? 'body' : window.location.hash === '#wardrobe' ? 'wardrobe' : 'dataset'
  ))
  const location = useLocation()

  const [characters, setCharacters] = useState<Character[]>([])
  const [characterId, setCharacterId] = useLocalStorage<number | null>('nai_chards_character', null)
  // 物語の「登場人物」から開いたとき(?character=ID)は、そのキャラを選ぶ。選んだら URL からは外す
  // (後で別のキャラを選んでも、開き直したときに戻されないように)
  const [searchParams, setSearchParams] = useSearchParams()
  useEffect(() => {
    const fromLink = Number(searchParams.get('character'))
    if (!fromLink) return
    setCharacterId(fromLink)
    // キャラ一覧から来たときの「戻る」(state)は残す
    setSearchParams({}, { replace: true, state: location.state })
  }, [searchParams, setCharacterId, setSearchParams, location.state])
  const [defaults, setDefaults] = useState<Variations>({ framings: [], poses: [], outfits: [], expressions: [], locations: [] })
  const [selected, setSelected] = useLocalStorage<Variations>('nai_chards_selected', {
    framings: [], poses: [], outfits: [], expressions: [], locations: [],
  })
  const [custom, setCustom] = useState<Record<Axis, string>>({ framings: '', poses: '', outfits: '', expressions: '', locations: '' })
  // バリエーションの候補(DB にある。プロンプトから読み込む・1つずつ足す・整理で消す)
  const [importing, setImporting] = useState(false)
  // シチュエーション・状態(選んだID。端末ごとに覚える)と、その一覧
  const [situationIds, setSituationIds] = useLocalStorage<number[]>('nai_chards_situations', [])
  const [stateIds, setStateIds] = useLocalStorage<number[]>('nai_chards_states', [])
  const [includePlain, setIncludePlain] = useLocalStorage('nai_chards_include_plain', true)
  const [stateMethod, setStateMethod] = useLocalStorage<'prompt' | 'inpaint'>('nai_chards_state_method', 'prompt')
  const [inpaintStrength, setInpaintStrength] = useLocalStorage('nai_chards_inpaint_strength', 1.0)
  const [saveFaces, setSaveFaces] = useLocalStorage('nai_chards_face_crops', false)
  const [sceneLists, setSceneLists] = useState<{ situations: Situation[]; states: DatasetState[] }>({ situations: [], states: [] })
  const [tidying, setTidying] = useState(false)
  const [variationNote, setVariationNote] = useState<string | null>(null)

  const [rootName, setRootName] = useLocalStorage('nai_chards_root', 'training_data')
  const [count, setCount] = useLocalStorage('nai_chards_count', 10)
  const [width, setWidth] = useLocalStorage('nai_chards_width', 832)
  const [height, setHeight] = useLocalStorage('nai_chards_height', 1216)
  const [steps, setSteps] = useLocalStorage('nai_chards_steps', 23)
  const [scale, setScale] = useLocalStorage('nai_chards_scale', 5.0)
  const [model, setModel] = useLocalStorage('nai_chards_model', 'nai-diffusion-4-5-full')
  const [sampler, setSampler] = useLocalStorage('nai_chards_sampler', 'k_euler_ancestral')
  const [noiseSchedule, setNoiseSchedule] = useLocalStorage('nai_chards_noise_schedule', 'karras')
  const [cfgRescale, setCfgRescale] = useLocalStorage('nai_chards_cfg_rescale', 0)
  const [ucPreset, setUcPreset] = useLocalStorage('nai_chards_uc_preset', 'light')
  const [quality, setQuality] = useLocalStorage('nai_chards_quality', true)
  const [varietyBoost, setVarietyBoost] = useLocalStorage('nai_chards_variety_boost', false)
  // 画像生成ページのプリセット(/api/image/presets)から、画像の設定だけを読み込む
  const [presets, setPresets] = useState<ImagePreset[]>([])
  const [presetMsg, setPresetMsg] = useState<string | null>(null)
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
  // 生成の進み具合(ほかの画面と同じ処理状況のダイアログ。最小化すれば別のページへ移っても見える)
  const task = useTaskStatus()
  // 生成結果の拡大表示(results の番号)
  const [preview, setPreview] = useState<number | null>(null)

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
    fetch('/api/lora-dataset/variations').then(r => (r.ok ? r.json() : null)).then(v => v && applyVariations(v)).catch(() => {})
  }, [])

  useEffect(() => {
    if (!token) return
    apiFetch<ImagePreset[]>(token, '/api/image/presets')
      .then(setPresets)
      .catch(() => {})  // 一覧が取れなくても生成はできるのでサイレント失敗
  }, [token])

  function applyPreset(preset: ImagePreset) {
    const s = preset.settings
    const notes: string[] = []
    if (MODELS.some(m => m.value === s.model)) setModel(s.model)
    else notes.push(`モデル ${s.model} はここでは使えないため変更していません`)
    setWidth(s.width)
    setHeight(s.height)
    setSteps(s.steps)
    setScale(s.scale)
    setSampler(s.sampler)
    setNoiseSchedule(s.noise_schedule)
    setCfgRescale(s.cfg_rescale)
    setVarietyBoost(s.variety_boost ?? false)
    // シード値とネガティブプロンプトはキャラシートのものを使う
    setPresetMsg([`「${preset.name}」を読み込みました(シード値・ネガティブはキャラシートのまま)`, ...notes].join('。'))
  }

  const character = characters.find(c => c.id === characterId) ?? null
  const hasReference = !!character?.reference_image_path
  const referenceModel = REFERENCE_MODELS.has(model)
  const sizeValue = SIZES.find(z => z.width === width && z.height === height)?.value ?? 'custom'
  // 成人キャラ以外では選べない(キャラを切り替えたら自動で全年齢に戻る)
  const r18 = r18Selected && !!character?.is_adult
  const rating = r18 ? 'r18' : 'general'
  const isRunning = phase === 'running'
  const combos = AXES.reduce((n, a) => n * Math.max(1, selected[a.key].length), 1)
  // シチュエーション×状態の単位(1枚ごとにどれか1つ。単位ごとに均等に割り振る)
  const pickedSituations = sceneLists.situations.filter(s => situationIds.includes(s.id))
  const pickedStates = sceneLists.states.filter(s => stateIds.includes(s.id))
  // インペイントで作る状態(部位のあるもの)は、ふだんの体の1枚ごとに増える
  const inpaintStates = stateMethod === 'inpaint' ? pickedStates.filter(s => s.part && s.part !== 'none') : []
  const promptStates = pickedStates.filter(s => !inpaintStates.includes(s))
  const sceneUnits = Math.max(1, pickedSituations.length) * (
    inpaintStates.length > 0 ? 1 + promptStates.length
      : pickedStates.length > 0 ? pickedStates.length + (includePlain ? 1 : 0) : 1)
  const plainShots = inpaintStates.length > 0 ? Math.ceil(count / (1 + promptStates.length)) : 0
  const totalImages = count + plainShots * inpaintStates.length
  // 組み合わせより多い枚数も出せる(一巡するごとにシードをずらして繰り返す)
  const planned = count
  const rounds = Math.ceil(planned / combos)
  const canRun = !!token && !!character && !isRunning && (maxAttempts === 1 || hasReference)

  function toggle(axis: Axis, value: string) {
    const list = selected[axis]
    setSelected({ ...selected, [axis]: list.includes(value) ? list.filter(v => v !== value) : [...list, value] })
  }

  /** 候補の一覧を画面に入れる。候補から消えたものは、選択からも外す(見えないまま生成に使われないように)。 */
  function applyVariations(next: Variations, alsoSelect?: { axis: Axis; value: string }) {
    setDefaults(next)
    const kept = Object.fromEntries(
      AXES.map(a => [a.key, (selected[a.key] ?? []).filter(v => next[a.key].includes(v))]),
    ) as Variations
    if (alsoSelect && next[alsoSelect.axis].includes(alsoSelect.value) && !kept[alsoSelect.axis].includes(alsoSelect.value)) {
      kept[alsoSelect.axis] = [...kept[alsoSelect.axis], alsoSelect.value]
    }
    setSelected(kept)
  }

  /** 「追加…」に入れたキーワードを候補に足して(DB に残る)、選ぶ。 */
  async function addCustom(axis: Axis) {
    const value = custom[axis].trim()
    if (!value) return
    setVariationNote(null)
    try {
      const res = await fetch('/api/lora-dataset/variations', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ axis, values: [value] }),
      })
      if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`)
      const next = await res.json() as Variations
      // 保存される形(空白をそろえたもの)で選ぶ
      const saved = next[axis].find(v => v.toLowerCase() === value.replace(/\s*,\s*/g, ', ').replace(/\s+/g, ' ').toLowerCase()) ?? value
      applyVariations(next, { axis, value: saved })
      setCustom({ ...custom, [axis]: '' })
    } catch (e) {
      setVariationNote(`足せませんでした: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  /** 候補から消す(整理のとき)。 */
  async function removeVariation(axis: Axis, value: string) {
    if (!window.confirm(`「${value}」を${AXES.find(a => a.key === axis)?.label}の候補から消します。よろしいですか?`)) return
    setVariationNote(null)
    try {
      const res = await fetch(`/api/lora-dataset/variations?axis=${axis}&value=${encodeURIComponent(value)}`, { method: 'DELETE' })
      if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `HTTP ${res.status}`)
      applyVariations(await res.json())
    } catch (e) {
      setVariationNote(`消せませんでした: ${e instanceof Error ? e.message : String(e)}`)
    }
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
    setPreview(null)
    setProgress({ current: 0, total: planned })
    task.start(`データセットの生成: ${character.name}`, `${planned}枚を生成します`)
    task.update(`1枚目を生成中(${planned}枚)`, 0, planned)

    const body: CharacterDatasetRequest = {
      root_name: rootName.trim() || 'training_data',
      ...selected,
      situation_ids: pickedSituations.map(s => s.id),
      state_ids: pickedStates.map(s => s.id),
      include_plain_state: includePlain,
      state_method: stateMethod,
      inpaint_strength: inpaintStrength,
      save_face_crops: saveFaces,
      count,
      model,
      width,
      height,
      steps,
      scale,
      sampler,
      noise_schedule: noiseSchedule,
      cfg_rescale: cfgRescale,
      quality,
      uc_preset: ucPreset,
      variety_boost: varietyBoost,
      use_reference: useReference && referenceModel,
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
        const last = e.status === 'ok' ? `${e.file} ができました` : `${e.file} は失敗しました`
        task.update(e.current < e.total ? `${last}。${e.current + 1}枚目を生成中` : last, e.current, e.total)
      },
      (e: LoraDatasetRetryEvent) => {
        const note = `${e.file}: 類似度 ${e.score.toFixed(2)} が基準未満のため引き直し中 (${e.attempt}/${maxAttempts})`
        setRetryNote(note)
        task.update(note)
      },
      e => {
        setSummary(e)
        setPhase('complete')
        task.finish(
          // 一部だけ失敗したときは完了扱い(できた分は保存されている)。1枚もできなければ失敗
          e.succeeded > 0 ? 'done' : 'error',
          `成功 ${e.succeeded}枚${e.failed > 0 ? `・失敗 ${e.failed}枚` : ''}(保存先: ${e.output_path})`,
        )
      },
      msg => {
        setErrorMsg(msg)
        setPhase('error')
        task.finish('error', msg)
      },
      controller.signal,
    )
  }

  function handleCancel() {
    abortRef.current?.abort()
    setPhase('cancelled')
    task.finish('cancelled', '中断しました。生成済みの画像は保存されています。')
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
  // 拡大表示できる(生成できた)結果
  const previewable = results.filter(e => e.status === 'ok' && e.image_b64)

  return (
    <div className="lora-root">
      <header className="lora-header">
        <BackButton className="lora-back" />
        <span className="lora-header-title">キャラ別データセット</span>
        <nav className="chards-tabs" role="tablist">
          {TABS.map(t => (
            <button key={t.id} type="button" role="tab" aria-selected={tab === t.id}
              className={tab === t.id ? 'chards-tab chards-tab--on' : 'chards-tab'} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </nav>
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
            <CharacterTransfer apiOrigin="" characters={characters} onImported={loadCharacters} />

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
                  candidateStyle="illustration"
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
            {character && (
              <details className="chards-faces" open={faceSheetOpen} onToggle={e => setFaceSheetOpen((e.target as HTMLDetailsElement).open)}>
                <summary>顔・表情シート(向き・表情を描いて見る)</summary>
                <FaceSheet characterId={character.id} characterName={character.name} token={token} rootName={rootName}
                  disabled={isRunning} />
              </details>
            )}

            <label className="lora-label" htmlFor="chards-root">出力ルート(outputs/ 配下)</label>
            <input id="chards-root" className="lora-input" value={rootName} onChange={e => setRootName(e.target.value)} disabled={isRunning} />

            <label className="lora-label" htmlFor="chards-count">生成枚数</label>
            <input
              id="chards-count" className="lora-input" type="number" min={1} max={200}
              value={count} onChange={e => setCount(Math.max(1, Number(e.target.value) || 1))} disabled={isRunning}
            />
            <p className="lora-hint">
              {sceneUnits > 1 || pickedSituations.length > 0
                ? `シチュエーション${pickedSituations.length || 'なし'}・状態${pickedStates.length ? pickedStates.length + (includePlain ? '+ふだん' : '') : 'なし'}の${sceneUnits}通りに、${planned}枚を均等に割り振ります(1通りあたり約${Math.ceil(planned / sceneUnits)}枚)。`
                : `組み合わせ ${combos} 通りから ${planned} 枚。`}
              {sceneUnits === 1 && pickedSituations.length === 0 && rounds > 1 && ` 組み合わせが足りない分は、シードをずらして繰り返します(${rounds}周)。`}
              {inpaintStates.length > 0 && ` 描き直しの絵が約${totalImages - count}枚増え、全部で約${totalImages}枚になります。`}
              {' '}内部で1枚ずつ順番にリクエストします。
            </p>

            <fieldset className="lora-fieldset">
              <legend>画像の設定</legend>
              {presets.length > 0 && (
                <label className="lora-label">画像生成のプリセットから読み込む
                  <select
                    className="lora-select" value=""
                    onChange={e => {
                      const p = presets.find(x => String(x.id) === e.target.value)
                      if (p) applyPreset(p)
                    }}
                    disabled={isRunning}
                  >
                    <option value="">選択して読み込み...</option>
                    {presets.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </select>
                </label>
              )}
              {presetMsg && <p className="lora-hint">{presetMsg}</p>}
              <label className="lora-label">モデル
                <select className="lora-select" value={model} onChange={e => setModel(e.target.value)} disabled={isRunning}>
                  {MODELS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                </select>
              </label>
              <label className="lora-label">サイズ
                <select
                  className="lora-select" value={sizeValue}
                  onChange={e => {
                    const z = SIZES.find(x => x.value === e.target.value)
                    if (z) { setWidth(z.width); setHeight(z.height) }
                  }}
                  disabled={isRunning}
                >
                  {sizeValue === 'custom' && <option value="custom">カスタム ({width}×{height})</option>}
                  {SIZES.map(z => <option key={z.value} value={z.value}>{z.label}</option>)}
                </select>
              </label>
              <div className="chards-row">
                <label className="lora-label">幅<input className="lora-input" type="number" step={64} value={width} onChange={e => setWidth(Number(e.target.value))} disabled={isRunning} /></label>
                <label className="lora-label">高さ<input className="lora-input" type="number" step={64} value={height} onChange={e => setHeight(Number(e.target.value))} disabled={isRunning} /></label>
              </div>
              <div className="chards-row">
                <label className="lora-label">ステップ<input className="lora-input" type="number" min={1} max={50} value={steps} onChange={e => setSteps(Number(e.target.value))} disabled={isRunning} /></label>
                <label className="lora-label">プロンプトガイダンス<input className="lora-input" type="number" min={0} max={10} step={0.1} value={scale} onChange={e => setScale(Number(e.target.value))} disabled={isRunning} /></label>
              </div>
              <div className="chards-row">
                <label className="lora-label">サンプラー
                  <select className="lora-select" value={sampler} onChange={e => setSampler(e.target.value)} disabled={isRunning}>
                    {SAMPLERS.map(x => <option key={x.value} value={x.value}>{x.label}</option>)}
                  </select>
                </label>
                <label className="lora-label">ノイズ設定
                  <select className="lora-select" value={noiseSchedule} onChange={e => setNoiseSchedule(e.target.value)} disabled={isRunning}>
                    {NOISE_SCHEDULES.map(n => <option key={n} value={n}>{n}</option>)}
                  </select>
                </label>
              </div>
              <label className="lora-label">プロンプトガイダンスの再調整 {cfgRescale.toFixed(2)}
                <input className="lora-range" type="range" min={0} max={1} step={0.01} value={cfgRescale} onChange={e => setCfgRescale(Number(e.target.value))} disabled={isRunning} />
              </label>
              <label className="lora-label">UC プリセット
                <select className="lora-select" value={ucPreset} onChange={e => setUcPreset(e.target.value)} disabled={isRunning}>
                  {UC_PRESETS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </label>
              <label className="lora-checkbox-label">
                <input type="checkbox" checked={quality} onChange={e => setQuality(e.target.checked)} disabled={isRunning} />
                品質タグを自動付与
              </label>
              <label className="lora-checkbox-label">
                <input type="checkbox" checked={varietyBoost} onChange={e => setVarietyBoost(e.target.checked)} disabled={isRunning} />
                多様性(Variety Boost)
              </label>
            </fieldset>

            <fieldset className="lora-fieldset">
              <legend>キャラ参照(V4.5)</legend>
              <label className="lora-checkbox-label">
                <input type="checkbox" checked={useReference && referenceModel} onChange={e => setUseReference(e.target.checked)} disabled={isRunning || !hasReference || !referenceModel} />
                参照画像を Character Reference に使う
              </label>
              {!referenceModel && <p className="lora-hint">キャラ参照は V4.5 系のモデルでだけ使えます。</p>}
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

            <fieldset className="lora-fieldset">
              <legend>顔の切り抜き</legend>
              <label className="lora-checkbox-label">
                <input type="checkbox" checked={saveFaces} onChange={e => setSaveFaces(e.target.checked)} disabled={isRunning} />
                顔の切り抜きも保存する(faces/ に置きます)
              </label>
              <p className="lora-hint">
                生成した絵から頭を見つけて、顔のアップを正方形で切り抜きます(256px より小さい顔は飛ばします)。
                キャプションは、トリガーワード+容姿(顔・髪・目)+ portrait, close-up です。学習で顔を覚えさせやすくします。
              </p>
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

        {tab === 'body' ? (
          <main className="lora-main">
            {character
              ? <BodyBase characterId={character.id} characterName={character.name} token={token} rootName={rootName} disabled={isRunning} />
              : <p className="lora-hint">キャラクターを選んでください。</p>}
          </main>
        ) : tab === 'wardrobe' ? (
          <main className="lora-main">
            {character
              ? <Wardrobe characterId={character.id} token={token} rootName={rootName} disabled={isRunning} onChanged={loadCharacters} />
              : <p className="lora-hint">キャラクターを選んでください。</p>}
          </main>
        ) : (
        <main className="lora-main">
          {token && (
            <DatasetScenes
              token={token}
              isAdult={!!character?.is_adult}
              disabled={isRunning}
              situationIds={situationIds}
              onSituationIds={setSituationIds}
              stateIds={stateIds}
              onStateIds={setStateIds}
              includePlain={includePlain}
              onIncludePlain={setIncludePlain}
              onLoaded={(situations, states) => setSceneLists({ situations, states })}
              stateMethod={stateMethod}
              onStateMethod={setStateMethod}
              inpaintStrength={inpaintStrength}
              onInpaintStrength={setInpaintStrength}
            />
          )}

          <section className="lora-section">
            <span className="lora-section-label">バリエーション(全年齢向け)</span>
            <div className="chards-variation-tools">
              <button type="button" className="lora-btn" onClick={() => setImporting(true)} disabled={isRunning || !token}
                title="プロンプト(貼り付け・画像生成の履歴・画像)やプロンプトチャンクから、キーワードを取り出して候補に足します">
                キーワードを読み込む(プロンプト・チャンク)
              </button>
              <button type="button" className={`lora-btn${tidying ? ' lora-btn--primary' : ''}`} aria-pressed={tidying}
                onClick={() => setTidying(!tidying)} disabled={isRunning}
                title="要らない候補を消します(押すと消すモードになり、候補を押すと消えます)">
                {tidying ? '整理を終える' : '候補を整理(消す)'}
              </button>
            </div>
            {tidying && <p className="lora-hint">消したい候補を押してください(確認のあと消えます)。</p>}
            {variationNote && <p className="lora-hint" role="status">{variationNote}</p>}
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
                      className={`chards-chip${selected[axis.key].includes(value) ? ' chards-chip--on' : ''}${tidying ? ' chards-chip--tidy' : ''}`}
                      onClick={() => (tidying ? void removeVariation(axis.key, value) : toggle(axis.key, value))}
                      disabled={isRunning}
                      aria-label={tidying ? `${axis.label}の候補「${value}」を消す` : undefined}
                    >
                      {value}{tidying && ' ✕'}
                    </button>
                  ))}
                  <input
                    className="chards-custom"
                    placeholder="追加…"
                    value={custom[axis.key]}
                    onChange={e => setCustom({ ...custom, [axis.key]: e.target.value })}
                    onKeyDown={e => { if (e.key === 'Enter') void addCustom(axis.key) }}
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
                      {e.situation && ` · ${e.situation}`}
                      {e.state && ` · ${e.state}`}
                      {e.method === 'inpaint' && e.base && ` · ${e.base}を描き直し`}
                      {e.method === 'prompt' && e.base && ' · 頭が見つからずタグで生成'}
                      {e.face_crop && ' · 顔も保存'}
                      {e.score != null && ` · 類似度 ${e.score.toFixed(2)}`}
                      {e.attempts != null && e.attempts > 1 && ` · ${e.attempts}回目`}
                    </span>
                    {e.status === 'ok' && e.image_b64
                      ? (
                        <button
                          type="button"
                          className="chards-zoom"
                          onClick={() => setPreview(previewable.findIndex(p => p.file === e.file))}
                          aria-label={`${e.file}を拡大`}
                        >
                          <img className="lora-preview-img" src={`data:image/png;base64,${e.image_b64}`} alt={e.file} />
                        </button>
                      )
                      : <span className="lora-preview-failed">✖ 失敗{e.message ? `: ${e.message}` : ''}</span>}
                  </div>
                ))}
              </div>
              <ImagePreview
                images={previewable.map(e => ({
                  src: `data:image/png;base64,${e.image_b64}`,
                  alt: e.file,
                  caption: [e.file, e.score != null ? `類似度 ${e.score.toFixed(2)}` : '', e.prompt].filter(Boolean).join(' ・ '),
                }))}
                index={preview}
                onIndexChange={setPreview}
              />
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
        )}
      </div>

      {importing && token && (
        <VariationImportDialog
          token={token}
          existing={defaults}
          character={character}
          onClose={() => setImporting(false)}
          onAdded={(next, added, sheetAdded) => {
            applyVariations(next)
            if (sheetAdded > 0) loadCharacters()
            setVariationNote([
              added > 0 ? `${added}個のキーワードを候補に足しました(使うものを選んでください)。` : '',
              sheetAdded > 0 && character ? `「${character.name}」のキャラシートに${sheetAdded}個のキーワードを入れました。` : '',
            ].filter(Boolean).join(' '))
            setImporting(false)
          }}
        />
      )}
      <TaskStatusDialog
        status={task.status}
        minimized={task.minimized}
        onMinimize={task.setMinimized}
        onCancel={handleCancel}
        onClose={task.dismiss}
      />
    </div>
  )
}
