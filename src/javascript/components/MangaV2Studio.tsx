import { useEffect, useMemo, useState } from 'react'
import { useLocalStorage } from '../hooks/useLocalStorage'
import { MangaImageSettingsValue } from './MangaImageSettings'
import { SceneCharacter } from './StoryCharacters'
import MangaV2PageEditor, { MangaV2Element } from './MangaV2PageEditor'
import MangaV2SfxFonts from './MangaV2SfxFonts'
import MangaV2Stamps from './MangaV2Stamps'

/** 漫画v2で使うシーンの項目(Story ページの StoryScene の一部)。 */
export interface MangaV2Scene {
  id: number
  scene_index: number
  draft_title: string | null
  draft_text: string
  novelai_text: string | null
  /** null は未設定(AI提案の対象)、空配列は「効果音なし」と決めた状態。 */
  sfx: string[] | null
  /** null は未設定(AI作成の対象)、空文字は「ナレーションなし」。 */
  narration: string | null
  characters: SceneCharacter[]
}

interface FontOption {
  id: string
  label: string
}

interface TemplateOption {
  id: string
  label: string
  panels: number
}

interface Panel {
  scene_id: number
  scene_index: number
  image_path: string
  seed: number | null
  created_at: string
}

/** 合成の設定(サーバーの MangaV2ComposeRequest)。物語ごとに最後に合成した値が保存される。 */
export interface MangaV2ComposeSettings {
  template: string
  font: string | null
  sfx_font: string | null
  bubble_opacity: number
  max_lines_per_panel: number
  text_scale: number
  sfx_scale: number
}

interface ComposeResult {
  pages: string[]
  page_width: number
  page_height: number
  elements: MangaV2Element[][]
}

interface Props {
  apiOrigin: string
  storyId: number
  scenes: MangaV2Scene[]
  /** この物語で最後に合成したときの設定。開いたときにこれへ戻す(未合成なら null)。 */
  savedComposeSettings: MangaV2ComposeSettings | null
  imageSettings: MangaImageSettingsValue
  token: string | null
  busy: boolean
  /** 進捗表示・キャンセル・エラー表示を Story ページ側に任せて処理を走らせる。 */
  runTask: (label: string, task: (signal: AbortSignal) => Promise<string | void>) => Promise<void>
  /** 物語のバックグラウンドジョブが終わるまで待つ(進捗は Story ページに出る)。 */
  pollJob: (signal: AbortSignal) => Promise<void>
  /** シーン(効果音)や完成画像が変わったので物語を読み直す。 */
  onChanged: () => Promise<void> | void
  fileUrl: (path: string) => string
}

type DownloadFormat = 'pdf' | 'zip'
type DownloadContent = 'pages' | 'pages_clean' | 'panels' | 'panels_clean'

const DOWNLOAD_CONTENTS: { id: DownloadContent; label: string }[] = [
  { id: 'pages', label: '漫画(セリフあり)' },
  { id: 'pages_clean', label: 'セリフなし漫画' },
  { id: 'panels', label: 'ページ合成前のコマ画像(セリフあり)' },
  { id: 'panels_clean', label: 'ページ合成前のコマ画像(セリフなし)' },
]

/** Content-Disposition の filename*(日本語名)を優先して取り出す。 */
function downloadFilename(res: Response, fallback: string): string {
  const header = res.headers.get('Content-Disposition') ?? ''
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header)
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1])
    } catch {
      // 壊れた名前なら下の filename を使う
    }
  }
  return /filename="([^"]+)"/i.exec(header)?.[1] ?? fallback
}

const DIALOGUE_RE = /[「『]([^」』]*)[」』]/g
const SFX_SPLIT_RE = /[、,，\s　]+/

function dialogueCount(scene: MangaV2Scene): number {
  return [...(scene.novelai_text ?? scene.draft_text).matchAll(DIALOGUE_RE)].length
}

async function readErrorDetail(res: Response): Promise<string> {
  const text = await res.text()
  try {
    return JSON.parse(text).detail ?? text
  } catch {
    return text || res.statusText
  }
}

export default function MangaV2Studio({
  apiOrigin, storyId, scenes, savedComposeSettings, imageSettings, token, busy, runTask, pollJob, onChanged, fileUrl,
}: Props) {
  const [fonts, setFonts] = useState<FontOption[]>([])
  const [templates, setTemplates] = useState<TemplateOption[]>([])
  const [panels, setPanels] = useState<Panel[]>([])
  const [composed, setComposed] = useState<ComposeResult | null>(null)
  const [editPage, setEditPage] = useState(0)
  const [sfxFontsKey, setSfxFontsKey] = useState(0)

  const [template, setTemplate] = useLocalStorage('nai_manga_v2_template', 'grid4')
  const [font, setFont] = useLocalStorage('nai_manga_v2_font', 'yu-mincho-demibold')
  const [sfxFont, setSfxFont] = useLocalStorage('nai_manga_v2_sfx_font', 'hg-soei-kakugothic-ub')
  const [opacity, setOpacity] = useLocalStorage('nai_manga_v2_bubble_opacity', 100)
  const [color, setColor] = useLocalStorage('nai_manga_v2_color', false)
  const [maxLines, setMaxLines] = useLocalStorage('nai_manga_v2_max_lines_v2', 2)
  // 文字の大きさの全体の倍率(%)。コマの大きさ・叫び/小声による自動調整に、さらに掛ける
  const [textScale, setTextScale] = useLocalStorage('nai_manga_v2_text_scale', 100)
  const [sfxScale, setSfxScale] = useLocalStorage('nai_manga_v2_sfx_scale', 100)
  const [useReference, setUseReference] = useLocalStorage('nai_manga_v2_use_reference', false)
  const [refStrength, setRefStrength] = useLocalStorage('nai_manga_v2_ref_strength', 1.0)
  const [refFidelity, setRefFidelity] = useLocalStorage('nai_manga_v2_ref_fidelity', 1.0)
  const [downloadFormat, setDownloadFormat] = useLocalStorage<DownloadFormat>('nai_manga_v2_download_format', 'pdf')
  const [downloadContents, setDownloadContents] = useLocalStorage<DownloadContent[]>(
    'nai_manga_v2_download_contents',
    ['pages'],
  )
  const [skipExisting, setSkipExisting] = useState(true)
  const [overwriteSfx, setOverwriteSfx] = useState(false)
  // 描き文字の自動選択で成人向け素材のスタンプも使うか(既定は使わない)
  const [includeAdult, setIncludeAdult] = useLocalStorage('nai_manga_v2_include_adult', false)
  // 対象のシーン範囲(表示は1始まり)。既定は1ページ分。
  const [sceneFrom, setSceneFrom] = useState(1)
  const [sceneTo, setSceneTo] = useState(4)
  // 効果音の編集中の値(scene_id → 入力文字列)
  const [sfxDrafts, setSfxDrafts] = useState<Record<number, string>>({})
  const [narrationDrafts, setNarrationDrafts] = useState<Record<number, string>>({})

  const authHeaders: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {}

  function loadFonts() {
    fetch(`${apiOrigin}/api/manga-v2/fonts`).then(r => r.json()).then(setFonts).catch(() => {})
  }

  useEffect(() => {
    loadFonts()
    fetch(`${apiOrigin}/api/manga-v2/templates`).then(r => r.json()).then(setTemplates).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiOrigin])

  function loadPanels() {
    fetch(`${apiOrigin}/api/manga-v2/${storyId}/panels`)
      .then(r => (r.ok ? r.json() : []))
      .then(setPanels)
      .catch(() => {})
  }

  useEffect(() => {
    loadPanels()
    setComposed(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiOrigin, storyId])

  // 物語を開いたら、その物語で最後に合成した設定に戻す(端末ごとに見た目が変わらないように)。
  // 物語を読み直すたびに戻すと、合成前に変えた設定が消えるので、開いたときだけにする。
  useEffect(() => {
    const saved = savedComposeSettings
    if (!saved) return
    setTemplate(saved.template)
    if (saved.font) setFont(saved.font)
    if (saved.sfx_font) setSfxFont(saved.sfx_font)
    setOpacity(Math.round(saved.bubble_opacity * 100))
    setMaxLines(saved.max_lines_per_panel)
    setTextScale(Math.round(saved.text_scale * 100))
    setSfxScale(Math.round(saved.sfx_scale * 100))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storyId])

  // 物語を読み直したら、保存済みの効果音を入力欄に反映する(編集途中のものは保たない)
  useEffect(() => {
    setSfxDrafts(Object.fromEntries(scenes.map(s => [s.id, (s.sfx ?? []).join('、')])))
    setNarrationDrafts(Object.fromEntries(scenes.map(s => [s.id, s.narration ?? ''])))
  }, [scenes])

  const perPage = templates.find(t => t.id === template)?.panels ?? 4
  const panelByScene = useMemo(() => new Map(panels.map(p => [p.scene_id, p])), [panels])
  const from = Math.max(1, Math.min(sceneFrom, scenes.length))
  const to = Math.max(from, Math.min(sceneTo, scenes.length))
  const targetScenes = scenes.filter(s => s.scene_index >= from - 1 && s.scene_index <= to - 1)
  const fontLabel = (id: string) => fonts.find(f => f.id === id)?.label ?? id
  // この物語に出てくるキャラ(シーンへの割り当てから集める)
  const storyCharacters = useMemo(() => {
    const byId = new Map<number, SceneCharacter>()
    for (const scene of scenes) for (const c of scene.characters) byId.set(c.id, c)
    return [...byId.values()]
  }, [scenes])
  const referencedNames = storyCharacters.filter(c => c.reference_image_path).map(c => c.name)

  const referenceOptions = {
    use_character_reference: useReference,
    reference_strength: refStrength,
    reference_fidelity: refFidelity,
  }

  async function startJob(path: string, body: unknown, signal: AbortSignal) {
    const res = await fetch(`${apiOrigin}/api/manga-v2/${storyId}/${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders },
      body: JSON.stringify(body),
      signal,
    })
    if (!res.ok) throw new Error(await readErrorDetail(res))
    await pollJob(signal)
  }

  function generatePanels() {
    return runTask(`コマの絵の生成(シーン${from}〜${to})`, async signal => {
      try {
        await startJob(
          'panels',
          {
            scene_from: from - 1,
            scene_to: to - 1,
            template,
            skip_existing: skipExisting,
            color,
            ...referenceOptions,
            settings: imageSettings,
          },
          signal,
        )
      } finally {
        loadPanels()
      }
    })
  }

  function redrawPanel(scene: MangaV2Scene) {
    const seed = Math.floor(Math.random() * 4294967296)
    return runTask(`シーン${scene.scene_index + 1}の描き直し`, async signal => {
      try {
        await startJob(
          'panels',
          {
            scene_from: scene.scene_index,
            scene_to: scene.scene_index,
            template,
            skip_existing: false,
            color,
            ...referenceOptions,
            settings: { ...imageSettings, seed },
          },
          signal,
        )
      } finally {
        loadPanels()
      }
    })
  }

  function suggestSfx() {
    return runTask('効果音のAI提案', async signal => {
      await startJob(
        'suggest-sfx',
        { scene_from: from - 1, scene_to: to - 1, overwrite: overwriteSfx },
        signal,
      )
      await onChanged()
    })
  }

  function suggestSfxFonts() {
    return runTask('描き文字(スタンプ・フォント)のAI選択', async signal => {
      try {
        await startJob(
          'suggest-sfx-fonts',
          { scene_from: from - 1, scene_to: to - 1, overwrite: overwriteSfx, include_adult: includeAdult },
          signal,
        )
      } finally {
        setSfxFontsKey(key => key + 1)
      }
    })
  }

  function suggestNarration() {
    return runTask('ナレーションのAI作成', async signal => {
      await startJob(
        'suggest-narration',
        { scene_from: from - 1, scene_to: to - 1, overwrite: overwriteSfx },
        signal,
      )
      await onChanged()
    })
  }

  async function saveNarration(scene: MangaV2Scene, narration: string | null) {
    const res = await fetch(`${apiOrigin}/api/manga-v2/scenes/${scene.id}/narration`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ narration }),
    })
    if (res.ok) await onChanged()
  }

  function saveNarrationDraft(scene: MangaV2Scene) {
    const value = (narrationDrafts[scene.id] ?? '').trim()
    if (scene.narration === null ? value === '' : value === scene.narration) return
    void saveNarration(scene, value)
  }

  async function saveSfx(scene: MangaV2Scene, sfx: string[] | null) {
    const res = await fetch(`${apiOrigin}/api/manga-v2/scenes/${scene.id}/sfx`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sfx }),
    })
    if (res.ok) await onChanged()
  }

  function saveSfxDraft(scene: MangaV2Scene) {
    const words = (sfxDrafts[scene.id] ?? '').split(SFX_SPLIT_RE).map(w => w.trim()).filter(Boolean)
    const current = scene.sfx ?? []
    // 変わっていなければ保存しない(未設定のまま空欄でフォーカスを外しただけ等)
    if (scene.sfx !== null && words.join('、') === current.join('、')) return
    if (scene.sfx === null && words.length === 0) return
    void saveSfx(scene, words)
  }

  async function setReference(characterId: number, body: { image?: string; scene_id?: number } | null) {
    const res = await fetch(`${apiOrigin}/api/manga-v2/characters/${characterId}/reference`, {
      method: body ? 'PUT' : 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      ...(body ? { body: JSON.stringify(body) } : {}),
    })
    if (res.ok) await onChanged()
  }

  function uploadReference(characterId: number, file: File) {
    const reader = new FileReader()
    reader.onload = () => void setReference(characterId, { image: reader.result as string })
    reader.readAsDataURL(file)
  }

  // 合成とダウンロードで同じ設定を使う(ダウンロードは同じ見た目のページを作り直す)
  const composeSettings = {
    template,
    font,
    sfx_font: sfxFont,
    bubble_opacity: opacity / 100,
    max_lines_per_panel: maxLines,
    text_scale: textScale / 100,
    sfx_scale: sfxScale / 100,
  }

  async function requestCompose(signal: AbortSignal) {
    const res = await fetch(`${apiOrigin}/api/manga-v2/${storyId}/compose`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(composeSettings),
      signal,
    })
    if (!res.ok) throw new Error(await readErrorDetail(res))
    const data: ComposeResult = await res.json()
    setComposed(data)
    setEditPage(page => Math.min(page, data.pages.length - 1))
    await onChanged()
  }

  function compose() {
    return runTask('ページの合成', requestCompose)
  }

  function toggleDownloadContent(id: DownloadContent, checked: boolean) {
    const next = checked ? [...downloadContents, id] : downloadContents.filter(c => c !== id)
    // 表示順にそろえておく(zip のフォルダ・PDF の並びもこの順になる)
    setDownloadContents(DOWNLOAD_CONTENTS.map(c => c.id).filter(c => next.includes(c)))
  }

  function download() {
    return runTask('ダウンロード用ファイルの作成', async signal => {
      const res = await fetch(`${apiOrigin}/api/manga-v2/${storyId}/download`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...composeSettings, format: downloadFormat, contents: downloadContents }),
        signal,
      })
      if (!res.ok) throw new Error(await readErrorDetail(res))
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = downloadFilename(res, `manga_story${storyId}.${blob.type === 'application/pdf' ? 'pdf' : 'zip'}`)
      document.body.appendChild(link)
      link.click()
      link.remove()
      // クリック直後に解放するとダウンロードが始まらないブラウザがあるので少し待つ
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
      return 'ダウンロードを開始しました'
    })
  }

  const downloadIsZip = downloadFormat === 'zip' || downloadContents.length > 1

  async function putOverride(key: string, x: number | null, y: number | null, signal: AbortSignal) {
    const res = await fetch(`${apiOrigin}/api/manga-v2/${storyId}/overrides`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, x, y }),
      signal,
    })
    if (!res.ok) throw new Error(await readErrorDetail(res))
  }

  function moveElement(element: MangaV2Element, x: number, y: number) {
    return runTask('位置の保存と再合成', async signal => {
      await putOverride(element.key, x, y, signal)
      await requestCompose(signal)
    })
  }

  function scaleElement(element: MangaV2Element, scale: number | null) {
    return runTask('大きさの保存と再合成', async signal => {
      const res = await fetch(`${apiOrigin}/api/manga-v2/${storyId}/scales`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: element.key, scale }),
        signal,
      })
      if (!res.ok) throw new Error(await readErrorDetail(res))
      await requestCompose(signal)
    })
  }

  function resetElements(elements: MangaV2Element[]) {
    return runTask('自動配置に戻して再合成', async signal => {
      for (const element of elements) await putOverride(element.key, null, null, signal)
      await requestCompose(signal)
    })
  }

  return (
    <div className="mv2">
      <div className="story-settings">
        <div className="story-row">
          <label>
            コマ割り
            <select value={template} disabled={busy} onChange={e => setTemplate(e.target.value)}>
              {templates.map(t => (
                <option key={t.id} value={t.id}>{t.label}</option>
              ))}
            </select>
          </label>
          <label>
            セリフのフォント
            <select value={font} disabled={busy} onChange={e => setFont(e.target.value)}>
              {fonts.map(f => (
                <option key={f.id} value={f.id}>{f.label}</option>
              ))}
            </select>
          </label>
          <label>
            効果音のフォント
            <select value={sfxFont} disabled={busy} onChange={e => setSfxFont(e.target.value)}>
              {fonts.map(f => (
                <option key={f.id} value={f.id}>{f.label}</option>
              ))}
            </select>
          </label>
        </div>
        <div className="story-row">
          <label>
            セリフの文字の大きさ: {textScale}%
            <input type="range" min={50} max={200} step={5} value={textScale} disabled={busy}
              onChange={e => setTextScale(Number(e.target.value))} />
          </label>
          <label>
            描き文字・スタンプの大きさ: {sfxScale}%
            <input type="range" min={50} max={200} step={5} value={sfxScale} disabled={busy}
              onChange={e => setSfxScale(Number(e.target.value))} />
          </label>
        </div>
        <p className="story-muted">
          文字はコマの大きさ(大ゴマほど大きく)と内容(叫びは大きく、小声は小さく)で自動調整され、
          そこに上の倍率が掛かります。1つずつ変えたいときは、合成後のページで要素をタップしてください。
        </p>
        <div className="story-row">
          <label>
            吹き出しの不透明度: {opacity}%{opacity === 0 ? '(輪郭のみ)' : ''}
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={opacity}
              disabled={busy}
              onChange={e => setOpacity(Number(e.target.value))}
            />
          </label>
          <label>
            1コマのセリフ上限(超えたら同じ絵の寄りでコマを足す・0で分けない)
            <input type="number" min={0} max={10} value={maxLines} disabled={busy}
              onChange={e => setMaxLines(Math.max(0, Number(e.target.value)))} />
          </label>
        </div>
        <div className="story-row">
          <label className="mv2-check">
            <input type="checkbox" checked={color} disabled={busy} onChange={e => setColor(e.target.checked)} />
            カラーで描く(オフならモノクロ)
          </label>
        </div>
        <p className="story-muted">
          画像生成の設定(モデル・ステップ数など)は上の「挿絵の設定」を使います。幅・高さはコマの形から自動で決めます。
          セリフ「{fontLabel(font)}」/ 効果音「{fontLabel(sfxFont)}」。
        </p>
      </div>

      <details className="story-characters-block" open={useReference}>
        <summary>キャラ参照(登場人物の見た目をコマ間で揃える)</summary>
        <label className="mv2-check">
          <input type="checkbox" checked={useReference} disabled={busy}
            onChange={e => setUseReference(e.target.checked)} />
          キャラ参照を使う
        </label>
        <p className="story-muted">
          参照画像のあるキャラが出るコマは <strong>V4.5 Full</strong> で生成します(V5はキャラ参照に未対応)。
          1コマあたり <strong>+5 Anlas</strong>(Opusの無料枠の対象外)。1コマに使えるのは1人分です。
        </p>
        {useReference && (
          <div className="story-row">
            <label>
              参照の強さ: {refStrength.toFixed(2)}
              <input type="range" min={0} max={1} step={0.05} value={refStrength} disabled={busy}
                onChange={e => setRefStrength(Number(e.target.value))} />
            </label>
            <label>
              忠実度: {refFidelity.toFixed(2)}
              <input type="range" min={0} max={1} step={0.05} value={refFidelity} disabled={busy}
                onChange={e => setRefFidelity(Number(e.target.value))} />
            </label>
          </div>
        )}
        {storyCharacters.length === 0 ? (
          <p className="story-muted">
            シーンに登場人物が割り当てられていません。「登場人物を抽出」するか、登場人物を登録して割り当ててください。
          </p>
        ) : (
          <ul className="mv2-refs">
            {storyCharacters.map(c => (
              <li key={c.id} className="mv2-ref">
                {c.reference_image_path ? (
                  <img className="mv2-ref-thumb" src={fileUrl(c.reference_image_path)} alt={`${c.name}の参照画像`} />
                ) : (
                  <div className="mv2-ref-thumb mv2-thumb--empty">なし</div>
                )}
                <div className="mv2-card-body">
                  <div className="mv2-card-title">{c.name}</div>
                  <select
                    value=""
                    disabled={busy || panels.length === 0}
                    onChange={e => { if (e.target.value) void setReference(c.id, { scene_id: Number(e.target.value) }) }}
                  >
                    <option value="">コマの絵から選ぶ…</option>
                    {panels.map(p => (
                      <option key={p.scene_id} value={p.scene_id}>シーン{p.scene_index + 1}の絵</option>
                    ))}
                  </select>
                  <label className="mv2-upload">
                    画像をアップロード
                    <input type="file" accept="image/*" disabled={busy}
                      onChange={e => { const f = e.target.files?.[0]; if (f) uploadReference(c.id, f); e.target.value = '' }} />
                  </label>
                  {c.reference_image_path && (
                    <button type="button" className="story-secondary" disabled={busy}
                      onClick={() => setReference(c.id, null)}>
                      参照を外す
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
        {useReference && referencedNames.length > 0 && (
          <p className="story-muted">参照あり: {referencedNames.join('、')}</p>
        )}
      </details>

      <MangaV2SfxFonts
        apiOrigin={apiOrigin}
        storyId={storyId}
        words={[...new Set(targetScenes.flatMap(s => s.sfx ?? []))]}
        fonts={fonts}
        busy={busy}
        runTask={runTask}
        onSuggest={suggestSfxFonts}
        onFontsChanged={loadFonts}
        refreshKey={sfxFontsKey}
      />

      <MangaV2Stamps
        apiOrigin={apiOrigin}
        storyId={storyId}
        words={[...new Set(targetScenes.flatMap(s => s.sfx ?? []))]}
        busy={busy}
        runTask={runTask}
        refreshKey={sfxFontsKey}
      />

      <div className="story-row">
        <label>
          対象の開始シーン
          <input type="number" min={1} max={scenes.length} value={sceneFrom}
            onChange={e => setSceneFrom(Number(e.target.value))} />
        </label>
        <label>
          対象の終了シーン(全{scenes.length}シーン / 1ページ{perPage}コマ)
          <input type="number" min={1} max={scenes.length} value={sceneTo}
            onChange={e => setSceneTo(Number(e.target.value))} />
        </label>
      </div>
      <div className="story-row">
        <label className="mv2-check">
          <input type="checkbox" checked={skipExisting} onChange={e => setSkipExisting(e.target.checked)} />
          絵があるシーンは飛ばす
        </label>
        <label className="mv2-check">
          <input type="checkbox" checked={overwriteSfx} onChange={e => setOverwriteSfx(e.target.checked)} />
          効果音・ナレーションを設定済みのシーンもAIで上書き
        </label>
        <label className="mv2-check">
          <input type="checkbox" checked={includeAdult} onChange={e => setIncludeAdult(e.target.checked)} />
          描き文字の自動選択に成人向けのスタンプも使う
        </label>
      </div>

      <div className="story-actions">
        <button type="button" onClick={generatePanels} disabled={busy}>
          コマの絵を生成(シーン{from}〜{to})
        </button>
        <button type="button" onClick={suggestSfx} disabled={busy}>
          効果音をAIに提案させる
        </button>
        <button type="button" onClick={suggestNarration} disabled={busy}>
          ナレーションをAIに書かせる
        </button>
        <button type="button" onClick={compose} disabled={busy || panels.length === 0}>
          ページを合成
        </button>
      </div>

      <ul className="mv2-grid">
        {targetScenes.map(scene => {
          const panel = panelByScene.get(scene.id)
          return (
            <li key={scene.id} className="mv2-card">
              {panel ? (
                <img
                  className="mv2-thumb"
                  loading="lazy"
                  src={`${fileUrl(panel.image_path)}&v=${encodeURIComponent(panel.created_at)}`}
                  alt={`シーン${scene.scene_index + 1}のコマ`}
                />
              ) : (
                <div className="mv2-thumb mv2-thumb--empty">未生成</div>
              )}
              <div className="mv2-card-body">
                <div className="mv2-card-title">
                  シーン{scene.scene_index + 1}{scene.draft_title ? `: ${scene.draft_title}` : ''}
                </div>
                <div className="story-muted">セリフ {dialogueCount(scene)} 個</div>
                <label className="mv2-sfx">
                  効果音
                  <input
                    type="text"
                    value={sfxDrafts[scene.id] ?? ''}
                    placeholder={scene.sfx === null ? '未設定(AI提案の対象)' : 'なし'}
                    disabled={busy}
                    onChange={e => setSfxDrafts({ ...sfxDrafts, [scene.id]: e.target.value })}
                    onBlur={() => saveSfxDraft(scene)}
                    onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
                  />
                </label>
                <label className="mv2-sfx">
                  ナレーション(80字まで)
                  <textarea
                    rows={2}
                    maxLength={80}
                    value={narrationDrafts[scene.id] ?? ''}
                    placeholder={scene.narration === null ? '未設定(AI作成の対象)' : 'なし'}
                    disabled={busy}
                    onChange={e => setNarrationDrafts({ ...narrationDrafts, [scene.id]: e.target.value })}
                    onBlur={() => saveNarrationDraft(scene)}
                  />
                </label>
                <div className="mv2-card-actions">
                  <button type="button" onClick={() => redrawPanel(scene)} disabled={busy}>
                    {panel ? '描き直す' : '描く'}
                  </button>
                  {scene.sfx !== null && (
                    <button type="button" className="story-secondary" disabled={busy}
                      onClick={() => saveSfx(scene, null)}>
                      効果音を未設定に戻す
                    </button>
                  )}
                  {scene.narration !== null && (
                    <button type="button" className="story-secondary" disabled={busy}
                      onClick={() => saveNarration(scene, null)}>
                      ナレーションを未設定に戻す
                    </button>
                  )}
                </div>
              </div>
            </li>
          )
        })}
      </ul>
      <p className="story-muted">
        効果音は「、」や空白で区切って入力します(フォーカスを外すと保存)。本文の《ガタッ》や
        カタカナだけのセリフ「ドキッ」も自動で描き文字になります。
      </p>

      {composed && composed.pages.length > 0 && (
        <div className="mv2-compose">
          <div className="mv2-page-tabs">
            {composed.pages.map((path, index) => (
              <button
                key={path}
                type="button"
                className={index === editPage ? 'mv2-mode-active' : 'story-secondary'}
                onClick={() => setEditPage(index)}
              >
                P{index + 1}
              </button>
            ))}
          </div>
          <p className="story-muted">
            吹き出し(青枠)・描き文字(橙枠)・ナレーション(緑枠)をドラッグすると、その位置で合成し直します。
            手で動かしたもの(実線)はダブルクリックで自動配置に戻せます。
          </p>
          <MangaV2PageEditor
            imageUrl={fileUrl(composed.pages[editPage])}
            elements={composed.elements[editPage] ?? []}
            pageWidth={composed.page_width}
            pageHeight={composed.page_height}
            busy={busy}
            onMove={moveElement}
            onReset={element => resetElements([element])}
            onScale={scaleElement}
          />
          <div className="story-actions">
            <a className="mv2-open" href={fileUrl(composed.pages[editPage])} target="_blank" rel="noreferrer">
              このページを開く
            </a>
            {(composed.elements[editPage] ?? []).some(e => e.moved) && (
              <button
                type="button"
                className="story-secondary"
                disabled={busy}
                onClick={() => resetElements((composed.elements[editPage] ?? []).filter(e => e.moved))}
              >
                このページの手動配置を戻す
              </button>
            )}
          </div>
        </div>
      )}

      {panels.length > 0 && (
        <div className="mv2-download">
          <h4>ダウンロード</h4>
          <div className="story-row">
            <span>形式</span>
            {(['pdf', 'zip'] as const).map(format => (
              <label key={format} className="mv2-check">
                <input
                  type="radio"
                  name="mv2-download-format"
                  value={format}
                  checked={downloadFormat === format}
                  disabled={busy}
                  onChange={() => setDownloadFormat(format)}
                />
                {format === 'pdf' ? 'PDF' : '画像一式(zip)'}
              </label>
            ))}
          </div>
          <div className="mv2-download-contents">
            {DOWNLOAD_CONTENTS.map(c => (
              <label key={c.id} className="mv2-check">
                <input
                  type="checkbox"
                  checked={downloadContents.includes(c.id)}
                  disabled={busy}
                  onChange={e => toggleDownloadContent(c.id, e.target.checked)}
                />
                {c.label}
              </label>
            ))}
          </div>
          <p className="story-muted">
            {savedComposeSettings
              ? `最後に「ページを合成」したときの設定(テンプレート「${templates.find(t => t.id === savedComposeSettings.template)?.label ?? savedComposeSettings.template}」・フォント・文字の大きさ)と手動配置で書き出します。設定を変えたら、先に合成し直してください。`
              : 'まだ合成していないので、今の設定(テンプレート・フォント・文字の大きさ)で書き出します。'}
            コマ画像(セリフなし)は生成した絵そのもの、(セリフあり)はその絵に吹き出しと描き文字を入れたものです。
            {downloadFormat === 'pdf' && downloadContents.length > 1 && ' PDFで複数選ぶと、種類ごとのPDFをまとめたzipになります。'}
          </p>
          <div className="story-actions">
            <button type="button" onClick={download} disabled={busy || downloadContents.length === 0}>
              {downloadIsZip ? 'zipでダウンロード' : 'PDFでダウンロード'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
